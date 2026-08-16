from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from analyza_zprav.db import connect, initialize
from analyza_zprav.importers.imessage import import_chat_db
from analyza_zprav.processing import materialize_message_features
from analyza_zprav.qa import verify
from analyza_zprav.stats import conversation_metrics, conversation_rows, overview


def _conn(path: str):
    conn = connect(path)
    initialize(conn)
    return conn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="analyza-zprav")
    parser.add_argument("--db", default="data/analyza-zprav.sqlite3", help="Normalized SQLite database")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize normalized database")
    p_import = sub.add_parser("import-imessage", help="Import macOS Messages chat.db")
    p_import.add_argument("chat_db")
    sub.add_parser("process", help="Materialize deterministic temporal features")
    sub.add_parser("verify", help="Run integrity verification")
    sub.add_parser("stats", help="Show normalized archive counts")
    p_conv = sub.add_parser("conversations", help="List conversations")
    p_conv.add_argument("--limit", type=int, default=50)
    p_metrics = sub.add_parser("metrics", help="Show metrics for one normalized conversation")
    p_metrics.add_argument("conversation_id", type=int)

    args = parser.parse_args(argv)
    conn = _conn(args.db)
    try:
        if args.command == "init":
            print(json.dumps({"status": "ok", "db": args.db}, ensure_ascii=False))
            return 0
        if args.command == "import-imessage":
            result = import_chat_db(args.chat_db, conn)
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            return 0
        if args.command == "process":
            written = materialize_message_features(conn)
            print(json.dumps({"status": "ok", "features_written": written}, ensure_ascii=False))
            return 0
        if args.command == "verify":
            report = verify(conn)
            print(json.dumps({**asdict(report), "ok": report.ok}, ensure_ascii=False, indent=2))
            return 0 if report.ok else 2
        if args.command == "stats":
            print(json.dumps(overview(conn), ensure_ascii=False, indent=2))
            return 0
        if args.command == "conversations":
            print(json.dumps([dict(r) for r in conversation_rows(conn, args.limit)], ensure_ascii=False, indent=2))
            return 0
        if args.command == "metrics":
            print(json.dumps(conversation_metrics(conn, args.conversation_id), ensure_ascii=False, indent=2))
            return 0
    finally:
        conn.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
