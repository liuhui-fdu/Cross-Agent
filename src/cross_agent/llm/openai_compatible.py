"""Minimal OpenAI-compatible chat completions client."""

from __future__ import annotations

import json
import http.client
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class OpenAICompatibleChatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 60,
        max_retries: int = 4,
        retry_base_seconds: float = 2.0,
    ):
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._retry_base_seconds = max(0.0, retry_base_seconds)

    def complete(
        self,
        messages: List[ChatMessage],
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload = {
            "model": self._model,
            "messages": [message.__dict__ for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = json.dumps(payload).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            request = urllib.request.Request(
                self._base_url,
                data=data,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self._timeout_seconds,
                ) as response:
                    body = response.read().decode("utf-8")
                return self._parse_content(json.loads(body))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(
                    f"chat completion failed: HTTP {exc.code}: {detail}"
                )
                if exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                    break
            except urllib.error.URLError as exc:
                last_error = RuntimeError(f"chat completion failed: {exc.reason}")
            except (http.client.HTTPException, TimeoutError, ConnectionError, OSError) as exc:
                last_error = RuntimeError(f"chat completion failed: {exc}")
            except (RuntimeError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < self._max_retries:
                time.sleep(self._retry_base_seconds * (2**attempt))
        raise RuntimeError(str(last_error or "chat completion failed")) from last_error

    def _parse_content(self, response: Dict[str, Any]) -> str:
        choices = response.get("choices") or []
        if not choices:
            raise RuntimeError(f"chat completion returned no choices: {response}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not content:
            raise RuntimeError(f"chat completion returned empty content: {response}")
        return str(content).strip()
