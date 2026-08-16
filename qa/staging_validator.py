from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"
APPLE_EPOCH_UNIX = datetime(2001, 1, 1, tzinfo=timezone.utc).timestamp()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


def _stable_message_key(
    source_sha256: str,
    source_guid: Any,
    source_message_id: Any,
    conversation_source_id: Any,
) -> str:
    parts = (
        source_sha256,
        source_guid or "",
        source_message_id,
        conversation_source_id,
    )
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def _issue(
    issues: list[dict[str, Any]],
    severity: str,
    code: str,
    detail: str,
    source_record_key: str | None = None,
    line: int | None = None,
) -> None:
    item: dict[str, Any] = {"severity": severity, "code": code, "detail": detail}
    if source_record_key is not None:
        item["source_record_key"] = source_record_key
    if line is not None:
        item["line"] = line
    issues.append(item)


def _extract_timestamp(record: dict[str, Any]) -> tuple[str | int | float | None, str | None]:
    for key in ("timestamp_utc", "sent_at_utc", "datetime_utc", "date_utc", "sent_at", "date"):
        if key in record and record[key] not in (None, ""):
            return record[key], key

    timestamp = record.get("timestamp")
    if isinstance(timestamp, dict):
        for key in ("iso_utc", "utc", "iso", "value"):
            if timestamp.get(key) not in (None, ""):
                return timestamp[key], f"timestamp.{key}"
        for key in ("unix_ns", "unix_us", "unix_ms", "unix_s"):
            if timestamp.get(key) is not None:
                return timestamp[key], f"timestamp.{key}"
    elif timestamp not in (None, ""):
        return timestamp, "timestamp"
    return None, None


def _parse_timestamp(value: Any, field: str | None) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a timestamp")

    if isinstance(value, (int, float)):
        numeric = float(value)
        if field:
            if field.endswith("unix_ns"):
                return numeric / 1_000_000_000
            if field.endswith("unix_us"):
                return numeric / 1_000_000
            if field.endswith("unix_ms"):
                return numeric / 1_000
            if field.endswith("unix_s"):
                return numeric
        raise ValueError("numeric timestamp requires an explicit unit")

    if not isinstance(value, str):
        raise ValueError(f"unsupported timestamp type: {type(value).__name__}")

    text = value.strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timestamp has no UTC offset")
    return dt.astimezone(timezone.utc).timestamp()


def _apple_timestamp_epoch(raw_value: Any) -> tuple[float, str]:
    if isinstance(raw_value, bool):
        raise ValueError("boolean Apple timestamp")
    try:
        raw = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Apple timestamp is not numeric") from exc

    precision = "nanosecond" if abs(raw) >= 100_000_000_000 else "second"
    seconds = raw / 1_000_000_000 if precision == "nanosecond" else raw
    return APPLE_EPOCH_UNIX + seconds, precision


def _attachment_path(attachment: dict[str, Any]) -> str | None:
    for key in ("relative_path", "copied_path", "file_path", "path"):
        value = attachment.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _iter_attachment_files(root: Path) -> Iterable[Path]:
    attachments_dir = root / "attachments"
    if not attachments_dir.is_dir():
        return []
    return (p for p in attachments_dir.rglob("*") if p.is_file())


