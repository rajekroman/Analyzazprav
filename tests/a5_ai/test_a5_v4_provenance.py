from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

from analyzazprav.a5_ai import (
    AIAnalysisRequest,
    AIAnalyzer,
    AnalysisCandidate,
    AnalysisContext,
    AnalysisMode,
    AnalysisStatus,
    AnalysisType,
    ContextBuilder,
    MessageRecord,
)
from analyzazprav.a5_ai.integration_a2 import A2SQLiteMessageSource
from analyzazprav.a5_ai.integration_a4_sqlite import A4SQLiteCandidateSource
from analyzazprav.a5_ai.providers import StaticProvider
from analyzazprav.a5_ai.validator import parse_and_validate_result
from analyzazprav.qa import validate_a5_evidence_chain

UTC = timezone.utc
BASE = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


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


def message(index: int) -> MessageRecord:
    return MessageRecord(
        id=f"m{index}",
        membership_id=f"mem{index}",
        conversation_id="c1",
        participant_id="p1" if index % 2 else "p2",
        timestamp=BASE + timedelta(minutes=index),
        text=f"message {index}",
        source_record_keys=(f"source-{index}",),
        source_snapshot_keys=("snapshot-1",),
        source_parser_versions=("parser-v1",),
    )


def payload(message_id: str = "m1", *, with_metric: bool = False):
    metric_refs = (
        [{"phase": "during", "name": "median_response_latency_seconds"}]
        if with_metric
        else []
    )
    evidence = {
        "message_ids": [message_id],
        "description": "source evidence",
        "metric_refs": metric_refs,
    }
    return {
        "summary": {
            "text": "Evidence-backed summary",
            "confidence": 0.8,
            "evidence": evidence,
        },
        "observations": [],
        "interpretations": [],
        "patterns": [],
        "turning_points": [],
        "participant_p1": None,
        "participant_p2": None,
        "shared_dynamic": None,
        "alternative_explanations": ["Other explanations remain possible."],
        "unknowns": ["Intent is not directly observable."],
        "overall_confidence": 0.8,
    }


class ContextReductionAuditTests(unittest.TestCase):
    def test_reduction_records_omissions_and_fingerprint(self):
        source = MemorySource([message(index) for index in range(12)])
        candidate = AnalysisCandidate(
            id="candidate",
            conversation_id="c1",
            start_ts=BASE,
            end_ts=BASE + timedelta(minutes=12),
            candidate_type="change_point",
            importance_score=90,
            evidence_message_ids=("m6",),
        )
        request = AIAnalysisRequest(
            conversation_id="c1",
            analysis_type=AnalysisType.CHANGE_POINT,
            start_ts=BASE,
            end_ts=BASE + timedelta(minutes=12),
            mode=AnalysisMode.RETROSPECTIVE,
        )
        first = ContextBuilder(source, max_messages=5, evidence_radius=1).build(
            request, candidate
        )
        second = ContextBuilder(source, max_messages=5, evidence_radius=1).build(
            request, candidate
        )
        self.assertIn("m6", [item.id for item in first.messages])
        self.assertGreater(first.omitted_message_count, 0)
        self.assertEqual(first.omitted_message_count, len(first.omitted_message_ids))
        self.assertEqual(first.omitted_message_ids_sha256, second.omitted_message_ids_sha256)
        self.assertTrue(any("omitted" in warning.lower() for warning in first.quality_warnings))
        self.assertEqual(first.missing_evidence_message_ids, ())

    def test_evidence_larger_than_nominal_limit_is_never_silently_dropped(self):
        source = MemorySource([message(index) for index in range(8)])
        evidence_ids = tuple(f"m{index}" for index in range(1, 6))
        candidate = AnalysisCandidate(
            id="candidate",
            conversation_id="c1",
            start_ts=BASE,
            end_ts=BASE + timedelta(minutes=8),
            candidate_type="segment",
            importance_score=90,
            evidence_message_ids=evidence_ids,
        )
        request = AIAnalysisRequest(
            conversation_id="c1",
            analysis_type=AnalysisType.SEGMENT,
            start_ts=BASE,
            end_ts=BASE + timedelta(minutes=8),
            mode=AnalysisMode.RETROSPECTIVE,
        )
        context = ContextBuilder(source, max_messages=3, evidence_radius=0).build(
            request, candidate
        )
        selected = {item.id for item in context.messages}
        self.assertTrue(set(evidence_ids).issubset(selected))
        self.assertGreaterEqual(len(context.messages), len(evidence_ids))
        self.assertTrue(any("never silently removed" in warning for warning in context.quality_warnings))

    def test_missing_candidate_evidence_stops_before_provider_call(self):
        source = MemorySource([message(1)])
        candidate = AnalysisCandidate(
            id="candidate",
            conversation_id="c1",
            start_ts=BASE,
            end_ts=BASE + timedelta(minutes=2),
            candidate_type="segment",
            importance_score=90,
            evidence_message_ids=("missing-message",),
        )
        request = AIAnalysisRequest(
            conversation_id="c1",
            analysis_type=AnalysisType.SEGMENT,
            start_ts=BASE,
            end_ts=BASE + timedelta(minutes=2),
            mode=AnalysisMode.RETROSPECTIVE,
        )
        provider = StaticProvider(payload("m1"))
        execution = AIAnalyzer(
            context_builder=ContextBuilder(source),
            provider=provider,
        ).analyze(request, candidate)
        self.assertEqual(execution.status, AnalysisStatus.FAILED_VALIDATION)
        self.assertEqual(provider.calls, 0)
        self.assertIn("missing-message", execution.error or "")


