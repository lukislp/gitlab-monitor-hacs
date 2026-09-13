"""Sensor platform for GitLab Monitor."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import PIPELINE_STATUSES
from .coordinator import GitLabConfigEntry, GitLabCoordinator, GitLabProjectData
from .entity import GitLabProjectEntity


def _parse_ts(value: str | None) -> datetime | None:
    """Parse a GitLab ISO timestamp."""
    if not value:
        return None
    return dt_util.parse_datetime(value)


def _truncate(value: str | None) -> str | None:
    """Truncate a string to the 255 char state limit."""
    if value is None:
        return None
    return value if len(value) <= 255 else f"{value[:252]}..."


def _coverage(p: GitLabProjectData) -> float | None:
    """Return the coverage of the latest pipeline as float."""
    raw = (p.pipeline or {}).get("coverage")
    if raw in (None, ""):
        return None
    try:
        return round(float(raw), 2)
    except (TypeError, ValueError):
        return None


def _pipeline_attrs(p: GitLabProjectData) -> Mapping[str, Any] | None:
    if not p.pipeline:
        return None
    pipe = p.pipeline
    return {
        "pipeline_id": pipe.get("id"),
        "ref": pipe.get("ref"),
        "sha": pipe.get("sha"),
        "source": pipe.get("source"),
        "created_at": pipe.get("created_at"),
        "updated_at": pipe.get("updated_at"),
        "started_at": pipe.get("started_at"),
        "finished_at": pipe.get("finished_at"),
        "queued_duration": pipe.get("queued_duration"),
        "web_url": pipe.get("web_url"),
        "triggered_by": (pipe.get("user") or {}).get("username"),
    }


def _commit_attrs(p: GitLabProjectData) -> Mapping[str, Any] | None:
    if not p.last_commit:
        return None
    commit = p.last_commit
    return {
        "short_id": commit.get("short_id"),
        "title": commit.get("title"),
        "message": commit.get("message"),
        "author_name": commit.get("author_name"),
        "web_url": commit.get("web_url"),
        "ref": p.info.get("default_branch"),
    }


def _release_attrs(p: GitLabProjectData) -> Mapping[str, Any] | None:
    if not p.latest_release:
        return None
    rel = p.latest_release
    return {
        "name": rel.get("name"),
        "released_at": rel.get("released_at"),
        "web_url": (rel.get("_links") or {}).get("self"),
        "description": (rel.get("description") or "")[:255],
        "total_releases": p.releases_count,
    }


def _tag_attrs(p: GitLabProjectData) -> Mapping[str, Any] | None:
    if not p.latest_tag:
        return None
    tag = p.latest_tag
    return {
        "message": tag.get("message"),
        "target": tag.get("target"),
        "created_at": (tag.get("commit") or {}).get("created_at"),
        "total_tags": p.tags_count,
    }


def _issue_attrs(p: GitLabProjectData) -> Mapping[str, Any] | None:
    if not p.latest_issue:
        return None
    issue = p.latest_issue
    return {
        "number": issue.get("iid"),
        "state": issue.get("state"),
        "author": (issue.get("author") or {}).get("username"),
        "assignee": (issue.get("assignee") or {}).get("username"),
        "labels": issue.get("labels"),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "web_url": issue.get("web_url"),
    }


def _mr_attrs(p: GitLabProjectData) -> Mapping[str, Any] | None:
    if not p.latest_merge_request:
        return None
    mr = p.latest_merge_request
    return {
        "number": mr.get("iid"),
        "state": mr.get("state"),
        "draft": mr.get("draft"),
        "author": (mr.get("author") or {}).get("username"),
        "source_branch": mr.get("source_branch"),
        "target_branch": mr.get("target_branch"),
        "created_at": mr.get("created_at"),
        "updated_at": mr.get("updated_at"),
        "web_url": mr.get("web_url"),
    }


def _deployment_attrs(p: GitLabProjectData) -> Mapping[str, Any] | None:
    if not p.latest_deployment:
        return None
    dep = p.latest_deployment
    return {
        "deployment_id": dep.get("id"),
        "status": dep.get("status"),
        "ref": dep.get("ref"),
        "sha": dep.get("sha"),
        "created_at": dep.get("created_at"),
        "updated_at": dep.get("updated_at"),
        "user": (dep.get("user") or {}).get("username"),
    }


def _project_attrs(p: GitLabProjectData) -> Mapping[str, Any] | None:
    info = p.info
    return {
        "project_id": info.get("id"),
        "name": info.get("name"),
        "description": info.get("description"),
        "default_branch": info.get("default_branch"),
        "visibility": info.get("visibility"),
        "web_url": info.get("web_url"),
        "topics": info.get("topics"),
        "created_at": info.get("created_at"),
    }


def _fetch_attrs(p: GitLabProjectData) -> Mapping[str, Any] | None:
    fetches = (p.fetch_statistics or {}).get("fetches")
    if not fetches:
        return None
    return {"days": fetches.get("days")}


@dataclass(frozen=True, kw_only=True)
class GitLabSensorEntityDescription(SensorEntityDescription):
    """Describes a GitLab project sensor."""

    value_fn: Callable[[GitLabProjectData], Any]
    attrs_fn: Callable[[GitLabProjectData], Mapping[str, Any] | None] = lambda _: None


SENSORS: tuple[GitLabSensorEntityDescription, ...] = (
    GitLabSensorEntityDescription(
        key="pipeline_status",
        translation_key="pipeline_status",
        name="Pipeline status",
        device_class=SensorDeviceClass.ENUM,
        options=PIPELINE_STATUSES,
        value_fn=lambda p: (p.pipeline or {}).get("status"),
        attrs_fn=_pipeline_attrs,
    ),
    GitLabSensorEntityDescription(
        key="pipeline_duration",
        translation_key="pipeline_duration",
        name="Pipeline duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda p: (p.pipeline or {}).get("duration"),
    ),
    GitLabSensorEntityDescription(
        key="coverage",
        translation_key="coverage",
        name="Pipeline coverage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent",
        value_fn=_coverage,
    ),
    GitLabSensorEntityDescription(
        key="open_issues",
        translation_key="open_issues",
        name="Open issues",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:alert-circle-outline",
        value_fn=lambda p: p.info.get("open_issues_count"),
    ),
    GitLabSensorEntityDescription(
        key="open_merge_requests",
        translation_key="open_merge_requests",
        name="Open merge requests",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:source-merge",
        value_fn=lambda p: p.open_merge_requests,
        attrs_fn=lambda p: {"count_is_exact": p.open_merge_requests_exact},
    ),
    GitLabSensorEntityDescription(
        key="latest_issue",
        translation_key="latest_issue",
        name="Latest issue",
        icon="mdi:alert-box-outline",
        value_fn=lambda p: _truncate((p.latest_issue or {}).get("title")),
        attrs_fn=_issue_attrs,
    ),
    GitLabSensorEntityDescription(
        key="latest_merge_request",
        translation_key="latest_merge_request",
        name="Latest merge request",
        icon="mdi:source-pull",
        value_fn=lambda p: _truncate((p.latest_merge_request or {}).get("title")),
        attrs_fn=_mr_attrs,
    ),
    GitLabSensorEntityDescription(
        key="latest_deployment",
        translation_key="latest_deployment",
        name="Latest deployment",
        icon="mdi:rocket-launch",
        value_fn=lambda p: ((p.latest_deployment or {}).get("environment") or {}).get(
            "name"
        ),
        attrs_fn=_deployment_attrs,
    ),
    GitLabSensorEntityDescription(
        key="branches",
        translation_key="branches",
        name="Branches",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:source-branch",
        value_fn=lambda p: p.branches,
        attrs_fn=lambda p: {"count_is_exact": p.branches_exact},
    ),
    GitLabSensorEntityDescription(
        key="stars",
        translation_key="stars",
        name="Stars",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:star",
        value_fn=lambda p: p.info.get("star_count"),
    ),
    GitLabSensorEntityDescription(
        key="forks",
        translation_key="forks",
        name="Forks",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:source-fork",
        value_fn=lambda p: p.info.get("forks_count"),
    ),
    GitLabSensorEntityDescription(
        key="contributors",
        translation_key="contributors",
        name="Contributors",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:account-group",
        value_fn=lambda p: p.contributors,
    ),
    GitLabSensorEntityDescription(
        key="members",
        translation_key="members",
        name="Members",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:account-multiple",
        value_fn=lambda p: p.members,
    ),
    GitLabSensorEntityDescription(
        key="commit_count",
        translation_key="commit_count",
        name="Commits",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:source-commit",
        value_fn=lambda p: (p.info.get("statistics") or {}).get("commit_count"),
    ),
    GitLabSensorEntityDescription(
        key="last_commit",
        translation_key="last_commit",
        name="Last commit",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: _parse_ts((p.last_commit or {}).get("committed_date")),
        attrs_fn=_commit_attrs,
    ),
    GitLabSensorEntityDescription(
        key="last_activity",
        translation_key="last_activity",
        name="Last activity",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: _parse_ts(p.info.get("last_activity_at")),
        attrs_fn=_project_attrs,
    ),
    GitLabSensorEntityDescription(
        key="latest_release",
        translation_key="latest_release",
        name="Latest release",
        icon="mdi:tag",
        value_fn=lambda p: (p.latest_release or {}).get("tag_name"),
        attrs_fn=_release_attrs,
    ),
    GitLabSensorEntityDescription(
        key="releases_count",
        translation_key="releases_count",
        name="Releases",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tag-multiple",
        entity_registry_enabled_default=False,
        value_fn=lambda p: p.releases_count,
    ),
    GitLabSensorEntityDescription(
        key="latest_tag",
        translation_key="latest_tag",
        name="Latest tag",
        icon="mdi:tag-outline",
        entity_registry_enabled_default=False,
        value_fn=lambda p: (p.latest_tag or {}).get("name"),
        attrs_fn=_tag_attrs,
    ),
    GitLabSensorEntityDescription(
        key="tags_count",
        translation_key="tags_count",
        name="Tags",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tag-multiple-outline",
        entity_registry_enabled_default=False,
        value_fn=lambda p: p.tags_count,
    ),
    GitLabSensorEntityDescription(
        key="repository_size",
        translation_key="repository_size",
        name="Repository size",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda p: (p.info.get("statistics") or {}).get("repository_size"),
    ),
    GitLabSensorEntityDescription(
        key="storage_size",
        translation_key="storage_size",
        name="Total storage size",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda p: (p.info.get("statistics") or {}).get("storage_size"),
    ),
    GitLabSensorEntityDescription(
        key="fetches_30d",
        translation_key="fetches_30d",
        name="Fetches (30 days)",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:download",
        entity_registry_enabled_default=False,
        value_fn=lambda p: ((p.fetch_statistics or {}).get("fetches") or {}).get(
            "total"
        ),
        attrs_fn=_fetch_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GitLabConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GitLab sensors from a config entry."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_projects() -> None:
        """Add entities for projects that appeared in coordinator data."""
        new_entities: list[GitLabSensor] = []
        for key in coordinator.data or {}:
            if key in known:
                continue
            known.add(key)
            new_entities.extend(
                GitLabSensor(coordinator, key, description) for description in SENSORS
            )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_projects()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_projects))


class GitLabSensor(GitLabProjectEntity, SensorEntity):
    """A sensor for a GitLab project."""

    entity_description: GitLabSensorEntityDescription

    def __init__(
        self,
        coordinator: GitLabCoordinator,
        project_key: str,
        description: GitLabSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, project_key, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        if (project := self.project) is None:
            return None
        return self.entity_description.value_fn(project)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return additional attributes."""
        if (project := self.project) is None:
            return None
        return self.entity_description.attrs_fn(project)
