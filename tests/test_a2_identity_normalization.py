from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase


class A2IdentityNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CanonicalDatabase(Path(self.tmp.name) / "messages.sqlite")
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_opaque_imessage_handles_preserve_punctuation(self):
        dotted = self.db.get_or_create_participant(
            identity_type="imessage_handle",
            identity_value="  user.name  ",
        )
        plain = self.db.get_or_create_participant(
            identity_type="imessage_handle",
            identity_value="username",
        )
        dashed = self.db.get_or_create_participant(
            identity_type="imessage_handle",
            identity_value="abc-def",
        )
        compact = self.db.get_or_create_participant(
            identity_type="imessage_handle",
            identity_value="abcdef",
        )

        self.assertNotEqual(dotted, plain)
        self.assertNotEqual(dashed, compact)
        self.assertEqual(
            self.db.conn.execute(
                """SELECT normalized_value FROM participant_identity
                   WHERE participant_id=?""",
                (dotted,),
            ).fetchone()[0],
            "user.name",
        )
        self.assertEqual(
            self.db.conn.execute(
                """SELECT normalized_value FROM participant_identity
                   WHERE participant_id=?""",
                (dashed,),
            ).fetchone()[0],
            "abc-def",
        )

    def test_phone_formatting_and_email_case_normalization_remain_stable(self):
        formatted_phone = self.db.get_or_create_participant(
            identity_type="phone",
            identity_value="+420 777-123-456",
        )
        compact_phone = self.db.get_or_create_participant(
            identity_type="phone",
            identity_value="+420777123456",
        )
        international_prefix = self.db.get_or_create_participant(
            identity_type="phone",
            identity_value="00420 777 123 456",
        )
        upper_email = self.db.get_or_create_participant(
            identity_type="email",
            identity_value="USER@example.com",
        )
        lower_email = self.db.get_or_create_participant(
            identity_type="email",
            identity_value="user@example.com",
        )

        self.assertEqual(formatted_phone, compact_phone)
        self.assertEqual(formatted_phone, international_prefix)
        self.assertEqual(upper_email, lower_email)
        self.assertEqual(
            self.db.conn.execute(
                "SELECT normalized_value FROM participant_identity WHERE participant_id=?",
                (formatted_phone,),
            ).fetchone()[0],
            "+420777123456",
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT normalized_value FROM participant_identity WHERE participant_id=?",
                (upper_email,),
            ).fetchone()[0],
            "user@example.com",
        )

        report = self.db.integrity_report()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], [])


if __name__ == "__main__":
    unittest.main()
