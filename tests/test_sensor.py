"""Tests for the GitLab Monitor sensor platform."""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import RELOAD_AFTER_UPDATE_DELAY
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.gitlab_monitor.api import GitLabClient
from custom_components.gitlab_monitor.const import (
    ATTR_MANUFACTURER,
    CONF_PROJECTS,
    CONF_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from custom_components.gitlab_monitor.coordinator import GitLabProjectData
from custom_components.gitlab_monitor.sensor import SENSORS

from .conftest import (
    TEST_PROJECT,
    TEST_PROJECT_ID,
    TEST_URL,
    make_commit,
    make_issue,
    make_merge_request,
    make_pipeline,
    make_project_info,
    make_release,
    patch_client_defaults,
)

TEST_TAG = {
    "name": "v1.2.3",
    "message": "Release v1.2.3",
    "target": "abcdef1234567890",
    "commit": {"created_at": "2026-07-30T11:00:00.000Z"},
}

TEST_DEPLOYMENT = {
    "id": 501,
    "status": "success",
    "ref": "main",
    "sha": "abcdef1234567890",
    "environment": {"name": "production"},
    "user": {"username": "tester"},
    "created_at": "2026-07-31T12:00:00.000Z",
    "updated_at": "2026-07-31T12:05:00.000Z",
}

TEST_FETCH_STATISTICS = {
    "fetches": {"total": 120, "days": [{"count": 4, "date": "2026-08-01"}]}
}

DISABLED_KEYS = {
    d.key for d in SENSORS if not d.entity_registry_enabled_default
}
ENABLED_KEYS = {d.key for d in SENSORS} - DISABLED_KEYS


def _seed_rich_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override client methods so every sensor data source has data.

    Must run after patch_client_defaults, which installs the benign defaults.
    """

    def first_with_total(
        project_id: int, resource: str, params: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, int, bool]:
        if resource == "merge_requests":
            return (make_merge_request(), 4, True)
        if resource == "releases":
            return (make_release(), 2, True)
        if resource == "repository/tags":
            return (dict(TEST_TAG), 6, True)
        raise AssertionError(f"Unexpected paginated resource: {resource}")

    def count(
        project_id: int, resource: str, params: dict[str, Any] | None = None
    ) -> tuple[int, bool]:
        if resource == "repository/branches":
            return (3, True)
        if resource == "repository/contributors":
            return (5, True)
        if resource == "members/all":
            return (2, True)
        raise AssertionError(f"Unexpected counted resource: {resource}")

    def get_first(
        project_id: int, resource: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if resource == "repository/commits":
            return make_commit(committed_date="2026-08-01T08:00:00.000Z")
        if resource == "issues":
            return make_issue()
        if resource == "deployments":
            return dict(TEST_DEPLOYMENT)
        raise AssertionError(f"Unexpected first-item resource: {resource}")

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
        GitLabClient,
        "async_get_project_statistics",
        AsyncMock(return_value=dict(TEST_FETCH_STATISTICS)),
    )


async def _setup(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    entry: MockConfigEntry,
    *,
    rich: bool = False,
    overrides: dict[str, AsyncMock] | None = None,
) -> MockConfigEntry:
    """Patch the client, optionally seed rich data, and set up the entry."""
    patch_client_defaults(monkeypatch)
    if rich:
        _seed_rich_data(monkeypatch)
    for name, mock in (overrides or {}).items():
        monkeypatch.setattr(GitLabClient, name, mock)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    suffix: str,
    project_id: int = TEST_PROJECT_ID,
) -> str:
    """Resolve an entity_id from the registry via its unique_id."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{project_id}_{suffix}"
    )
    assert entity_id is not None, f"No registry entry for suffix {suffix}"
    return entity_id


async def test_enabled_sensor_states(hass, monkeypatch, mock_config_entry) -> None:
    """All enabled-by-default sensors report the seeded values."""
    entry = await _setup(hass, monkeypatch, mock_config_entry, rich=True)

    expected = {
        "pipeline_status": "success",
        "coverage": "87.5",
        "open_issues": "3",
        "open_merge_requests": "4",
        "latest_issue": "Something is broken",
        "latest_merge_request": "Add a feature",
        "latest_deployment": "production",
        "branches": "3",
        "stars": "5",
        "forks": "2",
        "contributors": "5",
        "members": "2",
        "commit_count": "321",
        "last_commit": "2026-08-01T08:00:00+00:00",
        "last_activity": "2026-08-01T10:00:00+00:00",
        "latest_release": "v1.2.3",
    }
    assert set(expected) == ENABLED_KEYS

    for suffix, value in expected.items():
        state = hass.states.get(_entity_id(hass, entry, suffix))
        assert state is not None, f"Missing state for {suffix}"
        assert state.state == value, f"{suffix}: {state.state!r} != {value!r}"


