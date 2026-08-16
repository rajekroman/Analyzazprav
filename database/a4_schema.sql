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
    latency_seconds REAL NOT NULL CHECK(latency_seconds >= 0)
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

CREATE VIEW IF NOT EXISTS analysis_a4_events AS
SELECT e.*
FROM analytics_event AS e
JOIN analysis_a4_latest_run AS r ON r.analytics_run_id = e.analytics_run_id;
