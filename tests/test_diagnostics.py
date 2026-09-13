"""Tests for the GitLab Monitor diagnostics."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant

from custom_components.gitlab_monitor.api import GitLabClient
from custom_components.gitlab_monitor.const import (
    CONF_PROJECTS,
    CONF_SCAN_INTERVAL_MINUTES,
)
from custom_components.gitlab_monitor.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import (
    TEST_PROJECT,
    TEST_PROJECT_ID,
    TEST_URL,
    make_commit,
    setup_integration,
)


async def test_diagnostics_redacts_token(
    hass: HomeAssistant, monkeypatch, mock_config_entry
):
    """The token is redacted while options and project data stay intact."""
    entry = await setup_integration(hass, monkeypatch, mock_config_entry)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["entry"]["data"][CONF_TOKEN] == REDACTED
    assert diag["entry"]["data"][CONF_URL] == TEST_URL
    assert diag["entry"]["data"][CONF_VERIFY_SSL] is True

    # Options are present and not redacted.
    assert diag["entry"]["options"] == {
        CONF_PROJECTS: [TEST_PROJECT],
        CONF_SCAN_INTERVAL_MINUTES: 5,
    }

    # Project data is keyed by the configured project key.
    assert set(diag["projects"]) == {TEST_PROJECT}
    project = diag["projects"][TEST_PROJECT]
    assert project["key"] == TEST_PROJECT
    assert project["info"]["id"] == TEST_PROJECT_ID
    assert project["info"]["path_with_namespace"] == TEST_PROJECT
    assert project["pipeline"]["status"] == "success"


async def test_diagnostics_redacts_commit_author_email(
    hass: HomeAssistant, monkeypatch, mock_config_entry
):
    """An author_email inside the last commit is redacted in the project dump."""
    entry = await setup_integration(hass, monkeypatch, mock_config_entry)

    def _first(
        project_id: int, resource: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if resource == "repository/commits":
            return make_commit(author_email="secret@example.com")
        return None

    # setup_integration re-patches client defaults internally, so the override
    # has to land on the class-level mock after setup, followed by a refresh.
    GitLabClient.async_get_first.side_effect = _first
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)

    commit = diag["projects"][TEST_PROJECT]["last_commit"]
    assert commit is not None
    assert commit["author_email"] == REDACTED
    assert "secret@example.com" not in str(diag)
    # Non-sensitive commit fields survive unredacted.
    assert commit["author_name"] == "Test User"
    assert commit["short_id"] == "abcdef12"