async def test_pipeline_status_attributes(
    hass, monkeypatch, mock_config_entry
) -> None:
    """The pipeline status sensor exposes the pipeline details as attributes."""
    entry = await _setup(hass, monkeypatch, mock_config_entry, rich=True)

    state = hass.states.get(_entity_id(hass, entry, "pipeline_status"))
    assert state is not None
    attrs = state.attributes
    assert attrs["pipeline_id"] == 9001
    assert attrs["ref"] == "main"
    assert attrs["sha"] == "abcdef1234567890"
    assert attrs["source"] == "push"
    assert attrs["web_url"] == f"{TEST_URL}/{TEST_PROJECT}/-/pipelines/9001"
    assert attrs["triggered_by"] == "tester"
    assert attrs["finished_at"] == "2026-08-01T09:10:00.000Z"
    # Enum device class carries the allowed options.
    assert "success" in attrs["options"]


async def test_latest_issue_and_merge_request_attributes(
    hass, monkeypatch, mock_config_entry
) -> None:
    """Latest issue / MR sensors use the title as state and detail attributes."""
    entry = await _setup(hass, monkeypatch, mock_config_entry, rich=True)

    issue = hass.states.get(_entity_id(hass, entry, "latest_issue"))
    assert issue is not None
    assert issue.state == "Something is broken"
    assert issue.attributes["number"] == 12
    assert issue.attributes["state"] == "opened"
    assert issue.attributes["author"] == "tester"
    assert issue.attributes["labels"] == ["bug"]
    assert issue.attributes["web_url"] == f"{TEST_URL}/{TEST_PROJECT}/-/issues/12"

    mr = hass.states.get(_entity_id(hass, entry, "latest_merge_request"))
    assert mr is not None
    assert mr.state == "Add a feature"
    assert mr.attributes["number"] == 8
    assert mr.attributes["author"] == "tester"
    assert mr.attributes["source_branch"] == "feature/thing"
    assert mr.attributes["target_branch"] == "main"
    assert (
        mr.attributes["web_url"] == f"{TEST_URL}/{TEST_PROJECT}/-/merge_requests/8"
    )

    release = hass.states.get(_entity_id(hass, entry, "latest_release"))
    assert release is not None
    assert release.state == "v1.2.3"
    assert release.attributes["total_releases"] == 2
    assert (
        release.attributes["web_url"]
        == f"{TEST_URL}/{TEST_PROJECT}/-/releases/v1.2.3"
    )

    commit = hass.states.get(_entity_id(hass, entry, "last_commit"))
    assert commit is not None
    assert commit.attributes["short_id"] == "abcdef12"
    assert commit.attributes["title"] == "Fix the flux capacitor"
    assert commit.attributes["ref"] == "main"

    activity = hass.states.get(_entity_id(hass, entry, "last_activity"))
    assert activity is not None
    assert activity.attributes["project_id"] == TEST_PROJECT_ID
    assert activity.attributes["default_branch"] == "main"
    assert activity.attributes["visibility"] == "private"


async def test_count_is_exact_attributes(hass, monkeypatch, mock_config_entry) -> None:
    """Count sensors surface whether the total came from an exact header."""
    entry = await _setup(hass, monkeypatch, mock_config_entry, rich=True)

    mrs = hass.states.get(_entity_id(hass, entry, "open_merge_requests"))
    assert mrs is not None
    assert mrs.attributes["count_is_exact"] is True

    branches = hass.states.get(_entity_id(hass, entry, "branches"))
    assert branches is not None
    assert branches.attributes["count_is_exact"] is True


