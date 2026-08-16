from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, MessageInput


class A2GuidServiceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CanonicalDatabase(Path(self.tmp.name) / "messages.sqlite")
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _insert(self, *, suffix: str, guid: str, service: str | None) -> int:
        run = self.db.begin_import(
            source_type="fixture",
            source_fingerprint=f"source-{suffix}",
        )
        conversation = self.db.get_or_create_conversation(
            source_type="fixture",
            source_conversation_id=f"chat-{suffix}",
            import_run_id=run.id,
            canonical_key=f"chat:{suffix}",
            service=service,
        )
        message_id = self.db.insert_message(
            MessageInput(
                import_run_id=run.id,
                source_type="fixture",
                conversation_id=conversation,
                sender_id=None,
                sent_at_utc_us=1_700_000_000_000_000,
                direction="incoming",
                text=f"message-{suffix}",
                service=service,
                canonical_guid=guid,
                source_message_id=f"m-{suffix}",
                source_conversation_id=f"chat-{suffix}",
                source_record_key=(suffix * 64)[:64],
                raw_payload={"fixture": suffix},
            )
        )
        self.db.finish_import(run.id)
        return message_id

    def test_unknown_service_is_exact_null_boundary_not_guid_wildcard(self):
        known_imessage = self._insert(suffix="a", guid="SHARED-GUID", service="iMessage")
        unknown_first = self._insert(suffix="b", guid="SHARED-GUID", service=None)
        unknown_second = self._insert(suffix="c", guid="SHARED-GUID", service=None)
        known_sms = self._insert(suffix="d", guid="SHARED-GUID", service="SMS")
        known_imessage_again = self._insert(
            suffix="e", guid="SHARED-GUID", service="iMessage"
        )

        self.assertNotEqual(known_imessage, unknown_first)
        self.assertEqual(unknown_first, unknown_second)
        self.assertNotEqual(known_sms, known_imessage)
        self.assertNotEqual(known_sms, unknown_first)
        self.assertEqual(known_imessage_again, known_imessage)

        self.assertEqual(
            self.db.find_message_by_guid("SHARED-GUID", "iMessage"), known_imessage
        )
        self.assertEqual(self.db.find_message_by_guid("SHARED-GUID", "SMS"), known_sms)
        self.assertEqual(self.db.find_message_by_guid("SHARED-GUID", None), unknown_first)

        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 3)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_source").fetchone()[0], 5
        )

    def test_unknown_service_relation_lookup_does_not_jump_to_known_service(self):
        known_target = self._insert(
            suffix="f", guid="KNOWN-SERVICE-TARGET", service="iMessage"
        )
        self.assertEqual(
            self.db.find_message_by_guid("KNOWN-SERVICE-TARGET", "iMessage"),
            known_target,
        )
        self.assertIsNone(self.db.find_message_by_guid("KNOWN-SERVICE-TARGET", None))

        unknown_target = self._insert(
            suffix="g", guid="KNOWN-SERVICE-TARGET", service=None
        )
        self.assertEqual(
            self.db.find_message_by_guid("KNOWN-SERVICE-TARGET", None),
            unknown_target,
        )
        self.assertNotEqual(unknown_target, known_target)

        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])


if __name__ == "__main__":
    unittest.main()
