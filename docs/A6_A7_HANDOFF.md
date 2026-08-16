# A6 → A7 QA handoff

Tento dokument definuje release-blocking validační body pro rozhraní A6. A7 nemá důvěřovat tomu, že zobrazení v UI je správné pouze proto, že A6 resolver nic nehlásí.

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

## 4. A4 finding → evidence

Pro každý řádek, který A6 načte z:

- `analysis_a4_events`,
- `analysis_a4_changes`,
- `analysis_a4_regimes`,

musí A7 ověřit:

1. `source_message_ids_json` je validní JSON pole;
2. každý uvedený `message_id` existuje v A2 canonical datech;
3. každý uvedený `message_id` má membership ve stejném `conversation_id` jako nález;
4. A6 drill-down zobrazí přesně množinu evidence IDs z A4, bez tichého přidání nebo odebrání;
5. chybějící evidence ID je viditelná chyba, nikoli prázdná evidence.

## 5. A6 selection → A5 packet

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

## 6. A5 result → A6 evidence drill-down

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

## 7. Fallback analytika nesmí předstírat A4 metriku

Pokud A4 views nejsou dostupné, A6 smí zobrazit pouze jasně označené lokální prezentační fallbacky. Konkrétně sousední časový rozdíl při změně odesílatele je pouze adjacency gap a nesmí být prezentován jako prokázaná `response latency`.

A7 musí ověřit, že při dostupnosti A4 používá A6 autoritativní A4 views a fallback stejné metriky nepřepočítává paralelně.

## 8. Navigační invariant

A6 musí držet aktivní výběr v jedné konkrétní konverzaci. Změna `conversation_id` musí vyčistit:

- ruční selection,
- selection z A4 finding,
- poslední A5 execution/result.

Tím se zabrání tomu, aby UI zobrazovalo nebo analyzovalo evidence z jiné konverzace.

## 9. Golden end-to-end gate

Před označením A6/A7 za hotové musí existovat golden dataset, který projde:

`A1 → A2 → A3 → A4 → A6 finding drill-down → A5 → A6 result drill-down`

A7 musí pro tento běh potvrdit:

- reconciliation vstupních source records a relations;
- A2 membership integrity včetně unknown-time řádků;
- canonical message provenance;
- canonical attachment occurrence + source provenance;
- A4 evidence integrity;
- A6 packet integrity;
- A5 assertion-level evidence integrity;
- source provenance až k původnímu A1 záznamu.

Výsledek musí být reprodukovatelný bez zápisu A6 do zdrojových nebo odvozených analytických tabulek.
