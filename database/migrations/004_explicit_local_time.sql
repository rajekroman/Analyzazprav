ALTER TABLE message ADD COLUMN sent_at_local_iso TEXT;
ALTER TABLE message ADD COLUMN timezone_name TEXT;

DROP VIEW IF EXISTS analysis_messages;
CREATE VIEW analysis_messages AS
SELECT m.id, m.conversation_id, m.sender_id,
       p.canonical_name AS sender_name, p.is_self AS sender_is_self,
       m.sent_at_utc_us, m.sent_at_local_iso, m.timezone_name, m.timezone_offset_min,
       m.timestamp_precision, m.timestamp_quality,
       m.direction, m.message_type, m.text, m.is_edited, m.is_deleted, m.service
FROM message m LEFT JOIN participant p ON p.id = m.sender_id;

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

INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '4');
