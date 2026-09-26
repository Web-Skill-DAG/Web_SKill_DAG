#!/usr/bin/env python3
"""Shared OpenAI-compatible chat transport, parsing, retries, and routing."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import threading
import time
import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from tqdm import tqdm

_SHARED_HTTP_ADAPTERS: dict[tuple[Any, ...], HTTPAdapter] = {}
_SHARED_HTTP_ADAPTERS_LOCK = threading.Lock()


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def timestamped_write(message: str) -> None:
    tqdm.write(f"{utc_timestamp()} {message}")


class ModelHTTPError(RuntimeError):
    def __init__(self, status_code: int, detail: str, retry_after: float | None = None):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.retry_after = retry_after


class ModelTransportError(RuntimeError):
    pass


class AdaptiveRequestGate:
    """Bound model traffic and apply one shared cooldown after upstream failures."""

    def __init__(self, concurrency: int, max_backoff: float):
        self.semaphore = asyncio.Semaphore(concurrency)
        self.max_backoff = max_backoff
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
                initial = 5.0 if status_code == 429 else 1.0
                base = initial * (2 ** min(self.failure_streak - 1, 7))
            base = min(self.max_backoff, base)
            jitter = random.uniform(0.0, min(5.0, max(0.25, base * 0.2)))
            delay = min(self.max_backoff, base + jitter)
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + delay)
            return delay


def parse_model_pool(value: str | list[str] | tuple[str, ...] | None, primary: str) -> list[str]:
    """Return a stable, de-duplicated model route list with the primary first."""
    if isinstance(value, str):
        raw = re.split(r"[,\s]+", value.strip()) if value.strip() else []
    elif value:
        raw = [str(item).strip() for item in value]
    else:
        raw = []
    ordered: list[str] = []
    for model in [primary, *raw]:
        model = str(model).strip()
        if model and model not in ordered:
            ordered.append(model)
    return ordered


class AdaptiveModelRouter:
    """Balance requests across interchangeable routes and circuit-break weak ones.

    Selection is capacity-aware: a slow route naturally retains more in-flight
    requests and receives fewer new ones. Recent errors open a per-model circuit,
    while phase-matched latency samples can temporarily cool a route whose EWMA
    is materially slower than the best observed alternative.
    """

    def __init__(
        self,
        models: list[str],
        *,
        min_samples: int = 8,
        slow_ratio: float = 1.8,
        error_cooldown: float = 15.0,
        slow_cooldown: float = 60.0,
        quota_cooldown: float = 300.0,
        max_cooldown: float = 120.0,
        probe_interval: float = 120.0,
        switch_after_failures: int = 2,
        error_window: int = 20,
        error_min_samples: int = 20,
        error_rate_threshold: float = 0.30,
        disable_circuit: bool = False,
        per_model_concurrency: int = 0,
        state_path: Path | None = None,
    ):
        if not models:
            raise ValueError("AdaptiveModelRouter requires at least one model")
        self.models = list(dict.fromkeys(models))
        self.min_samples = max(1, min_samples)
        self.slow_ratio = max(1.05, slow_ratio)
        self.error_cooldown = max(0.1, error_cooldown)
        self.slow_cooldown = max(0.1, slow_cooldown)
        self.quota_cooldown = max(self.slow_cooldown, quota_cooldown)
        self.max_cooldown = max(self.error_cooldown, max_cooldown)
        self.probe_interval = max(1.0, probe_interval)
        self.switch_after_failures = max(1, switch_after_failures)
        self.error_window = max(1, error_window)
        self.error_min_samples = min(
            self.error_window, max(1, error_min_samples)
        )
        self.error_rate_threshold = min(1.0, max(0.01, error_rate_threshold))
        self.disable_circuit = disable_circuit
        self.per_model_concurrency = max(0, per_model_concurrency)
        self.state_path = state_path
        self.lock = asyncio.Lock()
        self.capacity_event = asyncio.Event()
        self.capacity_event.set()
        self.selection_index = 0
        self.event_count = 0
        self.last_snapshot_at = 0.0
        self.stats: dict[str, dict[str, Any]] = {
            model: {
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "inflight": 0,
                "consecutive_failures": 0,
                "ewma_latency": None,
                "recent_outcomes": deque(maxlen=self.error_window),
                "cooldown_until": 0.0,
                "last_attempt_at": 0.0,
                "last_success_at": 0.0,
                "last_failure_at": 0.0,
                "last_failure": "",
                "needs_probe": False,
                "phase": {},
            }
            for model in self.models
        }

    @staticmethod
    def _phase_stats(stats: dict[str, Any], phase: str) -> dict[str, Any]:
        return stats["phase"].setdefault(phase, {"successes": 0, "ewma_latency": None})

    @staticmethod
    def _recent_success_rate(stats: dict[str, Any]) -> float:
        outcomes = stats["recent_outcomes"]
        if not outcomes:
            return 1.0
        return max(0.1, sum(outcomes) / len(outcomes))

    def _best_phase_latency(self, phase: str) -> float | None:
        eligible = []
        for model in self.models:
            phase_stats = self._phase_stats(self.stats[model], phase)
            if phase_stats["successes"] >= self.min_samples and phase_stats["ewma_latency"]:
                eligible.append(float(phase_stats["ewma_latency"]))
        return min(eligible) if eligible else None

    def _score(self, model: str, phase: str, best_latency: float | None) -> float:
        stats = self.stats[model]
        phase_stats = self._phase_stats(stats, phase)
        latency = phase_stats["ewma_latency"] or stats["ewma_latency"]
        latency_factor = 1.0
        if latency and best_latency:
            latency_factor = min(4.0, max(0.5, float(latency) / best_latency))
        success_rate = self._recent_success_rate(stats)
        # The in-flight term performs online throughput balancing even before
        # enough completed samples exist for an explicit latency comparison.
        return (stats["inflight"] + 1) * latency_factor / success_rate

    async def choose(
        self,
        phase: str,
        exclude: str | None = None,
        preferred: str | None = None,
    ) -> str:
        while True:
            delay = 0.0
            wait_for_capacity = False
            async with self.lock:
                now = time.monotonic()
                available = [m for m in self.models if self.stats[m]["cooldown_until"] <= now]
                if not available:
                    delay = min(self.stats[m]["cooldown_until"] for m in self.models) - now
                else:
                    capacity_available = [
                        m for m in available
                        if self.per_model_concurrency <= 0
                        or self.stats[m]["inflight"] < self.per_model_concurrency
                    ]
                    if not capacity_available:
                        self.capacity_event.clear()
                        wait_for_capacity = True
                        available = []
                    else:
                        available = capacity_available
                if available:
                    alternatives = [m for m in available if m != exclude]
                    if alternatives:
                        available = alternatives
                    if preferred in available:
                        model = preferred
                    # After a circuit closes, send one bounded canary instead of
                    # permanently starving that route based on stale evidence.
                    else:
                        probes = [
                            m for m in available
                            if self.stats[m]["needs_probe"]
                            and self.stats[m]["inflight"] == 0
                            and now - self.stats[m]["last_attempt_at"] >= self.probe_interval
                        ]
                        if probes:
                            model = min(probes, key=lambda m: self.stats[m]["last_attempt_at"])
                            self.stats[model]["needs_probe"] = False
                        else:
                            best_latency = self._best_phase_latency(phase)
                            rotation = self.selection_index % len(self.models)
                            order = self.models[rotation:] + self.models[:rotation]
                            rank = {model: index for index, model in enumerate(order)}
                            model = min(
                                available,
                                key=lambda m: (self._score(m, phase, best_latency), rank[m]),
                            )
                    stats = self.stats[model]
                    stats["attempts"] += 1
                    stats["inflight"] += 1
                    stats["last_attempt_at"] = now
                    self.selection_index += 1
                    return model
            if wait_for_capacity:
                await self.capacity_event.wait()
            else:
                await asyncio.sleep(max(0.05, delay) + random.uniform(0.0, 0.25))

    async def begin_fixed_retry(self, model: str) -> None:
        """Account for another attempt while retaining the existing model slot."""
        async with self.lock:
            now = time.monotonic()
            stats = self.stats[model]
            stats["attempts"] += 1
            stats["last_attempt_at"] = now

    async def release(self, model: str) -> None:
        """Release a reserved model slot after cancellation or terminal exit."""
        async with self.lock:
            stats = self.stats[model]
            stats["inflight"] = max(0, stats["inflight"] - 1)
            self.capacity_event.set()

    async def success(self, model: str, phase: str, latency: float) -> None:
        message = ""
        async with self.lock:
            now = time.monotonic()
            stats = self.stats[model]
            stats["inflight"] = max(0, stats["inflight"] - 1)
            self.capacity_event.set()
            stats["successes"] += 1
            stats["consecutive_failures"] = 0
            stats["recent_outcomes"].append(1)
            stats["last_success_at"] = now
            stats["ewma_latency"] = (
                latency if stats["ewma_latency"] is None
                else 0.8 * stats["ewma_latency"] + 0.2 * latency
            )
            phase_stats = self._phase_stats(stats, phase)
            phase_stats["successes"] += 1
            phase_stats["ewma_latency"] = (
                latency if phase_stats["ewma_latency"] is None
                else 0.8 * phase_stats["ewma_latency"] + 0.2 * latency
            )
            best = self._best_phase_latency(phase)
            if (
                not self.disable_circuit
                and
                len(self.models) > 1
                and best
                and phase_stats["successes"] >= self.min_samples
                and phase_stats["ewma_latency"] > best * self.slow_ratio
            ):
                stats["cooldown_until"] = max(stats["cooldown_until"], now + self.slow_cooldown)
                stats["needs_probe"] = True
                message = (
                    f"[model-router] slow-circuit model={model} phase={phase} "
                    f"ewma={phase_stats['ewma_latency']:.1f}s best={best:.1f}s "
                    f"ratio={phase_stats['ewma_latency'] / best:.2f} "
                    f"cooldown={self.slow_cooldown:.1f}s"
                )
            self.event_count += 1
            self._write_snapshot_locked(now)
            if not message and self.event_count % 50 == 0:
                message = self._summary_locked(now)
        if message:
            timestamped_write(message)

    async def failure(
        self,
        model: str,
        phase: str,
        *,
        status_code: int | None,
        detail: str,
        validation_failure: bool = False,
        release_inflight: bool = True,
    ) -> float:
        async with self.lock:
            now = time.monotonic()
            stats = self.stats[model]
            if release_inflight:
                stats["inflight"] = max(0, stats["inflight"] - 1)
                self.capacity_event.set()
            stats["failures"] += 1
            stats["consecutive_failures"] = min(stats["consecutive_failures"] + 1, 10)
            stats["last_failure_at"] = now
            stats["last_failure"] = detail[:500]
            lowered = detail.lower()
            quota_failure = any(token in lowered for token in (
                "monthly_request_count", "servicequotaexceeded", "you have reached the limit",
            ))
            if validation_failure or self.disable_circuit:
                cooldown = 0.0
            else:
                stats["recent_outcomes"].append(0)
                outcomes = stats["recent_outcomes"]
                failure_rate = 1.0 - (sum(outcomes) / len(outcomes))
                if quota_failure:
                    cooldown = self.quota_cooldown
                elif (
                    len(outcomes) >= self.error_min_samples
                    and failure_rate >= self.error_rate_threshold
                ):
                    excess = max(
                        0.0,
                        failure_rate - self.error_rate_threshold,
                    )
                    exponent = min(int(excess / 0.15), 4)
                    cooldown = min(
                        self.max_cooldown,
                        self.error_cooldown * (2 ** exponent),
                    )
                else:
                    cooldown = 0.0
            if cooldown:
                stats["cooldown_until"] = max(stats["cooldown_until"], now + cooldown)
                stats["needs_probe"] = True
            self.event_count += 1
            self._write_snapshot_locked(now)
            reason = "validation" if validation_failure else ("quota" if quota_failure else f"http={status_code}")
            action = "circuit" if cooldown else "observe"
            outcomes = stats["recent_outcomes"]
            failure_rate = 1.0 - (sum(outcomes) / len(outcomes)) if outcomes else 0.0
            message = (
                f"[model-router] {action} model={model} phase={phase} reason={reason} "
                f"window={len(outcomes)}/{self.error_window} "
                f"failure_rate={failure_rate:.1%} cooldown={cooldown:.1f}s"
            )
        timestamped_write(message)
        return cooldown

    def _summary_locked(self, now: float) -> str:
        parts = []
        for model in self.models:
            stats = self.stats[model]
            latency = "-" if stats["ewma_latency"] is None else f"{stats['ewma_latency']:.1f}s"
            cooldown = max(0.0, stats["cooldown_until"] - now)
            parts.append(
                f"{model}:ok={stats['successes']},fail={stats['failures']},"
                f"inflight={stats['inflight']},ewma={latency},cooldown={cooldown:.0f}s"
            )
        return "[model-router] " + " | ".join(parts)

    def _snapshot_locked(self, now: float) -> dict[str, Any]:
        models: dict[str, Any] = {}
        for model in self.models:
            stats = self.stats[model]
            models[model] = {
                "attempts": stats["attempts"],
                "successes": stats["successes"],
                "failures": stats["failures"],
                "inflight": stats["inflight"],
                "consecutive_failures": stats["consecutive_failures"],
                "recent_success_rate": round(self._recent_success_rate(stats), 4),
                "ewma_latency_seconds": (
                    None if stats["ewma_latency"] is None else round(stats["ewma_latency"], 3)
                ),
                "cooldown_remaining_seconds": round(max(0.0, stats["cooldown_until"] - now), 3),
                "last_failure": stats["last_failure"],
                "phase": stats["phase"],
            }
        return {
            "updated_unix": time.time(),
            "policy": {
                "switch_after_failures_per_request": self.switch_after_failures,
                "error_window": self.error_window,
                "error_min_samples": self.error_min_samples,
                "error_rate_threshold": self.error_rate_threshold,
                "disable_circuit": self.disable_circuit,
                "per_model_concurrency": self.per_model_concurrency,
            },
            "models": models,
        }

    def _write_snapshot_locked(self, now: float) -> None:
        if self.state_path is None or now - self.last_snapshot_at < 5.0:
            return
        self.last_snapshot_at = now
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._snapshot_locked(now), ensure_ascii=False, indent=2, default=float) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.state_path)


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
    raise ValueError("model response does not contain a JSON object")


def _shared_http_adapter(args: argparse.Namespace) -> HTTPAdapter:
    """Return the process-wide bounded pool used by all model worker threads.

    Previously every worker/model pair owned an independent Session and pool.
    With 3,000 workers and six model routes that could retain roughly 18,000
    keep-alive connections and exhaust the host's usable ephemeral ports.
    """
    pool_maxsize = max(1, int(getattr(args, "model_concurrency", 1)))
    pool_connections = max(1, len(getattr(args, "model_pool", ()) or (args.model,)))
    key = (
        args.base_url,
        args.transport_retries,
        args.transport_backoff_factor,
        pool_connections,
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
                pool_connections=pool_connections,
                pool_maxsize=pool_maxsize,
                max_retries=retries,
                pool_block=True,
            )
            _SHARED_HTTP_ADAPTERS[key] = adapter
        return adapter


def close_shared_http_adapters() -> None:
    """Close all persistent model connections at the end of a pipeline run."""
    with _SHARED_HTTP_ADAPTERS_LOCK:
        adapters = list(_SHARED_HTTP_ADAPTERS.values())
        _SHARED_HTTP_ADAPTERS.clear()
    for adapter in adapters:
        adapter.close()


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return min(120.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


def call_chat_api(
    prompt: str,
    args: argparse.Namespace,
    session_id: str,
    model: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], int, int]:
    selected_model = model or args.model
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": args.max_output_tokens,
        "response_format": {"type": "json_object"},
    }
    if selected_model:
        payload["model"] = selected_model
    session = requests.Session()
    session.trust_env = False
    shared_adapter = _shared_http_adapter(args)
    session.mount("http://", shared_adapter)
    session.mount("https://", shared_adapter)
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
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            raise ModelHTTPError(status, detail or response.reason, retry_after)
        try:
            raw = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ModelTransportError(f"invalid JSON response: {exc}") from exc
        return payload, raw, status, len(response_bytes)
    except requests.RequestException as exc:
        raise ModelTransportError(f"{type(exc).__name__}: {exc}") from exc
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
    """Call the configured chat API and save its complete request trajectory."""
    gate = getattr(args, "request_gate", None)
    if gate is None:
        gate = AdaptiveRequestGate(
            getattr(args, "model_concurrency", 32),
            getattr(args, "max_global_backoff", 120.0),
        )
        args.request_gate = gate
    router = getattr(args, "model_router", None)
    if router is None:
        model_pool = parse_model_pool(getattr(args, "model_pool", ""), args.model)
        router = AdaptiveModelRouter(model_pool)
        args.model_router = router
    session_id = str(uuid.uuid4())
    trajectory = {
        "case_id": case_id,
        "phase": phase,
        "session_id": session_id,
        "session_ids": [session_id],
        "model_pool": router.models,
        "attempts": [],
    }
    error, attempt = "", 0
    selected_model = None
    reservation_active = False
    gate_wait_started = time.time()
    await gate.acquire()
    gate_wait_seconds = time.time() - gate_wait_started
    try:
        while True:
            attempt += 1
            started = time.time()
            started_at_utc = utc_timestamp()
            try:
                route_wait_started = time.time()
                if selected_model is None:
                    selected_model = await router.choose(phase)
                    reservation_active = True
                else:
                    # Keep the same logical request on its initially selected
                    # model, keeping retries on the originally selected model.
                    await router.begin_fixed_retry(selected_model)
                route_wait_seconds = time.time() - route_wait_started
                timestamped_write(
                    f"[model-start] phase={phase} case={case_id} model={selected_model} "
                    f"attempt={attempt} gate_wait={gate_wait_seconds:.3f}s "
                    f"route_wait={route_wait_seconds:.3f}s"
                )
                request_payload, raw_response, http_status, response_bytes = await asyncio.to_thread(
                    call_chat_api, prompt, args, session_id, selected_model
                )
                elapsed = time.time() - started
                text = response_text(raw_response)
                value = validator(extract_json_object(text))
                await router.success(selected_model, phase, elapsed)
                reservation_active = False
                await gate.success()
                timestamped_write(
                    f"[model-return] phase={phase} case={case_id} model={selected_model} "
                    f"status={http_status} attempt={attempt} elapsed={elapsed:.2f}s "
                    f"bytes={response_bytes}"
                )
                trajectory["attempts"].append(
                    {
                        "attempt": attempt,
                        "status": "accepted",
                        "started_at_utc": started_at_utc,
                        "duration_seconds": round(elapsed, 3),
                        "session_id": session_id,
                        "model": selected_model,
                        "http_status": http_status,
                        "response_bytes": response_bytes,
                        "messages": request_payload["messages"] + [{"role": "assistant", "content": text}],
                        "response": raw_response,
                    }
                )
                return value, trajectory
            except Exception as exc:
                error = str(exc)
                http_status = exc.status_code if isinstance(exc, ModelHTTPError) else None
                retry_after = exc.retry_after if isinstance(exc, ModelHTTPError) else None
                transport_failure = isinstance(exc, (ModelHTTPError, ModelTransportError))
                validation_failure = not transport_failure
                will_retry = args.retry_forever or attempt <= args.retries
                if selected_model is not None:
                    await router.failure(
                        selected_model,
                        phase,
                        status_code=http_status,
                        detail=error,
                        validation_failure=validation_failure,
                        release_inflight=not will_retry,
                    )
                    if not will_retry:
                        reservation_active = False
                trajectory["attempts"].append(
                    {
                        "attempt": attempt,
                        "status": "failed",
                        "started_at_utc": started_at_utc,
                        "error": error,
                        "duration_seconds": round(time.time() - started, 3),
                        "session_id": session_id,
                        "model": selected_model,
                        "http_status": http_status,
                        "retry_after": retry_after,
                    }
                )
                if not args.retry_forever and attempt > args.retries:
                    raise
                if transport_failure:
                    if retry_after is not None:
                        delay = min(args.max_global_backoff, max(0.0, retry_after))
                    else:
                        delay = min(
                            args.max_global_backoff,
                            0.5 * (2 ** min(attempt - 1, 8)),
                        )
                    retry_note = "fixed_model_payload_infinite_retry"
                else:
                    delay = min(
                        args.max_global_backoff,
                        args.retry_delay + random.uniform(
                            0.0, min(5.0, args.retry_delay * 0.2)
                        ),
                    )
                    retry_note = "fixed_model_payload_validation_retry"
                retry_total = "infinite" if args.retry_forever else str(args.retries)
                timestamped_write(
                    f"[{phase}] {case_id} model={selected_model} failed: {error}; "
                    f"retry {attempt}/{retry_total} in {delay:g}s; {retry_note}"
                )
                await asyncio.sleep(delay)
    finally:
        if reservation_active and selected_model is not None:
            await router.release(selected_model)
        gate.release()
