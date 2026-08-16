import json
from pathlib import Path

from analiza_zprav_a1.importer import (
    import_generic_csv,
    import_generic_json,
    import_generic_text,
    import_imazing_csv,
)


def _expect_exception(call, expected: str, exception_type: type[BaseException] = ValueError) -> None:
    try:
        call()
    except exception_type as exc:
        assert expected in str(exc)
    else:
        raise AssertionError(
            f"expected {exception_type.__name__} containing {expected!r}"
        )


def test_imazing_duplicate_headers_fail_before_staging_write(tmp_path: Path) -> None:
    source = tmp_path / "imazing.csv"
    staging = tmp_path / "staging"
    source.write_text(
        "Sender,Sender,Message\nAlice,Bob,Ahoj\n",
        encoding="utf-8",
    )

    _expect_exception(
        lambda: import_imazing_csv(source, staging),
        "duplicate header names",
    )
    assert not staging.exists()


def test_imazing_extra_fields_fail_before_staging_write(tmp_path: Path) -> None:
    source = tmp_path / "imazing.csv"
    staging = tmp_path / "staging"
    source.write_text(
        "Sender,Message\nAlice,Ahoj,UNACCOUNTED\n",
        encoding="utf-8",
    )

    _expect_exception(
        lambda: import_imazing_csv(source, staging),
        "more fields than the header",
    )
    assert not staging.exists()


def test_generic_csv_late_structural_error_does_not_leave_partial_bundle(
    tmp_path: Path,
) -> None:
    source = tmp_path / "messages.csv"
    staging = tmp_path / "staging"
    source.write_text(
        "Sender,Message\nAlice,first valid row\nBob,second row,UNACCOUNTED\n",
        encoding="utf-8",
    )

    _expect_exception(
        lambda: import_generic_csv(source, staging),
        "more fields than the header",
    )
    assert not staging.exists()


def test_generic_jsonl_late_parse_error_does_not_leave_partial_bundle(
    tmp_path: Path,
) -> None:
    source = tmp_path / "messages.jsonl"
    staging = tmp_path / "staging"
    source.write_text(
        json.dumps({"sender": "Alice", "text": "first valid row"})
        + "\n"
        + "{not valid json\n",
        encoding="utf-8",
    )

    _expect_exception(
        lambda: import_generic_json(source, staging),
        "Expecting property name enclosed in double quotes",
        json.JSONDecodeError,
    )
    assert not staging.exists()


def test_generic_text_decode_error_does_not_leave_partial_bundle(tmp_path: Path) -> None:
    source = tmp_path / "messages.txt"
    staging = tmp_path / "staging"
    source.write_bytes(b"first valid line\nsecond line\xff\n")

    _expect_exception(
        lambda: import_generic_text(source, staging, "line"),
        "invalid start byte",
        UnicodeDecodeError,
    )
    assert not staging.exists()
