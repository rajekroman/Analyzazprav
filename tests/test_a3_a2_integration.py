from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import (
    CanonicalDatabase,
    MessageInput,
    ingest_a1_staging_bundle,
)
from analyzazprav.processing import (
    ProcessingConfig,
    ProcessingStore,
    load_a2_projection,
    process_messages,
)


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
            identity_type="email",
            identity_value="owner@example.cz",
            canonical_name="Owner",
            is_self=True,
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
                source_conversation_id="chat-real-a2",
                source_row_id="1",
                source_record_key="fixture-a3-1",
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
                source_conversation_id="chat-real-a2",
                source_row_id="2",
                source_record_key="fixture-a3-2",
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
            position=0,
        )
        self.db.add_attachment(
            message_id=second,
            import_run_id=run.id,
            mime_type="application/pdf",
            size_bytes=42,
            filename="missing.pdf",
            availability="missing",
            source_attachment_id="att-2",
            position=0,
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
        self.assertEqual(projection.messages[0].source_record_keys, ("fixture-a3-1",))

        raw_before = self.db.conn.execute(
            "SELECT raw_text FROM message_source WHERE message_id=?", (first,)
        ).fetchone()[0]
        canonical_before = self.db.conn.execute(
            "SELECT text FROM message WHERE id=?", (first,)
        ).fetchone()[0]

        store = ProcessingStore(self.db.conn, ROOT / "database" / "a3_schema.sql")
        store.initialize()
        store.persist(result, ProcessingConfig())

        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0], 2
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT raw_text FROM message_source WHERE message_id=?", (first,)
            ).fetchone()[0],
            raw_before,
        )
        self.assertEqual(
            self.db.conn.execute("SELECT text FROM message WHERE id=?", (first,)).fetchone()[0],
            canonical_before,
        )
        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])

    def _write_multi_membership_staging(self) -> Path:
        staging = Path(self.tmp.name) / "staging-multi"
        staging.mkdir()
        source_sha = "9" * 64
        manifest = {
            "contract_version": "1",
            "source": {
                "type": "imessage_chat_db",
                "name": "chat.db",
                "sha256": source_sha,
            },
            "parser": {"name": "imessage-chatdb", "version": "0.4.0"},
            "outputs": {"messages": "messages.jsonl"},
            "counts": {"messages_seen": 1, "attachments_seen": 0, "errors": 0},
        }
        record = {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": source_sha,
            "source_message_id": "42",
            "source_guid": "A3-MULTI-GUID",
            "source_record_key": "8" * 64,
            "conversation_source_id": "guid:chat-a",
            "conversation_sources": [
                {
                    "source_conversation_key": "guid:chat-a",
                    "raw_chat_rowid": 7,
                    "chat_guid": "chat-a",
                    "participant_handles": ["+420111111111"],
                    "metadata": {},
                },
                {
                    "source_conversation_key": "guid:chat-b",
                    "raw_chat_rowid": 8,
                    "chat_guid": "chat-b",
                    "participant_handles": ["+420111111111"],
                    "metadata": {},
                },
            ],
            "timestamp_raw": 1,
            "timestamp_utc": "2026-08-16T06:00:00Z",
            "timestamp_precision": "second",
            "sender_handle": "+420111111111",
            "is_from_me": False,
            "text": "Jedna zpráva, dva chaty",
            "raw_text": "Jedna zpráva, dva chaty",
            "text_source": "text",
            "service": "iMessage",
            "reply_to_guid": None,
            "attachments": [],
            "raw_payload": {"ROWID": 42},
            "metadata": {},
        }
        (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (staging / "messages.jsonl").write_text(
            json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return staging

    def test_one_canonical_message_in_two_memberships_survives_a3_and_multiple_runs(self):
        ingest_a1_staging_bundle(self.db, self._write_multi_membership_staging())

        projection = load_a2_projection(self.db.conn)
        self.assertEqual(len(projection.messages), 2)
        self.assertEqual(len({message.id for message in projection.messages}), 1)
        self.assertEqual(len({message.membership_id for message in projection.messages}), 2)
        self.assertEqual(len({message.conversation_id for message in projection.messages}), 2)

        result = process_messages(list(projection.messages), list(projection.relations))
        self.assertEqual(len(result.messages), 2)
        self.assertEqual(len(result.sessions), 2)
        self.assertEqual(len({message.message_id for message in result.messages}), 1)
        self.assertEqual(len({message.membership_id for message in result.messages}), 2)

        store = ProcessingStore(self.db.conn, ROOT / "database" / "a3_schema.sql")
        store.initialize()
        first_run = store.persist(result, ProcessingConfig())
        second_run = store.persist(result, ProcessingConfig(session_gap_seconds=60 * 60))

        self.assertNotEqual(first_run, second_run)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0], 4
        )
        for run_id in (first_run, second_run):
            rows = self.db.conn.execute(
                """SELECT membership_id, message_id, conversation_id
                   FROM processed_message
                   WHERE processing_run_id=?
                   ORDER BY membership_id""",
                (run_id,),
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(len({row[0] for row in rows}), 2)
            self.assertEqual(len({row[1] for row in rows}), 1)
            self.assertEqual(len({row[2] for row in rows}), 2)

        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
