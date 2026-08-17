PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '7');

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
    source_snapshot_key TEXT NOT NULL,
    source_sha256 TEXT,
    source_conversation_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_type, source_snapshot_key, source_conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_conversation_source_snapshot
ON conversation_source(source_type, source_snapshot_key, source_conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_source_sha256
ON conversation_source(source_type, source_sha256, source_conversation_id)
WHERE source_sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversation_source_conversation
ON conversation_source(conversation_id);

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
    sent_at_local_iso TEXT,
    timezone_name TEXT,
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

CREATE TRIGGER IF NOT EXISTS trg_message_timezone_offset_insert
BEFORE INSERT ON message
WHEN NEW.timezone_offset_min IS NOT NULL
 AND (NEW.timezone_offset_min < -840 OR NEW.timezone_offset_min > 840)
BEGIN
    SELECT RAISE(ABORT, 'timezone_offset_min out of range');
END;

CREATE TRIGGER IF NOT EXISTS trg_message_timezone_offset_update
BEFORE UPDATE OF timezone_offset_min ON message
WHEN NEW.timezone_offset_min IS NOT NULL
 AND (NEW.timezone_offset_min < -840 OR NEW.timezone_offset_min > 840)
BEGIN
    SELECT RAISE(ABORT, 'timezone_offset_min out of range');
END;

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

CREATE TABLE IF NOT EXISTS message_conversation (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(message_id, conversation_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_message_conversation_one_primary
ON message_conversation(message_id) WHERE is_primary = 1;
CREATE INDEX IF NOT EXISTS idx_message_conversation_conversation_time
ON message_conversation(conversation_id, message_id);

CREATE TABLE IF NOT EXISTS message_source_conversation (
    message_source_id INTEGER NOT NULL REFERENCES message_source(id) ON DELETE CASCADE,
    conversation_source_id INTEGER NOT NULL REFERENCES conversation_source(id) ON DELETE CASCADE,
    membership_id INTEGER NOT NULL REFERENCES message_conversation(id) ON DELETE CASCADE,
    position INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(message_source_id, conversation_source_id)
);
CREATE INDEX IF NOT EXISTS idx_message_source_conversation_membership
ON message_source_conversation(membership_id);

CREATE TABLE IF NOT EXISTS message_relation (
    id INTEGER PRIMARY KEY,
    source_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    target_message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_message_id, target_message_id, relation_type)
);

CREATE TABLE IF NOT EXISTS message_relation_source (
    id INTEGER PRIMARY KEY,
    message_source_id INTEGER NOT NULL REFERENCES message_source(id) ON DELETE CASCADE,
    import_run_id INTEGER NOT NULL REFERENCES import_run(id) ON DELETE CASCADE,
    source_relation_key TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    target_source_guid TEXT,
    target_service TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'unresolved'
        CHECK(resolution_status IN ('resolved','unresolved')),
    resolved_relation_id INTEGER REFERENCES message_relation(id) ON DELETE RESTRICT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(import_run_id, source_relation_key),
    CHECK(
        (resolution_status='resolved' AND resolved_relation_id IS NOT NULL)
        OR
        (resolution_status='unresolved' AND resolved_relation_id IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_message_relation_source_message_source
ON message_relation_source(message_source_id);
CREATE INDEX IF NOT EXISTS idx_message_relation_source_resolved
ON message_relation_source(resolved_relation_id)
WHERE resolved_relation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_message_relation_source_target_guid
ON message_relation_source(target_service, target_source_guid)
WHERE target_source_guid IS NOT NULL;

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

CREATE TABLE IF NOT EXISTS message_attachment_occurrence (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    attachment_id INTEGER NOT NULL REFERENCES attachment(id) ON DELETE CASCADE,
    position INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_message_attachment_occurrence_position
ON message_attachment_occurrence(message_id, position) WHERE position IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_message_attachment_occurrence_attachment
ON message_attachment_occurrence(attachment_id);

CREATE TABLE IF NOT EXISTS attachment_source (
    id INTEGER PRIMARY KEY,
    attachment_id INTEGER NOT NULL REFERENCES attachment(id) ON DELETE CASCADE,
    import_run_id INTEGER NOT NULL REFERENCES import_run(id) ON DELETE CASCADE,
    source_attachment_id TEXT,
    original_filename TEXT,
    original_path TEXT,
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    message_attachment_occurrence_id INTEGER
        REFERENCES message_attachment_occurrence(id) ON DELETE SET NULL,
    source_occurrence_key TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_attachment_source_occurrence
ON attachment_source(import_run_id, source_occurrence_key)
WHERE source_occurrence_key IS NOT NULL;

CREATE VIEW IF NOT EXISTS analysis_messages AS
SELECT mc.id AS membership_id,
       m.id,
       mc.conversation_id,
       m.sender_id,
       p.canonical_name AS sender_name,
       p.is_self AS sender_is_self,
       m.sent_at_utc_us,
       m.sent_at_local_iso,
       m.timezone_name,
       m.timezone_offset_min,
       m.timestamp_precision,
       m.timestamp_quality,
       m.direction,
       m.message_type,
       m.text,
       m.is_edited,
       m.is_deleted,
       m.service
FROM message_conversation mc
JOIN message m ON m.id = mc.message_id
LEFT JOIN participant p ON p.id = m.sender_id;

CREATE VIEW IF NOT EXISTS analysis_conversations AS
SELECT c.id,
       c.canonical_key,
       c.title,
       c.conversation_type,
       c.service,
       COUNT(DISTINCT cp.participant_id) AS participant_count,
       COUNT(DISTINCT mc.message_id) AS message_count,
       MIN(m.sent_at_utc_us) AS first_message_at_utc_us,
       MAX(m.sent_at_utc_us) AS last_message_at_utc_us
FROM conversation c
LEFT JOIN conversation_participant cp ON cp.conversation_id = c.id
LEFT JOIN message_conversation mc ON mc.conversation_id = c.id
LEFT JOIN message m ON m.id = mc.message_id
GROUP BY c.id;

CREATE VIEW IF NOT EXISTS analysis_attachments AS
SELECT mao.id AS occurrence_id,
       mao.message_id,
       a.id AS attachment_id,
       a.sha256,
       a.mime_type,
       a.size_bytes,
       a.filename,
       a.storage_path,
       a.availability,
       mao.position
FROM message_attachment_occurrence mao
JOIN attachment a ON a.id = mao.attachment_id;

CREATE VIEW IF NOT EXISTS analysis_message_sources AS
SELECT ms.message_id,
       ms.source_type,
       COALESCE(ir.source_sha256, 'fingerprint:' || ir.source_fingerprint) AS source_snapshot_key,
       ir.source_sha256,
       ms.source_message_id,
       ms.source_conversation_id,
       ms.source_row_id,
       ms.source_record_key,
       ms.source_contract_version,
       ms.raw_timestamp,
       ms.raw_text,
       ms.source_hash,
       ms.import_run_id
FROM message_source ms
JOIN import_run ir ON ir.id = ms.import_run_id;

CREATE VIEW IF NOT EXISTS analysis_message_memberships AS
SELECT mc.id AS membership_id,
       mc.message_id,
       mc.conversation_id,
       mc.is_primary,
       msc.message_source_id,
       msc.conversation_source_id,
       cs.source_type,
       cs.source_snapshot_key,
       cs.source_sha256,
       cs.source_conversation_id,
       msc.position AS source_relation_position
FROM message_conversation mc
LEFT JOIN message_source_conversation msc ON msc.membership_id = mc.id
LEFT JOIN conversation_source cs ON cs.id = msc.conversation_source_id;

CREATE VIEW IF NOT EXISTS analysis_attachment_sources AS
SELECT ats.id AS attachment_source_id,
       ats.attachment_id,
       ats.message_attachment_occurrence_id AS occurrence_id,
       mao.message_id,
       mao.position,
       ats.import_run_id,
       ir.source_type,
       COALESCE(ir.source_sha256, 'fingerprint:' || ir.source_fingerprint) AS source_snapshot_key,
       ir.source_sha256,
       ir.parser_version,
       ats.source_attachment_id,
       ats.source_occurrence_key,
       ats.original_filename,
       ats.original_path
FROM attachment_source ats
JOIN import_run ir ON ir.id = ats.import_run_id
LEFT JOIN message_attachment_occurrence mao
  ON mao.id = ats.message_attachment_occurrence_id;

CREATE VIEW IF NOT EXISTS analysis_message_relation_sources AS
SELECT mrs.id AS source_relation_id,
       mrs.message_source_id,
       ms.message_id AS source_message_id,
       mrs.import_run_id,
       ir.source_type,
       COALESCE(ir.source_sha256, 'fingerprint:' || ir.source_fingerprint) AS source_snapshot_key,
       ir.source_sha256,
       ir.parser_version,
       mrs.source_relation_key,
       mrs.relation_type,
       mrs.target_source_guid,
       mrs.target_service,
       mrs.resolution_status,
       mrs.resolved_relation_id,
       mr.target_message_id AS resolved_target_message_id,
       mrs.metadata_json
FROM message_relation_source mrs
JOIN message_source ms ON ms.id = mrs.message_source_id
JOIN import_run ir ON ir.id = mrs.import_run_id
LEFT JOIN message_relation mr ON mr.id = mrs.resolved_relation_id;
