from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

VALID = "VALID"
PARTIAL = "PARTIALLY VALID"
INVALID = "INVALID"
NEEDS_REVIEW = "NEEDS REVIEW"


ALIASES = {
    "VALID": VALID,
    "PASS": VALID,
    "PARTIALLY VALID": PARTIAL,
    "PARTIALLY_VALID": PARTIAL,
    "WARNING": NEEDS_REVIEW,
    "NEEDS REVIEW": NEEDS_REVIEW,
    "NEEDS_REVIEW": NEEDS_REVIEW,
    "INVALID": INVALID,
    "FAIL": INVALID,
}


def _normalize(value: Any) -> str | None:
    if value is None:
        return None
    return ALIASES.get(str(value).strip().upper())


def _job_verdict(result: str) -> str:
    normalized = result.strip().lower()
    if normalized == "success":
        return VALID
    if normalized == "failure":
        return INVALID
    return NEEDS_REVIEW


def _load_report(path: Path) -> Mapping[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _component(
    name: str,
    job_result: str,
    report: Mapping[str, Any] | None,
    *,
    require_report: bool,
) -> dict[str, Any]:
    job_verdict = _job_verdict(job_result)
    report_verdict = None
    if report is not None:
        report_verdict = _normalize(report.get("verdict")) or _normalize(report.get("status"))

    if job_verdict == INVALID:
        verdict = INVALID
        reason = "CI job failed"
    elif job_verdict == NEEDS_REVIEW:
        verdict = NEEDS_REVIEW
        reason = f"CI job result is {job_result!r}"
    elif require_report and report is None:
        verdict = NEEDS_REVIEW
        reason = "CI job succeeded but machine-readable report is missing"
    elif report is not None and report_verdict is None:
        verdict = NEEDS_REVIEW
        reason = "machine-readable report has no recognized verdict/status"
    else:
        verdict = report_verdict or VALID
        reason = "independent audit report" if report is not None else "core CI/test gate"

    return {
        "component": name,
        "job_result": job_result,
        "verdict": verdict,
        "reason": reason,
        "report_schema_version": report.get("schema_version") if report is not None else None,
        "report_issue_count": len(report.get("issues", [])) if report is not None and isinstance(report.get("issues"), list) else None,
    }


def aggregate_release_verdict(
    *,
    core_job: str,
    a4_job: str,
    a5_job: str,
    a6_job: str,
    report_dir: str | Path,
) -> dict[str, Any]:
    report_dir = Path(report_dir)
    components = [
        _component("A1-A3 core", core_job, None, require_report=False),
        _component("A4 analytics", a4_job, _load_report(report_dir / "a7-a4-report.json"), require_report=True),
        _component("A5 AI evidence", a5_job, _load_report(report_dir / "a7-a5-report.json"), require_report=True),
        _component("A6 UI/read model", a6_job, _load_report(report_dir / "a7-a6-report.json"), require_report=True),
    ]

    verdicts = {row["verdict"] for row in components}
    if INVALID in verdicts:
        overall = INVALID
    elif NEEDS_REVIEW in verdicts:
        overall = NEEDS_REVIEW
    elif PARTIAL in verdicts:
        overall = PARTIAL
    else:
        overall = VALID

    blockers = [
        {
            "component": row["component"],
            "verdict": row["verdict"],
            "reason": row["reason"],
        }
        for row in components
        if row["verdict"] != VALID
    ]
    return {
        "schema_version": 1,
        "overall_verdict": overall,
        "components": components,
        "blockers": blockers,
        "release_ready": overall == VALID,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate A7 component audits into one release verdict")
    parser.add_argument("--core-job", required=True)
    parser.add_argument("--a4-job", required=True)
    parser.add_argument("--a5-job", required=True)
    parser.add_argument("--a6-job", required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--expect",
        choices=[VALID, PARTIAL, INVALID, NEEDS_REVIEW],
        default=VALID,
    )
    args = parser.parse_args(argv)

    report = aggregate_release_verdict(
        core_job=args.core_job,
        a4_job=args.a4_job,
        a5_job=args.a5_job,
        a6_job=args.a6_job,
        report_dir=args.report_dir,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["overall_verdict"] == args.expect else 1


if __name__ == "__main__":
    raise SystemExit(main())
