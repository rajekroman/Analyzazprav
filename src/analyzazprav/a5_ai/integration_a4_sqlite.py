from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

from .integration_a4 import (
    candidate_from_a4_change_point,
    candidate_from_a4_conflict,
    candidate_from_a4_engagement,
    candidate_from_a4_regime,
    candidate_from_a4_topic,
)
from .models import AnalysisCandidate


class A4SQLiteSourceError(RuntimeError):
    pass


def _json_object(value: object, *, field: str) -> dict[str, float]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError as exc:
        raise A4SQLiteSourceError(f"Invalid JSON in {field}") from exc
    if not isinstance(parsed, dict):
        raise A4SQLiteSourceError(f"{field} must contain a JSON object")
    try:
        return {str(k): float(v) for k, v in parsed.items()}
    except (TypeError, ValueError) as exc:
        raise A4SQLiteSourceError(f"{field} must contain numeric values") from exc


def _json_ids(value: object, *, field: str) -> tuple[int, ...]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError as exc:
        raise A4SQLiteSourceError(f"Invalid JSON in {field}") from exc
    if not isinstance(parsed, list):
        raise A4SQLiteSourceError(f"{field} must contain a JSON array")
    try:
        ids = tuple(int(item) for item in parsed)
    except (TypeError, ValueError) as exc:
        raise A4SQLiteSourceError(f"{field} must contain integer message IDs") from exc
    if len(ids) != len(set(ids)):
        raise A4SQLiteSourceError(f"{field} contains duplicate message IDs")
    return ids


