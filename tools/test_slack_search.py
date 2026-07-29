#!/usr/bin/env python3
"""
Does Slack search work with the token this box actually holds - and does it
find anything for a real booking id?

    python3 tools/test_slack_search.py              # BID 32885787
    python3 tools/test_slack_search.py 32885787
    python3 tools/test_slack_search.py --bid 32885787 --limit 20

Run it WHERE THE TOKEN LIVES (Replit). It reads SLACK_USER_TOKEN through
server/config.py, so it sees exactly what the server sees - not a shell export
that the app never gets.

This exists because the dashboard printed "No Slack messages found for this
booking" and nobody could say whether that meant the workspace had been
searched or that the search had never run. search_mentions returned an empty
list for three different things - no user token, an API refusal, and a genuine
zero - and the panel rendered all three with the same sentence. The RCA leans
on those ops/escalation pings, so "we did not look" being displayed as "there
is nothing there" is the failure worth catching.

Five questions, in order, stopping at the first hard failure, because every
later answer is worthless without the earlier one:

  1. Is SLACK_USER_TOKEN set, and is it a USER token? (xoxb- is a BOT token and
     can never call search.messages, whatever scopes it carries.)
  2. Does auth.test succeed - as which user, in which workspace?
  3. Does the token carry search:read? auth.test passing does not imply it.
  4. Does search.messages return without error at all?
  5. Does it return anything for a real BID?

Step 5 matching nothing is a PASS. "The search ran and this booking was never
discussed in Slack" is a real answer; it is only the first four steps that can
make the dashboard's sentence a lie.

Exit code is 0 only when the search actually ran.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The BID on screen when the panel claimed there were no Slack messages.
DEFAULT_BID = "32885787"

PASS, FAIL, WARN = "ok  ", "FAIL", "--  "
LINE = "-" * 66


def step(n, title):
    print(f"\n{n}. {title}")


def ok(msg):
    print(f"  {PASS}  {msg}")


def warn(msg):
    print(f"  {WARN}  {msg}")


def stop(msg, *fix):
    """Print the verdict and what to do about it, then hand back exit code 1.

    Every caller of this is a dead end for the run, so the fix is printed here
    rather than left to the reader - the point of the tool is that nobody has
    to interpret a stack trace to find out whether search works.
    """
    print(f"  {FAIL}  {msg}")
    for f in fix:
        print(f"        → {f}")
    print(f"\n{LINE}")
    print('VERDICT: Slack search is NOT working. The dashboard\'s '
          '"No Slack messages found"')
    print("         is not an answer about the workspace - the search never ran.")
    print(LINE)
    return 1


def token_kind(tok):
    """Slack encodes the token type in the prefix, and it is decisive here."""
    if tok.startswith("xoxb-"):
        return "bot"
    # xoxe- / xoxe.xoxp- are rotating user tokens; they call search fine.
    if tok.startswith("xoxp-") or tok.startswith("xoxe-") or tok.startswith("xoxe."):
        return "user"
    return "unknown"


def header(res, name):
    """Slack's scope headers, read case-insensitively.

    Header casing varies with the transport slack_sdk picked, and a scope check
    that silently misses the header would report "no scopes granted" for a
    token that is perfectly fine.
    """
    hdrs = getattr(res, "headers", None) or {}
    for k, v in hdrs.items():
        if str(k).lower() == name:
            return v if isinstance(v, str) else (v[0] if v else "")
    return ""


def api_error(e):
    """('missing_scope', {...}) - the code is what names the fix."""
    resp = getattr(e, "response", None)
    try:
        data = resp.data if resp is not None and hasattr(resp, "data") else {}
    except Exception:
        data = {}
    data = data if isinstance(data, dict) else {}
    return data.get("error") or type(e).__name__, data


def when(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except Exception:
        return str(ts or "?")


# Slack's error strings, translated into the one thing to go and do. Anything
# not listed here still prints its raw code, which is searchable.
FIXES = {
    "missing_scope": (
        "Add the search:read scope under OAuth & Permissions → USER TOKEN "
        "SCOPES (not bot token scopes), reinstall the app, then copy the new "
        "User OAuth Token into SLACK_USER_TOKEN.",),
    "not_allowed_token_type": (
        "This is a bot token. search.messages is user-token-only. Put the "
        "User OAuth Token (xoxp-...) in SLACK_USER_TOKEN.",),
    "invalid_auth": (
        "The token is rejected. It was revoked, reinstalled, or copied with "
        "whitespace/truncation. Re-copy it from OAuth & Permissions.",),
    "token_revoked": (
        "The token was revoked. Reinstall the app and copy the new one.",),
    "account_inactive": (
        "The user this token belongs to is deactivated. Reinstall it as an "
        "active member.",),
    "ratelimited": (
        "Rate limited - not a configuration problem. Wait a minute and re-run.",),
    "org_login_required": (
        "The token is scoped to the wrong org/workspace. Reinstall it in the "
        "workspace the ORM channels live in.",),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bid", nargs="?", default=None, help=f"booking id (default {DEFAULT_BID})")
    ap.add_argument("--bid", dest="bid_flag", default=None)
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()
    bid = str(a.bid_flag or a.bid or DEFAULT_BID).strip()

    print("Slack search check - can this box search the workspace for a BID?")
    print(LINE)

    # ── 1. token present, and of the right kind ──────────────────────────────
    step(1, "SLACK_USER_TOKEN")
    try:
        from server.config import SLACK_USER_TOKEN, SLACK_BOT_TOKEN
    except Exception as e:
        return stop(f"could not import server.config: {type(e).__name__}: {e}",
                    "Run this from the repo root: python3 tools/test_slack_search.py")

    if not SLACK_USER_TOKEN:
        extra = []
        if SLACK_BOT_TOKEN:
            extra.append("SLACK_BOT_TOKEN is set, but a bot token CANNOT call "
                         "search.messages - it is a separate token.")
        return stop(
            "SLACK_USER_TOKEN is empty (server/config.py reads it from the env "
            "via .env / Replit Secrets).",
            *extra,
            "Slack app → OAuth & Permissions → User Token Scopes: add "
            "search:read, install/reinstall to the workspace.",
            "Copy the 'User OAuth Token' (xoxp-...) into the SLACK_USER_TOKEN "
            "secret, then restart the server.",
            "Until then search_mentions returns its 'search unavailable' "
            "sentinel - it does not claim the booking is absent from Slack.")

    kind = token_kind(SLACK_USER_TOKEN)
    shown = SLACK_USER_TOKEN[:9] + "…" + SLACK_USER_TOKEN[-4:]
    if kind == "bot":
        return stop(
            f"SLACK_USER_TOKEN holds a BOT token ({shown}).",
            "xoxb- tokens are refused by search.messages with "
            "not_allowed_token_type - no scope can change that.",
            "Use the User OAuth Token (xoxp-...) from the same app's OAuth & "
            "Permissions page.")
    if kind == "unknown":
        warn(f"unrecognised token prefix ({shown}) - continuing, auth.test decides")
    else:
        ok(f"user token present ({shown})")

    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except Exception as e:
        return stop(f"slack_sdk is not importable: {e}",
                    "pip install -r requirements.txt (slack-sdk is already listed)")

    client = WebClient(token=SLACK_USER_TOKEN)

    # ── 2. auth.test ─────────────────────────────────────────────────────────
    step(2, "auth.test - is the token live, and whose is it?")
    try:
        auth = client.auth_test()
    except SlackApiError as e:
        code, _ = api_error(e)
        return stop(f"auth.test failed: {code}", *FIXES.get(code, (
            "Check the token value in Replit Secrets and restart.",)))
    except Exception as e:
        return stop(f"auth.test could not reach Slack: {type(e).__name__}: {e}",
                    "Network/proxy problem, not a token problem - retry from "
                    "the box that runs the server.")
    ok(f"authenticated as {auth.get('user')} ({auth.get('user_id')}) "
       f"in {auth.get('team')} ({auth.get('team_id')})")
    print(f"        {auth.get('url', '')}")
    if auth.get("bot_id") and kind != "user":
        warn("auth.test reports a bot identity - step 4 will refuse")

    # ── 3. scopes ────────────────────────────────────────────────────────────
    # auth.test succeeds for any live token, including one with no scope that
    # matters here, so this is a separate question from step 2.
    step(3, "search:read scope")
    granted = header(auth, "x-oauth-scopes")
    if not granted:
        warn("Slack returned no x-oauth-scopes header - cannot read the grant "
             "list; step 4 settles it either way")
    else:
        scopes = [s.strip() for s in granted.split(",") if s.strip()]
        print(f"        granted: {', '.join(scopes)}")
        if "search:read" in scopes:
            ok("search:read is granted")
        else:
            return stop(
                "search:read is NOT among the granted scopes.",
                "OAuth & Permissions → User Token Scopes → add search:read.",
                "Reinstall the app (scope changes need a reinstall), then "
                "re-copy the User OAuth Token into SLACK_USER_TOKEN.")

    # ── 4. does search.messages run at all ───────────────────────────────────
    step(4, "search.messages - does the endpoint answer?")
    try:
        probe = client.search_messages(query="booking", count=1)
    except SlackApiError as e:
        code, data = api_error(e)
        needed = data.get("needed") or ""
        fixes = list(FIXES.get(code, ("Look the error string up in Slack's "
                                      "search.messages docs.",)))
        if needed:
            fixes.insert(0, f"Slack says the missing scope is: {needed}")
        return stop(f"search.messages returned '{code}'", *fixes)
    except Exception as e:
        return stop(f"search.messages could not reach Slack: {type(e).__name__}: {e}",
                    "Network/proxy problem - retry from the box that runs the "
                    "server.")
    total = ((probe.get("messages") or {}).get("total"))
    ok(f"search.messages ran (harmless probe query matched {total} message(s))")

    # ── 5. the real question: a real BID ─────────────────────────────────────
    step(5, f"search.messages for BID {bid}")
    try:
        res = client.search_messages(query=bid, count=a.limit)
    except SlackApiError as e:
        code, _ = api_error(e)
        return stop(f"the BID query returned '{code}' although the probe "
                    f"query worked", *FIXES.get(code, (
                        "Re-run; if it persists the query itself is being "
                        "rejected, which is a Slack-side issue.",)))
    matches = (res.get("messages") or {}).get("matches") or []
    print(f"        {len(matches)} match(es), "
          f"{(res.get('messages') or {}).get('total')} total\n")
    for m in matches:
        ch = m.get("channel", {}) or {}
        text = " ".join((m.get("text") or "").split())
        print(f"        #{ch.get('name') or ch.get('id') or '?'}  "
              f"{m.get('username') or m.get('user') or '?'}  {when(m.get('ts'))}")
        print(f"          {text[:220]}{'…' if len(text) > 220 else ''}")
        if m.get("permalink"):
            print(f"          {m['permalink']}")
        print()

    # The same call the pipeline makes, so a green run above cannot coexist
    # with a dashboard that still shows nothing: if these two disagree the
    # problem is in our code, not in Slack.
    step("5b", "server/services/slack.py search_mentions (the path the dashboard uses)")
    import asyncio
    from server.services import slack as slk
    rows = asyncio.run(slk.search_mentions(bid, limit=a.limit))
    if slk.is_search_unavailable(rows):
        return stop("search_mentions reports the search as UNAVAILABLE even "
                    "though the raw API call above worked",
                    f"reason: {rows[0].get('reason')}",
                    "The module-level client was built at import time - "
                    "restart the server after changing the secret.")
    ok(f"search_mentions returned {len(rows)} row(s) - the dashboard will show "
       f"{'them' if rows else 'its empty state'}")

    print(f"\n{LINE}")
    if matches:
        print(f"VERDICT: Slack search WORKS. BID {bid} is mentioned in "
              f"{len(matches)} message(s).")
    else:
        print(f"VERDICT: Slack search WORKS - it ran, and BID {bid} is not "
              "mentioned in any\n         channel this token can see. That is "
              "a real result, not a failure.\n         (search.messages only "
              "sees channels this user is a member of -\n         if the "
              "booking was discussed somewhere they are not, join it.)")
    print(LINE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
