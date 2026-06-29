"""OpenAI-compatible embedding client with retries and bounded concurrency."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from cross_agent.config import EmbeddingConfig


class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str:
        ...

    @property
    def dimensions(self) -> int:
        ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


class OpenAICompatibleEmbeddingClient:
    def __init__(self, config: EmbeddingConfig, api_key: str):
        self._config = config
        self._api_key = api_key

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def dimensions(self) -> int:
        return self._config.dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        bounded = [text[: self._config.max_input_chars] for text in texts]
        workers = max(1, min(self._config.max_workers, len(bounded)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            return list(executor.map(self._embed_one, bounded))

    def _embed_one(self, text: str) -> list[float]:
        payload = json.dumps(
            {"model": self._config.model, "input": text},
            ensure_ascii=False,
        ).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self._config.max_retries + 1):
            request = urllib.request.Request(
                self._config.base_url,
                data=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self._config.timeout_seconds,
                ) as response:
                    body = json.loads(response.read().decode("utf-8"))
                return self._parse_vector(body)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(
                    f"embedding request failed: HTTP {exc.code}: {detail}"
                )
                if exc.code not in {429, 500, 502, 503, 504}:
                    break
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
                last_error = exc
            if attempt < self._config.max_retries:
                time.sleep(0.5 * (2**attempt))
        raise RuntimeError(f"embedding request failed: {last_error}") from last_error

    def _parse_vector(self, response: dict[str, Any]) -> list[float]:
        rows = response.get("data") or []
        if not rows:
            raise ValueError(f"embedding response has no data: {response}")
        values = rows[0].get("embedding")
        if not isinstance(values, list):
            raise ValueError("embedding response is missing vector values")
        vector = [float(value) for value in values]
        if len(vector) != self._config.dimensions:
            raise ValueError(
                f"embedding dimension mismatch: expected {self._config.dimensions}, "
                f"got {len(vector)}"
            )
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            raise ValueError("embedding response contains a zero vector")
        return [value / norm for value in vector]
