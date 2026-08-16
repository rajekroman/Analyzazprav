from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from a6.a5_bridge import a5_available, run_local_a5
from a6.data import analysis_packet, demo_messages


def test_a5_available_returns_boolean():
    assert isinstance(a5_available(), bool)


def test_run_local_a5_uses_packet_adapter_and_returns_execution(monkeypatch):
    package = ModuleType("analyzazprav")
    a5 = ModuleType("analyzazprav.a5_ai")
    providers = ModuleType("analyzazprav.a5_ai.providers")

    class FakePacketSource:
        @classmethod
        def from_packet(cls, packet):
            assert packet["schema_version"] == 1
            return cls()

    class FakeBuilder:
        def __init__(self, source):
            assert isinstance(source, FakePacketSource)

    class FakeProvider:
        def __init__(self, model_name, *, base_url):
            assert model_name == "test-model"
            assert base_url == "http://localhost:11434"

    class FakeResult:
        def to_dict(self):
            return {"summary": "ok", "overall_confidence": 0.9}

    class FakeAnalyzer:
        def __init__(self, *, context_builder, provider, cache):
            assert isinstance(context_builder, FakeBuilder)
            assert isinstance(provider, FakeProvider)
            assert cache is None

        def analyze(self, request, candidate):
            assert request == "request"
            assert candidate == "candidate"
            return SimpleNamespace(
                status=SimpleNamespace(value="completed"),
                context_hash="hash-1",
                error=None,
                result=FakeResult(),
            )

    def fake_candidate(packet):
        return "candidate"

    def fake_request(packet, *, analysis_type, mode, user_question):
        assert analysis_type == "segment"
        assert mode == "blind"
        assert user_question == "question"
        return "request"

    a5.A6PacketMessageSource = FakePacketSource
    a5.AIAnalyzer = FakeAnalyzer
    a5.AnalysisMode = lambda value: value
    a5.AnalysisType = lambda value: value
    a5.ContextBuilder = FakeBuilder
    a5.candidate_from_a6_packet = fake_candidate
    a5.request_from_a6_packet = fake_request
    providers.OllamaProvider = FakeProvider

    monkeypatch.setitem(sys.modules, "analyzazprav", package)
    monkeypatch.setitem(sys.modules, "analyzazprav.a5_ai", a5)
    monkeypatch.setitem(sys.modules, "analyzazprav.a5_ai.providers", providers)

    execution = run_local_a5(
        {"schema_version": 1},
        model_name="test-model",
        analysis_type="segment",
        mode="blind",
        user_question="question",
    )
    assert execution == {
        "status": "completed",
        "context_hash": "hash-1",
        "error": None,
        "result": {"summary": "ok", "overall_confidence": 0.9},
    }


def test_real_a5_accepts_current_a6_packet_when_package_is_composed():
    """Composition contract test.

    This intentionally skips on the standalone A6 branch where A5 is not yet
    installed. In an A6-over-A5 merge-ref it must execute against the real A5
    adapter and therefore turns package/API drift into a CI failure.
    """
    a5 = pytest.importorskip("analyzazprav.a5_ai")

    frame = demo_messages()
    selected_id = str(frame.iloc[5].message_id)
    packet = analysis_packet(frame, [selected_id], context_before=2, context_after=2)

    source = a5.A6PacketMessageSource.from_packet(packet)
    candidate = a5.candidate_from_a6_packet(packet)
    request = a5.request_from_a6_packet(
        packet,
        analysis_type=a5.AnalysisType.SEGMENT,
        mode=a5.AnalysisMode.BLIND,
        user_question="composition-check",
    )

    assert len(source.messages) == packet["message_count"]
    assert candidate.conversation_id == frame.iloc[5].conversation_id
    assert candidate.evidence_message_ids == (selected_id,)
    assert request.conversation_id == candidate.conversation_id
    assert request.candidate_id == candidate.id
    assert request.user_question == "composition-check"
