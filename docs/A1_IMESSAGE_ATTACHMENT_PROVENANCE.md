# A1 iMessage message↔attachment relation provenance

Apple `message_attachment_join` is a source relation table. A1 previously preserved attachment multiplicity, but a valid emitted attachment occurrence was identified only by target `attachment.ROWID` plus its output position. Two source join rows pointing to the same attachment therefore survived as two occurrences, but the individual join rows were not directly traceable from staging.

## Output contract

For iMessage parser `0.10.0`, every valid emitted attachment occurrence retains the original attachment row fields in `raw_payload` and adds one reserved A1 namespace:

```json
{
  "raw_payload": {
    "filename": "a.jpg",
    "mime_type": "image/jpeg",
    "__analyzazprav_a1_message_attachment_relation__": {
      "source_relation_ordinal": 0,
      "raw_join_rowid": 42,
      "raw_message_id": 10,
      "raw_attachment_id": 20,
      "resolution_status": "resolved"
    }
  }
}
```

`source_attachment_id` remains the target `attachment.ROWID`. A1 does not replace attachment identity with join-row identity.

## Deterministic ordering

To avoid unnecessary A2 occurrence remapping across parser versions, A1 keeps the historical primary order by `attachment.ROWID` and uses `message_attachment_join.ROWID` only as a deterministic tie-breaker:

```text
ORDER BY attachment.ROWID, message_attachment_join.ROWID
```

Consequences:

- distinct target attachments retain their previous relative ordering;
- two valid join rows pointing to the same attachment remain two occurrences;
- duplicate-target ties become deterministic and individually traceable;
- A2 `position` remains stable for ordinary distinct attachments.

## Unsupported relations

A join row with missing message/attachment identifiers or a dangling target remains an explicit `unsupported` source outcome in the existing reconciliation report. It is not emitted as a fabricated attachment object.

The new attachment-provenance check covers **valid** relation rows only. Existing base reconciliation remains authoritative for invalid/dangling rows and count accounting.

## Reconciliation

`attachment_reconciliation.py` wraps all prior reconciliation layers and, for iMessage parser `>=0.10.0`, reconstructs valid source join occurrences from the same immutable SQLite snapshot.

It verifies:

1. attachment occurrence count per emitted message;
2. exact `raw_join_rowid`;
3. exact raw message and attachment targets;
4. deterministic source ordinal;
5. `source_attachment_id` agrees with the relation target.

The report adds:

- `checks.source_attachment_relation_provenance_matches_snapshot`;
- `attachment_relation_provenance.failure_count` and mismatch details;
- `raw_counts.source_valid_attachment_relation_rows`;
- `raw_counts.source_attachment_relation_provenance_occurrences`.

Manual `az-import reconcile` uses the same wrapper, not a weaker code path.

## A2 persistence

A2 already persists each attachment occurrence separately and stores A1 attachment `raw_payload` in `attachment_source.raw_payload_json`. The new relation provenance therefore survives A1→A2 without a schema migration.

This is source provenance, not canonical attachment identity. Canonical blob identity and source occurrence identity remain separate.

## Reserved namespace and preflight

The reserved raw-payload key is:

`__analyzazprav_a1_message_attachment_relation__`

To avoid silently overwriting a hypothetical source attachment column with the same name, iMessage preflight rejects such a schema before staging files are created.

Preflight also verifies that `message_attachment_join` and `chat_message_join` provide SQLite `ROWID`, because exact relation reconciliation already depends on those source row identities. `WITHOUT ROWID` variants are rejected explicitly rather than failing later with a cryptic SQL error.

## Parser version

Valid staging output changes, so the iMessage parser/output fingerprint increases `0.9.0 → 0.10.0`.

## Boundary

A1 records only the source relationship and its exact occurrence identity. It does not infer attachment semantics, merge duplicate files, or decide that two identical bytes represent the same user-visible event. Canonical blob identity remains A2 responsibility and analytical interpretation remains downstream.
