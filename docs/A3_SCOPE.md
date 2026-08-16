# A3 — Zpracování a třídění

A3 je deterministická transformační vrstva nad kanonickými daty A2. Nemění raw ani canonical A2 záznamy a neprovádí behaviorální nebo psychologickou interpretaci.

## Vlastnictví odpovědností

- **A2:** canonical entities, source provenance, identity resolution and authoritative deduplication.
- **A3:** conservative text cleaning, deterministic ordering, secondary duplicate audit, sender runs, temporal sessions, source-evidenced reply threads, measurable message features and persistence of derived data.
- **A4:** communication statistics and behavioral metrics.
- **A5:** selective AI interpretation over evidence selected by A4.

## Invariants

1. A3 is read-only with respect to A2 raw/canonical records.
2. A3 never deletes or merges canonical messages.
3. Repeated punctuation, letter case and emoji survive cleaning.
4. Probable duplicates are audit candidates only.
5. Temporal adjacency is a feature, never proof of a reply.
6. Same A2 projection + config produces the same derived structure.
7. Session boundary defaults to a gap strictly greater than six hours.
8. A missing timestamp produces no latency and is isolated from temporal session inference.
9. Threads are created only from explicit source relations whose relation type is configured as a reply relation. Semantic topic inference is deferred.

## A2 contract used

A3 reads `analysis_messages`, `analysis_attachments`, `message_source` and `message_relation`. It does not depend on A1/iMessage/iMazing physical export formats.

## Persistence

A3 initializes `database/a3_schema.sql` and replaces only A3-derived tables. A2 canonical tables remain untouched. The initial MVP uses deterministic full rebuilds; incremental recomputation can be added after the source/import pipeline stabilizes.

## CLI

```bash
python -m analyzazprav.processing path/to/messages.sqlite
```

Optional flags configure the session gap and duplicate timestamp tolerance.
