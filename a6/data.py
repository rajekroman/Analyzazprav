from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Iterable

import pandas as pd


CANONICAL_COLUMNS = [
    "message_id",
    "conversation_id",
    "contact",
    "sender",
    "timestamp",
    "text",
]

_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "message_id": ("message_id", "id", "guid", "message_guid"),
    "conversation_id": ("conversation_id", "chat_id", "thread_id", "conversation"),
    "contact": ("contact", "contact_name", "conversation_title", "chat_name", "display_name"),
    "sender": ("sender", "sender_name", "participant_name", "author", "from_name"),
    "timestamp": ("timestamp", "sent_at_utc", "created_at_utc", "date_utc", "sent_at", "created_at", "date"),
    "text": ("text", "body", "message_text", "raw_text", "content"),
}


@dataclass(frozen=True)
class SourceInfo:
    kind: str
    label: str
    object_name: str | None = None


class DataSourceError(RuntimeError):
    pass


def _demo_rows() -> list[dict[str, object]]:
    base = pd.Timestamp("2026-08-01 08:00:00", tz="UTC")
    messages = [
        ("Roman", "Dobré ráno, jak se dnes máš?", 0),
        ("Ilona", "Dobré, jen mám hodně práce.", 420),
        ("Roman", "Rozumím. Ozvi se, až budeš mít chvíli.", 630),
        ("Ilona", "Díky, večer zavolám.", 3600),
        ("Roman", "Platí.", 3660),
        ("Ilona", "Nakonec dorazím později.", 86400 + 1500),
        ("Roman", "Dobře, dej vědět až vyrazíš.", 86400 + 2100),
        ("Ilona", "Jedu.", 86400 + 7200),
        ("Roman", "OK.", 86400 + 7260),
        ("Ilona", "Můžeme probrat včerejšek?", 2 * 86400 + 1200),
        ("Roman", "Ano. Chci tomu rozumět, ne se hádat.", 2 * 86400 + 1500),
        ("Ilona", "To bych chtěla taky.", 2 * 86400 + 1740),
    ]
    rows: list[dict[str, object]] = []
    for idx, (sender, text, offset) in enumerate(messages, start=1):
        rows.append(
            {
                "message_id": f"demo-{idx:04d}",
                "conversation_id": "demo-conversation-1",
                "contact": "Demo kontakt",
                "sender": sender,
                "timestamp": base + pd.Timedelta(seconds=offset),
                "text": text,
            }
        )
    return rows


def demo_messages() -> pd.DataFrame:
    return normalize_frame(pd.DataFrame(_demo_rows()))


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _connect_read_only(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path).expanduser().resolve()
    if not db_path.exists() or not db_path.is_file():
        raise DataSourceError(f"Databáze neexistuje: {db_path}")
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise DataSourceError(f"Databázi nelze otevřít pouze pro čtení: {exc}") from exc


