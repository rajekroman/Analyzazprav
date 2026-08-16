from __future__ import annotations

from dataclasses import dataclass, replace
import json
import sqlite3
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
        return {str(key): float(item) for key, item in parsed.items()}
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


_SEMANTICS = {
    "conflict": "heuristic_pattern_candidate_not_event_fact",
    "change_point": "statistical_change_candidate",
    "engagement_signal": "heuristic_signal_not_fact",
    "dyadic_regime": "operational_pattern_candidate_not_interpretation",
    "lexical_topic": "lexical_evidence_not_semantic_topic",
}


@dataclass(frozen=True)
class A4SQLiteCandidateSource:
    """Read-only adapter over A4 published analysis views.

    Current production rows are bound to exact A4 analytics-run provenance.
    Tiny legacy unit fixtures without analytics_run_id remain readable only so
    converter behavior can be tested in isolation; such candidates are marked
    as lacking production provenance.
    """

    database_path: Path

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise A4SQLiteSourceError(f"A4 database does not exist: {path}")
        object.__setattr__(self, "database_path", path)

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(
                f"file:{self.database_path.as_posix()}?mode=ro",
                uri=True,
            )
        except sqlite3.Error as exc:
            raise A4SQLiteSourceError(f"Cannot open A4 database read-only: {exc}") from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    @staticmethod
    def _object_exists(conn: sqlite3.Connection, name: str, object_type: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
            (object_type, name),
        ).fetchone() is not None

    def _provenance(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, object]:
        keys = set(row.keys())
        if "analytics_run_id" not in keys:
            return {
                "source": "a4",
                "a4_provenance_status": "legacy_fixture_missing",
            }
        if not self._object_exists(conn, "analytics_run", "table"):
            raise A4SQLiteSourceError(
                "A4 row exposes analytics_run_id but analytics_run table is missing"
            )
        run_id = int(row["analytics_run_id"])
        run = conn.execute(
            """SELECT analytics_version, processing_run_id, status
               FROM analytics_run WHERE id=?""",
            (run_id,),
        ).fetchone()
        if run is None:
            raise A4SQLiteSourceError(f"A4 analytics_run {run_id} does not exist")
        if str(run["status"]) != "completed":
            raise A4SQLiteSourceError(
                f"A4 analytics_run {run_id} is not completed: {run['status']!r}"
            )

        metadata: dict[str, object] = {
            "source": "a4",
            "a4_provenance_status": "complete",
            "analytics_run_id": run_id,
            "analytics_version": str(run["analytics_version"]),
            "processing_run_id": int(run["processing_run_id"]),
        }
        if not self._object_exists(conn, "analytics_conversation_state_v6", "table"):
            raise A4SQLiteSourceError(
                "Current A4 provenance requires analytics_conversation_state_v6"
            )
        state = conn.execute(
            """SELECT source_fingerprint, analysis_signature
               FROM analytics_conversation_state_v6
               WHERE analytics_run_id=? AND conversation_id=?""",
            (run_id, int(row["conversation_id"])),
        ).fetchone()
        if state is None:
            raise A4SQLiteSourceError(
                "A4 conversation state provenance missing for "
                f"run={run_id}, conversation={row['conversation_id']}"
            )
        metadata["source_fingerprint"] = str(state["source_fingerprint"])
        metadata["analysis_signature"] = str(state["analysis_signature"])
        return metadata

    def _rows(
        self,
        view: str,
        conversation_id: str,
    ) -> list[tuple[sqlite3.Row, dict[str, object]]]:
        with self._connect() as conn:
            if not self._object_exists(conn, view, "view"):
                return []
            try:
                rows = conn.execute(
                    f"SELECT * FROM {view} WHERE CAST(conversation_id AS TEXT)=?",
                    (str(conversation_id),),
                ).fetchall()
                return [(row, self._provenance(conn, row)) for row in rows]
            except sqlite3.Error as exc:
                raise A4SQLiteSourceError(f"Cannot read {view}: {exc}") from exc

    @staticmethod
    def _decorate(
        candidate: AnalysisCandidate,
        provenance: dict[str, object],
    ) -> AnalysisCandidate:
        semantics = _SEMANTICS.get(candidate.candidate_type, "deterministic_candidate")
        return replace(
            candidate,
            metadata={
                **dict(candidate.metadata),
                **provenance,
                "candidate_semantics": semantics,
            },
        )

    def conflicts(self, conversation_id: str) -> tuple[AnalysisCandidate, ...]:
        result: list[AnalysisCandidate] = []
        for row, provenance in self._rows("analysis_a4_events", conversation_id):
            if str(row["event_type"]) != "conflict":
                continue
            candidate = candidate_from_a4_conflict(
                SimpleNamespace(
                    conversation_id=int(row["conversation_id"]),
                    session_id=int(row["session_id"]),
                    score=float(row["score"]),
                    start_us=row["start_at_utc_us"],
                    end_us=row["end_at_utc_us"],
                    factors=_json_object(
                        row["factors_json"],
                        field="analysis_a4_events.factors_json",
                    ),
                    source_message_ids=_json_ids(
                        row["source_message_ids_json"],
                        field="analysis_a4_events.source_message_ids_json",
                    ),
                )
            )
            result.append(self._decorate(candidate, provenance))
        return tuple(result)

    def change_points(self, conversation_id: str) -> tuple[AnalysisCandidate, ...]:
        result: list[AnalysisCandidate] = []
        for row, provenance in self._rows("analysis_a4_changes", conversation_id):
            candidate = candidate_from_a4_change_point(
                SimpleNamespace(
                    conversation_id=int(row["conversation_id"]),
                    participant_id=int(row["participant_id"]),
                    metric=str(row["metric"]),
                    period_date=str(row["period_date"]),
                    value=float(row["value"]),
                    baseline_median=float(row["baseline_median"]),
                    robust_z_score=float(row["robust_z_score"]),
                    direction=str(row["direction"]),
                    source_message_ids=_json_ids(
                        row["source_message_ids_json"],
                        field="analysis_a4_changes.source_message_ids_json",
                    ),
                )
            )
            result.append(self._decorate(candidate, provenance))
        return tuple(result)

    def engagement_signals(self, conversation_id: str) -> tuple[AnalysisCandidate, ...]:
        result: list[AnalysisCandidate] = []
        for row, provenance in self._rows(
            "analysis_a4_engagement_signals", conversation_id
        ):
            candidate = candidate_from_a4_engagement(
                SimpleNamespace(
                    conversation_id=int(row["conversation_id"]),
                    participant_id=int(row["participant_id"]),
                    period_start=str(row["period_start"]),
                    period_end=str(row["period_end"]),
                    score=float(row["score"]),
                    direction=str(row["direction"]),
                    component_scores=_json_object(
                        row["component_scores_json"],
                        field="analysis_a4_engagement_signals.component_scores_json",
                    ),
                    source_message_ids=_json_ids(
                        row["source_message_ids_json"],
                        field="analysis_a4_engagement_signals.source_message_ids_json",
                    ),
                )
            )
            result.append(self._decorate(candidate, provenance))
        return tuple(result)

    def regimes(self, conversation_id: str) -> tuple[AnalysisCandidate, ...]:
        result: list[AnalysisCandidate] = []
        for row, provenance in self._rows("analysis_a4_regimes", conversation_id):
            candidate = candidate_from_a4_regime(
                SimpleNamespace(
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
                    source_message_ids=_json_ids(
                        row["source_message_ids_json"],
                        field="analysis_a4_regimes.source_message_ids_json",
                    ),
                )
            )
            result.append(self._decorate(candidate, provenance))
        return tuple(result)

    def topics(self, conversation_id: str) -> tuple[AnalysisCandidate, ...]:
        result: list[AnalysisCandidate] = []
        for row, provenance in self._rows("analysis_a4_topics", conversation_id):
            if row["first_period_date"] is None or row["last_period_date"] is None:
                continue
            candidate = candidate_from_a4_topic(
                SimpleNamespace(
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
                    source_message_ids=_json_ids(
                        row["source_message_ids_json"],
                        field="analysis_a4_topics.source_message_ids_json",
                    ),
                )
            )
            result.append(self._decorate(candidate, provenance))
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
        merged.sort(
            key=lambda candidate: (
                candidate.start_ts,
                candidate.end_ts,
                candidate.candidate_type,
                candidate.id,
            )
        )
        return tuple(merged)
