from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qa.release_verdict import INVALID, NEEDS_REVIEW, VALID, aggregate_release_verdict


class ReleaseVerdictTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload: dict) -> None:
        (root / name).write_text(json.dumps(payload), encoding="utf-8")

    def test_current_expected_state_is_invalid_only_because_a6(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "a7-a4-report.json", {"schema_version": 1, "verdict": "VALID", "issues": []})
            self._write(root, "a7-a5-report.json", {"schema_version": 1, "verdict": "VALID", "issues": []})
            self._write(root, "a7-a6-report.json", {"schema_version": 1, "verdict": "INVALID", "issues": [{"code": "loss"}]})
            report = aggregate_release_verdict(
                core_job="success",
                a4_job="success",
                a5_job="success",
                a6_job="success",
                report_dir=root,
            )
        self.assertEqual(INVALID, report["overall_verdict"])
        self.assertFalse(report["release_ready"])
        self.assertEqual(["A6 UI/read model"], [row["component"] for row in report["blockers"]])

    def test_all_valid_is_release_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a7-a4-report.json", "a7-a5-report.json", "a7-a6-report.json"):
                self._write(root, name, {"schema_version": 1, "verdict": "VALID", "issues": []})
            report = aggregate_release_verdict(
                core_job="success",
                a4_job="success",
                a5_job="success",
                a6_job="success",
                report_dir=root,
            )
        self.assertEqual(VALID, report["overall_verdict"])
        self.assertTrue(report["release_ready"])
        self.assertEqual([], report["blockers"])

    def test_missing_successful_audit_report_needs_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "a7-a4-report.json", {"verdict": "VALID"})
            self._write(root, "a7-a5-report.json", {"verdict": "VALID"})
            report = aggregate_release_verdict(
                core_job="success",
                a4_job="success",
                a5_job="success",
                a6_job="success",
                report_dir=root,
            )
        self.assertEqual(NEEDS_REVIEW, report["overall_verdict"])
        self.assertEqual("A6 UI/read model", report["blockers"][0]["component"])

    def test_failed_job_is_invalid_even_if_stale_report_says_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a7-a4-report.json", "a7-a5-report.json", "a7-a6-report.json"):
                self._write(root, name, {"verdict": "VALID"})
            report = aggregate_release_verdict(
                core_job="success",
                a4_job="failure",
                a5_job="success",
                a6_job="success",
                report_dir=root,
            )
        self.assertEqual(INVALID, report["overall_verdict"])
        self.assertEqual("A4 analytics", report["blockers"][0]["component"])


if __name__ == "__main__":
    unittest.main()
