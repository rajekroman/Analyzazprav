PRAGMA foreign_keys = ON;

DROP VIEW IF EXISTS analysis_a4_reconciliation;

CREATE VIEW analysis_a4_reconciliation AS
WITH latest_processing AS (
    SELECT id AS processing_run_id
    FROM processing_run
    WHERE status = 'completed'
    ORDER BY id DESC
    LIMIT 1
),
a3_counts AS (
    SELECT conversation_id,
           COUNT(*) AS processed_membership_count
    FROM analysis_processed_messages_latest
    GROUP BY conversation_id
),
response_invalid AS (
    SELECT r.analytics_run_id,
           r.conversation_id,
           SUM(CASE WHEN cs.id IS NULL THEN 1 ELSE 0 END) AS invalid_response_session_count
    FROM analytics_response_latency r
    JOIN analytics_run ar ON ar.id = r.analytics_run_id
    LEFT JOIN conversation_session cs
      ON cs.processing_run_id = ar.processing_run_id
     AND cs.id = r.session_id
     AND cs.conversation_id = r.conversation_id
    GROUP BY r.analytics_run_id, r.conversation_id
),
silence_invalid AS (
    SELECT s.analytics_run_id,
           s.conversation_id,
           SUM(
               CASE WHEN previous.id IS NULL OR next.id IS NULL THEN 1 ELSE 0 END
           ) AS invalid_silence_session_count
    FROM analytics_silence_event s
    JOIN analytics_run ar ON ar.id = s.analytics_run_id
    LEFT JOIN conversation_session previous
      ON previous.processing_run_id = ar.processing_run_id
     AND previous.id = s.previous_session_id
     AND previous.conversation_id = s.conversation_id
    LEFT JOIN conversation_session next
      ON next.processing_run_id = ar.processing_run_id
     AND next.id = s.next_session_id
     AND next.conversation_id = s.conversation_id
    GROUP BY s.analytics_run_id, s.conversation_id
),
event_invalid AS (
    SELECT e.analytics_run_id,
           e.conversation_id,
           SUM(
               CASE
                   WHEN e.session_id IS NOT NULL AND cs.id IS NULL THEN 1
                   ELSE 0
               END
           ) AS invalid_event_session_count
    FROM analytics_event e
    JOIN analytics_run ar ON ar.id = e.analytics_run_id
    LEFT JOIN conversation_session cs
      ON cs.processing_run_id = ar.processing_run_id
     AND cs.id = e.session_id
     AND cs.conversation_id = e.conversation_id
    GROUP BY e.analytics_run_id, e.conversation_id
)
SELECT a.analytics_run_id,
       a.conversation_id,
       ar.processing_run_id,
       lp.processing_run_id AS latest_processing_run_id,
       a.source_message_count AS a4_source_membership_count,
       COALESCE(c.processed_membership_count, 0) AS a3_processed_membership_count,
       a.source_message_count - COALESCE(c.processed_membership_count, 0)
           AS membership_count_delta,
       a.known_sender_message_count,
       a.unknown_sender_message_count,
       a.known_sender_message_count + a.unknown_sender_message_count
           AS sender_accounted_membership_count,
       COALESCE(r.invalid_response_session_count, 0)
           AS invalid_response_session_count,
       COALESCE(s.invalid_silence_session_count, 0)
           AS invalid_silence_session_count,
       COALESCE(e.invalid_event_session_count, 0)
           AS invalid_event_session_count,
       CASE WHEN ar.processing_run_id = lp.processing_run_id THEN 1 ELSE 0 END
           AS uses_latest_processing_run,
       CASE
           WHEN ar.processing_run_id = lp.processing_run_id
            AND a.source_message_count = COALESCE(c.processed_membership_count, 0)
            AND a.source_message_count =
                a.known_sender_message_count + a.unknown_sender_message_count
            AND COALESCE(r.invalid_response_session_count, 0) = 0
            AND COALESCE(s.invalid_silence_session_count, 0) = 0
            AND COALESCE(e.invalid_event_session_count, 0) = 0
           THEN 1 ELSE 0
       END AS reconciliation_ok
FROM analysis_a4_conversations a
JOIN analytics_run ar ON ar.id = a.analytics_run_id
CROSS JOIN latest_processing lp
LEFT JOIN a3_counts c ON c.conversation_id = a.conversation_id
LEFT JOIN response_invalid r
  ON r.analytics_run_id = a.analytics_run_id
 AND r.conversation_id = a.conversation_id
LEFT JOIN silence_invalid s
  ON s.analytics_run_id = a.analytics_run_id
 AND s.conversation_id = a.conversation_id
LEFT JOIN event_invalid e
  ON e.analytics_run_id = a.analytics_run_id
 AND e.conversation_id = a.conversation_id;
