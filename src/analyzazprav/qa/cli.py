from __future__ import annotations

import argparse
import json
from pathlib import Path

from .a4_current import validate_a4_metrics
from .participant_resolution import validate_participant_resolution
from .reconciliation import validate_staging_bundle
from .staging import STATUS_FAIL
from .vertical import validate_vertical_pipeline


def _emit(report: dict[str, object]) -> int:
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report.get("status") == STATUS_FAIL else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="az-qa",
        description=(
            "Validate staging, the A1→A2→A3 data path, A3 participant resolution, "
            "and deterministic A4 analytics."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    staging_parser = sub.add_parser(
        "staging",
        help="Validate an A1 staging bundle and its source reconciliation read-only.",
    )
    staging_parser.add_argument("--staging", type=Path, required=True)

    vertical_parser = sub.add_parser(
        "vertical",
        help="Reconcile validated A1 staging against A2 canonical data and the latest A3 run.",
    )
    vertical_parser.add_argument("--staging", type=Path, required=True)
    vertical_parser.add_argument("--database", type=Path, required=True)

    participants_parser = sub.add_parser(
        "participants",
        help=(
            "Independently reconcile the latest A3 participant-resolution sidecars "
            "against authoritative A2 participants and identities."
        ),
    )
    participants_parser.add_argument("--database", type=Path, required=True)

    analytics_parser = sub.add_parser(
        "analytics",
        help="Independently recompute release-critical A4 metrics from A2/A3 resolved identities.",
    )
    analytics_parser.add_argument("--database", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "staging":
        return _emit(validate_staging_bundle(args.staging))
    if args.command == "vertical":
        return _emit(validate_vertical_pipeline(args.staging, args.database))
    if args.command == "participants":
        return _emit(validate_participant_resolution(args.database))
    if args.command == "analytics":
        return _emit(validate_a4_metrics(args.database))
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
