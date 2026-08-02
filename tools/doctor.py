#!/usr/bin/env python3
"""What is working, what is not, and what could not be checked.

    python3 tools/doctor.py

Run it in the Replit shell, where the network is actually open. It uses the
app's OWN service modules, so what it reports is what the pipeline will do —
not a parallel implementation that can agree with itself while the app fails.

`is_live()` is not a health check. It is `bool(SOME_ID)` — it says a secret is
CONFIGURED, and says nothing about whether the thing answers. Every "live"
service here is therefore actually called. Three outcomes, never merged:

    WORKS      it answered, and the answer was usable
    BROKEN     it was reached and something is wrong — with the fix
    NOT SET    no credential; it was never going to run
    CANNOT     the check itself could not run; this is NOT a pass

Exit code is the number of BROKEN checks, so `python3 tools/doctor.py && echo
all good` behaves.
"""
import asyncio
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

W, B, N, C = "WORKS ", "BROKEN", "NOT SET", "CANNOT"
_results = []


def say(state, name, detail=""):
    _results.append((state, name, detail))
    tag = {W: "\033[32m ok  \033[0m", B: "\033[31mBROKEN\033[0m",
           N: "\033[90m  -  \033[0m", C: "\033[33m  ?  \033[0m"}[state]
    print(f"  [{tag}] {name}")
    for line in (detail or "").splitlines():
        if line.strip():
            print(f"          {line}")


def head(t):
    print(f"\n\033[1m{t}\033[0m")


def _short(e):
    return f"{type(e).__name__}: {' '.join(str(e).split())[:160]}"


# ── 1. configuration ────────────────────────────────────────────────────────

def check_config():
    head("CONFIGURATION")
    import server.config as cfg

    if cfg.MOCK_MODE:
        say(B, "MOCK_MODE is ON",
            "Every service below reports as not-live regardless of its "
            "credentials, and the pipeline uses stubs. Unset MOCK_MODE to "
            "test anything real.")
    else:
        say(W, "MOCK_MODE is off")

    # The one that has been wrong all along, and cannot be seen from the UI.
    env_id = os.getenv("CANNED_RESPONSES_SHEET_ID")
    code_default = "1aXnZzzFQ8tDiaXs0E2YRErOcTSOsbTOqnM7WhyOnmi4"
    env_example = "1BYTHYZK2um93IPwiOlBR8eKP4jfdjVTxKo9b1RIW_1o"
    if not env_id:
        say(B, "CANNED_RESPONSES_SHEET_ID is not set",
            f"Falling back to the code default {code_default}.\n"
            f".env.example names a DIFFERENT sheet ({env_example}) and calls "
            f"that one 'the confirmed Sheet ID'. Set the secret to whichever "
            f"is right — right now nobody can tell which is being read.")
    elif env_id == code_default:
        say(W, "CANNED_RESPONSES_SHEET_ID is set", f"{env_id} (the code default)")
    else:
        say(W, "CANNED_RESPONSES_SHEET_ID is set", f"{env_id} (an override)")

    for name, var in (("Anthropic", "ANTHROPIC_API_KEY"),
                      ("Zendesk", "ZENDESK_API_TOKEN"),
                      ("Slack bot", "SLACK_BOT_TOKEN"),
                      ("GCP service account", "GCP_SERVICE_ACCOUNT_JSON"),
                      ("Database", "DATABASE_URL")):
        if os.getenv(var) or (var == "ANTHROPIC_API_KEY"
                              and os.getenv("AI_INTEGRATIONS_ANTHROPIC_API_KEY")):
            say(W, f"{name} credential present")
        else:
            say(N, f"{name} credential ({var})", "not set")


# ── 2. the database, and whether it is the one the deployment uses ──────────

