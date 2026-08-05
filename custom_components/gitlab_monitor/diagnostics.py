"""Diagnostics support for GitLab Monitor."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant

from .coordinator import GitLabConfigEntry

TO_REDACT = {CONF_TOKEN, "author_email", "runners_token"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GitLabConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "projects": {
            key: async_redact_data(asdict(project), TO_REDACT)
            for key, project in (coordinator.data or {}).items()
        },
    }
