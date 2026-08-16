from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .attachments import resolve_attachments
from .hashing import sha256_file, stable_message_key
from .models import MessageRecord
from .parsers.generic_structured import GenericCSVParser, GenericJSONParser
from .parsers.generic_text import GenericTextParser, TextMode
from .parsers.imazing_csv import IMazingCSVParser
from .parsers.imessage import IMessageParser
from .reconciliation import reconcile_bundle, write_reconciliation

A1_CONTRACT_VERSION = "1"
IMESSAGE_PARSER_NAME = "imessage-chatdb"
IMESSAGE_PARSER_VERSION = "0.4.0"
IMAZING_PARSER_NAME = "imazing-messages-csv"
IMAZING_PARSER_VERSION = "0.2.0"
GENERIC_CSV_PARSER_NAME = "generic-message-csv"
GENERIC_JSON_PARSER_NAME = "generic-message-json"
GENERIC_STRUCTURED_PARSER_VERSION = "0.2.0"
GENERIC_TEXT_PARSER_NAME = "generic-message-text"
GENERIC_TEXT_PARSER_VERSION = "0.2.0"


@dataclass(slots=True)
class ImportStats:
    messages_seen: int
    messages_emitted: int
    attachments_seen: int
    attachments_resolved: int
    attachments_missing: int
    unsupported: int
    errors: int
    output_jsonl: str
    errors_jsonl: str
    reconciliation_json: str
    reconciliation_ok: bool
    manifest: str
    source_sha256: str


