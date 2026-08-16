from __future__ import annotations

import re
import unicodedata

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_WORD_RE = re.compile(r"\S+")


def clean_text(text: str | None) -> str | None:
    """Remove transport artifacts while preserving communication style signals."""
    if text is None:
        return None
    value = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    value = value.replace("\u00a0", " ")
    value = "".join(ch for ch in value if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    value = "\n".join(line.rstrip(" \t") for line in value.split("\n"))
    return value.strip(" \t\n")


def has_url(text: str | None) -> bool:
    return bool(text and _URL_RE.search(text))


def count_words(text: str | None) -> int:
    return len(_WORD_RE.findall(text or ""))


def count_emoji(text: str | None) -> int:
    if not text:
        return 0
    return sum(
        1
        for ch in text
        if 0x1F300 <= ord(ch) <= 0x1FAFF
        or 0x2600 <= ord(ch) <= 0x26FF
        or 0x2700 <= ord(ch) <= 0x27BF
    )


def uppercase_ratio(text: str | None) -> float:
    letters = [ch for ch in (text or "") if ch.isalpha()]
    return 0.0 if not letters else sum(ch.isupper() for ch in letters) / len(letters)
