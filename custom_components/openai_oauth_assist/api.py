"""Async ChatGPT/Codex API client for the integration."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass, field
import json
import logging
import time
from typing import Any, Final, TYPE_CHECKING
from urllib.parse import urlencode, urljoin
import uuid

try:
    from aiohttp import ClientError
except ImportError:  # pragma: no cover - Home Assistant provides aiohttp.
    ClientError = OSError  # type: ignore[misc, assignment]

if TYPE_CHECKING:
    from aiohttp import ClientResponse, ClientSession
    from homeassistant.components import conversation

from .const import (
    AUTH_METHOD_CHATGPT_CODEX,
    CONF_AUTH_METHOD,
    CONF_BASE_URL,
    CONF_CHATGPT_ACCESS_TOKEN,
    CONF_CHATGPT_ACCOUNT_ID,
    CONF_CHATGPT_ID_TOKEN,
    CONF_CHATGPT_LAST_REFRESH,
    CONF_CHATGPT_REFRESH_TOKEN,
    CONF_CODEX_CLIENT_VERSION,
    CONF_CODEX_INSTALLATION_ID,
    DEFAULT_CHATGPT_CODEX_BASE_URL,
    DEFAULT_CODEX_CLIENT_VERSION,
    OPENAI_CODEX_CLIENT_ID,
)

_LOGGER = logging.getLogger(__name__)

OPENAI_TIMEOUT: Final = 60
CHATGPT_TOKEN_URL: Final = "https://auth.openai.com/oauth/token"
TOKEN_REFRESH_SKEW_SECONDS: Final = 300
FALLBACK_INSTRUCTIONS: Final = "You are a concise Home Assistant voice assistant."


class OpenAIAssistError(Exception):
    """Base exception for OpenAI Assist API errors."""


class OpenAIAuthError(OpenAIAssistError):
    """OpenAI rejected the configured credentials."""


class OpenAIRateLimitError(OpenAIAssistError):
    """OpenAI rate limited the request."""


class OpenAIResponseError(OpenAIAssistError):
    """OpenAI returned an unusable response."""


class OpenAIConnectionError(OpenAIAssistError):
    """Home Assistant could not connect to OpenAI."""


class CodexAuthJsonError(ValueError):
    """The supplied Codex auth cache is missing required token fields."""


@dataclass(slots=True)
class OpenAIResponsesClient:
    """Small aiohttp client for ChatGPT/Codex OAuth."""

    session: ClientSession
    auth_method: str = AUTH_METHOD_CHATGPT_CODEX
    base_url: str = DEFAULT_CHATGPT_CODEX_BASE_URL
    chatgpt_access_token: str | None = None
    chatgpt_refresh_token: str | None = None
    chatgpt_account_id: str | None = None
    chatgpt_id_token: str | None = None
    codex_client_version: str = DEFAULT_CODEX_CLIENT_VERSION
    codex_installation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @classmethod
    def from_config_entry_data(
        cls, session: ClientSession, data: dict[str, Any]
    ) -> OpenAIResponsesClient:
        """Build a client from config entry data."""
        return cls(
            session=session,
            auth_method=AUTH_METHOD_CHATGPT_CODEX,
            base_url=data.get(CONF_BASE_URL, DEFAULT_CHATGPT_CODEX_BASE_URL),
            chatgpt_access_token=data.get(CONF_CHATGPT_ACCESS_TOKEN),
            chatgpt_refresh_token=data.get(CONF_CHATGPT_REFRESH_TOKEN),
            chatgpt_account_id=data.get(CONF_CHATGPT_ACCOUNT_ID),
            chatgpt_id_token=data.get(CONF_CHATGPT_ID_TOKEN),
            codex_client_version=data.get(
                CONF_CODEX_CLIENT_VERSION, DEFAULT_CODEX_CLIENT_VERSION
            ),
            codex_installation_id=data.get(CONF_CODEX_INSTALLATION_ID)
            or str(uuid.uuid4()),
        )

    async def async_validate(self) -> None:
        """Validate credentials without sending user prompts."""
        await self._request_chatgpt_json(
            "GET",
            "models",
            params={"client_version": self.codex_client_version},
        )

    async def async_create_response(
        self,
        *,
        model: str,
        user_prompt: str,
        system_prompt: str | None,
    ) -> str:
        """Create a model response and return plain output text."""
        return await self._async_create_chatgpt_codex_response(
            model=model,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
        )

    async def async_stream_chat_log(
        self,
        *,
        model: str,
        chat_log: conversation.ChatLog,
    ) -> AsyncGenerator[dict[str, Any]]:
        """Stream a ChatGPT/Codex response as Home Assistant chat-log deltas."""
        instructions, input_items = codex_input_from_chat_log(chat_log.content)
        payload: dict[str, Any] = {
            "model": model,
            "instructions": instructions or FALLBACK_INSTRUCTIONS,
            "input": input_items,
            "store": False,
            "stream": True,
        }

        if chat_log.llm_api and chat_log.llm_api.tools:
            payload["tools"] = [
                format_tool_for_codex(tool, chat_log.llm_api.custom_serializer)
                for tool in chat_log.llm_api.tools
            ]

        async for delta in stream_codex_response_deltas(
            self._stream_chatgpt_events("POST", "responses", json=payload)
        ):
            yield delta

    def chatgpt_token_needs_refresh(self) -> bool:
        """Return true when the access token is near expiry."""
        expires_at = jwt_expires_at(self.chatgpt_access_token)
        if expires_at is None:
            return False
        return expires_at <= int(time.time()) + TOKEN_REFRESH_SKEW_SECONDS

    async def async_refresh_chatgpt_tokens(self) -> dict[str, Any]:
        """Refresh a Codex OAuth token and update this client in memory."""
        if self.auth_method != AUTH_METHOD_CHATGPT_CODEX:
            return {}
        if not self.chatgpt_refresh_token:
            raise OpenAIAuthError("ChatGPT/Codex OAuth refresh token is missing")

        payload = {
            "client_id": OPENAI_CODEX_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": self.chatgpt_refresh_token,
        }

        try:
            async with self.session.request(
                "POST",
                CHATGPT_TOKEN_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=OPENAI_TIMEOUT,
            ) as response:
                data = await _handle_response(response)
        except OpenAIAssistError:
            raise
        except (ClientError, TimeoutError) as err:
            raise OpenAIConnectionError("Unable to connect to OpenAI") from err

        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OpenAIResponseError("OpenAI token refresh did not return a token")

        refresh_token = data.get("refresh_token")
        id_token = data.get("id_token")

        self.chatgpt_access_token = access_token
        if isinstance(refresh_token, str) and refresh_token:
            self.chatgpt_refresh_token = refresh_token
        if isinstance(id_token, str) and id_token:
            self.chatgpt_id_token = id_token

        updates: dict[str, Any] = {
            CONF_CHATGPT_ACCESS_TOKEN: self.chatgpt_access_token,
            CONF_CHATGPT_LAST_REFRESH: int(time.time()),
        }
        if self.chatgpt_refresh_token:
            updates[CONF_CHATGPT_REFRESH_TOKEN] = self.chatgpt_refresh_token
        if self.chatgpt_id_token:
            updates[CONF_CHATGPT_ID_TOKEN] = self.chatgpt_id_token
        return updates

    async def _async_create_chatgpt_codex_response(
        self,
        *,
        model: str,
        user_prompt: str,
        system_prompt: str | None,
    ) -> str:
        """Create a response through the ChatGPT/Codex backend."""
        payload: dict[str, Any] = {
            "model": model,
            "input": [{"role": "user", "content": user_prompt}],
            "store": False,
            "stream": True,
        }
        if system_prompt:
            payload["instructions"] = system_prompt

        return await self._request_chatgpt_stream("POST", "responses", json=payload)

    async def _request_chatgpt_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Issue a JSON request to the ChatGPT/Codex backend."""
        url = _join_url(self.base_url, path)
        if params:
            url = f"{url}?{urlencode(params)}"

        try:
            async with self.session.request(
                method,
                url,
                headers=self._chatgpt_headers(event_stream=False),
                timeout=OPENAI_TIMEOUT,
                **kwargs,
            ) as response:
                return await _handle_response(response)
        except OpenAIAssistError:
            raise
        except (ClientError, TimeoutError) as err:
            raise OpenAIConnectionError("Unable to connect to OpenAI") from err

    async def _request_chatgpt_stream(
        self, method: str, path: str, **kwargs: Any
    ) -> str:
        """Issue a streaming request to the ChatGPT/Codex backend."""
        url = _join_url(self.base_url, path)
        try:
            async with self.session.request(
                method,
                url,
                headers=self._chatgpt_headers(event_stream=True),
                timeout=OPENAI_TIMEOUT,
                **kwargs,
            ) as response:
                if not 200 <= response.status < 300:
                    await _handle_response(response)
                return await extract_sse_response_text(response)
        except OpenAIAssistError:
            raise
        except (ClientError, TimeoutError) as err:
            raise OpenAIConnectionError("Unable to connect to OpenAI") from err

    async def _stream_chatgpt_events(
        self, method: str, path: str, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any]]:
        """Issue a streaming request and yield decoded Codex response events."""
        url = _join_url(self.base_url, path)
        try:
            async with self.session.request(
                method,
                url,
                headers=self._chatgpt_headers(event_stream=True),
                timeout=OPENAI_TIMEOUT,
                **kwargs,
            ) as response:
                if not 200 <= response.status < 300:
                    await _handle_response(response)

                async for event in iter_sse_events(response):
                    raw_data = event.get("data", "")
                    if raw_data == "[DONE]":
                        break
                    if not raw_data:
                        continue
                    try:
                        data = json.loads(raw_data)
                    except json.JSONDecodeError as err:
                        raise OpenAIResponseError(
                            "OpenAI stream returned invalid JSON"
                        ) from err
                    if not isinstance(data, dict):
                        raise OpenAIResponseError(
                            "OpenAI stream returned a non-object event"
                        )
                    yield data
        except OpenAIAssistError:
            raise
        except (ClientError, TimeoutError) as err:
            raise OpenAIConnectionError("Unable to connect to OpenAI") from err

    def _chatgpt_headers(self, *, event_stream: bool) -> dict[str, str]:
        """Return safe ChatGPT/Codex request headers."""
        if not self.chatgpt_access_token or not self.chatgpt_account_id:
            raise OpenAIAuthError("ChatGPT/Codex OAuth credentials are missing")

        headers = {
            "Accept": "text/event-stream" if event_stream else "application/json",
            "Authorization": f"Bearer {self.chatgpt_access_token}",
            "ChatGPT-Account-ID": self.chatgpt_account_id,
            "Content-Type": "application/json",
            "User-Agent": f"codex-cli/{self.codex_client_version}",
            "x-codex-installation-id": self.codex_installation_id,
            "x-codex-window-id": f"{uuid.uuid4()}:0",
        }
        return headers


