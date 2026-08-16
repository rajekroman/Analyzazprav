from __future__ import annotations

"""A6 Streamlit entrypoint with A5-v4 provenance safeguards.

The mature A6 UI is kept in ``a6.app_legacy`` to preserve its already-tested
read model and interaction flow. This entrypoint replaces only the downstream
A5 packet/evidence boundaries and adds explicit semantics labels.
"""

from typing import Any

from a6 import app_legacy as _legacy
from a6.app_legacy import *  # noqa: F401,F403
from a6.data import analysis_packet as _base_analysis_packet
from a6.evidence import (
    FAIL,
    PASS,
    STALE,
    UNVERIFIED,
    PacketProvenanceError,
    enrich_analysis_packet_source_provenance,
    load_current_message_provenance,
    reconcile_a5_evidence_ref,
)
from a6.semantics import candidate_semantics

_CURRENT_DB_PATH: str | None = None
_ORIGINAL_SOURCE = _legacy.source
_ORIGINAL_CHARTS = _legacy.charts
_ORIGINAL_SIGNIFICANT_PERIODS = _legacy.significant_periods


def source():
    global _CURRENT_DB_PATH
    result = _ORIGINAL_SOURCE()
    _CURRENT_DB_PATH = result[3]
    return result


def analysis_packet(frame, selected_message_ids, *, context_before=20, context_after=20):
    packet = _base_analysis_packet(
        frame,
        selected_message_ids,
        context_before=context_before,
        context_after=context_after,
    )
    return enrich_analysis_packet_source_provenance(
        packet,
        _CURRENT_DB_PATH,
        require_provenance=_CURRENT_DB_PATH is not None,
    )


def _current_rows(frame) -> list[dict[str, Any]]:
    columns = [column for column in ("membership_id", "message_id", "conversation_id") if column in frame]
    if not columns:
        return []
    return frame[columns].to_dict("records")


def _render_materialized_snapshot(evidence_ref: dict) -> None:
    snapshots = evidence_ref.get("messages") or []
    if snapshots:
        _legacy.st.markdown("**A5 uložený evidence snapshot**")
        _legacy.st.dataframe(_legacy.pd.DataFrame(snapshots), use_container_width=True, hide_index=True)


def render_evidence_ref(evidence_ref, conversation_frame, db_path: str | None) -> None:
    if not isinstance(evidence_ref, dict):
        _legacy.st.error("A5 assertion nemá validní evidence objekt.")
        return
    if evidence_ref.get("description"):
        _legacy.st.write(evidence_ref["description"])

    ids = [str(value) for value in evidence_ref.get("message_ids") or []]
    metrics = evidence_ref.get("metrics") or []
    if ids:
        evidence, missing = _legacy.resolve_evidence(conversation_frame, ids)
        if missing:
            _legacy.st.error(
                "A5 odkazuje na message_id, které nejsou v aktuálních kanonických datech: "
                + ", ".join(missing)
            )

        if db_path is None:
            _legacy.st.warning(
                "A5 evidence běží nad demo/neprodukčním zdrojem; source provenance nelze nezávisle ověřit."
            )
            _render_materialized_snapshot(evidence_ref)
            if not evidence.empty:
                _legacy.render_message_evidence(evidence, _legacy.empty_provenance(), None)
        else:
            try:
                current_sources = load_current_message_provenance(db_path, ids)
                reconciliation = reconcile_a5_evidence_ref(
                    evidence_ref,
                    _current_rows(evidence),
                    current_sources,
                )
            except PacketProvenanceError as exc:
                _legacy.st.error(f"A5 evidence provenance nelze ověřit: {exc}")
                _render_materialized_snapshot(evidence_ref)
            else:
                if reconciliation.status == PASS:
                    _legacy.st.success("A5 evidence snapshot přesně odpovídá aktuální A2 membership/source provenance.")
                    if not evidence.empty:
                        _legacy.render_message_evidence(
                            evidence,
                            _legacy.provenance_for(db_path, list(evidence.message_id.astype(str))),
                            db_path,
                        )
                elif reconciliation.status == STALE:
                    _legacy.st.warning(
                        "A5 evidence je STALE: membership stále existuje, ale source provenance se od uloženého snapshotu změnila. "
                        "Historický snapshot a aktuální data se zobrazují odděleně."
                    )
                    _render_materialized_snapshot(evidence_ref)
                    if reconciliation.mismatches:
                        _legacy.st.dataframe(
                            _legacy.pd.DataFrame([item.__dict__ for item in reconciliation.mismatches]),
                            use_container_width=True,
                            hide_index=True,
                        )
                    if not evidence.empty:
                        _legacy.st.markdown("**Aktuální A2 data — pouze pro porovnání**")
                        _legacy.render_message_evidence(
                            evidence,
                            _legacy.provenance_for(db_path, list(evidence.message_id.astype(str))),
                            db_path,
                        )
                elif reconciliation.status == FAIL:
                    _legacy.st.error(
                        "A5 evidence nelze svázat s aktuální A2 membership/source provenance. "
                        "Aktuální databáze se nesmí vydávat za původní evidence."
                    )
                    _render_materialized_snapshot(evidence_ref)
                    if reconciliation.mismatches:
                        _legacy.st.dataframe(
                            _legacy.pd.DataFrame([item.__dict__ for item in reconciliation.mismatches]),
                            use_container_width=True,
                            hide_index=True,
                        )
                elif reconciliation.status == UNVERIFIED:
                    _legacy.st.warning(
                        "Výsledek neobsahuje materializovaný A5-v4 evidence snapshot; current-data drill-down je pouze UNVERIFIED."
                    )
                    if not evidence.empty:
                        _legacy.render_message_evidence(
                            evidence,
                            _legacy.provenance_for(db_path, list(evidence.message_id.astype(str))),
                            db_path,
                        )

    if metrics:
        _legacy.st.markdown("Deterministická metric evidence")
        _legacy.st.dataframe(_legacy.pd.DataFrame(metrics), use_container_width=True, hide_index=True)
    if not ids and not metrics:
        _legacy.st.error("A5 assertion nemá message ani metric evidence.")


def charts(frame, metrics, period_start, period_end) -> None:
    _ORIGINAL_CHARTS(frame, metrics, period_start, period_end)
    if metrics.available and not metrics.participants.empty and "engagement_score" in metrics.participants:
        semantics = candidate_semantics("engagement_signal")
        _legacy.st.caption(f"{semantics.label}: {semantics.explanation}")


def significant_periods(findings, conversation_frame, period_start, period_end, db_path) -> None:
    conflict = candidate_semantics("conflict")
    change = candidate_semantics("change_point")
    regime = candidate_semantics("dyadic_regime")
    _legacy.st.caption(
        "A4 významná období jsou deterministické kandidáty, nikoli prokázané vztahové události. "
        f"{conflict.explanation} {change.explanation} {regime.explanation}"
    )
    return _ORIGINAL_SIGNIFICANT_PERIODS(
        findings, conversation_frame, period_start, period_end, db_path
    )


# Replace the exact global call sites used inside the mature UI module.
_legacy.source = source
_legacy.analysis_packet = analysis_packet
_legacy.render_evidence_ref = render_evidence_ref
_legacy.charts = charts
_legacy.significant_periods = significant_periods


def main():
    return _legacy.main()


if __name__ == "__main__":
    main()
