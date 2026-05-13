"""Local LLM tools."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.components.homeassistant import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    llm,
)
from homeassistant.helpers.storage import Store
from homeassistant.util.json import JsonObjectType

from .const import DOMAIN

MEMORY_STORAGE_VERSION = 1
MEMORY_TEXT_MAX_LENGTH = 500
MEMORY_ITEMS_MAX = 500
HOME_STATUS_ENTITY_LIMIT = 80
MEMORY_SEARCH_LIMIT = 8
SECRET_PATTERN = re.compile(
    r"\b(password|passphrase|api[_ -]?key|secret|token|bearer|private[_ -]?key)\b",
    re.IGNORECASE,
)

ACTIVE_STATES = {"on", "open", "opening", "unlocked", "detected", "motion", "playing"}
PROBLEM_STATES = {"unavailable", "unknown", "jammed", "problem"}
STATUS_SENSOR_DEVICE_CLASSES = {
    "aqi",
    "battery",
    "co",
    "co2",
    "humidity",
    "illuminance",
    "pm10",
    "pm25",
    "power",
    "temperature",
    "volatile_organic_compounds",
}


@dataclass(slots=True)
class EntityStatus:
    """Entity state exposed to the model."""

    entity_id: str
    name: str
    domain: str
    state: str
    area: str | None

    def as_dict(self) -> dict[str, Any]:
        """Serialise the state."""
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "domain": self.domain,
            "state": self.state,
            "area": self.area,
        }


class MemoryStore:
    """Memory store backed by Home Assistant storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Prepare the store."""
        self._store = Store(
            hass,
            MEMORY_STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}.memory",
        )
        self._loaded = False
        self._items: list[dict[str, Any]] = []

    async def _async_ensure_loaded(self) -> None:
        """Load stored memories."""
        if self._loaded:
            return

        data = await self._store.async_load()
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            self._items = [
                item for item in data["items"] if isinstance(item, dict)
            ]
        self._loaded = True

    async def async_save_memory(self, text: str) -> dict[str, Any]:
        """Store one memory."""
        await self._async_ensure_loaded()
        text = " ".join(text.strip().split())
        if not text:
            return {"saved": False, "error": "Memory text was empty"}
        if SECRET_PATTERN.search(text):
            return {
                "saved": False,
                "error": "Memory text looked like it may contain a secret",
            }
        if len(text) > MEMORY_TEXT_MAX_LENGTH:
            text = text[:MEMORY_TEXT_MAX_LENGTH].rstrip()

        now = datetime.now(timezone.utc).isoformat()
        item = {
            "id": uuid.uuid4().hex,
            "text": text,
            "created_at": now,
        }
        self._items.append(item)
        self._items = self._items[-MEMORY_ITEMS_MAX:]
        await self._store.async_save({"items": self._items})
        return {"saved": True, "memory": item}

    async def async_search_memory(
        self, query: str | None, limit: int = MEMORY_SEARCH_LIMIT
    ) -> dict[str, Any]:
        """Search memories by keyword score."""
        await self._async_ensure_loaded()
        limit = max(1, min(limit, MEMORY_SEARCH_LIMIT))
        query_tokens = _tokenise(query or "")

        if not query_tokens:
            matches = list(reversed(self._items[-limit:]))
            return {"query": query or "", "memories": matches}

        scored: list[tuple[int, dict[str, Any]]] = []
        for item in self._items:
            text = item.get("text")
            if not isinstance(text, str):
                continue
            text_tokens = _tokenise(text)
            score = sum(1 for token in query_tokens if token in text_tokens)
            if score:
                scored.append((score, item))

        scored.sort(
            key=lambda row: (row[0], row[1].get("created_at", "")), reverse=True
        )
        return {
            "query": query,
            "memories": [item for _score, item in scored[:limit]],
        }


class GetHomeStatusTool(llm.Tool):
    """Read Assist-exposed entity state."""

    name = "GetHomeStatus"
    description = (
        "Get a read-only summary of the current home status from entities exposed "
        "to Assist. Use this for questions like 'what is the house status', "
        "'what is on', or 'is anything open'."
    )
    parameters = vol.Schema(
        {
            vol.Optional("area"): str,
            vol.Optional("domain"): str,
            vol.Optional("include_all", default=False): bool,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Read current exposed state."""
        args = tool_input.tool_args
        area_filter = _normalise(args.get("area"))
        domain_filter = _normalise(args.get("domain"))
        include_all = bool(args.get("include_all", False))
        assistant = llm_context.assistant or "conversation"

        entity_reg = er.async_get(hass)
        area_reg = ar.async_get(hass)
        device_reg = dr.async_get(hass)

        domain_counts: Counter[str] = Counter()
        area_counts: Counter[str] = Counter()
        active: list[EntityStatus] = []
        problems: list[EntityStatus] = []
        climate: list[EntityStatus] = []
        readings: list[EntityStatus] = []
        all_entities: list[EntityStatus] = []

        for state in hass.states.async_all():
            entity_id = state.entity_id
            if not async_should_expose(hass, assistant, entity_id):
                continue

            domain = entity_id.split(".", 1)[0]
            if domain_filter and domain_filter != domain:
                continue

            area_name = _entity_area_name(entity_id, entity_reg, area_reg, device_reg)
            if area_filter and area_filter != _normalise(area_name):
                continue

            entity_status = EntityStatus(
                entity_id=entity_id,
                name=state.name,
                domain=domain,
                state=state.state,
                area=area_name,
            )
            domain_counts[domain] += 1
            area_counts[area_name or "No area"] += 1

            state_value = state.state.lower()
            if state_value in ACTIVE_STATES:
                active.append(entity_status)
            if state_value in PROBLEM_STATES:
                problems.append(entity_status)
            if domain == "climate":
                climate.append(entity_status)
            if (
                domain == "sensor"
                and str(state.attributes.get("device_class", ""))
                in STATUS_SENSOR_DEVICE_CLASSES
            ):
                readings.append(entity_status)
            if include_all:
                all_entities.append(entity_status)

        active = active[:HOME_STATUS_ENTITY_LIMIT]
        problems = problems[:HOME_STATUS_ENTITY_LIMIT]
        climate = climate[:HOME_STATUS_ENTITY_LIMIT]
        readings = readings[:HOME_STATUS_ENTITY_LIMIT]
        all_entities = all_entities[:HOME_STATUS_ENTITY_LIMIT]

        summary_parts = [
            f"{sum(domain_counts.values())} exposed entities",
            f"{len(active)} active",
            f"{len(problems)} unavailable or unknown",
        ]
        if area_filter:
            summary_parts.append(f"area filter: {args.get('area')}")
        if domain_filter:
            summary_parts.append(f"domain filter: {domain_filter}")

        result: dict[str, Any] = {
            "summary": ", ".join(summary_parts),
            "domains": dict(domain_counts),
            "areas": dict(area_counts),
            "active": [item.as_dict() for item in active],
            "problems": [item.as_dict() for item in problems],
            "climate": [item.as_dict() for item in climate],
            "readings": [item.as_dict() for item in readings],
        }
        if include_all:
            result["entities"] = [item.as_dict() for item in all_entities]
        return result


