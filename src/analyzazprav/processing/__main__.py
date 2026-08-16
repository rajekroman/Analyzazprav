from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3

from .adapter import load_a2_projection
from .pipeline import ProcessingConfig, process_messages
from .store import ProcessingStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Run A3 processing over an A2 SQLite database")
    parser.add_argument("database", type=Path)
    parser.add_argument("--session-gap-hours", type=float, default=6.0)
    parser.add_argument("--duplicate-tolerance-seconds", type=int, default=2)
    args = parser.parse_args()

    conn = sqlite3.connect(args.database)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        projection = load_a2_projection(conn)
        config = ProcessingConfig(
            session_gap_seconds=max(1, int(args.session_gap_hours * 3600)),
            duplicate_tolerance_seconds=args.duplicate_tolerance_seconds,
        )
        result = process_messages(
            list(projection.messages),
            list(projection.relations),
            config,
            participants=list(projection.participants),
        )
        store = ProcessingStore(conn)
        store.initialize()
        run_id = store.replace_all(result, config)
        canonical_messages = len({message.message_id for message in result.messages})
        print(
            f"A3 PASS run={run_id} canonical_messages={canonical_messages} "
            f"memberships={len(result.messages)} participants={len(result.resolved_participants)} "
            f"aliases={len(result.participant_aliases)} runs={len(result.sender_runs)} "
            f"sessions={len(result.sessions)} threads={len(result.threads)} "
            f"duplicate_candidates={len(result.duplicate_candidates)} "
            f"participant_candidates={len(result.participant_resolution_candidates)}"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
