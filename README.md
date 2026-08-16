# Analýza zpráv

Local-first foundation for importing and analyzing message archives, initially macOS/iMessage `chat.db`.

## Implemented baseline

- normalized SQLite schema;
- read-only iMessage `chat.db` import;
- Apple timestamp normalization;
- lossless message↔conversation provenance (including chat-less source rows);
- participants, messages and attachment linkage;
- idempotent re-import;
- import reconciliation and integrity verification;
- deterministic session/reply-turn/response-latency features;
- first per-conversation metrics;
- zero runtime dependencies outside Python standard library.

## Run without installation

```bash
PYTHONPATH=src python -m analyza_zprav.cli --db data/analyza-zprav.sqlite3 init
PYTHONPATH=src python -m analyza_zprav.cli --db data/analyza-zprav.sqlite3 import-imessage /path/to/chat.db
PYTHONPATH=src python -m analyza_zprav.cli --db data/analyza-zprav.sqlite3 verify
PYTHONPATH=src python -m analyza_zprav.cli --db data/analyza-zprav.sqlite3 process
PYTHONPATH=src python -m analyza_zprav.cli --db data/analyza-zprav.sqlite3 conversations
PYTHONPATH=src python -m analyza_zprav.cli --db data/analyza-zprav.sqlite3 metrics 1
```

Use a copy of `~/Library/Messages/chat.db`; the source database itself is opened in SQLite read-only mode.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## CI

GitHub Actions validates Python 3.10 and 3.13 with compilation, the complete unit-test suite, and a CLI initialization/integrity smoke test.
