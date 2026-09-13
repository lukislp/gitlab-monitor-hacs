"""Config flow for the GitLab Monitor integration."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    GitLabApiError,
    GitLabAuthError,
    GitLabClient,
    GitLabConnectionError,
    GitLabNotFoundError,
)
from .const import (
    CONF_CUSTOM_PROJECTS,
    CONF_PROJECTS,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_URL,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default=DEFAULT_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_VERIFY_SSL, default=True): BooleanSelector(),
    }
)


def _normalize_url(url: str) -> str:
    """Normalize the GitLab base URL."""
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _parse_custom_projects(raw: str) -> list[str]:
    """Split a comma/newline separated string into project keys."""
    items: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        # Strip whitespace and slashes until nothing changes: one pass each left
        # "/ group/project" as " group/project" (found by fuzz/fuzz_parsers.py).
        key = chunk
        while (stripped := key.strip().strip("/")) != key:
            key = stripped
        if key and key not in items:
            items.append(key)
    return items


async def _async_validate_connection(
    hass: HomeAssistant, url: str, token: str, verify_ssl: bool
) -> tuple[GitLabClient, dict[str, Any]]:
    """Validate URL + token and return (client, user)."""
    session = async_get_clientsession(hass, verify_ssl)
    client = GitLabClient(session, url, token)
    user = await client.async_get_current_user()
    return client, user


class GitLabMonitorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for GitLab Monitor."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}
        self._client: GitLabClient | None = None
        self._membership: list[dict[str, Any]] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> GitLabMonitorOptionsFlow:
        """Return the options flow handler."""
        return GitLabMonitorOptionsFlow()

    # ------------------------------------------------------------------
    # Initial setup
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle URL + token input."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            try:
                client, user = await _async_validate_connection(
                    self.hass, url, user_input[CONF_TOKEN], user_input[CONF_VERIFY_SSL]
                )
            except GitLabAuthError:
                errors["base"] = "invalid_auth"
            except GitLabConnectionError:
                errors["base"] = "cannot_connect"
            except GitLabApiError:
                errors["base"] = "unknown"
            else:
                host = urlparse(url).netloc
                await self.async_set_unique_id(f"{host}_{user['id']}")
                self._abort_if_unique_id_configured()

                self._data = {
                    CONF_URL: url,
                    CONF_TOKEN: user_input[CONF_TOKEN],
                    CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                }
                self._client = client
                self.context["title_placeholders"] = {
                    "host": host,
                    "username": user.get("username", ""),
                }
                try:
                    self._membership = await client.async_list_membership_projects()
                except GitLabApiError:
                    self._membership = []
                return await self.async_step_projects()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_projects(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the projects to monitor."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {"invalid_projects": ""}

        if user_input is not None:
            assert self._client is not None
            selected: list[str] = list(user_input.get(CONF_PROJECTS, []))
            custom = _parse_custom_projects(user_input.get(CONF_CUSTOM_PROJECTS, ""))

            invalid: list[str] = []
            for key in custom:
                if key in selected:
                    continue
                try:
                    await self._client.async_get_project(key)
                except GitLabNotFoundError:
                    invalid.append(key)
                except GitLabApiError:
                    invalid.append(key)
                else:
                    selected.append(key)

            if invalid:
                errors["base"] = "invalid_projects"
                description_placeholders["invalid_projects"] = ", ".join(invalid)
            elif not selected:
                errors["base"] = "no_projects"
            else:
                host = urlparse(self._data[CONF_URL]).netloc
                return self.async_create_entry(
                    title=f"GitLab ({host})",
                    data=self._data,
                    options={
                        CONF_PROJECTS: selected,
                        CONF_SCAN_INTERVAL_MINUTES: DEFAULT_SCAN_INTERVAL_MINUTES,
                    },
                )

        options = [
            SelectOptionDict(
                value=proj["path_with_namespace"],
                label=proj["path_with_namespace"],
            )
            for proj in sorted(
                self._membership, key=lambda p: p["path_with_namespace"].lower()
            )
        ]
        schema = vol.Schema(
            {
                vol.Optional(CONF_PROJECTS, default=[]): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                        sort=True,
                        custom_value=False,
                    )
                ),
                vol.Optional(CONF_CUSTOM_PROJECTS, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
                ),
            }
        )
        return self.async_show_form(
            step_id="projects",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    # ------------------------------------------------------------------
    # Reauth
    # ------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauthentication when the token expired."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            try:
                _, user = await _async_validate_connection(
                    self.hass,
                    entry.data[CONF_URL],
                    user_input[CONF_TOKEN],
                    entry.data.get(CONF_VERIFY_SSL, True),
                )
            except GitLabAuthError:
                errors["base"] = "invalid_auth"
            except GitLabConnectionError:
                errors["base"] = "cannot_connect"
            except GitLabApiError:
                errors["base"] = "unknown"
            else:
                # Same wrong-account guard as reconfigure: a valid token belonging to a
                # DIFFERENT GitLab account must not be silently accepted - it would orphan
                # the entry's unique_id and monitor someone else's project access.
                host = urlparse(entry.data[CONF_URL]).netloc
                await self.async_set_unique_id(f"{host}_{user['id']}")
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Reconfigure (change URL / token / SSL)
    # ------------------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow changing URL, token and SSL verification."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            try:
                _, user = await _async_validate_connection(
                    self.hass, url, user_input[CONF_TOKEN], user_input[CONF_VERIFY_SSL]
                )
            except GitLabAuthError:
                errors["base"] = "invalid_auth"
            except GitLabConnectionError:
                errors["base"] = "cannot_connect"
            except GitLabApiError:
                errors["base"] = "unknown"
            else:
                host = urlparse(url).netloc
                await self.async_set_unique_id(f"{host}_{user['id']}")
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_URL: url,
                        CONF_TOKEN: user_input[CONF_TOKEN],
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=entry.data[CONF_URL]): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Required(CONF_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(
                    CONF_VERIFY_SSL, default=entry.data.get(CONF_VERIFY_SSL, True)
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )


class GitLabMonitorOptionsFlow(OptionsFlow):
    """Handle options: project list and update interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {"invalid_projects": ""}
        entry = self.config_entry

        session = async_get_clientsession(
            self.hass, entry.data.get(CONF_VERIFY_SSL, True)
        )
        client = GitLabClient(session, entry.data[CONF_URL], entry.data[CONF_TOKEN])

        if user_input is not None:
            selected: list[str] = list(user_input.get(CONF_PROJECTS, []))
            custom = _parse_custom_projects(user_input.get(CONF_CUSTOM_PROJECTS, ""))

            invalid: list[str] = []
            for key in custom:
                if key in selected:
                    continue
                try:
                    await client.async_get_project(key)
                except GitLabApiError:
                    invalid.append(key)
                else:
                    selected.append(key)

            if invalid:
                errors["base"] = "invalid_projects"
                description_placeholders["invalid_projects"] = ", ".join(invalid)
            elif not selected:
                errors["base"] = "no_projects"
            else:
                return self.async_create_entry(
                    data={
                        CONF_PROJECTS: selected,
                        CONF_SCAN_INTERVAL_MINUTES: int(
                            user_input[CONF_SCAN_INTERVAL_MINUTES]
                        ),
                    }
                )

        current: list[str] = list(entry.options.get(CONF_PROJECTS, []))
        try:
            membership = await client.async_list_membership_projects()
        except GitLabApiError:
            membership = []

        known = {p["path_with_namespace"] for p in membership} | set(current)
        options = [
            SelectOptionDict(value=key, label=key)
            for key in sorted(known, key=str.lower)
        ]
        schema = vol.Schema(
            {
                vol.Optional(CONF_PROJECTS, default=current): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                        sort=True,
                    )
                ),
                vol.Optional(CONF_CUSTOM_PROJECTS, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL_MINUTES,
                    default=entry.options.get(
                        CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_MINUTES,
                        max=MAX_SCAN_INTERVAL_MINUTES,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders=description_placeholders,
        )
