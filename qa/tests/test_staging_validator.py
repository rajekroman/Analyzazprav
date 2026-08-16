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


class StagingValidatorTests(unittest.TestCase):
    def test_golden_fixture_passes_and_is_deterministic(self) -> None:
        first = validate_staging_dir(FIXTURES / "golden")
        second = validate_staging_dir(FIXTURES / "golden")
        self.assertEqual(STATUS_PASS, first["status"])
        self.assertEqual(first["fingerprints"], second["fingerprints"])
        self.assertEqual(4, first["counts"]["records"])
        self.assertEqual(1, first["counts"]["attachments"])

    def test_corrupt_fixture_fails_without_deleting_records(self) -> None:
        report = validate_staging_dir(FIXTURES / "corrupt")
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertEqual(2, report["counts"]["records"])
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("SOURCE_RECORD_KEY_DUPLICATE", codes)
        self.assertIn("TIMESTAMP_INVALID", codes)

    def test_real_a1_contract_manifest_provenance_and_key_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_sha = hashlib.sha256(b"chat.db fixture").hexdigest()
            manifest = {
                "contract_version": "1",
                "source": {"type": "imessage_chat_db", "name": "chat.db", "sha256": source_sha},
                "parser": {"name": "imessage-chatdb", "version": "0.2.0"},
                "outputs": {"messages": "messages.jsonl"},
                "counts": {"messages_seen": 1, "attachments_seen": 0, "errors": 0},
            }
            key = stable_message_key(source_sha, "guid-1", "1", "chat-1")
            record = {
                "contract_version": "1",
                "record_type": "message",
                "source_type": "imessage_chat_db",
                "source_sha256": source_sha,
                "source_record_key": key,
                "source_message_id": "1",
                "source_guid": "guid-1",
                "conversation_source_id": "chat-1",
                "timestamp_raw": 0,
                "timestamp_utc": "2001-01-01T00:00:00Z",
                "timestamp_precision": "second",
                "attachments": [],
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "messages.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            report = validate_staging_dir(root)
            self.assertEqual(STATUS_PASS, report["status"])

            record["source_record_key"] = "0" * 64
            (root / "messages.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            bad_key = validate_staging_dir(root)
            self.assertEqual(STATUS_FAIL, bad_key["status"])
            self.assertIn("SOURCE_RECORD_KEY_MISMATCH", {i["code"] for i in bad_key["issues"]})

            record["source_record_key"] = key
            record["source_sha256"] = "0" * 64
            (root / "messages.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            bad_source = validate_staging_dir(root)
            self.assertEqual(STATUS_FAIL, bad_source["status"])
            self.assertIn("SOURCE_SHA256_MISMATCH", {i["code"] for i in bad_source["issues"]})

    def test_manifest_messages_seen_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(
                json.dumps({"counts": {"messages_seen": 2, "attachments_seen": 0, "errors": 0}}),
                encoding="utf-8",
            )
            (root / "messages.jsonl").write_text(
                json.dumps(
                    {
                        "source_record_key": "a" * 64,
                        "source_message_id": "1",
                        "conversation_source_id": "c1",
                        "timestamp_utc": "2026-08-16T05:00:00Z",
                        "attachments": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            report = validate_staging_dir(root)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("MANIFEST_COUNT_MISMATCH", {i["code"] for i in report["issues"]})

    def test_missing_local_attachment_is_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(json.dumps({"message_count": 1}), encoding="utf-8")
            record = {
                "source_record_key": "b" * 64,
                "source_message_id": "1",
                "conversation_source_id": "c1",
                "timestamp_utc": "2026-08-16T05:00:00Z",
                "attachments": [{"relative_path": "attachments/missing.jpg"}],
            }
            (root / "messages.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            report = validate_staging_dir(root)
        self.assertEqual("WARNING", report["status"])
        self.assertEqual(1, report["counts"]["missing_attachments"])


if __name__ == "__main__":
    unittest.main()
