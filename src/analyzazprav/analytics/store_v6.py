from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from .config import AnalyticsConfig
from .models import ConversationAnalytics
from .store_v5 import AnalyticsStore as V5AnalyticsStore
from .versioning import ANALYTICS_VERSION, analysis_signature


class AnalyticsStore(V5AnalyticsStore):
    """A4 v6 persistence: v5 outputs plus lexical topics and analysis state."""

    @staticmethod
    def _extension_schema_path_v6() -> Path:
        return Path(__file__).resolve().parents[3] / "database" / "a4_schema_v6.sql"

    def initialize(self) -> None:
        super().initialize()
        with self.conn:
            self.conn.executescript(
                self._extension_schema_path_v6().read_text(encoding="utf-8")
            )

    def write_run(
        self,
        results: Sequence[ConversationAnalytics],
        config: AnalyticsConfig,
        processing_run_id: int | None = None,
    ) -> int:
        run_id = super().write_run(results, config, processing_run_id)
        signature = analysis_signature(config)
        topic_rows = [
            (
                run_id,
                topic.conversation_id,
                topic.topic_key,
                topic.method,
                topic.normalized_phrase,
                topic.ngram_size,
                topic.document_frequency,
                topic.document_frequency_ratio,
                topic.occurrence_count,
                topic.participant_count,
                topic.salience,
                topic.first_period_date,
                topic.last_period_date,
                json.dumps(topic.source_message_ids),
            )
            for result in results
            for topic in result.topic_candidates
        ]
        evidence_rows = [
            (
                run_id,
                row.conversation_id,
                row.topic_key,
                row.message_id,
                row.participant_id,
                row.period_date,
                row.date_basis,
                row.occurrence_count,
            )
            for result in results
            for row in result.topic_evidence
        ]

        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE analytics_run SET analytics_version = ? WHERE id = ?",
                    (ANALYTICS_VERSION, run_id),
                )
                self.conn.executemany(
                    """INSERT INTO analytics_conversation_state_v6(
                           analytics_run_id, conversation_id, source_fingerprint,
                           analysis_signature
                       ) VALUES (?, ?, ?, ?)""",
                    [
                        (
                            run_id,
                            result.conversation_id,
                            result.source_fingerprint,
                            signature,
                        )
                        for result in results
                    ],
                )
                if topic_rows:
                    self.conn.executemany(
                        """INSERT INTO analytics_topic_candidate(
                               analytics_run_id, conversation_id, topic_key, method,
                               normalized_phrase, ngram_size, document_frequency,
                               document_frequency_ratio, occurrence_count, participant_count,
                               salience, first_period_date, last_period_date,
                               source_message_ids_json
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        topic_rows,
                    )
                if evidence_rows:
                    self.conn.executemany(
                        """INSERT INTO analytics_topic_evidence(
                               analytics_run_id, conversation_id, topic_key, message_id,
                               participant_id, period_date, date_basis, occurrence_count
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        evidence_rows,
                    )
        except Exception:
            # Base v5 persistence may already have committed its reproducible rows.
            # Never leave a partially extended v6 run exposed as completed.
            with self.conn:
                self.conn.execute(
                    "UPDATE analytics_run SET status = 'failed' WHERE id = ?",
                    (run_id,),
                )
            raise
        return run_id
