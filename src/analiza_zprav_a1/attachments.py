from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from .hashing import sha256_file
from .models import AttachmentRecord


def _is_within(candidate: Path, root: Path) -> bool:
    resolved_candidate = candidate.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    return resolved_candidate == resolved_root or resolved_root in resolved_candidate.parents


def _root_candidate(root: Path, raw_path: str) -> Path | None:
    """Resolve one source path inside an explicit attachment-root sandbox.

    Apple often stores historical absolute/tilde paths under an `Attachments`
    component. For those paths, preserve only the suffix below that component and
    remap it under the user-declared root. Other absolute paths are accepted only
    when they already resolve inside the declared root. Relative paths are always
    interpreted relative to the root, never relative to the process CWD.
    """

    resolved_root = root.resolve(strict=False)
    normalized = raw_path.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    lower = [part.lower() for part in parts]

    candidate: Path
    if "attachments" in lower:
        idx = lower.index("attachments")
        suffix = parts[idx + 1 :]
        if not suffix:
            return None
        candidate = resolved_root.joinpath(*suffix)
    else:
        expanded = Path(os.path.expanduser(raw_path))
        candidate = expanded if expanded.is_absolute() else resolved_root / expanded

    if not _is_within(candidate, resolved_root):
        return None
    return candidate


def resolve_attachment(
    attachment: AttachmentRecord,
    attachments_root: Path | None = None,
) -> AttachmentRecord:
    raw_path = attachment.source_path or attachment.filename
    if not raw_path:
        attachment.resolution_status = "no_path"
        return attachment

    candidates: list[Path] = []
    blocked_by_root = False

    if attachments_root is not None:
        candidate = _root_candidate(attachments_root, raw_path)
        if candidate is None:
            blocked_by_root = True
        else:
            candidates.append(candidate)
    else:
        # Without an explicit sandbox, preserve the historical behavior: an
        # absolute/tilde source path or caller-relative path may be resolved as
        # provided. Supplying --attachments-root opts into confined resolution.
        candidates.append(Path(os.path.expanduser(raw_path)))

    for candidate in candidates:
        if candidate.is_file():
            resolved = candidate.resolve()
            if attachments_root is not None and not _is_within(resolved, attachments_root):
                blocked_by_root = True
                continue
            attachment.resolved_path = str(resolved)
            attachment.resolution_status = "resolved"
            attachment.actual_bytes = resolved.stat().st_size
            attachment.sha256 = sha256_file(resolved)
            return attachment

    attachment.resolution_status = (
        "blocked_outside_root" if blocked_by_root else "missing"
    )
    return attachment


def resolve_attachments(
    attachments: list[AttachmentRecord],
    attachments_root: Path | None = None,
) -> tuple[int, int]:
    resolved = missing = 0
    for attachment in attachments:
        resolve_attachment(attachment, attachments_root)
        if attachment.resolution_status == "resolved":
            resolved += 1
        elif attachment.resolution_status in {"missing", "blocked_outside_root"}:
            missing += 1
    return resolved, missing
