import io
import json
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from analiza_zprav_a1.cli import main as import_cli_main
from analiza_zprav_a1.schema_diff import (
    compare_schema_files,
    compare_schema_inventories,
    load_schema_inventory,
)
from analiza_zprav_a1.sqlite_schema import inventory_sqlite_schema, write_schema_inventory


def _make_before(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=1")
    conn.executescript(
        """
        CREATE TABLE parent (
          id INTEGER PRIMARY KEY,
          label TEXT
        );
        CREATE TABLE child (
          id INTEGER PRIMARY KEY,
          parent_id INTEGER,
          body TEXT,
          FOREIGN KEY(parent_id) REFERENCES parent(id) ON DELETE CASCADE
        );
        CREATE INDEX child_body_idx ON child(body);
        """
    )
    conn.commit()
    conn.close()


def _make_after(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=2")
    conn.executescript(
        """
        CREATE TABLE parent (
          id INTEGER PRIMARY KEY,
          label TEXT,
          service TEXT
        );
        CREATE TABLE child (
          id INTEGER PRIMARY KEY,
          parent_id INTEGER,
          body TEXT,
          FOREIGN KEY(parent_id) REFERENCES parent(id) ON DELETE SET NULL
        );
        CREATE UNIQUE INDEX child_body_idx ON child(body);
        CREATE TABLE extra (
          id INTEGER PRIMARY KEY,
          note TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def _write_inventory(db_path: Path, output_path: Path) -> dict:
    inventory = inventory_sqlite_schema(db_path)
    write_schema_inventory(inventory, output_path)
    return inventory


def test_identical_schema_inventory_has_no_diff(tmp_path: Path) -> None:
    db = tmp_path / "same.sqlite"
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    _make_before(db)
    inventory = _write_inventory(db, before_path)
    write_schema_inventory(inventory, after_path)

    report = compare_schema_files(before_path, after_path)
    assert report["diff_version"] == "1"
    assert report["changed"] is False
    assert report["sqlite"]["changed"] == {}
    assert report["tables"] == {"added": [], "removed": [], "changed": []}
    assert report["before"]["signature_sha256"] == report["after"]["signature_sha256"]


def test_schema_diff_reports_structural_changes_without_row_data(tmp_path: Path) -> None:
    before_db = tmp_path / "before.sqlite"
    after_db = tmp_path / "after.sqlite"
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    _make_before(before_db)
    _make_after(after_db)

    before = _write_inventory(before_db, before_path)
    after = _write_inventory(after_db, after_path)
    report = compare_schema_inventories(before, after)

    assert report["changed"] is True
    assert report["sqlite"]["changed"]["user_version"] == {"before": 1, "after": 2}
    assert [table["name"] for table in report["tables"]["added"]] == ["extra"]
    assert report["tables"]["removed"] == []

    parent = next(item for item in report["tables"]["changed"] if item["name"] == "parent")
    assert [column["name"] for column in parent["columns"]["added"]] == ["service"]
    assert parent["indexes"] == {"added": [], "removed": [], "changed": []}

    child = next(item for item in report["tables"]["changed"] if item["name"] == "child")
    assert child["columns"] == {"added": [], "removed": [], "changed": []}
    assert len(child["indexes"]["changed"]) == 1
    assert child["indexes"]["changed"][0]["name"] == "child_body_idx"
    assert child["indexes"]["changed"][0]["before"]["unique"] is False
    assert child["indexes"]["changed"][0]["after"]["unique"] is True
    assert len(child["foreign_keys"]["removed"]) == 1
    assert len(child["foreign_keys"]["added"]) == 1
    assert child["foreign_keys"]["removed"][0]["on_delete"] == "CASCADE"
    assert child["foreign_keys"]["added"][0]["on_delete"] == "SET NULL"

    encoded = json.dumps(report, ensure_ascii=False)
    assert "PRIVATE-MESSAGE" not in encoded
    assert "row" not in report


def test_reverse_diff_reports_removed_table_and_column(tmp_path: Path) -> None:
    before_db = tmp_path / "before.sqlite"
    after_db = tmp_path / "after.sqlite"
    _make_before(before_db)
    _make_after(after_db)

    report = compare_schema_inventories(
        inventory_sqlite_schema(after_db),
        inventory_sqlite_schema(before_db),
    )

    assert [table["name"] for table in report["tables"]["removed"]] == ["extra"]
    parent = next(item for item in report["tables"]["changed"] if item["name"] == "parent")
    assert [column["name"] for column in parent["columns"]["removed"]] == ["service"]


def test_changed_column_definition_is_reported_as_changed() -> None:
    before = {
        "inventory_version": "1",
        "signature_sha256": "a" * 64,
        "sqlite": {"user_version": 1, "application_id": 0},
        "tables": [
            {
                "name": "message",
                "columns": [
                    {
                        "cid": 0,
                        "name": "text",
                        "type": "TEXT",
                        "not_null": False,
                        "default": None,
                        "primary_key_position": 0,
                        "hidden": 0,
                    }
                ],
                "foreign_keys": [],
                "indexes": [],
            }
        ],
    }
    after = json.loads(json.dumps(before))
    after["signature_sha256"] = "b" * 64
    after["tables"][0]["columns"][0]["not_null"] = True

    report = compare_schema_inventories(before, after)
    changed = report["tables"]["changed"][0]["columns"]["changed"]
    assert len(changed) == 1
    assert changed[0]["name"] == "text"
    assert changed[0]["before"]["not_null"] is False
    assert changed[0]["after"]["not_null"] is True


def test_loader_rejects_duplicate_table_names(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "inventory_version": "1",
                "signature_sha256": "a" * 64,
                "sqlite": {},
                "tables": [
                    {"name": "message", "columns": [], "foreign_keys": [], "indexes": []},
                    {"name": "message", "columns": [], "foreign_keys": [], "indexes": []},
                ],
            }
        ),
        encoding="utf-8",
    )

    try:
        load_schema_inventory(path)
    except ValueError as exc:
        assert "duplicate table names" in str(exc)
    else:
        raise AssertionError("Duplicate schema table names were accepted")


def test_schema_diff_cli_fail_on_change_exit_code(tmp_path: Path) -> None:
    before_db = tmp_path / "before.sqlite"
    after_db = tmp_path / "after.sqlite"
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    _make_before(before_db)
    _make_after(after_db)
    _write_inventory(before_db, before_path)
    _write_inventory(after_db, after_path)

    stdout = io.StringIO()
    with (
        patch.object(
            sys,
            "argv",
            [
                "az-import",
                "schema-diff",
                "--before",
                str(before_path),
                "--after",
                str(after_path),
                "--fail-on-change",
            ],
        ),
        redirect_stdout(stdout),
    ):
        exit_code = import_cli_main()

    assert exit_code == 2
    report = json.loads(stdout.getvalue())
    assert report["changed"] is True
