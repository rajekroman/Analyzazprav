# A7 QA and validation contract

A7 is an independent, read-only verification layer. It never repairs source
records, silently drops duplicates, or treats AI output as authoritative data.

## Validation gates

1. **SOURCE** — the original source is readable and fingerprinted.
2. **IMPORT** — A1 staging records reconcile to the source and preserve
   source-level identifiers and raw provenance.
3. **NORMALIZATION** — A2 canonical SQLite rows reconcile to A1 source mappings.
4. **ANALYTICS** — A3/A4 metrics reconcile to authoritative canonical SQL.
5. **TRACEABILITY** — A5 interpretations and A6 displays resolve to concrete
   canonical message IDs and, through provenance, back to source records.

The first implemented slice covers the IMPORT gate for an A1 staging directory
containing `manifest.json` and `messages.jsonl`.

## Run locally

```bash
python -m qa.staging_validator path/to/staging
python -m qa.staging_validator path/to/staging --report qa-report.json
python -m unittest discover -s qa/tests -v
```

The validator uses only the Python standard library and does not modify the
staging directory.

## Required A1 staging invariants

Each parsed JSONL record must be a JSON object and must contain a non-empty,
unique `source_record_key`. Duplicate keys are reported as errors; records are
not deleted.

A timestamp should be exposed in an unambiguous UTC/offset-aware form such as
`timestamp_utc`, or inside a timestamp object as `iso_utc`. Explicit Unix-unit
fields (`unix_s`, `unix_ms`, `unix_us`, `unix_ns`) are also supported.
Ambiguous local timestamps must not be silently assumed to be UTC.

`attachments` is optional but, when present, must be a list. If an attachment
claims a copied/relative local path and that file is missing, A7 reports it.
Files inside a staging `attachments/` directory that are not referenced by any
message are reported as orphans.

The manifest may declare the expected message count using `message_count`,
`messages_count`, `record_count`, `counts.messages`, or `counts.records`.
A mismatch is a hard failure.

## Result severity

- `PASS`: no errors or warnings.
- `WARNING`: no errors, but one or more conditions require review.
- `FAIL`: at least one integrity error.

Every issue carries a stable code and, where available, the exact
`source_record_key` and JSONL line number.

## Fingerprints

The report emits both source-file hashes and logical-record fingerprints:

- `messages_jsonl_sha256`: exact source bytes.
- `logical_sequence_sha256`: canonical JSON records in source sequence.
- `logical_record_set_sha256`: canonical JSON records sorted as a logical set.

This distinguishes byte-level changes, ordering changes, and logical content
changes.

## Golden dataset policy

`qa/fixtures/golden` is a tiny synthetic source-of-truth dataset that must pass.
`qa/fixtures/corrupt` is intentionally invalid and must fail. Future A2/A4/A5/A6
integration fixtures must be additive and keep source identifiers explicit.

## Integration requirements for downstream agents

A2 must preserve a source mapping that lets each canonical message resolve to
one or more A1 source records. A3/A4 must never replace canonical IDs with
derived-only identifiers. A5 must receive evidence message IDs with any
interpretation. A6 must keep those IDs available when displaying metrics,
selected messages, and AI analysis.

A7 will later add reconciliation tests for SQLite foreign keys, integrity,
canonical/source counts, attachment mappings, metric calculations, and AI/UI
evidence chains.
