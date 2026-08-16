# A1 → A2 canonical ingest contract

## Purpose

A1 is source extraction. A2 is the authoritative normalization and SQLite layer.

A1 writes a staging bundle:

```text
staging/
├── manifest.json
└── messages.jsonl
```

A2 consumes that bundle through `ingest_a1_staging_bundle()` and creates canonical participants, conversations, messages, relations, attachments and provenance records.

## Supported contract

A2 currently accepts A1 `contract_version = "1"` and the `message` record emitted by the iMessage `chat.db` importer.

Required manifest fields:

- `contract_version`
- `source.type`
- `source.sha256`
- `parser.name`
- `parser.version`
- `outputs.messages`
- `counts.errors`

Required message identity fields:

- `contract_version`
- `record_type = "message"`
- `source_type`
- `source_sha256`
- `source_record_key`
- `source_message_id`
- `conversation_source_id`

A2 also consumes, when available:

- `source_guid`
- `timestamp_raw`
- `timestamp_utc`
- `timestamp_precision`
- `sender_handle`
- `is_from_me`
- `text`
- `raw_text`
- `text_source`
- `service`
- `reply_to_guid`
- `attachments[]`
- `raw_payload`
- `metadata`

## Import and source identity

A2 deliberately stores two different fingerprints in `import_run`:

- `source_sha256` is the byte-level SHA-256 of the original A1 source (`manifest.source.sha256`); this is the authoritative bridge for A7 source reconciliation;
- `source_fingerprint` identifies one concrete ingest representation and includes source SHA-256, A1 contract version, parser name and parser version.

This distinction allows the same unchanged `chat.db` to be parsed again with a newer parser without pretending it is a different source file. The two import runs share `source_sha256` but have different `source_fingerprint` values and retain separate provenance rows.

A7 should identify a staging run by `source_type + source_sha256 + parser_version` when a specific A1 export must be reconciled. It must not treat the ingest fingerprint as the raw source hash.

## Time normalization

Canonical A2 time is `sent_at_utc_us`: Unix UTC microseconds stored as `INTEGER`.

A1 may report Apple source precision as `nanosecond`. A2 converts the timestamp to microseconds and records canonical precision as `microsecond`; the original A1 precision remains in source metadata as `a1_source_timestamp_precision`. A2 therefore does not claim nanosecond precision after truncation to microseconds.

If A1 does not provide a valid timezone-aware `timestamp_utc`, A2 stores no invented timestamp and uses `timestamp_quality = "unknown"`.

## Participant mapping

- outgoing records map to the canonical self participant (`identity_type=self`, `identity_value=local`);
- incoming e-mail handles map to `email`;
- phone-like handles map to `phone`;
- other handles map to `imessage_handle`;
- missing incoming sender handles remain `NULL` rather than being guessed.

A1 does not decide canonical person identity across unrelated handles.

## Conversation mapping

`conversation_source_id` is stored in `conversation_source`. A2 does not infer cross-export conversation equivalence without stable evidence.

## Message identity and idempotency

A2 distinguishes source-record identity from semantic duplicate detection.

Technical identity is resolved in this order:

1. same A1 `source_record_key` → same canonical message;
2. same stable `(service, source_guid)` → same canonical message;
3. otherwise create a new canonical message.

Every parser/import occurrence is retained in `message_source`. Reprocessing the same source with a newer parser version can therefore add new provenance without duplicating canonical messages.

Similarity-based or probable duplicate detection belongs to A3 and must not delete or merge A2 canonical/source records.

## Attachments

A1 attachment metadata is preserved even when the file is unavailable.

A2 availability states are:

- `external` — source path exists locally or A1 provides a content hash without managed local storage;
- `missing` — A1 provided a source path but that path does not exist;
- `unknown` — metadata exists but availability cannot be established;
- `available` / `corrupt` are reserved for later managed-storage verification.

Missing attachment bytes never cause the parent message to be dropped.

## Reply relations

A1 `reply_to_guid` is resolved after message ingestion. When the target GUID exists, A2 creates `message_relation(relation_type='reply_to')`.

Unresolved reply GUIDs are not converted into guessed temporal relations. A3 may use only source-evidenced relations as proof of replies.

## Fail-closed rules

A2 refuses canonical ingest when:

- the contract version is unsupported;
- manifest source identity is incomplete;
- `counts.errors` is non-zero;
- a record does not match manifest source identity;
- required source record identifiers are missing;
- the actual JSONL message count disagrees with `manifest.counts.messages_seen`.

A failed import is recorded as `failed`; it is not silently reported as complete.

## Database migrations

A2 uses append-only numbered migrations. Current chain:

1. `001_initial.sql` — original canonical A2 schema;
2. `002_a1_staging_contract.sql` — A1 source-record provenance and A2/A3 duplicate-responsibility split;
3. `003_source_content_hash.sql` — explicit original-source SHA-256 for exact A7 reconciliation.

Existing A2 databases are upgraded in place; migration tests verify canonical message/source rows survive the v1 → current upgrade.

## Downstream A2 views

A3+ may rely on:

- `analysis_messages`
- `analysis_attachments`
- `analysis_conversations`
- `analysis_message_sources`
- `message_relation`

Downstream modules must not depend on the physical A1 `chat.db` schema.
