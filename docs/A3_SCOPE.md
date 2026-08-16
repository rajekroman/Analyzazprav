# A3 — Zpracování a třídění

A3 je deterministická transformační vrstva nad kanonickými daty A2. Nemění raw ani canonical A2 záznamy a neprovádí behaviorální nebo psychologickou interpretaci.

## Vlastnictví odpovědností

- **A2:** canonical participants/person identities, conversations, canonical messages, source provenance and authoritative deduplication.
- **A3:** conservative text cleaning, deterministic ordering, secondary duplicate audit, sender runs, temporal sessions, source-evidenced reply threads, media classification, calendar features and persistence of derived data.
- **A4:** communication statistics and behavioral metrics.
- **A5:** selective AI interpretation over evidence selected by A4.

A3 používá `sender_id`/participant identity z A2; nevytváří konkurenční systém identity osob.

## Invariants

1. A3 is read-only with respect to A2 raw/canonical records.
2. A3 never deletes or merges canonical messages.
3. Repeated punctuation, letter case and emoji survive cleaning.
4. Probable duplicates are audit candidates only and retain both canonical message IDs.
5. Temporal adjacency is a measurable feature, never proof of a reply.
6. Same A2 projection + config produces the same derived logical structure.
7. Session boundary defaults to a gap strictly greater than six hours.
8. A missing timestamp produces no latency and is isolated from temporal session inference.
9. Threads are created only from explicit source relations whose relation type is configured as a reply relation. An explicit reply may span multiple temporal sessions; in that case the thread has no single `session_id`. Semantic topic inference is deferred.
10. Missing attachment files remain represented and are counted; their parent message is never discarded.
11. Local calendar fields are derived only when A2 provides the per-message timezone offset. If it is absent, local fields remain `NULL`.

## A2 contract used

A3 reads:

- `analysis_messages` for canonical messages, sender, UTC timestamp, per-message timezone offset, type and text;
- `analysis_attachments` for attachment identity, hash, MIME type, file metadata and availability;
- `message_source` for stable source identifiers/order hints;
- `message_relation` for source-evidenced relations such as replies.

It does not depend on iMessage, iMazing or another A1 export format.

## Text processing

Cleaning is deliberately conservative: transport control characters, line-ending differences and NBSP are normalized, while case, emoji and repeated `!`/`?` are retained. `text_clean` is stored separately; A2 source/canonical text remains unchanged.

## Media classification

Attachments are deterministically classified from MIME type first and filename extension second into:

- `image`
- `gif`
- `video`
- `audio`
- `document`
- `other`

A3 also stores total attachment count and count of attachments whose A2 availability is `missing`. Voice-message semantics are not guessed from a generic audio file; that distinction can use an explicit source/message type when available.

## Time periods

For every timestamped message A3 derives UTC year, month, day, weekday and hour. If `timezone_offset_min` is present, it also derives the corresponding local year/month/day/weekday/hour using that exact per-message offset, so CET/CEST transitions do not require a hard-coded offset.

`utc_weekday` and `local_weekday` follow Python's `datetime.weekday()` convention: Monday = `0`, Sunday = `6`.

## Persistence

A3 initializes `database/a3_schema.sql` and replaces only A3-derived tables. A2 canonical tables remain untouched. The MVP deliberately uses deterministic full rebuilds; incremental recomputation can be added once the import pipeline stabilizes.

## CLI

```bash
python -m analyzazprav.processing path/to/messages.sqlite
```

Optional flags configure the session gap and duplicate timestamp tolerance.
