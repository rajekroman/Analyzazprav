import sqlite3
import tempfile
import unittest
from pathlib import Path

from analyza_zprav.db import connect, initialize
from analyza_zprav.importers.imessage import import_chat_db
from analyza_zprav.processing import materialize_message_features
from analyza_zprav.qa import verify
from analyza_zprav.stats import conversation_metrics


def make_chat_db(path: Path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, display_name TEXT, service_name TEXT);
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT,
            text TEXT,
            handle_id INTEGER,
            date INTEGER,
            is_from_me INTEGER,
            service TEXT,
            item_type INTEGER,
            associated_message_guid TEXT,
            cache_has_attachments INTEGER
        );
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE attachment (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT,
            filename TEXT,
            mime_type TEXT,
            transfer_name TEXT,
            total_bytes INTEGER,
            is_sticker INTEGER
        );
        CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
        """
    )
    conn.execute("INSERT INTO handle VALUES(1, '+420123456789')")
    conn.execute("INSERT INTO chat VALUES(1, 'iMessage;-;+420123456789', 'Test chat', 'iMessage')")
    conn.execute("INSERT INTO message VALUES(1, 'm1', 'Ahoj', 1, 700000000000000000, 0, 'iMessage', 0, NULL, 0)")
    conn.execute("INSERT INTO message VALUES(2, 'm2', 'Nazdar', NULL, 700000060000000000, 1, 'iMessage', 0, NULL, 1)")
    conn.execute("INSERT INTO message VALUES(3, 'm3', 'Jak se mas?', 1, 700000120000000000, 0, 'iMessage', 0, NULL, 0)")
    # A source row without any chat join must still be retained.
    conn.execute("INSERT INTO message VALUES(4, 'm4', 'System-like orphan', NULL, 700000180000000000, 1, 'iMessage', 0, NULL, 0)")
    conn.executemany("INSERT INTO chat_message_join VALUES(1, ?)", [(1,), (2,), (3,)])
    conn.execute("INSERT INTO attachment VALUES(1, 'a1', '~/Library/Messages/Attachments/a.jpg', 'image/jpeg', 'a.jpg', 1234, 0)")
    conn.execute("INSERT INTO message_attachment_join VALUES(2, 1)")
    conn.commit()
    conn.close()


class IMessageImportTests(unittest.TestCase):
    def test_import_is_lossless_idempotent_and_processes_features(self):
        with tempfile.TemporaryDirectory() as td:
            chat = Path(td) / "chat.db"
            make_chat_db(chat)
            dst = connect(Path(td) / "normalized.sqlite3")
            initialize(dst)

            first = import_chat_db(chat, dst)
            self.assertEqual(first.source_message_count, 4)
            self.assertEqual(first.imported_message_count, 4)
            self.assertEqual(first.duplicate_message_count, 0)
            self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 4)
            self.assertEqual(dst.execute("SELECT COUNT(*) FROM message_conversations").fetchone()[0], 4)
            self.assertEqual(dst.execute("SELECT COUNT(*) FROM message_attachments").fetchone()[0], 1)
            self.assertTrue(verify(dst).ok)

            written = materialize_message_features(dst)
            self.assertEqual(written, 4)
            test_chat_id = dst.execute("SELECT id FROM conversations WHERE display_name='Test chat'").fetchone()[0]
            metrics = conversation_metrics(dst, test_chat_id)
            self.assertEqual(metrics["messages"], 3)
            self.assertEqual(metrics["sent_by_me"], 1)
            self.assertEqual(metrics["sent_to_me"], 2)
            self.assertAlmostEqual(metrics["my_avg_response_seconds"], 60.0, places=3)
            self.assertAlmostEqual(metrics["their_avg_response_seconds"], 60.0, places=3)

            second = import_chat_db(chat, dst)
            self.assertEqual(second.imported_message_count, 0)
            self.assertEqual(second.duplicate_message_count, 4)
            self.assertEqual(dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 4)
            self.assertTrue(verify(dst).ok)
            dst.close()


if __name__ == "__main__":
    unittest.main()
