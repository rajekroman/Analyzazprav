from __future__ import annotations

from collections.abc import Iterable
import sqlite3

import pandas as pd

from .data import DataSourceError, _objects


_RECON_ZERO_COLUMNS = (
    "membership_count_delta",
    "invalid_response_session_count",
    "invalid_silence_session_count",
    "invalid_event_session_count",
)


def _validate_reconciliation_rows(frame: pd.DataFrame) -> None:
    """Independently check the published A4/A7 reconciliation contract.

    Legacy fixtures/databases that publish only ``reconciliation_ok`` remain
    readable. When current A4 v7+ accounting columns are published, A6 checks
    them rather than trusting a single derived boolean blindly.
    """
    if frame.empty:
        return

    for column in _RECON_ZERO_COLUMNS:
        if column in frame.columns:
            values = pd.to_numeric(frame[column], errors="coerce")
            if values.isna().any() or (values != 0).any():
                bad = frame.loc[values.isna() | (values != 0), "conversation_id"].astype(str)
                raise DataSourceError(
                    f"A4 reconciliation invariant {column}=0 selhal pro conversation_id: "
                    + ", ".join(sorted(set(bad)))
                )

    for column in ("uses_latest_processing_run", "reconciliation_ok"):
        if column in frame.columns:
            values = pd.to_numeric(frame[column], errors="coerce")
            if values.isna().any() or (values != 1).any():
                # reconciliation_ok=0 is handled by require_reconciled with a
                # more contextual message, so only the stronger current-A4
                # latest-run invariant is raised here.
                if column == "reconciliation_ok":
                    continue
                bad = frame.loc[values.isna() | (values != 1), "conversation_id"].astype(str)
                raise DataSourceError(
                    "A4 reconciliation nepoužívá latest A3 processing run pro conversation_id: "
                    + ", ".join(sorted(set(bad)))
                )

    required_count_columns = {
        "a4_source_membership_count",
        "a3_processed_membership_count",
        "sender_accounted_membership_count",
    }
    if required_count_columns.issubset(frame.columns):
        source = pd.to_numeric(frame["a4_source_membership_count"], errors="coerce")
        processed = pd.to_numeric(frame["a3_processed_membership_count"], errors="coerce")
        accounted = pd.to_numeric(
            frame["sender_accounted_membership_count"], errors="coerce"
        )
        invalid = source.isna() | processed.isna() | accounted.isna()
        invalid |= (source != processed) | (source != accounted)
        if invalid.any():
            bad = frame.loc[invalid, "conversation_id"].astype(str)
            raise DataSourceError(
                "A4 reconciliation membership accounting nesedí pro conversation_id: "
                + ", ".join(sorted(set(bad)))
            )


def reconciliation_map(conn: sqlite3.Connection) -> dict[str, bool] | None:
    """Return latest published A4 reconciliation status by conversation.

    ``None`` means the database predates the reconciliation view. Once the
    view exists, duplicate conversation rows are invalid because A6 cannot
    choose one audit result without inventing precedence.
    """
    if "analysis_a4_reconciliation" not in set(_objects(conn)):
        return None
    frame = pd.read_sql_query("SELECT * FROM analysis_a4_reconciliation", conn)
    if frame.empty:
        return {}
    if "conversation_id" not in frame.columns or "reconciliation_ok" not in frame.columns:
        raise DataSourceError(
            "A4 reconciliation view postrádá conversation_id nebo reconciliation_ok."
        )
    frame["conversation_id"] = frame["conversation_id"].astype(str)
    duplicated = frame["conversation_id"].duplicated(keep=False)
    if duplicated.any():
        values = sorted(frame.loc[duplicated, "conversation_id"].unique())
        raise DataSourceError(
            "A4 reconciliation obsahuje více latest řádků pro conversation_id: "
            + ", ".join(values)
        )

    _validate_reconciliation_rows(frame)

    result: dict[str, bool] = {}
    for row in frame.itertuples(index=False):
        try:
            result[str(row.conversation_id)] = bool(int(row.reconciliation_ok))
        except (TypeError, ValueError) as exc:
            raise DataSourceError(
                f"A4 reconciliation_ok není validní integer flag pro conversation_id {row.conversation_id}."
            ) from exc
    return result


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
