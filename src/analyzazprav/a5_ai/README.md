# A5 selective AI analysis

A5 is the interpretive layer of Analýza zpráv. It never replaces deterministic A1–A4 processing. It receives only a bounded, relevant context and returns structured, evidence-backed interpretations.

## Core guarantees

- local-first provider abstraction; **no provider or external AI service is selected automatically**;
- the caller must explicitly inject a provider implementation;
- deterministic A4 candidate selection before AI inference;
- A5 only consumes current A4 findings that pass `analysis_a4_reconciliation`;
- chronological context with physical blind-mode cutoffs;
- bounded context reduction that must preserve every declared candidate evidence message;
- missing/duplicate/out-of-window evidence fails before the provider is called;
- exactly one repair attempt after invalid model output;
- cache keyed by context, analysis type, mode, provider/model and prompt version;
- cached results are revalidated against current source-derived message snapshots and deterministic metric values before `CACHE_HIT` is returned;
- every material assertion must resolve to supplied source evidence;
- invalid or duplicate evidence IDs fail closed.

## Privacy boundary

A5 does not send a whole archive to a provider. `ContextBuilder` loads only the requested/candidate time window, applies blind-mode cutoff when requested, and deterministically reduces large contexts while treating declared evidence messages as mandatory.

If an A4 candidate declares a source message that is not present in the loaded A2 context, A5 refuses analysis rather than silently dropping that evidence. If the mandatory evidence alone exceeds `max_messages`, A5 also refuses rather than truncating evidence.

Provider choice remains an application/deployment decision. Local providers may be used, but there is no implicit Ollama/OpenAI/cloud fallback in A5 core.

## Evidence chain

The model is allowed to cite only existing message IDs and existing deterministic metric references. After validation, A5 enriches those references from `AnalysisContext` with the authoritative message timestamp, sender ID, safe excerpt and metric value. The model therefore cannot invent provenance metadata.

Assertion-bearing synthesis retains A6-compatible text fields with parallel source-derived evidence refs:

- `summary` + `summary_evidence`
- `turning_points` + `turning_point_evidence`
- `participant_p1` + `participant_p1_evidence`
- `participant_p2` + `participant_p2_evidence`
- `shared_dynamic` + `shared_dynamic_evidence`

Observations, interpretations and patterns also require evidence. Alternative explanations and explicit unknowns are kept separately so hypotheses are not presented as deterministic facts.

Prompt/cache contract: `a5-v3-assertion-evidence`.

## Cache trust boundary

Only a result that passed provider-output validation is stored with `COMPLETED` status. `AnalysisCache.get()` is not treated as authority: `AIAnalyzer` revalidates the deserialized cached result against the newly rebuilt `AnalysisContext` before returning `CACHE_HIT`.

Revalidation checks at least:

- all evidence IDs still exist in the current context;
- evidence message snapshots exactly match current source-derived timestamp, sender and safe excerpt;
- deterministic metric references still exist and have the current value;
- assertion-bearing fields still have non-empty evidence;
- confidence/strength fields remain within their contract.

A malformed or tampered cache entry is ignored and recomputed through the provider path.

## A2 handoff

`A2SQLiteMessageSource` reads canonical analytical views in read-only mode and provides message IDs, participant IDs, UTC timestamps, reply relations, attachment MIME types and edited/deleted flags.

## A4 v9 handoff

A4 is a deterministic candidate index, not an interpretation source. A5 adapters accept:

- conflict candidate → `conflict`
- change point → `change_point`
- engagement signal → `engagement_signal`
- dyadic regime → `dyadic_regime`
- lexical topic candidate → `lexical_topic`

`A4SQLiteCandidateSource` reads only published `analysis_a4_*` views in read-only/query-only mode. Before reading any candidate for a conversation it requires exactly one passing `analysis_a4_reconciliation` row, including:

- `uses_latest_processing_run = 1`;
- `membership_count_delta = 0`;
- zero invalid response/silence/event session counts;
- `reconciliation_ok = 1`;
- sender-accounted membership count equal to A4 source membership count when both fields are published.

An unreconciled or stale A4 run is therefore never sent to AI.

All A4 `source_message_ids` are preserved as A5 evidence IDs. A4 metric names, directions, regime labels and change points remain deterministic source signals; A5 must not silently reinterpret them as motives or psychological facts.

Lexical topic candidates remain explicitly lexical. For automatic A5 topic candidates, aggregate `source_message_ids_json` must exactly match normalized `analysis_a4_topic_evidence` for the same analytics run/topic key. Undated topic evidence is not automatically converted into a bounded A5 period candidate.

## Prompt behavior

The system prompt requires:

- observation vs interpretation separation;
- citations for every assertion-bearing field;
- metric references only to supplied deterministic A4 metrics;
- alternative explanations;
- explicit unknowns/limitations;
- no invented message, timestamp, sender, excerpt, metric, event, motive, diagnosis or external fact;
- psychological language only as a hypothesis, never a diagnosis or proven motive.

The validator, not the provider, resolves cited IDs and metrics back to source-derived evidence.

## A6 handoff

`integration_a6.py` accepts A6 `analysis_packet` schema v1. Selected message IDs become explicit manual evidence and can produce both an A5 request and a bounded message source. Duplicate packet message IDs or duplicate selected IDs are rejected.

## CI / golden slices

A5 promotion uses the full repository test suite, not only A5 unit tests.

A5 tests include:

1. hand-authored negative published-view fixtures for malformed/unreconciled A4 input;
2. a real current-main `A2 → A3 v5 → A4 v9 → A4SQLiteCandidateSource` smoke test;
3. deterministic A4 finding → A2 context → static provider → validated A5 result → A6 packet handoff;
4. missing-evidence tests that prove the provider is not called;
5. corrupted-cache tests that prove tampered evidence cannot return as `CACHE_HIT`.

No external AI service is required for CI.

## Failure isolation

If the model is unavailable, times out or produces invalid evidence, A5 returns an explicit failure status. A1–A4 and A6 remain usable; AI is enrichment, never a data-path dependency.
