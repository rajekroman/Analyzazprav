from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .config import AnalyticsConfig
from .models import ConversationAnalytics
from .store_v7 import AnalyticsStore as V7AnalyticsStore


class AnalyticsStore(V7AnalyticsStore):
    """A4 v8 persistence: integrated v7 outputs plus topic-marker evidence."""

    @staticmethod
    def _extension_schema_path_v8() -> Path:
        return Path(__file__).resolve().parents[3] / "database" / "a4_schema_v8.sql"

    def _drop_v8_extension(self) -> None:
        for view in (
            "analysis_a4_topic_marker_reconciliation",
            "analysis_a4_topic_marker_periods",
            "analysis_a4_topic_marker_summary",
            "analysis_a4_topic_marker_evidence",
        ):
            self.conn.execute(f"DROP VIEW IF EXISTS {view}")
        self.conn.execute("DROP TABLE IF EXISTS analytics_topic_marker_evidence")

    def initialize(self) -> None:
        # If v7 needs to rebuild its reproducible derived schema, remove the v8
        # child table first so foreign-key ordering cannot block that rebuild.
        with self.conn:
            if self._needs_integrated_schema_rebuild():
                self._drop_v8_extension()
        super().initialize()
        with self.conn:
            self.conn.executescript(
                self._extension_schema_path_v8().read_text(encoding="utf-8")
            )

    def write_run(
        self,
        results: Sequence[ConversationAnalytics],
        config: AnalyticsConfig,
        processing_run_id: int | None = None,
    ) -> int:
        run_id = super().write_run(results, config, processing_run_id)
        rows = [
            (
                run_id,
                row.conversation_id,
                row.topic_key,
                row.message_id,
                row.affection_hit_count,
                row.negative_hit_count,
            )
            for result in results
            for row in result.topic_marker_evidence
        ]
        try:
            if rows:
                with self.conn:
                    self.conn.executemany(
                        """INSERT INTO analytics_topic_marker_evidence(
                               analytics_run_id, conversation_id, topic_key, message_id,
                               affection_hit_count, negative_hit_count
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        rows,
                    )
        except Exception:
            with self.conn:
                self.conn.execute(
                    "UPDATE analytics_run SET status = 'failed' WHERE id = ?",
                    (run_id,),
                )
            raise
        return run_id
