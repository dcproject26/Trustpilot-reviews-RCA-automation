#!/usr/bin/env python3
"""Build the daily ORM digest and post it to the reports channel.

This is what the Replit Scheduled Deployment runs at 8pm IST:

    python tools/send_daily_report.py

It runs INSIDE the deployment container, so it already has the database and
Slack credentials from the app's environment — no HTTP call, no token, no
external URL. It is read-only against Postgres (a few group-by passes over rows
that already exist) and posts exactly one message. NO BigQuery, NO model.

Exit codes so a scheduler's run log distinguishes the outcomes rather than
showing a uniform "ran":
  0  posted
  2  built, but Slack did not accept it (reason printed; e.g. bot not in
     channel, or SLACK_BOT_TOKEN unset) — a real failure, not an empty day
  3  misconfigured: SLACK_CHANNEL_DAILY is not set
"""
import os
import sys

# Allow "python tools/send_daily_report.py" from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db import SessionLocal
from server.services.daily_report import build_daily_digest
from server.services import slack


def main() -> int:
    channel = os.getenv("SLACK_CHANNEL_DAILY", "").strip()
    if not channel:
        print("[daily-report] SLACK_CHANNEL_DAILY is not set — nothing to post. "
              "Set it to the reports channel id (e.g. C045KG5AJF5).",
              file=sys.stderr)
        return 3

    db = SessionLocal()
    try:
        text = build_daily_digest(db)
    finally:
        db.close()

    print(f"[daily-report] built digest ({len(text)} chars):\n{text}",
          file=sys.stderr)

    ts = slack.post_to_channel(channel, text)
    if ts:
        print(f"[daily-report] posted to {channel} (ts={ts})", file=sys.stderr)
        return 0

    # No ts is a fault, not an empty day — the digest was built and handed to
    # Slack. Say WHY (post_to_channel recorded it), so the run log names the fix
    # instead of reading like a quiet success.
    why = slack.last_post_failure.get("why") or "Slack returned no message ts."
    nxt = slack.last_post_failure.get("next") or ""
    print(f"[daily-report] NOT posted to {channel}: {why} {nxt}".strip(),
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
