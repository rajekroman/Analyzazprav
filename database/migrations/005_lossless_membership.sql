-- A0 contract v1: source-snapshot-safe conversation identity,
-- explicit message<->conversation membership, source relation provenance,
-- and attachment occurrence fidelity.

ALTER TABLE conversation_source RENAME TO conversation_source_legacy_v4;

CREATE TABLE conversation_source (
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

INSERT INTO conversation_source(
    id, conversation_id, import_run_id, source_type, source_snapshot_key,
    source_sha256, source_conversation_id, metadata_json
)
SELECT cs.id,
       cs.conversation_id,
       cs.import_run_id,
       cs.source_type,
       COALESCE(
           ir.source_sha256,
           CASE WHEN ir.source_fingerprint IS NOT NULL THEN 'fingerprint:' || ir.source_fingerprint END,
           'legacy-conversation-source:' || CAST(cs.id AS TEXT)
       ),
       ir.source_sha256,
       cs.source_conversation_id,
       cs.metadata_json
FROM conversation_source_legacy_v4 cs
LEFT JOIN import_run ir ON ir.id = cs.import_run_id;

-- Recover source-snapshot-specific provenance for any legacy message_source rows.
-- This cannot infer a different canonical conversation after an old collision,
-- but it does restore distinct source relations for audit and future processing.
INSERT OR IGNORE INTO conversation_source(
    conversation_id, import_run_id, source_type, source_snapshot_key,
    source_sha256, source_conversation_id, metadata_json
)
SELECT DISTINCT
       m.conversation_id,
       ms.import_run_id,
       ms.source_type,
       COALESCE(ir.source_sha256, 'fingerprint:' || ir.source_fingerprint),
       ir.source_sha256,
       ms.source_conversation_id,
       '{"recovered_from":"message_source"}'
FROM message_source ms
JOIN message m ON m.id = ms.message_id
JOIN import_run ir ON ir.id = ms.import_run_id
WHERE ms.source_conversation_id IS NOT NULL
  AND ms.source_conversation_id <> '';

DROP TABLE conversation_source_legacy_v4;

CREATE INDEX idx_conversation_source_snapshot
ON conversation_source(source_type, source_snapshot_key, source_conversation_id);
CREATE INDEX idx_conversation_source_sha256
ON conversation_source(source_type, source_sha256, source_conversation_id)
WHERE source_sha256 IS NOT NULL;
CREATE INDEX idx_conversation_source_conversation
ON conversation_source(conversation_id);

CREATE TABLE message_conversation (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(message_id, conversation_id)
);

CREATE UNIQUE INDEX idx_message_conversation_one_primary
ON message_conversation(message_id) WHERE is_primary = 1;
CREATE INDEX idx_message_conversation_conversation_time
ON message_conversation(conversation_id, message_id);

INSERT INTO message_conversation(message_id, conversation_id, is_primary, metadata_json)
SELECT id, conversation_id, 1, '{"backfill":"message.conversation_id"}'
FROM message;

CREATE TABLE message_source_conversation (
    message_source_id INTEGER NOT NULL REFERENCES message_source(id) ON DELETE CASCADE,
    conversation_source_id INTEGER NOT NULL REFERENCES conversation_source(id) ON DELETE CASCADE,
    membership_id INTEGER NOT NULL REFERENCES message_conversation(id) ON DELETE CASCADE,
    position INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(message_source_id, conversation_source_id)
);
CREATE INDEX idx_message_source_conversation_membership
ON message_source_conversation(membership_id);

INSERT OR IGNORE INTO message_source_conversation(
    message_source_id, conversation_source_id, membership_id, position, metadata_json
)
SELECT ms.id,
       cs.id,
       mc.id,
       0,
       '{"backfill":"message_source.source_conversation_id"}'
FROM message_source ms
JOIN import_run ir ON ir.id = ms.import_run_id
JOIN conversation_source cs
  ON cs.source_type = ms.source_type
 AND cs.source_snapshot_key = COALESCE(ir.source_sha256, 'fingerprint:' || ir.source_fingerprint)
 AND cs.source_conversation_id = ms.source_conversation_id
JOIN message_conversation mc
  ON mc.message_id = ms.message_id
 AND mc.conversation_id = cs.conversation_id
WHERE ms.source_conversation_id IS NOT NULL
  AND ms.source_conversation_id <> '';

CREATE TABLE message_attachment_occurrence (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    attachment_id INTEGER NOT NULL REFERENCES attachment(id) ON DELETE CASCADE,
    position INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX idx_message_attachment_occurrence_position
ON message_attachment_occurrence(message_id, position) WHERE position IS NOT NULL;
CREATE INDEX idx_message_attachment_occurrence_attachment
ON message_attachment_occurrence(attachment_id);

INSERT INTO message_attachment_occurrence(message_id, attachment_id, position, metadata_json)
SELECT message_id, attachment_id, position, '{"backfill":"message_attachment"}'
FROM message_attachment;

ALTER TABLE attachment_source ADD COLUMN message_attachment_occurrence_id INTEGER
    REFERENCES message_attachment_occurrence(id) ON DELETE SET NULL;
ALTER TABLE attachment_source ADD COLUMN source_occurrence_key TEXT;
CREATE UNIQUE INDEX idx_attachment_source_occurrence
ON attachment_source(import_run_id, source_occurrence_key)
WHERE source_occurrence_key IS NOT NULL;

DROP VIEW IF EXISTS analysis_messages;
CREATE VIEW analysis_messages AS
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

DROP VIEW IF EXISTS analysis_conversations;
CREATE VIEW analysis_conversations AS
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

DROP VIEW IF EXISTS analysis_attachments;
CREATE VIEW analysis_attachments AS
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

DROP VIEW IF EXISTS analysis_message_sources;
CREATE VIEW analysis_message_sources AS
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

CREATE VIEW analysis_message_memberships AS
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

INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '5');
