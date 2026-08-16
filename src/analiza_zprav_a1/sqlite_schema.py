from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_INVENTORY_VERSION = "1"


def _readonly_sqlite(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    quoted = _quote_identifier(table)
    try:
        rows = list(conn.execute(f"PRAGMA table_xinfo({quoted})"))
        has_hidden = bool(rows) and "hidden" in rows[0].keys()
    except sqlite3.DatabaseError:
        rows = list(conn.execute(f"PRAGMA table_info({quoted})"))
        has_hidden = False

    columns: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "cid": int(row["cid"]),
            "name": str(row["name"]),
            "type": "" if row["type"] is None else str(row["type"]),
            "not_null": bool(row["notnull"]),
            "default": row["dflt_value"],
            "primary_key_position": int(row["pk"]),
        }
        if has_hidden:
            item["hidden"] = int(row["hidden"])
        columns.append(item)
    return columns


def _foreign_keys(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    quoted = _quote_identifier(table)
    rows = conn.execute(f"PRAGMA foreign_key_list({quoted})")
    result = [
        {
            "id": int(row["id"]),
            "seq": int(row["seq"]),
            "table": str(row["table"]),
            "from": None if row["from"] is None else str(row["from"]),
            "to": None if row["to"] is None else str(row["to"]),
            "on_update": str(row["on_update"]),
            "on_delete": str(row["on_delete"]),
            "match": str(row["match"]),
        }
        for row in rows
    ]
    return sorted(result, key=lambda item: (item["id"], item["seq"]))


def _indexes(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    quoted_table = _quote_identifier(table)
    indexes: list[dict[str, Any]] = []
    for row in conn.execute(f"PRAGMA index_list({quoted_table})"):
        name = str(row["name"])
        quoted_index = _quote_identifier(name)
        columns = [
            {
                "seqno": int(column["seqno"]),
                "cid": int(column["cid"]),
                "name": None if column["name"] is None else str(column["name"]),
            }
            for column in conn.execute(f"PRAGMA index_info({quoted_index})")
        ]
        indexes.append(
            {
                "name": name,
                "unique": bool(row["unique"]),
                "origin": str(row["origin"]),
                "partial": bool(row["partial"]),
                "columns": sorted(columns, key=lambda item: item["seqno"]),
            }
        )
    return sorted(indexes, key=lambda item: item["name"])


def _inventory_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    application_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
    table_names = [
        str(row[0])
        for row in conn.execute(
            """SELECT name
               FROM sqlite_master
               WHERE type='table' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        )
    ]

    return {
        "inventory_version": SCHEMA_INVENTORY_VERSION,
        "sqlite": {
            "user_version": user_version,
            "application_id": application_id,
        },
        "tables": [
            {
                "name": table,
                "columns": _table_columns(conn, table),
                "foreign_keys": _foreign_keys(conn, table),
                "indexes": _indexes(conn, table),
            }
            for table in table_names
        ],
    }


def schema_signature(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def inventory_sqlite_schema(path: Path) -> dict[str, Any]:
    """Inventory SQLite schema metadata without reading user row values.

    The returned signature depends only on canonical PRAGMA/schema metadata. It
    intentionally excludes source data and SQLite's mutable `schema_version` so
    identical logical schemas receive the same signature across snapshots.
    """

    if not path.is_file():
        raise FileNotFoundError(path)
    with _readonly_sqlite(path) as conn:
        payload = _inventory_payload(conn)
    return {
        **payload,
        "signature_sha256": schema_signature(payload),
    }


def write_schema_inventory(inventory: dict[str, Any], path: Path) -> None:
    path.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
