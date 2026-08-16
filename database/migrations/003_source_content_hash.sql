ALTER TABLE import_run ADD COLUMN source_sha256 TEXT;

CREATE INDEX IF NOT EXISTS idx_import_run_source_sha256
ON import_run(source_type, source_sha256, parser_version);

UPDATE import_run
SET source_sha256 = json_extract(metadata_json, '$.source.sha256')
WHERE source_sha256 IS NULL
  AND json_valid(metadata_json)
  AND json_extract(metadata_json, '$.source.sha256') IS NOT NULL;

INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '3');
