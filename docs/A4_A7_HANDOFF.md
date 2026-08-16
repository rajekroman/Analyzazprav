# A4 → A7 QA / validační kontrakt

A4 je deterministická analytická vrstva nad autoritativním A2/A3 kontraktem. A7 nesmí validovat pouze to, že výpočet doběhl; musí ověřit účetní úplnost, přesnou provenance a reprodukovatelnost.

## Release-blocking invarianty

### 1. Membership accounting

Pro každý řádek `analysis_a4_reconciliation` musí platit:

- `uses_latest_processing_run = 1`,
- `a4_source_membership_count = a3_processed_membership_count`,
- `membership_count_delta = 0`,
- `sender_accounted_membership_count = a4_source_membership_count`,
- `reconciliation_ok = 1`.

Pokud ne, A4 výstup se nesmí předat A5/A6 jako aktuální analýza.

## 2. Session provenance

Musí být nulové:

- `invalid_response_session_count`,
- `invalid_silence_session_count`,
- `invalid_event_session_count`.

Každý A4 session reference je interpretován pouze spolu s `analytics_run.processing_run_id`. Samotný `session_id` není globální identifikátor.

## 3. Source immutability

A4 nesmí měnit tabulky A1-A3. Při A4 rerunu se mohou měnit pouze A4 derived tabulky a views.

A7 má před/po A4 běhu porovnat alespoň:

- počty `message`, `message_conversation`, `message_source`,
- počty `processing_run`, `processed_message`, `conversation_session`,
- kontrolní source/canonical fingerprinty, pokud jsou v daném QA scénáři k dispozici.

## 4. Deterministický rerun

Při stejných A2/A3 datech, stejné A4 verzi a stejném `AnalyticsConfig` musí být analytický obsah stejný bez ohledu na ID nového `analytics_run` nebo jeho wall-clock timestamp.

## 5. Incremental invalidation

Default incremental režim smí vrátit `up_to_date` pouze tehdy, když se nezměnil:

- `source_fingerprint`, ani
- `analysis_signature`.

Změna membership, A3 processed feature, A4 verze/metody nebo konfigurace musí dotčenou konverzaci invalidovat a přepočítat celou.

## 6. Topic evidence reconciliation

`analysis_a4_topic_period_reconciliation` musí vysvětlit všechny topic evidence rows:

`evidence_row_count = dated_evidence_row_count + undated_evidence_row_count`

`unknown_participant_evidence_row_count` není chyba sama o sobě; je explicitní nejistota a nesmí se tiše připsat jiné osobě.

Každý řádek `analysis_a4_topic_evidence` musí být dohledatelný na canonical `message(id)`. Topic candidate je lexical evidence, ne prokázaná sémantická nebo psychologická interpretace.

## 7. Foreign keys a SQLite integrity

Release gate musí obsahovat:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

Požadovaný výsledek:

- `integrity_check = ok`,
- `foreign_key_check` bez řádků.

## 8. Regression suite

Minimální promotion gate A4 je full repository test suite:

```bash
python -m pip install -e . pytest
python -m pytest -q
```

A4 PR navíc musí mít zelené samostatné A1, A2, A3 i A4 GitHub Actions workflow.

## 9. Handoff do A5/A6

A5 a A6 smějí používat pouze A4 konverzace, pro které A7 akceptuje reconciliation. AI interpretace musí odkazovat na konkrétní A4 metriky a/nebo canonical messages; A4 pattern ani topic candidate se nesmí prezentovat jako jistý psychologický fakt.