class SaveMemoryTool(llm.Tool):
    """Save assistant memory."""

    name = "SaveMemory"
    description = (
        "Save a durable memory about the user's stable preferences, home naming, "
        "routines, or instructions. Do not save secrets, passwords, tokens, or "
        "one-off transient facts."
    )
    parameters = vol.Schema({vol.Required("text"): str})

    def __init__(self, memory_store: MemoryStore) -> None:
        """Bind memory storage."""
        self._memory_store = memory_store

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Save memory text."""
        return await self._memory_store.async_save_memory(
            str(tool_input.tool_args.get("text", ""))
        )


class SearchMemoryTool(llm.Tool):
    """Search assistant memory."""

    name = "SearchMemory"
    description = (
        "Search durable memories saved for this Home Assistant assistant. Use this "
        "when the user's request may depend on previously saved preferences, room "
        "aliases, routines, or instructions."
    )
    parameters = vol.Schema(
        {
            vol.Optional("query", default=""): str,
            vol.Optional("limit", default=5): int,
        }
    )

    def __init__(self, memory_store: MemoryStore) -> None:
        """Bind memory storage."""
        self._memory_store = memory_store

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Search stored memories."""
        try:
            limit = int(tool_input.tool_args.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5
        return await self._memory_store.async_search_memory(
            tool_input.tool_args.get("query", ""),
            limit,
        )


def build_extra_tools(memory_store: MemoryStore) -> list[llm.Tool]:
    """Build local LLM tools."""
    return [
        GetHomeStatusTool(),
        SaveMemoryTool(memory_store),
        SearchMemoryTool(memory_store),
    ]


def _entity_area_name(
    entity_id: str,
    entity_reg: er.EntityRegistry,
    area_reg: ar.AreaRegistry,
    device_reg: dr.DeviceRegistry,
) -> str | None:
    """Look up the entity area name."""
    entity_entry = entity_reg.async_get(entity_id)
    area_id = entity_entry.area_id if entity_entry else None
    if not area_id and entity_entry and entity_entry.device_id:
        device = device_reg.async_get(entity_entry.device_id)
        area_id = device.area_id if device else None
    if not area_id:
        return None
    area = area_reg.async_get_area(area_id)
    return area.name if area else None


def _normalise(value: Any) -> str | None:
    """Normalise filter text."""
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _tokenise(text: str) -> set[str]:
    """Tokenise memory text."""
    return set(re.findall(r"[a-z0-9_'-]+", text.lower()))