def _manifest_count(manifest: dict[str, Any] | None, *names: str) -> int | None:
    if not manifest:
        return None
    for name in names:
        value = manifest.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    counts = manifest.get("counts")
    if isinstance(counts, dict):
        for name in names:
            value = counts.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def validate_staging_dir(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    issues: list[dict[str, Any]] = []
    manifest_path = root / "manifest.json"
    messages_path = root / "messages.jsonl"

    manifest: dict[str, Any] | None = None
    if not manifest_path.is_file():
        _issue(issues, "ERROR", "MANIFEST_MISSING", "manifest.json is missing")
    else:
        try:
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("manifest root must be a JSON object")
            manifest = parsed
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            _issue(issues, "ERROR", "MANIFEST_INVALID", str(exc))

    manifest_contract = manifest.get("contract_version") if manifest else None
    manifest_source = manifest.get("source") if manifest else None
    if manifest_source is not None and not isinstance(manifest_source, dict):
        _issue(issues, "ERROR", "MANIFEST_SOURCE_INVALID", "manifest.source must be an object")
        manifest_source = None
    manifest_source_sha = manifest_source.get("sha256") if isinstance(manifest_source, dict) else None
    manifest_source_type = manifest_source.get("type") if isinstance(manifest_source, dict) else None

    if manifest_source_sha is not None and not _is_sha256(manifest_source_sha):
        _issue(issues, "ERROR", "MANIFEST_SOURCE_SHA256_INVALID", "manifest.source.sha256 is not SHA-256")

    records: list[dict[str, Any]] = []
    raw_line_hashes: list[str] = []
    if not messages_path.is_file():
        _issue(issues, "ERROR", "MESSAGES_MISSING", "messages.jsonl is missing")
    else:
        try:
            with messages_path.open("r", encoding="utf-8") as handle:
                for line_no, raw_line in enumerate(handle, start=1):
                    text = raw_line.strip()
                    if not text:
                        _issue(issues, "WARNING", "EMPTY_LINE", "Empty line ignored", line=line_no)
                        continue
                    raw_line_hashes.append(_sha256_text(text))
                    try:
                        record = json.loads(text)
                    except json.JSONDecodeError as exc:
                        _issue(
                            issues,
                            "ERROR",
                            "MESSAGE_JSON_INVALID",
                            f"{exc.msg} at column {exc.colno}",
                            line=line_no,
                        )
                        continue
                    if not isinstance(record, dict):
                        _issue(
                            issues,
                            "ERROR",
                            "MESSAGE_NOT_OBJECT",
                            "Each JSONL record must be an object",
                            line=line_no,
                        )
                        continue
                    record["_qa_line"] = line_no
                    records.append(record)
        except OSError as exc:
            _issue(issues, "ERROR", "MESSAGES_UNREADABLE", str(exc))

    keys_seen: dict[str, int] = {}
    timestamps: list[tuple[float, str, int]] = []
    referenced_files: set[str] = set()
    attachment_count = 0
    missing_attachment_count = 0

    for record in records:
        line_no = int(record.get("_qa_line", 0))
        key_value = record.get("source_record_key")
        key = key_value.strip() if isinstance(key_value, str) else ""
        key_for_issue = key or None

        if not key:
            _issue(
                issues,
                "ERROR",
                "SOURCE_RECORD_KEY_MISSING",
                "source_record_key must be a non-empty string",
                line=line_no,
            )
        else:
            if not _is_sha256(key):
                _issue(
                    issues,
                    "ERROR",
                    "SOURCE_RECORD_KEY_INVALID",
                    "source_record_key must be a SHA-256 hex digest",
                    source_record_key=key,
                    line=line_no,
                )
            if key in keys_seen:
                _issue(
                    issues,
                    "ERROR",
                    "SOURCE_RECORD_KEY_DUPLICATE",
                    f"Duplicate key; first seen on line {keys_seen[key]}",
                    source_record_key=key,
                    line=line_no,
                )
            else:
                keys_seen[key] = line_no

        if manifest_contract is not None and record.get("contract_version") != manifest_contract:
            _issue(
                issues,
                "ERROR",
                "CONTRACT_VERSION_MISMATCH",
                f"Record contract_version {record.get('contract_version')!r} != manifest {manifest_contract!r}",
                key_for_issue,
                line_no,
            )

        if record.get("record_type") not in (None, "message"):
            _issue(
                issues,
                "ERROR",
                "RECORD_TYPE_INVALID",
                f"Expected record_type='message'; got {record.get('record_type')!r}",
                key_for_issue,
                line_no,
            )

        record_source_type = record.get("source_type")
        if manifest_source_type is not None and record_source_type != manifest_source_type:
            _issue(
                issues,
                "ERROR",
                "SOURCE_TYPE_MISMATCH",
                f"Record source_type {record_source_type!r} != manifest {manifest_source_type!r}",
                key_for_issue,
                line_no,
            )

        record_source_sha = record.get("source_sha256")
        if record_source_sha is not None and not _is_sha256(record_source_sha):
            _issue(
                issues,
                "ERROR",
                "SOURCE_SHA256_INVALID",
                "Record source_sha256 is not SHA-256",
                key_for_issue,
                line_no,
            )
        if manifest_source_sha is not None and record_source_sha != manifest_source_sha:
            _issue(
                issues,
                "ERROR",
                "SOURCE_SHA256_MISMATCH",
                "Record source_sha256 does not match manifest source SHA-256",
                key_for_issue,
                line_no,
            )

        source_message_id = record.get("source_message_id")
        conversation_source_id = record.get("conversation_source_id")
        if source_message_id in (None, ""):
            _issue(
                issues,
                "ERROR",
                "SOURCE_MESSAGE_ID_MISSING",
                "source_message_id is required for provenance",
                key_for_issue,
                line_no,
            )
        if conversation_source_id in (None, ""):
            _issue(
                issues,
                "ERROR",
                "SOURCE_CONVERSATION_ID_MISSING",
                "conversation_source_id is required for provenance",
                key_for_issue,
                line_no,
            )

        if (
            key
            and _is_sha256(manifest_source_sha)
            and source_message_id not in (None, "")
            and conversation_source_id not in (None, "")
        ):
            expected_key = _stable_message_key(
                manifest_source_sha,
                record.get("source_guid"),
                source_message_id,
                conversation_source_id,
            )
            if key != expected_key:
                _issue(
                    issues,
                    "ERROR",
                    "SOURCE_RECORD_KEY_MISMATCH",
                    "source_record_key does not match the A1 stable_message_key contract",
                    key_for_issue,
                    line_no,
                )

        timestamp_value, timestamp_field = _extract_timestamp(record)
        raw_apple_timestamp = record.get("timestamp_raw")
        if timestamp_value is None:
            severity = "ERROR" if raw_apple_timestamp is not None else "WARNING"
            code = "A1_TIMESTAMP_CONVERSION_MISSING" if raw_apple_timestamp is not None else "TIMESTAMP_MISSING"
            _issue(
                issues,
                severity,
                code,
                "No normalized timestamp found",
                key_for_issue,
                line_no,
            )
        else:
            try:
                epoch = _parse_timestamp(timestamp_value, timestamp_field)
                timestamps.append((epoch, key or f"line:{line_no}", line_no))
                if raw_apple_timestamp is not None:
                    expected_epoch, expected_precision = _apple_timestamp_epoch(raw_apple_timestamp)
                    actual_precision = record.get("timestamp_precision")
                    if actual_precision != expected_precision:
                        _issue(
                            issues,
                            "ERROR",
                            "A1_TIMESTAMP_PRECISION_MISMATCH",
                            f"timestamp_precision {actual_precision!r} != expected {expected_precision!r}",
                            key_for_issue,
                            line_no,
                        )
                    if abs(epoch - expected_epoch) > 0.001:
                        _issue(
                            issues,
                            "ERROR",
                            "A1_TIMESTAMP_CONVERSION_MISMATCH",
                            "timestamp_utc does not match timestamp_raw Apple epoch conversion",
                            key_for_issue,
                            line_no,
                        )
            except (ValueError, TypeError, OverflowError) as exc:
                _issue(
                    issues,
                    "ERROR",
                    "TIMESTAMP_INVALID",
                    f"{timestamp_field}: {exc}",
                    key_for_issue,
                    line_no,
                )

        attachments = record.get("attachments", [])
        if attachments is None:
            attachments = []
        if not isinstance(attachments, list):
            _issue(
                issues,
                "ERROR",
                "ATTACHMENTS_NOT_LIST",
                "attachments must be a list when present",
                key_for_issue,
                line_no,
            )
            continue

        for index, attachment in enumerate(attachments):
            attachment_count += 1
            if not isinstance(attachment, dict):
                _issue(
                    issues,
                    "ERROR",
                    "ATTACHMENT_NOT_OBJECT",
                    f"Attachment index {index} is not an object",
                    key_for_issue,
                    line_no,
                )
                continue

            if attachment.get("source_attachment_id") in (None, "") and "source_attachment_id" in attachment:
                _issue(
                    issues,
                    "ERROR",
                    "ATTACHMENT_SOURCE_ID_MISSING",
                    f"Attachment index {index} has no source_attachment_id",
                    key_for_issue,
                    line_no,
                )

            attachment_sha = attachment.get("sha256")
            if attachment_sha is not None and not _is_sha256(attachment_sha):
                _issue(
                    issues,
                    "ERROR",
                    "ATTACHMENT_SHA256_INVALID",
                    f"Attachment index {index} sha256 is invalid",
                    key_for_issue,
                    line_no,
                )

            total_bytes = attachment.get("total_bytes")
            if total_bytes is not None and (
                not isinstance(total_bytes, int) or isinstance(total_bytes, bool) or total_bytes < 0
            ):
                _issue(
                    issues,
                    "ERROR",
                    "ATTACHMENT_SIZE_INVALID",
                    f"Attachment index {index} total_bytes must be a non-negative integer",
                    key_for_issue,
                    line_no,
                )

            path_text = _attachment_path(attachment)
            if not path_text:
                continue

            candidate = Path(path_text)
            if candidate.is_absolute():
                resolved = candidate
                reference_key = str(candidate)
            else:
                resolved = root / candidate
                reference_key = candidate.as_posix()
            referenced_files.add(reference_key)

            explicitly_missing = attachment.get("exists") is False
            should_exist_locally = (
                "relative_path" in attachment
                or "copied_path" in attachment
                or (
                    not candidate.is_absolute()
                    and candidate.parts
                    and candidate.parts[0] == "attachments"
                )
            )
            if explicitly_missing or (should_exist_locally and not resolved.is_file()):
                missing_attachment_count += 1
                _issue(
                    issues,
                    "WARNING",
                    "ATTACHMENT_MISSING",
                    f"Attachment file not found: {path_text}",
                    key_for_issue,
                    line_no,
                )

    order_violations = 0
    for previous, current in zip(timestamps, timestamps[1:]):
        if current[0] < previous[0]:
            order_violations += 1
            _issue(
                issues,
                "WARNING",
                "TIMESTAMP_ORDER",
                f"Timestamp precedes previous valid record ({previous[1]})",
                current[1],
                current[2],
            )

    orphan_count = 0
    root_resolved = root.resolve()
    for file_path in _iter_attachment_files(root):
        try:
            rel = file_path.resolve().relative_to(root_resolved).as_posix()
        except ValueError:
            rel = str(file_path.resolve())
        if rel not in referenced_files and str(file_path.resolve()) not in referenced_files:
            orphan_count += 1
            _issue(issues, "WARNING", "ATTACHMENT_ORPHAN", f"Unreferenced attachment file: {rel}")

    expected_messages = _manifest_count(
        manifest, "message_count", "messages_count", "record_count", "messages_seen", "messages", "records"
    )
    if expected_messages is not None and expected_messages != len(records):
        _issue(
            issues,
            "ERROR",
            "MANIFEST_COUNT_MISMATCH",
            f"Manifest expects {expected_messages} messages; parsed {len(records)}",
        )

    expected_attachments = _manifest_count(manifest, "attachments_seen", "attachments")
    if expected_attachments is not None and expected_attachments != attachment_count:
        _issue(
            issues,
            "ERROR",
            "MANIFEST_ATTACHMENT_COUNT_MISMATCH",
            f"Manifest expects {expected_attachments} attachments; parsed {attachment_count}",
        )

    export_errors = _manifest_count(manifest, "errors")
    if export_errors is not None and export_errors > 0:
        _issue(
            issues,
            "ERROR",
            "A1_EXPORT_ERRORS",
            f"A1 manifest reports {export_errors} export error(s)",
        )

    logical_records = [{k: v for k, v in record.items() if k != "_qa_line"} for record in records]
    sequence_material = "\n".join(_canonical_json(record) for record in logical_records)
    record_set_material = "\n".join(sorted(_canonical_json(record) for record in logical_records))

    error_count = sum(1 for item in issues if item["severity"] == "ERROR")
    warning_count = sum(1 for item in issues if item["severity"] == "WARNING")
    status = STATUS_FAIL if error_count else STATUS_WARNING if warning_count else STATUS_PASS

    report = {
        "schema_version": 2,
        "status": status,
        "root": str(root),
        "counts": {
            "records": len(records),
            "unique_source_record_keys": len(keys_seen),
            "attachments": attachment_count,
            "missing_attachments": missing_attachment_count,
            "orphan_attachments": orphan_count,
            "timestamp_order_violations": order_violations,
            "errors": error_count,
            "warnings": warning_count,
        },
        "fingerprints": {
            "logical_record_set_sha256": _sha256_text(record_set_material),
            "logical_sequence_sha256": _sha256_text(sequence_material),
            "jsonl_line_sequence_sha256": _sha256_text("\n".join(raw_line_hashes)),
        },
        "issues": issues,
    }

    if manifest_path.is_file():
        report["fingerprints"]["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if messages_path.is_file():
        report["fingerprints"]["messages_jsonl_sha256"] = hashlib.sha256(messages_path.read_bytes()).hexdigest()

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate an A1 staging export without modifying source data."
    )
    parser.add_argument("staging_dir", type=Path)
    parser.add_argument("--report", type=Path, help="Write JSON report to this path.")
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
