from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .data import DataSourceError, _connect_read_only, _objects


@dataclass(frozen=True)
class A4ConversationMetrics:
    daily: pd.DataFrame
    participants: pd.DataFrame
    responses: pd.DataFrame
    conversation: pd.DataFrame

    @property
    def available(self) -> bool:
        return not self.conversation.empty or not self.daily.empty or not self.participants.empty or not self.responses.empty


def empty_a4_metrics() -> A4ConversationMetrics:
    return A4ConversationMetrics(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())


def load_a4_conversation_metrics(path: str | Path, conversation_id: str) -> A4ConversationMetrics:
    """Read A4 latest-run metrics for one conversation without recomputation."""

    try:
        with _connect_read_only(path) as conn:
            objects = set(_objects(conn))
            required = {
                "analysis_a4_daily",
                "analysis_a4_participants",
                "analysis_a4_responses",
                "analysis_a4_conversations",
            }
            if not required.intersection(objects):
                return empty_a4_metrics()

            labels = pd.DataFrame(columns=["participant_id", "sender"])
            if "analysis_messages" in objects:
                labels = pd.read_sql_query(
                    "SELECT DISTINCT sender_id AS participant_id, sender_name AS sender "
                    "FROM analysis_messages WHERE sender_id IS NOT NULL",
                    conn,
                )

            def read(view: str) -> pd.DataFrame:
                if view not in objects:
                    return pd.DataFrame()
                return pd.read_sql_query(
                    f"SELECT * FROM {view} WHERE CAST(conversation_id AS TEXT) = ?",
                    conn,
                    params=(str(conversation_id),),
                )

            daily = read("analysis_a4_daily")
            participants = read("analysis_a4_participants")
            responses = read("analysis_a4_responses")
            conversation = read("analysis_a4_conversations")
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Chyba při čtení A4 metrik: {exc}") from exc

    if not labels.empty:
        labels["participant_id"] = labels["participant_id"].astype(str)
        for frame in (daily, participants):
            if not frame.empty and "participant_id" in frame:
                frame["participant_id"] = frame["participant_id"].astype(str)
                frame = frame.merge(labels, on="participant_id", how="left")
                if frame is daily:
                    daily = frame
                else:
                    participants = frame
        if not responses.empty:
            for source_col, target_col in (("responder_id", "responder"), ("from_participant_id", "from_participant")):
                if source_col in responses:
                    responses[source_col] = responses[source_col].astype(str)
                    lookup = labels.rename(columns={"participant_id": source_col, "sender": target_col})
                    responses = responses.merge(lookup, on=source_col, how="left")

    if not daily.empty and "period_date" in daily:
        daily["period_date"] = pd.to_datetime(daily["period_date"], errors="coerce")
    return A4ConversationMetrics(
        daily=daily.reset_index(drop=True),
        participants=participants.reset_index(drop=True),
        responses=responses.reset_index(drop=True),
        conversation=conversation.reset_index(drop=True),
    )
