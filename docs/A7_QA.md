# A7 — QA / validace

A7 je nezávislá auditní vrstva projektu Analýza zpráv. Jejím úkolem není opravovat data, ale prokázat, že skutečná cesta A1→A2→A3 zachovala zdrojové záznamy, vztahy a provenance a že derived processing odpovídá canonical memberships.

## Autoritativní cesta

```text
source
→ A1 staging + reconciliation.json
→ A2 canonical SQLite v5
→ A3 derived processing v4
→ A7 reconciliation report
```

A7 neudržuje paralelní message model. Čte existující staging kontrakt, A1 source reconciliation a existující A2/A3 tabulky read-only.

## CLI

```bash
az-qa staging --staging ./staging/imessage
```

ověří aktuální A1 bundle **včetně povinného `reconciliation.json`**.

```bash
az-qa vertical \
  --staging ./staging/imessage \
  --database ./messages.sqlite
```

provede A1→A2→A3 reconciliation. Vertical gate nejprve vyžaduje PASS staging/reconciliation gate; teprve potom čte databázi.

Návratový kód je nenulový při `FAIL`. Report je JSON a obsahuje `PASS`, `WARNING` nebo `FAIL`, konkrétní issue codes, counts, fingerprints a IDs aktuálních A2/A3 runs.

## A1 staging gate

Nízkoúrovňový strukturální validator kontroluje přesný současný A1 kontrakt:

- `contract_version = 1`;
- source type + source snapshot SHA-256;
- parser name/version;
- `messages_seen`, `messages_emitted`, `attachments_seen`, `errors`;
- JSONL record count;
- source type/SHA shodu každého recordu;
- unique `source_record_key`;
- deklarovaný record-key algoritmus/version;
- iMessage key v2 = source snapshot + physical `message.ROWID`, bez chat membership;
- iMessage `sqlite_online_backup_v1` + committed-WAL provenance;
- timestamp syntax bez hádání neznámých timestamps;
- `conversation_sources[]` shape a duplicate relations;
- attachment occurrence count a hash format.

A1 `errors != 0`, count mismatch, duplicate/malformed record key nebo nevalidní iMessage snapshot provenance jsou `FAIL`.

## Povinný A1 source reconciliation gate

`az-qa staging` nad strukturálním validátorem vyžaduje `manifest.outputs.reconciliation` (standardně `reconciliation.json`). Report musí být výstup A1 reconciliation nad stejným immutable source snapshotem, který byl hashován a parsován.

A7 kontroluje minimálně:

- `reconciliation_version = 1`;
- `status = ok` a `ok = true`;
- prázdné `failed_checks` a `parse_failures`;
- všechny interní `checks` musí být `true`;
- reconciliation source type/SHA i `actual_sha256` musí souhlasit s A1 manifestem;
- `messages_jsonl_records` a `errors_jsonl_records` musí odpovídat fyzickým JSONL souborům;
- počty `unsupported_records` a `duplicate_records` musí odpovídat manifest counts;
- `manifest.counts.reconciliation_errors` musí být 0.

`unsupported` a `duplicate` nejsou samy o sobě chyba: jsou platný explicitní osud source záznamu. Chybějící, nevalidní nebo failed reconciliation report je vždy `FAIL`.

A7 ukládá také SHA-256 fingerprint `reconciliation.json`, aby šel konkrétní QA výsledek svázat s konkrétním source reconciliation důkazem.

## A1 → A2 reconciliation

A7 najde přesný completed A2 import run podle:

```text
source_type + source_sha256 + parser_version
```

Potom kontroluje:

```text
A1 source_record_key multiset
== A2 message_source.source_record_key pro import run
```

Dále:

```text
A1 attachment occurrences
== A2 attachment_source rows pro import run
```

A:

```text
A1 conversation_sources relations
== A2 message_source_conversation relations pro import run
```

Tím se kontrolují nejen entities, ale i vazby. Jedna source message ve dvou source chats proto musí dát dvě source relation rows; nestačí pouze zachovat canonical message.

## A2 integrity gate

A7 vždy kontroluje:

- `PRAGMA integrity_check`;
- `PRAGMA foreign_key_check`;
- přítomnost required A2/A3 tables;
- exact source-record provenance;
- complete membership model.

`canonical_fingerprint()` vytváří deterministický logical SHA-256 nad autoritativními A2 tabulkami. Golden integration test počítá fingerprint před a po A3 persistence a vyžaduje jejich rovnost.

Tím test prokazuje, že A3 derived processing nezměnil A2 source/canonical vrstvu.

## A2 → A3 reconciliation

A7 používá poslední `completed` A3 processing run a vyžaduje:

```text
COUNT(A2 analysis_messages memberships)
== processing_run.input_membership_count
== processing_run.output_membership_count
== COUNT(processed_message rows pro run)
```

Současně:

```text
SET(A2 membership_id)
== SET(A3 processed_message.membership_id)
```

A:

```text
COUNT(DISTINCT A2 canonical message IDs)
== processing_run.canonical_message_count
```

To je kritické pro A2 M:N model: jedna canonical message ve dvou konverzacích musí zůstat dvěma processed memberships, nikoli se znovu deduplikovat podle message ID.

## Provenance gate

Každá A3 processed membership musí být dohledatelná k source recordu:

```text
processed_message.membership_id
→ message_source_conversation
→ message_source.source_record_key
```

Pokud jedna processed membership nemá žádný `source_record_key`, A7 vrací `FAIL`.

## Golden vertical fixture

Release-blocking A7 test nevytváří paralelní ručně psané staging schema. Skutečně spouští:

```text
synthetic Apple chat.db
→ current import_imessage()
→ current A1 source reconciliation
→ current A7 staging bundle gate
→ current ingest_a1_staging_bundle()
→ current A2 v5
→ current load_a2_projection()
→ current process_messages()
→ current ProcessingStore.persist()
→ current A7 vertical validator
```

Fixture obsahuje:

- dvě physical source messages;
- jednu message současně ve dvou source chats;
- explicit reply GUID;
- attachment metadata;
- 2 canonical messages;
- 3 canonical memberships;
- 3 source conversation relations.

Expected QA result je `PASS`, A1 reconciliation je `ok` a A2 fingerprint před/po A3 musí být identický.

## Negativní fixtures

A7 úmyslně porušuje data a očekává `FAIL` minimálně v těchto případech:

1. staging `source_record_key` je změněný oproti deklarovanému algorithm/version;
2. `reconciliation.json` chybí;
3. `reconciliation.json` deklaruje failed check;
4. jedna A3 `processed_message` membership je odstraněna.

Poslední případ musí aktivovat minimálně:

- `A3_OUTPUT_ACCOUNTING_MISMATCH`;
- `A2_A3_MEMBERSHIP_SET_MISMATCH`.

Vertical gate s chybějícím/failed A1 reconciliation reportem nesmí pokračovat jako PASS do databázové vrstvy.

## Stavové kódy

- `PASS` — žádná chyba ani warning;
- `WARNING` — data jsou použitelné, ale existuje explicitní quality limitation;
- `FAIL` — reconciliation/integrity/provenance invariant je porušen.

A7 nikdy chybu neopravuje a nikdy tiše nemaže problematický záznam.

## Hranice A7

A7 zatím validuje L0/L1/L2 datovou cestu. Po stabilizaci A4/A5/A6 se stejný princip rozšíří o:

- A4 metric recomputation/evidence IDs;
- A5 citation validity a evidence-packet provenance;
- A6 zobrazené message/membership IDs a metric IDs.

AI output nikdy není autoritativní source data.
