-- Stable downstream projection for attachment-source provenance.
--
-- Keep one output row per physical attachment_source provenance row.  The view
-- deliberately exposes existing source/import identity instead of inventing a
-- second attachment identity or parsing source_occurrence_key.

DROP VIEW IF EXISTS analysis_attachment_sources;
CREATE VIEW analysis_attachment_sources AS
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

INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '6');
