from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .jsonl import iter_physical_jsonl_lines
from .staging import STATUS_FAIL, STATUS_PASS, STATUS_WARNING, validate_staging_dir


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _count_jsonl(path: Path) -> tuple[int, list[str]]:
    count = 0
    failures: list[str] = []
    if not path.is_file():
        return 0, [f"missing file: {path.name}"]
    try:
        for line_number, raw in iter_physical_jsonl_lines(path):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                failures.append(f"{path.name}:{line_number}: {exc.msg}")
                continue
            if not isinstance(value, dict):
                failures.append(f"{path.name}:{line_number}: record is not an object")
                continue
            count += 1
    except OSError as exc:
        failures.append(str(exc))
    return count, failures


def _direct_output(root: Path, value: Any, default: str) -> Path | None:
    name = value if isinstance(value, str) and value else default
    candidate = (root / name).resolve()
    if candidate.parent != root.resolve():
        return None
    return candidate


def validate_staging_bundle(root: str | Path) -> dict[str, Any]:
    """Validate current A1 staging plus its mandatory source reconciliation artifact.

    This composes the structural staging validator with the A1 reconciliation report.
    Unsupported/duplicate source outcomes are valid when they are explicitly counted;
    a missing or failed reconciliation report is release-blocking.
    """

    root = Path(root)
    base = validate_staging_dir(root)
    issues = [dict(item) for item in base.get("issues", [])]
    counts = dict(base.get("counts", {}))
    fingerprints = dict(base.get("fingerprints", {}))
    checks: dict[str, Any] = {"staging_status": base.get("status")}

    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        _issue(issues, "ERROR", "RECONCILIATION_MANIFEST_MISSING", "manifest.json is required")
        return _finalize(root, base, counts, fingerprints, checks, issues, None)

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _issue(issues, "ERROR", "RECONCILIATION_MANIFEST_INVALID", str(exc))
        return _finalize(root, base, counts, fingerprints, checks, issues, None)
    if not isinstance(manifest, dict):
        _issue(issues, "ERROR", "RECONCILIATION_MANIFEST_INVALID", "manifest root must be an object")
        return _finalize(root, base, counts, fingerprints, checks, issues, None)

    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    reconciliation_path = _direct_output(root, outputs.get("reconciliation"), "reconciliation.json")
    if reconciliation_path is None:
        _issue(
            issues,
            "ERROR",
            "RECONCILIATION_PATH_INVALID",
            "manifest.outputs.reconciliation must remain directly inside the staging directory",
        )
        return _finalize(root, base, counts, fingerprints, checks, issues, None)
    if not reconciliation_path.is_file():
        _issue(issues, "ERROR", "RECONCILIATION_MISSING", f"missing {reconciliation_path.name}")
        return _finalize(root, base, counts, fingerprints, checks, issues, None)

    try:
        report = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _issue(issues, "ERROR", "RECONCILIATION_INVALID", str(exc))
        return _finalize(root, base, counts, fingerprints, checks, issues, None)
    if not isinstance(report, dict):
        _issue(issues, "ERROR", "RECONCILIATION_INVALID", "reconciliation root must be an object")
        return _finalize(root, base, counts, fingerprints, checks, issues, None)

    fingerprints["reconciliation_sha256"] = hashlib.sha256(reconciliation_path.read_bytes()).hexdigest()
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    report_source = report.get("source") if isinstance(report.get("source"), dict) else {}
    manifest_counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    bundle = report.get("bundle") if isinstance(report.get("bundle"), dict) else {}
    report_checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    failed_checks = report.get("failed_checks") if isinstance(report.get("failed_checks"), list) else []
    parse_failures = report.get("parse_failures") if isinstance(report.get("parse_failures"), list) else []
    unsupported = report.get("unsupported_records") if isinstance(report.get("unsupported_records"), list) else []
    duplicates = report.get("duplicate_records") if isinstance(report.get("duplicate_records"), list) else []

    messages_path = _direct_output(root, outputs.get("messages"), "messages.jsonl")
    errors_path = _direct_output(root, outputs.get("errors"), "errors.jsonl")
    message_rows, message_parse_failures = _count_jsonl(messages_path) if messages_path else (0, ["invalid messages path"])
    error_rows, error_parse_failures = _count_jsonl(errors_path) if errors_path else (0, ["invalid errors path"])

    checks.update(
        {
            "reconciliation_version": report.get("reconciliation_version"),
            "reconciliation_status": report.get("status"),
            "reconciliation_ok": report.get("ok"),
            "reconciliation_failed_check_count": len(failed_checks),
            "reconciliation_parse_failure_count": len(parse_failures),
            "reconciliation_internal_failed_check_count": sum(1 for value in report_checks.values() if value is not True),
            "reconciliation_source_type_matches": report_source.get("type") == source.get("type"),
            "reconciliation_source_sha_matches": report_source.get("sha256") == source.get("sha256"),
            "reconciliation_actual_sha_matches": report_source.get("actual_sha256") == source.get("sha256"),
            "reconciliation_message_rows": bundle.get("messages_jsonl_records"),
            "reconciliation_error_rows": bundle.get("errors_jsonl_records"),
            "reconciliation_message_rows_match_file": bundle.get("messages_jsonl_records") == message_rows,
            "reconciliation_error_rows_match_file": bundle.get("errors_jsonl_records") == error_rows,
            "reconciliation_unsupported_count": len(unsupported),
            "reconciliation_duplicate_count": len(duplicates),
            "manifest_unsupported_count": manifest_counts.get("unsupported"),
            "manifest_duplicate_count": manifest_counts.get("duplicates"),
        }
    )

    if report.get("reconciliation_version") != "1":
        _issue(issues, "ERROR", "RECONCILIATION_VERSION_UNSUPPORTED", f"got {report.get('reconciliation_version')!r}")
    if report.get("status") != "ok" or report.get("ok") is not True:
        _issue(issues, "ERROR", "RECONCILIATION_FAILED", f"status={report.get('status')!r}, ok={report.get('ok')!r}")
    if failed_checks:
        _issue(issues, "ERROR", "RECONCILIATION_FAILED_CHECKS", f"{failed_checks[:10]}")
    if parse_failures or message_parse_failures or error_parse_failures:
        _issue(
            issues,
            "ERROR",
            "RECONCILIATION_PARSE_FAILURES",
            f"report={parse_failures[:5]}, messages={message_parse_failures[:5]}, errors={error_parse_failures[:5]}",
        )
    if any(value is not True for value in report_checks.values()):
        bad = [name for name, value in report_checks.items() if value is not True]
        _issue(issues, "ERROR", "RECONCILIATION_INTERNAL_CHECK_FAILED", f"{bad[:10]}")
    if report_source.get("type") != source.get("type"):
        _issue(issues, "ERROR", "RECONCILIATION_SOURCE_TYPE_MISMATCH", "report source type differs from manifest")
    if report_source.get("sha256") != source.get("sha256") or report_source.get("actual_sha256") != source.get("sha256"):
        _issue(issues, "ERROR", "RECONCILIATION_SOURCE_SHA_MISMATCH", "report source SHA does not match manifest snapshot SHA")
    if bundle.get("messages_jsonl_records") != message_rows:
        _issue(issues, "ERROR", "RECONCILIATION_MESSAGE_COUNT_MISMATCH", f"report={bundle.get('messages_jsonl_records')!r}, file={message_rows}")
    if bundle.get("errors_jsonl_records") != error_rows:
        _issue(issues, "ERROR", "RECONCILIATION_ERROR_COUNT_MISMATCH", f"report={bundle.get('errors_jsonl_records')!r}, file={error_rows}")
    if manifest_counts.get("unsupported") != len(unsupported):
        _issue(issues, "ERROR", "RECONCILIATION_UNSUPPORTED_COUNT_MISMATCH", f"manifest={manifest_counts.get('unsupported')!r}, report={len(unsupported)}")
    if manifest_counts.get("duplicates") != len(duplicates):
        _issue(issues, "ERROR", "RECONCILIATION_DUPLICATE_COUNT_MISMATCH", f"manifest={manifest_counts.get('duplicates')!r}, report={len(duplicates)}")
    if manifest_counts.get("reconciliation_errors") not in (0, None):
        _issue(issues, "ERROR", "A1_RECONCILIATION_ERRORS", f"manifest reports reconciliation_errors={manifest_counts.get('reconciliation_errors')!r}")

    return _finalize(root, base, counts, fingerprints, checks, issues, report)


def _finalize(
    root: Path,
    base: dict[str, Any],
    counts: dict[str, Any],
    fingerprints: dict[str, Any],
    checks: dict[str, Any],
    issues: list[dict[str, Any]],
    report: dict[str, Any] | None,
) -> dict[str, Any]:
    errors = sum(item.get("severity") == "ERROR" for item in issues)
    warnings = sum(item.get("severity") == "WARNING" for item in issues)
    status = STATUS_FAIL if errors else STATUS_WARNING if warnings else STATUS_PASS
    counts["errors"] = int(errors)
    counts["warnings"] = int(warnings)
    return {
        "schema_version": 3,
        "status": status,
        "root": str(root),
        "contract_version": base.get("contract_version"),
        "counts": counts,
        "fingerprints": fingerprints,
        "checks": checks,
        "reconciliation": None
        if report is None
        else {
            "version": report.get("reconciliation_version"),
            "status": report.get("status"),
            "ok": report.get("ok"),
        },
        "issues": issues,
    }
