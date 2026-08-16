from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from a6.data import (
    DataSourceError,
    SourceInfo,
    add_response_latency,
    analysis_packet,
    demo_messages,
    filter_messages,
    load_sqlite_messages,
)
from a6.findings import empty_findings, filter_findings, load_a4_findings, resolve_evidence
from a6.provenance import empty_provenance, load_message_sources

st.set_page_config(page_title="Analýza zpráv", page_icon="💬", layout="wide")


@st.cache_data(show_spinner=False)
def load_db(path: str):
    messages, info = load_sqlite_messages(path)
    findings = load_a4_findings(path)
    return messages, info, findings


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


def contact_overview(frame: pd.DataFrame) -> None:
    overview = (
        frame.groupby("contact", dropna=False)
        .agg(
            messages=("message_id", "count"),
            conversations=("conversation_id", "nunique"),
            first_message=("timestamp", "min"),
            last_message=("timestamp", "max"),
        )
        .reset_index()
        .sort_values("last_message", ascending=False)
    )
    with st.expander("Přehled kontaktů"):
        st.dataframe(overview, use_container_width=True, hide_index=True)


def timeline(frame: pd.DataFrame, findings: pd.DataFrame) -> None:
    if frame.empty:
        st.info("Bez dat pro časovou osu.")
        return
    daily = frame.assign(day=frame.timestamp.dt.floor("D")).groupby(["day", "sender"]).size().unstack(fill_value=0)
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


def charts(frame):
    activity = frame.assign(day=frame.timestamp.dt.floor("D")).groupby(["day", "sender"]).size().unstack(fill_value=0)
    st.markdown("**Aktivita**")
    st.line_chart(activity)
    st.markdown("**Poměr zpráv**")
    st.bar_chart(frame.sender.value_counts())
    latency = add_response_latency(frame)
    latency.response_seconds = pd.to_numeric(latency.response_seconds, errors="coerce")
    latency = latency.dropna(subset=["response_seconds"])
    if not latency.empty:
        daily = latency.assign(day=latency.timestamp.dt.floor("D")).groupby(["day", "sender"]).response_seconds.median().unstack()
        st.markdown("**Response latency — medián v sekundách**")
        st.line_chart(daily)
    st.caption("Tyto grafy jsou MVP fallback. Po integraci A4 se deterministické metriky načtou z autoritativních A4 views.")


def conversation(frame):
    if frame.empty:
        st.info("Žádné zprávy pro zvolené filtry.")
        st.session_state.a6_manual_selected = []
        return
    limit = int(st.number_input("Zobrazit posledních zpráv", 20, 500, 100, 20))
    display_frame = frame.tail(limit)
    labels = {}
    for row in display_frame.itertuples(index=False):
        preview = row.text.replace("\n", " ")[:70]
        label = f"{row.message_id} · {row.timestamp:%Y-%m-%d %H:%M} · {row.sender} · {preview}"
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
            st.markdown(f"**{row.sender}** · {row.timestamp:%Y-%m-%d %H:%M:%S UTC}")
            st.write(row.text or "_Bez textu_")
            st.caption(f"message_id: `{row.message_id}` · conversation_id: `{row.conversation_id}`")


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


def render_message_evidence(frame: pd.DataFrame, provenance: pd.DataFrame) -> None:
    for row in frame.itertuples(index=False):
        with st.container(border=True):
            st.markdown(f"**{row.sender}** · {row.timestamp:%Y-%m-%d %H:%M:%S UTC}")
            st.write(row.text or "_Bez textu_")
            st.caption(f"canonical message_id: `{row.message_id}` · conversation_id: `{row.conversation_id}`")
            sources = provenance[provenance.message_id == str(row.message_id)] if not provenance.empty else provenance
            if sources.empty:
                st.caption("Source provenance není pro tuto zprávu v aktuálním zdroji dostupná.")
            else:
                st.markdown("Source provenance")
                st.dataframe(
                    sources[[
                        "source_type",
                        "source_message_id",
                        "source_conversation_id",
                        "source_row_id",
                        "source_record_key",
                        "raw_timestamp",
                        "import_run_id",
                    ]],
                    use_container_width=True,
                    hide_index=True,
                )


