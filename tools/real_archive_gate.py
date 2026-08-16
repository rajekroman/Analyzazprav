from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any, Mapping, Sequence


class RealArchiveGateError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RealArchiveGateError("JSON_OBJECT_REQUIRED", f"Expected JSON object: {path}")
    return value


def _json_from_stdout(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _normalize_match(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _known_source_labels(metadata: Mapping[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}

    def collect(value: Mapping[str, Any]) -> None:
        for key in (
            "display_name",
            "chat_identifier",
            "guid",
            "room_name",
            "group_id",
            "account_login",
        ):
            item = value.get(key)
            if item not in (None, "") and key not in labels:
                labels[key] = str(item)
        nested = value.get("metadata")
        if isinstance(nested, Mapping):
            collect(nested)

    collect(metadata)
    return labels


def _ro_connect(database: str | Path) -> sqlite3.Connection:
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise RealArchiveGateError("DATABASE_NOT_FOUND", f"Database does not exist: {path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def conversation_inventory(database: str | Path) -> list[dict[str, Any]]:
    """Return an auditable, message-text-free inventory of canonical conversations."""

    result: list[dict[str, Any]] = []
    with _ro_connect(database) as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.canonical_key, c.conversation_type, c.service,
                   COUNT(am.membership_id) AS membership_count,
                   SUM(CASE WHEN am.sent_at_utc_us IS NULL THEN 1 ELSE 0 END) AS unknown_timestamp_count,
                   MIN(am.sent_at_utc_us) AS first_at_utc_us,
                   MAX(am.sent_at_utc_us) AS last_at_utc_us
            FROM conversation c
            LEFT JOIN analysis_messages am ON am.conversation_id=c.id
            GROUP BY c.id, c.title, c.canonical_key, c.conversation_type, c.service
            ORDER BY membership_count DESC, c.id
            """
        ).fetchall()
        for row in rows:
            conversation_id = int(row["id"])
            participant_rows = conn.execute(
                """
                SELECT p.id AS participant_id, p.canonical_name, p.is_self,
                       pi.identity_type, pi.normalized_value, pi.original_value
                FROM conversation_participant cp
                JOIN participant p ON p.id=cp.participant_id
                LEFT JOIN participant_identity pi ON pi.participant_id=p.id
                WHERE cp.conversation_id=?
                ORDER BY p.is_self DESC, p.id, pi.id
                """,
                (conversation_id,),
            ).fetchall()
            participants: dict[int, dict[str, Any]] = {}
            for participant in participant_rows:
                participant_id = int(participant["participant_id"])
                entry = participants.setdefault(
                    participant_id,
                    {
                        "participant_id": participant_id,
                        "canonical_name": participant["canonical_name"],
                        "is_self": bool(participant["is_self"]),
                        "identities": [],
                    },
                )
                if participant["identity_type"] is not None:
                    entry["identities"].append(
                        {
                            "type": str(participant["identity_type"]),
                            "normalized_value": participant["normalized_value"],
                            "original_value": participant["original_value"],
                        }
                    )

            source_rows = conn.execute(
                """
                SELECT source_type, source_conversation_id, metadata_json
                FROM conversation_source
                WHERE conversation_id=?
                ORDER BY id
                """,
                (conversation_id,),
            ).fetchall()
            sources: list[dict[str, Any]] = []
            for source in source_rows:
                try:
                    metadata = json.loads(str(source["metadata_json"] or "{}"))
                except json.JSONDecodeError:
                    metadata = {}
                if not isinstance(metadata, dict):
                    metadata = {}
                sources.append(
                    {
                        "source_type": str(source["source_type"]),
                        "source_conversation_id": str(source["source_conversation_id"]),
                        "labels": _known_source_labels(metadata),
                    }
                )

            result.append(
                {
                    "conversation_id": conversation_id,
                    "title": row["title"],
                    "canonical_key": row["canonical_key"],
                    "conversation_type": str(row["conversation_type"] or "unknown"),
                    "service": row["service"],
                    "membership_count": int(row["membership_count"] or 0),
                    "unknown_timestamp_count": int(row["unknown_timestamp_count"] or 0),
                    "first_at_utc_us": row["first_at_utc_us"],
                    "last_at_utc_us": row["last_at_utc_us"],
                    "participants": list(participants.values()),
                    "sources": sources,
                }
            )
    return result


def _exact_target_reasons(item: Mapping[str, Any], target: str) -> list[str]:
    wanted = _normalize_match(target)
    if not wanted:
        return []
    reasons: list[str] = []
    for field in ("title", "canonical_key"):
        value = item.get(field)
        if value not in (None, "") and _normalize_match(value) == wanted:
            reasons.append(field)

    participants = item.get("participants") or []
    if isinstance(participants, list):
        for participant in participants:
            if not isinstance(participant, Mapping):
                continue
            if _normalize_match(participant.get("canonical_name")) == wanted:
                reasons.append(f"participant:{participant.get('participant_id')}:canonical_name")
            identities = participant.get("identities") or []
            if not isinstance(identities, list):
                continue
            for identity in identities:
                if not isinstance(identity, Mapping):
                    continue
                for field in ("normalized_value", "original_value"):
                    if _normalize_match(identity.get(field)) == wanted:
                        reasons.append(
                            f"participant:{participant.get('participant_id')}:identity:{identity.get('type')}:{field}"
                        )

    sources = item.get("sources") or []
    if isinstance(sources, list):
        for index, source in enumerate(sources):
            if not isinstance(source, Mapping):
                continue
            if _normalize_match(source.get("source_conversation_id")) == wanted:
                reasons.append(f"source:{index}:source_conversation_id")
            labels = source.get("labels") or {}
            if isinstance(labels, Mapping):
                for key, value in labels.items():
                    if _normalize_match(value) == wanted:
                        reasons.append(f"source:{index}:{key}")
    return sorted(set(reasons))


def resolve_conversation(
    inventory: Sequence[Mapping[str, Any]],
    *,
    target: str | None = None,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    """Resolve one conversation without fuzzy/substring auto-selection."""

    if (target is None) == (conversation_id is None):
        raise RealArchiveGateError(
            "TARGET_SELECTOR_INVALID",
            "Provide exactly one of target or conversation_id.",
        )
    if conversation_id is not None:
        exact = [item for item in inventory if int(item["conversation_id"]) == int(conversation_id)]
        if len(exact) != 1:
            raise RealArchiveGateError(
                "CONVERSATION_ID_NOT_FOUND",
                f"conversation_id={conversation_id} does not exist in canonical inventory.",
            )
        return {
            "conversation_id": int(conversation_id),
            "selector": "conversation_id",
            "target": None,
            "match_reasons": ["explicit_conversation_id"],
        }

    assert target is not None
    matches: list[tuple[Mapping[str, Any], list[str]]] = []
    for item in inventory:
        reasons = _exact_target_reasons(item, target)
        if reasons:
            matches.append((item, reasons))
    if not matches:
        raise RealArchiveGateError(
            "TARGET_NOT_RESOLVED",
            f"Target {target!r} has no exact canonical/source identity match. Use a conversation_id from the inventory instead of guessing.",
        )
    if len(matches) > 1:
        ids = [int(item["conversation_id"]) for item, _ in matches]
        raise RealArchiveGateError(
            "TARGET_AMBIGUOUS",
            f"Target {target!r} matches multiple conversations: {ids}. Use explicit --conversation-id.",
        )
    item, reasons = matches[0]
    return {
        "conversation_id": int(item["conversation_id"]),
        "selector": "exact_target",
        "target": target,
        "match_reasons": reasons,
    }


def _env_for_repo() -> dict[str, str]:
    env = dict(os.environ)
    root = _repo_root()
    additions = [str(root / "src"), str(root)]
    existing = env.get("PYTHONPATH")
    if existing:
        additions.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(additions)
    return env


def _run_module_step(
    *,
    name: str,
    module: str,
    arguments: Sequence[str],
    logs_dir: Path,
) -> dict[str, Any]:
    command = [sys.executable, "-m", module, *map(str, arguments)]
    completed = subprocess.run(
        command,
        cwd=_repo_root(),
        env=_env_for_repo(),
        text=True,
        capture_output=True,
        check=False,
    )
    stdout_path = logs_dir / f"{name}.stdout.log"
    stderr_path = logs_dir / f"{name}.stderr.log"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    step = {
        "name": name,
        "module": module,
        "arguments": [str(value) for value in arguments],
        "returncode": int(completed.returncode),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "json": _json_from_stdout(completed.stdout),
        "status": "PASS" if completed.returncode == 0 else "FAIL",
    }
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "No process output"
        error = RealArchiveGateError(
            f"STEP_{name.upper()}_FAILED",
            f"{name} exited with {completed.returncode}: {detail[-2000:]}",
        )
        setattr(error, "step", step)
        raise error
    return step


def _oracle_source(messages: Sequence[Any]) -> list[dict[str, int | None]]:
    return [
        {
            "message_id": int(message.message_id),
            "conversation_id": int(message.conversation_id),
            "participant_id": None if message.participant_id is None else int(message.participant_id),
            "session_id": int(message.session_id),
            "sequence_number": int(message.sequence_number),
            "timestamp_us": None if message.timestamp_us is None else int(message.timestamp_us),
            "word_count": int(message.word_count),
        }
        for message in messages
    ]


def _a4_probe(database: Path, conversation_id: int) -> dict[str, Any]:
    from analyzazprav.analytics import AnalyticsConfig, analyze_conversation
    from analyzazprav.analytics.adapter_v7 import load_analytic_messages
    from analyzazprav.qa.analytics_validator import validate_analytics_result

    conn = sqlite3.connect(database)
    try:
        conn.execute("PRAGMA query_only=ON")
        messages = load_analytic_messages(conn, conversation_id)
    finally:
        conn.close()
    if not messages:
        raise RealArchiveGateError(
            "A4_SOURCE_EMPTY",
            f"Conversation {conversation_id} has no A4 analytic memberships.",
        )
    result = analyze_conversation(messages, AnalyticsConfig())
    oracle = validate_analytics_result(_oracle_source(messages), asdict(result))
    if oracle.get("status") != "PASS":
        raise RealArchiveGateError(
            "A4_ORACLE_FAILED",
            json.dumps(oracle.get("issues", []), ensure_ascii=False),
        )
    return {
        "status": "PASS",
        "source_membership_count": len(messages),
        "source_canonical_message_count": len({int(message.message_id) for message in messages}),
        "oracle": oracle,
    }


def _manual_a5_candidate(database: Path, conversation_id: int):
    from analyzazprav.a5_ai.models import AnalysisCandidate

    with _ro_connect(database) as conn:
        row = conn.execute(
            """
            SELECT id, sent_at_utc_us
            FROM analysis_messages
            WHERE conversation_id=? AND sent_at_utc_us IS NOT NULL
            ORDER BY sent_at_utc_us, id
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
    if row is None:
        raise RealArchiveGateError(
            "A5_NO_TIMESTAMPED_MESSAGE",
            "A5 temporal context cannot be validated because the selected conversation has no known timestamps.",
        )
    timestamp = datetime.fromtimestamp(int(row["sent_at_utc_us"]) / 1_000_000, tz=timezone.utc)
    return AnalysisCandidate(
        id="real-archive-manual-probe",
        conversation_id=str(conversation_id),
        start_ts=timestamp,
        end_ts=timestamp,
        candidate_type="manual_selection",
        importance_score=100.0,
        evidence_message_ids=(str(row["id"]),),
        manual_request=True,
        metadata={"source": "real_archive_gate", "a4_provenance_status": "not_applicable_manual_probe"},
    )


def _analysis_type(candidate_type: str):
    from analyzazprav.a5_ai.models import AnalysisType

    return {
        "conflict": AnalysisType.CONFLICT,
        "change_point": AnalysisType.CHANGE_POINT,
        "dyadic_regime": AnalysisType.INTERACTION_CYCLE,
        "engagement_signal": AnalysisType.RELATIONSHIP_DYNAMICS,
        "lexical_topic": AnalysisType.LONGITUDINAL,
    }.get(candidate_type, AnalysisType.SEGMENT)


def _a5_probe(database: Path, conversation_id: int) -> dict[str, Any]:
    from analyzazprav.a5_ai.context_builder import ContextBuilder
    from analyzazprav.a5_ai.integration_a2 import A2SQLiteMessageSource
    from analyzazprav.a5_ai.integration_a4_sqlite import A4SQLiteCandidateSource
    from analyzazprav.a5_ai.models import AIAnalysisRequest, AnalysisMode

    candidates = list(A4SQLiteCandidateSource(database).candidates(str(conversation_id)))
    by_type: dict[str, Any] = {}
    for candidate in candidates:
        by_type.setdefault(candidate.candidate_type, candidate)
    selected = list(by_type.values())
    manual_probe = False
    if not selected:
        selected = [_manual_a5_candidate(database, conversation_id)]
        manual_probe = True

    builder = ContextBuilder(A2SQLiteMessageSource(database))
    checked: list[dict[str, Any]] = []
    quality_warnings: list[str] = []
    for candidate in selected:
        if not manual_probe and candidate.metadata.get("a4_provenance_status") != "complete":
            raise RealArchiveGateError(
                "A5_A4_PROVENANCE_INCOMPLETE",
                f"Stored A4 candidate type {candidate.candidate_type!r} lacks complete run provenance.",
            )
        request = AIAnalysisRequest(
            conversation_id=str(conversation_id),
            analysis_type=_analysis_type(candidate.candidate_type),
            start_ts=candidate.start_ts,
            end_ts=candidate.end_ts,
            mode=AnalysisMode.RETROSPECTIVE,
            candidate_id=candidate.id,
        )
        context = builder.build(request, candidate)
        if context.missing_evidence_message_ids:
            raise RealArchiveGateError(
                "A5_EVIDENCE_MISSING",
                f"Candidate type {candidate.candidate_type!r} has unavailable evidence IDs.",
            )
        missing_identity = [
            message.id
            for message in context.messages
            if message.membership_id is None
            or not message.source_record_keys
            or not message.source_snapshot_keys
        ]
        if missing_identity:
            raise RealArchiveGateError(
                "A5_CONTEXT_PROVENANCE_MISSING",
                f"A5 context contains messages without production membership/source provenance: {missing_identity[:10]}",
            )
        quality_warnings.extend(context.quality_warnings)
        checked.append(
            {
                "candidate_type": candidate.candidate_type,
                "context_message_count": len(context.messages),
                "available_message_count": context.available_message_count,
                "omitted_message_count": context.omitted_message_count,
                "evidence_message_count": len(context.evidence_message_ids),
                "a4_provenance_status": candidate.metadata.get("a4_provenance_status"),
            }
        )
    return {
        "status": "PASS",
        "stored_candidate_count": len(candidates),
        "checked_candidate_type_count": len(checked),
        "manual_probe": manual_probe,
        "checked": checked,
        "quality_warnings": list(dict.fromkeys(quality_warnings)),
    }


def _a6_probe(database: Path, conversation_id: int) -> dict[str, Any]:
    from a6.data import analysis_packet, load_sqlite_messages
    from a6.evidence import enrich_analysis_packet_source_provenance
    from analyzazprav.a5_ai.integration_a6 import A6PacketMessageSource
    from analyzazprav.qa.a6_contract import validate_a6_packet

    frame, info = load_sqlite_messages(database)
    conversation = frame[frame["conversation_id"].astype(str) == str(conversation_id)].copy()
    timestamped = conversation[conversation["timestamp"].notna()]
    if timestamped.empty:
        raise RealArchiveGateError(
            "A6_NO_TIMESTAMPED_MESSAGE",
            "A6 cannot create a temporal production packet because the selected conversation has no known timestamps.",
        )
    selected_message_id = str(timestamped.iloc[0]["message_id"])
    base_packet = analysis_packet(
        conversation,
        [selected_message_id],
        context_before=0,
        context_after=0,
    )
    packet = enrich_analysis_packet_source_provenance(base_packet, database)
    oracle = validate_a6_packet(packet)
    if oracle.get("status") != "PASS":
        raise RealArchiveGateError(
            "A6_PACKET_ORACLE_FAILED",
            json.dumps(oracle.get("issues", []), ensure_ascii=False),
        )
    adapted = A6PacketMessageSource.from_packet(packet)
    if len(adapted.messages) != 1:
        raise RealArchiveGateError(
            "A6_A5_ADAPTER_CARDINALITY",
            f"Expected one A6 probe message, got {len(adapted.messages)}.",
        )
    message = adapted.messages[0]
    if (
        message.membership_id != str(packet["messages"][0]["membership_id"])
        or list(message.source_record_keys) != packet["messages"][0]["source_record_keys"]
        or list(message.source_snapshot_keys) != packet["messages"][0]["source_snapshot_keys"]
        or list(message.source_parser_versions) != packet["messages"][0]["source_parser_versions"]
    ):
        raise RealArchiveGateError(
            "A6_A5_PROVENANCE_MISMATCH",
            "A6→A5 adapter changed production membership/source provenance.",
        )
    return {
        "status": "PASS",
        "read_object": info.object_name,
        "conversation_membership_count": int(len(conversation)),
        "unknown_timestamp_count": int(conversation["timestamp"].isna().sum()),
        "packet_message_count": len(packet["messages"]),
        "packet_source_provenance_status": packet.get("source_provenance_status"),
        "a7_packet_status": oracle.get("status"),
        "a5_adapter_provenance_preserved": True,
    }


def _contract_sha() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_repo_root(),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and len(value) == 40 else None


def _candidate_summary(inventory: Sequence[Mapping[str, Any]], limit: int = 100) -> list[dict[str, Any]]:
    return [
        {
            "conversation_id": item["conversation_id"],
            "title": item.get("title"),
            "service": item.get("service"),
            "membership_count": item.get("membership_count"),
            "unknown_timestamp_count": item.get("unknown_timestamp_count"),
            "participants": item.get("participants"),
            "sources": item.get("sources"),
        }
        for item in inventory[:limit]
    ]


def _add_issue(report: dict[str, Any], severity: str, code: str, detail: str) -> None:
    issue = {"severity": severity, "code": code, "detail": detail}
    if issue not in report["issues"]:
        report["issues"].append(issue)


def _classify_a5_quality_warnings(report: dict[str, Any]) -> None:
    for warning in report["a5_probe"].get("quality_warnings", []):
        text = str(warning)
        if text.startswith("A5 context reduction omitted "):
            continue
        if "lack source_record_key provenance" in text:
            _add_issue(report, "ERROR", "A5_CONTEXT_SOURCE_PROVENANCE_WARNING", text)
        else:
            _add_issue(report, "WARNING", "A5_CONTEXT_QUALITY_WARNING", text)


def run_gate(
    *,
    chat_db: str | Path,
    workdir: str | Path,
    target: str | None = None,
    conversation_id: int | None = None,
    attachments_root: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(chat_db).expanduser().resolve()
    root = Path(workdir).expanduser().resolve()
    if not source.is_file():
        raise RealArchiveGateError("SOURCE_NOT_FOUND", f"chat.db does not exist: {source}")
    if root.exists() and not root.is_dir():
        raise RealArchiveGateError("WORKDIR_INVALID", f"Workdir exists and is not a directory: {root}")
    if root.exists() and any(root.iterdir()):
        raise RealArchiveGateError(
            "WORKDIR_NOT_EMPTY",
            f"Workdir must be new/empty to avoid mixing derived runs: {root}",
        )
    if attachments_root is not None:
        attachment_path = Path(attachments_root).expanduser().resolve()
        if not attachment_path.is_dir():
            raise RealArchiveGateError(
                "ATTACHMENTS_ROOT_INVALID",
                f"Attachments root is not a directory: {attachment_path}",
            )
    else:
        attachment_path = None

    root.mkdir(parents=True, exist_ok=True)
    logs = root / "logs"
    logs.mkdir()
    staging = root / "a1_staging"
    database = root / "messages.sqlite"
    report_path = root / "real_archive_report.json"

    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "real Apple Messages archive gate; no LLM call; derived outputs only",
        "status": "RUNNING",
        "verdict": "NEEDS_REVIEW",
        "release_ready": False,
        "contract_sha": _contract_sha(),
        "source": {"path": str(source), "type": "imessage_chat_db"},
        "workdir": str(root),
        "database": str(database),
        "selector": {"target": target, "conversation_id": conversation_id},
        "steps": [],
        "issues": [],
    }
    _write_json(report_path, report)

    def step(name: str, module: str, arguments: Sequence[str]) -> dict[str, Any]:
        try:
            value = _run_module_step(
                name=name,
                module=module,
                arguments=arguments,
                logs_dir=logs,
            )
        except RealArchiveGateError as exc:
            failed_step = getattr(exc, "step", None)
            report["steps"].append(
                failed_step
                if isinstance(failed_step, dict)
                else {
                    "name": name,
                    "module": module,
                    "arguments": [str(value) for value in arguments],
                    "status": "FAIL",
                }
            )
            raise
        report["steps"].append(value)
        _write_json(report_path, report)
        return value

    try:
        a1_args = ["imessage", "--chat-db", str(source), "--output-dir", str(staging)]
        if attachment_path is not None:
            a1_args.extend(["--attachments-root", str(attachment_path)])
        step("01_a1_import", "analiza_zprav_a1.cli", a1_args)
        manifest = _read_json(staging / "manifest.json")
        report["source"]["sha256"] = (manifest.get("source") or {}).get("sha256")
        report["source"]["snapshot_method"] = (manifest.get("source") or {}).get("snapshot_method")
        report["a1_counts"] = dict(manifest.get("counts") or {})

        step("02_a7_staging", "analyzazprav.qa.cli", ["staging", "--staging", str(staging)])
        step(
            "03_a2_ingest",
            "analyzazprav.normalization.cli",
            ["ingest-a1", "--database", str(database), "--staging", str(staging)],
        )
        step(
            "04_a2_integrity",
            "analyzazprav.normalization.cli",
            ["check", "--database", str(database)],
        )

        inventory = conversation_inventory(database)
        report["conversation_inventory"] = _candidate_summary(inventory)
        resolved = resolve_conversation(
            inventory,
            target=target,
            conversation_id=conversation_id,
        )
        report["resolved_conversation"] = resolved
        selected_id = int(resolved["conversation_id"])
        _write_json(report_path, report)

        step("05_a3_process", "analyzazprav.processing", [str(database)])
        step(
            "06_a7_participants",
            "analyzazprav.qa.cli",
            ["participants", "--database", str(database)],
        )
        step(
            "07_a7_vertical",
            "analyzazprav.qa.cli",
            ["vertical", "--staging", str(staging), "--database", str(database)],
        )
        step(
            "08_a4_analytics",
            "analyzazprav.analytics",
            [str(database), "--conversation-id", str(selected_id), "--full"],
        )

        report["a4_probe"] = _a4_probe(database, selected_id)
        report["a5_probe"] = _a5_probe(database, selected_id)
        report["a6_probe"] = _a6_probe(database, selected_id)

        counts = report.get("a1_counts") or {}
        if int(counts.get("attachments_missing", 0) or 0) > 0:
            _add_issue(
                report,
                "WARNING",
                "A1_ATTACHMENTS_MISSING",
                f"{int(counts.get('attachments_missing', 0))} attachment occurrence(s) could not be resolved from the supplied attachment root.",
            )
        if int(counts.get("unsupported", 0) or 0) > 0:
            _add_issue(
                report,
                "WARNING",
                "A1_UNSUPPORTED_RECORDS_PRESENT",
                f"A1 classified {int(counts.get('unsupported', 0))} unsupported record(s).",
            )
        _classify_a5_quality_warnings(report)

        errors = [item for item in report["issues"] if item.get("severity") == "ERROR"]
        warnings = [item for item in report["issues"] if item.get("severity") == "WARNING"]
        if errors:
            report["status"] = "FAIL"
            report["verdict"] = "INVALID"
        elif warnings:
            report["status"] = "WARNING"
            report["verdict"] = "NEEDS_REVIEW"
        else:
            report["status"] = "PASS"
            report["verdict"] = "VALID"
        report["release_ready"] = report["verdict"] == "VALID"
        report["counts"] = {"errors": len(errors), "warnings": len(warnings)}
        _write_json(report_path, report)
        return report
    except RealArchiveGateError as exc:
        _add_issue(report, "ERROR", exc.code, exc.detail)
        report["status"] = "FAIL"
        report["verdict"] = "INVALID"
        report["release_ready"] = False
        errors = [item for item in report["issues"] if item.get("severity") == "ERROR"]
        warnings = [item for item in report["issues"] if item.get("severity") == "WARNING"]
        report["counts"] = {"errors": len(errors), "warnings": len(warnings)}
        _write_json(report_path, report)
        raise
    except Exception as exc:
        _add_issue(
            report,
            "ERROR",
            "REAL_ARCHIVE_GATE_EXCEPTION",
            f"{type(exc).__name__}: {exc}",
        )
        report["status"] = "FAIL"
        report["verdict"] = "INVALID"
        report["release_ready"] = False
        _write_json(report_path, report)
        raise RealArchiveGateError("REAL_ARCHIVE_GATE_EXCEPTION", str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.real_archive_gate",
        description=(
            "Run the local fail-closed real Apple Messages archive gate over existing A1-A7 modules. "
            "No LLM call is made."
        ),
    )
    parser.add_argument("--chat-db", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--target", help="Exact canonical/source identity label; never fuzzy auto-selected")
    selector.add_argument("--conversation-id", type=int, help="Authoritative canonical conversation ID")
    parser.add_argument("--attachments-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_gate(
            chat_db=args.chat_db,
            workdir=args.workdir,
            target=args.target,
            conversation_id=args.conversation_id,
            attachments_root=args.attachments_root,
        )
    except RealArchiveGateError as exc:
        print(json.dumps({"status": "FAIL", "code": exc.code, "detail": exc.detail}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("release_ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
