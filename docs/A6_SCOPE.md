# A6 — Lokální rozhraní

A6 je poslední interaktivní vrstva projektu Analýza zpráv. Neprovádí import ani nenahrazuje deterministickou analytiku; zpřístupňuje skutečná data A2, nálezy A4 a selektivní interpretaci A5 tak, aby každý významný závěr šel dohledat zpět ke konkrétním zprávám a jejich source provenance.

## Primární workflow

`kontakt → období → konverzace → časová osa / grafy → významné období → evidence → původní zpráva → AI analýza`

## MVP obrazovky

- přehled kontaktů;
- konverzace a message browser;
- časová osa aktivity;
- základní grafy;
- významná období / analytické nálezy A4;
- vybrané zprávy;
- AI analýza A5.

## Aktuální implementace

- Streamlit, čistě lokálně;
- anonymizovaná vestavěná demo data pro vývoj bez pipeline;
- SQLite pouze pro čtení (`mode=ro`);
- přímá kompatibilita s A2 analytickými views;
- bezpečný fallback přes automatickou detekci kompatibilní message tabulky/view;
- filtr kontaktu, období, odesílatele a full-textového výrazu;
- auditovatelný prohlížeč zpráv s canonical `message_id`;
- lazy A2 provenance resolver přes `analysis_message_sources`;
- aktivita, poměr odesílatelů a response latency jako dočasný MVP fallback;
- A4 latest-run adapter pro events, change points a nestabilní dyadické režimy;
- explicitní drill-down `A4 nález → source_message_ids → canonical message → A2 source record`;
- explicitní ruční výběr zpráv;
- konfigurovatelné okolí před/po vybraných zprávách;
- A6 `analysis_packet` schema v1 pro A5;
- volitelný explicitní lokální A5 trigger přes Ollama, aktivovaný pouze pokud je A5 modul integrován;
- evidence-backed zobrazení A5 observations / interpretations / patterns;
- žádné automatické AI volání a žádný cloud fallback.

## Hranice odpovědnosti

A6 neimportuje iMessage, nededuplikuje zdrojová data, nemění A2/A3/A4 SQLite vrstvy a nevytváří vlastní analytickou pravdu. Výpočty, které patří do A4, jsou v A6 pouze dočasný fallback a po integraci A4 autoritativních metrik se odstraní.

A6 nesmí skrýt porušenou evidence chain. Pokud A4 nebo A5 odkazuje na `message_id`, který v kanonických datech chybí, UI tuto skutečnost explicitně zobrazí jako chybu dohledatelnosti.

## Spuštění

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Vývojový režim může použít anonymizovaná demo data. Pro skutečnou analýzu se v levém panelu zvolí SQLite databáze vytvořená pipeline A1 → A2 → A3 → A4.

## Kontrakt A2

A6 preferuje autoritativní A2 views:

- `analysis_messages` — canonical zprávy, odesílatel a `sent_at_utc_us`;
- `analysis_conversations` — název / canonical key konverzace;
- `analysis_message_sources` — source provenance canonical zpráv;
- `analysis_attachments` — připravené pro další media slice.

`sent_at_utc_us` se převádí explicitně jako Unix epoch v mikrosekundách. Pokud A2 views nejsou přítomné, message adapter může použít read-only schema discovery; provenance a vyšší analytické vrstvy se v takovém zdroji nezfalšují.

## Kontrakt A4

A6 čte pouze publikované latest-run views:

- `analysis_a4_events`;
- `analysis_a4_changes`;
- `analysis_a4_regimes`.

Každý nález musí zachovat `source_message_ids_json`. Malformed evidence JSON je chyba a nesmí se převést na prázdný seznam.

## Kontrakt A5

A6 exportuje pouze explicitně vybranou evidence a omezený kontext ve stejném `conversation_id`. Každá položka nese `message_id`, timestamp, odesílatele, text a boolean `selected`.

Po integraci A5 může A6 stejný packet explicitně předat lokálnímu A5 `A6PacketMessageSource` a `request_from_a6_packet`. Výchozí provider v UI je lokální Ollama; modelové volání vznikne pouze po explicitním kliknutí uživatele.

A5 výsledek se zobrazuje odděleně jako pozorování, interpretace, vzorce, alternativní vysvětlení a nejistoty. Evidence references se znovu resolvují proti aktuálním kanonickým zprávám a A2 provenance.

## Definition of Done pro A6

A6 lze považovat za hotové až po integračním běhu nad skutečným golden datasetem, kde projde celý řetězec:

`A1 import → A2 canonical/provenance → A3 processing → A4 finding → A6 evidence drill-down → A5 result → A6 evidence drill-down`

A7 musí nezávisle potvrdit, že v tomto řetězci nebylo ztraceno ani podvrženo žádné source/message ID.
