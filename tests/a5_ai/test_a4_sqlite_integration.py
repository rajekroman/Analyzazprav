from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.a5_ai import A4SQLiteCandidateSource, A4SQLiteSourceError


class A4SQLiteCandidateSourceTests(unittest.TestCase):
    def make_db(self) -> Path:
        """Legacy converter fixture without production A4 provenance."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "a4.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.executescript("""
                CREATE TABLE event_src (
                    conversation_id INTEGER, session_id INTEGER, event_type TEXT, score REAL,
                    start_at_utc_us INTEGER, end_at_utc_us INTEGER,
                    factors_json TEXT, source_message_ids_json TEXT
                );
                CREATE VIEW analysis_a4_events AS SELECT * FROM event_src;

                CREATE TABLE change_src (
                    conversation_id INTEGER, participant_id INTEGER, metric TEXT,
                    period_date TEXT, value REAL, baseline_median REAL,
                    robust_z_score REAL, direction TEXT, source_message_ids_json TEXT
                );
                CREATE VIEW analysis_a4_changes AS SELECT * FROM change_src;

                CREATE TABLE topic_src (
                    conversation_id INTEGER, topic_key TEXT, method TEXT, normalized_phrase TEXT,
                    ngram_size INTEGER, document_frequency INTEGER,
                    document_frequency_ratio REAL, occurrence_count INTEGER,
                    participant_count INTEGER, salience REAL,
                    first_period_date TEXT, last_period_date TEXT,
                    source_message_ids_json TEXT
                );
                CREATE VIEW analysis_a4_topics AS SELECT * FROM topic_src;
            """)
            conn.execute(
                "INSERT INTO event_src VALUES (7,3,'conflict_candidate',0.8,1000000,3000000,?,?)",
                ('{"negative":0.7}', '[10,11]'),
            )
            conn.execute(
                "INSERT INTO change_src VALUES (7,2,'message_count','2025-05-10',20,8,3.1,'increasing',?)",
                ('[11,12]',),
            )
            conn.execute(
                "INSERT INTO topic_src VALUES (7,'t1','lexical_ngram_v1','meeting',1,4,0.2,5,2,3.5,'2025-05-01','2025-05-10',?)",
                ('[10,12]',),
            )
        return db

    def make_production_db(self, *, reconciliation_ok: int = 1) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "a4-production.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.executescript("""
                CREATE TABLE analytics_run (
                    id INTEGER PRIMARY KEY,
                    analytics_version TEXT NOT NULL,
                    processing_run_id INTEGER NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE analytics_conversation_state_v6 (
                    analytics_run_id INTEGER NOT NULL,
                    conversation_id INTEGER NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    analysis_signature TEXT NOT NULL
                );
                CREATE TABLE reconciliation_src (
                    conversation_id INTEGER NOT NULL,
                    reconciliation_ok INTEGER NOT NULL
                );
                CREATE VIEW analysis_a4_reconciliation AS SELECT * FROM reconciliation_src;
                CREATE TABLE event_src (
                    analytics_run_id INTEGER NOT NULL,
                    conversation_id INTEGER NOT NULL,
                    session_id INTEGER,
                    event_type TEXT NOT NULL,
                    score REAL NOT NULL,
                    start_at_utc_us INTEGER,
                    end_at_utc_us INTEGER,
                    factors_json TEXT NOT NULL,
                    source_message_ids_json TEXT NOT NULL
                );
                CREATE VIEW analysis_a4_events AS SELECT * FROM event_src;
            """)
            conn.execute("INSERT INTO analytics_run VALUES (5,'9',42,'completed')")
            conn.execute(
                "INSERT INTO analytics_conversation_state_v6 VALUES (5,7,'source-fp','analysis-sig')"
            )
            conn.execute(
                "INSERT INTO reconciliation_src VALUES (7,?)",
                (reconciliation_ok,),
            )
            conn.execute(
                "INSERT INTO event_src VALUES (5,7,3,'conflict_candidate',0.8,1000000,3000000,?,?)",
                ('{"negative":0.7}', '[10,11]'),
            )
        return db

    def test_reads_published_views_and_preserves_evidence(self):
        source = A4SQLiteCandidateSource(self.make_db())
        candidates = source.candidates("7")
        self.assertEqual([c.candidate_type for c in candidates], ["conflict", "lexical_topic", "change_point"])
        by_type = {c.candidate_type: c for c in candidates}
        self.assertEqual(by_type["conflict"].evidence_message_ids, ("10", "11"))
        self.assertEqual(by_type["change_point"].metrics_during["robust_z_score"], 3.1)
        self.assertEqual(by_type["lexical_topic"].metadata["method"], "lexical_ngram_v1")
        self.assertEqual(by_type["lexical_topic"].metadata["normalized_phrase"], "meeting")

    def test_production_conflict_candidate_keeps_exact_a4_provenance(self):
        candidate = A4SQLiteCandidateSource(self.make_production_db()).conflicts("7")[0]
        self.assertEqual(candidate.candidate_type, "conflict")
        self.assertEqual(candidate.evidence_message_ids, ("10", "11"))
        self.assertEqual(candidate.metadata["analytics_run_id"], 5)
        self.assertEqual(candidate.metadata["processing_run_id"], 42)
        self.assertEqual(candidate.metadata["source_fingerprint"], "source-fp")
        self.assertEqual(candidate.metadata["analysis_signature"], "analysis-sig")

    def test_failed_production_reconciliation_blocks_candidates(self):
        with self.assertRaises(A4SQLiteSourceError):
            A4SQLiteCandidateSource(
                self.make_production_db(reconciliation_ok=0)
            ).conflicts("7")

    def test_missing_production_reconciliation_blocks_candidates(self):
        db = self.make_production_db()
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM reconciliation_src WHERE conversation_id=7")
        with self.assertRaises(A4SQLiteSourceError):
            A4SQLiteCandidateSource(db).conflicts("7")

    def test_historical_conflict_label_remains_readable(self):
        db = self.make_db()
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE event_src SET event_type='conflict' WHERE session_id=3")
        candidates = A4SQLiteCandidateSource(db).conflicts("7")
        self.assertEqual(len(candidates), 1)

    def test_unrelated_event_is_not_promoted_to_conflict(self):
        db = self.make_db()
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE event_src SET event_type='other' WHERE session_id=3")
        self.assertEqual(A4SQLiteCandidateSource(db).conflicts("7"), ())

    def test_missing_optional_views_return_no_candidates_for_that_type(self):
        source = A4SQLiteCandidateSource(self.make_db())
        self.assertEqual(source.engagement_signals("7"), ())
        self.assertEqual(source.regimes("7"), ())

    def test_duplicate_source_message_ids_fail_closed(self):
        db = self.make_db()
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE event_src SET source_message_ids_json='[10,10]' WHERE session_id=3")
        with self.assertRaises(A4SQLiteSourceError):
            A4SQLiteCandidateSource(db).conflicts("7")

    def test_invalid_json_fails_closed(self):
        db = self.make_db()
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE event_src SET factors_json='{' WHERE session_id=3")
        with self.assertRaises(A4SQLiteSourceError):
            A4SQLiteCandidateSource(db).conflicts("7")


if __name__ == "__main__":
    unittest.main()
