# A3 → A7 QA handoff

A7 must validate A3 as a deterministic L2 derived-data layer over the canonical A1+A2 `main` contract. A3 is not accepted merely because isolated processing unit tests pass; the checks below are release-blocking invariants.

## 1. Data preservation

A3 must not change A2 source/canonical rows in:

- `message`
- `message_source`
- `message_conversation`
- `message_source_conversation`
- `attachment`
- `message_attachment_occurrence`
- `participant`
- `conversation`

`text_clean` exists only in A3 derived storage. A2 `message.text` and `message_source.raw_text` remain authoritative values.

`PRAGMA foreign_key_check` and A2 integrity checks must remain clean after A3 persistence.

## 2. Membership reconciliation

A2 `analysis_messages` is membership-aware. A7 must verify for the tested A3 run:

```text
A2 membership rows selected for processing
    == processing_run.input_membership_count
    == processing_run.output_membership_count
    == processed_message rows for that run
```

`processing_run.canonical_message_count` may be smaller because one canonical message can belong to multiple conversations.

Release-blocking fixture:

```text
1 canonical message
2 A2 message_conversation memberships
→ 2 A3 processed membership rows
```

No membership may disappear because canonical `message_id` is duplicated across chats.

## 3. Determinism

For an unchanged A2 projection and unchanged A3 config, two runs must produce the same logical values for:

- per-conversation sequence numbers;
- sender-run memberships;
- session memberships;
- explicit thread memberships;
- cleaned text;
- duplicate candidate pairs/classification/method/confidence;
- structural, timing, media and calendar features.

`processing_run.id` and wall-clock audit timestamps are expected to differ and are excluded from logical equality.

## 4. Immutable processing history

A3 v4 appends processing runs. A new run must **not** delete or replace a previous completed run.

A7 must verify:

- two different configs/algorithm runs remain simultaneously queryable;
- each run has its own `processed_message`, sender-run, session, thread and duplicate-candidate rows;
- `analysis_processed_messages_latest` resolves only the latest completed run;
- old completed rows remain unchanged after a later run.

Only an obsolete incompatible **draft A3 schema** may be rebuilt. This rebuild is restricted to A3-derived tables; A2 data must survive byte/logical comparison.

## 5. Ordering and unknown timestamps

Canonical deterministic tie breakers include timestamp, source-order hint, source message ID, canonical message ID and membership ID.

- Messages without reliable timestamps remain present.
- Unknown timestamps receive no invented latency.
- Unknown timestamps receive no UTC/local calendar components.
- They are not silently inserted between timestamped rows by guessed time.

## 6. Sessions

Default session boundary: gap strictly greater than six hours.

Boundary fixtures:

- just below six hours → same session;
- exactly six hours → same session;
- greater than six hours → new session;
- unknown timestamp → no temporal-gap inference across that membership.

Session membership is scoped to one conversation membership stream.

## 7. Replies and threads

Only explicit A2 relation types configured as replies (`reply`, `reply_to`) create structural threads.

- reactions are not replies;
- temporal adjacency is never factual reply evidence;
- an explicit reply may cross temporal sessions; then thread `session_id` is `NULL`;
- A2 relations are canonical-message-level, so A3 must project them only into conversations shared by both endpoint memberships;
- a relation must never create a thread across different conversations;
- current method is `explicit_reply_membership_component_v2`, confidence `1.0`.

A7 must include a canonical message with memberships in two chats and verify a reply relation affects only the shared chat.

## 8. Duplicate audit

A2 remains authoritative for canonical deduplication. A3 only emits non-destructive audit candidates.

A7 must verify:

- no canonical message or membership disappears because of a candidate;
- stable source identity can produce `exact_source_identity` evidence;
- weaker equal-content/time evidence remains `probable_cross_export`;
- legitimate repeated text remains distinct;
- candidate canonical message IDs are stored in stable ascending order;
- repeated short messages do not trigger O(n²) candidate explosion.

## 9. Text cleaning

Cleaning may normalize transport artifacts only. Fixtures must confirm preservation of:

- case;
- emoji;
- repeated `!` and `?`;
- meaningful line breaks.

CRLF/CR, NBSP, trailing transport whitespace and invalid controls may be normalized only in A3 `text_clean`.

## 10. Media

A3 classifies attachment occurrences using MIME first and filename extension second into:

- image
- gif
- video
- audio
- document
- other

A7 validates occurrence counts and `missing_attachment_count`. Missing bytes never remove the parent message/membership.

## 11. Calendar fields

- UTC fields require a real A2 UTC timestamp.
- Local fields require A2 `timezone_offset_min`.
- No hard-coded CET/CEST offset is allowed.
- Weekday convention is Python `datetime.weekday()`: Monday `0`, Sunday `6`.

A7 should include source offsets on both sides of a CET/CEST change.

## 12. Source provenance

A3 projection preserves the deterministic set of A2 `source_record_key` values associated with a canonical message. A3 must not silently choose provenance based on whichever `message_source` row happens to appear first.

For downstream A4/A5 evidence, A7 should verify every processed membership can resolve through:

```text
processed_message.membership_id
→ message_conversation
→ canonical message
→ message_source / source_record_key
```

and, where applicable:

```text
membership_id
→ message_source_conversation
→ conversation_source
→ source_snapshot_key
```

## 13. Real vertical contract tests

Current release-blocking integration tests include:

1. real A2 v5 canonical database → A3 projection/process/persist with attachments, missing media and explicit reply;
2. A1-style multi-chat staging → A2 v5 → A3, proving one canonical message survives as two processed memberships;
3. two persisted A3 runs over the same A2 data remain simultaneously available;
4. A2 raw/canonical text and provenance remain unchanged.

## 14. Acceptance evidence

A7 handoff is green only when it records:

1. exact tested repository/main SHA;
2. exact A3 processing version/config;
3. A1/A2/A3 workflow state on the tested integration head;
4. full vertical regression PASS;
5. A2 membership ↔ A3 processed-membership reconciliation PASS;
6. source-record provenance resolution PASS;
7. foreign-key/integrity PASS;
8. no A2 RAW/canonical mutation;
9. deterministic logical rerun PASS;
10. immutable previous processing-run retention PASS.

No AI/LLM output is required to validate A3.