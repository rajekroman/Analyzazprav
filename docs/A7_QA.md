# A7 — QA / validace

A7 je nezávislá auditní vrstva projektu Analýza zpráv. Neopravuje source ani canonical data. Programově ověřuje, že jednotlivé vrstvy zachovaly vstupní záznamy, memberships, provenance a deterministické analytické výsledky.

## Autoritativní cesta

```text
source
→ A1 staging + reconciliation.json
→ A2 canonical SQLite v6
→ A3 processing / participant resolution v5
→ A4 deterministic analytics v9
→ A5 selective evidence-backed AI
→ A6 local UI/read model
→ A7 independent reports
```

A7 nesmí vytvářet paralelní datový nebo analytický model tam, kde už autoritativní implementace existuje. A4 má po promotion na `main` vlastní nezávislý A7 arithmetic/evidence oracle (`qa/a4_oracle.py`, `qa/analytics_validator.py`); downstream A7 proto tuto logiku neduplikuje a rozšiřuje pouze dosud chybějící A5/A6 gate.

## L0–L2 CLI

```bash
az-qa staging --staging ./staging/imessage
```

ověří A1 bundle včetně povinného `reconciliation.json`.

```bash
az-qa vertical \
  --staging ./staging/imessage \
  --database ./messages.sqlite
```

provede A1→A2→A3 reconciliation. Vertical gate vyžaduje PASS staging/reconciliation gate před databázovou kontrolou.

Report je JSON s `PASS`, `WARNING` nebo `FAIL`, issue codes, counts, fingerprints a run IDs. `FAIL` vrací nenulový exit code.

## A1 staging / source reconciliation

A7 kontroluje současný A1 contract včetně:

- immutable source type/SHA-256;
- parser/version identity;
- physical JSONL counts;
- unikátních `source_record_key` podle deklarovaného algoritmu;
- iMessage immutable snapshot/WAL provenance;
- explicitních unknown timestamps;
- `conversation_sources[]` vztahů;
- attachment occurrence accounting;
- `reconciliation_version = 1`, `status = ok`, `ok = true`;
- prázdných `failed_checks` / `parse_failures`;
- shody reconciliation source SHA s manifestem.

Explicitně označený `unsupported` nebo `duplicate` záznam není tichá ztráta; chybějící nebo failed reconciliation report je vždy `FAIL`.

## A1 → A2 v6

A7 hledá exact completed import run přes:

```text
source_type + source_sha256 + parser_version
```

a vyžaduje exact reconciliation:

```text
A1 source_record_key multiset
== A2 message_source.source_record_key
```

```text
A1 conversation source relations
== A2 message_source_conversation relations
```

```text
A1 attachment occurrences
== A2 attachment_source occurrences
```

