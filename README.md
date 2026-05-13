# OpenAI OAuth Assist POC

Home Assistant custom integration for an OpenAI-backed Assist conversation agent.

The POC uses ChatGPT/Codex OAuth only. It imports Codex CLI `auth.json`, stores parsed token fields in a Home Assistant config entry, and calls the ChatGPT/Codex backend.

No API-key fallback exists.

## Install

1. Copy `custom_components/openai_oauth_assist/` into your Home Assistant `custom_components/` directory.
2. Put a Codex `auth.json` file where Home Assistant can read it.
3. Restart Home Assistant.
4. Configure the integration through the UI or the REST config-flow API.
5. Select the agent in **Settings > Voice assistants** or in the Assist pipeline settings.
6. Expose the entities, areas, scenes, and scripts you want this agent to control.

## HACS

Add this repository as a HACS custom repository:

```text
https://github.com/simonses1/ha-chatgpt
```

Category:

```text
Integration
```

Install the integration from HACS, restart Home Assistant, then configure it from **Settings > Devices & services**.

## Auth File Path

The config flow needs the Codex `auth.json` path as Home Assistant sees it.

Default:

```text
~/.codex/auth.json
```

If the Home Assistant environment sets `CODEX_HOME`, the default becomes:

```text
$CODEX_HOME/auth.json
```

For container installs, `~` usually belongs to the Home Assistant container user. Put the file under the mounted config directory and pass a container-visible path, for example:

```text
/config/.codex/auth.json
```

## API Test Flow

Run `codex login` on a trusted machine, then make the auth file readable by Home Assistant:

```bash
mkdir -p /path/to/homeassistant/config/.codex
cp "$HOME/.codex/auth.json" /path/to/homeassistant/config/.codex/auth.json
chmod 600 /path/to/homeassistant/config/.codex/auth.json
```

Configure and test through the Home Assistant API:

```bash
export HA_URL='http://homeassistant.example:8123'
export HA_TOKEN='long-lived-home-assistant-access-token'

pwsh -NoProfile -File scripts/test-ha-api.ps1 \
  -HaUrl "$HA_URL" \
  -HaToken "$HA_TOKEN" \
  -CodexAuthJsonPath '/config/.codex/auth.json' \
  -Configure
```

For an existing config entry, pass the config entry ID or conversation entity ID:

```bash
pwsh -NoProfile -File scripts/test-ha-api.ps1 \
  -HaUrl "$HA_URL" \
  -HaToken "$HA_TOKEN" \
  -EntryId 'conversation.openai_assist_poc' \
  -Text 'Reply with the single word pong.'
```

Test device control with a harmless exposed helper first:

```bash
pwsh -NoProfile -File scripts/test-ha-api.ps1 \
  -HaUrl "$HA_URL" \
  -HaToken "$HA_TOKEN" \
  -EntryId 'conversation.openai_assist_poc' \
  -Text 'Turn on the test helper.'
```

Home Assistant must expose the target entity or helper to Assist.

Test whole-home status:

```bash
pwsh -NoProfile -File scripts/test-ha-api.ps1 \
  -HaUrl "$HA_URL" \
  -HaToken "$HA_TOKEN" \
  -EntryId 'conversation.openai_assist_poc' \
  -Text 'What is the home status?'
```

Test assistant memory:

```bash
pwsh -NoProfile -File scripts/test-ha-api.ps1 \
  -HaUrl "$HA_URL" \
  -HaToken "$HA_TOKEN" \
  -EntryId 'conversation.openai_assist_poc' \
  -Text 'Remember that the lounge lamp is the cosy lamp.'

pwsh -NoProfile -File scripts/test-ha-api.ps1 \
  -HaUrl "$HA_URL" \
  -HaToken "$HA_TOKEN" \
  -EntryId 'conversation.openai_assist_poc' \
  -Text 'What do you remember about the cosy lamp?'
```

## Authentication

The integration reads these fields from Codex CLI's auth cache:

```text
tokens.access_token
tokens.refresh_token
tokens.id_token
tokens.account_id
```