def significant_periods(
    findings: pd.DataFrame,
    contact_frame: pd.DataFrame,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
    db_path: str | None,
) -> None:
    relevant = filter_findings(
        findings,
        conversation_ids=contact_frame.conversation_id.unique(),
        start=period_start,
        end=period_end,
    )
    if relevant.empty:
        st.info("Pro zvolený kontakt a období nejsou v A4 dostupné významné nálezy.")
        return

    options = {}
    for row in relevant.itertuples(index=False):
        when = row.start_timestamp.strftime("%Y-%m-%d") if not pd.isna(row.start_timestamp) else "bez data"
        label = f"{when} · {row.finding_type} · {row.label} · score {row.score:.2f}"
        options[label] = row.finding_id
    chosen_label = st.selectbox("Analytický nález", list(options))
    finding = relevant[relevant.finding_id == options[chosen_label]].iloc[0]

    st.json(json.loads(finding.details) if finding.details else {})
    evidence, missing = resolve_evidence(contact_frame, finding.evidence_message_ids)
    if missing:
        st.error("A4 evidence obsahuje message_id, které A6 v kanonických zprávách nenašlo: " + ", ".join(missing))
    if evidence.empty:
        st.warning("Nález nemá dostupnou konkrétní message evidence.")
        return

    evidence_ids = list(evidence.message_id.astype(str))
    st.markdown(f"**Evidence — {len(evidence_ids)} zpráv**")
    provenance = provenance_for(db_path, evidence_ids)
    render_message_evidence(evidence, provenance)
    if st.button("Použít evidence tohoto nálezu pro AI analýzu", key=f"use-{finding.finding_id}"):
        st.session_state.a6_finding_selected = evidence_ids
        st.session_state.a6_selection_source = "finding"
        st.success("Evidence nálezu byla nastavena jako aktivní výběr pro A5.")


def main():
    frame, info, findings, db_path = source()
    if frame.empty:
        st.error("Zdroj neobsahuje použitelné zprávy.")
        st.stop()

    st.title("Analýza zpráv")
    suffix = f" · {info.object_name}" if info.object_name else ""
    st.caption(f"Zdroj: {info.label}{suffix} · {len(frame):,} zpráv")
    contact_overview(frame)

    contacts = sorted(frame.contact.unique())
    contact = st.sidebar.selectbox("Kontakt / konverzace", contacts)
    contact_frame = filter_messages(frame, contact=contact)
    lo, hi = contact_frame.timestamp.min().date(), contact_frame.timestamp.max().date()
    dates = st.sidebar.date_input("Období", (lo, hi), min_value=lo, max_value=hi)
    if not isinstance(dates, tuple) or len(dates) != 2:
        dates = (dates, dates)
    senders = sorted(contact_frame.sender.unique())
    selected_senders = st.sidebar.multiselect("Odesílatelé", senders, default=senders)
    query = st.sidebar.text_input("Hledat v textu").strip()

    period_start = pd.Timestamp(dates[0])
    period_end = pd.Timestamp(dates[1]) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    filtered = filter_messages(
        contact_frame,
        start=period_start,
        end=period_end,
        senders=selected_senders,
        search=query or None,
    )
    contact_findings = filter_findings(
        findings,
        conversation_ids=contact_frame.conversation_id.unique(),
        start=period_start,
        end=period_end,
    )

    lat = add_response_latency(filtered)
    replies = pd.to_numeric(lat.response_seconds, errors="coerce").dropna()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Zprávy", len(filtered))
    c2.metric("Aktivní dny", filtered.timestamp.dt.date.nunique())
    c3.metric("Odesílatelé", filtered.sender.nunique())
    c4.metric("Medián odpovědi", duration(replies.median() if not replies.empty else None))
    c5.metric("A4 nálezy", len(contact_findings))

    tabs = st.tabs(["Konverzace", "Časová osa", "Grafy", "Významná období", "Vybrané zprávy", "Analýza"])
    with tabs[0]:
        conversation(filtered)
    with tabs[1]:
        timeline(filtered, contact_findings)
    with tabs[2]:
        charts(filtered) if not filtered.empty else st.info("Bez dat.")
    with tabs[3]:
        significant_periods(findings, contact_frame, period_start, period_end, db_path)

    selected, selection_source = active_selection()
    with tabs[4]:
        st.caption(f"Aktivní zdroj výběru: {selection_source}")
        chosen, missing = resolve_evidence(contact_frame, selected)
        if missing:
            st.error("Aktivní výběr obsahuje nedostupné message_id: " + ", ".join(missing))
        if chosen.empty:
            st.info("Zatím není aktivní žádný výběr zpráv.")
        else:
            render_message_evidence(chosen, provenance_for(db_path, list(chosen.message_id.astype(str))))

    with tabs[5]:
        selected, selection_source = active_selection()
        st.write("A6 neposílá celý archiv do AI. Připraví pouze explicitně vybranou evidence a omezený okolní kontext pro A5.")
        st.caption(f"Aktivní zdroj výběru: {selection_source}")
        if selected:
            radius = int(st.number_input("Kontext před/po vybrané zprávě", 0, 100, 20, 5))
            st.caption("Okolní kontext se načítá z původní chronologie kontaktu bez textového a sender filtru.")
            packet = analysis_packet(contact_frame, selected, context_before=radius, context_after=radius)
            payload = json.dumps(packet, ensure_ascii=False, indent=2)
            st.code(payload, language="json")
            st.download_button("Stáhnout A5 kontext", payload, "a5-context.json", "application/json")
            st.caption("Packet schema v1 je přímo podporováno A5 adapterem; samotné modelové volání zůstává explicitní a nikdy se nespouští automaticky.")
        else:
            st.info("Vyberte zprávy ručně nebo použijte evidence některého A4 nálezu.")


if __name__ == "__main__":
    main()
