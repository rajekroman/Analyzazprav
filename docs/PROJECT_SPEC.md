# Analýza zpráv — hlavní specifikace projektu

## 1. Účel

Cílem projektu je vytvořit spolehlivý, jednoduchý a auditovatelný systém pro import, třídění, analýzu a interpretaci dlouhodobé osobní komunikace, primárně iMessage.

Výsledný proces:

`zdrojová data → deterministické zpracování → statistická analýza → výběr relevantních úseků → AI interpretace → doložitelný závěr`

## 2. Zásady

- Data mají přednost před interpretací.
- Originální data se nikdy nemění.
- Žádná zpráva se nesmí tiše ztratit.
- Každý výsledek musí být dohledatelný ke zdroji.
- AI není primární analytický engine.
- Projekt je local-first.
- Jednodušší auditovatelné řešení má přednost před zbytečně složitým.

## 3. Datové vrstvy

### L0 — RAW
Originální data beze změny.

### L1 — NORMALIZED
Jednotný datový model.

### L2 — DERIVED
Programově vypočítané atributy a metriky.

### L3 — ANALYSIS
Významná období, změny a AI interpretace s evidencí.

## 4. Kanonický datový model

`conversation → participant → message → attachment → timestamp → metadata`

Každá zpráva má pokud možno stabilní interní ID, source ID, conversation ID, sender ID, UTC a lokální timestamp, timezone, text, message type, attachment references a provenance.

## 5. Moduly

- A0 — Hlavní koordinace
- A1 — Import dat
- A2 — Normalizace a databáze
- A3 — Zpracování a třídění
- A4 — Analytický engine
- A5 — AI analýza
- A6 — Rozhraní
- A7 — QA / validace

Každý modul musí mít jasné `INPUT → PROCESSING → OUTPUT`.

## 6. MVP

První použitelná verze musí zvládnout:

1. import iMessage dat,
2. normalizaci,
3. zachování provenance,
4. výběr kontaktu a období,
5. zobrazení skutečných zpráv,
6. základní metriky,
7. response latency,
8. iniciaci komunikace,
9. detekci významných období,
10. AI analýzu relevantního výběru,
11. odkazy z AI závěrů na evidence,
12. QA report a reconciliation.

## 7. Analytický standard

Výsledky rozlišují:

- fakt,
- metriku,
- vzorec,
- interpretaci,
- nejistotu.

AI závěr má obsahovat:

- pozorování,
- evidence,
- interpretaci,
- alternativní vysvětlení,
- jistotu.

## 8. QA

Po každém importu musí být možné vysvětlit osud všech vstupních záznamů.

Kontrolují se počty, timestamps, IDs, foreign keys, attachments, duplicates, chyby a analytické regrese.

## 9. Priorita rozhodování

1. správnost dat,
2. úplnost dat,
3. dohledatelnost,
4. spolehlivost,
5. jednoduchost,
6. testovatelnost,
7. rychlost,
8. analytická kvalita,
9. UX,
10. vizuální vzhled.

## 10. Hlavní zásada

**Nejdříve správná data. Potom správné metriky. Až potom AI interpretace.**
