"""Tests for the GitLab Monitor button platform."""
from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.gitlab_monitor.api import GitLabClient
from custom_components.gitlab_monitor.const import DOMAIN

from .conftest import TEST_PROJECT_ID, setup_integration


def _button_entity_id(hass: HomeAssistant, entry) -> str:
    """Resolve the refresh button's entity_id from its unique_id."""
    registry = er.async_get(hass)
    unique_id = f"{entry.entry_id}_{TEST_PROJECT_ID}_refresh"
    entity_id = registry.async_get_entity_id("button", DOMAIN, unique_id)
    assert entity_id is not None, f"No button registered for {unique_id}"
    return entity_id


async def test_refresh_button_registered(
    hass: HomeAssistant, monkeypatch, mock_config_entry
):
    """One diagnostic refresh button exists per configured project."""
    entry = await setup_integration(hass, monkeypatch, mock_config_entry)
    registry = er.async_get(hass)

    entity_id = _button_entity_id(hass, entry)
    reg_entry = registry.async_get(entity_id)
    assert reg_entry is not None
    assert reg_entry.unique_id == f"{entry.entry_id}_{TEST_PROJECT_ID}_refresh"
    assert reg_entry.entity_category is EntityCategory.DIAGNOSTIC
    assert hass.states.get(entity_id) is not None

    # It is the only button produced by the integration for this single project.
    buttons = [
        e
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "button"
    ]
    assert len(buttons) == 1


async def test_refresh_button_press_triggers_refresh(
    hass: HomeAssistant, monkeypatch, mock_config_entry
):
    """Pressing the button requests a coordinator refresh (new client fetches)."""
    entry = await setup_integration(hass, monkeypatch, mock_config_entry)
    entity_id = _button_entity_id(hass, entry)

    calls_before = GitLabClient.async_get_project.await_count
    assert calls_before >= 1  # the initial refresh during setup

    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert GitLabClient.async_get_project.await_count == calls_before + 1
