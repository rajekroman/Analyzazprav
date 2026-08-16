from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

from analyzazprav.analytics import AnalyticsConfig, AnalyticsStore, analyze_database, load_analytic_messages
from analyzazprav.normalization import CanonicalDatabase, MessageInput
from analyzazprav.processing import (
    ProcessingConfig,
    ProcessingStore,
    load_a2_projection,
    process_messages,
)


class A4IntegratedMainContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CanonicalDatabase(
            Path(self.tmp.name) / "messages.sqlite",
            schema_path=ROOT / "database" / "schema.sql",
        )
        self.db.initialize()

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _build_a2_a3(self) -> tuple[int, int, int]:
        run = self.db.begin_import(
            source_type="fixture", source_fingerprint="a4-integrated-main"
        )
        alice = self.db.get_or_create_participant(
            identity_type="phone",
            identity_value="+420 777 111 222",
            canonical_name="Alice",
        )
        owner = self.db.get_or_create_participant(
            identity_type="email",
            identity_value="owner@example.cz",
            canonical_name="Owner",
            is_self=True,
        )
        chat_a = self.db.get_or_create_conversation(
            source_type="fixture",
            source_conversation_id="chat-a",
            import_run_id=run.id,
            canonical_key="fixture:chat-a",
            participant_ids=[alice, owner],
        )
        chat_b = self.db.get_or_create_conversation(
            source_type="fixture",
            source_conversation_id="chat-b",
            import_run_id=run.id,
            canonical_key="fixture:chat-b",
            participant_ids=[alice, owner],
        )

        first = self.db.insert_message(
            MessageInput(
                import_run_id=run.id,
                source_type="fixture",
                conversation_id=chat_a,
                sender_id=alice,
                sent_at_utc_us=1_700_000_000_000_000,
                timezone_offset_min=60,
                timestamp_precision="microsecond",
                timestamp_quality="exact",
                direction="incoming",
                message_type="text",
                text="Dovolená Chorvatsko",
                service="iMessage",
                canonical_guid="A4-MAIN-1",
                source_message_id="A4-MAIN-1",
                source_conversation_id="chat-a",
                source_row_id="1",
                source_record_key="a4-main-1",
                raw_timestamp="1700000000000000",
                raw_text="Dovolená Chorvatsko",
                raw_payload={"rowid": 1},
            )
        )
        second = self.db.insert_message(
            MessageInput(
                import_run_id=run.id,
                source_type="fixture",
                conversation_id=chat_a,
                sender_id=owner,
                sent_at_utc_us=1_700_000_060_000_000,
                timezone_offset_min=60,
                timestamp_precision="microsecond",
                timestamp_quality="exact",
                direction="outgoing",
                message_type="text",
                text="Ano",
                service="iMessage",
                canonical_guid="A4-MAIN-2",
                source_message_id="A4-MAIN-2",
                source_conversation_id="chat-a",
                source_row_id="2",
                source_record_key="a4-main-2",
                raw_timestamp="1700000060000000",
                raw_text="Ano",
                raw_payload={"rowid": 2},
            )
        )
        # The canonical message remains one row in `message`, but the second
        # conversation gets its own immutable membership occurrence.
        self.db.conn.execute(
            """INSERT INTO message_conversation(
                   message_id, conversation_id, is_primary, metadata_json
               ) VALUES (?, ?, 0, '{}')""",
            (first, chat_b),
        )
        self.db.finish_import(run.id)

        projection = load_a2_projection(self.db.conn)
        processed = process_messages(list(projection.messages), list(projection.relations))
        store = ProcessingStore(self.db.conn, ROOT / "database" / "a3_schema.sql")
        store.initialize()
        first_processing_run = store.persist(processed, ProcessingConfig())
        second_processing_run = store.persist(
            processed, ProcessingConfig(session_gap_seconds=60 * 60)
        )
        self.assertNotEqual(first_processing_run, second_processing_run)
        return chat_a, chat_b, second_processing_run

    def test_a4_reads_latest_memberships_once_and_persists_scoped_sessions(self) -> None:
        chat_a, chat_b, latest_processing_run = self._build_a2_a3()

        messages = load_analytic_messages(self.db.conn)
        self.assertEqual(len(messages), 3)
        self.assertEqual(len({message.membership_id for message in messages}), 3)
        self.assertEqual(
            sorted((message.conversation_id, message.message_id) for message in messages),
            sorted(
                self.db.conn.execute(
                    "SELECT conversation_id, message_id FROM message_conversation"
                ).fetchall()
            ),
        )

        results = {item.conversation_id: item for item in analyze_database(self.db.conn)}
        self.assertEqual(results[chat_a].source_message_count, 2)
        self.assertEqual(results[chat_b].source_message_count, 1)
        self.assertEqual(len(results[chat_a].response_samples), 1)

        a4_store = AnalyticsStore(self.db.conn)
        a4_store.initialize()
        analytics_run = a4_store.write_run(list(results.values()), AnalyticsConfig())
        stored_processing_run = self.db.conn.execute(
            "SELECT processing_run_id FROM analytics_run WHERE id=?", (analytics_run,)
        ).fetchone()[0]
        self.assertEqual(stored_processing_run, latest_processing_run)

        response = self.db.conn.execute(
            """SELECT r.conversation_id, r.session_id, ar.processing_run_id
               FROM analysis_a4_responses r
               JOIN analytics_run ar ON ar.id=r.analytics_run_id"""
        ).fetchone()
        self.assertIsNotNone(response)
        self.assertEqual(response[0], chat_a)
        self.assertIsNotNone(
            self.db.conn.execute(
                """SELECT 1 FROM conversation_session
                   WHERE processing_run_id=? AND id=? AND conversation_id=?""",
                (response[2], response[1], response[0]),
            ).fetchone()
        )
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
