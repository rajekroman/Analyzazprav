PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS analytics_run (
    id INTEGER PRIMARY KEY,
    analytics_version TEXT NOT NULL,
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id),
    started_at_utc_us INTEGER NOT NULL,
    finished_at_utc_us INTEGER,
    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
    config_json TEXT NOT NULL DEFAULT '{}',
    conversation_count INTEGER NOT NULL DEFAULT 0,
    input_message_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS analytics_conversation_summary (
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    source_message_count INTEGER NOT NULL,
    known_sender_message_count INTEGER NOT NULL,
    unknown_sender_message_count INTEGER NOT NULL,
    turn_count INTEGER NOT NULL,
    session_count INTEGER NOT NULL,
    message_reciprocity REAL,
    word_reciprocity REAL,
    turn_reciprocity REAL,
    initiation_reciprocity REAL,
    PRIMARY KEY(analytics_run_id, conversation_id)
);

CREATE TABLE IF NOT EXISTS analytics_participant_summary (
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    message_count INTEGER NOT NULL,
    word_count INTEGER NOT NULL,
    character_count INTEGER NOT NULL,
    active_days INTEGER NOT NULL,
    turn_count INTEGER NOT NULL,
    initiations INTEGER NOT NULL,
    initiation_share REAL NOT NULL,
    question_count INTEGER NOT NULL,
    exclamation_count INTEGER NOT NULL,
    affection_marker_count INTEGER NOT NULL,
    negative_marker_count INTEGER NOT NULL,
    median_response_latency_seconds REAL,
    median_response_effort_ratio REAL,
    engagement_score REAL NOT NULL,
    PRIMARY KEY(analytics_run_id, conversation_id, participant_id)
);

CREATE TABLE IF NOT EXISTS analytics_response_latency (
    id INTEGER PRIMARY KEY,
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    session_id INTEGER NOT NULL REFERENCES conversation_session(id) ON DELETE CASCADE,
    from_participant_id INTEGER NOT NULL REFERENCES participant(id),
    responder_id INTEGER NOT NULL REFERENCES participant(id),
    previous_turn_id INTEGER NOT NULL,
    response_turn_id INTEGER NOT NULL,
    latency_seconds REAL CHECK(latency_seconds IS NULL OR latency_seconds >= 0),
    response_effort_ratio REAL NOT NULL CHECK(response_effort_ratio >= 0)
);

CREATE TABLE IF NOT EXISTS analytics_daily_participant (
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    period_date TEXT NOT NULL,
    date_basis TEXT NOT NULL CHECK(date_basis IN ('local','utc')),
    message_count INTEGER NOT NULL,
    word_count INTEGER NOT NULL,
    turn_count INTEGER NOT NULL,
    initiations INTEGER NOT NULL,
    question_count INTEGER NOT NULL,
    affection_marker_count INTEGER NOT NULL,
    negative_marker_count INTEGER NOT NULL,
    median_response_latency_seconds REAL,
    median_response_effort_ratio REAL,
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(analytics_run_id, conversation_id, participant_id, period_date)
);

CREATE TABLE IF NOT EXISTS analytics_period_participant (
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    period_kind TEXT NOT NULL CHECK(period_kind IN ('week','month')),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    date_basis TEXT NOT NULL CHECK(date_basis IN ('local','utc')),
    message_count INTEGER NOT NULL,
    word_count INTEGER NOT NULL,
    turn_count INTEGER NOT NULL,
    initiations INTEGER NOT NULL,
    question_count INTEGER NOT NULL,
    affection_marker_count INTEGER NOT NULL,
    negative_marker_count INTEGER NOT NULL,
    median_response_latency_seconds REAL,
    median_response_effort_ratio REAL,
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(analytics_run_id, conversation_id, participant_id, period_kind, period_start)
);

CREATE TABLE IF NOT EXISTS analytics_engagement_signal (
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    score REAL NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('increase','decrease','stable')),
    component_scores_json TEXT NOT NULL DEFAULT '{}',
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(analytics_run_id, conversation_id, participant_id, period_start)
);

CREATE TABLE IF NOT EXISTS analytics_dyadic_regime (
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    participant_a_id INTEGER NOT NULL REFERENCES participant(id),
    participant_a_direction TEXT NOT NULL CHECK(participant_a_direction IN ('increase','decrease','stable')),
    participant_a_score REAL NOT NULL,
    participant_b_id INTEGER NOT NULL REFERENCES participant(id),
    participant_b_direction TEXT NOT NULL CHECK(participant_b_direction IN ('increase','decrease','stable')),
    participant_b_score REAL NOT NULL,
    regime_type TEXT NOT NULL CHECK(regime_type IN (
        'mutual_approach','mutual_withdrawal','opposing_directions',
        'one_sided_increase','one_sided_decrease','stable_or_mixed'
    )),
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(analytics_run_id, conversation_id, period_start)
);

