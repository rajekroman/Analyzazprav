from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from qa.staging_validator import STATUS_FAIL, STATUS_PASS, validate_staging_dir


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def stable_message_key(*parts: object) -> str:
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def write_v2_bundle(root: Path, *, seen: int = 2, emitted: int = 2, errors: list[dict] | None = None) -> None:
    source_sha = hashlib.sha256(b"logical sqlite snapshot").hexdigest()
    error_rows = errors or []
    records = [
        {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": source_sha,
            "source_record_key": stable_message_key(source_sha, "message", "10"),
            "source_message_id": "10",
            "source_guid": "GUID-10",
            "conversation_source_id": "guid:chat-a",
            "conversation_sources": [
                {"source_conversation_key": "guid:chat-a", "chat_guid": "chat-a"},
                {"source_conversation_key": "guid:chat-b", "chat_guid": "chat-b"},
            ],
            "timestamp_raw": 0,
            "timestamp_utc": "2001-01-01T00:00:00Z",
            "timestamp_precision": "second",
            "timestamp_local": "2001-01-01T01:00:00+01:00",
            "timezone_offset_min": 60,
            "sender_handle": "+420111222333",
            "is_from_me": False,
            "text": "Ahoj",
            "raw_text": "Ahoj",
            "service": "iMessage",
            "attachments": [
                {
                    "source_attachment_id": "22",
                    "filename": "a.jpg",
                    "mime_type": "image/jpeg",
                    "transfer_name": "a.jpg",
                    "total_bytes": 123,
                    "source_path": None,
                    "sha256": None,
                    "resolved_path": None,
                    "resolution_status": "no_path",
                    "actual_bytes": None,
                    "raw_payload": {},
                }
            ],
            "raw_payload": {},
            "metadata": {},
        },
        {
            "contract_version": "1",
            "record_type": "message",
            "source_type": "imessage_chat_db",
            "source_sha256": source_sha,
            "source_record_key": stable_message_key(source_sha, "message", "11"),
            "source_message_id": "11",
            "source_guid": "GUID-11",
            "conversation_source_id": "guid:chat-b",
            "conversation_sources": [
                {"source_conversation_key": "guid:chat-b", "chat_guid": "chat-b"}
            ],
            "timestamp_raw": 60,
            "timestamp_utc": "2001-01-01T00:01:00Z",
            "timestamp_precision": "second",
            "sender_handle": None,
            "is_from_me": True,
            "text": "Odpověď",
            "raw_text": "Odpověď",
            "service": "iMessage",
            "attachments": [],
            "raw_payload": {},
            "metadata": {},
        },
    ][:emitted]
    manifest = {
        "contract_version": "1",
        "source": {
            "type": "imessage_chat_db",
            "name": "chat.db",
            "sha256": source_sha,
            "snapshot_method": "sqlite_online_backup_v1",
            "snapshot_includes_committed_wal": True,
        },
        "parser": {"name": "imessage-chatdb", "version": "0.4.0"},
        "source_record_key": {
            "algorithm": "sha256-unit-separator",
            "version": "2",
            "scope": "source_snapshot+message_rowid",
        },
        "outputs": {"messages": "messages.jsonl", "errors": "errors.jsonl"},
        "counts": {
            "messages_seen": seen,
            "messages_emitted": emitted,
            "attachments_seen": 1 if emitted else 0,
            "attachments_resolved": 0,
            "attachments_missing": 0,
            "errors": len(error_rows),
        },
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "messages.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    (root / "errors.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in error_rows), encoding="utf-8"
    )


class StagingValidatorTests(unittest.TestCase):
    def test_golden_fixture_passes_and_is_deterministic(self) -> None:
        first = validate_staging_dir(FIXTURES / "golden")
        second = validate_staging_dir(FIXTURES / "golden")
        self.assertEqual(STATUS_PASS, first["status"])
        self.assertEqual(first["fingerprints"], second["fingerprints"])
        self.assertEqual(4, first["counts"]["records"])

    def test_corrupt_fixture_fails_without_deleting_records(self) -> None:
        report = validate_staging_dir(FIXTURES / "corrupt")
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertEqual(2, report["counts"]["records"])
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("SOURCE_RECORD_KEY_DUPLICATE", codes)
        self.assertIn("TIMESTAMP_INVALID", codes)

    def test_current_imessage_v2_contract_and_multichat_relation_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_v2_bundle(root)
            report = validate_staging_dir(root)
        self.assertEqual(STATUS_PASS, report["status"])
        self.assertEqual(2, report["counts"]["messages_seen"])
        self.assertEqual(2, report["counts"]["messages_emitted"])
        self.assertEqual(3, report["counts"]["conversation_relations"])
        self.assertEqual(1, report["counts"]["attachments"])

    def test_import_reconciliation_must_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_v2_bundle(root, seen=3, emitted=2)
            report = validate_staging_dir(root)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("IMPORT_RECONCILIATION_MISMATCH", {i["code"] for i in report["issues"]})

    def test_error_jsonl_is_counted_and_export_errors_fail_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_v2_bundle(
                root,
                seen=3,
                emitted=2,
                errors=[
                    {
                        "source_message_id": "12",
                        "error_type": "ValueError",
                        "error": "synthetic failure",
                    }
                ],
            )
            report = validate_staging_dir(root)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertEqual(1, report["counts"]["error_records"])
        self.assertNotIn("IMPORT_RECONCILIATION_MISMATCH", {i["code"] for i in report["issues"]})
        self.assertIn("A1_EXPORT_ERRORS", {i["code"] for i in report["issues"]})

    def test_declared_errors_file_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_v2_bundle(root)
            (root / "errors.jsonl").unlink()
            report = validate_staging_dir(root)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("ERROR_MISSING_FILE", {i["code"] for i in report["issues"]})


if __name__ == "__main__":
    unittest.main()
