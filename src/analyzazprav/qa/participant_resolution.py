from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from .staging import STATUS_FAIL, STATUS_PASS

_REQUIRED_TABLES = {
    "participant",
    "participant_identity",
    "processing_run",
    "processed_message",
    "sender_run",
    "resolved_participant",
    "resolved_participant_member",
    "participant_alias",
    "participant_resolution_candidate",
    "processed_message_resolved_sender",
    "sender_run_resolved_participant",
}


def _normalized_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    normalized = " ".join(normalized.split()).casefold()
    return normalized or None


def validate_participant_resolution(database: str | Path) -> dict[str, Any]:
    """Independently reconcile the latest A3 participant-resolution sidecars.

    A7 intentionally re-derives the conservative v5 rules from A2 tables instead
    of importing or calling A3 participant-resolution code.
    """

    path = Path(database)
    issues: list[dict[str, str]] = []
    checks: dict[str, Any] = {}

    def fail(code: str, detail: str) -> None:
        issues.append({"severity": "ERROR", "code": code, "detail": detail})

    if not path.is_file():
        fail("DATABASE_MISSING", f"SQLite database not found: {path}")
        return _finish(path, checks, issues)

    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(_REQUIRED_TABLES - tables)
        checks["missing_tables"] = missing
        if missing:
            fail("A3_PARTICIPANT_RESOLUTION_TABLES_MISSING", ", ".join(missing))
            return _finish(path, checks, issues)

        run = conn.execute(
            "SELECT MAX(id) FROM processing_run WHERE status='completed'"
        ).fetchone()[0]
        if run is None:
            fail("A3_COMPLETED_RUN_MISSING", "No completed A3 processing run exists.")
            return _finish(path, checks, issues)
        run_id = int(run)
        checks["processing_run_id"] = run_id

        participant_rows = list(
            conn.execute(
                "SELECT id, canonical_name, is_self FROM participant ORDER BY id"
            )
        )
        participants = {
            int(row["id"]): {
                "canonical_name": row["canonical_name"],
                "is_self": bool(row["is_self"]),
            }
            for row in participant_rows
        }
        self_ids = tuple(sorted(pid for pid, row in participants.items() if row["is_self"]))
        self_resolved_id = self_ids[0] if self_ids else None
        expected_map = {
            pid: self_resolved_id if row["is_self"] and self_resolved_id is not None else pid
            for pid, row in participants.items()
        }
        checks["a2_participants"] = len(participants)
        checks["a2_self_participants"] = len(self_ids)

        members = list(
            conn.execute(
                """SELECT resolved_participant_id, participant_id, method, confidence
                   FROM resolved_participant_member
                   WHERE processing_run_id=?
                   ORDER BY participant_id""",
                (run_id,),
            )
        )
        actual_member_map: dict[int, list[int]] = {}
        for row in members:
            actual_member_map.setdefault(int(row["participant_id"]), []).append(
                int(row["resolved_participant_id"])
            )
        checks["resolved_member_rows"] = len(members)
        missing_members = sorted(set(participants) - set(actual_member_map))
        extra_members = sorted(set(actual_member_map) - set(participants))
        multi_members = sorted(pid for pid, values in actual_member_map.items() if len(values) != 1)
        if missing_members:
            fail("A3_PARTICIPANT_MEMBER_MISSING", f"Missing A2 participant IDs: {missing_members[:10]}")
        if extra_members:
            fail("A3_PARTICIPANT_MEMBER_EXTRA", f"Unknown participant IDs: {extra_members[:10]}")
        if multi_members:
            fail("A3_PARTICIPANT_MEMBER_DUPLICATE", f"Participants mapped more than once: {multi_members[:10]}")
        mapping_mismatches = [
            pid
            for pid, expected in expected_map.items()
            if actual_member_map.get(pid, [None])[0] != expected
        ]
        checks["participant_mapping_mismatches"] = len(mapping_mismatches)
        if mapping_mismatches:
            fail(
                "A3_PARTICIPANT_MAPPING_MISMATCH",
                f"Resolved mapping disagrees with conservative A2-derived rule for {mapping_mismatches[:10]}",
            )

        resolved_rows = {
            int(row["id"]): row
            for row in conn.execute(
                """SELECT id, canonical_name, is_self, method, confidence
                   FROM resolved_participant WHERE processing_run_id=?""",
                (run_id,),
            )
        }
        expected_resolved_ids = set(expected_map.values())
        checks["resolved_participants"] = len(resolved_rows)
        if set(resolved_rows) != expected_resolved_ids:
            fail(
                "A3_RESOLVED_PARTICIPANT_SET_MISMATCH",
                f"actual={sorted(resolved_rows)}, expected={sorted(expected_resolved_ids)}",
            )

        groups: dict[int, list[int]] = {}
        for pid, resolved_id in expected_map.items():
            groups.setdefault(resolved_id, []).append(pid)
        for resolved_id, member_ids in sorted(groups.items()):
            row = resolved_rows.get(resolved_id)
            if row is None:
                continue
            member_ids.sort()
            expected_self = any(participants[pid]["is_self"] for pid in member_ids)
            names = [
                str(participants[pid]["canonical_name"])
                for pid in member_ids
                if participants[pid]["canonical_name"] is not None
                and str(participants[pid]["canonical_name"]).strip()
            ]
            expected_name = names[0] if names else None
            expected_method = (
                "explicit_is_self_union_v1"
                if expected_self and len(member_ids) > 1
                else "a2_participant_membership_v1"
            )
            if bool(row["is_self"]) != expected_self:
                fail("A3_RESOLVED_SELF_MISMATCH", f"resolved {resolved_id} has wrong is_self")
            if row["canonical_name"] != expected_name:
                fail("A3_RESOLVED_NAME_MISMATCH", f"resolved {resolved_id} has wrong canonical_name")
            if row["method"] != expected_method or float(row["confidence"]) != 1.0:
                fail("A3_RESOLUTION_METHOD_MISMATCH", f"resolved {resolved_id} has wrong method/confidence")

        identity_rows = list(
            conn.execute(
                """SELECT id, participant_id, identity_type, normalized_value, original_value
                   FROM participant_identity ORDER BY id"""
            )
        )
        aliases = {
            int(row["participant_identity_id"]): row
            for row in conn.execute(
                """SELECT participant_identity_id, resolved_participant_id, participant_id,
                          identity_type, normalized_value, original_value, method, confidence
                   FROM participant_alias WHERE processing_run_id=?""",
                (run_id,),
            )
        }
        checks["a2_participant_identities"] = len(identity_rows)
        checks["participant_aliases"] = len(aliases)
        if set(aliases) != {int(row["id"]) for row in identity_rows}:
            fail("A3_ALIAS_SET_MISMATCH", "A3 aliases do not exactly cover A2 participant_identity rows.")
        for identity in identity_rows:
            identity_id = int(identity["id"])
            alias = aliases.get(identity_id)
            if alias is None:
                continue
            participant_id = int(identity["participant_id"])
            resolved_id = expected_map[participant_id]
            group_size = len(groups[resolved_id])
            expected_method = (
                "explicit_is_self_alias_v1"
                if participants[participant_id]["is_self"] and group_size > 1
                else "a2_identity_membership_v1"
            )
            expected_values = (
                resolved_id,
                participant_id,
                identity["identity_type"],
                identity["normalized_value"],
                identity["original_value"],
                expected_method,
                1.0,
            )
            actual_values = (
                int(alias["resolved_participant_id"]),
                int(alias["participant_id"]),
                alias["identity_type"],
                alias["normalized_value"],
                alias["original_value"],
                alias["method"],
                float(alias["confidence"]),
            )
            if actual_values != expected_values:
                fail("A3_ALIAS_PROVENANCE_MISMATCH", f"participant_identity {identity_id} alias differs from A2")

        name_groups: dict[str, list[int]] = {}
        for pid, participant in participants.items():
            normalized = _normalized_name(participant["canonical_name"])
            if normalized is not None:
                name_groups.setdefault(normalized, []).append(pid)
        expected_candidates: set[tuple[int, int, str, float, str]] = set()
        for name in sorted(name_groups):
            ids = sorted(name_groups[name])
            for left, right in zip(ids, ids[1:]):
                if expected_map[left] == expected_map[right]:
                    continue
                a, b = sorted((left, right))
                expected_candidates.add(
                    (a, b, "same_normalized_canonical_name", 0.35, "normalized_canonical_name_candidate_v1")
                )
        actual_candidates = {
            (
                int(row["participant_id_a"]),
                int(row["participant_id_b"]),
                str(row["reason"]),
                float(row["confidence"]),
                str(row["method"]),
            )
            for row in conn.execute(
                """SELECT participant_id_a, participant_id_b, reason, confidence, method
                   FROM participant_resolution_candidate WHERE processing_run_id=?""",
                (run_id,),
            )
        }
        checks["participant_candidates"] = len(actual_candidates)
        if actual_candidates != expected_candidates:
            fail("A3_PARTICIPANT_CANDIDATE_MISMATCH", "Equal-name candidate set differs from A2-derived oracle.")

        expected_message_sender = {
            int(row["membership_id"]): expected_map[int(row["sender_id"])]
            for row in conn.execute(
                """SELECT pm.membership_id, m.sender_id
                   FROM processed_message pm
                   JOIN message m ON m.id=pm.message_id
                   WHERE pm.processing_run_id=? AND m.sender_id IS NOT NULL""",
                (run_id,),
            )
        }
        actual_message_sender = {
            int(row["membership_id"]): int(row["resolved_participant_id"])
            for row in conn.execute(
                """SELECT membership_id, resolved_participant_id
                   FROM processed_message_resolved_sender WHERE processing_run_id=?""",
                (run_id,),
            )
        }
        checks["resolved_sender_rows"] = len(actual_message_sender)
        if actual_message_sender != expected_message_sender:
            fail("A3_RESOLVED_MESSAGE_SENDER_MISMATCH", "Processed-message resolved sender mapping differs from A2 sender identities.")

        expected_run_sender = {
            int(row["id"]): expected_map[int(row["sender_id"])]
            for row in conn.execute(
                "SELECT id, sender_id FROM sender_run WHERE processing_run_id=? AND sender_id IS NOT NULL",
                (run_id,),
            )
        }
        actual_run_sender = {
            int(row["sender_run_id"]): int(row["resolved_participant_id"])
            for row in conn.execute(
                """SELECT sender_run_id, resolved_participant_id
                   FROM sender_run_resolved_participant WHERE processing_run_id=?""",
                (run_id,),
            )
        }
        checks["resolved_sender_run_rows"] = len(actual_run_sender)
        if actual_run_sender != expected_run_sender:
            fail("A3_RESOLVED_SENDER_RUN_MISMATCH", "Sender-run resolved participant mapping differs from A2-derived oracle.")

        fk_errors = list(conn.execute("PRAGMA foreign_key_check"))
        checks["foreign_key_errors"] = len(fk_errors)
        if fk_errors:
            fail("SQLITE_FOREIGN_KEY_ERRORS", f"{len(fk_errors)} foreign-key violation(s)")
    except sqlite3.Error as exc:
        fail("A7_PARTICIPANT_VALIDATION_QUERY_FAILED", str(exc))
    finally:
        conn.close()

    return _finish(path, checks, issues)


def _finish(path: Path, checks: dict[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": STATUS_FAIL if issues else STATUS_PASS,
        "database": str(path),
        "checks": checks,
        "counts": {"errors": len(issues)},
        "issues": issues,
    }
