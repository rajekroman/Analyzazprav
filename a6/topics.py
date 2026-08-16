from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .a4_integrity import require_reconciled
from .data import DataSourceError, _connect_read_only, _objects


@dataclass(frozen=True)
class A4Topics:
    topics: pd.DataFrame
    evidence: pd.DataFrame
    periods: pd.DataFrame
    period_reconciliation: pd.DataFrame
    marker_evidence: pd.DataFrame
    marker_summary: pd.DataFrame
    marker_periods: pd.DataFrame
    marker_reconciliation: pd.DataFrame

    @property
    def available(self) -> bool:
        return not self.topics.empty

    @property
    def marker_available(self) -> bool:
        return not self.marker_evidence.empty


def empty_topics() -> A4Topics:
    return A4Topics(
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
    )


def _message_ids(value: object, source: str) -> tuple[str, ...]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ()
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataSourceError(
                f"Neplatná topic evidence v {source}: source_message_ids_json není validní JSON."
            ) from exc
    if not isinstance(parsed, list):
        raise DataSourceError(
            f"Neplatná topic evidence v {source}: source_message_ids_json musí být pole."
        )
    ids = tuple(str(item) for item in parsed)
    if len(ids) != len(set(ids)):
        raise DataSourceError(
            f"Neplatná topic evidence v {source}: source_message_ids_json obsahuje duplicitní ID."
        )
    return ids


def _read_optional(conn, objects: set[str], view: str, params: tuple[str]) -> pd.DataFrame:
    if view not in objects:
        return pd.DataFrame()
    return pd.read_sql_query(
        f"SELECT * FROM {view} WHERE CAST(conversation_id AS TEXT) = ?",
        conn,
        params=params,
    )


def _validate_marker_evidence(
    evidence: pd.DataFrame,
    marker_evidence: pd.DataFrame,
    marker_reconciliation: pd.DataFrame,
) -> None:
    if marker_evidence.empty:
        if not marker_reconciliation.empty:
            if len(marker_reconciliation) != 1:
                raise DataSourceError(
                    "A4 topic-marker reconciliation musí mít právě jeden řádek pro konverzaci."
                )
            recon = marker_reconciliation.iloc[0]
            if int(recon.get("marker_evidence_row_count", 0)) != 0:
                raise DataSourceError(
                    "A4 topic-marker reconciliation deklaruje marker evidence, ale view je prázdné."
                )
        return

    required = {"topic_key", "message_id", "affection_hit_count", "negative_hit_count"}
    missing_columns = sorted(required - set(marker_evidence.columns))
    if missing_columns:
        raise DataSourceError(
            "A4 topic-marker evidence postrádá sloupce: " + ", ".join(missing_columns)
        )

    duplicate_marker = marker_evidence.duplicated(
        subset=["topic_key", "message_id"], keep=False
    )
    if duplicate_marker.any():
        pairs = marker_evidence.loc[
            duplicate_marker, ["topic_key", "message_id"]
        ].drop_duplicates()
        rendered = [
            f"{row.topic_key}/{row.message_id}" for row in pairs.itertuples(index=False)
        ]
        raise DataSourceError(
            "A4 topic-marker evidence obsahuje duplicitní topic/message řádky: "
            + ", ".join(rendered)
        )

    evidence_pairs = set(zip(evidence["topic_key"], evidence["message_id"], strict=False))
    marker_pairs = set(
        zip(marker_evidence["topic_key"], marker_evidence["message_id"], strict=False)
    )
    orphan_pairs = sorted(marker_pairs - evidence_pairs)
    if orphan_pairs:
        rendered = [f"{topic_key}/{message_id}" for topic_key, message_id in orphan_pairs]
        raise DataSourceError(
            "A4 topic-marker evidence nemá parent v exact topic evidence: "
            + ", ".join(rendered)
        )

    affection = pd.to_numeric(marker_evidence["affection_hit_count"], errors="coerce")
    negative = pd.to_numeric(marker_evidence["negative_hit_count"], errors="coerce")
    invalid = affection.isna() | negative.isna() | (affection < 0) | (negative < 0)
    invalid |= (affection == 0) & (negative == 0)
    if invalid.any():
        raise DataSourceError(
            "A4 topic-marker evidence obsahuje neplatné hit counts; marker řádek musí mít alespoň jeden explicitní hit."
        )

    if marker_reconciliation.empty:
        raise DataSourceError(
            "A4 publikuje topic-marker evidence bez analysis_a4_topic_marker_reconciliation."
        )
    if len(marker_reconciliation) != 1:
        raise DataSourceError(
            "A4 topic-marker reconciliation musí mít právě jeden řádek pro konverzaci."
        )
    recon = marker_reconciliation.iloc[0]
    expected = {
        "topic_evidence_row_count": len(evidence),
        "marker_evidence_row_count": len(marker_evidence),
        "affection_evidence_row_count": int((affection > 0).sum()),
        "negative_evidence_row_count": int((negative > 0).sum()),
        "reconciliation_ok": 1,
    }
    for column, observed in expected.items():
        if column not in marker_reconciliation.columns:
            raise DataSourceError(
                f"A4 topic-marker reconciliation postrádá sloupec {column}."
            )
        published = int(recon[column])
        if published != int(observed):
            raise DataSourceError(
                f"A4 topic-marker reconciliation nesedí pro {column}: "
                f"published={published}, observed={int(observed)}"
            )