def check_db():
    head("DATABASE")
    try:
        from server.db import engine, SessionLocal, Review, RcaDraft
        from sqlalchemy import text
    except Exception as e:
        say(C, "database module", _short(e))
        return

    url = engine.url
    dialect = url.get_backend_name()
    if dialect.startswith("sqlite"):
        say(B, "this surface is on SQLite",
            f"{url.database} — a file in THIS container. The published "
            f"deployment has its own copy and neither can see the other's "
            f"reviews. Point DATABASE_URL at Postgres.")
        return

    host = (url.host or "?")
    flavour = ("Helium (Replit's current Postgres)" if "helium" in host.lower()
               else "Neon-backed legacy Replit Postgres" if "neon" in host.lower()
               else "Postgres")
    try:
        with engine.connect() as c:
            ident = c.execute(text(
                "SELECT system_identifier::text FROM pg_control_system()")).scalar()
    except Exception as e:
        ident = None
        say(C, "database identity", _short(e) + "\nWithout it I cannot tell "
            "whether this surface shares a database with the other one.")

    s = SessionLocal()
    try:
        rv, dr = s.query(Review).count(), s.query(RcaDraft).count()
    finally:
        s.close()
    say(W, f"connected to {flavour}",
        f"host {host}\nidentity {ident or 'unknown'}\n"
        f"{rv} reviews, {dr} drafts\n"
        f"Run this on your OTHER surface (workspace vs published deployment) "
        f"and compare the identity. Different identity = different database, "
        f"whatever the hostnames say.")


# ── 3. the canned sheet — the one that actually broke the reply voice ───────

def check_canned():
    head("CANNED RESPONSES SHEET")
    import server.config as cfg
    if not cfg.CANNED_RESPONSES_SHEET_ID:
        say(N, "canned sheet", "no sheet id configured")
        return
    import server.services.canned as C
    C._cache_rows, C._cache_at = [], 0
    try:
        rows = asyncio.run(C._fetch_rows())
    except Exception as e:
        say(B, "canned sheet", _short(e))
        return

    reason = C.last_failure_reason()
    if not rows:
        say(B, "the sheet produced no replies",
            (reason or "no reason recorded, which is itself a bug") +
            "\nEvery draft reply is being written with NO tone reference.")
        return

    tabs = {}
    for r in rows:
        tabs[r.get("tab", "?")] = tabs.get(r.get("tab", "?"), 0) + 1
    detail = "\n".join(f"{n:4d} replies from {t}" for t, n in sorted(
        tabs.items(), key=lambda kv: -kv[1]))
    if any("first tab only" in t for t in tabs):
        say(B, "only ONE tab of the sheet is readable",
            detail + "\nThe service account could not be used, so this fell "
            "back to the public CSV export — which can only ever see tab one. "
            "The sheet is split by channel, so the Trustpilot replies may not "
            "be in it at all. Share the sheet with the service account.")
    else:
        say(W, f"{len(rows)} replies across {len(tabs)} tab(s)", detail)

    tp = [t for t in tabs if "TP" in t or "Trustpilot" in t]
    if tp:
        say(W, "the Trustpilot tab is being read", ", ".join(tp))
    else:
        say(B, "no Trustpilot tab among the replies",
            f"Read: {sorted(tabs)}\nThis dashboard drafts Trustpilot replies, "
            f"so the tone reference is from the wrong channel.")


# ── 4. everything else that the pipeline calls ──────────────────────────────

def check_service(label, live_key, call):
    from server.config import is_live
    if not is_live(live_key):
        say(N, label, f"is_live({live_key!r}) is False — no credential, so the "
                      f"pipeline skips it")
        return
    try:
        ok, detail = call()
    except Exception as e:
        say(B, label, _short(e))
        return
    say(W if ok else B, label, detail)


