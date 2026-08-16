# A3 → A7 QA handoff

Authoritative A3 branch: `agent/a3-processing-classification`

A7 validates A3 as a deterministic L2 / DERIVED layer over the current A2 canonical SQLite contract. Unit tests alone are insufficient; the checks below are release-blocking.

## 1. Data preservation

Before and after A3 compare at minimum:

- `message`
- `message_source`
- `message_conversation`
- `attachment`
- `message_attachment_occurrence`
- `participant`
- `participant_identity`
- `conversation`

Counts and authoritative content must remain unchanged.

`text_clean`, resolved participants, sessions, runs and features exist only in A3-derived storage. `PRAGMA foreign_key_check` must return no rows.

## 2. Message↔conversation membership preservation

A0 contract #15 is mandatory.

A7 fixture must include one canonical message with two valid A2 conversation memberships.

Expected:

- one A2 canonical `message`;
- two A2 `message_conversation` rows;
- two A3 `processed_message` rows;
- same canonical `message_id`;
- different `conversation_id`;
- both exact A2 `membership_id` values retained;
- no collision or silent dropping.

`processing_run.input_message_count` and `input_membership_count` must therefore be allowed to differ.

A3 logical occurrence key is `(conversation_id, message_id)`; `membership_id` remains evidence provenance.

## 3. Participant resolution / aliases

A7 must verify:

- every A2 participant is represented in exactly one current A3 `resolved_participant_member`;
- every A2 participant identity loaded by A3 is represented by one `participant_alias`;
- multiple A2 participants explicitly marked `is_self=1` resolve to one self group with confidence `1.0`;
- different non-self participants are not merged merely because normalized `canonical_name` matches;
- name equality creates only `participant_resolution_candidate` with method `normalized_canonical_name_candidate_v1`;
- `processed_message.resolved_sender_id` points to the derived participant used for analysis;
- consecutive messages from two explicit self aliases form one sender-run rather than a false sender change;
- A2 `participant` and `participant_identity` rows remain unchanged.

## 4. Determinism

For unchanged A2 projection + A3 config, repeat execution and compare logical output:

- membership sequence numbers;
- resolved participant membership and aliases;
- sender-run membership;
- session membership;
- explicit thread membership;
- cleaned text;
- duplicate candidates;
- timing/media/calendar features.

Wall-clock processing timestamps and `processing_run.id` are excluded.

## 5. Ordering and unknown timestamps

Ordering inside each conversation uses timestamp plus deterministic source/membership/message tie breakers.

Unknown timestamp:

- remains present;
- receives no invented latency;
- receives no UTC/local calendar fields;
- forms a temporal boundary rather than a guessed placement.

## 6. Sessions

Default boundary: gap **strictly greater than six hours**.

Fixtures:

- below 6h → same session;
- exactly 6h → same session;
- above 6h → new session;
- unknown timestamp → no gap inference across that occurrence.

Session first/last evidence should preserve message ID and membership ID when available.

## 7. Replies and threads

Only configured explicit relations (`reply`, `reply_to`) create structural threads.

Checks:

- reactions are not replies;
- temporal adjacency is not factual reply evidence;
- reply may cross sessions;
- relation cannot connect different conversations;
- when source and target share more than one conversation membership, A3 must not guess the target conversation;
- an explicit relation metadata `conversation_id` may disambiguate if it matches a shared conversation;
- `conversation_thread_message` retains membership IDs.

Thread method: `explicit_reply_component_v2`, confidence `1.0`.

## 8. Duplicate audit

A2 remains canonical dedup authority.

Verify:

- no message/membership disappears when duplicate candidate exists;
- strong stable source identity → `exact_source_identity`;
- weaker equal-content/time evidence → `probable_cross_export`;
- legitimate repeated texts remain separate;
- candidate IDs are ascending/stable;
- repeated-message fixtures do not create O(n²) all-pairs output.

## 9. Text cleaning

Preserve:

- case;
- emoji;
- repeated `!` / `?`;
- meaningful line breaks.

Only transport artifacts may be normalized in `text_clean`.

## 10. Attachments / media

A2 attachment occurrence fidelity is authoritative.

Verify:

- repeated same canonical attachment blob can appear in feature counts multiple times when A2 exposes multiple occurrences;
- image/gif/video/audio/document/other classification is deterministic;
- missing physical file increments `missing_attachment_count`;
- parent message membership is never removed.

## 11. Calendar

- UTC fields only from real UTC timestamp;
- local fields only with A2 `timezone_offset_min`;
- no hard-coded CET/CEST;
- weekday: Monday=0, Sunday=6.

Include fixture on both sides of explicit CET/CEST offset change.

## 12. A3 schema rebuild

A3 may rebuild obsolete A3-derived tables only.

Fixture must prove:

- old A3 schema is replaced;
- A2 rows survive unchanged;
- subsequent A3 run succeeds;
- foreign keys remain valid.

## 13. Real A2 integration

`tests/test_a3_a2_integration.py` plus the A3 v5 contract regression tests are mandatory.

The v5 regression must prove A0 contract #15 case:

`same canonical message → multiple conversations → all memberships processed`.

Any A2 view/schema change that loses `membership_id`, participant identities or required provenance blocks A3 integration.

## 14. Acceptance evidence

A7 records:

1. exact A2 SHA;
2. exact A3 SHA;
3. A2 exact-head CI success;
4. A3 exact-head CI success;
5. multi-conversation membership regression PASS;
6. participant-resolution regression PASS;
7. real A2→A3 integration PASS;
8. deterministic rerun PASS;
9. no A2 mutation;
10. foreign-key check PASS.

No AI/LLM output is required for A3 validation.
