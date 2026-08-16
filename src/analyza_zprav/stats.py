from __future__ import annotations

import sqlite3


def overview(conn: sqlite3.Connection) -> dict[str, int]:
    scalar = lambda sql: int(conn.execute(sql).fetchone()[0])
    return {
        "sources": scalar("SELECT COUNT(*) FROM sources"),
        "conversations": scalar("SELECT COUNT(*) FROM conversations"),
        "participants": scalar("SELECT COUNT(*) FROM participants"),
        "messages": scalar("SELECT COUNT(*) FROM messages"),
        "attachments": scalar("SELECT COUNT(*) FROM attachments"),
        "derived_features": scalar("SELECT COUNT(*) FROM message_features"),
    }


def conversation_rows(conn: sqlite3.Connection, limit: int = 50):
    return conn.execute(
        """
        SELECT c.id, COALESCE(c.display_name, c.external_id) AS name,
               c.service, COUNT(DISTINCT mc.message_id) AS message_count,
               MIN(m.sent_at_utc) AS first_message,
               MAX(m.sent_at_utc) AS last_message
        FROM conversations c
        LEFT JOIN message_conversations mc ON mc.conversation_id=c.id
        LEFT JOIN messages m ON m.id=mc.message_id
        GROUP BY c.id
        ORDER BY message_count DESC, c.id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def conversation_metrics(conn: sqlite3.Connection, conversation_id: int) -> dict:
    row = conn.execute(
        """
        SELECT
          COUNT(DISTINCT m.id) AS messages,
          SUM(CASE WHEN m.is_from_me=1 THEN 1 ELSE 0 END) AS sent_by_me,
          SUM(CASE WHEN m.is_from_me=0 THEN 1 ELSE 0 END) AS sent_to_me,
          COUNT(DISTINCT mf.session_index) AS sessions,
          AVG(CASE WHEN m.is_from_me=1 THEN mf.response_latency_seconds END) AS my_avg_response_seconds,
          AVG(CASE WHEN m.is_from_me=0 THEN mf.response_latency_seconds END) AS their_avg_response_seconds
        FROM message_conversations mc
        JOIN messages m ON m.id=mc.message_id
        LEFT JOIN message_features mf ON mf.message_id=m.id AND mf.conversation_id=mc.conversation_id
        WHERE mc.conversation_id=?
        """,
        (conversation_id,),
    ).fetchone()
    return dict(row) if row else {}
