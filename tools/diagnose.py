#!/usr/bin/env python3
"""
One command that answers every question worth asking about this deployment.

    python3 tools/diagnose.py                 # everything, plus one live pipeline run
    python3 tools/diagnose.py --no-run        # skip the pipeline run (read-only)
    python3 tools/diagnose.py --run tp_...    # run that specific review instead
    python3 tools/diagnose.py --hours 168     # widen the Slack history window

Why this exists: every failure in this app looks the same from the dashboard -
reviews sit in Untraceable, a section is empty, a change "does not reflect" -
and the cause is somewhere in a chain of eight things (which process is
running, which database it holds, whether BigQuery/Zendesk/Anthropic are live,
whether the sheets are readable, whether Slack ever delivered the review,
whether the pipeline raised). Asking for those one at a time costs a round
trip each. This walks the whole chain, prints a verdict per link, and ends
with the one line that says which link is broken.

Writes the same output to diagnose_report.txt so it can be pasted whole.
Secrets are never printed - only whether they are set, and how long they are.
"""
import argparse
import io
import os
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK, BAD, WARN, INFO = " OK ", "FAIL", "WARN", "    "
_problems: list[str] = []


def hdr(title: str):
    print(f"\n{'═' * 78}\n {title}\n{'═' * 78}")


def brief(text, limit: int = 160) -> str:
    """First meaningful line, clipped. A SQLAlchemy error is twenty lines of
    SQL wrapped around six useful words."""
    t = " ".join(str(text).split())
    return t[:limit] + ("…" if len(t) > limit else "")


def line(state: str, label: str, detail: str = ""):
    print(f"[{state}] {label}")
    if detail:
        for l in str(detail).splitlines()[:12]:
            print(f"       {l}")
    if state == BAD:
        _problems.append(brief(label))


def section(fn, title: str, *a, **kw):
    """Run one section; a failure inside it must not stop the rest."""
    hdr(title)
    try:
        fn(*a, **kw)
    except Exception as e:
        line(BAD, f"{title} could not be checked: {type(e).__name__}: {brief(e)}")


# ── 1. build / process ──────────────────────────────────────────────────────

def sec_build():
    import server.api as api
    v = api.get_version()
    line(OK if not v.get("stale") else BAD,
         f"code: {v.get('short')} ({v.get('environment')})",
         f"on disk: {(v.get('on_disk') or '')[:7]}\n"
         f"started: {v.get('started_at')} (up {v.get('uptime_s')}s)"
         + ("\nSTALE: the files have moved on but this process has not - restart it"
            if v.get("stale") else ""))
    if v.get("environment") == "deployment":
        line(WARN, "this is a DEPLOYMENT",
             "a deployment is a frozen snapshot; a git pull in the repl does not "
             "change it. Press Deploy to update it.")
    # Is a server actually up, and is it this code?
    try:
        import httpx
        r = httpx.get("http://localhost:5000/api/version", timeout=3)
        served = r.json()
        line(OK, f"server on :5000 answers, running {served.get('short')}",
             "" if served.get("short") == v.get("short")
             else f"MISMATCH: the server is running {served.get('short')} but this "
                  f"checkout is {v.get('short')} - restart the server")
    except Exception as e:
        line(WARN, "no server answering on :5000", f"{type(e).__name__}: {e}")


# ── 2. services ─────────────────────────────────────────────────────────────

def sec_services():
    from server.config import status_summary
    st = status_summary()
    line(INFO, f"mock_mode = {st.get('mock_mode')}")
    for name, live in (st.get("services") or {}).items():
        crit = name in ("anthropic", "bigquery", "zendesk")
        line(OK if live else (BAD if crit else WARN), f"{name}: {live}",
             "" if live else
             ("nothing can match without this" if name == "bigquery" else
              "no timeline or ticket facts without this" if name == "zendesk" else
              "no classification, RCA or response without this" if name == "anthropic" else
              ""))
    for var in ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_USER_TOKEN",
                "GCP_SERVICE_ACCOUNT_JSON", "DSS_SHEET_ID",
                "CANNED_RESPONSES_SHEET_ID", "DATABASE_URL", "ANTHROPIC_API_KEY"):
        val = os.getenv(var, "")
        shown = (val if var in ("DSS_SHEET_ID", "CANNED_RESPONSES_SHEET_ID")
                 else (f"set ({len(val)} chars)" if val else "EMPTY"))
        line(INFO, f"env {var}: {shown}")


# ── 3. database ─────────────────────────────────────────────────────────────

