"""Tests for the Pi-hole list model (models.py)."""

from dataclasses import FrozenInstanceError

import pytest

from custom_components.pi_hole_lists.models import PiHoleList

# Full FTL row, including read-only fields the integration does not model.
LIST = {
    "id": 1,
    "address": "https://example.com/ads.txt",
    "enabled": True,
    "type": "block",
    "comment": "Example blocklist",
    "groups": [1, 2],
    "number": 12345,
    "invalid_domains": 3,
    "status": "enabled",
    "date_updated": 1750000000,
    "date_added": 1750000000,
    "date_modified": 1750000000,
    "abp_entries": 42,
}


def test_from_dict_parses_modeled_fields():
    """All modeled fields are parsed from an FTL list row."""
    parsed = PiHoleList.from_dict(LIST)

    assert parsed.id == 1
    assert parsed.address == "https://example.com/ads.txt"
    assert parsed.enabled is True
    assert parsed.type == "block"
    assert parsed.comment == "Example blocklist"
    assert parsed.groups == [1, 2]
    assert parsed.number == 12345
    assert parsed.invalid_domains == 3
    assert parsed.status == "enabled"
    assert parsed.date_updated == 1750000000


def test_from_dict_drops_unknown_keys():
    """Read-only FTL fields that are not modeled are dropped, not errors."""
    parsed = PiHoleList.from_dict(LIST)

    assert not hasattr(parsed, "date_added")
    assert not hasattr(parsed, "date_modified")
    assert not hasattr(parsed, "abp_entries")


def test_from_dict_defaults_missing_fields_to_none():
    """Only id is required; every other field is nullable."""
    parsed = PiHoleList.from_dict({"id": 7})

    assert parsed.id == 7
    assert parsed.address is None
    assert parsed.enabled is None
    assert parsed.comment is None


def test_from_dict_raises_without_id():
    """A row without an id cannot be keyed by the coordinator."""
    with pytest.raises(ValueError, match="missing its id"):
        PiHoleList.from_dict({"address": "https://example.com/ads.txt"})


def test_update_payload_echoes_comment():
    """The PUT body carries the enabled flag and the list comment."""
    parsed = PiHoleList.from_dict(LIST)

    assert parsed.update_payload(False) == {
        "enabled": False,
        "comment": "Example blocklist",
    }


def test_update_payload_sends_none_comment():
    """A missing comment is echoed as JSON null rather than omitted.

    JSON null and an absent key are equivalent to FTL (both leave the
    comment NULL), and the payload shape stays uniform.
    """
    parsed = PiHoleList.from_dict({**LIST, "comment": None})

    assert parsed.update_payload(True) == {"enabled": True, "comment": None}


def test_merge_update_copies_only_non_none_fields():
    """A slim response must not wipe fields it does not carry."""
    current = PiHoleList.from_dict(LIST)
    partial = PiHoleList.from_dict({"id": 1, "status": "disabled"})

    merged = current.merge_update(partial)

    assert merged.status == "disabled"
    assert merged.address == current.address
    assert merged.comment == current.comment
    assert merged.enabled is current.enabled


def test_merge_update_is_identity_for_full_row():
    """A full FTL response merges into an identical model."""
    current = PiHoleList.from_dict(LIST)
    full = PiHoleList.from_dict({**LIST, "enabled": False})

    merged = current.merge_update(full)

    assert merged == full
    assert merged.enabled is False


def test_instances_are_frozen():
    """Model instances are immutable values."""
    parsed = PiHoleList.from_dict(LIST)

    with pytest.raises(FrozenInstanceError):
        parsed.comment = "mutated"
