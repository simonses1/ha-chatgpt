"""Config flow for the OpenAI OAuth Assist proof of concept."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import os
from pathlib import Path
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    CodexAuthJsonError,
    OpenAIAuthError,
    OpenAIConnectionError,
    OpenAIResponsesClient,
    OpenAIResponseError,
    parse_codex_auth_json,
)
from .const import (
    AUTH_METHOD_CHATGPT_CODEX,
    CONF_AUTH_METHOD,
    CONF_BASE_URL,
    CONF_CODEX_AUTH_JSON_PATH,
    CONF_CODEX_CLIENT_VERSION,
    CONF_CODEX_INSTALLATION_ID,
    CONF_MODEL,
    CONF_SYSTEM_PROMPT,
    DEFAULT_CHATGPT_CODEX_BASE_URL,
    DEFAULT_CODEX_CLIENT_VERSION,
    DEFAULT_MODEL,
    DEFAULT_NAME,
    DEFAULT_SYSTEM_PROMPT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class CodexAuthFileError(ValueError):
    """The configured Codex auth file path is not readable."""


def _default_codex_auth_json_path() -> str:
    """Return Codex's normal auth.json path for the Home Assistant runtime."""
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return str(Path(codex_home).expanduser() / "auth.json")
    return "~/.codex/auth.json"


def _read_codex_auth_json_file(path_value: str) -> str:
    """Read Codex auth.json from a validated local Home Assistant path."""
    if not path_value.strip():
        raise CodexAuthFileError("Codex auth.json path is empty")

    path = Path(path_value).expanduser()
    try:
        if not path.is_file():
            raise CodexAuthFileError("Codex auth.json path is not a readable file")
        return path.read_text(encoding="utf-8")
    except OSError as err:
        raise CodexAuthFileError("Codex auth.json path is not readable") from err
    except UnicodeDecodeError as err:
        raise CodexAuthFileError("Codex auth.json is not UTF-8 text") from err


def _codex_data_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Return the ChatGPT/Codex-only config flow schema."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME,
                default=defaults.get(CONF_NAME, DEFAULT_NAME),
            ): TextSelector(),
            vol.Required(
                CONF_CODEX_AUTH_JSON_PATH,
                default=defaults.get(
                    CONF_CODEX_AUTH_JSON_PATH, _default_codex_auth_json_path()
                ),
            ): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Required(
                CONF_MODEL,
                default=defaults.get(CONF_MODEL, DEFAULT_MODEL),
            ): TextSelector(),
            vol.Required(
                CONF_SYSTEM_PROMPT,
                default=defaults.get(CONF_SYSTEM_PROMPT, DEFAULT_SYSTEM_PROMPT),
            ): TextSelector(
                TextSelectorConfig(multiline=True, type=TextSelectorType.TEXT)
            ),
        }
    )


def _options_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Return options flow schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_MODEL,
                default=defaults.get(CONF_MODEL, DEFAULT_MODEL),
            ): TextSelector(),
            vol.Required(
                CONF_SYSTEM_PROMPT,
                default=defaults.get(CONF_SYSTEM_PROMPT, DEFAULT_SYSTEM_PROMPT),
            ): TextSelector(
                TextSelectorConfig(multiline=True, type=TextSelectorType.TEXT)
            ),
        }
    )


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate user credentials against OpenAI."""
    session = async_get_clientsession(hass)
    client = OpenAIResponsesClient.from_config_entry_data(session, data)
    await client.async_validate()


async def _normalise_codex_config_data(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Read auth.json, extract tokens, and add hidden Codex defaults."""
    data = dict(user_input)
    path_value = data.get(CONF_CODEX_AUTH_JSON_PATH) or _default_codex_auth_json_path()

    codex_auth_json = await hass.async_add_executor_job(
        _read_codex_auth_json_file, path_value
    )
    parsed_tokens = parse_codex_auth_json(codex_auth_json)
    data.update(parsed_tokens)
    data[CONF_AUTH_METHOD] = AUTH_METHOD_CHATGPT_CODEX
    data[CONF_CODEX_AUTH_JSON_PATH] = path_value
    data[CONF_BASE_URL] = DEFAULT_CHATGPT_CODEX_BASE_URL
    data[CONF_CODEX_CLIENT_VERSION] = DEFAULT_CODEX_CLIENT_VERSION
    data.setdefault(CONF_CODEX_INSTALLATION_ID, str(uuid.uuid4()))
    return data


class OpenAIAssistConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the OpenAI OAuth Assist config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the ChatGPT/Codex-only setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                data = await _normalise_codex_config_data(self.hass, user_input)
                await self.async_set_unique_id(f"{DOMAIN}_{AUTH_METHOD_CHATGPT_CODEX}")
                if self.source != SOURCE_REAUTH:
                    self._abort_if_unique_id_configured()
                await validate_input(self.hass, data)
            except CodexAuthFileError:
                errors["base"] = "codex_auth_file_unreadable"
            except CodexAuthJsonError:
                errors["base"] = "invalid_codex_auth_json"
            except OpenAIAuthError:
                errors["base"] = "invalid_auth"
            except OpenAIConnectionError:
                errors["base"] = "cannot_connect"
            except OpenAIResponseError:
                errors["base"] = "api_error"
            except Exception:
                _LOGGER.exception("Unexpected exception while validating OpenAI auth")
                errors["base"] = "unknown"
            else:
                title = data.pop(CONF_NAME)
                if self.source == SOURCE_REAUTH:
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(),
                        data_updates=data,
                    )
                return self.async_create_entry(
                    title=title,
                    data=data,
                    options={
                        CONF_MODEL: data[CONF_MODEL],
                        CONF_SYSTEM_PROMPT: data[CONF_SYSTEM_PROMPT],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_codex_data_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect replacement ChatGPT/Codex credentials."""
        if user_input is None:
            entry = self._get_reauth_entry()
            defaults = dict(entry.data)
            defaults[CONF_NAME] = entry.title
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=_codex_data_schema(defaults),
                errors={},
            )
        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Create the options flow."""
        return OpenAIAssistOptionsFlow()


class OpenAIAssistOptionsFlow(OptionsFlow):
    """Handle reconfigurable model options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        defaults = {
            **self.config_entry.data,
            **self.config_entry.options,
        }
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(defaults),
        )
