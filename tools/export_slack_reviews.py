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
            res = fn(**kw)
        except SlackApiError as e:
            # A 429 is a plain throttle: wait the stated time and retry.
            if e.response is not None and e.response.status_code == 429:
                wait = int(e.response.headers.get("Retry-After", "5"))
                print(f"  (rate-limited, waiting {wait}s…)", flush=True)
                time.sleep(wait + 1)
                continue
            raise
        # Slack ALSO returns errors as HTTP 200 with {"ok": false, "error": ...}
        # (e.g. "ratelimited", "missing_scope", "not_in_channel"). The slack_sdk
        # normally raises on these, but not always — so check explicitly rather
        # than let an error body slip through as "0 messages". "ratelimited"
        # here means back off and retry; anything else is a hard failure that
        # must be NAMED, not silently reported as an empty result.
        if not res.get("ok", True):
            err = res.get("error", "unknown_error")
            if err in ("ratelimited", "rate_limited"):
                print("  (rate-limited body, waiting 30s…)", flush=True)
                time.sleep(30)
                continue
            raise RuntimeError(
                f"Slack returned ok=false: {err!r}. "
                f"(missing_scope → the bot token lacks channels:history/"
                f"groups:history or conversations.replies; not_in_channel → add "
                f"the bot to the channel; channel_not_found → wrong channel id.)")
        return res
    raise RuntimeError("Slack kept rate-limiting after 8 retries — wait a few "
                       "minutes and run again; the previous full pull likely "
                       "used up the app's history-read budget.")


def _workspace_url(client):
    """The workspace base URL, fetched ONCE, so every review permalink is built
    locally (channel + ts) instead of an API call per review."""
    try:
        r = _call(client.auth_test)
        return (r.get("url") or "").rstrip("/") + "/"
    except Exception:
        return ""


def _permalink(base_url, channel, ts):
    """The standard Slack archive link: <workspace>/archives/<channel>/p<ts no dot>."""
    if not base_url or not ts:
        return ""
    return f"{base_url}archives/{channel}/p{ts.replace('.', '')}"


def _tp_reference_raw(msg):
    """The RAW value of Trustpilot's 'Reference number' field, as the guest typed
    it — which is often NOT a booking id (a venue name, a date, free text). This
    is separate from the extracted BID; parse_review keeps only the BID."""
    for att in msg.get("attachments", []) or []:
        for f in att.get("fields", []) or []:
            if "reference" in str(f.get("title") or "").lower():
                v = str(f.get("value") or "").strip()
                if v:
                    return v
    return ""


