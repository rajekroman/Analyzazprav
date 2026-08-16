from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"
APPLE_EPOCH_UNIX = datetime(2001, 1, 1, tzinfo=timezone.utc).timestamp()


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str, key: str | None = None, line: int | None = None) -> None:
    row: dict[str, Any] = {"severity": severity, "code": code, "detail": detail}
    if key is not None:
        row["source_record_key"] = key
    if line is not None:
        row["line"] = line
    issues.append(row)


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        return False


def _stable_key(*parts: Any) -> str:
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _manifest_count(manifest: Mapping[str, Any] | None, *names: str) -> int | None:
    if not manifest:
        return None
    counts = manifest.get("counts")
    for source in (manifest, counts if isinstance(counts, Mapping) else {}):
        for name in names:
            value = source.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def _output_path(root: Path, manifest: Mapping[str, Any] | None, name: str, default: str) -> Path:
    outputs = manifest.get("outputs") if manifest else None
    value = outputs.get(name) if isinstance(outputs, Mapping) else None
    return root / (str(value) if value else default)


def _load_jsonl(path: Path, issues: list[dict[str, Any]], prefix: str, *, required: bool) -> list[dict[str, Any]]:
    if not path.is_file():
        if required:
            _issue(issues, "ERROR", f"{prefix}_MISSING_FILE", f"Missing {path.name}")
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_no, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    _issue(issues, "ERROR", f"{prefix}_JSON_INVALID", f"{exc.msg} at column {exc.colno}", line=line_no)
                    continue
                if not isinstance(value, dict):
                    _issue(issues, "ERROR", f"{prefix}_NOT_OBJECT", "JSONL row must be an object", line=line_no)
                    continue
                value["_qa_line"] = line_no
                rows.append(value)
    except OSError as exc:
        _issue(issues, "ERROR", f"{prefix}_UNREADABLE", str(exc))
    return rows


def _conversation_ids(record: Mapping[str, Any]) -> list[str]:
    raw = record.get("conversation_sources")
    result: list[str] = []
    if raw is not None:
        if not isinstance(raw, list):
            raise ValueError("conversation_sources must be an array")
        for item in raw:
            if isinstance(item, str):
                value = item.strip()
            elif isinstance(item, Mapping):
                candidate = item.get("source_conversation_key") or item.get("conversation_source_id") or item.get("source_conversation_id")
                if candidate is None and item.get("chat_guid") is not None:
                    candidate = f"guid:{item['chat_guid']}"
                if candidate is None and item.get("raw_chat_rowid") is not None:
                    candidate = f"rowid:{item['raw_chat_rowid']}"
                value = "" if candidate is None else str(candidate).strip()
            else:
                raise ValueError("conversation_sources entries must be strings or objects")
            if not value:
                raise ValueError("conversation source has no identity")
            if value not in result:
                result.append(value)
    legacy = record.get("conversation_source_id")
    if not result and legacy not in (None, ""):
        result.append(str(legacy).strip())
    return [value for value in result if value]


def _expected_key(manifest: Mapping[str, Any], record: Mapping[str, Any]) -> str | None:
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        return None
    sha = source.get("sha256")
    source_type = source.get("type")
    message_id = record.get("source_message_id")
    if not _is_sha256(sha) or message_id in (None, ""):
        return None
    spec = manifest.get("source_record_key")
    if isinstance(spec, Mapping):
        algorithm = str(spec.get("algorithm") or "sha256-unit-separator")
        version = str(spec.get("version") or "1")
        scope = str(spec.get("scope") or "")
        if algorithm != "sha256-unit-separator":
            raise ValueError(f"unsupported source_record_key algorithm {algorithm!r}")
        if source_type == "imessage_chat_db" and version == "2":
            if scope != "source_snapshot+message_rowid":
                raise ValueError(f"unsupported iMessage v2 key scope {scope!r}")
            return _stable_key(sha, "message", message_id)
        if version != "1":
            raise ValueError(f"unsupported source_record_key version {version!r}")
    conversations = _conversation_ids(record)
    if not conversations:
        return None
    return _stable_key(sha, record.get("source_guid") or "", message_id, record.get("conversation_source_id") or conversations[0])


def _parse_iso(value: Any) -> float:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be non-empty ISO-8601 string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no UTC offset")
    return parsed.astimezone(timezone.utc).timestamp()


def _apple_epoch(raw: Any) -> tuple[float, str]:
    if isinstance(raw, bool):
        raise ValueError("boolean Apple timestamp")
    value = float(raw)
    precision = "nanosecond" if abs(value) >= 100_000_000_000 else "second"
    seconds = value / 1_000_000_000 if precision == "nanosecond" else value
    return APPLE_EPOCH_UNIX + seconds, precision


