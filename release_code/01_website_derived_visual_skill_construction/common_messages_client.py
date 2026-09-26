"""Minimal Anthropic Messages transport used by the released query pipelines.

This keeps model I/O independent of the website-generation runtime.
"""

from __future__ import annotations

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class ClaudeMessagesClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        max_tokens: int = 8192,
        enable_thinking: bool = True,
        effort: str | None = None,
        request_timeout: int = 600,
        rate_limit_retries: int = 0,
        retry_until_success: bool = False,
        transport_retries: int = 3,
        transport_backoff_factor: float = 0.5,
        log_returns: bool = False,
        **_: Any,
    ) -> None:
        if not api_key:
            raise ValueError("An API key is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking
        self.effort = effort
        self.request_timeout = request_timeout
        self.http = requests.Session()
        self.http.trust_env = False
        retries = Retry(
            total=transport_retries,
            connect=transport_retries,
            read=transport_retries,
            status=transport_retries,
            backoff_factor=transport_backoff_factor,
            status_forcelist=(500, 502),
            allowed_methods=frozenset(("POST",)),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retries)
        self.http.mount("http://", adapter)
        self.http.mount("https://", adapter)

    @property
    def messages_url(self) -> str:
        if self.base_url.endswith("/v1/messages"):
            return self.base_url
        if self.base_url.endswith("/v1/chat/completions"):
            return self.base_url.removesuffix("/v1/chat/completions") + "/v1/messages"
        if self.base_url.endswith("/v1"):
            return self.base_url + "/messages"
        return self.base_url + "/v1/messages"

    def _headers(self, enable_cache: bool) -> dict[str, str]:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if self.enable_thinking:
            headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"
        elif enable_cache and "anthropic.com" in self.base_url:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"
        return headers

    def post_single_turn(
        self, prompt: str, enable_cache: bool = True
    ) -> tuple[dict[str, Any], requests.Response]:
        block: dict[str, Any] = {"type": "text", "text": prompt}
        if enable_cache:
            block["cache_control"] = {"type": "ephemeral"}
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": [block]}],
            "max_tokens": self.max_tokens,
        }
        if self.enable_thinking:
            payload["thinking"] = {"type": "adaptive", "display": "summarized"}
        elif not self.model.startswith("claude"):
            payload["thinking"] = {"type": "disabled"}
        if self.effort:
            payload["output_config"] = {"effort": self.effort}
        response = self.http.post(
            self.messages_url,
            headers=self._headers(enable_cache),
            json=payload,
            timeout=self.request_timeout,
        )
        return payload, response
