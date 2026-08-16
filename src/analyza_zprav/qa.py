from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class VerificationReport:
    total_messages: int
    messages_without_conversation: int
    messages_without_sender: int
    messages_without_conversation_link: int
    orphan_message_attachments: int
    duplicate_external_ids: int
    failed_import_runs: int
    import_reconciliation_mismatches: int

    @property
    def ok(self) -> bool:
        return all(value == 0 for key, value in asdict(self).items() if key != "total_messages")


def verify(conn: sqlite3.Connection) -> VerificationReport:
    scalar = lambda sql: int(conn.execute(sql).fetchone()[0])
    return VerificationReport(
        total_messages=scalar("SELECT COUNT(*) FROM messages"),
        messages_without_conversation=scalar("SELECT COUNT(*) FROM messages WHERE conversation_id IS NULL"),
        messages_without_sender=scalar("SELECT COUNT(*) FROM messages WHERE sender_participant_id IS NULL"),
        messages_without_conversation_link=scalar(
            """
            SELECT COUNT(*) FROM messages m
            LEFT JOIN message_conversations mc ON mc.message_id=m.id
            WHERE mc.message_id IS NULL
            """
        ),
        orphan_message_attachments=scalar(
            """
            SELECT COUNT(*) FROM message_attachments ma
            LEFT JOIN messages m ON m.id=ma.message_id
            LEFT JOIN attachments a ON a.id=ma.attachment_id
            WHERE m.id IS NULL OR a.id IS NULL
            """
        ),
        duplicate_external_ids=scalar(
            """
            SELECT COUNT(*) FROM (
              SELECT source_id, external_id, COUNT(*) c FROM messages
              GROUP BY source_id, external_id HAVING c > 1
            )
            """
        ),
        failed_import_runs=scalar("SELECT COUNT(*) FROM import_runs WHERE status='failed'"),
        import_reconciliation_mismatches=scalar(
            """
            SELECT COUNT(*) FROM import_runs
            WHERE status='completed'
              AND source_message_count != imported_message_count + duplicate_message_count
            """
        ),
    )
