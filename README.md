# Analyzazprav

Local-first pipeline pro auditovatelný import, normalizaci, deterministickou analýzu a AI interpretaci osobní komunikace.

`RAW DATA → NORMALIZED DATA → DERIVED DATA → ANALYTICS → RELEVANT CONTEXT → AI ANALYSIS → UI → QA`

## Vývojové spuštění

```bash
python -m pip install -r requirements.txt
pytest -q
```

Lokální UI:

```bash
streamlit run app.py
```

## Reálný Apple Messages archiv

Pro kompletní read-only gate nad skutečným `chat.db` spusťte z kořene repozitáře:

```bash
python -m tools.real_archive_gate \
  --chat-db /cesta/k/chat.db \
  --workdir /cesta/k/novemu-prazdnemu-workdir \
  --target ILA
```

Resolver nikdy fuzzy nevybere conversation. Pokud textový target není přesná canonical/source identita, `real_archive_report.json` obsahuje lokální inventář a další běh se provede s `--conversation-id`.

Podrobný kontrakt: [`docs/A0_REAL_ARCHIVE_GATE.md`](docs/A0_REAL_ARCHIVE_GATE.md).

## Moduly

- A1 — import a source reconciliation
- A2 — canonical SQLite, provenance a integrity
- A3 — processing, sessions, threads a participant resolution
- A4 — deterministická analytika
- A5 — bounded AI context a evidence provenance
- A6 — lokální Streamlit UI
- A7 — nezávislá QA, vertical reconciliation a release gates

Zdrojová data se nemění. Deterministické metriky se nepočítají pomocí AI. AI dostává pouze relevantní bounded context a výsledky musí zůstat dohledatelné ke zprávám nebo metrikám.
