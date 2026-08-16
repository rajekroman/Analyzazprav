# A4 → A7 QA / validační kontrakt

A4 je deterministická analytická vrstva nad autoritativním A2/A3 kontraktem. A7 nesmí validovat pouze to, že výpočet doběhl; musí nezávisle ověřit účetní úplnost, metriku, provenance a reprodukovatelnost.

## Autoritativní vstup A4

A4 čte A2 message/membership provenance a A3 derived data z posledního completed `processing_run`.

Participant attribution používá:

```text
A3 resolved_sender_id → fallback A2 sender_id
```

A2 `message_id` a `membership_id` zůstávají provenance identitou a participant resolution je nesmí přepsat.

## Release-blocking invarianty

### 1. Membership accounting

Pro každý řádek `analysis_a4_reconciliation` musí platit:

- `uses_latest_processing_run = 1`;
- `a4_source_membership_count = a3_processed_membership_count`;
- `membership_count_delta = 0`;
- `sender_accounted_membership_count = a4_source_membership_count`;
- `reconciliation_ok = 1`.

Pokud ne, A4 výstup se nesmí předat A5/A6 jako aktuální analýza.

### 2. A3 processing-run provenance

Každý A4 `analytics_run` je vázaný na konkrétní `processing_run_id`. Samotné A3 `session_id` není globální identifikátor.

Default incremental režim smí vrátit `up_to_date` pouze tehdy, když se nezměnil:

- A4 source fingerprint;
- A4 analysis signature/config/version;
- **přesný latest A3 `processing_run_id`**.

Nový A3 run invaliduje A4 i tehdy, když jsou jeho logické message hodnoty identické. Důvodem je provenance namespace session/run IDs.

### 3. Session provenance

Musí být nulové:

- `invalid_response_session_count`;
- `invalid_silence_session_count`;
- `invalid_event_session_count`.

Každá A4 session reference se interpretuje pouze spolu s `analytics_run.processing_run_id`.

### 4. Response latency

Autoritativní definice response sample:

- dva sousední A4 turns;
- stejná A3 session;
- oba participant IDs známé;
- participant se změnil.

Latency:

```text
response_turn.start_us - previous_turn.end_us
```

Vzorek s chybějícím timestampem nemá vymyšlenou latency. Záporná delta se neprezentuje jako platná latency.

A7 `az-qa analytics` znovu sestavuje turns a response samples z A2/A3 dat a porovnává jejich multiset s `analytics_response_latency`.

### 5. Initiation

Initiator je **doslovný první turn A3 session**.

Pokud je sender prvního turnu neznámý, initiation zůstává neznámá. Nesmí být převedena na prvního pozdějšího známého účastníka.

Stejná definice musí platit v:

- participant summary;
- daily metrics;
- weekly metrics;
- monthly metrics.

### 6. Participant identity

A4 účtuje metriky podle A3 resolved participant identity. Automatické A3 sjednocení je konzervativní: explicitní A2 `is_self` aliases lze sjednotit; stejné display name bez explicitního důkazu je pouze kandidát.

A7 používá tutéž auditovanou A3 mapping tabulku, ale metriky počítá nezávisle na A4 engine.

### 7. Daily metrics a change points

Denní řady jsou gap-free: nulová aktivita je skutečná analytická hodnota a nesmí se zahodit.

Change-point kandidát používá pouze předchozí osobní baseline; výchozí konfigurace A4 je rolling window 28 dní, minimálně 7 baseline hodnot a robustní z-threshold 2.5, pokud uložený `AnalyticsConfig` neurčí jinak.

A7 znovu počítá daily metrics a change points z uloženého transparentního configu. Uložený A4 change point musí mít stejnou hodnotu, baseline, score, direction a source-message evidence.

Change point je statistický kandidát, ne psychologický fakt.

### 8. Source immutability

A4 nesmí měnit A1–A3 authoritative/derived vstupní tabulky. Mění pouze A4 analytics tabulky/views.

Release fixture má porovnat source/canonical fingerprinty a SQLite integritu před/po A4 tam, kde je to relevantní.

### 9. Independent A7 oracle

Release-blocking příkaz:

```bash
az-qa analytics --database path/to/messages.sqlite
```

Oracle **neimportuje A4 engine**. Z A2/A3 dat nezávisle rekonstruuje a kontroluje minimálně:

- conversation coverage;
- resolved participant set;
- source/known/unknown message counts;
- turns a sessions;
- initiations;
- response samples a latency distribuce;
- participant activity/word/question/style counts;
- reciprocity a engagement inputs;
- gap-free daily metrics;
- change-point candidates a evidence IDs;
- exact latest A3 processing-run binding;
- SQLite integrity a foreign keys.

Negativní fixture musí prokázat, že ruční změna persisted response latency způsobí `FAIL` (`A4_RESPONSE_SAMPLE_MISMATCH`). Další fixture musí detekovat `A4_STALE_A3_PROVENANCE` a po korektním incremental A4 rerunu znovu projít.

### 10. Topic evidence reconciliation

`analysis_a4_topic_period_reconciliation` musí vysvětlit všechny topic evidence rows:

```text
evidence_row_count = dated_evidence_row_count + undated_evidence_row_count
```

Unknown participant evidence je explicitní nejistota a nesmí se tiše připsat jiné osobě. Každý topic evidence row musí být dohledatelný na canonical `message(id)`.

Topic candidate je lexical evidence, ne prokázaná sémantická nebo psychologická interpretace.

### 11. Foreign keys a SQLite integrity

Release gate obsahuje:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

Požadovaný výsledek:

- `integrity_check = ok`;
- `foreign_key_check` bez řádků.

## Regression / promotion gate

A4 promotion vyžaduje:

```bash
python -m pip install -e . pytest
az-import --help
az-normalize --help
az-process --help
az-analyze --help
az-qa --help
python -m pytest -q
```

Na exact A4 headu musí být zelené workflow A1, A2, A3, A4 a A7.

## Handoff do A5/A6

A5 a A6 smějí používat pouze A4 konverzace, pro které A7 analytics oracle akceptuje aktuální report.

AI interpretace musí odkazovat na konkrétní A4 metric/evidence IDs a/nebo canonical messages. A4 pattern, regime, topic candidate ani change point se nesmí prezentovat jako jistý psychologický fakt.
