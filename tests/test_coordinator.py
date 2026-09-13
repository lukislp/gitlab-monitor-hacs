"""Tests for the GitLab Monitor data update coordinator."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gitlab_monitor.api import (
    GitLabApiError,
    GitLabAuthError,
    GitLabClient,
    GitLabConnectionError,
    GitLabNotFoundError,
)
from custom_components.gitlab_monitor.const import (
    CONF_PROJECTS,
    CONF_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from custom_components.gitlab_monitor.coordinator import GitLabCoordinator

from .conftest import (
    TEST_PROJECT,
    TEST_PROJECT_ID,
    TEST_TOKEN,
    TEST_URL,
    make_commit,
    make_issue,
    make_merge_request,
    make_pipeline,
    make_project_info,
    make_release,
    patch_client_defaults,
)


def _make_entry(projects: list[str]) -> MockConfigEntry:
    """Build a config entry with the given project keys."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="GitLab (gitlab.example.com)",
        data={
            CONF_URL: TEST_URL,
            CONF_TOKEN: TEST_TOKEN,
            CONF_VERIFY_SSL: True,
        },
        options={
            CONF_PROJECTS: projects,
            CONF_SCAN_INTERVAL_MINUTES: 5,
        },
    )


def _make_coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> GitLabCoordinator:
    """Add the entry to hass and build a coordinator around a (patched) client."""
    entry.add_to_hass(hass)
    client = GitLabClient(async_get_clientsession(hass), TEST_URL, TEST_TOKEN)
    return GitLabCoordinator(hass, entry, client)


async def test_full_data_mapping(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every GitLabProjectData field maps from the corresponding client call."""
    patch_client_defaults(monkeypatch)

    pipeline = make_pipeline(id=1234, status="failed")
    commit = make_commit(id="feedface", short_id="feedface"[:8])
    release = make_release(name="v2.0.0", tag_name="v2.0.0")
    tag = {"name": "v2.0.0", "target": "feedface"}
    issue = make_issue(iid=55, title="Latest issue")
    mr = make_merge_request(iid=66, title="Latest MR")
    deployment = {"id": 77, "status": "success", "environment": {"name": "prod"}}
    stats = {"fetches": {"total": 10, "days": [{"count": 10, "date": "2026-08-04"}]}}

    async def first_with_total(
        project_id: int, endpoint: str, params: dict[str, Any] | None = None
    ) -> tuple[Any, int, bool]:
        assert project_id == TEST_PROJECT_ID
        if endpoint == "merge_requests":
            assert params == {"state": "opened"}
            return (mr, 7, False)
        if endpoint == "releases":
            return (release, 3, True)
        assert endpoint == "repository/tags"
        return (tag, 9, True)

    async def count(
        project_id: int, endpoint: str, params: dict[str, Any] | None = None
    ) -> tuple[int, bool]:
        return {
            "repository/branches": (12, False),
            "repository/contributors": (4, True),
            "members/all": (6, True),
        }[endpoint]

    async def get_first(
        project_id: int, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if endpoint == "repository/commits":
            return commit
        if endpoint == "issues":
            return issue
        assert endpoint == "deployments"
        assert params == {"order_by": "created_at", "sort": "desc"}
        return deployment

    monkeypatch.setattr(
        GitLabClient, "async_get_latest_pipeline", AsyncMock(return_value=pipeline)
    )
    monkeypatch.setattr(
        GitLabClient,
        "async_get_first_with_total",
        AsyncMock(side_effect=first_with_total),
    )
    monkeypatch.setattr(GitLabClient, "async_count", AsyncMock(side_effect=count))
    monkeypatch.setattr(
        GitLabClient, "async_get_first", AsyncMock(side_effect=get_first)
    )
    monkeypatch.setattr(
        GitLabClient, "async_get_project_statistics", AsyncMock(return_value=stats)
    )

    coordinator = _make_coordinator(hass, _make_entry([TEST_PROJECT]))
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    data = coordinator.data[TEST_PROJECT]
    assert data.key == TEST_PROJECT
    assert data.info == make_project_info()
    assert data.project_id == TEST_PROJECT_ID
    assert data.pipeline == pipeline
    # mrs triple unpacks into latest MR + open count + exactness.
    assert data.latest_merge_request == mr
    assert data.open_merge_requests == 7
    assert data.open_merge_requests_exact is False
    assert data.branches == 12
    assert data.branches_exact is False
    assert data.last_commit == commit
    assert data.latest_release == release
    assert data.releases_count == 3
    assert data.latest_tag == tag
    assert data.tags_count == 9
    assert data.latest_issue == issue
    assert data.latest_deployment == deployment
    assert data.contributors == 4
    assert data.members == 6
    assert data.fetch_statistics == stats


async def test_partial_subfetch_failure_keeps_rest(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing sub-fetch nulls only its field; the project still updates."""
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_get_latest_pipeline",
        AsyncMock(side_effect=GitLabApiError("HTTP 500")),
    )

    coordinator = _make_coordinator(hass, _make_entry([TEST_PROJECT]))
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    data = coordinator.data[TEST_PROJECT]
    assert data.pipeline is None
    # Everything else came through from the default mocks.
    assert data.info == make_project_info()
    assert data.open_merge_requests == 0
    assert data.open_merge_requests_exact is True
    assert data.branches == 0
    assert data.contributors == 0
    assert data.members == 0


