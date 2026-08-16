# A6 — Lokální rozhraní

A6 je tenká prezentační vrstva nad lokálními daty a výsledky analytického enginu.

## Primární workflow

`kontakt → období → konverzace → grafy → vybrané zprávy → analýza`

## MVP

- Streamlit, čistě lokálně;
- anonymizovaná vestavěná demo data pro spuštění bez A1/A2;
- SQLite pouze pro čtení (`mode=ro`);
- přímá kompatibilita s A2 analytickými views;
- bezpečný fallback přes automatickou detekci kompatibilní message tabulky/view;
- filtr kontaktu, období, odesílatele a full-textového výrazu;
- auditovatelný prohlížeč zpráv s `message_id`;
- aktivita, poměr odesílatelů a response latency;
- explicitní výběr zpráv;
- konfigurovatelné okolí před/po vybraných zprávách;
- export kontextového JSON balíčku pro A5 s příznakem `selected`;
- žádné automatické AI volání.

## Hranice odpovědnosti

A6 neimportuje iMessage, nededuplikuje zdrojová data, nemění SQLite a neurčuje psychologický význam komunikace. Výpočty, které patří do A4, jsou zde pouze jako lehký MVP fallback a později se nahradí autoritativními analytickými výstupy.

## Spuštění

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Výchozí režim používá demo data. V levém panelu lze přepnout na SQLite a zadat cestu k lokální databázi.

## Integrační kontrakt A2

A6 preferuje autoritativní A2 views:

- `analysis_messages` — zprávy, odesílatel a `sent_at_utc_us`;
- `analysis_conversations` — název / canonical key konverzace;
- `analysis_attachments` — budoucí napojení příloh.

`sent_at_utc_us` se převádí explicitně jako Unix epoch v mikrosekundách. Pokud A2 views nejsou přítomné, adapter použije read-only schema discovery a hledá ekvivalenty `message_id`, timestampu a textu.

## Kontrakt A5

A6 exportuje pouze explicitně vybrané zprávy a zvolený počet okolních zpráv ve stejném `conversation_id`. Každá položka nese `message_id`, čas, odesílatele, text a boolean `selected`, aby A5 mohl rozlišit evidenci od kontextu.
