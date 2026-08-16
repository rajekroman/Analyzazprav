from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.a5_ai import A4SQLiteCandidateSource
from analyzazprav.analytics import AnalyticsConfig, AnalyticsStore, analyze_database
from analyzazprav.normalization import CanonicalDatabase, MessageInput
from analyzazprav.processing import (
    ProcessingConfig,
    ProcessingStore,
    load_a2_projection,
    process_messages,
)


UTC = timezone.utc


def _insert(
    db: CanonicalDatabase,
    *,
    import_run_id: int,
    conversation_id: int,
    sender_id: int,
    row_number: int,
    timestamp: datetime,
    text: str,
    is_outgoing: bool,
) -> int:
    timestamp_us = int(timestamp.timestamp() * 1_000_000)
    guid = f"A5-REAL-{row_number}"
    return db.insert_message(
        MessageInput(
            import_run_id=import_run_id,
            source_type="fixture",
            conversation_id=conversation_id,
            sender_id=sender_id,
            sent_at_utc_us=timestamp_us,
            timezone_offset_min=0,
            timestamp_precision="microsecond",
            timestamp_quality="exact",
            direction="outgoing" if is_outgoing else "incoming",
            message_type="text",
            text=text,
            service="iMessage",
            canonical_guid=guid,
            source_message_id=guid,
            source_conversation_id="a5-real-chat",
            source_row_id=str(row_number),
            source_record_key=f"{row_number + 5000:064x}",
            raw_timestamp=str(timestamp_us),
            raw_text=text,
            raw_payload={"rowid": row_number},
        )
    )


def test_a5_reads_real_reconciled_a4_v9_change_point(tmp_path: Path) -> None:
    database = tmp_path / "messages.sqlite"
    db = CanonicalDatabase(database, schema_path=ROOT / "database" / "schema.sql")
    db.initialize()
    run = db.begin_import(
        source_type="fixture",
        source_fingerprint="a5-real-a4-contract",
    )
    alice = db.get_or_create_participant(
        identity_type="phone",
        identity_value="+420777100001",
        canonical_name="Alice",
    )
    owner = db.get_or_create_participant(
        identity_type="email",
        identity_value="owner-a5@example.cz",
        canonical_name="Owner",
        is_self=True,
    )
    conversation = db.get_or_create_conversation(
        source_type="fixture",
        source_conversation_id="a5-real-chat",
        import_run_id=run.id,
        canonical_key="fixture:a5-real-chat",
        participant_ids=[alice, owner],
    )

    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    row_number = 1
    for day in range(7):
        base = start + timedelta(days=day)
        _insert(
            db,
            import_run_id=run.id,
            conversation_id=conversation,
            sender_id=alice,
            row_number=row_number,
            timestamp=base,
            text="Ahoj?",
            is_outgoing=False,
        )
        row_number += 1
        _insert(
            db,
            import_run_id=run.id,
            conversation_id=conversation,
            sender_id=owner,
            row_number=row_number,
            timestamp=base + timedelta(minutes=1),
            text="Ano",
            is_outgoing=True,
        )
        row_number += 1

    departure = start + timedelta(days=7)
    departure_message_ids: list[int] = []
    for offset in range(6):
        departure_message_ids.append(
            _insert(
                db,
                import_run_id=run.id,
                conversation_id=conversation,
                sender_id=alice,
                row_number=row_number,
                timestamp=departure + timedelta(minutes=offset),
                text="Ahoj?",
                is_outgoing=False,
            )
        )
        row_number += 1
    _insert(
        db,
        import_run_id=run.id,
        conversation_id=conversation,
        sender_id=owner,
        row_number=row_number,
        timestamp=departure + timedelta(minutes=10),
        text="Ano",
        is_outgoing=True,
    )
    db.finish_import(run.id)

    projection = load_a2_projection(db.conn)
    processed = process_messages(
        list(projection.messages),
        list(projection.relations),
        participants=list(projection.participants),
    )
    processing_store = ProcessingStore(
        db.conn, ROOT / "database" / "a3_schema.sql"
    )
    processing_store.initialize()
    processing_store.persist(processed, ProcessingConfig())

    config = AnalyticsConfig(
        change_min_baseline_days=7,
        change_baseline_window_days=7,
        change_z_threshold=2.5,
    )
    results = analyze_database(db.conn, config)
    assert len(results) == 1
    analytics_store = AnalyticsStore(db.conn)
    analytics_store.initialize()
    analytics_store.write_run(results, config)

    reconciliation = db.conn.execute(
        """SELECT uses_latest_processing_run, membership_count_delta,
                  invalid_response_session_count, invalid_silence_session_count,
                  invalid_event_session_count, reconciliation_ok
           FROM analysis_a4_reconciliation
           WHERE conversation_id=?""",
        (conversation,),
    ).fetchone()
    assert reconciliation == (1, 0, 0, 0, 0, 1)
    db.close()

    candidates = A4SQLiteCandidateSource(database).change_points(str(conversation))
    message_count_candidates = [
        candidate
        for candidate in candidates
        if candidate.metadata.get("metric") == "message_count"
        and candidate.metadata.get("participant_id") == str(alice)
    ]
    assert message_count_candidates, candidates
    candidate = message_count_candidates[-1]
    assert candidate.candidate_type == "change_point"
    assert candidate.detected_signals == ("change_point", "increasing")
    assert candidate.metrics_during["value"] == 6.0
    assert candidate.metrics_before["baseline_median"] == 1.0
    assert set(candidate.evidence_message_ids) == {
        str(message_id) for message_id in departure_message_ids
    }
