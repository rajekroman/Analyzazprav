from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle
from analyzazprav.normalization.relations import record_source_relation


class A2RelationProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = CanonicalDatabase(self.root / "messages.sqlite")
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _record(
        self,
        *,
        source_sha: str,
        source_message_id: str,
        source_record_key: str,
        guid: str,
        service: str | None = "iMessage",
        reply_to_guid: str | None = None,
        malformed: bool = False,
    ) -> dict:
        return {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": source_sha,
            "source_record_key": "" if malformed else source_record_key,
            "source_message_id": source_message_id,
            "source_guid": guid,
            "conversation_source_id": f"chat-{source_sha[0]}",
            "timestamp_raw": int(source_message_id),
            "timestamp_utc": f"2026-08-16T07:00:{int(source_message_id) % 60:02d}Z",
            "timestamp_precision": "nanosecond",
            "sender_handle": None,
            "is_from_me": True,
            "text": f"message-{source_message_id}",
            "raw_text": f"message-{source_message_id}",
            "text_source": "text",
            "service": service,
            "reply_to_guid": reply_to_guid,
            "attachments": [],
            "raw_payload": {"rowid": int(source_message_id)},
            "metadata": {},
        }

    def _write_bundle(
        self,
        name: str,
        *,
        source_sha: str,
        records: list[dict],
        parser_version: str = "relation-test",
    ) -> Path:
        staging = self.root / name
        staging.mkdir(exist_ok=True)
        manifest = {
            "contract_version": "1",
            "source": {
                "type": "imessage_chat_db",
                "name": f"{name}.db",
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
        (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (staging / "messages.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        return staging

    def test_unresolved_reply_is_preserved_as_source_fact(self):
        source_sha = "a" * 64
        staging = self._write_bundle(
            "unresolved",
            source_sha=source_sha,
            records=[
                self._record(
                    source_sha=source_sha,
                    source_message_id="1",
                    source_record_key="1" * 64,
                    guid="SOURCE-GUID",
                    reply_to_guid="MISSING-GUID",
                )
            ],
        )

        result = ingest_a1_staging_bundle(self.db, staging)
        self.assertEqual(result.messages, 1)
        self.assertEqual(result.relation_sources, 1)
        self.assertEqual(result.relations, 0)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_relation").fetchone()[0], 0)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_relation_source").fetchone()[0], 1)

        row = self.db.conn.execute(
            """SELECT source_record_key, relation_type, target_identifier_type,
                      target_identifier_value, target_service, resolution_status,
                      canonical_relation_id, canonical_target_message_id
               FROM analysis_message_relation_sources"""
        ).fetchone()
        self.assertEqual(row["source_record_key"], "1" * 64)
        self.assertEqual(row["relation_type"], "reply_to")
        self.assertEqual(row["target_identifier_type"], "guid")
        self.assertEqual(row["target_identifier_value"], "MISSING-GUID")
        self.assertEqual(row["target_service"], "iMessage")
        self.assertEqual(row["resolution_status"], "unresolved")
        self.assertIsNone(row["canonical_relation_id"])
        self.assertIsNone(row["canonical_target_message_id"])

    def test_later_exact_target_resolves_prior_source_fact(self):
        first_sha = "b" * 64
        first = self._write_bundle(
            "source-first",
            source_sha=first_sha,
            records=[
                self._record(
                    source_sha=first_sha,
                    source_message_id="2",
                    source_record_key="2" * 64,
                    guid="SOURCE-LATE",
                    reply_to_guid="TARGET-LATE",
                )
            ],
        )
        ingest_a1_staging_bundle(self.db, first)
        self.assertEqual(
            self.db.conn.execute(
                "SELECT resolution_status FROM analysis_message_relation_sources"
            ).fetchone()[0],
            "unresolved",
        )

        target_sha = "c" * 64
        second = self._write_bundle(
            "target-later",
            source_sha=target_sha,
            records=[
                self._record(
                    source_sha=target_sha,
                    source_message_id="3",
                    source_record_key="3" * 64,
                    guid="TARGET-LATE",
                )
            ],
        )
        ingest_a1_staging_bundle(self.db, second)

        row = self.db.conn.execute(
            """SELECT resolution_status, canonical_relation_id, canonical_target_message_id
               FROM analysis_message_relation_sources"""
        ).fetchone()
        self.assertEqual(row["resolution_status"], "resolved")
        self.assertIsNotNone(row["canonical_relation_id"])
        target_id = self.db.conn.execute(
            "SELECT id FROM message WHERE canonical_guid='TARGET-LATE' AND service='iMessage'"
        ).fetchone()[0]
        self.assertEqual(row["canonical_target_message_id"], target_id)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_relation").fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_relation_source").fetchone()[0], 1)

    def test_unknown_service_relation_does_not_resolve_to_known_service_target(self):
        source_sha = "d" * 64
        source_bundle = self._write_bundle(
            "unknown-service-source",
            source_sha=source_sha,
            records=[
                self._record(
                    source_sha=source_sha,
                    source_message_id="4",
                    source_record_key="4" * 64,
                    guid="SOURCE-NULL-SERVICE",
                    service=None,
                    reply_to_guid="TARGET-KNOWN-SERVICE",
                )
            ],
        )
        ingest_a1_staging_bundle(self.db, source_bundle)

        target_sha = "e" * 64
        target_bundle = self._write_bundle(
            "known-service-target",
            source_sha=target_sha,
            records=[
                self._record(
                    source_sha=target_sha,
                    source_message_id="5",
                    source_record_key="5" * 64,
                    guid="TARGET-KNOWN-SERVICE",
                    service="iMessage",
                )
            ],
        )
        ingest_a1_staging_bundle(self.db, target_bundle)

        row = self.db.conn.execute(
            """SELECT target_service, resolution_status, canonical_relation_id
               FROM analysis_message_relation_sources"""
        ).fetchone()
        self.assertIsNone(row["target_service"])
        self.assertEqual(row["resolution_status"], "unresolved")
        self.assertIsNone(row["canonical_relation_id"])
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_relation").fetchone()[0], 0)

    def test_relation_occurrence_position_keeps_identical_source_facts_distinct(self):
        source_sha = "f" * 64
        staging = self._write_bundle(
            "positions",
            source_sha=source_sha,
            records=[
                self._record(
                    source_sha=source_sha,
                    source_message_id="6",
                    source_record_key="6" * 64,
                    guid="POSITION-SOURCE",
                )
            ],
        )
        result = ingest_a1_staging_bundle(self.db, staging)
        source_id = self.db.conn.execute(
            "SELECT id FROM message_source WHERE import_run_id=?",
            (result.import_run_id,),
        ).fetchone()[0]

        first = record_source_relation(
            self.db,
            message_source_id=source_id,
            relation_type="source_association",
            target_identifier_type="guid",
            target_identifier_value="SAME-TARGET",
            target_service="iMessage",
            source_relation_type="raw-code",
            position=0,
        )
        second = record_source_relation(
            self.db,
            message_source_id=source_id,
            relation_type="source_association",
            target_identifier_type="guid",
            target_identifier_value="SAME-TARGET",
            target_service="iMessage",
            source_relation_type="raw-code",
            position=1,
        )
        repeated = record_source_relation(
            self.db,
            message_source_id=source_id,
            relation_type="source_association",
            target_identifier_type="guid",
            target_identifier_value="SAME-TARGET",
            target_service="iMessage",
            source_relation_type="raw-code",
            position=0,
        )
        self.assertNotEqual(first, second)
        self.assertEqual(first, repeated)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_relation_source").fetchone()[0], 2)

    def test_failed_import_rolls_back_relation_source_evidence(self):
        source_sha = "9" * 64
        staging = self._write_bundle(
            "relation-rollback",
            source_sha=source_sha,
            records=[
                self._record(
                    source_sha=source_sha,
                    source_message_id="7",
                    source_record_key="7" * 64,
                    guid="ROLLBACK-SOURCE",
                    reply_to_guid="ROLLBACK-MISSING",
                ),
                self._record(
                    source_sha=source_sha,
                    source_message_id="8",
                    source_record_key="8" * 64,
                    guid="ROLLBACK-BAD",
                    malformed=True,
                ),
            ],
        )

        with self.assertRaisesRegex(ValueError, "requires source_message_id and source_record_key"):
            ingest_a1_staging_bundle(self.db, staging)

        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 0)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_source").fetchone()[0], 0)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM message_relation_source").fetchone()[0], 0)
        run = self.db.conn.execute("SELECT status FROM import_run").fetchone()
        self.assertEqual(run["status"], "failed")
        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])


if __name__ == "__main__":
    unittest.main()
