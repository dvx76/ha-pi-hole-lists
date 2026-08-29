"""Config flow for the Pi-hole Lists integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from hole.exceptions import HoleAuthenticationError, HoleConnectionError, HoleError
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import BooleanSelector

from .api import PiHoleV6Lists
from .const import (
    CONF_APP_PASSWORD,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class PiHoleListsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pi-hole Lists."""

    VERSION = 1

    async def async_step_user(
        self, user_input: Mapping[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step: URL, app password, verify-SSL."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._async_validate_credentials(
                    user_input[CONF_URL],
                    user_input[CONF_APP_PASSWORD],
                    user_input.get(CONF_VERIFY_SSL, True),
                )
            except HoleAuthenticationError:
                errors["base"] = "invalid_auth"
            except (HoleConnectionError, HoleError):
                errors["base"] = "cannot_connect"
            except Exception:
                # Never probe authentication in a loop; anything unexpected is
                # treated as unreachable.
                _LOGGER.exception("Unexpected error validating Pi-hole credentials")
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_URL],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self._user_schema(user_input or {}),
            description_placeholders={
                "url_example": "http://pi.hole:8081",
            },
            errors=errors,
        )

    def _user_schema(self, user_input: dict[str, Any]) -> vol.Schema:
        """Build the user step schema; verify-SSL is only shown for https."""
        schema = {
            vol.Required(CONF_URL, default=user_input.get(CONF_URL, "")): str,
            vol.Required(CONF_APP_PASSWORD): str,
        }
        if user_input.get(CONF_URL, "").startswith("https"):
            schema[
                vol.Required(
                    CONF_VERIFY_SSL,
                    default=user_input.get(CONF_VERIFY_SSL, True),
                )
            ] = BooleanSelector()
        return vol.Schema(schema)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Start reauthentication for an existing entry."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_not_needed")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: Mapping[str, Any] | None = None
    ) -> FlowResult:
        """Confirm the new app password."""
        errors: dict[str, str] = {}
        if user_input is not None and self._reauth_entry is not None:
            try:
                await self._async_validate_credentials(
                    self._reauth_entry.data[CONF_URL],
                    user_input[CONF_APP_PASSWORD],
                    self._reauth_entry.data.get(CONF_VERIFY_SSL, True),
                )
            except HoleAuthenticationError:
                errors["base"] = "invalid_auth"
            except (HoleConnectionError, HoleError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating Pi-hole credentials")
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_APP_PASSWORD: user_input[CONF_APP_PASSWORD],
                    },
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_APP_PASSWORD): str}),
            errors=errors,
        )

    async def _async_validate_credentials(
        self, url: str, password: str, verify_ssl: bool
    ) -> None:
        """Authenticate once and release the session immediately.

        Pi-hole rate-limits failed logins, so there is exactly one attempt
        per flow step — never a retry loop.
        """
        api = PiHoleV6Lists(
            url,
            password,
            session=aiohttp_client.async_get_clientsession(self.hass),
            verify_ssl=verify_ssl,
        )
        try:
            await api.authenticate()
        finally:
            await api.logout()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PiHoleListsOptionsFlow:
        """Get the options flow for this handler."""
        # The entry is resolved via the OptionsFlow.config_entry property
        # (its setter was removed in HA 2025.12), so it must not be stored
        # on the flow at construction time.
        return PiHoleListsOptionsFlow()


class PiHoleListsOptionsFlow(OptionsFlow):
    """Handle options for Pi-hole Lists."""

    async def async_step_init(
        self, user_input: Mapping[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                }
            ),
        )
