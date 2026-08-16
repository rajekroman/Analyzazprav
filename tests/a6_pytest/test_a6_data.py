from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from a6.data import (
    DataSourceError,
    add_opposite_sender_gap,
    add_response_latency,
    analysis_packet,
    demo_messages,
    filter_messages,
    load_sqlite_messages,
    normalize_frame,
)


def test_demo_messages_are_canonical_and_sorted():
    frame = demo_messages()
    assert not frame.empty
    assert list(frame.columns) == [
        "membership_id",
        "message_id",
        "conversation_id",
        "contact",
        "sender",
        "timestamp",
        "timestamp_precision",
        "timestamp_quality",
        "text",
    ]
    assert frame["membership_id"].is_unique
    assert frame["message_id"].is_unique
    assert frame["timestamp"].is_monotonic_increasing


def test_opposite_sender_gap_only_counts_sender_changes():
    frame = pd.DataFrame(
        [
            {"message_id": "1", "conversation_id": "c", "contact": "x", "sender": "A", "timestamp": "2026-01-01T10:00:00Z", "text": "one"},
            {"message_id": "2", "conversation_id": "c", "contact": "x", "sender": "A", "timestamp": "2026-01-01T10:01:00Z", "text": "two"},
            {"message_id": "3", "conversation_id": "c", "contact": "x", "sender": "B", "timestamp": "2026-01-01T10:03:00Z", "text": "adjacent opposite sender"},
        ]
    )
    result = add_opposite_sender_gap(frame)
    assert pd.isna(result.loc[0, "opposite_sender_gap_seconds"])
    assert pd.isna(result.loc[1, "opposite_sender_gap_seconds"])
    assert result.loc[2, "opposite_sender_gap_seconds"] == 120.0

    compatibility = add_response_latency(frame)
    assert compatibility.loc[2, "response_seconds"] == 120.0


def test_sqlite_schema_discovery_and_read_only_load(tmp_path):
    db_path = tmp_path / "messages.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE analytical_messages (id TEXT, thread_id TEXT, chat_name TEXT, author TEXT, sent_at_utc TEXT, body TEXT)"
        )
        conn.execute(
            "INSERT INTO analytical_messages VALUES (?, ?, ?, ?, ?, ?)",
            ("m1", "c1", "Kontakt", "Osoba A", "2026-08-01T08:00:00Z", "Ahoj"),
        )
        conn.commit()

    frame, source = load_sqlite_messages(db_path)
    assert source.object_name == "analytical_messages"
    assert frame.loc[0, "message_id"] == "m1"
    assert frame.loc[0, "membership_id"] == "compat:c1:m1"
    assert frame.loc[0, "contact"] == "Kontakt"
    assert frame.loc[0, "sender"] == "Osoba A"
    assert frame.loc[0, "text"] == "Ahoj"


def test_analysis_packet_keeps_message_ids_and_memberships():
    frame = demo_messages()
    selected = [frame.iloc[0]["message_id"], frame.iloc[-1]["message_id"]]
    packet = analysis_packet(frame, selected)
    assert packet["message_count"] == 2
    assert packet["selected_message_count"] == 2
    assert [item["message_id"] for item in packet["messages"]] == selected
    assert all(item["membership_id"] for item in packet["messages"])


def test_empty_sender_filter_returns_no_messages():
    frame = demo_messages()
    assert filter_messages(frame, senders=[]).empty


def test_response_latency_does_not_cross_conversations():
    frame = pd.DataFrame([
        {"message_id": "1", "conversation_id": "a", "contact": "x", "sender": "A", "timestamp": "2026-01-01T10:00:00Z", "text": "a"},
        {"message_id": "2", "conversation_id": "b", "contact": "x", "sender": "B", "timestamp": "2026-01-01T10:01:00Z", "text": "b"},
    ])
    result = add_opposite_sender_gap(frame)
    assert result["opposite_sender_gap_seconds"].isna().all()


def test_analysis_packet_can_add_context():
    frame = demo_messages()
    selected = [frame.iloc[5]["message_id"]]
    packet = analysis_packet(frame, selected, context_before=2, context_after=3)
    assert packet["selected_message_count"] == 1
    assert packet["message_count"] == 6
    assert sum(1 for item in packet["messages"] if item["selected"]) == 1


