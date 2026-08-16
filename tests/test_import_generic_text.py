import json
from pathlib import Path

from analiza_zprav_a1.importer import import_generic_text


def test_generic_text_block_mode_is_explicit_and_lossless_per_block(tmp_path: Path):
    source = tmp_path / "messages.txt"
    source.write_text("First line\ncontinued\n\nSecond block\n", encoding="utf-8")
    output = tmp_path / "out"

    stats = import_generic_text(source, output, "block")

    assert stats.messages_seen == 2
    assert stats.messages_emitted == 2
    records = [json.loads(line) for line in (output / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [record["source_message_id"] for record in records] == ["block:1", "block:2"]
    assert records[0]["text"] == "First line\ncontinued"
    assert records[1]["text"] == "Second block\n"
    assert all(record["sender_handle"] is None for record in records)
    assert all(record["timestamp_utc"] is None for record in records)
    assert all(record["metadata"]["text_boundary_mode"] == "block" for record in records)
