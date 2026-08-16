from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from analyzazprav.a5_ai.integration_a6 import A6PacketError, A6PacketMessageSource
from analyzazprav.normalization import CanonicalDatabase, MessageInput
from analyzazprav.qa.a6_contract import validate_a6_packet
from a6.attachments import load_attachment_sources, load_message_attachments
from a6.data import analysis_packet, load_sqlite_messages
from a6.evidence import enrich_analysis_packet_source_provenance
from a6.provenance import load_message_sources
from tools.a7_release.common import finalize, issue, write_report


def _message(
    *,
    run_id: int,
    conversation_id: int,
    sender_id: int,
    timestamp_us: int | None,
    source_message_id: str,
    source_conversation_id: str,
    source_record_key: str,
    text: str,
) -> MessageInput:
    return MessageInput(
        import_run_id=run_id,
        source_type="imessage_chat_db",
        conversation_id=conversation_id,
        sender_id=sender_id,
        sent_at_utc_us=timestamp_us,
        timestamp_precision="microsecond" if timestamp_us is not None else "unknown",
        timestamp_quality="exact" if timestamp_us is not None else "unknown",
        direction="incoming",
        message_type="text",
        text=text,
        service="iMessage",
        source_message_id=source_message_id,
        source_conversation_id=source_conversation_id,
        source_row_id=source_message_id,
        source_record_key=source_record_key,
        source_contract_version="1",
        raw_timestamp=None if timestamp_us is None else str(timestamp_us),
        raw_text=text,
    )


