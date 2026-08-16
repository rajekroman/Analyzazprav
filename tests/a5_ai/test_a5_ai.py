from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.a5_ai import (
    AIAnalysisRequest,
    AIAnalyzer,
    AnalysisCache,
    AnalysisCandidate,
    AnalysisMode,
    AnalysisStatus,
    AnalysisType,
    CandidateDisposition,
    CandidateSelector,
    ContextBuilder,
    MessageRecord,
)
from analyzazprav.a5_ai.providers import SequenceProvider, StaticProvider
from analyzazprav.a5_ai.router import QueryRoute, route_question

UTC = timezone.utc
BASE = datetime(2025, 5, 10, 12, 0, tzinfo=UTC)


class MemorySource:
    def __init__(self, messages):
        self.messages = list(messages)

    def list_messages(self, conversation_id, start_ts, end_ts):
        return [
            message
            for message in self.messages
            if message.conversation_id == conversation_id
            and start_ts <= message.timestamp <= end_ts
        ]


def msg(
    i: int,
    *,
    hours: int = 0,
    participant: str = "p1",
    conversation: str = "c1",
) -> MessageRecord:
    return MessageRecord(
        id=f"m{i}",
        conversation_id=conversation,
        participant_id=participant,
        timestamp=BASE + timedelta(hours=hours),
        text=f"message {i}",
    )


def valid_payload(message_id="m1"):
    evidence = {"message_ids": [message_id], "description": "Direct message"}
    return {
        "summary": {
            "text": "Evidence-backed summary",
            "confidence": 0.72,
            "evidence": evidence,
        },
        "observations": [
            {"text": "Observed behavior", "evidence": evidence, "strength": 0.9}
        ],
        "interpretations": [
            {
                "text": "Cautious interpretation",
                "evidence_message_ids": [message_id],
                "confidence": 0.7,
            }
        ],
        "patterns": [],
        "turning_points": [],
        "participant_p1": None,
        "participant_p2": None,
        "shared_dynamic": None,
        "alternative_explanations": [
            "External workload could also explain this."
        ],
        "unknowns": ["External context is unavailable."],
        "overall_confidence": 0.72,
    }


class CandidateSelectorTests(unittest.TestCase):
    def candidate(self, score, manual=False, start=BASE, end=None, ident="c"):
        return AnalysisCandidate(
            id=ident,
            conversation_id="conv",
            start_ts=start,
            end_ts=end or start + timedelta(hours=10),
            candidate_type="conflict",
            importance_score=score,
            manual_request=manual,
        )

    def test_thresholds(self):
        selector = CandidateSelector()
        self.assertEqual(
            selector.decide(self.candidate(20)).disposition,
            CandidateDisposition.IGNORE,
        )
        self.assertEqual(
            selector.decide(self.candidate(70)).disposition,
            CandidateDisposition.SUGGEST,
        )
        self.assertEqual(
            selector.decide(self.candidate(90)).disposition,
            CandidateDisposition.ANALYZE,
        )

    def test_manual_override(self):
        self.assertEqual(
            CandidateSelector().decide(self.candidate(1, manual=True)).disposition,
            CandidateDisposition.ANALYZE,
        )

    def test_merges_heavily_overlapping_same_type(self):
        selector = CandidateSelector(merge_overlap_threshold=0.7)
        first = self.candidate(
            70, ident="a", start=BASE, end=BASE + timedelta(hours=10)
        )
        second = self.candidate(
            85,
            ident="b",
            start=BASE + timedelta(hours=2),
            end=BASE + timedelta(hours=11),
        )
        merged = selector.merge_overlapping([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].importance_score, 85)
        self.assertEqual(merged[0].start_ts, BASE)
        self.assertEqual(merged[0].end_ts, BASE + timedelta(hours=11))


