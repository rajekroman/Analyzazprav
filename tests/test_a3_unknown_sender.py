from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.processing import CanonicalMessage, CanonicalParticipant, process_messages
from analyzazprav.processing.pipeline import PROCESSING_VERSION


def msg(
    membership_id: int,
    message_id: int,
    sender_id: int | None,
    timestamp_us: int,
) -> CanonicalMessage:
    return CanonicalMessage(
        membership_id=membership_id,
        id=message_id,
        conversation_id=10,
        sender_id=sender_id,
        timestamp_us=timestamp_us,
        text=f"m{message_id}",
        source_message_id=f"g-{message_id}",
        source_record_keys=(f"rk-{message_id}",),
        source_order=message_id,
    )


class UnknownSenderFailClosedTests(unittest.TestCase):
    def test_unknown_sender_neither_groups_runs_nor_creates_other_sender_latency(self):
        messages = [
            msg(1, 1, 100, 0),
            msg(2, 2, None, 1_000_000),
            msg(3, 3, None, 2_000_000),
            msg(4, 4, 200, 3_000_000),
        ]

        result = process_messages(messages)
        by_membership = {item.membership_id: item for item in result.messages}

        self.assertEqual([run.message_count for run in result.sender_runs], [1, 1, 1, 1])
        self.assertNotEqual(by_membership[2].sender_run_id, by_membership[3].sender_run_id)
        self.assertIsNone(by_membership[2].resolved_sender_id)
        self.assertIsNone(by_membership[3].resolved_sender_id)
        self.assertIsNone(by_membership[2].features.seconds_since_previous_other_sender)
        self.assertIsNone(by_membership[3].features.seconds_since_previous_other_sender)

        # The unidentified rows are ignored for identity comparison. The last
        # earlier known participant different from sender 200 is sender 100.
        self.assertEqual(by_membership[4].features.seconds_since_previous_other_sender, 3.0)

    def test_unknown_between_same_known_sender_does_not_become_other_sender(self):
        messages = [
            msg(1, 1, 100, 0),
            msg(2, 2, None, 1_000_000),
            msg(3, 3, 100, 2_000_000),
        ]

        result = process_messages(messages)
        by_membership = {item.membership_id: item for item in result.messages}

        self.assertEqual([run.message_count for run in result.sender_runs], [1, 1, 1])
        self.assertIsNone(by_membership[2].features.seconds_since_previous_other_sender)
        self.assertIsNone(by_membership[3].features.seconds_since_previous_other_sender)

    def test_explicit_self_aliases_still_share_one_run(self):
        messages = [
            msg(1, 1, 10, 0),
            msg(2, 2, 20, 1_000_000),
        ]
        participants = [
            CanonicalParticipant(id=10, canonical_name="Me phone", is_self=True),
            CanonicalParticipant(id=20, canonical_name="Me email", is_self=True),
        ]

        result = process_messages(messages, participants=participants)

        self.assertEqual(len(result.sender_runs), 1)
        self.assertEqual(result.sender_runs[0].message_count, 2)
        self.assertIsNotNone(result.sender_runs[0].resolved_participant_id)
        self.assertIsNone(result.messages[1].features.seconds_since_previous_other_sender)

    def test_processing_version_marks_changed_semantics(self):
        self.assertEqual(PROCESSING_VERSION, "6")


if __name__ == "__main__":
    unittest.main()
