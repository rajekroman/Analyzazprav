from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, MessageInput


class A2DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CanonicalDatabase(
            Path(self.tmp.name) / "messages.sqlite",
            schema_path=ROOT / "database" / "schema.sql",
        )
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _import(self, fingerprint: str):
        return self.db.begin_import(source_type="fixture", source_fingerprint=fingerprint)

    def test_schema_integrity(self):
        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])
        row = self.db.conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        self.assertEqual(row["value"], "6")

    def test_import_is_idempotent_after_completion(self):
        run = self._import("same-file")
        self.assertFalse(run.already_imported)
        self.db.finish_import(run.id)
        repeated = self._import("same-file")
        self.assertTrue(repeated.already_imported)
        self.assertEqual(repeated.id, run.id)

    def test_same_guid_from_two_sources_is_one_canonical_message(self):
        run1 = self._import("source-a")
        sender = self.db.get_or_create_participant(
            identity_type="phone", identity_value="+420 777 123 456", canonical_name="Test"
        )
        conversation = self.db.get_or_create_conversation(
            source_type="chatdb", source_conversation_id="chat-1", import_run_id=run1.id,
            canonical_key="one-to-one:+420777123456", participant_ids=[sender]
        )
        first = self.db.insert_message(MessageInput(
            import_run_id=run1.id, source_type="chatdb", conversation_id=conversation,
            sender_id=sender, sent_at_utc_us=1_700_000_000_000_000,
            direction="incoming", text="Ahoj", service="iMessage", canonical_guid="GUID-1",
            source_message_id="GUID-1", source_conversation_id="chat-1",
            raw_text="Ahoj", raw_payload={"rowid": 1}
        ))
        self.db.finish_import(run1.id)

        run2 = self._import("source-b")
        second = self.db.insert_message(MessageInput(
            import_run_id=run2.id, source_type="imazing", conversation_id=conversation,
            sender_id=sender, sent_at_utc_us=1_700_000_000_000_000,
            direction="incoming", text="Ahoj", service="iMessage", canonical_guid="GUID-1",
            source_message_id="GUID-1", raw_text="Ahoj", raw_payload={"export": "b"}
        ))
        self.assertEqual(first, second)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_source").fetchone()[0], 2)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_conversation").fetchone()[0], 1
        )

    def test_repeated_same_text_is_not_destructively_deduplicated(self):
        run = self._import("repeated-text")
        sender = self.db.get_or_create_participant(
            identity_type="email", identity_value="USER@example.com"
        )
        conversation = self.db.get_or_create_conversation(
            source_type="fixture", source_conversation_id="c1", import_run_id=run.id,
            participant_ids=[sender]
        )
        ids = []
        for source_id in ("m1", "m2"):
            ids.append(self.db.insert_message(MessageInput(
                import_run_id=run.id, source_type="fixture", conversation_id=conversation,
                sender_id=sender, sent_at_utc_us=123456789,
                direction="incoming", text="Ano", raw_text="Ano",
                source_message_id=source_id, source_conversation_id="c1",
                raw_payload={"id": source_id}
            )))
        self.assertNotEqual(ids[0], ids[1])
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 2)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_conversation").fetchone()[0], 2
        )

    def test_missing_attachment_is_preserved(self):
        run = self._import("attachment")
        sender = self.db.get_or_create_participant(identity_type="email", identity_value="a@b.cz")
        conversation = self.db.get_or_create_conversation(
            source_type="fixture", source_conversation_id="c2", import_run_id=run.id
        )
        message_id = self.db.insert_message(MessageInput(
            import_run_id=run.id, source_type="fixture", conversation_id=conversation,
            sender_id=sender, sent_at_utc_us=None, message_type="attachment",
            source_message_id="m-attachment", source_conversation_id="c2",
            raw_payload={"id": "m-attachment"}
        ))
        attachment_id = self.db.add_attachment(
            message_id=message_id, import_run_id=run.id, filename="IMG_0001.HEIC",
            availability="missing", original_path="~/Library/Messages/Attachments/..."
        )
        row = self.db.conn.execute(
            "SELECT availability FROM attachment WHERE id=?", (attachment_id,)
        ).fetchone()
        self.assertEqual(row["availability"], "missing")
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