async def test_inexact_counts(hass, monkeypatch, mock_config_entry) -> None:
    """Lower-bound counts are exposed with count_is_exact False."""
    entry = await _setup(
        hass,
        monkeypatch,
        mock_config_entry,
        overrides={
            "async_count": AsyncMock(return_value=(100, False)),
            "async_get_first_with_total": AsyncMock(
                return_value=(make_merge_request(), 100, False)
            ),
        },
    )

    branches = hass.states.get(_entity_id(hass, entry, "branches"))
    assert branches is not None
    assert branches.state == "100"
    assert branches.attributes["count_is_exact"] is False

    mrs = hass.states.get(_entity_id(hass, entry, "open_merge_requests"))
    assert mrs is not None
    assert mrs.state == "100"
    assert mrs.attributes["count_is_exact"] is False


async def test_absent_data_sources_report_unknown(
    hass, monkeypatch, mock_config_entry
) -> None:
    """Sensors whose data source has no data exist but report unknown."""
    entry = await _setup(
        hass,
        monkeypatch,
        mock_config_entry,
        overrides={"async_get_latest_pipeline": AsyncMock(return_value=None)},
    )

    for suffix in (
        "pipeline_status",
        "coverage",
        "latest_release",
        "latest_issue",
        "latest_merge_request",
        "latest_deployment",
        "last_commit",
    ):
        state = hass.states.get(_entity_id(hass, entry, suffix))
        assert state is not None, f"Missing state for {suffix}"
        assert state.state == STATE_UNKNOWN, f"{suffix} is {state.state!r}"

    # The MR count itself came back as an exact zero, not unknown.
    mrs = hass.states.get(_entity_id(hass, entry, "open_merge_requests"))
    assert mrs is not None
    assert mrs.state == "0"


async def test_coverage_unparseable(hass, monkeypatch, mock_config_entry) -> None:
    """A missing or malformed coverage value yields an unknown state."""
    entry = await _setup(
        hass,
        monkeypatch,
        mock_config_entry,
        overrides={
            "async_get_latest_pipeline": AsyncMock(
                return_value=make_pipeline(coverage=None)
            )
        },
    )

    coverage = hass.states.get(_entity_id(hass, entry, "coverage"))
    assert coverage is not None
    assert coverage.state == STATE_UNKNOWN
    # The pipeline itself is still reported.
    status = hass.states.get(_entity_id(hass, entry, "pipeline_status"))
    assert status is not None
    assert status.state == "success"


async def test_disabled_by_default_entities(
    hass, monkeypatch, mock_config_entry
) -> None:
    """Diagnostic-style sensors are registered but disabled by the integration."""
    entry = await _setup(hass, monkeypatch, mock_config_entry, rich=True)
    registry = er.async_get(hass)

    assert DISABLED_KEYS == {
        "pipeline_duration",
        "releases_count",
        "latest_tag",
        "tags_count",
        "repository_size",
        "storage_size",
        "fetches_30d",
    }
    for suffix in DISABLED_KEYS:
        unique_id = f"{entry.entry_id}_{TEST_PROJECT_ID}_{suffix}"
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        assert entity_id is not None, f"No registry entry for {suffix}"
        entity_entry = registry.async_get(entity_id)
        assert entity_entry is not None
        assert entity_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert hass.states.get(entity_id) is None, f"{suffix} has a state"


async def test_enabling_disabled_entities(
    hass, monkeypatch, mock_config_entry
) -> None:
    """Enabling a default-disabled sensor and reloading produces its state."""
    entry = await _setup(hass, monkeypatch, mock_config_entry, rich=True)
    registry = er.async_get(hass)

    entity_ids = {
        suffix: _entity_id(hass, entry, suffix)
        for suffix in ("latest_tag", "releases_count", "fetches_30d")
    }
    for entity_id in entity_ids.values():
        registry.async_update_entity(entity_id, disabled_by=None)
    await hass.async_block_till_done()
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=RELOAD_AFTER_UPDATE_DELAY)
    )
    await hass.async_block_till_done()

    tag = hass.states.get(entity_ids["latest_tag"])
    assert tag is not None
    assert tag.state == "v1.2.3"
    assert tag.attributes["message"] == "Release v1.2.3"
    assert tag.attributes["target"] == "abcdef1234567890"
    assert tag.attributes["total_tags"] == 6

    releases = hass.states.get(entity_ids["releases_count"])
    assert releases is not None
    assert releases.state == "2"

    fetches = hass.states.get(entity_ids["fetches_30d"])
    assert fetches is not None
    assert fetches.state == "120"
    assert fetches.attributes["days"] == [{"count": 4, "date": "2026-08-01"}]


