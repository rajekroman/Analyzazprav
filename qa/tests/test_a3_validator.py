from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle
from analyzazprav.processing import (
    ProcessingConfig,
    ProcessingStore,
    load_a2_projection,
    process_messages,
)
from qa.a3_validator import STATUS_FAIL, STATUS_PASS, validate_a3_database
from qa.tests.test_staging_validator import write_v2_bundle


class A3ValidatorTests(unittest.TestCase):
    def _build_processed_database(self, tmp_path: Path) -> Path:
        staging = tmp_path / "staging"
        database = tmp_path / "messages.sqlite"
        write_v2_bundle(staging)

        db = CanonicalDatabase(database)
        try:
            db.initialize()
            ingest_a1_staging_bundle(db, staging)
            store = ProcessingStore(db.conn)
            store.initialize()
            projection = load_a2_projection(db.conn)
            config = ProcessingConfig()
            result = process_messages(
                list(projection.messages), list(projection.relations), config
            )
            run_id = store.persist(result, config)
            self.assertGreater(run_id, 0)
            self.assertEqual(3, len(result.messages))
        finally:
            db.close()
        return database

    def test_real_integrated_a3_processing_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = self._build_processed_database(Path(tmp))
            report = validate_a3_database(database)
        self.assertEqual(STATUS_PASS, report["status"])
        self.assertEqual(3, report["checks"]["canonical_membership_count"])
        self.assertEqual(3, report["checks"]["processed_membership_count"])
        self.assertEqual(2, report["checks"]["expected_session_count"])
        self.assertEqual(3, report["checks"]["expected_sender_run_count"])
        self.assertEqual(0, report["checks"]["processed_memberships_without_source_record_key"])

    def test_lost_processed_membership_fails_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = self._build_processed_database(Path(tmp))
            conn = sqlite3.connect(database)
            run_id = conn.execute("SELECT MAX(id) FROM processing_run").fetchone()[0]
            conn.execute(
                "DELETE FROM processed_message WHERE processing_run_id=? AND membership_id=(SELECT MIN(membership_id) FROM processed_message WHERE processing_run_id=?)",
                (run_id, run_id),
            )
            conn.commit()
            conn.close()
            report = validate_a3_database(database)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A3_MEMBERSHIP_RECONCILIATION_FAILED", {i["code"] for i in report["issues"]})

    def test_sequence_corruption_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = self._build_processed_database(Path(tmp))
            conn = sqlite3.connect(database)
            run_id = conn.execute("SELECT MAX(id) FROM processing_run").fetchone()[0]
            conn.execute(
                "UPDATE processed_message SET sequence_number=99 WHERE processing_run_id=? AND membership_id=(SELECT MIN(membership_id) FROM processed_message WHERE processing_run_id=?)",
                (run_id, run_id),
            )
            conn.commit()
            conn.close()
            report = validate_a3_database(database)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A3_SEQUENCE_ORDER_MISMATCH", {i["code"] for i in report["issues"]})

    def test_cross_conversation_session_assignment_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = self._build_processed_database(Path(tmp))
            conn = sqlite3.connect(database)
            run_id = conn.execute("SELECT MAX(id) FROM processing_run").fetchone()[0]
            sessions = conn.execute(
                "SELECT id,conversation_id FROM conversation_session WHERE processing_run_id=? ORDER BY id",
                (run_id,),
            ).fetchall()
            self.assertGreaterEqual(len(sessions), 2)
            foreign_session, foreign_conversation = sessions[0]
            target = conn.execute(
                "SELECT membership_id FROM processed_message WHERE processing_run_id=? AND conversation_id<>? LIMIT 1",
                (run_id, foreign_conversation),
            ).fetchone()[0]
            conn.execute(
                "UPDATE processed_message SET session_id=? WHERE processing_run_id=? AND membership_id=?",
                (foreign_session, run_id, target),
            )
            conn.commit()
            conn.close()
            report = validate_a3_database(database)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertTrue(
            {"A3_SESSION_PARTITION_MISMATCH", "A3_SESSION_METADATA_MISMATCH"}
            & {i["code"] for i in report["issues"]}
        )

    def test_attachment_feature_corruption_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = self._build_processed_database(Path(tmp))
            conn = sqlite3.connect(database)
            run_id = conn.execute("SELECT MAX(id) FROM processing_run").fetchone()[0]
            conn.execute(
                "UPDATE processed_message SET attachment_count=0, has_attachment=0 WHERE processing_run_id=? AND message_id=(SELECT message_id FROM processed_message WHERE processing_run_id=? AND attachment_count>0 LIMIT 1)",
                (run_id, run_id),
            )
            conn.commit()
            conn.close()
            report = validate_a3_database(database)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A3_ATTACHMENT_FEATURE_MISMATCH", {i["code"] for i in report["issues"]})


if __name__ == "__main__":
    unittest.main()
