"""OpenAI OAuth Assist proof-of-concept integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OpenAIResponsesClient
from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.CONVERSATION]

OpenAIAssistConfigEntry = ConfigEntry[OpenAIResponsesClient]


async def async_setup_entry(
    hass: HomeAssistant, entry: OpenAIAssistConfigEntry
) -> bool:
    """Set up OpenAI OAuth Assist from a config entry."""
    session = async_get_clientsession(hass)
    entry.runtime_data = OpenAIResponsesClient.from_config_entry_data(
        session, entry.data
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: OpenAIAssistConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: HomeAssistant, entry: OpenAIAssistConfigEntry
) -> None:
    """Reload a config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def async_migrate_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Migrate old config entries."""
    return True
