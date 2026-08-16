# A7 QA and validation contract

A7 is an independent, read-only verification layer. It never repairs source
records, silently drops duplicates, or treats self-reported module diagnostics
or AI output as authoritative evidence.

## Validation gates

1. **SOURCE / IMPORT** — A1 staging is internally reconciled, fingerprinted and
   every extraction error is explicit.
2. **NORMALIZATION** — A2 canonical SQLite reconciles exactly to A1 source
   records, conversation memberships and attachment occurrences.
3. **PROCESSING** — A3 membership-scoped derived data is a lossless,
   deterministic partition of current A2 memberships.
4. **ANALYTICS** — A4 basic metrics match an independent A7 arithmetic oracle;
   derived candidates retain source-message evidence.
5. **TRACEABILITY** — A5 interpretations and A6 displays must resolve to
   concrete canonical message IDs and, through A2 provenance, back to A1.

A gate is not considered valid merely because the module's own tests pass.
A7 maintains independent checks and deliberately corrupt fixtures.

## A1 staging gate

```bash
python -m qa.staging_validator path/to/staging
```

The current A1 contract uses `manifest.json`, `messages.jsonl` and
`errors.jsonl`. A7 validates source SHA-256, exact source IDs, the declared
`source_record_key` algorithm, Apple timestamp conversion, explicit local-time
offsets, attachment metadata and deterministic fingerprints.

Import accounting must close:

```text
messages_seen = messages_emitted + duplicates + unsupported + errors
```

`messages_emitted` must equal the actual message JSONL count and `errors` must
equal the actual error JSONL count. An extraction/serialization error remains a
hard QA failure even when it is correctly accounted for; reconciliation makes
the loss explicit, it does not make the loss acceptable.

## A2 v5 SQLite gate

```bash
python -m qa.sqlite_validator path/to/messages.sqlite
```

The read-only validator runs `PRAGMA integrity_check` and
`PRAGMA foreign_key_check`, requires schema v5 membership/occurrence objects,
and checks:

- every canonical message has source provenance and conversation membership;
- every message has exactly one primary membership and the legacy primary
  pointer agrees with it;
- keyed `message_source` rows retain source-conversation relations;
- `conversation_source` rows retain immutable source snapshot identity;
- attachment source rows retain concrete message-attachment occurrence links;
- completed import statistics reconcile to persisted provenance rows;
- local timestamp offsets are internally consistent;
- analytical views reconcile to v5 semantics: memberships and attachment
  occurrences, not the old one-row-per-message projection.

A non-empty WAL is surfaced because byte-level database hashing is not stable
until the snapshot is checkpointed.

## Exact A1 → A2 reconciliation

```bash
python -m qa.reconcile_a1_a2 path/to/staging path/to/messages.sqlite
```

A7 independently derives the A2 v5 source fingerprint from the A1 contract and
requires exactly one completed matching `import_run`. It then checks exact
multiset equality for:

- A1 `source_record_key` ↔ A2 `message_source.source_record_key`;
- `(source_record_key, source_conversation_id)` memberships;
- source attachment occurrence keys.

A valid many-source → one-canonical-message deduplication is allowed, but no
source occurrence may disappear. No fuzzy matching by text or timestamp is
used.

## A3 processing gate

```bash
python -m qa.a3_validator path/to/messages.sqlite
```

A7 validates the latest completed A3 run against current A2 canonical state.
It independently checks:

- exact set equality of A2 `message_conversation` memberships and A3
  `processed_message` memberships;
- `processing_run` input/output/canonical counts;
- preservation of `membership_id`, `message_id` and `conversation_id`;
- deterministic per-conversation ordering and contiguous sequence numbers;
- session boundaries re-derived from canonical timestamps and the persisted
  session-gap config;
- consecutive-sender run partition;
- thread memberships never cross conversations;
- every processed membership still resolves to an A1 `source_record_key`;
- attachment-count/has-attachment features reconcile to A2 occurrence data.

The regression suite creates a real A1 bundle, ingests it through production A2,
runs production A3, then deliberately corrupts membership, sequence, session
and attachment-feature state to prove that each error is detected.

## A4 independent metric oracle

`qa/a4_oracle.py` contains a small arithmetic implementation that does **not**
import A4. Its manually checkable golden dataset has seven source messages and
asserts exact expected message/turn/session counts, initiations, unanswered
turns, response transitions, median latency and P25/P75/P90 latency.

```bash
python -m qa.analytics_validator source-messages.json analytics-result.json
```

The validator checks the A4 result against that oracle and also verifies
`source_message_ids` for conflict, silence, period, change-point, engagement,
dyadic and trend outputs.

CI additionally checks out a concrete A4 v6 implementation SHA and executes
production `analyze_conversation` on the same golden data. The actual A4 output
must match the independent oracle. Topic candidates/evidence are also required
to resolve exclusively to real source message IDs and evidence rows must resolve
to an emitted candidate.

Current pinned A4 contract SHA:

```text
ff18c7bd561583451f5b1c5f57f2cc0c82b5d200
```

## Result severity and module verdicts

Individual validators emit:

- `PASS` — no detected error or warning;
- `WARNING` — no proven integrity failure, but review is required;
- `FAIL` — at least one integrity, reconciliation or provenance failure.

At module level A7 uses the project verdict vocabulary:

- `VALID` — required checks for the stated scope pass independently;
- `PARTIALLY VALID` — validated scope passes, but an important untested scope
  remains;
- `INVALID` — a required invariant is proven false;
- `NEEDS REVIEW` — evidence is insufficient for a reliable verdict.

A verdict must always state its scope. Passing a synthetic/golden contract test
is not a claim that an arbitrary real archive has already been validated.

## Regression execution

```bash
python -m pip install -e .
python -m unittest discover -s qa/tests -v
```

The A7 GitHub Actions workflow runs the current A1→A2→A3 integration tests,
golden/corrupt staging gates, and a separate pinned A4 live-contract job.

## Next traceability gate

The remaining high-priority work is A5→A6 evidence-chain validation:

- duplicate or ambiguous message identity must fail closed;
- every assertion-bearing AI result exposed to the UI must have a traceable
  evidence contract or be explicitly marked as synthesis/non-evidentiary text;
- every selected/context/evidence message ID must exist in the same canonical
  conversation membership scope;
- UI packets must preserve canonical IDs without silently rewriting or dropping
  records;
- the complete chain must be resolvable as
  `UI/AI claim → canonical message → message_source → A1 source_record_key`.
