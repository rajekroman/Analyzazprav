# A7 — QA / validace

A7 je nezávislá auditní vrstva projektu Analýza zpráv. Neopravuje source ani canonical data. Jejím úkolem je programově prokázat, že jednotlivé vrstvy zachovaly vstupní záznamy, membership identity, provenance a deterministické analytické hodnoty.

## Autoritativní cesta

```text
source
→ A1 staging + reconciliation.json
→ A2 canonical SQLite v6
→ A3 derived processing v4
→ A4 deterministic analytics
→ A5 selective evidence-backed AI
→ A6 local UI/read model
→ A7 independent reports
```

A7 neudržuje paralelní message model a nesmí nahrazovat produkční import/normalizaci vlastním mockem. Pro L0→L2 používá skutečný A1/A2/A3 kód. Pro downstream A4/A5/A6 používá čisté nezávislé validátory nad serializovanými kontrakty a oddělené exact-head live adaptéry.

## CLI pro autoritativní L0→L2 gate

```bash
az-qa staging --staging ./staging/imessage
```

ověří aktuální A1 bundle včetně povinného `reconciliation.json`.

```bash
az-qa vertical \
  --staging ./staging/imessage \
  --database ./messages.sqlite
```

provede A1→A2→A3 reconciliation. Vertical gate nejprve vyžaduje PASS staging/reconciliation gate; teprve potom čte databázi.

Report je JSON a obsahuje `PASS`, `WARNING` nebo `FAIL`, konkrétní issue codes, counts, fingerprints a IDs aktuálních A2/A3 runs. Nenulový návratový kód znamená `FAIL`.

## A1 staging gate

Strukturální validator kontroluje současný A1 kontrakt:

- `contract_version = 1`;
- source type + immutable snapshot SHA-256;
- parser name/version;
- `messages_seen`, `messages_emitted`, `attachments_seen`, `errors`;
- fyzické JSONL counts;
- source type/SHA shodu každého recordu;
- unikátní `source_record_key` a deklarovaný key algorithm/version;
- iMessage key v2 = source snapshot + physical `message.ROWID`, nezávisle na chat membership;
- `sqlite_online_backup_v1` + committed-WAL provenance;
- timestamp syntax bez domýšlení neznámých timestamps;
- `conversation_sources[]` shape a source-relation multiplicitu;
- attachment occurrence accounting a hash format.

A1 `errors != 0`, count mismatch, duplicate/malformed record key nebo nevalidní snapshot provenance jsou `FAIL`.

## Povinný A1 source reconciliation gate

`az-qa staging` vyžaduje `manifest.outputs.reconciliation` (standardně `reconciliation.json`) vytvořený nad stejným immutable source snapshotem jako parser.

A7 kontroluje minimálně:

- `reconciliation_version = 1`;
- `status = ok` a `ok = true`;
- prázdné `failed_checks` a `parse_failures`;
- všechny interní `checks = true`;
- source type/SHA a `actual_sha256` shodné s manifestem;
- physical JSONL counts;
- explicitní `unsupported` a `duplicate` accounting;
- `manifest.counts.reconciliation_errors = 0`.

`unsupported` a `duplicate` nejsou samy o sobě ztráta dat: jsou explicitní osud source záznamu. Chybějící nebo failed reconciliation report je vždy `FAIL`.

## A1 → A2 reconciliation

A7 najde přesný completed import run podle:

```text
source_type + source_sha256 + parser_version
```

Potom vyžaduje exact accounting:

```text
A1 source_record_key multiset
== A2 message_source.source_record_key
```

```text
A1 attachment occurrences
== A2 attachment_source provenance occurrences
```

```text
A1 conversation_sources relations
== A2 message_source_conversation relations
```

Jedna physical source message ve dvou source chats proto musí zachovat jednu canonical message a dvě canonical/source membership vazby.

## A2 v6 integrity a provenance

A7 kontroluje:

- `PRAGMA integrity_check`;
- `PRAGMA foreign_key_check`;
- exact source-record provenance;
- complete M:N `message_conversation` model;
- attachment occurrence provenance;
- stabilní downstream views včetně `analysis_attachment_sources`.

`canonical_fingerprint()` vytváří deterministický logical SHA-256 nad autoritativními A2 tabulkami. Golden fixture vyžaduje stejný fingerprint před a po A3 persistence, takže A3 nesmí modifikovat canonical/source vrstvu.

## A2 → A3 reconciliation

A7 používá poslední completed A3 processing run a vyžaduje:

```text
COUNT(A2 memberships)
== processing_run.input_membership_count
== processing_run.output_membership_count
== COUNT(processed_message rows)
```

```text
SET(A2 membership_id)
== SET(A3 processed_message.membership_id)
```

```text
COUNT(DISTINCT A2 canonical message IDs)
== processing_run.canonical_message_count
```

Každá processed membership musí být dohledatelná k `source_record_key` přes A2 provenance. M:N canonical message nesmí být v A3 znovu deduplikována podle `message_id`.

## Golden vertical L0→L2 fixture

Release-blocking fixture skutečně spouští:

```text
synthetic Apple chat.db
→ import_imessage()
→ A1 reconciliation
→ A7 staging gate
→ ingest_a1_staging_bundle()
→ A2 v6
→ load_a2_projection()
→ process_messages()
→ ProcessingStore.persist()
→ A7 vertical validator
```

