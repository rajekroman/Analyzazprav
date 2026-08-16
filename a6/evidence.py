from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

PASS = "PASS"
STALE = "STALE"
FAIL = "FAIL"
UNVERIFIED = "UNVERIFIED"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _set(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted({text for value in values if (text := _text(value)) is not None}))


@dataclass(frozen=True)
class EvidenceMismatch:
    code: str
    message_id: str
    detail: str
    severity: str


@dataclass(frozen=True)
class EvidenceReconciliation:
    status: str
    checked_message_ids: tuple[str, ...]
    mismatches: tuple[EvidenceMismatch, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PacketProvenanceError(RuntimeError):
    pass


def _connect_read_only(database: str | Path) -> sqlite3.Connection:
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise PacketProvenanceError(f"Database does not exist: {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise PacketProvenanceError(f"Cannot open A2 database read-only: {exc}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _objects(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
    }


def _columns(conn: sqlite3.Connection, name: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({name})")}
    except sqlite3.Error:
        return set()


def load_current_message_provenance(
    database: str | Path,
    message_ids: Iterable[str],
) -> dict[str, list[dict[str, str | None]]]:
    """Read exact A2 source provenance for canonical message IDs, read-only.

    The published ``analysis_message_sources`` view remains the relationship
    authority. ``import_run`` is joined only by the view's own ``import_run_id``
    to carry parser_version, which is not yet projected by the view itself.
    """

    ids = tuple(dict.fromkeys(str(value) for value in message_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    try:
        with _connect_read_only(database) as conn:
            objects = _objects(conn)
            if "analysis_message_sources" not in objects:
                raise PacketProvenanceError(
                    "A2 production source provenance view analysis_message_sources is missing."
                )
            columns = _columns(conn, "analysis_message_sources")
            required = {"message_id", "source_record_key", "source_snapshot_key", "import_run_id"}
            if not required.issubset(columns):
                missing = sorted(required - columns)
                raise PacketProvenanceError(
                    "analysis_message_sources lacks required columns: " + ", ".join(missing)
                )
            parser_expr = "ir.parser_version" if "import_run" in objects and "parser_version" in _columns(conn, "import_run") else "NULL"
            join = "LEFT JOIN import_run ir ON ir.id=ams.import_run_id" if "import_run" in objects else ""
            rows = conn.execute(
                f"""
                SELECT
                    CAST(ams.message_id AS TEXT) AS message_id,
                    ams.source_record_key,
                    ams.source_snapshot_key,
                    {parser_expr} AS parser_version
                FROM analysis_message_sources ams
                {join}
                WHERE CAST(ams.message_id AS TEXT) IN ({placeholders})
                ORDER BY CAST(ams.message_id AS TEXT), ams.import_run_id, ams.source_record_key
                """,
                ids,
            ).fetchall()
    except PacketProvenanceError:
        raise
    except sqlite3.Error as exc:
        raise PacketProvenanceError(f"A2 source provenance query failed: {exc}") from exc

    result: dict[str, list[dict[str, str | None]]] = {}
    for row in rows:
        result.setdefault(str(row["message_id"]), []).append(
            {
                "source_record_key": _text(row["source_record_key"]),
                "source_snapshot_key": _text(row["source_snapshot_key"]),
                "parser_version": _text(row["parser_version"]),
            }
        )
    return result


def enrich_analysis_packet_source_provenance(
    packet: Mapping[str, Any],
    database: str | Path | None,
    *,
    require_provenance: bool | None = None,
) -> dict[str, Any]:
    """Add A5-v4 source provenance to every A6 packet message.

    Production SQLite calls default to fail-closed provenance. Demo/non-SQLite
    packets are explicitly marked unverified instead of pretending provenance.
    """

    result = copy.deepcopy(dict(packet))
    raw_messages = result.get("messages")
    if not isinstance(raw_messages, list):
        raise PacketProvenanceError("A6 packet messages must be an array")
    required = bool(database) if require_provenance is None else bool(require_provenance)
    message_ids: list[str] = []
    for index, item in enumerate(raw_messages):
        if not isinstance(item, Mapping):
            raise PacketProvenanceError(f"A6 packet messages[{index}] must be an object")
        message_id = _text(item.get("message_id"))
        membership_id = _text(item.get("membership_id"))
        if message_id is None:
            raise PacketProvenanceError(f"A6 packet messages[{index}] lacks message_id")
        if membership_id is None:
            raise PacketProvenanceError(f"A6 packet messages[{index}] lacks membership_id")
        message_ids.append(message_id)

    provenance = load_current_message_provenance(database, message_ids) if database is not None else {}
    missing: list[str] = []
    for item in raw_messages:
        message_id = str(item["message_id"])
        rows = provenance.get(message_id, [])
        record_keys = _set(row.get("source_record_key") for row in rows)
        snapshot_keys = _set(row.get("source_snapshot_key") for row in rows)
        parser_versions = _set(row.get("parser_version") for row in rows)
        item["source_record_keys"] = list(record_keys)
        item["source_snapshot_keys"] = list(snapshot_keys)
        item["source_parser_versions"] = list(parser_versions)
        item["source_provenance_status"] = "complete" if record_keys and snapshot_keys else "missing"
        if not record_keys or not snapshot_keys:
            missing.append(message_id)

    result["source_provenance_required"] = required
    result["source_provenance_status"] = "complete" if not missing else "missing"
    result["source_provenance_missing_message_ids"] = sorted(set(missing))
    if required and missing:
        raise PacketProvenanceError(
            "A6 cannot send production A5 context without source provenance for message_id: "
            + ", ".join(sorted(set(missing)))
        )
    return result


def reconcile_a5_evidence_ref(
    evidence_ref: Mapping[str, Any],
    current_message_rows: Sequence[Mapping[str, Any]],
    current_source_rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> EvidenceReconciliation:
    """Compare immutable A5 evidence snapshots to the current A2/A6 read model.

    PASS       exact current match
    STALE      canonical membership exists but source provenance drifted
    FAIL       message/membership identity cannot be reconciled
    UNVERIFIED legacy result has no materialized A5 message snapshots
    """

    claimed_raw = [str(value) for value in evidence_ref.get("message_ids", []) if value is not None]
    claimed_ids = tuple(dict.fromkeys(claimed_raw))
    if "messages" not in evidence_ref:
        return EvidenceReconciliation(
            status=UNVERIFIED,
            checked_message_ids=claimed_ids,
            mismatches=(
                EvidenceMismatch(
                    code="A5_MATERIALIZED_EVIDENCE_MISSING",
                    message_id="",
                    detail="EvidenceRef does not contain A5 v4 materialized message snapshots.",
                    severity="WARNING",
                ),
            ),
        )
    snapshots_raw = evidence_ref.get("messages")
    if not isinstance(snapshots_raw, list):
        return EvidenceReconciliation(
            status=FAIL,
            checked_message_ids=claimed_ids,
            mismatches=(
                EvidenceMismatch(
                    code="A5_MATERIALIZED_EVIDENCE_MALFORMED",
                    message_id="",
                    detail="EvidenceRef.messages exists but is not an array.",
                    severity="ERROR",
                ),
            ),
        )

    rows_by_message: dict[str, list[Mapping[str, Any]]] = {}
    for row in current_message_rows:
        message_id = _text(row.get("message_id"))
        if message_id is not None:
            rows_by_message.setdefault(message_id, []).append(row)

    snapshots_by_id: dict[str, list[Mapping[str, Any]]] = {}
    for raw in snapshots_raw:
        if not isinstance(raw, Mapping):
            continue
        message_id = _text(raw.get("message_id"))
        if message_id is not None:
            snapshots_by_id.setdefault(message_id, []).append(raw)

    mismatches: list[EvidenceMismatch] = []
    if len(claimed_raw) != len(set(claimed_raw)):
        mismatches.append(EvidenceMismatch(
            code="A5_EVIDENCE_MESSAGE_ID_DUPLICATE", message_id="",
            detail="EvidenceRef.message_ids contains duplicate canonical message IDs.", severity="ERROR"
        ))
    if set(claimed_ids) != set(snapshots_by_id):
        mismatches.append(EvidenceMismatch(
            code="A5_EVIDENCE_ID_SNAPSHOT_SET_MISMATCH", message_id="",
            detail=f"message_ids={sorted(set(claimed_ids))!r}, snapshots={sorted(snapshots_by_id)!r}", severity="ERROR"
        ))

    for message_id in sorted(set(claimed_ids) | set(snapshots_by_id)):
        snapshots = snapshots_by_id.get(message_id, [])
        if len(snapshots) != 1:
            mismatches.append(EvidenceMismatch(
                code="A5_EVIDENCE_SNAPSHOT_CARDINALITY", message_id=message_id,
                detail=f"Expected exactly one materialized snapshot; found {len(snapshots)}.", severity="ERROR"
            ))
            continue
        snapshot = snapshots[0]
        current_rows = rows_by_message.get(message_id, [])
        if not current_rows:
            mismatches.append(EvidenceMismatch(
                code="A6_CURRENT_MESSAGE_MISSING", message_id=message_id,
                detail="Canonical message is not present in the current conversation read model.", severity="ERROR"
            ))
            continue
        expected_membership = _text(snapshot.get("membership_id"))
        if expected_membership is None:
            mismatches.append(EvidenceMismatch(
                code="A5_EVIDENCE_MEMBERSHIP_MISSING", message_id=message_id,
                detail="A5 snapshot has no membership_id; current membership cannot be proven.", severity="ERROR"
            ))
            continue
        membership_matches = [
            row for row in current_rows if _text(row.get("membership_id")) == expected_membership
        ]
        if len(membership_matches) != 1:
            current_memberships = _set(row.get("membership_id") for row in current_rows)
            mismatches.append(EvidenceMismatch(
                code="A6_MEMBERSHIP_MISMATCH", message_id=message_id,
                detail=f"A5 membership={expected_membership!r}; current memberships={list(current_memberships)!r}; exact matches={len(membership_matches)}.",
                severity="ERROR",
            ))
            continue

        source_rows = list(current_source_rows.get(message_id, ()))
        current_record_keys = _set(row.get("source_record_key") for row in source_rows)
        current_snapshot_keys = _set(row.get("source_snapshot_key") for row in source_rows)
        current_parser_versions = _set(row.get("parser_version") for row in source_rows)

        for field, code, current, required_field in (
            ("source_record_keys", "A6_SOURCE_RECORD_KEYS_DRIFT", current_record_keys, True),
            ("source_snapshot_keys", "A6_SOURCE_SNAPSHOT_KEYS_DRIFT", current_snapshot_keys, True),
            ("source_parser_versions", "A6_SOURCE_PARSER_VERSIONS_DRIFT", current_parser_versions, False),
        ):
            raw_expected = snapshot.get(field, [])
            if raw_expected is None:
                raw_expected = []
            if not isinstance(raw_expected, (list, tuple)):
                mismatches.append(EvidenceMismatch(
                    code="A5_EVIDENCE_PROVENANCE_MALFORMED", message_id=message_id,
                    detail=f"{field} is not a list/tuple in materialized A5 evidence.", severity="ERROR"
                ))
                continue
            expected = _set(raw_expected)
            if required_field and not expected:
                mismatches.append(EvidenceMismatch(
                    code="A5_EVIDENCE_PROVENANCE_MISSING", message_id=message_id,
                    detail=f"{field} is empty in production materialized A5 evidence.", severity="ERROR"
                ))
                continue
            if expected != current:
                mismatches.append(EvidenceMismatch(
                    code=code, message_id=message_id,
                    detail=f"A5={list(expected)!r}; current A2={list(current)!r}.", severity="WARNING"
                ))

    if any(item.severity == "ERROR" for item in mismatches):
        status = FAIL
    elif mismatches:
        status = STALE
    else:
        status = PASS
    return EvidenceReconciliation(
        status=status,
        checked_message_ids=tuple(sorted(set(claimed_ids))),
        mismatches=tuple(mismatches),
    )
