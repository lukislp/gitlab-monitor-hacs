"""The GitLab Monitor integration."""

from __future__ import annotations

from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GitLabAuthError, GitLabClient, GitLabConnectionError
from .const import PLATFORMS
from .coordinator import GitLabConfigEntry, GitLabCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: GitLabConfigEntry) -> bool:
    """Set up GitLab Monitor from a config entry."""
    session = async_get_clientsession(hass, entry.data.get(CONF_VERIFY_SSL, True))
    client = GitLabClient(session, entry.data[CONF_URL], entry.data[CONF_TOKEN])

    # Validate token / connectivity before setting up.
    try:
        await client.async_get_current_user()
    except GitLabAuthError as err:
        raise ConfigEntryAuthFailed from err
    except GitLabConnectionError as err:
        raise ConfigEntryNotReady from err

    coordinator = GitLabCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry when options (projects / interval) change.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: GitLabConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GitLabConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
