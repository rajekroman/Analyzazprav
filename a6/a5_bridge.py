from __future__ import annotations

from typing import Any, Mapping


class A5Unavailable(RuntimeError):
    pass


def a5_available() -> bool:
    try:
        import analyzazprav.a5_ai  # noqa: F401
    except ImportError:
        return False
    return True


def run_local_a5(
    packet: Mapping[str, Any],
    *,
    model_name: str,
    base_url: str = "http://localhost:11434",
    analysis_type: str = "segment",
    mode: str = "blind",
    user_question: str | None = None,
) -> dict[str, Any]:
    """Run A5 explicitly through local Ollama only.

    The A5 packet adapter validates membership and source provenance before the
    provider is constructed. No cloud fallback is attempted.
    """

    try:
        from analyzazprav.a5_ai import (
            A6PacketMessageSource,
            AIAnalyzer,
            AnalysisMode,
            AnalysisType,
            ContextBuilder,
            candidate_from_a6_packet,
            request_from_a6_packet,
        )
        from analyzazprav.a5_ai.providers import OllamaProvider
    except ImportError as exc:
        raise A5Unavailable("A5 modul není v aktuálním checkoutu nainstalovaný.") from exc

    # All contract validation happens before OllamaProvider/network work.
    source = A6PacketMessageSource.from_packet(packet)
    candidate = candidate_from_a6_packet(packet)
    request = request_from_a6_packet(
        packet,
        analysis_type=AnalysisType(analysis_type),
        mode=AnalysisMode(mode),
        user_question=user_question or None,
    )
    analyzer = AIAnalyzer(
        context_builder=ContextBuilder(source),
        provider=OllamaProvider(model_name, base_url=base_url),
        cache=None,
    )
    execution = analyzer.analyze(request, candidate)
    return {
        "status": execution.status.value,
        "context_hash": execution.context_hash,
        "error": execution.error,
        "result": execution.result.to_dict() if execution.result is not None else None,
    }
