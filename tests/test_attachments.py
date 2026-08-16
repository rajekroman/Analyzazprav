import hashlib
from pathlib import Path

from analiza_zprav_a1.attachments import resolve_attachment
from analiza_zprav_a1.models import AttachmentRecord


def test_resolves_apple_attachments_suffix_and_hashes(tmp_path: Path):
    root = tmp_path / "Attachments"
    file_path = root / "ab" / "cd" / "photo.jpg"
    file_path.parent.mkdir(parents=True)
    content = b"attachment-bytes"
    file_path.write_bytes(content)

    record = AttachmentRecord(
        source_attachment_id="22",
        filename="~/Library/Messages/Attachments/ab/cd/photo.jpg",
        mime_type="image/jpeg",
        transfer_name="photo.jpg",
        total_bytes=len(content),
        source_path="~/Library/Messages/Attachments/ab/cd/photo.jpg",
    )

    resolve_attachment(record, root)

    assert record.resolution_status == "resolved"
    assert record.resolved_path == str(file_path.resolve())
    assert record.actual_bytes == len(content)
    assert record.sha256 == hashlib.sha256(content).hexdigest()


def test_missing_attachment_is_explicit(tmp_path: Path):
    record = AttachmentRecord(
        source_attachment_id="1",
        filename="missing.jpg",
        mime_type="image/jpeg",
        transfer_name="missing.jpg",
        total_bytes=None,
        source_path="missing.jpg",
    )
    resolve_attachment(record, tmp_path)
    assert record.resolution_status == "missing"
    assert record.sha256 is None
