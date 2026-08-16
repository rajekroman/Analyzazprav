from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Mapping

from tools.a7_downstream.common import load_downstream_validator, write_report


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _read(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if _SHA_RE.fullmatch(text) else None


def apply_contract_provenance(
    report: dict,
    component_reports: Mapping[str, Mapping[str, object] | None],
    expected_shas: Mapping[str, str],
) -> dict:
    """Bind a release verdict to the exact component commits that were tested.

    Component reports are produced inside the same workflow run, but the final
    artifact must still be self-describing. A missing, malformed or different
    ``contract_sha`` therefore invalidates the release verdict instead of
    forcing an auditor to infer provenance from workflow YAML or job history.
    """

    contracts: dict[str, dict[str, str | None]] = {}
    provenance_issues: list[dict[str, str]] = []

    for name, expected_raw in expected_shas.items():
        expected = _sha(expected_raw)
        component = component_reports.get(name)
        observed = _sha(component.get("contract_sha")) if component is not None else None
        contracts[name] = {
            "expected_sha": expected,
            "observed_sha": observed,
        }

        if expected is None:
            provenance_issues.append(
                {
                    "severity": "ERROR",
                    "code": "A7_EXPECTED_CONTRACT_SHA_INVALID",
                    "detail": f"{name} expected contract SHA is not a 40-character git SHA",
                }
            )
            continue
        if observed is None:
            provenance_issues.append(
                {
                    "severity": "ERROR",
                    "code": "A7_COMPONENT_CONTRACT_SHA_MISSING",
                    "detail": f"{name} report is missing a valid contract_sha",
                }
            )
            continue
        if observed != expected:
            provenance_issues.append(
                {
                    "severity": "ERROR",
                    "code": "A7_COMPONENT_CONTRACT_SHA_MISMATCH",
                    "detail": f"{name} observed contract SHA {observed} != expected {expected}",
                }
            )

    report["schema_version"] = 2
    report["component_contracts"] = contracts
    if provenance_issues:
        report.setdefault("issues", []).extend(provenance_issues)
        report["overall_verdict"] = "INVALID"
        report["release_ready"] = False
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", default="a7-reports")
    parser.add_argument("--report", required=True)
    parser.add_argument("--core-result", default="success")
    parser.add_argument("--a5-result", default="success")
    parser.add_argument("--a6-result", default="success")
    parser.add_argument("--core-sha", required=True)
    parser.add_argument("--a5-sha", required=True)
    parser.add_argument("--a6-sha", required=True)
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
    report = apply_contract_provenance(
        report,
        reports,
        {
            "core": args.core_sha,
            "A5": args.a5_sha,
            "A6": args.a6_sha,
        },
    )
    report["scope"] = (
        "integrated A1-A4/A7 core plus exact-head A5/A6 synthetic downstream contract; "
        "not a claim that an arbitrary real user archive or source-specific Apple schema has been validated"
    )
    write_report(report, args.report)
    return 0 if report["overall_verdict"] == "VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
