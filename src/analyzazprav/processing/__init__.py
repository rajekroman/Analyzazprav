"""A3 deterministic message processing and classification layer."""

from .adapter import load_a2_projection
from .models import (
    A2Projection,
    AttachmentRef,
    CanonicalMessage,
    DuplicateCandidate,
    MessageFeatures,
    MessageRelation,
    ProcessedMessage,
    ProcessingResult,
    SenderRun,
    Session,
    Thread,
)
from .pipeline import PROCESSING_VERSION, ProcessingConfig, process_messages
from .store import ProcessingStore

__all__ = [
    "A2Projection",
    "AttachmentRef",
    "CanonicalMessage",
    "DuplicateCandidate",
    "MessageFeatures",
    "MessageRelation",
    "ProcessedMessage",
    "PROCESSING_VERSION",
    "ProcessingConfig",
    "ProcessingResult",
    "ProcessingStore",
    "SenderRun",
    "Session",
    "Thread",
    "load_a2_projection",
    "process_messages",
]
