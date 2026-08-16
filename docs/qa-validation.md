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

## A1 staging gate

A1 currently emits `manifest.json` and `messages.jsonl`. The authoritative
contract observed on `agent/a1-imessage-import` uses:

- `contract_version`
- `source.type`, `source.sha256`
- `counts.messages_seen`, `counts.attachments_seen`, `counts.errors`
- per-message `source_record_key`, `source_message_id`,
  `conversation_source_id`, `source_sha256`
- `timestamp_raw`, `timestamp_utc`, `timestamp_precision`
- attachment source metadata

Run:

```bash
python -m qa.staging_validator path/to/staging
python -m qa.staging_validator path/to/staging --report a7-staging-report.json
```

A7 independently re-checks Apple-epoch timestamp conversion, reconciles manifest
message/attachment counts, rejects export errors, checks source SHA-256
provenance, recomputes A1 `stable_message_key`, detects duplicate record keys,
and reports local attachment problems without deleting data.

The current A1 first slice stores `source_path` as metadata; A7 does not assume
that this original absolute path has been copied into the staging directory.
Future copied files can use `relative_path`/`copied_path`, which A7 verifies.

## A2 SQLite gate

Run:

```bash
python -m qa.sqlite_validator path/to/messages.sqlite
python -m qa.sqlite_validator path/to/messages.sqlite --report a7-sqlite-report.json
```

The validator opens SQLite read-only and checks:

- `PRAGMA integrity_check`
- `PRAGMA foreign_key_check`
- required A2 canonical tables and analytical views
- every canonical message has `message_source` provenance
- every canonical attachment has `attachment_source` provenance
- source hashes are present
- completed imports have finish timestamps
- analytical view counts reconcile to canonical tables/mappings
- non-empty WAL presence is surfaced for reproducibility

## Exact A1 → A2 reconciliation

A source-level identity must survive normalization. A2's independently computed
`source_hash` is useful for A2 idempotency but is not a substitute for the A1
`source_record_key`.

The A1→A2 bridge contract therefore requires A2 `message_source` to preserve the
upstream `source_record_key` verbatim and to bind `import_run.source_fingerprint`
to A1 `manifest.source.sha256`.

Run the exact reconciliation with:

```bash
python -m qa.reconcile_a1_a2 path/to/staging path/to/messages.sqlite
python -m qa.reconcile_a1_a2 path/to/staging path/to/messages.sqlite --report a7-reconcile.json
```

The reconciler refuses fuzzy matching. It checks exact set equality of A1 and
A2 message source keys for the matching import run and reconciles attachment
source IDs as a multiset. Multiple A2 `message_source` rows may legitimately
map to one canonical `message` after GUID-based deduplication; no source row may
disappear.

Until A2 exposes/persists `message_source.source_record_key`, this NORMALIZATION
gate intentionally fails with `A2_SOURCE_RECORD_KEY_COLUMN_MISSING`.

## Result severity

- `PASS`: no errors or warnings.
- `WARNING`: no errors, but one or more conditions require review.
- `FAIL`: at least one integrity error.

Every staging issue carries a stable code and, where available, the exact
`source_record_key` and JSONL line number.

## Fingerprints

Staging reports emit exact-source and logical fingerprints:

- `messages_jsonl_sha256`
- `manifest_sha256`
- `logical_sequence_sha256`
- `logical_record_set_sha256`

SQLite reports emit `database_sha256` and, when present, `wal_sha256`.

## Golden dataset policy

`qa/fixtures/golden` mirrors the current A1 staging shape and must pass.
`qa/fixtures/corrupt` is intentionally invalid and must fail. SQLite and
A1→A2 reconciliation tests create minimal temporary databases and cover both
valid mappings and deliberate provenance failures.

Run all A7 regression tests with:

```bash
python -m unittest discover -s qa/tests -v
```

## Downstream traceability requirements

A2 must preserve a source mapping that lets each canonical message resolve to
one or more A1 source records. A3/A4 must never replace canonical IDs with
derived-only identifiers. A5 must receive evidence message IDs with any
interpretation. A6 must keep those IDs available when displaying metrics,
selected messages, and AI analysis.

Future A7 slices will add canonical timestamp/text mapping checks after the
A1→A2 bridge lands, exact analytical metric checks, and A5/A6 evidence-chain
tests against integrated branches.