def _objects(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('view', 'table') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'view' THEN 0 ELSE 1 END, name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _columns(conn: sqlite3.Connection, object_name: str) -> list[str]:
    query = f"PRAGMA table_info({_quote_identifier(object_name)})"
    return [str(row[1]) for row in conn.execute(query).fetchall()]


def _resolve_mapping(columns: Iterable[str]) -> dict[str, str]:
    available = {column.lower(): column for column in columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias.lower() in available:
                mapping[canonical] = available[alias.lower()]
                break
    return mapping


def _score_mapping(mapping: dict[str, str]) -> int:
    required = {"message_id", "timestamp", "text"}
    if not required.issubset(mapping):
        return -1
    score = len(mapping)
    if "sender" in mapping:
        score += 2
    if "contact" in mapping or "conversation_id" in mapping:
        score += 2
    return score


def discover_message_object(path: str | Path) -> tuple[str, dict[str, str]]:
    with _connect_read_only(path) as conn:
        best: tuple[int, str, dict[str, str]] | None = None
        for object_name in _objects(conn):
            mapping = _resolve_mapping(_columns(conn, object_name))
            score = _score_mapping(mapping)
            if score < 0:
                continue
            candidate = (score, object_name, mapping)
            if best is None or candidate[0] > best[0]:
                best = candidate
        if best is None:
            raise DataSourceError(
                "Nebyla nalezena kompatibilní tabulka/view se sloupci pro message_id, timestamp a text."
            )
        return best[1], best[2]


def load_sqlite_messages(path: str | Path) -> tuple[pd.DataFrame, SourceInfo]:
    object_name, mapping = discover_message_object(path)
    select_parts = [
        f"{_quote_identifier(source)} AS {_quote_identifier(canonical)}"
        for canonical, source in mapping.items()
    ]
    query = f"SELECT {', '.join(select_parts)} FROM {_quote_identifier(object_name)}"
    try:
        with _connect_read_only(path) as conn:
            frame = pd.read_sql_query(query, conn)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise DataSourceError(f"Chyba při čtení databáze: {exc}") from exc

    if "conversation_id" not in frame:
        frame["conversation_id"] = "conversation"
    if "contact" not in frame:
        frame["contact"] = frame["conversation_id"].astype(str)
    if "sender" not in frame:
        frame["sender"] = "Neznámý odesílatel"

    return normalize_frame(frame), SourceInfo(
        kind="sqlite",
        label=str(Path(path).expanduser()),
        object_name=object_name,
    )


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in CANONICAL_COLUMNS:
        if column not in result:
            result[column] = ""

    result["message_id"] = result["message_id"].astype(str)
    result["conversation_id"] = result["conversation_id"].fillna("").astype(str)
    result["contact"] = result["contact"].fillna("").astype(str)
    result["sender"] = result["sender"].fillna("Neznámý odesílatel").astype(str)
    result["text"] = result["text"].fillna("").astype(str)
    result["timestamp"] = pd.to_datetime(result["timestamp"], errors="coerce", utc=True)
    result = result.dropna(subset=["timestamp"])
    result = result.drop_duplicates(subset=["message_id"], keep="first")
    result = result.sort_values(["timestamp", "message_id"], kind="stable").reset_index(drop=True)
    return result[CANONICAL_COLUMNS]


def add_response_latency(frame: pd.DataFrame, max_gap_hours: float = 72.0) -> pd.DataFrame:
    result = normalize_frame(frame)
    result["response_seconds"] = pd.NA
    if result.empty:
        return result

    previous_sender = result["sender"].shift(1)
    previous_time = result["timestamp"].shift(1)
    delta = (result["timestamp"] - previous_time).dt.total_seconds()
    is_reply = result["sender"].ne(previous_sender) & previous_sender.notna()
    valid_gap = delta.between(0, max_gap_hours * 3600, inclusive="both")
    result.loc[is_reply & valid_gap, "response_seconds"] = delta[is_reply & valid_gap]
    return result


def filter_messages(
    frame: pd.DataFrame,
    contact: str | None = None,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    senders: Iterable[str] | None = None,
    search: str | None = None,
) -> pd.DataFrame:
    result = normalize_frame(frame)
    if contact:
        result = result[result["contact"] == contact]
    if start is not None:
        start_utc = pd.Timestamp(start)
        start_utc = start_utc.tz_localize("UTC") if start_utc.tzinfo is None else start_utc.tz_convert("UTC")
        result = result[result["timestamp"] >= start_utc]
    if end is not None:
        end_utc = pd.Timestamp(end)
        end_utc = end_utc.tz_localize("UTC") if end_utc.tzinfo is None else end_utc.tz_convert("UTC")
        result = result[result["timestamp"] <= end_utc]
    if senders:
        sender_set = set(senders)
        result = result[result["sender"].isin(sender_set)]
    if search:
        result = result[result["text"].str.contains(search, case=False, regex=False, na=False)]
    return result.reset_index(drop=True)


def analysis_packet(frame: pd.DataFrame, selected_ids: Iterable[str]) -> dict[str, object]:
    selected = set(str(value) for value in selected_ids)
    subset = normalize_frame(frame)
    subset = subset[subset["message_id"].isin(selected)]
    messages = [
        {
            "message_id": row.message_id,
            "conversation_id": row.conversation_id,
            "contact": row.contact,
            "sender": row.sender,
            "timestamp": row.timestamp.isoformat(),
            "text": row.text,
        }
        for row in subset.itertuples(index=False)
    ]
    return {
        "schema_version": 1,
        "message_count": len(messages),
        "messages": messages,
    }
