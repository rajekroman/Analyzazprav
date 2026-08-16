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
