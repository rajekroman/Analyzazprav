from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .importer import import_imazing_csv, import_imessage


def _add_output_and_attachments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--attachments-root",
        type=Path,
        help="Optional root directory used to resolve exported/original attachment files",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="az-import", description="Analýza zpráv A1 local source importer")
    sub = parser.add_subparsers(dest="command", required=True)

    imessage = sub.add_parser("imessage", help="Extract Apple Messages chat.db into the A1 staging contract")
    imessage.add_argument("--chat-db", required=True, type=Path)
    _add_output_and_attachments(imessage)

    imazing = sub.add_parser("imazing-csv", help="Extract an iMazing Messages CSV export into the A1 staging contract")
    imazing.add_argument("--csv", required=True, type=Path)
    _add_output_and_attachments(imazing)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "imessage":
        stats = import_imessage(args.chat_db, args.output_dir, args.attachments_root)
    elif args.command == "imazing-csv":
        stats = import_imazing_csv(args.csv, args.output_dir, args.attachments_root)
    else:
        return 1
    print(json.dumps(asdict(stats), ensure_ascii=False, indent=2))
    return 0 if stats.errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
