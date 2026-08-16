from __future__ import annotations

from pathlib import Path
import sqlite3

from analyzazprav.analytics import (
    AnalyticsConfig,
    AnalyticsStore,
    analyze_database,
    analyze_incremental_database,
)
from analyzazprav.normalization import CanonicalDatabase, MessageInput
from analyzazprav.processing import (
    ProcessingConfig,
    ProcessingStore,
    load_a2_projection,
    process_messages,
)
from analyzazprav.qa import STATUS_FAIL, STATUS_PASS, validate_a4_metrics


ROOT = Path(__file__).resolve().parents[1]
DAY_US = 86_400_000_000


def _insert(
    db: CanonicalDatabase,
    *,
    import_run_id: int,
    conversation_id: int,
    sender_id: int,
    message_number: int,
    timestamp_us: int,
    text: str,
) -> int:
    guid = f"A7-A4-{message_number}"
    return db.insert_message(
        MessageInput(
            import_run_id=import_run_id,
            source_type="fixture",
            conversation_id=conversation_id,
            sender_id=sender_id,
            sent_at_utc_us=timestamp_us,
            timezone_offset_min=60,
            timestamp_precision="microsecond",
            timestamp_quality="exact",
            direction="incoming" if sender_id == 1 else "outgoing",
            message_type="text",
            text=text,
            service="iMessage",
            canonical_guid=guid,
            source_message_id=guid,
            source_conversation_id="oracle-chat",
            source_row_id=str(message_number),
            source_record_key=f"{message_number:064x}",
            raw_timestamp=str(timestamp_us),
            raw_text=text,
            raw_payload={"rowid": message_number},
        )
    )


def _build_database(path: Path) -> int:
    db = CanonicalDatabase(path, schema_path=ROOT / "database" / "schema.sql")
    db.initialize()
    run = db.begin_import(source_type="fixture", source_fingerprint="a7-a4-oracle")
    alice = db.get_or_create_participant(
        identity_type="phone", identity_value="+420777000001", canonical_name="Alice"
    )
    owner = db.get_or_create_participant(
        identity_type="email", identity_value="owner@example.cz", canonical_name="Owner", is_self=True
    )
    conversation = db.get_or_create_conversation(
        source_type="fixture",
        source_conversation_id="oracle-chat",
        import_run_id=run.id,
        canonical_key="fixture:oracle-chat",
        participant_ids=[alice, owner],
    )

    number = 1
    start = 1_700_000_000_000_000
    for day in range(7):
        base = start + day * DAY_US
        _insert(
            db,
            import_run_id=run.id,
            conversation_id=conversation,
            sender_id=alice,
            message_number=number,
            timestamp_us=base,
            text="Ahoj?",
        )
        number += 1
        _insert(
            db,
            import_run_id=run.id,
            conversation_id=conversation,
            sender_id=owner,
            message_number=number,
            timestamp_us=base + 60_000_000,
            text="Ano",
        )
        number += 1

    # Day 8 is a deterministic activity departure from the seven-day baseline.
    base = start + 7 * DAY_US
    for offset in range(6):
        _insert(
            db,
            import_run_id=run.id,
            conversation_id=conversation,
            sender_id=alice,
            message_number=number,
            timestamp_us=base + offset * 10_000_000,
            text="Ahoj? ❤️" if offset == 0 else "Ahoj?",
        )
        number += 1
    _insert(
        db,
        import_run_id=run.id,
        conversation_id=conversation,
        sender_id=owner,
        message_number=number,
        timestamp_us=base + 120_000_000,
        text="Ano",
    )
    db.finish_import(run.id)

    projection = load_a2_projection(db.conn)
    processed = process_messages(list(projection.messages), list(projection.relations))
    processing_store = ProcessingStore(db.conn, ROOT / "database" / "a3_schema.sql")
    processing_store.initialize()
    processing_store.persist(processed, ProcessingConfig())

    results = analyze_database(db.conn)
    analytics_store = AnalyticsStore(db.conn)
    analytics_store.initialize()
    analytics_store.write_run(results, AnalyticsConfig())
    db.close()
    return conversation


def test_a7_oracle_recomputes_a4_core_metrics_and_change_points(tmp_path: Path) -> None:
    path = tmp_path / "messages.sqlite"
    conversation = _build_database(path)

    report = validate_a4_metrics(path)
    assert report["status"] == STATUS_PASS, report
    assert report["checks"]["oracle_ok"] is True
    assert report["checks"]["a3_conversation_count"] == 1
    assert report["checks"]["a4_conversation_count"] == 1
    assert report["checks"]["oracle_participant_count"] == 2
    assert report["checks"]["oracle_response_sample_count"] == 8
    assert report["checks"]["oracle_change_point_count"] >= 1

    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            """SELECT initiations, latency_sample_count, median_response_latency_seconds
               FROM analysis_a4_participants
               WHERE conversation_id=? AND participant_id=(
                   SELECT sender_id FROM analysis_messages
                   WHERE conversation_id=? AND sender_id IS NOT NULL
                   ORDER BY membership_id DESC LIMIT 1
               )""",
            (conversation, conversation),
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_a7_oracle_detects_corrupted_response_latency(tmp_path: Path) -> None:
    path = tmp_path / "messages.sqlite"
    _build_database(path)

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE analytics_response_latency SET latency_seconds=999999 WHERE id=(SELECT MIN(id) FROM analytics_response_latency)"
        )
        conn.commit()
    finally:
        conn.close()

    report = validate_a4_metrics(path)
    assert report["status"] == STATUS_FAIL
    codes = {issue["code"] for issue in report["issues"]}
    assert "A4_RESPONSE_SAMPLE_MISMATCH" in codes
    assert "A4_PARTICIPANT_METRIC_MISMATCH" not in codes  # summary was not tampered


def test_a7_oracle_rejects_a4_bound_to_old_a3_run_and_incremental_repairs_it(tmp_path: Path) -> None:
    path = tmp_path / "messages.sqlite"
    _build_database(path)

    db = CanonicalDatabase(path, schema_path=ROOT / "database" / "schema.sql")
    try:
        projection = load_a2_projection(db.conn)
        processed = process_messages(list(projection.messages), list(projection.relations))
        processing_store = ProcessingStore(db.conn, ROOT / "database" / "a3_schema.sql")
        processing_store.initialize()
        new_processing_run = processing_store.persist(
            processed, ProcessingConfig(session_gap_seconds=5 * 60 * 60)
        )
    finally:
        db.close()

    stale = validate_a4_metrics(path)
    assert stale["status"] == STATUS_FAIL
    assert "A4_STALE_A3_PROVENANCE" in {issue["code"] for issue in stale["issues"]}

    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        changed = analyze_incremental_database(conn)
        assert changed, "new A3 processing run must invalidate A4 even when logical messages are unchanged"
        store = AnalyticsStore(conn)
        store.initialize()
        new_analytics_run = store.write_run(changed, AnalyticsConfig())
        assert conn.execute(
            "SELECT processing_run_id FROM analytics_run WHERE id=?", (new_analytics_run,)
        ).fetchone()[0] == new_processing_run
    finally:
        conn.close()

    repaired = validate_a4_metrics(path)
    assert repaired["status"] == STATUS_PASS, repaired
