"""Constants for the OpenAI OAuth Assist proof of concept."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "openai_oauth_assist"

CONF_AUTH_METHOD: Final = "auth_method"
CONF_BASE_URL: Final = "base_url"
CONF_CHATGPT_ACCESS_TOKEN: Final = "chatgpt_access_token"
CONF_CHATGPT_ACCOUNT_ID: Final = "chatgpt_account_id"
CONF_CHATGPT_ID_TOKEN: Final = "chatgpt_id_token"
CONF_CHATGPT_LAST_REFRESH: Final = "chatgpt_last_refresh"
CONF_CHATGPT_REFRESH_TOKEN: Final = "chatgpt_refresh_token"
CONF_CODEX_AUTH_JSON_PATH: Final = "codex_auth_json_path"
CONF_CODEX_CLIENT_VERSION: Final = "codex_client_version"
CONF_CODEX_INSTALLATION_ID: Final = "codex_installation_id"
CONF_MODEL: Final = "model"
CONF_SYSTEM_PROMPT: Final = "system_prompt"

AUTH_METHOD_CHATGPT_CODEX: Final = "chatgpt_codex"

DEFAULT_CHATGPT_CODEX_BASE_URL: Final = "https://chatgpt.com/backend-api/codex"
DEFAULT_CODEX_CLIENT_VERSION: Final = "0.130.0"
DEFAULT_MODEL: Final = "gpt-5.3-codex-spark"
DEFAULT_NAME: Final = "OpenAI Assist POC"
DEFAULT_SYSTEM_PROMPT: Final = (
    "You are a concise Home Assistant voice assistant. Answer the user's "
    "smart-home question directly. Do not claim to control devices unless "
    "Home Assistant has supplied tool results showing the action happened. "
    "Use GetHomeStatus for whole-home status questions. Use SearchMemory when "
    "a request may depend on saved user preferences or house-specific aliases. "
    "Use SaveMemory only when the user explicitly asks you to remember a stable "
    "preference, routine, alias, or instruction. Never save secrets."
)
OPENAI_CODEX_CLIENT_ID: Final = "app_EMoamEEZ73f0CkXaXp7hrann"
