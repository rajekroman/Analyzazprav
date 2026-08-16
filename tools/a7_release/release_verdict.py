from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Mapping

from tools.a7_release.common import write_report

_SHA = re.compile(r"^[0-9a-f]{40}$")
_COMPONENTS = ("core", "A5", "A6")


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def aggregate_release_verdict(
    reports: Mapping[str, Mapping[str, Any] | None],
    *,
    job_results: Mapping[str, str],
    expected_sha: str,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    components: dict[str, str] = {}
    observed_contracts: dict[str, str | None] = {}

    if not _SHA.fullmatch(expected_sha):
        issues.append({
            "severity": "ERROR",
            "code": "A7_EXPECTED_SHA_INVALID",
            "detail": "Expected release contract SHA is not a 40-character lowercase git SHA.",
        })

    for name in _COMPONENTS:
        job = str(job_results.get(name, "missing"))
        report = reports.get(name)
        if job != "success":
            components[name] = "INVALID"
            issues.append({
                "severity": "ERROR",
                "code": "A7_COMPONENT_JOB_FAILED",
                "detail": f"{name} workflow job result is {job!r}, expected 'success'.",
            })
            continue
        if report is None:
            components[name] = "NEEDS_REVIEW"
            observed_contracts[name] = None
            issues.append({
                "severity": "WARNING",
                "code": "A7_COMPONENT_REPORT_MISSING",
                "detail": f"{name} job succeeded but its report artifact is missing.",
            })
            continue

        verdict = str(report.get("verdict") or "NEEDS_REVIEW")
        components[name] = verdict if verdict in {"VALID", "INVALID", "NEEDS_REVIEW"} else "INVALID"
        observed = str(report.get("contract_sha") or "") or None
        observed_contracts[name] = observed
        if observed != expected_sha:
            components[name] = "INVALID"
            issues.append({
                "severity": "ERROR",
                "code": "A7_COMPONENT_SHA_MISMATCH",
                "detail": f"{name} report SHA {observed!r} != tested SHA {expected_sha!r}.",
            })
        if verdict == "INVALID":
            issues.append({
                "severity": "ERROR",
                "code": "A7_COMPONENT_INVALID",
                "detail": f"{name} live/current-main validator returned INVALID.",
            })
        elif verdict == "NEEDS_REVIEW":
            issues.append({
                "severity": "WARNING",
                "code": "A7_COMPONENT_NEEDS_REVIEW",
                "detail": f"{name} live/current-main validator requires review.",
            })
        elif verdict != "VALID":
            issues.append({
                "severity": "ERROR",
                "code": "A7_COMPONENT_VERDICT_UNKNOWN",
                "detail": f"{name} returned unsupported verdict {verdict!r}.",
            })

    if any(row["severity"] == "ERROR" for row in issues):
        overall = "INVALID"
    elif any(value != "VALID" for value in components.values()) or issues:
        overall = "NEEDS_REVIEW"
    else:
        overall = "VALID"

    return {
        "schema_version": 1,
        "scope": (
            "exact current-checkout synthetic A1-A7 integration gate; "
            "not proof that an arbitrary real user archive or every Apple Messages schema variant has been validated"
        ),
        "tested_sha": expected_sha,
        "component_contracts": observed_contracts,
        "components": components,
        "issues": issues,
        "overall_verdict": overall,
        "release_ready": overall == "VALID" and all(components.get(name) == "VALID" for name in _COMPONENTS),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", default="a7-reports")
    parser.add_argument("--report", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--core-result", default="success")
    parser.add_argument("--a5-result", default="success")
    parser.add_argument("--a6-result", default="success")
    args = parser.parse_args()

    root = Path(args.reports)
    reports = {
        "core": _read(root / "a7-core-report.json"),
        "A5": _read(root / "a7-a5-report.json"),
        "A6": _read(root / "a7-a6-report.json"),
    }
    report = aggregate_release_verdict(
        reports,
        job_results={
            "core": args.core_result,
            "A5": args.a5_result,
            "A6": args.a6_result,
        },
        expected_sha=args.sha,
    )
    write_report(report, args.report)
    return 0 if report["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
