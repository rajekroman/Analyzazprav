from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3
from statistics import median
from time import perf_counter
from typing import Iterable

from . import adapter as _membership_adapter
from .adapter_v7 import (
    _analyze_grouped,
    _group_messages,
    _load_selected_messages,
    _object_exists,
)
from .config import AnalyticsConfig


@dataclass(frozen=True, slots=True)
class StageTiming:
    min_seconds: float
    median_seconds: float
    max_seconds: float


@dataclass(frozen=True, slots=True)
class A4Profile:
    repeat: int
    selected_conversation_ids: tuple[int, ...] | None
    conversation_count: int
    message_count: int
    load_timing: StageTiming
    analysis_timing: StageTiming
    total_median_seconds: float
    messages_per_second: float | None
    query_plan: dict[str, list[str]]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _timing(values: list[float]) -> StageTiming:
    return StageTiming(
        min_seconds=round(min(values), 6),
        median_seconds=round(median(values), 6),
        max_seconds=round(max(values), 6),
    )


def _selection(conversation_ids: Iterable[int] | None) -> tuple[int, ...] | None:
    if conversation_ids is None:
        return None
    return tuple(sorted({int(conversation_id) for conversation_id in conversation_ids}))


def _plan_details(
    conn: sqlite3.Connection, sql: str, params: tuple[int, ...]
) -> list[str]:
    return [
        str(row[3])
        for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    ]


def explain_read_plan(
    conn: sqlite3.Connection,
    conversation_ids: Iterable[int] | None = None,
) -> dict[str, list[str]]:
    """Return SQLite planner evidence for the exact A4 source read path.

    Plans are diagnostic evidence only. They are deliberately not converted into
    hard-coded pass/fail thresholds because SQLite planner wording can change
    between versions. The profiler keeps the raw details auditable.
    """

    selected = _selection(conversation_ids)
    plan: dict[str, list[str]] = {}
    targets: tuple[int | None, ...] = (None,) if selected is None else selected

    for conversation_id in targets:
        sql, params, _ = _membership_adapter._analytic_query(conn, conversation_id)
        key = "membership:all" if conversation_id is None else f"membership:{conversation_id}"
        plan[key] = _plan_details(conn, sql, params)

    if _object_exists(conn, "analysis_processed_messages_resolved_latest", "view"):
        if selected is None:
            sql = """SELECT membership_id, message_id, conversation_id, resolved_sender_id
                     FROM analysis_processed_messages_resolved_latest"""
            plan["resolved:all"] = _plan_details(conn, sql, ())
        elif selected:
            placeholders = ",".join("?" for _ in selected)
            sql = f"""SELECT membership_id, message_id, conversation_id, resolved_sender_id
                      FROM analysis_processed_messages_resolved_latest
                      WHERE conversation_id IN ({placeholders})"""
            plan["resolved:selected"] = _plan_details(conn, sql, tuple(selected))

    return plan


def profile_connection(
    conn: sqlite3.Connection,
    *,
    conversation_ids: Iterable[int] | None = None,
    repeat: int = 3,
    config: AnalyticsConfig | None = None,
) -> A4Profile:
    """Measure read and deterministic-analysis stages without persisting anything."""

    if repeat < 1:
        raise ValueError("repeat must be >= 1")

    cfg = config or AnalyticsConfig()
    selected = _selection(conversation_ids)
    load_samples: list[float] = []
    analysis_samples: list[float] = []
    expected_snapshot: tuple[tuple[int, int, str], ...] | None = None
    final_message_count = 0
    final_conversation_count = 0

    for _ in range(repeat):
        load_start = perf_counter()
        messages = _load_selected_messages(conn, selected)
        load_end = perf_counter()

        grouped = _group_messages(messages)
        analysis_start = perf_counter()
        results = _analyze_grouped(grouped, cfg)
        analysis_end = perf_counter()

        snapshot = tuple(
            (result.conversation_id, result.source_message_count, result.source_fingerprint)
            for result in results
        )
        if expected_snapshot is None:
            expected_snapshot = snapshot
        elif snapshot != expected_snapshot:
            raise RuntimeError("A4 profiler observed non-deterministic repeated results")

        load_samples.append(load_end - load_start)
        analysis_samples.append(analysis_end - analysis_start)
        final_message_count = len(messages)
        final_conversation_count = len(results)

    load_timing = _timing(load_samples)
    analysis_timing = _timing(analysis_samples)
    total_median = round(
        load_timing.median_seconds + analysis_timing.median_seconds,
        6,
    )
    throughput = (
        None
        if total_median <= 0
        else round(final_message_count / total_median, 3)
    )

    return A4Profile(
        repeat=repeat,
        selected_conversation_ids=selected,
        conversation_count=final_conversation_count,
        message_count=final_message_count,
        load_timing=load_timing,
        analysis_timing=analysis_timing,
        total_median_seconds=total_median,
        messages_per_second=throughput,
        query_plan=explain_read_plan(conn, selected),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m analyzazprav.analytics.profile",
        description="Read-only A4 performance and SQLite query-plan profiler.",
    )
    parser.add_argument("database", type=Path, help="Path to project SQLite database")
    parser.add_argument(
        "--conversation-id",
        action="append",
        type=int,
        dest="conversation_ids",
        help="Profile only this conversation ID; may be repeated",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=3,
        help="Number of deterministic repetitions (default: 3)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.database.resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        profile = profile_connection(
            conn,
            conversation_ids=args.conversation_ids,
            repeat=args.repeat,
        )
        print(json.dumps(profile.as_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
