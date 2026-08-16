from __future__ import annotations

from .models import CanonicalMessage, MessageFeatures
from .text import clean_text, count_emoji, count_words, has_url, uppercase_ratio


def _gap_seconds(current: CanonicalMessage, previous: CanonicalMessage | None) -> float | None:
    if previous is None or current.timestamp_us is None or previous.timestamp_us is None:
        return None
    return (current.timestamp_us - previous.timestamp_us) / 1_000_000


def build_features(grouped: dict[int, list[CanonicalMessage]]) -> dict[int, MessageFeatures]:
    result: dict[int, MessageFeatures] = {}
    for conversation_id in sorted(grouped):
        previous: CanonicalMessage | None = None
        last_by_sender: dict[int | None, CanonicalMessage] = {}
        for message in grouped[conversation_id]:
            clean = clean_text(message.text)
            others = [candidate for sender, candidate in last_by_sender.items() if sender != message.sender_id]
            previous_other = max(
                (candidate for candidate in others if candidate.timestamp_us is not None),
                key=lambda candidate: candidate.timestamp_us,
                default=None,
            )
            result[message.id] = MessageFeatures(
                char_count=len(clean or ""),
                word_count=count_words(clean),
                line_count=0 if clean is None else clean.count("\n") + 1,
                emoji_count=count_emoji(clean),
                question_mark_count=(clean or "").count("?"),
                exclamation_mark_count=(clean or "").count("!"),
                uppercase_ratio=uppercase_ratio(clean),
                has_question="?" in (clean or ""),
                has_url=has_url(clean),
                has_attachment=bool(message.attachment_keys),
                seconds_since_previous_message=_gap_seconds(message, previous),
                seconds_since_previous_other_sender=_gap_seconds(message, previous_other),
            )
            previous = message
            last_by_sender[message.sender_id] = message
    return result
