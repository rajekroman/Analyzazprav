from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from .data import DataSourceError, _connect_read_only, _objects


PROVENANCE_COLUMNS = [
    "message_id",
    "source_type",
    "source_message_id",
    "source_conversation_id",
    "source_row_id",
    "source_record_key",
    "source_contract_version",
    "raw_timestamp",
    "raw_text",
    "source_hash",
    "import_run_id",
]


def empty_provenance() -> pd.DataFrame:
    return pd.DataFrame(columns=PROVENANCE_COLUMNS)


def load_message_sources(path: str | Path, message_ids: Iterable[str]) -> pd.DataFrame:
    """Resolve canonical message IDs to A2 source records read-only.

    Compatible non-A2 SQLite sources may not expose analysis_message_sources;
    in that case provenance is simply unavailable and an empty frame is
    returned. A2 query failures are surfaced rather than hidden.
    """

    requested = tuple(dict.fromkeys(str(value) for value in message_ids))
    if not requested:
        return empty_provenance()

    frames: list[pd.DataFrame] = []
    try:
        with _connect_read_only(path) as conn:
            if "analysis_message_sources" not in set(_objects(conn)):
                return empty_provenance()
            for offset in range(0, len(requested), 500):
                batch = requested[offset : offset + 500]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    "SELECT message_id, source_type, source_message_id, source_conversation_id, "
                    "source_row_id, source_record_key, source_contract_version, raw_timestamp, "
                    "raw_text, source_hash, import_run_id FROM analysis_message_sources "
                    f"WHERE CAST(message_id AS TEXT) IN ({placeholders})"
                )
                frames.append(pd.read_sql_query(query, conn, params=batch))
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Chyba při čtení provenance A2: {exc}") from exc

    if not frames:
        return empty_provenance()
    result = pd.concat(frames, ignore_index=True)
    if result.empty:
        return empty_provenance()
    result["message_id"] = result["message_id"].astype(str)
    order = {message_id: index for index, message_id in enumerate(requested)}
    result["_message_order"] = result["message_id"].map(order)
    result = result.sort_values(["_message_order", "import_run_id"], kind="stable").drop(columns="_message_order")
    return result[PROVENANCE_COLUMNS].reset_index(drop=True)