def _detect_language(text):
    """Best-effort language NAME, only if langdetect is installed. Returns '' when
    it is not, so the column is honestly empty rather than guessed — never a
    silent wrong label. Install with: pip install langdetect"""
    t = (text or "").strip()
    if len(t) < 8:
        return ""
    try:
        from langdetect import detect
    except Exception:
        return ""
    try:
        return detect(t)          # ISO code, e.g. 'en', 'it', 'de'
    except Exception:
        return ""


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
    ap.add_argument("--since", default="",
                    help="start date YYYY-MM-DD (e.g. 2026-04-01). Overrides --months.")
    ap.add_argument("--months", type=float, default=6,
                    help="how far back to go if --since is not given (default 6)")
    ap.add_argument("--out", default="rca_slack_export.csv")
    ap.add_argument("--channel", default=os.getenv("SLACK_CHANNEL_ORM", "").strip(),
                    help="channel id (defaults to SLACK_CHANNEL_ORM)")
    ap.add_argument("--sleep", type=float, default=0.4,
                    help="seconds to pause between thread-reply calls, to stay "
                         "under Slack's rate limit (default 0.4)")
    a = ap.parse_args()
    if not a.channel:
        sys.exit("No channel — set SLACK_CHANNEL_ORM or pass --channel C0XXXX.")

    # Reuse the tool's OWN review detection + parsing, so this export sees a
    # review exactly the way the dashboard does.
    from server.services.slack import is_trustpilot_message, parse_review

    client = _client()
    base_url = _workspace_url(client)          # one call, reused for every permalink
    if a.since:
        try:
            oldest = datetime.strptime(a.since, "%Y-%m-%d").replace(
                tzinfo=IST).timestamp()
        except ValueError:
            sys.exit(f"--since must be YYYY-MM-DD, got {a.since!r}")
        window_desc = f"since {a.since}"
    else:
        oldest = time.time() - a.months * 30.44 * 24 * 3600
        window_desc = f"~{a.months} months"

    # 1. page the channel history back to `oldest`.
    # An ok:true page with ZERO messages on a channel we know is busy is Slack
    # softly throttling (it hands back empty pages under load rather than a 429).
    # So the FIRST page is retried with backoff before we believe an empty
    # channel — the "found-nothing vs did-not-run" rule applied to a soft limit.
    msgs, cursor, pages = [], None, 0
    while True:
        pages += 1
        res = _call(client.conversations_history, channel=a.channel,
                    oldest=str(oldest), limit=200,
                    **({"cursor": cursor} if cursor else {}))
        page_msgs = res.get("messages", [])
        if pages == 1 and not page_msgs and not cursor:
            warn = res.get("warning") or (res.get("response_metadata") or {}).get("warnings")
            for wait in (20, 40, 80):
                print(f"  page 1 came back EMPTY (ok=true, warning={warn!r}) — "
                      f"likely a soft throttle; waiting {wait}s and retrying…",
                      flush=True)
                time.sleep(wait)
                res = _call(client.conversations_history, channel=a.channel,
                            oldest=str(oldest), limit=200)
                page_msgs = res.get("messages", [])
                if page_msgs:
                    break
        msgs.extend(page_msgs)
        cursor = (res.get("response_metadata") or {}).get("next_cursor") or None
        print(f"  history page {pages}: {len(msgs)} messages so far", flush=True)
        if not cursor:
            break

    # `_call` now raises on any Slack error, so reaching here with zero messages
    # means Slack genuinely returned an empty window — NOT a swallowed failure.
    # Say what the two honest causes are rather than a bare "0 reviews".
    if not msgs:
        sys.exit(
            "Slack returned 0 messages for this window, with no error.\n"
            "  Two real causes:\n"
            "   1) Rate-limit cool-down — a big pull just before this can leave the\n"
            "      app's conversations.history budget spent for a while. Wait ~15\n"
            "      minutes and run again.\n"
            "   2) Retention — the channel does not hold messages this far back.\n"
            "      Try a shorter window, e.g. --months 1, to tell the two apart:\n"
            "      if --months 1 returns rows, it was retention/throttle on the\n"
            "      longer window; if it is also 0, the app cannot read the channel\n"
            "      right now (throttle) — wait and retry.")

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
        review_text = (p.get("body_original") or "").strip()
        rows.append({
            "slack_ts": ts,
            "datetime_ist": when,
            "permalink": _permalink(base_url, a.channel, ts),
            "author": p.get("author") or "",
            "rating": p.get("rating") or "",
            "language": _detect_language(review_text),
            "booking_id": p.get("reference_number") or "",     # the extracted BID
            "tp_reference_raw": _tp_reference_raw(m),           # raw TP reference field
            "review_text": review_text,
            "has_rca": "yes" if rca else "no",
            "rca_text": rca,
        })
        if a.sleep:
            time.sleep(a.sleep)          # gentle pacing so we don't trip the limiter
        if n_rev % 25 == 0:
            print(f"  pulled RCA threads for {n_rev} reviews…", flush=True)

    # 3. write the CSV
    rows.sort(key=lambda r: r["slack_ts"])
    cols = ["slack_ts", "datetime_ist", "permalink", "author", "rating",
            "language", "booking_id", "tp_reference_raw",
            "review_text", "has_rca", "rca_text"]
    with open(a.out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    with_rca = sum(1 for r in rows if r["has_rca"] == "yes")
    print(f"\nDone. {n_rev} reviews ({window_desc}) → {a.out}")
    print(f"  {with_rca} have an RCA in-thread, {n_rev - with_rca} do not.")
    print("  (Nothing was written to the database.)")


if __name__ == "__main__":
    main()
