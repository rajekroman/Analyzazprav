from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, Sequence

from .models import AnalysisCandidate, AnalysisContext, AIAnalysisRequest, AnalysisMode, AnalysisType, MessageRecord


class MessageSource(Protocol):
    def list_messages(self, conversation_id: str, start_ts: datetime, end_ts: datetime) -> Sequence[MessageRecord]:
        ...


@dataclass(frozen=True)
class WindowRule:
    before: timedelta
    after: timedelta


DEFAULT_WINDOW_RULES: dict[AnalysisType, WindowRule] = {
    AnalysisType.CONFLICT: WindowRule(timedelta(hours=24), timedelta(hours=24)),
    AnalysisType.CHANGE_POINT: WindowRule(timedelta(days=14), timedelta(days=7)),
    AnalysisType.INTERACTION_CYCLE: WindowRule(timedelta(days=14), timedelta(days=14)),
    AnalysisType.LONGITUDINAL: WindowRule(timedelta(days=30), timedelta(days=30)),
    AnalysisType.RELATIONSHIP_DYNAMICS: WindowRule(timedelta(days=30), timedelta(days=30)),
    AnalysisType.PSYCHOLOGICAL_HYPOTHESES: WindowRule(timedelta(days=30), timedelta(days=30)),
    AnalysisType.SEGMENT: WindowRule(timedelta(days=1), timedelta(days=1)),
}


class ContextBuilder:
    def __init__(self, source: MessageSource, *, max_messages: int = 180, evidence_radius: int = 3, window_rules: dict[AnalysisType, WindowRule] | None = None) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be positive")
        if evidence_radius < 0:
            raise ValueError("evidence_radius must be non-negative")
        self.source = source
        self.max_messages = max_messages
        self.evidence_radius = evidence_radius
        self.window_rules = window_rules or DEFAULT_WINDOW_RULES

    def build(self, request: AIAnalysisRequest, candidate: AnalysisCandidate | None = None) -> AnalysisContext:
        if candidate and candidate.conversation_id != request.conversation_id:
            raise ValueError("candidate and request conversation_id differ")
        rule = self.window_rules[request.analysis_type]
        context_start = request.start_ts - rule.before
        context_end = request.end_ts + rule.after
        cutoff_ts: datetime | None = None
        if request.mode == AnalysisMode.BLIND:
            cutoff_ts = request.end_ts
            context_end = min(context_end, cutoff_ts)
        raw_messages = list(self.source.list_messages(request.conversation_id, context_start, context_end))
        raw_messages = [m for m in raw_messages if m.conversation_id == request.conversation_id]
        if cutoff_ts is not None:
            raw_messages = [m for m in raw_messages if m.timestamp <= cutoff_ts]
        raw_messages.sort(key=lambda m: (m.timestamp, m.id))
        evidence_ids = tuple(candidate.evidence_message_ids) if candidate else ()
        reduced = self._reduce_messages(raw_messages, evidence_ids)
        reduced_ids = {m.id for m in reduced}
        return AnalysisContext(
            conversation_id=request.conversation_id,
            analysis_type=request.analysis_type,
            mode=request.mode,
            requested_start_ts=request.start_ts,
            requested_end_ts=request.end_ts,
            context_start_ts=context_start,
            context_end_ts=context_end,
            cutoff_ts=cutoff_ts,
            messages=tuple(reduced),
            evidence_message_ids=tuple(mid for mid in evidence_ids if mid in reduced_ids),
            metrics_before=candidate.metrics_before if candidate else {},
            metrics_during=candidate.metrics_during if candidate else {},
            metrics_after=candidate.metrics_after if candidate else {},
            detected_signals=candidate.detected_signals if candidate else (),
        )

    def _reduce_messages(self, messages: Sequence[MessageRecord], evidence_ids: Sequence[str]) -> list[MessageRecord]:
        if len(messages) <= self.max_messages:
            return list(messages)
        selected: set[int] = set()
        index_by_id = {m.id: i for i, m in enumerate(messages)}
        for message_id in evidence_ids:
            idx = index_by_id.get(message_id)
            if idx is None:
                continue
            for offset in range(-self.evidence_radius, self.evidence_radius + 1):
                pos = idx + offset
                if 0 <= pos < len(messages):
                    selected.add(pos)
        selected.update({0, len(messages) - 1})
        remaining = self.max_messages - len(selected)
        if remaining > 0:
            candidates = [i for i in range(len(messages)) if i not in selected]
            if len(candidates) <= remaining:
                selected.update(candidates)
            elif remaining == 1:
                selected.add(candidates[len(candidates) // 2])
            else:
                for slot in range(remaining):
                    pos = round(slot * (len(candidates) - 1) / (remaining - 1))
                    selected.add(candidates[pos])
        ordered_indexes = sorted(selected)
        if len(ordered_indexes) > self.max_messages:
            protected = {index_by_id[mid] for mid in evidence_ids if mid in index_by_id}
            keep = [i for i in ordered_indexes if i in protected]
            for i in ordered_indexes:
                if len(keep) >= self.max_messages:
                    break
                if i not in protected:
                    keep.append(i)
            ordered_indexes = sorted(keep[: self.max_messages])
        return [messages[i] for i in ordered_indexes]
