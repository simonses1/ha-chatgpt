# OpenAI OAuth Assist POC

## Summary

Home Assistant custom integration prototype for an OpenAI-backed Assist conversation agent.

This POC is ChatGPT/Codex OAuth only. It reads Codex CLI's `auth.json`, stores the parsed ChatGPT/Codex OAuth tokens in a Home Assistant config entry, and calls the ChatGPT/Codex backend.

There is no API-key fallback.

## Install

1. Copy `custom_components/openai_oauth_assist/` into your Home Assistant `custom_components/` directory.
2. Make sure Home Assistant can read a Codex `auth.json` file.
3. Restart Home Assistant.
4. Configure through either the Home Assistant UI or the REST config-flow API.
5. Select the new agent in **Settings > Voice assistants** or in the Assist pipeline settings.
6. Expose the entities, areas, scenes, and scripts you want this agent to control to Assist.

## Auth File Path

The config flow asks for the path to Codex `auth.json` as seen by the Home Assistant process.

Default:

```text
~/.codex/auth.json
```

If `CODEX_HOME` is set in Home Assistant's environment, the default becomes:

```text
$CODEX_HOME/auth.json
```

For a containerised Home Assistant install, `~` usually means the Home Assistant container user's home, not your laptop shell. If needed, place the file somewhere under the mounted config directory and pass that container-visible path, for example:

```text
/config/.codex/auth.json
```

On your current mounted config directory, that would correspond to:

```text
/Users/simon/mnt/podman/homeassistant/.codex/auth.json
```

## API-Only Test Flow

Run `codex login` somewhere trusted, then make the resulting auth file readable by Home Assistant. For example, if your HA container sees `/config`:

```bash
mkdir -p /Users/simon/mnt/podman/homeassistant/.codex
cp "$HOME/.codex/auth.json" /Users/simon/mnt/podman/homeassistant/.codex/auth.json
chmod 600 /Users/simon/mnt/podman/homeassistant/.codex/auth.json
```

Then configure and test through the HA API:

```bash
export HA_URL='http://homeassistant.example:8123'
export HA_TOKEN='long-lived-home-assistant-access-token'

pwsh -NoProfile -File scripts/test-ha-api.ps1 \
  -HaUrl "$HA_URL" \
  -HaToken "$HA_TOKEN" \
  -CodexAuthJsonPath '/config/.codex/auth.json' \
  -Configure
```

If the config entry already exists, pass either the returned config entry ID or the conversation entity ID:

```bash
pwsh -NoProfile -File scripts/test-ha-api.ps1 \
  -HaUrl "$HA_URL" \
  -HaToken "$HA_TOKEN" \
  -EntryId 'conversation.openai_assist_poc' \
  -Text 'Reply with the single word pong.'
```

To test device control, use a harmless exposed helper first, such as an `input_boolean`:

```bash
pwsh -NoProfile -File scripts/test-ha-api.ps1 \
  -HaUrl "$HA_URL" \
  -HaToken "$HA_TOKEN" \
  -EntryId 'conversation.openai_assist_poc' \
  -Text 'Turn on the test helper.'
```

The entity or helper must be exposed to Assist. Home Assistant's Assist LLM API supplies the available device-control tools and executes the selected tool call.

To test whole-home status:

```bash
pwsh -NoProfile -File scripts/test-ha-api.ps1 \
  -HaUrl "$HA_URL" \
  -HaToken "$HA_TOKEN" \
  -EntryId 'conversation.openai_assist_poc' \
  -Text 'What is the home status?'
```

To test assistant memory:

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

The raw JSON is not stored. Parsed token fields are stored in the Home Assistant config entry:

```text
chatgpt_access_token
chatgpt_refresh_token
chatgpt_id_token
chatgpt_account_id
```

Requests are sent to:

```text
https://chatgpt.com/backend-api/codex
```

The integration sends `Authorization: Bearer <access-token>` and `ChatGPT-Account-ID: <account-id>`. On expiry it refreshes through OpenAI's OAuth token endpoint using Codex's public client ID.