def _write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_records(
    records: Iterable[MessageRecord],
    *,
    source_path: Path,
    source_type: str,
    parser_name: str,
    parser_version: str,
    output_dir: Path,
    attachments_root: Path | None = None,
) -> ImportStats:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = output_dir / "messages.jsonl"
    manifest_path = output_dir / "manifest.json"
    errors_jsonl = output_dir / "errors.jsonl"
    reconciliation_path = output_dir / "reconciliation.json"
    source_hash = sha256_file(source_path)

    seen = emitted = attachments = resolved = missing = message_errors = 0
    with (
        output_jsonl.open("w", encoding="utf-8", newline="\n") as stream,
        errors_jsonl.open("w", encoding="utf-8", newline="\n") as error_stream,
    ):
        for record in records:
            seen += 1
            attachments += len(record.attachments)
            just_resolved, just_missing = resolve_attachments(record.attachments, attachments_root)
            resolved += just_resolved
            missing += just_missing
            try:
                payload = asdict(record)
                payload.update(
                    {
                        "contract_version": A1_CONTRACT_VERSION,
                        "record_type": "message",
                        "source_type": source_type,
                        "source_sha256": source_hash,
                        "source_record_key": stable_message_key(
                            source_hash,
                            record.source_guid or "",
                            record.source_message_id,
                            record.conversation_source_id,
                        ),
                    }
                )
                stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                stream.write("\n")
                emitted += 1
            except Exception as exc:
                message_errors += 1
                error_payload = {
                    "scope": "message",
                    "source_message_id": record.source_message_id,
                    "source_guid": record.source_guid,
                    "conversation_source_id": record.conversation_source_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                error_stream.write(json.dumps(error_payload, ensure_ascii=False, sort_keys=True))
                error_stream.write("\n")

    manifest = {
        "contract_version": A1_CONTRACT_VERSION,
        "source": {
            "type": source_type,
            "name": source_path.name,
            "sha256": source_hash,
        },
        "parser": {"name": parser_name, "version": parser_version},
        "attachments": {
            "root": str(attachments_root.resolve()) if attachments_root is not None else None,
        },
        "outputs": {
            "messages": output_jsonl.name,
            "errors": errors_jsonl.name,
            "reconciliation": reconciliation_path.name,
        },
        "counts": {
            "messages_seen": seen,
            "messages_emitted": emitted,
            "attachments_seen": attachments,
            "attachments_resolved": resolved,
            "attachments_missing": missing,
            "unsupported": 0,
            "message_errors": message_errors,
            "reconciliation_errors": 0,
            "errors": message_errors,
        },
    }
    _write_manifest(manifest_path, manifest)

    report = reconcile_bundle(output_dir, source_path)
    unsupported = len(report.get("unsupported_records") or [])
    manifest["counts"]["unsupported"] = unsupported

    reconciliation_errors = 0
    if not report["ok"]:
        reconciliation_errors = 1
        error_payload = {
            "scope": "reconciliation",
            "error_type": "ReconciliationError",
            "error": "A1 source reconciliation failed",
            "failed_checks": report.get("failed_checks", []),
        }
        with errors_jsonl.open("a", encoding="utf-8", newline="\n") as error_stream:
            error_stream.write(json.dumps(error_payload, ensure_ascii=False, sort_keys=True))
            error_stream.write("\n")

    manifest["counts"]["reconciliation_errors"] = reconciliation_errors
    manifest["counts"]["errors"] = message_errors + reconciliation_errors
    _write_manifest(manifest_path, manifest)

    if reconciliation_errors:
        report = reconcile_bundle(output_dir, source_path)
    write_reconciliation(report, reconciliation_path)

    return ImportStats(
        messages_seen=seen,
        messages_emitted=emitted,
        attachments_seen=attachments,
        attachments_resolved=resolved,
        attachments_missing=missing,
        unsupported=unsupported,
        errors=message_errors + reconciliation_errors,
        output_jsonl=str(output_jsonl),
        errors_jsonl=str(errors_jsonl),
        reconciliation_json=str(reconciliation_path),
        reconciliation_ok=bool(report["ok"]),
        manifest=str(manifest_path),
        source_sha256=source_hash,
    )


def import_imessage(chat_db: Path, output_dir: Path, attachments_root: Path | None = None) -> ImportStats:
    if not chat_db.is_file():
        raise FileNotFoundError(chat_db)
    if attachments_root is not None and not attachments_root.is_dir():
        raise NotADirectoryError(attachments_root)
    return _write_records(
        IMessageParser(chat_db).iter_messages(),
        source_path=chat_db,
        source_type="imessage_chat_db",
        parser_name=IMESSAGE_PARSER_NAME,
        parser_version=IMESSAGE_PARSER_VERSION,
        output_dir=output_dir,
        attachments_root=attachments_root,
    )


def import_imazing_csv(csv_path: Path, output_dir: Path, attachments_root: Path | None = None) -> ImportStats:
    if not csv_path.is_file():
        raise FileNotFoundError(csv_path)
    if attachments_root is not None and not attachments_root.is_dir():
        raise NotADirectoryError(attachments_root)
    return _write_records(
        IMazingCSVParser(csv_path).iter_messages(),
        source_path=csv_path,
        source_type="imazing_messages_csv",
        parser_name=IMAZING_PARSER_NAME,
        parser_version=IMAZING_PARSER_VERSION,
        output_dir=output_dir,
        attachments_root=attachments_root,
    )


def import_generic_csv(csv_path: Path, output_dir: Path, attachments_root: Path | None = None) -> ImportStats:
    if not csv_path.is_file():
        raise FileNotFoundError(csv_path)
    if attachments_root is not None and not attachments_root.is_dir():
        raise NotADirectoryError(attachments_root)
    return _write_records(
        GenericCSVParser(csv_path).iter_messages(),
        source_path=csv_path,
        source_type="generic_message_csv",
        parser_name=GENERIC_CSV_PARSER_NAME,
        parser_version=GENERIC_STRUCTURED_PARSER_VERSION,
        output_dir=output_dir,
        attachments_root=attachments_root,
    )


def import_generic_json(json_path: Path, output_dir: Path, attachments_root: Path | None = None) -> ImportStats:
    if not json_path.is_file():
        raise FileNotFoundError(json_path)
    if attachments_root is not None and not attachments_root.is_dir():
        raise NotADirectoryError(attachments_root)
    source_type = "generic_message_jsonl" if json_path.suffix.lower() == ".jsonl" else "generic_message_json"
    return _write_records(
        GenericJSONParser(json_path).iter_messages(),
        source_path=json_path,
        source_type=source_type,
        parser_name=GENERIC_JSON_PARSER_NAME,
        parser_version=GENERIC_STRUCTURED_PARSER_VERSION,
        output_dir=output_dir,
        attachments_root=attachments_root,
    )


def import_generic_text(txt_path: Path, output_dir: Path, mode: TextMode) -> ImportStats:
    if not txt_path.is_file():
        raise FileNotFoundError(txt_path)
    return _write_records(
        GenericTextParser(txt_path, mode).iter_messages(),
        source_path=txt_path,
        source_type="generic_message_text",
        parser_name=GENERIC_TEXT_PARSER_NAME,
        parser_version=GENERIC_TEXT_PARSER_VERSION,
        output_dir=output_dir,
    )