def load_a4_topics(path: str | Path, conversation_id: str) -> A4Topics:
    """Load deterministic A4 lexical-topic and topic-marker evidence.

    Topic candidate ``source_message_ids_json`` and normalized evidence rows
    must agree exactly. Optional A4 v8+ topic-marker co-occurrence is accepted
    only as sparse message-level evidence and is independently reconciled.
    A6 never upgrades marker hits into sentiment or psychological meaning.
    """
    try:
        with _connect_read_only(path) as conn:
            objects = set(_objects(conn))
            if "analysis_a4_topics" not in objects:
                return empty_topics()

            params = (str(conversation_id),)
            topics = pd.read_sql_query(
                "SELECT * FROM analysis_a4_topics WHERE CAST(conversation_id AS TEXT) = ?",
                conn,
                params=params,
            )
            if topics.empty:
                return empty_topics()

            require_reconciled(conn, [str(conversation_id)], context="lexikální témata")

            if "analysis_a4_topic_evidence" not in objects:
                raise DataSourceError(
                    "A4 publikuje témata bez analysis_a4_topic_evidence."
                )
            evidence = pd.read_sql_query(
                "SELECT * FROM analysis_a4_topic_evidence WHERE CAST(conversation_id AS TEXT) = ?",
                conn,
                params=params,
            )
            periods = _read_optional(
                conn, objects, "analysis_a4_topic_periods", params
            )
            period_reconciliation = _read_optional(
                conn, objects, "analysis_a4_topic_period_reconciliation", params
            )
            marker_evidence = _read_optional(
                conn, objects, "analysis_a4_topic_marker_evidence", params
            )
            marker_summary = _read_optional(
                conn, objects, "analysis_a4_topic_marker_summary", params
            )
            marker_periods = _read_optional(
                conn, objects, "analysis_a4_topic_marker_periods", params
            )
            marker_reconciliation = _read_optional(
                conn, objects, "analysis_a4_topic_marker_reconciliation", params
            )
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Chyba při čtení A4 lexikálních témat: {exc}") from exc

    topics = topics.copy()
    evidence = evidence.copy()
    topics["conversation_id"] = topics["conversation_id"].astype(str)
    topics["topic_key"] = topics["topic_key"].astype(str)
    evidence["conversation_id"] = evidence["conversation_id"].astype(str)
    evidence["topic_key"] = evidence["topic_key"].astype(str)
    evidence["message_id"] = evidence["message_id"].astype(str)

    topics["evidence_message_ids"] = [
        _message_ids(value, "analysis_a4_topics")
        for value in topics["source_message_ids_json"]
    ]

    duplicate_evidence = evidence.duplicated(
        subset=["topic_key", "message_id"], keep=False
    )
    if duplicate_evidence.any():
        pairs = evidence.loc[
            duplicate_evidence, ["topic_key", "message_id"]
        ].drop_duplicates()
        rendered = [
            f"{row.topic_key}/{row.message_id}" for row in pairs.itertuples(index=False)
        ]
        raise DataSourceError(
            "A4 topic evidence obsahuje duplicitní topic/message řádky: "
            + ", ".join(rendered)
        )

    evidence_keys = set(evidence["topic_key"])
    topic_keys = set(topics["topic_key"])
    orphan_keys = sorted(evidence_keys - topic_keys)
    if orphan_keys:
        raise DataSourceError(
            "A4 topic evidence odkazuje na nepublikované topic_key: "
            + ", ".join(orphan_keys)
        )

    for row in topics.itertuples(index=False):
        expected = tuple(row.evidence_message_ids)
        actual = tuple(
            evidence.loc[
                evidence["topic_key"] == row.topic_key, "message_id"
            ].astype(str)
        )
        if set(expected) != set(actual) or len(expected) != len(actual):
            raise DataSourceError(
                f"A4 topic evidence mismatch pro topic_key {row.topic_key}: "
                f"candidate={list(expected)}, evidence={list(actual)}"
            )

    if not period_reconciliation.empty:
        if len(period_reconciliation) != 1:
            raise DataSourceError(
                "A4 topic period reconciliation musí mít právě jeden řádek pro konverzaci."
            )
        recon = period_reconciliation.iloc[0]
        expected_counts = {
            "evidence_row_count": len(evidence),
            "topic_count": topics["topic_key"].nunique(),
            "evidence_message_count": evidence["message_id"].nunique(),
        }
        for column, expected in expected_counts.items():
            if column not in period_reconciliation.columns:
                raise DataSourceError(
                    f"A4 topic period reconciliation postrádá sloupec {column}."
                )
            if int(recon[column]) != int(expected):
                raise DataSourceError(
                    f"A4 topic period reconciliation nesedí pro {column}: "
                    f"published={int(recon[column])}, observed={int(expected)}"
                )
        if {
            "dated_evidence_row_count",
            "undated_evidence_row_count",
        }.issubset(period_reconciliation.columns):
            dated = int(recon["dated_evidence_row_count"])
            undated = int(recon["undated_evidence_row_count"])
            if dated + undated != len(evidence):
                raise DataSourceError(
                    "A4 topic period reconciliation nevysvětluje všechny evidence rows."
                )

    if not periods.empty:
        periods = periods.copy()
        periods["conversation_id"] = periods["conversation_id"].astype(str)
        periods["topic_key"] = periods["topic_key"].astype(str)
        period_orphans = sorted(set(periods["topic_key"]) - topic_keys)
        if period_orphans:
            raise DataSourceError(
                "A4 topic periods odkazují na nepublikované topic_key: "
                + ", ".join(period_orphans)
            )

    if not marker_evidence.empty:
        marker_evidence = marker_evidence.copy()
        marker_evidence["conversation_id"] = marker_evidence[
            "conversation_id"
        ].astype(str)
        marker_evidence["topic_key"] = marker_evidence["topic_key"].astype(str)
        marker_evidence["message_id"] = marker_evidence["message_id"].astype(str)

    for frame in (marker_summary, marker_periods, marker_reconciliation):
        if not frame.empty:
            if "conversation_id" in frame:
                frame["conversation_id"] = frame["conversation_id"].astype(str)
            if "topic_key" in frame:
                frame["topic_key"] = frame["topic_key"].astype(str)

    _validate_marker_evidence(evidence, marker_evidence, marker_reconciliation)

    marker_topic_keys = set(marker_evidence["topic_key"]) if not marker_evidence.empty else set()
    marker_orphans = sorted(marker_topic_keys - topic_keys)
    if marker_orphans:
        raise DataSourceError(
            "A4 topic-marker evidence odkazuje na nepublikované topic_key: "
            + ", ".join(marker_orphans)
        )

    for frame_name, frame in (
        ("summary", marker_summary),
        ("periods", marker_periods),
    ):
        if not frame.empty:
            frame_orphans = sorted(set(frame["topic_key"].astype(str)) - topic_keys)
            if frame_orphans:
                raise DataSourceError(
                    f"A4 topic-marker {frame_name} odkazuje na nepublikované topic_key: "
                    + ", ".join(frame_orphans)
                )

    return A4Topics(
        topics=topics.reset_index(drop=True),
        evidence=evidence.reset_index(drop=True),
        periods=periods.reset_index(drop=True),
        period_reconciliation=period_reconciliation.reset_index(drop=True),
        marker_evidence=marker_evidence.reset_index(drop=True),
        marker_summary=marker_summary.reset_index(drop=True),
        marker_periods=marker_periods.reset_index(drop=True),
        marker_reconciliation=marker_reconciliation.reset_index(drop=True),
    )
