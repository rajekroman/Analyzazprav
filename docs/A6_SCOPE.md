# A6 — Lokální rozhraní

A6 je poslední interaktivní vrstva projektu Analýza zpráv. Neprovádí import ani nenahrazuje deterministickou analytiku; zpřístupňuje skutečná data A2, metriky a nálezy A4 a selektivní interpretaci A5 tak, aby každý významný závěr šel dohledat zpět ke konkrétním zprávám a jejich source provenance.

## Primární workflow

`kontakt → konverzace → období → zprávy / časová osa / grafy → významné období / lexikální téma → evidence → původní zpráva → AI analýza`

## MVP obrazovky

- přehled kontaktů;
- explicitní výběr konkrétní konverzace;
- konverzace a message browser;
- časová osa aktivity;
- základní grafy;
- významná období / analytické nálezy A4;
- auditovatelná A4 lexikální témata s exact message evidence;
- vybrané zprávy, canonical attachment occurrences a jejich provenance;
- AI analýza A5 s assertion-level evidence.

## Aktuální implementace

- Streamlit, čistě lokálně;
- anonymizovaná vestavěná demo data pro vývoj bez pipeline;
- SQLite pouze pro čtení (`mode=ro`);
- explicitní production adapter pro membership-aware A2 `analysis_messages`;
- kompatibilní fallback přes automatickou detekci message tabulky/view pouze pro ne-A2 SQLite zdroje;
- navigace `Kontakt → conversation_id → období`;
- preservation `membership_id`, stable canonical `message_id` a unknown timestamps;
- explicitní prezentační filtr unknown-time zpráv; defaultně se neztrácejí;
- auditovatelný prohlížeč zpráv s membership i canonical identitou;
- lazy A2 provenance resolver přes `analysis_message_sources`;
- canonical attachment occurrences přes `analysis_attachments`;
- source provenance attachment occurrences přes `analysis_attachment_sources`, pokud je view publikované;
- A4 latest-run metriky přes `analysis_a4_daily`, `analysis_a4_participants`, `analysis_a4_responses`, `analysis_a4_conversations`;
- pokud A4 publikuje `analysis_a4_reconciliation`, metriky, nálezy a témata se považují za autoritativní pouze při `reconciliation_ok=1`;
- lokální adjacency-gap výpočet pouze jako explicitně označený fallback, pokud A4 views nejsou dostupné;
- A4 latest-run adapter pro events, change points a nestabilní dyadické režimy;
- explicitní drill-down `A4 nález → source_message_ids → canonical message membership → A2 source record`;
- A4 `lexical_ngram_v1` témata přes `analysis_a4_topics`, `analysis_a4_topic_evidence`, `analysis_a4_topic_periods` a `analysis_a4_topic_period_reconciliation`;
- topic candidate `source_message_ids_json` se musí přesně shodovat s normalizovanými evidence rows; nesoulad failuje closed;
- topic period reconciliation se před zobrazením kontroluje proti skutečně načteným evidence/topic/message counts;
- lexikální phrase/salience se explicitně neprezentují jako sémantická nebo psychologická interpretace;
- exact topic evidence lze použít jako explicitní selekci pro A5;
- explicitní ruční výběr zpráv;
- konfigurovatelné okolí před/po vybraných zprávách;
- A6 `analysis_packet` schema v1 pro A5;
- packet zachovává `membership_id` jako auditní metadata, A5 evidence identita zůstává `message_id`;
- unknown-time zprávy se pro A5 časově nedomýšlejí; selected/context unknown timestamp packet explicitně zablokuje;
- volitelný explicitní lokální A5 trigger přes Ollama, aktivovaný pouze pokud je A5 modul integrován;
- evidence-backed zobrazení A5 summary, observations, interpretations, patterns, turning points, participant assertions a shared dynamic;
- message i metric evidence se u A5 assertionu zobrazují jako dohledatelná evidence;
- žádné automatické AI volání a žádný cloud fallback.

## Hranice odpovědnosti

A6 neimportuje iMessage, nededuplikuje zdrojová data, nemění A2/A3/A4 SQLite vrstvy a nevytváří vlastní analytickou pravdu. Pokud jsou A4 analytické views dostupné, A6 je používá jako autoritativní zdroj metrik pouze tehdy, když publikovaný reconciliation kontrakt nehlásí chybu.

A6 nesmí skrýt porušenou evidence chain. Pokud A4 nebo A5 odkazuje na `message_id`, který v membership-scoped canonical datech dané konverzace chybí, UI tuto skutečnost explicitně zobrazí jako chybu dohledatelnosti.

A6 nesmí považovat `message_id` za identitu UI řádku napříč všemi konverzacemi. A2 podporuje více memberships stejné fyzické zprávy a A6 proto používá `membership_id` jako row identity. Stejně tak `NULL sent_at_utc_us` není důvod zprávu odstranit.

Lokální fallback při absenci A4 je pouze prezentační pomůcka. Sousední časový rozdíl při změně odesílatele se označuje jako adjacency gap, nikoli jako prokázaná response latency.

