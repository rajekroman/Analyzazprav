from analiza_zprav_a1.text_decode import decode_attributed_body


def test_recovers_emoji_only_utf8_text() -> None:
    blob = b"\x01\x02" + "👍🏽".encode("utf-8") + b"\x00\x03"
    assert decode_attributed_body(blob) == "👍🏽"


def test_recovers_non_latin_utf8_text() -> None:
    blob = b"\x01" + "日本語テスト".encode("utf-8") + b"\x00"
    assert decode_attributed_body(blob) == "日本語テスト"


def test_recovers_utf16le_text_with_bom() -> None:
    blob = b"\xff\xfe" + "Привет světe".encode("utf-16-le")
    assert decode_attributed_body(blob) == "Привет světe"


def test_rejects_archive_metadata_without_message_content() -> None:
    blob = b"\x01NSString\x00NSAttributedString\x00NSDictionary\x02"
    assert decode_attributed_body(blob) is None


def test_prefers_real_text_over_separate_archive_metadata_runs() -> None:
    blob = (
        b"NSString\x00"
        + "Ahoj 👋".encode("utf-8")
        + b"\x00NSAttributedString"
    )
    assert decode_attributed_body(blob) == "Ahoj 👋"


def test_punctuation_only_is_not_invented_as_message_text() -> None:
    assert decode_attributed_body(b"\x01!!!???\x00") is None


def test_empty_and_none_are_none() -> None:
    assert decode_attributed_body(None) is None
    assert decode_attributed_body(b"") is None
