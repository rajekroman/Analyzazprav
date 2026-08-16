-- Preserve source-evidenced message relations even when a canonical target
-- cannot yet be resolved. One row belongs to one message_source occurrence.

CREATE TABLE IF NOT EXISTS message_relation_source (
    id INTEGER PRIMARY KEY,
    message_source_id INTEGER NOT NULL REFERENCES message_source(id) ON DELETE CASCADE,
    relation_key TEXT NOT NULL,
    position INTEGER,
    relation_type TEXT NOT NULL,
    target_identifier_type TEXT NOT NULL,
    target_identifier_value TEXT NOT NULL,
    target_service TEXT,
    source_relation_type TEXT,
    canonical_relation_id INTEGER REFERENCES message_relation(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(message_source_id, relation_key)
);

CREATE INDEX IF NOT EXISTS idx_message_relation_source_canonical
ON message_relation_source(canonical_relation_id)
WHERE canonical_relation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_message_relation_source_unresolved_target
ON message_relation_source(
    relation_type,
    target_identifier_type,
    target_identifier_value,
    target_service
)
WHERE canonical_relation_id IS NULL;

CREATE VIEW IF NOT EXISTS analysis_message_relation_sources AS
SELECT mrs.id AS relation_source_id,
       mrs.message_source_id,
       ms.message_id AS canonical_source_message_id,
       ms.source_record_key,
       ms.import_run_id,
       ms.source_type,
       ir.source_sha256,
       ir.parser_version,
       mrs.relation_key,
       mrs.position,
       mrs.relation_type,
       mrs.target_identifier_type,
       mrs.target_identifier_value,
       mrs.target_service,
       mrs.source_relation_type,
       CASE
           WHEN mrs.canonical_relation_id IS NULL THEN 'unresolved'
           ELSE 'resolved'
       END AS resolution_status,
       mrs.canonical_relation_id,
       mr.target_message_id AS canonical_target_message_id,
       mrs.metadata_json
FROM message_relation_source mrs
JOIN message_source ms ON ms.id = mrs.message_source_id
JOIN import_run ir ON ir.id = ms.import_run_id
LEFT JOIN message_relation mr ON mr.id = mrs.canonical_relation_id;

INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '7');
