# A7 QA and validation contract

A7 is an independent, read-only verification layer. It never repairs source
records, silently drops duplicates, or treats AI output as authoritative data.

## Validation gates

1. **SOURCE** — the original source is readable and fingerprinted.
2. **IMPORT** — A1 staging records reconcile to the source and preserve
   source-level identifiers and raw provenance.
3. **NORMALIZATION** — A2 canonical SQLite rows reconcile to A1 source mappings.
4. **ANALYTICS** — A3/A4 metrics reconcile to authoritative source message IDs.
5. **TRACEABILITY** — A5 interpretations and A6 displays resolve to concrete
   canonical message IDs and, through provenance, back to source records.

## A1 staging gate

A1 currently emits `manifest.json` and `messages.jsonl`. The authoritative
contract observed on `agent/a1-imessage-import` uses `contract_version`, source
SHA-256, exact source IDs, Apple timestamps, and attachment source metadata.

```bash
python -m qa.staging_validator path/to/staging
```

A7 independently re-checks Apple-epoch timestamp conversion, manifest counts,
source SHA-256 provenance and A1 `stable_message_key`. It never deletes a
record to make a validation pass.

## A2 SQLite gate

```bash
python -m qa.sqlite_validator path/to/messages.sqlite
```

The read-only validator runs SQLite integrity/foreign-key checks, requires the
canonical A2 objects, checks message/attachment provenance, import state and
analytical view reconciliation, and surfaces a live WAL for reproducibility.

## Exact A1 → A2 reconciliation

A2's independently computed `source_hash` is not a substitute for A1's upstream
identity. The bridge must preserve `source_record_key` verbatim and bind
`import_run.source_fingerprint` to A1 `manifest.source.sha256`.

```bash
python -m qa.reconcile_a1_a2 path/to/staging path/to/messages.sqlite
```

The reconciler uses exact message-key set equality and attachment-source-ID
multiset equality. It allows multiple source rows to map to one canonical
message after valid deduplication, but no source row may disappear. Until A2
persists `message_source.source_record_key`, this gate intentionally returns
`A2_SOURCE_RECORD_KEY_COLUMN_MISSING`.

## A4 analytical accounting gate

A7 does not accept A4's own `diagnostics.accounting_ok` as proof. The result
must independently reconcile to the exact source messages supplied to A4.
Serialize a `ConversationAnalytics` result with dataclass/JSON-compatible
fields and run:

```bash
python -m qa.analytics_validator source-messages.json analytics-result.json
```

The validator checks, independently of A4 formulas:

- every non-reaction source message occurs exactly once across turns;
- excluded reactions never re-enter analytical turns;
- declared message/turn/session counts equal their actual structures;
- every turn occurs exactly once in the session partition;
- session initiator equals the first turn participant;
- every cross-participant adjacent turn transition has exactly one latency
  sample and its reported seconds equal the timestamps;
- conflict evidence resolves to real source messages in the referenced session;
- participant message/turn/initiation totals reconcile to global totals.

This specifically catches structural loss even if A4's internal diagnostic says
`accounting_ok=True`.

## Result severity

- `PASS`: no errors or warnings.
- `WARNING`: no errors, but review is required.
- `FAIL`: at least one integrity or provenance error.

## Fingerprints and golden data

Staging reports include byte-level and logical SHA-256 fingerprints. SQLite
reports include the database hash and WAL hash when present. `qa/fixtures/golden`
mirrors the current A1 contract, while deliberately corrupt fixtures and
synthetic SQLite/analytics results prove that the validators reject known bad
states.

Run all A7 regressions with:

```bash
python -m unittest discover -s qa/tests -v
```

## Downstream traceability requirements

A3/A4 must preserve canonical message IDs in all derived structures and
candidates. A5 must receive explicit evidence message IDs, validate every model
reference against the supplied context, and expose evidence in its output. A6
must retain those IDs in the displayed context packet and analytical views.

Future A7 slices will bind the validator directly to integrated A4 outputs,
check exact metric formula values against independent SQL/oracle fixtures, and
validate the A5→A6 evidence chain end-to-end.
