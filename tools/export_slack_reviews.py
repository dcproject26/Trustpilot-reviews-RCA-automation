#!/usr/bin/env python3
"""Export Trustpilot reviews + their RCA thread replies straight from the Slack
channel to a CSV — WITHOUT touching the dashboard database.

It reads the same channel the bot ingests from (SLACK_CHANNEL_ORM) with the same
token (SLACK_BOT_TOKEN), pages back N months of history, and for every review
message pulls its thread replies (where the RCA Assistant posts the RCA). Pure
read: it never writes to Postgres and never calls the pipeline.

    python tools/export_slack_reviews.py --months 6
    python tools/export_slack_reviews.py --months 6 --out reviews_6mo.csv

Requires SLACK_BOT_TOKEN (xoxb-, with channels:history/groups:history +
conversations.replies access) and SLACK_CHANNEL_ORM — both already set in the
Repl. If the channel is private, the bot must be a member of it.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def _client():
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError  # noqa: F401  (imported for callers)
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not token:
        sys.exit("SLACK_BOT_TOKEN is not set — nothing to read Slack with.")
    return WebClient(token=token)


def _call(fn, **kw):
    """One Slack call with simple 429 back-off, so a 6-month pull that trips the
    rate limit waits and retries instead of dying half-way."""
    from slack_sdk.errors import SlackApiError
    for attempt in range(8):
        try:
            return fn(**kw)
        except SlackApiError as e:
            if e.response is not None and e.response.status_code == 429:
                wait = int(e.response.headers.get("Retry-After", "5"))
                time.sleep(wait + 1)
                continue
            raise
    raise RuntimeError("Slack kept rate-limiting after 8 retries")


def _rca_from_replies(client, channel, ts):
    """Join the thread replies for one review into the RCA text. The review
    message itself is the first item in a thread and is excluded; everything
    else in the thread is what the bot (and people) posted under it."""
    out, cursor = [], None
    for _ in range(20):                       # up to 20 pages of replies
        res = _call(client.conversations_replies, channel=channel, ts=ts,
                    limit=200, **({"cursor": cursor} if cursor else {}))
        for m in res.get("messages", []):
            if m.get("ts") == ts:             # the review post, not a reply
                continue
            txt = (m.get("text") or "").strip()
            # pull attachment text too — the RCA is sometimes an attachment
            for a in m.get("attachments", []) or []:
                for k in ("pretext", "title", "text", "fallback"):
                    if a.get(k):
                        txt += ("\n" if txt else "") + str(a[k])
            if txt:
                out.append(txt)
        cursor = (res.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    return "\n\n---\n\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=float, default=6,
                    help="how far back to go (default 6)")
    ap.add_argument("--out", default="rca_slack_export.csv")
    ap.add_argument("--channel", default=os.getenv("SLACK_CHANNEL_ORM", "").strip(),
                    help="channel id (defaults to SLACK_CHANNEL_ORM)")
    a = ap.parse_args()
    if not a.channel:
        sys.exit("No channel — set SLACK_CHANNEL_ORM or pass --channel C0XXXX.")

    # Reuse the tool's OWN review detection + parsing, so this export sees a
    # review exactly the way the dashboard does.
    from server.services.slack import is_trustpilot_message, parse_review

    client = _client()
    oldest = time.time() - a.months * 30.44 * 24 * 3600

    # 1. page the channel history back to `oldest`
    msgs, cursor, pages = [], None, 0
    while True:
        pages += 1
        res = _call(client.conversations_history, channel=a.channel,
                    oldest=str(oldest), limit=200,
                    **({"cursor": cursor} if cursor else {}))
        msgs.extend(res.get("messages", []))
        cursor = (res.get("response_metadata") or {}).get("next_cursor") or None
        print(f"  history page {pages}: {len(msgs)} messages so far", flush=True)
        if not cursor:
            break

    # 2. keep the reviews, pull each one's RCA thread
    rows, n_rev = [], 0
    for m in msgs:
        ev = {**m, "channel": a.channel}
        if not is_trustpilot_message(ev):
            continue
        n_rev += 1
        p = parse_review(ev)
        ts = m.get("ts", "")
        when = datetime.fromtimestamp(float(ts), IST).strftime("%Y-%m-%d %H:%M") if ts else ""
        rca = _rca_from_replies(client, a.channel, ts) if ts else ""
        rows.append({
            "slack_ts": ts,
            "datetime_ist": when,
            "author": p.get("author") or "",
            "rating": p.get("rating") or "",
            "booking_id": p.get("booking_id") or "",
            "review_text": (p.get("body") or p.get("text") or "").strip(),
            "has_rca": "yes" if rca else "no",
            "rca_text": rca,
        })
        if n_rev % 25 == 0:
            print(f"  pulled RCA threads for {n_rev} reviews…", flush=True)

    # 3. write the CSV
    rows.sort(key=lambda r: r["slack_ts"])
    cols = ["slack_ts", "datetime_ist", "author", "rating", "booking_id",
            "review_text", "has_rca", "rca_text"]
    with open(a.out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    with_rca = sum(1 for r in rows if r["has_rca"] == "yes")
    print(f"\nDone. {n_rev} reviews over ~{a.months} months → {a.out}")
    print(f"  {with_rca} have an RCA in-thread, {n_rev - with_rca} do not.")
    print("  (Nothing was written to the database.)")


if __name__ == "__main__":
    main()
