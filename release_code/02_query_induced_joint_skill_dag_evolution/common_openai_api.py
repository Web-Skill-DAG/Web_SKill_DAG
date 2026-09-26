#!/usr/bin/env python3
"""OpenAI-compatible chat transport, parsing, retries, and request gating."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry


_SHARED_HTTP_ADAPTERS: dict[tuple[Any, ...], HTTPAdapter] = {}
_SHARED_HTTP_ADAPTERS_LOCK = threading.Lock()


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def timestamped_write(message: str) -> None:
    tqdm.write(f"{utc_timestamp()} {message}")


class APIHTTPError(RuntimeError):
    def __init__(self, status_code: int, detail: str, retry_after: float | None = None):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.retry_after = retry_after


class APITransportError(RuntimeError):
    pass


class AdaptiveRequestGate:
    """Bound concurrent requests and coordinate one cooldown after failures."""

    def __init__(self, concurrency: int, max_backoff: float):
        self.semaphore = asyncio.Semaphore(max(1, concurrency))
        self.max_backoff = max(0.0, max_backoff)
        self.cooldown_until = 0.0
        self.failure_streak = 0
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            delay = max(0.0, self.cooldown_until - time.monotonic())
            if delay:
                await asyncio.sleep(delay)
            await self.semaphore.acquire()
            delay = max(0.0, self.cooldown_until - time.monotonic())
            if not delay:
                return
            self.semaphore.release()

    def release(self) -> None:
        self.semaphore.release()

    async def success(self) -> None:
        async with self.lock:
            self.failure_streak = max(0, self.failure_streak - 1)

    async def failure(self, status_code: int | None, retry_after: float | None) -> float:
        async with self.lock:
            self.failure_streak = min(self.failure_streak + 1, 12)
            if retry_after is not None:
                base = max(0.0, retry_after)
            else:
                base = (5.0 if status_code == 429 else 1.0) * (2 ** min(self.failure_streak - 1, 7))
            base = min(self.max_backoff, base)
            jitter = random.uniform(0.0, min(5.0, max(0.25, base * 0.2)))
            delay = min(self.max_backoff, base + jitter)
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + delay)
            return delay


def api_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/v1/chat/completions"):
        return value
    if value.endswith("/v1"):
        return value + "/chat/completions"
    return value + "/v1/chat/completions"


def response_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("API response has no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ValueError("API response has no assistant message")
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
        ).strip()
    raise ValueError("API response assistant content is not text")


def extract_json_object(text: str) -> Any:
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.IGNORECASE)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", cleaned):
        try:
            value, _ = decoder.raw_decode(cleaned[match.start():])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError("API response does not contain a JSON object")


def _shared_http_adapter(args: argparse.Namespace) -> HTTPAdapter:
    pool_maxsize = max(1, int(getattr(args, "request_concurrency", 1)))
    key = (
        args.base_url,
        args.transport_retries,
        args.transport_backoff_factor,
        pool_maxsize,
    )
    with _SHARED_HTTP_ADAPTERS_LOCK:
        adapter = _SHARED_HTTP_ADAPTERS.get(key)
        if adapter is None:
            retries = Retry(
                total=args.transport_retries,
                connect=args.transport_retries,
                read=args.transport_retries,
                status=args.transport_retries,
                backoff_factor=args.transport_backoff_factor,
                status_forcelist=(500, 502, 503, 504),
                allowed_methods=frozenset(("POST",)),
                respect_retry_after_header=True,
                raise_on_status=False,
            )
            adapter = HTTPAdapter(
                pool_connections=1,
                pool_maxsize=pool_maxsize,
                max_retries=retries,
                pool_block=True,
            )
            _SHARED_HTTP_ADAPTERS[key] = adapter
        return adapter


def close_shared_http_adapters() -> None:
    with _SHARED_HTTP_ADAPTERS_LOCK:
        adapters = list(_SHARED_HTTP_ADAPTERS.values())
        _SHARED_HTTP_ADAPTERS.clear()
    for adapter in adapters:
        adapter.close()


def _retry_after_seconds(value: str | None, maximum: float) -> float | None:
    if value is None:
        return None
    try:
        return min(maximum, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


def call_chat_api(
    prompt: str,
    args: argparse.Namespace,
    session_id: str,
) -> tuple[dict[str, Any], dict[str, Any], int, int]:
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": args.max_output_tokens,
        "response_format": {"type": "json_object"},
    }
    session = requests.Session()
    session.trust_env = False
    session.mount("http://", _shared_http_adapter(args))
    session.mount("https://", _shared_http_adapter(args))
    response: requests.Response | None = None
    try:
        response = session.post(
            api_url(args.base_url),
            headers={
                "Authorization": f"Bearer {args.api_key}",
                "Content-Type": "application/json",
                "X-Session-ID": session_id,
            },
            json=payload,
            timeout=args.api_timeout,
        )
        status = int(response.status_code)
        response_bytes = response.content or b""
        if status != 200:
            detail = response_bytes.decode("utf-8", errors="replace")[:4000]
            retry_after = _retry_after_seconds(
                response.headers.get("Retry-After"),
                getattr(args, "max_global_backoff", 120.0),
            )
            raise APIHTTPError(status, detail or response.reason, retry_after)
        try:
            raw = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise APITransportError(f"invalid JSON response: {exc}") from exc
        return payload, raw, status, len(response_bytes)
    except requests.RequestException as exc:
        raise APITransportError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if response is not None:
            response.close()
        session.adapters.clear()
        session.close()


async def retry_direct_dialogue(
    prompt: str,
    phase: str,
    case_id: str,
    validator: Any,
    args: argparse.Namespace,
) -> tuple[Any, dict[str, Any]]:
    """Call the configured API and retain the complete request trajectory."""
    gate = getattr(args, "request_gate", None)
    if gate is None:
        gate = AdaptiveRequestGate(
            getattr(args, "request_concurrency", 32),
            getattr(args, "max_global_backoff", 120.0),
        )
        args.request_gate = gate
    session_id = str(uuid.uuid4())
    trajectory = {
        "case_id": case_id,
        "phase": phase,
        "session_id": session_id,
        "session_ids": [session_id],
        "attempts": [],
    }
    attempt = 0
    gate_wait_started = time.time()
    await gate.acquire()
    gate_wait_seconds = time.time() - gate_wait_started
    try:
        while True:
            attempt += 1
            started = time.time()
            started_at_utc = utc_timestamp()
            try:
                timestamped_write(
                    f"[api-start] phase={phase} case={case_id} attempt={attempt} "
                    f"gate_wait={gate_wait_seconds:.3f}s"
                )
                request_payload, raw_response, http_status, response_bytes = await asyncio.to_thread(
                    call_chat_api, prompt, args, session_id
                )
                elapsed = time.time() - started
                text = response_text(raw_response)
                value = validator(extract_json_object(text))
                await gate.success()
                timestamped_write(
                    f"[api-return] phase={phase} case={case_id} status={http_status} "
                    f"attempt={attempt} elapsed={elapsed:.2f}s bytes={response_bytes}"
                )
                trajectory["attempts"].append(
                    {
                        "attempt": attempt,
                        "status": "accepted",
                        "started_at_utc": started_at_utc,
                        "duration_seconds": round(elapsed, 3),
                        "session_id": session_id,
                        "http_status": http_status,
                        "response_bytes": response_bytes,
                        "messages": request_payload["messages"] + [{"role": "assistant", "content": text}],
                        "response": raw_response,
                    }
                )
                return value, trajectory
            except Exception as exc:
                http_status = exc.status_code if isinstance(exc, APIHTTPError) else None
                retry_after = exc.retry_after if isinstance(exc, APIHTTPError) else None
                transport_failure = isinstance(exc, (APIHTTPError, APITransportError))
                trajectory["attempts"].append(
                    {
                        "attempt": attempt,
                        "status": "failed",
                        "started_at_utc": started_at_utc,
                        "error": str(exc),
                        "duration_seconds": round(time.time() - started, 3),
                        "session_id": session_id,
                        "http_status": http_status,
                        "retry_after": retry_after,
                    }
                )
                if not args.retry_forever and attempt > args.retries:
                    raise
                if retry_after is not None:
                    delay = min(args.max_global_backoff, max(0.0, retry_after))
                elif transport_failure:
                    delay = min(args.max_global_backoff, 0.5 * (2 ** min(attempt - 1, 8)))
                else:
                    delay = min(
                        args.max_global_backoff,
                        args.retry_delay + random.uniform(0.0, min(5.0, args.retry_delay * 0.2)),
                    )
                await gate.failure(http_status, retry_after)
                retry_total = "infinite" if args.retry_forever else str(args.retries)
                timestamped_write(
                    f"[{phase}] {case_id} API request failed: {exc}; "
                    f"retry {attempt}/{retry_total} in {delay:g}s"
                )
                await asyncio.sleep(delay)
    finally:
        gate.release()