class EvidenceSnapshotAuditTests(unittest.TestCase):
    def context(self) -> AnalysisContext:
        source_message = message(1)
        return AnalysisContext(
            conversation_id="c1",
            analysis_type=AnalysisType.SEGMENT,
            mode=AnalysisMode.RETROSPECTIVE,
            requested_start_ts=BASE,
            requested_end_ts=BASE + timedelta(minutes=2),
            context_start_ts=BASE,
            context_end_ts=BASE + timedelta(minutes=2),
            cutoff_ts=None,
            messages=(source_message,),
            evidence_message_ids=("m1",),
            metrics_during={"median_response_latency_seconds": 60.0},
            candidate_provenance={
                "analytics_run_id": 17,
                "analytics_version": "9",
                "processing_run_id": 11,
                "analysis_signature": "sig-v9",
                "source_fingerprint": "source-fingerprint",
            },
            available_message_count=1,
        )

    def test_validator_enriches_source_and_metric_provenance(self):
        context = self.context()
        result = parse_and_validate_result(payload("m1", with_metric=True), context)
        evidence = result.summary_evidence
        self.assertEqual(evidence.messages[0].membership_id, "mem1")
        self.assertEqual(evidence.messages[0].source_record_keys, ("source-1",))
        self.assertEqual(evidence.messages[0].source_snapshot_keys, ("snapshot-1",))
        self.assertEqual(evidence.metrics[0].analytics_run_id, "17")
        self.assertEqual(evidence.metrics[0].analytics_version, "9")
        self.assertEqual(evidence.metrics[0].processing_run_id, "11")
        self.assertEqual(evidence.metrics[0].analysis_signature, "sig-v9")
        self.assertEqual(evidence.metrics[0].source_fingerprint, "source-fingerprint")
        report = validate_a5_evidence_chain(context, result)
        self.assertEqual(report["status"], "PASS", report)

    def test_a7_detects_dropped_message_source_provenance(self):
        context = self.context()
        result = parse_and_validate_result(payload("m1", with_metric=True), context)
        original = result.summary_evidence.messages[0]
        corrupted_message = replace(original, source_record_keys=())
        corrupted_evidence = replace(
            result.summary_evidence,
            messages=(corrupted_message,),
        )
        corrupted = replace(result, summary_evidence=corrupted_evidence)
        report = validate_a5_evidence_chain(context, corrupted)
        self.assertEqual(report["status"], "FAIL")
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("A5_MESSAGE_PROVENANCE_MISMATCH", codes)
        self.assertIn("A5_SOURCE_RECORD_PROVENANCE_DROPPED", codes)


