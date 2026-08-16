# A6 → A7 QA handoff

Tento dokument definuje release-blocking validační body pro rozhraní A6. A7 nemá důvěřovat tomu, že zobrazení v UI je správné pouze proto, že A6 resolver nic nehlásí.

## 1. A2 canonical message → source provenance

Pro každý canonical `message_id`, který A6 zobrazí jako evidence:

1. `message_id` musí existovat v `analysis_messages`;
2. pokud zdroj pochází z A1/A2 pipeline, musí existovat alespoň jeden odpovídající záznam v `analysis_message_sources`;
3. `source_record_key` musí být totožný s klíčem zachovaným z A1 staging contractu;
4. `source_message_id`, `source_conversation_id`, `source_row_id`, `raw_timestamp` a `source_hash` se nesmí v A6 měnit;
5. více source rows pro jeden canonical message je povoleno a A6 je nesmí sloučit do falešného jediného zdroje.

## 2. Canonical message → attachment metadata

Pokud A6 zobrazí přílohu u evidence message, A7 musí ověřit:

1. dvojice `message_id` / `attachment_id` existuje v `analysis_attachments`;
2. A6 zachovává `position`, `sha256`, `mime_type`, `size_bytes`, `filename`, `storage_path` a `availability` beze změny;
3. chybějící/corrupt/external attachment se nesmí z UI ztratit pouze proto, že soubor není fyzicky dostupný;
4. A6 nesmí tvrdit attachment source provenance, dokud A2 nepublikuje stabilní attachment-source view;
5. po publikaci takového view musí A7 rozšířit gate až k původnímu attachment source recordu.

## 3. A4 finding → evidence

Pro každý řádek, který A6 načte z:

- `analysis_a4_events`,
- `analysis_a4_changes`,
- `analysis_a4_regimes`,

musí A7 ověřit:

1. `source_message_ids_json` je validní JSON pole;
2. každý uvedený `message_id` existuje v A2 canonical datech;
3. každý uvedený `message_id` patří ke stejnému `conversation_id` jako nález;
4. A6 drill-down zobrazí přesně množinu evidence IDs z A4, bez tichého přidání nebo odebrání;
5. chybějící evidence ID je viditelná chyba, nikoli prázdná evidence.

## 4. A6 selection → A5 packet

Pro každý A6 `analysis_packet` schema v1:

1. `selected_message_ids` musí být neprázdná množina explicitně vybraných IDs;
2. každé selected ID musí být přítomné v `messages` a mít `selected=true`;
3. všechny selected zprávy musí patřit do jednoho `conversation_id`;
4. okolní context může obsahovat pouze zprávy stejné konverzace;
5. pořadí `messages` musí odpovídat canonical chronologii;
6. context radius nesmí měnit selected evidence IDs;
7. textový/sender filtr UI nesmí odstranit okolní zprávy z contextu, pokud patří do zvoleného context radius.

## 5. A5 result → A6 evidence drill-down

Pro každý přijatý A5 výsledek musí A7 nezávisle ověřit evidence references v:

- `observations[].evidence.message_ids`,
- `interpretations[].evidence_message_ids`,
- `patterns[].evidence_message_ids`.

Každý odkaz musí:

1. existovat v contextu skutečně předaném A5;
2. existovat v aktuální A2 canonical databázi;
3. resolveovat na stejný text/timestamp/sender jako při sestavení A5 contextu;
4. být v A6 dohledatelný až na A2 source provenance;
5. při chybě být zobrazen jako porušená evidence chain.

## 6. Navigační invariant

A6 musí držet aktivní výběr v jedné konkrétní konverzaci. Změna `conversation_id` musí vyčistit:

- ruční selection,
- selection z A4 finding,
- poslední A5 execution/result.

Tím se zabrání tomu, aby UI zobrazovalo nebo analyzovalo evidence z jiné konverzace.

## 7. Golden end-to-end gate

Před označením A6/A7 za hotové musí existovat golden dataset, který projde:

`A1 → A2 → A3 → A4 → A6 finding drill-down → A5 → A6 result drill-down`

A7 musí pro tento běh potvrdit:

- reconciliation vstupních source records;
- canonical integrity;
- canonical attachment-link integrity;
- A4 evidence integrity;
- A6 packet integrity;
- A5 evidence integrity;
- source provenance až k původnímu A1 záznamu.

Výsledek musí být reprodukovatelný bez zápisu A6 do zdrojových nebo odvozených analytických tabulek.
