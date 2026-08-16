# A4 — performance a query-plan profilování

A4 optimalizace nesmí měnit datovou úplnost, provenance ani výsledné metriky. Výkon je nižší priorita než correctness; každý zásah proto musí vycházet ze skutečného read path a musí zachovat A7 reconciliation invarianty.

## 1. Dva podporované režimy

### Vybraná konverzace

Interaktivní workflow typicky analyzuje jeden konkrétní chat. Pokud volající předá `conversation_ids`, A4 nyní propisuje výběr až do SQL membership readu a do A3 v5 resolved-sender lookupu.

To znamená, že například analýza jedné dlouhé konverzace nemusí nejdřív materializovat zprávy a participant-resolution evidence všech ostatních chatů v archivu.

Fail-closed pravidla se nemění: chybná nebo neúplná provenance uvnitř vybrané konverzace stále analýzu zastaví.

### Celý archiv

Bez `conversation_ids` A4 zachovává jeden full-archive průchod. Žádné záznamy se kvůli optimalizaci nevynechávají.

## 2. Read-only profiler

Profiler se spouští nad existující projektovou SQLite databází:

```bash
python -m analyzazprav.analytics.profile /path/to/messages.sqlite --conversation-id 123 --repeat 3
```

Celý archiv:

```bash
python -m analyzazprav.analytics.profile /path/to/messages.sqlite --repeat 3
```

Profiler otevírá databázi v SQLite `mode=ro` a nastavuje `PRAGMA query_only = ON`. Nic nepersistuje.

JSON výstup obsahuje:

- počet analyzovaných conversations a messages,
- min/median/max čas source-load fáze,
- min/median/max čas deterministické A4 analýzy,
- median celkového času,
- orientační messages/second,
- raw `EXPLAIN QUERY PLAN` evidence pro membership read a A3 resolved-sender read.

Opakované běhy zároveň kontrolují, že source-message count a source fingerprint výsledků zůstávají deterministické.

## 3. Existující indexy relevantní pro A4

Před přidáním dalšího indexu je nutné vzít v úvahu již existující struktury:

- A2 `message_conversation(conversation_id, message_id)`,
- A2 `message(conversation_id, sent_at_utc_us, id)`,
- A3 `processed_message(processing_run_id, conversation_id, sequence_number)`,
- A3 `processed_message(membership_id, processing_run_id)`,
- A3 composite primary key `processed_message_resolved_sender(processing_run_id, membership_id)`.

Nový index se nemá přidávat pouze proto, že „by mohl být rychlejší“. Nejprve musí profiler na realistické databázi ukázat konkrétní scan, temp-sort nebo jiný opakovatelný bottleneck, který existující indexy neřeší.

## 4. Benchmark pravidla

Výkonnostní čísla nejsou correctness gate sama o sobě, protože závisejí na CPU, SQLite verzi, cache a velikosti databáze. Pro porovnání změn používat:

1. stejnou databázi,
2. stejný `conversation_id` nebo stejný full-archive režim,
3. stejný počet opakování,
4. median místo jediného běhu,
5. raw query plan před i po změně,
6. beze změny source count, fingerprintů a A7 reconciliation výsledků.

CI má testovat strukturální performance invarianty (například SQL pushdown vybrané konverzace), ne křehký limit v milisekundách.

## 5. Další krok nad reálným exportem

Po připojení realistického dlouhodobého iMessage archivu se mají uložit profiler výsledky pro:

- cílovou dlouhou konverzaci,
- několik krátkých konverzací,
- celý archiv.

Teprve z těchto dat se rozhodne, zda je potřeba další index, streaming/chunking nebo jiná optimalizace. AI ani heuristická interpretace se do této vrstvy nezapojují.
