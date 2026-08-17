from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.normalization import CanonicalDatabase, legacy_opaque_handle_issues


class A2IdentityNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CanonicalDatabase(Path(self.tmp.name) / "messages.sqlite")
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_opaque_imessage_handles_preserve_punctuation_and_case(self):
        dotted = self.db.get_or_create_participant(
            identity_type="imessage_handle", identity_value=" user.name "
        )
        plain = self.db.get_or_create_participant(
            identity_type="imessage_handle", identity_value="username"
        )
        dashed = self.db.get_or_create_participant(
            identity_type="imessage_handle", identity_value="alpha-beta"
        )
        compact = self.db.get_or_create_participant(
            identity_type="imessage_handle", identity_value="alphabeta"
        )
        upper = self.db.get_or_create_participant(
            identity_type="imessage_handle", identity_value="CaseHandle"
        )
        lower = self.db.get_or_create_participant(
            identity_type="imessage_handle", identity_value="casehandle"
        )

        self.assertNotEqual(dotted, plain)
        self.assertNotEqual(dashed, compact)
        self.assertNotEqual(upper, lower)
        self.assertEqual(
            dotted,
            self.db.get_or_create_participant(
                identity_type="imessage_handle", identity_value="user.name"
            ),
        )

        rows = self.db.conn.execute(
            """SELECT normalized_value
               FROM participant_identity
               WHERE identity_type='imessage_handle'
               ORDER BY normalized_value"""
        ).fetchall()
        self.assertEqual(
            {str(row["normalized_value"]) for row in rows},
            {"user.name", "username", "alpha-beta", "alphabeta", "CaseHandle", "casehandle"},
        )
        self.assertEqual(legacy_opaque_handle_issues(self.db), [])

    def test_phone_and_email_normalization_are_unchanged(self):
        self.assertEqual(
            CanonicalDatabase.normalize_identity("phone", "00 420 123-456.789"),
            "+420123456789",
        )
        self.assertEqual(
            CanonicalDatabase.normalize_identity("email", " User.Name@Example.COM "),
            "user.name@example.com",
        )

    def test_legacy_destructive_opaque_handle_is_reported_without_repair(self):
        with self.db.conn:
            participant_id = int(
                self.db.conn.execute(
                    "INSERT INTO participant(canonical_name, is_self, metadata_json) VALUES (NULL, 0, '{}')"
                ).lastrowid
            )
            identity_id = int(
                self.db.conn.execute(
                    """INSERT INTO participant_identity(
                           participant_id, identity_type, normalized_value, original_value
                       ) VALUES (?, 'imessage_handle', 'legacyhandle', 'legacy.handle')""",
                    (participant_id,),
                ).lastrowid
            )

        issues = legacy_opaque_handle_issues(self.db)
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue["code"], "LEGACY_OPAQUE_HANDLE_NORMALIZATION")
        self.assertEqual(issue["participant_identity_id"], identity_id)
        self.assertEqual(issue["participant_id"], participant_id)
        self.assertEqual(issue["normalized_value"], "legacyhandle")
        self.assertEqual(issue["expected_exact_value"], "legacy.handle")
        self.assertEqual(issue["repair"], "reimport_from_source_provenance")

        stored = self.db.conn.execute(
            "SELECT normalized_value, original_value FROM participant_identity WHERE id=?",
            (identity_id,),
        ).fetchone()
        self.assertEqual(stored["normalized_value"], "legacyhandle")
        self.assertEqual(stored["original_value"], "legacy.handle")


if __name__ == "__main__":
    unittest.main()