def check_services():
    head("SERVICES")

    def _zendesk():
        import httpx
        from server.config import ZENDESK_SUBDOMAIN, ZENDESK_API_TOKEN, ZENDESK_EMAIL
        r = httpx.get(
            f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/users/me.json",
            auth=(f"{ZENDESK_EMAIL}/token", ZENDESK_API_TOKEN), timeout=15)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code} — {r.text[:120]}"
        return True, f"authenticated as {r.json().get('user', {}).get('email', '?')}"

    def _bigquery():
        from server.services import bigquery as bq
        n = asyncio.run(bq.get_l1_l2_by_bid("1"))
        return True, f"query returned (shape {type(n).__name__})"

    def _anthropic():
        from server.services.claude import _call
        out = asyncio.run(_call("Reply with the single word: ok", max_tokens=8))
        return ("ok" in (out or "").lower()), f"model replied {str(out)[:60]!r}"

    def _slack():
        import httpx
        from server.config import SLACK_BOT_TOKEN
        r = httpx.post("https://slack.com/api/auth.test",
                       headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                       timeout=15)
        j = r.json()
        return bool(j.get("ok")), (f"team {j.get('team')}" if j.get("ok")
                                   else f"{j.get('error')}")

    def _sheet(mod, fn, label):
        def _go():
            m = __import__(mod, fromlist=["x"])
            rows = asyncio.run(getattr(m, fn)())
            return bool(rows), f"{len(rows) if rows else 0} rows"
        return _go

    check_service("Anthropic (writes every RCA and reply)", "anthropic", _anthropic)
    check_service("Zendesk (the events timeline)", "zendesk", _zendesk)
    check_service("BigQuery (booking match + insights)", "bigquery", _bigquery)
    check_service("Slack (ingest + posting)", "slack_inbound", _slack)


# ── 5. what the drafts themselves say ───────────────────────────────────────

def check_drafts():
    head("DRAFTS — what the last run produced")
    try:
        from server.db import SessionLocal, RcaDraft
        from server.prompts import RCA_PROMPT_VERSION
    except Exception as e:
        say(C, "drafts", _short(e))
        return
    s = SessionLocal()
    try:
        try:
            rows = s.query(RcaDraft).all()
        except Exception as e:
            # An empty database with no schema is a legitimate state, not a
            # broken query — and it must not be reported as one.
            say(C, "drafts table not readable",
                _short(e).split("(Background")[0]
                + "\nIf this database has never been initialised that is "
                  "expected: start the app once, or run init_db().")
            return
        if not rows:
            say(C, "no drafts on this surface", "nothing to inspect")
            return
        fresh = [d for d in rows if d.rca_prompt_version == RCA_PROMPT_VERSION]
        say(W if fresh else C,
            f"{len(fresh)} of {len(rows)} drafts written by the current prompt",
            f"current stamp: {RCA_PROMPT_VERSION}\n"
            + ("" if fresh else
               "None were. The pipeline fixes are written INTO a draft when it "
               "runs, so re-run a review before judging whether they work."))
        noclass = [d for d in rows if not (d.l1 and d.l2)]
        say(B if noclass else W,
            f"{len(noclass)} of {len(rows)} drafts have no L1/L2",
            "Everything keyed on the classification is skipped for these: the "
            "support-tag comparison, the review-variant comparison, the "
            "scenario lookup, and the canned-response tone lookup."
            if noclass else "")
    finally:
        s.close()


def main():
    print("\033[1mORM RCA — what is working\033[0m")
    print(f"cwd {ROOT}")
    for fn in (check_config, check_db, check_canned, check_services, check_drafts):
        try:
            fn()
        except Exception:
            say(C, fn.__name__, traceback.format_exc().splitlines()[-1])

    broken = [r for r in _results if r[0] == B]
    cannot = [r for r in _results if r[0] == C]
    print(f"\n\033[1mSUMMARY\033[0m")
    print(f"  {len([r for r in _results if r[0] == W])} working, "
          f"{len(broken)} broken, "
          f"{len([r for r in _results if r[0] == N])} not configured, "
          f"{len(cannot)} could not be checked")
    for _, n, _d in broken:
        print(f"  \033[31mBROKEN\033[0m  {n}")
    for _, n, _d in cannot:
        print(f"  \033[33m  ?   \033[0m {n} — could not be checked, which is "
              f"NOT the same as passing")
    return len(broken)


if __name__ == "__main__":
    sys.exit(main())
