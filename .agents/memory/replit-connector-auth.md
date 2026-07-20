---
name: Replit connector auth pattern
description: How BigQuery/Zendesk Replit connectors are wired in this project (REST token fetch, TTL cache, 401 retry)
---
Rule: prefer Replit Connectors over env-var secrets. Fetch settings via
`GET https://$REPLIT_CONNECTORS_HOSTNAME/api/v2/connection?include_secrets=true&connector_names=<name>`
with header `X_REPLIT_TOKEN: "repl "+$REPL_IDENTITY` (or `"depl "+$WEB_REPL_RENEWAL` in deployments).
Cache settings ~5 min; on 401 force a re-fetch and rebuild the client, retry once.

**Why:** Connector OAuth tokens expire (~hourly) and are refreshed upstream; a module-level client goes stale.
**How to apply:** Use lazy client getters (never module-level clients), a TTL settings cache with a lock, and a 401-recovery retry at call sites. Zendesk connector settings expose `access_token` + `subdomain` (Zenpy accepts `oauth_token=`); BigQuery exposes `access_token` + `project_id`.

Anthropic goes through Replit AI Integrations: env vars `AI_INTEGRATIONS_ANTHROPIC_BASE_URL` + `AI_INTEGRATIONS_ANTHROPIC_API_KEY` (dummy key, real base URL). Only AI-Integrations-listed models work (e.g. claude-sonnet-4-6); dated model names like claude-sonnet-4-20250514 are rejected.
