"""Tests for GitLab Monitor integration setup, unload and reload."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gitlab_monitor.api import (
    GitLabAuthError,
    GitLabClient,
    GitLabConnectionError,
)
from custom_components.gitlab_monitor.const import (
    CONF_PROJECTS,
    CONF_SCAN_INTERVAL_MINUTES,
)
from custom_components.gitlab_monitor.coordinator import GitLabCoordinator

from .conftest import (
    TEST_PROJECT,
    TEST_PROJECT_ID,
    patch_client_defaults,
    setup_integration,
)


async def test_setup_entry_success(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A valid token and reachable instance load the entry fully."""
    entry = await setup_integration(hass, monkeypatch, mock_config_entry)

    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    assert isinstance(coordinator, GitLabCoordinator)
    assert coordinator.last_update_success
    assert TEST_PROJECT in coordinator.data
    assert coordinator.data[TEST_PROJECT].project_id == TEST_PROJECT_ID


async def test_setup_entry_auth_failed(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An invalid token during validation puts the entry in SETUP_ERROR."""
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_get_current_user",
        AsyncMock(side_effect=GitLabAuthError("HTTP 401")),
    )
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    # ConfigEntryAuthFailed must have started a reauth flow.
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_setup_entry_connection_failed(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An unreachable instance during validation puts the entry in SETUP_RETRY."""
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_get_current_user",
        AsyncMock(side_effect=GitLabConnectionError("cannot connect")),
    )
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_entry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Unloading a loaded entry unloads all platforms cleanly."""
    entry = await setup_integration(hass, monkeypatch, mock_config_entry)
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_options_update_reloads_entry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Changing options reloads the entry and applies the new interval."""
    entry = await setup_integration(hass, monkeypatch, mock_config_entry)
    assert entry.state is ConfigEntryState.LOADED

    # The class-level AsyncMock is shared, so its call count tracks reloads:
    # async_setup_entry validates the token exactly once per setup.
    user_mock = GitLabClient.async_get_current_user
    calls_before = user_mock.call_count

    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_PROJECTS: [TEST_PROJECT],
            CONF_SCAN_INTERVAL_MINUTES: 30,
        },
    )
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert user_mock.call_count == calls_before + 1
    # The reloaded coordinator picked up the new scan interval.
    assert entry.runtime_data.update_interval == timedelta(minutes=30)
