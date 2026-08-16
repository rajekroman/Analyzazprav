# Architektura — Analýza zpráv

## Princip

Local-first pipeline. Zdrojový export je neměnný autoritativní vstup. Normalizovaná SQLite databáze je odvozená pracovní vrstva. AI nikdy není zdrojem dat a nesmí měnit původní zprávy.

## Tok

`source → importer → normalized SQLite → deterministic processing → metrics → selected context → optional AI interpretation → UI/report`

## MVP stack

- Python 3.10+;
- standard library only for core ingestion/QA;
- SQLite for normalized local data;
- no cloud database, paid API, vector database or background queue in core MVP;
- optional AI is a later adapter, not a dependency of ingestion/verification.

## Data invariants

1. Every normalized message keeps `source_id` + stable `external_id` and original `raw_rowid` where available.
2. Re-import is idempotent.
3. Attachments are separate entities linked through a join table.
4. Timestamps are normalized to UTC, source-specific conversion is isolated in importer code.
5. QA must be able to reconcile source message count with import results.
