# A2 — local normalization workflow

A2 converts an A1 staging bundle into the canonical local SQLite database used by A3+.

## Install for development

```bash
python -m pip install -e .
```

No external runtime dependency is required; A2 uses Python's standard-library `sqlite3`.

## 1. Create or migrate the canonical database

```bash
az-normalize init --database data/messages.sqlite
```

This applies all pending numbered migrations and runs SQLite integrity/foreign-key checks.

## 2. Ingest an A1 staging bundle

Given:

```text
staging/
├── manifest.json
└── messages.jsonl
```

run:

```bash
az-normalize ingest-a1 \
  --database data/messages.sqlite \
  --staging staging
```

The command returns JSON containing:

- schema version;
- import run ID;
- whether the same A1 ingest was already present;
- message/attachment/relation counts;
- SQLite integrity results.

A repeated ingest of the same source + parser version is idempotent. Reprocessing the same source with a different parser version creates a new provenance run while reusing stable canonical messages where A1 source identity or GUID proves equivalence.

## 3. Check the canonical database

```bash
az-normalize check --database data/messages.sqlite
```

`check` first brings the database to the current A2 migration level and then runs:

- `PRAGMA integrity_check`;
- `PRAGMA foreign_key_check`;
- canonical table counts.

The command exits non-zero when the database is missing or an integrity check fails.

## Source traceability

For A1 imports:

- `import_run.source_sha256` is the exact SHA-256 of the original source from A1 `manifest.source.sha256`;
- `import_run.source_fingerprint` identifies the concrete A1 parser/contract representation;
- `message_source.source_record_key` preserves A1 source-message identity verbatim;
- `message_source.raw_text` and `raw_payload_json` preserve source evidence.

This provides the exact A1 → A2 bridge required by A7 without making A3/A4 depend on the original `chat.db` schema.

## Privacy

Real message databases, SQLite/WAL files, staging data and attachments belong under ignored local paths and must not be committed to Git.
