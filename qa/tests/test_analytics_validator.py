from __future__ import annotations

import unittest

from qa.a4_oracle import compute_a4_oracle
from qa.analytics_validator import STATUS_FAIL, STATUS_PASS, validate_analytics_result
from qa.tests.test_a4_oracle import MESSAGES, result_from_oracle


class AnalyticsValidatorTests(unittest.TestCase):
    def test_current_a4_shape_passes_oracle_and_traceability(self) -> None:
        expected = compute_a4_oracle(MESSAGES)
        actual = result_from_oracle(expected)
        actual.update(
            {
                "conflicts": [
                    {
                        "conversation_id": 7,
                        "session_id": 1,
                        "score": 0.7,
                        "source_message_ids": [1, 2, 3, 4, 5],
                    }
                ],
                "silence_events": [],
                "time_buckets": [],
                "daily_metrics": [],
                "change_points": [],
                "period_metrics": [],
                "engagement_signals": [],
                "dyadic_regimes": [],
                "trend_summaries": [],
                "diagnostics": {"message_accounting_ok": True, "uses_a3_session_boundaries": True, "duplicate_message_ids": []},
            }
        )
        report = validate_analytics_result(MESSAGES, actual)
        self.assertEqual(STATUS_PASS, report["status"])
        self.assertEqual(1, report["checks"]["trace_rows_checked"])

    def test_unknown_evidence_fails(self) -> None:
        expected = compute_a4_oracle(MESSAGES)
        actual = result_from_oracle(expected)
        actual["conflicts"] = [{"session_id": 1, "source_message_ids": [1, 999]}]
        report = validate_analytics_result(MESSAGES, actual)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A4_EVIDENCE_NOT_IN_SOURCE", {item["code"] for item in report["issues"]})

    def test_conflict_evidence_from_other_session_fails(self) -> None:
        expected = compute_a4_oracle(MESSAGES)
        actual = result_from_oracle(expected)
        actual["conflicts"] = [{"session_id": 1, "source_message_ids": [6]}]
        report = validate_analytics_result(MESSAGES, actual)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A4_CONFLICT_EVIDENCE_OUTSIDE_SESSION", {item["code"] for item in report["issues"]})

    def test_wrong_latency_metric_fails_oracle(self) -> None:
        expected = compute_a4_oracle(MESSAGES)
        actual = result_from_oracle(expected)
        actual["participant_metrics"][10]["median_response_latency_seconds"] = 500.0
        report = validate_analytics_result(MESSAGES, actual)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A4_ORACLE_METRIC_MISMATCH", {item["code"] for item in report["issues"]})


if __name__ == "__main__":
    unittest.main()