Obsahuje multichat membership, reply relation a attachment metadata. Negativní fixtures musí failovat při tampered source key, chybějícím/failed reconciliation reportu a odstraněné A3 membership.

# Downstream A4/A5/A6 gate

`src/analyzazprav/qa/downstream.py` je stdlib-only a neimportuje A4, A5 ani A6. Produkční moduly jsou spouštěny samostatnými exact-head adaptéry v `tools/a7_downstream/`; jejich serializovaný výstup teprve dostane nezávislý A7 validator.

Tím se A7 vyhýbá circular self-validation: modul nemůže projít jen proto, že jeho vlastní diagnostika říká `ok`.

## A4 independent metric oracle

`validate_a4_result()` znovu odvozuje ze source message/membership fixture:

- turn partition z A3 `session_id` + participant sequence;
- response transitions pouze mezi adjacent cross-participant turns ve stejné session;
- response latency z source timestamps;
- response effort ratio;
- participant message/word/turn counts;
- session initiations a initiation share;
- unanswered turns;
- mean / median / P25 / P75 / P90 response latency;
- median response effort;
- reciprocity.

Dále vyžaduje, aby všechna `source_message_ids` v conflicts, silence, time buckets, daily/period metrics, change points, engagement, dyadic regimes, trends a topics patřila skutečně do source fixture. Topic evidence musí odkazovat na existující source message a emitovaný topic candidate.

CI spouští skutečný A4 exact head a porovnává jeho `ConversationAnalytics` s tímto oracle. Exact SHA je vždy uvedeno v JSON reportu; VALID je omezené na daný contract SHA a fixture.

## A5 evidence-chain gate

`validate_a5_result()` nezávisle porovnává validovaný A5 result s přesným `AnalysisContext` předaným modelu.

Kontroluje:

- `summary_evidence`;
- observations / interpretations / patterns evidence;
- 1:1 turning-point text ↔ evidence;
- participant P1/P2 a shared-dynamic assertions ↔ evidence;
- evidence message IDs pouze z contextu;
- source-derived timestamp, sender a whitespace-normalized excerpt;
- metric phase/name/value pouze z deterministických `metrics_before/during/after`.

Live gate navíc ověřuje fail-closed A5 parser: neexistující message ID, neexistující metric reference a duplicate A6 packet message ID musí být odmítnuty.

AI text není source of truth. A7 validuje pouze jeho evidence chain a hranice tvrzení.

## A6 lossless read-model a UI traceability gate

A6 live gate používá skutečnou A2 v6 SQLite databázi vytvořenou produkčním `CanonicalDatabase` API. Fixture obsahuje:

- jednu canonical message současně ve dvou conversations;
- samostatné `membership_id` pro oba chaty;
- canonical zprávu s `sent_at_utc_us = NULL`;
- známý timestamp pro další zprávu;
- attachment occurrence;
- attachment-source provenance přes `analysis_attachment_sources`.

A7 potom porovnává A6 výstup přímo se stabilními A2 views a vyžaduje:

- exact membership set equality;
- zachování `message_id + conversation_id + membership_id` identity;
- žádné tiché odstranění unknown-time zprávy;
- A5 packet pouze z jedné conversation scope;
- unikátní packet message IDs a selected IDs;
- `membership_id` v každé packet message;
- exact `analysis_message_sources` projection;
- exact `analysis_attachments` projection;
- exact occurrence-scoped `analysis_attachment_sources` projection.

A7 také parsuje A6 renderer přes Python AST a ověřuje řetězec:

```text
A5 assertion
→ render_assertion
→ render_evidence_ref
→ render_result_evidence
→ canonical message / A2 source provenance
```

`summary`, participant claims, shared dynamic a turning points nesmí obejít evidence-aware rendering.

## Downstream release verdict

Workflow `.github/workflows/a7-downstream.yml` vytváří samostatné JSON reporty pro:

- authoritative A1→A3/A7 core;
- pinned A4;
- pinned A5;
- pinned A6.

`aggregate_release_verdict()` vrací:

- `VALID` — všechny požadované component reports jsou VALID a jejich jobs úspěšné;
- `NEEDS_REVIEW` — report nebo důkaz chybí;
- `INVALID` — některý invariant je prokazatelně porušen nebo component job selhal.

Failed job vždy přebíjí stale VALID report. Chybějící report se nikdy nepovažuje za PASS.

`release_ready=true` v tomto workflow znamená pouze **synthetic exact-head downstream contract ready**. Neznamená, že už byl validován libovolný reálný uživatelský archiv nebo všechny Apple schema varianty. Reálný `chat.db` zůstává samostatný source-specific release gate.

## Stavové zásady

- `PASS` — žádná chyba ani warning v konkrétním validatoru;
- `WARNING` — explicitní quality limitation;
- `FAIL` — reconciliation/integrity/provenance invariant je porušen;
- module `VALID` — požadovaný rozsah prošel nezávisle;
- module `NEEDS_REVIEW` — chybí dostatečný důkaz;
- module `INVALID` — alespoň jeden povinný invariant je prokazatelně false.

A7 nikdy chybu neopravuje, nikdy problematický záznam tiše nemaže a nikdy nepovyšuje interpretaci na fakt.
