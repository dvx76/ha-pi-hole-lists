"""Tests for the Pi-hole Lists options flow."""

from unittest.mock import MagicMock

import pytest

from custom_components.pi_hole_lists.config_flow import PiHoleListsOptionsFlow
from custom_components.pi_hole_lists.const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)


def _flow(entry_options: dict | None = None) -> PiHoleListsOptionsFlow:
    """Build an options flow bound to a mocked hass and config entry.

    Mirrors what HA's FlowManager does before running the first step: it sets
    flow.hass and flow.handler (the entry id), and the OptionsFlow.config_entry
    property resolves the entry through hass.
    """
    entry = MagicMock()
    entry.options = entry_options or {}
    hass = MagicMock()
    hass.config_entries.async_get_known_entry.return_value = entry

    flow = PiHoleListsOptionsFlow()
    flow.hass = hass
    flow.handler = "test-entry-id"
    return flow


def _scan_interval_default(result: dict) -> int:
    """Return the scan-interval default of a form result's schema."""
    return result["data_schema"]({})[CONF_SCAN_INTERVAL]


def test_options_flow_constructible_without_entry():
    """Options flow must not read or store config_entry at construction.

    Regression: assigning self.config_entry in __init__ raised
    AttributeError ("property 'config_entry' ... has no setter") on
    HA >= 2025.12 when clicking Configure on the device.
    """
    flow = PiHoleListsOptionsFlow()
    assert flow.handler is None


@pytest.mark.asyncio
async def test_options_flow_init_shows_configured_scan_interval():
    """The form is shown with the stored scan interval as default."""
    flow = _flow({CONF_SCAN_INTERVAL: 10})

    result = await flow.async_step_init()

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert _scan_interval_default(result) == 10
    flow.hass.config_entries.async_get_known_entry.assert_called_once_with(
        "test-entry-id"
    )


@pytest.mark.asyncio
async def test_options_flow_init_shows_default_scan_interval():
    """Without stored options the default scan interval is shown."""
    flow = _flow()

    result = await flow.async_step_init()

    assert result["type"] == "form"
    assert _scan_interval_default(result) == DEFAULT_SCAN_INTERVAL


@pytest.mark.asyncio
async def test_options_flow_submit_creates_entry():
    """Submitting the form finishes the flow with the new options."""
    flow = _flow({CONF_SCAN_INTERVAL: 10})

    result = await flow.async_step_init({CONF_SCAN_INTERVAL: 15})

    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_SCAN_INTERVAL: 15}
