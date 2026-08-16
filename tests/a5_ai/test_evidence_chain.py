from __future__ import annotations

import json
import sqlite3
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
    ContextBuilder,
    MessageRecord,
)
from analyzazprav.a5_ai.providers import StaticProvider
from analyzazprav.a5_ai.validator import ValidationError, parse_and_validate_result

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


def payload(*, metric_ref=None, include_assertions=False):
    evidence = {
        "message_ids": ["m1"],
        "description": "Observed directly in supplied message",
    }
    if metric_ref is not None:
        evidence["metric_refs"] = [metric_ref]
    result = {
        "summary": {
            "text": "Summary",
            "confidence": 0.72,
            "evidence": evidence,
        },
        "observations": [
            {
                "text": "Observed behavior",
                "evidence": evidence,
                "strength": 0.9,
            }
        ],
        "interpretations": [
            {
                "text": "Cautious interpretation",
                "evidence_message_ids": ["m1"],
                "confidence": 0.7,
            }
        ],
        "patterns": [],
        "turning_points": [],
        "participant_p1": None,
        "participant_p2": None,
        "shared_dynamic": None,
        "alternative_explanations": ["Another explanation is possible."],
        "unknowns": ["External context is unknown."],
        "overall_confidence": 0.72,
    }
    if include_assertions:
        result["turning_points"] = [
            {
                "text": "A turning point",
                "confidence": 0.8,
                "evidence": evidence,
            }
        ]
        result["participant_p1"] = {
            "text": "P1 conclusion",
            "confidence": 0.6,
            "evidence": evidence,
        }
        result["shared_dynamic"] = {
            "text": "Shared dynamic",
            "confidence": 0.65,
            "evidence": evidence,
        }
    return result