async def _handle_response(response: ClientResponse) -> dict[str, Any]:
    """Parse a response body and raise safe exceptions for failures."""
    status = response.status
    content_type = response.headers.get("Content-Type", "")

    if "application/json" in content_type:
        data = await response.json()
    else:
        body = await response.text()
        data = {"error": {"message": body[:200]}}

    if 200 <= status < 300:
        if not isinstance(data, dict):
            raise OpenAIResponseError("OpenAI returned a non-object JSON body")
        return data

    error = data.get("error") if isinstance(data, dict) else None
    error_type = error.get("type") if isinstance(error, dict) else "unknown"
    _LOGGER.warning(
        "OpenAI request failed with status %s and error type %s",
        status,
        error_type,
    )

    if status in (401, 403):
        raise OpenAIAuthError("OpenAI rejected the configured credentials")
    if status == 429:
        raise OpenAIRateLimitError("OpenAI rate limit exceeded")
    raise OpenAIResponseError(f"OpenAI request failed with HTTP {status}")


async def extract_sse_response_text(response: ClientResponse) -> str:
    """Extract text from a ChatGPT/Codex streamed Responses API body."""
    deltas: list[str] = []
    done_text: str | None = None

    async for event in iter_sse_events(response):
        raw_data = event.get("data", "")
        if raw_data == "[DONE]":
            break
        if not raw_data:
            continue

        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError as err:
            raise OpenAIResponseError("OpenAI stream returned invalid JSON") from err

        event_type = data.get("type") or event.get("event")
        if event_type == "response.output_text.delta":
            delta = data.get("delta")
            if isinstance(delta, str):
                deltas.append(delta)
        elif event_type == "response.output_text.done":
            text = data.get("text")
            if isinstance(text, str):
                done_text = text
        elif event_type in {"response.failed", "error"} or isinstance(
            data.get("error"), dict
        ):
            _LOGGER.warning("OpenAI stream failed with event type %s", event_type)
            raise OpenAIResponseError("OpenAI stream returned an error")

    text = "".join(deltas).strip()
    if text:
        return text
    if done_text and done_text.strip():
        return done_text.strip()
    raise OpenAIResponseError("OpenAI stream did not contain output text")


