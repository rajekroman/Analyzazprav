from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import (
    CanonicalDatabase,
    full_integrity_report,
    ingest_a1_staging_bundle,
)


class A2RelationIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = CanonicalDatabase(self.root / "messages.sqlite")
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _ingest_resolved_reply(self) -> None:
        staging = self.root / "staging"
        staging.mkdir()
        source_sha = "z" * 64
        manifest = {
            "contract_version": "1",
            "source": {
                "type": "imessage_chat_db",
                "name": "chat.db",
                "sha256": source_sha,
            },
            "parser": {"name": "imessage-chatdb", "version": "integrity"},
            "outputs": {"messages": "messages.jsonl"},
            "counts": {"messages_seen": 2, "attachments_seen": 0, "errors": 0},
        }
        common = {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": source_sha,
            "conversation_source_id": "chat-integrity",
            "timestamp_precision": "nanosecond",
            "sender_handle": None,
            "is_from_me": True,
            "service": "iMessage",
            "attachments": [],
            "metadata": {},
        }
        rows = [
            {
                **common,
                "source_message_id": "1",
                "source_guid": "INTEGRITY-TARGET",
                "source_record_key": "1" * 64,
                "timestamp_raw": 1,
                "timestamp_utc": "2026-08-16T07:00:00Z",
                "text": "target",
                "raw_text": "target",
                "text_source": "text",
                "reply_to_guid": None,
                "raw_payload": {"rowid": 1},
            },
            {
                **common,
                "source_message_id": "2",
                "source_guid": "INTEGRITY-SOURCE",
                "source_record_key": "2" * 64,
                "timestamp_raw": 2,
                "timestamp_utc": "2026-08-16T07:00:01Z",
                "text": "reply",
                "raw_text": "reply",
                "text_source": "text",
                "reply_to_guid": "INTEGRITY-TARGET",
                "raw_payload": {"rowid": 2},
            },
        ]
        (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (staging / "messages.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        ingest_a1_staging_bundle(self.db, staging)

    def test_valid_relation_source_passes_composed_integrity_report(self):
        self._ingest_resolved_reply()
        report = full_integrity_report(self.db)
        self.assertTrue(report["ok"])
        self.assertEqual(report["semantic_errors"], [])
        self.assertEqual(report["checks"]["relation_source_import_mismatches"], 0)
        self.assertEqual(report["checks"]["resolved_relation_source_semantic_mismatches"], 0)
        self.assertEqual(
            report["checks"]["analysis_relation_sources_vs_physical"],
            {"actual": 1, "expected": 1},
        )

    def test_import_run_mismatch_is_detected_even_when_foreign_keys_pass(self):
        self._ingest_resolved_reply()
        other = self.db.begin_import(
            source_type="fixture",
            source_fingerprint="other-completed-run",
        )
        self.db.finish_import(other.id)

        with self.db.conn:
            self.db.conn.execute(
                "UPDATE message_relation_source SET import_run_id=?",
                (other.id,),
            )

        self.assertEqual(self.db.integrity_report()["integrity"], "ok")
        self.assertEqual(self.db.integrity_report()["foreign_key_errors"], [])

        report = full_integrity_report(self.db)
        self.assertFalse(report["ok"])
        self.assertEqual(report["checks"]["relation_source_import_mismatches"], 1)
        self.assertIn(
            "RELATION_SOURCE_IMPORT_MISMATCH",
            {error["code"] for error in report["semantic_errors"]},
        )

    def test_missing_v7_analysis_view_is_detected(self):
        self._ingest_resolved_reply()
        with self.db.conn:
            self.db.conn.execute("DROP VIEW analysis_message_relation_sources")

        report = full_integrity_report(self.db)
        self.assertFalse(report["ok"])
        self.assertIn(
            "A2_V7_RELATION_OBJECTS_MISSING",
            {error["code"] for error in report["semantic_errors"]},
        )


if __name__ == "__main__":
    unittest.main()
