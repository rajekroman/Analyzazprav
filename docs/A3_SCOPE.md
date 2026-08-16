# A3 — Zpracování a třídění

A3 je deterministická L2 transformační vrstva nad kanonickými daty A2. Nemění RAW ani canonical A2 záznamy a neprovádí behaviorální nebo psychologickou interpretaci.

## Vlastnictví odpovědností

- **A2:** canonical participants/person identities, conversations, messages, source provenance, source/canonical memberships a autoritativní technická deduplikace.
- **A3:** konzervativní čištění textu, **derived participant resolution a alias mapping**, deterministické pořadí, secondary duplicate audit, sender runs, temporal sessions, source-evidenced reply threads, media classification, calendar features a verzovaná persistence derived dat.
- **A4:** deterministické komunikační statistiky a neutrální změnové kandidáty.
- **A5:** selektivní AI interpretace nad doložitelným kontextem.

A2 `participant` a `participant_identity` zůstávají kanonickou autoritou. A3 nad nimi vytváří pouze auditovatelnou DERIVED person mapu; nepřepisuje A2 identity ani nevytváří paralelní canonical model.

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
16. Participant alias se nikdy neslučuje pouze podle display name.
17. Každý derived participant/alias mapping nese source participant/identity ID, metodu a confidence.
18. Sender-run a opposite-sender timing používají resolved person identity, aby změna aliasu téže explicitně potvrzené osoby nevytvořila falešný turn/response.

## A2 v5 contract used

A3 čte:

- `analysis_messages` — **jedna řádka na message-conversation membership**, včetně `membership_id`, canonical message ID, conversation ID, sendera a timestamps;
- `analysis_attachments` — attachment occurrences;
- `message_source` — source IDs, source ordering hints a `source_record_key` provenance;
- `message_relation` — explicit source-evidenced relations;
- `participant` — canonical A2 participant IDs, names a explicit `is_self` evidence;
- `participant_identity` — canonical phone/e-mail/iMessage identity aliases.

A3 není závislé na konkrétním A1 formátu (iMessage/iMazing/CSV/JSON/TXT).

## Message ↔ conversation identity

A3 strukturální identita zprávy je `membership_id`, nikoli samotné canonical `message_id`.

```text
canonical message 42
├── membership 501 → conversation A
└── membership 502 → conversation B
```

A3 vytvoří dva processed membership rows. Canonical `message_id=42` zůstává na obou pro provenance. Sender runs, sessions, sequence numbers a timing features se počítají uvnitř konkrétní konverzace.

## Participant resolution a aliasy

A3 persistence obsahuje:

- `resolved_participant`;
- `resolved_participant_member`;
- `participant_alias`;
- `participant_resolution_candidate`.

Výchozí pravidla jsou záměrně konzervativní:

- jeden A2 participant je samostatný resolved participant;
- více A2 participants explicitně označených `is_self=1` se sjednotí do jednoho self group (`explicit_is_self_union_v1`, confidence `1.0`);
- všechny A2 `participant_identity` záznamy se zachovají jako aliases s původním `participant_id` a `participant_identity_id`;
- shodné normalizované `canonical_name` u různých non-self participants vytváří pouze `participant_resolution_candidate` (`confidence=0.35`), nikdy automatický merge;
- neexistuje fuzzy/name-only destructive merge.

`processed_message.resolved_sender_id` a `sender_run.resolved_participant_id` jsou DERIVED odkazy pro A4. Pokud dvě sousední zprávy patří dvěma explicitním self aliases téže resolved osoby, zůstávají v jednom sender-run a nevzniká falešná `seconds_since_previous_other_sender` hodnota.

## Text processing

Cleaning je záměrně konzervativní: transport control characters, line-ending differences a NBSP mohou být normalizovány, ale case, emoji a repeated `!`/`?` se zachovávají. `text_clean` existuje pouze v A3 derived storage; A2 `message.text` a `message_source.raw_text` se nemění.

## Media classification

Attachment occurrences jsou deterministicky klasifikovány z MIME type a následně filename extension do `image`, `gif`, `video`, `audio`, `document`, `other`. A3 ukládá total attachment count a `missing_attachment_count`; missing file nikdy neodstraní parent message.

## Time periods

Pro timestamped membership A3 odvozuje UTC year/month/day/weekday/hour. Pokud A2 poskytne `timezone_offset_min`, odvodí i local year/month/day/weekday/hour. `weekday()` používá Monday = `0`, Sunday = `6`.

## Derived persistence

A3 používá `database/a3_schema.sql` a ukládá immutable logical runs. `processing_run` obsahuje algorithm version, config, input membership count, unique canonical message count, output membership count a audit timestamps/status.

`processed_message` má primární klíč `(processing_run_id, membership_id)`. Sender runs, sessions, threads a participant-resolution outputs jsou rovněž processing-run scoped. Convenience views zpřístupňují poslední completed run bez ztráty historie.

Pouze při detekci starého nekompatibilního **draft A3 schema** může A3 rebuildnout A3-derived tables; A2 tables se nikdy nedropují.

## Duplicate audit

A3 nevykonává destructive deduplication. Výstupem jsou pouze candidate pairs s metodou a confidence. Canonical rozhodnutí zůstává A2.

## CLI

```bash
az-process path/to/messages.sqlite
```

nebo:

```bash
python -m analyzazprav.processing path/to/messages.sqlite
```

CLI reportuje memberships, canonical messages, resolved participants, aliases, sender runs, sessions, threads a audit candidates.

## Vertical gate

A3 promotion gate instaluje jednotný projekt, ověřuje `az-import --help`, `az-normalize --help`, `az-process --help` a spouští kompletní A1+A2+A3 regression suite. Release-blocking testy zahrnují membership preservation i participant-resolution/alias switching bez mutace A2 dat.
