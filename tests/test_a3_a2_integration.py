from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, MessageInput
from analyzazprav.processing import ProcessingConfig, ProcessingStore, load_a2_projection, process_messages


class A3RealA2ContractTests(unittest.TestCase):
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

    def test_real_a2_database_flows_through_a3_without_source_mutation(self):
        run = self.db.begin_import(source_type="fixture", source_fingerprint="a3-real-a2")
        alice = self.db.get_or_create_participant(
            identity_type="phone", identity_value="+420 777 111 222", canonical_name="Alice"
        )
        owner = self.db.get_or_create_participant(
            identity_type="email", identity_value="owner@example.cz", canonical_name="Owner", is_self=True
        )
        conversation = self.db.get_or_create_conversation(
            source_type="fixture",
            source_conversation_id="chat-real-a2",
            import_run_id=run.id,
            canonical_key="fixture:chat-real-a2",
            participant_ids=[alice, owner],
        )

        first = self.db.insert_message(
            MessageInput(
                import_run_id=run.id,
                source_type="fixture",
                conversation_id=conversation,
                sender_id=alice,
                sent_at_utc_us=1_700_000_000_000_000,
                timezone_offset_min=60,
                timestamp_precision="microsecond",
                timestamp_quality="exact",
                direction="incoming",
                message_type="text",
                text="Ahoj!!!",
                service="iMessage",
                canonical_guid="A3-GUID-1",
                source_message_id="A3-GUID-1",
                source_row_id="1",
                raw_timestamp="1700000000000000",
                raw_text="Ahoj!!!",
                raw_payload={"rowid": 1},
            )
        )
        second = self.db.insert_message(
            MessageInput(
                import_run_id=run.id,
                source_type="fixture",
                conversation_id=conversation,
                sender_id=owner,
                sent_at_utc_us=1_700_000_001_000_000,
                timezone_offset_min=60,
                timestamp_precision="microsecond",
                timestamp_quality="exact",
                direction="outgoing",
                message_type="text",
                text="Ano",
                service="iMessage",
                canonical_guid="A3-GUID-2",
                source_message_id="A3-GUID-2",
                source_row_id="2",
                raw_timestamp="1700000001000000",
                raw_text="Ano",
                raw_payload={"rowid": 2},
            )
        )
        self.db.add_attachment(
            message_id=first,
            import_run_id=run.id,
            sha256_value="a" * 64,
            mime_type="image/jpeg",
            size_bytes=1234,
            filename="photo.jpg",
            availability="available",
            source_attachment_id="att-1",
        )
        self.db.add_attachment(
            message_id=second,
            import_run_id=run.id,
            mime_type="application/pdf",
            size_bytes=42,
            filename="missing.pdf",
            availability="missing",
            source_attachment_id="att-2",
        )
        self.db.add_relation(second, first, "reply", {"origin": "fixture"})
        self.db.finish_import(run.id)

        projection = load_a2_projection(self.db.conn)
        result = process_messages(list(projection.messages), list(projection.relations))
        by_id = {message.message_id: message for message in result.messages}

        self.assertEqual(len(result.messages), 2)
        self.assertEqual(len(result.threads), 1)
        self.assertEqual(result.threads[0].message_ids, (first, second))
        self.assertEqual(by_id[first].features.image_count, 1)
        self.assertEqual(by_id[second].features.document_count, 1)
        self.assertEqual(by_id[second].features.missing_attachment_count, 1)
        self.assertEqual(by_id[second].features.seconds_since_previous_other_sender, 1.0)
        self.assertIsNotNone(by_id[first].features.local_hour)

        store = ProcessingStore(self.db.conn, ROOT / "database" / "a3_schema.sql")
        store.initialize()
        store.replace_all(result, ProcessingConfig())

        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0], 2)
        self.assertEqual(self.db.conn.execute("SELECT raw_text FROM message_source WHERE message_id=?", (first,)).fetchone()[0], "Ahoj!!!")
        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])


if __name__ == "__main__":
    unittest.main()
