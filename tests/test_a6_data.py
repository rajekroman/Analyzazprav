from __future__ import annotations

import sqlite3

import pandas as pd

from a6.data import add_response_latency, analysis_packet, demo_messages, filter_messages, load_sqlite_messages


def test_demo_messages_are_canonical_and_sorted():
    frame = demo_messages()
    assert not frame.empty
    assert list(frame.columns) == [
        "message_id",
        "conversation_id",
        "contact",
        "sender",
        "timestamp",
        "text",
    ]
    assert frame["message_id"].is_unique
    assert frame["timestamp"].is_monotonic_increasing


def test_response_latency_only_counts_sender_changes():
    frame = pd.DataFrame(
        [
            {"message_id": "1", "conversation_id": "c", "contact": "x", "sender": "A", "timestamp": "2026-01-01T10:00:00Z", "text": "one"},
            {"message_id": "2", "conversation_id": "c", "contact": "x", "sender": "A", "timestamp": "2026-01-01T10:01:00Z", "text": "two"},
            {"message_id": "3", "conversation_id": "c", "contact": "x", "sender": "B", "timestamp": "2026-01-01T10:03:00Z", "text": "reply"},
        ]
    )
    result = add_response_latency(frame)
    assert pd.isna(result.loc[0, "response_seconds"])
    assert pd.isna(result.loc[1, "response_seconds"])
    assert result.loc[2, "response_seconds"] == 120.0


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
    assert frame.loc[0, "contact"] == "Kontakt"
    assert frame.loc[0, "sender"] == "Osoba A"
    assert frame.loc[0, "text"] == "Ahoj"


def test_analysis_packet_keeps_message_ids():
    frame = demo_messages()
    selected = [frame.iloc[0]["message_id"], frame.iloc[-1]["message_id"]]
    packet = analysis_packet(frame, selected)
    assert packet["message_count"] == 2
    assert packet["selected_message_count"] == 2
    assert [item["message_id"] for item in packet["messages"]] == selected


def test_empty_sender_filter_returns_no_messages():
    frame = demo_messages()
    assert filter_messages(frame, senders=[]).empty


def test_response_latency_does_not_cross_conversations():
    frame = pd.DataFrame([
        {"message_id": "1", "conversation_id": "a", "contact": "x", "sender": "A", "timestamp": "2026-01-01T10:00:00Z", "text": "a"},
        {"message_id": "2", "conversation_id": "b", "contact": "x", "sender": "B", "timestamp": "2026-01-01T10:01:00Z", "text": "b"},
    ])
    result = add_response_latency(frame)
    assert result["response_seconds"].isna().all()


def test_analysis_packet_can_add_context():
    frame = demo_messages()
    selected = [frame.iloc[5]["message_id"]]
    packet = analysis_packet(frame, selected, context_before=2, context_after=3)
    assert packet["selected_message_count"] == 1
    assert packet["message_count"] == 6
    assert sum(1 for item in packet["messages"] if item["selected"]) == 1


def test_loads_a2_analysis_views_with_microsecond_timestamps(tmp_path):
    db_path = tmp_path / "a2.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE analysis_messages (id INTEGER, conversation_id INTEGER, sender_name TEXT, sent_at_utc_us INTEGER, text TEXT)"
        )
        conn.execute(
            "CREATE TABLE analysis_conversations (id INTEGER, title TEXT, canonical_key TEXT)"
        )
        conn.execute(
            "INSERT INTO analysis_conversations VALUES (1, 'Test kontakt', 'fallback-key')"
        )
        ts = int(pd.Timestamp("2026-08-16T05:00:00Z").timestamp() * 1_000_000)
        conn.execute(
            "INSERT INTO analysis_messages VALUES (?, ?, ?, ?, ?)",
            (101, 1, 'Osoba A', ts, 'Test'),
        )
        conn.commit()

    frame, source = load_sqlite_messages(db_path)
    assert source.object_name == "analysis_messages"
    assert frame.loc[0, "message_id"] == "101"
    assert frame.loc[0, "contact"] == "Test kontakt"
    assert frame.loc[0, "timestamp"] == pd.Timestamp("2026-08-16T05:00:00Z")
