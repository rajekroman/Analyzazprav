from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


class A2DirectionContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = CanonicalDatabase(self.root / "messages.sqlite")
        self.db.initialize()
        self.staging = self.root / "staging"
        self.staging.mkdir()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _write(self, records: list[dict], *, source_char: str) -> None:
        source_sha = source_char * 64
        manifest = {
            "contract_version": "1",
            "source": {
                "type": "generic_message_json",
                "name": "messages.json",
                "sha256": source_sha,
            },
            "parser": {"name": "direction-fixture", "version": "1"},
            "outputs": {"messages": "messages.jsonl"},
            "counts": {
                "messages_seen": len(records),
                "attachments_seen": 0,
                "errors": 0,
            },
        }
        normalized = []
        for index, record in enumerate(records, start=1):
            item = {
                "contract_version": "1",
                "record_type": "message",
                "source_type": "generic_message_json",
                "source_sha256": source_sha,
                "source_message_id": str(index),
                "source_record_key": f"{source_char}{index}" * 32,
                "source_guid": f"DIRECTION-{source_char}-{index}",
                "conversation_source_id": "chat-direction",
                "timestamp_raw": index,
                "timestamp_utc": f"2026-08-16T07:00:0{index}Z",
                "timestamp_precision": "second",
                "sender_handle": record.pop("sender_handle", None),
                "text": f"message-{index}",
                "raw_text": f"message-{index}",
                "text_source": "text",
                "service": None,
                "reply_to_guid": None,
                "attachments": [],
                "raw_payload": {"fixture": index},
                "metadata": {},
            }
            item.update(record)
            normalized.append(item)

        (self.staging / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (self.staging / "messages.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in normalized),
            encoding="utf-8",
        )

    def test_true_false_null_and_omitted_direction_are_distinct(self):
        self._write(
            [
                {"is_from_me": True},
                {"is_from_me": False, "sender_handle": "incoming@example.com"},
                {"is_from_me": None, "sender_handle": "unknown@example.com"},
                {},
            ],
            source_char="a",
        )
        result = ingest_a1_staging_bundle(self.db, self.staging)
        self.assertEqual(result.messages, 4)

        rows = self.db.conn.execute(
            """SELECT ms.source_message_id, m.direction, p.is_self, ms.metadata_json
               FROM message_source ms
               JOIN message m ON m.id=ms.message_id
               LEFT JOIN participant p ON p.id=m.sender_id
               ORDER BY CAST(ms.source_message_id AS INTEGER)"""
        ).fetchall()
        self.assertEqual(
            [(row["source_message_id"], row["direction"]) for row in rows],
            [
                ("1", "outgoing"),
                ("2", "incoming"),
                ("3", "unknown"),
                ("4", "unknown"),
            ],
        )
        self.assertEqual(rows[0]["is_self"], 1)
        self.assertEqual(rows[1]["is_self"], 0)
        self.assertEqual(rows[2]["is_self"], 0)
        self.assertIsNone(rows[3]["is_self"])

        observations = [
            json.loads(row["metadata_json"])["a1_is_from_me"] for row in rows
        ]
        self.assertEqual(observations, [True, False, None, None])

    def test_non_boolean_is_from_me_fails_instead_of_using_python_truthiness(self):
        self._write(
            [{"is_from_me": "false"}],
            source_char="b",
        )
        with self.assertRaisesRegex(ValueError, "is_from_me must be a boolean or null"):
            ingest_a1_staging_bundle(self.db, self.staging)

        run = self.db.conn.execute("SELECT status FROM import_run").fetchone()
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 0)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_source").fetchone()[0], 0
        )
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM analysis_messages").fetchone()[0], 0
        )

        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])


if __name__ == "__main__":
    unittest.main()
