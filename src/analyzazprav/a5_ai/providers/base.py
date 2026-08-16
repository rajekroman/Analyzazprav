from __future__ import annotations

from typing import Any, Mapping, Protocol


class ProviderError(RuntimeError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class ProviderTimeout(ProviderError):
    pass


class AIProvider(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def model_name(self) -> str:
        ...

    def analyze(self, *, system_prompt: str, user_prompt: str) -> Mapping[str, Any]:
        ...
