# A6 — Lokální rozhraní

A6 je tenká prezentační vrstva nad lokálními daty a výsledky analytického enginu.

## Primární workflow

`kontakt → období → konverzace → grafy → vybrané zprávy → analýza`

## MVP

- Streamlit, čistě lokálně;
- anonymizovaná vestavěná demo data pro spuštění bez A1/A2;
- SQLite pouze pro čtení (`mode=ro`);
- automatická detekce kompatibilní message tabulky/view;
- filtr kontaktu, období, odesílatele a full-textového výrazu;
- auditovatelný prohlížeč zpráv s `message_id`;
- aktivita, poměr odesílatelů a response latency;
- explicitní výběr zpráv;
- konfigurovatelné okolí před/po vybraných zprávách;
- export kontextového JSON balíčku pro A5 s příznakem `selected`;
- žádné automatické AI volání.

## Hranice odpovědnosti

A6 neimportuje iMessage, nededuplikuje zdrojová data, nemění SQLite a neurčuje psychologický význam komunikace. Výpočty, které patří do A4, jsou zde pouze jako lehký MVP fallback a později se nahradí autoritativními analytickými views.

## Spuštění

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Výchozí režim používá demo data. V levém panelu lze přepnout na SQLite a zadat cestu k lokální databázi.

## Integrační kontrakt A2

A6 nevyžaduje konkrétní název view. Adapter hledá read-only tabulku/view s minimálně ekvivalenty:

- `message_id` / `id` / `guid`;
- `timestamp` / `sent_at_utc` / `created_at_utc`;
- `text` / `body` / `raw_text`.

Volitelně mapuje conversation/contact/sender sloupce. Jakmile A2 stabilizuje názvy analytických views, adapter lze zjednodušit bez zásahu do UI.

## Kontrakt A5

A6 exportuje pouze explicitně vybrané zprávy a zvolený počet okolních zpráv ve stejném `conversation_id`. Každá položka nese `message_id`, čas, odesílatele, text a boolean `selected`, aby A5 mohl rozlišit evidenci od kontextu.
