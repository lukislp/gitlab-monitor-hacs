"""Tests for the GitLab Monitor binary sensor platform."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.gitlab_monitor.api import GitLabClient
from custom_components.gitlab_monitor.binary_sensor import (
    BINARY_SENSORS,
    GitLabBinarySensor,
)
from custom_components.gitlab_monitor.const import DOMAIN
from custom_components.gitlab_monitor.coordinator import GitLabProjectData

from .conftest import (
    TEST_PROJECT,
    TEST_PROJECT_ID,
    make_pipeline,
    make_project_info,
    setup_integration,
)


def _entity_id(hass: HomeAssistant, entry, suffix: str) -> str:
    """Resolve a binary sensor's entity_id from its unique_id."""
    registry = er.async_get(hass)
    unique_id = f"{entry.entry_id}_{TEST_PROJECT_ID}_{suffix}"
    entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id)
    assert entity_id is not None, f"No binary_sensor registered for {unique_id}"
    return entity_id


async def _refresh_with_pipeline(
    hass: HomeAssistant, entry, pipeline: dict[str, Any] | None
) -> None:
    """Point the mocked client at a new latest-pipeline payload and refresh.

    setup_integration() re-patches the client defaults internally, so the
    pipeline response has to be swapped on the class-level mock afterwards.
    """
    GitLabClient.async_get_latest_pipeline.return_value = pipeline
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_pipeline_failed(hass: HomeAssistant, monkeypatch, mock_config_entry):
    """A failed pipeline turns the problem sensor on and the running sensor off."""
    entry = await setup_integration(hass, monkeypatch, mock_config_entry)
    await _refresh_with_pipeline(hass, entry, make_pipeline(status="failed"))

    failed = hass.states.get(_entity_id(hass, entry, "pipeline_failed"))
    running = hass.states.get(_entity_id(hass, entry, "pipeline_running"))
    assert failed is not None
    assert failed.state == STATE_ON
    assert running is not None
    assert running.state == STATE_OFF


async def test_pipeline_running(hass: HomeAssistant, monkeypatch, mock_config_entry):
    """A running pipeline turns the running sensor on and the failed sensor off."""
    entry = await setup_integration(hass, monkeypatch, mock_config_entry)
    await _refresh_with_pipeline(hass, entry, make_pipeline(status="running"))

    assert (
        hass.states.get(_entity_id(hass, entry, "pipeline_running")).state == STATE_ON
    )
    assert (
        hass.states.get(_entity_id(hass, entry, "pipeline_failed")).state == STATE_OFF
    )

    # "pending" is one of the other running-ish states.
    await _refresh_with_pipeline(hass, entry, make_pipeline(status="pending"))
    assert (
        hass.states.get(_entity_id(hass, entry, "pipeline_running")).state == STATE_ON
    )
    assert (
        hass.states.get(_entity_id(hass, entry, "pipeline_failed")).state == STATE_OFF
    )


async def test_pipeline_success(hass: HomeAssistant, monkeypatch, mock_config_entry):
    """A successful pipeline (the fixture default) leaves both sensors off."""
    entry = await setup_integration(hass, monkeypatch, mock_config_entry)

    assert (
        hass.states.get(_entity_id(hass, entry, "pipeline_failed")).state == STATE_OFF
    )
    assert (
        hass.states.get(_entity_id(hass, entry, "pipeline_running")).state == STATE_OFF
    )


async def test_no_pipeline(hass: HomeAssistant, monkeypatch, mock_config_entry):
    """Without any pipeline the is_on_fn returns None, i.e. state unknown."""
    entry = await setup_integration(hass, monkeypatch, mock_config_entry)
    await _refresh_with_pipeline(hass, entry, None)

    assert (
        hass.states.get(_entity_id(hass, entry, "pipeline_failed")).state
        == STATE_UNKNOWN
    )
    assert (
        hass.states.get(_entity_id(hass, entry, "pipeline_running")).state
        == STATE_UNKNOWN
    )


async def test_device_classes_and_unique_ids(
    hass: HomeAssistant, monkeypatch, mock_config_entry
):
    """Both sensors expose the expected device classes and unique_id suffixes."""
    entry = await setup_integration(hass, monkeypatch, mock_config_entry)
    registry = er.async_get(hass)

    failed_id = _entity_id(hass, entry, "pipeline_failed")
    running_id = _entity_id(hass, entry, "pipeline_running")

    failed_entry = registry.async_get(failed_id)
    running_entry = registry.async_get(running_id)
    assert failed_entry.unique_id.endswith(f"_{TEST_PROJECT_ID}_pipeline_failed")
    assert running_entry.unique_id.endswith(f"_{TEST_PROJECT_ID}_pipeline_running")

    failed_state = hass.states.get(failed_id)
    running_state = hass.states.get(running_id)
    assert failed_state.attributes["device_class"] == BinarySensorDeviceClass.PROBLEM
    assert running_state.attributes["device_class"] == BinarySensorDeviceClass.RUNNING


def test_is_on_returns_none_when_project_unavailable() -> None:
    """is_on's own project-is-None guard - a direct unit test, since HA's entity base
    class doesn't call is_on at all once `available` is already False (the normal path
    a real integration test would exercise never actually reaches this line)."""
    coordinator = Mock()
    coordinator.config_entry.entry_id = "entry123"
    coordinator.data = {
        TEST_PROJECT: GitLabProjectData(key=TEST_PROJECT, info=make_project_info())
    }
    entity = GitLabBinarySensor(coordinator, TEST_PROJECT, BINARY_SENSORS[0])

    coordinator.data = {}  # project dropped out of the data after construction
    assert entity.is_on is None