def _build_database(path: Path) -> tuple[int, int, int]:
    db = CanonicalDatabase(path)
    db.initialize()
    run = db.begin_import(
        source_type="imessage_chat_db",
        source_fingerprint="a7-current-main-a6-live",
        source_sha256="a" * 64,
        parser_version="a7-current-fixture",
    )
    p1 = db.get_or_create_participant(
        identity_type="email", identity_value="p1@example.test", canonical_name="P1"
    )
    p2 = db.get_or_create_participant(
        identity_type="email", identity_value="p2@example.test", canonical_name="P2"
    )
    c1 = db.get_or_create_conversation(
        source_type="imessage_chat_db",
        source_conversation_id="chat-1",
        import_run_id=run.id,
        title="Chat 1",
        participant_ids=(p1, p2),
    )
    c2 = db.get_or_create_conversation(
        source_type="imessage_chat_db",
        source_conversation_id="chat-2",
        import_run_id=run.id,
        title="Chat 2",
        participant_ids=(p1, p2),
    )

    shared_key = "1" * 64
    shared = db.insert_message(_message(
        run_id=run.id,
        conversation_id=c1,
        sender_id=p1,
        timestamp_us=1_000_000,
        source_message_id="1",
        source_conversation_id="chat-1",
        source_record_key=shared_key,
        text="shared message",
    ))
    shared_again = db.insert_message(_message(
        run_id=run.id,
        conversation_id=c2,
        sender_id=p1,
        timestamp_us=1_000_000,
        source_message_id="1",
        source_conversation_id="chat-2",
        source_record_key=shared_key,
        text="shared message",
    ))
    if shared_again != shared:
        raise AssertionError("Fixture failed to create one canonical message with two memberships")

    db.insert_message(_message(
        run_id=run.id,
        conversation_id=c1,
        sender_id=p2,
        timestamp_us=None,
        source_message_id="2",
        source_conversation_id="chat-1",
        source_record_key="2" * 64,
        text="unknown timestamp",
    ))
    db.insert_message(_message(
        run_id=run.id,
        conversation_id=c1,
        sender_id=p2,
        timestamp_us=2_000_000,
        source_message_id="3",
        source_conversation_id="chat-1",
        source_record_key="3" * 64,
        text="known timestamp",
    ))

    db.add_attachment(
        message_id=shared,
        import_run_id=run.id,
        sha256_value="b" * 64,
        mime_type="image/png",
        size_bytes=123,
        filename="photo.png",
        availability="missing",
        source_attachment_id="att-1",
        original_filename="photo.png",
        original_path="~/Library/Messages/Attachments/photo.png",
        position=0,
    )
    with db.conn:
        db.conn.execute(
            "UPDATE attachment_source SET source_occurrence_key=? WHERE import_run_id=? AND source_attachment_id=?",
            ("occurrence-att-1", run.id, "att-1"),
        )
    db.finish_import(run.id, statistics={"fixture": True})
    db.close()
    return c1, c2, shared


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--contract-sha", required=True)
    args = parser.parse_args()

    checks: dict[str, object] = {}
    issues: list[dict[str, str]] = []
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "messages.sqlite"
        c1, c2, shared = _build_database(db_path)
        frame, info = load_sqlite_messages(db_path)
        checks["authoritative_a2_view"] = info.object_name == "analysis_messages"
        if not checks["authoritative_a2_view"]:
            issues.append(issue("ERROR", "A7_A6_NONAUTHORITATIVE_READ_MODEL", info.object_name))

        db = CanonicalDatabase(db_path)
        expected_memberships = {
            str(row["membership_id"])
            for row in db.conn.execute("SELECT membership_id FROM analysis_messages").fetchall()
        }
        actual_memberships = set(frame["membership_id"].astype(str))
        checks["membership_set_exact"] = expected_memberships == actual_memberships
        checks["membership_count"] = len(actual_memberships)
        if not checks["membership_set_exact"]:
            issues.append(issue(
                "ERROR", "A7_A6_MEMBERSHIP_SET_MISMATCH",
                f"missing={sorted(expected_memberships - actual_memberships)!r}, extra={sorted(actual_memberships - expected_memberships)!r}",
            ))

        selected_id = str(shared)
        checks["shared_message_memberships"] = int((frame["message_id"].astype(str) == selected_id).sum())
        checks["unknown_timestamp_memberships"] = int(frame["timestamp"].isna().sum())
        if checks["shared_message_memberships"] != 2:
            issues.append(issue("ERROR", "A7_A6_MULTICHAT_MEMBERSHIP_LOSS", "Shared canonical message must keep two memberships."))
        if checks["unknown_timestamp_memberships"] != 1:
            issues.append(issue("ERROR", "A7_A6_UNKNOWN_TIMESTAMP_LOSS", "Exactly one unknown-time membership must remain visible."))

        conversation = frame[frame["conversation_id"].astype(str) == str(c1)].reset_index(drop=True)
        base_packet = analysis_packet(conversation, [selected_id], context_before=0, context_after=0)
        packet = enrich_analysis_packet_source_provenance(base_packet, db_path)
        packet_report = validate_a6_packet(packet)
        checks["a7_packet_oracle_status"] = packet_report["status"]
        checks["packet_source_provenance_status"] = packet.get("source_provenance_status")
        if packet_report["status"] != "PASS":
            issues.append(issue("ERROR", "A7_A6_PACKET_ORACLE_FAILED", str(packet_report.get("issues"))))

        a5_source = A6PacketMessageSource.from_packet(packet)
        adapted = a5_source.messages[0]
        packet_row = packet["messages"][0]
        checks.update({
            "a5_membership_preserved": adapted.membership_id == str(packet_row["membership_id"]),
            "a5_source_records_preserved": list(adapted.source_record_keys) == packet_row["source_record_keys"],
            "a5_source_snapshots_preserved": list(adapted.source_snapshot_keys) == packet_row["source_snapshot_keys"],
            "a5_parser_versions_preserved": list(adapted.source_parser_versions) == packet_row["source_parser_versions"],
        })
        for name in (
            "a5_membership_preserved", "a5_source_records_preserved",
            "a5_source_snapshots_preserved", "a5_parser_versions_preserved",
        ):
            if checks[name] is not True:
                issues.append(issue("ERROR", "A7_A6_A5_ADAPTER_PROVENANCE_LOSS", name))

        source_rows = load_message_sources(db_path, [selected_id])
        checks["message_source_rows_visible"] = len(source_rows) >= 1
        attachment_rows = load_message_attachments(db_path, [selected_id])
        checks["attachment_occurrence_count"] = len(attachment_rows)
        occurrence_ids = list(attachment_rows["occurrence_id"].astype(str))
        attachment_source_rows = load_attachment_sources(db_path, occurrence_ids)
        checks["attachment_source_count"] = len(attachment_source_rows)
        if len(source_rows) < 1 or len(attachment_rows) != 1 or len(attachment_source_rows) != 1:
            issues.append(issue(
                "ERROR", "A7_A6_PROVENANCE_PROJECTION_LOSS",
                f"message_sources={len(source_rows)}, attachments={len(attachment_rows)}, attachment_sources={len(attachment_source_rows)}",
            ))

        corrupted = deepcopy(packet)
        corrupted["messages"][0]["source_record_keys"] = []
        corrupted_packet_report = validate_a6_packet(corrupted)
        a5_rejected = False
        try:
            A6PacketMessageSource.from_packet(corrupted)
        except A6PacketError:
            a5_rejected = True
        checks["negative_missing_source_provenance_rejected"] = (
            corrupted_packet_report["status"] == "FAIL" and a5_rejected
        )
        if not checks["negative_missing_source_provenance_rejected"]:
            issues.append(issue(
                "ERROR", "A7_A6_NEGATIVE_PROBE_FAILED",
                "A7 packet oracle and A5 adapter must both reject missing production source provenance.",
            ))
        db.close()

    report = finalize("A6", checks, issues, contract_sha=args.contract_sha)
    write_report(report, args.report)
    return 0 if report["verdict"] == "VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
