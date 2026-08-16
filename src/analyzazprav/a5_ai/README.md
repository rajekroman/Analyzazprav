# A5 selective AI analysis

A5 is the interpretive layer of Analyzazprav. It never replaces deterministic A2–A4 processing. It receives only a bounded, relevant context and returns structured, evidence-backed interpretations.

## Core guarantees

- local-first provider abstraction; Ollama is the default implementation
- deterministic candidate selection before AI inference
- chronological context with physical blind-mode cutoffs
- bounded context reduction that preserves explicit evidence
- exactly one repair attempt after invalid model output
- cache keyed by context, analysis type, mode, provider/model and prompt version
- every material claim must resolve to supplied source evidence
- invalid or duplicate evidence IDs fail closed

## Evidence chain

The model is allowed to cite only existing message IDs and existing deterministic metric references. After validation, A5 enriches those references from `AnalysisContext` with the authoritative message timestamp, sender ID, safe excerpt and metric value. The model therefore cannot invent provenance metadata.

Assertion-bearing synthesis retains A6-compatible text fields with parallel source-derived evidence refs:

- `summary` + `summary_evidence`
- `turning_points` + `turning_point_evidence`
- `participant_p1` + `participant_p1_evidence`
- `participant_p2` + `participant_p2_evidence`
- `shared_dynamic` + `shared_dynamic_evidence`

Prompt/cache contract: `a5-v3-assertion-evidence`.

## A2 handoff

`A2SQLiteMessageSource` reads canonical analytical views in read-only mode and provides message IDs, participant IDs, UTC timestamps, reply relations, attachment MIME types and edited/deleted flags.

## A4 v9 handoff

The current A4 contract is used only as a deterministic candidate index. A5 adapters accept conflict candidates, change points, engagement signals, dyadic regimes, lexical topic candidates and analytic messages while preserving A4 `source_message_ids` exactly.

A4 metric names, directions and regime labels remain deterministic source signals; A5 does not silently reinterpret them as motives or psychological facts. Lexical topic candidates remain explicitly lexical (`lexical_ngram_v1`) and are never promoted to semantic truth by the adapter.

`A4SQLiteCandidateSource` reads published `analysis_a4_*` SQLite views in read-only mode and requires `analysis_a4_reconciliation.reconciliation_ok = 1` for the requested conversation before any A4 candidate can be used. Missing, ambiguous or failed reconciliation blocks A5 interpretation. Malformed JSON and duplicate source IDs also fail closed.

Production A4 v9 persists conflict events as `event_type = 'conflict_candidate'`. A5 consumes that exact contract and retains read compatibility with the historical draft label `conflict`.

## A6 handoff

`integration_a6.py` accepts A6 `analysis_packet` schema v1. Selected message IDs become explicit manual evidence and can produce both an A5 request and a bounded message source. Duplicate packet message IDs or duplicate selected IDs are rejected.

The current A6 PR renderer consumes the finalized parallel evidence fields through its assertion/evidence drill-down path, so no additional A5-side compatibility shim is needed.

## Golden deterministic E2E slice

`tests/a5_ai/test_golden_e2e.py` validates one synthetic SQLite database through the full A5 integration boundary:

`A4 reconciliation + analysis_a4_events -> A4SQLiteCandidateSource -> A2 analysis_messages -> ContextBuilder -> StaticProvider -> validated A5 result -> A6 analysis_packet candidate`

The fixture uses the production A4 v9 `conflict_candidate` label and a successful reconciliation row. The test proves that the same canonical message IDs survive from the A4 finding through source-derived A5 evidence and into the A6 handoff. It also verifies deterministic metric evidence (`conflict_score`) and source-derived sender/timestamp/excerpt data without any external AI service.

This is intentionally a CI-safe golden integration slice. A7 downstream validation is responsible for independently validating the exact A5 head used in a release composition; a validation pinned to an older A5 commit is not treated as evidence for a newer head.

## Failure isolation

If A4 is unreconciled, the model is unavailable, inference times out, or model output contains invalid evidence, A5 returns/raises an explicit failure rather than silently degrading provenance. A1–A4 and A6 remain usable; AI is enrichment, never a data-path dependency.
