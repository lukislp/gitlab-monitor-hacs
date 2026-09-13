"""Shared fixtures and test-data builders for the GitLab Monitor test suite."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gitlab_monitor.api import GitLabClient
from custom_components.gitlab_monitor.const import (
    CONF_PROJECTS,
    CONF_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"

TEST_URL = "https://gitlab.example.com"
TEST_TOKEN = "glpat-test-token"
TEST_PROJECT = "group/project"
TEST_PROJECT_ID = 42
TEST_USER = {"id": 7, "username": "tester", "name": "Test User"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components/ discoverable for every test in this suite."""
    return


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A config entry matching what the real config flow produces."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="GitLab (gitlab.example.com)",
        data={
            CONF_URL: TEST_URL,
            CONF_TOKEN: TEST_TOKEN,
            CONF_VERIFY_SSL: True,
        },
        options={
            CONF_PROJECTS: [TEST_PROJECT],
            CONF_SCAN_INTERVAL_MINUTES: 5,
        },
        unique_id=f"gitlab.example.com_{TEST_USER['id']}",
    )


def make_project_info(**overrides: Any) -> dict[str, Any]:
    """Raw /projects/:id response (simple=false, statistics=true) as GitLab returns it."""
    info: dict[str, Any] = {
        "id": TEST_PROJECT_ID,
        "path_with_namespace": TEST_PROJECT,
        "name": "project",
        "web_url": f"{TEST_URL}/{TEST_PROJECT}",
        "star_count": 5,
        "forks_count": 2,
        "open_issues_count": 3,
        "last_activity_at": "2026-08-01T10:00:00.000Z",
        "default_branch": "main",
        "visibility": "private",
        "statistics": {
            "commit_count": 321,
            "repository_size": 1048576,
            "storage_size": 2097152,
        },
    }
    info.update(overrides)
    return info


def make_pipeline(**overrides: Any) -> dict[str, Any]:
    """Raw single-pipeline response (details endpoint)."""
    pipeline: dict[str, Any] = {
        "id": 9001,
        "status": "success",
        "ref": "main",
        "sha": "abcdef1234567890",
        "source": "push",
        "web_url": f"{TEST_URL}/{TEST_PROJECT}/-/pipelines/9001",
        "created_at": "2026-08-01T09:00:00.000Z",
        "updated_at": "2026-08-01T09:10:00.000Z",
        "finished_at": "2026-08-01T09:10:00.000Z",
        "duration": 600,
        "coverage": "87.5",
        "user": {"username": "tester", "name": "Test User"},
    }
    pipeline.update(overrides)
    return pipeline


def make_commit(**overrides: Any) -> dict[str, Any]:
    """Raw repository/commits[0] item."""
    commit: dict[str, Any] = {
        "id": "abcdef1234567890",
        "short_id": "abcdef12",
        "title": "Fix the flux capacitor",
        "message": "Fix the flux capacitor\n\nLonger body.",
        "author_name": "Test User",
        "created_at": "2026-08-01T08:00:00.000Z",
        "web_url": f"{TEST_URL}/{TEST_PROJECT}/-/commit/abcdef12",
    }
    commit.update(overrides)
    return commit


def make_release(**overrides: Any) -> dict[str, Any]:
    """Raw releases[0] item."""
    release: dict[str, Any] = {
        "name": "v1.2.3",
        "tag_name": "v1.2.3",
        "released_at": "2026-07-30T12:00:00.000Z",
        "_links": {"self": f"{TEST_URL}/{TEST_PROJECT}/-/releases/v1.2.3"},
    }
    release.update(overrides)
    return release


def make_issue(**overrides: Any) -> dict[str, Any]:
    """Raw issues[0] item."""
    issue: dict[str, Any] = {
        "iid": 12,
        "title": "Something is broken",
        "state": "opened",
        "author": {"username": "tester"},
        "labels": ["bug"],
        "created_at": "2026-07-29T12:00:00.000Z",
        "web_url": f"{TEST_URL}/{TEST_PROJECT}/-/issues/12",
    }
    issue.update(overrides)
    return issue


def make_merge_request(**overrides: Any) -> dict[str, Any]:
    """Raw merge_requests[0] item."""
    mr: dict[str, Any] = {
        "iid": 8,
        "title": "Add a feature",
        "state": "opened",
        "source_branch": "feature/thing",
        "target_branch": "main",
        "author": {"username": "tester"},
        "created_at": "2026-07-28T12:00:00.000Z",
        "web_url": f"{TEST_URL}/{TEST_PROJECT}/-/merge_requests/8",
    }
    mr.update(overrides)
    return mr


def patch_client_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch every GitLabClient fetch method with benign defaults.

    Individual tests override single methods afterwards. Patching the class (not an
    instance) is required because both async_setup_entry and the config flow construct
    their own clients internally - there is no injection seam.
    """
    monkeypatch.setattr(
        GitLabClient, "async_get_current_user", AsyncMock(return_value=dict(TEST_USER))
    )
    monkeypatch.setattr(
        GitLabClient, "async_get_project", AsyncMock(return_value=make_project_info())
    )
    monkeypatch.setattr(
        GitLabClient,
        "async_get_latest_pipeline",
        AsyncMock(return_value=make_pipeline()),
    )
    monkeypatch.setattr(
        GitLabClient,
        "async_get_first_with_total",
        AsyncMock(return_value=(None, 0, True)),
    )
    monkeypatch.setattr(GitLabClient, "async_count", AsyncMock(return_value=(0, True)))
    monkeypatch.setattr(GitLabClient, "async_get_first", AsyncMock(return_value=None))
    monkeypatch.setattr(
        GitLabClient,
        "async_get_project_statistics",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        GitLabClient,
        "async_list_membership_projects",
        AsyncMock(return_value=[make_project_info()]),
    )


async def setup_integration(
    hass,
    monkeypatch: pytest.MonkeyPatch,
    entry: MockConfigEntry,
) -> MockConfigEntry:
    """Patch the GitLab client and set up a config entry end-to-end."""
    patch_client_defaults(monkeypatch)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# aiohttp 3.14 (pinned by Home Assistant 2026.9) added a required `stream_writer` argument to
# ClientResponse.__init__, which aioresponses 0.7.9 (the latest release) does not pass yet -
# every mocked request would fail with a TypeError. Until aioresponses catches up, give the
# class it instantiates a default for that argument. Test-only; nothing in the integration
# touches this.
import aiohttp as _aiohttp
import aioresponses.core as _aioresponses_core


class _NoUploadWriter:
    """Stands in for the request body writer of an already-sent (mocked) request."""

    output_size = 0


class _CompatClientResponse(_aiohttp.ClientResponse):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stream_writer", _NoUploadWriter())
        super().__init__(*args, **kwargs)


_aioresponses_core.ClientResponse = _CompatClientResponse
