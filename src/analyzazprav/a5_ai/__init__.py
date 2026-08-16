"""A5: selective, evidence-backed AI analysis for Analyzazprav."""

from .analyzer import AIAnalyzer
from .cache import AnalysisCache
from .context_builder import ContextBuilder, MessageSource
from .integration_a2 import A2SQLiteMessageSource, A2SourceError
from .integration_a4 import (
    A4MessageSource,
    candidate_from_a4_change_point,
    candidate_from_a4_conflict,
    candidate_from_a4_engagement,
    candidate_from_a4_regime,
    candidate_from_a4_topic,
    message_from_a4,
)
from .integration_a4_sqlite import A4SQLiteCandidateSource, A4SQLiteSourceError
from .integration_a6 import (
    A6PacketError,
    A6PacketMessageSource,
    candidate_from_a6_packet,
    messages_from_a6_packet,
    request_from_a6_packet,
)
from .models import (
    AIAnalysisRequest,
    AIAnalysisResult,
    AnalysisCandidate,
    AnalysisContext,
    AnalysisExecution,
    AnalysisMode,
    AnalysisStatus,
    AnalysisType,
    CandidateDisposition,
    EvidenceRef,
    MessageEvidence,
    MessageRecord,
    MetricEvidence,
)
from .selector import CandidateSelector

__all__ = [
    "AIAnalyzer", "AnalysisCache", "ContextBuilder", "MessageSource",
    "A2SQLiteMessageSource", "A2SourceError", "A4MessageSource",
    "A4SQLiteCandidateSource", "A4SQLiteSourceError",
    "candidate_from_a4_change_point", "candidate_from_a4_conflict",
    "candidate_from_a4_engagement", "candidate_from_a4_regime",
    "candidate_from_a4_topic", "message_from_a4",
    "A6PacketError", "A6PacketMessageSource", "candidate_from_a6_packet",
    "messages_from_a6_packet", "request_from_a6_packet",
    "AIAnalysisRequest", "AIAnalysisResult", "AnalysisCandidate", "AnalysisContext",
    "AnalysisExecution", "AnalysisMode", "AnalysisStatus", "AnalysisType",
    "CandidateDisposition", "EvidenceRef", "MessageEvidence", "MetricEvidence",
    "MessageRecord", "CandidateSelector",
]
