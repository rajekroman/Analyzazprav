# A3 — Zpracování a třídění

A3 je deterministická L2 transformační vrstva nad kanonickými daty A2. Nemění RAW ani canonical A2 záznamy a neprovádí behaviorální nebo psychologickou interpretaci.

## Vlastnictví odpovědností

- **A2:** canonical participants/person identities, conversations, messages, source provenance, source/canonical memberships a autoritativní technická deduplikace.
- **A3:** konzervativní čištění textu, deterministické pořadí, secondary duplicate audit, sender runs, temporal sessions, source-evidenced reply threads, media classification, calendar features a verzovaná persistence derived dat.
- **A4:** deterministické komunikační statistiky a neutrální změnové kandidáty.
- **A5:** selektivní AI interpretace nad doložitelným kontextem.

A3 používá A2 `sender_id`, canonical `message.id` a hlavně A2 `message_conversation.id` (`membership_id`). Nevytváří konkurenční systém osob ani konverzací.

## Invariants

1. A3 je read-only vůči A2 RAW/canonical vrstvě.
2. A3 nikdy nemaže ani neslučuje canonical messages nebo memberships.
3. Jedna canonical message může mít více validních conversation memberships; A3 zpracuje každou membership samostatně.
4. Repeated punctuation, letter case a emoji přežijí cleaning.
5. Probable duplicates jsou pouze audit candidates; A2 entity zůstávají zachované.
6. Temporal adjacency je měřitelný strukturální jev, nikdy důkaz reply.
7. Stejná A2 projection + config vytvoří stejný logický derived výsledek.
8. Default session boundary je gap **strictly greater than six hours**.
9. Missing timestamp nevytváří latency ani odhadovaný časový vztah.
10. Threads vznikají pouze z explicitních A2 relation types nakonfigurovaných jako reply (`reply`, `reply_to`).
11. A2 message-level reply relation se v A3 promítne pouze do conversation memberships, které oba endpointy skutečně sdílejí. Relation nesmí spojit dva různé chaty.
12. Explicit reply může překročit temporal session boundary; takový thread má `session_id = NULL`.
13. Missing attachment zůstává reprezentovaný; parent message se neztrácí.
14. Local calendar fields vznikají pouze z explicitního A2 `timezone_offset_min`; bez něj zůstávají `NULL`.
15. Každý persisted processing run je auditovatelný a starší runs se při novém výpočtu nemažou.

## A2 v5 contract used

A3 čte:

- `analysis_messages` — **jedna řádka na message-conversation membership**, včetně `membership_id`, canonical message ID, conversation ID, sendera a timestamps;
- `analysis_attachments` — attachment occurrences;
- `message_source` — source IDs, source ordering hints a `source_record_key` provenance;
- `message_relation` — explicit source-evidenced relations.

A3 není závislé na konkrétním A1 formátu (iMessage/iMazing/CSV/JSON/TXT).

## Identity model

A3 strukturální identita je `membership_id`, nikoli samotné canonical `message_id`.

Příklad:

```text
canonical message 42
├── membership 501 → conversation A
└── membership 502 → conversation B
```

A3 vytvoří dva processed membership rows. Canonical `message_id=42` zůstává na obou pro provenance. Sender runs, sessions, sequence numbers a timing features se počítají uvnitř konkrétní konverzace.

## Text processing

Cleaning je záměrně konzervativní: transport control characters, line-ending differences a NBSP mohou být normalizovány, ale case, emoji a repeated `!`/`?` se zachovávají. `text_clean` existuje pouze v A3 derived storage; A2 `message.text` a `message_source.raw_text` se nemění.

## Media classification

Attachment occurrences jsou deterministicky klasifikovány z MIME type a následně filename extension do:

- `image`
- `gif`
- `video`
- `audio`
- `document`
- `other`

A3 ukládá také total attachment count a `missing_attachment_count`. Obecný audio soubor není bez explicitního source evidence automaticky označen jako voice message.

## Time periods

Pro timestamped membership A3 odvozuje UTC year/month/day/weekday/hour. Pokud A2 poskytne `timezone_offset_min`, odvodí i local year/month/day/weekday/hour přesně z tohoto offsetu.

`utc_weekday` a `local_weekday` používají Python `datetime.weekday()`: Monday = `0`, Sunday = `6`.

## Derived persistence

A3 používá `database/a3_schema.sql` a ukládá immutable logical runs.

`processing_run` obsahuje:

- processing algorithm version;
- config;
- input membership count;
- unique canonical message count;
- output membership count;
- audit timestamps/status.

`processed_message` má primární klíč:

```text
(processing_run_id, membership_id)
```

Sender runs, sessions a threads jsou rovněž `processing_run_id` scoped. Druhý výpočet nad stejnou databází proto nepřepisuje první. Convenience view `analysis_processed_messages_latest` zpřístupňuje poslední completed run bez ztráty historie.

Pouze při detekci starého nekompatibilního **draft A3 schema** může A3 rebuildnout A3-derived tables; A2 tables se nikdy nedropují.

## Duplicate audit

A3 nevykonává destructive deduplication. Výstupem jsou pouze candidate pairs s metodou a confidence. Canonical rozhodnutí zůstává A2.

## CLI

Po instalaci projektu:

```bash
az-process path/to/messages.sqlite
```

nebo:

```bash
python -m analyzazprav.processing path/to/messages.sqlite
```

Volitelné flags nastavují session gap a duplicate timestamp tolerance.

## Current vertical gate

A3 promotion gate instaluje jednotný projekt, ověřuje:

```text
az-import --help
az-normalize --help
az-process --help
```

a spouští kompletní A1+A2+A3 regression suite. Release-blocking test obsahuje i A2 v5 případ, kdy jedna canonical message existuje ve dvou conversation memberships, a ověřuje dva současně zachované processing runs.