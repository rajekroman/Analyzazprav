from __future__ import annotations

from typing import Any, Mapping

PASS = "PASS"
FAIL = "FAIL"
WARNING = "WARNING"


def validate_a6_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    checks: dict[str, Any] = {}

    def issue(severity: str, code: str, detail: str) -> None:
        issues.append({"severity": severity, "code": code, "detail": detail})

    messages = packet.get("messages")
    if not isinstance(messages, list):
        issue("ERROR", "A6_PACKET_MESSAGES_INVALID", "messages must be an array")
        return _finish(checks, issues)

    selected_raw = packet.get("selected_message_ids", [])
    if not isinstance(selected_raw, list):
        issue("ERROR", "A6_PACKET_SELECTED_IDS_INVALID", "selected_message_ids must be an array")
        selected_raw = []
    selected = [str(value) for value in selected_raw]
    if len(selected) != len(set(selected)):
        issue("ERROR", "A6_PACKET_SELECTED_IDS_DUPLICATE", "selected_message_ids contains duplicates")

    memberships: list[str] = []
    message_ids: list[str] = []
    conversation_ids: list[str] = []
    selected_from_rows: list[str] = []
    provenance_missing: list[str] = []
    provenance_status_bad: list[str] = []
    unknown_time: list[str] = []
    for index, raw in enumerate(messages):
        if not isinstance(raw, Mapping):
            issue("ERROR", "A6_PACKET_MESSAGE_INVALID", f"messages[{index}] is not an object")
            continue
        message_id = str(raw.get("message_id", ""))
        membership_id = str(raw.get("membership_id", ""))
        conversation_id = str(raw.get("conversation_id", ""))
        if not message_id:
            issue("ERROR", "A6_PACKET_MESSAGE_ID_MISSING", f"messages[{index}] lacks message_id")
        if not membership_id:
            issue("ERROR", "A6_PACKET_MEMBERSHIP_ID_MISSING", f"message {message_id!r} lacks membership_id")
        if not conversation_id:
            issue("ERROR", "A6_PACKET_CONVERSATION_ID_MISSING", f"message {message_id!r} lacks conversation_id")
        message_ids.append(message_id)
        memberships.append(membership_id)
        conversation_ids.append(conversation_id)
        if bool(raw.get("selected")):
            selected_from_rows.append(message_id)
        if raw.get("timestamp") in (None, ""):
            unknown_time.append(message_id)
        if packet.get("source_provenance_required"):
            keys = raw.get("source_record_keys")
            snapshots = raw.get("source_snapshot_keys")
            parsers = raw.get("source_parser_versions")
            if (
                not isinstance(keys, list) or not keys
                or not isinstance(snapshots, list) or not snapshots
                or not isinstance(parsers, list) or not parsers
            ):
                provenance_missing.append(message_id)
            if raw.get("source_provenance_status") != "complete":
                provenance_status_bad.append(message_id)

    duplicate_memberships = sorted({value for value in memberships if memberships.count(value) > 1 and value})
    if duplicate_memberships:
        issue(
            "ERROR",
            "A6_PACKET_MEMBERSHIP_DUPLICATE",
            f"Duplicate membership IDs: {duplicate_memberships[:10]!r}",
        )
    nonempty_conversations = {value for value in conversation_ids if value}
    if len(nonempty_conversations) > 1:
        issue(
            "ERROR",
            "A6_PACKET_MULTIPLE_CONVERSATIONS",
            f"Temporal A5 packet spans multiple conversations: {sorted(nonempty_conversations)!r}",
        )
    if set(selected) != set(selected_from_rows):
        issue(
            "ERROR",
            "A6_PACKET_SELECTED_FLAG_MISMATCH",
            f"selected_message_ids={sorted(set(selected))!r}; selected rows={sorted(set(selected_from_rows))!r}",
        )
    if not selected:
        issue("ERROR", "A6_PACKET_SELECTION_EMPTY", "At least one selected message is required")
    if not set(selected).issubset(set(message_ids)):
        issue("ERROR", "A6_PACKET_SELECTED_MESSAGE_MISSING", "A selected message is absent from packet messages")
    if unknown_time:
        issue(
            "ERROR",
            "A6_PACKET_UNKNOWN_TIMESTAMP",
            f"A5 temporal packet contains unknown timestamps: {sorted(set(unknown_time))[:10]!r}",
        )
    if provenance_missing:
        issue(
            "ERROR",
            "A6_PACKET_SOURCE_PROVENANCE_MISSING",
            f"Production packet lacks source provenance: {sorted(set(provenance_missing))[:10]!r}",
        )
    if provenance_status_bad:
        issue(
            "ERROR",
            "A6_PACKET_MESSAGE_PROVENANCE_STATUS_INVALID",
            f"Production packet message provenance status is not complete: {sorted(set(provenance_status_bad))[:10]!r}",
        )
    declared_message_count = packet.get("message_count")
    if declared_message_count is not None and declared_message_count != len(messages):
        issue(
            "ERROR",
            "A6_PACKET_MESSAGE_COUNT_MISMATCH",
            f"message_count={declared_message_count!r}; actual={len(messages)}",
        )
    declared_selected_count = packet.get("selected_message_count")
    if declared_selected_count is not None and declared_selected_count != len(selected):
        issue(
            "ERROR",
            "A6_PACKET_SELECTED_COUNT_MISMATCH",
            f"selected_message_count={declared_selected_count!r}; actual={len(selected)}",
        )
    missing_declared = packet.get("source_provenance_missing_message_ids")
    if packet.get("source_provenance_required") and missing_declared not in (None, []):
        issue(
            "ERROR",
            "A6_PACKET_PROVENANCE_MISSING_LIST_NONEMPTY",
            "Production packet declares missing source provenance while requiring complete provenance.",
        )
    if packet.get("source_provenance_required") and packet.get("source_provenance_status") != "complete":
        issue(
            "ERROR",
            "A6_PACKET_SOURCE_PROVENANCE_STATUS_INVALID",
            "Production packet source_provenance_status must be complete.",
        )
    if not packet.get("source_provenance_required"):
        issue(
            "WARNING",
            "A6_PACKET_SOURCE_PROVENANCE_UNVERIFIED",
            "Packet is demo/non-production and does not require A2 source provenance.",
        )

    checks.update(
        message_count=len(messages),
        selected_message_count=len(selected),
        unique_membership_count=len(set(value for value in memberships if value)),
        source_provenance_required=bool(packet.get("source_provenance_required")),
    )
    return _finish(checks, issues)


def _finish(checks: dict[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
    errors = sum(item["severity"] == "ERROR" for item in issues)
    warnings = sum(item["severity"] == "WARNING" for item in issues)
    status = FAIL if errors else WARNING if warnings else PASS
    return {
        "schema_version": 1,
        "status": status,
        "checks": checks,
        "counts": {"errors": errors, "warnings": warnings},
        "issues": issues,
    }
