from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        return False


def _stable_key(*parts: object) -> str:
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def _issue(
    issues: list[dict[str, Any]],
    severity: str,
    code: str,
    detail: str,
    *,
    source_record_key: str | None = None,
    line: int | None = None,
) -> None:
    item: dict[str, Any] = {"severity": severity, "code": code, "detail": detail}
    if source_record_key:
        item["source_record_key"] = source_record_key
    if line is not None:
        item["line"] = line
    issues.append(item)


def _parse_iso_utc(value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp_utc must be a non-empty ISO string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp_utc has no UTC offset")


def _expected_record_key(
    manifest: dict[str, Any], record: dict[str, Any]
) -> str | None:
    source = manifest.get("source") or {}
    key_contract = manifest.get("source_record_key") or {}
    source_sha = source.get("sha256")
    if not _is_sha256(source_sha):
        return None

    version = str(key_contract.get("version") or "")
    source_type = source.get("type")
    if source_type == "imessage_chat_db" and version == "2":
        return _stable_key(source_sha, "message", record.get("source_message_id"))
    if version == "1":
        return _stable_key(
            source_sha,
            record.get("source_guid") or "",
            record.get("source_message_id"),
            record.get("conversation_source_id"),
        )
    return None


def _finalize(
    root: Path,
    manifest: dict[str, Any] | None,
    records: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    *,
    attachment_count: int,
    conversation_relation_count: int,
) -> dict[str, Any]:
    errors = sum(item["severity"] == "ERROR" for item in issues)
    warnings = sum(item["severity"] == "WARNING" for item in issues)
    status = STATUS_FAIL if errors else STATUS_WARNING if warnings else STATUS_PASS

    logical_lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in records
    ]
    fingerprints = {
        "record_sequence_sha256": _sha256_text("\n".join(logical_lines)),
        "record_set_sha256": _sha256_text("\n".join(sorted(logical_lines))),
    }
    manifest_path = root / "manifest.json"
    messages_path = root / "messages.jsonl"
    if manifest_path.is_file():
        fingerprints["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if messages_path.is_file():
        fingerprints["messages_jsonl_sha256"] = hashlib.sha256(messages_path.read_bytes()).hexdigest()

    return {
        "schema_version": 2,
        "status": status,
        "root": str(root),
        "contract_version": None if manifest is None else manifest.get("contract_version"),
        "counts": {
            "records": len(records),
            "attachments": attachment_count,
            "conversation_relations": conversation_relation_count,
            "errors": int(errors),
            "warnings": int(warnings),
        },
        "fingerprints": fingerprints,
        "issues": issues,
    }


def validate_staging_dir(root: str | Path) -> dict[str, Any]:
    """Validate the current A1 staging contract without mutating any input."""

    root = Path(root)
    issues: list[dict[str, Any]] = []
    manifest_path = root / "manifest.json"
    messages_path = root / "messages.jsonl"

    manifest: dict[str, Any] | None = None
    if not manifest_path.is_file():
        _issue(issues, "ERROR", "MANIFEST_MISSING", "manifest.json is missing")
    else:
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("manifest root must be an object")
            manifest = loaded
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            _issue(issues, "ERROR", "MANIFEST_INVALID", str(exc))

    records: list[dict[str, Any]] = []
    if not messages_path.is_file():
        _issue(issues, "ERROR", "MESSAGES_MISSING", "messages.jsonl is missing")
    else:
        try:
            for line_no, raw in enumerate(messages_path.read_text(encoding="utf-8").splitlines(), start=1):
                if not raw.strip():
                    _issue(issues, "WARNING", "EMPTY_LINE", "empty JSONL line ignored", line=line_no)
                    continue
                try:
                    item = json.loads(raw)
                except json.JSONDecodeError as exc:
                    _issue(issues, "ERROR", "MESSAGE_JSON_INVALID", str(exc), line=line_no)
                    continue
                if not isinstance(item, dict):
                    _issue(issues, "ERROR", "MESSAGE_NOT_OBJECT", "message JSONL record must be an object", line=line_no)
                    continue
                item = dict(item)
                item["_a7_line"] = line_no
                records.append(item)
        except OSError as exc:
            _issue(issues, "ERROR", "MESSAGES_UNREADABLE", str(exc))

    attachment_count = 0
    conversation_relation_count = 0
    if manifest is None:
        clean_records = [{k: v for k, v in row.items() if k != "_a7_line"} for row in records]
        return _finalize(
            root,
            manifest,
            clean_records,
            issues,
            attachment_count=0,
            conversation_relation_count=0,
        )

    contract_version = manifest.get("contract_version")
    source = manifest.get("source")
    parser = manifest.get("parser")
    outputs = manifest.get("outputs")
    counts = manifest.get("counts")
    key_contract = manifest.get("source_record_key")

    if contract_version != "1":
        _issue(issues, "ERROR", "CONTRACT_VERSION_UNSUPPORTED", f"expected contract_version '1', got {contract_version!r}")
    if not isinstance(source, dict):
        _issue(issues, "ERROR", "MANIFEST_SOURCE_INVALID", "manifest.source must be an object")
        source = {}
    if not isinstance(parser, dict) or not parser.get("name") or not parser.get("version"):
        _issue(issues, "ERROR", "MANIFEST_PARSER_INVALID", "manifest.parser.name/version are required")
        parser = {}
    if not isinstance(outputs, dict) or not outputs.get("messages"):
        _issue(issues, "ERROR", "MANIFEST_OUTPUTS_INVALID", "manifest.outputs.messages is required")
    if not isinstance(counts, dict):
        _issue(issues, "ERROR", "MANIFEST_COUNTS_INVALID", "manifest.counts must be an object")
        counts = {}
    if not isinstance(key_contract, dict):
        _issue(issues, "ERROR", "SOURCE_RECORD_KEY_CONTRACT_MISSING", "manifest.source_record_key is required")
        key_contract = {}

    source_type = source.get("type")
    source_sha = source.get("sha256")
    if not isinstance(source_type, str) or not source_type:
        _issue(issues, "ERROR", "SOURCE_TYPE_MISSING", "manifest.source.type is required")
    if not _is_sha256(source_sha):
        _issue(issues, "ERROR", "SOURCE_SHA256_INVALID", "manifest.source.sha256 must be SHA-256")

    if source_type == "imessage_chat_db":
        if key_contract.get("version") != "2" or key_contract.get("scope") != "source_snapshot+message_rowid":
            _issue(issues, "ERROR", "IMESSAGE_RECORD_KEY_CONTRACT_INVALID", "iMessage requires source_record_key v2 scoped to source_snapshot+message_rowid")
        if source.get("snapshot_method") != "sqlite_online_backup_v1":
            _issue(issues, "ERROR", "IMESSAGE_SNAPSHOT_METHOD_INVALID", "iMessage source must declare sqlite_online_backup_v1")
        if source.get("snapshot_includes_committed_wal") is not True:
            _issue(issues, "ERROR", "IMESSAGE_WAL_PROVENANCE_MISSING", "iMessage source must declare committed WAL inclusion")

    keys_seen: dict[str, int] = {}
    for record in records:
        line_no = int(record["_a7_line"])
        key = record.get("source_record_key")
        key_for_issue = key if isinstance(key, str) else None
        if not _is_sha256(key):
            _issue(issues, "ERROR", "SOURCE_RECORD_KEY_INVALID", "source_record_key must be SHA-256", line=line_no)
        elif key in keys_seen:
            _issue(issues, "ERROR", "SOURCE_RECORD_KEY_DUPLICATE", f"first seen on line {keys_seen[key]}", source_record_key=key, line=line_no)
        else:
            keys_seen[str(key)] = line_no

        if record.get("contract_version") != contract_version:
            _issue(issues, "ERROR", "CONTRACT_VERSION_MISMATCH", "record contract_version differs from manifest", source_record_key=key_for_issue, line=line_no)
        if record.get("record_type") != "message":
            _issue(issues, "ERROR", "RECORD_TYPE_INVALID", "record_type must be 'message'", source_record_key=key_for_issue, line=line_no)
        if record.get("source_type") != source_type:
            _issue(issues, "ERROR", "SOURCE_TYPE_MISMATCH", "record source_type differs from manifest", source_record_key=key_for_issue, line=line_no)
        if record.get("source_sha256") != source_sha:
            _issue(issues, "ERROR", "SOURCE_SHA256_MISMATCH", "record source_sha256 differs from manifest", source_record_key=key_for_issue, line=line_no)
        if record.get("source_message_id") in (None, ""):
            _issue(issues, "ERROR", "SOURCE_MESSAGE_ID_MISSING", "source_message_id is required", source_record_key=key_for_issue, line=line_no)
        if record.get("conversation_source_id") in (None, ""):
            _issue(issues, "ERROR", "SOURCE_CONVERSATION_ID_MISSING", "conversation_source_id is required", source_record_key=key_for_issue, line=line_no)

        expected_key = _expected_record_key(manifest, record)
        if expected_key is None:
            _issue(issues, "ERROR", "SOURCE_RECORD_KEY_CONTRACT_UNKNOWN", "cannot validate source_record_key contract", source_record_key=key_for_issue, line=line_no)
        elif key != expected_key:
            _issue(issues, "ERROR", "SOURCE_RECORD_KEY_MISMATCH", "source_record_key does not match declared algorithm/version", source_record_key=key_for_issue, line=line_no)

        timestamp_utc = record.get("timestamp_utc")
        if timestamp_utc not in (None, ""):
            try:
                _parse_iso_utc(timestamp_utc)
            except (ValueError, TypeError) as exc:
                _issue(issues, "ERROR", "TIMESTAMP_INVALID", str(exc), source_record_key=key_for_issue, line=line_no)
        elif source_type == "imessage_chat_db" and record.get("timestamp_raw") is not None:
            _issue(issues, "ERROR", "IMESSAGE_TIMESTAMP_CONVERSION_MISSING", "Apple timestamp_raw exists but timestamp_utc is missing", source_record_key=key_for_issue, line=line_no)
        elif record.get("timestamp_raw") is not None:
            _issue(issues, "WARNING", "TIMESTAMP_UNNORMALIZED", "source timestamp exists but could not be normalized without guessing", source_record_key=key_for_issue, line=line_no)

        relations = record.get("conversation_sources")
        if relations is None:
            relations = []
        if not isinstance(relations, list):
            _issue(issues, "ERROR", "CONVERSATION_SOURCES_NOT_LIST", "conversation_sources must be an array", source_record_key=key_for_issue, line=line_no)
            relations = []
        relation_keys: set[str] = set()
        for relation in relations:
            if not isinstance(relation, dict):
                _issue(issues, "ERROR", "CONVERSATION_SOURCE_NOT_OBJECT", "conversation_sources entries must be objects", source_record_key=key_for_issue, line=line_no)
                continue
            relation_key = relation.get("source_conversation_key")
            if not isinstance(relation_key, str) or not relation_key:
                _issue(issues, "ERROR", "CONVERSATION_SOURCE_KEY_MISSING", "source_conversation_key is required", source_record_key=key_for_issue, line=line_no)
                continue
            if relation_key in relation_keys:
                _issue(issues, "ERROR", "CONVERSATION_SOURCE_DUPLICATE", f"duplicate relation {relation_key!r}", source_record_key=key_for_issue, line=line_no)
                continue
            relation_keys.add(relation_key)
            conversation_relation_count += 1
        if source_type == "imessage_chat_db" and not relation_keys:
            _issue(issues, "ERROR", "IMESSAGE_CONVERSATION_SOURCES_MISSING", "iMessage record must preserve at least one source conversation relation", source_record_key=key_for_issue, line=line_no)

        attachments = record.get("attachments")
        if attachments is None:
            attachments = []
        if not isinstance(attachments, list):
            _issue(issues, "ERROR", "ATTACHMENTS_NOT_LIST", "attachments must be an array", source_record_key=key_for_issue, line=line_no)
            attachments = []
        for index, attachment in enumerate(attachments):
            attachment_count += 1
            if not isinstance(attachment, dict):
                _issue(issues, "ERROR", "ATTACHMENT_NOT_OBJECT", f"attachment {index} must be an object", source_record_key=key_for_issue, line=line_no)
                continue
            if attachment.get("source_attachment_id") in (None, ""):
                _issue(issues, "WARNING", "ATTACHMENT_SOURCE_ID_MISSING", f"attachment {index} has no source_attachment_id", source_record_key=key_for_issue, line=line_no)
            sha = attachment.get("sha256")
            if sha is not None and not _is_sha256(sha):
                _issue(issues, "ERROR", "ATTACHMENT_SHA256_INVALID", f"attachment {index} sha256 is invalid", source_record_key=key_for_issue, line=line_no)

    expected_seen = counts.get("messages_seen")
    expected_emitted = counts.get("messages_emitted", expected_seen)
    expected_attachments = counts.get("attachments_seen")
    expected_errors = counts.get("errors")
    if expected_seen != len(records):
        _issue(issues, "ERROR", "MESSAGES_SEEN_MISMATCH", f"manifest={expected_seen!r}, parsed={len(records)}")
    if expected_emitted != len(records):
        _issue(issues, "ERROR", "MESSAGES_EMITTED_MISMATCH", f"manifest={expected_emitted!r}, parsed={len(records)}")
    if expected_attachments != attachment_count:
        _issue(issues, "ERROR", "ATTACHMENTS_SEEN_MISMATCH", f"manifest={expected_attachments!r}, parsed={attachment_count}")
    if expected_errors != 0:
        _issue(issues, "ERROR", "A1_EXTRACTION_ERRORS", f"manifest reports errors={expected_errors!r}")

    clean_records = [{k: v for k, v in row.items() if k != "_a7_line"} for row in records]
    return _finalize(
        root,
        manifest,
        clean_records,
        issues,
        attachment_count=attachment_count,
        conversation_relation_count=conversation_relation_count,
    )
