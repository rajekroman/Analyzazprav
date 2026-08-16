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

## A4 v6 handoff

The current A4 contract is used as a deterministic candidate index. A5 adapters accept:

- `ConflictCandidate` → `conflict`
- `ChangePoint` → `change_point`
- `EngagementPeriodSignal` → `engagement_signal`
- `DyadicRegime` → `dyadic_regime`
- `TopicCandidate` → `lexical_topic`
- `AnalyticMessage` → bounded A5 message context

All A4 `source_message_ids` are preserved as A5 evidence IDs. A4 metric names, directions and regime labels remain deterministic source signals; A5 does not silently reinterpret them as motives or psychological facts.

`A4SQLiteCandidateSource` additionally reads the published `analysis_a4_*` SQLite views directly in read-only mode. Malformed JSON and duplicate source IDs fail closed. Missing optional views only disable that candidate type.

Lexical topic candidates remain explicitly lexical (`lexical_ngram_v1`). A5 may interpret their surrounding message evidence, but the candidate itself is not promoted to semantic truth.

## A6 handoff

`integration_a6.py` accepts A6 `analysis_packet` schema v1. Selected message IDs become explicit manual evidence and can produce both an A5 request and a bounded message source. Duplicate packet message IDs or duplicate selected IDs are rejected.

The current A6 PR renderer already consumes the finalized parallel evidence fields through its assertion/evidence drill-down path, so no additional A5-side compatibility shim is needed.

## Golden deterministic E2E slice

`tests/a5_ai/test_golden_e2e.py` validates one synthetic SQLite database through the full A5 integration boundary:

`A4 analysis_a4_events -> A4SQLiteCandidateSource -> A2 analysis_messages -> ContextBuilder -> StaticProvider -> validated A5 result -> A6 analysis_packet candidate`

The test proves that the same canonical message IDs survive from the A4 finding through source-derived A5 evidence and into the A6 handoff. It also verifies deterministic metric evidence (`conflict_score`) and source-derived sender/timestamp/excerpt data without any external AI service.

This is intentionally a CI-safe golden integration slice. A7 PR #11 has been notified of the finalized A5 contract and this golden handoff. The final project release gate still requires the stacked A1→A7 pipeline to be assembled and independently reconciled by A7 on the integrated database.

## Failure isolation

If the model is unavailable, times out or produces invalid evidence, A5 returns an explicit failure status. A1–A4 and A6 remain usable; AI is enrichment, never a data-path dependency.
