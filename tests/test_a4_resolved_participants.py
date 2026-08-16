from __future__ import annotations

from pathlib import Path

from analyzazprav.analytics import AnalyticsConfig, AnalyticsStore, analyze_database, load_analytic_messages
from analyzazprav.normalization import CanonicalDatabase, MessageInput
from analyzazprav.processing import ProcessingConfig, ProcessingStore, load_a2_projection, process_messages
from analyzazprav.qa import STATUS_PASS, validate_a4_metrics


ROOT = Path(__file__).resolve().parents[1]


def _insert(
    db: CanonicalDatabase,
    *,
    import_run_id: int,
    conversation_id: int,
    sender_id: int,
    row: int,
    timestamp_us: int,
) -> int:
    guid = f"RESOLVED-{row}"
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
            direction="outgoing" if row < 3 else "incoming",
            message_type="text",
            text=f"m{row}",
            service="iMessage",
            canonical_guid=guid,
            source_message_id=guid,
            source_conversation_id="resolved-chat",
            source_row_id=str(row),
            source_record_key=f"{row + 100:064x}",
            raw_timestamp=str(timestamp_us),
            raw_text=f"m{row}",
            raw_payload={"rowid": row},
        )
    )


def test_a4_uses_a3_resolved_sender_and_a7_recomputes_same_identity(tmp_path: Path) -> None:
    path = tmp_path / "messages.sqlite"
    db = CanonicalDatabase(path, schema_path=ROOT / "database" / "schema.sql")
    db.initialize()
    run = db.begin_import(source_type="fixture", source_fingerprint="resolved-participants")

    self_phone = db.get_or_create_participant(
        identity_type="phone",
        identity_value="+420777111111",
        canonical_name="Owner phone",
        is_self=True,
    )
    self_email = db.get_or_create_participant(
        identity_type="email",
        identity_value="owner@example.cz",
        canonical_name="Owner email",
        is_self=True,
    )
    other = db.get_or_create_participant(
        identity_type="phone",
        identity_value="+420777222222",
        canonical_name="Other",
        is_self=False,
    )
    conversation = db.get_or_create_conversation(
        source_type="fixture",
        source_conversation_id="resolved-chat",
        import_run_id=run.id,
        canonical_key="fixture:resolved-chat",
        participant_ids=[self_phone, self_email, other],
    )

    base = 1_700_000_000_000_000
    _insert(db, import_run_id=run.id, conversation_id=conversation, sender_id=self_phone, row=1, timestamp_us=base)
    _insert(db, import_run_id=run.id, conversation_id=conversation, sender_id=self_email, row=2, timestamp_us=base + 1_000_000)
    _insert(db, import_run_id=run.id, conversation_id=conversation, sender_id=other, row=3, timestamp_us=base + 60_000_000)
    db.finish_import(run.id)

    projection = load_a2_projection(db.conn)
    processed = process_messages(
        list(projection.messages),
        list(projection.relations),
        participants=list(projection.participants),
    )
    pstore = ProcessingStore(db.conn, ROOT / "database" / "a3_schema.sql")
    pstore.initialize()
    pstore.persist(processed, ProcessingConfig())

    analytic_messages = load_analytic_messages(db.conn)
    resolved_self = min(self_phone, self_email)
    assert [message.participant_id for message in analytic_messages] == [resolved_self, resolved_self, other]

    results = analyze_database(db.conn)
    assert len(results) == 1
    result = results[0]
    assert set(result.participant_metrics) == {resolved_self, other}
    assert result.participant_metrics[resolved_self]["message_count"] == 2
    assert result.participant_metrics[resolved_self]["turn_count"] == 1
    assert result.participant_metrics[other]["message_count"] == 1

    astore = AnalyticsStore(db.conn)
    astore.initialize()
    astore.write_run(results, AnalyticsConfig())
    db.close()

    qa = validate_a4_metrics(path)
    assert qa["status"] == STATUS_PASS, qa
    assert qa["checks"]["uses_resolved_participant_identity"] is True
    assert qa["checks"]["oracle_participant_count"] == 2
