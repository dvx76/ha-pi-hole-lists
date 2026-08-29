"""Tests for the Gravity update binary sensor."""

from unittest.mock import MagicMock

import pytest

from custom_components.pi_hole_lists.binary_sensor import (
    GravityUpdateSensor,
    async_setup_entry,
)
from custom_components.pi_hole_lists.const import (
    GRAVITY_FAILED,
    GRAVITY_IDLE,
    GRAVITY_PENDING,
    GRAVITY_RUNNING,
)

ENTRY_ID = "entry-1"


def _coordinator(state: str) -> MagicMock:
    """Build a coordinator mock carrying the given gravity state."""
    coordinator = MagicMock()
    coordinator.gravity_state = state
    return coordinator


def _entry() -> MagicMock:
    """Build a config entry mock."""
    entry = MagicMock()
    entry.entry_id = ENTRY_ID
    entry.data = {"url": "http://pi.hole:8081"}
    return entry


def _sensor(state: str) -> GravityUpdateSensor:
    """Build a sensor with a stubbed state writer."""
    sensor = GravityUpdateSensor(_coordinator(state), _entry())
    sensor.async_write_ha_state = MagicMock()
    return sensor


def test_is_on_maps_gravity_states():
    """The sensor is on while a rebuild is pending or running."""
    assert _sensor(GRAVITY_PENDING).is_on is True
    assert _sensor(GRAVITY_RUNNING).is_on is True
    assert _sensor(GRAVITY_IDLE).is_on is False
    assert _sensor(GRAVITY_FAILED).is_on is False


def test_status_attribute():
    """The raw state machine status is exposed as an attribute."""
    sensor = _sensor(GRAVITY_RUNNING)
    assert sensor.extra_state_attributes == {"status": GRAVITY_RUNNING}

    sensor.coordinator.gravity_state = GRAVITY_FAILED
    assert sensor.extra_state_attributes == {"status": GRAVITY_FAILED}


def test_unique_id_and_name():
    """The unique id is entry-scoped and the name is fixed."""
    sensor = _sensor(GRAVITY_IDLE)

    assert sensor.unique_id == f"{ENTRY_ID}-gravity-update"
    assert sensor.name == "Gravity update"


def test_device_info_per_entry():
    """The sensor lives on the same device as the list switches."""
    info = _sensor(GRAVITY_IDLE).device_info

    assert info["identifiers"] == {("pi_hole_lists", ENTRY_ID)}
    assert info["name"] == "Pi-hole lists (pi.hole:8081)"


def test_handle_coordinator_update_writes_state():
    """A coordinator notification re-renders the sensor."""
    sensor = _sensor(GRAVITY_IDLE)

    sensor._handle_coordinator_update()

    sensor.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_setup_entry_creates_one_entity():
    """Exactly one Gravity update sensor per config entry."""
    entry = _entry()
    entry.runtime_data = MagicMock(coordinator=_coordinator(GRAVITY_PENDING))
    add_entities = MagicMock()

    await async_setup_entry(MagicMock(), entry, add_entities)

    [entity] = list(add_entities.call_args.args[0])
    assert isinstance(entity, GravityUpdateSensor)
    assert entity.unique_id == f"{ENTRY_ID}-gravity-update"
    assert entity.is_on is True