async def stream_codex_response_deltas(
    events: AsyncIterable[dict[str, Any]],
) -> AsyncGenerator[dict[str, Any]]:
    """Transform Codex Responses stream events to Home Assistant chat-log deltas."""
    from homeassistant.helpers import llm  # noqa: PLC0415

    emitted_text = False
    function_calls: dict[str, dict[str, Any]] = {}

    async for data in events:
        event_type = data.get("type")
        if event_type == "response.output_text.delta":
            delta = data.get("delta")
            if isinstance(delta, str) and delta:
                emitted_text = True
                yield {"content": delta}
            continue

        if event_type == "response.output_text.done":
            text = data.get("text")
            if not emitted_text and isinstance(text, str) and text:
                emitted_text = True
                yield {"content": text}
            continue

        if event_type == "response.output_item.added":
            item = data.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                item_id = item.get("id")
                if isinstance(item_id, str):
                    function_calls[item_id] = {
                        "name": item.get("name"),
                        "call_id": item.get("call_id"),
                        "arguments": item.get("arguments") or "",
                    }
            continue

        if event_type == "response.function_call_arguments.delta":
            item_id = data.get("item_id")
            delta = data.get("delta")
            if isinstance(item_id, str) and isinstance(delta, str):
                function_calls.setdefault(item_id, {})["arguments"] = (
                    function_calls.get(item_id, {}).get("arguments", "") + delta
                )
            continue

        if event_type == "response.function_call_arguments.done":
            item_id = data.get("item_id")
            arguments = data.get("arguments")
            if isinstance(item_id, str) and isinstance(arguments, str):
                function_calls.setdefault(item_id, {})["arguments"] = arguments
            continue

        if event_type == "response.output_item.done":
            item = data.get("item")
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue

            item_id = item.get("id")
            call_data = function_calls.get(item_id, {}) if isinstance(item_id, str) else {}
            tool_name = item.get("name") or call_data.get("name")
            call_id = item.get("call_id") or call_data.get("call_id")
            arguments = item.get("arguments") or call_data.get("arguments") or "{}"

            if not isinstance(tool_name, str) or not isinstance(call_id, str):
                raise OpenAIResponseError("OpenAI stream returned an invalid tool call")
            if not isinstance(arguments, str):
                raise OpenAIResponseError(
                    "OpenAI stream returned invalid tool arguments"
                )
            try:
                tool_args = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError as err:
                raise OpenAIResponseError(
                    "OpenAI stream returned malformed tool arguments"
                ) from err
            if not isinstance(tool_args, dict):
                raise OpenAIResponseError(
                    "OpenAI stream returned non-object tool arguments"
                )

            yield {
                "tool_calls": [
                    llm.ToolInput(
                        id=call_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )
                ]
            }
            continue

        if event_type in {"response.failed", "error"} or isinstance(
            data.get("error"), dict
        ):
            _LOGGER.warning("OpenAI stream failed with event type %s", event_type)
            raise OpenAIResponseError("OpenAI stream returned an error")


