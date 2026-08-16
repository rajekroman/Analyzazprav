from __future__ import annotations

from collections.abc import Iterable
import sqlite3

import pandas as pd

from .data import DataSourceError, _objects


def reconciliation_map(conn: sqlite3.Connection) -> dict[str, bool] | None:
    """Return latest published A4 reconciliation status by conversation.

    ``None`` means the database predates the reconciliation view. Once the
    view exists, duplicate conversation rows are invalid because A6 cannot
    choose one audit result without inventing precedence.
    """
    if "analysis_a4_reconciliation" not in set(_objects(conn)):
        return None
    frame = pd.read_sql_query(
        "SELECT conversation_id, reconciliation_ok FROM analysis_a4_reconciliation",
        conn,
    )
    if frame.empty:
        return {}
    frame["conversation_id"] = frame["conversation_id"].astype(str)
    duplicated = frame["conversation_id"].duplicated(keep=False)
    if duplicated.any():
        values = sorted(frame.loc[duplicated, "conversation_id"].unique())
        raise DataSourceError(
            "A4 reconciliation obsahuje více latest řádků pro conversation_id: "
            + ", ".join(values)
        )
    return {
        str(row.conversation_id): bool(int(row.reconciliation_ok))
        for row in frame.itertuples(index=False)
    }


def require_reconciled(
    conn: sqlite3.Connection,
    conversation_ids: Iterable[str],
    *,
    context: str,
) -> None:
    """Fail closed on A4 outputs that have a published failed/missing gate."""
    status = reconciliation_map(conn)
    if status is None:
        return
    requested = tuple(dict.fromkeys(str(value) for value in conversation_ids))
    missing = [value for value in requested if value not in status]
    invalid = [value for value in requested if value in status and not status[value]]
    if missing or invalid:
        parts: list[str] = []
        if invalid:
            parts.append("reconciliation_ok=0: " + ", ".join(invalid))
        if missing:
            parts.append("bez reconciliation řádku: " + ", ".join(missing))
        raise DataSourceError(
            f"A4 {context} nelze v A6 označit za autoritativní; " + "; ".join(parts)
        )
