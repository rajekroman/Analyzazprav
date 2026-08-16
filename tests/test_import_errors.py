import json
from pathlib import Path

from analiza_zprav_a1.importer import _write_records
from analiza_zprav_a1.models import MessageRecord


def test_serialization_error_is_auditable(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    record = MessageRecord(
        source_message_id="m1",
        source_guid=None,
        conversation_source_id="c1",
        timestamp_raw=None,
        timestamp_utc=None,
        timestamp_precision=None,
        sender_handle=None,
        is_from_me=None,
        text="hello",
        raw_text="hello",
        text_source="test",
        service=None,
        raw_payload={"not_json": object()},
    )

    stats = _write_records(
        [record],
        source_path=source,
        source_type="test",
        parser_name="test",
        parser_version="1",
        output_dir=tmp_path / "out",
    )

    assert stats.messages_seen == 1
    assert stats.messages_emitted == 0
    assert stats.errors == 1
    assert Path(stats.output_jsonl).read_text(encoding="utf-8") == ""
    errors = Path(stats.errors_jsonl).read_text(encoding="utf-8").splitlines()
    assert len(errors) == 1
    payload = json.loads(errors[0])
    assert payload["source_message_id"] == "m1"
    assert payload["error_type"] == "TypeError"

    manifest = json.loads(Path(stats.manifest).read_text(encoding="utf-8"))
    assert manifest["counts"]["messages_seen"] == 1
    assert manifest["counts"]["messages_emitted"] == 0
    assert manifest["outputs"]["errors"] == "errors.jsonl"
