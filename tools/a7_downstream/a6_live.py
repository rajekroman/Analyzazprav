from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from analyzazprav.normalization import CanonicalDatabase, MessageInput
from a6.attachments import load_attachment_sources, load_message_attachments
from a6.data import analysis_packet, load_sqlite_messages
from a6.provenance import load_message_sources

from tools.a7_downstream.common import load_downstream_validator, write_report


CONTRACT_SHA = "f54c2807176515f509af50f340ea8f60a1ff7aea"


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


def _stringify_ids(rows: list[dict], columns: tuple[str, ...]) -> list[dict]:
    for row in rows:
        for column in columns:
            if row.get(column) is not None:
                row[column] = str(row[column])
    return rows


def _query_rows(conn, query: str, params=()) -> list[dict]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _build_database(path: Path) -> tuple[int, int, int]:
    db = CanonicalDatabase(path)
    db.initialize()
    run = db.begin_import(
        source_type="imessage_chat_db",
        source_fingerprint="a7-a6-live-v1",
        source_sha256="a" * 64,
        parser_version="a7-fixture",
    )
    p1 = db.get_or_create_participant(identity_type="email", identity_value="p1@example.test", canonical_name="P1")
    p2 = db.get_or_create_participant(identity_type="email", identity_value="p2@example.test", canonical_name="P2")
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
    shared = db.insert_message(
        _message(
            run_id=run.id,
            conversation_id=c1,
            sender_id=p1,
            timestamp_us=1_000_000,
            source_message_id="1",
            source_conversation_id="chat-1",
            source_record_key=shared_key,
            text="shared message",
        )
    )
    shared_again = db.insert_message(
        _message(
            run_id=run.id,
            conversation_id=c2,
            sender_id=p1,
            timestamp_us=1_000_000,
            source_message_id="1",
            source_conversation_id="chat-2",
            source_record_key=shared_key,
            text="shared message",
        )
    )
    if shared_again != shared:
        raise AssertionError("Fixture failed to create one canonical message with two memberships")

    db.insert_message(
        _message(
            run_id=run.id,
            conversation_id=c1,
            sender_id=p2,
            timestamp_us=None,
            source_message_id="2",
            source_conversation_id="chat-1",
            source_record_key="2" * 64,
            text="unknown timestamp",
        )
    )
    db.insert_message(
        _message(
            run_id=run.id,
            conversation_id=c1,
            sender_id=p2,
            timestamp_us=2_000_000,
            source_message_id="3",
            source_conversation_id="chat-1",
            source_record_key="3" * 64,
            text="known timestamp",
        )
    )

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
    parser.add_argument("--a6-root", default="a6-under-test")
    args = parser.parse_args()

    validator = load_downstream_validator()
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "messages.sqlite"
        c1, c2, shared = _build_database(db_path)

        frame, info = load_sqlite_messages(db_path)
        if info.object_name != "analysis_messages":
            raise AssertionError(f"A6 did not choose authoritative A2 view: {info.object_name}")

        db = CanonicalDatabase(db_path)
        expected_memberships = _query_rows(
            db.conn,
            "SELECT membership_id, id AS message_id, conversation_id, sent_at_utc_us FROM analysis_messages ORDER BY membership_id",
        )
        expected_memberships = [
            {
                "membership_id": str(row["membership_id"]),
                "message_id": str(row["message_id"]),
                "conversation_id": str(row["conversation_id"]),
                "timestamp_known": row["sent_at_utc_us"] is not None,
            }
            for row in expected_memberships
        ]
        actual_rows = [
            {
                "membership_id": str(row.membership_id),
                "message_id": str(row.message_id),
                "conversation_id": str(row.conversation_id),
                "timestamp_known": not pd.isna(row.timestamp),
            }
            for row in frame.itertuples(index=False)
        ]

        conversation = frame[frame["conversation_id"].astype(str) == str(c1)].reset_index(drop=True)
        selected_id = str(shared)
        packet = analysis_packet(conversation, [selected_id], context_before=0, context_after=0)

        expected_sources = _query_rows(
            db.conn,
            "SELECT message_id, source_type, source_message_id, source_conversation_id, source_row_id, source_record_key, source_contract_version, raw_timestamp, raw_text, source_hash, import_run_id FROM analysis_message_sources WHERE message_id=? ORDER BY import_run_id",
            (shared,),
        )
        expected_sources = _stringify_ids(expected_sources, ("message_id",))
        actual_sources = load_message_sources(db_path, [selected_id]).to_dict(orient="records")

        expected_attachments = _query_rows(
            db.conn,
            "SELECT occurrence_id, message_id, attachment_id, sha256, mime_type, size_bytes, filename, storage_path, availability, position FROM analysis_attachments WHERE message_id=? ORDER BY occurrence_id",
            (shared,),
        )
        expected_attachments = _stringify_ids(expected_attachments, ("occurrence_id", "message_id", "attachment_id"))
        actual_attachments_frame = load_message_attachments(db_path, [selected_id])
        actual_attachments = actual_attachments_frame.to_dict(orient="records")

        occurrence_ids = list(actual_attachments_frame["occurrence_id"].astype(str))
        expected_attachment_sources = _query_rows(
            db.conn,
            "SELECT attachment_source_id, attachment_id, occurrence_id, message_id, position, import_run_id, source_type, source_snapshot_key, source_sha256, parser_version, source_attachment_id, source_occurrence_key, original_filename, original_path FROM analysis_attachment_sources WHERE message_id=? ORDER BY attachment_source_id",
            (shared,),
        )
        expected_attachment_sources = _stringify_ids(
            expected_attachment_sources,
            ("attachment_source_id", "attachment_id", "occurrence_id", "message_id"),
        )
        actual_attachment_sources = load_attachment_sources(db_path, occurrence_ids).to_dict(orient="records")
        db.close()

        renderer_source = (Path(args.a6_root) / "app.py").read_text(encoding="utf-8")
        report = validator.validate_a6_contract(
            expected_memberships=expected_memberships,
            actual_rows=actual_rows,
            packet=packet,
            requested_selected_ids=[selected_id],
            expected_message_sources=expected_sources,
            actual_message_sources=actual_sources,
            expected_attachments=expected_attachments,
            actual_attachments=actual_attachments,
            expected_attachment_sources=expected_attachment_sources,
            actual_attachment_sources=actual_attachment_sources,
            renderer_source=renderer_source,
        )
        report["contract_sha"] = CONTRACT_SHA
        report["checks"].update(
            {
                "canonical_message_count": int(frame["message_id"].nunique()),
                "membership_count": len(frame),
                "unknown_timestamp_memberships": int(frame["timestamp"].isna().sum()),
                "shared_message_memberships": int((frame["message_id"].astype(str) == selected_id).sum()),
                "chat_1": c1,
                "chat_2": c2,
            }
        )
        if report["checks"]["shared_message_memberships"] != 2:
            report["issues"].append(
                {"severity": "ERROR", "code": "A6_LIVE_MULTICHAT_MEMBERSHIP_LOSS", "detail": "Shared canonical message must remain visible in both conversation memberships"}
            )
            report["status"] = "FAIL"
            report["verdict"] = "INVALID"
        if report["checks"]["unknown_timestamp_memberships"] != 1:
            report["issues"].append(
                {"severity": "ERROR", "code": "A6_LIVE_UNKNOWN_TIMESTAMP_LOSS", "detail": "Exactly one unknown-time canonical membership must remain visible"}
            )
            report["status"] = "FAIL"
            report["verdict"] = "INVALID"

        write_report(report, args.report)
        return 0 if report["verdict"] == "VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
