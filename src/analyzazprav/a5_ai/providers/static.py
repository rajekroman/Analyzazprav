from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


class StaticProvider:
    """Deterministic provider for tests and offline integration checks."""

    def __init__(self, payload: Mapping[str, Any], model_name: str = "static-test") -> None:
        self.payload = dict(payload)
        self._model_name = model_name
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "static"

    @property
    def model_name(self) -> str:
        return self._model_name

    def analyze(self, *, system_prompt: str, user_prompt: str) -> Mapping[str, Any]:
        self.calls += 1
        return deepcopy(self.payload)


class SequenceProvider:
    """Deterministic provider that returns queued payloads in order."""

    def __init__(self, payloads, model_name: str = "sequence-test") -> None:
        self.payloads = [deepcopy(dict(payload)) for payload in payloads]
        self._model_name = model_name
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "sequence"

    @property
    def model_name(self) -> str:
        return self._model_name

    def analyze(self, *, system_prompt: str, user_prompt: str) -> Mapping[str, Any]:
        if self.calls >= len(self.payloads):
            raise RuntimeError("SequenceProvider exhausted")
        payload = deepcopy(self.payloads[self.calls])
        self.calls += 1
        return payload
