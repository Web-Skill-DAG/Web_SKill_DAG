#!/usr/bin/env python3
"""Shared, thread-safe OpenAI-compatible transport for Potential Query generation."""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


_HTTP_LOCAL = threading.local()


class APIHTTPError(RuntimeError):
    def __init__(self, status_code: int, detail: str, retry_after: float | None, backoff_seconds: float):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.retry_after = retry_after
        self.backoff_seconds = backoff_seconds


class APITransportError(RuntimeError):
    def __init__(self, detail: str, backoff_seconds: float):
        super().__init__(detail)
        self.status_code = None
        self.retry_after = None
        self.backoff_seconds = backoff_seconds


class AdaptiveRequestGate:
    """Bound real HTTP traffic and coordinate one cooldown across all threads."""

    def __init__(self, concurrency: int, max_backoff: float):
        if concurrency <= 0:
            raise ValueError("request concurrency must be positive")
        self.semaphore = threading.BoundedSemaphore(concurrency)
        self.max_backoff = max(0.0, max_backoff)
        self.cooldown_until = 0.0
        self.failure_streak = 0
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                delay = max(0.0, self.cooldown_until - time.monotonic())
            if delay:
                time.sleep(delay)
            self.semaphore.acquire()
            with self.lock:
                delay = max(0.0, self.cooldown_until - time.monotonic())
            if not delay:
                return
            self.semaphore.release()

    def release(self) -> None:
        self.semaphore.release()

    def success(self) -> None:
        with self.lock:
            self.failure_streak = max(0, self.failure_streak - 1)

    def failure(self, status_code: int | None, retry_after: float | None) -> float:
        with self.lock:
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


@dataclass(frozen=True)
class TransportConfig:
    base_url: str
    api_key: str
    connect_timeout: float = 30.0
    read_timeout: float = 1800.0
    request_concurrency: int = 30
    transport_retries: int = 5
    transport_backoff_factor: float = 0.5
    max_global_backoff: float = 120.0


def _api_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/v1/chat/completions"):
        return value
    if value.endswith("/v1"):
        return value + "/chat/completions"
    return value + "/v1/chat/completions"


def _retry_after_seconds(value: str | None, maximum: float) -> float | None:
    if value is None:
        return None
    try:
        return min(maximum, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


def _session(config: TransportConfig) -> requests.Session:
    key = (config.transport_retries, config.transport_backoff_factor)
    if getattr(_HTTP_LOCAL, "key", None) == key:
        return _HTTP_LOCAL.session
    session = requests.Session()
    session.trust_env = False
    retries = Retry(
        total=config.transport_retries,
        connect=config.transport_retries,
        read=config.transport_retries,
        status=config.transport_retries,
        backoff_factor=config.transport_backoff_factor,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(("POST",)),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    _HTTP_LOCAL.key = key
    _HTTP_LOCAL.session = session
    return session


class ChatTransport:
    def __init__(self, config: TransportConfig):
        self.config = config
        self.gate = AdaptiveRequestGate(config.request_concurrency, config.max_global_backoff)

    def request(self, prompt: str, max_tokens: int, session_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        started = time.time()
        self.gate.acquire()
        try:
            try:
                response = _session(self.config).post(
                    _api_url(self.config.base_url),
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.config.api_key}",
                        "X-Session-ID": session_id,
                    },
                    timeout=(self.config.connect_timeout, self.config.read_timeout),
                )
            except requests.RequestException as exc:
                delay = self.gate.failure(None, None)
                raise APITransportError(f"{type(exc).__name__}: {exc}", delay) from exc
        finally:
            self.gate.release()

        status = int(response.status_code)
        response_bytes = response.content or b""
        elapsed = time.time() - started
        if status != 200:
            detail = response_bytes.decode("utf-8", errors="replace")[:4000]
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"), self.config.max_global_backoff)
            response.close()
            delay = self.gate.failure(status, retry_after)
            raise APIHTTPError(status, detail or response.reason, retry_after, delay)
        try:
            raw = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            delay = self.gate.failure(None, None)
            raise APITransportError(f"invalid JSON response: {exc}", delay) from exc
        finally:
            response.close()
        self.gate.success()
        metadata = {
            "http_status": status,
            "response_bytes": len(response_bytes),
            "duration_seconds": round(elapsed, 3),
            "session_id": session_id,
        }
        return payload, raw, metadata
