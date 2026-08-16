# A1 → A2 canonical ingest contract

## Purpose

A1 is the source-extraction layer. A2 is the authoritative normalization and SQLite layer.

The critical path is:

```text
source → immutable A1 snapshot → staging → A2 canonical SQLite
```

A1 never writes canonical tables. A2 never silently repairs or discards an A1 source occurrence.

## Staging bundle

A1 writes:

```text
staging/
├── manifest.json
├── messages.jsonl
└── errors.jsonl
```

A2 consumes the bundle through `ingest_a1_staging_bundle()`.

A2 accepts A1 `contract_version = "1"`. The iMessage parser currently emits parser version `0.4.0` and source-record-key algorithm version `2`.

Required manifest identity fields:

- `contract_version`
- `source.type`
- `source.sha256`
- `parser.name`
- `parser.version`
- `outputs.messages`
- `counts.messages_seen`
- `counts.attachments_seen`
- `counts.errors`

For iMessage the manifest also documents:

```json
{
  "source_record_key": {
    "algorithm": "sha256-unit-separator",
    "version": "2",
    "scope": "source_snapshot+message_rowid"
  }
}
```

## SQLite source snapshot and WAL consistency

A live macOS Messages database may consist logically of `chat.db` plus committed state in SQLite WAL. Hashing only the main `chat.db` file while SQLite reads a newer logical state from `chat.db-wal` would make provenance inconsistent with the data actually parsed.

For the iMessage importer A1 therefore:

1. opens the original database read-only;
2. creates a temporary consistent database using SQLite's online backup API;
3. parses that immutable backup snapshot;
4. computes `manifest.source.sha256` from that same backup snapshot;
5. deletes the temporary snapshot after staging output has been written.

The manifest identifies this explicitly:

```json
{
  "source": {
    "type": "imessage_chat_db",
    "name": "chat.db",
    "sha256": "...",
    "snapshot_method": "sqlite_online_backup_v1",
    "snapshot_includes_committed_wal": true
  }
}
```

For iMessage, `source.sha256` is therefore the SHA-256 of the **immutable logical SQLite snapshot actually parsed by A1**, not a hash of the main `chat.db` file in isolation. This is the A1/A2 source-snapshot identity used for reconciliation and idempotency.

A1 does not checkpoint, rewrite or mutate the original database. The regression suite keeps an active WAL open, verifies a committed WAL-only message is included in staging, and verifies that both the main database bytes and WAL bytes remain unchanged by import.

For non-SQLite static exports such as CSV/JSON/TXT, `source.sha256` remains the byte-level SHA-256 of the input file.

## One physical source message = one A1 record

For Apple `chat.db`, A1 iterates the `message` table directly. `chat_message_join` is **not** joined into the main message SELECT.

Therefore:

- one `message.ROWID` produces one staging message record;
- zero, one or multiple chat memberships cannot change the number of emitted source messages;
- attachment metadata is emitted once per physical source message;
- `source_record_key` does not depend on chat membership.

For iMessage v2 record identity is derived from logical source-snapshot SHA-256 + source table marker + `message.ROWID`.

## Source conversation relations

A message retains the compatibility field `conversation_source_id`, but lossless cardinality is represented by:

```json
{
  "conversation_sources": [
    {
      "source_conversation_key": "guid:iMessage;-;+420123456789",
      "raw_chat_rowid": 7,
      "chat_guid": "iMessage;-;+420123456789",
      "service": "iMessage",
      "participant_handles": ["+420123456789"],
      "metadata": {}
    }
  ]
}
```

Rules:

- Apple `chat.guid` is preferred when available;
- the source key is then `guid:<chat-guid>`;
- when no GUID exists, A1 uses the explicit fallback `rowid:<chat-rowid>`;
- raw chat ROWID is retained separately as provenance;
- A2 additionally namespaces every source conversation key by the immutable source snapshot, so identical local ROWIDs from unrelated databases cannot collide;
- an orphan source message is represented explicitly as `orphan:<message-rowid>` rather than being dropped.

## A2 source snapshot and import-run identity

A2 stores two distinct concepts:

- `source_sha256` — identity of the immutable source snapshot actually consumed by A1; for live SQLite this is the logical online-backup snapshot hash, for static files it is the input-file hash;
- `source_fingerprint` — one concrete parser/contract ingest representation.

The same unchanged logical source parsed by a newer parser therefore has:

- the same `source_sha256`;
- a different parser-aware `source_fingerprint`;
- a new auditable import run;
- additional provenance rows without unnecessary canonical duplication.

A7 should reconcile a concrete A1 staging run using source type, source snapshot SHA-256, parser version/contract and source record keys. The parser-aware ingest fingerprint is not a replacement for the source-snapshot hash.

## Canonical message↔conversation cardinality

A2 schema v5 represents membership explicitly:

- `message` — canonical message entity;
- `conversation` — canonical conversation entity;
- `message_conversation` — M:N canonical membership;
- `message_source` — one source message occurrence/provenance row;
- `conversation_source` — source-snapshot-scoped conversation identity;
- `message_source_conversation` — exact source relation connecting a source message occurrence to a source conversation and canonical membership.

A convenience primary `message.conversation_id` remains for compatibility, but it is **not** the complete source-of-truth for memberships.

