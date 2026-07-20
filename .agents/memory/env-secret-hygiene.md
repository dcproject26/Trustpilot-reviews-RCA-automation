---
name: Env secret hygiene
description: Trailing whitespace in pasted Replit Secrets silently breaks equality comparisons
---
Rule: always `.strip()` env-var values read in config, especially IDs and channel names used in equality checks.

**Why:** `SLACK_CHANNEL_ORM` was pasted with trailing spaces, so the webhook channel filter (`event["channel"] not in ORM_CHANNELS`) silently dropped every valid event — no error, just missing rows.
**How to apply:** Strip at the single point of ingestion (config module), not at each use site. When an env-driven filter mysteriously rejects everything, print the repr of the config value first.
