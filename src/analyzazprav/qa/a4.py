from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import json
from math import floor
from pathlib import Path
import sqlite3
from statistics import mean, median, pstdev
from typing import Any, Iterable, Sequence

from .staging import STATUS_FAIL, STATUS_PASS, STATUS_WARNING


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _close(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return abs(float(left) - float(right)) <= tolerance


def _float_key(value: Any) -> float | None:
    return None if value is None else round(float(value), 9)


def _percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _marker_hits(text: str, markers: Sequence[str]) -> int:
    folded = text.casefold()
    return sum(folded.count(str(marker).casefold()) for marker in markers if marker)


def _date_text(year: Any, month: Any, day: Any) -> str | None:
    if year is None or month is None or day is None:
        return None
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _period_date(row: dict[str, Any]) -> str | None:
    return row["local_date"] or row["utc_date"]


def _date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _load_source_rows(
    conn: sqlite3.Connection, processing_run_id: int
) -> dict[int, list[dict[str, Any]]]:
    rows = conn.execute(
        """SELECT am.membership_id,
                  am.id AS message_id,
                  am.conversation_id,
                  am.sender_id,
                  am.sent_at_utc_us,
                  pm.session_id,
                  pm.sequence_number,
                  pm.text_clean,
                  pm.word_count,
                  pm.char_count,
                  pm.question_mark_count,
                  pm.exclamation_mark_count,
                  pm.utc_year, pm.utc_month, pm.utc_day, pm.utc_weekday, pm.utc_hour,
                  pm.local_year, pm.local_month, pm.local_day, pm.local_weekday, pm.local_hour
           FROM analysis_messages AS am
           JOIN processed_message AS pm
             ON pm.processing_run_id=?
            AND pm.membership_id=am.membership_id
            AND pm.message_id=am.id
            AND pm.conversation_id=am.conversation_id
           ORDER BY am.conversation_id, pm.sequence_number, am.membership_id""",
        (processing_run_id,),
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item = {
            "membership_id": int(row[0]),
            "message_id": int(row[1]),
            "conversation_id": int(row[2]),
            "sender_id": None if row[3] is None else int(row[3]),
            "timestamp_us": None if row[4] is None else int(row[4]),
            "session_id": int(row[5]),
            "sequence_number": int(row[6]),
            "text_clean": str(row[7] or ""),
            "word_count": int(row[8]),
            "character_count": int(row[9]),
            "question_mark_count": int(row[10]),
            "exclamation_mark_count": int(row[11]),
            "utc_date": _date_text(row[12], row[13], row[14]),
            "utc_weekday": None if row[15] is None else int(row[15]),
            "utc_hour": None if row[16] is None else int(row[16]),
            "local_date": _date_text(row[17], row[18], row[19]),
            "local_weekday": None if row[20] is None else int(row[20]),
            "local_hour": None if row[21] is None else int(row[21]),
        }
        grouped[item["conversation_id"]].append(item)
    return grouped


def _build_turns(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []

    def flush() -> None:
        if not batch:
            return
        turns.append(
            {
                "turn_id": len(turns) + 1,
                "conversation_id": batch[0]["conversation_id"],
                "session_id": batch[0]["session_id"],
                "participant_id": batch[0]["sender_id"],
                "start_us": batch[0]["timestamp_us"],
                "end_us": batch[-1]["timestamp_us"],
                "message_ids": tuple(item["message_id"] for item in batch),
                "word_count": sum(item["word_count"] for item in batch),
                "character_count": sum(item["character_count"] for item in batch),
            }
        )

    for row in rows:
        if not batch:
            batch.append(row)
            continue
        if (
            row["session_id"] == batch[-1]["session_id"]
            and row["sender_id"] == batch[-1]["sender_id"]
        ):
            batch.append(row)
        else:
            flush()
            batch = [row]
    flush()
    return turns


def _responses(turns: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for previous, current in zip(turns, turns[1:]):
        if previous["session_id"] != current["session_id"]:
            continue
        if previous["participant_id"] is None or current["participant_id"] is None:
            continue
        if previous["participant_id"] == current["participant_id"]:
            continue
        latency = None
        if previous["end_us"] is not None and current["start_us"] is not None:
            delta = int(current["start_us"]) - int(previous["end_us"])
            if delta >= 0:
                latency = delta / 1_000_000
        result.append(
            {
                "conversation_id": int(current["conversation_id"]),
                "session_id": int(current["session_id"]),
                "from_participant_id": int(previous["participant_id"]),
                "responder_id": int(current["participant_id"]),
                "previous_turn_id": int(previous["turn_id"]),
                "response_turn_id": int(current["turn_id"]),
                "latency_seconds": latency,
                "response_effort_ratio": float(current["word_count"]) / max(1, int(previous["word_count"])),
            }
        )
    return result


def _unanswered(turns: Sequence[dict[str, Any]]) -> Counter[int]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for turn in turns:
        grouped[int(turn["session_id"])].append(turn)
    result: Counter[int] = Counter()
    for session_turns in grouped.values():
        for index, turn in enumerate(session_turns):
            participant = turn["participant_id"]
            if participant is None:
                continue
            answered = any(
                later["participant_id"] is not None
                and later["participant_id"] != participant
                for later in session_turns[index + 1 :]
            )
            if not answered:
                result[int(participant)] += 1
    return result


def _reciprocity(metrics: dict[int, dict[str, Any]], field: str) -> float | None:
    if len(metrics) != 2:
        return None
    values = [float(item.get(field) or 0) for item in metrics.values()]
    if max(values, default=0.0) == 0.0:
        return 1.0
    return min(values) / max(values)


def _participant_metrics(
    rows: Sequence[dict[str, Any]],
    turns: Sequence[dict[str, Any]],
    responses: Sequence[dict[str, Any]],
    config: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    participants = sorted({int(row["sender_id"]) for row in rows if row["sender_id"] is not None})
    message_count = Counter(int(row["sender_id"]) for row in rows if row["sender_id"] is not None)
    word_count: Counter[int] = Counter()
    character_count: Counter[int] = Counter()
    question_count: Counter[int] = Counter()
    exclamation_count: Counter[int] = Counter()
    affection_count: Counter[int] = Counter()
    negative_count: Counter[int] = Counter()
    active_days: dict[int, set[Any]] = defaultdict(set)
    affection_markers = tuple(config.get("affection_markers") or ())
    negative_markers = tuple(config.get("negative_markers") or ())

    for row in rows:
        if row["sender_id"] is None:
            continue
        pid = int(row["sender_id"])
        word_count[pid] += int(row["word_count"])
        character_count[pid] += int(row["character_count"])
        question_count[pid] += int(row["question_mark_count"])
        exclamation_count[pid] += int(row["exclamation_mark_count"])
        affection_count[pid] += _marker_hits(row["text_clean"], affection_markers)
        negative_count[pid] += _marker_hits(row["text_clean"], negative_markers)
        period = _period_date(row)
        if period is not None:
            active_days[pid].add(period)
        elif row["timestamp_us"] is not None:
            active_days[pid].add(int(row["timestamp_us"]) // 86_400_000_000)

    turn_count = Counter(
        int(turn["participant_id"])
        for turn in turns
        if turn["participant_id"] is not None
    )
    initiations: Counter[int] = Counter()
    seen_sessions: set[int] = set()
    known_initiated_sessions = 0
    for turn in turns:
        session_id = int(turn["session_id"])
        if session_id in seen_sessions:
            continue
        seen_sessions.add(session_id)
        if turn["participant_id"] is not None:
            pid = int(turn["participant_id"])
            initiations[pid] += 1
            known_initiated_sessions += 1

    unanswered = _unanswered(turns)
    response_turn_count: Counter[int] = Counter()
    latency_by_responder: dict[int, list[float]] = defaultdict(list)
    effort_by_responder: dict[int, list[float]] = defaultdict(list)
    for sample in responses:
        pid = int(sample["responder_id"])
        response_turn_count[pid] += 1
        if sample["latency_seconds"] is not None:
            latency_by_responder[pid].append(float(sample["latency_seconds"]))
        effort_by_responder[pid].append(float(sample["response_effort_ratio"]))

    known_turns = max(1, sum(turn_count.values()))
    responsiveness_reference = float(config.get("responsiveness_reference_seconds", 6 * 60 * 60))
    result: dict[int, dict[str, Any]] = {}
    for pid in participants:
        latencies = latency_by_responder.get(pid, [])
        efforts = effort_by_responder.get(pid, [])
        median_latency = median(latencies) if latencies else None
        initiation_share = initiations[pid] / max(1, known_initiated_sessions)
        activity_share = turn_count[pid] / known_turns
        responsiveness = (
            1.0 - _clamp(float(median_latency) / responsiveness_reference)
            if median_latency is not None
            else 0.0
        )
        question_rate = _clamp(question_count[pid] / max(1, turn_count[pid]))
        affection_rate = _clamp(affection_count[pid] / max(1, turn_count[pid]))
        engagement = 100.0 * (
            float(config.get("engagement_activity_weight", 0.25)) * activity_share
            + float(config.get("engagement_initiation_weight", 0.20)) * initiation_share
            + float(config.get("engagement_responsiveness_weight", 0.25)) * responsiveness
            + float(config.get("engagement_question_weight", 0.15)) * question_rate
            + float(config.get("engagement_affection_weight", 0.15)) * affection_rate
        )
        result[pid] = {
            "message_count": message_count[pid],
            "word_count": word_count[pid],
            "character_count": character_count[pid],
            "active_days": len(active_days[pid]),
            "turn_count": turn_count[pid],
            "initiations": initiations[pid],
            "initiation_share": initiation_share,
            "question_count": question_count[pid],
            "exclamation_count": exclamation_count[pid],
            "affection_marker_count": affection_count[pid],
            "negative_marker_count": negative_count[pid],
            "response_turn_count": response_turn_count[pid],
            "latency_sample_count": len(latencies),
            "unanswered_turn_count": unanswered[pid],
            "mean_response_latency_seconds": mean(latencies) if latencies else None,
            "median_response_latency_seconds": median_latency,
            "p25_response_latency_seconds": _percentile(latencies, 0.25),
            "p75_response_latency_seconds": _percentile(latencies, 0.75),
            "p90_response_latency_seconds": _percentile(latencies, 0.90),
            "median_response_effort_ratio": median(efforts) if efforts else None,
            "engagement_score": round(engagement, 6),
        }
    return result


def _daily_metrics(
    rows: Sequence[dict[str, Any]],
    turns: Sequence[dict[str, Any]],
    responses: Sequence[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    dated = [row for row in rows if row["sender_id"] is not None and _period_date(row)]
    if not dated:
        return []
    participants = sorted({int(row["sender_id"]) for row in dated})
    dates = [date.fromisoformat(str(_period_date(row))) for row in dated]
    start_date, end_date = min(dates), max(dates)
    by_key: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    participant_basis: dict[int, str] = {}
    by_message_id = {int(row["message_id"]): row for row in rows}
    affection_markers = tuple(config.get("affection_markers") or ())
    negative_markers = tuple(config.get("negative_markers") or ())

    for row in dated:
        pid = int(row["sender_id"])
        period = str(_period_date(row))
        by_key[(pid, period)].append(row)
        if row["local_date"] is not None:
            participant_basis[pid] = "local"
        else:
            participant_basis.setdefault(pid, "utc")

    turn_date: dict[int, str] = {}
    for turn in turns:
        if turn["participant_id"] is None or not turn["message_ids"]:
            continue
        first = by_message_id.get(int(turn["message_ids"][0]))
        if first is not None and _period_date(first):
            turn_date[int(turn["turn_id"])] = str(_period_date(first))

    turn_counts: Counter[tuple[int, str]] = Counter()
    initiations: Counter[tuple[int, str]] = Counter()
    seen_sessions: set[int] = set()
    for turn in turns:
        session_id = int(turn["session_id"])
        first_turn = session_id not in seen_sessions
        if first_turn:
            seen_sessions.add(session_id)
        if turn["participant_id"] is None:
            continue
        pid = int(turn["participant_id"])
        period = turn_date.get(int(turn["turn_id"]))
        if period:
            turn_counts[(pid, period)] += 1
        if first_turn and period:
            initiations[(pid, period)] += 1

    latency_by_key: dict[tuple[int, str], list[float]] = defaultdict(list)
    effort_by_key: dict[tuple[int, str], list[float]] = defaultdict(list)
    for sample in responses:
        period = turn_date.get(int(sample["response_turn_id"]))
        if not period:
            continue
        key = (int(sample["responder_id"]), period)
        if sample["latency_seconds"] is not None:
            latency_by_key[key].append(float(sample["latency_seconds"]))
        effort_by_key[key].append(float(sample["response_effort_ratio"]))

    result: list[dict[str, Any]] = []
    for pid in participants:
        for day in _date_range(start_date, end_date):
            period = day.isoformat()
            source = by_key.get((pid, period), [])
            if source:
                basis = "local" if any(row["local_date"] == period for row in source) else "utc"
            else:
                basis = participant_basis.get(pid, "utc")
            latencies = latency_by_key.get((pid, period), [])
            efforts = effort_by_key.get((pid, period), [])
            result.append(
                {
                    "participant_id": pid,
                    "period_date": period,
                    "date_basis": basis,
                    "message_count": len(source),
                    "word_count": sum(int(row["word_count"]) for row in source),
                    "turn_count": turn_counts[(pid, period)],
                    "initiations": initiations[(pid, period)],
                    "question_count": sum(int(row["question_mark_count"]) for row in source),
                    "affection_marker_count": sum(
                        _marker_hits(row["text_clean"], affection_markers) for row in source
                    ),
                    "negative_marker_count": sum(
                        _marker_hits(row["text_clean"], negative_markers) for row in source
                    ),
                    "median_response_latency_seconds": median(latencies) if latencies else None,
                    "median_response_effort_ratio": median(efforts) if efforts else None,
                    "source_message_ids": tuple(int(row["message_id"]) for row in source),
                }
            )
    return result


def _robust_z(value: float, baseline: Sequence[float]) -> tuple[float, float]:
    center = median(baseline)
    deviations = [abs(item - center) for item in baseline]
    mad = median(deviations)
    if mad > 0:
        return center, 0.67448975 * (value - center) / mad
    spread = pstdev(baseline)
    if spread > 0:
        return center, (value - center) / spread
    return center, (value - center) / max(1.0, abs(center) * 0.1)


def _change_points(
    conversation_id: int,
    daily: Sequence[dict[str, Any]],
    config: dict[str, Any],
) -> list[tuple[Any, ...]]:
    metrics = (
        "message_count",
        "word_count",
        "turn_count",
        "initiations",
        "question_count",
        "affection_marker_count",
        "negative_marker_count",
        "median_response_latency_seconds",
        "median_response_effort_ratio",
    )
    window = int(config.get("change_baseline_window_days", 28))
    minimum = int(config.get("change_min_baseline_days", 7))
    threshold = float(config.get("change_z_threshold", 2.5))
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in daily:
        grouped[int(row["participant_id"])].append(row)
    result: list[tuple[Any, ...]] = []
    for pid, participant_rows in grouped.items():
        participant_rows.sort(key=lambda item: item["period_date"])
        for metric in metrics:
            history: list[float] = []
            for row in participant_rows:
                raw = row[metric]
                if raw is None:
                    continue
                value = float(raw)
                baseline = history[-window:]
                if len(baseline) >= minimum:
                    center, z_score = _robust_z(value, baseline)
                    if abs(z_score) >= threshold:
                        result.append(
                            (
                                conversation_id,
                                pid,
                                metric,
                                row["period_date"],
                                _float_key(value),
                                _float_key(center),
                                round(z_score, 6),
                                "increasing" if z_score > 0 else "decreasing",
                                tuple(row["source_message_ids"]),
                            )
                        )
                history.append(value)
    return result


def _response_key(sample: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(sample["conversation_id"]),
        int(sample["session_id"]),
        int(sample["from_participant_id"]),
        int(sample["responder_id"]),
        int(sample["previous_turn_id"]),
        int(sample["response_turn_id"]),
        _float_key(sample["latency_seconds"]),
        _float_key(sample["response_effort_ratio"]),
    )


def _actual_change_key(row: sqlite3.Row) -> tuple[Any, ...]:
    try:
        source_ids = tuple(int(value) for value in json.loads(row["source_message_ids_json"] or "[]"))
    except (json.JSONDecodeError, TypeError, ValueError):
        source_ids = ("__invalid_json__",)
    return (
        int(row["conversation_id"]),
        int(row["participant_id"]),
        str(row["metric"]),
        str(row["period_date"]),
        _float_key(row["value"]),
        _float_key(row["baseline_median"]),
        round(float(row["robust_z_score"]), 6),
        str(row["direction"]),
        source_ids,
    )


def validate_a4_metrics(database: str | Path) -> dict[str, Any]:
    """Independently recompute release-critical A4 metrics from A2/A3 data.

    This module intentionally does not import `analyzazprav.analytics`. It is an
    A7 oracle, not a second call into the implementation being validated.
    """

    database = Path(database)
    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    if not database.is_file():
        _issue(issues, "ERROR", "DATABASE_MISSING", str(database))
        return _finalize(database, issues, checks)

    try:
        conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        _issue(issues, "ERROR", "DATABASE_OPEN_FAILED", str(exc))
        return _finalize(database, issues, checks)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        required = {
            "processing_run",
            "processed_message",
            "analytics_run",
            "analytics_conversation_summary",
            "analytics_participant_summary",
            "analytics_response_latency",
            "analytics_daily_participant",
            "analytics_change_point",
        }
        present = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(required - present)
        if missing:
            _issue(issues, "ERROR", "A4_TABLES_MISSING", ", ".join(missing))
            return _finalize(database, issues, checks)

        view_present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='analysis_a4_latest_conversation_run'"
        ).fetchone()
        if view_present is None:
            _issue(issues, "ERROR", "A4_LATEST_VIEW_MISSING", "analysis_a4_latest_conversation_run")
            return _finalize(database, issues, checks)

        latest_a3_row = conn.execute(
            "SELECT id FROM processing_run WHERE status='completed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest_a3_row is None:
            _issue(issues, "ERROR", "A3_COMPLETED_RUN_MISSING", "no completed A3 run")
            return _finalize(database, issues, checks)
        latest_a3 = int(latest_a3_row[0])
        checks["latest_a3_processing_run_id"] = latest_a3

        source_by_conversation = _load_source_rows(conn, latest_a3)
        expected_conversations = set(source_by_conversation)
        latest_a4_rows = list(
            conn.execute(
                """SELECT latest.conversation_id,
                          latest.analytics_run_id,
                          ar.processing_run_id,
                          ar.config_json
                   FROM analysis_a4_latest_conversation_run AS latest
                   JOIN analytics_run AS ar ON ar.id=latest.analytics_run_id
                   ORDER BY latest.conversation_id"""
            )
        )
        actual_conversations = {int(row["conversation_id"]) for row in latest_a4_rows}
        checks["a3_conversation_count"] = len(expected_conversations)
        checks["a4_conversation_count"] = len(actual_conversations)
        if actual_conversations != expected_conversations:
            _issue(
                issues,
                "ERROR",
                "A4_CONVERSATION_COVERAGE_MISMATCH",
                f"missing={sorted(expected_conversations-actual_conversations)}, extra={sorted(actual_conversations-expected_conversations)}",
            )

        oracle_response_count = 0
        oracle_change_count = 0
        checked_participants = 0
        for state in latest_a4_rows:
            conversation_id = int(state["conversation_id"])
            analytics_run_id = int(state["analytics_run_id"])
            processing_run_id = int(state["processing_run_id"])
            if processing_run_id != latest_a3:
                _issue(
                    issues,
                    "ERROR",
                    "A4_STALE_A3_PROVENANCE",
                    f"conversation={conversation_id}, A4 processing_run={processing_run_id}, latest A3={latest_a3}",
                )
                continue
            try:
                config = json.loads(state["config_json"] or "{}")
            except json.JSONDecodeError as exc:
                _issue(issues, "ERROR", "A4_CONFIG_JSON_INVALID", f"conversation={conversation_id}: {exc}")
                continue
            if not isinstance(config, dict):
                _issue(issues, "ERROR", "A4_CONFIG_JSON_INVALID", f"conversation={conversation_id}: config is not object")
                continue

            rows = source_by_conversation.get(conversation_id, [])
            turns = _build_turns(rows)
            responses = _responses(turns)
            participants = _participant_metrics(rows, turns, responses, config)
            daily = _daily_metrics(rows, turns, responses, config)
            expected_changes = Counter(_change_points(conversation_id, daily, config))
            oracle_response_count += len(responses)
            oracle_change_count += sum(expected_changes.values())
            checked_participants += len(participants)

            expected_summary = {
                "source_message_count": len(rows),
                "known_sender_message_count": sum(row["sender_id"] is not None for row in rows),
                "unknown_sender_message_count": sum(row["sender_id"] is None for row in rows),
                "turn_count": len(turns),
                "session_count": len({int(row["session_id"]) for row in rows}),
                "message_reciprocity": _reciprocity(participants, "message_count"),
                "word_reciprocity": _reciprocity(participants, "word_count"),
                "turn_reciprocity": _reciprocity(participants, "turn_count"),
                "initiation_reciprocity": _reciprocity(participants, "initiations"),
            }
            actual_summary = conn.execute(
                "SELECT * FROM analytics_conversation_summary WHERE analytics_run_id=? AND conversation_id=?",
                (analytics_run_id, conversation_id),
            ).fetchone()
            if actual_summary is None:
                _issue(issues, "ERROR", "A4_CONVERSATION_SUMMARY_MISSING", f"conversation={conversation_id}")
            else:
                for field, expected in expected_summary.items():
                    actual = actual_summary[field]
                    same = _close(actual, expected) if isinstance(expected, float) or expected is None else actual == expected
                    if not same:
                        _issue(
                            issues,
                            "ERROR",
                            "A4_CONVERSATION_SUMMARY_MISMATCH",
                            f"conversation={conversation_id}, field={field}, expected={expected!r}, actual={actual!r}",
                        )

            actual_participant_rows = {
                int(row["participant_id"]): row
                for row in conn.execute(
                    "SELECT * FROM analytics_participant_summary WHERE analytics_run_id=? AND conversation_id=?",
                    (analytics_run_id, conversation_id),
                )
            }
            if set(actual_participant_rows) != set(participants):
                _issue(
                    issues,
                    "ERROR",
                    "A4_PARTICIPANT_SET_MISMATCH",
                    f"conversation={conversation_id}, expected={sorted(participants)}, actual={sorted(actual_participant_rows)}",
                )
            participant_fields = (
                "message_count", "word_count", "character_count", "active_days",
                "turn_count", "initiations", "initiation_share", "question_count",
                "exclamation_count", "affection_marker_count", "negative_marker_count",
                "response_turn_count", "latency_sample_count", "unanswered_turn_count",
                "mean_response_latency_seconds", "median_response_latency_seconds",
                "p25_response_latency_seconds", "p75_response_latency_seconds",
                "p90_response_latency_seconds", "median_response_effort_ratio",
                "engagement_score",
            )
            for pid, expected in participants.items():
                actual = actual_participant_rows.get(pid)
                if actual is None:
                    continue
                for field in participant_fields:
                    expected_value = expected[field]
                    actual_value = actual[field]
                    if isinstance(expected_value, float) or expected_value is None:
                        same = _close(actual_value, expected_value, 1e-6 if field == "engagement_score" else 1e-9)
                    else:
                        same = actual_value == expected_value
                    if not same:
                        _issue(
                            issues,
                            "ERROR",
                            "A4_PARTICIPANT_METRIC_MISMATCH",
                            f"conversation={conversation_id}, participant={pid}, field={field}, expected={expected_value!r}, actual={actual_value!r}",
                        )

            expected_response_rows = Counter(_response_key(sample) for sample in responses)
            actual_response_rows = Counter(
                (
                    int(row["conversation_id"]), int(row["session_id"]),
                    int(row["from_participant_id"]), int(row["responder_id"]),
                    int(row["previous_turn_id"]), int(row["response_turn_id"]),
                    _float_key(row["latency_seconds"]), _float_key(row["response_effort_ratio"]),
                )
                for row in conn.execute(
                    "SELECT * FROM analytics_response_latency WHERE analytics_run_id=? AND conversation_id=?",
                    (analytics_run_id, conversation_id),
                )
            )
            if actual_response_rows != expected_response_rows:
                _issue(
                    issues,
                    "ERROR",
                    "A4_RESPONSE_SAMPLE_MISMATCH",
                    f"conversation={conversation_id}, expected={list(expected_response_rows.elements())[:5]}, actual={list(actual_response_rows.elements())[:5]}",
                )

            expected_daily = {
                (int(row["participant_id"]), str(row["period_date"])): row for row in daily
            }
            actual_daily = {
                (int(row["participant_id"]), str(row["period_date"])): row
                for row in conn.execute(
                    "SELECT * FROM analytics_daily_participant WHERE analytics_run_id=? AND conversation_id=?",
                    (analytics_run_id, conversation_id),
                )
            }
            if set(expected_daily) != set(actual_daily):
                _issue(
                    issues,
                    "ERROR",
                    "A4_DAILY_SET_MISMATCH",
                    f"conversation={conversation_id}, expected_rows={len(expected_daily)}, actual_rows={len(actual_daily)}",
                )
            daily_fields = (
                "date_basis", "message_count", "word_count", "turn_count", "initiations",
                "question_count", "affection_marker_count", "negative_marker_count",
                "median_response_latency_seconds", "median_response_effort_ratio",
            )
            for key, expected in expected_daily.items():
                actual = actual_daily.get(key)
                if actual is None:
                    continue
                for field in daily_fields:
                    expected_value = expected[field]
                    actual_value = actual[field]
                    if isinstance(expected_value, float) or expected_value is None:
                        same = _close(actual_value, expected_value)
                    else:
                        same = actual_value == expected_value
                    if not same:
                        _issue(
                            issues,
                            "ERROR",
                            "A4_DAILY_METRIC_MISMATCH",
                            f"conversation={conversation_id}, key={key}, field={field}, expected={expected_value!r}, actual={actual_value!r}",
                        )
                try:
                    actual_ids = tuple(int(value) for value in json.loads(actual["source_message_ids_json"] or "[]"))
                except (json.JSONDecodeError, TypeError, ValueError):
                    actual_ids = ("__invalid_json__",)
                if actual_ids != tuple(expected["source_message_ids"]):
                    _issue(
                        issues,
                        "ERROR",
                        "A4_DAILY_EVIDENCE_MISMATCH",
                        f"conversation={conversation_id}, key={key}, expected={expected['source_message_ids']}, actual={actual_ids}",
                    )

            actual_changes = Counter(
                _actual_change_key(row)
                for row in conn.execute(
                    "SELECT * FROM analytics_change_point WHERE analytics_run_id=? AND conversation_id=?",
                    (analytics_run_id, conversation_id),
                )
            )
            if actual_changes != expected_changes:
                missing_changes = list((expected_changes - actual_changes).elements())[:5]
                extra_changes = list((actual_changes - expected_changes).elements())[:5]
                _issue(
                    issues,
                    "ERROR",
                    "A4_CHANGE_POINT_MISMATCH",
                    f"conversation={conversation_id}, missing={missing_changes}, extra={extra_changes}",
                )

        checks["oracle_participant_count"] = checked_participants
        checks["oracle_response_sample_count"] = oracle_response_count
        checks["oracle_change_point_count"] = oracle_change_count
        checks["sqlite_integrity"] = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        checks["foreign_key_error_count"] = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        if checks["sqlite_integrity"] != "ok":
            _issue(issues, "ERROR", "SQLITE_INTEGRITY_FAILED", checks["sqlite_integrity"])
        if checks["foreign_key_error_count"]:
            _issue(issues, "ERROR", "FOREIGN_KEY_ERRORS", str(checks["foreign_key_error_count"]))
    except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        _issue(issues, "ERROR", "A4_ORACLE_QUERY_FAILED", str(exc))
    finally:
        conn.close()
    return _finalize(database, issues, checks)


def _finalize(database: Path, issues: list[dict[str, Any]], checks: dict[str, Any]) -> dict[str, Any]:
    errors = sum(issue.get("severity") == "ERROR" for issue in issues)
    warnings = sum(issue.get("severity") == "WARNING" for issue in issues)
    status = STATUS_FAIL if errors else STATUS_WARNING if warnings else STATUS_PASS
    checks["oracle_ok"] = errors == 0
    return {
        "schema_version": 1,
        "status": status,
        "database": str(database),
        "checks": checks,
        "counts": {"errors": int(errors), "warnings": int(warnings)},
        "issues": issues,
    }
