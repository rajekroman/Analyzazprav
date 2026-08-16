from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


class A2AtomicImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = CanonicalDatabase(self.root / "messages.sqlite")
        self.db.initialize()
        self.staging = self.root / "staging"
        self.staging.mkdir()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _write_bundle(self, *, malformed_second: bool) -> None:
        source_sha = "f" * 64
        manifest = {
            "contract_version": "1",
            "source": {
                "type": "imessage_chat_db",
                "name": "chat.db",
                "sha256": source_sha,
            },
            "parser": {"name": "imessage-chatdb", "version": "atomic-test"},
            "outputs": {"messages": "messages.jsonl"},
            "counts": {"messages_seen": 2, "attachments_seen": 0, "errors": 0},
        }
        common = {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": source_sha,
            "conversation_source_id": "chat-atomic",
            "timestamp_precision": "nanosecond",
            "service": "iMessage",
            "sender_handle": None,
            "is_from_me": True,
            "text_source": "text",
            "reply_to_guid": None,
            "attachments": [],
            "metadata": {},
        }
        first = {
            **common,
            "source_message_id": "1",
            "source_guid": "ATOMIC-GUID-1",
            "source_record_key": "1" * 64,
            "timestamp_raw": 1,
            "timestamp_utc": "2026-08-16T07:00:00Z",
            "text": "first",
            "raw_text": "first",
            "raw_payload": {"rowid": 1},
        }
        second = {
            **common,
            "source_message_id": "2",
            "source_guid": "ATOMIC-GUID-2",
            "source_record_key": "" if malformed_second else "2" * 64,
            "timestamp_raw": 2,
            "timestamp_utc": "2026-08-16T07:00:01Z",
            "text": "second",
            "raw_text": "second",
            "raw_payload": {"rowid": 2},
        }
        (self.staging / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (self.staging / "messages.jsonl").write_text(
            json.dumps(first) + "\n" + json.dumps(second) + "\n",
            encoding="utf-8",
        )

    def test_midstream_failure_rolls_back_canonical_state_but_keeps_failed_run(self):
        self._write_bundle(malformed_second=True)

        with self.assertRaisesRegex(
            ValueError, "requires source_message_id and source_record_key"
        ):
            ingest_a1_staging_bundle(self.db, self.staging)

        run = self.db.conn.execute(
            "SELECT id, status FROM import_run"
        ).fetchone()
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "failed")

        for table in (
            "participant",
            "participant_identity",
            "conversation",
            "conversation_source",
            "conversation_participant",
            "message",
            "message_source",
            "message_conversation",
            "message_source_conversation",
            "attachment",
            "message_attachment",
            "message_attachment_occurrence",
            "attachment_source",
        ):
            self.assertEqual(
                self.db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                0,
                table,
            )
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM analysis_messages").fetchone()[0],
            0,
        )

        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])

        failed_run_id = int(run["id"])
        self._write_bundle(malformed_second=False)
        result = ingest_a1_staging_bundle(self.db, self.staging)

        self.assertEqual(result.import_run_id, failed_run_id)
        self.assertFalse(result.already_imported)
        self.assertEqual(result.messages, 2)
        self.assertEqual(
            self.db.conn.execute(
                "SELECT status FROM import_run WHERE id=?", (failed_run_id,)
            ).fetchone()[0],
            "completed",
        )
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 2)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_source").fetchone()[0], 2)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM analysis_messages").fetchone()[0], 2)

        repeated = ingest_a1_staging_bundle(self.db, self.staging)
        self.assertTrue(repeated.already_imported)
        self.assertEqual(repeated.import_run_id, failed_run_id)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 2)

        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])


if __name__ == "__main__":
    unittest.main()