A message linked to two source chats must remain:

```text
1 source message occurrence
1 canonical message (when canonical identity proves this)
2 source conversation relations
2 canonical message memberships
```

No downstream layer may deduplicate those two membership rows merely because `message.id` is identical.

## Canonical message identity and deduplication

A2 distinguishes source occurrence identity from semantic duplicate detection.

Technical reconciliation is conservative:

1. same authoritative A1 `source_record_key` → reuse canonical message;
2. same stable `(service, source_guid)` → reuse canonical message;
3. otherwise create a new canonical message.

Every parser/import occurrence remains separately traceable in `message_source`.

Identical or similar text/timestamps without authoritative identity are never destructively merged by A2. Probable/similarity duplicate analysis belongs to A3 and remains non-destructive.

## Attachments: blob identity vs occurrence identity

A2 schema v5 separates:

- canonical attachment/blob identity (`attachment`);
- message attachment occurrence (`message_attachment_occurrence`);
- source attachment provenance (`attachment_source`).

Consequences:

- one file SHA-256 may identify one canonical blob;
- the same blob appearing twice on one message remains two occurrences with separate positions;
- a retry of the same import run does not duplicate a source occurrence;
- a newer parser run over the same immutable source reuses the same canonical occurrence and adds new provenance;
- contradictory SHA-256 values for the same source attachment occurrence fail explicitly.

Missing attachment bytes never cause the parent message to be dropped.

## Time normalization

Canonical ordering time is `sent_at_utc_us`: Unix UTC microseconds stored as `INTEGER`.

Apple source precision may be nanosecond. A2 stores UTC microseconds and retains the stronger original precision in provenance; it does not claim nanosecond canonical precision after truncation.

A2 also supports explicit source-local fields:

- `sent_at_local_iso`
- `timezone_name`
- `timezone_offset_min`

They are populated only when explicitly supplied by the source. A2 never derives local wall time using the host machine timezone.

`timestamp_local` must contain an explicit UTC offset. If `timezone_offset_min` is also supplied, the values must agree. Civil offsets outside ±14 hours are rejected.

Current iMessage A1 output supplies UTC-normalized time but does not invent source-local timezone facts, so those local fields normally remain `NULL`.

## Participant mapping

A2 currently maps message senders deterministically:

- outgoing → canonical local self identity;
- incoming email-like handle → `email`;
- incoming phone-like handle → `phone`;
- other handles → `imessage_handle`;
- missing sender → `NULL` rather than guessed identity.

A1 also preserves source chat participant handles inside each `conversation_sources[]` relation. Cross-handle/person identity resolution remains an A2/A3 integration concern and is never guessed by A1.

## Reply relations

A1 `reply_to_guid` is resolved after message ingestion. If the target stable GUID exists, A2 creates an explicit `message_relation(relation_type='reply_to')`.

Temporal adjacency alone is never converted into proof of a reply.

## Fail-closed rules

A2 refuses or fails canonical ingest when, among other conditions:

- the A1 contract version is unsupported;
- manifest source identity is incomplete;
- `counts.errors != 0`;
- record source identity disagrees with the manifest;
- required source identifiers are absent;
- parsed message count differs from `counts.messages_seen`;
- parsed attachment occurrence count differs from `counts.attachments_seen`;
- explicit source time fields contradict each other;
- one source attachment occurrence produces conflicting content hashes.

A failed run is recorded as failed where an import run already exists; pre-ingest validation that can be completed before a run is created fails before canonical data is written.

## Database migrations

A2 uses append-only migrations:

1. `001_initial.sql` — initial canonical entities;
2. `002_a1_staging_contract.sql` — A1 provenance contract;
3. `003_source_content_hash.sql` — source-snapshot SHA-256 identity;
4. `004_explicit_local_time.sql` — explicit source-local time fields;
5. `005_lossless_membership.sql` — source-snapshot-safe conversation identity, M:N memberships, source relation provenance and attachment occurrences.

Migration tests verify populated legacy databases upgrade without losing canonical/source messages.

## Downstream analytical views

A3+ should use the stable A2 views rather than source-specific schemas:

- `analysis_messages` — **one row per message-conversation membership**, includes `membership_id`;
- `analysis_conversations`;
- `analysis_attachments` — one row per attachment occurrence;
- `analysis_message_sources` — source provenance;
- `analysis_message_memberships` — canonical and source relation traceability;
- `message_relation` — explicit source-evidenced relations.

The same canonical message may therefore appear in multiple `analysis_messages` rows when source evidence places it in multiple conversations. A3/A4/A6 must use membership identity, not only canonical message ID.

## End-to-end regression gates

The repository contains two critical iMessage integration regressions:

1. one physical source message belongs to two chats:

```text
Apple chat.db
  → A1 iMessage staging
  → A2 schema v5 ingest
  → 1 message / 2 memberships / 2 source relations
```

2. a committed message exists in active WAL state:

```text
live chat.db + chat.db-wal
  → read-only SQLite online backup snapshot
  → hash + parse same immutable snapshot
  → WAL message present in staging
  → original chat.db and WAL bytes unchanged
```

The first gate proves relational losslessness. The second proves that A1 provenance describes the same logical SQLite state that was actually parsed.
