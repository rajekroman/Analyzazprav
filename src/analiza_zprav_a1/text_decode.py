from __future__ import annotations
import re

_PRINTABLE = re.compile(r"[\x20-\x7E\u00A0-\u024F\u1E00-\u1EFF]{2,}")


def decode_attributed_body(blob: bytes | memoryview | None) -> str | None:
    """Best-effort fallback for Apple attributedBody typedstream.

    The authoritative text column is preferred. This function deliberately avoids
    third-party parsers; it extracts plausible UTF-8/UTF-16 text fragments from the
    archived blob and rejects common archive class names/metadata.
    """
    if blob is None:
        return None
    data = bytes(blob)
    candidates: list[str] = []
    for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
        try:
            decoded = data.decode(encoding, errors="ignore")
        except Exception:
            continue
        for part in _PRINTABLE.findall(decoded):
            p = part.strip("\x00 \t\r\n")
            if len(p) < 2:
                continue
            low = p.lower()
            if any(token in low for token in (
                "nsattributedstring", "nsmutablestring", "nsstring", "nsdictionary",
                "streamtyped", "__k", "messagepart", "attribute"
            )):
                continue
            if sum(ch.isalpha() or ch.isdigit() for ch in p) < 2:
                continue
            candidates.append(p)
    if not candidates:
        return None
    candidates = sorted(set(candidates), key=lambda s: (sum(c.isalpha() for c in s), len(s)), reverse=True)
    return candidates[0][:100_000]
