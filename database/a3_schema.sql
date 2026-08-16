PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS processing_run (
    id INTEGER PRIMARY KEY,
    processing_version TEXT NOT NULL,
    started_at_utc_us INTEGER NOT NULL,
    finished_at_utc_us INTEGER,
    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
    config_json TEXT NOT NULL DEFAULT '{}',
    input_message_count INTEGER NOT NULL DEFAULT 0,
    output_message_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sender_run (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    sender_id INTEGER REFERENCES participant(id) ON DELETE SET NULL,
    first_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    last_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
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
    session_id INTEGER NOT NULL REFERENCES conversation_session(id) ON DELETE CASCADE,
    method TEXT NOT NULL,
    confidence REAL NOT NULL,
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id)
);

CREATE TABLE IF NOT EXISTS conversation_thread_message (
    thread_id INTEGER NOT NULL REFERENCES conversation_thread(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY(thread_id, message_id)
);

CREATE TABLE IF NOT EXISTS processed_message (
    message_id INTEGER PRIMARY KEY REFERENCES message(id) ON DELETE CASCADE,
    processing_run_id INTEGER NOT NULL REFERENCES processing_run(id),
    sequence_number INTEGER NOT NULL,
    text_clean TEXT,
    sender_run_id INTEGER NOT NULL REFERENCES sender_run(id),
    session_id INTEGER NOT NULL REFERENCES conversation_session(id),
    thread_id INTEGER REFERENCES conversation_thread(id),
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
    seconds_since_previous_message REAL,
    seconds_since_previous_other_sender REAL
);

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

CREATE INDEX IF NOT EXISTS idx_processed_message_session ON processed_message(session_id);
CREATE INDEX IF NOT EXISTS idx_processed_message_thread ON processed_message(thread_id);
CREATE INDEX IF NOT EXISTS idx_sender_run_conversation ON sender_run(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_session_conversation ON conversation_session(conversation_id, id);
