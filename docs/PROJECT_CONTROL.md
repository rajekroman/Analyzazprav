# A0 Project Control — Analýza zpráv

## Authoritative baseline

Version: `0.2.0-local-baseline`

## Workstreams

| Stream | Scope | Status | Current gate |
|---|---|---|---|
| A1 | iMessage/import | ACTIVE | lossless source-row import + idempotence PASS |
| A2 | normalized DB | ACTIVE | M:N message↔conversation provenance + attachments PASS |
| A3 | cleaning/threading | ACTIVE | deterministic sessions/reply-turn/latency slice PASS |
| A4 | metrics engine | ACTIVE | first per-conversation metrics PASS |
| A5 | AI interpretation | FROZEN | unlock only after evidence-selection contract |
| A6 | local UI | FROZEN | unlock after stable A1–A4 interfaces |
| A7 | QA/validation | ACTIVE | reconciliation + orphan checks PASS |

## A0 decisions

- One Python codebase, one normalized SQLite database.
- Source data is immutable; importer opens `chat.db` read-only.
- Message↔chat source relations are preserved separately from the convenience primary conversation.
- Re-import must be idempotent and `source_message_count == imported + duplicates` for every completed import run.
- No microservices, cloud DB, paid API, vector DB or background queue in core MVP.
- A3/A4 are deterministic. AI output later must cite normalized message IDs and time windows.

## Immediate integration order

1. Validate against a real copied `chat.db` and record schema deviations.
2. Add generic CSV/JSON import contract.
3. Expand A3 with burst/session statistics and media/system-message classification.
4. Expand A4 with initiation, turn length, latency distributions and period deltas.
5. Add evidence-selection contract for A5.
6. Unlock A6 only when source→metric traceability is demonstrated end-to-end.
