import json
import sqlite3
from pathlib import Path

from analiza_zprav_a1.schema_diff import load_schema_inventory
from analiza_zprav_a1.sqlite_schema import inventory_sqlite_schema, write_schema_inventory


def test_schema_diff_loader_rejects_tampered_signed_inventory(tmp_path: Path) -> None:
    db = tmp_path / "source.sqlite"
    path = tmp_path / "schema.json"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE message (id INTEGER PRIMARY KEY, text TEXT)")
    conn.commit()
    conn.close()

    write_schema_inventory(inventory_sqlite_schema(db), path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["tables"][0]["columns"][1]["name"] = "tampered_text"
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    try:
        load_schema_inventory(path)
    except ValueError as exc:
        assert "signature_sha256 does not match" in str(exc)
    else:
        raise AssertionError("Tampered signed schema inventory was accepted")
