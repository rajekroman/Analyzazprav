PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS analytics_conversation_fingerprint (
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    source_fingerprint TEXT NOT NULL,
    PRIMARY KEY(analytics_run_id, conversation_id)
);

CREATE TABLE IF NOT EXISTS analytics_trend_summary (
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    period_kind TEXT NOT NULL CHECK(period_kind IN ('week','month')),
    metric TEXT NOT NULL,
    window_periods INTEGER NOT NULL CHECK(window_periods >= 2),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    first_value REAL NOT NULL,
    last_value REAL NOT NULL,
    slope_per_period REAL NOT NULL,
    normalized_slope REAL NOT NULL,
    percent_change REAL,
    direction TEXT NOT NULL CHECK(direction IN ('increasing','decreasing','stable')),
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(analytics_run_id, conversation_id, participant_id, period_kind, metric)
);

CREATE INDEX IF NOT EXISTS idx_a4_fingerprint_conversation
    ON analytics_conversation_fingerprint(conversation_id, analytics_run_id);
CREATE INDEX IF NOT EXISTS idx_a4_trend_conversation
    ON analytics_trend_summary(conversation_id, participant_id, period_kind, analytics_run_id);

DROP VIEW IF EXISTS analysis_a4_events;
DROP VIEW IF EXISTS analysis_a4_changes;
DROP VIEW IF EXISTS analysis_a4_trends;
DROP VIEW IF EXISTS analysis_a4_regimes;
DROP VIEW IF EXISTS analysis_a4_engagement_signals;
DROP VIEW IF EXISTS analysis_a4_periods;
DROP VIEW IF EXISTS analysis_a4_daily;
DROP VIEW IF EXISTS analysis_a4_silences;
DROP VIEW IF EXISTS analysis_a4_time_buckets;
DROP VIEW IF EXISTS analysis_a4_responses;
DROP VIEW IF EXISTS analysis_a4_participants;
DROP VIEW IF EXISTS analysis_a4_conversations;
DROP VIEW IF EXISTS analysis_a4_latest_conversation_run;

CREATE VIEW analysis_a4_latest_conversation_run AS
SELECT s.conversation_id, MAX(s.analytics_run_id) AS analytics_run_id
FROM analytics_conversation_summary AS s
JOIN analytics_run AS r ON r.id = s.analytics_run_id
WHERE r.status = 'completed'
GROUP BY s.conversation_id;

CREATE VIEW analysis_a4_conversations AS
SELECT s.*, f.source_fingerprint
FROM analytics_conversation_summary AS s
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = s.conversation_id
 AND r.analytics_run_id = s.analytics_run_id
LEFT JOIN analytics_conversation_fingerprint AS f
  ON f.conversation_id = s.conversation_id
 AND f.analytics_run_id = s.analytics_run_id;

CREATE VIEW analysis_a4_participants AS
SELECT s.*
FROM analytics_participant_summary AS s
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = s.conversation_id
 AND r.analytics_run_id = s.analytics_run_id;

CREATE VIEW analysis_a4_responses AS
SELECT s.*
FROM analytics_response_latency AS s
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = s.conversation_id
 AND r.analytics_run_id = s.analytics_run_id;

CREATE VIEW analysis_a4_time_buckets AS
SELECT s.*
FROM analytics_time_bucket AS s
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = s.conversation_id
 AND r.analytics_run_id = s.analytics_run_id;

CREATE VIEW analysis_a4_silences AS
SELECT s.*
FROM analytics_silence_event AS s
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = s.conversation_id
 AND r.analytics_run_id = s.analytics_run_id;

CREATE VIEW analysis_a4_daily AS
SELECT s.*
FROM analytics_daily_participant AS s
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = s.conversation_id
 AND r.analytics_run_id = s.analytics_run_id;

CREATE VIEW analysis_a4_periods AS
SELECT s.*
FROM analytics_period_participant AS s
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = s.conversation_id
 AND r.analytics_run_id = s.analytics_run_id;

CREATE VIEW analysis_a4_engagement_signals AS
SELECT s.*
FROM analytics_engagement_signal AS s
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = s.conversation_id
 AND r.analytics_run_id = s.analytics_run_id;

CREATE VIEW analysis_a4_regimes AS
SELECT s.*
FROM analytics_dyadic_regime AS s
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = s.conversation_id
 AND r.analytics_run_id = s.analytics_run_id;

CREATE VIEW analysis_a4_trends AS
SELECT s.*
FROM analytics_trend_summary AS s
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = s.conversation_id
 AND r.analytics_run_id = s.analytics_run_id;

CREATE VIEW analysis_a4_changes AS
SELECT s.*
FROM analytics_change_point AS s
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = s.conversation_id
 AND r.analytics_run_id = s.analytics_run_id;

CREATE VIEW analysis_a4_events AS
SELECT e.*
FROM analytics_event AS e
JOIN analysis_a4_latest_conversation_run AS r
  ON r.conversation_id = e.conversation_id
 AND r.analytics_run_id = e.analytics_run_id;