CREATE TABLE IF NOT EXISTS analytics_change_point (
    id INTEGER PRIMARY KEY,
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    metric TEXT NOT NULL,
    period_date TEXT NOT NULL,
    value REAL NOT NULL,
    baseline_median REAL NOT NULL,
    robust_z_score REAL NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('increasing','decreasing')),
    source_message_ids_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS analytics_event (
    id INTEGER PRIMARY KEY,
    analytics_run_id INTEGER NOT NULL REFERENCES analytics_run(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    session_id INTEGER REFERENCES conversation_session(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    score REAL NOT NULL,
    start_at_utc_us INTEGER,
    end_at_utc_us INTEGER,
    factors_json TEXT NOT NULL DEFAULT '{}',
    source_message_ids_json TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_a4_participant_conversation
    ON analytics_participant_summary(conversation_id, participant_id, analytics_run_id);
CREATE INDEX IF NOT EXISTS idx_a4_latency_conversation
    ON analytics_response_latency(conversation_id, responder_id, analytics_run_id);
CREATE INDEX IF NOT EXISTS idx_a4_daily_conversation
    ON analytics_daily_participant(conversation_id, participant_id, period_date, analytics_run_id);
CREATE INDEX IF NOT EXISTS idx_a4_period_conversation
    ON analytics_period_participant(conversation_id, participant_id, period_kind, period_start, analytics_run_id);
CREATE INDEX IF NOT EXISTS idx_a4_signal_conversation
    ON analytics_engagement_signal(conversation_id, participant_id, period_start, analytics_run_id);
CREATE INDEX IF NOT EXISTS idx_a4_regime_conversation
    ON analytics_dyadic_regime(conversation_id, period_start, analytics_run_id);
CREATE INDEX IF NOT EXISTS idx_a4_change_conversation
    ON analytics_change_point(conversation_id, participant_id, period_date, analytics_run_id);
CREATE INDEX IF NOT EXISTS idx_a4_event_conversation
    ON analytics_event(conversation_id, event_type, analytics_run_id);

CREATE VIEW IF NOT EXISTS analysis_a4_latest_run AS
SELECT MAX(id) AS analytics_run_id
FROM analytics_run
WHERE status = 'completed';

CREATE VIEW IF NOT EXISTS analysis_a4_conversations AS
SELECT s.*
FROM analytics_conversation_summary AS s
JOIN analysis_a4_latest_run AS r ON r.analytics_run_id = s.analytics_run_id;

CREATE VIEW IF NOT EXISTS analysis_a4_participants AS
SELECT s.*
FROM analytics_participant_summary AS s
JOIN analysis_a4_latest_run AS r ON r.analytics_run_id = s.analytics_run_id;

CREATE VIEW IF NOT EXISTS analysis_a4_responses AS
SELECT s.*
FROM analytics_response_latency AS s
JOIN analysis_a4_latest_run AS r ON r.analytics_run_id = s.analytics_run_id;

CREATE VIEW IF NOT EXISTS analysis_a4_daily AS
SELECT s.*
FROM analytics_daily_participant AS s
JOIN analysis_a4_latest_run AS r ON r.analytics_run_id = s.analytics_run_id;

CREATE VIEW IF NOT EXISTS analysis_a4_periods AS
SELECT s.*
FROM analytics_period_participant AS s
JOIN analysis_a4_latest_run AS r ON r.analytics_run_id = s.analytics_run_id;

CREATE VIEW IF NOT EXISTS analysis_a4_engagement_signals AS
SELECT s.*
FROM analytics_engagement_signal AS s
JOIN analysis_a4_latest_run AS r ON r.analytics_run_id = s.analytics_run_id;

CREATE VIEW IF NOT EXISTS analysis_a4_regimes AS
SELECT s.*
FROM analytics_dyadic_regime AS s
JOIN analysis_a4_latest_run AS r ON r.analytics_run_id = s.analytics_run_id;

CREATE VIEW IF NOT EXISTS analysis_a4_changes AS
SELECT s.*
FROM analytics_change_point AS s
JOIN analysis_a4_latest_run AS r ON r.analytics_run_id = s.analytics_run_id;

CREATE VIEW IF NOT EXISTS analysis_a4_events AS
SELECT e.*
FROM analytics_event AS e
JOIN analysis_a4_latest_run AS r ON r.analytics_run_id = e.analytics_run_id;
