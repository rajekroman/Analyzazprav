# A6 → A7 QA handoff

Tento dokument definuje release-blocking validační body pro rozhraní A6. A7 nemá důvěřovat tomu, že zobrazení v UI je správné pouze proto, že A6 resolver nic nehlásí.

Aktuální autoritativní baseline pro tento handoff je A4 v9 na `main`. Starší SQLite zdroje mohou být čteny pouze přes explicitně kompatibilní legacy cestu; A6 nesmí chybějící nové invarianty domýšlet.

## 1. A2 membership-scoped read model je lossless

A2 `analysis_messages` od schema v5 reprezentuje membership zprávy v konverzaci, nikoli pouze fyzickou canonical message. A6 proto musí zachovat `membership_id` jako identitu read-model řádku.

A7 musí ověřit:

1. každý vstupní `membership_id` z `analysis_messages` přežije A6 read model právě jednou;
2. stejný canonical `message_id` může legitimně existovat ve více `conversation_id` a A6 nesmí takové řádky deduplikovat pouze podle `message_id`;
3. duplicitní `membership_id` nebo duplicitní dvojice `(conversation_id, message_id)` v A6 vstupu musí failovat closed místo tichého výběru jednoho řádku;
4. `sent_at_utc_us = NULL` je validní canonical stav a taková zpráva nesmí být odstraněna;
5. zpráva s neznámým časem musí být v konverzaci viditelná a explicitně označená jako časově neznámá;
6. vypnutí zobrazení unknown-time zpráv je pouze explicitní uživatelský prezentační filtr, nikoli normalizační krok.

## 2. A2 canonical message → source provenance

Pro každý canonical `message_id`, který A6 zobrazí jako evidence:

1. `message_id` musí existovat v `analysis_messages`;
2. pokud zdroj pochází z A1/A2 pipeline, musí existovat alespoň jeden odpovídající záznam v `analysis_message_sources`;
3. `source_record_key` musí být totožný s klíčem zachovaným z A1 staging contractu;
4. `source_message_id`, `source_conversation_id`, `source_row_id`, `raw_timestamp` a `source_hash` se nesmí v A6 měnit;
5. více source rows pro jeden canonical message je povoleno a A6 je nesmí sloučit do falešného jediného zdroje.

## 3. Canonical attachment occurrence → source provenance

Pokud A6 zobrazí přílohu u evidence message, A7 musí ověřit:

1. `occurrence_id`, `message_id` a `attachment_id` odpovídají přesně řádku v `analysis_attachments`;
2. A6 zachovává `position`, `sha256`, `mime_type`, `size_bytes`, `filename`, `storage_path` a `availability` beze změny;
3. chybějící/corrupt/external attachment se nesmí z UI ztratit pouze proto, že soubor není fyzicky dostupný;
4. pokud je publikované `analysis_attachment_sources`, A6 musí resolveovat provenance přes `occurrence_id`, ne pouze přes deduplikovaný `attachment_id`;
5. A6 musí zachovat všechny source rows stejného occurrence a zobrazit minimálně `attachment_source_id`, `import_run_id`, `source_type`, `source_snapshot_key`, `source_sha256`, `parser_version`, `source_attachment_id`, `source_occurrence_key`, `original_filename` a `original_path`;
6. pokud view není dostupné ve starším kompatibilním SQLite zdroji, A6 nesmí provenance domýšlet dotazem do interních physical tabulek.

## 4. A4 reconciliation je autoritativní gate

Pokud databáze publikuje `analysis_a4_reconciliation`, A7 musí ověřit, že A6:

1. považuje A4 metriky, findings, lexikální témata a topic-marker evidence za autoritativní pouze při `reconciliation_ok=1` pro zvolenou konverzaci;
2. při současném A4 kontraktu navíc nezávisle ověřuje `uses_latest_processing_run=1`;
3. nezávisle ověřuje `a4_source_membership_count = a3_processed_membership_count = sender_accounted_membership_count` a `membership_count_delta=0`;
4. nezávisle ověřuje nulové `invalid_response_session_count`, `invalid_silence_session_count` a `invalid_event_session_count`;
5. při `reconciliation_ok=0` nebo při porušení kteréhokoli publikovaného accounting/session/latest-run invariantu failuje closed s viditelnou chybou;
6. při existujícím reconciliation view, ale chybějícím řádku pro konverzaci failuje closed;
7. při více latest reconciliation řádcích pro jeden `conversation_id` failuje closed místo výběru jednoho;
8. starší kompatibilní SQLite bez reconciliation view může číst pouze jako legacy kontrakt a nesmí si reconciliation výsledek domyslet.

