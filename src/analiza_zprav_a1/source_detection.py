from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


_SQLITE_MAGIC = b"SQLite format 3\x00"


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


@dataclass(frozen=True, slots=True)
class DetectionResult:
    source_type: str
    confidence: str
    reason: str
    requires_explicit_mode: bool = False


def _looks_like_imessage_sqlite(path: Path) -> bool:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return False
    try:
        conn.execute("PRAGMA query_only=ON")
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "message" not in tables:
            return False
        columns = {row[1] for row in conn.execute("PRAGMA table_info(message)")}
        return {"date", "is_from_me"}.issubset(columns)
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _detect_csv(path: Path) -> DetectionResult:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        sample = stream.read(8192)
        stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(stream, dialect=dialect)
        try:
            header = next(reader)
        except StopIteration:
            return DetectionResult("unknown", "none", "CSV file is empty")

    normalized = {_norm(value) for value in header if value}
    imazing_markers = {"chatsession", "chatsessionname"}
    message_markers = {
        "message",
        "messagetext",
        "text",
        "body",
        "content",
        "sentdate",
        "date",
        "datetime",
        "timestamp",
        "sender",
        "sendername",
        "from",
        "participant",
    }
    if normalized.intersection(imazing_markers) and normalized.intersection(message_markers):
        return DetectionResult(
            "imazing_messages_csv",
            "high",
            "CSV header contains iMazing chat-session fields and message fields",
        )
    if normalized.intersection(message_markers):
        return DetectionResult(
            "generic_message_csv",
            "high",
            "CSV header contains supported message fields",
        )
    return DetectionResult(
        "unknown",
        "none",
        "CSV header does not contain a supported message field",
    )


def detect_source(path: Path) -> DetectionResult:
    if not path.is_file():
        raise FileNotFoundError(path)

    with path.open("rb") as stream:
        head = stream.read(8192)

    if head.startswith(_SQLITE_MAGIC):
        if _looks_like_imessage_sqlite(path):
            return DetectionResult(
                "imessage_chat_db",
                "exact",
                "SQLite schema contains the required Apple Messages message fields",
            )
        return DetectionResult(
            "unknown",
            "none",
            "SQLite database is not a supported Apple Messages schema",
        )

    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _detect_csv(path)
    if suffix == ".jsonl":
        return DetectionResult(
            "generic_message_jsonl",
            "extension",
            "JSONL requires record validation during import",
        )
    if suffix == ".json":
        return DetectionResult(
            "generic_message_json",
            "extension",
            "JSON structure is validated during import",
        )
    if suffix == ".txt":
        return DetectionResult(
            "generic_message_text",
            "extension",
            "Plain text requires an explicit record-boundary mode",
            requires_explicit_mode=True,
        )

    return DetectionResult(
        "unknown",
        "none",
        f"Unsupported or ambiguous source format: {suffix or 'no extension'}",
    )