async def test_stale_data_kept_on_project_failure(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project that stops responding keeps its previous data."""
    patch_client_defaults(monkeypatch)

    infos = {
        "group/alpha": make_project_info(id=101, path_with_namespace="group/alpha"),
        "group/beta": make_project_info(id=202, path_with_namespace="group/beta"),
    }
    failing: set[str] = set()

    async def get_project(key: str) -> dict[str, Any]:
        if key in failing:
            raise GitLabConnectionError(f"cannot reach {key}")
        return infos[key]

    monkeypatch.setattr(
        GitLabClient, "async_get_project", AsyncMock(side_effect=get_project)
    )

    coordinator = _make_coordinator(hass, _make_entry(["group/alpha", "group/beta"]))
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert set(coordinator.data) == {"group/alpha", "group/beta"}
    old_beta = coordinator.data["group/beta"]

    # Beta becomes unreachable; alpha keeps working.
    failing.add("group/beta")
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert set(coordinator.data) == {"group/alpha", "group/beta"}
    # Beta's entry is exactly the stale object from the previous refresh.
    assert coordinator.data["group/beta"] is old_beta
    # Alpha was fetched fresh.
    assert coordinator.data["group/alpha"] is not old_beta
    assert coordinator.data["group/alpha"].project_id == 101


async def test_all_projects_failed_raises_update_failed(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When every project fails, the refresh fails as a whole."""
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_get_project",
        AsyncMock(side_effect=GitLabConnectionError("cannot connect")),
    )

    coordinator = _make_coordinator(hass, _make_entry([TEST_PROJECT]))
    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)


async def test_all_projects_auth_failed_raises_auth_failed(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When every project auth-fails, ConfigEntryAuthFailed is surfaced."""
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_get_project",
        AsyncMock(side_effect=GitLabAuthError("HTTP 401")),
    )

    coordinator = _make_coordinator(hass, _make_entry([TEST_PROJECT]))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)


async def test_no_projects_configured_raises_update_failed(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty project list fails the refresh."""
    patch_client_defaults(monkeypatch)

    coordinator = _make_coordinator(hass, _make_entry([]))
    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert "No projects configured" in str(coordinator.last_exception)


async def test_partial_project_failure_keeps_good_project(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One good project and one 404 project: the good one survives, 404 is logged."""
    patch_client_defaults(monkeypatch)

    async def get_project(key: str) -> dict[str, Any]:
        if key == "group/gone":
            raise GitLabNotFoundError("Not found: group/gone")
        return make_project_info()

    monkeypatch.setattr(
        GitLabClient, "async_get_project", AsyncMock(side_effect=get_project)
    )

    coordinator = _make_coordinator(hass, _make_entry([TEST_PROJECT, "group/gone"]))
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert TEST_PROJECT in coordinator.data
    assert "group/gone" not in coordinator.data
    assert "Error updating project group/gone" in caplog.text


async def test_guarded_subfetch_auth_error_fails_only_that_project(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GUARDED sub-fetch (not the top-level async_get_project call) raising
    GitLabAuthError propagates out of _guarded (re-raised, not swallowed) - unlike
    every other exception type, which _guarded logs and turns into a None field.
    A good project alongside it still comes through fine."""
    patch_client_defaults(monkeypatch)

    bad_id = 999

    async def get_project(key: str) -> dict[str, Any]:
        if key == "group/bad":
            return make_project_info(id=bad_id, path_with_namespace="group/bad")
        return make_project_info()

    async def get_pipeline(project_id: int):
        if project_id == bad_id:
            raise GitLabAuthError("HTTP 401")

    monkeypatch.setattr(
        GitLabClient, "async_get_project", AsyncMock(side_effect=get_project)
    )
    monkeypatch.setattr(
        GitLabClient, "async_get_latest_pipeline", AsyncMock(side_effect=get_pipeline)
    )

    coordinator = _make_coordinator(hass, _make_entry([TEST_PROJECT, "group/bad"]))
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert TEST_PROJECT in coordinator.data
    assert "group/bad" not in coordinator.data
