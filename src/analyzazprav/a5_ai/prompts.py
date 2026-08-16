from __future__ import annotations

import json

from .models import AnalysisContext, AnalysisType


SYSTEM_PROMPT = """You analyze human text communication using an evidence-first method.
Rules:
1. Separate observable facts from interpretation.
2. Every assertion-bearing field must cite message IDs from the supplied context. This includes summary, turning points, participant-specific conclusions and shared-dynamic conclusions.
3. Cite deterministic metrics only by phase/name references that exist in supplied context metrics.
4. Never invent messages, timestamps, senders, excerpts, metrics, events, motives, diagnoses or external facts.
5. Do not infer causality from correlation, response latency, silence, sentiment, or message length alone.
6. Present plausible alternative explanations where evidence is ambiguous.
7. Say what cannot be determined from the supplied messages.
8. Psychological language must remain hypothesis-level; do not diagnose participants.
9. Respect chronology and participant IDs exactly.
10. Confidence means strength of support from supplied communication evidence, not probability that a person's hidden motive is true.
11. Return JSON only, matching the requested structure.
Important: you provide only message IDs and optional metric references. The application resolves timestamps, senders, excerpts and metric values directly from validated source context after your response.
"""

ANALYSIS_INSTRUCTIONS: dict[AnalysisType, str] = {
    AnalysisType.SEGMENT: "Identify the key observable communication changes and cautiously interpret the selected segment.",
    AnalysisType.CHANGE_POINT: "Evaluate whether the messages support the detected statistical change, identify what immediately preceded it, concurrent changes, and alternative explanations. Do not assert causality without direct evidence.",
    AnalysisType.CONFLICT: "Analyze preconditions, trigger, escalation, each participant's responses, repair attempts, withdrawal, resolution or non-resolution, and aftermath.",
    AnalysisType.INTERACTION_CYCLE: "Look for repeated interaction sequences. Only call something recurring when the supplied evidence contains multiple distinct occurrences; report the count when supportable.",
    AnalysisType.LONGITUDINAL: "Compare phases over time, distinguish stable trends from isolated events, and identify evidence-backed turning points.",
    AnalysisType.RELATIONSHIP_DYNAMICS: "Describe reciprocity, initiative, responsiveness, closeness/distance signals, conflict-repair behavior and changes over time without inferring hidden motives as facts.",
    AnalysisType.PSYCHOLOGICAL_HYPOTHESES: "Offer only cautious communication-pattern hypotheses. Explicitly separate behavioral observations from psychological interpretation and state important unknowns. Never diagnose.",
}

METRIC_REF = {"phase": "before|during|after", "name": "metric-name-from-context"}
EVIDENCE = {"message_ids": ["message-id"], "metric_refs": [METRIC_REF], "description": "string"}
CLAIM = {"text": "string", "confidence": 0.0, "evidence": EVIDENCE}
RESULT_SCHEMA_DESCRIPTION = {
    "summary": CLAIM,
    "observations": [{"text": "string", "evidence": EVIDENCE, "strength": 0.0}],
    "interpretations": [{"text": "string", "evidence_message_ids": ["message-id"], "metric_refs": [METRIC_REF], "confidence": 0.0}],
    "patterns": [{"pattern_type": "string", "description": "string", "occurrences": 0, "confidence": 0.0, "evidence_message_ids": ["message-id"], "metric_refs": [METRIC_REF]}],
    "turning_points": [CLAIM],
    "participant_p1": "CLAIM or null",
    "participant_p2": "CLAIM or null",
    "shared_dynamic": "CLAIM or null",
    "alternative_explanations": ["string"],
    "unknowns": ["string"],
    "overall_confidence": 0.0,
}


def build_user_prompt(context: AnalysisContext, user_question: str | None = None) -> str:
    parts = [
        "ANALYSIS TASK:\n" + ANALYSIS_INSTRUCTIONS[context.analysis_type],
        "\nREQUIRED OUTPUT SHAPE:\n" + json.dumps(RESULT_SCHEMA_DESCRIPTION, ensure_ascii=False, indent=2),
    ]
    if user_question:
        parts.append("\nUSER QUESTION:\n" + user_question.strip())
    parts.append("\nSUPPLIED CONTEXT:\n" + json.dumps(context.prompt_payload(), ensure_ascii=False, indent=2))
    return "\n".join(parts)


def build_repair_prompt(original_user_prompt: str, invalid_payload: object, validation_error: str) -> str:
    return (
        original_user_prompt
        + "\n\nREPAIR INSTRUCTION:\n"
        + "Your previous JSON response failed validation. Return a corrected complete JSON object only. "
        + "Do not add new evidence, message IDs or metric references.\n"
        + "Validation error: " + validation_error
        + "\nPrevious invalid response:\n"
        + json.dumps(invalid_payload, ensure_ascii=False, indent=2)
    )
