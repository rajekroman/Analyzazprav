from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from .data import DataSourceError, _connect_read_only, _objects


ATTACHMENT_COLUMNS = [
    "occurrence_id",
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

ATTACHMENT_SOURCE_COLUMNS = [
    "attachment_source_id",
    "attachment_id",
    "occurrence_id",
    "message_id",
    "position",
    "import_run_id",
    "source_type",
    "source_snapshot_key",
    "source_sha256",
    "parser_version",
    "source_attachment_id",
    "source_occurrence_key",
    "original_filename",
    "original_path",
]


def empty_attachments() -> pd.DataFrame:
    return pd.DataFrame(columns=ATTACHMENT_COLUMNS)


def empty_attachment_sources() -> pd.DataFrame:
    return pd.DataFrame(columns=ATTACHMENT_SOURCE_COLUMNS)


def load_message_attachments(path: str | Path, message_ids: Iterable[str]) -> pd.DataFrame:
    """Read canonical A2 attachment occurrences for selected messages."""
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
                    "SELECT occurrence_id, message_id, attachment_id, sha256, mime_type, size_bytes, filename, "
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

    result["occurrence_id"] = result["occurrence_id"].astype(str)
    result["message_id"] = result["message_id"].astype(str)
    result["attachment_id"] = result["attachment_id"].astype(str)
    order = {message_id: index for index, message_id in enumerate(requested)}
    result["_message_order"] = result["message_id"].map(order)
    position = pd.to_numeric(result["position"], errors="coerce")
    result["_position_order"] = position.fillna(10**12)
    result = result.sort_values(["_message_order", "_position_order", "attachment_id"], kind="stable")
    result = result.drop(columns=["_message_order", "_position_order"])
    return result[ATTACHMENT_COLUMNS].reset_index(drop=True)


def load_attachment_sources(path: str | Path, occurrence_ids: Iterable[str]) -> pd.DataFrame:
    """Read stable A2 attachment-source provenance when schema v6 view exists.

    The view is optional until A2 PR #27 is promoted. A6 never falls back to
    the physical ``attachment_source`` table because that would bypass the
    published downstream contract.
    """
    requested = tuple(dict.fromkeys(str(value) for value in occurrence_ids))
    if not requested:
        return empty_attachment_sources()

    frames: list[pd.DataFrame] = []
    try:
        with _connect_read_only(path) as conn:
            if "analysis_attachment_sources" not in set(_objects(conn)):
                return empty_attachment_sources()
            for offset in range(0, len(requested), 500):
                batch = requested[offset : offset + 500]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    "SELECT attachment_source_id, attachment_id, occurrence_id, message_id, position, "
                    "import_run_id, source_type, source_snapshot_key, source_sha256, parser_version, "
                    "source_attachment_id, source_occurrence_key, original_filename, original_path "
                    "FROM analysis_attachment_sources "
                    f"WHERE CAST(occurrence_id AS TEXT) IN ({placeholders})"
                )
                frames.append(pd.read_sql_query(query, conn, params=batch))
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Chyba při čtení A2 provenance příloh: {exc}") from exc

    if not frames:
        return empty_attachment_sources()
    result = pd.concat(frames, ignore_index=True)
    if result.empty:
        return empty_attachment_sources()

    for column in ("attachment_source_id", "attachment_id", "occurrence_id", "message_id"):
        result[column] = result[column].astype(str)
    order = {occurrence_id: index for index, occurrence_id in enumerate(requested)}
    result["_occurrence_order"] = result["occurrence_id"].map(order)
    result = result.sort_values(["_occurrence_order", "attachment_source_id"], kind="stable")
    result = result.drop(columns=["_occurrence_order"])
    return result[ATTACHMENT_SOURCE_COLUMNS].reset_index(drop=True)
