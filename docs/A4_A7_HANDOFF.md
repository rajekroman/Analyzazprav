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

A4 v8 zahrnuje do `analysis_signature` i metodu `topic_marker_cooccurrence_v1`; přechod ze starší analytické verze tedy nesmí recyklovat starý incremental výsledek.

## 6. Topic evidence reconciliation

`analysis_a4_topic_period_reconciliation` musí vysvětlit všechny topic evidence rows:

`evidence_row_count = dated_evidence_row_count + undated_evidence_row_count`

`unknown_participant_evidence_row_count` není chyba sama o sobě; je explicitní nejistota a nesmí se tiše připsat jiné osobě.

Každý řádek `analysis_a4_topic_evidence` musí být dohledatelný na canonical `message(id)`. Topic candidate je lexical evidence, ne prokázaná sémantická nebo psychologická interpretace.

## 7. Topic × marker evidence reconciliation (A4 v8)

`topic_marker_cooccurrence_v1` je pouze deterministická evidence, že **ve stejné zprávě**, která už je doloženou topic evidence, nastal alespoň jeden explicitně nakonfigurovaný marker.

Autoritativní vztah je:

`analytics_topic_evidence → analytics_topic_marker_evidence`

Každý marker řádek musí mít composite FK na přesný parent:

`(analytics_run_id, conversation_id, topic_key, message_id)`.

Platí:

- `affection_hit_count >= 0`,
- `negative_hit_count >= 0`,
- alespoň jeden z nich je `> 0`,
- marker evidence je sparse podmnožina topic evidence,
- neutrální topic evidence se nesmí odstraňovat; zůstává v `analysis_a4_topic_evidence`,
- `analysis_a4_topic_marker_reconciliation.reconciliation_ok = 1`.

A7 musí ověřit, že počet marker evidence řádků nikdy nepřekročí počet topic evidence řádků v témže latest A4 runu/konverzaci.

Read kontrakty:

- `analysis_a4_topic_marker_evidence` — konkrétní message-level evidence,
- `analysis_a4_topic_marker_summary` — agregace za topic,
- `analysis_a4_topic_marker_periods` — week/month × participant × date basis,
- `analysis_a4_topic_marker_reconciliation` — accounting marker evidence vůči topic evidence.

Marker hit **není sentiment, emoce, motivace ani psychologický stav**. Například `negative_hit_count > 0` znamená pouze výskyt explicitního markeru podle `AnalyticsConfig.negative_markers`. A4 ani A7 z toho nesmějí odvodit, že „téma je negativní“ nebo že účastník něco určitě cítil.

## 8. Foreign keys a SQLite integrity

Release gate musí obsahovat:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

Požadovaný výsledek:

- `integrity_check = ok`,
- `foreign_key_check` bez řádků.

## 9. Regression suite

Minimální promotion gate A4 je full repository test suite:

```bash
python -m pip install -e . pytest
python -m pytest -q
```

A4 PR navíc musí mít zelené samostatné A1, A2, A3, A4 a A7 GitHub Actions workflow, pokud jsou na aktuálním `main` aktivní.

## 10. Handoff do A5/A6

A5 a A6 smějí používat pouze A4 konverzace, pro které A7 akceptuje reconciliation. AI interpretace musí odkazovat na konkrétní A4 metriky a/nebo canonical messages; A4 pattern, topic candidate ani topic-marker co-occurrence se nesmí prezentovat jako jistý psychologický fakt.
