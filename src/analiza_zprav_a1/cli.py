from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .importer import import_imessage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="az-import", description="Analýza zpráv A1 local source importer")
    sub = parser.add_subparsers(dest="command", required=True)
    imessage = sub.add_parser("imessage", help="Extract Apple Messages chat.db into the A1 staging contract")
    imessage.add_argument("--chat-db", required=True, type=Path)
    imessage.add_argument("--output-dir", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "imessage":
        stats = import_imessage(args.chat_db, args.output_dir)
        print(json.dumps(asdict(stats), ensure_ascii=False, indent=2))
        return 0 if stats.errors == 0 else 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
