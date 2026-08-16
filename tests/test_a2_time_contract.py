from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


class A2ExplicitTimeTests(unittest.TestCase):
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

    def _bundle(self, **time_fields):
        staging = Path(self.tmp.name) / "staging"
        staging.mkdir(exist_ok=True)
        source_sha = "b" * 64
        manifest = {
            "contract_version": "1",
            "source": {"type": "fixture", "name": "fixture.json", "sha256": source_sha},
            "parser": {"name": "fixture", "version": "1.0"},
            "outputs": {"messages": "messages.jsonl"},
            "counts": {"messages_seen": 1, "attachments_seen": 0, "errors": 0},
        }
        record = {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "fixture",
            "source_sha256": source_sha,
            "source_record_key": "c" * 64,
            "source_message_id": "1",
            "source_guid": "TIME-GUID-1",
            "conversation_source_id": "c1",
            "timestamp_raw": "raw",
            "timestamp_utc": "2026-08-08T00:00:00Z",
            "timestamp_precision": "second",
            "sender_handle": "user@example.com",
            "is_from_me": False,
            "text": "time",
            "raw_text": "time",
            "text_source": "text",
            "service": "iMessage",
            "reply_to_guid": None,
            "attachments": [],
            "raw_payload": {},
            "metadata": {},
            **time_fields,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (staging / "messages.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
        return staging

    def test_explicit_local_time_is_preserved_without_utc_derivation(self):
        result = ingest_a1_staging_bundle(
            self.db,
            self._bundle(
                timestamp_local="2026-08-08T02:00:00+02:00",
                timezone_name="Europe/Prague",
                timezone_offset_min=120,
            ),
        )
        row = self.db.conn.execute(
            """SELECT sent_at_utc_us, sent_at_local_iso, timezone_name, timezone_offset_min
               FROM message WHERE canonical_guid='TIME-GUID-1'"""
        ).fetchone()
        self.assertEqual(row["sent_at_local_iso"], "2026-08-08T02:00:00+02:00")
        self.assertEqual(row["timezone_name"], "Europe/Prague")
        self.assertEqual(row["timezone_offset_min"], 120)
        source = self.db.conn.execute(
            "SELECT metadata_json FROM message_source WHERE import_run_id=?",
            (result.import_run_id,),
        ).fetchone()
        observation = json.loads(source["metadata_json"])["a1_time_observation"]
        self.assertEqual(observation["timestamp_local"], "2026-08-08T02:00:00+02:00")
        self.assertEqual(observation["timezone_name"], "Europe/Prague")
        self.assertEqual(observation["offset_origin"], "timezone_offset_min")

    def test_offset_may_be_read_from_explicit_local_iso(self):
        ingest_a1_staging_bundle(
            self.db,
            self._bundle(timestamp_local="2026-08-08T05:30:00+05:30"),
        )
        row = self.db.conn.execute(
            "SELECT timezone_offset_min FROM message WHERE canonical_guid='TIME-GUID-1'"
        ).fetchone()
        self.assertEqual(row["timezone_offset_min"], 330)

    def test_naive_local_time_fails_before_import(self):
        with self.assertRaisesRegex(ValueError, "explicit UTC offset"):
            ingest_a1_staging_bundle(
                self.db,
                self._bundle(timestamp_local="2026-08-08T02:00:00"),
            )
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM import_run").fetchone()[0], 0)

    def test_disagreeing_offsets_fail_before_import(self):
        with self.assertRaisesRegex(ValueError, "disagrees"):
            ingest_a1_staging_bundle(
                self.db,
                self._bundle(
                    timestamp_local="2026-08-08T02:00:00+02:00",
                    timezone_offset_min=60,
                ),
            )
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM import_run").fetchone()[0], 0)

    def test_database_rejects_impossible_timezone_offset(self):
        run = self.db.begin_import(source_type="fixture", source_fingerprint="offset-guard")
        self.db.conn.execute("INSERT INTO conversation(canonical_key) VALUES ('offset-test')")
        conversation_id = self.db.conn.execute("SELECT id FROM conversation").fetchone()[0]
        with self.assertRaisesRegex(Exception, "timezone_offset_min out of range"):
            self.db.conn.execute(
                """INSERT INTO message(
                       conversation_id, timezone_offset_min, direction, created_import_id
                   ) VALUES (?, 900, 'incoming', ?)""",
                (conversation_id, run.id),
            )


if __name__ == "__main__":
    unittest.main()
