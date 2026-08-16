from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator

_LATIN_PRINTABLE = re.compile(r"[\x20-\x7E\u00A0-\u024F\u1E00-\u1EFF]{2,}")
_BLOCKED_TOKENS = (
    "nsattributedstring",
    "nsmutableattributedstring",
    "nsmutablestring",
    "nsstring",
    "nsdictionary",
    "nsmutabledictionary",
    "nsarray",
    "nsobject",
    "streamtyped",
    "__k",
    "messagepart",
    "attribute",
)
_MAX_TEXT_CHARS = 100_000


def _is_semantic_char(char: str) -> bool:
    category = unicodedata.category(char)
    return category[0] in {"L", "N"} or category in {"So", "Sk"}


def _clean_candidate(value: str) -> str | None:
    candidate = value.strip("\ufeff\x00 \t\r\n")
    if not candidate:
        return None
    lowered = candidate.lower()
    if any(token in lowered for token in _BLOCKED_TOKENS):
        return None
    if not any(_is_semantic_char(char) for char in candidate):
        return None
    return candidate


def _unicode_runs(decoded: str) -> Iterator[str]:
    """Yield Unicode text runs separated by binary/control archive material.

    Newline/tab are retained because they can be real message content. Unicode
    format/mark characters are retained so emoji ZWJ/variation sequences are not
    destroyed. Other control/surrogate/unassigned characters terminate a run.
    """

    current: list[str] = []
    for char in decoded:
        category = unicodedata.category(char)
        keep = (
            char in "\n\r\t"
            or char.isprintable()
            or category in {"Cf", "Mn", "Mc", "Me"}
        )
        if keep and char != "\x00":
            current.append(char)
            continue
        if current:
            yield "".join(current)
            current.clear()
    if current:
        yield "".join(current)


def _utf16_is_plausible(data: bytes, encoding: str) -> bool:
    if len(data) < 2:
        return False
    if encoding == "utf-16-le" and data.startswith(b"\xff\xfe"):
        return True
    if encoding == "utf-16-be" and data.startswith(b"\xfe\xff"):
        return True

    sample = data[: min(len(data) - (len(data) % 2), 8192)]
    if not sample:
        return False
    if encoding == "utf-16-le":
        zero_positions = sample[1::2]
    else:
        zero_positions = sample[0::2]

    # Without a BOM, require a strong byte-alignment signal. A permissive gate
    # can turn punctuation/binary bytes into accidental Unicode symbols and thus
    # manufacture message text. This intentionally favors false negatives over
    # false positives; the original BLOB remains available for future decoders.
    return sum(byte == 0 for byte in zero_positions) / max(1, len(zero_positions)) >= 0.40


def _candidate_score(value: str, encoding: str) -> tuple[int, int, int, int]:
    semantic = sum(_is_semantic_char(char) for char in value)
    symbols = sum(unicodedata.category(char) in {"So", "Sk"} for char in value)
    letters_numbers = sum(char.isalpha() or char.isdigit() for char in value)
    # Prefer UTF-8 on ties: arbitrary binary decoded as UTF-16 is more likely to
    # create plausible-looking accidental glyphs.
    encoding_priority = 1 if encoding == "utf-8" else 0
    return semantic, letters_numbers + symbols, min(len(value), _MAX_TEXT_CHARS), encoding_priority


def decode_attributed_body(blob: bytes | memoryview | None) -> str | None:
    """Conservative best-effort fallback for Apple ``attributedBody`` blobs.

    ``message.text`` remains authoritative and is always preferred by the iMessage
    parser. This fallback does not claim to be a full typedstream decoder. It
    extracts plausible textual runs while preserving Unicode letters, scripts and
    emoji, rejects known archive metadata, and returns ``None`` when no defensible
    candidate exists. The original BLOB remains losslessly preserved in
    ``raw_payload`` by the importer.
    """

    if blob is None:
        return None
    data = bytes(blob)
    if not data:
        return None

    candidates: list[tuple[str, str]] = []
    for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
        if encoding.startswith("utf-16") and not _utf16_is_plausible(data, encoding):
            continue
        try:
            decoded = data.decode(encoding, errors="ignore")
        except (LookupError, UnicodeError):
            continue

        # Retain the legacy Latin-oriented extraction because it is conservative
        # and works well for many typedstream blobs.
        for part in _LATIN_PRINTABLE.findall(decoded):
            candidate = _clean_candidate(part)
            if candidate is not None:
                candidates.append((candidate, encoding))

        # UTF-8 is safe enough for Unicode run extraction. Plausible UTF-16 data
        # may use the same path, but only after the byte-alignment gate above.
        for part in _unicode_runs(decoded):
            candidate = _clean_candidate(part)
            if candidate is not None:
                candidates.append((candidate, encoding))

    if not candidates:
        return None

    unique: dict[str, str] = {}
    for candidate, encoding in candidates:
        # If the same text appears through multiple decoders, retain the safer
        # UTF-8 provenance for tie-breaking.
        current = unique.get(candidate)
        if current is None or (current != "utf-8" and encoding == "utf-8"):
            unique[candidate] = encoding

    best_text, best_encoding = max(
        unique.items(),
        key=lambda item: _candidate_score(item[0], item[1]),
    )
    return best_text[:_MAX_TEXT_CHARS]
