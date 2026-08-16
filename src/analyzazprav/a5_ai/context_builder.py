from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from typing import Protocol, Sequence

from .models import (
    AnalysisCandidate,
    AnalysisContext,
    AIAnalysisRequest,
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
                request.conversation_id,
                context_start,
                context_end,
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

        evidence_ids = tuple(dict.fromkeys(candidate.evidence_message_ids)) if candidate else ()
        raw_ids = {message.id for message in raw_messages}
        missing_evidence_ids = tuple(
            message_id for message_id in evidence_ids if message_id not in raw_ids
        )
        reduced = self._reduce_messages(raw_messages, evidence_ids)
        reduced_keys = {(message.id, message.membership_id) for message in reduced}
        omitted = [
            message
            for message in raw_messages
            if (message.id, message.membership_id) not in reduced_keys
        ]
        omitted_ids = tuple(message.id for message in omitted)
        omitted_hash = None
        if omitted_ids:
            encoded = json.dumps(
                omitted_ids,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            omitted_hash = hashlib.sha256(encoded).hexdigest()

        reduced_ids = {message.id for message in reduced}
        quality_warnings: list[str] = []
        warning_source = getattr(self.source, "context_warnings", None)
        if callable(warning_source):
            quality_warnings.extend(
                str(item)
                for item in warning_source(
                    request.conversation_id,
                    context_start,
                    context_end,
                )
                if str(item).strip()
            )
        if omitted_ids:
            quality_warnings.append(
                f"A5 context reduction omitted {len(omitted_ids)} of "
                f"{len(raw_messages)} timestamped messages in the context window."
            )
        if len(reduced) > self.max_messages:
            quality_warnings.append(
                f"A5 selected {len(reduced)} messages although max_messages={self.max_messages} "
                "because candidate evidence is never silently removed."
            )
        if missing_evidence_ids:
            quality_warnings.append(
                "Candidate evidence is unavailable in the temporal/source context: "
                + ", ".join(missing_evidence_ids[:20])
            )

        selected_evidence_ids = tuple(
            message_id for message_id in evidence_ids if message_id in reduced_ids
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
            evidence_message_ids=selected_evidence_ids,
            metrics_before=candidate.metrics_before if candidate else {},
            metrics_during=candidate.metrics_during if candidate else {},
            metrics_after=candidate.metrics_after if candidate else {},
            detected_signals=candidate.detected_signals if candidate else (),
            candidate_provenance=dict(candidate.metadata) if candidate else {},
            available_message_count=len(raw_messages),
            omitted_message_count=len(omitted_ids),
            omitted_message_ids=omitted_ids,
            omitted_message_ids_sha256=omitted_hash,
            missing_evidence_message_ids=missing_evidence_ids,
            quality_warnings=tuple(dict.fromkeys(quality_warnings)),
        )

    def _reduce_messages(
        self,
        messages: Sequence[MessageRecord],
        evidence_ids: Sequence[str],
    ) -> list[MessageRecord]:
        if len(messages) <= self.max_messages:
            return list(messages)

        index_by_id: dict[str, list[int]] = {}
        for index, message in enumerate(messages):
            index_by_id.setdefault(message.id, []).append(index)

        protected: set[int] = set()
        selected: set[int] = set()
        for message_id in evidence_ids:
            for index in index_by_id.get(message_id, ()):
                protected.add(index)
                for offset in range(-self.evidence_radius, self.evidence_radius + 1):
                    position = index + offset
                    if 0 <= position < len(messages):
                        selected.add(position)
        selected.update(protected)
        selected.update({0, len(messages) - 1})

        # Evidence itself is an absolute constraint. If evidence alone exceeds
        # the nominal context limit, keep all evidence and let the caller report
        # the over-limit condition instead of silently discarding provenance.
        target_size = max(self.max_messages, len(protected))
        remaining = target_size - len(selected)
        if remaining > 0:
            candidates = [index for index in range(len(messages)) if index not in selected]
            if len(candidates) <= remaining:
                selected.update(candidates)
            elif remaining == 1:
                selected.add(candidates[len(candidates) // 2])
            else:
                for slot in range(remaining):
                    position = round(slot * (len(candidates) - 1) / (remaining - 1))
                    selected.add(candidates[position])

        if len(selected) > target_size:
            # Trim only non-evidence rows, preferring deterministic chronology.
            keep = set(protected)
            for index in sorted(selected):
                if len(keep) >= target_size:
                    break
                keep.add(index)
            selected = keep

        return [messages[index] for index in sorted(selected)]
