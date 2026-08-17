from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


class A2RelationProvenanceTests(unittest.TestCase):
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

    def _write_bundle(
        self,
        *,
        parser_version: str,
        include_target: bool,
        source_sha: str = "r" * 64,
        include_reply: bool = True,
    ) -> None:
        records: list[dict] = []
        common = {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": source_sha,
            "conversation_source_id": "chat-relations",
            "timestamp_precision": "nanosecond",
            "sender_handle": None,
            "is_from_me": True,
            "service": "iMessage",
            "attachments": [],
            "metadata": {},
        }
        if include_target:
            records.append(
                {
                    **common,
                    "source_message_id": "10",
                    "source_guid": "TARGET-GUID",
                    "source_record_key": "t" * 64,
                    "timestamp_raw": 10,
                    "timestamp_utc": "2026-08-16T07:00:00Z",
                    "text": "target",
                    "raw_text": "target",
                    "text_source": "text",
                    "reply_to_guid": None,
                    "raw_payload": {"rowid": 10},
                }
            )
        records.append(
            {
                **common,
                "source_message_id": "20",
                "source_guid": "SOURCE-GUID",
                "source_record_key": "s" * 64,
                "timestamp_raw": 20,
                "timestamp_utc": "2026-08-16T07:00:01Z",
                "text": "reply",
                "raw_text": "reply",
                "text_source": "text",
                "reply_to_guid": "TARGET-GUID" if include_reply else None,
                "raw_payload": {"rowid": 20},
            }
        )
        manifest = {
            "contract_version": "1",
            "source": {
                "type": "imessage_chat_db",
                "name": "chat.db",
                "sha256": source_sha,
            },
            "parser": {"name": "imessage-chatdb", "version": parser_version},
            "outputs": {"messages": "messages.jsonl"},
            "counts": {
                "messages_seen": len(records),
                "attachments_seen": 0,
                "errors": 0,
            },
        }
        (self.staging / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (self.staging / "messages.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    def test_resolved_reply_has_source_and_canonical_relation(self):
        self._write_bundle(parser_version="1.0", include_target=True)
        result = ingest_a1_staging_bundle(self.db, self.staging)

        self.assertEqual(result.relations, 1)
        self.assertEqual(result.source_relations, 1)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_relation").fetchone()[0], 1
        )
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_relation_source").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM analysis_message_relation_sources"
            ).fetchone()[0],
            1,
        )

        row = self.db.conn.execute(
            """SELECT source_relation_id, source_message_id, relation_type,
                      target_source_guid, target_service, resolution_status,
                      resolved_relation_id, resolved_target_message_id,
                      source_sha256, parser_version, metadata_json
               FROM analysis_message_relation_sources"""
        ).fetchone()
        self.assertEqual(row["relation_type"], "reply_to")
        self.assertEqual(row["target_source_guid"], "TARGET-GUID")
        self.assertEqual(row["target_service"], "iMessage")
        self.assertEqual(row["resolution_status"], "resolved")
        self.assertIsNotNone(row["resolved_relation_id"])
        self.assertEqual(row["source_sha256"], "r" * 64)
        self.assertEqual(row["parser_version"], "1.0")
        self.assertEqual(json.loads(row["metadata_json"])["source"], "a1.reply_to_guid")

        canonical = self.db.conn.execute(
            """SELECT mr.source_message_id, mr.target_message_id, mr.relation_type,
                      source.canonical_guid AS source_guid,
                      target.canonical_guid AS target_guid
               FROM message_relation mr
               JOIN message source ON source.id=mr.source_message_id
               JOIN message target ON target.id=mr.target_message_id"""
        ).fetchone()
        self.assertEqual(canonical["relation_type"], "reply_to")
        self.assertEqual(canonical["source_guid"], "SOURCE-GUID")
        self.assertEqual(canonical["target_guid"], "TARGET-GUID")
        self.assertEqual(row["source_message_id"], canonical["source_message_id"])
        self.assertEqual(row["resolved_target_message_id"], canonical["target_message_id"])

    def test_unresolved_reply_is_preserved_instead_of_collapsing_to_absent(self):
        self._write_bundle(parser_version="1.0", include_target=False)
        result = ingest_a1_staging_bundle(self.db, self.staging)

        self.assertEqual(result.relations, 0)
        self.assertEqual(result.source_relations, 1)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_relation").fetchone()[0], 0
        )
        row = self.db.conn.execute(
            """SELECT relation_type, target_source_guid, target_service,
                      resolution_status, resolved_relation_id,
                      resolved_target_message_id
               FROM analysis_message_relation_sources"""
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["relation_type"], "reply_to")
        self.assertEqual(row["target_source_guid"], "TARGET-GUID")
        self.assertEqual(row["target_service"], "iMessage")
        self.assertEqual(row["resolution_status"], "unresolved")
        self.assertIsNone(row["resolved_relation_id"])
        self.assertIsNone(row["resolved_target_message_id"])

        no_relation_root = self.root / "no-relation"
        no_relation_root.mkdir()
        old_staging = self.staging
        self.staging = no_relation_root
        try:
            self._write_bundle(
                parser_version="2.0",
                include_target=False,
                source_sha="n" * 64,
                include_reply=False,
            )
            no_relation = ingest_a1_staging_bundle(self.db, self.staging)
            self.assertEqual(no_relation.source_relations, 0)
        finally:
            self.staging = old_staging
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_relation_source").fetchone()[0],
            1,
        )

    def test_parser_rerun_reuses_canonical_relation_but_adds_source_provenance(self):
        self._write_bundle(parser_version="1.0", include_target=True)
        first = ingest_a1_staging_bundle(self.db, self.staging)
        self._write_bundle(parser_version="2.0", include_target=True)
        second = ingest_a1_staging_bundle(self.db, self.staging)

        self.assertNotEqual(first.import_run_id, second.import_run_id)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 2)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_relation").fetchone()[0], 1
        )
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_relation_source").fetchone()[0],
            2,
        )
        rows = self.db.conn.execute(
            """SELECT parser_version, resolution_status, resolved_relation_id
               FROM analysis_message_relation_sources ORDER BY import_run_id"""
        ).fetchall()
        self.assertEqual([row["parser_version"] for row in rows], ["1.0", "2.0"])
        self.assertEqual([row["resolution_status"] for row in rows], ["resolved", "resolved"])
        self.assertEqual(len({row["resolved_relation_id"] for row in rows}), 1)

        repeated = ingest_a1_staging_bundle(self.db, self.staging)
        self.assertTrue(repeated.already_imported)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_relation_source").fetchone()[0],
            2,
        )

    def test_later_target_import_does_not_retroactively_mutate_unresolved_provenance(self):
        self._write_bundle(parser_version="1.0", include_target=False, source_sha="u" * 64)
        unresolved = ingest_a1_staging_bundle(self.db, self.staging)
        source_relation_id = self.db.conn.execute(
            "SELECT id FROM message_relation_source"
        ).fetchone()[0]

        target_staging = self.root / "target-staging"
        target_staging.mkdir()
        self.staging = target_staging
        target_common = {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": "v" * 64,
            "source_message_id": "10",
            "source_guid": "TARGET-GUID",
            "source_record_key": "x" * 64,
            "conversation_source_id": "chat-relations",
            "timestamp_raw": 10,
            "timestamp_utc": "2026-08-16T07:00:02Z",
            "timestamp_precision": "nanosecond",
            "sender_handle": None,
            "is_from_me": True,
            "text": "later target",
            "raw_text": "later target",
            "text_source": "text",
            "service": "iMessage",
            "reply_to_guid": None,
            "attachments": [],
            "raw_payload": {"rowid": 10},
            "metadata": {},
        }
        manifest = {
            "contract_version": "1",
            "source": {
                "type": "imessage_chat_db",
                "name": "chat.db",
                "sha256": "v" * 64,
            },
            "parser": {"name": "imessage-chatdb", "version": "1.0"},
            "outputs": {"messages": "messages.jsonl"},
            "counts": {"messages_seen": 1, "attachments_seen": 0, "errors": 0},
        }
        (self.staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (self.staging / "messages.jsonl").write_text(
            json.dumps(target_common) + "\n", encoding="utf-8"
        )
        ingest_a1_staging_bundle(self.db, self.staging)

        row = self.db.conn.execute(
            """SELECT resolution_status, resolved_relation_id
               FROM message_relation_source WHERE id=?""",
            (source_relation_id,),
        ).fetchone()
        self.assertEqual(row["resolution_status"], "unresolved")
        self.assertIsNone(row["resolved_relation_id"])
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM message_relation").fetchone()[0], 0
        )
        self.assertFalse(unresolved.already_imported)

        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])


if __name__ == "__main__":
    unittest.main()
