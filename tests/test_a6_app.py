from __future__ import annotations

from streamlit.testing.v1 import AppTest


def test_streamlit_app_renders_demo_workflow_without_exception():
    app = AppTest.from_file("app.py", default_timeout=10)
    app.run()

    assert not app.exception
    assert app.title[0].value == "Analýza zpráv"
    assert len(app.tabs) == 6
    assert [tab.label for tab in app.tabs] == [
        "Konverzace",
        "Časová osa",
        "Grafy",
        "Významná období",
        "Vybrané zprávy",
        "Analýza",
    ]
    assert app.sidebar.radio[0].value == "Demo"
    assert app.sidebar.selectbox[0].label == "Kontakt"
    assert app.sidebar.date_input[0].label == "Období"
