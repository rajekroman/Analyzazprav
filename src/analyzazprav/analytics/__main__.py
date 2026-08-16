from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from .adapter import analyze_database
from .config import AnalyticsConfig
from .incremental import analyze_incremental_database
from .store_v7 import AnalyticsStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="az-analyze",
        description="Run deterministic A4 analytics over an A2/A3 SQLite database.",
    )
    parser.add_argument("database", type=Path, help="Path to the project SQLite database")
    parser.add_argument(
        "--conversation-id",
        action="append",
        type=int,
        dest="conversation_ids",
        help="Analyze only this conversation ID; may be repeated",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Recompute selected conversations even when their A2/A3 fingerprint is unchanged",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AnalyticsConfig()
    conn = sqlite3.connect(args.database)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        store = AnalyticsStore(conn)
        store.initialize()
        if args.full:
            results = analyze_database(conn, config, args.conversation_ids)
        else:
            results = analyze_incremental_database(conn, config, args.conversation_ids)
        if not results:
            print(json.dumps({"status": "up_to_date", "conversation_count": 0}))
            return 0
        run_id = store.write_run(results, config)
        print(
            json.dumps(
                {
                    "status": "completed",
                    "analytics_run_id": run_id,
                    "conversation_count": len(results),
                    "membership_count": sum(item.source_message_count for item in results),
                    "change_point_count": sum(len(item.change_points) for item in results),
                    "regime_count": sum(len(item.dyadic_regimes) for item in results),
                    "trend_count": sum(len(item.trend_summaries) for item in results),
                    "topic_candidate_count": sum(
                        len(item.topic_candidates) for item in results
                    ),
                    "topic_evidence_count": sum(
                        len(item.topic_evidence) for item in results
                    ),
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
