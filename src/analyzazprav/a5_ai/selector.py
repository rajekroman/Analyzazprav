from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Iterable

from .models import AnalysisCandidate, CandidateDecision, CandidateDisposition


class CandidateSelector:
    def __init__(self, suggest_threshold: float = 60.0, analyze_threshold: float = 80.0, merge_overlap_threshold: float = 0.70) -> None:
        if not 0 <= suggest_threshold <= analyze_threshold <= 100:
            raise ValueError("thresholds must satisfy 0 <= suggest <= analyze <= 100")
        if not 0 <= merge_overlap_threshold <= 1:
            raise ValueError("merge_overlap_threshold must be in [0, 1]")
        self.suggest_threshold = suggest_threshold
        self.analyze_threshold = analyze_threshold
        self.merge_overlap_threshold = merge_overlap_threshold

    def decide(self, candidate: AnalysisCandidate) -> CandidateDecision:
        if candidate.manual_request:
            return CandidateDecision(candidate.id, CandidateDisposition.ANALYZE, "manual request overrides score thresholds")
        if candidate.importance_score >= self.analyze_threshold:
            return CandidateDecision(candidate.id, CandidateDisposition.ANALYZE, f"importance_score >= {self.analyze_threshold:g}")
        if candidate.importance_score >= self.suggest_threshold:
            return CandidateDecision(candidate.id, CandidateDisposition.SUGGEST, f"importance_score >= {self.suggest_threshold:g}")
        return CandidateDecision(candidate.id, CandidateDisposition.IGNORE, f"importance_score < {self.suggest_threshold:g}")

    def merge_overlapping(self, candidates: Iterable[AnalysisCandidate]) -> list[AnalysisCandidate]:
        ordered = sorted(candidates, key=lambda c: (c.conversation_id, c.candidate_type, c.start_ts, c.end_ts))
        merged: list[AnalysisCandidate] = []
        for candidate in ordered:
            if not merged:
                merged.append(candidate)
                continue
            previous = merged[-1]
            if self._compatible(previous, candidate) and self._overlap_ratio(previous, candidate) >= self.merge_overlap_threshold:
                merged[-1] = self._merge_pair(previous, candidate)
            else:
                merged.append(candidate)
        return merged

    @staticmethod
    def _compatible(a: AnalysisCandidate, b: AnalysisCandidate) -> bool:
        return a.conversation_id == b.conversation_id and a.candidate_type == b.candidate_type

    @staticmethod
    def _seconds(start: datetime, end: datetime) -> float:
        return max((end - start).total_seconds(), 1.0)

    def _overlap_ratio(self, a: AnalysisCandidate, b: AnalysisCandidate) -> float:
        start = max(a.start_ts, b.start_ts)
        end = min(a.end_ts, b.end_ts)
        if end <= start:
            return 0.0
        overlap = (end - start).total_seconds()
        denominator = min(self._seconds(a.start_ts, a.end_ts), self._seconds(b.start_ts, b.end_ts))
        return overlap / denominator

    @staticmethod
    def _merge_pair(a: AnalysisCandidate, b: AnalysisCandidate) -> AnalysisCandidate:
        raw_id = f"{a.id}|{b.id}|{min(a.start_ts,b.start_ts).isoformat()}|{max(a.end_ts,b.end_ts).isoformat()}"
        merged_id = "merged_" + sha256(raw_id.encode("utf-8")).hexdigest()[:16]
        return AnalysisCandidate(
            id=merged_id,
            conversation_id=a.conversation_id,
            start_ts=min(a.start_ts, b.start_ts),
            end_ts=max(a.end_ts, b.end_ts),
            candidate_type=a.candidate_type,
            importance_score=max(a.importance_score, b.importance_score),
            metrics_before=dict(a.metrics_before) or dict(b.metrics_before),
            metrics_during={**dict(a.metrics_during), **dict(b.metrics_during)},
            metrics_after=dict(b.metrics_after) or dict(a.metrics_after),
            detected_signals=tuple(dict.fromkeys((*a.detected_signals, *b.detected_signals))),
            evidence_message_ids=tuple(dict.fromkeys((*a.evidence_message_ids, *b.evidence_message_ids))),
            manual_request=a.manual_request or b.manual_request,
            metadata={"merged_from": [a.id, b.id]},
        )
