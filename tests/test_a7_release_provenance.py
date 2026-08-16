from __future__ import annotations

from copy import deepcopy

from tools.a7_downstream.release_verdict import apply_contract_provenance


CORE_SHA = "1" * 40
A5_SHA = "2" * 40
A6_SHA = "3" * 40


def _base_report(verdict: str = "VALID") -> dict:
    return {
        "schema_version": 1,
        "overall_verdict": verdict,
        "release_ready": verdict == "VALID",
        "components": {"core": "VALID", "A5": "VALID", "A6": "VALID"},
        "issues": [],
    }


def _components() -> dict:
    return {
        "core": {"verdict": "VALID", "contract_sha": CORE_SHA},
        "A5": {"verdict": "VALID", "contract_sha": A5_SHA},
        "A6": {"verdict": "VALID", "contract_sha": A6_SHA},
    }


def _expected() -> dict:
    return {"core": CORE_SHA, "A5": A5_SHA, "A6": A6_SHA}


def test_release_provenance_accepts_exact_component_shas() -> None:
    report = apply_contract_provenance(_base_report(), _components(), _expected())

    assert report["schema_version"] == 2
    assert report["overall_verdict"] == "VALID"
    assert report["release_ready"] is True
    assert report["issues"] == []
    assert report["component_contracts"] == {
        "core": {"expected_sha": CORE_SHA, "observed_sha": CORE_SHA},
        "A5": {"expected_sha": A5_SHA, "observed_sha": A5_SHA},
        "A6": {"expected_sha": A6_SHA, "observed_sha": A6_SHA},
    }


def test_release_provenance_rejects_mismatched_component_sha() -> None:
    components = _components()
    components["A6"]["contract_sha"] = "4" * 40

    report = apply_contract_provenance(_base_report(), components, _expected())

    assert report["overall_verdict"] == "INVALID"
    assert report["release_ready"] is False
    assert any(
        issue["code"] == "A7_COMPONENT_CONTRACT_SHA_MISMATCH"
        for issue in report["issues"]
    )


def test_release_provenance_rejects_present_report_without_valid_sha() -> None:
    components = _components()
    components["A5"].pop("contract_sha")

    report = apply_contract_provenance(_base_report(), components, _expected())

    assert report["overall_verdict"] == "INVALID"
    assert report["release_ready"] is False
    assert any(
        issue["code"] == "A7_COMPONENT_CONTRACT_SHA_MISSING"
        for issue in report["issues"]
    )


def test_release_provenance_preserves_needs_review_for_absent_report() -> None:
    components = _components()
    components["A5"] = None
    base = _base_report("NEEDS_REVIEW")
    base["components"]["A5"] = "NEEDS_REVIEW"

    report = apply_contract_provenance(base, components, _expected())

    assert report["overall_verdict"] == "NEEDS_REVIEW"
    assert report["release_ready"] is False
    assert report["component_contracts"]["A5"]["observed_sha"] is None
    assert not any(
        issue["code"] == "A7_COMPONENT_CONTRACT_SHA_MISSING"
        for issue in report["issues"]
    )


def test_release_provenance_rejects_invalid_expected_sha() -> None:
    expected = deepcopy(_expected())
    expected["core"] = "not-a-sha"

    report = apply_contract_provenance(_base_report(), _components(), expected)

    assert report["overall_verdict"] == "INVALID"
    assert report["release_ready"] is False
    assert any(
        issue["code"] == "A7_EXPECTED_CONTRACT_SHA_INVALID"
        for issue in report["issues"]
    )
