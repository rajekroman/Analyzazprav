from pathlib import Path


def test_entrypoint_replaces_legacy_a5_boundaries():
    text = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "_legacy.analysis_packet = analysis_packet" in text
    assert "_legacy.render_evidence_ref = render_evidence_ref" in text
    assert "enrich_analysis_packet_source_provenance" in text
    assert "reconcile_a5_evidence_ref" in text
    assert "Aktuální databáze se nesmí vydávat za původní evidence" in text
