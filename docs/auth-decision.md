# Auth Decision Record

## Summary

This POC is ChatGPT/Codex OAuth only.

It reads Codex CLI `auth.json`, stores the parsed ChatGPT/Codex OAuth tokens in the Home Assistant config entry, and calls `https://chatgpt.com/backend-api/codex`.

There is no OpenAI API-key fallback in this integration.

## Decision

Implement ChatGPT/Codex OAuth import for the POC, while documenting that this is not a public third-party OpenAI API OAuth flow.

Do not copy or depend on OpenClaw. OpenClaw was inspected only to understand its approach: it imports Codex CLI auth data rather than implementing a separate OAuth flow.

## Research Findings

| Topic | Finding | Impact |
|---|---|---|
| OpenAI API authentication | The OpenAI API reference documents API-key bearer authentication for `/v1` API calls. | This POC intentionally does not use the standard `/v1` API path. |
| ChatGPT/Codex OAuth | OpenAI documents ChatGPT sign-in for Codex clients. The Codex source stores access, refresh, ID token, and account ID in `auth.json`. | OAuth tokens can be obtained without scraping browser sessions. |
| Codex backend | Codex source selects `https://chatgpt.com/backend-api/codex` when using ChatGPT auth, and sends bearer token plus `ChatGPT-Account-ID`. | The OAuth token works for the Codex backend, not as a general `/v1` API token. |
| Token refresh | Codex source refreshes via `https://auth.openai.com/oauth/token` with public client ID `app_EMoamEEZ73f0CkXaXp7hrann`. | The POC can refresh expired access tokens if a refresh token is present. |
| OpenClaw | OpenClaw's Codex auth extension imports Codex CLI auth cache fields. | It supports the feasibility claim, but no OpenClaw code is used here. |
| ChatGPT subscription vs API platform | ChatGPT and the API platform are separate products. Codex can use ChatGPT subscription access, while standard API-key usage is API-platform billing. | A ChatGPT OAuth token should not be treated as a normal OpenAI API key. |

## Implemented OAuth Model

1. User runs `codex login` on a trusted machine.
2. User makes Codex CLI `auth.json` readable by Home Assistant.
3. User configures the integration with the Home Assistant-visible auth file path.
4. `config_flow.py` validates that the path is readable, parses the JSON, and validates the token by calling the Codex models endpoint.
5. Config entry data stores:

```text
auth_method = chatgpt_codex
codex_auth_json_path
chatgpt_access_token
chatgpt_refresh_token
chatgpt_id_token
chatgpt_account_id
codex_client_version
codex_installation_id
base_url = https://chatgpt.com/backend-api/codex
```

6. `conversation.py` sends Assist prompts to `POST /responses` on the Codex backend with `stream: true`.
7. If the access token is close to expiry, or a request returns auth failure, the integration refreshes the token and persists the replacement token fields.

## Why This Is Still a POC

OpenAI has not published a general third-party OAuth application-registration flow for Home Assistant integrations whose resulting token is accepted by the standard OpenAI `/v1` APIs.

The implemented path proves that ChatGPT-account OAuth authentication can back Home Assistant Assist responses via the Codex backend. It does not prove official support for arbitrary third-party apps or for the standard OpenAI API.

## Rejected Alternatives

- **API-key fallback**: removed at the user's request. This repo is now OAuth-only for the POC.
- **Copying OpenClaw**: rejected. This repo does not copy or depend on OpenClaw code.
- **Scraped ChatGPT browser/session tokens**: rejected. The POC uses Codex's OAuth token cache, not browser scraping.
- **Pretending ChatGPT OAuth is an OpenAI `/v1` token**: rejected. Testing and Codex source show the working endpoint is the Codex backend.

## Security Notes

- Codex `auth.json` contains secrets for the user's ChatGPT account.
- Home Assistant config entry storage is not encrypted.
- Backups of `.storage/` must be protected.
- Diagnostics redact ChatGPT/Codex token fields.
- Prompts and token bodies are not logged.
- The configured auth file path must point to a file Home Assistant should be allowed to read.

## Sources

- [OpenAI Codex authentication](https://developers.openai.com/codex/auth)
- [OpenAI Codex source](https://github.com/openai/codex)
- [OpenClaw OpenAI Codex auth extension](https://github.com/Kiwi1009/openclaw/tree/main/extensions/openai-codex-auth)
- [Home Assistant config flow docs](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant conversation entity docs](https://developers.home-assistant.io/docs/core/entity/conversation/)