It discards the raw JSON and stores these parsed fields in the config entry:

```text
chatgpt_access_token
chatgpt_refresh_token
chatgpt_id_token
chatgpt_account_id
```

Requests go to:

```text
https://chatgpt.com/backend-api/codex
```

The client sends `Authorization: Bearer <access-token>` and `ChatGPT-Account-ID: <account-id>`. On token expiry, it refreshes through OpenAI's OAuth token endpoint with Codex's public client ID.

Read [docs/auth-decision.md](docs/auth-decision.md) for the auth findings and caveats.

## Device Control

The conversation entity declares Home Assistant control support and uses Home Assistant's LLM Assist API. For each request, Home Assistant supplies:

- the Assist system prompt
- exposed entity and device context
- allowed intent tools such as `HassTurnOn`, `HassTurnOff`, scripts, and live-state lookup

The client sends those tools to the Codex backend as Responses API function tools. When Codex emits a tool call, Home Assistant executes it through the Assist intent layer, appends the tool result to the chat log, and the client sends that result back to Codex for the final response.

The integration does not expose a raw `call_service` tool.

## Home Status

`GetHomeStatus` reads Assist-exposed entities and returns:

- entity counts by domain and area
- active entities such as lights on, open covers, unlocked locks, detected motion, and playing media
- unavailable, unknown, jammed, or problem states
- climate entities
- common sensor readings such as temperature, humidity, battery, power, light level, CO, CO2, PM10, and PM2.5

The model can request an area or domain filter. The tool only reads Home Assistant state.

## Memory

The integration adds two local LLM tools:

- `SaveMemory`: stores stable preferences, room aliases, routines, or instructions.
- `SearchMemory`: retrieves saved memories with keyword matching.

Home Assistant stores memory here:

```text
.storage/openai_oauth_assist.<config-entry-id>.memory
```

The POC uses simple local storage, not embeddings or semantic search. Home Assistant does not encrypt `.storage`. The memory tool blocks text that looks like secrets. Do not store passwords, tokens, API keys, private keys, or other sensitive material.

## YAML

The integration uses config entries. No YAML configuration is required.

For logger troubleshooting:

```yaml
logger:
  logs:
    custom_components.openai_oauth_assist: debug
```

Avoid broad debug logging in production when other integrations may log sensitive data.

## Limitations

- Proof of concept.
- ChatGPT/Codex backend only, not the standard OpenAI `/v1` API.
- No OpenClaw dependency or copied OpenClaw code.
- No scraped ChatGPT browser or session-token flow.
- No API-key fallback.
- Device control depends on Assist entity exposure.
- Home-status summaries depend on Assist entity exposure.
- Memory search uses keyword matching.
- Conversation-history retention has no custom controls.
- Home Assistant does not encrypt config entry storage or the local memory store.

## Security

- Treat Codex `auth.json` as a ChatGPT account secret.
- Protect Home Assistant `.storage/` and backups.
- Diagnostics redact ChatGPT/Codex tokens.
- API error logs include status and error type, not prompts or tokens.
- Point `codex_auth_json_path` only at files Home Assistant should read.
- Expose only the entities you want the model to control.
- Treat assistant memories as personal data.

## Files

```text
custom_components/openai_oauth_assist/
  __init__.py
  manifest.json
  const.py
  config_flow.py
  conversation.py
  tools.py
  api.py
  diagnostics.py
  brand/icon.png
  translations/en.json

docs/
  architecture.md
  auth-decision.md

scripts/
  test-ha-api.ps1

tests/
  test_api.py
  test_tools.py

hacs.json
```

## Sources

- [OpenAI Codex authentication](https://developers.openai.com/codex/auth)
- [OpenAI Codex source](https://github.com/openai/codex)
- [OpenClaw OpenAI Codex auth extension](https://github.com/Kiwi1009/openclaw/tree/main/extensions/openai-codex-auth)
- [Home Assistant conversation entity docs](https://developers.home-assistant.io/docs/core/entity/conversation/)
- [Home Assistant config flow docs](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant LLM helper API](https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/llm.py)
