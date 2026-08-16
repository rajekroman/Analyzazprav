from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from .config import AnalyticsConfig
from .models import ConversationAnalytics
from .store import AnalyticsStore as BaseAnalyticsStore

ANALYTICS_VERSION = "5"


class AnalyticsStore(BaseAnalyticsStore):
    """A4 v5 persistence: v4 outputs plus fingerprints and trend summaries."""

    @staticmethod
    def _extension_schema_path() -> Path:
        return Path(__file__).resolve().parents[3] / "database" / "a4_schema_v5.sql"

    def initialize(self) -> None:
        super().initialize()
        with self.conn:
            self.conn.executescript(self._extension_schema_path().read_text(encoding="utf-8"))

    def write_run(
        self,
        results: Sequence[ConversationAnalytics],
        config: AnalyticsConfig,
        processing_run_id: int | None = None,
    ) -> int:
        run_id = super().write_run(results, config, processing_run_id)
        with self.conn:
            self.conn.execute(
                "UPDATE analytics_run SET analytics_version = ? WHERE id = ?",
                (ANALYTICS_VERSION, run_id),
            )
            self.conn.executemany(
                """INSERT INTO analytics_conversation_fingerprint(
                       analytics_run_id, conversation_id, source_fingerprint
                   ) VALUES (?, ?, ?)""",
                [
                    (run_id, result.conversation_id, result.source_fingerprint)
                    for result in results
                ],
            )
            self.conn.executemany(
                """INSERT INTO analytics_trend_summary(
                       analytics_run_id, conversation_id, participant_id, period_kind,
                       metric, window_periods, period_start, period_end, first_value,
                       last_value, slope_per_period, normalized_slope, percent_change,
                       direction, source_message_ids_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        run_id,
                        trend.conversation_id,
                        trend.participant_id,
                        trend.period_kind,
                        trend.metric,
                        trend.window_periods,
                        trend.period_start,
                        trend.period_end,
                        trend.first_value,
                        trend.last_value,
                        trend.slope_per_period,
                        trend.normalized_slope,
                        trend.percent_change,
                        trend.direction,
                        json.dumps(trend.source_message_ids),
                    )
                    for result in results
                    for trend in result.trend_summaries
                ],
            )
        return run_id