class EvidenceChainTests(unittest.TestCase):
    def setUp(self):
        self.message = MessageRecord(
            id="m1",
            conversation_id="c1",
            participant_id="p2",
            timestamp=BASE,
            text="  This   is a source message with normalized whitespace.  ",
        )
        self.source = MemorySource([self.message])
        self.request = AIAnalysisRequest(
            conversation_id="c1",
            analysis_type=AnalysisType.SEGMENT,
            start_ts=BASE - timedelta(hours=1),
            end_ts=BASE + timedelta(hours=1),
            mode=AnalysisMode.BLIND,
        )

    def context(self, candidate=None):
        return ContextBuilder(self.source).build(self.request, candidate)

    def test_validator_enriches_message_id_from_source_context(self):
        result = parse_and_validate_result(payload(), self.context())
        evidence = result.observations[0].evidence
        self.assertEqual(evidence.message_ids, ("m1",))
        self.assertEqual(evidence.messages[0].message_id, "m1")
        self.assertEqual(evidence.messages[0].timestamp, BASE.isoformat())
        self.assertEqual(evidence.messages[0].sender_id, "p2")
        self.assertEqual(
            evidence.messages[0].excerpt,
            "This is a source message with normalized whitespace.",
        )
        self.assertEqual(
            result.interpretations[0].evidence.messages[0].message_id,
            "m1",
        )
        self.assertEqual(result.summary, "Summary")
        self.assertEqual(result.summary_evidence.messages[0].message_id, "m1")

    def test_assertion_bearing_synthesis_fields_require_and_preserve_evidence(self):
        result = parse_and_validate_result(
            payload(include_assertions=True), self.context()
        )
        self.assertEqual(result.turning_points, ("A turning point",))
        self.assertEqual(result.turning_point_evidence[0].message_ids, ("m1",))
        self.assertEqual(result.participant_p1, "P1 conclusion")
        self.assertEqual(
            result.participant_p1_evidence.messages[0].sender_id, "p2"
        )
        self.assertEqual(result.shared_dynamic, "Shared dynamic")
        self.assertEqual(result.shared_dynamic_evidence.message_ids, ("m1",))

    def test_unreferenced_summary_is_rejected(self):
        invalid = payload()
        invalid["summary"] = "unreferenced text"
        with self.assertRaises(ValidationError):
            parse_and_validate_result(invalid, self.context())

    def test_metric_reference_is_resolved_to_deterministic_value(self):
        candidate = AnalysisCandidate(
            id="cand",
            conversation_id="c1",
            start_ts=BASE,
            end_ts=BASE,
            candidate_type="change_point",
            importance_score=90,
            evidence_message_ids=("m1",),
            metrics_during={"median_response_latency_seconds": 42.0},
        )
        context = self.context(candidate)
        result = parse_and_validate_result(
            payload(
                metric_ref={
                    "phase": "during",
                    "name": "median_response_latency_seconds",
                }
            ),
            context,
        )
        metric = result.observations[0].evidence.metrics[0]
        self.assertEqual(metric.phase, "during")
        self.assertEqual(metric.name, "median_response_latency_seconds")
        self.assertEqual(metric.value, 42.0)
        self.assertEqual(result.summary_evidence.metrics[0].value, 42.0)

    def test_unknown_metric_reference_is_rejected(self):
        with self.assertRaises(ValidationError):
            parse_and_validate_result(
                payload(
                    metric_ref={"phase": "during", "name": "invented_metric"}
                ),
                self.context(),
            )

    def test_cache_roundtrip_preserves_full_evidence_chain(self):
        provider = StaticProvider(payload(include_assertions=True))
        with tempfile.TemporaryDirectory() as tmp:
            analyzer = AIAnalyzer(
                context_builder=ContextBuilder(self.source),
                provider=provider,
                cache=AnalysisCache(Path(tmp) / "a5.sqlite3"),
            )
            first = analyzer.analyze(self.request)
            second = analyzer.analyze(self.request)
            self.assertEqual(first.status, AnalysisStatus.COMPLETED)
            self.assertEqual(second.status, AnalysisStatus.CACHE_HIT)
            cached = second.result.observations[0].evidence.messages[0]
            self.assertEqual(cached.message_id, "m1")
            self.assertEqual(cached.timestamp, BASE.isoformat())
            self.assertEqual(cached.sender_id, "p2")
            self.assertEqual(
                second.result.summary_evidence.message_ids, ("m1",)
            )
            self.assertEqual(
                second.result.turning_point_evidence[0].message_ids, ("m1",)
            )
            self.assertEqual(
                second.result.shared_dynamic_evidence.message_ids, ("m1",)
            )
            self.assertEqual(provider.calls, 1)

    def test_corrupted_cached_excerpt_is_rejected_and_recomputed(self):
        provider = StaticProvider(payload(include_assertions=True))
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "a5.sqlite3"
            analyzer = AIAnalyzer(
                context_builder=ContextBuilder(self.source),
                provider=provider,
                cache=AnalysisCache(cache_path),
            )
            first = analyzer.analyze(self.request)
            self.assertEqual(first.status, AnalysisStatus.COMPLETED)
            self.assertEqual(provider.calls, 1)

            with sqlite3.connect(cache_path) as conn:
                row = conn.execute(
                    "SELECT context_hash, result_json FROM ai_analysis"
                ).fetchone()
                cached = json.loads(row[1])
                cached["summary_evidence"]["messages"][0]["excerpt"] = (
                    "tampered excerpt"
                )
                conn.execute(
                    "UPDATE ai_analysis SET result_json=? WHERE context_hash=?",
                    (json.dumps(cached), row[0]),
                )
                conn.commit()

            second = analyzer.analyze(self.request)
            self.assertEqual(second.status, AnalysisStatus.COMPLETED)
            self.assertEqual(provider.calls, 2)
            self.assertEqual(
                second.result.summary_evidence.messages[0].excerpt,
                "This is a source message with normalized whitespace.",
            )

    def test_corrupted_cached_message_id_is_rejected_and_recomputed(self):
        provider = StaticProvider(payload())
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "a5.sqlite3"
            analyzer = AIAnalyzer(
                context_builder=ContextBuilder(self.source),
                provider=provider,
                cache=AnalysisCache(cache_path),
            )
            first = analyzer.analyze(self.request)
            self.assertEqual(first.status, AnalysisStatus.COMPLETED)

            with sqlite3.connect(cache_path) as conn:
                row = conn.execute(
                    "SELECT context_hash, result_json FROM ai_analysis"
                ).fetchone()
                cached = json.loads(row[1])
                cached["observations"][0]["evidence"]["message_ids"] = ["ghost"]
                cached["observations"][0]["evidence"]["messages"][0][
                    "message_id"
                ] = "ghost"
                conn.execute(
                    "UPDATE ai_analysis SET result_json=? WHERE context_hash=?",
                    (json.dumps(cached), row[0]),
                )
                conn.commit()

            second = analyzer.analyze(self.request)
            self.assertEqual(second.status, AnalysisStatus.COMPLETED)
            self.assertEqual(provider.calls, 2)
            self.assertEqual(
                second.result.observations[0].evidence.message_ids, ("m1",)
            )


if __name__ == "__main__":
    unittest.main()
