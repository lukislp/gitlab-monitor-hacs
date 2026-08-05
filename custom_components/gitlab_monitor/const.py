"""Constants for the GitLab Monitor integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "gitlab_monitor"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
]

CONF_PROJECTS = "projects"
CONF_CUSTOM_PROJECTS = "custom_projects"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"

DEFAULT_URL = "https://gitlab.com"
DEFAULT_SCAN_INTERVAL_MINUTES = 1
MIN_SCAN_INTERVAL_MINUTES = 1
MAX_SCAN_INTERVAL_MINUTES = 1440

# Max parallel requests against the GitLab API during one update cycle.
MAX_CONCURRENT_REQUESTS = 4

ATTR_MANUFACTURER = "GitLab"

PIPELINE_STATUSES = [
    "created",
    "waiting_for_resource",
    "preparing",
    "pending",
    "running",
    "success",
    "failed",
    "canceled",
    "skipped",
    "manual",
    "scheduled",
    "waiting_for_callback",
]
