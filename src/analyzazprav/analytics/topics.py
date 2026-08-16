from __future__ import annotations

from collections import Counter, defaultdict
import math
import re
from typing import Iterable, Sequence
import unicodedata

from .config import AnalyticsConfig
from .models import AnalyticMessage, TopicCandidate, TopicEvidence

TOPIC_METHOD = "lexical_ngram_v1"

# Deliberately small and explicit. This is not a language model; it only removes
# high-frequency function words that would otherwise dominate lexical candidates.
DEFAULT_TOPIC_STOPWORDS = frozenset(
    {
        # Czech
        "a", "i", "ale", "že", "se", "si", "to", "je", "jsem", "jsme", "jsi",
        "jste", "na", "do", "v", "ve", "z", "ze", "s", "za", "pro", "od", "o",
        "u", "k", "ke", "co", "jak", "tak", "už", "jen", "ne", "ano", "jo", "no",
        "ten", "ta", "ty", "mi", "mě", "ti", "tě", "ho", "ji", "mu", "nám", "vám",
        "oni", "ona", "on",
        # English
        "the", "and", "or", "but", "a", "an", "to", "of", "in", "on", "for", "with",
        "is", "are", "was", "were", "be", "been", "it", "this", "that", "i", "you",
        "we", "he", "she", "they", "my", "your", "our", "do", "did", "not", "yes",
        "no",
    }
)

_TOKEN_RE = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)


def tokenize_topic_text(text: str) -> tuple[str, ...]:
    """Normalize text without stemming or synonym expansion.

    NFKC + casefold is deterministic and preserves Czech diacritics. Tokens may
    contain digits, but a candidate must still contain at least one alphabetic
    character so timestamps/IDs do not become topics by themselves.
    """

    normalized = unicodedata.normalize("NFKC", text.casefold())
    return tuple(_TOKEN_RE.findall(normalized))


def _valid_ngram(parts: Sequence[str], config: AnalyticsConfig) -> bool:
    if not parts:
        return False
    if parts[0] in DEFAULT_TOPIC_STOPWORDS or parts[-1] in DEFAULT_TOPIC_STOPWORDS:
        return False

    content = [token for token in parts if token not in DEFAULT_TOPIC_STOPWORDS]
    if not content:
        return False
    if not any(any(char.isalpha() for char in token) for token in content):
        return False
    return all(
        len(token) >= config.topic_min_token_length or any(char.isdigit() for char in token)
        for token in content
    )


def _message_ngram_counts(
    message: AnalyticMessage, config: AnalyticsConfig
) -> Counter[tuple[int, str]]:
    tokens = tokenize_topic_text(message.text_clean)
    counts: Counter[tuple[int, str]] = Counter()
    for ngram_size in range(config.topic_min_ngram_size, config.topic_max_ngram_size + 1):
        if len(tokens) < ngram_size:
            continue
        for start in range(0, len(tokens) - ngram_size + 1):
            parts = tokens[start : start + ngram_size]
            if not _valid_ngram(parts, config):
                continue
            counts[(ngram_size, " ".join(parts))] += 1
    return counts


def build_lexical_topic_candidates(
    messages: Iterable[AnalyticMessage],
    config: AnalyticsConfig | None = None,
) -> tuple[list[TopicCandidate], list[TopicEvidence]]:
    """Discover lexical n-gram candidates with message-level provenance.

    This intentionally does *not* merge synonyms, infer latent semantic topics,
    or assign psychological meaning. It reports recurring lexical evidence only.
    """

    cfg = config or AnalyticsConfig()
    source = sorted(messages, key=lambda item: (item.sequence_number, item.message_id))
    if not source:
        return [], []
    conversation_ids = {message.conversation_id for message in source}
    if len(conversation_ids) != 1:
        raise ValueError("build_lexical_topic_candidates expects exactly one conversation")

    per_message: dict[int, Counter[tuple[int, str]]] = {}
    message_by_id: dict[int, AnalyticMessage] = {}
    text_document_count = 0

    for message in source:
        tokens = tokenize_topic_text(message.text_clean)
        if not tokens:
            continue
        text_document_count += 1
        counts = _message_ngram_counts(message, cfg)
        per_message[message.message_id] = counts
        message_by_id[message.message_id] = message

    if text_document_count == 0:
        return [], []

    document_ids_by_topic: dict[tuple[int, str], set[int]] = defaultdict(set)
    occurrence_count_by_topic: Counter[tuple[int, str]] = Counter()
    for message_id, counts in per_message.items():
        for topic, count in counts.items():
            document_ids_by_topic[topic].add(message_id)
            occurrence_count_by_topic[topic] += count

    ranked: list[tuple[float, int, int, str, tuple[int, ...], int]] = []
    for (ngram_size, phrase), message_ids in document_ids_by_topic.items():
        document_frequency = len(message_ids)
        if document_frequency < cfg.topic_min_document_frequency:
            continue
        document_frequency_ratio = document_frequency / text_document_count
        if document_frequency_ratio > cfg.topic_max_document_frequency_ratio:
            continue

        # Smoothed IDF keeps a dominant but meaningful topic visible while still
        # rewarding phrases that are not present in every message.
        idf = math.log((text_document_count + 1) / (document_frequency + 1)) + 1.0
        specificity = 1.0 + 0.15 * (ngram_size - 1)
        salience = document_frequency * idf * specificity
        ranked.append(
            (
                salience,
                document_frequency,
                ngram_size,
                phrase,
                tuple(sorted(message_ids)),
                occurrence_count_by_topic[(ngram_size, phrase)],
            )
        )

    ranked.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3]))
    selected = ranked[: cfg.topic_max_candidates]

    candidates: list[TopicCandidate] = []
    evidence: list[TopicEvidence] = []
    conversation_id = source[0].conversation_id

    for (
        salience,
        document_frequency,
        ngram_size,
        phrase,
        source_message_ids,
        occurrence_count,
    ) in selected:
        topic_key = f"{TOPIC_METHOD}:{ngram_size}:{phrase}"
        participants = {
            message_by_id[message_id].participant_id
            for message_id in source_message_ids
            if message_by_id[message_id].participant_id is not None
        }
        dates = sorted(
            {
                message_by_id[message_id].period_date
                for message_id in source_message_ids
                if message_by_id[message_id].period_date is not None
            }
        )
        candidates.append(
            TopicCandidate(
                conversation_id=conversation_id,
                topic_key=topic_key,
                method=TOPIC_METHOD,
                normalized_phrase=phrase,
                ngram_size=ngram_size,
                document_frequency=document_frequency,
                document_frequency_ratio=round(
                    document_frequency / text_document_count, 6
                ),
                occurrence_count=occurrence_count,
                participant_count=len(participants),
                salience=round(salience, 6),
                first_period_date=dates[0] if dates else None,
                last_period_date=dates[-1] if dates else None,
                source_message_ids=source_message_ids,
            )
        )
        for message_id in source_message_ids:
            message = message_by_id[message_id]
            count = per_message[message_id][(ngram_size, phrase)]
            evidence.append(
                TopicEvidence(
                    conversation_id=conversation_id,
                    topic_key=topic_key,
                    message_id=message_id,
                    participant_id=message.participant_id,
                    period_date=message.period_date,
                    date_basis=message.period_basis,
                    occurrence_count=count,
                )
            )

    evidence.sort(key=lambda row: (row.topic_key, row.message_id))
    return candidates, evidence
