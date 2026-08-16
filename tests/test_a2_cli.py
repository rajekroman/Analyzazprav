from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization.cli import main


class A2CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.database = self.root / "messages.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, argv):
        output = StringIO()
        with redirect_stdout(output):
            code = main(argv)
        return code, json.loads(output.getvalue())

    def _write_staging(self):
        staging = self.root / "staging"
        staging.mkdir()
        source_sha = "b" * 64
        manifest = {
            "contract_version": "1",
            "source": {"type": "imessage_chat_db", "name": "chat.db", "sha256": source_sha},
            "parser": {"name": "imessage-chatdb", "version": "0.2.0"},
            "outputs": {"messages": "messages.jsonl"},
            "counts": {"messages_seen": 1, "attachments_seen": 0, "errors": 0},
        }
        record = {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": source_sha,
            "source_record_key": "c" * 64,
            "source_message_id": "1",
            "source_guid": "CLI-GUID-1",
            "conversation_source_id": "chat-1",
            "timestamp_raw": 1,
            "timestamp_utc": "2026-08-16T05:00:00Z",
            "timestamp_precision": "nanosecond",
            "sender_handle": None,
            "is_from_me": True,
            "text": "CLI fixture",
            "raw_text": "CLI fixture",
            "text_source": "text",
            "service": "iMessage",
            "reply_to_guid": None,
            "attachments": [],
            "raw_payload": {"rowid": 1},
            "metadata": {},
        }
        (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (staging / "messages.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
        return staging

    def test_init_creates_current_database(self):
        code, payload = self._run(["init", "--database", str(self.database)])
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["schema_version"], "4")
        self.assertTrue(self.database.is_file())

    def test_check_missing_database_fails_without_creating_it(self):
        code, payload = self._run(["check", "--database", str(self.database)])
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "database_not_found")
        self.assertFalse(self.database.exists())

    def test_ingest_a1_is_cli_idempotent(self):
        staging = self._write_staging()
        first_code, first = self._run([
            "ingest-a1", "--database", str(self.database), "--staging", str(staging)
        ])
        second_code, second = self._run([
            "ingest-a1", "--database", str(self.database), "--staging", str(staging)
        ])
        self.assertEqual((first_code, second_code), (0, 0))
        self.assertFalse(first["already_imported"])
        self.assertEqual(first["messages"], 1)
        self.assertTrue(second["already_imported"])
        self.assertEqual(first["import_run_id"], second["import_run_id"])
        self.assertEqual(second["integrity"]["counts"]["message"], 1)
        self.assertEqual(second["integrity"]["counts"]["message_source"], 1)


if __name__ == "__main__":
    unittest.main()
