"""Local LLM tool tests with stubs."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from dataclasses import dataclass
from types import SimpleNamespace


def _install_stubs() -> None:
    """Install Home Assistant import stubs."""
    homeassistant = sys.modules.setdefault(
        "homeassistant", types.ModuleType("homeassistant")
    )
    del homeassistant

    config_entries = sys.modules.setdefault(
        "homeassistant.config_entries",
        types.ModuleType("homeassistant.config_entries"),
    )
    const = sys.modules.setdefault(
        "homeassistant.const", types.ModuleType("homeassistant.const")
    )
    core = sys.modules.setdefault(
        "homeassistant.core", types.ModuleType("homeassistant.core")
    )
    components = sys.modules.setdefault(
        "homeassistant.components", types.ModuleType("homeassistant.components")
    )
    homeassistant_component = sys.modules.setdefault(
        "homeassistant.components.homeassistant",
        types.ModuleType("homeassistant.components.homeassistant"),
    )
    helpers = sys.modules.setdefault(
        "homeassistant.helpers", types.ModuleType("homeassistant.helpers")
    )
    aiohttp_client = sys.modules.setdefault(
        "homeassistant.helpers.aiohttp_client",
        types.ModuleType("homeassistant.helpers.aiohttp_client"),
    )
    area_registry = sys.modules.setdefault(
        "homeassistant.helpers.area_registry",
        types.ModuleType("homeassistant.helpers.area_registry"),
    )
    device_registry = sys.modules.setdefault(
        "homeassistant.helpers.device_registry",
        types.ModuleType("homeassistant.helpers.device_registry"),
    )
    entity_registry = sys.modules.setdefault(
        "homeassistant.helpers.entity_registry",
        types.ModuleType("homeassistant.helpers.entity_registry"),
    )
    llm = sys.modules.setdefault(
        "homeassistant.helpers.llm", types.ModuleType("homeassistant.helpers.llm")
    )
    storage = sys.modules.setdefault(
        "homeassistant.helpers.storage",
        types.ModuleType("homeassistant.helpers.storage"),
    )
    util = sys.modules.setdefault(
        "homeassistant.util", types.ModuleType("homeassistant.util")
    )
    util_json = sys.modules.setdefault(
        "homeassistant.util.json", types.ModuleType("homeassistant.util.json")
    )
    voluptuous = sys.modules.setdefault("voluptuous", types.ModuleType("voluptuous"))

    class ConfigEntry:
        @classmethod
        def __class_getitem__(cls, item):
            return cls

    class Platform:
        CONVERSATION = "conversation"

    class HomeAssistant:
        pass

    class Tool:
        pass

    @dataclass
    class ToolInput:
        tool_name: str
        tool_args: dict
        id: str = "call_1"
        external: bool = False

    @dataclass
    class LLMContext:
        assistant: str | None = None

    class Store:
        def __init__(self, hass, version, key):
            self._hass = hass
            self._key = key
            hass._fake_store_data = getattr(hass, "_fake_store_data", {})

        async def async_load(self):
            return self._hass._fake_store_data.get(self._key)

        async def async_save(self, data):
            self._hass._fake_store_data[self._key] = data

    def async_get_clientsession(hass):
        return None

    class _EmptyRegistry:
        def async_get(self, key):
            return None

        def async_get_area(self, key):
            return None

    def _registry_get(hass):
        return _EmptyRegistry()

    def _schema(value):
        return value

    def _marker(name, default=None):
        return name

    config_entries.ConfigEntry = ConfigEntry
    const.Platform = Platform
    core.HomeAssistant = HomeAssistant
    aiohttp_client.async_get_clientsession = async_get_clientsession
    homeassistant_component.async_should_expose = (
        lambda hass, assistant, entity_id: True
    )
    area_registry.AreaRegistry = _EmptyRegistry
    area_registry.async_get = _registry_get
    device_registry.DeviceRegistry = _EmptyRegistry
    device_registry.async_get = _registry_get
    entity_registry.EntityRegistry = _EmptyRegistry
    entity_registry.async_get = _registry_get
    helpers.area_registry = area_registry
    helpers.device_registry = device_registry
    helpers.entity_registry = entity_registry
    helpers.llm = llm
    llm.Tool = Tool
    llm.ToolInput = ToolInput
    llm.LLMContext = LLMContext
    storage.Store = Store
    util.json = util_json
    util_json.JsonObjectType = dict
    voluptuous.Schema = _schema
    voluptuous.Optional = _marker
    voluptuous.Required = _marker


_install_stubs()
tools = importlib.import_module("custom_components.openai_oauth_assist.tools")


class ToolTests(unittest.IsolatedAsyncioTestCase):
    """Local tool coverage."""

    async def test_memory_store_saves_and_searches(self):
        hass = SimpleNamespace()
        store = tools.MemoryStore(hass, "entry1")

        await store.async_save_memory("Lounge lamp is the cosy lamp")
        await store.async_save_memory("Kitchen blind closes at sunset")

        result = await store.async_search_memory("cosy", 5)
        self.assertEqual(len(result["memories"]), 1)
        self.assertEqual(
            result["memories"][0]["text"],
            "Lounge lamp is the cosy lamp",
        )

    async def test_memory_store_refuses_obvious_secret(self):
        hass = SimpleNamespace()
        store = tools.MemoryStore(hass, "entry1")

        result = await store.async_save_memory("My API key is abc123")

        self.assertFalse(result["saved"])
        self.assertEqual(hass._fake_store_data, {})

    async def test_home_status_filters_to_exposed_entities(self):
        class FakeStates:
            def async_all(self):
                return [
                    SimpleNamespace(
                        entity_id="light.kitchen",
                        name="Kitchen",
                        state="on",
                        attributes={},
                    ),
                    SimpleNamespace(
                        entity_id="sensor.hall_temperature",
                        name="Hall temperature",
                        state="21",
                        attributes={"device_class": "temperature"},
                    ),
                    SimpleNamespace(
                        entity_id="sensor.hidden",
                        name="Hidden",
                        state="1",
                        attributes={"device_class": "battery"},
                    ),
                ]

        original_should_expose = tools.async_should_expose
        tools.async_should_expose = (
            lambda hass, assistant, entity_id: entity_id != "sensor.hidden"
        )
        try:
            result = await tools.GetHomeStatusTool().async_call(
                SimpleNamespace(states=FakeStates()),
                sys.modules["homeassistant.helpers.llm"].ToolInput(
                    "GetHomeStatus", {}
                ),
                sys.modules["homeassistant.helpers.llm"].LLMContext(
                    "openai_oauth_assist"
                ),
            )
        finally:
            tools.async_should_expose = original_should_expose

        self.assertEqual(result["domains"], {"light": 1, "sensor": 1})
        self.assertEqual(result["active"][0]["entity_id"], "light.kitchen")
        self.assertEqual(
            result["readings"][0]["entity_id"], "sensor.hall_temperature"
        )

    def test_build_extra_tools(self):
        hass = SimpleNamespace()
        names = {
            tool.name
            for tool in tools.build_extra_tools(tools.MemoryStore(hass, "x"))
        }

        self.assertEqual(names, {"GetHomeStatus", "SaveMemory", "SearchMemory"})


if __name__ == "__main__":
    unittest.main()