A4 lexikální témata jsou deterministická lexical-ngram evidence. A6 z nich samo nevytváří latentní témata, motivace ani psychologické závěry.

## Spuštění

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Vývojový režim může použít anonymizovaná demo data. Pro skutečnou analýzu se v levém panelu zvolí SQLite databáze vytvořená pipeline A1 → A2 → A3 → A4.

## Kontrakt A2

A6 preferuje autoritativní A2 views:

- `analysis_messages` — membership-scoped zprávy; A6 vyžaduje `membership_id`, `id`, `conversation_id`, `sender_name`, `sent_at_utc_us`, `timestamp_precision`, `timestamp_quality`, `text`;
- `analysis_conversations` — název / canonical key konverzace;
- `analysis_message_sources` — source provenance canonical zpráv;
- `analysis_attachments` — canonical attachment occurrence metadata včetně `occurrence_id`;
- `analysis_attachment_sources` — source provenance attachment occurrence přes `occurrence_id`.

`sent_at_utc_us` se převádí explicitně jako Unix epoch v mikrosekundách. `NULL` zůstává `NaT` a je validní stav read modelu. A6 jej nevynucuje na falešný čas.

Pokud A2 views nejsou přítomné, message adapter může použít read-only schema discovery jako kompatibilní fallback. V takovém zdroji se nevyrábí falešná A2 provenance ani membership tvrzení; chybějící membership identity dostane pouze explicitní `compat:` lokální identifikátor read-model řádku.

Attachment provenance se čte výhradně z publikovaného `analysis_attachment_sources`. A6 nepoužívá physical `attachment_source` jako paralelní API.

## Kontrakt A4

A6 čte pouze publikované latest-run views.

Metriky:

- `analysis_a4_conversations`;
- `analysis_a4_participants`;
- `analysis_a4_responses`;
- `analysis_a4_daily`.

Významné nálezy:

- `analysis_a4_events`;
- `analysis_a4_changes`;
- `analysis_a4_regimes`.

Lexikální témata:

- `analysis_a4_topics`;
- `analysis_a4_topic_evidence`;
- `analysis_a4_topic_periods`;
- `analysis_a4_topic_period_reconciliation`.

Integrita:

- `analysis_a4_reconciliation` — pokud je publikované, A6 failuje closed pro conversation s `reconciliation_ok=0` nebo bez odpovídajícího reconciliation řádku.

Denní A4 řady jsou používány pro activity, initiations a denní median response latency. Souhrnný response median se z `analysis_a4_responses` počítá pouze tam, kde jej publikovaný kontrakt dovoluje bez matematického zkreslení.

Každý nález musí zachovat unikátní `source_message_ids_json`. Malformed nebo duplicitní evidence JSON je chyba a nesmí se převést na prázdný seznam.

Topic candidate musí mít exact message evidence shodnou s `analysis_a4_topic_evidence`. Sparse topic periods se zobrazují jako sparse projekce; chybějící období se neinterpretují jako nulová aktivita.

## Kontrakt A5

A6 exportuje pouze explicitně vybranou evidence a omezený kontext ve stejném `conversation_id`. Každá položka nese minimálně `membership_id`, `message_id`, timestamp, timestamp quality/precision, odesílatele, text a boolean `selected`.

Aktuální A5 `analysis_packet` schema v1 vyžaduje timezone-aware timestamp každé předané zprávy a unique `message_id` v packetu. A6 proto neodhaduje čas zpráv s `timestamp_quality=unknown`: pokud by taková zpráva byla selected nebo vstoupila do context radius, packet se nevytvoří a UI zobrazí důvod.

Po integraci A5 může A6 stejný packet explicitně předat lokálnímu A5 `A6PacketMessageSource` a `request_from_a6_packet`. Výchozí provider v UI je lokální Ollama; modelové volání vznikne pouze po explicitním kliknutí uživatele.

A5 výsledek se nezobrazuje pouze jako volný text. A6 umí resolveovat evidence pro assertion-bearing části současného kontraktu:

- summary + summary evidence;
- observations;
- interpretations;
- patterns;
- turning points + parallel evidence;
- participant P1/P2 assertions + evidence;
- shared dynamic + evidence;
- deterministic metric evidence.

Pokud assertion nemá message ani metric evidence tam, kde je evidence očekávána, UI ji označí jako porušenou traceability chain.

## Definition of Done pro A6

A6 lze považovat za hotové až po integračním běhu nad skutečným golden datasetem, kde projde celý řetězec:

`A1 import/reconciliation → A2 memberships/provenance → A3 processing → A4 reconciliation/metrics/finding/topic → A6 evidence drill-down → A5 result → A6 assertion evidence drill-down`

A7 musí nezávisle potvrdit, že v tomto řetězci nebyl ztracen žádný source record, canonical message membership, unknown-time canonical row, attachment occurrence, A4 topic evidence ani A5 evidence reference.
