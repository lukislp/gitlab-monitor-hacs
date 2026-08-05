"""Binary sensor platform for GitLab Monitor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GitLabConfigEntry, GitLabCoordinator, GitLabProjectData
from .entity import GitLabProjectEntity


@dataclass(frozen=True, kw_only=True)
class GitLabBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a GitLab project binary sensor."""

    is_on_fn: Callable[[GitLabProjectData], bool | None]


BINARY_SENSORS: tuple[GitLabBinarySensorEntityDescription, ...] = (
    GitLabBinarySensorEntityDescription(
        key="pipeline_failed",
        translation_key="pipeline_failed",
        name="Pipeline failed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda p: (
            None if p.pipeline is None else p.pipeline.get("status") == "failed"
        ),
    ),
    GitLabBinarySensorEntityDescription(
        key="pipeline_running",
        translation_key="pipeline_running",
        name="Pipeline running",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda p: (
            None
            if p.pipeline is None
            else p.pipeline.get("status")
            in ("created", "waiting_for_resource", "preparing", "pending", "running")
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GitLabConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GitLab binary sensors from a config entry."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_projects() -> None:
        new_entities: list[GitLabBinarySensor] = []
        for key in coordinator.data or {}:
            if key in known:
                continue
            known.add(key)
            new_entities.extend(
                GitLabBinarySensor(coordinator, key, description)
                for description in BINARY_SENSORS
            )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_projects()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_projects))


class GitLabBinarySensor(GitLabProjectEntity, BinarySensorEntity):
    """A binary sensor for a GitLab project."""

    entity_description: GitLabBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: GitLabCoordinator,
        project_key: str,
        description: GitLabBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, project_key, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        if (project := self.project) is None:
            return None
        return self.entity_description.is_on_fn(project)
