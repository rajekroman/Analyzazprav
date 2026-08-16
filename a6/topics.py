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

    @property
    def available(self) -> bool:
        return not self.topics.empty


def empty_topics() -> A4Topics:
    return A4Topics(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())


def _message_ids(value: object, source: str) -> tuple[str, ...]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ()
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataSourceError(f"Neplatná topic evidence v {source}: source_message_ids_json není validní JSON.") from exc
    if not isinstance(parsed, list):
        raise DataSourceError(f"Neplatná topic evidence v {source}: source_message_ids_json musí být pole.")
    ids = tuple(str(item) for item in parsed)
    if len(ids) != len(set(ids)):
        raise DataSourceError(f"Neplatná topic evidence v {source}: source_message_ids_json obsahuje duplicitní ID.")
    return ids


def load_a4_topics(path: str | Path, conversation_id: str) -> A4Topics:
    """Load A4 lexical topic evidence without adding semantic interpretation.

    Candidate ``source_message_ids_json`` and normalized evidence rows must
    agree exactly. When A4 publishes period reconciliation, A6 independently
    checks its core counts before rendering topic period projections.
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
                raise DataSourceError("A4 publikuje témata bez analysis_a4_topic_evidence.")
            evidence = pd.read_sql_query(
                "SELECT * FROM analysis_a4_topic_evidence WHERE CAST(conversation_id AS TEXT) = ?",
                conn,
                params=params,
            )
            periods = (
                pd.read_sql_query(
                    "SELECT * FROM analysis_a4_topic_periods WHERE CAST(conversation_id AS TEXT) = ?",
                    conn,
                    params=params,
                )
                if "analysis_a4_topic_periods" in objects
                else pd.DataFrame()
            )
            period_reconciliation = (
                pd.read_sql_query(
                    "SELECT * FROM analysis_a4_topic_period_reconciliation WHERE CAST(conversation_id AS TEXT) = ?",
                    conn,
                    params=params,
                )
                if "analysis_a4_topic_period_reconciliation" in objects
                else pd.DataFrame()
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

    duplicate_evidence = evidence.duplicated(subset=["topic_key", "message_id"], keep=False)
    if duplicate_evidence.any():
        pairs = evidence.loc[duplicate_evidence, ["topic_key", "message_id"]].drop_duplicates()
        rendered = [f"{row.topic_key}/{row.message_id}" for row in pairs.itertuples(index=False)]
        raise DataSourceError("A4 topic evidence obsahuje duplicitní topic/message řádky: " + ", ".join(rendered))

    evidence_keys = set(evidence["topic_key"])
    topic_keys = set(topics["topic_key"])
    orphan_keys = sorted(evidence_keys - topic_keys)
    if orphan_keys:
        raise DataSourceError("A4 topic evidence odkazuje na nepublikované topic_key: " + ", ".join(orphan_keys))

    for row in topics.itertuples(index=False):
        expected = tuple(row.evidence_message_ids)
        actual = tuple(
            evidence.loc[evidence["topic_key"] == row.topic_key, "message_id"].astype(str)
        )
        if set(expected) != set(actual) or len(expected) != len(actual):
            raise DataSourceError(
                f"A4 topic evidence mismatch pro topic_key {row.topic_key}: "
                f"candidate={list(expected)}, evidence={list(actual)}"
            )

    if not period_reconciliation.empty:
        if len(period_reconciliation) != 1:
            raise DataSourceError("A4 topic period reconciliation musí mít právě jeden řádek pro konverzaci.")
        recon = period_reconciliation.iloc[0]
        expected_counts = {
            "evidence_row_count": len(evidence),
            "topic_count": topics["topic_key"].nunique(),
            "evidence_message_count": evidence["message_id"].nunique(),
        }
        for column, expected in expected_counts.items():
            if column not in period_reconciliation.columns:
                raise DataSourceError(f"A4 topic period reconciliation postrádá sloupec {column}.")
            if int(recon[column]) != int(expected):
                raise DataSourceError(
                    f"A4 topic period reconciliation nesedí pro {column}: "
                    f"published={int(recon[column])}, observed={int(expected)}"
                )

    if not periods.empty:
        periods = periods.copy()
        periods["conversation_id"] = periods["conversation_id"].astype(str)
        periods["topic_key"] = periods["topic_key"].astype(str)
        period_orphans = sorted(set(periods["topic_key"]) - topic_keys)
        if period_orphans:
            raise DataSourceError("A4 topic periods odkazují na nepublikované topic_key: " + ", ".join(period_orphans))

    return A4Topics(
        topics=topics.reset_index(drop=True),
        evidence=evidence.reset_index(drop=True),
        periods=periods.reset_index(drop=True),
        period_reconciliation=period_reconciliation.reset_index(drop=True),
    )
