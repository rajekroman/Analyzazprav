import sqlite3
import tempfile
import unittest
from pathlib import Path

from analyza_zprav.db import connect, initialize
from analyza_zprav.qa import verify


class DbTests(unittest.TestCase):
    def test_initialization_and_empty_verification(self):
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "db.sqlite3")
            initialize(conn)
            version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
            self.assertEqual(version, "2")
            report = verify(conn)
            self.assertTrue(report.ok)
            self.assertEqual(report.total_messages, 0)
            conn.close()


if __name__ == "__main__":
    unittest.main()
