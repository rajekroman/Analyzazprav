from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .database import CanonicalDatabase
from .integrity import full_integrity_report
from .time_contract import ingest_a1_staging_bundle


def _schema_version(db: CanonicalDatabase) -> str | None:
    row = db.conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    return None if row is None else str(row["value"])


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _open_current(path: Path) -> CanonicalDatabase:
    db = CanonicalDatabase(path)
    db.initialize()
    return db


def _cmd_init(database: Path) -> int:
    db = _open_current(database)
    try:
        report = full_integrity_report(db)
        payload = {
            "status": "ok" if report["ok"] else "error",
            "database": str(database),
            "schema_version": _schema_version(db),
            "integrity": report,
        }
        _emit(payload)
        return 0 if payload["status"] == "ok" else 1
    finally:
        db.close()


def _cmd_ingest_a1(database: Path, staging: Path) -> int:
    db = _open_current(database)
    try:
        result = ingest_a1_staging_bundle(db, staging)
        report = full_integrity_report(db)
        ok = bool(report["ok"])
        _emit(
            {
                "status": "ok" if ok else "error",
                "database": str(database),
                "schema_version": _schema_version(db),
                "import_run_id": result.import_run_id,
                "already_imported": result.already_imported,
                "messages": result.messages,
                "attachments": result.attachments,
                "relations": result.relations,
                "conversation_relations": result.conversation_relations,
                "integrity": report,
            }
        )
        return 0 if ok else 1
    finally:
        db.close()


def _cmd_check(database: Path) -> int:
    if not database.is_file():
        _emit({"status": "error", "database": str(database), "error": "database_not_found"})
        return 2
    db = _open_current(database)
    try:
        report = full_integrity_report(db)
        ok = bool(report["ok"])
        _emit(
            {
                "status": "ok" if ok else "error",
                "database": str(database),
                "schema_version": _schema_version(db),
                "integrity": report,
            }
        )
        return 0 if ok else 1
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="az-normalize",
        description="A2 canonical normalization and SQLite database tools.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or migrate the canonical SQLite database.")
    init_parser.add_argument("--database", type=Path, required=True)

    ingest_parser = subparsers.add_parser("ingest-a1", help="Normalize an A1 staging bundle into SQLite.")
    ingest_parser.add_argument("--database", type=Path, required=True)
    ingest_parser.add_argument("--staging", type=Path, required=True)

    check_parser = subparsers.add_parser(
        "check",
        help="Migrate if needed, then run structural and semantic A2 integrity checks.",
    )
    check_parser.add_argument("--database", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _cmd_init(args.database)
        if args.command == "ingest-a1":
            return _cmd_ingest_a1(args.database, args.staging)
        if args.command == "check":
            return _cmd_check(args.database)
    except (OSError, ValueError, RuntimeError) as exc:
        _emit({"status": "error", "error": str(exc)})
        return 2
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
