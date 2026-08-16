from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from .data import DataSourceError, _connect_read_only, _objects


ATTACHMENT_COLUMNS = [
    "message_id",
    "attachment_id",
    "sha256",
    "mime_type",
    "size_bytes",
    "filename",
    "storage_path",
    "availability",
    "position",
]


def empty_attachments() -> pd.DataFrame:
    return pd.DataFrame(columns=ATTACHMENT_COLUMNS)


def load_message_attachments(path: str | Path, message_ids: Iterable[str]) -> pd.DataFrame:
    """Read canonical A2 attachment metadata for selected messages.

    A6 consumes only the published analysis_attachments view. Missing view means
    the source has no compatible attachment projection; query failures are not
    hidden. No attachment file is opened or modified here.
    """

    requested = tuple(dict.fromkeys(str(value) for value in message_ids))
    if not requested:
        return empty_attachments()

    frames: list[pd.DataFrame] = []
    try:
        with _connect_read_only(path) as conn:
            if "analysis_attachments" not in set(_objects(conn)):
                return empty_attachments()
            for offset in range(0, len(requested), 500):
                batch = requested[offset : offset + 500]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    "SELECT message_id, attachment_id, sha256, mime_type, size_bytes, filename, "
                    "storage_path, availability, position FROM analysis_attachments "
                    f"WHERE CAST(message_id AS TEXT) IN ({placeholders})"
                )
                frames.append(pd.read_sql_query(query, conn, params=batch))
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Chyba při čtení A2 příloh: {exc}") from exc

    if not frames:
        return empty_attachments()
    result = pd.concat(frames, ignore_index=True)
    if result.empty:
        return empty_attachments()

    result["message_id"] = result["message_id"].astype(str)
    order = {message_id: index for index, message_id in enumerate(requested)}
    result["_message_order"] = result["message_id"].map(order)
    position = pd.to_numeric(result["position"], errors="coerce")
    result["_position_order"] = position.fillna(10**12)
    result = result.sort_values(["_message_order", "_position_order", "attachment_id"], kind="stable")
    result = result.drop(columns=["_message_order", "_position_order"])
    return result[ATTACHMENT_COLUMNS].reset_index(drop=True)
