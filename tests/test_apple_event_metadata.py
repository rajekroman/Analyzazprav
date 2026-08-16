from analiza_zprav_a1.apple_event_metadata import project_apple_event_metadata


def test_default_zero_fields_do_not_create_false_event_metadata() -> None:
    metadata = project_apple_event_metadata(
        {
            "associated_message_guid": None,
            "associated_message_type": 0,
            "associated_message_range_location": 0,
            "associated_message_range_length": 0,
            "date_edited": 0,
            "date_retracted": 0,
            "is_edited": 0,
            "is_deleted": 0,
            "is_retracted": 0,
        }
    )

    assert metadata == {}


def test_malformed_edit_history_stays_present_without_invented_size() -> None:
    metadata = project_apple_event_metadata(
        {
            "edit_history": {"encoding": "base64", "data": "not-valid-base64"},
        }
    )

    assert metadata == {
        "apple_edit_state": {
            "edit_history_present": True,
        }
    }


def test_associated_projection_requires_and_preserves_exact_target_guid() -> None:
    metadata = project_apple_event_metadata(
        {
            "associated_message_guid": "p:7/RAW-GUID",
            "associated_message_type": 0,
            "associated_message_emoji": None,
            "associated_message_range_location": 0,
            "associated_message_range_length": 0,
        }
    )

    assert metadata["apple_associated_message"] == {
        "associated_message_guid": "p:7/RAW-GUID",
        "associated_message_type": 0,
        "associated_message_range_location": 0,
        "associated_message_range_length": 0,
    }
