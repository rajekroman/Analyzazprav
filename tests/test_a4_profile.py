from __future__ import annotations

from unittest.mock import patch

import pytest

from analyzazprav.analytics import AnalyticMessage
from analyzazprav.analytics import profile as a4_profile


def _message(message_id: int, participant_id: int) -> AnalyticMessage:
    return AnalyticMessage(
        message_id=message_id,
        conversation_id=10,
        participant_id=participant_id,
        timestamp_us=message_id * 60_000_000,
        text_clean="profil test",
        session_id=1,
        sequence_number=message_id,
        word_count=2,
        character_count=11,
        question_mark_count=0,
        exclamation_mark_count=0,
        local_date="2026-08-16",
        local_weekday=6,
        local_hour=10,
        membership_id=100 + message_id,
    )


def test_profile_connection_reports_stable_stage_timings_and_counts() -> None:
    messages = [_message(1, 1), _message(2, 2)]
    fake_plan = {
        "membership:10": ["SEARCH processed_message USING INDEX fixture"],
        "resolved:selected": ["SEARCH resolved sender fixture"],
    }

    with patch.object(
        a4_profile,
        "_load_selected_messages",
        return_value=messages,
    ) as loader, patch.object(
        a4_profile,
        "explain_read_plan",
        return_value=fake_plan,
    ):
        result = a4_profile.profile_connection(
            object(),
            conversation_ids=[10, 10],
            repeat=2,
        )

    assert result.repeat == 2
    assert result.selected_conversation_ids == (10,)
    assert result.conversation_count == 1
    assert result.message_count == 2
    assert result.load_timing.min_seconds >= 0
    assert result.analysis_timing.min_seconds >= 0
    assert result.total_median_seconds >= 0
    assert result.query_plan == fake_plan
    assert loader.call_count == 2
    assert all(call.args[1] == (10,) for call in loader.call_args_list)


def test_profile_rejects_non_positive_repeat() -> None:
    with pytest.raises(ValueError, match="repeat must be >= 1"):
        a4_profile.profile_connection(object(), repeat=0)
