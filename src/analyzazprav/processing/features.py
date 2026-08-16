from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import CanonicalMessage, MessageFeatures, MessageOccurrenceKey
from .text import clean_text, count_emoji, count_words, has_url, uppercase_ratio


def _gap_seconds(current: CanonicalMessage, previous: CanonicalMessage | None) -> float | None:
    if previous is None or current.timestamp_us is None or previous.timestamp_us is None:
        return None
    return (current.timestamp_us - previous.timestamp_us) / 1_000_000


def _calendar_parts(message: CanonicalMessage) -> tuple[int | None, ...]:
    if message.timestamp_us is None:
        return (None,) * 10
    utc_dt = datetime.fromtimestamp(message.timestamp_us / 1_000_000, tz=timezone.utc)
    utc_parts = (utc_dt.year, utc_dt.month, utc_dt.day, utc_dt.weekday(), utc_dt.hour)
    if message.timezone_offset_min is None:
        return utc_parts + (None,) * 5
    local_dt = utc_dt + timedelta(minutes=message.timezone_offset_min)
    local_parts = (local_dt.year, local_dt.month, local_dt.day, local_dt.weekday(), local_dt.hour)
    return utc_parts + local_parts


def build_features(
    grouped: dict[int, list[CanonicalMessage]],
    *,
    resolved_sender_map: dict[int, int] | None = None,
) -> dict[MessageOccurrenceKey, MessageFeatures]:
    result: dict[MessageOccurrenceKey, MessageFeatures] = {}
    resolved_sender_map = resolved_sender_map or {}

    def sender_key(message: CanonicalMessage) -> int | None:
        if message.sender_id is None:
            return None
        return resolved_sender_map.get(message.sender_id, message.sender_id)

    for conversation_id in sorted(grouped):
        previous: CanonicalMessage | None = None
        last_by_sender: dict[int | None, CanonicalMessage] = {}
        for message in grouped[conversation_id]:
            clean = clean_text(message.text)
            current_sender = sender_key(message)
            others = [
                candidate
                for sender, candidate in last_by_sender.items()
                if sender != current_sender
            ]
            previous_other = max(
                (candidate for candidate in others if candidate.timestamp_us is not None),
                key=lambda candidate: candidate.timestamp_us,
                default=None,
            )
            media_counts = {
                kind: 0 for kind in ("image", "gif", "video", "audio", "document", "other")
            }
            for attachment in message.attachments:
                media_counts[attachment.media_type] = (
                    media_counts.get(attachment.media_type, 0) + 1
                )
            calendar = _calendar_parts(message)
            result[message.occurrence_key] = MessageFeatures(
                char_count=len(clean or ""),
                word_count=count_words(clean),
                line_count=0 if clean is None else clean.count("\n") + 1,
                emoji_count=count_emoji(clean),
                question_mark_count=(clean or "").count("?"),
                exclamation_mark_count=(clean or "").count("!"),
                uppercase_ratio=uppercase_ratio(clean),
                has_question="?" in (clean or ""),
                has_url=has_url(clean),
                has_attachment=bool(message.attachments),
                attachment_count=len(message.attachments),
                image_count=media_counts["image"],
                gif_count=media_counts["gif"],
                video_count=media_counts["video"],
                audio_count=media_counts["audio"],
                document_count=media_counts["document"],
                other_media_count=media_counts["other"],
                missing_attachment_count=sum(
                    a.availability == "missing" for a in message.attachments
                ),
                seconds_since_previous_message=_gap_seconds(message, previous),
                seconds_since_previous_other_sender=_gap_seconds(message, previous_other),
                utc_year=calendar[0],
                utc_month=calendar[1],
                utc_day=calendar[2],
                utc_weekday=calendar[3],
                utc_hour=calendar[4],
                local_year=calendar[5],
                local_month=calendar[6],
                local_day=calendar[7],
                local_weekday=calendar[8],
                local_hour=calendar[9],
            )
            previous = message
            last_by_sender[current_sender] = message
    return result