## 5. A4 finding → evidence

Pro každý řádek, který A6 načte z:

- `analysis_a4_events`,
- `analysis_a4_changes`,
- `analysis_a4_regimes`,

musí A7 ověřit:

1. `source_message_ids_json` je validní JSON pole bez duplicit;
2. každý uvedený `message_id` existuje v A2 canonical datech;
3. každý uvedený `message_id` má membership ve stejném `conversation_id` jako nález;
4. A6 drill-down zobrazí přesně množinu evidence IDs z A4, bez tichého přidání nebo odebrání;
5. chybějící evidence ID je viditelná chyba, nikoli prázdná evidence.

## 6. A4 lexical topic → exact evidence

A4 `lexical_ngram_v1` je deterministická lexical evidence, nikoli latentní sémantická interpretace. A7 musí pro každý topic, který A6 zobrazí, ověřit:

1. topic pochází z `analysis_a4_topics` a nese původní `topic_key`, `method`, `normalized_phrase`, `ngram_size`, frequency/salience a časové metadata;
2. `source_message_ids_json` je validní pole bez duplicit;
3. množina candidate `source_message_ids_json` se přesně shoduje s normalizovanými message rows v `analysis_a4_topic_evidence` pro daný `topic_key`;
4. orphan evidence nebo orphan period `topic_key` způsobí chybu místo tichého odhození;
5. pokud je publikované `analysis_a4_topic_period_reconciliation`, A6 ověří minimálně `evidence_row_count`, `topic_count` a `evidence_message_count` proti skutečně načteným řádkům;
6. pokud reconciliation publikuje `dated_evidence_row_count` a `undated_evidence_row_count`, jejich součet musí přesně vysvětlit všechny topic evidence rows;
7. sparse `analysis_a4_topic_periods` se nezobrazuje jako dense nulová časová řada a chybějící období se neinterpretují;
8. A6 phrase/salience neoznačuje jako motivaci, psychologický význam ani AI sémantický topic;
9. topic drill-down vede přes exact message evidence až na A2 source provenance;
10. použití topic evidence pro A5 zachová přesně evidence messages daného topic kandidáta.

## 6A. A4 topic × marker co-occurrence → exact evidence

A4 `topic_marker_cooccurrence_v1` je pouze deterministická evidence, že ve stejné zprávě, která už je exact topic evidence, nastal alespoň jeden explicitně nakonfigurovaný lexical marker. Není to sentiment, emoce, motivace, diagnóza ani psychologický stav.

Pokud databáze publikuje topic-marker views, A7 musí ověřit, že A6:

1. čte message-level rows výhradně z `analysis_a4_topic_marker_evidence` a zachovává původní `topic_key`, `message_id`, `participant_id`, časová metadata, `topic_occurrence_count`, `affection_hit_count` a `negative_hit_count`;
2. každý `(topic_key, message_id)` marker row má přesný parent v `analysis_a4_topic_evidence` stejné konverzace;
3. duplicitní `(topic_key, message_id)` marker rows failují closed;
4. oba hit counts jsou nezáporné a alespoň jeden je `> 0`;
5. marker evidence zůstává sparse podmnožinou topic evidence; neutrální topic evidence se nesmí odstranit ani přepsat;
6. při neprázdné marker evidence vyžaduje `analysis_a4_topic_marker_reconciliation` a nezávisle ověří `topic_evidence_row_count`, `marker_evidence_row_count`, `affection_evidence_row_count`, `negative_evidence_row_count` a `reconciliation_ok=1`;
7. orphan `topic_key` v marker evidence/summary/period views failuje closed;
8. `affection_hit_count` ani `negative_hit_count` se v A6 neprezentují jako prokázaný sentiment nebo vztahový význam;
9. marker evidence drill-down musí vést přes stejný canonical `message_id` až na A2 source provenance;
10. chybějící marker views ve starším kompatibilním SQLite zdroji jsou explicitně nepřítomná capability, nikoli nulové marker skóre.

