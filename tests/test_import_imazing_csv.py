import hashlib
import json
from pathlib import Path

from analiza_zprav_a1.importer import import_imazing_csv


def test_imazing_semicolon_csv_with_attachment(tmp_path: Path):
    attachment_root = tmp_path / "exported_attachments"
    attachment_root.mkdir()
    attachment = attachment_root / "photo.jpg"
    attachment.write_bytes(b"photo")

    source = tmp_path / "messages.csv"
    source.write_text(
        "Chat Session;Sender;Sent Date;Message;Service;Direction;Attachment File Name;Attachment Type\n"
        "Eva;+420111222333;2026-08-15T18:30:00+02:00;Ahoj;iMessage;Received;photo.jpg;image/jpeg\n",
        encoding="utf-8",
    )
    output = tmp_path / "staging"

    stats = import_imazing_csv(source, output, attachment_root)

    assert stats.messages_seen == 1
    assert stats.attachments_seen == 1
    assert stats.attachments_resolved == 1
    assert stats.attachments_missing == 0
    assert stats.errors == 0

    record = json.loads((output / "messages.jsonl").read_text(encoding="utf-8"))
    assert record["source_type"] == "imazing_messages_csv"
    assert record["source_message_id"] == "row:2"
    assert record["conversation_source_id"] == "Eva"
    assert record["sender_handle"] == "+420111222333"
    assert record["is_from_me"] is False
    assert record["text"] == "Ahoj"
    assert record["timestamp_utc"] == "2026-08-15T16:30:00Z"
    assert record["timestamp_precision"] == "second"
    assert record["raw_payload"]["Message"] == "Ahoj"
    assert record["attachments"][0]["resolution_status"] == "resolved"
    assert record["attachments"][0]["sha256"] == hashlib.sha256(b"photo").hexdigest()

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"]["type"] == "imazing_messages_csv"
    assert manifest["counts"]["attachments_resolved"] == 1


def test_imazing_local_timestamp_is_not_falsely_declared_utc(tmp_path: Path):
    source = tmp_path / "messages.csv"
    source.write_text(
        "Conversation,From,Date,Text\n"
        "Test chat,Alice,2026-08-15 18:30:00,Hello\n",
        encoding="utf-8",
    )
    output = tmp_path / "staging"
    import_imazing_csv(source, output)
    record = json.loads((output / "messages.jsonl").read_text(encoding="utf-8"))
    assert record["timestamp_raw"] == "2026-08-15 18:30:00"
    assert record["timestamp_utc"] is None
    assert record["timestamp_precision"] == "local_text"
    assert record["is_from_me"] is None