def sec_database():
    from sqlalchemy import inspect as sa_inspect
    from server.db import engine, SessionLocal, Review, RcaDraft, init_db
    # Idempotent, and it is the fix for the most common cause of "every review
    # is untraceable": a column the model declares that the table lacks.
    try:
        init_db()
        line(INFO, "ran create_all + column migration (idempotent)")
    except Exception as e:
        line(BAD, "migration failed", brief(e))
    url = engine.url
    is_sqlite = url.get_backend_name().startswith("sqlite")
    line(WARN if is_sqlite else OK,
         f"database: {url.get_backend_name()} -> "
         f"{url.database if is_sqlite else f'{url.host}/{url.database}'}",
         "CONTAINER-LOCAL FILE. A published deployment keeps its own copy, so two "
         "dashboards will disagree and no cache clearing can fix it. Set "
         "DATABASE_URL to a shared Postgres in BOTH environments."
         if is_sqlite else "shared - both environments can see the same rows")

    declared = {c.name for c in RcaDraft.__table__.columns}
    try:
        actual = {c["name"] for c in sa_inspect(engine).get_columns("rca_drafts")}
        missing = sorted(declared - actual)
        line(OK if not missing else BAD,
             f"rca_drafts columns: {len(actual)} present",
             "" if not missing else
             f"MISSING {missing} - every query on this table fails with 'no such "
             f"column', /api/reviews returns nothing, and EVERY review shows as "
             f"untraceable. Restart the server to run the migration.")
    except Exception as e:
        line(BAD, "cannot inspect rca_drafts", brief(e))

    s = SessionLocal()
    try:
        total = s.query(Review).count()
        drafts = s.query(RcaDraft).count()
        t1 = s.query(RcaDraft).filter(RcaDraft.match_tier == 1).count()
        cand = s.query(RcaDraft).filter(RcaDraft.candidate_state.is_(True)).count()
        untr = total - s.query(RcaDraft).filter(RcaDraft.match_tier.isnot(None)).count()
        newest = s.query(Review).order_by(Review.received_at.desc()).first()
        line(INFO, f"reviews {total} | drafts {drafts} | tier1 {t1} | "
                   f"candidates {cand} | untraceable {untr}")
        if newest:
            age = datetime.utcnow() - (newest.received_at or datetime.utcnow())
            line(INFO, f"newest review {newest.id} "
                       f"({int(age.total_seconds() // 3600)}h ago)")
        if total and drafts < total:
            line(BAD, f"{total - drafts} review(s) have NO draft row",
                 "the pipeline never completed for them - see the RUN section")
    finally:
        s.close()


# ── 4. sheets ───────────────────────────────────────────────────────────────

def sec_sheets():
    import asyncio
    from server.services import dss
    tabs = asyncio.run(dss._get_tabs())
    empty = [t for t, r in tabs.items() if not r]
    line(OK if tabs and not empty else BAD,
         "DSS tabs: " + (", ".join(f"{t}={len(r)}" for t, r in tabs.items()) or "none"),
         "" if not empty else f"empty: {empty} - the sheet is private, renamed, or "
                              f"DSS_SHEET_ID points elsewhere")
    from server.services.canned import _get_rows
    rows = asyncio.run(_get_rows())
    line(OK if rows else WARN, f"canned responses: {len(rows)} row(s)",
         "" if rows else "responses will be drafted with no tone reference")


# ── 5. slack ingestion ──────────────────────────────────────────────────────

def sec_slack(hours: int):
    from server.config import (SLACK_BOT_TOKEN, SLACK_CHANNEL_ORM,
                               TRUSTPILOT_BOT_USER_ID)
    from server.db import SessionLocal, Review, SlackEventSeen
    line(INFO, f"channel = {SLACK_CHANNEL_ORM or 'EMPTY'} | "
               f"trustpilot bot id = {TRUSTPILOT_BOT_USER_ID or '(matching by ★)'}")
    s = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        hits = s.query(SlackEventSeen).filter(SlackEventSeen.seen_at > cutoff).count()
        line(OK if hits else WARN, f"webhook deliveries in {hours}h: {hits}",
             "" if hits else "Slack is not reaching this server. Check the app's "
                             "Event Subscriptions URL points at this host's "
                             "/webhook/slack and shows Verified.")
    finally:
        s.close()

    if not (SLACK_BOT_TOKEN and SLACK_CHANNEL_ORM):
        line(WARN, "cannot read channel history (token or channel missing)")
        return
    from slack_sdk import WebClient
    c = WebClient(token=SLACK_BOT_TOKEN)
    who = c.auth_test()
    line(OK, f"bot token valid: {who.get('user')} on {who.get('team')}")
    info = c.conversations_info(channel=SLACK_CHANNEL_ORM)["channel"]
    line(OK if info.get("is_member") else BAD,
         f"channel #{info.get('name')} is_member={info.get('is_member')}",
         "" if info.get("is_member") else "the bot is NOT in the channel, so Slack "
                                          "sends it no message events")
    from server.services.slack import is_trustpilot_message, parse_review
    oldest = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
    msgs = (c.conversations_history(channel=SLACK_CHANNEL_ORM,
                                    oldest=str(oldest), limit=200)
            .get("messages") or [])
    tp = [m for m in msgs if is_trustpilot_message({**m, "channel": SLACK_CHANNEL_ORM})]
    s = SessionLocal()
    try:
        missing = [m for m in tp
                   if not s.query(Review).filter(
                       Review.id == f"tp_{str(m.get('ts','')).replace('.', '_')}").first()]
    finally:
        s.close()
    line(OK if not missing else BAD,
         f"channel history {hours}h: {len(msgs)} messages, {len(tp)} Trustpilot, "
         f"{len(missing)} not ingested",
         "" if not missing else "press ↻ Refresh in the dashboard, or POST "
                                "/api/reviews/refresh-slack, to pull them in")