class A2ProductionContextTests(unittest.TestCase):
    def test_membership_source_provenance_and_unknown_time_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a2.sqlite"
            with sqlite3.connect(path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE analysis_messages(
                        id INTEGER PRIMARY KEY,
                        membership_id INTEGER,
                        conversation_id INTEGER NOT NULL,
                        sender_id INTEGER,
                        sent_at_utc_us INTEGER,
                        message_type TEXT,
                        text TEXT,
                        is_edited INTEGER,
                        is_deleted INTEGER
                    );
                    CREATE TABLE import_run(
                        id INTEGER PRIMARY KEY,
                        source_fingerprint TEXT NOT NULL,
                        source_sha256 TEXT,
                        parser_version TEXT
                    );
                    CREATE TABLE message_source(
                        id INTEGER PRIMARY KEY,
                        message_id INTEGER NOT NULL,
                        import_run_id INTEGER NOT NULL,
                        source_record_key TEXT
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO import_run VALUES (1, 'fp', 'abc123', 'parser-v2')"
                )
                timestamp = int(BASE.timestamp() * 1_000_000)
                conn.execute(
                    "INSERT INTO analysis_messages VALUES (1,101,7,22,?,'text','hello',0,0)",
                    (timestamp,),
                )
                conn.execute(
                    "INSERT INTO analysis_messages VALUES (2,102,7,22,NULL,'text','unknown time',0,0)"
                )
                conn.execute(
                    "INSERT INTO message_source VALUES (1,1,1,'source-key-1')"
                )
                conn.execute(
                    "INSERT INTO message_source VALUES (2,2,1,'source-key-2')"
                )
            source = A2SQLiteMessageSource(path)
            rows = source.list_messages(
                "7", BASE - timedelta(minutes=1), BASE + timedelta(minutes=1)
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].membership_id, "101")
            self.assertEqual(rows[0].source_record_keys, ("source-key-1",))
            self.assertEqual(rows[0].source_snapshot_keys, ("abc123",))
            self.assertEqual(rows[0].source_parser_versions, ("parser-v2",))
            warnings = source.context_warnings(
                "7", BASE - timedelta(minutes=1), BASE + timedelta(minutes=1)
            )
            self.assertTrue(any("unknown timestamp" in item for item in warnings))


class A4V9ProvenanceTests(unittest.TestCase):
    def test_candidate_carries_exact_analytics_run_provenance_and_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a4.sqlite"
            with sqlite3.connect(path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE analytics_run(
                        id INTEGER PRIMARY KEY,
                        analytics_version TEXT NOT NULL,
                        processing_run_id INTEGER NOT NULL,
                        status TEXT NOT NULL
                    );
                    CREATE TABLE analytics_conversation_state_v6(
                        analytics_run_id INTEGER NOT NULL,
                        conversation_id INTEGER NOT NULL,
                        source_fingerprint TEXT NOT NULL,
                        analysis_signature TEXT NOT NULL
                    );
                    CREATE TABLE change_src(
                        analytics_run_id INTEGER,
                        conversation_id INTEGER,
                        participant_id INTEGER,
                        metric TEXT,
                        period_date TEXT,
                        value REAL,
                        baseline_median REAL,
                        robust_z_score REAL,
                        direction TEXT,
                        source_message_ids_json TEXT
                    );
                    CREATE VIEW analysis_a4_changes AS SELECT * FROM change_src;
                    """
                )
                conn.execute("INSERT INTO analytics_run VALUES (17,'9',11,'completed')")
                conn.execute(
                    "INSERT INTO analytics_conversation_state_v6 VALUES (17,7,'fp-v9','sig-v9')"
                )
                conn.execute(
                    "INSERT INTO change_src VALUES (17,7,2,'message_count','2026-01-10',20,8,3.1,'increasing','[1,2]')"
                )
            candidate = A4SQLiteCandidateSource(path).change_points("7")[0]
            self.assertEqual(candidate.metadata["analytics_run_id"], 17)
            self.assertEqual(candidate.metadata["analytics_version"], "9")
            self.assertEqual(candidate.metadata["processing_run_id"], 11)
            self.assertEqual(candidate.metadata["source_fingerprint"], "fp-v9")
            self.assertEqual(candidate.metadata["analysis_signature"], "sig-v9")
            self.assertEqual(
                candidate.metadata["candidate_semantics"],
                "statistical_change_candidate",
            )


if __name__ == "__main__":
    unittest.main()
