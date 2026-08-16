from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class AnalysisType(str, Enum):
    SEGMENT = "segment"
    CHANGE_POINT = "change_point"
    CONFLICT = "conflict"
    INTERACTION_CYCLE = "interaction_cycle"
    LONGITUDINAL = "longitudinal"
    RELATIONSHIP_DYNAMICS = "relationship_dynamics"
    PSYCHOLOGICAL_HYPOTHESES = "psychological_hypotheses"


class AnalysisMode(str, Enum):
    BLIND = "blind"
    RETROSPECTIVE = "retrospective"


class CandidateDisposition(str, Enum):
    IGNORE = "ignore"
    SUGGEST = "suggest"
    ANALYZE = "analyze"


class AnalysisStatus(str, Enum):
    COMPLETED = "completed"
    CACHE_HIT = "cache_hit"
    MODEL_UNAVAILABLE = "model_unavailable"
    ANALYSIS_TIMEOUT = "analysis_timeout"
    INVALID_OUTPUT = "invalid_output"
    FAILED_VALIDATION = "failed_validation"
    CONTEXT_TOO_LARGE = "context_too_large"


@dataclass(frozen=True)
class MessageRecord:
    id: str
    conversation_id: str
    participant_id: str
    timestamp: datetime
    text: str = ""
    reply_to_message_id: str | None = None
    attachment_types: tuple[str, ...] = ()
    reaction: str | None = None
    edited: bool = False
    deleted: bool = False
    membership_id: str | None = None
    source_record_keys: tuple[str, ...] = ()
    source_snapshot_keys: tuple[str, ...] = ()
    source_parser_versions: tuple[str, ...] = ()

    def safe_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "membership_id": self.membership_id,
            "participant_id": self.participant_id,
            "timestamp": self.timestamp.isoformat(),
            "text": self.text,
            "reply_to_message_id": self.reply_to_message_id,
            "attachment_types": list(self.attachment_types),
            "reaction": self.reaction,
            "edited": self.edited,
            "deleted": self.deleted,
            "source_record_keys": list(self.source_record_keys),
            "source_snapshot_keys": list(self.source_snapshot_keys),
            "source_parser_versions": list(self.source_parser_versions),
        }


@dataclass(frozen=True)
class AnalysisCandidate:
    id: str
    conversation_id: str
    start_ts: datetime
    end_ts: datetime
    candidate_type: str
    importance_score: float
    metrics_before: Mapping[str, float] = field(default_factory=dict)
    metrics_during: Mapping[str, float] = field(default_factory=dict)
    metrics_after: Mapping[str, float] = field(default_factory=dict)
    detected_signals: tuple[str, ...] = ()
    evidence_message_ids: tuple[str, ...] = ()
    manual_request: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.end_ts < self.start_ts:
            raise ValueError("candidate end_ts must be >= start_ts")
        if not 0 <= self.importance_score <= 100:
            raise ValueError("importance_score must be between 0 and 100")


@dataclass(frozen=True)
class CandidateDecision:
    candidate_id: str
    disposition: CandidateDisposition
    reason: str


@dataclass(frozen=True)
class AIAnalysisRequest:
    conversation_id: str
    analysis_type: AnalysisType
    start_ts: datetime
    end_ts: datetime
    mode: AnalysisMode = AnalysisMode.BLIND
    candidate_id: str | None = None
    user_question: str | None = None
    force_refresh: bool = False

    def __post_init__(self) -> None:
        if self.end_ts < self.start_ts:
            raise ValueError("request end_ts must be >= start_ts")


