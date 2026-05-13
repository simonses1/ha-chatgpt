"""Diagnostics for OpenAI OAuth Assist."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import OpenAIAssistConfigEntry
from .const import (
    CONF_CHATGPT_ACCESS_TOKEN,
    CONF_CHATGPT_ID_TOKEN,
    CONF_CHATGPT_REFRESH_TOKEN,
)

TO_REDACT = {
    CONF_CHATGPT_ACCESS_TOKEN,
    CONF_CHATGPT_ID_TOKEN,
    CONF_CHATGPT_REFRESH_TOKEN,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OpenAIAssistConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with secrets redacted."""
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
    }
