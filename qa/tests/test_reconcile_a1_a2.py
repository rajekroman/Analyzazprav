from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle
from qa.reconcile_a1_a2 import STATUS_FAIL, STATUS_PASS, reconcile_a1_a2
from qa.tests.test_staging_validator import write_v2_bundle


class A1A2ReconciliationTests(unittest.TestCase):
    def _ingest(self, root: Path, db_path: Path) -> None:
        db = CanonicalDatabase(db_path)
        try:
            db.initialize()
            result = ingest_a1_staging_bundle(db, root)
            self.assertEqual(2, result.messages)
            self.assertEqual(1, result.attachments)
            self.assertEqual(3, result.conversation_relations)
        finally:
            db.close()

    def test_real_a2_v5_ingest_reconciles_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            staging = tmp_path / "staging"
            db_path = tmp_path / "canonical.sqlite"
            write_v2_bundle(staging)
            self._ingest(staging, db_path)
            report = reconcile_a1_a2(staging, db_path)
        self.assertEqual(STATUS_PASS, report["status"])
        self.assertEqual(0, report["checks"]["messages_missing_in_a2"])
        self.assertEqual(0, report["checks"]["conversation_relations_missing_in_a2"])
        self.assertEqual(0, report["checks"]["attachments_missing_in_a2"])
        self.assertEqual(2, report["checks"]["a2_message_source_count"])
        self.assertEqual(3, report["checks"]["a2_conversation_relation_count"])

    def test_missing_source_conversation_relation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            staging = tmp_path / "staging"
            db_path = tmp_path / "canonical.sqlite"
            write_v2_bundle(staging)
            self._ingest(staging, db_path)
            conn = sqlite3.connect(db_path)
            conn.execute(
                "DELETE FROM message_source_conversation WHERE rowid IN (SELECT rowid FROM message_source_conversation LIMIT 1)"
            )
            conn.commit()
            conn.close()
            report = reconcile_a1_a2(staging, db_path)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A1_CONVERSATION_RELATIONS_MISSING_IN_A2", {i["code"] for i in report["issues"]})

    def test_mutated_source_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            staging = tmp_path / "staging"
            db_path = tmp_path / "canonical.sqlite"
            write_v2_bundle(staging)
            self._ingest(staging, db_path)
            conn = sqlite3.connect(db_path)
            conn.execute("UPDATE message_source SET source_record_key=? WHERE id=(SELECT MIN(id) FROM message_source)", ("f" * 64,))
            conn.commit()
            conn.close()
            report = reconcile_a1_a2(staging, db_path)
        self.assertEqual(STATUS_FAIL, report["status"])
        codes = {i["code"] for i in report["issues"]}
        self.assertTrue({"A1_MESSAGES_MISSING_IN_A2", "A2_MESSAGES_NOT_IN_A1"} <= codes)

    def test_mutated_attachment_occurrence_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            staging = tmp_path / "staging"
            db_path = tmp_path / "canonical.sqlite"
            write_v2_bundle(staging)
            self._ingest(staging, db_path)
            conn = sqlite3.connect(db_path)
            conn.execute("UPDATE attachment_source SET source_occurrence_key='wrong-occurrence'")
            conn.commit()
            conn.close()
            report = reconcile_a1_a2(staging, db_path)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A1_ATTACHMENTS_MISSING_IN_A2", {i["code"] for i in report["issues"]})


if __name__ == "__main__":
    unittest.main()