See [docs/auth-decision.md](docs/auth-decision.md) for the feasibility report and caveats.

## Device Control

The conversation entity declares Home Assistant control support and uses Home Assistant's LLM Assist API. For each request it asks Home Assistant for:

- the Assist system prompt
- exposed entity/device context
- allowed intent tools such as `HassTurnOn`, `HassTurnOff`, scripts, and live-state lookup

The Codex backend receives those tools as Responses API function tools. If Codex emits a tool call, Home Assistant executes it through its normal Assist intent layer, appends the tool result to the chat log, and the integration sends the result back to Codex for the final spoken response.

This integration does not expose a raw `call_service` tool.

## Home Status

The integration adds a read-only `GetHomeStatus` LLM tool. It summarises the entities exposed to Assist, including:

- exposed entity counts by domain and area
- active entities such as lights that are on, open covers, unlocked locks, detected motion, and playing media
- unavailable, unknown, jammed, or problem states
- climate entities
- common sensor readings such as temperature, humidity, battery, power, light level, CO, CO2, PM10, and PM2.5

The model may request an area or domain filter. The tool only reads Home Assistant state and only includes entities that Home Assistant exposes to this assistant.

## Memory

The integration adds two local LLM tools:

- `SaveMemory`: stores stable preferences, room aliases, routines, or instructions.
- `SearchMemory`: retrieves saved memories by simple keyword matching.

Memory is stored in Home Assistant storage as:

```text
.storage/openai_oauth_assist.<config-entry-id>.memory
```

This is deliberately simple POC storage. It is not an embeddings/vector database, and Home Assistant storage is not encrypted. The memory tool refuses obvious secret-looking text, but you should still avoid asking it to remember passwords, tokens, API keys, private keys, or other sensitive material.

## YAML

No YAML configuration is required. The integration is configured through Home Assistant config entries.

For logger troubleshooting:

```yaml
logger:
  logs:
    custom_components.openai_oauth_assist: debug
```

Do not enable broad debug logging in production if other integrations may log sensitive data.

## Limitations

- This is a proof of concept, not a supported OpenAI third-party OAuth integration.
- The OAuth path uses the Codex token audience and the ChatGPT/Codex backend, not the standard OpenAI `/v1` API.
- No OpenClaw dependency and no OpenClaw code.
- No scraped ChatGPT browser/session-token flow.
- No API-key fallback.
- Device control depends on entities being exposed to Assist.
- Home-status summaries depend on entities being exposed to Assist.
- Memory search is simple keyword matching, not semantic search.
- Conversation-history retention has no custom controls in this POC.
- Config entry storage and the local memory store are not encrypted by Home Assistant.

## Security

- Treat Codex `auth.json` as a secret. It contains bearer and refresh tokens for your ChatGPT account.
- Protect Home Assistant `.storage/` and backups.
- Diagnostics redact ChatGPT/Codex tokens.
- The integration logs API error status/type only; it does not log prompts or tokens.
- The configured auth file path must point to a file Home Assistant should be allowed to read.
- Be deliberate about which entities are exposed to Assist. Those are the devices the model can ask Home Assistant to control.
- Treat assistant memories as personal data. Protect Home Assistant `.storage/` and backups accordingly.

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
  translations/en.json

docs/
  architecture.md
  auth-decision.md

scripts/
  test-ha-api.ps1

tests/
  test_api.py
  test_tools.py
```

## Sources

- [OpenAI Codex authentication](https://developers.openai.com/codex/auth)
- [OpenAI Codex source](https://github.com/openai/codex)
- [OpenClaw OpenAI Codex auth extension](https://github.com/Kiwi1009/openclaw/tree/main/extensions/openai-codex-auth)
- [Home Assistant conversation entity docs](https://developers.home-assistant.io/docs/core/entity/conversation/)
- [Home Assistant config flow docs](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant LLM helper API](https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/llm.py)
