"""Data update coordinator for GitLab Monitor."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    GitLabApiError,
    GitLabAuthError,
    GitLabClient,
    GitLabConnectionError,
    GitLabNotFoundError,
)
from .const import (
    CONF_PROJECTS,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_CONCURRENT_REQUESTS,
)

_LOGGER = logging.getLogger(__name__)

type GitLabConfigEntry = ConfigEntry[GitLabCoordinator]


@dataclass
class GitLabProjectData:
    """Collected data for a single GitLab project."""

    key: str
    info: dict[str, Any] = field(default_factory=dict)
    pipeline: dict[str, Any] | None = None
    open_merge_requests: int | None = None
    open_merge_requests_exact: bool = True
    branches: int | None = None
    branches_exact: bool = True
    last_commit: dict[str, Any] | None = None
    latest_release: dict[str, Any] | None = None
    releases_count: int | None = None
    latest_tag: dict[str, Any] | None = None
    tags_count: int | None = None
    latest_issue: dict[str, Any] | None = None
    latest_merge_request: dict[str, Any] | None = None
    latest_deployment: dict[str, Any] | None = None
    contributors: int | None = None
    members: int | None = None
    fetch_statistics: dict[str, Any] | None = None

    @property
    def project_id(self) -> int | None:
        """Return the numeric GitLab project id."""
        return self.info.get("id")


class GitLabCoordinator(DataUpdateCoordinator[dict[str, GitLabProjectData]]):
    """Coordinator fetching data for all configured projects."""

    config_entry: GitLabConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: GitLabConfigEntry,
        client: GitLabClient,
    ) -> None:
        """Initialize the coordinator."""
        interval = entry.options.get(
            CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=timedelta(minutes=interval),
        )
        self.client = client
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    @property
    def projects(self) -> list[str]:
        """Return the configured project keys (paths or numeric IDs)."""
        return list(self.config_entry.options.get(CONF_PROJECTS, []))

    async def _async_update_data(self) -> dict[str, GitLabProjectData]:
        """Fetch data for all projects."""
        projects = self.projects
        if not projects:
            raise UpdateFailed("No projects configured")

        results = await asyncio.gather(
            *(self._async_fetch_project(key) for key in projects),
            return_exceptions=True,
        )

        data: dict[str, GitLabProjectData] = {}
        auth_errors = 0
        failures = 0
        for key, result in zip(projects, results):
            if isinstance(result, GitLabAuthError):
                auth_errors += 1
                failures += 1
            elif isinstance(result, BaseException):
                failures += 1
                _LOGGER.warning("Error updating project %s: %s", key, result)
                # Keep previous data for this project if we have any.
                if self.data and key in self.data:
                    data[key] = self.data[key]
            else:
                data[key] = result

        if auth_errors and auth_errors == len(projects):
            raise ConfigEntryAuthFailed("GitLab token is invalid or expired")
        if failures == len(projects):
            raise UpdateFailed("All GitLab project updates failed")
        return data

    async def _async_fetch_project(self, key: str) -> GitLabProjectData:
        """Fetch all data for a single project."""
        async with self._semaphore:
            info = await self.client.async_get_project(key)
        project_id: int = info["id"]
        result = GitLabProjectData(key=key, info=info)

        async def _guarded(coro):
            async with self._semaphore:
                try:
                    return await coro
                except GitLabAuthError:
                    raise
                except (
                    GitLabConnectionError,
                    GitLabNotFoundError,
                    GitLabApiError,
                ) as err:
                    _LOGGER.debug("Partial fetch failed for %s: %s", key, err)
                    return None

        (
            result.pipeline,
            mrs,
            branch_count,
            result.last_commit,
            releases,
            tags,
            result.latest_issue,
            result.latest_deployment,
            contributors,
            members,
            result.fetch_statistics,
        ) = await asyncio.gather(
            _guarded(self.client.async_get_latest_pipeline(project_id)),
            _guarded(
                self.client.async_get_first_with_total(
                    project_id, "merge_requests", {"state": "opened"}
                )
            ),
            _guarded(self.client.async_count(project_id, "repository/branches")),
            _guarded(self.client.async_get_first(project_id, "repository/commits")),
            _guarded(self.client.async_get_first_with_total(project_id, "releases")),
            _guarded(
                self.client.async_get_first_with_total(project_id, "repository/tags")
            ),
            _guarded(self.client.async_get_first(project_id, "issues")),
            _guarded(
                self.client.async_get_first(
                    project_id,
                    "deployments",
                    {"order_by": "created_at", "sort": "desc"},
                )
            ),
            _guarded(self.client.async_count(project_id, "repository/contributors")),
            _guarded(self.client.async_count(project_id, "members/all")),
            _guarded(self.client.async_get_project_statistics(project_id)),
        )

        if mrs is not None:
            (
                result.latest_merge_request,
                result.open_merge_requests,
                result.open_merge_requests_exact,
            ) = mrs
        if branch_count is not None:
            result.branches, result.branches_exact = branch_count
        if releases is not None:
            result.latest_release, result.releases_count, _ = releases
        if tags is not None:
            result.latest_tag, result.tags_count, _ = tags
        if contributors is not None:
            result.contributors, _ = contributors
        if members is not None:
            result.members, _ = members
        return result
