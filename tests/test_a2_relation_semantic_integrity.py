from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, full_integrity_report, ingest_a1_staging_bundle


class A2RelationSemanticIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = CanonicalDatabase(self.root / "canonical.sqlite")
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _write_bundle(self) -> Path:
        staging = self.root / "staging"
        staging.mkdir()
        source_sha = "e" * 64
        manifest = {
            "contract_version": "1",
            "source": {"type": "imessage_chat_db", "name": "chat.db", "sha256": source_sha},
            "parser": {"name": "imessage-chatdb", "version": "relation-integrity"},
            "outputs": {"messages": "messages.jsonl"},
            "counts": {"messages_seen": 4, "attachments_seen": 0, "errors": 0},
        }
        common = {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": source_sha,
            "conversation_source_id": "chat-integrity",
            "timestamp_precision": "nanosecond",
            "sender_handle": None,
            "is_from_me": True,
            "text_source": "text",
            "service": "iMessage",
            "attachments": [],
            "metadata": {},
        }
        records = [
            {
                **common,
                "source_message_id": "1",
                "source_record_key": "1" * 64,
                "source_guid": "TARGET-ONE",
                "timestamp_raw": 1,
                "timestamp_utc": "2026-08-16T07:00:01Z",
                "text": "target one",
                "raw_text": "target one",
                "reply_to_guid": None,
                "raw_payload": {"rowid": 1},
            },
            {
                **common,
                "source_message_id": "2",
                "source_record_key": "2" * 64,
                "source_guid": "SOURCE-ONE",
                "timestamp_raw": 2,
                "timestamp_utc": "2026-08-16T07:00:02Z",
                "text": "reply one",
                "raw_text": "reply one",
                "reply_to_guid": "TARGET-ONE",
                "raw_payload": {"rowid": 2},
            },
            {
                **common,
                "source_message_id": "3",
                "source_record_key": "3" * 64,
                "source_guid": "TARGET-TWO",
                "timestamp_raw": 3,
                "timestamp_utc": "2026-08-16T07:00:03Z",
                "text": "target two",
                "raw_text": "target two",
                "reply_to_guid": None,
                "raw_payload": {"rowid": 3},
            },
            {
                **common,
                "source_message_id": "4",
                "source_record_key": "4" * 64,
                "source_guid": "SOURCE-TWO",
                "timestamp_raw": 4,
                "timestamp_utc": "2026-08-16T07:00:04Z",
                "text": "reply two",
                "raw_text": "reply two",
                "reply_to_guid": "TARGET-TWO",
                "raw_payload": {"rowid": 4},
            },
        ]
        (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (staging / "messages.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        return staging

    @staticmethod
    def _codes(report):
        return {item["code"] for item in report["semantic_errors"]}

    def test_valid_resolved_and_unresolved_relation_provenance_passes(self):
        ingest_a1_staging_bundle(self.db, self._write_bundle())
        source_id = self.db.conn.execute(
            "SELECT id FROM message_source WHERE source_message_id='1'"
        ).fetchone()[0]
        with self.db.conn:
            self.db.conn.execute(
                """INSERT INTO message_relation_source(
                       message_source_id, relation_key, position, relation_type,
                       target_identifier_type, target_identifier_value,
                       target_service, source_relation_type, metadata_json
                   ) VALUES (?, 'neutral-unresolved', 0, 'source_association',
                             'apple_associated_message_guid', 'p:0/RAW-TARGET',
                             'iMessage', '2001', '{}')""",
                (source_id,),
            )

        report = full_integrity_report(self.db)
        self.assertTrue(report["ok"])
        self.assertEqual(report["semantic_errors"], [])
        self.assertEqual(
            report["checks"]["analysis_relation_sources_vs_sources"],
            {"actual": 3, "expected": 3},
        )

    def test_wrong_canonical_relation_link_fails_even_when_foreign_keys_pass(self):
        ingest_a1_staging_bundle(self.db, self._write_bundle())
        first_source_relation = self.db.conn.execute(
            """SELECT mrs.id
               FROM message_relation_source mrs
               JOIN message_source ms ON ms.id=mrs.message_source_id
               WHERE ms.source_message_id='2'"""
        ).fetchone()[0]
        second_canonical_relation = self.db.conn.execute(
            """SELECT mr.id
               FROM message_relation mr
               JOIN message source ON source.id=mr.source_message_id
               WHERE source.canonical_guid='SOURCE-TWO'"""
        ).fetchone()[0]

        with self.db.conn:
            self.db.conn.execute(
                "UPDATE message_relation_source SET canonical_relation_id=? WHERE id=?",
                (second_canonical_relation, first_source_relation),
            )

        self.assertEqual(list(self.db.conn.execute("PRAGMA foreign_key_check")), [])
        report = full_integrity_report(self.db)
        self.assertFalse(report["ok"])
        codes = self._codes(report)
        self.assertIn("RELATION_SOURCE_MESSAGE_MISMATCH", codes)
        self.assertIn("RELATION_SOURCE_TARGET_MISMATCH", codes)


if __name__ == "__main__":
    unittest.main()
