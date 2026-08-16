from __future__ import annotations

from typing import Any

from .database import CanonicalDatabase


def legacy_opaque_handle_issues(db: CanonicalDatabase) -> list[dict[str, Any]]:
    """Return legacy opaque-handle identities created by destructive normalization.

    Older A2 builds applied phone-style punctuation stripping to
    ``imessage_handle``. Such rows cannot be safely auto-split because another
    source identity may already have been merged into the same canonical
    participant. The audit is intentionally read-only: rebuild/re-import from
    preserved source provenance is the safe recovery path.
    """

    rows = db.conn.execute(
        """SELECT id, participant_id, normalized_value, original_value
           FROM participant_identity
           WHERE identity_type='imessage_handle'
           ORDER BY id"""
    ).fetchall()

    issues: list[dict[str, Any]] = []
    for row in rows:
        original = "" if row["original_value"] is None else str(row["original_value"])
        expected = original.strip()
        actual = "" if row["normalized_value"] is None else str(row["normalized_value"])
        if actual == expected:
            continue
        issues.append(
            {
                "code": "LEGACY_OPAQUE_HANDLE_NORMALIZATION",
                "participant_identity_id": int(row["id"]),
                "participant_id": int(row["participant_id"]),
                "normalized_value": actual,
                "expected_exact_value": expected,
                "original_value": original,
                "repair": "reimport_from_source_provenance",
            }
        )
    return issues
