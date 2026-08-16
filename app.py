from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from a6.a5_bridge import A5Unavailable, a5_available, run_local_a5
from a6.attachments import (
    empty_attachment_sources,
    empty_attachments,
    load_attachment_sources,
    load_message_attachments,
)
from a6.data import (
    DataSourceError,
    SourceInfo,
    add_opposite_sender_gap,
    analysis_packet,
    demo_messages,
    filter_messages,
    load_sqlite_messages,
)
from a6.findings import empty_findings, filter_findings, load_a4_findings, resolve_evidence
from a6.metrics import A4ConversationMetrics, empty_a4_metrics, load_a4_conversation_metrics
from a6.provenance import empty_provenance, load_message_sources

st.set_page_config(page_title="Analýza zpráv", page_icon="💬", layout="wide")


@st.cache_data(show_spinner=False)
def load_db(path: str):
    messages, info = load_sqlite_messages(path)
    findings = load_a4_findings(path)
    return messages, info, findings


@st.cache_data(show_spinner=False)
def load_a4_metrics(path: str, conversation_id: str) -> A4ConversationMetrics:
    return load_a4_conversation_metrics(path, conversation_id)


def source():
    st.sidebar.header("Zdroj dat")
    if st.sidebar.radio("Režim", ["Demo", "SQLite"], horizontal=True) == "Demo":
        return demo_messages(), SourceInfo("demo", "Vestavěná demo data"), empty_findings(), None
    path = st.sidebar.text_input("SQLite", "database/messages.sqlite").strip()
    try:
        messages, info, findings = load_db(path)
        return messages, info, findings, path
    except DataSourceError as exc:
        st.sidebar.error(str(exc))
        st.stop()


def duration(value):
    if value is None or pd.isna(value):
        return "—"
    value = float(value)
    if value < 60:
        return f"{value:.0f} s"
    if value < 3600:
        return f"{value / 60:.1f} min"
    if value < 86400:
        return f"{value / 3600:.1f} h"
    return f"{value / 86400:.1f} d"


def timestamp_label(value, *, seconds: bool = True) -> str:
    if value is None or pd.isna(value):
        return "čas neznámý"
    fmt = "%Y-%m-%d %H:%M:%S UTC" if seconds else "%Y-%m-%d %H:%M"
    return pd.Timestamp(value).strftime(fmt)


def date_label(value) -> str:
    if value is None or pd.isna(value):
        return "čas neznámý"
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def contact_overview(frame: pd.DataFrame) -> None:
    overview = (
        frame.groupby("contact", dropna=False)
        .agg(
            memberships=("membership_id", "count"),
            canonical_messages=("message_id", "nunique"),
            conversations=("conversation_id", "nunique"),
            unknown_time=("timestamp", lambda values: int(values.isna().sum())),
            first_message=("timestamp", "min"),
            last_message=("timestamp", "max"),
        )
        .reset_index()
        .sort_values("last_message", ascending=False, na_position="last")
    )
    with st.expander("Přehled kontaktů"):
        st.dataframe(overview, use_container_width=True, hide_index=True)


def select_conversation(frame: pd.DataFrame) -> tuple[str, pd.DataFrame]:
    contacts = sorted(frame.contact.unique())
    contact = st.sidebar.selectbox("Kontakt", contacts)
    contact_scope = filter_messages(frame, contact=contact)
    summary = (
        contact_scope.groupby("conversation_id", dropna=False)
        .agg(
            memberships=("membership_id", "count"),
            canonical_messages=("message_id", "nunique"),
            unknown_time=("timestamp", lambda values: int(values.isna().sum())),
            first=("timestamp", "min"),
            last=("timestamp", "max"),
        )
        .reset_index()
    )
    options: dict[str, str] = {}
    for row in summary.itertuples(index=False):
        conversation_id = str(row.conversation_id)
        label = (
            f"{conversation_id} · {int(row.memberships)} memberships / {int(row.canonical_messages)} zpráv · "
            f"{date_label(row.first)} → {date_label(row.last)}"
        )
        if row.unknown_time:
            label += f" · {int(row.unknown_time)} bez času"
        options[label] = conversation_id
    if len(options) == 1:
        conversation_id = next(iter(options.values()))
        st.sidebar.caption(f"Konverzace: `{conversation_id}`")
    else:
        chosen = st.sidebar.selectbox("Konverzace", list(options))
        conversation_id = options[chosen]

    if st.session_state.get("a6_conversation_id") != conversation_id:
        st.session_state.a6_conversation_id = conversation_id
        st.session_state.a6_manual_selected = []
        st.session_state.a6_finding_selected = []
        st.session_state.a6_selection_source = "manual"
        st.session_state.pop("a6_last_execution", None)
        st.session_state.pop("a6_last_analysis_selection", None)

    conversation_frame = contact_scope[contact_scope.conversation_id.astype(str) == conversation_id].reset_index(drop=True)
    return contact, conversation_frame


