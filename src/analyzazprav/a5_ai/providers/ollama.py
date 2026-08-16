from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Mapping

from .base import ProviderError, ProviderTimeout, ProviderUnavailable


class OllamaProvider:
    def __init__(self, model_name: str, *, base_url: str = "http://localhost:11434", timeout_seconds: float = 120.0) -> None:
        self._model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model_name

    def analyze(self, *, system_prompt: str, user_prompt: str) -> Mapping[str, Any]:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ConnectionError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeout(f"Ollama request timed out: {reason}") from exc
            raise ProviderUnavailable(f"Ollama is unavailable at {self.base_url}: {reason}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeout(f"Ollama request timed out: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("Ollama returned invalid JSON envelope") from exc
        try:
            content = body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderError("Ollama response did not contain message.content") from exc
        if isinstance(content, Mapping):
            return content
        if not isinstance(content, str):
            raise ProviderError("Ollama message.content was not a string or object")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderError("Ollama model output was not valid JSON") from exc
        if not isinstance(parsed, Mapping):
            raise ProviderError("Ollama model output must be a JSON object")
        return parsed
