from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from analyzazprav.analytics import analyze_database, load_analytic_messages
from analyzazprav.normalization import CanonicalDatabase, MessageInput
from analyzazprav.processing import (
    ProcessingConfig,
    ProcessingStore,
    load_a2_projection,
    process_messages,
)

ROOT = Path(__file__).resolve().parents[1]


class A4ResolvedParticipantContractTests(unittest.TestCase):
    def test_self_alias_switch_is_not_a_false_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = CanonicalDatabase(Path(tmp) / "messages.sqlite")
            try:
                db.initialize()
                run = db.begin_import(
                    source_type="fixture",
                    source_fingerprint="a4-resolved-participants",
                )
                self_email = db.get_or_create_participant(
                    identity_type="email",
                    identity_value="owner@example.cz",
                    canonical_name="Owner",
                    is_self=True,
                )
                self_phone = db.get_or_create_participant(
                    identity_type="phone",
                    identity_value="+420 777 000 111",
                    canonical_name="Owner mobile",
                    is_self=True,
                )
                other = db.get_or_create_participant(
                    identity_type="email",
                    identity_value="alice@example.cz",
                    canonical_name="Alice",
                )
                conversation = db.get_or_create_conversation(
                    source_type="fixture",
                    source_conversation_id="alias-chat",
                    import_run_id=run.id,
                    canonical_key="fixture:alias-chat",
                    participant_ids=[self_email, self_phone, other],
                )

                for index, (sender_id, text) in enumerate(
                    [
                        (self_email, "První alias"),
                        (self_phone, "Druhý alias"),
                        (other, "Odpověď"),
                    ],
                    start=1,
                ):
                    db.insert_message(
                        MessageInput(
                            import_run_id=run.id,
                            source_type="fixture",
                            conversation_id=conversation,
                            sender_id=sender_id,
                            sent_at_utc_us=1_700_000_000_000_000 + index * 60_000_000,
                            timezone_offset_min=60,
                            timestamp_precision="microsecond",
                            timestamp_quality="exact",
                            direction="outgoing" if sender_id in {self_email, self_phone} else "incoming",
                            message_type="text",
                            text=text,
                            service="iMessage",
                            canonical_guid=f"A4-RESOLVED-{index}",
                            source_message_id=f"A4-RESOLVED-{index}",
                            source_conversation_id="alias-chat",
                            source_row_id=str(index),
                            source_record_key=f"a4-resolved-{index}",
                            raw_timestamp=str(1_700_000_000_000_000 + index * 60_000_000),
                            raw_text=text,
                            raw_payload={"rowid": index},
                        )
                    )
                db.finish_import(run.id)

                projection = load_a2_projection(db.conn)
                processed = process_messages(
                    list(projection.messages),
                    list(projection.relations),
                    participants=list(projection.participants),
                )
                store = ProcessingStore(db.conn, ROOT / "database" / "a3_schema.sql")
                store.initialize()
                store.persist(processed, ProcessingConfig())

                analytic = load_analytic_messages(db.conn)
                self.assertEqual(len(analytic), 3)
                resolved_self = min(self_email, self_phone)
                self.assertEqual(
                    [message.participant_id for message in analytic],
                    [resolved_self, resolved_self, other],
                )

                result = analyze_database(db.conn)[0]
                self.assertEqual(result.turn_count, 2)
                self.assertEqual(len(result.response_samples), 1)
                sample = result.response_samples[0]
                self.assertEqual(sample.from_participant_id, resolved_self)
                self.assertEqual(sample.responder_id, other)
                self.assertEqual(result.participant_metrics[resolved_self]["message_count"], 2)
                self.assertEqual(result.participant_metrics[other]["message_count"], 1)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
