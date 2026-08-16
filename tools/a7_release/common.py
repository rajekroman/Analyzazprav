from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_report(report: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def issue(severity: str, code: str, detail: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "detail": detail}


def finalize(module: str, checks: dict[str, Any], issues: list[dict[str, str]], *, contract_sha: str) -> dict[str, Any]:
    errors = sum(row.get("severity") == "ERROR" for row in issues)
    warnings = sum(row.get("severity") == "WARNING" for row in issues)
    status = "FAIL" if errors else "WARNING" if warnings else "PASS"
    verdict = "INVALID" if errors else "NEEDS_REVIEW" if warnings else "VALID"
    return {
        "schema_version": 1,
        "module": module,
        "status": status,
        "verdict": verdict,
        "contract_sha": contract_sha,
        "checks": checks,
        "counts": {"errors": errors, "warnings": warnings},
        "issues": issues,
    }
