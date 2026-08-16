from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .database import CanonicalDatabase
from .staging import StagingIngestResult
from .staging import ingest_a1_staging_bundle as _base_ingest_a1_staging_bundle


@dataclass(frozen=True)
class SourceTimeObservation:
    source_record_key: str
    local_iso: str | None
    timezone_name: str | None
    offset_min: int | None
    offset_origin: str | None


def _offset_from_local_iso(value: str) -> int:
    text = value.strip()
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp_local: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp_local must include an explicit UTC offset")
    seconds = parsed.utcoffset().total_seconds()
    if seconds % 60:
        raise ValueError("timestamp_local UTC offset must resolve to whole minutes")
    offset_min = int(seconds // 60)
    if not -840 <= offset_min <= 840:
        raise ValueError("timestamp_local UTC offset is outside the supported range")
    return offset_min


def _optional_string(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"A1 {key} must be a string when present")
    value = value.strip()
    return value or None


def _read_time_observations(staging_dir: Path) -> list[SourceTimeObservation]:
    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("A1 manifest must be a JSON object")
    outputs = manifest.get("outputs") or {}
    if not isinstance(outputs, dict):
        raise ValueError("A1 manifest.outputs must be an object")
    messages_path = staging_dir / str(outputs.get("messages") or "messages.jsonl")
    if not messages_path.is_file():
        raise FileNotFoundError(messages_path)

    observations: list[SourceTimeObservation] = []
    with messages_path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid A1 JSONL at {messages_path}:{line_number}: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"A1 record at {messages_path}:{line_number} is not an object")

            source_record_key = str(record.get("source_record_key") or "")
            if not source_record_key:
                continue

            local_iso = _optional_string(record, "timestamp_local")
            timezone_name = _optional_string(record, "timezone_name")
            legacy_timezone_name = _optional_string(record, "timezone")
            if timezone_name and legacy_timezone_name and timezone_name != legacy_timezone_name:
                raise ValueError("A1 timezone and timezone_name disagree")
            timezone_name = timezone_name or legacy_timezone_name

            raw_offset = record.get("timezone_offset_min")
            explicit_offset: int | None = None
            if raw_offset is not None:
                if isinstance(raw_offset, bool):
                    raise ValueError("A1 timezone_offset_min must be an integer")
                try:
                    explicit_offset = int(raw_offset)
                except (TypeError, ValueError) as exc:
                    raise ValueError("A1 timezone_offset_min must be an integer") from exc
                if not -840 <= explicit_offset <= 840:
                    raise ValueError("A1 timezone_offset_min is outside the supported range")

            local_offset = _offset_from_local_iso(local_iso) if local_iso is not None else None
            if explicit_offset is not None and local_offset is not None and explicit_offset != local_offset:
                raise ValueError("A1 timestamp_local offset disagrees with timezone_offset_min")

            offset_min = explicit_offset if explicit_offset is not None else local_offset
            offset_origin = (
                "timezone_offset_min"
                if explicit_offset is not None
                else "timestamp_local"
                if local_offset is not None
                else None
            )
            if local_iso is None and timezone_name is None and offset_min is None:
                continue
            observations.append(
                SourceTimeObservation(
                    source_record_key=source_record_key,
                    local_iso=local_iso,
                    timezone_name=timezone_name,
                    offset_min=offset_min,
                    offset_origin=offset_origin,
                )
            )
    return observations


def _merge_time_observation(
    db: CanonicalDatabase,
    import_run_id: int,
    observation: SourceTimeObservation,
) -> None:
    source_row = db.conn.execute(
        """SELECT id, message_id, metadata_json
           FROM message_source
           WHERE import_run_id=? AND source_record_key=?""",
        (import_run_id, observation.source_record_key),
    ).fetchone()
    if source_row is None:
        raise RuntimeError(
            f"A2 provenance row missing for source_record_key={observation.source_record_key!r}"
        )

    try:
        source_metadata = json.loads(source_row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        source_metadata = {"_invalid_metadata_json": source_row["metadata_json"]}
    source_metadata["a1_time_observation"] = {
        "timestamp_local": observation.local_iso,
        "timezone_name": observation.timezone_name,
        "timezone_offset_min": observation.offset_min,
        "offset_origin": observation.offset_origin,
    }

    message_row = db.conn.execute(
        """SELECT sent_at_local_iso, timezone_name, timezone_offset_min
           FROM message WHERE id=?""",
        (source_row["message_id"],),
    ).fetchone()
    if message_row is None:
        raise RuntimeError(f"Canonical message {source_row['message_id']} disappeared")

    conflicts: dict[str, dict[str, Any]] = {}
    requested = {
        "sent_at_local_iso": observation.local_iso,
        "timezone_name": observation.timezone_name,
        "timezone_offset_min": observation.offset_min,
    }
    assignments: dict[str, Any] = {}
    for column, value in requested.items():
        if value is None:
            continue
        current = message_row[column]
        if current is None:
            assignments[column] = value
        elif current != value:
            conflicts[column] = {"canonical": current, "source": value}
    if conflicts:
        source_metadata["canonical_time_conflict"] = conflicts

    with db.conn:
        if assignments:
            columns = ", ".join(f"{name}=?" for name in assignments)
            db.conn.execute(
                f"UPDATE message SET {columns} WHERE id=?",
                (*assignments.values(), source_row["message_id"]),
            )
        db.conn.execute(
            "UPDATE message_source SET metadata_json=? WHERE id=?",
            (
                json.dumps(
                    source_metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                source_row["id"],
            ),
        )


def ingest_a1_staging_bundle(
    db: CanonicalDatabase,
    staging_dir: str | Path,
) -> StagingIngestResult:
    """Normalize A1 staging and preserve only explicitly supplied local-time facts.

    UTC remains the ordering authority. This wrapper never derives local wall time
    from UTC or from the machine's timezone. `timestamp_local`, `timezone_name`
    (or legacy `timezone`) and `timezone_offset_min` are accepted only when they
    are present in the A1 source record. An offset embedded in `timestamp_local`
    may populate `timezone_offset_min` because it is explicit in that source text.
    """

    root = Path(staging_dir)
    observations = _read_time_observations(root)
    result = _base_ingest_a1_staging_bundle(db, root)
    for observation in observations:
        _merge_time_observation(db, result.import_run_id, observation)
    return result
