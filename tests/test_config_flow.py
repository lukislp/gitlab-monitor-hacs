"""Tests for the GitLab Monitor config, reauth, reconfigure and options flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gitlab_monitor.api import (
    GitLabApiError,
    GitLabAuthError,
    GitLabClient,
    GitLabConnectionError,
    GitLabNotFoundError,
)
from custom_components.gitlab_monitor.config_flow import (
    _normalize_url,
    _parse_custom_projects,
)
from custom_components.gitlab_monitor.const import (
    CONF_CUSTOM_PROJECTS,
    CONF_PROJECTS,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)

from .conftest import (
    TEST_PROJECT,
    TEST_TOKEN,
    TEST_URL,
    TEST_USER,
    make_project_info,
    patch_client_defaults,
)

USER_INPUT = {
    CONF_URL: TEST_URL,
    CONF_TOKEN: TEST_TOKEN,
    CONF_VERIFY_SSL: True,
}


@pytest.fixture
def mock_setup_entry():
    """Keep entry setup (and post-reauth reload) from running the real integration."""
    with patch(
        "custom_components.gitlab_monitor.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def _start_user_flow(hass: HomeAssistant):
    """Initialize a user config flow and return the first form."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


# ----------------------------------------------------------------------
# Plain helper functions
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("gitlab.example.com", "https://gitlab.example.com"),
        ("https://gitlab.example.com/", "https://gitlab.example.com"),
        (" http://gitlab.local:8080/ ", "http://gitlab.local:8080"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    """The URL normalizer adds https:// and strips whitespace/trailing slashes."""
    assert _normalize_url(raw) == expected


def test_parse_custom_projects() -> None:
    """Custom projects split on commas/newlines, strip slashes and dedupe."""
    raw = "group/a, group/b\n/group/c/\n\n group/a ,"
    assert _parse_custom_projects(raw) == ["group/a", "group/b", "group/c"]
    assert _parse_custom_projects("") == []


# ----------------------------------------------------------------------
# Initial setup: user + projects steps
# ----------------------------------------------------------------------


async def test_full_flow_with_membership_project(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_setup_entry: AsyncMock,
) -> None:
    """Happy path: user step, pick a membership project, entry is created."""
    patch_client_defaults(monkeypatch)

    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "projects"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROJECTS: [TEST_PROJECT], CONF_CUSTOM_PROJECTS: ""},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "GitLab (gitlab.example.com)"
    assert result["data"] == {
        CONF_URL: TEST_URL,
        CONF_TOKEN: TEST_TOKEN,
        CONF_VERIFY_SSL: True,
    }
    assert result["options"] == {
        CONF_PROJECTS: [TEST_PROJECT],
        CONF_SCAN_INTERVAL_MINUTES: DEFAULT_SCAN_INTERVAL_MINUTES,
    }
    assert result["result"].unique_id == f"gitlab.example.com_{TEST_USER['id']}"


async def test_full_flow_with_custom_projects(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_setup_entry: AsyncMock,
) -> None:
    """Custom projects are normalized, deduped against the selection and validated."""
    patch_client_defaults(monkeypatch)
    get_project = AsyncMock(return_value=make_project_info())
    monkeypatch.setattr(GitLabClient, "async_get_project", get_project)

    result = await _start_user_flow(hass)
    # A bare host with a trailing slash must be normalized to https://host.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: "gitlab.example.com/",
            CONF_TOKEN: TEST_TOKEN,
            CONF_VERIFY_SSL: True,
        },
    )
    assert result["step_id"] == "projects"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_PROJECTS: [TEST_PROJECT],
            # group/project duplicates the selection and must not be re-validated.
            CONF_CUSTOM_PROJECTS: "other/repo,\n /group/project/ ",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == TEST_URL
    assert result["options"][CONF_PROJECTS] == [TEST_PROJECT, "other/repo"]
    get_project.assert_awaited_once_with("other/repo")


