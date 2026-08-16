from __future__ import annotations

import base64
from typing import Any, Mapping

from .apple_time import apple_timestamp_to_iso

_ASSOCIATED_MESSAGE_FIELDS = (
    "associated_message_guid",
    "associated_message_type",
    "associated_message_emoji",
    "associated_message_range_location",
    "associated_message_range_length",
)

_EDIT_DATE_FIELDS = (
    "date_edited",
    "date_retracted",
)

_EDIT_FLAG_FIELDS = (
    "is_edited",
    "is_deleted",
    "is_retracted",
)


def _base64_payload_size(value: Any) -> int | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("encoding") != "base64":
        return None
    data = value.get("data")
    if not isinstance(data, str):
        return None
    try:
        return len(base64.b64decode(data, validate=True))
    except (ValueError, TypeError):
        return None


def _positive_source_flag(value: Any) -> bool:
    return value not in (None, "", 0, False, "0")


def project_apple_event_metadata(raw_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a conservative structured projection of Apple message metadata.

    This function deliberately does not map undocumented Apple numeric relation
    codes to semantic reaction names. The exact source values remain in
    ``raw_payload``; the projection only makes selected provenance fields easier
    for A2/A7 to audit without changing their meaning.
    """

    metadata: dict[str, Any] = {}

    # Apple schemas may expose numeric associated-message columns with default
    # zero values even for ordinary messages. Require an actual target GUID
    # before creating the structured association projection; all raw defaults
    # remain available in raw_payload regardless.
    associated_guid = raw_payload.get("associated_message_guid")
    if associated_guid not in (None, ""):
        associated: dict[str, Any] = {}
        for field in _ASSOCIATED_MESSAGE_FIELDS:
            if field in raw_payload and raw_payload[field] is not None:
                associated[field] = raw_payload[field]
        metadata["apple_associated_message"] = associated

    edit_state: dict[str, Any] = {}
    for field in _EDIT_DATE_FIELDS:
        if field not in raw_payload:
            continue
        raw_value = raw_payload[field]
        if raw_value in (None, "", 0):
            continue
        edit_state[f"{field}_raw"] = raw_value
        utc_value = apple_timestamp_to_iso(raw_value)
        if utc_value is not None:
            edit_state[f"{field}_utc"] = utc_value

    # Do not create edit metadata merely because a schema exposes a default 0
    # flag. A positive source flag is evidence; the exact zero remains raw.
    for field in _EDIT_FLAG_FIELDS:
        raw_value = raw_payload.get(field)
        if _positive_source_flag(raw_value):
            edit_state[f"{field}_raw"] = raw_value

    if "edit_history" in raw_payload and raw_payload["edit_history"] is not None:
        edit_state["edit_history_present"] = True
        size = _base64_payload_size(raw_payload["edit_history"])
        if size is not None:
            edit_state["edit_history_bytes"] = size

    if edit_state:
        metadata["apple_edit_state"] = edit_state

    return metadata
