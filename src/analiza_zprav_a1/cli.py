from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .importer import import_generic_csv, import_generic_json, import_generic_text, import_imazing_csv, import_imessage


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

    generic_csv = sub.add_parser("csv", help="Extract a headered generic message CSV into the A1 staging contract")
    generic_csv.add_argument("--csv", required=True, type=Path)
    _add_output_and_attachments(generic_csv)

    generic_json = sub.add_parser("json", help="Extract generic JSON/JSONL message records into the A1 staging contract")
    generic_json.add_argument("--json", required=True, type=Path)
    _add_output_and_attachments(generic_json)

    generic_txt = sub.add_parser("txt", help="Import plain text with an explicit record boundary mode")
    generic_txt.add_argument("--txt", required=True, type=Path)
    generic_txt.add_argument("--mode", required=True, choices=("line", "block", "whole"))
    generic_txt.add_argument("--output-dir", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "imessage":
        stats = import_imessage(args.chat_db, args.output_dir, args.attachments_root)
    elif args.command == "imazing-csv":
        stats = import_imazing_csv(args.csv, args.output_dir, args.attachments_root)
    elif args.command == "csv":
        stats = import_generic_csv(args.csv, args.output_dir, args.attachments_root)
    elif args.command == "json":
        stats = import_generic_json(args.json, args.output_dir, args.attachments_root)
    elif args.command == "txt":
        stats = import_generic_text(args.txt, args.output_dir, args.mode)
    else:
        return 1
    print(json.dumps(asdict(stats), ensure_ascii=False, indent=2))
    return 0 if stats.errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