def codex_input_from_chat_log(
    chat_content: list[conversation.Content],
) -> tuple[str, list[dict[str, Any]]]:
    """Convert Home Assistant chat-log content to Codex Responses input."""
    instructions = ""
    input_items: list[dict[str, Any]] = []

    for content in chat_content:
        role = getattr(content, "role", None)
        text = getattr(content, "content", None)

        if role == "system":
            if isinstance(text, str):
                instructions = text
            continue

        if role in {"user", "assistant"}:
            if isinstance(text, str) and text:
                input_items.append(
                    {
                        "type": "message",
                        "role": role,
                        "content": text,
                    }
                )

            if role == "assistant" and getattr(content, "tool_calls", None):
                for tool_call in content.tool_calls:
                    if getattr(tool_call, "external", False):
                        continue
                    input_items.append(
                        {
                            "type": "function_call",
                            "name": tool_call.tool_name,
                            "call_id": tool_call.id,
                            "arguments": json.dumps(tool_call.tool_args),
                        }
                    )
            continue

        if role == "tool_result":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": content.tool_call_id,
                    "output": json.dumps(content.tool_result),
                }
            )

    return instructions, input_items


def format_tool_for_codex(tool: Any, custom_serializer: Any | None) -> dict[str, Any]:
    """Convert a Home Assistant LLM tool to a Codex Responses function tool."""
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool_parameters_to_json_schema(
            tool.parameters, custom_serializer
        ),
        "strict": False,
    }