# ── 6. reviews ──────────────────────────────────────────────────────────────

def sec_reviews():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "wu", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "why_untraceable.py"))
    wu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wu)
    from server.config import is_live
    from server.db import SessionLocal, Review
    bq = is_live("bigquery")
    s = SessionLocal()
    try:
        reviews = s.query(Review).order_by(Review.received_at.desc()).limit(100).all()
        counts: dict[str, int] = {}
        rows = []
        for r in reviews:
            verdict, detail = wu.classify(r, r.draft, bq)
            counts[verdict] = counts.get(verdict, 0) + 1
            if verdict != "matched":
                rows.append((r.id, r.reference_number, verdict, detail))
        for k, v in sorted(counts.items(), key=lambda x: -x[1]):
            line(OK if k == "matched" else
                 (WARN if k == "NOTHING TO SEARCH WITH" else BAD), f"{v:>3}  {k}")
        if rows:
            print("\n  first 10 unmatched:")
            for rid, ref, verdict, detail in rows[:10]:
                print(f"   {rid:<26} ref={str(ref or '-'):<11} {verdict}")
                print(f"     {detail}")
        return rows
    finally:
        s.close()


# ── 7. one real pipeline run ────────────────────────────────────────────────

def sec_run(review_id: str):
    import asyncio
    import logging
    import re
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)-7s %(name)s: %(message)s", force=True)
    from server.pipeline import process_review
    from server.db import SessionLocal, Review
    line(INFO, f"running the pipeline for {review_id} (this takes ~30s)")
    raised = None
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            asyncio.run(process_review(review_id))
    except Exception as e:
        raised = traceback.format_exc()
    log_tail = "\n".join(buf.getvalue().splitlines()[-40:])
    if log_tail:
        print("  ── pipeline log (last 40 lines) ──")
        for l in log_tail.splitlines():
            print(f"   {l}")
    if raised:
        line(BAD, "THE PIPELINE RAISED - this is the cause", raised)
        return
    s = SessionLocal()
    try:
        r = s.query(Review).filter(Review.id == review_id).first()
        d = r.draft if r else None
        if not d:
            line(BAD, "no draft row after a clean run",
                 "the save never happened - a bug, not a match failure")
            return
        line(OK if d.match_tier else WARN,
             f"result: tier={d.match_tier} booking="
             f"{(d.booking or {}).get('id') or '(none)'}"
             f"{' [UNVERIFIED]' if (d.booking or {}).get('_unverified') else ''} "
             f"candidates={len(d.candidates_list or [])}",
             (d.extracted_signals or {}).get("untraceable_reason") or "")
        print(f"\n  confidence trail ({len(d.confidence_trail or [])} step(s)):")
        for step in (d.confidence_trail or []):
            print(f"   [{step.get('mark','?'):<4}] "
                  f"{re.sub(r'<[^>]+>', '', step.get('text',''))}")
        print(f"\n  extracted signals:")
        for k, v in (d.extracted_signals or {}).items():
            print(f"   {k}: {str(v)[:160]}")
        print(f"\n  narrowing attempts ({len(d.narrowing_attempts or [])}):")
        for a in (d.narrowing_attempts or []):
            print(f"   {a}")
        print(f"\n  produced: timeline={len(d.timeline or [])} events · "
              f"insights={'yes' if d.insights else 'NO'} · "
              f"dss={(d.dss_rec or {}).get('match_score', '(empty)')} · "
              f"rca_v3={'yes' if d.rca_v3 else 'NO'} · "
              f"response={'yes' if d.suggested_response else 'NO'}")
    finally:
        s.close()


