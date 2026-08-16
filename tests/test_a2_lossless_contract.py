from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


class A2LosslessContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CanonicalDatabase(
            Path(self.tmp.name) / "messages.sqlite",
            migrations_path=ROOT / "database" / "migrations",
        )
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_same_raw_chat_id_in_two_source_snapshots_does_not_collide(self):
        run_a = self.db.begin_import(
            source_type="imessage_chat_db",
            source_fingerprint="parser-a-source-a",
            source_sha256="a" * 64,
        )
        run_b = self.db.begin_import(
            source_type="imessage_chat_db",
            source_fingerprint="parser-a-source-b",
            source_sha256="b" * 64,
        )
        conversation_a = self.db.get_or_create_conversation(
            source_type="imessage_chat_db",
            source_conversation_id="1",
            import_run_id=run_a.id,
        )
        conversation_b = self.db.get_or_create_conversation(
            source_type="imessage_chat_db",
            source_conversation_id="1",
            import_run_id=run_b.id,
        )
        self.assertNotEqual(conversation_a, conversation_b)
        rows = self.db.conn.execute(
            """SELECT source_snapshot_key, source_sha256, source_conversation_id
               FROM conversation_source ORDER BY source_snapshot_key"""
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["source_snapshot_key"] for row in rows}, {"a" * 64, "b" * 64})
        self.assertEqual({row["source_conversation_id"] for row in rows}, {"1"})

    def test_explicit_stable_canonical_key_can_merge_source_snapshots(self):
        run_a = self.db.begin_import(
            source_type="imessage_chat_db",
            source_fingerprint="stable-a",
            source_sha256="c" * 64,
        )
        run_b = self.db.begin_import(
            source_type="imessage_chat_db",
            source_fingerprint="stable-b",
            source_sha256="d" * 64,
        )
        canonical_key = "imessage_chat_db:chat-guid:iMessage;-;+420123456789"
        first = self.db.get_or_create_conversation(
            source_type="imessage_chat_db",
            source_conversation_id="11",
            import_run_id=run_a.id,
            canonical_key=canonical_key,
        )
        second = self.db.get_or_create_conversation(
            source_type="imessage_chat_db",
            source_conversation_id="99",
            import_run_id=run_b.id,
            canonical_key=canonical_key,
        )
        self.assertEqual(first, second)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM conversation_source").fetchone()[0], 2)

    def _write_multichat_bundle(self) -> Path:
        staging = Path(self.tmp.name) / "multi-chat"
        staging.mkdir(exist_ok=True)
        source_sha = "e" * 64
        manifest = {
            "contract_version": "1",
            "source": {"type": "imessage_chat_db", "name": "chat.db", "sha256": source_sha},
            "parser": {"name": "imessage-chatdb", "version": "contract-v1-test"},
            "outputs": {"messages": "messages.jsonl"},
            "counts": {"messages_seen": 1, "attachments_seen": 2, "errors": 0},
        }
        record = {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": source_sha,
            "source_message_id": "42",
            "source_guid": "GUID-MULTI-CHAT",
            "source_record_key": "f" * 64,
            "conversation_sources": [
                {"raw_chat_rowid": 1, "source_conversation_key": "rowid:1"},
                {"raw_chat_rowid": 2, "source_conversation_key": "rowid:2"},
            ],
            "timestamp_raw": 1,
            "timestamp_utc": "2026-08-16T06:00:00Z",
            "timestamp_precision": "second",
            "sender_handle": None,
            "is_from_me": True,
            "text": "Jedna fyzická zpráva, dvě chat vazby",
            "raw_text": "Jedna fyzická zpráva, dvě chat vazby",
            "text_source": "text",
            "service": "iMessage",
            "reply_to_guid": None,
            "attachments": [
                {
                    "source_attachment_id": "501",
                    "filename": "same.jpg",
                    "mime_type": "image/jpeg",
                    "transfer_name": "same.jpg",
                    "total_bytes": 10,
                    "source_path": None,
                    "sha256": "1" * 64,
                    "raw_payload": {"rowid": 501},
                },
                {
                    "source_attachment_id": "502",
                    "filename": "same.jpg",
                    "mime_type": "image/jpeg",
                    "transfer_name": "same.jpg",
                    "total_bytes": 10,
                    "source_path": None,
                    "sha256": "1" * 64,
                    "raw_payload": {"rowid": 502},
                },
            ],
            "raw_payload": {"ROWID": 42},
            "metadata": {},
        }
        (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (staging / "messages.jsonl").write_text(
            json.dumps(record, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return staging

    def test_one_source_message_keeps_two_conversation_memberships(self):
        staging = self._write_multichat_bundle()
        result = ingest_a1_staging_bundle(self.db, staging)
        self.assertEqual(result.messages, 1)
        self.assertEqual(result.conversation_relations, 2)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_source").fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0], 2)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM conversation_source").fetchone()[0], 2)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_conversation").fetchone()[0], 2)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_source_conversation").fetchone()[0], 2)
        analysis_rows = self.db.conn.execute(
            "SELECT membership_id, id, conversation_id FROM analysis_messages ORDER BY conversation_id"
        ).fetchall()
        self.assertEqual(len(analysis_rows), 2)
        self.assertEqual(len({row["id"] for row in analysis_rows}), 1)
        self.assertEqual(len({row["membership_id"] for row in analysis_rows}), 2)

    def test_identical_attachment_blob_occurs_twice_and_retry_is_idempotent(self):
        staging = self._write_multichat_bundle()
        first = ingest_a1_staging_bundle(self.db, staging)
        self.assertFalse(first.already_imported)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM attachment").fetchone()[0], 1)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_attachment_occurrence").fetchone()[0],
            2,
        )
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM attachment_source").fetchone()[0], 2)
        positions = [
            row["position"]
            for row in self.db.conn.execute(
                "SELECT position FROM analysis_attachments ORDER BY position"
            ).fetchall()
        ]
        self.assertEqual(positions, [0, 1])

        repeated = ingest_a1_staging_bundle(self.db, staging)
        self.assertTrue(repeated.already_imported)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM attachment").fetchone()[0], 1)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_attachment_occurrence").fetchone()[0],
            2,
        )
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM attachment_source").fetchone()[0], 2)

        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])


if __name__ == "__main__":
    unittest.main()
