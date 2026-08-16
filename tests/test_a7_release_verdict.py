from __future__ import annotations

import unittest

from tools.a7_release.release_verdict import aggregate_release_verdict

SHA = "1" * 40


def component(verdict="VALID", sha=SHA):
    return {"verdict": verdict, "contract_sha": sha}


class ReleaseVerdictTests(unittest.TestCase):
    def test_all_valid_same_sha_is_release_ready(self):
        report = aggregate_release_verdict(
            {"core": component(), "A5": component(), "A6": component()},
            job_results={"core": "success", "A5": "success", "A6": "success"},
            expected_sha=SHA,
        )
        self.assertEqual(report["overall_verdict"], "VALID")
        self.assertTrue(report["release_ready"])

    def test_missing_report_is_needs_review_not_ready(self):
        report = aggregate_release_verdict(
            {"core": component(), "A5": component(), "A6": None},
            job_results={"core": "success", "A5": "success", "A6": "success"},
            expected_sha=SHA,
        )
        self.assertEqual(report["overall_verdict"], "NEEDS_REVIEW")
        self.assertFalse(report["release_ready"])
        self.assertIn("A7_COMPONENT_REPORT_MISSING", {row["code"] for row in report["issues"]})

    def test_failed_job_is_invalid(self):
        report = aggregate_release_verdict(
            {"core": component(), "A5": component(), "A6": component()},
            job_results={"core": "success", "A5": "failure", "A6": "success"},
            expected_sha=SHA,
        )
        self.assertEqual(report["overall_verdict"], "INVALID")
        self.assertFalse(report["release_ready"])

    def test_component_invalid_is_invalid(self):
        report = aggregate_release_verdict(
            {"core": component(), "A5": component("INVALID"), "A6": component()},
            job_results={"core": "success", "A5": "success", "A6": "success"},
            expected_sha=SHA,
        )
        self.assertEqual(report["overall_verdict"], "INVALID")
        self.assertFalse(report["release_ready"])

    def test_sha_mismatch_is_invalid(self):
        report = aggregate_release_verdict(
            {"core": component(), "A5": component(sha="2" * 40), "A6": component()},
            job_results={"core": "success", "A5": "success", "A6": "success"},
            expected_sha=SHA,
        )
        self.assertEqual(report["overall_verdict"], "INVALID")
        self.assertFalse(report["release_ready"])
        self.assertIn("A7_COMPONENT_SHA_MISMATCH", {row["code"] for row in report["issues"]})

    def test_invalid_expected_sha_never_releases(self):
        report = aggregate_release_verdict(
            {"core": component(sha="bad"), "A5": component(sha="bad"), "A6": component(sha="bad")},
            job_results={"core": "success", "A5": "success", "A6": "success"},
            expected_sha="bad",
        )
        self.assertEqual(report["overall_verdict"], "INVALID")
        self.assertFalse(report["release_ready"])


if __name__ == "__main__":
    unittest.main()
