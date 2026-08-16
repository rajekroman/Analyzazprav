from __future__ import annotations
from datetime import datetime, timedelta, timezone

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def apple_timestamp_precision(value: int | float | None) -> str | None:
    if value is None:
        return None
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    return "nanosecond" if abs(raw) >= 100_000_000_000 else "second"


def apple_timestamp_to_iso(value: int | float | None) -> str | None:
    if value is None:
        return None
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    seconds = raw / 1_000_000_000 if abs(raw) >= 100_000_000_000 else raw
    try:
        return (APPLE_EPOCH + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
    except (OverflowError, ValueError):
        return None