@dataclass(frozen=True)
class AnalysisContext:
    conversation_id: str
    analysis_type: AnalysisType
    mode: AnalysisMode
    requested_start_ts: datetime
    requested_end_ts: datetime
    context_start_ts: datetime
    context_end_ts: datetime
    cutoff_ts: datetime | None
    messages: tuple[MessageRecord, ...]
    evidence_message_ids: tuple[str, ...]
    metrics_before: Mapping[str, float] = field(default_factory=dict)
    metrics_during: Mapping[str, float] = field(default_factory=dict)
    metrics_after: Mapping[str, float] = field(default_factory=dict)
    detected_signals: tuple[str, ...] = ()
    candidate_provenance: Mapping[str, Any] = field(default_factory=dict)
    available_message_count: int = 0
    omitted_message_count: int = 0
    omitted_message_ids: tuple[str, ...] = ()
    omitted_message_ids_sha256: str | None = None
    missing_evidence_message_ids: tuple[str, ...] = ()
    quality_warnings: tuple[str, ...] = ()

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "analysis_type": self.analysis_type.value,
            "mode": self.mode.value,
            "requested_period": {
                "from": self.requested_start_ts.isoformat(),
                "to": self.requested_end_ts.isoformat(),
            },
            "context_period": {
                "from": self.context_start_ts.isoformat(),
                "to": self.context_end_ts.isoformat(),
            },
            "cutoff_ts": self.cutoff_ts.isoformat() if self.cutoff_ts else None,
            "metrics": {
                "before": dict(self.metrics_before),
                "during": dict(self.metrics_during),
                "after": dict(self.metrics_after),
            },
            "metric_provenance": dict(self.candidate_provenance),
            "detected_signals": list(self.detected_signals),
            "evidence_message_ids": list(self.evidence_message_ids),
            "missing_evidence_message_ids": list(self.missing_evidence_message_ids),
            "context_reduction": {
                "available_message_count": self.available_message_count,
                "selected_message_count": len(self.messages),
                "omitted_message_count": self.omitted_message_count,
                "omitted_message_ids_sha256": self.omitted_message_ids_sha256,
                "omitted_message_id_sample": list(self.omitted_message_ids[:20]),
            },
            "quality_warnings": list(self.quality_warnings),
            "messages": [message.safe_payload() for message in self.messages],
        }


@dataclass(frozen=True)
class MessageEvidence:
    """Immutable, source-derived evidence snapshot attached after LLM validation."""

    message_id: str
    timestamp: str
    sender_id: str
    excerpt: str
    membership_id: str | None = None
    source_record_keys: tuple[str, ...] = ()
    source_snapshot_keys: tuple[str, ...] = ()
    source_parser_versions: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetricEvidence:
    """Reference to a deterministic A4 metric supplied by the context builder."""

    phase: str
    name: str
    value: float
    analytics_run_id: str | None = None
    analytics_version: str | None = None
    analysis_signature: str | None = None
    source_fingerprint: str | None = None
    processing_run_id: str | None = None


@dataclass(frozen=True)
class EvidenceRef:
    message_ids: tuple[str, ...]
    description: str = ""
    messages: tuple[MessageEvidence, ...] = ()
    metrics: tuple[MetricEvidence, ...] = ()


@dataclass(frozen=True)
class Observation:
    text: str
    evidence: EvidenceRef
    strength: float


@dataclass(frozen=True)
class Interpretation:
    text: str
    evidence_message_ids: tuple[str, ...]
    confidence: float
    evidence: EvidenceRef | None = None


@dataclass(frozen=True)
class Pattern:
    pattern_type: str
    description: str
    occurrences: int | None
    confidence: float
    evidence_message_ids: tuple[str, ...]
    evidence: EvidenceRef | None = None


@dataclass(frozen=True)
class AIAnalysisResult:
    summary: str
    summary_evidence: EvidenceRef
    observations: tuple[Observation, ...] = ()
    interpretations: tuple[Interpretation, ...] = ()
    patterns: tuple[Pattern, ...] = ()
    turning_points: tuple[str, ...] = ()
    turning_point_evidence: tuple[EvidenceRef, ...] = ()
    participant_p1: str | None = None
    participant_p1_evidence: EvidenceRef | None = None
    participant_p2: str | None = None
    participant_p2_evidence: EvidenceRef | None = None
    shared_dynamic: str | None = None
    shared_dynamic_evidence: EvidenceRef | None = None
    alternative_explanations: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    overall_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalysisExecution:
    status: AnalysisStatus
    result: AIAnalysisResult | None
    context_hash: str
    error: str | None = None
