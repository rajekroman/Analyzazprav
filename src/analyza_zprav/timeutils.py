from __future__ import annotations

from datetime import datetime, timedelta, timezone

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def apple_timestamp_to_datetime(value: int | float | None) -> datetime | None:
    """Convert Apple Messages timestamps to UTC datetimes.

    Apple Messages has historically stored seconds since 2001 and, on newer
    systems, nanoseconds since 2001. This function accepts either representation.
    """
    if value is None:
        return None
    raw = float(value)
    if abs(raw) >= 1e12:
        seconds = raw / 1_000_000_000
    else:
        seconds = raw
    return APPLE_EPOCH + timedelta(seconds=seconds)


def to_iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
