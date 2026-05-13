"""Conversation platform for OpenAI OAuth Assist."""

from __future__ import annotations

import logging
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent, llm
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OpenAIAssistConfigEntry
from .api import (
    OpenAIAssistError,
    OpenAIAuthError,
    OpenAIConnectionError,
    OpenAIResponsesClient,
    OpenAIRateLimitError,
    OpenAIResponseError,
)
from .const import (
    AUTH_METHOD_CHATGPT_CODEX,
    CONF_AUTH_METHOD,
    CONF_MODEL,
    CONF_SYSTEM_PROMPT,
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    DOMAIN,
)
from .tools import MemoryStore, build_extra_tools

_LOGGER = logging.getLogger(__name__)
MAX_TOOL_ITERATIONS = 10


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenAIAssistConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the conversation entity."""
    async_add_entities([OpenAIAssistConversationEntity(entry)])


class OpenAIAssistConversationEntity(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
):
    """OpenAI-backed conversation agent."""

    _attr_has_entity_name = True
    _attr_supports_streaming = True
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: OpenAIAssistConfigEntry) -> None:
        """Initialise the entity."""
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_name = entry.title
        self._memory_store: MemoryStore | None = None

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return supported languages."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register this entity as a conversation agent."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister this entity as a conversation agent."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Process the user input with OpenAI."""
        options = {**self.entry.data, **self.entry.options}
        client = self.entry.runtime_data

        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                [llm.LLM_API_ASSIST],
                options.get(CONF_SYSTEM_PROMPT, DEFAULT_SYSTEM_PROMPT),
                getattr(user_input, "extra_system_prompt", None),
            )
            self._async_add_extra_tools(chat_log)
            if client.chatgpt_token_needs_refresh():
                await self._async_refresh_chatgpt_tokens(client)
            try:
                await self._async_generate_chat_log(
                    user_input, chat_log, client, options
                )
            except OpenAIAuthError:
                if not await self._async_try_refresh_auth(user_input, client):
                    raise
                await self._async_generate_chat_log(
                    user_input, chat_log, client, options
                )
        except conversation.ConverseError as err:
            return err.as_conversation_result()
        except OpenAIAuthError:
            _LOGGER.warning("OpenAI authentication failed; starting reauth flow")
            self.entry.async_start_reauth(self.hass)
            return _error_result(
                user_input,
                "OpenAI authentication failed. Reconfigure the integration with valid credentials.",
            )
        except OpenAIRateLimitError:
            return _error_result(
                user_input,
                "OpenAI rate limits were exceeded. Try again later.",
            )
        except OpenAIConnectionError:
            return _error_result(
                user_input,
                "Home Assistant could not connect to OpenAI.",
            )
        except OpenAIResponseError:
            _LOGGER.warning("OpenAI returned an invalid or failed response")
            return _error_result(
                user_input,
                "OpenAI returned an error while generating the response.",
            )
        except HomeAssistantError:
            _LOGGER.warning("Home Assistant could not execute the assistant request")
            return _error_result(
                user_input,
                "Home Assistant could not execute that request.",
            )

        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    async def _async_generate_chat_log(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        client: OpenAIResponsesClient,
        options: dict[str, Any],
    ) -> None:
        """Generate assistant content and execute Home Assistant tool calls."""
        for _iteration in range(MAX_TOOL_ITERATIONS):
            async for _content in chat_log.async_add_delta_content_stream(
                user_input.agent_id,
                client.async_stream_chat_log(
                    model=options.get(CONF_MODEL, DEFAULT_MODEL),
                    chat_log=chat_log,
                ),
            ):
                pass

            if not chat_log.unresponded_tool_results:
                return

        raise OpenAIResponseError("OpenAI exceeded the maximum tool-call iterations")

    def _async_add_extra_tools(self, chat_log: conversation.ChatLog) -> None:
        """Append integration-specific tools to the Home Assistant LLM API."""
        if chat_log.llm_api is None:
            return
        if self._memory_store is None:
            self._memory_store = MemoryStore(self.hass, self.entry.entry_id)

        existing = {tool.name for tool in chat_log.llm_api.tools}
        for tool in build_extra_tools(self._memory_store):
            if tool.name not in existing:
                chat_log.llm_api.tools.append(tool)

    async def _async_try_refresh_auth(
        self,
        user_input: conversation.ConversationInput,
        client: OpenAIResponsesClient,
    ) -> bool:
        """Refresh ChatGPT/Codex OAuth credentials after one auth failure."""
        if self.entry.data.get(CONF_AUTH_METHOD) != AUTH_METHOD_CHATGPT_CODEX:
            return False
        if client.chatgpt_refresh_token is None:
            return False

        try:
            await self._async_refresh_chatgpt_tokens(client)
        except OpenAIAssistError:
            return False

        _LOGGER.info(
            "Refreshed ChatGPT/Codex OAuth token for conversation agent %s",
            user_input.agent_id,
        )
        return True

    async def _async_refresh_chatgpt_tokens(
        self, client: OpenAIResponsesClient
    ) -> None:
        """Refresh and persist ChatGPT/Codex OAuth tokens."""
        updates = await client.async_refresh_chatgpt_tokens()
        if not updates:
            return

        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, **updates},
        )


def _error_result(
    user_input: conversation.ConversationInput, message: str
) -> conversation.ConversationResult:
    """Return a safe assistant-visible error response."""
    response = intent.IntentResponse(language=user_input.language)
    response.async_set_speech(message)
    return conversation.ConversationResult(
        response=response,
        conversation_id=user_input.conversation_id,
        continue_conversation=False,
    )
