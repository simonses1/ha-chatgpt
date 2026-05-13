# Architecture

## Summary

`openai_oauth_assist` is a Home Assistant custom conversation integration backed by ChatGPT/Codex OAuth.

It does not include an API-key fallback.

## Components

```text
Home Assistant config flow/API
  |
  | path to Codex auth.json
  v
Config entry storage
  |  parsed OAuth tokens
  |  model, prompt, Codex client metadata
  v
custom_components/openai_oauth_assist
  |
  | registers ConversationEntity
  v
Assist / conversation pipeline
  |
  | user prompt, Assist tools, status/memory tools
  v
OpenAIResponsesClient
  |
  | streamed HTTPS POST /responses
  | function tools for Assist intents, status, and memory
  v
chatgpt.com/backend-api/codex

Integration-local tool execution
  |
  | read-only status, memory save/search
  v
Home Assistant state registry / .storage
```

## Data Flow

1. The user runs `codex login` on a trusted machine.
2. The user makes Codex CLI `auth.json` readable by Home Assistant.
3. The user enters the Home Assistant-visible auth file path in the config flow.
4. `config_flow.py` reads the file without blocking the event loop, extracts token fields, drops the raw JSON, and validates with the Codex models endpoint.
5. `__init__.py` creates `OpenAIResponsesClient` from the config entry.
6. `conversation.py` registers an `OpenAIAssistConversationEntity`.
7. For each Assist request, `conversation.py` asks Home Assistant's LLM Assist API for the system prompt, exposed entity context, and allowed tools.
8. `conversation.py` appends integration-local tools for home status and assistant memory.
9. `api.py` sends the chat log and available tools to the Codex backend.
10. If Codex returns a function tool call, `chat_log.async_add_delta_content_stream` lets Home Assistant execute either the Assist intent tool or the integration-local tool and append the tool result.
11. The integration sends the tool result back to Codex until Codex returns final assistant text or the tool-iteration limit is hit.
12. Near-expiry or rejected access tokens are refreshed with the stored refresh token.

## Extra LLM Tools

| Tool | Scope | Behaviour |
|---|---|---|
| `GetHomeStatus` | Read-only Home Assistant state | Summarises exposed entities by domain and area, active states, problem states, climate entities, and common sensor readings. |
| `SaveMemory` | Local Home Assistant storage | Saves short stable preferences, aliases, routines, or instructions for this config entry. Obvious secret-looking text is rejected. |
| `SearchMemory` | Local Home Assistant storage | Searches saved memories with simple keyword matching and returns the most relevant recent items. |

Memory is stored through Home Assistant's storage helper as:

```text
.storage/openai_oauth_assist.<config-entry-id>.memory
```

## Files

| File | Responsibility |
|---|---|
| `__init__.py` | Config entry setup, unload, reload, runtime client creation. |
| `api.py` | Async aiohttp Codex client, OAuth refresh, SSE parsing, Assist tool formatting, Codex-to-chat-log delta conversion. |
| `config_flow.py` | UI/API setup, auth-file readability validation, token parsing, re-auth flow, options flow. |
| `conversation.py` | Home Assistant conversation agent entity, LLM Assist API setup, tool loop, token-refresh retry. |
| `tools.py` | Integration-local LLM tools for home status and assistant memory. |
| `diagnostics.py` | Redacted diagnostics. |
| `translations/en.json` | UI strings. |
| `scripts/test-ha-api.ps1` | API-only config and conversation smoke test. |

## Failure Modes

| Failure | Behaviour |
|---|---|
| Auth file missing or unreadable | Config flow blocks setup with a readable error. |
| Auth file is invalid JSON or missing token fields | Config flow blocks setup with a token-cache error. |
| Expired ChatGPT access token with refresh token | Integration refreshes and retries once. |
| Expired ChatGPT access token without refresh token | Conversation returns a safe error and starts re-auth. |
| Codex repeatedly calls tools without final text | Conversation stops after 10 tool iterations and returns a safe error. |
| Tool call targets an unexposed or ambiguous entity | Home Assistant returns an intent/tool error to Codex, which can ask for clarification or report failure. |
| Home-status request has no exposed entities | The status tool returns an empty exposed-entity summary. |
| Memory request contains obvious secret wording | The memory tool refuses to save the item. |
| Rate limit | Conversation returns a safe retry-later error. |
| Network failure | Conversation returns a connectivity error. |
| Malformed JSON or SSE stream | Conversation returns a generic OpenAI error and logs status/type only. |

## Security Notes

- Prompts and tokens are not logged by the integration.
- Diagnostics redact ChatGPT/Codex token fields.
- Config entry storage is not encrypted; protect `.storage/` and Home Assistant backups.
- Codex `auth.json` should only live on storage trusted by the Home Assistant process.
- The configured auth file path must point to a file Home Assistant should be allowed to read.
- Device control is limited by Home Assistant's Assist exposure settings and intent/tool layer.
- The integration does not expose raw service-call access.
- Assistant memories may contain personal data. They are stored in Home Assistant `.storage/`, which is not encrypted by Home Assistant.
- The status tool only exposes entities that Home Assistant has made visible to Assist.

## Scaling and Performance

- The integration uses Home Assistant's aiohttp client and does not perform blocking network I/O.
- The initial auth file read is performed through Home Assistant's executor helper.
- Each Assist request performs one HTTPS request, plus occasional token refresh.
- Device-control requests may perform multiple HTTPS requests: one to choose a tool, one or more Home Assistant tool executions, then another Codex request for the final response.
- Home-status tool calls scan exposed Home Assistant states in memory and do not perform network I/O.
- Memory search is in-process keyword matching over at most 500 short records.
- ChatGPT/Codex responses are streamed from the backend, and tool calls are executed as soon as the stream yields them.
- Conversation history is replayed through Home Assistant's chat log so tool results can be returned to Codex.

## Future Work

- Replace auth-cache import with an explicit OAuth/device flow if we decide to stop depending on Codex CLI's auth cache.
- Add conversation-history support with configurable retention.
- Replace keyword memory with semantic retrieval if memory volume or ambiguity grows.
