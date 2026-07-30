#!/usr/bin/env python3
"""
Walk the Slack -> dashboard ingestion chain and name the broken link.

    python3 tools/check_slack_ingestion.py
    python3 tools/check_slack_ingestion.py --replay      # re-ingest what was missed

"Reviews stopped arriving" has about eight possible causes and they look
identical from the dashboard: token revoked, bot removed from the channel,
wrong channel id in env, Slack app's Event Subscription URL pointing at a
dead Repl, signing-secret mismatch, Trustpilot bot id changed, parser
rejecting the post, or the pipeline throwing. This checks each in order and
prints which one it is.

Read-only unless --replay is passed. --replay pulls recent channel history,
finds Trustpilot posts with no Review row, and pushes them through the same
pipeline the webhook uses - so a webhook outage does not mean those reviews
are lost, they were sitting in Slack the whole time.
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK, BAD, WARN = "  OK  ", " FAIL ", " WARN "


def line(state, label, detail=""):
    print(f"[{state}] {label}" + (f"\n         {detail}" if detail else ""))


def check_config():
    from server.config import (
        SLACK_SIGNING_SECRET, SLACK_BOT_TOKEN, SLACK_USER_TOKEN,
        SLACK_CHANNEL_ORM, TRUSTPILOT_BOT_USER_ID, ORM_CHANNELS, MOCK_MODE,
    )
    ok = True
    line(WARN if MOCK_MODE else OK,
         f"MOCK_MODE = {MOCK_MODE}",
         "In mock mode nothing real is ingested." if MOCK_MODE else "")
    for name, val, needed in (
        ("SLACK_SIGNING_SECRET", SLACK_SIGNING_SECRET, True),
        ("SLACK_BOT_TOKEN",      SLACK_BOT_TOKEN,      True),
        ("SLACK_USER_TOKEN",     SLACK_USER_TOKEN,     False),
        ("SLACK_CHANNEL_ORM",    SLACK_CHANNEL_ORM,    True),
        ("TRUSTPILOT_BOT_USER_ID", TRUSTPILOT_BOT_USER_ID, False),
    ):
        if val:
            shown = val if name.endswith(("CHANNEL_ORM", "USER_ID")) else f"set ({len(val)} chars)"
            line(OK, f"{name} = {shown}")
        elif needed:
            ok = False
            line(BAD, f"{name} is EMPTY",
                 "Inbound webhook cannot work without it." if name != "SLACK_CHANNEL_ORM"
                 else "With no channel set, ORM_CHANNELS is empty and EVERY channel "
                      "is accepted - or, if you meant to scope it, nothing is.")
        else:
            line(WARN, f"{name} is empty",
                 "Trustpilot posts are then matched by star symbols only."
                 if name == "TRUSTPILOT_BOT_USER_ID" else "Outbound posting disabled.")
    line(OK, f"ORM_CHANNELS = {ORM_CHANNELS or '(empty - no channel filter)'}")
    return ok


def check_tokens_and_channel():
    """auth.test proves the token lives; conversations_info proves the bot can
    see the channel; is_member proves it was not removed from it."""
    from server.config import SLACK_BOT_TOKEN, SLACK_CHANNEL_ORM
    if not SLACK_BOT_TOKEN:
        return None
    try:
        from slack_sdk import WebClient
    except Exception as e:
        line(BAD, f"slack_sdk not importable: {e}")
        return None
    c = WebClient(token=SLACK_BOT_TOKEN)
    try:
        who = c.auth_test()
        line(OK, f"bot token valid - team {who.get('team')}, bot {who.get('user')} "
                 f"({who.get('user_id')})")
    except Exception as e:
        line(BAD, f"auth.test failed: {e}",
             "Token revoked or rotated - the webhook will 401 on every event.")
        return None
    if not SLACK_CHANNEL_ORM:
        return c
    try:
        info = c.conversations_info(channel=SLACK_CHANNEL_ORM)["channel"]
        member = info.get("is_member")
        line(OK if member else BAD,
             f"channel {SLACK_CHANNEL_ORM} = #{info.get('name')}, "
             f"bot is_member={member}",
             "" if member else "The bot is NOT in the channel, so Slack sends it "
                               "no message events. Invite it back.")
    except Exception as e:
        line(BAD, f"conversations_info({SLACK_CHANNEL_ORM}) failed: {e}",
             "Wrong channel id, or the bot has no access to it.")
    return c


def check_db(hours: int):
    """What actually landed. A gap between the newest review and now is the
    symptom; the checks above say why."""
    from server.db import SessionLocal, Review, SlackEventSeen
    db = SessionLocal()
    try:
        total = db.query(Review).count()
        newest = db.query(Review).order_by(Review.received_at.desc()).first()
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        recent = db.query(Review).filter(Review.received_at > cutoff).count()
        seen = db.query(SlackEventSeen).count()
        seen_recent = (db.query(SlackEventSeen)
                         .filter(SlackEventSeen.seen_at > cutoff).count())
        line(OK, f"reviews in DB: {total} total, {recent} in the last {hours}h")
        if newest:
            age = datetime.utcnow() - (newest.received_at or datetime.utcnow())
            line(OK if age < timedelta(hours=hours) else WARN,
                 f"newest review: {newest.id} at {newest.received_at} "
                 f"({int(age.total_seconds() // 3600)}h ago)")
        # SlackEventSeen rows are written by the webhook BEFORE any filtering,
        # so they separate "Slack is not calling us" from "we are dropping it".
        line(OK if seen_recent else BAD,
             f"slack events received (webhook hits): {seen} total, "
             f"{seen_recent} in the last {hours}h",
             "" if seen_recent else
             "ZERO webhook hits: Slack is not reaching this server at all. "
             "Check the Slack app's Event Subscriptions Request URL points at "
             "this Repl's /webhook/slack and shows 'Verified', and that "
             "message.channels is subscribed.")
        return seen_recent
    finally:
        db.close()


def scan_history(client, hours: int, replay: bool):
    """Read the channel directly: what Trustpilot posted vs what we stored.
    This is the ground truth the webhook is supposed to have delivered."""
    from server.config import SLACK_CHANNEL_ORM
    from server.services.slack import is_trustpilot_message, parse_review
    from server.db import SessionLocal, Review
    if not client or not SLACK_CHANNEL_ORM:
        line(WARN, "skipping history scan (no client or channel)")
        return
    oldest = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
    try:
        res = client.conversations_history(
            channel=SLACK_CHANNEL_ORM, oldest=str(oldest), limit=200)
    except Exception as e:
        line(BAD, f"conversations_history failed: {e}",
             "Needs the channels:history scope on the bot token.")
        return
    msgs = res.get("messages") or []
    tp = [m for m in msgs if is_trustpilot_message({**m, "channel": SLACK_CHANNEL_ORM})]
    line(OK, f"channel history: {len(msgs)} messages in {hours}h, "
             f"{len(tp)} look like Trustpilot reviews")
    if len(msgs) and not tp:
        line(WARN, "messages exist but none matched the Trustpilot filter",
             "Either the Trustpilot bot id changed, or the post format no "
             "longer carries ★ symbols - is_trustpilot_message is rejecting them.")

    db = SessionLocal()
    missing = []
    try:
        for m in tp:
            rid = f"tp_{str(m.get('ts', '')).replace('.', '_')}"
            if not db.query(Review).filter(Review.id == rid).first():
                missing.append((rid, m))
    finally:
        db.close()

    if not missing:
        line(OK, f"every Trustpilot post in the last {hours}h has a Review row")
        return
    line(BAD, f"{len(missing)} Trustpilot post(s) in Slack have NO Review row")
    for rid, m in missing[:10]:
        parsed = parse_review({**m, "channel": SLACK_CHANNEL_ORM})
        print(f"         {rid}  rating={parsed.get('rating')}  "
              f"bid={parsed.get('reference_number') or '-'}  "
              f"{(parsed.get('body_original') or '')[:60]!r}")
    if not replay:
        print("\n         Re-run with --replay to push these through the pipeline.")
        return

    print(f"\nreplaying {len(missing)} review(s) through the pipeline ...")
    from server.pipeline import process_review
    for rid, m in missing:
        parsed = parse_review({**m, "channel": SLACK_CHANNEL_ORM})
        db = SessionLocal()
        try:
            if db.query(Review).filter(Review.id == rid).first():
                continue
            db.add(Review(
                id=rid, slack_ts=parsed["slack_ts"],
                slack_channel=parsed["slack_channel"], rating=parsed["rating"],
                language=parsed["language"], author=parsed.get("author") or None,
                body_original=parsed["body_original"],
                reference_number=parsed["reference_number"]))
            db.commit()
        finally:
            db.close()
        try:
            asyncio.run(process_review(rid))
            print(f"  ok   {rid}")
        except Exception as e:
            print(f"  FAIL {rid}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=72,
                    help="lookback window for history and DB counts")
    ap.add_argument("--replay", action="store_true",
                    help="ingest Trustpilot posts that have no Review row")
    args = ap.parse_args()

    print("═══ 1. config ═══")
    check_config()
    print("\n═══ 2. slack tokens + channel membership ═══")
    client = check_tokens_and_channel()
    # A missing table or unreadable DB is itself a finding, not a reason to
    # abandon the run - section 4 still tells you what Slack holds.
    print(f"\n═══ 3. what landed in the DB (last {args.hours}h) ═══")
    try:
        check_db(args.hours)
    except Exception as e:
        line(BAD, f"database not readable: {type(e).__name__}: {e}",
             "Run this where the server runs - it reads the server's own DB.")
    print(f"\n═══ 4. slack history vs DB (last {args.hours}h) ═══")
    try:
        scan_history(client, args.hours, args.replay)
    except Exception as e:
        line(BAD, f"history scan failed: {type(e).__name__}: {e}")
    print("\nDone." + ("" if args.replay else
                       " Nothing was written (read-only without --replay)."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