def tool_parameters_to_json_schema(
    parameters: Any, custom_serializer: Any | None
) -> dict[str, Any]:
    """Convert a voluptuous tool schema to an OpenAI-compatible JSON schema."""
    if isinstance(parameters, dict):
        schema = dict(parameters)
    else:
        try:
            from voluptuous_openapi import convert  # noqa: PLC0415
        except ImportError as err:
            raise OpenAIResponseError(
                "Home Assistant cannot convert LLM tool parameters"
            ) from err
        schema = convert(parameters, custom_serializer=custom_serializer)

    if not isinstance(schema, dict):
        raise OpenAIResponseError("Home Assistant returned an invalid tool schema")
    schema.setdefault("type", "object")
    normalise_tool_schema(schema)
    return schema


def normalise_tool_schema(schema: dict[str, Any]) -> None:
    """Remove schema constructs that Codex function tools do not accept reliably."""
    for key in ("oneOf", "anyOf", "allOf", "not"):
        schema.pop(key, None)

    schema_type = schema.get("type")
    if schema_type == "object":
        schema.setdefault("properties", {})
        schema.setdefault("additionalProperties", False)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for prop_schema in properties.values():
                if isinstance(prop_schema, dict):
                    normalise_tool_schema(prop_schema)
    elif schema_type == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            normalise_tool_schema(items)


async def iter_sse_events(response: ClientResponse):
    """Yield parsed Server-Sent Events from an aiohttp response."""
    buffer = ""
    event_type: str | None = None
    data_lines: list[str] = []

    async def emit_event() -> dict[str, str] | None:
        nonlocal event_type, data_lines
        if not data_lines:
            event_type = None
            return None
        event = {"event": event_type or "", "data": "\n".join(data_lines)}
        event_type = None
        data_lines = []
        return event

    async for chunk in response.content.iter_chunked(4096):
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.rstrip("\r")
            if line == "":
                event = await emit_event()
                if event is not None:
                    yield event
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

    if buffer:
        line = buffer.rstrip("\r")
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    event = await emit_event()
    if event is not None:
        yield event


def parse_codex_auth_json(raw_json: str) -> dict[str, Any]:
    """Extract ChatGPT/Codex OAuth tokens from Codex CLI auth.json content."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as err:
        raise CodexAuthJsonError("Codex auth JSON is not valid JSON") from err

    if not isinstance(data, dict):
        raise CodexAuthJsonError("Codex auth JSON must contain an object")

    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        raise CodexAuthJsonError("Codex auth JSON does not contain tokens")

    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not isinstance(access_token, str) or not access_token:
        raise CodexAuthJsonError("Codex auth JSON is missing access_token")
    if not isinstance(account_id, str) or not account_id:
        raise CodexAuthJsonError("Codex auth JSON is missing account_id")

    parsed: dict[str, Any] = {
        CONF_AUTH_METHOD: AUTH_METHOD_CHATGPT_CODEX,
        CONF_CHATGPT_ACCESS_TOKEN: access_token,
        CONF_CHATGPT_ACCOUNT_ID: account_id,
    }

    refresh_token = tokens.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token:
        parsed[CONF_CHATGPT_REFRESH_TOKEN] = refresh_token

    id_token = tokens.get("id_token")
    if isinstance(id_token, str) and id_token:
        parsed[CONF_CHATGPT_ID_TOKEN] = id_token

    last_refresh = data.get("last_refresh")
    if isinstance(last_refresh, int):
        parsed[CONF_CHATGPT_LAST_REFRESH] = last_refresh

    return parsed


def jwt_expires_at(token: str | None) -> int | None:
    """Return a JWT exp claim without verifying the token signature."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) < 2:
        return None

    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return None

    exp = data.get("exp")
    if isinstance(exp, int):
        return exp
    return None


def _join_url(base_url: str, path: str) -> str:
    """Join a base URL and API path without losing path prefixes."""
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def extract_response_text(data: dict[str, Any]) -> str:
    """Extract plain text from a Responses API response body."""
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    parts: list[str] = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if not isinstance(content_item, dict):
                    continue
                text = content_item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)

    text = "".join(parts).strip()
    if text:
        return text

    raise OpenAIResponseError("OpenAI response did not contain output text")
