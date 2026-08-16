PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS processing_run (
    id INTEGER PRIMARY KEY,
    processing_version TEXT NOT NULL,
    started_at_utc_us INTEGER NOT NULL,
    finished_at_utc_us INTEGER,
    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
    config_json TEXT NOT NULL DEFAULT '{}',
    input_message_count INTEGER NOT NULL DEFAULT 0,
    output_message_count INTEGER NOT NULL DEFAULT 0,
    input_membership_count INTEGER NOT NULL DEFAULT 0,
    output_membership_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS resolved_participant (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT,
    is_self INTEGER NOT NULL CHECK(is_self IN (0,1)),
    method TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id)
);

CREATE TABLE IF NOT EXISTS resolved_participant_member (
    resolved_participant_id INTEGER NOT NULL REFERENCES resolved_participant(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    method TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id),
    PRIMARY KEY(resolved_participant_id, participant_id)
);

CREATE TABLE IF NOT EXISTS participant_alias (
    id INTEGER PRIMARY KEY,
    resolved_participant_id INTEGER NOT NULL REFERENCES resolved_participant(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    participant_identity_id INTEGER NOT NULL,
    identity_type TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    original_value TEXT,
    method TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id),
    UNIQUE(participant_identity_id)
);

CREATE TABLE IF NOT EXISTS participant_resolution_candidate (
    id INTEGER PRIMARY KEY,
    participant_id_a INTEGER NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    participant_id_b INTEGER NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    method TEXT NOT NULL,
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id),
    CHECK(participant_id_a < participant_id_b),
    UNIQUE(participant_id_a, participant_id_b, reason, processing_run_id)
);

CREATE TABLE IF NOT EXISTS sender_run (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    sender_id INTEGER REFERENCES participant(id) ON DELETE SET NULL,
    resolved_participant_id INTEGER REFERENCES resolved_participant(id) ON DELETE SET NULL,
    first_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    last_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    first_membership_id INTEGER,
    last_membership_id INTEGER,
    start_at_utc_us INTEGER,
    end_at_utc_us INTEGER,
    message_count INTEGER NOT NULL,
    char_count INTEGER NOT NULL,
    method TEXT NOT NULL,
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id)
);

CREATE TABLE IF NOT EXISTS conversation_session (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    first_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    last_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    first_membership_id INTEGER,
    last_membership_id INTEGER,
    start_at_utc_us INTEGER,
    end_at_utc_us INTEGER,
    message_count INTEGER NOT NULL,
    gap_threshold_us INTEGER NOT NULL,
    method TEXT NOT NULL,
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id)
);

CREATE TABLE IF NOT EXISTS conversation_thread (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    session_id INTEGER REFERENCES conversation_session(id) ON DELETE CASCADE,
    method TEXT NOT NULL,
    confidence REAL NOT NULL,
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id)
);

