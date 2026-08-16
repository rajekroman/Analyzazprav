from __future__ import annotations

from dataclasses import asdict
import copy
import unittest

from analyzazprav.analytics import AnalyticMessage, AnalyticsConfig, analyze_conversation
from analyzazprav.qa.analytics_validator import validate_analytics_result


class A7A4OracleTests(unittest.TestCase):
    def _source(self) -> list[AnalyticMessage]:
        return [
            AnalyticMessage(1, 7, 10, 0, "projekt alpha", 1, 1, 2, 13, 0, 0, membership_id=101),
            AnalyticMessage(2, 7, 10, 60_000_000, "pokračujeme", 1, 2, 1, 11, 0, 0, membership_id=102),
            AnalyticMessage(3, 7, 20, 300_000_000, "odpověď projekt alpha", 1, 3, 3, 21, 0, 0, membership_id=103),
            AnalyticMessage(4, 7, 10, 360_000_000, "další odpověď", 1, 4, 2, 14, 0, 0, membership_id=104),
        ]

    @staticmethod
    def _oracle_source(messages: list[AnalyticMessage]) -> list[dict[str, int | None]]:
        return [
            {
                "message_id": message.message_id,
                "conversation_id": message.conversation_id,
                "participant_id": message.participant_id,
                "session_id": message.session_id,
                "sequence_number": message.sequence_number,
                "timestamp_us": message.timestamp_us,
                "word_count": message.word_count,
            }
            for message in messages
        ]

    def test_a4_result_matches_independent_oracle(self) -> None:
        messages = self._source()
        result = analyze_conversation(
            messages,
            AnalyticsConfig(topic_min_document_frequency=2),
        )
        report = validate_analytics_result(self._oracle_source(messages), asdict(result))
        self.assertEqual("PASS", report["status"], report)
        self.assertEqual(4, report["checks"]["expected_source_message_count"])
        self.assertEqual(3, report["checks"]["expected_turn_count"])
        self.assertEqual(1, report["checks"]["expected_session_count"])

    def test_corrupt_turn_count_fails_closed(self) -> None:
        messages = self._source()
        result = asdict(analyze_conversation(messages, AnalyticsConfig()))
        corrupted = copy.deepcopy(result)
        corrupted["turn_count"] = int(corrupted["turn_count"]) + 1
        report = validate_analytics_result(self._oracle_source(messages), corrupted)
        self.assertEqual("FAIL", report["status"])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertTrue(any(code.startswith("A4_") for code in codes), report)


if __name__ == "__main__":
    unittest.main()