@dataclass(frozen=True)
class A4SQLiteCandidateSource:
    """Read-only adapter over A4's published, reconciled analysis views.

    A5 is interpretive and therefore may only consume deterministic A4 findings
    whose current conversation row passes A4's published reconciliation gate.
    The adapter never repairs or reinterprets A4 rows; malformed provenance is a
    hard error before any candidate can reach an AI provider.
    """

    database_path: Path

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise A4SQLiteSourceError(f"A4 database does not exist: {path}")
        object.__setattr__(self, "database_path", path)

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(f"file:{self.database_path.as_posix()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise A4SQLiteSourceError(f"Cannot open A4 database read-only: {exc}") from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    @staticmethod
    def _view_exists(conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def _assert_reconciled(self, conn: sqlite3.Connection, conversation_id: str) -> sqlite3.Row:
        if not self._view_exists(conn, "analysis_a4_reconciliation"):
            raise A4SQLiteSourceError(
                "A4 database is missing required analysis_a4_reconciliation; "
                "A5 refuses deterministic findings without a current QA/provenance gate"
            )
        try:
            rows = conn.execute(
                """SELECT * FROM analysis_a4_reconciliation
                   WHERE CAST(conversation_id AS TEXT)=?""",
                (str(conversation_id),),
            ).fetchall()
        except sqlite3.Error as exc:
            raise A4SQLiteSourceError(f"Cannot read analysis_a4_reconciliation: {exc}") from exc
        if len(rows) != 1:
            raise A4SQLiteSourceError(
                f"Expected exactly one A4 reconciliation row for conversation {conversation_id}; "
                f"found {len(rows)}"
            )
        row = rows[0]
        required_zero = (
            "membership_count_delta",
            "invalid_response_session_count",
            "invalid_silence_session_count",
            "invalid_event_session_count",
        )
        for field in required_zero:
            if field not in row.keys() or int(row[field]) != 0:
                raise A4SQLiteSourceError(
                    f"A4 reconciliation failed for conversation {conversation_id}: {field}={row[field] if field in row.keys() else 'missing'}"
                )
        if "uses_latest_processing_run" not in row.keys() or int(row["uses_latest_processing_run"]) != 1:
            raise A4SQLiteSourceError(
                f"A4 reconciliation failed for conversation {conversation_id}: stale A3 processing run"
            )
        if "reconciliation_ok" not in row.keys() or int(row["reconciliation_ok"]) != 1:
            raise A4SQLiteSourceError(
                f"A4 reconciliation failed for conversation {conversation_id}: reconciliation_ok != 1"
            )
        if (
            "a4_source_membership_count" in row.keys()
            and "sender_accounted_membership_count" in row.keys()
            and int(row["a4_source_membership_count"])
            != int(row["sender_accounted_membership_count"])
        ):
            raise A4SQLiteSourceError(
                f"A4 reconciliation failed for conversation {conversation_id}: sender accounting mismatch"
            )
        return row

    def _rows(self, view: str, conversation_id: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            self._assert_reconciled(conn, conversation_id)
            if not self._view_exists(conn, view):
                return []
            try:
                return conn.execute(
                    f"SELECT * FROM {view} WHERE CAST(conversation_id AS TEXT)=?",
                    (str(conversation_id),),
                ).fetchall()
            except sqlite3.Error as exc:
                raise A4SQLiteSourceError(f"Cannot read {view}: {exc}") from exc

    def conflicts(self, conversation_id: str) -> tuple[AnalysisCandidate, ...]:
        result: list[AnalysisCandidate] = []
        for row in self._rows("analysis_a4_events", conversation_id):
            if str(row["event_type"]) != "conflict":
                continue
            result.append(candidate_from_a4_conflict(SimpleNamespace(
                conversation_id=int(row["conversation_id"]),
                session_id=int(row["session_id"]),
                score=float(row["score"]),
                start_us=row["start_at_utc_us"],
                end_us=row["end_at_utc_us"],
                factors=_json_object(row["factors_json"], field="analysis_a4_events.factors_json"),
                source_message_ids=_json_ids(row["source_message_ids_json"], field="analysis_a4_events.source_message_ids_json"),
            )))
        return tuple(result)

    def change_points(self, conversation_id: str) -> tuple[AnalysisCandidate, ...]:
        result: list[AnalysisCandidate] = []
        for row in self._rows("analysis_a4_changes", conversation_id):
            result.append(candidate_from_a4_change_point(SimpleNamespace(
                conversation_id=int(row["conversation_id"]),
                participant_id=int(row["participant_id"]),
                metric=str(row["metric"]),
                period_date=str(row["period_date"]),
                value=float(row["value"]),
                baseline_median=float(row["baseline_median"]),
                robust_z_score=float(row["robust_z_score"]),
                direction=str(row["direction"]),
                source_message_ids=_json_ids(row["source_message_ids_json"], field="analysis_a4_changes.source_message_ids_json"),
            )))
        return tuple(result)

    def engagement_signals(self, conversation_id: str) -> tuple[AnalysisCandidate, ...]:
        result: list[AnalysisCandidate] = []
        for row in self._rows("analysis_a4_engagement_signals", conversation_id):
            result.append(candidate_from_a4_engagement(SimpleNamespace(
                conversation_id=int(row["conversation_id"]),
                participant_id=int(row["participant_id"]),
                period_start=str(row["period_start"]),
                period_end=str(row["period_end"]),
                score=float(row["score"]),
                direction=str(row["direction"]),
                component_scores=_json_object(row["component_scores_json"], field="analysis_a4_engagement_signals.component_scores_json"),
                source_message_ids=_json_ids(row["source_message_ids_json"], field="analysis_a4_engagement_signals.source_message_ids_json"),
            )))
        return tuple(result)

    def regimes(self, conversation_id: str) -> tuple[AnalysisCandidate, ...]:
        result: list[AnalysisCandidate] = []
        for row in self._rows("analysis_a4_regimes", conversation_id):
            result.append(candidate_from_a4_regime(SimpleNamespace(
                conversation_id=int(row["conversation_id"]),
                period_start=str(row["period_start"]),
                period_end=str(row["period_end"]),
                participant_a_id=int(row["participant_a_id"]),
                participant_a_direction=str(row["participant_a_direction"]),
                participant_a_score=float(row["participant_a_score"]),
                participant_b_id=int(row["participant_b_id"]),
                participant_b_direction=str(row["participant_b_direction"]),
                participant_b_score=float(row["participant_b_score"]),
                regime_type=str(row["regime_type"]),
                source_message_ids=_json_ids(row["source_message_ids_json"], field="analysis_a4_regimes.source_message_ids_json"),
            )))
        return tuple(result)

    def _topic_evidence_ids(
        self,
        conn: sqlite3.Connection,
        *,
        conversation_id: int,
        analytics_run_id: int,
        topic_key: str,
    ) -> tuple[int, ...]:
        if not self._view_exists(conn, "analysis_a4_topic_evidence"):
            raise A4SQLiteSourceError(
                "A4 publishes topic candidates but analysis_a4_topic_evidence is missing"
            )
        try:
            rows = conn.execute(
                """SELECT message_id
                   FROM analysis_a4_topic_evidence
                   WHERE conversation_id=? AND analytics_run_id=? AND topic_key=?
                   ORDER BY message_id""",
                (conversation_id, analytics_run_id, topic_key),
            ).fetchall()
        except sqlite3.Error as exc:
            raise A4SQLiteSourceError(f"Cannot read analysis_a4_topic_evidence: {exc}") from exc
        return tuple(int(row[0]) for row in rows)

    def topics(self, conversation_id: str) -> tuple[AnalysisCandidate, ...]:
        result: list[AnalysisCandidate] = []
        with self._connect() as conn:
            self._assert_reconciled(conn, conversation_id)
            if not self._view_exists(conn, "analysis_a4_topics"):
                return ()
            try:
                rows = conn.execute(
                    "SELECT * FROM analysis_a4_topics WHERE CAST(conversation_id AS TEXT)=?",
                    (str(conversation_id),),
                ).fetchall()
            except sqlite3.Error as exc:
                raise A4SQLiteSourceError(f"Cannot read analysis_a4_topics: {exc}") from exc

            for row in rows:
                # Undated topics remain valid A4 lexical evidence but cannot define
                # a bounded A5 period; do not convert them into an automatic candidate.
                if row["first_period_date"] is None or row["last_period_date"] is None:
                    continue
                source_ids = _json_ids(
                    row["source_message_ids_json"],
                    field="analysis_a4_topics.source_message_ids_json",
                )
                evidence_ids = self._topic_evidence_ids(
                    conn,
                    conversation_id=int(row["conversation_id"]),
                    analytics_run_id=int(row["analytics_run_id"]),
                    topic_key=str(row["topic_key"]),
                )
                if tuple(sorted(source_ids)) != evidence_ids:
                    raise A4SQLiteSourceError(
                        "A4 lexical topic evidence mismatch for "
                        f"conversation={row['conversation_id']}, topic_key={row['topic_key']}: "
                        f"candidate={tuple(sorted(source_ids))}, evidence={evidence_ids}"
                    )
                result.append(candidate_from_a4_topic(SimpleNamespace(
                    conversation_id=int(row["conversation_id"]),
                    topic_key=str(row["topic_key"]),
                    method=str(row["method"]),
                    normalized_phrase=str(row["normalized_phrase"]),
                    ngram_size=int(row["ngram_size"]),
                    document_frequency=int(row["document_frequency"]),
                    document_frequency_ratio=float(row["document_frequency_ratio"]),
                    occurrence_count=int(row["occurrence_count"]),
                    participant_count=int(row["participant_count"]),
                    salience=float(row["salience"]),
                    first_period_date=str(row["first_period_date"]),
                    last_period_date=str(row["last_period_date"]),
                    source_message_ids=source_ids,
                )))
        return tuple(result)

    def candidates(self, conversation_id: str) -> tuple[AnalysisCandidate, ...]:
        groups: Iterable[tuple[AnalysisCandidate, ...]] = (
            self.conflicts(conversation_id),
            self.change_points(conversation_id),
            self.engagement_signals(conversation_id),
            self.regimes(conversation_id),
            self.topics(conversation_id),
        )
        merged = [candidate for group in groups for candidate in group]
        merged.sort(key=lambda c: (c.start_ts, c.end_ts, c.candidate_type, c.id))
        return tuple(merged)