def _create_a2_views(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE analysis_messages ("
        "membership_id INTEGER, id INTEGER, conversation_id INTEGER, sender_name TEXT, "
        "sent_at_utc_us INTEGER, timestamp_precision TEXT, timestamp_quality TEXT, text TEXT)"
    )
    conn.execute(
        "CREATE TABLE analysis_conversations (id INTEGER, title TEXT, canonical_key TEXT)"
    )


def test_loads_a2_analysis_views_with_microsecond_timestamps(tmp_path):
    db_path = tmp_path / "a2.sqlite"
    with sqlite3.connect(db_path) as conn:
        _create_a2_views(conn)
        conn.execute("INSERT INTO analysis_conversations VALUES (1, 'Test kontakt', 'fallback-key')")
        ts = int(pd.Timestamp("2026-08-16T05:00:00Z").timestamp() * 1_000_000)
        conn.execute(
            "INSERT INTO analysis_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (9001, 101, 1, 'Osoba A', ts, 'microsecond', 'exact', 'Test'),
        )
        conn.commit()

    frame, source = load_sqlite_messages(db_path)
    assert source.object_name == "analysis_messages"
    assert frame.loc[0, "membership_id"] == "9001"
    assert frame.loc[0, "message_id"] == "101"
    assert frame.loc[0, "contact"] == "Test kontakt"
    assert frame.loc[0, "timestamp"] == pd.Timestamp("2026-08-16T05:00:00Z")
    assert frame.loc[0, "timestamp_precision"] == "microsecond"
    assert frame.loc[0, "timestamp_quality"] == "exact"


def test_a2_read_model_preserves_multiple_memberships_for_one_message_and_unknown_time(tmp_path):
    db_path = tmp_path / "a2-lossless.sqlite"
    with sqlite3.connect(db_path) as conn:
        _create_a2_views(conn)
        conn.executemany(
            "INSERT INTO analysis_conversations VALUES (?, ?, ?)",
            [(1, 'Chat A', 'a'), (2, 'Chat B', 'b')],
        )
        known = int(pd.Timestamp("2026-08-16T05:00:00Z").timestamp() * 1_000_000)
        conn.executemany(
            "INSERT INTO analysis_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (10, 100, 1, 'Osoba A', known, 'microsecond', 'exact', 'shared'),
                (11, 100, 2, 'Osoba A', known, 'microsecond', 'exact', 'shared'),
                (12, 101, 1, 'Osoba B', None, 'unknown', 'unknown', 'unknown-time'),
            ],
        )
        conn.commit()

    frame, _ = load_sqlite_messages(db_path)
    assert len(frame) == 3
    assert set(frame["membership_id"]) == {"10", "11", "12"}
    assert (frame["message_id"] == "100").sum() == 2
    unknown = frame[frame["membership_id"] == "12"].iloc[0]
    assert pd.isna(unknown["timestamp"])
    assert unknown["timestamp_quality"] == "unknown"

    chat_a = frame[frame["conversation_id"] == "1"]
    assert set(chat_a["membership_id"]) == {"10", "12"}
    filtered = filter_messages(
        chat_a,
        start=pd.Timestamp("2026-08-16"),
        end=pd.Timestamp("2026-08-17"),
    )
    assert set(filtered["membership_id"]) == {"10", "12"}
    assert set(filter_messages(chat_a, start=pd.Timestamp("2026-08-16"), include_unknown_timestamps=False)["membership_id"]) == {"10"}


def test_normalize_frame_fails_closed_on_ambiguous_membership_identity():
    frame = pd.DataFrame([
        {"membership_id": "x", "message_id": "1", "conversation_id": "a", "timestamp": "2026-01-01T00:00:00Z", "text": "a"},
        {"membership_id": "x", "message_id": "2", "conversation_id": "b", "timestamp": "2026-01-02T00:00:00Z", "text": "b"},
    ])
    with pytest.raises(DataSourceError, match="membership identity"):
        normalize_frame(frame)


def test_analysis_packet_rejects_selected_unknown_timestamp():
    frame = pd.DataFrame([
        {"membership_id": "m1", "message_id": "1", "conversation_id": "a", "contact": "x", "sender": "A", "timestamp": None, "text": "unknown"},
    ])
    with pytest.raises(ValueError, match="A5 vyžaduje známý"):
        analysis_packet(frame, ["1"])
