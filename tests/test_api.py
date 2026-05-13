"""Dependency-light tests for the OpenAI API helper module."""

from __future__ import annotations

import importlib
import json
import sys
import types
import unittest
from dataclasses import dataclass
from types import SimpleNamespace


def _install_homeassistant_stubs() -> None:
    """Install enough Home Assistant stubs to import the custom component."""
    if "homeassistant" in sys.modules:
        return

    homeassistant = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    const = types.ModuleType("homeassistant.const")
    core = types.ModuleType("homeassistant.core")
    helpers = types.ModuleType("homeassistant.helpers")
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    llm = types.ModuleType("homeassistant.helpers.llm")

    class ConfigEntry:
        @classmethod
        def __class_getitem__(cls, item):
            return cls

    class Platform:
        CONVERSATION = "conversation"

    class HomeAssistant:
        pass

    def async_get_clientsession(hass):
        return None

    @dataclass
    class ToolInput:
        tool_name: str
        tool_args: dict
        id: str
        external: bool = False

    config_entries.ConfigEntry = ConfigEntry
    const.Platform = Platform
    core.HomeAssistant = HomeAssistant
    aiohttp_client.async_get_clientsession = async_get_clientsession
    llm.ToolInput = ToolInput

    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client
    sys.modules["homeassistant.helpers.llm"] = llm


_install_homeassistant_stubs()
api = importlib.import_module("custom_components.openai_oauth_assist.api")


class FakeResponse:
    """Small fake aiohttp response object."""

    def __init__(self, status, data, content_type="application/json"):
        self.status = status
        self._data = data
        self.headers = {"Content-Type": content_type}
        self.content = FakeContent(data if isinstance(data, bytes) else b"")

    async def json(self):
        return self._data

    async def text(self):
        return str(self._data)


class FakeContent:
    """Small fake aiohttp stream object."""

    def __init__(self, body):
        self._body = body

    async def iter_chunked(self, size):
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]


class ApiHelperTests(unittest.IsolatedAsyncioTestCase):
    """Test API helper behaviour."""

    def test_extracts_output_text_shortcut(self):
        self.assertEqual(
            api.extract_response_text({"output_text": "hello"}),
            "hello",
        )

    def test_extracts_nested_response_text(self):
        data = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "hello "},
                        {"type": "output_text", "text": "world"},
                    ],
                }
            ]
        }
        self.assertEqual(api.extract_response_text(data), "hello world")

    def test_rejects_empty_response_text(self):
        with self.assertRaises(api.OpenAIResponseError):
            api.extract_response_text({"output": []})

    def test_parses_codex_auth_json(self):
        data = api.parse_codex_auth_json(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "last_refresh": 123,
                    "tokens": {
                        "access_token": "access",
                        "refresh_token": "refresh",
                        "id_token": "id",
                        "account_id": "account",
                    },
                }
            )
        )

        self.assertEqual(data["auth_method"], "chatgpt_codex")
        self.assertEqual(data["chatgpt_access_token"], "access")
        self.assertEqual(data["chatgpt_refresh_token"], "refresh")
        self.assertEqual(data["chatgpt_account_id"], "account")

    def test_rejects_codex_auth_json_without_tokens(self):
        with self.assertRaises(api.CodexAuthJsonError):
            api.parse_codex_auth_json("{}")

    def test_reads_jwt_exp_claim(self):
        token = "x.eyJleHAiOjEyM30.y"
        self.assertEqual(api.jwt_expires_at(token), 123)

    def test_converts_chat_log_to_codex_input(self):
        tool_call = SimpleNamespace(
            id="call_1",
            tool_name="HassTurnOn",
            tool_args={"name": "Kitchen", "domain": "light"},
            external=False,
        )
        content = [
            SimpleNamespace(role="system", content="system prompt"),
            SimpleNamespace(role="user", content="Turn on Kitchen"),
            SimpleNamespace(
                role="assistant",
                content=None,
                tool_calls=[tool_call],
            ),
            SimpleNamespace(
                role="tool_result",
                tool_call_id="call_1",
                tool_name="HassTurnOn",
                tool_result={"done": True},
            ),
        ]

        instructions, input_items = api.codex_input_from_chat_log(content)

        self.assertEqual(instructions, "system prompt")
        self.assertEqual(input_items[0]["role"], "user")
        self.assertEqual(input_items[1]["type"], "function_call")
        self.assertEqual(input_items[1]["call_id"], "call_1")
        self.assertEqual(input_items[2]["type"], "function_call_output")

    def test_formats_dict_schema_tool_for_codex(self):
        tool = SimpleNamespace(
            name="HassTurnOn",
            description="Turn on a thing",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        )

        result = api.format_tool_for_codex(tool, None)

        self.assertEqual(result["type"], "function")
        self.assertEqual(result["name"], "HassTurnOn")
        self.assertFalse(result["strict"])
        self.assertFalse(result["parameters"]["additionalProperties"])

    def test_join_url_preserves_codex_prefix(self):
        self.assertEqual(
            api._join_url("https://chatgpt.com/backend-api/codex", "responses"),
            "https://chatgpt.com/backend-api/codex/responses",
        )

    async def test_handle_response_raises_auth_error(self):
        response = FakeResponse(
            401,
            {"error": {"type": "invalid_auth", "message": "secret detail"}},
        )
        with self.assertRaises(api.OpenAIAuthError):
            await api._handle_response(response)

    async def test_handle_response_raises_rate_limit_error(self):
        response = FakeResponse(429, {"error": {"type": "rate_limit_exceeded"}})
        with self.assertRaises(api.OpenAIRateLimitError):
            await api._handle_response(response)

    async def test_extracts_streamed_response_text(self):
        body = b"".join(
            [
                b'data: {"type":"response.output_text.delta","delta":"po"}\n\n',
                b'data: {"type":"response.output_text.delta","delta":"ng"}\n\n',
                b'data: {"type":"response.completed"}\n\n',
            ]
        )
        response = FakeResponse(200, body, "text/event-stream")

        self.assertEqual(await api.extract_sse_response_text(response), "pong")

    async def test_extracts_streamed_done_text_without_deltas(self):
        body = b'data: {"type":"response.output_text.done","text":"pong"}\n\n'
        response = FakeResponse(200, body, "text/event-stream")

        self.assertEqual(await api.extract_sse_response_text(response), "pong")

    async def test_streams_function_tool_call_delta(self):
        async def events():
            for event in [
                {
                    "type": "response.output_item.added",
                    "item": {
                        "id": "fc_1",
                        "type": "function_call",
                        "name": "HassTurnOn",
                        "call_id": "call_1",
                        "arguments": "",
                    },
                },
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": "fc_1",
                    "arguments": '{"name":"Kitchen","domain":"light"}',
                },
                {
                    "type": "response.output_item.done",
                    "item": {
                        "id": "fc_1",
                        "type": "function_call",
                        "name": "HassTurnOn",
                        "call_id": "call_1",
                        "arguments": '{"name":"Kitchen","domain":"light"}',
                    },
                },
            ]:
                yield event

        deltas = [delta async for delta in api.stream_codex_response_deltas(events())]

        self.assertEqual(len(deltas), 1)
        tool_input = deltas[0]["tool_calls"][0]
        self.assertEqual(tool_input.id, "call_1")
        self.assertEqual(tool_input.tool_name, "HassTurnOn")
        self.assertEqual(
            tool_input.tool_args,
            {"name": "Kitchen", "domain": "light"},
        )


if __name__ == "__main__":
    unittest.main()
