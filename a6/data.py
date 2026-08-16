from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Iterable

import pandas as pd


CANONICAL_COLUMNS = [
    "membership_id",
    "message_id",
    "conversation_id",
    "contact",
    "sender",
    "timestamp",
    "timestamp_precision",
    "timestamp_quality",
    "text",
]

_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "membership_id": ("membership_id",),
    "message_id": ("message_id", "id", "guid", "message_guid"),
    "conversation_id": ("conversation_id", "chat_id", "thread_id", "conversation"),
    "contact": ("contact", "contact_name", "conversation_title", "chat_name", "display_name"),
    "sender": ("sender", "sender_name", "participant_name", "author", "from_name"),
    "timestamp": ("timestamp", "sent_at_utc_us", "sent_at_utc", "created_at_utc", "date_utc", "sent_at", "created_at", "date"),
    "timestamp_precision": ("timestamp_precision",),
    "timestamp_quality": ("timestamp_quality",),
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
        ("Osoba A", "Dobré ráno, jak se dnes máš?", 0),
        ("Osoba B", "Dobré, jen mám hodně práce.", 420),
        ("Osoba A", "Rozumím. Ozvi se, až budeš mít chvíli.", 630),
        ("Osoba B", "Díky, večer zavolám.", 3600),
        ("Osoba A", "Platí.", 3660),
        ("Osoba B", "Nakonec dorazím později.", 86400 + 1500),
        ("Osoba A", "Dobře, dej vědět až vyrazíš.", 86400 + 2100),
        ("Osoba B", "Jedu.", 86400 + 7200),
        ("Osoba A", "OK.", 86400 + 7260),
        ("Osoba B", "Můžeme probrat včerejšek?", 2 * 86400 + 1200),
        ("Osoba A", "Ano. Chci tomu rozumět, ne se hádat.", 2 * 86400 + 1500),
        ("Osoba B", "To bych chtěla taky.", 2 * 86400 + 1740),
    ]
    rows: list[dict[str, object]] = []
    for idx, (sender, text, offset) in enumerate(messages, start=1):
        rows.append(
            {
                "membership_id": f"demo-membership-{idx:04d}",
                "message_id": f"demo-{idx:04d}",
                "conversation_id": "demo-conversation-1",
                "contact": "Demo kontakt",
                "sender": sender,
                "timestamp": base + pd.Timedelta(seconds=offset),
                "timestamp_precision": "second",
                "timestamp_quality": "exact",
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
    if "membership_id" in mapping:
        score += 4
    return score


def discover_message_object(path: str | Path) -> tuple[str, dict[str, str]]:
    """Compatibility-only discovery for non-A2 SQLite exports.

    Production A2 databases are handled explicitly by ``load_sqlite_messages``
    and never pass through heuristic object selection.
    """
    with _connect_read_only(path) as conn:
        best: tuple[int, str, dict[str, str]] | None = None
        for object_name in _objects(conn):
            if object_name == "analysis_messages":
                continue
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


def _load_a2_messages(conn: sqlite3.Connection) -> pd.DataFrame:
    columns = set(_columns(conn, "analysis_messages"))
    required = {
        "membership_id",
        "id",
        "conversation_id",
        "sender_name",
        "sent_at_utc_us",
        "timestamp_precision",
        "timestamp_quality",
        "text",
    }
    missing = sorted(required - columns)
    if missing:
        raise DataSourceError(
            "A2 analysis_messages nemá očekávaný membership-aware kontrakt; chybí: "
            + ", ".join(missing)
        )
    frame = pd.read_sql_query(
        "SELECT membership_id, id AS message_id, conversation_id, "
        "sender_name AS sender, sent_at_utc_us AS timestamp, "
        "timestamp_precision, timestamp_quality, text "
        "FROM analysis_messages",
        conn,
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="us", errors="coerce", utc=True)

    if "analysis_conversations" in set(_objects(conn)):
        conversations = pd.read_sql_query(
            "SELECT id AS conversation_id, title, canonical_key FROM analysis_conversations",
            conn,
        )
        frame = frame.merge(conversations, on="conversation_id", how="left")
        frame["contact"] = (
            frame["title"]
            .fillna(frame["canonical_key"])
            .fillna(frame["conversation_id"].astype(str))
        )
        frame = frame.drop(columns=["title", "canonical_key"])
    else:
        frame["contact"] = frame["conversation_id"].astype(str)
    return frame


def load_sqlite_messages(path: str | Path) -> tuple[pd.DataFrame, SourceInfo]:
    try:
        with _connect_read_only(path) as conn:
            objects = set(_objects(conn))
            if "analysis_messages" in objects:
                frame = _load_a2_messages(conn)
                object_name = "analysis_messages"
            else:
                object_name, mapping = discover_message_object(path)
                select_parts = [
                    f"{_quote_identifier(source)} AS {_quote_identifier(canonical)}"
                    for canonical, source in mapping.items()
                ]
                query = f"SELECT {', '.join(select_parts)} FROM {_quote_identifier(object_name)}"
                frame = pd.read_sql_query(query, conn)
                if mapping.get("timestamp") == "sent_at_utc_us" and "timestamp" in frame:
                    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="us", errors="coerce", utc=True)
    except DataSourceError:
        raise
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
    """Normalize display fields without deleting canonical memberships.

    A2 v5 ``analysis_messages`` is membership-scoped. A physical message may
    therefore occur more than once with distinct ``membership_id`` values.
    Unknown timestamps are valid canonical state and remain as ``NaT``.
    """
    result = frame.copy()
    for column in CANONICAL_COLUMNS:
        if column not in result:
            result[column] = ""

    result["message_id"] = result["message_id"].fillna("").astype(str)
    result["conversation_id"] = result["conversation_id"].fillna("").astype(str)
    result["contact"] = result["contact"].fillna("").astype(str)
    result["sender"] = result["sender"].fillna("Neznámý odesílatel").astype(str)
    result["text"] = result["text"].fillna("").astype(str)
    result["timestamp_precision"] = result["timestamp_precision"].replace("", "unknown").fillna("unknown").astype(str)
    result["timestamp_quality"] = result["timestamp_quality"].replace("", "unknown").fillna("unknown").astype(str)
    result["timestamp"] = pd.to_datetime(result["timestamp"], errors="coerce", utc=True)

    membership = result["membership_id"].fillna("").astype(str)
    missing_membership = membership.eq("")
    membership.loc[missing_membership] = (
        "compat:" + result.loc[missing_membership, "conversation_id"] + ":" + result.loc[missing_membership, "message_id"]
    )
    result["membership_id"] = membership

    duplicated_memberships = result["membership_id"].duplicated(keep=False)
    if duplicated_memberships.any():
        duplicates = sorted(result.loc[duplicated_memberships, "membership_id"].unique())
        raise DataSourceError("Nejednoznačná membership identity v read modelu: " + ", ".join(duplicates))

    duplicated_pair = result.duplicated(subset=["conversation_id", "message_id"], keep=False)
    if duplicated_pair.any():
        pairs = result.loc[duplicated_pair, ["conversation_id", "message_id"]].drop_duplicates()
        rendered = [f"{row.conversation_id}/{row.message_id}" for row in pairs.itertuples(index=False)]
        raise DataSourceError("Duplicitní message membership uvnitř jedné konverzace: " + ", ".join(rendered))

    result = result.sort_values(
        ["timestamp", "conversation_id", "membership_id"],
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)
    return result[CANONICAL_COLUMNS]


def add_opposite_sender_gap(frame: pd.DataFrame, max_gap_hours: float = 72.0) -> pd.DataFrame:
    """Fallback adjacency heuristic; this is not proof of a reply."""
    result = normalize_frame(frame)
    result["opposite_sender_gap_seconds"] = pd.NA
    if result.empty:
        return result

    grouped = result.groupby("conversation_id", sort=False, dropna=False)
    previous_sender = grouped["sender"].shift(1)
    previous_time = grouped["timestamp"].shift(1)
    delta = (result["timestamp"] - previous_time).dt.total_seconds()
    sender_changed = result["sender"].ne(previous_sender) & previous_sender.notna()
    valid_gap = delta.between(0, max_gap_hours * 3600, inclusive="both")
    result.loc[sender_changed & valid_gap, "opposite_sender_gap_seconds"] = delta[sender_changed & valid_gap]
    return result


def add_response_latency(frame: pd.DataFrame, max_gap_hours: float = 72.0) -> pd.DataFrame:
    """Compatibility wrapper for older A6 callers.

    The value is an opposite-sender adjacency gap, not an asserted reply
    latency. New UI code should use ``add_opposite_sender_gap``.
    """
    result = add_opposite_sender_gap(frame, max_gap_hours=max_gap_hours)
    result["response_seconds"] = result["opposite_sender_gap_seconds"]
    return result


def filter_messages(
    frame: pd.DataFrame,
    contact: str | None = None,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    senders: Iterable[str] | None = None,
    search: str | None = None,
    include_unknown_timestamps: bool = True,
) -> pd.DataFrame:
    result = normalize_frame(frame)
    if contact:
        result = result[result["contact"] == contact]
    if start is not None:
        start_utc = pd.Timestamp(start)
        start_utc = start_utc.tz_localize("UTC") if start_utc.tzinfo is None else start_utc.tz_convert("UTC")
        mask = result["timestamp"] >= start_utc
        if include_unknown_timestamps:
            mask |= result["timestamp"].isna()
        result = result[mask]
    if end is not None:
        end_utc = pd.Timestamp(end)
        end_utc = end_utc.tz_localize("UTC") if end_utc.tzinfo is None else end_utc.tz_convert("UTC")
        mask = result["timestamp"] <= end_utc
        if include_unknown_timestamps:
            mask |= result["timestamp"].isna()
        result = result[mask]
    if senders is not None:
        sender_set = set(senders)
        result = result[result["sender"].isin(sender_set)]
    if search:
        result = result[result["text"].str.contains(search, case=False, regex=False, na=False)]
    return result.reset_index(drop=True)


def analysis_packet(
    frame: pd.DataFrame,
    selected_ids: Iterable[str],
    context_before: int = 0,
    context_after: int = 0,
) -> dict[str, object]:
    canonical = normalize_frame(frame)
    selected_list = [str(value) for value in selected_ids]
    if len(set(selected_list)) != len(selected_list):
        raise ValueError("Vybrané message_id obsahují duplicitu.")
    selected = set(selected_list)
    if not selected:
        raise ValueError("Pro A5 musí být vybrána alespoň jedna zpráva.")

    selected_rows = canonical[canonical["message_id"].isin(selected)]
    missing = sorted(selected - set(selected_rows["message_id"]))
    if missing:
        raise ValueError("Vybraná message_id nejsou v aktuální konverzaci: " + ", ".join(missing))
    if selected_rows["conversation_id"].nunique(dropna=False) != 1:
        raise ValueError("A5 výběr musí patřit právě do jedné konverzace.")
    if selected_rows["timestamp"].isna().any():
        blocked = list(selected_rows.loc[selected_rows["timestamp"].isna(), "message_id"].astype(str))
        raise ValueError(
            "A5 vyžaduje známý timezone-aware timestamp; nelze analyzovat zprávy bez času: "
            + ", ".join(blocked)
        )

    include_indexes: set[int] = set()
    for _, conversation in canonical.groupby("conversation_id", sort=False, dropna=False):
        positions = list(conversation.index)
        local_position = {index: pos for pos, index in enumerate(positions)}
        for index in positions:
            if canonical.at[index, "message_id"] not in selected:
                continue
            pos = local_position[index]
            lo = max(0, pos - max(0, int(context_before)))
            hi = min(len(positions), pos + max(0, int(context_after)) + 1)
            include_indexes.update(positions[lo:hi])

    subset = canonical.loc[sorted(include_indexes)] if include_indexes else canonical.iloc[0:0]
    if subset["timestamp"].isna().any():
        blocked = list(subset.loc[subset["timestamp"].isna(), "message_id"].astype(str))
        raise ValueError(
            "Zvolený A5 kontext obsahuje zprávy bez známého času. Zmenšete kontext nebo zvolte jiný úsek: "
            + ", ".join(blocked)
        )

    messages = [
        {
            "membership_id": row.membership_id,
            "message_id": row.message_id,
            "conversation_id": row.conversation_id,
            "contact": row.contact,
            "sender": row.sender,
            "timestamp": row.timestamp.isoformat(),
            "timestamp_precision": row.timestamp_precision,
            "timestamp_quality": row.timestamp_quality,
            "text": row.text,
            "selected": row.message_id in selected,
        }
        for row in subset.itertuples(index=False)
    ]
    return {
        "schema_version": 1,
        "selected_message_ids": selected_list,
        "selected_message_count": sum(1 for item in messages if item["selected"]),
        "message_count": len(messages),
        "context_before": max(0, int(context_before)),
        "context_after": max(0, int(context_after)),
        "messages": messages,
    }