def timeline(frame: pd.DataFrame, findings: pd.DataFrame) -> None:
    if frame.empty:
        st.info("Bez dat pro časovou osu.")
        return
    unknown_count = int(frame.timestamp.isna().sum())
    known = frame[frame.timestamp.notna()]
    if unknown_count:
        st.info(f"{unknown_count} zpráv má neznámý čas. Zůstávají v konverzaci, ale nelze je umístit na časovou osu.")
    if known.empty:
        st.info("Žádná zpráva nemá známý čas pro časovou osu.")
    else:
        daily = known.assign(day=known.timestamp.dt.floor("D")).groupby(["day", "sender"]).size().unstack(fill_value=0)
        st.markdown("**Časová osa aktivity**")
        st.line_chart(daily)
    if not findings.empty:
        view = findings[["start_timestamp", "end_timestamp", "finding_type", "label", "score"]].copy()
        view = view.rename(columns={
            "start_timestamp": "od",
            "end_timestamp": "do",
            "finding_type": "typ",
            "label": "nález",
            "score": "skóre",
        })
        st.markdown("**Detekované významné body / období**")
        st.dataframe(view, use_container_width=True, hide_index=True)


def _period_a4_daily(
    metrics: A4ConversationMetrics,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> pd.DataFrame:
    if metrics.daily.empty or "period_date" not in metrics.daily:
        return pd.DataFrame()
    daily = metrics.daily.copy()
    if start is None or end is None:
        return daily.reset_index(drop=True)
    valid = daily["period_date"].notna()
    valid &= daily["period_date"].dt.date.between(pd.Timestamp(start).date(), pd.Timestamp(end).date())
    return daily[valid].reset_index(drop=True)


def charts(
    frame: pd.DataFrame,
    metrics: A4ConversationMetrics,
    period_start: pd.Timestamp | None,
    period_end: pd.Timestamp | None,
) -> None:
    if metrics.available:
        st.caption("Zdroj metrik: A4 latest-run views (autoritativní deterministická vrstva).")
        daily = _period_a4_daily(metrics, period_start, period_end)
        if not daily.empty:
            sender_col = "sender" if "sender" in daily else "participant_id"
            activity = daily.pivot_table(index="period_date", columns=sender_col, values="message_count", aggfunc="sum", fill_value=0)
            st.markdown("**Aktivita — A4 message_count**")
            st.line_chart(activity)
            initiations = daily.pivot_table(index="period_date", columns=sender_col, values="initiations", aggfunc="sum", fill_value=0)
            st.markdown("**Iniciace komunikace — A4 initiations**")
            st.line_chart(initiations)
            latency_rows = daily.dropna(subset=["median_response_latency_seconds"])
            if not latency_rows.empty:
                latency = latency_rows.pivot_table(index="period_date", columns=sender_col, values="median_response_latency_seconds", aggfunc="first")
                st.markdown("**Response latency — A4 denní medián v sekundách**")
                st.line_chart(latency)
        else:
            st.info("A4 je dostupné, ale pro zvolené období nemá denní metriky.")
        if not metrics.participants.empty:
            columns = [column for column in (
                "sender", "participant_id", "message_count", "active_days", "initiations", "initiation_share",
                "median_response_latency_seconds", "median_response_effort_ratio", "engagement_score",
            ) if column in metrics.participants]
            st.markdown("**Souhrn účastníků — celá konverzace v posledním A4 runu**")
            st.dataframe(metrics.participants[columns], use_container_width=True, hide_index=True)
        return

    st.caption("Zdroj metrik: lokální A6 fallback. Jde o adjacency heuristiky, nikoli autoritativní A4 metriky.")
    known = frame[frame.timestamp.notna()]
    if not known.empty:
        activity = known.assign(day=known.timestamp.dt.floor("D")).groupby(["day", "sender"]).size().unstack(fill_value=0)
        st.markdown("**Aktivita — fallback**")
        st.line_chart(activity)
    st.markdown("**Poměr zpráv**")
    st.bar_chart(frame.sender.value_counts())
    gaps = add_opposite_sender_gap(frame)
    samples = pd.to_numeric(gaps.opposite_sender_gap_seconds, errors="coerce").dropna()
    if not samples.empty:
        gap_rows = gaps.dropna(subset=["opposite_sender_gap_seconds", "timestamp"])
        daily = gap_rows.assign(day=gap_rows.timestamp.dt.floor("D")).groupby(["day", "sender"]).opposite_sender_gap_seconds.median().unstack()
        st.markdown("**Odstup při změně odesílatele — fallback medián v sekundách**")
        st.line_chart(daily)
        st.caption("Tato hodnota měří sousední zprávy různých odesílatelů. Není důkazem explicitní odpovědi.")


def conversation(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.info("Žádné zprávy pro zvolené filtry.")
        st.session_state.a6_manual_selected = []
        return
    limit = int(st.number_input("Zobrazit posledních zpráv", 20, 500, 100, 20))
    display_frame = frame.tail(limit)
    labels: dict[str, str] = {}
    for row in display_frame.itertuples(index=False):
        preview = row.text.replace("\n", " ")[:70]
        label = f"{row.message_id} · {timestamp_label(row.timestamp, seconds=False)} · {row.sender} · {preview}"
        labels[label] = row.message_id
    existing = set(st.session_state.get("a6_manual_selected", []))
    defaults = [label for label, message_id in labels.items() if message_id in existing]
    chosen = st.multiselect("Vybrat zprávy pro analýzu", labels, default=defaults)
    manual_ids = [labels[item] for item in chosen]
    st.session_state.a6_manual_selected = manual_ids
    if st.button("Použít ruční výběr pro analýzu", disabled=not manual_ids):
        st.session_state.a6_selection_source = "manual"
    for row in display_frame.itertuples(index=False):
        with st.container(border=True):
            st.markdown(f"**{row.sender}** · {timestamp_label(row.timestamp)}")
            st.write(row.text or "_Bez textu_")
            st.caption(
                f"membership_id: `{row.membership_id}` · message_id: `{row.message_id}` · "
                f"conversation_id: `{row.conversation_id}` · timestamp: {row.timestamp_quality}/{row.timestamp_precision}"
            )


def active_selection() -> tuple[list[str], str]:
    source_name = st.session_state.get("a6_selection_source", "manual")
    if source_name == "finding":
        return list(st.session_state.get("a6_finding_selected", [])), "A4 nález"
    return list(st.session_state.get("a6_manual_selected", [])), "ruční výběr"


def provenance_for(db_path: str | None, message_ids: list[str]) -> pd.DataFrame:
    if db_path is None or not message_ids:
        return empty_provenance()
    try:
        return load_message_sources(db_path, message_ids)
    except DataSourceError as exc:
        st.error(str(exc))
        return empty_provenance()


def attachments_for(db_path: str | None, message_ids: list[str]) -> pd.DataFrame:
    if db_path is None or not message_ids:
        return empty_attachments()
    try:
        return load_message_attachments(db_path, message_ids)
    except DataSourceError as exc:
        st.error(str(exc))
        return empty_attachments()


def attachment_sources_for(db_path: str | None, occurrence_ids: list[str]) -> pd.DataFrame:
    if db_path is None or not occurrence_ids:
        return empty_attachment_sources()
    try:
        return load_attachment_sources(db_path, occurrence_ids)
    except DataSourceError as exc:
        st.error(str(exc))
        return empty_attachment_sources()


def render_message_evidence(frame: pd.DataFrame, provenance: pd.DataFrame, db_path: str | None = None) -> None:
    attachments = attachments_for(db_path, list(frame.message_id.astype(str)))
    occurrence_ids = list(attachments.occurrence_id.astype(str)) if not attachments.empty else []
    attachment_sources = attachment_sources_for(db_path, occurrence_ids)
    for row in frame.itertuples(index=False):
        with st.container(border=True):
            st.markdown(f"**{row.sender}** · {timestamp_label(row.timestamp)}")
            st.write(row.text or "_Bez textu_")
            st.caption(
                f"membership_id: `{row.membership_id}` · canonical message_id: `{row.message_id}` · "
                f"conversation_id: `{row.conversation_id}` · timestamp: {row.timestamp_quality}/{row.timestamp_precision}"
            )
            message_attachments = attachments[attachments.message_id == str(row.message_id)] if not attachments.empty else attachments
            if not message_attachments.empty:
                st.markdown("Přílohy — canonical occurrences")
                st.dataframe(message_attachments, use_container_width=True, hide_index=True)
                message_occurrences = set(message_attachments.occurrence_id.astype(str))
                source_rows = attachment_sources[attachment_sources.occurrence_id.isin(message_occurrences)] if not attachment_sources.empty else attachment_sources
                if source_rows.empty:
                    st.caption("Attachment source provenance není v tomto A2 schema dostupná.")
                else:
                    st.markdown("Attachment source provenance")
                    st.dataframe(source_rows, use_container_width=True, hide_index=True)

            sources = provenance[provenance.message_id == str(row.message_id)] if not provenance.empty else provenance
            if sources.empty:
                st.caption("Source provenance není pro tuto zprávu v aktuálním zdroji dostupná.")
            else:
                st.markdown("Message source provenance")
                columns = [column for column in (
                    "source_type", "source_message_id", "source_conversation_id", "source_row_id",
                    "source_record_key", "raw_timestamp", "import_run_id",
                ) if column in sources]
                st.dataframe(sources[columns], use_container_width=True, hide_index=True)


def significant_periods(
    findings: pd.DataFrame,
    conversation_frame: pd.DataFrame,
    period_start: pd.Timestamp | None,
    period_end: pd.Timestamp | None,
    db_path: str | None,
) -> None:
    relevant = filter_findings(
        findings,
        conversation_ids=conversation_frame.conversation_id.unique(),
        start=period_start,
        end=period_end,
    )
    if relevant.empty:
        st.info("Pro zvolenou konverzaci a období nejsou v A4 dostupné významné nálezy.")
        return
    options: dict[str, str] = {}
    for row in relevant.itertuples(index=False):
        when = date_label(row.start_timestamp)
        options[f"{when} · {row.finding_type} · {row.label} · score {row.score:.2f}"] = row.finding_id
    chosen_label = st.selectbox("Analytický nález", list(options))
    finding = relevant[relevant.finding_id == options[chosen_label]].iloc[0]
    try:
        details = json.loads(finding.details) if finding.details else {}
    except json.JSONDecodeError:
        details = {"raw_details": finding.details}
    st.json(details)
    evidence, missing = resolve_evidence(conversation_frame, finding.evidence_message_ids)
    if missing:
        st.error("A4 evidence obsahuje message_id, které A6 v kanonických zprávách nenašlo: " + ", ".join(missing))
    if evidence.empty:
        st.warning("Nález nemá dostupnou konkrétní message evidence.")
        return
    evidence_ids = list(evidence.message_id.astype(str))
    st.markdown(f"**Evidence — {len(evidence_ids)} zpráv**")
    render_message_evidence(evidence, provenance_for(db_path, evidence_ids), db_path)
    if st.button("Použít evidence tohoto nálezu pro AI analýzu", key=f"use-{finding.finding_id}"):
        st.session_state.a6_finding_selected = evidence_ids
        st.session_state.a6_selection_source = "finding"
        st.success("Evidence nálezu byla nastavena jako aktivní výběr pro A5.")


def render_result_evidence(message_ids: list[str], conversation_frame: pd.DataFrame, db_path: str | None) -> None:
    evidence, missing = resolve_evidence(conversation_frame, message_ids)
    if missing:
        st.error("A5 odkazuje na message_id, které nejsou v aktuálních kanonických datech: " + ", ".join(missing))
    if not evidence.empty:
        render_message_evidence(evidence, provenance_for(db_path, list(evidence.message_id.astype(str))), db_path)


def _evidence_ref_ids(evidence_ref) -> list[str]:
    if not isinstance(evidence_ref, dict):
        return []
    return [str(value) for value in evidence_ref.get("message_ids") or []]


def render_evidence_ref(evidence_ref, conversation_frame: pd.DataFrame, db_path: str | None) -> None:
    if not isinstance(evidence_ref, dict):
        st.error("A5 assertion nemá validní evidence objekt.")
        return
    if evidence_ref.get("description"):
        st.write(evidence_ref["description"])
    ids = _evidence_ref_ids(evidence_ref)
    if ids:
        render_result_evidence(ids, conversation_frame, db_path)
    metrics = evidence_ref.get("metrics") or []
    if metrics:
        st.markdown("Deterministická metric evidence")
        st.dataframe(pd.DataFrame(metrics), use_container_width=True, hide_index=True)
    if not ids and not metrics:
        st.error("A5 assertion nemá message ani metric evidence.")


def render_assertion(
    title: str,
    claim,
    parallel_evidence,
    conversation_frame: pd.DataFrame,
    db_path: str | None,
) -> None:
    if claim in (None, "", []):
        return
    if isinstance(claim, dict):
        text = str(claim.get("text") or "")
        confidence = claim.get("confidence")
        evidence_ref = claim.get("evidence") or parallel_evidence
    else:
        text = str(claim)
        confidence = None
        evidence_ref = parallel_evidence
    with st.expander(f"{title}: {text}", expanded=title == "Shrnutí"):
        if confidence is not None:
            st.caption(f"jistota: {float(confidence):.2f}")
        render_evidence_ref(evidence_ref, conversation_frame, db_path)


def render_a5_execution(execution: dict, conversation_frame: pd.DataFrame, db_path: str | None) -> None:
    status = str(execution.get("status") or "unknown")
    st.markdown(f"### Výsledek A5 · `{status}`")
    if execution.get("context_hash"):
        st.caption(f"context_hash: `{execution['context_hash']}`")
    result = execution.get("result")
    if not result:
        st.error(str(execution.get("error") or "A5 nevrátil validní výsledek."))
        return
    st.metric("Celková jistota", f"{float(result.get('overall_confidence', 0.0)):.2f}")

    render_assertion("Shrnutí", result.get("summary"), result.get("summary_evidence"), conversation_frame, db_path)

    observations = result.get("observations") or []
    if observations:
        st.markdown("#### Pozorování")
        for index, observation in enumerate(observations, start=1):
            with st.expander(f"{index}. {observation.get('text', '')}"):
                st.caption(f"síla: {float(observation.get('strength', 0.0)):.2f}")
                render_evidence_ref(observation.get("evidence"), conversation_frame, db_path)

    interpretations = result.get("interpretations") or []
    if interpretations:
        st.markdown("#### Interpretace")
        for index, interpretation in enumerate(interpretations, start=1):
            evidence_ref = interpretation.get("evidence") or {"message_ids": interpretation.get("evidence_message_ids") or []}
            with st.expander(f"{index}. {interpretation.get('text', '')}"):
                st.caption(f"jistota: {float(interpretation.get('confidence', 0.0)):.2f}")
                render_evidence_ref(evidence_ref, conversation_frame, db_path)

    patterns = result.get("patterns") or []
    if patterns:
        st.markdown("#### Vzorce")
        for index, pattern in enumerate(patterns, start=1):
            evidence_ref = pattern.get("evidence") or {"message_ids": pattern.get("evidence_message_ids") or []}
            title = f"{index}. {pattern.get('pattern_type', 'pattern')} · {pattern.get('description', '')}"
            with st.expander(title):
                st.caption(f"jistota: {float(pattern.get('confidence', 0.0)):.2f}")
                render_evidence_ref(evidence_ref, conversation_frame, db_path)

    turning_points = result.get("turning_points") or []
    turning_evidence = result.get("turning_point_evidence") or []
    for index, claim in enumerate(turning_points):
        evidence_ref = turning_evidence[index] if index < len(turning_evidence) else None
        render_assertion(f"Bod obratu {index + 1}", claim, evidence_ref, conversation_frame, db_path)
    render_assertion("Účastník P1", result.get("participant_p1"), result.get("participant_p1_evidence"), conversation_frame, db_path)
    render_assertion("Účastník P2", result.get("participant_p2"), result.get("participant_p2_evidence"), conversation_frame, db_path)
    render_assertion("Sdílená dynamika", result.get("shared_dynamic"), result.get("shared_dynamic_evidence"), conversation_frame, db_path)

    alternatives = result.get("alternative_explanations") or []
    if alternatives:
        st.markdown("#### Alternativní vysvětlení")
        for item in alternatives:
            st.markdown(f"- {item}")
    unknowns = result.get("unknowns") or []
    if unknowns:
        st.markdown("#### Nejistoty / chybějící informace")
        for item in unknowns:
            st.markdown(f"- {item}")


def main():
    frame, info, findings, db_path = source()
    if frame.empty:
        st.error("Zdroj neobsahuje použitelné zprávy.")
        st.stop()

    st.title("Analýza zpráv")
    suffix = f" · {info.object_name}" if info.object_name else ""
    st.caption(f"Zdroj: {info.label}{suffix} · {len(frame):,} membership řádků · {frame.message_id.nunique():,} canonical zpráv")
    contact_overview(frame)

    contact, conversation_frame = select_conversation(frame)
    conversation_id = str(conversation_frame.iloc[0].conversation_id)
    a4_metrics = load_a4_metrics(db_path, conversation_id) if db_path else empty_a4_metrics()

    known_times = conversation_frame.timestamp.dropna()
    unknown_time_count = int(conversation_frame.timestamp.isna().sum())
    period_start: pd.Timestamp | None = None
    period_end: pd.Timestamp | None = None
    full_period = True
    if not known_times.empty:
        lo, hi = known_times.min().date(), known_times.max().date()
        dates = st.sidebar.date_input("Období", (lo, hi), min_value=lo, max_value=hi)
        if not isinstance(dates, tuple) or len(dates) != 2:
            dates = (dates, dates)
        period_start = pd.Timestamp(dates[0])
        period_end = pd.Timestamp(dates[1]) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        full_period = dates[0] == lo and dates[1] == hi
    else:
        st.sidebar.info("Tato konverzace nemá žádný známý timestamp; časový filtr je vypnutý.")

    include_unknown = True
    if unknown_time_count:
        include_unknown = st.sidebar.checkbox(
            f"Zahrnout zprávy s neznámým časem ({unknown_time_count})",
            value=True,
            help="Neznámý timestamp je validní canonical stav. Vypnutí je explicitní prezentační filtr, nikoli odstranění dat.",
        )
    senders = sorted(conversation_frame.sender.unique())
    selected_senders = st.sidebar.multiselect("Odesílatelé", senders, default=senders)
    query = st.sidebar.text_input("Hledat v textu").strip()

    filtered = filter_messages(
        conversation_frame,
        start=period_start,
        end=period_end,
        senders=selected_senders,
        search=query or None,
        include_unknown_timestamps=include_unknown,
    )
    conversation_findings = filter_findings(findings, [conversation_id], start=period_start, end=period_end)

    gap_or_response = None
    metric_label = "Medián odpovědi"
    if a4_metrics.available:
        if full_period and not a4_metrics.responses.empty and "latency_seconds" in a4_metrics.responses:
            samples = pd.to_numeric(a4_metrics.responses["latency_seconds"], errors="coerce").dropna()
            gap_or_response = samples.median() if not samples.empty else None
    else:
        metric_label = "Medián odstupu při změně odesílatele"
        gaps = add_opposite_sender_gap(filtered)
        samples = pd.to_numeric(gaps.opposite_sender_gap_seconds, errors="coerce").dropna()
        gap_or_response = samples.median() if not samples.empty else None

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Memberships", len(filtered))
    c2.metric("Canonical zprávy", filtered.message_id.nunique())
    c3.metric("Aktivní dny", filtered.timestamp.dt.date.nunique())
    c4.metric("Bez času", int(filtered.timestamp.isna().sum()))
    c5.metric(metric_label, duration(gap_or_response))
    c6.metric("A4 nálezy", len(conversation_findings))

    st.caption(
        f"Kontakt: {contact} · conversation_id: `{conversation_id}` · "
        f"analytické metriky: {'A4 latest-run' if a4_metrics.available else 'A6 explicitně označený fallback'}"
    )
    if a4_metrics.available and not full_period:
        st.caption("Souhrnný medián odpovědi pro zúžené období se neagreguje z mediánů; denní A4 latency zůstává v grafech.")

    tabs = st.tabs(["Konverzace", "Časová osa", "Grafy", "Významná období", "Vybrané zprávy", "Analýza"])
    with tabs[0]:
        conversation(filtered)
    with tabs[1]:
        timeline(filtered, conversation_findings)
    with tabs[2]:
        charts(filtered, a4_metrics, period_start, period_end) if not filtered.empty else st.info("Bez dat.")
    with tabs[3]:
        significant_periods(findings, conversation_frame, period_start, period_end, db_path)

    selected, selection_source = active_selection()
    with tabs[4]:
        st.caption(f"Aktivní zdroj výběru: {selection_source}")
        chosen, missing = resolve_evidence(conversation_frame, selected)
        if missing:
            st.error("Aktivní výběr obsahuje nedostupné message_id: " + ", ".join(missing))
        if chosen.empty:
            st.info("Zatím není aktivní žádný výběr zpráv.")
        else:
            render_message_evidence(chosen, provenance_for(db_path, list(chosen.message_id.astype(str))), db_path)

    with tabs[5]:
        selected, selection_source = active_selection()
        st.write("A6 neposílá celý archiv do AI. Připraví pouze explicitně vybranou evidence a omezený okolní kontext pro A5.")
        st.caption(f"Aktivní zdroj výběru: {selection_source}")
        if selected:
            radius = int(st.number_input("Kontext před/po vybrané zprávě", 0, 100, 20, 5))
            st.caption("Okolní kontext se bere z membership-scoped chronologie této konverzace; zpráva bez známého času není pro A5 chronologicky domýšlena.")
            packet = None
            try:
                packet = analysis_packet(conversation_frame, selected, context_before=radius, context_after=radius)
            except ValueError as exc:
                st.error(str(exc))
            if packet is not None:
                payload = json.dumps(packet, ensure_ascii=False, indent=2)
                st.code(payload, language="json")
                st.download_button("Stáhnout A5 kontext", payload, "a5-context.json", "application/json")
                st.markdown("### Lokální AI analýza")
                if not a5_available():
                    st.info("A5 modul není součástí tohoto checkoutu. Export packetu zůstává dostupný; žádný cloud fallback se nespouští.")
                else:
                    model_name = st.text_input("Ollama model", "qwen3:8b")
                    base_url = st.text_input("Ollama URL", "http://localhost:11434")
                    analysis_type = st.selectbox(
                        "Typ analýzy",
                        ["segment", "change_point", "conflict", "interaction_cycle", "longitudinal", "relationship_dynamics", "psychological_hypotheses"],
                    )
                    mode = st.selectbox("Režim", ["blind", "retrospective"])
                    user_question = st.text_area("Volitelná otázka pro A5").strip()
                    if st.button("Spustit A5 lokálně přes Ollama"):
                        try:
                            with st.spinner("Probíhá lokální A5 analýza…"):
                                execution = run_local_a5(
                                    packet,
                                    model_name=model_name,
                                    base_url=base_url,
                                    analysis_type=analysis_type,
                                    mode=mode,
                                    user_question=user_question or None,
                                )
                            st.session_state.a6_last_execution = execution
                            st.session_state.a6_last_analysis_selection = list(selected)
                        except (A5Unavailable, ValueError) as exc:
                            st.error(str(exc))
                        except Exception as exc:
                            st.error(f"A5 analýzu se nepodařilo spustit: {exc}")
                    last_execution = st.session_state.get("a6_last_execution")
                    last_selection = st.session_state.get("a6_last_analysis_selection", [])
                    if last_execution and list(selected) == list(last_selection):
                        render_a5_execution(last_execution, conversation_frame, db_path)
                    elif last_execution:
                        st.info("Poslední A5 výsledek patří k jinému výběru zpráv a proto se zde nezobrazuje.")
        else:
            st.info("Vyberte zprávy ručně nebo použijte evidence některého A4 nálezu.")


if __name__ == "__main__":
    main()
