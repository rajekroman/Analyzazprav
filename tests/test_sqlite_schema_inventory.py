import json
import sqlite3
from pathlib import Path

from analiza_zprav_a1.sqlite_schema import inventory_sqlite_schema


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=42")
    conn.execute("PRAGMA application_id=123456")
    conn.executescript(
        """
        CREATE TABLE parent (
          id INTEGER PRIMARY KEY,
          label TEXT NOT NULL DEFAULT 'x'
        );
        CREATE TABLE child (
          id INTEGER PRIMARY KEY,
          parent_id INTEGER,
          body TEXT,
          FOREIGN KEY(parent_id) REFERENCES parent(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX child_body_idx ON child(body);
        """
    )
    conn.execute("INSERT INTO parent(id, label) VALUES(1, 'SECRET-ALPHA')")
    conn.execute("INSERT INTO child(id, parent_id, body) VALUES(1, 1, 'PRIVATE-MESSAGE')")
    conn.commit()
    conn.close()


def test_schema_inventory_contains_structure_but_no_row_values(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    _make_db(path)

    inventory = inventory_sqlite_schema(path)
    encoded = json.dumps(inventory, ensure_ascii=False, sort_keys=True)

    assert inventory["inventory_version"] == "1"
    assert inventory["sqlite"] == {"application_id": 123456, "user_version": 42}
    assert [table["name"] for table in inventory["tables"]] == ["child", "parent"]
    assert len(inventory["signature_sha256"]) == 64
    assert "SECRET-ALPHA" not in encoded
    assert "PRIVATE-MESSAGE" not in encoded

    child = inventory["tables"][0]
    assert [column["name"] for column in child["columns"]] == ["id", "parent_id", "body"]
    assert child["foreign_keys"] == [
        {
            "from": "parent_id",
            "id": 0,
            "match": "NONE",
            "on_delete": "CASCADE",
            "on_update": "NO ACTION",
            "seq": 0,
            "table": "parent",
            "to": "id",
        }
    ]
    assert child["indexes"][0]["name"] == "child_body_idx"
    assert child["indexes"][0]["unique"] is True
    assert child["indexes"][0]["columns"][0]["name"] == "body"


def test_schema_signature_does_not_change_when_only_rows_change(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    _make_db(path)
    first = inventory_sqlite_schema(path)

    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO parent(id, label) VALUES(2, 'DIFFERENT-DATA')")
    conn.execute("UPDATE child SET body='OTHER-PRIVATE-TEXT' WHERE id=1")
    conn.commit()
    conn.close()

    second = inventory_sqlite_schema(path)
    assert second["signature_sha256"] == first["signature_sha256"]
    assert second == first


def test_schema_signature_changes_when_schema_changes(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    _make_db(path)
    first = inventory_sqlite_schema(path)

    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE child ADD COLUMN service TEXT")
    conn.commit()
    conn.close()

    second = inventory_sqlite_schema(path)
    assert second["signature_sha256"] != first["signature_sha256"]
    child = next(table for table in second["tables"] if table["name"] == "child")
    assert child["columns"][-1]["name"] == "service"
