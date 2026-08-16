import hashlib
import json
from pathlib import Path

import pytest

from analiza_zprav_a1.csv_mapping import CSVMappingProfile
from analiza_zprav_a1.importer import import_generic_csv
from analiza_zprav_a1.parsers.generic_structured import GenericCSVParser


def _write_profile(path: Path, value: dict, *, pretty: bool = False) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2 if pretty else None),
        encoding="utf-8",
    )


def test_headered_profile_maps_exact_nonstandard_columns_and_records_identity(tmp_path: Path) -> None:
    source = tmp_path / "messages.csv"
    source.write_text(
        "Klíč;Osoba;Kdy;Obsah;Vlákno;Směr\n"
        "m-1;alice@example.com;2026-08-15T14:00:00+02:00;Ahoj;chat-x;incoming\n",
        encoding="utf-8",
    )
    profile_path = tmp_path / "mapping.json"
    profile_value = {
        "version": "1",
        "delimiter": ";",
        "has_header": True,
        "fields": {
            "id": "Klíč",
            "sender": "Osoba",
            "timestamp": "Kdy",
            "text": "Obsah",
            "conversation": "Vlákno",
            "direction": "Směr",
        },
    }
    _write_profile(profile_path, profile_value)

    output = tmp_path / "out"
    stats = import_generic_csv(source, output, mapping_profile_path=profile_path)
    assert stats.errors == 0
    assert stats.reconciliation_ok is True

    record = json.loads((output / "messages.jsonl").read_text(encoding="utf-8"))
    assert record["source_message_id"] == "m-1"
    assert record["sender_handle"] == "alice@example.com"
    assert record["timestamp_utc"] == "2026-08-15T12:00:00Z"
    assert record["text"] == "Ahoj"
    assert record["conversation_source_id"] == "chat-x"
    assert record["is_from_me"] is False
    assert record["text_source"] == "Obsah"
    assert record["raw_payload"]["Obsah"] == "Ahoj"
    assert record["metadata"]["column_map"]["text"] == "Obsah"

    profile = CSVMappingProfile.load(profile_path)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    mapping = manifest["parser"]["mapping_profile"]
    assert manifest["parser"]["version"] == f"0.2.0+profile.{profile.semantic_sha256()}"
    assert mapping["semantic_sha256"] == profile.semantic_sha256()
    assert mapping["file_sha256"] == hashlib.sha256(profile_path.read_bytes()).hexdigest()
    assert mapping["fields"]["text"] == "Obsah"


def test_headerless_profile_preserves_every_raw_column_by_index(tmp_path: Path) -> None:
    source = tmp_path / "headerless.csv"
    source.write_text(
        "m1,+420111222333,2026-08-15T12:00:00Z,Ahoj,c1,outgoing,EXTRA\n"
        "m2,+420999888777,2026-08-15T12:01:00Z,Nazdar,c1,incoming,KEEP\n",
        encoding="utf-8",
    )
    profile_path = tmp_path / "mapping.json"
    _write_profile(
        profile_path,
        {
            "version": "1",
            "delimiter": ",",
            "has_header": False,
            "fields": {
                "id": 0,
                "sender": 1,
                "timestamp": 2,
                "text": 3,
                "conversation": 4,
                "direction": 5,
            },
        },
    )

    output = tmp_path / "out"
    stats = import_generic_csv(source, output, mapping_profile_path=profile_path)
    assert stats.messages_seen == 2
    records = [
        json.loads(line)
        for line in (output / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["source_message_id"] for record in records] == ["m1", "m2"]
    assert records[0]["text_source"] == "column:3"
    assert records[0]["raw_payload"] == {
        "column:0": "m1",
        "column:1": "+420111222333",
        "column:2": "2026-08-15T12:00:00Z",
        "column:3": "Ahoj",
        "column:4": "c1",
        "column:5": "outgoing",
        "column:6": "EXTRA",
    }
    assert records[0]["metadata"]["ordinal"] == 1
    assert records[0]["is_from_me"] is True
    assert records[1]["is_from_me"] is False


def test_semantic_profile_fingerprint_ignores_json_formatting_but_changes_with_mapping(tmp_path: Path) -> None:
    source = tmp_path / "messages.csv"
    source.write_text("who;words\nalice;hello\n", encoding="utf-8")
    base = {
        "version": "1",
        "delimiter": ";",
        "has_header": True,
        "fields": {"sender": "who", "text": "words"},
    }
    same_a = tmp_path / "a.json"
    same_b = tmp_path / "b.json"
    changed = tmp_path / "changed.json"
    _write_profile(same_a, base, pretty=False)
    _write_profile(same_b, base, pretty=True)
    _write_profile(
        changed,
        {
            **base,
            "fields": {"sender": "words", "text": "who"},
        },
        pretty=True,
    )

    out_a = tmp_path / "out-a"
    out_b = tmp_path / "out-b"
    out_changed = tmp_path / "out-changed"
    import_generic_csv(source, out_a, mapping_profile_path=same_a)
    import_generic_csv(source, out_b, mapping_profile_path=same_b)
    import_generic_csv(source, out_changed, mapping_profile_path=changed)

    manifest_a = json.loads((out_a / "manifest.json").read_text(encoding="utf-8"))
    manifest_b = json.loads((out_b / "manifest.json").read_text(encoding="utf-8"))
    manifest_changed = json.loads((out_changed / "manifest.json").read_text(encoding="utf-8"))

    assert manifest_a["parser"]["version"] == manifest_b["parser"]["version"]
    assert (
        manifest_a["parser"]["mapping_profile"]["file_sha256"]
        != manifest_b["parser"]["mapping_profile"]["file_sha256"]
    )
    assert manifest_a["parser"]["version"] != manifest_changed["parser"]["version"]


def test_profile_rejects_missing_header_and_invalid_headerless_index(tmp_path: Path) -> None:
    headered = tmp_path / "headered.json"
    _write_profile(
        headered,
        {
            "version": "1",
            "delimiter": ",",
            "has_header": True,
            "fields": {"text": "missing-column"},
        },
    )
    source = tmp_path / "source.csv"
    source.write_text("actual\nhello\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing header"):
        list(GenericCSVParser(source, CSVMappingProfile.load(headered)).iter_messages())

    invalid = tmp_path / "invalid.json"
    _write_profile(
        invalid,
        {
            "version": "1",
            "delimiter": ",",
            "has_header": False,
            "fields": {"text": -1},
        },
    )
    with pytest.raises(ValueError, match="zero-based non-negative"):
        CSVMappingProfile.load(invalid)


def test_default_csv_rejects_duplicate_headers_and_overwide_rows(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text("text,text\na,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate header"):
        list(GenericCSVParser(duplicate).iter_messages())

    overwide = tmp_path / "overwide.csv"
    overwide.write_text("text,sender\nhello,alice,hidden\n", encoding="utf-8")
    with pytest.raises(ValueError, match="more fields than the header"):
        list(GenericCSVParser(overwide).iter_messages())
