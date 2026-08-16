from __future__ import annotations

import ast
from math import isclose
from typing import Any, Iterable, Mapping, Sequence

STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"

VERDICT_VALID = "VALID"
VERDICT_INVALID = "INVALID"
VERDICT_NEEDS_REVIEW = "NEEDS_REVIEW"


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _finalize(module: str, issues: list[dict[str, Any]], checks: Mapping[str, Any]) -> dict[str, Any]:
    errors = sum(item.get("severity") == "ERROR" for item in issues)
    warnings = sum(item.get("severity") == "WARNING" for item in issues)
    if errors:
        status, verdict = STATUS_FAIL, VERDICT_INVALID
    elif warnings:
        status, verdict = STATUS_WARNING, VERDICT_NEEDS_REVIEW
    else:
        status, verdict = STATUS_PASS, VERDICT_VALID
    return {
        "schema_version": 1,
        "module": module,
        "status": status,
        "verdict": verdict,
        "checks": dict(checks),
        "issues": issues,
    }


def _same_number(actual: Any, expected: Any, *, tolerance: float = 1e-9) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    try:
        return isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)
    except (TypeError, ValueError):
        return False


def _safe_excerpt(text: str) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= 240:
        return normalized
    return normalized[:239].rstrip() + "…"


def _context_message_map(context: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in context.get("messages") or []:
        if not isinstance(row, Mapping):
            raise ValueError("context message must be an object")
        message_id = str(row.get("id"))
        if not message_id or message_id == "None":
            raise ValueError("context message id is missing")
        if message_id in result:
            raise ValueError(f"duplicate context message id: {message_id}")
        result[message_id] = row
    return result


def _validate_evidence_ref(
    evidence: Any,
    *,
    path: str,
    context_messages: Mapping[str, Mapping[str, Any]],
    metrics: Mapping[str, Mapping[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    if not isinstance(evidence, Mapping):
        _issue(issues, "ERROR", "A5_EVIDENCE_OBJECT_MISSING", f"{path} must be an evidence object")
        return

    ids = [str(value) for value in evidence.get("message_ids") or []]
    if not ids:
        _issue(issues, "ERROR", "A5_EVIDENCE_MESSAGE_IDS_EMPTY", f"{path}.message_ids must not be empty")
    if len(ids) != len(set(ids)):
        _issue(issues, "ERROR", "A5_EVIDENCE_MESSAGE_IDS_DUPLICATE", f"{path}.message_ids contains duplicates")
    unknown = [message_id for message_id in ids if message_id not in context_messages]
    if unknown:
        _issue(issues, "ERROR", "A5_EVIDENCE_OUTSIDE_CONTEXT", f"{path} references unknown context IDs {unknown}")

    snapshots = list(evidence.get("messages") or [])
    snapshot_by_id = {
        str(row.get("message_id")): row
        for row in snapshots
        if isinstance(row, Mapping)
    }
    for message_id in ids:
        source = context_messages.get(message_id)
        snapshot = snapshot_by_id.get(message_id)
        if source is None:
            continue
        if snapshot is None:
            _issue(issues, "ERROR", "A5_EVIDENCE_SNAPSHOT_MISSING", f"{path} has no enriched snapshot for {message_id}")
            continue
        expected = {
            "message_id": message_id,
            "timestamp": str(source.get("timestamp")),
            "sender_id": str(source.get("participant_id")),
            "excerpt": _safe_excerpt(str(source.get("text") or "")),
        }
        for name, value in expected.items():
            if str(snapshot.get(name)) != value:
                _issue(
                    issues,
                    "ERROR",
                    "A5_EVIDENCE_SNAPSHOT_MISMATCH",
                    f"{path} {message_id} {name}: expected {value!r}, got {snapshot.get(name)!r}",
                )

    snapshot_ids = [str(row.get("message_id")) for row in snapshots if isinstance(row, Mapping)]
    unexpected_snapshots = sorted(set(snapshot_ids) - set(ids))
    if unexpected_snapshots:
        _issue(
            issues,
            "ERROR",
            "A5_EVIDENCE_SNAPSHOT_EXTRA",
            f"{path} contains snapshots not declared by message_ids: {unexpected_snapshots}",
        )

    for index, metric in enumerate(evidence.get("metrics") or []):
        if not isinstance(metric, Mapping):
            _issue(issues, "ERROR", "A5_METRIC_EVIDENCE_INVALID", f"{path}.metrics[{index}] is not an object")
            continue
        phase = str(metric.get("phase"))
        name = str(metric.get("name"))
        phase_metrics = metrics.get(phase) or {}
        if phase not in {"before", "during", "after"} or name not in phase_metrics:
            _issue(
                issues,
                "ERROR",
                "A5_METRIC_EVIDENCE_OUTSIDE_CONTEXT",
                f"{path}.metrics[{index}] references {phase}.{name}",
            )
            continue
        if not _same_number(metric.get("value"), phase_metrics[name]):
            _issue(
                issues,
                "ERROR",
                "A5_METRIC_EVIDENCE_VALUE_MISMATCH",
                f"{path}.metrics[{index}] expected value {phase_metrics[name]!r}, got {metric.get('value')!r}",
            )


def validate_a5_result(context: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    try:
        context_messages = _context_message_map(context)
    except ValueError as exc:
        _issue(issues, "ERROR", "A5_CONTEXT_INVALID", str(exc))
        return _finalize("A5", issues, checks)

    metrics = context.get("metrics") or {}
    metrics_by_phase = {
        "before": metrics.get("before") or {},
        "during": metrics.get("during") or {},
        "after": metrics.get("after") or {},
    }
    checks["context_message_count"] = len(context_messages)

    _validate_evidence_ref(
        result.get("summary_evidence"),
        path="summary_evidence",
        context_messages=context_messages,
        metrics=metrics_by_phase,
        issues=issues,
    )

    for index, row in enumerate(result.get("observations") or []):
        _validate_evidence_ref(
            row.get("evidence") if isinstance(row, Mapping) else None,
            path=f"observations[{index}].evidence",
            context_messages=context_messages,
            metrics=metrics_by_phase,
            issues=issues,
        )

    for collection_name in ("interpretations", "patterns"):
        for index, row in enumerate(result.get(collection_name) or []):
            _validate_evidence_ref(
                row.get("evidence") if isinstance(row, Mapping) else None,
                path=f"{collection_name}[{index}].evidence",
                context_messages=context_messages,
                metrics=metrics_by_phase,
                issues=issues,
            )

    turning_points = list(result.get("turning_points") or [])
    turning_evidence = list(result.get("turning_point_evidence") or [])
    if len(turning_points) != len(turning_evidence):
        _issue(
            issues,
            "ERROR",
            "A5_TURNING_POINT_EVIDENCE_COUNT_MISMATCH",
            f"{len(turning_points)} turning points but {len(turning_evidence)} evidence refs",
        )
    for index, evidence in enumerate(turning_evidence):
        _validate_evidence_ref(
            evidence,
            path=f"turning_point_evidence[{index}]",
            context_messages=context_messages,
            metrics=metrics_by_phase,
            issues=issues,
        )

    for field_name in ("participant_p1", "participant_p2", "shared_dynamic"):
        value = result.get(field_name)
        evidence = result.get(f"{field_name}_evidence")
        if value not in (None, "") and evidence is None:
            _issue(issues, "ERROR", "A5_ASSERTION_EVIDENCE_MISSING", f"{field_name} is populated without evidence")
        if evidence is not None:
            _validate_evidence_ref(
                evidence,
                path=f"{field_name}_evidence",
                context_messages=context_messages,
                metrics=metrics_by_phase,
                issues=issues,
            )

    checks["assertion_evidence_surface_checked"] = True
    return _finalize("A5", issues, checks)


def _rows_by_membership(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        membership = str(row.get("membership_id"))
        if not membership or membership == "None":
            raise ValueError("missing membership_id")
        if membership in result:
            raise ValueError(f"duplicate membership_id {membership}")
        result[membership] = row
    return result


def _canonical_rows(rows: Iterable[Mapping[str, Any]]) -> list[tuple[tuple[str, str], ...]]:
    normalized: list[tuple[tuple[str, str], ...]] = []
    for row in rows:
        normalized.append(tuple(sorted((str(key), repr(value)) for key, value in row.items())))
    return sorted(normalized)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _result_get_key(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr != "get" or not isinstance(node.func.value, ast.Name) or node.func.value.id != "result":
        return None
    if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
        return None
    return node.args[0].value


def _function_calls(function: ast.FunctionDef, target: str) -> list[ast.Call]:
    return [node for node in ast.walk(function) if isinstance(node, ast.Call) and _call_name(node) == target]


def validate_a6_renderer_source(source: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        _issue(issues, "ERROR", "A6_RENDERER_SYNTAX_ERROR", str(exc))
        return issues

    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    required = {"render_a5_execution", "render_assertion", "render_evidence_ref", "render_result_evidence"}
    missing = sorted(required - set(functions))
    if missing:
        _issue(issues, "ERROR", "A6_RENDERER_FUNCTION_MISSING", ", ".join(missing))
        return issues

    direct_keys: set[str] = set()
    for call in _function_calls(functions["render_a5_execution"], "render_assertion"):
        for argument in call.args:
            key = _result_get_key(argument)
            if key:
                direct_keys.add(key)
    for key in ("summary", "participant_p1", "participant_p2", "shared_dynamic"):
        if key not in direct_keys:
            _issue(
                issues,
                "ERROR",
                "A6_ASSERTION_RENDER_NOT_EVIDENCE_AWARE",
                f"render_a5_execution does not route result.{key} through render_assertion",
            )

    execution_text = ast.unparse(functions["render_a5_execution"])
    if "turning_point_evidence" not in execution_text or "render_assertion" not in execution_text:
        _issue(
            issues,
            "ERROR",
            "A6_TURNING_POINT_EVIDENCE_NOT_RENDERED",
            "Turning-point evidence is not routed through assertion rendering",
        )
    if not _function_calls(functions["render_assertion"], "render_evidence_ref"):
        _issue(issues, "ERROR", "A6_ASSERTION_EVIDENCE_CHAIN_BROKEN", "render_assertion does not call render_evidence_ref")
    if not _function_calls(functions["render_evidence_ref"], "render_result_evidence"):
        _issue(issues, "ERROR", "A6_MESSAGE_EVIDENCE_CHAIN_BROKEN", "render_evidence_ref does not call render_result_evidence")
    return issues


def validate_a6_contract(
    *,
    expected_memberships: Sequence[Mapping[str, Any]],
    actual_rows: Sequence[Mapping[str, Any]],
    packet: Mapping[str, Any],
    requested_selected_ids: Sequence[str],
    expected_message_sources: Sequence[Mapping[str, Any]] = (),
    actual_message_sources: Sequence[Mapping[str, Any]] = (),
    expected_attachments: Sequence[Mapping[str, Any]] = (),
    actual_attachments: Sequence[Mapping[str, Any]] = (),
    expected_attachment_sources: Sequence[Mapping[str, Any]] = (),
    actual_attachment_sources: Sequence[Mapping[str, Any]] = (),
    renderer_source: str | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    try:
        expected_by_membership = _rows_by_membership(expected_memberships)
        actual_by_membership = _rows_by_membership(actual_rows)
    except ValueError as exc:
        _issue(issues, "ERROR", "A6_MEMBERSHIP_ID_INVALID", str(exc))
        return _finalize("A6", issues, checks)

    expected_ids = set(expected_by_membership)
    actual_ids = set(actual_by_membership)
    checks["expected_membership_count"] = len(expected_ids)
    checks["actual_membership_count"] = len(actual_ids)
    if expected_ids != actual_ids:
        _issue(
            issues,
            "ERROR",
            "A6_MEMBERSHIP_SET_MISMATCH",
            f"missing={sorted(expected_ids - actual_ids)}, extra={sorted(actual_ids - expected_ids)}",
        )

    for membership_id, expected in expected_by_membership.items():
        actual = actual_by_membership.get(membership_id)
        if actual is None:
            continue
        for name in ("message_id", "conversation_id"):
            if str(actual.get(name)) != str(expected.get(name)):
                _issue(
                    issues,
                    "ERROR",
                    "A6_MEMBERSHIP_IDENTITY_MISMATCH",
                    f"membership {membership_id} {name}: expected {expected.get(name)!r}, got {actual.get(name)!r}",
                )
        if bool(actual.get("timestamp_known")) != bool(expected.get("timestamp_known")):
            _issue(
                issues,
                "ERROR",
                "A6_TIMESTAMP_PRESERVATION_MISMATCH",
                f"membership {membership_id} timestamp-known state changed",
            )

    selected_ids = [str(value) for value in packet.get("selected_message_ids") or []]
    requested = [str(value) for value in requested_selected_ids]
    if selected_ids != requested:
        _issue(issues, "ERROR", "A6_PACKET_SELECTION_MISMATCH", f"expected selected IDs {requested}, got {selected_ids}")
    if len(selected_ids) != len(set(selected_ids)):
        _issue(issues, "ERROR", "A6_PACKET_DUPLICATE_SELECTED_ID", "Packet selected_message_ids contains duplicates")

    packet_messages = list(packet.get("messages") or [])
    packet_ids = [str(row.get("message_id")) for row in packet_messages]
    if len(packet_ids) != len(set(packet_ids)):
        _issue(issues, "ERROR", "A6_PACKET_DUPLICATE_MESSAGE_ID", "Packet message IDs are not unique inside one conversation scope")
    conversations = {str(row.get("conversation_id")) for row in packet_messages}
    if len(conversations) != 1:
        _issue(issues, "ERROR", "A6_PACKET_CROSSES_CONVERSATIONS", f"Packet contains conversation IDs {sorted(conversations)}")
    selected_rows = [row for row in packet_messages if bool(row.get("selected"))]
    if {str(row.get("message_id")) for row in selected_rows} != set(selected_ids):
        _issue(issues, "ERROR", "A6_PACKET_SELECTED_FLAGS_MISMATCH", "selected=true rows do not equal selected_message_ids")
    if any(not str(row.get("membership_id") or "").strip() for row in packet_messages):
        _issue(issues, "ERROR", "A6_PACKET_MEMBERSHIP_ID_MISSING", "At least one packet message lacks membership_id")

    table_pairs = (
        ("message_sources", expected_message_sources, actual_message_sources),
        ("attachments", expected_attachments, actual_attachments),
        ("attachment_sources", expected_attachment_sources, actual_attachment_sources),
    )
    for name, expected, actual in table_pairs:
        if _canonical_rows(expected) != _canonical_rows(actual):
            _issue(issues, "ERROR", "A6_PROVENANCE_PROJECTION_MISMATCH", f"{name} rows differ from authoritative downstream view")
        checks[f"{name}_row_count"] = len(actual)

    if renderer_source is None:
        _issue(issues, "WARNING", "A6_RENDERER_NOT_CHECKED", "No A6 renderer source supplied for assertion-evidence routing audit")
    else:
        issues.extend(validate_a6_renderer_source(renderer_source))
        checks["renderer_evidence_chain_checked"] = True

    return _finalize("A6", issues, checks)


def aggregate_release_verdict(
    component_reports: Mapping[str, Mapping[str, Any] | None],
    *,
    job_results: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    components: dict[str, str] = {}
    jobs = dict(job_results or {})

    for name, report in component_reports.items():
        job_result = jobs.get(name)
        if job_result is not None and job_result != "success":
            components[name] = VERDICT_INVALID if job_result == "failure" else VERDICT_NEEDS_REVIEW
            _issue(
                issues,
                "ERROR" if job_result == "failure" else "WARNING",
                "A7_COMPONENT_JOB_NOT_SUCCESS",
                f"{name} job result is {job_result}",
            )
            continue
        if report is None:
            components[name] = VERDICT_NEEDS_REVIEW
            _issue(issues, "WARNING", "A7_COMPONENT_REPORT_MISSING", f"{name} report is missing")
            continue
        verdict = str(report.get("verdict") or VERDICT_NEEDS_REVIEW)
        if verdict not in {VERDICT_VALID, VERDICT_INVALID, VERDICT_NEEDS_REVIEW}:
            verdict = VERDICT_NEEDS_REVIEW
            _issue(issues, "WARNING", "A7_COMPONENT_VERDICT_UNKNOWN", f"{name} report has unknown verdict")
        components[name] = verdict

    if any(value == VERDICT_INVALID for value in components.values()):
        overall = VERDICT_INVALID
    elif any(value == VERDICT_NEEDS_REVIEW for value in components.values()):
        overall = VERDICT_NEEDS_REVIEW
    else:
        overall = VERDICT_VALID
    return {
        "schema_version": 1,
        "overall_verdict": overall,
        "release_ready": overall == VERDICT_VALID,
        "components": components,
        "issues": issues,
    }
