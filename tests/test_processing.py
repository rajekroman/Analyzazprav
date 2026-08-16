import tempfile
import unittest
from pathlib import Path

from analyza_zprav.db import connect, initialize
from analyza_zprav.processing import materialize_message_features


class ProcessingTests(unittest.TestCase):
    def test_session_gap_and_reply_turn(self):
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "db.sqlite3")
            initialize(conn)
            conn.execute("INSERT INTO sources(kind, canonical_path) VALUES('test','x')")
            source_id = conn.execute("SELECT id FROM sources").fetchone()[0]
            conn.execute("INSERT INTO participants(canonical_key,is_me) VALUES('me',1)")
            me = conn.execute("SELECT id FROM participants WHERE canonical_key='me'").fetchone()[0]
            conn.execute("INSERT INTO participants(canonical_key,address,is_me) VALUES('address:x','x',0)")
            other = conn.execute("SELECT id FROM participants WHERE canonical_key='address:x'").fetchone()[0]
            conn.execute("INSERT INTO conversations(source_id,external_id) VALUES(?, 'c')", (source_id,))
            conv = conn.execute("SELECT id FROM conversations").fetchone()[0]
            rows = [
                ('m1', other, '2026-01-01T10:00:00Z', 0, 1),
                ('m2', me, '2026-01-01T10:01:00Z', 1, 2),
                ('m3', other, '2026-01-01T13:30:00Z', 0, 3),
            ]
            for ext, sender, ts, from_me, rowid in rows:
                conn.execute(
                    "INSERT INTO messages(source_id,external_id,conversation_id,sender_participant_id,sent_at_utc,is_from_me,raw_rowid) VALUES(?,?,?,?,?,?,?)",
                    (source_id, ext, conv, sender, ts, from_me, rowid),
                )
                mid = conn.execute("SELECT id FROM messages WHERE external_id=?", (ext,)).fetchone()[0]
                conn.execute("INSERT INTO message_conversations(message_id,conversation_id,is_primary) VALUES(?,?,1)", (mid, conv))
            conn.commit()

            self.assertEqual(materialize_message_features(conn), 3)
            f2 = conn.execute("SELECT * FROM message_features WHERE sequence_in_conversation=2").fetchone()
            f3 = conn.execute("SELECT * FROM message_features WHERE sequence_in_conversation=3").fetchone()
            self.assertEqual(f2['response_latency_seconds'], 60.0)
            self.assertEqual(f2['session_index'], 0)
            self.assertEqual(f3['session_index'], 1)
            self.assertEqual(f3['response_latency_seconds'], 12540.0)
            conn.close()


if __name__ == '__main__':
    unittest.main()