async def test_unique_ids_and_device(hass, monkeypatch, mock_config_entry) -> None:
    """Every sensor uses {entry_id}_{project_id}_{key} and shares one device."""
    entry = await _setup(hass, monkeypatch, mock_config_entry, rich=True)
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    device = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_{TEST_PROJECT_ID}")}
    )
    assert device is not None
    assert device.name == TEST_PROJECT
    assert device.manufacturer == ATTR_MANUFACTURER
    assert device.model == "Repository"
    assert device.configuration_url == f"{TEST_URL}/{TEST_PROJECT}"

    entries = [
        e
        for e in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if e.domain == "sensor"
    ]
    assert len(entries) == len(SENSORS) == 23
    expected_unique_ids = {
        f"{entry.entry_id}_{TEST_PROJECT_ID}_{d.key}" for d in SENSORS
    }
    assert {e.unique_id for e in entries} == expected_unique_ids
    assert all(e.device_id == device.id for e in entries)


async def test_dynamic_project_addition_via_listener(
    hass, monkeypatch, mock_config_entry
) -> None:
    """Projects appearing in coordinator data get entities without a reload."""
    entry = await _setup(hass, monkeypatch, mock_config_entry, rich=True)
    coordinator = entry.runtime_data

    other_info = make_project_info(
        id=99,
        path_with_namespace="group/other",
        web_url=f"{TEST_URL}/group/other",
        star_count=9,
    )
    data = dict(coordinator.data)
    data["group/other"] = GitLabProjectData(key="group/other", info=other_info)
    coordinator.async_set_updated_data(data)
    await hass.async_block_till_done()

    stars = hass.states.get(_entity_id(hass, entry, "stars", project_id=99))
    assert stars is not None
    assert stars.state == "9"
    # New project's pipeline data is absent, so its status is unknown.
    status = hass.states.get(
        _entity_id(hass, entry, "pipeline_status", project_id=99)
    )
    assert status is not None
    assert status.state == STATE_UNKNOWN

    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_99")}
    )
    assert device is not None
    assert device.name == "group/other"

    # The original project's entities are untouched.
    original = hass.states.get(_entity_id(hass, entry, "stars"))
    assert original is not None
    assert original.state == "5"


async def test_dynamic_project_addition_via_options(
    hass, monkeypatch, mock_config_entry
) -> None:
    """Adding a project through the options flow reloads and adds entities."""

    def get_project(key: str) -> dict[str, Any]:
        if key == TEST_PROJECT:
            return make_project_info()
        return make_project_info(
            id=99,
            path_with_namespace="group/other",
            web_url=f"{TEST_URL}/group/other",
            open_issues_count=7,
        )

    entry = await _setup(
        hass,
        monkeypatch,
        mock_config_entry,
        rich=True,
        overrides={"async_get_project": AsyncMock(side_effect=get_project)},
    )
    assert hass.states.get(_entity_id(hass, entry, "open_issues")) is not None

    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_PROJECTS: [TEST_PROJECT, "group/other"],
            CONF_SCAN_INTERVAL_MINUTES: 5,
        },
    )
    await hass.async_block_till_done()

    new_issues = hass.states.get(
        _entity_id(hass, entry, "open_issues", project_id=99)
    )
    assert new_issues is not None
    assert new_issues.state == "7"
    original = hass.states.get(_entity_id(hass, entry, "open_issues"))
    assert original is not None
    assert original.state == "3"


async def test_unavailable_when_project_disappears(
    hass, monkeypatch, mock_config_entry
) -> None:
    """Sensors become unavailable when their project drops out of the data."""
    entry = await _setup(hass, monkeypatch, mock_config_entry, rich=True)
    coordinator = entry.runtime_data

    status_id = _entity_id(hass, entry, "pipeline_status")
    state = hass.states.get(status_id)
    assert state is not None
    assert state.state == "success"

    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()

    for suffix in ENABLED_KEYS:
        state = hass.states.get(_entity_id(hass, entry, suffix))
        assert state is not None
        assert state.state == STATE_UNAVAILABLE, f"{suffix} is {state.state!r}"
