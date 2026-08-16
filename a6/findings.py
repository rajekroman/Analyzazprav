from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from .a4_integrity import require_reconciled
from .data import DataSourceError, _connect_read_only, _objects, normalize_frame


FINDING_COLUMNS = [
    "finding_id",
    "conversation_id",
    "finding_type",
    "label",
    "start_timestamp",
    "end_timestamp",
    "score",
    "evidence_message_ids",
    "details",
]


def empty_findings() -> pd.DataFrame:
    return pd.DataFrame(columns=FINDING_COLUMNS)


def _parse_message_ids(value: object, source: str) -> tuple[str, ...]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ()
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataSourceError(f"Neplatná evidence v {source}: source_message_ids_json není validní JSON.") from exc
    if not isinstance(parsed, list):
        raise DataSourceError(f"Neplatná evidence v {source}: source_message_ids_json musí být pole.")
    ids = tuple(str(item) for item in parsed)
    if len(set(ids)) != len(ids):
        raise DataSourceError(f"Neplatná evidence v {source}: source_message_ids_json obsahuje duplicitní ID.")
    return ids


def _as_utc(value: object) -> pd.Timestamp:
    if value is None or pd.isna(value):
        return pd.NaT
    return pd.to_datetime(value, errors="coerce", utc=True)


def _us_as_utc(value: object) -> pd.Timestamp:
    if value is None or pd.isna(value):
        return pd.NaT
    return pd.to_datetime(value, unit="us", errors="coerce", utc=True)


def load_a4_findings(path: str | Path) -> pd.DataFrame:
    """Load auditable, reconciled A4 latest-run findings read-only.

    Missing A4 views mean analytics are not available yet. Malformed or
    duplicate evidence fails closed. Once A4 publishes
    ``analysis_a4_reconciliation``, every conversation represented by a loaded
    finding must have ``reconciliation_ok=1``.
    """

    rows: list[dict[str, object]] = []
    try:
        with _connect_read_only(path) as conn:
            objects = set(_objects(conn))

            if "analysis_a4_events" in objects:
                events = pd.read_sql_query(
                    "SELECT id, conversation_id, event_type, score, start_at_utc_us, "
                    "end_at_utc_us, factors_json, source_message_ids_json "
                    "FROM analysis_a4_events",
                    conn,
                )
                for row in events.itertuples(index=False):
                    rows.append({
                        "finding_id": f"event:{row.id}",
                        "conversation_id": str(row.conversation_id),
                        "finding_type": "event",
                        "label": str(row.event_type),
                        "start_timestamp": _us_as_utc(row.start_at_utc_us),
                        "end_timestamp": _us_as_utc(row.end_at_utc_us),
                        "score": float(row.score),
                        "evidence_message_ids": _parse_message_ids(row.source_message_ids_json, "analysis_a4_events"),
                        "details": str(row.factors_json or "{}"),
                    })

            if "analysis_a4_changes" in objects:
                changes = pd.read_sql_query(
                    "SELECT id, conversation_id, participant_id, metric, period_date, value, "
                    "baseline_median, robust_z_score, direction, source_message_ids_json "
                    "FROM analysis_a4_changes",
                    conn,
                )
                for row in changes.itertuples(index=False):
                    when = _as_utc(row.period_date)
                    rows.append({
                        "finding_id": f"change:{row.id}",
                        "conversation_id": str(row.conversation_id),
                        "finding_type": "change_point",
                        "label": f"{row.metric} · {row.direction}",
                        "start_timestamp": when,
                        "end_timestamp": when,
                        "score": abs(float(row.robust_z_score)),
                        "evidence_message_ids": _parse_message_ids(row.source_message_ids_json, "analysis_a4_changes"),
                        "details": json.dumps({
                            "participant_id": row.participant_id,
                            "metric": row.metric,
                            "value": row.value,
                            "baseline_median": row.baseline_median,
                            "robust_z_score": row.robust_z_score,
                            "direction": row.direction,
                        }, ensure_ascii=False, sort_keys=True),
                    })

            if "analysis_a4_regimes" in objects:
                regimes = pd.read_sql_query(
                    "SELECT conversation_id, period_start, period_end, participant_a_id, "
                    "participant_a_direction, participant_a_score, participant_b_id, "
                    "participant_b_direction, participant_b_score, regime_type, "
                    "source_message_ids_json FROM analysis_a4_regimes",
                    conn,
                )
                for row in regimes.itertuples(index=False):
                    if row.regime_type == "stable_or_mixed":
                        continue
                    rows.append({
                        "finding_id": f"regime:{row.conversation_id}:{row.period_start}",
                        "conversation_id": str(row.conversation_id),
                        "finding_type": "regime",
                        "label": str(row.regime_type),
                        "start_timestamp": _as_utc(row.period_start),
                        "end_timestamp": _as_utc(row.period_end),
                        "score": max(abs(float(row.participant_a_score)), abs(float(row.participant_b_score))),
                        "evidence_message_ids": _parse_message_ids(row.source_message_ids_json, "analysis_a4_regimes"),
                        "details": json.dumps({
                            "participant_a_id": row.participant_a_id,
                            "participant_a_direction": row.participant_a_direction,
                            "participant_a_score": row.participant_a_score,
                            "participant_b_id": row.participant_b_id,
                            "participant_b_direction": row.participant_b_direction,
                            "participant_b_score": row.participant_b_score,
                            "regime_type": row.regime_type,
                        }, ensure_ascii=False, sort_keys=True),
                    })

            if rows:
                require_reconciled(
                    conn,
                    [str(row["conversation_id"]) for row in rows],
                    context="nálezy",
                )
    except (DataSourceError, ValueError):
        raise
    except Exception as exc:
        raise DataSourceError(f"Chyba při čtení A4 analytických views: {exc}") from exc

    if not rows:
        return empty_findings()
    frame = pd.DataFrame(rows, columns=FINDING_COLUMNS)
    frame = frame.sort_values(["start_timestamp", "finding_id"], ascending=[False, True], na_position="last")
    return frame.reset_index(drop=True)


def filter_findings(
    findings: pd.DataFrame,
    conversation_ids: Iterable[str],
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    if findings.empty:
        return empty_findings()
    result = findings.copy()
    allowed = {str(value) for value in conversation_ids}
    result = result[result["conversation_id"].astype(str).isin(allowed)]

    if start is not None:
        start_utc = _as_utc(start)
        result = result[result["end_timestamp"].notna() & (result["end_timestamp"] >= start_utc)]
    if end is not None:
        end_utc = _as_utc(end)
        result = result[result["start_timestamp"].notna() & (result["start_timestamp"] <= end_utc)]
    return result.reset_index(drop=True)


def resolve_evidence(messages: pd.DataFrame, evidence_message_ids: Iterable[str]) -> tuple[pd.DataFrame, tuple[str, ...]]:
    canonical = normalize_frame(messages)
    requested = tuple(dict.fromkeys(str(value) for value in evidence_message_ids))
    if not requested:
        return canonical.iloc[0:0], ()
    available = set(canonical["message_id"])
    missing = tuple(message_id for message_id in requested if message_id not in available)
    evidence = canonical[canonical["message_id"].isin(requested)].copy()
    return evidence.reset_index(drop=True), missing
