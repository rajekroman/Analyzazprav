from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qa.staging_validator import STATUS_FAIL, STATUS_PASS, validate_staging_dir


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class StagingValidatorTests(unittest.TestCase):
    def test_golden_fixture_passes_and_is_deterministic(self) -> None:
        first = validate_staging_dir(FIXTURES / "golden")
        second = validate_staging_dir(FIXTURES / "golden")

        self.assertEqual(STATUS_PASS, first["status"])
        self.assertEqual(first["fingerprints"], second["fingerprints"])
        self.assertEqual(4, first["counts"]["records"])
        self.assertEqual(1, first["counts"]["attachments"])
        self.assertEqual(0, first["counts"]["errors"])
        self.assertEqual(0, first["counts"]["warnings"])

    def test_corrupt_fixture_fails_without_deleting_records(self) -> None:
        report = validate_staging_dir(FIXTURES / "corrupt")

        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertEqual(2, report["counts"]["records"])
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("SOURCE_RECORD_KEY_DUPLICATE", codes)
        self.assertIn("TIMESTAMP_INVALID", codes)

    def test_manifest_count_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(
                json.dumps({"message_count": 2}),
                encoding="utf-8",
            )
            (root / "messages.jsonl").write_text(
                json.dumps(
                    {
                        "source_record_key": "imessage:1",
                        "timestamp_utc": "2026-08-16T05:00:00Z",
                        "attachments": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = validate_staging_dir(root)

        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertTrue(
            any(item["code"] == "MANIFEST_COUNT_MISMATCH" for item in report["issues"])
        )

    def test_missing_local_attachment_is_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(
                json.dumps({"message_count": 1}),
                encoding="utf-8",
            )
            record = {
                "source_record_key": "imessage:1",
                "timestamp_utc": "2026-08-16T05:00:00Z",
                "attachments": [{"relative_path": "attachments/missing.jpg"}],
            }
            (root / "messages.jsonl").write_text(
                json.dumps(record) + "\n",
                encoding="utf-8",
            )

            report = validate_staging_dir(root)

        self.assertEqual("WARNING", report["status"])
        self.assertEqual(1, report["counts"]["missing_attachments"])


if __name__ == "__main__":
    unittest.main()
