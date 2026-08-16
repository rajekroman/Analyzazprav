ALTER TABLE message_source ADD COLUMN source_record_key TEXT;
ALTER TABLE message_source ADD COLUMN source_contract_version TEXT;

CREATE INDEX IF NOT EXISTS idx_message_source_record_key
ON message_source(source_type, source_record_key) WHERE source_record_key IS NOT NULL;

DROP TABLE IF EXISTS duplicate_candidate;

CREATE VIEW IF NOT EXISTS analysis_message_sources AS
SELECT ms.message_id, ms.source_type, ms.source_message_id, ms.source_conversation_id,
       ms.source_row_id, ms.source_record_key, ms.source_contract_version,
       ms.raw_timestamp, ms.raw_text, ms.source_hash, ms.import_run_id
FROM message_source ms;

INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '2');
