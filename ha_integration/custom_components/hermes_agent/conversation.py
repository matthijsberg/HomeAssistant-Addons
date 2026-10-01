"""Conversation agent that forwards Assist turns to the user's Hermes profile."""

from __future__ import annotations

from collections import OrderedDict
import logging
import re
from typing import Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_API_KEY,
    CONF_PRIMARY_PROFILE,
    CONF_URL,
    DOMAIN,
    REQUEST_TIMEOUT,
    SESSION_HEADER,
    VOICE_STYLE_PROMPT,
)

_LOGGER = logging.getLogger(__name__)

# HA conversation_id -> (profile, Hermes session id). Bounded; HA ends idle chats anyway.
_MAX_TRACKED_CONVERSATIONS = 200
_MARKDOWN = re.compile(r"(\*\*|__|`+|^#+\s*)", re.MULTILINE)


def sanitize_profile_name(raw: str) -> str:
    """Mirror the add-on's profile-init.sh sanitize_profile_name()."""
    base = raw.rsplit("/", 1)[-1].removeprefix(".")
    return re.sub(r"[^0-9A-Za-z_]+", "_", base).rstrip("_")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([HermesConversationEntity(entry)])


class HermesConversationEntity(conversation.ConversationEntity):
    """Hermes Agent, routed per Home Assistant user."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._url: str = entry.data[CONF_URL]
        self._api_key: str = entry.data[CONF_API_KEY]
        self._primary: str = sanitize_profile_name(entry.data[CONF_PRIMARY_PROFILE])
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Hermes Agent",
            "manufacturer": "Nous Research",
            "entry_type": "service",
        }
        self._sessions: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self._profile_exists: dict[str, bool] = {}

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    async def _profile_for_user(self, user_id: str | None) -> str:
        """HA user -> linked person entity -> Hermes profile, else the primary profile."""
        if not user_id:
            return self._primary
        for state in self.hass.states.async_all("person"):
            if state.attributes.get("user_id") != user_id:
                continue
            name = sanitize_profile_name(state.object_id)
            if not name or name == self._primary:
                return self._primary
            if name not in self._profile_exists:
                self._profile_exists[name] = await self._health_ok(f"/profile/{name}")
            return name if self._profile_exists[name] else self._primary
        return self._primary

    async def _health_ok(self, prefix: str) -> bool:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                f"{self._url}{prefix}/v1/health", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return resp.status == 200
        except (aiohttp.ClientError, TimeoutError):
            return False

    def _prefix(self, profile: str) -> str:
        return "" if profile == self._primary else f"/profile/{profile}"

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        profile = await self._profile_for_user(user_input.context.user_id)
        tracked = self._sessions.get(chat_log.conversation_id)
        hermes_session = tracked[1] if tracked and tracked[0] == profile else None

        system = VOICE_STYLE_PROMPT
        if user_input.extra_system_prompt:
            system = f"{system}\n\n{user_input.extra_system_prompt}"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if hermes_session:
            headers[SESSION_HEADER] = hermes_session
        payload = {
            "model": "hermes-agent",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_input.text},
            ],
        }

        session = async_get_clientsession(self.hass)
        try:
            async with session.post(
                f"{self._url}{self._prefix(profile)}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:300]
                    _LOGGER.error("Hermes (%s) returned %s: %s", profile, resp.status, body)
                    raise HomeAssistantError(f"Hermes returned HTTP {resp.status}")
                data = await resp.json()
                new_session = resp.headers.get(SESSION_HEADER)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"Cannot reach Hermes: {err}") from err

        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as err:
            raise HomeAssistantError("Unexpected response from Hermes") from err

        if new_session:
            self._sessions[chat_log.conversation_id] = (profile, new_session)
            self._sessions.move_to_end(chat_log.conversation_id)
            while len(self._sessions) > _MAX_TRACKED_CONVERSATIONS:
                self._sessions.popitem(last=False)

        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=user_input.agent_id,
                content=_MARKDOWN.sub("", text).strip(),
            )
        )
        return conversation.async_get_result_from_chat_log(user_input, chat_log)
