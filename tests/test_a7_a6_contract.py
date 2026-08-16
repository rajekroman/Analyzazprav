from __future__ import annotations

from analyzazprav.qa.a6_contract import FAIL, PASS, WARNING, validate_a6_packet


def good_packet():
    return {
        "schema_version": 1,
        "source_provenance_required": True,
        "source_provenance_status": "complete",
        "source_provenance_missing_message_ids": [],
        "selected_message_ids": ["11"],
        "message_count": 2,
        "selected_message_count": 1,
        "messages": [
            {
                "message_id": "11",
                "membership_id": "101",
                "conversation_id": "7",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "selected": True,
                "source_record_keys": ["rk11"],
                "source_snapshot_keys": ["sha1"],
                "source_parser_versions": ["p1"],
                "source_provenance_status": "complete",
            },
            {
                "message_id": "12",
                "membership_id": "102",
                "conversation_id": "7",
                "timestamp": "2026-01-01T00:01:00+00:00",
                "selected": False,
                "source_record_keys": ["rk12"],
                "source_snapshot_keys": ["sha1"],
                "source_parser_versions": ["p1"],
                "source_provenance_status": "complete",
            },
        ],
    }


def test_production_packet_passes_a7_oracle():
    assert validate_a6_packet(good_packet())["status"] == PASS


def test_missing_provenance_fails_a7_oracle():
    value = good_packet()
    value["messages"][0]["source_record_keys"] = []
    report = validate_a6_packet(value)
    assert report["status"] == FAIL
    assert "A6_PACKET_SOURCE_PROVENANCE_MISSING" in {x["code"] for x in report["issues"]}


def test_unknown_timestamp_fails_a7_oracle():
    value = good_packet()
    value["messages"][1]["timestamp"] = None
    report = validate_a6_packet(value)
    assert report["status"] == FAIL
    assert "A6_PACKET_UNKNOWN_TIMESTAMP" in {x["code"] for x in report["issues"]}


def test_demo_packet_is_warning_not_production_pass():
    value = good_packet()
    value["source_provenance_required"] = False
    value["source_provenance_status"] = "missing"
    value["source_provenance_missing_message_ids"] = ["11", "12"]
    for row in value["messages"]:
        row["source_record_keys"] = []
        row["source_snapshot_keys"] = []
        row["source_parser_versions"] = []
        row["source_provenance_status"] = "missing"
    report = validate_a6_packet(value)
    assert report["status"] == WARNING
    assert "A6_PACKET_SOURCE_PROVENANCE_UNVERIFIED" in {x["code"] for x in report["issues"]}
