"""Base entity for GitLab Monitor."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_MANUFACTURER, DOMAIN
from .coordinator import GitLabCoordinator, GitLabProjectData


class GitLabProjectEntity(CoordinatorEntity[GitLabCoordinator]):
    """Base class for all per-project GitLab entities."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: GitLabCoordinator, project_key: str, suffix: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._project_key = project_key
        project = coordinator.data[project_key]
        info = project.info
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{info['id']}_{suffix}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_{info['id']}")},
            name=info.get("path_with_namespace", project_key),
            manufacturer=ATTR_MANUFACTURER,
            model="Repository",
            configuration_url=info.get("web_url"),
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def project(self) -> GitLabProjectData | None:
        """Return the current data for this project."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._project_key)

    @property
    def available(self) -> bool:
        """Entity is available while the project can be fetched."""
        return super().available and self.project is not None
