from __future__ import annotations

import unittest

from qa.a4_oracle import STATUS_FAIL, STATUS_PASS, compute_a4_oracle, validate_a4_against_oracle


MESSAGES = [
    {"message_id": 1, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 1, "timestamp_us": 0, "word_count": 2},
    {"message_id": 2, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 2, "timestamp_us": 60_000_000, "word_count": 3},
    {"message_id": 3, "conversation_id": 7, "participant_id": 20, "session_id": 1, "sequence_number": 3, "timestamp_us": 300_000_000, "word_count": 4},
    {"message_id": 4, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 4, "timestamp_us": 360_000_000, "word_count": 2},
    {"message_id": 5, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 5, "timestamp_us": 420_000_000, "word_count": 1},
    {"message_id": 6, "conversation_id": 7, "participant_id": 20, "session_id": 2, "sequence_number": 6, "timestamp_us": 3_600_000_000, "word_count": 2},
    {"message_id": 7, "conversation_id": 7, "participant_id": None, "session_id": 3, "sequence_number": 7, "timestamp_us": None, "word_count": 1},
]


def result_from_oracle(expected: dict) -> dict:
    metrics = {pid: dict(row) for pid, row in expected["participant_metrics"].items()}
    return {
        "conversation_id": expected["conversation_id"],
        "source_message_count": expected["source_message_count"],
        "known_sender_message_count": expected["known_sender_message_count"],
        "unknown_sender_message_count": expected["unknown_sender_message_count"],
        "turn_count": expected["turn_count"],
        "session_count": expected["session_count"],
        "participant_metrics": metrics,
        "turns": [dict(row) for row in expected["turns"]],
        "response_samples": [dict(row) for row in expected["response_samples"]],
    }


class A4OracleTests(unittest.TestCase):
    def test_manual_dataset_has_expected_metrics(self) -> None:
        result = compute_a4_oracle(MESSAGES)
        self.assertEqual(7, result["source_message_count"])
        self.assertEqual(6, result["known_sender_message_count"])
        self.assertEqual(1, result["unknown_sender_message_count"])
        self.assertEqual(5, result["turn_count"])
        self.assertEqual(3, result["session_count"])

        a = result["participant_metrics"][10]
        b = result["participant_metrics"][20]
        self.assertEqual(4, a["message_count"])
        self.assertEqual(2, a["turn_count"])
        self.assertEqual(1, a["initiations"])
        self.assertEqual(1, a["unanswered_turn_count"])
        self.assertEqual(60.0, a["median_response_latency_seconds"])

        self.assertEqual(2, b["message_count"])
        self.assertEqual(2, b["turn_count"])
        self.assertEqual(1, b["initiations"])
        self.assertEqual(1, b["unanswered_turn_count"])
        self.assertEqual(240.0, b["median_response_latency_seconds"])

        self.assertEqual(2, len(result["response_samples"]))
        self.assertEqual([240.0, 60.0], [row["latency_seconds"] for row in result["response_samples"]])
        self.assertEqual((1, 2), result["turns"][0]["message_ids"])
        self.assertEqual((4, 5), result["turns"][2]["message_ids"])

    def test_matching_serialized_a4_result_passes(self) -> None:
        expected = compute_a4_oracle(MESSAGES)
        report = validate_a4_against_oracle(MESSAGES, result_from_oracle(expected))
        self.assertEqual(STATUS_PASS, report["status"])

    def test_wrong_median_is_detected(self) -> None:
        expected = compute_a4_oracle(MESSAGES)
        actual = result_from_oracle(expected)
        actual["participant_metrics"][20]["median_response_latency_seconds"] = 999.0
        report = validate_a4_against_oracle(MESSAGES, actual)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A4_ORACLE_METRIC_MISMATCH", {item["code"] for item in report["issues"]})

    def test_missing_message_from_turn_partition_is_detected(self) -> None:
        expected = compute_a4_oracle(MESSAGES)
        actual = result_from_oracle(expected)
        actual["turns"][0]["message_ids"] = (1,)
        report = validate_a4_against_oracle(MESSAGES, actual)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A4_ORACLE_TURN_PARTITION_MISMATCH", {item["code"] for item in report["issues"]})


if __name__ == "__main__":
    unittest.main()
