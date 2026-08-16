from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .models import AIAnalysisRequest, AnalysisCandidate, AnalysisMode, AnalysisType, MessageRecord


class A6PacketError(ValueError):
    pass


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise A6PacketError("A6 message timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise A6PacketError(f"Invalid A6 message timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise A6PacketError("A6 message timestamp must include timezone information")
    return parsed


def messages_from_a6_packet(packet: Mapping[str, Any]) -> tuple[MessageRecord, ...]:
    if packet.get("schema_version") != 1:
        raise A6PacketError("Unsupported A6 analysis_packet schema_version")
    raw_messages = packet.get("messages")
    if not isinstance(raw_messages, list):
        raise A6PacketError("A6 analysis_packet.messages must be a list")
    messages: list[MessageRecord] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_messages):
        if not isinstance(raw, Mapping):
            raise A6PacketError(f"A6 messages[{index}] must be an object")
        try:
            message_id = str(raw["message_id"])
            conversation_id = str(raw["conversation_id"])
            sender = str(raw["sender"])
            text = str(raw.get("text") or "")
            timestamp = _parse_timestamp(raw["timestamp"])
        except KeyError as exc:
            raise A6PacketError(f"A6 messages[{index}] missing field: {exc.args[0]}") from exc
        if message_id in seen_ids:
            raise A6PacketError(f"Duplicate A6 message_id in packet context: {message_id}")
        seen_ids.add(message_id)
        messages.append(MessageRecord(
            id=message_id,
            conversation_id=conversation_id,
            participant_id=sender,
            timestamp=timestamp,
            text=text,
        ))
    messages.sort(key=lambda m: (m.timestamp, m.id))
    return tuple(messages)


@dataclass
class A6PacketMessageSource:
    messages: tuple[MessageRecord, ...]

    @classmethod
    def from_packet(cls, packet: Mapping[str, Any]) -> "A6PacketMessageSource":
        return cls(messages_from_a6_packet(packet))

    def list_messages(self, conversation_id: str, start_ts: datetime, end_ts: datetime) -> Sequence[MessageRecord]:
        return [
            message
            for message in self.messages
            if message.conversation_id == str(conversation_id)
            and start_ts <= message.timestamp <= end_ts
        ]


def candidate_from_a6_packet(packet: Mapping[str, Any]) -> AnalysisCandidate:
    messages = messages_from_a6_packet(packet)
    selected_raw = packet.get("selected_message_ids")
    if not isinstance(selected_raw, list) or not selected_raw:
        raise A6PacketError("A6 analysis_packet must contain selected_message_ids")
    selected_ids = tuple(str(value) for value in selected_raw)
    if len(set(selected_ids)) != len(selected_ids):
        raise A6PacketError("A6 selected_message_ids must not contain duplicates")
    by_id = {message.id: message for message in messages}
    missing = [message_id for message_id in selected_ids if message_id not in by_id]
    if missing:
        raise A6PacketError(f"Selected A6 message IDs are missing from packet context: {missing}")
    selected_messages = [by_id[message_id] for message_id in selected_ids]
    conversations = {message.conversation_id for message in selected_messages}
    if len(conversations) != 1:
        raise A6PacketError("A6 manual selection must belong to exactly one conversation")
    selected_messages.sort(key=lambda m: (m.timestamp, m.id))
    conversation_id = next(iter(conversations))
    return AnalysisCandidate(
        id="a6-manual:" + ",".join(selected_ids),
        conversation_id=conversation_id,
        start_ts=selected_messages[0].timestamp,
        end_ts=selected_messages[-1].timestamp,
        candidate_type="manual_selection",
        importance_score=100.0,
        detected_signals=("manual_selection",),
        evidence_message_ids=selected_ids,
        manual_request=True,
        metadata={
            "source": "a6",
            "schema_version": 1,
            "context_before": int(packet.get("context_before", 0) or 0),
            "context_after": int(packet.get("context_after", 0) or 0),
        },
    )


def request_from_a6_packet(
    packet: Mapping[str, Any],
    *,
    analysis_type: AnalysisType = AnalysisType.SEGMENT,
    mode: AnalysisMode = AnalysisMode.BLIND,
    user_question: str | None = None,
) -> AIAnalysisRequest:
    candidate = candidate_from_a6_packet(packet)
    return AIAnalysisRequest(
        conversation_id=candidate.conversation_id,
        analysis_type=analysis_type,
        start_ts=candidate.start_ts,
        end_ts=candidate.end_ts,
        mode=mode,
        candidate_id=candidate.id,
        user_question=user_question,
    )
