from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .attachments import resolve_attachments
from .hashing import sha256_file, stable_message_key
from .models import MessageRecord
from .parsers.imazing_csv import IMazingCSVParser
from .parsers.imessage import IMessageParser

A1_CONTRACT_VERSION = "1"
IMESSAGE_PARSER_NAME = "imessage-chatdb"
IMESSAGE_PARSER_VERSION = "0.3.0"
IMAZING_PARSER_NAME = "imazing-messages-csv"
IMAZING_PARSER_VERSION = "0.1.0"


@dataclass(slots=True)
class ImportStats:
    messages_seen: int
    attachments_seen: int
    attachments_resolved: int
    attachments_missing: int
    errors: int
    output_jsonl: str
    manifest: str
    source_sha256: str


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
    source_hash = sha256_file(source_path)

    seen = attachments = resolved = missing = errors = 0
    with output_jsonl.open("w", encoding="utf-8", newline="\n") as stream:
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
            except Exception:
                errors += 1

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
        "outputs": {"messages": output_jsonl.name},
        "counts": {
            "messages_seen": seen,
            "attachments_seen": attachments,
            "attachments_resolved": resolved,
            "attachments_missing": missing,
            "errors": errors,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return ImportStats(
        messages_seen=seen,
        attachments_seen=attachments,
        attachments_resolved=resolved,
        attachments_missing=missing,
        errors=errors,
        output_jsonl=str(output_jsonl),
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
