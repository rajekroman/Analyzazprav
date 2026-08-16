from __future__ import annotations

from typing import Sequence

from .config import AnalyticsConfig
from .models import ConversationAnalytics
from .store_v6 import AnalyticsStore as V6AnalyticsStore


class AnalyticsStore(V6AnalyticsStore):
    """Persistence guard for the integrated membership-scoped A3 contract.

    A3 session ids are scoped by `processing_run_id`. A4 stores the processing
    run once in `analytics_run` and validates every referenced session before
    persistence. This keeps the schema normalized while preserving exact A3
    provenance.
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
        if not self._object_exists("table", "analytics_response_latency"):
            return True
        # Old draft A4 referenced conversation_session(id) directly. Integrated
        # A3 uses the composite identity (processing_run_id, id), so any direct
        # session FK marks a reproducible A4 schema that must be rebuilt.
        foreign_keys = self.conn.execute(
            "PRAGMA foreign_key_list(analytics_response_latency)"
        ).fetchall()
        return any(str(row[2]) == "conversation_session" for row in foreign_keys)

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

    def _validate_session_provenance(
        self,
        results: Sequence[ConversationAnalytics],
        processing_run_id: int,
    ) -> None:
        references: set[tuple[int, int]] = set()
        for result in results:
            references.update(
                (sample.conversation_id, sample.session_id)
                for sample in result.response_samples
            )
            references.update(
                (event.conversation_id, event.session_id)
                for event in result.conflicts
            )
            for event in result.silence_events:
                references.add((event.conversation_id, event.previous_session_id))
                references.add((event.conversation_id, event.next_session_id))
        if not references:
            return

        session_columns = {
            str(row[1])
            for row in self.conn.execute("PRAGMA table_info(conversation_session)")
        }
        scoped = "processing_run_id" in session_columns
        missing: list[tuple[int, int]] = []
        for conversation_id, session_id in sorted(references):
            if scoped:
                row = self.conn.execute(
                    """SELECT 1 FROM conversation_session
                       WHERE processing_run_id=? AND id=? AND conversation_id=?""",
                    (processing_run_id, session_id, conversation_id),
                ).fetchone()
            else:
                # Compatibility path for small isolated A4 fixtures only.
                row = self.conn.execute(
                    "SELECT 1 FROM conversation_session WHERE id=? AND conversation_id=?",
                    (session_id, conversation_id),
                ).fetchone()
            if row is None:
                missing.append((conversation_id, session_id))

        if missing:
            raise RuntimeError(
                "A4 session provenance does not exist in selected A3 processing run: "
                f"{missing}"
            )

    def write_run(
        self,
        results: Sequence[ConversationAnalytics],
        config: AnalyticsConfig,
        processing_run_id: int | None = None,
    ) -> int:
        processing_id = processing_run_id or self.latest_processing_run_id()
        self._validate_session_provenance(results, processing_id)
        return super().write_run(results, config, processing_id)
