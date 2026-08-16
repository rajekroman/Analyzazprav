"""A3 deterministic message processing and classification layer."""

from .adapter import load_a2_projection
from .models import (
    A2Projection,
    AttachmentRef,
    CanonicalMessage,
    CanonicalParticipant,
    DuplicateCandidate,
    MessageFeatures,
    MessageRelation,
    ParticipantAlias,
    ParticipantIdentity,
    ParticipantResolutionCandidate,
    ProcessedMessage,
    ProcessingResult,
    ResolvedParticipant,
    SenderRun,
    Session,
    Thread,
)
from .participants import resolve_participants
from .pipeline import PROCESSING_VERSION, ProcessingConfig, process_messages
from .store import ProcessingStore

__all__ = [
    "A2Projection",
    "AttachmentRef",
    "CanonicalMessage",
    "CanonicalParticipant",
    "DuplicateCandidate",
    "MessageFeatures",
    "MessageRelation",
    "ParticipantAlias",
    "ParticipantIdentity",
    "ParticipantResolutionCandidate",
    "ProcessedMessage",
    "PROCESSING_VERSION",
    "ProcessingConfig",
    "ProcessingResult",
    "ProcessingStore",
    "ResolvedParticipant",
    "SenderRun",
    "Session",
    "Thread",
    "load_a2_projection",
    "process_messages",
    "resolve_participants",
]
