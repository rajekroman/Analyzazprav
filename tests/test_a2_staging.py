from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


class A2StagingTests(unittest.TestCase):
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

    def _write_bundle(self, parser_version="0.2.0", errors=0):
        staging = Path(self.tmp.name) / "staging"
        staging.mkdir(exist_ok=True)
        source_sha = "a" * 64
        manifest = {
            "contract_version": "1",
            "source": {"type": "imessage_chat_db", "name": "chat.db", "sha256": source_sha},
            "parser": {"name": "imessage-chatdb", "version": parser_version},
            "outputs": {"messages": "messages.jsonl"},
            "counts": {"messages_seen": 2, "attachments_seen": 1, "errors": errors},
        }
        (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        common = {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": source_sha,
            "conversation_source_id": "chat-7",
            "timestamp_precision": "nanosecond",
            "service": "iMessage",
            "raw_payload": {"fixture": True},
            "metadata": {},
        }
        records = [
            {
                **common,
                "source_message_id": "101",
                "source_guid": "GUID-A",
                "source_record_key": "1" * 64,
                "timestamp_raw": 808012800000000000,
                "timestamp_utc": "2026-08-08T00:00:00Z",
                "sender_handle": None,
                "is_from_me": True,
                "text": "Ahoj",
                "raw_text": "Ahoj",
                "text_source": "text",
                "reply_to_guid": None,
                "attachments": [],
            },
            {
                **common,
                "source_message_id": "102",
                "source_guid": "GUID-B",
                "source_record_key": "2" * 64,
                "timestamp_raw": 808012805000000000,
                "timestamp_utc": "2026-08-08T00:00:05Z",
                "sender_handle": "USER@example.com",
                "is_from_me": False,
                "text": None,
                "raw_text": None,
                "text_source": None,
                "reply_to_guid": "GUID-A",
                "attachments": [{
                    "source_attachment_id": "501",
                    "filename": "photo.heic",
                    "mime_type": "image/heic",
                    "transfer_name": "photo.heic",
                    "total_bytes": 123,
                    "source_path": "/definitely/missing/photo.heic",
                    "sha256": None,
                    "raw_payload": {"rowid": 501},
                }],
            },
        ]
        (staging / "messages.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )
        return staging

    def test_migration_chain_is_recorded(self):
        rows = self.db.conn.execute(
            "SELECT version, name FROM schema_migration ORDER BY version"
        ).fetchall()
        self.assertEqual(
            [(r["version"], r["name"]) for r in rows],
            [(1, "001_initial.sql"), (2, "002_a1_staging_contract.sql")],
        )
        version = self.db.conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"]
        self.assertEqual(version, "2")

    def test_v1_database_upgrades_to_v2_without_data_loss(self):
        legacy_path = Path(self.tmp.name) / "legacy.sqlite"
        conn = sqlite3.connect(legacy_path)
        conn.executescript((ROOT / "database" / "migrations" / "001_initial.sql").read_text(encoding="utf-8"))
        conn.execute(
            """INSERT INTO import_run(
                   source_type, source_fingerprint, started_at_utc_us, status
               ) VALUES ('fixture', 'legacy', 1, 'completed')"""
        )
        run_id = conn.execute("SELECT id FROM import_run").fetchone()[0]
        conn.execute("INSERT INTO conversation(canonical_key) VALUES ('legacy-conversation')")
        conversation_id = conn.execute("SELECT id FROM conversation").fetchone()[0]
        conn.execute(
            """INSERT INTO message(
                   conversation_id, sent_at_utc_us, direction, created_import_id
               ) VALUES (?, 123, 'incoming', ?)""",
            (conversation_id, run_id),
        )
        message_id = conn.execute("SELECT id FROM message").fetchone()[0]
        conn.execute(
            """INSERT INTO message_source(
                   message_id, import_run_id, source_type, source_message_id,
                   source_hash, raw_payload_json
               ) VALUES (?, ?, 'fixture', 'legacy-message', 'legacy-hash', '{}')""",
            (message_id, run_id),
        )
        conn.commit()
        conn.close()

        upgraded = CanonicalDatabase(
            legacy_path,
            migrations_path=ROOT / "database" / "migrations",
        )
        try:
            upgraded.initialize()
            columns = {
                row["name"] for row in upgraded.conn.execute("PRAGMA table_info(message_source)")
            }
            self.assertIn("source_record_key", columns)
            self.assertIn("source_contract_version", columns)
            self.assertIsNone(
                upgraded.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='duplicate_candidate'"
                ).fetchone()
            )
            self.assertEqual(
                upgraded.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 1
            )
            self.assertEqual(
                upgraded.conn.execute("SELECT COUNT(*) FROM message_source").fetchone()[0], 1
            )
            rows = upgraded.conn.execute(
                "SELECT version FROM schema_migration ORDER BY version"
            ).fetchall()
            self.assertEqual([row["version"] for row in rows], [1, 2])
            self.assertEqual(
                upgraded.conn.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()["value"],
                "2",
            )
            self.assertEqual(upgraded.integrity_report()["integrity"], "ok")
            self.assertEqual(upgraded.integrity_report()["foreign_key_errors"], [])
        finally:
            upgraded.close()

    def test_a1_bundle_ingest_preserves_provenance_and_relations(self):
        result = ingest_a1_staging_bundle(self.db, self._write_bundle())
        self.assertEqual((result.messages, result.attachments, result.relations), (2, 1, 1))
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 2)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM participant").fetchone()[0], 2)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_relation").fetchone()[0], 1)
        self.assertEqual(
            self.db.conn.execute("SELECT availability FROM attachment").fetchone()["availability"],
            "missing",
        )
        msg = self.db.conn.execute(
            "SELECT timestamp_precision, timestamp_quality FROM message WHERE canonical_guid='GUID-A'"
        ).fetchone()
        self.assertEqual((msg["timestamp_precision"], msg["timestamp_quality"]), ("microsecond", "converted"))
        source = self.db.conn.execute(
            "SELECT source_record_key, source_contract_version, metadata_json FROM message_source WHERE source_message_id='101'"
        ).fetchone()
        self.assertEqual(source["source_record_key"], "1" * 64)
        self.assertEqual(source["source_contract_version"], "1")
        self.assertEqual(json.loads(source["metadata_json"])["a1_source_timestamp_precision"], "nanosecond")
        repeated = ingest_a1_staging_bundle(self.db, Path(self.tmp.name) / "staging")
        self.assertTrue(repeated.already_imported)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_source").fetchone()[0], 2)

    def test_new_parser_version_reuses_canonical_messages_but_adds_provenance(self):
        staging = self._write_bundle(parser_version="0.2.0")
        first = ingest_a1_staging_bundle(self.db, staging)
        self._write_bundle(parser_version="0.3.0")
        second = ingest_a1_staging_bundle(self.db, staging)
        self.assertNotEqual(first.import_run_id, second.import_run_id)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 2)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_source").fetchone()[0], 4)

    def test_manifest_with_extraction_errors_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "extraction errors"):
            ingest_a1_staging_bundle(self.db, self._write_bundle(errors=1))
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM import_run").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
