"""Tests for the GitLab REST API v4 client (``custom_components.gitlab_monitor.api``).

These tests exercise ``GitLabClient`` against a real ``aiohttp.ClientSession``
whose transport is intercepted by ``aioresponses`` - no Home Assistant instance
is required and no network traffic occurs.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from typing import Any

import aiohttp
import pytest
from aioresponses import aioresponses
from yarl import URL

from custom_components.gitlab_monitor.api import (
    GitLabApiError,
    GitLabAuthError,
    GitLabClient,
    GitLabConnectionError,
    GitLabForbiddenError,
    GitLabNotFoundError,
)

from .conftest import TEST_PROJECT, TEST_PROJECT_ID, TEST_TOKEN, TEST_URL, TEST_USER

API = f"{TEST_URL}/api/v4"


@pytest.fixture
async def session() -> AsyncGenerator[aiohttp.ClientSession, None]:
    """A real aiohttp session (aioresponses intercepts before any socket use)."""
    async with aiohttp.ClientSession() as sess:
        yield sess


@pytest.fixture
async def client(session: aiohttp.ClientSession) -> GitLabClient:
    """A GitLabClient pointed at the test instance."""
    return GitLabClient(session, TEST_URL, TEST_TOKEN)


@pytest.fixture
def mock_api() -> Generator[aioresponses, None, None]:
    """Intercept all aiohttp requests."""
    with aioresponses() as mocked:
        yield mocked


def _requested_urls(mock_api: aioresponses) -> list[URL]:
    """Return the (normalized) URLs of all recorded requests, in key order."""
    return [url for (_method, url) in mock_api.requests]


def _single_request_url(mock_api: aioresponses) -> URL:
    """Assert exactly one distinct request was made and return its URL."""
    urls = _requested_urls(mock_api)
    assert len(urls) == 1
    return urls[0]


def _assert_token_sent(mock_api: aioresponses) -> None:
    """Assert every recorded request carried the PRIVATE-TOKEN header."""
    calls = [call for calls in mock_api.requests.values() for call in calls]
    assert calls, "no requests were recorded"
    for call in calls:
        assert call.kwargs["headers"]["PRIVATE-TOKEN"] == TEST_TOKEN


# ----------------------------------------------------------------------
# Instance / user endpoints
# ----------------------------------------------------------------------


async def test_async_get_current_user(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """The /user endpoint is hit with the token and its JSON is returned."""
    mock_api.get(f"{API}/user", payload=TEST_USER)

    user = await client.async_get_current_user()

    assert user == TEST_USER
    assert str(_single_request_url(mock_api)) == f"{API}/user"
    _assert_token_sent(mock_api)


async def test_async_get_version(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """The /version endpoint JSON is returned as-is."""
    payload = {"version": "17.2.1", "revision": "deadbeef"}
    mock_api.get(f"{API}/version", payload=payload)

    assert await client.async_get_version() == payload
    assert str(_single_request_url(mock_api)) == f"{API}/version"


async def test_async_list_membership_projects(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """Membership projects are listed with the expected query parameters."""
    projects = [{"id": 1, "path_with_namespace": "a/b"}]
    mock_api.get(
        f"{API}/projects?membership=true&simple=true&per_page=100"
        "&order_by=last_activity_at&sort=desc",
        payload=projects,
    )

    assert await client.async_list_membership_projects() == projects

    url = _single_request_url(mock_api)
    assert url.path == "/api/v4/projects"
    assert dict(url.query) == {
        "membership": "true",
        "simple": "true",
        "per_page": "100",
        "order_by": "last_activity_at",
        "sort": "desc",
    }
    _assert_token_sent(mock_api)


# ----------------------------------------------------------------------
# Project details
# ----------------------------------------------------------------------


async def test_async_get_project_numeric_id(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """A numeric project ID is passed through and statistics are requested."""
    payload = {"id": TEST_PROJECT_ID, "star_count": 5}
    mock_api.get(
        f"{API}/projects/{TEST_PROJECT_ID}?statistics=true", payload=payload
    )

    assert await client.async_get_project(TEST_PROJECT_ID) == payload

    url = _single_request_url(mock_api)
    assert url.path == f"/api/v4/projects/{TEST_PROJECT_ID}"
    assert dict(url.query) == {"statistics": "true"}


async def test_async_get_project_encodes_path(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """A 'group/project' path is URL-encoded to 'group%2Fproject'."""
    payload = {"id": TEST_PROJECT_ID, "path_with_namespace": TEST_PROJECT}
    mock_api.get(
        f"{API}/projects/group%2Fproject?statistics=true", payload=payload
    )

    assert await client.async_get_project(TEST_PROJECT) == payload

    url = _single_request_url(mock_api)
    assert url.raw_path == "/api/v4/projects/group%2Fproject"


async def test_base_url_trailing_slash_is_stripped(
    session: aiohttp.ClientSession, mock_api: aioresponses
) -> None:
    """A trailing slash in the configured URL does not produce '//api/v4'."""
    slash_client = GitLabClient(session, f"{TEST_URL}/", TEST_TOKEN)
    assert slash_client.base_url == TEST_URL

    mock_api.get(f"{API}/version", payload={"version": "17.2.1"})
    assert await slash_client.async_get_version() == {"version": "17.2.1"}
    assert str(_single_request_url(mock_api)) == f"{API}/version"


# ----------------------------------------------------------------------
# Error mapping
# ----------------------------------------------------------------------


async def test_401_raises_auth_error(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """HTTP 401 maps to GitLabAuthError."""
    mock_api.get(f"{API}/user", status=401)

    with pytest.raises(GitLabAuthError, match="401"):
        await client.async_get_current_user()


async def test_403_raises_forbidden_error(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """HTTP 403 maps to GitLabForbiddenError."""
    mock_api.get(f"{API}/user", status=403)

    with pytest.raises(GitLabForbiddenError):
        await client.async_get_current_user()


async def test_404_raises_not_found_error(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """HTTP 404 maps to GitLabNotFoundError."""
    mock_api.get(f"{API}/projects/999?statistics=true", status=404)

    with pytest.raises(GitLabNotFoundError):
        await client.async_get_project(999)


async def test_500_raises_generic_api_error_with_body(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """Other >=400 statuses raise GitLabApiError including status and body."""
    mock_api.get(f"{API}/version", status=500, body="internal fireworks")

    with pytest.raises(GitLabApiError) as excinfo:
        await client.async_get_version()

    message = str(excinfo.value)
    assert "500" in message
    assert "internal fireworks" in message


async def test_500_body_is_truncated_to_200_chars(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """The error body snippet is capped at 200 characters."""
    mock_api.get(f"{API}/version", status=502, body="x" * 500)

    with pytest.raises(GitLabApiError) as excinfo:
        await client.async_get_version()

    message = str(excinfo.value)
    assert "x" * 200 in message
    assert "x" * 201 not in message


async def test_client_error_raises_connection_error(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """aiohttp.ClientError maps to GitLabConnectionError."""
    mock_api.get(f"{API}/user", exception=aiohttp.ClientError("boom"))

    with pytest.raises(GitLabConnectionError):
        await client.async_get_current_user()


async def test_timeout_raises_connection_error(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """asyncio.TimeoutError maps to GitLabConnectionError."""
    mock_api.get(f"{API}/user", exception=asyncio.TimeoutError())

    with pytest.raises(GitLabConnectionError):
        await client.async_get_current_user()


# ----------------------------------------------------------------------
# async_get_latest_pipeline
# ----------------------------------------------------------------------

PIPELINES_LIST_URL = f"{API}/projects/{TEST_PROJECT_ID}/pipelines?per_page=1"
PIPELINE_DETAILS_URL = f"{API}/projects/{TEST_PROJECT_ID}/pipelines/9001"


async def test_latest_pipeline_empty_list_returns_none(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """No pipelines at all yields None without a details request."""
    mock_api.get(PIPELINES_LIST_URL, payload=[])

    assert await client.async_get_latest_pipeline(TEST_PROJECT_ID) is None
    assert len(_requested_urls(mock_api)) == 1


async def test_latest_pipeline_returns_details(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """The richer single-pipeline details response is preferred."""
    list_item = {"id": 9001, "status": "success", "ref": "main"}
    details = {**list_item, "duration": 600, "user": {"username": "tester"}}
    mock_api.get(PIPELINES_LIST_URL, payload=[list_item])
    mock_api.get(PIPELINE_DETAILS_URL, payload=details)

    assert await client.async_get_latest_pipeline(TEST_PROJECT_ID) == details

    urls = _requested_urls(mock_api)
    assert len(urls) == 2
    assert urls[1].path == f"/api/v4/projects/{TEST_PROJECT_ID}/pipelines/9001"


async def test_latest_pipeline_falls_back_to_list_item_on_details_error(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """If the details fetch fails, the pipeline list item is returned instead."""
    list_item = {"id": 9001, "status": "running", "ref": "main"}
    mock_api.get(PIPELINES_LIST_URL, payload=[list_item])
    mock_api.get(PIPELINE_DETAILS_URL, status=500, body="oops")

    assert await client.async_get_latest_pipeline(TEST_PROJECT_ID) == list_item


# ----------------------------------------------------------------------
# async_count
# ----------------------------------------------------------------------

ISSUES_PROBE_URL = f"{API}/projects/{TEST_PROJECT_ID}/issues?per_page=1"
ISSUES_PAGE_URL = f"{API}/projects/{TEST_PROJECT_ID}/issues?per_page=100"


async def test_count_uses_x_total_header(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """When x-total is present, its value is returned as an exact count."""
    mock_api.get(
        ISSUES_PROBE_URL, payload=[{"iid": 1}], headers={"x-total": "123"}
    )

    assert await client.async_count(TEST_PROJECT_ID, "issues") == (123, True)
    assert len(_requested_urls(mock_api)) == 1
    _assert_token_sent(mock_api)


async def test_count_merges_extra_params(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """Caller-supplied params are merged into the probe request."""
    mock_api.get(
        f"{ISSUES_PROBE_URL}&state=opened",
        payload=[{"iid": 1}],
        headers={"x-total": "3"},
    )

    result = await client.async_count(
        TEST_PROJECT_ID, "issues", {"state": "opened"}
    )

    assert result == (3, True)
    url = _single_request_url(mock_api)
    assert dict(url.query) == {"per_page": "1", "state": "opened"}


async def test_count_fallback_full_page_is_inexact(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """Missing x-total plus a full 100-item page yields a lower bound."""
    mock_api.get(ISSUES_PROBE_URL, payload=[{"iid": 1}])
    mock_api.get(
        ISSUES_PAGE_URL, payload=[{"iid": i} for i in range(100)]
    )

    assert await client.async_count(TEST_PROJECT_ID, "issues") == (100, False)


async def test_count_fallback_short_page_is_exact(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """Missing x-total plus a short page yields an exact count."""
    mock_api.get(ISSUES_PROBE_URL, payload=[{"iid": 1}])
    mock_api.get(ISSUES_PAGE_URL, payload=[{"iid": i} for i in range(3)])

    assert await client.async_count(TEST_PROJECT_ID, "issues") == (3, True)


async def test_count_not_found_returns_none_exact(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """A 404 on the resource yields (None, True) instead of raising."""
    mock_api.get(ISSUES_PROBE_URL, status=404)

    assert await client.async_count(TEST_PROJECT_ID, "issues") == (None, True)


# ----------------------------------------------------------------------
# async_get_first_with_total
# ----------------------------------------------------------------------

MRS_PROBE_URL = f"{API}/projects/{TEST_PROJECT_ID}/merge_requests?per_page=1"


async def test_get_first_with_total_success(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """First item and exact total come from one request with x-total."""
    item = {"iid": 8, "title": "Add a feature"}
    mock_api.get(MRS_PROBE_URL, payload=[item], headers={"x-total": "5"})

    result = await client.async_get_first_with_total(
        TEST_PROJECT_ID, "merge_requests"
    )

    assert result == (item, 5, True)


async def test_get_first_with_total_empty_list(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """An empty collection yields (None, 0, True)."""
    mock_api.get(MRS_PROBE_URL, payload=[], headers={"x-total": "0"})

    result = await client.async_get_first_with_total(
        TEST_PROJECT_ID, "merge_requests"
    )

    assert result == (None, 0, True)


async def test_get_first_with_total_missing_header(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """Without x-total the item is returned but the total is unknown."""
    item = {"iid": 8}
    mock_api.get(MRS_PROBE_URL, payload=[item])

    result = await client.async_get_first_with_total(
        TEST_PROJECT_ID, "merge_requests"
    )

    assert result == (item, None, False)


async def test_get_first_with_total_not_found(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """A 404 yields (None, None, True) instead of raising."""
    mock_api.get(MRS_PROBE_URL, status=404)

    result = await client.async_get_first_with_total(
        TEST_PROJECT_ID, "merge_requests"
    )

    assert result == (None, None, True)


async def test_get_first_with_total_merges_extra_params(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """Extra params (e.g. state=opened for merge_requests) are merged into the request,
    matching how the coordinator actually calls this for open-only merge requests."""
    mock_api.get(
        f"{API}/projects/{TEST_PROJECT_ID}/merge_requests?per_page=1&state=opened",
        payload=[],
        headers={"x-total": "0"},
    )

    result = await client.async_get_first_with_total(
        TEST_PROJECT_ID, "merge_requests", {"state": "opened"}
    )

    assert result == (None, 0, True)
    url = _single_request_url(mock_api)
    assert dict(url.query) == {"per_page": "1", "state": "opened"}


# ----------------------------------------------------------------------
# async_get_first
# ----------------------------------------------------------------------


async def test_get_first_returns_first_item(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """The first item of the page is returned."""
    items: list[dict[str, Any]] = [{"id": "abc"}]
    mock_api.get(
        f"{API}/projects/{TEST_PROJECT_ID}/repository/commits?per_page=1",
        payload=items,
    )

    result = await client.async_get_first(TEST_PROJECT_ID, "repository/commits")

    assert result == items[0]
    url = _single_request_url(mock_api)
    assert url.path == f"/api/v4/projects/{TEST_PROJECT_ID}/repository/commits"
    assert dict(url.query) == {"per_page": "1"}


async def test_get_first_merges_extra_params(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """Extra params (e.g. order_by/sort for deployments) are merged into the request,
    matching how the coordinator calls this for the latest deployment."""
    items: list[dict[str, Any]] = [{"id": "abc", "environment": "production"}]
    mock_api.get(
        f"{API}/projects/{TEST_PROJECT_ID}/deployments"
        "?per_page=1&order_by=created_at&sort=desc",
        payload=items,
    )

    result = await client.async_get_first(
        TEST_PROJECT_ID, "deployments", {"order_by": "created_at", "sort": "desc"}
    )

    assert result == items[0]
    url = _single_request_url(mock_api)
    assert dict(url.query) == {"per_page": "1", "order_by": "created_at", "sort": "desc"}


async def test_get_first_empty_returns_none(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """An empty page yields None."""
    mock_api.get(
        f"{API}/projects/{TEST_PROJECT_ID}/repository/commits?per_page=1",
        payload=[],
    )

    assert (
        await client.async_get_first(TEST_PROJECT_ID, "repository/commits")
        is None
    )


async def test_get_first_not_found_returns_none(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """A 404 (e.g. empty repository) yields None instead of raising."""
    mock_api.get(
        f"{API}/projects/{TEST_PROJECT_ID}/repository/commits?per_page=1",
        status=404,
    )

    assert (
        await client.async_get_first(TEST_PROJECT_ID, "repository/commits")
        is None
    )


# ----------------------------------------------------------------------
# async_get_project_statistics
# ----------------------------------------------------------------------


async def test_async_get_project_statistics(
    client: GitLabClient, mock_api: aioresponses
) -> None:
    """The statistics endpoint JSON is returned as-is."""
    payload = {"fetches": {"total": 50, "days": []}}
    mock_api.get(
        f"{API}/projects/{TEST_PROJECT_ID}/statistics", payload=payload
    )

    result = await client.async_get_project_statistics(TEST_PROJECT_ID)

    assert result == payload
    assert (
        str(_single_request_url(mock_api))
        == f"{API}/projects/{TEST_PROJECT_ID}/statistics"
    )
