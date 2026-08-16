import tempfile
import unittest
from pathlib import Path

from analyza_zprav.db import connect, initialize
from analyza_zprav.qa import verify


class QaTests(unittest.TestCase):
    def test_reconciliation_mismatch_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / 'db.sqlite3')
            initialize(conn)
            conn.execute("INSERT INTO sources(kind,canonical_path) VALUES('test','x')")
            sid = conn.execute("SELECT id FROM sources").fetchone()[0]
            conn.execute(
                "INSERT INTO import_runs(source_id,source_message_count,imported_message_count,duplicate_message_count,status) VALUES(?,?,?,?, 'completed')",
                (sid, 10, 9, 0),
            )
            conn.commit()
            report = verify(conn)
            self.assertFalse(report.ok)
            self.assertEqual(report.import_reconciliation_mismatches, 1)
            conn.close()


if __name__ == '__main__':
    unittest.main()
