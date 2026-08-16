from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import (
    CanonicalDatabase,
    MessageInput,
    full_integrity_report,
)
from analyzazprav.normalization.cli import main as cli_main


class A2SemanticIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Path(self.tmp.name) / "messages.sqlite"
        self.db = CanonicalDatabase(
            self.database,
            migrations_path=ROOT / "database" / "migrations",
        )
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _message(
        self,
        *,
        suffix: str,
        source_sha: str = "a" * 64,
        conversation_source_id: str | None = None,
    ) -> tuple[int, int, int]:
        source_conversation_id = conversation_source_id or f"chat-{suffix}"
        run = self.db.begin_import(
            source_type="fixture",
            source_fingerprint=f"fixture-{suffix}",
            source_sha256=source_sha,
            parser_version="test",
        )
        conversation = self.db.get_or_create_conversation(
            source_type="fixture",
            source_conversation_id=source_conversation_id,
            import_run_id=run.id,
            canonical_key=f"fixture:{source_sha}:{source_conversation_id}",
        )
        message_id = self.db.insert_message(
            MessageInput(
                import_run_id=run.id,
                source_type="fixture",
                conversation_id=conversation,
                sender_id=None,
                sent_at_utc_us=1_700_000_000_000_000 + int(suffix),
                direction="incoming",
                text=f"message-{suffix}",
                source_message_id=f"m-{suffix}",
                source_conversation_id=source_conversation_id,
                source_record_key=(suffix * 64)[:64],
                raw_payload={"fixture": suffix},
            )
        )
        self.db.finish_import(run.id)
        return run.id, conversation, message_id

    @staticmethod
    def _codes(report):
        return {item["code"] for item in report["semantic_errors"]}

    def test_valid_a2_message_passes_semantic_integrity(self):
        self._message(suffix="1")
        report = full_integrity_report(self.db)
        self.assertTrue(report["ok"])
        self.assertEqual(report["semantic_errors"], [])
        self.assertEqual(report["checks"]["messages_without_membership"], 0)
        self.assertEqual(
            report["checks"]["analysis_messages_vs_memberships"],
            {"actual": 1, "expected": 1},
        )

    def test_missing_message_membership_fails_even_when_foreign_keys_pass(self):
        _, _, message_id = self._message(suffix="2")
        with self.db.conn:
            self.db.conn.execute(
                "DELETE FROM message_conversation WHERE message_id=?",
                (message_id,),
            )

        self.assertEqual(list(self.db.conn.execute("PRAGMA foreign_key_check")), [])
        report = full_integrity_report(self.db)
        self.assertFalse(report["ok"])
        self.assertIn("MESSAGE_CONVERSATION_MEMBERSHIP_MISSING", self._codes(report))
        self.assertIn("MESSAGE_PRIMARY_MEMBERSHIP_COUNT_INVALID", self._codes(report))
        self.assertIn("MESSAGE_PRIMARY_POINTER_MISMATCH", self._codes(report))
        self.assertIn("ANALYSIS_MESSAGES_CANONICAL_COVERAGE_MISMATCH", self._codes(report))

        self.db.close()
        output = StringIO()
        with redirect_stdout(output):
            code = cli_main(["check", "--database", str(self.database)])
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertFalse(payload["integrity"]["ok"])
        self.db = CanonicalDatabase(
            self.database,
            migrations_path=ROOT / "database" / "migrations",
        )

    def test_source_relation_to_wrong_membership_is_detected_without_fk_error(self):
        _, _, message_a = self._message(suffix="3", source_sha="b" * 64)
        _, _, message_b = self._message(suffix="4", source_sha="c" * 64)
        source_a = self.db.conn.execute(
            "SELECT id FROM message_source WHERE message_id=?",
            (message_a,),
        ).fetchone()[0]
        membership_b = self.db.conn.execute(
            "SELECT id FROM message_conversation WHERE message_id=? AND is_primary=1",
            (message_b,),
        ).fetchone()[0]
        with self.db.conn:
            self.db.conn.execute(
                "UPDATE message_source_conversation SET membership_id=? WHERE message_source_id=?",
                (membership_b, source_a),
            )

        self.assertEqual(list(self.db.conn.execute("PRAGMA foreign_key_check")), [])
        report = full_integrity_report(self.db)
        codes = self._codes(report)
        self.assertFalse(report["ok"])
        self.assertIn("MESSAGE_SOURCE_MEMBERSHIP_MESSAGE_MISMATCH", codes)
        self.assertIn("SOURCE_MEMBERSHIP_CONVERSATION_MISMATCH", codes)

    def test_attachment_source_to_wrong_occurrence_attachment_is_detected(self):
        run_id, _, message_id = self._message(suffix="5", source_sha="d" * 64)
        self.db.add_attachment(
            message_id=message_id,
            import_run_id=run_id,
            sha256_value="1" * 64,
            filename="one.bin",
            source_attachment_id="one",
            position=0,
            raw_payload={"source": "one"},
        )
        self.db.add_attachment(
            message_id=message_id,
            import_run_id=run_id,
            sha256_value="2" * 64,
            filename="two.bin",
            source_attachment_id="two",
            position=1,
            raw_payload={"source": "two"},
        )
        wrong_attachment = self.db.conn.execute(
            "SELECT attachment_id FROM attachment_source WHERE source_attachment_id='two'"
        ).fetchone()[0]
        with self.db.conn:
            self.db.conn.execute(
                "UPDATE attachment_source SET attachment_id=? WHERE source_attachment_id='one'",
                (wrong_attachment,),
            )

        self.assertEqual(list(self.db.conn.execute("PRAGMA foreign_key_check")), [])
        report = full_integrity_report(self.db)
        self.assertFalse(report["ok"])
        self.assertIn("ATTACHMENT_SOURCE_OCCURRENCE_MISMATCH", self._codes(report))

    def test_analysis_messages_uses_membership_count_not_canonical_message_count(self):
        run_id, conversation_a, message_id = self._message(suffix="6", source_sha="e" * 64)
        conversation_b = self.db.get_or_create_conversation(
            source_type="fixture",
            source_conversation_id="chat-secondary",
            import_run_id=run_id,
            canonical_key="fixture:secondary",
        )
        with self.db.conn:
            self.db.conn.execute(
                """INSERT INTO message_conversation(
                       message_id, conversation_id, is_primary, metadata_json
                   ) VALUES (?, ?, 0, '{}')""",
                (message_id, conversation_b),
            )
        self.assertNotEqual(conversation_a, conversation_b)

        report = full_integrity_report(self.db)
        self.assertTrue(report["ok"])
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 1)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM analysis_messages").fetchone()[0],
            2,
        )
        self.assertEqual(
            report["checks"]["analysis_messages_vs_memberships"],
            {"actual": 2, "expected": 2},
        )
        self.assertEqual(
            report["checks"]["analysis_messages_distinct_ids_vs_messages"],
            {"actual": 1, "expected": 1},
        )


if __name__ == "__main__":
    unittest.main()
