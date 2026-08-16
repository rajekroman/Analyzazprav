from __future__ import annotations

import copy
import unittest

from qa.analytics_validator import STATUS_FAIL, STATUS_PASS, validate_analytics_result


SOURCE = [
    {"message_id": "m1", "is_reaction": False},
    {"message_id": "m2", "is_reaction": False},
    {"message_id": "m3", "is_reaction": False},
    {"message_id": "r1", "is_reaction": True},
]

VALID = {
    "conversation_id": "c1",
    "message_count": 3,
    "turn_count": 2,
    "session_count": 1,
    "turns": [
        {
            "turn_id": "t1",
            "participant_id": "A",
            "start_at": "2026-01-01T10:00:00+00:00",
            "end_at": "2026-01-01T10:01:00+00:00",
            "message_ids": ["m1", "m2"],
            "message_count": 2,
        },
        {
            "turn_id": "t2",
            "participant_id": "B",
            "start_at": "2026-01-01T10:06:00+00:00",
            "end_at": "2026-01-01T10:06:00+00:00",
            "message_ids": ["m3"],
            "message_count": 1,
        },
    ],
    "sessions": [
        {
            "session_id": "s1",
            "initiator_id": "A",
            "turn_ids": ["t1", "t2"],
        }
    ],
    "latency_samples": [
        {
            "session_id": "s1",
            "from_participant_id": "A",
            "responder_id": "B",
            "previous_turn_id": "t1",
            "response_turn_id": "t2",
            "latency_seconds": 300.0,
        }
    ],
    "conflicts": [
        {
            "session_id": "s1",
            "source_message_ids": ["m1", "m2", "m3"],
        }
    ],
    "participant_metrics": {
        "A": {"message_count": 2, "turn_count": 1, "initiations": 1, "initiation_share": 1.0},
        "B": {"message_count": 1, "turn_count": 1, "initiations": 0, "initiation_share": 0.0},
    },
    "diagnostics": {
        "source_message_count": 4,
        "excluded_reactions": 1,
        "accounting_ok": True,
    },
}


class AnalyticsValidatorTests(unittest.TestCase):
    def test_valid_accounting_passes(self) -> None:
        report = validate_analytics_result(SOURCE, VALID)
        self.assertEqual(STATUS_PASS, report["status"])
        self.assertEqual(0, report["checks"]["analytic_messages_missing_from_turns"])
        self.assertEqual(1, report["checks"]["expected_latency_samples"])

    def test_missing_message_from_turns_fails_even_if_diagnostic_claims_ok(self) -> None:
        result = copy.deepcopy(VALID)
        result["turns"][0]["message_ids"] = ["m1"]
        result["turns"][0]["message_count"] = 1
        report = validate_analytics_result(SOURCE, result)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("ANALYTIC_MESSAGES_MISSING_FROM_TURNS", {i["code"] for i in report["issues"]})

    def test_reaction_in_turn_fails(self) -> None:
        result = copy.deepcopy(VALID)
        result["turns"][0]["message_ids"] = ["m1", "r1"]
        report = validate_analytics_result(SOURCE, result)
        self.assertEqual(STATUS_FAIL, report["status"])
        codes = {i["code"] for i in report["issues"]}
        self.assertIn("REACTION_INCLUDED_IN_ANALYTIC_TURN", codes)
        self.assertIn("TURN_MESSAGES_NOT_IN_SOURCE", codes)

    def test_bad_session_initiator_and_latency_fail(self) -> None:
        result = copy.deepcopy(VALID)
        result["sessions"][0]["initiator_id"] = "B"
        result["latency_samples"][0]["latency_seconds"] = 299.0
        report = validate_analytics_result(SOURCE, result)
        self.assertEqual(STATUS_FAIL, report["status"])
        codes = {i["code"] for i in report["issues"]}
        self.assertIn("SESSION_INITIATOR_MISMATCH", codes)
        self.assertIn("LATENCY_VALUE_MISMATCH", codes)


if __name__ == "__main__":
    unittest.main()
