# A3 → A7 QA handoff

Authoritative A3 branch: `agent/a3-processing-classification`

A7 must validate A3 as a deterministic derived-data layer over the current A2 canonical SQLite contract. A3 must not be accepted based only on unit tests; the checks below are release-blocking invariants.

## 1. Data preservation

- Record counts and source content in A2 `message`, `message_source`, `attachment`, `message_attachment`, `participant`, and `conversation` must be unchanged by an A3 run.
- `text_clean` must exist only in A3-derived storage. A2 `text` and `message_source.raw_text` remain authoritative source/canonical values.
- Running A3 repeatedly may replace A3-derived rows but must never delete or rewrite A2 canonical/source rows.
- `PRAGMA foreign_key_check` must return no rows after A3 persistence.

## 2. Determinism

For an unchanged A2 projection and unchanged A3 config, two processing runs must produce the same logical values for:

- message sequence numbers;
- sender-run membership;
- session membership;
- explicit thread membership;
- cleaned text;
- duplicate candidate pairs/classification/method/confidence;
- structural, timing, media, and calendar features.

`processing_run.id` and wall-clock audit timestamps are expected to differ and are excluded from logical-output equality.

## 3. Ordering and unknown timestamps

- Ordering uses timestamp, source-order hint, source message ID, and internal ID as deterministic tie breakers.
- Messages without a reliable timestamp remain present.
- Unknown timestamps must not receive invented latency values.
- Unknown timestamps must not receive UTC/local calendar components.
- Unknown timestamps must not be silently positioned between timestamped messages by guessed time.

## 4. Sessions

Default session boundary: gap strictly greater than six hours.

A7 boundary fixtures must include:

- gap just below six hours → same session;
- exactly six hours → same session;
- greater than six hours → new session;
- unknown timestamp → no temporal gap inference across that message.

## 5. Replies and threads

- Only configured explicit source relation types (`reply`, `reply_to` by default) create structural threads.
- Reactions must not be treated as replies.
- Temporal adjacency must never create a factual reply relation.
- Explicit replies may cross temporal-session boundaries. Such a thread has `session_id = NULL` because no single session owns it.
- A relation must never join messages from different conversations into one thread.
- Structural threads use `method = explicit_reply_component_v1` and `confidence = 1.0`.

## 6. Duplicate audit

A2 remains authoritative for canonical deduplication. A3 only emits audit candidates.

A7 must verify:

- no canonical message disappears because A3 flags a duplicate candidate;
- stable source-message identity can create `exact_source_identity` evidence;
- weaker equal-content/time evidence remains `probable_cross_export`;
- legitimate repeated text remains distinct canonical messages;
- candidate IDs are stored in stable ascending order;
- large groups of repeated short messages do not cause all-pairs/O(n²) candidate explosion.

## 7. Text cleaning

Cleaning may normalize transport artifacts only. Fixtures must confirm preservation of:

- letter case;
- emoji;
- repeated `!` and `?`;
- meaningful line breaks.

CRLF/CR line endings, NBSP, trailing transport whitespace, and invalid control characters may be normalized in `text_clean` only.

## 8. Media

A3 classifies attachments deterministically using MIME first and filename extension second:

- image;
- gif;
- video;
- audio;
- document;
- other.

A7 must verify attachment counts and `missing_attachment_count`. A missing physical file must never remove its parent message. Generic audio must not be promoted to a voice-message semantic type without source evidence.

## 9. Calendar fields

- UTC calendar components are derived only from a real UTC timestamp.
- Local calendar components are derived only when A2 provides `timezone_offset_min` for that message.
- No hard-coded CET/CEST offset is allowed.
- Weekday convention is Python `datetime.weekday()`: Monday = 0, Sunday = 6.

A7 should include at least one fixture on each side of a CET/CEST offset change using explicit source offsets.

## 10. Persistence and old A3 draft schema

A3 may rebuild obsolete A3-derived tables when their draft schema is incompatible. It must never drop A2 tables.

A7 should initialize an obsolete A3 fixture and confirm:

- A3 derived schema is upgraded/rebuilt;
- all A2 records remain intact;
- foreign keys remain valid;
- a subsequent A3 run completes successfully.

## 11. Real A2 contract test

`tests/test_a3_a2_integration.py` is the mandatory integration smoke test. It creates a real `CanonicalDatabase`, inserts canonical messages, media, a missing attachment and an explicit reply, then executes and persists A3 output.

Any future A2 schema/view change that breaks this test is an A2↔A3 contract regression and blocks integration until resolved.

## 12. Acceptance evidence

A7 handoff is green only when it records:

1. exact tested A2 SHA;
2. exact tested A3 SHA;
3. A2 workflow success on the tested contract;
4. A3 workflow success on the integration SHA;
5. real A2→A3 contract test PASS;
6. foreign-key check PASS;
7. no A2 source/canonical mutation;
8. deterministic rerun PASS.

No AI/LLM output is required to validate A3.
