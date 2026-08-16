from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .hashing import sha256_file, stable_message_key
from .parsers.imessage import IMessageParser

A1_CONTRACT_VERSION = "1"
PARSER_NAME = "imessage-chatdb"
PARSER_VERSION = "0.2.0"


@dataclass(slots=True)
class ImportStats:
    messages_seen: int
    attachments_seen: int
    errors: int
    output_jsonl: str
    manifest: str
    source_sha256: str


def import_imessage(chat_db: Path, output_dir: Path) -> ImportStats:
    if not chat_db.is_file():
        raise FileNotFoundError(chat_db)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = output_dir / "messages.jsonl"
    manifest_path = output_dir / "manifest.json"
    source_hash = sha256_file(chat_db)

    seen = attachments = errors = 0
    parser = IMessageParser(chat_db)

    with output_jsonl.open("w", encoding="utf-8", newline="\n") as stream:
        for record in parser.iter_messages():
            seen += 1
            attachments += len(record.attachments)
            try:
                payload = asdict(record)
                payload.update(
                    {
                        "contract_version": A1_CONTRACT_VERSION,
                        "record_type": "message",
                        "source_type": "imessage_chat_db",
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
            "type": "imessage_chat_db",
            "name": chat_db.name,
            "sha256": source_hash,
        },
        "parser": {"name": PARSER_NAME, "version": PARSER_VERSION},
        "outputs": {"messages": output_jsonl.name},
        "counts": {
            "messages_seen": seen,
            "attachments_seen": attachments,
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
        errors=errors,
        output_jsonl=str(output_jsonl),
        manifest=str(manifest_path),
        source_sha256=source_hash,
    )