CREATE TABLE IF NOT EXISTS conversation_thread_message (
    thread_id INTEGER NOT NULL REFERENCES conversation_thread(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    membership_id INTEGER,
    position INTEGER NOT NULL,
    PRIMARY KEY(thread_id, message_id, conversation_id)
);

CREATE TABLE IF NOT EXISTS processed_message (
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id),
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    membership_id INTEGER,
    sequence_number INTEGER NOT NULL,
    text_clean TEXT,
    sender_run_id INTEGER NOT NULL REFERENCES sender_run(id),
    session_id INTEGER NOT NULL REFERENCES conversation_session(id),
    thread_id INTEGER REFERENCES conversation_thread(id),
    resolved_sender_id INTEGER REFERENCES resolved_participant(id) ON DELETE SET NULL,
    char_count INTEGER NOT NULL,
    word_count INTEGER NOT NULL,
    line_count INTEGER NOT NULL,
    emoji_count INTEGER NOT NULL,
    question_mark_count INTEGER NOT NULL,
    exclamation_mark_count INTEGER NOT NULL,
    uppercase_ratio REAL NOT NULL,
    has_question INTEGER NOT NULL CHECK(has_question IN (0,1)),
    has_url INTEGER NOT NULL CHECK(has_url IN (0,1)),
    has_attachment INTEGER NOT NULL CHECK(has_attachment IN (0,1)),
    attachment_count INTEGER NOT NULL,
    image_count INTEGER NOT NULL,
    gif_count INTEGER NOT NULL,
    video_count INTEGER NOT NULL,
    audio_count INTEGER NOT NULL,
    document_count INTEGER NOT NULL,
    other_media_count INTEGER NOT NULL,
    missing_attachment_count INTEGER NOT NULL,
    seconds_since_previous_message REAL,
    seconds_since_previous_other_sender REAL,
    utc_year INTEGER,
    utc_month INTEGER,
    utc_day INTEGER,
    utc_weekday INTEGER,
    utc_hour INTEGER,
    local_year INTEGER,
    local_month INTEGER,
    local_day INTEGER,
    local_weekday INTEGER,
    local_hour INTEGER,
    PRIMARY KEY(processing_run_id, message_id, conversation_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_processed_message_membership
ON processed_message(processing_run_id, membership_id)
WHERE membership_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS a3_duplicate_candidate (
    id INTEGER PRIMARY KEY,
    message_id_a INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    message_id_b INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    classification TEXT NOT NULL,
    confidence REAL NOT NULL,
    method TEXT NOT NULL,
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id),
    CHECK(message_id_a < message_id_b),
    UNIQUE(message_id_a, message_id_b, classification, processing_run_id)
);

CREATE INDEX IF NOT EXISTS idx_resolved_participant_self ON resolved_participant(is_self, id);
CREATE INDEX IF NOT EXISTS idx_resolved_participant_member_participant ON resolved_participant_member(participant_id);
CREATE INDEX IF NOT EXISTS idx_participant_alias_resolved ON participant_alias(resolved_participant_id, identity_type);
CREATE INDEX IF NOT EXISTS idx_participant_resolution_candidate_pair ON participant_resolution_candidate(participant_id_a, participant_id_b);
CREATE INDEX IF NOT EXISTS idx_processed_message_conversation ON processed_message(conversation_id, sequence_number);
CREATE INDEX IF NOT EXISTS idx_processed_message_resolved_sender ON processed_message(resolved_sender_id);
CREATE INDEX IF NOT EXISTS idx_processed_message_session ON processed_message(session_id);
CREATE INDEX IF NOT EXISTS idx_processed_message_thread ON processed_message(thread_id);
CREATE INDEX IF NOT EXISTS idx_processed_message_utc_period ON processed_message(utc_year, utc_month, utc_day);
CREATE INDEX IF NOT EXISTS idx_processed_message_local_period ON processed_message(local_year, local_month, local_day);
CREATE INDEX IF NOT EXISTS idx_sender_run_conversation ON sender_run(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_sender_run_resolved_participant ON sender_run(resolved_participant_id, id);
CREATE INDEX IF NOT EXISTS idx_session_conversation ON conversation_session(conversation_id, id);

CREATE VIEW IF NOT EXISTS a3_analysis_participants AS
SELECT rp.id AS resolved_participant_id,
       rp.canonical_name,
       rp.is_self,
       rp.method,
       rp.confidence,
       COUNT(DISTINCT rpm.participant_id) AS member_count,
       COUNT(DISTINCT pa.participant_identity_id) AS alias_count
FROM resolved_participant rp
LEFT JOIN resolved_participant_member rpm ON rpm.resolved_participant_id = rp.id
LEFT JOIN participant_alias pa ON pa.resolved_participant_id = rp.id
GROUP BY rp.id;

CREATE VIEW IF NOT EXISTS a3_analysis_participant_aliases AS
SELECT pa.resolved_participant_id,
       pa.participant_id,
       pa.participant_identity_id,
       pa.identity_type,
       pa.normalized_value,
       pa.original_value,
       pa.method,
       pa.confidence
FROM participant_alias pa;

CREATE VIEW IF NOT EXISTS a3_analysis_messages AS
SELECT pm.processing_run_id,
       pm.membership_id,
       pm.message_id,
       pm.conversation_id,
       pm.sequence_number,
       pm.resolved_sender_id,
       pm.sender_run_id,
       pm.session_id,
       pm.thread_id,
       pm.text_clean,
       pm.seconds_since_previous_message,
       pm.seconds_since_previous_other_sender,
       pm.attachment_count,
       pm.missing_attachment_count
FROM processed_message pm;
