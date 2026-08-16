from analiza_zprav_a1.apple_time import apple_timestamp_precision, apple_timestamp_to_iso


def test_apple_seconds_and_nanoseconds_match():
    seconds = 800_000_000
    nanos = seconds * 1_000_000_000
    assert apple_timestamp_to_iso(seconds) == apple_timestamp_to_iso(nanos)
    assert apple_timestamp_precision(seconds) == "second"
    assert apple_timestamp_precision(nanos) == "nanosecond"
