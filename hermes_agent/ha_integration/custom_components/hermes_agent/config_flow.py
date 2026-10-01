"""Config flow for Hermes Agent."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_API_KEY, CONF_PRIMARY_PROFILE, CONF_URL, DEFAULT_URL, DOMAIN


async def _validate(hass, url: str, api_key: str) -> str | None:
    """Return an error key, or None when the API accepts the key."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"{url}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status in (401, 403):
                return "invalid_auth"
            if resp.status != 200:
                return "cannot_connect"
    except (aiohttp.ClientError, TimeoutError):
        return "cannot_connect"
    return None


class HermesAgentConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            if not (error := await _validate(self.hass, url, user_input[CONF_API_KEY])):
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Hermes Agent",
                    data={**user_input, CONF_URL: url},
                )
            errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=DEFAULT_URL): str,
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_PRIMARY_PROFILE): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
