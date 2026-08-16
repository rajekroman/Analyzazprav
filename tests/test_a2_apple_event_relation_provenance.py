from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


class A2AppleEventRelationProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = CanonicalDatabase(self.root / "canonical.sqlite")
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_associated_message_is_neutral_source_fact_and_edit_state_is_not_inferred(self):
        staging = self.root / "staging"
        staging.mkdir()
        source_sha = "a" * 64
        manifest = {
            "contract_version": "1",
            "source": {"type": "imessage_chat_db", "name": "chat.db", "sha256": source_sha},
            "parser": {"name": "imessage-chatdb", "version": "0.6.0"},
            "outputs": {"messages": "messages.jsonl"},
            "counts": {"messages_seen": 1, "attachments_seen": 0, "errors": 0},
        }
        apple_associated = {
            "associated_message_guid": "p:0/GUID-10",
            "associated_message_type": 2001,
            "associated_message_emoji": "👍",
            "associated_message_range_location": 0,
            "associated_message_range_length": 4,
        }
        apple_edit_state = {
            "date_edited_raw": 123,
            "date_edited_utc": "2001-01-01T00:02:03Z",
            "is_edited_raw": 1,
            "edit_history_present": True,
            "edit_history_bytes": 12,
        }
        record = {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": source_sha,
            "source_record_key": "1" * 64,
            "source_message_id": "10",
            "source_guid": "EVENT-GUID",
            "conversation_source_id": "chat-event",
            "timestamp_raw": 1,
            "timestamp_utc": "2026-08-16T07:00:00Z",
            "timestamp_precision": "nanosecond",
            "sender_handle": None,
            "is_from_me": True,
            "text": None,
            "raw_text": None,
            "text_source": None,
            "service": "iMessage",
            "reply_to_guid": None,
            "attachments": [],
            "raw_payload": {
                "rowid": 10,
                "associated_message_guid": "p:0/GUID-10",
                "associated_message_type": 2001,
            },
            "metadata": {
                "apple_associated_message": apple_associated,
                "apple_edit_state": apple_edit_state,
            },
        }
        (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (staging / "messages.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

        result = ingest_a1_staging_bundle(self.db, staging)
        self.assertEqual(result.messages, 1)
        self.assertEqual(result.relation_sources, 1)
        self.assertEqual(result.relations, 0)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_relation").fetchone()[0], 0)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_relation_source").fetchone()[0], 1)

        relation = self.db.conn.execute(
            """SELECT relation_type, target_identifier_type, target_identifier_value,
                      target_service, source_relation_type, resolution_status,
                      canonical_relation_id, metadata_json
               FROM analysis_message_relation_sources"""
        ).fetchone()
        self.assertEqual(relation["relation_type"], "source_association")
        self.assertEqual(relation["target_identifier_type"], "apple_associated_message_guid")
        self.assertEqual(relation["target_identifier_value"], "p:0/GUID-10")
        self.assertEqual(relation["target_service"], "iMessage")
        self.assertEqual(relation["source_relation_type"], "2001")
        self.assertEqual(relation["resolution_status"], "unresolved")
        self.assertIsNone(relation["canonical_relation_id"])
        relation_metadata = json.loads(relation["metadata_json"])
        self.assertEqual(relation_metadata["apple_associated_message"], apple_associated)

        source_metadata = json.loads(
            self.db.conn.execute("SELECT metadata_json FROM message_source").fetchone()[0]
        )
        self.assertEqual(source_metadata["apple_associated_message"], apple_associated)
        self.assertEqual(source_metadata["apple_edit_state"], apple_edit_state)

        canonical = self.db.conn.execute(
            "SELECT is_edited, is_deleted FROM message WHERE canonical_guid='EVENT-GUID'"
        ).fetchone()
        self.assertEqual((canonical["is_edited"], canonical["is_deleted"]), (0, 0))

        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])


if __name__ == "__main__":
    unittest.main()