def _local_offset(record: Mapping[str, Any]) -> int | None:
    explicit = record.get("timezone_offset_min")
    if explicit is not None:
        if isinstance(explicit, bool) or not isinstance(explicit, int) or not -840 <= explicit <= 840:
            raise ValueError("timezone_offset_min must be integer in [-840, 840]")
    local = record.get("timestamp_local")
    embedded: int | None = None
    if local is not None:
        if not isinstance(local, str):
            raise ValueError("timestamp_local must be a string")
        text = local[:-1] + "+00:00" if local.endswith("Z") else local
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp_local has no explicit offset")
        seconds = parsed.utcoffset().total_seconds()
        if seconds % 60:
            raise ValueError("timestamp_local offset must be whole minutes")
        embedded = int(seconds // 60)
    if explicit is not None and embedded is not None and explicit != embedded:
        raise ValueError("timestamp_local offset disagrees with timezone_offset_min")
    return explicit if explicit is not None else embedded


def validate_staging_dir(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    issues: list[dict[str, Any]] = []
    manifest_path = root / "manifest.json"
    manifest: dict[str, Any] | None = None
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("manifest root must be an object")
        manifest = value
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        _issue(issues, "ERROR", "MANIFEST_INVALID", str(exc))

    messages_path = _output_path(root, manifest, "messages", "messages.jsonl")
    errors_path = _output_path(root, manifest, "errors", "errors.jsonl")
    records = _load_jsonl(messages_path, issues, "MESSAGE", required=True)
    manifest_errors = _manifest_count(manifest, "errors")
    outputs = manifest.get("outputs") if manifest else None
    errors_declared = errors_path.is_file() or manifest_errors not in (None, 0) or (isinstance(outputs, Mapping) and bool(outputs.get("errors")))
    error_rows = _load_jsonl(errors_path, issues, "ERROR", required=errors_declared)

    source = manifest.get("source") if manifest else None
    source_type = source.get("type") if isinstance(source, Mapping) else None
    source_sha = source.get("sha256") if isinstance(source, Mapping) else None
    if source_sha is not None and not _is_sha256(source_sha):
        _issue(issues, "ERROR", "MANIFEST_SOURCE_SHA256_INVALID", "manifest source sha256 is invalid")

    seen_keys: dict[str, int] = {}
    timestamps: list[tuple[float, str, int]] = []
    attachment_count = 0
    missing_attachments = 0
    relation_count = 0
    referenced_files: set[str] = set()

    for record in records:
        line_no = int(record.get("_qa_line", 0))
        key = record.get("source_record_key")
        key = key.strip() if isinstance(key, str) else ""
        key_ref = key or None
        if not key or not _is_sha256(key):
            _issue(issues, "ERROR", "SOURCE_RECORD_KEY_INVALID", "source_record_key must be SHA-256", key_ref, line_no)
        elif key in seen_keys:
            _issue(issues, "ERROR", "SOURCE_RECORD_KEY_DUPLICATE", f"first seen on line {seen_keys[key]}", key, line_no)
        else:
            seen_keys[key] = line_no

        if manifest and record.get("contract_version") not in (None, manifest.get("contract_version")):
            _issue(issues, "ERROR", "CONTRACT_VERSION_MISMATCH", "record and manifest contract_version differ", key_ref, line_no)
        if record.get("record_type") not in (None, "message"):
            _issue(issues, "ERROR", "RECORD_TYPE_INVALID", "record_type must be message", key_ref, line_no)
        if source_type is not None and record.get("source_type") != source_type:
            _issue(issues, "ERROR", "SOURCE_TYPE_MISMATCH", "record source_type differs from manifest", key_ref, line_no)
        if source_sha is not None and record.get("source_sha256") != source_sha:
            _issue(issues, "ERROR", "SOURCE_SHA256_MISMATCH", "record source_sha256 differs from manifest", key_ref, line_no)
        if record.get("source_message_id") in (None, ""):
            _issue(issues, "ERROR", "SOURCE_MESSAGE_ID_MISSING", "source_message_id is required", key_ref, line_no)

        try:
            conversations = _conversation_ids(record)
        except ValueError as exc:
            conversations = []
            _issue(issues, "ERROR", "CONVERSATION_SOURCE_INVALID", str(exc), key_ref, line_no)
        if not conversations:
            _issue(issues, "ERROR", "SOURCE_CONVERSATION_ID_MISSING", "message has no source conversation relation", key_ref, line_no)
        relation_count += len(conversations)

        if manifest and key:
            try:
                expected = _expected_key(manifest, record)
            except ValueError as exc:
                expected = None
                _issue(issues, "ERROR", "SOURCE_RECORD_KEY_CONTRACT_INVALID", str(exc), key_ref, line_no)
            if expected is not None and key != expected:
                _issue(issues, "ERROR", "SOURCE_RECORD_KEY_MISMATCH", "source_record_key does not match declared A1 algorithm", key_ref, line_no)

        timestamp = record.get("timestamp_utc")
        if timestamp not in (None, ""):
            try:
                epoch = _parse_iso(timestamp)
                timestamps.append((epoch, key or f"line:{line_no}", line_no))
                if source_type == "imessage_chat_db" and record.get("timestamp_raw") is not None:
                    expected_epoch, precision = _apple_epoch(record["timestamp_raw"])
                    if record.get("timestamp_precision") != precision:
                        _issue(issues, "ERROR", "A1_TIMESTAMP_PRECISION_MISMATCH", f"expected {precision}", key_ref, line_no)
                    if abs(epoch - expected_epoch) > 0.001:
                        _issue(issues, "ERROR", "A1_TIMESTAMP_CONVERSION_MISMATCH", "UTC timestamp differs from Apple epoch conversion", key_ref, line_no)
            except (TypeError, ValueError, OverflowError) as exc:
                _issue(issues, "ERROR", "TIMESTAMP_INVALID", str(exc), key_ref, line_no)
        elif record.get("timestamp_raw") is not None:
            _issue(issues, "ERROR", "A1_TIMESTAMP_CONVERSION_MISSING", "raw timestamp has no normalized UTC timestamp", key_ref, line_no)
        else:
            _issue(issues, "WARNING", "TIMESTAMP_MISSING", "message has no recognized timestamp", key_ref, line_no)
        try:
            _local_offset(record)
        except (TypeError, ValueError) as exc:
            _issue(issues, "ERROR", "TIMEZONE_INVALID", str(exc), key_ref, line_no)

        attachments = record.get("attachments") or []
        if not isinstance(attachments, list):
            _issue(issues, "ERROR", "ATTACHMENTS_NOT_LIST", "attachments must be an array", key_ref, line_no)
            continue
        for index, attachment in enumerate(attachments):
            attachment_count += 1
            if not isinstance(attachment, Mapping):
                _issue(issues, "ERROR", "ATTACHMENT_NOT_OBJECT", f"attachment {index} must be an object", key_ref, line_no)
                continue
            source_attachment_id = attachment.get("source_attachment_id")
            if "source_attachment_id" in attachment and source_attachment_id in (None, ""):
                _issue(issues, "ERROR", "ATTACHMENT_SOURCE_ID_MISSING", f"attachment {index} has empty source_attachment_id", key_ref, line_no)
            sha = attachment.get("sha256")
            if sha is not None and not _is_sha256(sha):
                _issue(issues, "ERROR", "ATTACHMENT_SHA256_INVALID", f"attachment {index} sha256 invalid", key_ref, line_no)
            for field in ("total_bytes", "actual_bytes"):
                size = attachment.get(field)
                if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
                    _issue(issues, "ERROR", "ATTACHMENT_SIZE_INVALID", f"attachment {index} {field} invalid", key_ref, line_no)
            status = attachment.get("resolution_status")
            if status not in (None, "resolved", "missing", "no_path"):
                _issue(issues, "ERROR", "ATTACHMENT_RESOLUTION_STATUS_INVALID", f"attachment {index} status invalid", key_ref, line_no)
            if status == "missing":
                missing_attachments += 1
            if status == "resolved":
                path_value = attachment.get("resolved_path")
                if not isinstance(path_value, str) or not path_value:
                    _issue(issues, "ERROR", "ATTACHMENT_RESOLVED_PATH_MISSING", f"attachment {index} resolved without path", key_ref, line_no)
                else:
                    path = Path(path_value).expanduser()
                    referenced_files.add(str(path.resolve()))
                    if not path.is_file():
                        _issue(issues, "WARNING", "ATTACHMENT_RESOLVED_FILE_UNAVAILABLE", f"resolved file unavailable: {path}", key_ref, line_no)
                    else:
                        if sha and hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                            _issue(issues, "ERROR", "ATTACHMENT_CONTENT_HASH_MISMATCH", f"attachment {index} hash differs", key_ref, line_no)
                        actual_bytes = attachment.get("actual_bytes")
                        if isinstance(actual_bytes, int) and path.stat().st_size != actual_bytes:
                            _issue(issues, "ERROR", "ATTACHMENT_ACTUAL_SIZE_MISMATCH", f"attachment {index} size differs", key_ref, line_no)
            for field in ("relative_path", "copied_path", "file_path", "path"):
                path_value = attachment.get(field)
                if isinstance(path_value, str) and path_value.strip():
                    path = Path(path_value) if Path(path_value).is_absolute() else root / path_value
                    referenced_files.add(str(path.resolve()))
                    if not path.is_file():
                        missing_attachments += 1
                        _issue(issues, "WARNING", "ATTACHMENT_MISSING", f"attachment file not found: {path_value}", key_ref, line_no)
                    break

    order_violations = 0
    for previous, current in zip(timestamps, timestamps[1:]):
        if current[0] < previous[0]:
            order_violations += 1
            _issue(issues, "WARNING", "TIMESTAMP_ORDER", f"timestamp precedes previous record {previous[1]}", current[1], current[2])

    orphan_count = 0
    attachments_dir = root / "attachments"
    if attachments_dir.is_dir():
        for path in attachments_dir.rglob("*"):
            if path.is_file() and str(path.resolve()) not in referenced_files:
                orphan_count += 1
                _issue(issues, "WARNING", "ATTACHMENT_ORPHAN", f"unreferenced attachment file: {path}")

    seen = _manifest_count(manifest, "messages_seen")
    emitted = _manifest_count(manifest, "messages_emitted")
    duplicates = _manifest_count(manifest, "duplicates", "duplicate_count") or 0
    unsupported = _manifest_count(manifest, "unsupported", "unsupported_count") or 0
    source_errors = manifest_errors or 0
    if emitted is not None and emitted != len(records):
        _issue(issues, "ERROR", "MANIFEST_EMITTED_COUNT_MISMATCH", f"messages_emitted={emitted}; JSONL={len(records)}")
    elif emitted is None and seen is not None and source_errors == 0 and seen != len(records):
        _issue(issues, "ERROR", "MANIFEST_COUNT_MISMATCH", f"messages_seen={seen}; JSONL={len(records)}")
    if seen is not None and emitted is not None and seen != emitted + duplicates + unsupported + source_errors:
        _issue(issues, "ERROR", "IMPORT_RECONCILIATION_MISMATCH", f"{seen} != {emitted}+{duplicates}+{unsupported}+{source_errors}")
    if manifest_errors is not None and manifest_errors != len(error_rows):
        _issue(issues, "ERROR", "ERRORS_JSONL_COUNT_MISMATCH", f"manifest errors={manifest_errors}; errors.jsonl={len(error_rows)}")
    for error in error_rows:
        if all(error.get(field) in (None, "") for field in ("source_message_id", "source_guid", "conversation_source_id")):
            _issue(issues, "ERROR", "ERROR_RECORD_IDENTITY_MISSING", "errors.jsonl row has no source identifier", line=int(error.get("_qa_line", 0)))
    if source_errors:
        _issue(issues, "ERROR", "A1_EXPORT_ERRORS", f"A1 reports {source_errors} extraction/serialization error(s)")

    expected_attachments = _manifest_count(manifest, "attachments_seen")
    if expected_attachments is not None and source_errors == 0 and expected_attachments != attachment_count:
        _issue(issues, "ERROR", "MANIFEST_ATTACHMENT_COUNT_MISMATCH", f"attachments_seen={expected_attachments}; JSONL={attachment_count}")
    resolved_count = _manifest_count(manifest, "attachments_resolved")
    missing_count = _manifest_count(manifest, "attachments_missing")
    if expected_attachments is not None and resolved_count is not None and missing_count is not None and resolved_count + missing_count > expected_attachments:
        _issue(issues, "ERROR", "ATTACHMENT_STATUS_ACCOUNTING_INVALID", "resolved + missing exceeds attachments_seen")

    logical = [{k: v for k, v in row.items() if k != "_qa_line"} for row in records]
    sequence = "\n".join(_canonical(row) for row in logical)
    logical_set = "\n".join(sorted(_canonical(row) for row in logical))
    errors = sum(item["severity"] == "ERROR" for item in issues)
    warnings = sum(item["severity"] == "WARNING" for item in issues)
    status = STATUS_FAIL if errors else STATUS_WARNING if warnings else STATUS_PASS
    report = {
        "schema_version": 3,
        "status": status,
        "root": str(root),
        "counts": {
            "records": len(records), "error_records": len(error_rows), "unique_source_record_keys": len(seen_keys),
            "conversation_relations": relation_count, "attachments": attachment_count, "missing_attachments": missing_attachments,
            "orphan_attachments": orphan_count, "timestamp_order_violations": order_violations,
            "messages_seen": seen, "messages_emitted": emitted, "duplicates": duplicates, "unsupported": unsupported,
            "source_errors": source_errors, "errors": errors, "warnings": warnings,
        },
        "fingerprints": {
            "logical_sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
            "logical_record_set_sha256": hashlib.sha256(logical_set.encode()).hexdigest(),
        },
        "issues": issues,
    }
    for name, path in (("manifest", manifest_path), ("messages_jsonl", messages_path), ("errors_jsonl", errors_path)):
        if path.is_file():
            report["fingerprints"][f"{name}_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only A7 validator for A1 staging bundles")
    parser.add_argument("staging_dir", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = validate_staging_dir(args.staging_dir)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 1 if report["status"] == STATUS_FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
