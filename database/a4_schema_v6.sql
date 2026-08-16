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
