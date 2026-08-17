from __future__ import annotations

import sqlite3
from unittest.mock import call, patch

from analyzazprav.analytics import AnalyticMessage
from analyzazprav.analytics import adapter_v7


def _message(
    message_id: int,
    conversation_id: int,
    participant_id: int | None,
    membership_id: int,
) -> AnalyticMessage:
    return AnalyticMessage(
        message_id=message_id,
        conversation_id=conversation_id,
        participant_id=participant_id,
        timestamp_us=message_id * 1_000_000,
        text_clean="test",
        session_id=1,
        sequence_number=message_id,
        word_count=1,
        character_count=4,
        question_mark_count=0,
        exclamation_mark_count=0,
        membership_id=membership_id,
    )


def test_selected_conversations_are_pushed_into_membership_loader() -> None:
    conn = object()
    loaded = {
        10: [_message(1, 10, 1, 101)],
        20: [_message(2, 20, 2, 202)],
    }

    with patch.object(
        adapter_v7,
        "load_analytic_messages",
        side_effect=lambda _conn, conversation_id=None: loaded.get(conversation_id, []),
    ) as loader:
        messages = adapter_v7._load_selected_messages(conn, [20, 10, 20])

    assert [message.conversation_id for message in messages] == [10, 20]
    assert loader.call_args_list == [call(conn, 10), call(conn, 20)]


def test_full_archive_mode_keeps_single_pass_loader() -> None:
    conn = object()
    all_messages = [_message(1, 10, 1, 101), _message(2, 20, 2, 202)]

    with patch.object(
        adapter_v7,
        "load_analytic_messages",
        return_value=all_messages,
    ) as loader:
        messages = adapter_v7._load_selected_messages(conn, None)

    assert messages == all_messages
    loader.assert_called_once_with(conn)


def test_resolved_sender_lookup_ignores_unselected_conversation_rows() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(
            """
            CREATE TABLE resolved_participant(id INTEGER PRIMARY KEY);
            CREATE TABLE resolved_source(
                membership_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                conversation_id INTEGER NOT NULL,
                resolved_sender_id INTEGER
            );
            CREATE VIEW analysis_processed_messages_resolved_latest AS
            SELECT membership_id, message_id, conversation_id, resolved_sender_id
            FROM resolved_source;

            INSERT INTO resolved_participant(id) VALUES (7);
            INSERT INTO resolved_source VALUES (101, 1, 10, 7);

            -- Deliberately invalid duplicate evidence in an unrelated conversation.
            -- A selected-chat read must not scan or reconcile these rows.
            INSERT INTO resolved_source VALUES (999, 90, 20, 8);
            INSERT INTO resolved_source VALUES (999, 91, 20, 8);
            """
        )

        resolved = adapter_v7._resolve_participants(
            conn,
            [_message(1, 10, 1, 101)],
        )

        assert len(resolved) == 1
        assert resolved[0].participant_id == 7
    finally:
        conn.close()


def test_selected_conversation_still_fails_closed_on_its_own_duplicate_evidence() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(
            """
            CREATE TABLE resolved_participant(id INTEGER PRIMARY KEY);
            CREATE TABLE resolved_source(
                membership_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                conversation_id INTEGER NOT NULL,
                resolved_sender_id INTEGER
            );
            CREATE VIEW analysis_processed_messages_resolved_latest AS
            SELECT membership_id, message_id, conversation_id, resolved_sender_id
            FROM resolved_source;

            INSERT INTO resolved_participant(id) VALUES (7);
            INSERT INTO resolved_source VALUES (101, 1, 10, 7);
            INSERT INTO resolved_source VALUES (101, 1, 10, 7);
            """
        )

        try:
            adapter_v7._resolve_participants(conn, [_message(1, 10, 1, 101)])
        except RuntimeError as exc:
            assert "duplicate memberships" in str(exc)
        else:
            raise AssertionError("selected conversation duplicate evidence must fail closed")
    finally:
        conn.close()
