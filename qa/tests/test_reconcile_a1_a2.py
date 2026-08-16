from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from qa.reconcile_a1_a2 import STATUS_FAIL, STATUS_PASS, reconcile_a1_a2


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def staging_records() -> tuple[dict, list[dict]]:
    root = FIXTURES / "golden"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (root / "messages.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return manifest, records


def create_bridge_db(path: Path, *, include_key_column: bool = True, omit_last_message: bool = False) -> None:
    manifest, records = staging_records()
    source = manifest["source"]
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        CREATE TABLE import_run(
            id INTEGER PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE TABLE message(id INTEGER PRIMARY KEY)")
    if include_key_column:
        conn.execute(
            """
            CREATE TABLE message_source(
                id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL REFERENCES message(id),
                import_run_id INTEGER NOT NULL REFERENCES import_run(id),
                source_record_key TEXT NOT NULL
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE message_source(
                id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL REFERENCES message(id),
                import_run_id INTEGER NOT NULL REFERENCES import_run(id)
            )
            """
        )
    conn.execute(
        """
        CREATE TABLE attachment_source(
            id INTEGER PRIMARY KEY,
            import_run_id INTEGER NOT NULL REFERENCES import_run(id),
            source_attachment_id TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO import_run(id,source_type,source_fingerprint,status) VALUES (1,?,?, 'completed')",
        (source["type"], source["sha256"]),
    )

    selected = records[:-1] if omit_last_message else records
    for index, record in enumerate(selected, start=1):
        conn.execute("INSERT INTO message(id) VALUES (?)", (index,))
        if include_key_column:
            conn.execute(
                "INSERT INTO message_source(id,message_id,import_run_id,source_record_key) VALUES (?,?,1,?)",
                (index, index, record["source_record_key"]),
            )
        else:
            conn.execute(
                "INSERT INTO message_source(id,message_id,import_run_id) VALUES (?,?,1)",
                (index, index),
            )

    attachment_id = 1
    for record in selected:
        for attachment in record.get("attachments") or []:
            source_attachment_id = attachment.get("source_attachment_id")
            if source_attachment_id not in (None, ""):
                conn.execute(
                    "INSERT INTO attachment_source(id,import_run_id,source_attachment_id) VALUES (?,1,?)",
                    (attachment_id, str(source_attachment_id)),
                )
                attachment_id += 1

    conn.commit()
    conn.close()


class A1A2ReconciliationTests(unittest.TestCase):
    def test_exact_source_sets_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "bridge.sqlite"
            create_bridge_db(db)
            report = reconcile_a1_a2(FIXTURES / "golden", db)
        self.assertEqual(STATUS_PASS, report["status"])
        self.assertEqual(0, report["checks"]["messages_missing_in_a2"])
        self.assertEqual(0, report["checks"]["attachments_missing_in_a2"])
        self.assertEqual(4, report["checks"]["a2_message_source_count"])

    def test_missing_source_message_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "bridge.sqlite"
            create_bridge_db(db, omit_last_message=True)
            report = reconcile_a1_a2(FIXTURES / "golden", db)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertEqual(1, report["checks"]["messages_missing_in_a2"])
        self.assertIn("A1_MESSAGES_MISSING_IN_A2", {i["code"] for i in report["issues"]})

    def test_missing_upstream_key_contract_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "bridge.sqlite"
            create_bridge_db(db, include_key_column=False)
            report = reconcile_a1_a2(FIXTURES / "golden", db)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A2_SOURCE_RECORD_KEY_COLUMN_MISSING", {i["code"] for i in report["issues"]})


if __name__ == "__main__":
    unittest.main()