@pytest.mark.parametrize(
    ("side_effect", "expected_error"),
    [
        (GitLabAuthError("bad token"), "invalid_auth"),
        (GitLabConnectionError("timeout"), "cannot_connect"),
        (GitLabApiError("boom"), "unknown"),
    ],
)
async def test_user_step_errors_and_recovery(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_setup_entry: AsyncMock,
    side_effect: Exception,
    expected_error: str,
) -> None:
    """Validation errors re-show the user form and the flow recovers afterwards."""
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient, "async_get_current_user", AsyncMock(side_effect=side_effect)
    )

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": expected_error}

    monkeypatch.setattr(
        GitLabClient, "async_get_current_user", AsyncMock(return_value=dict(TEST_USER))
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "projects"


async def test_already_configured_aborts(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A second entry for the same host + user aborts."""
    mock_config_entry.add_to_hass(hass)
    patch_client_defaults(monkeypatch)

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_projects_step_invalid_custom_project(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_setup_entry: AsyncMock,
) -> None:
    """An unknown custom project shows invalid_projects and the flow recovers."""
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_get_project",
        AsyncMock(side_effect=GitLabNotFoundError("404")),
    )

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROJECTS: [], CONF_CUSTOM_PROJECTS: "missing/repo"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "projects"
    assert result["errors"] == {"base": "invalid_projects"}
    assert result["description_placeholders"]["invalid_projects"] == "missing/repo"

    # The project appears (or the typo is fixed): the same input now succeeds.
    monkeypatch.setattr(
        GitLabClient, "async_get_project", AsyncMock(return_value=make_project_info())
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROJECTS: [], CONF_CUSTOM_PROJECTS: "missing/repo"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_PROJECTS] == ["missing/repo"]


async def test_projects_step_custom_project_generic_api_error(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_setup_entry: AsyncMock,
) -> None:
    """A generic API error validating a custom project (not just 404) also lands
    it in invalid_projects - the except GitLabApiError branch, not just NotFound."""
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_get_project",
        AsyncMock(side_effect=GitLabApiError("HTTP 500")),
    )

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROJECTS: [], CONF_CUSTOM_PROJECTS: "broken/repo"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_projects"}
    assert result["description_placeholders"]["invalid_projects"] == "broken/repo"


async def test_projects_step_empty_selection(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_setup_entry: AsyncMock,
) -> None:
    """Submitting no projects at all shows no_projects and the flow recovers."""
    patch_client_defaults(monkeypatch)

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "projects"
    assert result["errors"] == {"base": "no_projects"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROJECTS: [TEST_PROJECT], CONF_CUSTOM_PROJECTS: ""},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_membership_fetch_failure_still_allows_custom_projects(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_setup_entry: AsyncMock,
) -> None:
    """If listing membership projects fails, the projects form still works."""
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_list_membership_projects",
        AsyncMock(side_effect=GitLabApiError("boom")),
    )

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "projects"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROJECTS: [], CONF_CUSTOM_PROJECTS: TEST_PROJECT},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_PROJECTS] == [TEST_PROJECT]


# ----------------------------------------------------------------------
# Reauth
# ----------------------------------------------------------------------


async def test_reauth_success(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """A new valid token for the same account updates the entry."""
    mock_config_entry.add_to_hass(hass)
    patch_client_defaults(monkeypatch)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "glpat-new-token"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_TOKEN] == "glpat-new-token"
    assert mock_config_entry.data[CONF_URL] == TEST_URL


async def test_reauth_invalid_auth_and_recovery(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """A still-invalid token re-shows the form; a valid one then succeeds."""
    mock_config_entry.add_to_hass(hass)
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_get_current_user",
        AsyncMock(side_effect=GitLabAuthError("bad token")),
    )

    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "glpat-still-bad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}

    monkeypatch.setattr(
        GitLabClient, "async_get_current_user", AsyncMock(return_value=dict(TEST_USER))
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "glpat-good"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_TOKEN] == "glpat-good"


@pytest.mark.parametrize(
    ("exception", "expected_error"),
    [
        (GitLabConnectionError("timeout"), "cannot_connect"),
        (GitLabApiError("HTTP 500"), "unknown"),
    ],
)
async def test_reauth_connection_errors(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
    exception: Exception,
    expected_error: str,
) -> None:
    """Connection/generic errors during reauth show the matching form error."""
    mock_config_entry.add_to_hass(hass)
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient, "async_get_current_user", AsyncMock(side_effect=exception)
    )

    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "glpat-new"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}
    assert mock_config_entry.data[CONF_TOKEN] == TEST_TOKEN


async def test_reauth_wrong_account_aborts(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """A token for a different user should abort with wrong_account."""
    mock_config_entry.add_to_hass(hass)
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_get_current_user",
        AsyncMock(return_value={"id": 99, "username": "someone-else"}),
    )

    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "glpat-other-account"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert mock_config_entry.data[CONF_TOKEN] == TEST_TOKEN


# ----------------------------------------------------------------------
# Reconfigure
# ----------------------------------------------------------------------


async def test_reconfigure_success(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """Changing scheme, token and SSL for the same host + user updates the entry."""
    mock_config_entry.add_to_hass(hass)
    patch_client_defaults(monkeypatch)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: "http://gitlab.example.com/",
            CONF_TOKEN: "glpat-rotated",
            CONF_VERIFY_SSL: False,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == {
        CONF_URL: "http://gitlab.example.com",
        CONF_TOKEN: "glpat-rotated",
        CONF_VERIFY_SSL: False,
    }


@pytest.mark.parametrize(
    ("exception", "expected_error"),
    [
        (GitLabAuthError("bad token"), "invalid_auth"),
        (GitLabConnectionError("timeout"), "cannot_connect"),
        (GitLabApiError("HTTP 500"), "unknown"),
    ],
)
async def test_reconfigure_connection_errors(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
    exception: Exception,
    expected_error: str,
) -> None:
    """Auth/connection/generic errors during reconfigure show the matching form
    error and leave the entry untouched."""
    mock_config_entry.add_to_hass(hass)
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient, "async_get_current_user", AsyncMock(side_effect=exception)
    )

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: TEST_URL, CONF_TOKEN: "glpat-new", CONF_VERIFY_SSL: True},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}
    assert mock_config_entry.data[CONF_TOKEN] == TEST_TOKEN


async def test_reconfigure_wrong_account_aborts(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """A token that belongs to a different user aborts and keeps the entry intact."""
    mock_config_entry.add_to_hass(hass)
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_get_current_user",
        AsyncMock(return_value={"id": 99, "username": "someone-else"}),
    )

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: TEST_URL, CONF_TOKEN: "glpat-other-account", CONF_VERIFY_SSL: True},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert mock_config_entry.data[CONF_TOKEN] == TEST_TOKEN


# ----------------------------------------------------------------------
# Options flow
# ----------------------------------------------------------------------


async def test_options_flow_updates_options(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Changing projects and scan interval stores the new options."""
    patch_client_defaults(monkeypatch)
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PROJECTS: [TEST_PROJECT],
            CONF_CUSTOM_PROJECTS: "extra/repo",
            CONF_SCAN_INTERVAL_MINUTES: 30,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_PROJECTS: [TEST_PROJECT, "extra/repo"],
        CONF_SCAN_INTERVAL_MINUTES: 30,
    }
    assert dict(mock_config_entry.options) == {
        CONF_PROJECTS: [TEST_PROJECT, "extra/repo"],
        CONF_SCAN_INTERVAL_MINUTES: 30,
    }


async def test_options_flow_custom_project_already_selected_is_deduped(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Re-entering an already ticked project in the free-text field is silently
    skipped (not re-validated, not duplicated) - the `if key in selected: continue`
    branch, distinct from the invalid/empty error paths."""
    patch_client_defaults(monkeypatch)
    get_project = AsyncMock(return_value=make_project_info())
    monkeypatch.setattr(GitLabClient, "async_get_project", get_project)
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PROJECTS: [TEST_PROJECT],
            CONF_CUSTOM_PROJECTS: TEST_PROJECT,
            CONF_SCAN_INTERVAL_MINUTES: 5,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PROJECTS] == [TEST_PROJECT]
    # The duplicate was never even looked up.
    get_project.assert_not_awaited()


async def test_options_flow_invalid_custom_project(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An unresolvable custom project re-shows the form with invalid_projects."""
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_get_project",
        AsyncMock(side_effect=GitLabNotFoundError("404")),
    )
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PROJECTS: [TEST_PROJECT],
            CONF_CUSTOM_PROJECTS: "missing/repo",
            CONF_SCAN_INTERVAL_MINUTES: 5,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "invalid_projects"}
    assert result["description_placeholders"]["invalid_projects"] == "missing/repo"
    # The stored options are untouched while the flow shows an error.
    assert mock_config_entry.options[CONF_PROJECTS] == [TEST_PROJECT]


async def test_options_flow_empty_selection(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Deselecting everything shows the no_projects error."""
    patch_client_defaults(monkeypatch)
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PROJECTS: [],
            CONF_CUSTOM_PROJECTS: "",
            CONF_SCAN_INTERVAL_MINUTES: 5,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_projects"}


async def test_options_flow_membership_fetch_failure_still_opens(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mock_config_entry: MockConfigEntry,
) -> None:
    """If listing memberships fails when opening the options form, it degrades to
    an empty membership list instead of crashing the flow - the currently
    configured project(s) are still offered as options."""
    patch_client_defaults(monkeypatch)
    monkeypatch.setattr(
        GitLabClient,
        "async_list_membership_projects",
        AsyncMock(side_effect=GitLabApiError("HTTP 500")),
    )
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
