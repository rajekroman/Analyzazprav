from __future__ import annotations

import pytest

from analyzazprav.a5_ai.integration_a6 import A6PacketError, messages_from_a6_packet


def packet(required: bool = True):
    return {
        "schema_version": 1,
        "source_provenance_required": required,
        "source_provenance_status": "complete" if required else "missing",
        "selected_message_ids": ["11"],
        "messages": [{
            "message_id": "11",
            "membership_id": "101",
            "conversation_id": "7",
            "sender": "Alice",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "text": "hello",
            "source_record_keys": ["rk11"],
            "source_snapshot_keys": ["sha1"],
            "source_parser_versions": ["p1"],
            "source_provenance_status": "complete" if required else "missing",
        }],
    }


def test_a5_packet_adapter_preserves_membership_and_source_provenance():
    row = messages_from_a6_packet(packet())[0]
    assert row.id == "11"
    assert row.membership_id == "101"
    assert row.source_record_keys == ("rk11",)
    assert row.source_snapshot_keys == ("sha1",)
    assert row.source_parser_versions == ("p1",)


def test_production_missing_record_provenance_fails_before_provider():
    value = packet()
    value["messages"][0]["source_record_keys"] = []
    with pytest.raises(A6PacketError, match="required A2 source provenance"):
        messages_from_a6_packet(value)


def test_production_packet_status_must_be_complete():
    value = packet()
    value["source_provenance_status"] = "missing"
    with pytest.raises(A6PacketError, match="source provenance is not complete"):
        messages_from_a6_packet(value)


def test_demo_packet_may_be_explicitly_unverified():
    value = packet(required=False)
    value["messages"][0]["source_record_keys"] = []
    value["messages"][0]["source_snapshot_keys"] = []
    value["messages"][0]["source_parser_versions"] = []
    row = messages_from_a6_packet(value)[0]
    assert row.source_record_keys == ()
    assert row.source_snapshot_keys == ()


def test_duplicate_membership_fails_closed():
    value = packet()
    value["messages"].append(dict(value["messages"][0], message_id="12"))
    with pytest.raises(A6PacketError, match="Duplicate A6 membership_id"):
        messages_from_a6_packet(value)


def test_multiple_conversations_fail_closed():
    value = packet()
    value["messages"].append(dict(
        value["messages"][0],
        message_id="12",
        membership_id="102",
        conversation_id="8",
    ))
    with pytest.raises(A6PacketError, match="multiple conversations"):
        messages_from_a6_packet(value)