A2 integrity gate vždy zahrnuje `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, complete M:N `message_conversation` model a source provenance. A2 v6 publikuje stabilní downstream views včetně `analysis_attachment_sources`.

## A2 → A3 v5

A7 vyžaduje:

```text
COUNT(A2 memberships)
== processing_run.input_membership_count
== processing_run.output_membership_count
== COUNT(A3 processed memberships)
```

```text
SET(A2 membership_id)
== SET(A3 processed_message.membership_id)
```

A3 participant-resolution oracle je samostatně exportovaný přes `validate_participant_resolution`; A7 neimplementuje vlastní alias heuristiku.

`canonical_fingerprint()` prokazuje, že A3 persistence nezměnila autoritativní A2 source/canonical vrstvu.

## A4 v9 — autoritativní oracle už na main

A4 promotion na `main` obsahuje nezávislé A7 kontroly v:

- `src/analyzazprav/qa/a4_oracle.py`;
- `src/analyzazprav/qa/analytics_validator.py`;
- A4/A7 reconciliation testech.

Oracle nezávisle počítá jednoduché manuálně ověřitelné turns/response/latency/accounting metriky a kontroluje evidence IDs. Downstream A7 PR tuto implementaci neduplikuje; plná repository regression suite je `core` komponenta downstream release verdictu.

## A5 evidence-chain gate

`src/analyzazprav/qa/downstream.py::validate_a5_result()` nezávisle porovnává validovaný A5 výsledek s přesným `AnalysisContext` předaným modelu.

Kontroluje:

- `summary_evidence`;
- observations / interpretations / patterns evidence;
- 1:1 turning-point text ↔ evidence;
- participant P1/P2 a shared-dynamic assertions ↔ evidence;
- evidence message IDs pouze z contextu;
- source-derived timestamp, sender a whitespace-normalized excerpt;
- metric phase/name/value pouze z deterministických `metrics_before/during/after`;
- žádné extra evidence snapshots mimo deklarované message IDs.

Exact-head live adapter navíc vyžaduje, aby A5 fail-closed odmítlo neexistující message ID, neexistující metric reference a duplicate A6 packet message ID.

AI text není source of truth. A7 validuje evidence chain a auditní hranice tvrzení.

## A6 lossless read-model / UI traceability gate

A6 live gate používá reálnou A2 v6 SQLite databázi vytvořenou produkčním `CanonicalDatabase` API. Fixture obsahuje:

- jednu canonical message současně ve dvou conversations;
- dvě samostatná `membership_id`;
- canonical zprávu s `sent_at_utc_us = NULL`;
- další známý timestamp;
- attachment occurrence;
- attachment-source provenance.

A7 porovnává A6 výstup přímo s autoritativními A2 views a vyžaduje:

- exact membership set equality;
- zachování `membership_id + message_id + conversation_id`;
- žádné tiché odstranění unknown-time zprávy;
- A5 packet pouze z jedné conversation scope;
- unikátní packet message/selected IDs;
- `membership_id` v packet messages;
- exact `analysis_message_sources` projection;
- exact `analysis_attachments` projection;
- exact occurrence-scoped `analysis_attachment_sources` projection.

A7 také parsuje A6 renderer přes Python AST a kontroluje řetězec:

```text
A5 assertion
→ render_assertion
→ render_evidence_ref
→ render_result_evidence
→ canonical message / A2 source provenance
```

Summary, participant claims, shared dynamic a turning points nesmí obejít evidence-aware rendering.

## Downstream release verdict

Workflow `.github/workflows/a7-downstream.yml` má tři autoritativní komponenty:

1. `core-a7` — kompletní integrovaná A1→A4 + A7 regression/oracle suite na aktuální branch;
2. `a5-live` — exact-head A5 evidence contract;
3. `a6-live` — exact-head A6 lossless/provenance/UI contract proti A2 v6 fixture.

`aggregate_release_verdict()` vrací:

- `VALID` — všechny požadované reports jsou VALID a jobs úspěšné;
- `NEEDS_REVIEW` — report/důkaz chybí;
- `INVALID` — invariant je prokazatelně porušen nebo component job selhal.

Failed job vždy přebíjí stale VALID report. Chybějící report není PASS.

### Self-contained release provenance — schema v2

Finální `a7-release-verdict.json` musí být auditovatelný bez zpětného čtení workflow YAML. Schema v2 proto obsahuje `component_contracts` s immutable expected/observed Git SHAs pro každou testovanou komponentu:

```json
{
  "schema_version": 2,
  "component_contracts": {
    "core": {
      "expected_sha": "<tested merge-ref SHA>",
      "observed_sha": "<SHA emitted by core report>"
    },
    "A5": {
      "expected_sha": "<pinned A5 SHA>",
      "observed_sha": "<SHA emitted by A5 live report>"
    },
    "A6": {
      "expected_sha": "<pinned A6 SHA>",
      "observed_sha": "<SHA emitted by A6 live report>"
    }
  }
}
```

A7 vyžaduje full 40-character lowercase Git SHA a exact equality `expected_sha == observed_sha`. Přítomný component report s chybějícím/malformed SHA nebo s jiným SHA je release-blocking `INVALID`. Zcela chybějící component report si ponechává obecnou semantiku `NEEDS_REVIEW` — A7 nesmí tvrdit, že nesoulad existuje, pokud důkaz vůbec nebyl dodán.

Tím je samotný release artefakt svázán s přesnými testovanými kódy; auditor nemusí identitu komponent odvozovat z branch names, workflow konfigurace ani pořadí jobů.

`release_ready=true` znamená pouze **integrovaný core + synthetic exact-head downstream contract ready**. Neznamená, že byl validován libovolný reálný uživatelský archiv nebo všechny Apple Messages schema varianty.

## Stavové zásady

- `PASS` — žádná chyba ani warning v konkrétním validatoru;
- `WARNING` — explicitní quality limitation;
- `FAIL` — reconciliation/integrity/provenance invariant je porušen;
- module `VALID` — deklarovaný rozsah prošel nezávisle;
- module `NEEDS_REVIEW` — chybí dostatečný důkaz;
- module `INVALID` — alespoň jeden povinný invariant je false.

A7 nikdy chybu neopravuje, nikdy problematický záznam tiše nemaže a nikdy nepovyšuje AI interpretaci na fakt.