class ContextBuilderTests(unittest.TestCase):
    def test_blind_mode_physically_excludes_future_messages(self):
        source = MemorySource(
            [msg(1, hours=-1), msg(2, hours=1), msg(3, hours=30)]
        )
        request = AIAnalysisRequest(
            conversation_id="c1",
            analysis_type=AnalysisType.CONFLICT,
            start_ts=BASE - timedelta(hours=2),
            end_ts=BASE + timedelta(hours=2),
            mode=AnalysisMode.BLIND,
        )
        context = ContextBuilder(source).build(request)
        self.assertEqual([message.id for message in context.messages], ["m1", "m2"])
        self.assertTrue(
            all(message.timestamp <= request.end_ts for message in context.messages)
        )

    def test_retrospective_mode_includes_post_window(self):
        source = MemorySource([msg(1, hours=1), msg(2, hours=10)])
        request = AIAnalysisRequest(
            conversation_id="c1",
            analysis_type=AnalysisType.CONFLICT,
            start_ts=BASE,
            end_ts=BASE + timedelta(hours=2),
            mode=AnalysisMode.RETROSPECTIVE,
        )
        self.assertEqual(
            [
                message.id
                for message in ContextBuilder(source).build(request).messages
            ],
            ["m1", "m2"],
        )

    def test_reduction_preserves_evidence_and_chronology(self):
        source = MemorySource([msg(i, hours=i) for i in range(50)])
        builder = ContextBuilder(source, max_messages=10, evidence_radius=2)
        candidate = AnalysisCandidate(
            id="cand",
            conversation_id="c1",
            start_ts=BASE,
            end_ts=BASE + timedelta(hours=49),
            candidate_type="change_point",
            importance_score=90,
            evidence_message_ids=("m25",),
        )
        request = AIAnalysisRequest(
            conversation_id="c1",
            analysis_type=AnalysisType.CHANGE_POINT,
            start_ts=BASE,
            end_ts=BASE + timedelta(hours=49),
            mode=AnalysisMode.RETROSPECTIVE,
        )
        context = builder.build(request, candidate)
        self.assertEqual(context.evidence_message_ids, ("m25",))
        self.assertIn("m25", [message.id for message in context.messages])
        self.assertLessEqual(len(context.messages), 10)
        self.assertEqual(
            context.messages,
            tuple(
                sorted(context.messages, key=lambda message: (message.timestamp, message.id))
            ),
        )

    def test_missing_candidate_evidence_fails_closed(self):
        source = MemorySource([msg(1), msg(2, hours=1)])
        candidate = AnalysisCandidate(
            id="cand",
            conversation_id="c1",
            start_ts=BASE,
            end_ts=BASE + timedelta(hours=1),
            candidate_type="conflict",
            importance_score=90,
            evidence_message_ids=("m1", "m999"),
        )
        request = AIAnalysisRequest(
            conversation_id="c1",
            analysis_type=AnalysisType.CONFLICT,
            start_ts=BASE,
            end_ts=BASE + timedelta(hours=1),
            mode=AnalysisMode.RETROSPECTIVE,
        )
        with self.assertRaisesRegex(ValueError, "candidate evidence is missing"):
            ContextBuilder(source).build(request, candidate)

    def test_blind_mode_rejects_candidate_evidence_from_the_future(self):
        source = MemorySource([msg(1), msg(2, hours=3)])
        candidate = AnalysisCandidate(
            id="cand",
            conversation_id="c1",
            start_ts=BASE,
            end_ts=BASE + timedelta(hours=3),
            candidate_type="conflict",
            importance_score=90,
            evidence_message_ids=("m2",),
        )
        request = AIAnalysisRequest(
            conversation_id="c1",
            analysis_type=AnalysisType.CONFLICT,
            start_ts=BASE,
            end_ts=BASE + timedelta(hours=1),
            mode=AnalysisMode.BLIND,
        )
        with self.assertRaisesRegex(ValueError, "candidate evidence is missing"):
            ContextBuilder(source).build(request, candidate)

    def test_evidence_larger_than_context_limit_fails_instead_of_dropping_ids(self):
        source = MemorySource([msg(i, hours=i) for i in range(4)])
        candidate = AnalysisCandidate(
            id="cand",
            conversation_id="c1",
            start_ts=BASE,
            end_ts=BASE + timedelta(hours=3),
            candidate_type="change_point",
            importance_score=90,
            evidence_message_ids=("m0", "m1", "m2", "m3"),
        )
        request = AIAnalysisRequest(
            conversation_id="c1",
            analysis_type=AnalysisType.CHANGE_POINT,
            start_ts=BASE,
            end_ts=BASE + timedelta(hours=3),
            mode=AnalysisMode.RETROSPECTIVE,
        )
        with self.assertRaisesRegex(ValueError, "evidence alone exceeds"):
            ContextBuilder(source, max_messages=3).build(request, candidate)

    def test_duplicate_message_ids_from_source_fail_closed(self):
        duplicate = msg(1)
        source = MemorySource([duplicate, duplicate])
        request = AIAnalysisRequest(
            conversation_id="c1",
            analysis_type=AnalysisType.SEGMENT,
            start_ts=BASE - timedelta(hours=1),
            end_ts=BASE + timedelta(hours=1),
        )
        with self.assertRaisesRegex(ValueError, "duplicate message IDs"):
            ContextBuilder(source).build(request)


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.builder = ContextBuilder(
            MemorySource([msg(1), msg(2, hours=1, participant="p2")])
        )
        self.request = AIAnalysisRequest(
            conversation_id="c1",
            analysis_type=AnalysisType.SEGMENT,
            start_ts=BASE - timedelta(hours=1),
            end_ts=BASE + timedelta(hours=2),
            mode=AnalysisMode.BLIND,
        )

    def test_valid_result_completes(self):
        execution = AIAnalyzer(
            context_builder=self.builder,
            provider=StaticProvider(valid_payload("m1")),
        ).analyze(self.request)
        self.assertEqual(execution.status, AnalysisStatus.COMPLETED)

    def test_fake_message_reference_fails_validation(self):
        provider = SequenceProvider(
            [valid_payload("not-supplied"), valid_payload("not-supplied")]
        )
        execution = AIAnalyzer(
            context_builder=self.builder, provider=provider
        ).analyze(self.request)
        self.assertEqual(execution.status, AnalysisStatus.FAILED_VALIDATION)
        self.assertEqual(provider.calls, 2)

    def test_one_repair_attempt_can_recover_invalid_output(self):
        provider = SequenceProvider(
            [valid_payload("not-supplied"), valid_payload("m1")]
        )
        execution = AIAnalyzer(
            context_builder=self.builder, provider=provider
        ).analyze(self.request)
        self.assertEqual(execution.status, AnalysisStatus.COMPLETED)
        self.assertEqual(provider.calls, 2)

    def test_repair_attempt_stops_after_second_invalid_output(self):
        invalid = valid_payload("not-supplied")
        provider = SequenceProvider([invalid, invalid])
        execution = AIAnalyzer(
            context_builder=self.builder, provider=provider
        ).analyze(self.request)
        self.assertEqual(execution.status, AnalysisStatus.FAILED_VALIDATION)
        self.assertEqual(provider.calls, 2)

    def test_cache_prevents_second_provider_call(self):
        provider = StaticProvider(valid_payload("m1"))
        with tempfile.TemporaryDirectory() as tmp:
            analyzer = AIAnalyzer(
                context_builder=self.builder,
                provider=provider,
                cache=AnalysisCache(Path(tmp) / "a5.sqlite3"),
            )
            self.assertEqual(
                analyzer.analyze(self.request).status, AnalysisStatus.COMPLETED
            )
            self.assertEqual(
                analyzer.analyze(self.request).status, AnalysisStatus.CACHE_HIT
            )
            self.assertEqual(provider.calls, 1)

    def test_force_refresh_calls_provider_again(self):
        provider = StaticProvider(valid_payload("m1"))
        with tempfile.TemporaryDirectory() as tmp:
            analyzer = AIAnalyzer(
                context_builder=self.builder,
                provider=provider,
                cache=AnalysisCache(Path(tmp) / "a5.sqlite3"),
            )
            analyzer.analyze(self.request)
            forced = AIAnalysisRequest(
                conversation_id=self.request.conversation_id,
                analysis_type=self.request.analysis_type,
                start_ts=self.request.start_ts,
                end_ts=self.request.end_ts,
                mode=self.request.mode,
                force_refresh=True,
            )
            analyzer.analyze(forced)
            self.assertEqual(provider.calls, 2)

    def test_missing_candidate_evidence_stops_before_provider_call(self):
        provider = StaticProvider(valid_payload("m1"))
        candidate = AnalysisCandidate(
            id="cand",
            conversation_id="c1",
            start_ts=BASE,
            end_ts=BASE + timedelta(hours=1),
            candidate_type="conflict",
            importance_score=90,
            evidence_message_ids=("missing",),
        )
        with self.assertRaisesRegex(ValueError, "candidate evidence is missing"):
            AIAnalyzer(context_builder=self.builder, provider=provider).analyze(
                self.request, candidate
            )
        self.assertEqual(provider.calls, 0)


class RouterTests(unittest.TestCase):
    def test_data_route(self):
        self.assertEqual(
            route_question("Kdo více inicioval komunikaci?"), QueryRoute.DATA
        )

    def test_combined_route(self):
        self.assertEqual(
            route_question("Kdy se změnila komunikace a proč?"), QueryRoute.COMBINED
        )

    def test_interpretive_route(self):
        self.assertEqual(
            route_question("Jaký vzorec se opakoval během konfliktů?"),
            QueryRoute.INTERPRETIVE,
        )


if __name__ == "__main__":
    unittest.main()
