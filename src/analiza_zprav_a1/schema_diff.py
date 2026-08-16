from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .sqlite_schema import schema_signature

SCHEMA_DIFF_VERSION = "1"
SUPPORTED_SCHEMA_INVENTORY_VERSIONS = {"1"}


def load_schema_inventory(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid schema inventory JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Schema inventory must be a JSON object")

    version = str(value.get("inventory_version") or "")
    if version not in SUPPORTED_SCHEMA_INVENTORY_VERSIONS:
        raise ValueError(f"Unsupported schema inventory version: {version!r}")
    signature = value.get("signature_sha256")
    if not isinstance(signature, str) or not signature:
        raise ValueError("Schema inventory requires signature_sha256")

    tables = value.get("tables")
    if not isinstance(tables, list):
        raise ValueError("Schema inventory tables must be an array")

    names: list[str] = []
    for table in tables:
        if not isinstance(table, dict):
            raise ValueError("Schema inventory table entries must be objects")
        name = table.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Schema inventory table requires a non-empty name")
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError("Schema inventory contains duplicate table names")

    signed_payload = {key: item for key, item in value.items() if key != "signature_sha256"}
    actual_signature = schema_signature(signed_payload)
    if actual_signature != signature:
        raise ValueError(
            "Schema inventory signature_sha256 does not match its canonical payload"
        )
    return value


def _mapping_by_name(items: Any, *, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise ValueError(f"Schema inventory {label} must be an array")
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"Schema inventory {label} entries must be objects")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Schema inventory {label} entry requires a non-empty name")
        if name in result:
            raise ValueError(f"Schema inventory {label} contains duplicate name {name!r}")
        result[name] = item
    return result


def _canonical_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _set_diff(before: Any, after: Any, *, label: str) -> dict[str, list[Any]]:
    if not isinstance(before, list) or not isinstance(after, list):
        raise ValueError(f"Schema inventory {label} must be arrays")
    before_map = {_canonical_key(item): item for item in before}
    after_map = {_canonical_key(item): item for item in after}
    return {
        "added": [after_map[key] for key in sorted(set(after_map) - set(before_map))],
        "removed": [before_map[key] for key in sorted(set(before_map) - set(after_map))],
    }


def _named_diff(before: Any, after: Any, *, label: str) -> dict[str, Any]:
    before_map = _mapping_by_name(before, label=label)
    after_map = _mapping_by_name(after, label=label)
    before_names = set(before_map)
    after_names = set(after_map)
    changed = [
        {
            "name": name,
            "before": before_map[name],
            "after": after_map[name],
        }
        for name in sorted(before_names & after_names)
        if before_map[name] != after_map[name]
    ]
    return {
        "added": [after_map[name] for name in sorted(after_names - before_names)],
        "removed": [before_map[name] for name in sorted(before_names - after_names)],
        "changed": changed,
    }


def _has_delta(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_delta(item) for item in value.values())
    if isinstance(value, list):
        return bool(value)
    return bool(value)


def compare_schema_inventories(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    before_version = str(before.get("inventory_version") or "")
    after_version = str(after.get("inventory_version") or "")
    if before_version not in SUPPORTED_SCHEMA_INVENTORY_VERSIONS:
        raise ValueError(f"Unsupported before schema inventory version: {before_version!r}")
    if after_version not in SUPPORTED_SCHEMA_INVENTORY_VERSIONS:
        raise ValueError(f"Unsupported after schema inventory version: {after_version!r}")

    before_tables = _mapping_by_name(before.get("tables"), label="tables")
    after_tables = _mapping_by_name(after.get("tables"), label="tables")
    before_names = set(before_tables)
    after_names = set(after_tables)

    table_changes: list[dict[str, Any]] = []
    for name in sorted(before_names & after_names):
        before_table = before_tables[name]
        after_table = after_tables[name]
        columns = _named_diff(
            before_table.get("columns"),
            after_table.get("columns"),
            label=f"table {name!r} columns",
        )
        indexes = _named_diff(
            before_table.get("indexes"),
            after_table.get("indexes"),
            label=f"table {name!r} indexes",
        )
        foreign_keys = _set_diff(
            before_table.get("foreign_keys"),
            after_table.get("foreign_keys"),
            label=f"table {name!r} foreign_keys",
        )
        delta = {
            "name": name,
            "columns": columns,
            "foreign_keys": foreign_keys,
            "indexes": indexes,
        }
        if _has_delta({key: value for key, value in delta.items() if key != "name"}):
            table_changes.append(delta)

    before_sqlite = before.get("sqlite") or {}
    after_sqlite = after.get("sqlite") or {}
    if not isinstance(before_sqlite, dict) or not isinstance(after_sqlite, dict):
        raise ValueError("Schema inventory sqlite metadata must be objects")
    sqlite_changes = {
        key: {"before": before_sqlite.get(key), "after": after_sqlite.get(key)}
        for key in sorted(set(before_sqlite) | set(after_sqlite))
        if before_sqlite.get(key) != after_sqlite.get(key)
    }

    tables = {
        "added": [after_tables[name] for name in sorted(after_names - before_names)],
        "removed": [before_tables[name] for name in sorted(before_names - after_names)],
        "changed": table_changes,
    }
    changed = bool(sqlite_changes) or _has_delta(tables)

    return {
        "diff_version": SCHEMA_DIFF_VERSION,
        "changed": changed,
        "before": {
            "inventory_version": before_version,
            "signature_sha256": before.get("signature_sha256"),
        },
        "after": {
            "inventory_version": after_version,
            "signature_sha256": after.get("signature_sha256"),
        },
        "sqlite": {"changed": sqlite_changes},
        "tables": tables,
    }


def compare_schema_files(before_path: Path, after_path: Path) -> dict[str, Any]:
    return compare_schema_inventories(
        load_schema_inventory(before_path),
        load_schema_inventory(after_path),
    )
