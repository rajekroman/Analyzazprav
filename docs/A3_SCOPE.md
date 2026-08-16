# A3 — Zpracování a třídění

A3 je deterministická vrstva L2 / DERIVED nad kanonickými daty A2. Nemění RAW ani NORMALIZED A2 záznamy a nevytváří psychologické interpretace.

## Vlastnictví odpovědností

- **A2:** kanonické participant identity, conversations, messages, message↔conversation memberships, attachments a source provenance.
- **A3:** conservative cleaning, derived participant resolution/aliases, deterministic ordering, secondary duplicate audit, sender runs, sessions, source-evidenced reply threads, media classification, calendar/timing features a persistence derived dat.
- **A4:** programové komunikační metriky nad stabilním A2/A3 kontraktem.
- **A5:** selektivní AI interpretace pouze nad evidence připravenou A4.

A3 participant resolution není konkurenční kanonický model. A2 `participant` a `participant_identity` zůstávají autoritativní; A3 nad nimi vytváří auditovatelnou derived mapu pro analytické použití.

## Invariants

1. A3 je read-only vůči A2 RAW/NORMALIZED tabulkám.
2. A3 nikdy nemaže ani neslučuje kanonické messages.
3. Každý platný A2 `message_conversation` membership je v A3 zpracován samostatně.
4. Derived message features jsou identifikovány kombinací processing run + message + conversation a zachovávají A2 `membership_id`, pokud existuje.
5. Repeated punctuation, case a emoji přežijí cleaning.
6. Probable duplicates jsou pouze audit candidates.
7. Same A2 projection + config → stejný logický A3 výsledek.
8. Default session boundary je gap **strictly greater than 6 hours**.
9. Missing timestamp nevytváří vymyšlenou latency ani calendar fields.
10. Factual thread vzniká pouze z explicitního source relation.
11. Explicit reply může překročit session; nejednoznačný multi-conversation reply se bez conversation evidence nepřiřadí žádné conversation.
12. Missing attachment nikdy neodstraní parent message.
13. Local calendar fields vznikají pouze z explicitního A2 timezone offset.
14. Participant merge se neprovádí pouze na základě jména.

## Message ↔ conversation memberships

A2 v5 podporuje M:N vztah `message ↔ conversation`. A3 proto zpracovává **message occurrence in conversation**, ne pouze globální `message_id`.

Interní deterministický klíč je:

`(conversation_id, message_id)`

A2 `membership_id` je zachován jako provenance/evidence ID.

Příklad:

```text
message 100
 ├─ membership 500 → conversation 10
 └─ membership 501 → conversation 20
```

A3 vytvoří dvě derived rows. Canonical `message 100` zůstává v A2 pouze jednou.

`processing_run` eviduje odděleně počet kanonických messages a počet memberships.

## Participant resolution a aliasy

A3 načítá:

- `participant`
- `participant_identity`

a vytváří derived:

- `resolved_participant`
- `resolved_participant_member`
- `participant_alias`
- `participant_resolution_candidate`

### Bezpečná výchozí pravidla

- jeden A2 participant → jeden resolved participant;
- více A2 participants explicitně označených `is_self=1` → jeden resolved self participant (`explicit_is_self_union_v1`, confidence `1.0`);
- všechny A2 identities se zachovají jako aliases s odkazem na původní `participant_id` a `participant_identity_id`;
- shodné normalizované `canonical_name` u různých participants → pouze candidate (`confidence=0.35`), nikdy automatický merge;
- nejisté aliasy se neslučují destruktivně.

`processed_message.resolved_sender_id` umožňuje A4 agregovat různé identity stejné explicitně potvrzené osoby.

Sender-runs používají resolved sender. Pokud tedy dvě sousední zprávy pocházejí ze dvou A2 identities, které jsou explicitně stejný `is_self` participant cluster, nevznikne falešný nový turn.

## Replies a threads

`reply` / `reply_to` jsou factual pouze tehdy, když jejich conversation membership lze určit bez hádání.

- source a target mají právě jednu společnou conversation → relation se použije;
- mají více společných conversations → A3 relation nepřiřadí, pokud metadata neposkytují explicitní `conversation_id`;
- relation nikdy nepropojí dvě různé conversations.

Structural thread method: `explicit_reply_component_v2`, confidence `1.0`.

## Duplicate audit

A2 je autorita kanonické deduplikace. A3 pouze flaguje:

- `exact_source_identity`
- `probable_cross_export`

Kandidáti nikdy nemažou canonical messages. Porovnávání je omezené na relevantní sousední records, ne O(n²) all-pairs.

## Text processing

`text_clean` normalizuje pouze transportní artefakty (CR/LF, NBSP, control chars, trailing transport whitespace). Zachovává case, emoji, repeated punctuation a meaningful line breaks.

A2 `message.text` a `message_source.raw_text` se nemění.

## Media

A3 deterministicky klasifikuje attachment occurrences podle MIME, fallback extension:

- image
- gif
- video
- audio
- document
- other

Attachment occurrence rows z A2 se nezplošťují; opakovaný stejný blob je při feature counts započítán podle skutečných occurrences.

## Time

A3 počítá UTC year/month/day/weekday/hour. Lokální atributy počítá pouze z A2 `timezone_offset_min`.

Žádný hard-coded CET/CEST offset.

## Persistence

`database/a3_schema.sql` obsahuje pouze rebuildovatelná DERIVED data. Při změně draft A3 schema lze A3 tabulky znovu vytvořit; A2 tabulky se nikdy nedropují.

Hlavní analytické views:

- `a3_analysis_messages`
- `a3_analysis_participants`
- `a3_analysis_participant_aliases`

## CLI

```bash
python -m analyzazprav.processing path/to/messages.sqlite
```

Výstup reportuje zvlášť canonical message count a membership count.

## Výstup pro A4

A4 dostane zejména:

- conversation/message/membership evidence IDs;
- resolved participant + alias mapping;
- resolved sender per membership;
- sender runs;
- sessions;
- explicit threads;
- duplicate audit candidates;
- media/timing/calendar features;
- processing version/run provenance.

A4 nesmí znovu rekonstruovat source memberships ani vytvářet vlastní person model.
