PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS processing_run (
    id INTEGER PRIMARY KEY,
    processing_version TEXT NOT NULL,
    started_at_utc_us INTEGER NOT NULL,
    finished_at_utc_us INTEGER,
    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
    config_json TEXT NOT NULL DEFAULT '{}',
    input_membership_count INTEGER NOT NULL DEFAULT 0,
    canonical_message_count INTEGER NOT NULL DEFAULT 0,
    output_membership_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sender_run (
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id) ON DELETE CASCADE,
    id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    sender_id INTEGER REFERENCES participant(id) ON DELETE SET NULL,
    first_membership_id INTEGER NOT NULL REFERENCES message_conversation(id) ON DELETE CASCADE,
    last_membership_id INTEGER NOT NULL REFERENCES message_conversation(id) ON DELETE CASCADE,
    first_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    last_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    start_at_utc_us INTEGER,
    end_at_utc_us INTEGER,
    message_count INTEGER NOT NULL,
    char_count INTEGER NOT NULL,
    method TEXT NOT NULL,
    PRIMARY KEY(processing_run_id, id)
);

CREATE TABLE IF NOT EXISTS conversation_session (
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id) ON DELETE CASCADE,
    id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    first_membership_id INTEGER NOT NULL REFERENCES message_conversation(id) ON DELETE CASCADE,
    last_membership_id INTEGER NOT NULL REFERENCES message_conversation(id) ON DELETE CASCADE,
    first_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    last_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    start_at_utc_us INTEGER,
    end_at_utc_us INTEGER,
    message_count INTEGER NOT NULL,
    gap_threshold_us INTEGER NOT NULL,
    method TEXT NOT NULL,
    PRIMARY KEY(processing_run_id, id)
);

CREATE TABLE IF NOT EXISTS conversation_thread (
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id) ON DELETE CASCADE,
    id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    session_id INTEGER,
    method TEXT NOT NULL,
    confidence REAL NOT NULL,
    PRIMARY KEY(processing_run_id, id),
    FOREIGN KEY(processing_run_id, session_id)
        REFERENCES conversation_session(processing_run_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS conversation_thread_message (
    processing_run_id INTEGER NOT NULL,
    thread_id INTEGER NOT NULL,
    membership_id INTEGER NOT NULL REFERENCES message_conversation(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY(processing_run_id, thread_id, membership_id),
    FOREIGN KEY(processing_run_id, thread_id)
        REFERENCES conversation_thread(processing_run_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS processed_message (
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id) ON DELETE CASCADE,
    membership_id INTEGER NOT NULL REFERENCES message_conversation(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    sequence_number INTEGER NOT NULL,
    text_clean TEXT,
    sender_run_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    thread_id INTEGER,
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
    PRIMARY KEY(processing_run_id, membership_id),
    UNIQUE(processing_run_id, conversation_id, sequence_number),
    FOREIGN KEY(processing_run_id, sender_run_id)
        REFERENCES sender_run(processing_run_id, id) ON DELETE CASCADE,
    FOREIGN KEY(processing_run_id, session_id)
        REFERENCES conversation_session(processing_run_id, id) ON DELETE CASCADE,
    FOREIGN KEY(processing_run_id, thread_id)
        REFERENCES conversation_thread(processing_run_id, id)
);

CREATE TABLE IF NOT EXISTS a3_duplicate_candidate (
    id INTEGER PRIMARY KEY,
    message_id_a INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    message_id_b INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    classification TEXT NOT NULL,
    confidence REAL NOT NULL,
    method TEXT NOT NULL,
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id) ON DELETE CASCADE,
    CHECK(message_id_a < message_id_b),
    UNIQUE(message_id_a, message_id_b, classification, processing_run_id)
);

CREATE INDEX IF NOT EXISTS idx_processed_message_membership
ON processed_message(membership_id, processing_run_id);
CREATE INDEX IF NOT EXISTS idx_processed_message_conversation
ON processed_message(processing_run_id, conversation_id, sequence_number);
CREATE INDEX IF NOT EXISTS idx_processed_message_session
ON processed_message(processing_run_id, session_id);
CREATE INDEX IF NOT EXISTS idx_processed_message_thread
ON processed_message(processing_run_id, thread_id);
CREATE INDEX IF NOT EXISTS idx_processed_message_utc_period
ON processed_message(processing_run_id, utc_year, utc_month, utc_day);
CREATE INDEX IF NOT EXISTS idx_processed_message_local_period
ON processed_message(processing_run_id, local_year, local_month, local_day);
CREATE INDEX IF NOT EXISTS idx_sender_run_conversation
ON sender_run(processing_run_id, conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_session_conversation
ON conversation_session(processing_run_id, conversation_id, id);

CREATE VIEW IF NOT EXISTS analysis_processed_messages_latest AS
SELECT pm.*
FROM processed_message pm
JOIN (
    SELECT MAX(id) AS processing_run_id
    FROM processing_run
    WHERE status='completed'
) latest ON latest.processing_run_id = pm.processing_run_id;
