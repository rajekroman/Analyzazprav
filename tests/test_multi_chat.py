import sqlite3
import tempfile
import unittest
from pathlib import Path

from analyza_zprav.db import connect, initialize
from analyza_zprav.importers.imessage import import_chat_db
from analyza_zprav.qa import verify


class MultiChatTests(unittest.TestCase):
    def test_all_source_chat_relations_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'chat.db'
            src = sqlite3.connect(source)
            src.executescript('''
                CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
                CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, display_name TEXT, service_name TEXT);
                CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, handle_id INTEGER, date INTEGER, is_from_me INTEGER, service TEXT);
                CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            ''')
            src.execute("INSERT INTO handle VALUES(1,'x')")
            src.execute("INSERT INTO chat VALUES(1,'c1','One','iMessage')")
            src.execute("INSERT INTO chat VALUES(2,'c2','Two','iMessage')")
            src.execute("INSERT INTO message VALUES(1,'m1','x',1,700000000000000000,0,'iMessage')")
            src.execute("INSERT INTO chat_message_join VALUES(1,1)")
            src.execute("INSERT INTO chat_message_join VALUES(2,1)")
            src.commit(); src.close()

            dst = connect(Path(td) / 'norm.sqlite3')
            initialize(dst)
            result = import_chat_db(source, dst)
            self.assertEqual(result.source_message_count, 1)
            self.assertEqual(result.imported_message_count, 1)
            self.assertEqual(dst.execute("SELECT COUNT(*) FROM message_conversations").fetchone()[0], 2)
            self.assertEqual(dst.execute("SELECT COUNT(*) FROM message_conversations WHERE is_primary=1").fetchone()[0], 1)
            self.assertTrue(verify(dst).ok)
            dst.close()


if __name__ == '__main__':
    unittest.main()