def sec_remote(url: str):
    """Check a PUBLISHED deployment over HTTP.

    A deployment is a frozen snapshot: it keeps running the code it was built
    from no matter what is pulled into the workspace, and the two can share a
    database, so the published dashboard shows current data rendered by old
    code. That is invisible from either side - this compares them.
    """
    import httpx
    base = url.rstrip("/")
    try:
        v = httpx.get(f"{base}/api/version", timeout=20).json()
    except Exception as e:
        line(BAD, f"cannot reach {base}", brief(e))
        return
    local = ""
    try:
        import server.api as api
        local = api.get_version().get("short", "")
    except Exception:
        pass
    same = (not local) or v.get("short") == local
    line(OK if same else BAD,
         f"deployed code: {v.get('short')} ({v.get('environment')})",
         "" if same else
         f"BEHIND: the workspace is on {local}, the deployment is on "
         f"{v.get('short')}. Press Deploy / Redeploy - a git pull does not "
         f"touch a deployment.")
    db = v.get("db") or {}
    line(INFO, f"deployed db: {db.get('dialect')} -> {db.get('target')} "
               f"(shared={db.get('shared')})",
         f"reviews {db.get('reviews')} · drafts {db.get('drafts')} · "
         f"untraceable {db.get('untraceable')}"
         if db.get("reviews") is not None else "")
    try:
        h = httpx.get(f"{base}/api/health", timeout=20).json()
        dead = [k for k, val in (h.get("services") or {}).items() if not val]
        line(OK if not dead else BAD,
             f"deployed services: {len(h.get('services') or {}) - len(dead)} live",
             "" if not dead else f"not live: {dead}")
    except Exception as e:
        line(WARN, "deployed /api/health unreadable", brief(e))
    try:
        b = httpx.get(f"{base}/api/reviews/bulk-status", timeout=20).json()
        if b.get("running"):
            line(INFO, f"a bulk re-run is in flight there: "
                       f"{b.get('done')}/{b.get('total')}, {b.get('failed')} failed")
        elif b.get("total"):
            line(INFO, f"last bulk re-run there: {b.get('done')}/{b.get('total')}, "
                       f"{b.get('failed')} failed")
    except Exception:
        line(WARN, "deployed build has no /api/reviews/bulk-status",
             "which itself means it is running code older than a6b3545")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="",
                    help="also check a published deployment over HTTP, e.g. "
                         "https://trustpilot-rca.replit.app")
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--run", default="", help="review id to run; default picks the "
                                              "first unmatched one")
    ap.add_argument("--no-run", action="store_true", help="skip the pipeline run")
    args = ap.parse_args()

    out = io.StringIO()

    class Tee:
        def write(self, s):
            sys.__stdout__.write(s)
            out.write(s)

        def flush(self):
            sys.__stdout__.flush()

    with redirect_stdout(Tee()):
        print(f"ORM RCA diagnostic · {datetime.utcnow().isoformat()}Z · "
              f"cwd {os.getcwd()}")
        section(sec_build, "1. BUILD & PROCESS")
        if args.url:
            section(sec_remote, "1b. PUBLISHED DEPLOYMENT", args.url)
        section(sec_services, "2. SERVICES")
        section(sec_database, "3. DATABASE")
        section(sec_sheets, "4. SHEETS (DSS, canned)")
        section(sec_slack, "5. SLACK INGESTION", args.hours)
        unmatched = []
        hdr("6. REVIEWS")
        try:
            unmatched = sec_reviews() or []
        except Exception as e:
            line(BAD, f"review scan failed: {brief(e)}")
        if not args.no_run:
            target = args.run or (unmatched[0][0] if unmatched else "")
            if target:
                section(sec_run, "7. LIVE PIPELINE RUN", target)
            else:
                hdr("7. LIVE PIPELINE RUN")
                line(INFO, "nothing unmatched to run")

        hdr("VERDICT")
        if _problems:
            print(" The broken links, in order:")
            seen = set()
            for p in _problems:
                if p in seen:
                    continue
                seen.add(p)
                print(f"   ✗ {p}")
        else:
            print(" No broken link found. Every check passed.")

    path = os.path.join(os.getcwd(), "diagnose_report.txt")
    try:
        with open(path, "w") as f:
            f.write(out.getvalue())
        print(f"\nfull report written to {path}")
    except Exception:
        pass
    return 1 if _problems else 0


if __name__ == "__main__":
    sys.exit(main())
