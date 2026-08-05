"""Button platform for GitLab Monitor."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GitLabConfigEntry, GitLabCoordinator
from .entity import GitLabProjectEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GitLabConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GitLab buttons from a config entry."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_projects() -> None:
        new_entities: list[GitLabRefreshButton] = []
        for key in coordinator.data or {}:
            if key in known:
                continue
            known.add(key)
            new_entities.append(GitLabRefreshButton(coordinator, key))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_projects()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_projects))


class GitLabRefreshButton(GitLabProjectEntity, ButtonEntity):
    """Button to manually refresh a project's data."""

    _attr_translation_key = "refresh"
    _attr_name = "Refresh"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: GitLabCoordinator, project_key: str) -> None:
        """Initialize the button."""
        super().__init__(coordinator, project_key, "refresh")

    async def async_press(self) -> None:
        """Trigger an immediate update of all projects."""
        await self.coordinator.async_request_refresh()
