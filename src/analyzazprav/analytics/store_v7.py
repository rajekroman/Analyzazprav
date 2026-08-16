from __future__ import annotations

from .store_v6 import AnalyticsStore as V6AnalyticsStore


class AnalyticsStore(V6AnalyticsStore):
    """A4 persistence guard for the integrated membership-scoped A3 contract.

    Older draft A4 schemas referenced `conversation_session(id)` as if session
    ids were globally unique. Integrated A3 scopes sessions by processing run.
    If an old A4 derived schema is found, rebuild only A4 reproducible data and
    leave all A1-A3 source/normalized/processed tables untouched.
    """

    def _object_exists(self, object_type: str, name: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
                (object_type, name),
            ).fetchone()
            is not None
        )

    def _needs_integrated_schema_rebuild(self) -> bool:
        if not self._object_exists("table", "analytics_run"):
            return False
        return not (
            self._object_exists("trigger", "a4_validate_response_session")
            and self._object_exists("trigger", "a4_validate_silence_sessions")
            and self._object_exists("trigger", "a4_validate_event_session")
        )

    def _drop_all_a4_derived(self) -> None:
        views = (
            "analysis_a4_topic_period_reconciliation",
            "analysis_a4_topic_periods",
            "analysis_a4_topic_evidence",
            "analysis_a4_topics",
            "analysis_a4_events",
            "analysis_a4_changes",
            "analysis_a4_trends",
            "analysis_a4_regimes",
            "analysis_a4_engagement_signals",
            "analysis_a4_periods",
            "analysis_a4_daily",
            "analysis_a4_silences",
            "analysis_a4_time_buckets",
            "analysis_a4_responses",
            "analysis_a4_participants",
            "analysis_a4_conversations",
            "analysis_a4_latest_conversation_run",
            "analysis_a4_latest_run",
        )
        tables = (
            "analytics_topic_evidence",
            "analytics_topic_candidate",
            "analytics_conversation_state_v6",
            "analytics_trend_summary",
            "analytics_conversation_fingerprint",
            "analytics_event",
            "analytics_dyadic_regime",
            "analytics_engagement_signal",
            "analytics_period_participant",
            "analytics_change_point",
            "analytics_daily_participant",
            "analytics_silence_event",
            "analytics_time_bucket",
            "analytics_response_latency",
            "analytics_participant_summary",
            "analytics_conversation_summary",
            "analytics_run",
        )
        for trigger in (
            "a4_validate_response_session",
            "a4_validate_silence_sessions",
            "a4_validate_event_session",
        ):
            self.conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        for view in views:
            self.conn.execute(f"DROP VIEW IF EXISTS {view}")
        for table in tables:
            self.conn.execute(f"DROP TABLE IF EXISTS {table}")

    def initialize(self) -> None:
        with self.conn:
            if self._needs_integrated_schema_rebuild():
                self._drop_all_a4_derived()
        super().initialize()
