from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, Sequence

from .models import (
    AIAnalysisRequest,
    AnalysisCandidate,
    AnalysisContext,
    AnalysisMode,
    AnalysisType,
    MessageRecord,
)


class MessageSource(Protocol):
    def list_messages(
        self, conversation_id: str, start_ts: datetime, end_ts: datetime
    ) -> Sequence[MessageRecord]:
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
    def __init__(
        self,
        source: MessageSource,
        *,
        max_messages: int = 180,
        evidence_radius: int = 3,
        window_rules: dict[AnalysisType, WindowRule] | None = None,
    ) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be positive")
        if evidence_radius < 0:
            raise ValueError("evidence_radius must be non-negative")
        self.source = source
        self.max_messages = max_messages
        self.evidence_radius = evidence_radius
        self.window_rules = window_rules or DEFAULT_WINDOW_RULES

    def build(
        self,
        request: AIAnalysisRequest,
        candidate: AnalysisCandidate | None = None,
    ) -> AnalysisContext:
        if candidate and candidate.conversation_id != request.conversation_id:
            raise ValueError("candidate and request conversation_id differ")
        rule = self.window_rules[request.analysis_type]
        context_start = request.start_ts - rule.before
        context_end = request.end_ts + rule.after
        cutoff_ts: datetime | None = None
        if request.mode == AnalysisMode.BLIND:
            cutoff_ts = request.end_ts
            context_end = min(context_end, cutoff_ts)

        raw_messages = list(
            self.source.list_messages(
                request.conversation_id, context_start, context_end
            )
        )
        raw_messages = [
            message
            for message in raw_messages
            if message.conversation_id == request.conversation_id
        ]
        if cutoff_ts is not None:
            raw_messages = [
                message for message in raw_messages if message.timestamp <= cutoff_ts
            ]
        raw_messages.sort(key=lambda message: (message.timestamp, message.id))

        ids = [message.id for message in raw_messages]
        if len(ids) != len(set(ids)):
            raise ValueError("message source returned duplicate message IDs in A5 context")

        evidence_ids = tuple(candidate.evidence_message_ids) if candidate else ()
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("candidate contains duplicate evidence message IDs")
        available_ids = set(ids)
        missing_evidence = [
            message_id for message_id in evidence_ids if message_id not in available_ids
        ]
        if missing_evidence:
            raise ValueError(
                "candidate evidence is missing from A5 source/time context: "
                + ", ".join(missing_evidence)
            )
        if len(evidence_ids) > self.max_messages:
            raise ValueError(
                "candidate evidence alone exceeds A5 max_messages; refusing to drop evidence"
            )

        reduced = self._reduce_messages(raw_messages, evidence_ids)
        reduced_ids = {message.id for message in reduced}
        still_missing = [
            message_id for message_id in evidence_ids if message_id not in reduced_ids
        ]
        if still_missing:
            raise RuntimeError(
                "A5 context reduction lost required evidence: "
                + ", ".join(still_missing)
            )

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
            evidence_message_ids=evidence_ids,
            metrics_before=candidate.metrics_before if candidate else {},
            metrics_during=candidate.metrics_during if candidate else {},
            metrics_after=candidate.metrics_after if candidate else {},
            detected_signals=candidate.detected_signals if candidate else (),
        )

    def _reduce_messages(
        self, messages: Sequence[MessageRecord], evidence_ids: Sequence[str]
    ) -> list[MessageRecord]:
        if len(messages) <= self.max_messages:
            return list(messages)

        selected: set[int] = set()
        index_by_id = {message.id: index for index, message in enumerate(messages)}
        protected = {index_by_id[message_id] for message_id in evidence_ids}
        if len(protected) > self.max_messages:
            raise ValueError(
                "candidate evidence alone exceeds A5 max_messages; refusing to drop evidence"
            )

        for message_id in evidence_ids:
            index = index_by_id[message_id]
            for offset in range(-self.evidence_radius, self.evidence_radius + 1):
                position = index + offset
                if 0 <= position < len(messages):
                    selected.add(position)

        selected.update({0, len(messages) - 1})
        if len(selected) > self.max_messages:
            # Evidence is mandatory. Context-neighbor and boundary rows are optional
            # and are admitted deterministically only while capacity remains.
            keep = set(protected)
            for index in sorted(selected):
                if len(keep) >= self.max_messages:
                    break
                keep.add(index)
            selected = keep

        remaining = self.max_messages - len(selected)
        if remaining > 0:
            candidates = [
                index for index in range(len(messages)) if index not in selected
            ]
            if len(candidates) <= remaining:
                selected.update(candidates)
            elif remaining == 1:
                selected.add(candidates[len(candidates) // 2])
            else:
                for slot in range(remaining):
                    position = round(
                        slot * (len(candidates) - 1) / (remaining - 1)
                    )
                    selected.add(candidates[position])

        ordered_indexes = sorted(selected)
        if len(ordered_indexes) > self.max_messages:
            optional = [index for index in ordered_indexes if index not in protected]
            keep = set(protected)
            for index in optional:
                if len(keep) >= self.max_messages:
                    break
                keep.add(index)
            ordered_indexes = sorted(keep)
        return [messages[index] for index in ordered_indexes]
