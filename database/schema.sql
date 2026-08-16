PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '3');

CREATE TABLE IF NOT EXISTS schema_migration (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at_utc_us INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS import_run (
    id INTEGER PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_path TEXT,
    source_fingerprint TEXT NOT NULL,
    source_sha256 TEXT,
    parser_version TEXT,
    normalizer_version TEXT NOT NULL DEFAULT '1',
    started_at_utc_us INTEGER NOT NULL,
    finished_at_utc_us INTEGER,
    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
    statistics_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_type, source_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_import_run_source_sha256
ON import_run(source_type, source_sha256, parser_version);

CREATE TABLE IF NOT EXISTS participant (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT,
    is_self INTEGER NOT NULL DEFAULT 0 CHECK(is_self IN (0,1)),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS participant_identity (
    id INTEGER PRIMARY KEY,
    participant_id INTEGER NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    identity_type TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    original_value TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(identity_type, normalized_value)
);

CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT UNIQUE,
    title TEXT,
    conversation_type TEXT NOT NULL DEFAULT 'unknown',
    service TEXT,
    created_at_utc_us INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS conversation_source (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    import_run_id INTEGER REFERENCES import_run(id) ON DELETE SET NULL,
    source_type TEXT NOT NULL,
    source_conversation_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_type, source_conversation_id)
);

CREATE TABLE IF NOT EXISTS conversation_participant (
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    role TEXT,
    PRIMARY KEY(conversation_id, participant_id)
);

CREATE TABLE IF NOT EXISTS message (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    sender_id INTEGER REFERENCES participant(id) ON DELETE SET NULL,
    sent_at_utc_us INTEGER,
    timezone_offset_min INTEGER,
    timestamp_precision TEXT NOT NULL DEFAULT 'unknown'
        CHECK(timestamp_precision IN ('microsecond','millisecond','second','minute','unknown')),
    timestamp_quality TEXT NOT NULL DEFAULT 'unknown'
        CHECK(timestamp_quality IN ('exact','converted','inferred','unknown')),
    direction TEXT NOT NULL DEFAULT 'unknown'
        CHECK(direction IN ('incoming','outgoing','system','unknown')),
    message_type TEXT NOT NULL DEFAULT 'text',
    text TEXT,
    is_edited INTEGER NOT NULL DEFAULT 0 CHECK(is_edited IN (0,1)),
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),
    service TEXT,
    canonical_guid TEXT,
    created_import_id INTEGER NOT NULL REFERENCES import_run(id),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_message_guid
ON message(service, canonical_guid) WHERE canonical_guid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_message_conversation_time
ON message(conversation_id, sent_at_utc_us, id);
CREATE INDEX IF NOT EXISTS idx_message_sender_time
ON message(sender_id, sent_at_utc_us, id);

CREATE TABLE IF NOT EXISTS message_source (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    import_run_id INTEGER NOT NULL REFERENCES import_run(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_message_id TEXT,
    source_conversation_id TEXT,
    source_row_id TEXT,
    source_record_key TEXT,
    source_contract_version TEXT,
    raw_timestamp TEXT,
    raw_text TEXT,
    source_hash TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(import_run_id, source_hash)
);
CREATE INDEX IF NOT EXISTS idx_message_source_record_key
ON message_source(source_type, source_record_key) WHERE source_record_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_message_source_source_id
ON message_source(source_type, source_message_id) WHERE source_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_message_source_message ON message_source(message_id);

CREATE TABLE IF NOT EXISTS message_relation (
    id INTEGER PRIMARY KEY,
    source_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    target_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_message_id, target_message_id, relation_type)
);

CREATE TABLE IF NOT EXISTS attachment (
    id INTEGER PRIMARY KEY,
    sha256 TEXT,
    mime_type TEXT,
    size_bytes INTEGER,
    filename TEXT,
    storage_path TEXT,
    availability TEXT NOT NULL DEFAULT 'unknown'
        CHECK(availability IN ('available','missing','corrupt','external','unknown')),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_attachment_sha256 ON attachment(sha256) WHERE sha256 IS NOT NULL;

CREATE TABLE IF NOT EXISTS message_attachment (
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    attachment_id INTEGER NOT NULL REFERENCES attachment(id) ON DELETE CASCADE,
    position INTEGER,
    PRIMARY KEY(message_id, attachment_id)
);

CREATE TABLE IF NOT EXISTS attachment_source (
    id INTEGER PRIMARY KEY,
    attachment_id INTEGER NOT NULL REFERENCES attachment(id) ON DELETE CASCADE,
    import_run_id INTEGER NOT NULL REFERENCES import_run(id) ON DELETE CASCADE,
    source_attachment_id TEXT,
    original_filename TEXT,
    original_path TEXT,
    raw_payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE VIEW IF NOT EXISTS analysis_messages AS
SELECT m.id, m.conversation_id, m.sender_id,
       p.canonical_name AS sender_name, p.is_self AS sender_is_self,
       m.sent_at_utc_us, m.timezone_offset_min,
       m.timestamp_precision, m.timestamp_quality,
       m.direction, m.message_type, m.text, m.is_edited, m.is_deleted, m.service
FROM message m LEFT JOIN participant p ON p.id = m.sender_id;

CREATE VIEW IF NOT EXISTS analysis_conversations AS
SELECT c.id, c.canonical_key, c.title, c.conversation_type, c.service,
       COUNT(DISTINCT cp.participant_id) AS participant_count,
       COUNT(DISTINCT m.id) AS message_count,
       MIN(m.sent_at_utc_us) AS first_message_at_utc_us,
       MAX(m.sent_at_utc_us) AS last_message_at_utc_us
FROM conversation c
LEFT JOIN conversation_participant cp ON cp.conversation_id = c.id
LEFT JOIN message m ON m.conversation_id = c.id
GROUP BY c.id;

CREATE VIEW IF NOT EXISTS analysis_attachments AS
SELECT ma.message_id, a.id AS attachment_id, a.sha256, a.mime_type,
       a.size_bytes, a.filename, a.storage_path, a.availability, ma.position
FROM message_attachment ma JOIN attachment a ON a.id = ma.attachment_id;

CREATE VIEW IF NOT EXISTS analysis_message_sources AS
SELECT ms.message_id, ms.source_type, ms.source_message_id, ms.source_conversation_id,
       ms.source_row_id, ms.source_record_key, ms.source_contract_version,
       ms.raw_timestamp, ms.raw_text, ms.source_hash, ms.import_run_id
FROM message_source ms;
