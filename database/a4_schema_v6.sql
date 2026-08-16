PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS analytics_conversation_state_v6 (
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    source_fingerprint TEXT NOT NULL,
    analysis_signature TEXT NOT NULL,
    PRIMARY KEY(analytics_run_id, conversation_id)
);

CREATE TABLE IF NOT EXISTS analytics_topic_candidate (
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    topic_key TEXT NOT NULL,
    method TEXT NOT NULL,
    normalized_phrase TEXT NOT NULL,
    ngram_size INTEGER NOT NULL CHECK(ngram_size BETWEEN 1 AND 5),
    document_frequency INTEGER NOT NULL CHECK(document_frequency >= 1),
    document_frequency_ratio REAL NOT NULL CHECK(
        document_frequency_ratio >= 0 AND document_frequency_ratio <= 1
    ),
    occurrence_count INTEGER NOT NULL CHECK(occurrence_count >= document_frequency),
    participant_count INTEGER NOT NULL CHECK(participant_count >= 0),
    salience REAL NOT NULL CHECK(salience >= 0),
    first_period_date TEXT,
    last_period_date TEXT,
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(analytics_run_id, conversation_id, topic_key)
);

CREATE TABLE IF NOT EXISTS analytics_topic_evidence (
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    topic_key TEXT NOT NULL,
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    participant_id INTEGER REFERENCES participant(id) ON DELETE SET NULL,
    period_date TEXT,
    date_basis TEXT CHECK(date_basis IN ('local','utc') OR date_basis IS NULL),
    occurrence_count INTEGER NOT NULL CHECK(occurrence_count >= 1),
    PRIMARY KEY(analytics_run_id, conversation_id, topic_key, message_id),
    FOREIGN KEY(analytics_run_id, conversation_id, topic_key)
        REFERENCES analytics_topic_candidate(
            analytics_run_id, conversation_id, topic_key
        ) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_a4_v6_state_conversation
    ON analytics_conversation_state_v6(conversation_id, analytics_run_id);
CREATE INDEX IF NOT EXISTS idx_a4_topic_candidate_conversation
    ON analytics_topic_candidate(conversation_id, salience DESC, analytics_run_id);
CREATE INDEX IF NOT EXISTS idx_a4_topic_evidence_message
    ON analytics_topic_evidence(message_id, analytics_run_id);
CREATE INDEX IF NOT EXISTS idx_a4_topic_evidence_participant_date
    ON analytics_topic_evidence(
        conversation_id, participant_id, period_date, analytics_run_id
    );

DROP VIEW IF EXISTS analysis_a4_topic_period_reconciliation;
DROP VIEW IF EXISTS analysis_a4_topic_periods;
DROP VIEW IF EXISTS analysis_a4_topics;
DROP VIEW IF EXISTS analysis_a4_topic_evidence;

CREATE VIEW analysis_a4_topics AS
SELECT t.*
FROM analytics_topic_candidate AS t
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = t.conversation_id
 AND r.analytics_run_id = t.analytics_run_id;

CREATE VIEW analysis_a4_topic_evidence AS
SELECT e.*
FROM analytics_topic_evidence AS e
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = e.conversation_id
 AND r.analytics_run_id = e.analytics_run_id;

-- Sparse topic intensity view: one row exists only when the topic has dated
-- evidence in the period. A6 may render absent rows as zero inside a selected
-- timeline, but the stored evidence remains the authority. Local and UTC
-- evidence are kept separate by date_basis.
CREATE VIEW analysis_a4_topic_periods AS
WITH dated_evidence AS (
    SELECT e.analytics_run_id,
           e.conversation_id,
           e.topic_key,
           e.message_id,
           e.participant_id,
           e.period_date,
           e.date_basis,
           e.occurrence_count,
           t.normalized_phrase,
           t.method
    FROM analysis_a4_topic_evidence AS e
    JOIN analysis_a4_topics AS t
      ON t.analytics_run_id = e.analytics_run_id
     AND t.conversation_id = e.conversation_id
     AND t.topic_key = e.topic_key
    WHERE e.period_date IS NOT NULL
), expanded AS (
    SELECT d.*,
           'week' AS period_kind,
           date(
               d.period_date,
               '-' || ((CAST(strftime('%w', d.period_date) AS INTEGER) + 6) % 7) || ' days'
           ) AS period_start,
           date(
               d.period_date,
               '-' || ((CAST(strftime('%w', d.period_date) AS INTEGER) + 6) % 7) || ' days',
               '+6 days'
           ) AS period_end
    FROM dated_evidence AS d
    UNION ALL
    SELECT d.*,
           'month' AS period_kind,
           date(d.period_date, 'start of month') AS period_start,
           date(d.period_date, 'start of month', '+1 month', '-1 day') AS period_end
    FROM dated_evidence AS d
)
SELECT x.analytics_run_id,
       x.conversation_id,
       x.topic_key,
       x.normalized_phrase,
       x.method,
       x.participant_id,
       x.date_basis,
       x.period_kind,
       x.period_start,
       x.period_end,
       COUNT(DISTINCT x.message_id) AS topic_message_count,
       SUM(x.occurrence_count) AS occurrence_count,
       p.message_count AS participant_period_message_count,
       CASE
           WHEN p.message_count > 0 THEN
               ROUND(CAST(COUNT(DISTINCT x.message_id) AS REAL) / p.message_count, 6)
           ELSE NULL
       END AS topic_message_share
FROM expanded AS x
LEFT JOIN analysis_a4_periods AS p
  ON p.analytics_run_id = x.analytics_run_id
 AND p.conversation_id = x.conversation_id
 AND p.participant_id = x.participant_id
 AND p.date_basis = x.date_basis
 AND p.period_kind = x.period_kind
 AND p.period_start = x.period_start
GROUP BY x.analytics_run_id,
         x.conversation_id,
         x.topic_key,
         x.normalized_phrase,
         x.method,
         x.participant_id,
         x.date_basis,
         x.period_kind,
         x.period_start,
         x.period_end,
         p.message_count;

-- Explicit accounting for evidence that cannot participate in a time/participant
-- projection. Nothing is dropped: all rows remain queryable in
-- analysis_a4_topic_evidence.
CREATE VIEW analysis_a4_topic_period_reconciliation AS
SELECT e.analytics_run_id,
       e.conversation_id,
       COUNT(*) AS evidence_row_count,
       COUNT(DISTINCT e.topic_key) AS topic_count,
       COUNT(DISTINCT e.message_id) AS evidence_message_count,
       SUM(CASE WHEN e.period_date IS NOT NULL THEN 1 ELSE 0 END)
           AS dated_evidence_row_count,
       SUM(CASE WHEN e.period_date IS NULL THEN 1 ELSE 0 END)
           AS undated_evidence_row_count,
       SUM(CASE WHEN e.participant_id IS NULL THEN 1 ELSE 0 END)
           AS unknown_participant_evidence_row_count
FROM analysis_a4_topic_evidence AS e
GROUP BY e.analytics_run_id, e.conversation_id;
