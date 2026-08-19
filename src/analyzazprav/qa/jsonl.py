from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path


def iter_physical_jsonl_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Yield JSONL records using physical LF as the only record delimiter.

    Unicode line/paragraph separators (U+2028/U+2029) are valid JSON string
    content and must never be interpreted as record boundaries.
    """

    with path.open("r", encoding="utf-8", newline="\n") as handle:
        for line_number, raw in enumerate(handle, start=1):
            yield line_number, raw
