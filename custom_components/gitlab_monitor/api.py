"""Lightweight async client for the GitLab REST API v4.

Uses Home Assistant's shared aiohttp session and a Personal Access Token
(``PRIVATE-TOKEN`` header). No external requirements needed.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import aiohttp
from multidict import CIMultiDictProxy

API_TIMEOUT = aiohttp.ClientTimeout(total=30)


class GitLabApiError(Exception):
    """Generic GitLab API error."""


class GitLabConnectionError(GitLabApiError):
    """Error connecting to the GitLab instance."""


class GitLabAuthError(GitLabApiError):
    """Authentication failed (invalid/expired token)."""


class GitLabForbiddenError(GitLabApiError):
    """Token lacks permission for this resource (HTTP 403)."""


class GitLabNotFoundError(GitLabApiError):
    """Requested resource was not found (or token has no access to it)."""


class GitLabClient:
    """Minimal async GitLab API v4 client."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        """Initialize the client."""
        self._session = session
        self.base_url = url.rstrip("/")
        self._api = f"{self.base_url}/api/v4"
        self._headers = {"PRIVATE-TOKEN": token}

    async def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[Any, CIMultiDictProxy[str]]:
        """Perform a GET request and return (json, headers)."""
        url = f"{self._api}/{path.lstrip('/')}"
        try:
            async with self._session.get(
                url, headers=self._headers, params=params, timeout=API_TIMEOUT
            ) as resp:
                if resp.status == 401:
                    raise GitLabAuthError(f"Authentication failed for {url} (HTTP 401)")
                if resp.status == 403:
                    raise GitLabForbiddenError(f"Access forbidden: {url}")
                if resp.status == 404:
                    raise GitLabNotFoundError(f"Not found: {url}")
                if resp.status >= 400:
                    body = await resp.text()
                    raise GitLabApiError(
                        f"GitLab API error {resp.status} for {url}: {body[:200]}"
                    )
                return await resp.json(), resp.headers
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise GitLabConnectionError(
                f"Error connecting to GitLab at {self.base_url}: {err}"
            ) from err

    @staticmethod
    def _encode_project(project: str | int) -> str:
        """URL-encode a project path or pass through a numeric ID."""
        return quote(str(project), safe="")

    # ------------------------------------------------------------------
    # Instance / user
    # ------------------------------------------------------------------

    async def async_get_current_user(self) -> dict[str, Any]:
        """Return the user the token belongs to (also validates the token)."""
        data, _ = await self._request("user")
        return data

    async def async_get_version(self) -> dict[str, Any]:
        """Return the GitLab server version."""
        data, _ = await self._request("version")
        return data

    async def async_list_membership_projects(self) -> list[dict[str, Any]]:
        """Return up to 100 projects the token's user is a member of."""
        data, _ = await self._request(
            "projects",
            params={
                "membership": "true",
                "simple": "true",
                "per_page": 100,
                "order_by": "last_activity_at",
                "sort": "desc",
            },
        )
        return data

    # ------------------------------------------------------------------
    # Project data
    # ------------------------------------------------------------------

    async def async_get_project(self, project: str | int) -> dict[str, Any]:
        """Return project details (stars, forks, open issues, statistics, ...)."""
        data, _ = await self._request(
            f"projects/{self._encode_project(project)}",
            params={"statistics": "true"},
        )
        return data

    async def async_get_latest_pipeline(self, project_id: int) -> dict[str, Any] | None:
        """Return the most recent pipeline of a project, incl. details."""
        data, _ = await self._request(
            f"projects/{project_id}/pipelines", params={"per_page": 1}
        )
        if not data:
            return None
        # Fetch the single-pipeline endpoint for duration / user / timing.
        try:
            details, _ = await self._request(
                f"projects/{project_id}/pipelines/{data[0]['id']}"
            )
        except GitLabApiError:
            return data[0]
        return details

    async def async_count(
        self, project_id: int, resource: str, params: dict[str, Any] | None = None
    ) -> tuple[int | None, bool]:
        """Count items of a project resource via the ``x-total`` header.

        Returns (count, exact). On gitlab.com the header is omitted for
        collections larger than 10 000 items; in that case the length of a
        100-item page is returned as a lower bound (exact=False).
        """
        req_params: dict[str, Any] = {"per_page": 1}
        if params:
            req_params.update(params)
        try:
            data, headers = await self._request(
                f"projects/{project_id}/{resource}", params=req_params
            )
        except GitLabNotFoundError:
            return None, True
        total = headers.get("x-total")
        if total is not None and total.isdigit():
            return int(total), True
        # Fallback: count a full page as a lower bound.
        req_params["per_page"] = 100
        data, _ = await self._request(
            f"projects/{project_id}/{resource}", params=req_params
        )
        return len(data), len(data) < 100

    async def async_get_first_with_total(
        self, project_id: int, resource: str, params: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, int | None, bool]:
        """Return (first item, total count, exact) of a paginated resource."""
        req_params: dict[str, Any] = {"per_page": 1}
        if params:
            req_params.update(params)
        try:
            data, headers = await self._request(
                f"projects/{project_id}/{resource}", params=req_params
            )
        except GitLabNotFoundError:
            return None, None, True
        first = data[0] if data else None
        total = headers.get("x-total")
        if total is not None and total.isdigit():
            return first, int(total), True
        return first, None, False

    async def async_get_project_statistics(
        self, project_id: int
    ) -> dict[str, Any] | None:
        """Return fetch statistics (requires Maintainer role)."""
        data, _ = await self._request(f"projects/{project_id}/statistics")
        return data

    async def async_get_first(
        self, project_id: int, resource: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Return the first item of a paginated project resource, or None."""
        req_params: dict[str, Any] = {"per_page": 1}
        if params:
            req_params.update(params)
        try:
            data, _ = await self._request(
                f"projects/{project_id}/{resource}", params=req_params
            )
        except GitLabNotFoundError:
            # e.g. empty repository -> commits endpoint returns 404
            return None
        if not data:
            return None
        return data[0]
