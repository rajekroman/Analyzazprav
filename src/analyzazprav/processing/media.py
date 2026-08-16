from __future__ import annotations

from pathlib import Path

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".tif", ".tiff"}
_VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm"}
_AUDIO_EXTENSIONS = {".m4a", ".aac", ".mp3", ".wav", ".caf", ".aiff", ".flac", ".ogg"}
_DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".rtf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip"}


def classify_media(mime_type: str | None, filename: str | None) -> str:
    mime = (mime_type or "").lower().strip()
    if mime == "image/gif":
        return "gif"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime in {"application/pdf", "text/plain", "text/rtf"}:
        return "document"

    suffix = Path(filename or "").suffix.lower()
    if suffix == ".gif":
        return "gif"
    if suffix in _IMAGE_EXTENSIONS:
        return "image"
    if suffix in _VIDEO_EXTENSIONS:
        return "video"
    if suffix in _AUDIO_EXTENSIONS:
        return "audio"
    if suffix in _DOCUMENT_EXTENSIONS:
        return "document"
    return "other"