## 7. A6 selection → A5 packet

Pro každý A6 `analysis_packet` schema v1:

1. `selected_message_ids` musí být neprázdný explicitní výběr bez duplicit;
2. každé selected ID musí být přítomné v `messages` a mít `selected=true`;
3. všechny selected zprávy musí patřit do jednoho `conversation_id`;
4. okolní context může obsahovat pouze memberships stejné konverzace;
5. pořadí `messages` musí odpovídat známé canonical chronologii dané konverzace;
6. context radius nesmí měnit selected evidence IDs;
7. textový/sender filtr UI nesmí odstranit okolní zprávy z contextu, pokud patří do zvoleného context radius;
8. packet zachovává `membership_id` jako auditní metadata, ale A5 evidence identita zůstává stable canonical `message_id`;
9. zpráva bez známého timezone-aware timestampu se nesmí pro A5 časově domyslet. Pokud je selected nebo vstoupí do zvoleného context radius, A6 musí packet odmítnout s viditelnou chybou.

## 8. A5 result → A6 evidence drill-down

Pro každý přijatý A5 výsledek musí A7 nezávisle ověřit evidence references v:

- `summary_evidence` nebo evidence vložené přímo do strukturovaného summary claimu;
- `observations[].evidence`;
- `interpretations[].evidence` / `evidence_message_ids`;
- `patterns[].evidence` / `evidence_message_ids`;
- `turning_point_evidence[]` nebo evidence vložené v turning-point claimu;
- `participant_p1_evidence`;
- `participant_p2_evidence`;
- `shared_dynamic_evidence`.

Každý assertion-bearing claim musí:

1. zobrazit vlastní text odděleně od evidence;
2. zobrazit confidence, pokud ji kontrakt poskytuje;
3. zachovat message evidence i deterministickou metric evidence;
4. každý message odkaz resolveovat na context skutečně předaný A5;
5. každý message odkaz resolveovat na aktuální A2 canonical membership ve zvolené konverzaci;
6. zobrazit stejný text/timestamp/sender jako při sestavení A5 contextu;
7. být v A6 dohledatelný až na A2 source provenance;
8. při chybě nebo chybějící evidence být zobrazen jako porušená evidence chain, nikoli jako nepodložený běžný text.

## 9. Fallback analytika nesmí předstírat A4 metriku

Pokud A4 views nejsou dostupné, A6 smí zobrazit pouze jasně označené lokální prezentační fallbacky. Konkrétně sousední časový rozdíl při změně odesílatele je pouze adjacency gap a nesmí být prezentován jako prokázaná `response latency`.

A7 musí ověřit, že při dostupnosti A4 používá A6 autoritativní A4 views a fallback stejné metriky nepřepočítává paralelně.

## 10. Navigační invariant

A6 musí držet aktivní výběr v jedné konkrétní konverzaci. Změna `conversation_id` musí vyčistit:

- ruční selection;
- selection z A4 finding;
- selection z A4 lexikálního topicu;
- poslední A5 execution/result.

Tím se zabrání tomu, aby UI zobrazovalo nebo analyzovalo evidence z jiné konverzace.

## 11. Golden end-to-end gate

Před označením A6/A7 za hotové musí existovat golden dataset, který projde:

`A1 → A2 → A3 → A4 reconciliation/metrics/finding/topic/topic-marker → A6 evidence drill-down → A5 → A6 result drill-down`

A7 musí pro tento běh potvrdit:

- reconciliation vstupních source records a relations;
- A2 membership integrity včetně unknown-time řádků;
- canonical message provenance;
- canonical attachment occurrence + source provenance;
- A4 reconciliation integrity včetně latest-processing, membership accounting a session provenance;
- A4 finding evidence integrity;
- A4 lexical topic candidate/evidence/period reconciliation integrity;
- pokud je přítomná marker evidence, A4 topic-marker exact-parent/subset/reconciliation integrity;
- A6 packet integrity;
- A5 assertion-level evidence integrity;
- source provenance až k původnímu A1 záznamu.

Výsledek musí být reprodukovatelný bez zápisu A6 do zdrojových nebo odvozených analytických tabulek.
