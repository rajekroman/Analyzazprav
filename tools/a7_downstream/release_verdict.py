from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.a7_downstream.common import load_downstream_validator, write_report


def _read(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", default="a7-reports")
    parser.add_argument("--report", required=True)
    parser.add_argument("--core-result", default="success")
    parser.add_argument("--a5-result", default="success")
    parser.add_argument("--a6-result", default="success")
    args = parser.parse_args()

    root = Path(args.reports)
    validator = load_downstream_validator()
    reports = {
        "core": _read(root / "a7-core-report.json"),
        "A5": _read(root / "a7-a5-report.json"),
        "A6": _read(root / "a7-a6-report.json"),
    }
    job_results = {
        "core": args.core_result,
        "A5": args.a5_result,
        "A6": args.a6_result,
    }
    report = validator.aggregate_release_verdict(reports, job_results=job_results)
    report["scope"] = (
        "integrated A1-A4/A7 core plus exact-head A5/A6 synthetic downstream contract; "
        "not a claim that an arbitrary real user archive or source-specific Apple schema has been validated"
    )
    write_report(report, args.report)
    return 0 if report["overall_verdict"] == "VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
