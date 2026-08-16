PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS analytics_topic_marker_evidence (
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    topic_key TEXT NOT NULL,
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    affection_hit_count INTEGER NOT NULL CHECK(affection_hit_count >= 0),
    negative_hit_count INTEGER NOT NULL CHECK(negative_hit_count >= 0),
    PRIMARY KEY(analytics_run_id, conversation_id, topic_key, message_id),
    CHECK(affection_hit_count > 0 OR negative_hit_count > 0),
    FOREIGN KEY(analytics_run_id, conversation_id, topic_key, message_id)
        REFERENCES analytics_topic_evidence(
            analytics_run_id, conversation_id, topic_key, message_id
        ) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_a4_topic_marker_message
    ON analytics_topic_marker_evidence(message_id, analytics_run_id);
CREATE INDEX IF NOT EXISTS idx_a4_topic_marker_topic
    ON analytics_topic_marker_evidence(conversation_id, topic_key, analytics_run_id);

DROP VIEW IF EXISTS analysis_a4_topic_marker_periods;
DROP VIEW IF EXISTS analysis_a4_topic_marker_summary;
DROP VIEW IF EXISTS analysis_a4_topic_marker_evidence;
DROP VIEW IF EXISTS analysis_a4_topic_marker_reconciliation;

CREATE VIEW analysis_a4_topic_marker_evidence AS
SELECT m.analytics_run_id,
       m.conversation_id,
       m.topic_key,
       m.message_id,
       e.participant_id,
       e.period_date,
       e.date_basis,
       e.occurrence_count AS topic_occurrence_count,
       m.affection_hit_count,
       m.negative_hit_count
FROM analytics_topic_marker_evidence AS m
JOIN analysis_a4_topic_evidence AS e
  ON e.analytics_run_id = m.analytics_run_id
 AND e.conversation_id = m.conversation_id
 AND e.topic_key = m.topic_key
 AND e.message_id = m.message_id;

CREATE VIEW analysis_a4_topic_marker_summary AS
SELECT m.analytics_run_id,
       m.conversation_id,
       m.topic_key,
       t.normalized_phrase,
       t.document_frequency AS topic_message_count,
       COUNT(*) AS marker_message_count,
       SUM(CASE WHEN m.affection_hit_count > 0 THEN 1 ELSE 0 END)
           AS affection_message_count,
       SUM(CASE WHEN m.negative_hit_count > 0 THEN 1 ELSE 0 END)
           AS negative_message_count,
       SUM(m.affection_hit_count) AS affection_hit_count,
       SUM(m.negative_hit_count) AS negative_hit_count,
       ROUND(CAST(COUNT(*) AS REAL) / t.document_frequency, 6)
           AS marker_message_share
FROM analysis_a4_topic_marker_evidence AS m
JOIN analysis_a4_topics AS t
  ON t.analytics_run_id = m.analytics_run_id
 AND t.conversation_id = m.conversation_id
 AND t.topic_key = m.topic_key
GROUP BY m.analytics_run_id,
         m.conversation_id,
         m.topic_key,
         t.normalized_phrase,
         t.document_frequency;

CREATE VIEW analysis_a4_topic_marker_periods AS
WITH expanded AS (
    SELECT m.*,
           'week' AS period_kind,
           date(
               m.period_date,
               '-' || ((CAST(strftime('%w', m.period_date) AS INTEGER) + 6) % 7) || ' days'
           ) AS period_start,
           date(
               m.period_date,
               '-' || ((CAST(strftime('%w', m.period_date) AS INTEGER) + 6) % 7) || ' days',
               '+6 days'
           ) AS period_end
    FROM analysis_a4_topic_marker_evidence AS m
    WHERE m.period_date IS NOT NULL
    UNION ALL
    SELECT m.*,
           'month' AS period_kind,
           date(m.period_date, 'start of month') AS period_start,
           date(m.period_date, 'start of month', '+1 month', '-1 day') AS period_end
    FROM analysis_a4_topic_marker_evidence AS m
    WHERE m.period_date IS NOT NULL
)
SELECT x.analytics_run_id,
       x.conversation_id,
       x.topic_key,
       t.normalized_phrase,
       x.participant_id,
       x.date_basis,
       x.period_kind,
       x.period_start,
       x.period_end,
       COUNT(DISTINCT x.message_id) AS marker_message_count,
       SUM(CASE WHEN x.affection_hit_count > 0 THEN 1 ELSE 0 END)
           AS affection_message_count,
       SUM(CASE WHEN x.negative_hit_count > 0 THEN 1 ELSE 0 END)
           AS negative_message_count,
       SUM(x.affection_hit_count) AS affection_hit_count,
       SUM(x.negative_hit_count) AS negative_hit_count,
       p.topic_message_count,
       CASE
           WHEN p.topic_message_count > 0 THEN
               ROUND(CAST(COUNT(DISTINCT x.message_id) AS REAL) / p.topic_message_count, 6)
           ELSE NULL
       END AS marker_message_share
FROM expanded AS x
JOIN analysis_a4_topics AS t
  ON t.analytics_run_id = x.analytics_run_id
 AND t.conversation_id = x.conversation_id
 AND t.topic_key = x.topic_key
LEFT JOIN analysis_a4_topic_periods AS p
  ON p.analytics_run_id = x.analytics_run_id
 AND p.conversation_id = x.conversation_id
 AND p.topic_key = x.topic_key
 AND p.participant_id = x.participant_id
 AND p.date_basis = x.date_basis
 AND p.period_kind = x.period_kind
 AND p.period_start = x.period_start
GROUP BY x.analytics_run_id,
         x.conversation_id,
         x.topic_key,
         t.normalized_phrase,
         x.participant_id,
         x.date_basis,
         x.period_kind,
         x.period_start,
         x.period_end,
         p.topic_message_count;

CREATE VIEW analysis_a4_topic_marker_reconciliation AS
SELECT e.analytics_run_id,
       e.conversation_id,
       COUNT(*) AS topic_evidence_row_count,
       SUM(CASE WHEN m.message_id IS NOT NULL THEN 1 ELSE 0 END)
           AS marker_evidence_row_count,
       SUM(CASE WHEN m.affection_hit_count > 0 THEN 1 ELSE 0 END)
           AS affection_evidence_row_count,
       SUM(CASE WHEN m.negative_hit_count > 0 THEN 1 ELSE 0 END)
           AS negative_evidence_row_count,
       CASE
           WHEN SUM(CASE WHEN m.message_id IS NOT NULL THEN 1 ELSE 0 END) <= COUNT(*)
           THEN 1 ELSE 0
       END AS reconciliation_ok
FROM analysis_a4_topic_evidence AS e
LEFT JOIN analytics_topic_marker_evidence AS m
  ON m.analytics_run_id = e.analytics_run_id
 AND m.conversation_id = e.conversation_id
 AND m.topic_key = e.topic_key
 AND m.message_id = e.message_id
GROUP BY e.analytics_run_id, e.conversation_id;
