from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from .hashing import sha256_file
from .models import AttachmentRecord


def _root_candidate(root: Path, raw_path: str) -> Path | None:
    normalized = raw_path.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    lower = [part.lower() for part in parts]
    if "attachments" in lower:
        idx = lower.index("attachments")
        suffix = parts[idx + 1 :]
        if suffix:
            return root.joinpath(*suffix)
    path = Path(normalized)
    if not path.is_absolute() and not normalized.startswith("~"):
        return root / path
    return None


def resolve_attachment(attachment: AttachmentRecord, attachments_root: Path | None = None) -> AttachmentRecord:
    raw_path = attachment.source_path or attachment.filename
    if not raw_path:
        attachment.resolution_status = "no_path"
        return attachment

    candidates: list[Path] = []
    expanded = Path(os.path.expanduser(raw_path))
    candidates.append(expanded)
    if attachments_root is not None:
        candidate = _root_candidate(attachments_root, raw_path)
        if candidate is not None and candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        if candidate.is_file():
            resolved = candidate.resolve()
            attachment.resolved_path = str(resolved)
            attachment.resolution_status = "resolved"
            attachment.actual_bytes = resolved.stat().st_size
            attachment.sha256 = sha256_file(resolved)
            return attachment

    attachment.resolution_status = "missing"
    return attachment


def resolve_attachments(
    attachments: list[AttachmentRecord], attachments_root: Path | None = None
) -> tuple[int, int]:
    resolved = missing = 0
    for attachment in attachments:
        resolve_attachment(attachment, attachments_root)
        if attachment.resolution_status == "resolved":
            resolved += 1
        elif attachment.resolution_status == "missing":
            missing += 1
    return resolved, missing
