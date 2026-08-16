# A7 — QA / validace

A7 je nezávislá auditní vrstva projektu Analýza zpráv. Neopravuje data; prokazuje, že skutečná cesta od source importu přes canonical/derived data až po deterministické A4 metriky zachovala účetní úplnost, provenance a reprodukovatelnost.

## Autoritativní cesta

```text
source
→ A1 staging + reconciliation.json
→ A2 canonical SQLite v6
→ A3 derived processing v5 + participant resolution
→ A4 deterministic analytics
→ A7 reconciliation/oracle report
```

A7 neudržuje paralelní message model. Čte existující A1 kontrakt a A2/A3/A4 tabulky read-only. Kde validuje metriku, provádí vlastní výpočet místo důvěry v self-reported A4 diagnostiku.

## CLI

```bash
az-qa staging --staging ./staging/imessage
az-qa vertical --staging ./staging/imessage --database ./messages.sqlite
az-qa participants --database ./messages.sqlite
az-qa analytics --database ./messages.sqlite
```

- `staging` validuje A1 staging a povinný source reconciliation artifact;
- `vertical` reconciliuje A1→A2→A3 membership/provenance cestu;
- `participants` nezávisle ověřuje A3 participant/alias resolution;
- `analytics` nezávisle přepočítává release-critical A4 metriky.

Návratový kód je nenulový při `FAIL`. Reporty obsahují konkrétní issue codes, counts a relevantní run/provenance IDs.

## A1 staging a reconciliation

A7 kontroluje současný A1 kontrakt včetně:

- source snapshot SHA-256 a parser version;
- `source_record_key` uniqueness a iMessage key-v2 pravidla;
- WAL-aware immutable SQLite snapshot provenance;
- message/attachment/conversation-relation counts;
- povinného `reconciliation.json`;
- `status=ok`, `ok=true`, nulových failed checks/parse failures;
- shody source type/SHA a fyzických JSONL counts;
- explicitního accounting `unsupported` a `duplicate` outcomes.

Chybějící nebo failed reconciliation je `FAIL`. Explicitní `unsupported`/`duplicate` není chyba, pokud je korektně zaúčtovaný.

## A1 → A2

A7 vyžaduje přesnou shodu:

```text
A1 source_record_key multiset
== A2 message_source.source_record_key
```

pro přesný completed import run. Dále reconciliuje attachment occurrences a source conversation relations přes `message_source_conversation`.

## A2 integrity

Povinné jsou:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

A7 kontroluje required tables/views, exact source provenance a complete M:N membership model. `canonical_fingerprint()` umožňuje dokázat, že downstream processing nezměnil A2 authoritative data.

## A2 → A3

Pro poslední completed A3 run musí platit:

```text
COUNT(A2 analysis_messages memberships)
== processing_run.input_membership_count
== processing_run.output_membership_count
== COUNT(processed_message rows)
```

a zároveň:

```text
SET(A2 membership_id) == SET(A3 processed_message.membership_id)
```

Každá processed membership musí být dohledatelná přes `message_source_conversation` na `message_source.source_record_key`.

## A3 participant resolution

A7 `participants` nezávisle kontroluje A3 resolution sidecars proti A2 participants/identities.

Konzervativní pravidlo:

- explicitní A2 `is_self` identities se mohou sjednotit do jedné resolved identity;
- stejné display name samo o sobě nesmí způsobit automatický merge;
- nejisté vazby zůstávají candidate/evidence, nikoli fakt.

A4 participant accounting musí používat auditovaný A3 resolved sender, s fallbackem na A2 sender tam, kde resolution není dostupná.

## A4 analytics oracle

Příkaz:

```bash
az-qa analytics --database ./messages.sqlite
```

neimportuje A4 engine. Z A2/A3 dat znovu sestaví a porovná minimálně:

- conversation coverage;
- resolved participant attribution;
- message/word/turn/session accounting;
- session initiations;
- response samples;
- response latency a percentily;
- unanswered turns;
- question/style marker counts;
- participant summaries a reciprocity;
- gap-free daily metrics;
- deterministic change-point candidates a source-message evidence;
- exact vazbu A4 na latest A3 `processing_run_id`.

### Response latency

Platný response sample vzniká pouze mezi sousedními turns ve stejné A3 session, s dvěma známými různými participants.

```text
latency = response_turn.start_us - previous_turn.end_us
```

Neznámý timestamp nevytváří vymyšlenou latency.

### Initiation

Initiator je doslovný první turn session. Je-li jeho sender neznámý, initiation zůstává neznámá a nepřipíše se pozdějšímu známému účastníkovi.

### Change points

A7 z transparentního uloženého A4 configu znovu počítá gap-free daily series a rolling prior-person baseline. Nulové activity dny se nezahazují. Change point je statistický kandidát, ne interpretace motivace nebo psychologie.

## A4 provenance / incremental gate

Každý A4 analytics run je vázaný na konkrétní A3 `processing_run_id`.

Nový A3 run invaliduje A4 i při identickém logickém obsahu, protože session/run IDs existují v novém provenance namespace. A7 hlásí stale vazbu jako `A4_STALE_A3_PROVENANCE`.

## Negativní release fixtures

A7 musí fail-closed zachytit minimálně:

- změněný A1 `source_record_key`;
- chybějící/failed `reconciliation.json`;
- zahozenou A3 membership;
- chybnou A3 participant resolution;
- ručně změněnou A4 persisted response latency (`A4_RESPONSE_SAMPLE_MISMATCH`);
- A4 navázané na starší A3 run;
- nekonzistentní initiation semantics.

## Stavové kódy

- `PASS` — žádná chyba ani warning;
- `WARNING` — použitelný výsledek s explicitní quality limitation;
- `FAIL` — porušený reconciliation/integrity/provenance/metric invariant.

A7 chyby neopravuje a nikdy tiše nemaže problematický záznam.

## Hranice po A4

Po povýšení A4 je A7 pokrytí autoritativní pro datovou cestu L0→L2 a deterministické části L3/A4.

Další rozšíření A7 patří až k A5/A6:

- A5 evidence-packet completeness, citation validity a assertion→evidence vazby;
- A6 zobrazené message/membership/metric IDs a možnost dohledat UI závěr zpět na source.

AI output nikdy není autoritativní source data a A7 nesmí nahrazovat deterministickou metriku AI odhadem.
