from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Literal

from ..models import MessageRecord

TextMode = Literal["line", "block", "whole"]


class GenericTextParser:
    def __init__(self, path: Path, mode: TextMode):
        self.path = path
        self.mode = mode

    def _chunks(self) -> Iterator[tuple[str, str]]:
        text = self.path.read_text(encoding="utf-8-sig")
        if self.mode == "whole":
            if text:
                yield "document:1", text
            return
        if self.mode == "line":
            for line_number, line in enumerate(text.splitlines(), start=1):
                if line.strip():
                    yield f"line:{line_number}", line
            return
        if self.mode == "block":
            blocks = re.split(r"\n(?:[ \t]*\n)+", text)
            for block_number, block in enumerate(blocks, start=1):
                if block.strip():
                    yield f"block:{block_number}", block
            return
        raise ValueError(f"Unsupported TXT mode: {self.mode}")

    def iter_messages(self) -> Iterator[MessageRecord]:
        for source_id, chunk in self._chunks():
            yield MessageRecord(
                source_message_id=source_id,
                source_guid=None,
                conversation_source_id=self.path.stem,
                timestamp_raw=None,
                timestamp_utc=None,
                timestamp_precision=None,
                sender_handle=None,
                is_from_me=None,
                text=chunk,
                raw_text=chunk,
                text_source=f"txt:{self.mode}",
                service=None,
                raw_payload={"text": chunk},
                metadata={"text_boundary_mode": self.mode},
            )
