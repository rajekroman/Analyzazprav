# A1 static-source preflight

## Purpose

Static export adapters must not create a staging directory and then fail later
because a structural or decoding error appears after one or more apparently
valid records.

The affected adapters are:

- iMazing Messages CSV;
- generic CSV, including explicit mapping profiles;
- generic JSON / JSONL;
- generic TXT.

## Contract

Before `_write_records()` creates any staging artifact, A1 exhausts the same
adapter parser once as a dry validation pass. The real import then reopens the
immutable static source and performs a second deterministic parser pass.

No parallel validation grammar is introduced. Validation and import therefore
use the same parser implementation.

For a structurally invalid or undecodable source:

- import raises explicitly;
- `staging/` is not created;
- no partial `messages.jsonl`, `errors.jsonl` or manifest is left behind;
- the source remains unchanged.

For a valid source, emitted staging data is unchanged; the trade-off is a second
sequential read of the static source. Reliability and auditability take priority
over this additional read cost.

## iMazing CSV losslessness

The iMazing adapter additionally rejects two structures that Python
`csv.DictReader` can otherwise make lossy:

1. duplicate literal header names, because later values can overwrite earlier
   values under the same dictionary key;
2. rows with more fields than the header, because extra values are exposed under
   the synthetic `None` key and must not be silently discarded.

Short rows remain representable: missing cells are preserved as empty values and
are not invented.

## Scope boundary

This preflight validates source structure and decoding only. It does not infer
message meaning, participant identity, timezone, or missing business semantics.
Attachment byte-resolution failures are a separate A1 concern.
