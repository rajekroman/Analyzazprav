import json
from pathlib import Path

from analiza_zprav_a1.importer import import_generic_csv, import_generic_json


def test_generic_tab_csv_maps_common_message_fields(tmp_path: Path):
    source = tmp_path / "export.csv"
    source.write_text(
        "thread\tfrom\ttimestamp\tbody\tdirection\n"
        "chat-1\talice@example.com\t2026-08-15T12:00:00Z\tHello\tincoming\n",
        encoding="utf-8",
    )
    output = tmp_path / "out"
    stats = import_generic_csv(source, output)
    assert stats.messages_seen == 1
    record = json.loads((output / "messages.jsonl").read_text(encoding="utf-8"))
    assert record["source_type"] == "generic_message_csv"
    assert record["conversation_source_id"] == "chat-1"
    assert record["sender_handle"] == "alice@example.com"
    assert record["timestamp_utc"] == "2026-08-15T12:00:00Z"
    assert record["text"] == "Hello"
    assert record["is_from_me"] is False
    assert record["raw_payload"]["body"] == "Hello"


def test_generic_json_preserves_nested_attachment_and_raw_payload(tmp_path: Path):
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    (attachments / "doc.pdf").write_bytes(b"pdf")
    source = tmp_path / "export.json"
    source.write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "id": "m1",
                        "conversation": "c1",
                        "sender": "Bob",
                        "date": "2026-08-15 13:00:00",
                        "message": "File",
                        "attachments": [{"path": "doc.pdf", "mime_type": "application/pdf", "size": 3}],
                        "custom": {"keep": True},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out"
    stats = import_generic_json(source, output, attachments)
    assert stats.attachments_resolved == 1
    record = json.loads((output / "messages.jsonl").read_text(encoding="utf-8"))
    assert record["source_message_id"] == "m1"
    assert record["timestamp_utc"] is None
    assert record["timestamp_precision"] == "local_text"
    assert record["attachments"][0]["resolution_status"] == "resolved"
    assert record["raw_payload"]["custom"]["keep"] is True


def test_generic_jsonl_is_supported(tmp_path: Path):
    source = tmp_path / "export.jsonl"
    source.write_text(
        '{"id":"1","text":"one"}\n{"id":"2","text":"two"}\n',
        encoding="utf-8",
    )
    output = tmp_path / "out"
    stats = import_generic_json(source, output)
    assert stats.messages_seen == 2
    lines = (output / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["text"] for line in lines] == ["one", "two"]
