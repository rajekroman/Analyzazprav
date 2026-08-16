from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from a6.data import analysis_packet, normalize_frame


VERDICT_VALID = "VALID"
VERDICT_INVALID = "INVALID"


def audit_a6_contract() -> dict[str, object]:
    issues: list[dict[str, str]] = []

    frame = pd.DataFrame(
        [
            {
                "membership_id": "mship-101",
                "message_id": "42",
                "conversation_id": "chat-a",
                "contact": "A",
                "sender": "sender-a",
                "timestamp": "2026-01-01T10:00:00Z",
                "text": "one physical message in chat A",
            },
            {
                "membership_id": "mship-102",
                "message_id": "42",
                "conversation_id": "chat-b",
                "contact": "B",
                "sender": "sender-a",
                "timestamp": "2026-01-01T10:00:00Z",
                "text": "one physical message in chat B",
            },
            {
                "membership_id": "mship-103",
                "message_id": "43",
                "conversation_id": "chat-a",
                "contact": "A",
                "sender": "sender-b",
                "timestamp": None,
                "text": "timestamp intentionally unknown",
            },
        ]
    )

    normalized = normalize_frame(frame)
    pairs = {
        (str(row.message_id), str(row.conversation_id))
        for row in normalized.itertuples(index=False)
    }
    expected_pairs = {("42", "chat-a"), ("42", "chat-b"), ("43", "chat-a")}
    if pairs != expected_pairs:
        issues.append(
            {
                "severity": "ERROR",
                "code": "A6_CANONICAL_ROW_LOSS",
                "detail": f"normalize_frame preserved {sorted(pairs)!r}; expected {sorted(expected_pairs)!r}",
            }
        )

    if "membership_id" not in normalized.columns:
        issues.append(
            {
                "severity": "ERROR",
                "code": "A6_MEMBERSHIP_ID_DROPPED",
                "detail": "A2 v5 membership_id is not preserved in the A6 canonical frame",
            }
        )
    else:
        memberships = set(normalized["membership_id"].astype(str))
        expected_memberships = {"mship-101", "mship-102", "mship-103"}
        if memberships != expected_memberships:
            issues.append(
                {
                    "severity": "ERROR",
                    "code": "A6_MEMBERSHIP_SET_MISMATCH",
                    "detail": f"membership set {sorted(memberships)!r} != {sorted(expected_memberships)!r}",
                }
            )

    unknown_time_rows = normalized[normalized["message_id"].astype(str) == "43"]
    if unknown_time_rows.empty:
        issues.append(
            {
                "severity": "ERROR",
                "code": "A6_UNKNOWN_TIMESTAMP_MESSAGE_DROPPED",
                "detail": "A canonical message with unknown timestamp disappeared from the A6 read model",
            }
        )

    if not normalized[normalized["conversation_id"].astype(str) == "chat-a"].empty:
        packet_source = normalized[
            (normalized["conversation_id"].astype(str) == "chat-a")
            & normalized["timestamp"].notna()
        ].reset_index(drop=True)
        packet = analysis_packet(packet_source, ["42"], context_before=0, context_after=0)
        if packet.get("selected_message_ids") != ["42"] or packet.get("selected_message_count") != 1:
            issues.append(
                {
                    "severity": "ERROR",
                    "code": "A6_PACKET_SELECTION_MISMATCH",
                    "detail": "single-conversation packet did not preserve selected canonical message ID",
                }
            )

    return {
        "schema_version": 1,
        "verdict": VERDICT_INVALID if issues else VERDICT_VALID,
        "input_row_count": len(frame),
        "normalized_row_count": len(normalized),
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A7 audit of pinned A6 lossless read-model contract")
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--expect",
        choices=[VERDICT_VALID, VERDICT_INVALID],
        default=VERDICT_VALID,
        help="Expected pinned-module verdict. INVALID is used only to track a known release blocker.",
    )
    args = parser.parse_args(argv)
    report = audit_a6_contract()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["verdict"] == args.expect else 1


if __name__ == "__main__":
    raise SystemExit(main())
