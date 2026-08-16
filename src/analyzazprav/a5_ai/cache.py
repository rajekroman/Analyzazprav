from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    AIAnalysisResult,
    AnalysisStatus,
    EvidenceRef,
    Interpretation,
    MessageEvidence,
    MetricEvidence,
    Observation,
    Pattern,
)


def make_context_hash(*, context_payload: dict[str, Any], analysis_type: str, mode: str, provider_name: str, model_name: str, prompt_version: str) -> str:
    canonical = {
        "analysis_type": analysis_type,
        "mode": mode,
        "provider": provider_name,
        "model": model_name,
        "prompt_version": prompt_version,
        "context": context_payload,
    }
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class AnalysisCache:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_analysis (
                    context_hash TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    analysis_type TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    provider_name TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL
                )
            """)

    def get(self, context_hash: str) -> AIAnalysisResult | None:
        with self._connect() as conn:
            row = conn.execute("SELECT result_json, status FROM ai_analysis WHERE context_hash = ?", (context_hash,)).fetchone()
        if row is None or row["status"] != AnalysisStatus.COMPLETED.value or not row["result_json"]:
            return None
        return _result_from_json(row["result_json"])

    def put(self, *, context_hash: str, conversation_id: str, analysis_type: str, mode: str, provider_name: str, model_name: str, prompt_version: str, status: AnalysisStatus, result: AIAnalysisResult | None, error: str | None = None) -> None:
        result_json = json.dumps(asdict(result), ensure_ascii=False, sort_keys=True) if result else None
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO ai_analysis (
                    context_hash, conversation_id, analysis_type, mode,
                    provider_name, model_name, prompt_version, status,
                    result_json, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(context_hash) DO UPDATE SET
                    status = excluded.status,
                    result_json = excluded.result_json,
                    error = excluded.error,
                    created_at = excluded.created_at
            """, (
                context_hash, conversation_id, analysis_type, mode,
                provider_name, model_name, prompt_version, status.value,
                result_json, error, datetime.now(timezone.utc).isoformat(),
            ))


def _evidence_from_data(data: dict[str, Any] | None, fallback_ids: tuple[str, ...] = ()) -> EvidenceRef | None:
    if data is None:
        return None
    return EvidenceRef(
        message_ids=tuple(data.get("message_ids", fallback_ids)),
        description=data.get("description", ""),
        messages=tuple(
            MessageEvidence(
                message_id=item["message_id"],
                timestamp=item["timestamp"],
                sender_id=item["sender_id"],
                excerpt=item.get("excerpt", ""),
            )
            for item in data.get("messages", [])
        ),
        metrics=tuple(
            MetricEvidence(
                phase=item["phase"],
                name=item["name"],
                value=float(item["value"]),
            )
            for item in data.get("metrics", [])
        ),
    )


def _result_from_json(raw: str) -> AIAnalysisResult:
    data = json.loads(raw)
    summary_evidence = _evidence_from_data(data.get("summary_evidence"))
    if summary_evidence is None:
        raise ValueError("Cached result is missing summary evidence")

    observations = []
    for o in data.get("observations", []):
        evidence_data = o["evidence"]
        observations.append(
            Observation(
                text=o["text"],
                evidence=_evidence_from_data(evidence_data) or EvidenceRef(message_ids=()),
                strength=float(o["strength"]),
            )
        )
    interpretations = []
    for i in data.get("interpretations", []):
        ids = tuple(i.get("evidence_message_ids", []))
        interpretations.append(
            Interpretation(
                text=i["text"],
                evidence_message_ids=ids,
                confidence=float(i["confidence"]),
                evidence=_evidence_from_data(i.get("evidence"), ids),
            )
        )
    patterns = []
    for p in data.get("patterns", []):
        ids = tuple(p.get("evidence_message_ids", []))
        patterns.append(
            Pattern(
                pattern_type=p["pattern_type"],
                description=p["description"],
                occurrences=p.get("occurrences"),
                confidence=float(p["confidence"]),
                evidence_message_ids=ids,
                evidence=_evidence_from_data(p.get("evidence"), ids),
            )
        )
    turning_points = tuple(data.get("turning_points", []))
    turning_point_evidence = tuple(
        evidence for item in data.get("turning_point_evidence", [])
        if (evidence := _evidence_from_data(item)) is not None
    )
    if len(turning_points) != len(turning_point_evidence):
        raise ValueError("Cached turning point evidence count does not match turning points")

    return AIAnalysisResult(
        summary=data["summary"],
        summary_evidence=summary_evidence,
        observations=tuple(observations),
        interpretations=tuple(interpretations),
        patterns=tuple(patterns),
        turning_points=turning_points,
        turning_point_evidence=turning_point_evidence,
        participant_p1=data.get("participant_p1"),
        participant_p1_evidence=_evidence_from_data(data.get("participant_p1_evidence")),
        participant_p2=data.get("participant_p2"),
        participant_p2_evidence=_evidence_from_data(data.get("participant_p2_evidence")),
        shared_dynamic=data.get("shared_dynamic"),
        shared_dynamic_evidence=_evidence_from_data(data.get("shared_dynamic_evidence")),
        alternative_explanations=tuple(data.get("alternative_explanations", [])),
        unknowns=tuple(data.get("unknowns", [])),
        overall_confidence=float(data.get("overall_confidence", 0.0)),
    )
