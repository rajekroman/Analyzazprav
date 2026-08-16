from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle
from qa.sqlite_validator import STATUS_FAIL, STATUS_PASS, validate_sqlite_database
from qa.tests.test_staging_validator import write_v2_bundle


class SQLiteValidatorTests(unittest.TestCase):
    def _make_valid(self, tmp_path: Path) -> Path:
        staging = tmp_path / "staging"
        path = tmp_path / "messages.sqlite"
        write_v2_bundle(staging)
        db = CanonicalDatabase(path)
        try:
            db.initialize()
            ingest_a1_staging_bundle(db, staging)
        finally:
            db.close()
        return path

    def test_real_a2_v5_database_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._make_valid(Path(tmp))
            report = validate_sqlite_database(path)
        self.assertEqual(STATUS_PASS, report["status"])
        self.assertGreaterEqual(report["checks"]["schema_version"], 5)
        self.assertEqual(report["counts"]["message_conversation"], report["checks"]["analysis_messages"])
        self.assertEqual(
            report["counts"]["message_attachment_occurrence"],
            report["checks"]["analysis_attachments"],
        )

    def test_message_without_source_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._make_valid(Path(tmp))
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DELETE FROM message_source")
            conn.commit()
            conn.close()
            report = validate_sqlite_database(path)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("MESSAGE_SOURCE_TRACE_MISSING", {i["code"] for i in report["issues"]})

    def test_missing_primary_membership_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._make_valid(Path(tmp))
            conn = sqlite3.connect(path)
            conn.execute("UPDATE message_conversation SET is_primary=0")
            conn.commit()
            conn.close()
            report = validate_sqlite_database(path)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("MESSAGE_PRIMARY_MEMBERSHIP_INVALID", {i["code"] for i in report["issues"]})

    def test_source_relation_loss_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._make_valid(Path(tmp))
            conn = sqlite3.connect(path)
            conn.execute("DELETE FROM message_source_conversation")
            conn.commit()
            conn.close()
            report = validate_sqlite_database(path)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("MESSAGE_SOURCE_CONVERSATION_TRACE_MISSING", {i["code"] for i in report["issues"]})

    def test_old_or_incomplete_schema_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.sqlite"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE message(id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()
            report = validate_sqlite_database(path)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A2_REQUIRED_TABLES_MISSING", {i["code"] for i in report["issues"]})


if __name__ == "__main__":
    unittest.main()
