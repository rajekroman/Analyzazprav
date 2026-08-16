from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

from analyzazprav.analytics import analyze_database, load_analytic_messages
from analyzazprav.normalization import CanonicalDatabase, MessageInput
from analyzazprav.processing import (
    ProcessingConfig,
    ProcessingStore,
    load_a2_projection,
    process_messages,
)


class A4A3ResolvedParticipantContractTests(unittest.TestCase):
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

    def test_a4_uses_a3_v5_conservative_resolved_sender(self) -> None:
        run = self.db.begin_import(
            source_type="fixture",
            source_fingerprint="a4-a3-v5-resolved-participant",
        )
        self_phone = self.db.get_or_create_participant(
            identity_type="phone",
            identity_value="+420777111222",
            canonical_name="Owner phone",
            is_self=True,
        )
        self_email = self.db.get_or_create_participant(
            identity_type="email",
            identity_value="owner@example.cz",
            canonical_name="Owner email",
            is_self=True,
        )
        alice = self.db.get_or_create_participant(
            identity_type="phone",
            identity_value="+420777333444",
            canonical_name="Alice",
        )
        conversation = self.db.get_or_create_conversation(
            source_type="fixture",
            source_conversation_id="chat-a4-a3-v5",
            import_run_id=run.id,
            canonical_key="fixture:chat-a4-a3-v5",
            participant_ids=[self_phone, self_email, alice],
        )

        sender_ids = (self_phone, self_email, alice)
        texts = ("první moje zpráva", "druhá moje zpráva", "odpověď Alice")
        for index, (sender_id, text) in enumerate(zip(sender_ids, texts), start=1):
            self.db.insert_message(
                MessageInput(
                    import_run_id=run.id,
                    source_type="fixture",
                    conversation_id=conversation,
                    sender_id=sender_id,
                    sent_at_utc_us=1_700_000_000_000_000 + index * 1_000_000,
                    timestamp_precision="microsecond",
                    timestamp_quality="exact",
                    direction="outgoing" if sender_id in (self_phone, self_email) else "incoming",
                    message_type="text",
                    text=text,
                    service="iMessage",
                    canonical_guid=f"A4-A3-V5-{index}",
                    source_message_id=f"A4-A3-V5-{index}",
                    source_conversation_id="chat-a4-a3-v5",
                    source_row_id=str(index),
                    source_record_key=f"a4-a3-v5-{index}",
                    raw_timestamp=str(1_700_000_000_000_000 + index * 1_000_000),
                    raw_text=text,
                    raw_payload={"rowid": index},
                )
            )
        self.db.finish_import(run.id)

        projection = load_a2_projection(self.db.conn)
        processed = process_messages(
            list(projection.messages),
            list(projection.relations),
            participants=list(projection.participants),
        )
        store = ProcessingStore(self.db.conn, ROOT / "database" / "a3_schema.sql")
        store.initialize()
        processing_run_id = store.persist(processed, ProcessingConfig())

        resolved_self_id = min(self_phone, self_email)
        a3_rows = self.db.conn.execute(
            """SELECT message_id, resolved_sender_id
               FROM analysis_processed_messages_resolved_latest
               ORDER BY sequence_number"""
        ).fetchall()
        self.assertEqual([row[1] for row in a3_rows], [resolved_self_id, resolved_self_id, alice])

        a4_messages = load_analytic_messages(self.db.conn)
        self.assertEqual(
            [message.participant_id for message in a4_messages],
            [resolved_self_id, resolved_self_id, alice],
        )
        result = analyze_database(self.db.conn)[0]
        self.assertEqual(set(result.participant_metrics), {resolved_self_id, alice})
        self.assertEqual(result.participant_metrics[resolved_self_id]["message_count"], 2)
        self.assertEqual(result.participant_metrics[alice]["message_count"], 1)

        self.assertEqual(
            self.db.conn.execute(
                "SELECT MAX(id) FROM processing_run WHERE status='completed'"
            ).fetchone()[0],
            processing_run_id,
        )
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
