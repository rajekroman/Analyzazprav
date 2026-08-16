from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from analyzazprav.analytics import AnalyticsConfig, Message, analyze_conversation
from analyzazprav.analytics.core import build_sessions, build_turns, response_latencies


BASE = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def msg(number: int, sender: str, minutes: int, text: str = "x", **kwargs) -> Message:
    return Message(
        message_id=f"m{number}",
        conversation_id="c1",
        participant_id=sender,
        timestamp=BASE + timedelta(minutes=minutes),
        text=text,
        **kwargs,
    )


def test_multiple_messages_from_same_sender_form_one_turn_and_one_response() -> None:
    config = AnalyticsConfig()
    messages = [
        msg(1, "A", 0, "one"),
        msg(2, "A", 1, "two"),
        msg(3, "A", 2, "three"),
        msg(4, "B", 7, "reply"),
    ]
    turns = build_turns(messages, config)
    sessions = build_sessions(turns, config)
    latencies = response_latencies(turns, sessions)

    assert len(turns) == 2
    assert turns[0].message_ids == ("m1", "m2", "m3")
    assert len(latencies) == 1
    assert latencies[0].latency_seconds == 5 * 60


def test_session_gap_creates_new_initiation() -> None:
    messages = [
        msg(1, "A", 0),
        msg(2, "B", 5),
        msg(3, "B", 6 * 60 + 10),
    ]
    result = analyze_conversation(messages)

    assert result.session_count == 2
    assert result.participant_metrics["A"]["initiations"] == 1
    assert result.participant_metrics["B"]["initiations"] == 1


def test_reactions_are_explicitly_excluded_and_accounted_for() -> None:
    result = analyze_conversation(
        [
            msg(1, "A", 0, "hello"),
            msg(2, "B", 1, "liked", is_reaction=True),
        ]
    )

    assert result.message_count == 1
    assert result.diagnostics["excluded_reactions"] == 1
    assert result.diagnostics["source_message_count"] == 2
    assert result.diagnostics["accounting_ok"] is True


def test_reciprocity_is_symmetric_ratio() -> None:
    result = analyze_conversation(
        [
            msg(1, "A", 0),
            msg(2, "A", 1),
            msg(3, "B", 2),
            msg(4, "A", 3),
        ]
    )
    reciprocity = result.diagnostics["reciprocity"]
    assert reciprocity["message_reciprocity"] == pytest.approx(1 / 3, abs=1e-6)


def test_conflict_candidate_keeps_source_message_traceability() -> None:
    config = AnalyticsConfig(conflict_threshold=0.35)
    result = analyze_conversation(
        [
            msg(1, "A", 0, "Tohle mě štve!!!"),
            msg(2, "B", 1, "Jsem naštvaný!!!"),
            msg(3, "A", 2, "Nech mě!!!"),
        ],
        config,
    )

    assert len(result.conflicts) == 1
    assert result.conflicts[0].source_message_ids == ("m1", "m2", "m3")
    assert result.conflicts[0].score >= config.conflict_threshold
