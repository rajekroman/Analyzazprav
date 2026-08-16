# A3 → A7 QA handoff

A7 must validate A3 as a deterministic L2 derived-data layer over the canonical A1+A2 `main` contract. A3 is not accepted merely because isolated processing tests pass.

## 1. Data preservation

A3 must not change A2 source/canonical rows in:

- `message`
- `message_source`
- `message_conversation`
- `message_source_conversation`
- `attachment`
- `message_attachment_occurrence`
- `participant`
- `participant_identity`
- `conversation`

`text_clean` and participant-resolution records exist only in A3 DERIVED storage. `PRAGMA foreign_key_check` and A2 integrity checks must remain clean after A3 persistence.

## 2. Membership reconciliation

For every tested run:

```text
A2 memberships selected
    == processing_run.input_membership_count
    == processing_run.output_membership_count
    == processed_message rows for that run
```

One canonical message with two A2 memberships must yield two A3 processed membership rows with the same canonical message ID and distinct preserved `membership_id` values.

## 3. Participant resolution / aliases

A7 must verify all of the following:

- every A2 participant used by the processed dataset belongs to exactly one current `resolved_participant` group;
- every loaded A2 `participant_identity` is represented by one `participant_alias` with original `participant_id` + `participant_identity_id` provenance;
- `participant_alias.participant_identity_id` resolves to the real A2 identity row;
- the alias identity must belong to the same A2 `participant_id`; the DB trigger must reject an identity/participant mismatch;
- multiple A2 participants explicitly marked `is_self=1` resolve to one self group with method `explicit_is_self_union_v1` and confidence `1.0`;
- non-self participants are **not** merged merely because normalized `canonical_name` matches;
- equal-name evidence produces only `participant_resolution_candidate` with method `normalized_canonical_name_candidate_v1` and confidence `0.35`;
- the in-memory `ProcessedMessage.resolved_sender_id` is persisted via `processed_message_resolved_sender`;
- the in-memory `SenderRun.resolved_participant_id` is persisted via `sender_run_resolved_participant`;
- `analysis_processed_messages_resolved_latest` and `analysis_sender_runs_resolved_latest` expose the resolved IDs without changing the integrated v4 base tables;
- switching between two explicit aliases of the same resolved person does not create a new sender-run;
- such an alias switch does not create a false `seconds_since_previous_other_sender` value;
- A2 `participant` and `participant_identity` rows are byte/logically unchanged before and after A3.

## 4. Determinism

For unchanged A2 projection + config, two runs must produce the same logical values for:

- resolved participant membership;
- participant aliases and unresolved candidates;
- per-conversation sequence numbers;
- sender-run memberships;
- session memberships;
- explicit thread memberships;
- cleaned text;
- duplicate candidates;
- timing/media/calendar features.

Run IDs and wall-clock timestamps are excluded from logical equality.

## 5. Immutable processing history and v4 → v5 upgrade

A3 appends processing runs. A new run must not delete or replace an earlier completed run.

The A3 v5 participant-resolution schema is **additive**. Integrated A3 v4 is a supported upgrade source and MUST NOT be treated as an obsolete draft merely because v5 sidecar tables are absent.

A7 release-blocking fixture must create at least one completed v4 `processing_run` with persisted v4 `processed_message` data, run the v5 initialization, and prove all of the following:

- the existing v4 `processing_run` row is byte/logically unchanged;
- its existing `processed_message` rows are byte/logically unchanged;
- calling v5 initialization repeatedly is idempotent;
- v5 creates `resolved_participant`, `resolved_participant_member`, `participant_alias`, `participant_resolution_candidate`, `sender_run_resolved_participant` and `processed_message_resolved_sender` without rebuilding v4 tables;
- historical v4 rows remain queryable through `analysis_processed_messages_latest`;
- the resolved-person view returns `NULL` for an old v4 message that naturally has no v5 sidecar mapping;
- existing `idx_processed_message_utc_period` and `idx_processed_message_local_period` indexes remain present;
- `PRAGMA foreign_key_check` remains empty.

Only an actually incompatible **pre-v4 draft A3 schema** may be rebuilt. A2 tables must never be dropped or rewritten.

## 6. Ordering and unknown timestamps

Deterministic tie-breakers include timestamp, source-order hint, source message ID, canonical message ID and membership ID. Unknown timestamps remain present and receive no invented latency or UTC/local calendar values.

## 7. Sessions

Default boundary is gap **strictly greater than six hours**:

- below 6h → same session;
- exactly 6h → same session;
- above 6h → new session;
- unknown timestamp → no temporal inference across the membership.

## 8. Replies and threads

Only explicit A2 `reply` / `reply_to` relations create structural threads. Temporal adjacency is never factual reply evidence. A relation may only project into conversations shared by both endpoint memberships and must never connect different chats. Cross-session explicit reply is allowed and then thread `session_id` is `NULL`.

## 9. Duplicate audit

A2 remains canonical dedup authority. A3 only emits non-destructive candidates. No message or membership may disappear because of a candidate. Repeated short-message fixtures must not create O(n²) candidate growth.

## 10. Text cleaning

Preserve case, emoji, repeated `!`/`?` and meaningful line breaks. Only transport artifacts may be normalized in `text_clean`.

## 11. Media

Validate deterministic image/gif/video/audio/document/other classification, occurrence counts and `missing_attachment_count`. Missing bytes never remove the parent membership.

## 12. Calendar

UTC fields require real UTC timestamp. Local fields require explicit A2 `timezone_offset_min`; no hard-coded CET/CEST. Weekday convention: Monday `0`, Sunday `6`.

## 13. Source provenance

Every processed membership must resolve through:

```text
processed_message.membership_id
→ message_conversation
→ canonical message
→ message_source / source_record_key
```

Resolved participant evidence must resolve through:

```text
processed_message
→ processed_message_resolved_sender
→ resolved_participant
→ resolved_participant_member
→ participant
→ participant_alias
→ participant_identity
```

Sender-run evidence must likewise resolve through:

```text
sender_run
→ sender_run_resolved_participant
→ resolved_participant
→ resolved_participant_member
→ participant
```

## 14. Real vertical contract tests

Release-blocking coverage includes:

1. A1→A2→A3 membership preservation;
2. actual A2 canonical database → A3 process/persist;
3. immutable multiple A3 processing runs;
4. participant alias union for explicit self identities;
5. equal-name non-self candidate without merge;
6. resolved sender-run and response-latency semantics;
7. alias identity FK + participant-match enforcement;
8. non-destructive integrated A3 v4 → v5 sidecar initialization;
9. restored/retained period indexes;
10. no A2 source/canonical mutation.

## 15. Acceptance evidence

A7 handoff is green only when it records:

1. exact tested base/integration SHA;
2. A3 processing version/config;
3. A1, A2 and A3 workflows success on the tested head;
4. full vertical regression PASS;
5. membership reconciliation PASS;
6. participant/alias reconciliation PASS;
7. source provenance resolution PASS;
8. foreign-key/integrity PASS;
9. no A2 mutation;
10. deterministic logical rerun PASS;
11. immutable previous processing-run retention PASS;
12. non-destructive integrated A3 v4 → v5 sidecar upgrade PASS.

No AI/LLM output is required to validate A3.
