from __future__ import annotations

from analyzazprav.analytics import AnalyticMessage, analyze_conversation


def _message(message_id: int, sender: int | None, second: int) -> AnalyticMessage:
    return AnalyticMessage(
        message_id=message_id,
        conversation_id=10,
        participant_id=sender,
        timestamp_us=second * 1_000_000,
        text_clean="x",
        session_id=1,
        sequence_number=message_id,
        word_count=1,
        character_count=1,
        question_mark_count=0,
        exclamation_mark_count=0,
        local_date="2026-01-05",
        local_weekday=0,
        local_hour=12,
    )


def test_unknown_first_turn_does_not_transfer_initiation_to_later_known_sender() -> None:
    result = analyze_conversation([
        _message(1, None, 0),
        _message(2, 1, 10),
    ])

    assert result.participant_metrics[1]["initiations"] == 0
    assert sum(
        row.initiations for row in result.daily_metrics if row.participant_id == 1
    ) == 0
    assert sum(
        row.initiations
        for row in result.period_metrics
        if row.participant_id == 1 and row.period_kind == "week"
    ) == 0
    assert sum(
        row.initiations
        for row in result.period_metrics
        if row.participant_id == 1 and row.period_kind == "month"
    ) == 0
