from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    fingerprint TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kind, canonical_path)
);

CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    source_message_count INTEGER NOT NULL DEFAULT 0,
    imported_message_count INTEGER NOT NULL DEFAULT 0,
    duplicate_message_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT NOT NULL UNIQUE,
    display_name TEXT,
    address TEXT,
    is_me INTEGER NOT NULL DEFAULT 0 CHECK(is_me IN (0,1))
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    external_id TEXT NOT NULL,
    display_name TEXT,
    service TEXT,
    UNIQUE(source_id, external_id)
);

CREATE TABLE IF NOT EXISTS conversation_participants (
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    PRIMARY KEY(conversation_id, participant_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    external_id TEXT NOT NULL,
    conversation_id INTEGER REFERENCES conversations(id),
    sender_participant_id INTEGER REFERENCES participants(id),
    sent_at_utc TEXT,
    text TEXT,
    is_from_me INTEGER NOT NULL DEFAULT 0 CHECK(is_from_me IN (0,1)),
    service TEXT,
    message_type INTEGER,
    associated_message_guid TEXT,
    has_attachments INTEGER NOT NULL DEFAULT 0 CHECK(has_attachments IN (0,1)),
    raw_rowid INTEGER,
    UNIQUE(source_id, external_id)
);

CREATE TABLE IF NOT EXISTS message_conversations (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
    PRIMARY KEY(message_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_time
ON messages(conversation_id, sent_at_utc, id);

CREATE INDEX IF NOT EXISTS idx_messages_sender_time
ON messages(sender_participant_id, sent_at_utc);

CREATE INDEX IF NOT EXISTS idx_message_conversations_conversation
ON message_conversations(conversation_id, message_id);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    external_id TEXT NOT NULL,
    filename TEXT,
    mime_type TEXT,
    transfer_name TEXT,
    total_bytes INTEGER,
    is_sticker INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, external_id)
);

CREATE TABLE IF NOT EXISTS message_attachments (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    attachment_id INTEGER NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    PRIMARY KEY(message_id, attachment_id)
);

CREATE TABLE IF NOT EXISTS message_features (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sequence_in_conversation INTEGER NOT NULL,
    session_index INTEGER NOT NULL,
    previous_message_id INTEGER REFERENCES messages(id),
    reply_to_message_id INTEGER REFERENCES messages(id),
    response_latency_seconds REAL,
    PRIMARY KEY(message_id, conversation_id)
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
