from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from a6.data import DataSourceError, SourceInfo, add_response_latency, analysis_packet, demo_messages, filter_messages, load_sqlite_messages

st.set_page_config(page_title="Analýza zpráv", page_icon="💬", layout="wide")


@st.cache_data(show_spinner=False)
def load_db(path: str):
    return load_sqlite_messages(path)


def source():
    st.sidebar.header("Zdroj dat")
    if st.sidebar.radio("Režim", ["Demo", "SQLite"], horizontal=True) == "Demo":
        return demo_messages(), SourceInfo("demo", "Vestavěná demo data")
    path = st.sidebar.text_input("SQLite", "database/messages.sqlite").strip()
    try:
        return load_db(path)
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


def conversation(frame):
    if frame.empty:
        st.info("Žádné zprávy pro zvolené filtry.")
        return []
    labels = {}
    for row in frame.itertuples(index=False):
        preview = row.text.replace("\n", " ")[:70]
        label = f"{row.message_id} · {row.timestamp:%Y-%m-%d %H:%M} · {row.sender} · {preview}"
        labels[label] = row.message_id
    chosen = st.multiselect("Vybrat zprávy pro analýzu", labels)
    limit = int(st.number_input("Zobrazit posledních zpráv", 20, 500, 100, 20))
    for row in frame.tail(limit).itertuples(index=False):
        with st.container(border=True):
            st.markdown(f"**{row.sender}** · {row.timestamp:%Y-%m-%d %H:%M:%S UTC}")
            st.write(row.text or "_Bez textu_")
            st.caption(f"message_id: `{row.message_id}` · conversation_id: `{row.conversation_id}`")
    return [labels[item] for item in chosen]


def main():
    frame, info = source()
    if frame.empty:
        st.error("Zdroj neobsahuje použitelné zprávy.")
        st.stop()

    st.title("Analýza zpráv")
    suffix = f" · {info.object_name}" if info.object_name else ""
    st.caption(f"Zdroj: {info.label}{suffix} · {len(frame):,} zpráv")

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

    filtered = filter_messages(
        contact_frame,
        start=pd.Timestamp(dates[0]),
        end=pd.Timestamp(dates[1]) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1),
        senders=selected_senders,
        search=query or None,
    )
    lat = add_response_latency(filtered)
    replies = pd.to_numeric(lat.response_seconds, errors="coerce").dropna()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Zprávy", len(filtered))
    c2.metric("Aktivní dny", filtered.timestamp.dt.date.nunique())
    c3.metric("Odesílatelé", filtered.sender.nunique())
    c4.metric("Medián odpovědi", duration(replies.median() if not replies.empty else None))

    t1, t2, t3, t4 = st.tabs(["Konverzace", "Grafy", "Vybrané zprávy", "Analýza"])
    with t1:
        selected = conversation(filtered)
        st.session_state.a6_selected = selected
    selected = st.session_state.get("a6_selected", [])
    with t2:
        charts(filtered) if not filtered.empty else st.info("Bez dat.")
    with t3:
        chosen = filtered[filtered.message_id.isin(selected)]
        st.dataframe(chosen[["message_id", "timestamp", "sender", "text"]], use_container_width=True, hide_index=True)
    with t4:
        st.write("Analýza se nespouští automaticky. A6 pouze připraví auditovatelný kontext pro A5.")
        if selected:
            radius = int(st.number_input("Kontext před/po vybrané zprávě", 0, 100, 20, 5))
            payload = json.dumps(
                analysis_packet(filtered, selected, context_before=radius, context_after=radius),
                ensure_ascii=False,
                indent=2,
            )
            st.code(payload, language="json")
            st.download_button("Stáhnout A5 kontext", payload, "a5-context.json", "application/json")
        else:
            st.info("Nejprve vyberte zprávy v kartě Konverzace.")


if __name__ == "__main__":
    main()
