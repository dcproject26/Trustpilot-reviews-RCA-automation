#!/usr/bin/env python3
"""Is the host actually serving this work, and which parts can it show yet?

    python3 tools/verify_fixes.py https://your-host
    python3 tools/verify_fixes.py                       # defaults to localhost:5000

Two questions, in this order, because the second is meaningless without the
first:

  1. IS THIS BUILD EVEN RUNNING THERE. A published Replit deployment is a
     frozen snapshot; a git pull into the repl does not touch it. Reading a
     deployment URL while pulling into the repl looks exactly like a fix that
     did not work — for every fix, indefinitely. That cost 17 hours once.

  2. WHICH FIXES CAN THIS PAGE SHOW YET. This is the part that wastes an
     afternoon if nobody says it out loud. The changes live in three places
     and only one of them appears on a draft that already exists:

       client   — rendering. Visible on any draft after a hard refresh.
       server   — the read path. Visible on any draft on reload, no re-run.
       pipeline — confidence-trail entries and the prompt. Written INTO the
                  draft row when the review ran. A draft generated before the
                  deploy will never show them however many times you reload.
                  Re-run the review.

A check that cannot run says so, in words distinguishable from a check that
ran and found the fix missing. Grepping the served HTML is a deployment
question — is this host serving the new file — not a correctness one; the
suite answers correctness.
"""
import argparse
import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, BAD, DUNNO, WAIT = "  ok  ", " MISS ", " ???? ", " re-run "


def local_fingerprint() -> str:
    """The same hash /api/version computes, so the two are comparable."""
    h = hashlib.sha256()
    files = sorted(list((ROOT / "server").rglob("*.py"))
                   + [ROOT / "client" / "index.html"])
    for f in files:
        if "__pycache__" in str(f) or not f.is_file():
            continue
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def get(url: str, timeout: int = 20):
    """(status, content_type, body) or (None, None, reason)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return (r.status, r.headers.get("Content-Type", ""),
                    r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode(
            "utf-8", "replace")
    except Exception as e:
        return None, None, f"{type(e).__name__}: {str(e)[:120]}"


# Each entry: (where, what the reader would call it, a needle in the served
# file). The needles are chosen to be the load-bearing line of each fix, not
# a nearby comment — a comment survives the code being reverted.
CLIENT_FIXES = [
    ("collapse toggles work",
     "if (!label.querySelector('.section-chev')) {"),
    ("machinery toggle is bound in the RCA column",
     "ev.target.closest('[data-tl-toggle]')"),
    ("+ Add SP record writes to the notes key",
     "rca.v3.sp_interaction_notes = sp;"),
    ("SP section survives a draft with no SP data",
     "|| {raised: 'N/A', records: []}"),
    ("guest name says which lookup failed",
     "b.guestNameNote"),
    ("stale guest-name placeholder is stripped at render",
     "g !== '[Guest name in Zendesk ticket]'"),
    ("hidden internal events are not reported as absent",
     "internal Headout machinery and hidden by the filter"),
    ("matched tickets are named on an empty timeline",
     "carried no timeline events"),
    ("ticket ids reach the renderer",
     "r.zendeskTicketIds = draft.zendesk_ticket_ids"),
    ("empty stated issue does not blame the review",
     "so it is the step that failed"),
    ("undated timelines say why",
     "function _undatedNote("),
    ("contact count reads the list it labels",
     "${_rows.length} contact${_rows.length === 1"),
    ("flag modal is the centred backdrop",
     "modal.className = 'flag-modal'"),
]

# Written into the draft when the review RAN. Reloading cannot conjure them.
PIPELINE_FIXES = [
    "classification failed / returned nothing / was repaired",
    "stated issue returned nothing",
    "Zendesk timeline: searched / not searched / failed / no usable events",
    "reply written without a tone reference (and why)",
    'booking_logs carry "undated" instead of null (prompt rule 10b)',
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="http://localhost:5000")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    print(f"host: {base}\n")

    # ── 1. is this build running there ──────────────────────────────────────
    status, ctype, body = get(base + "/api/version")
    if status is None:
        print(f"CANNOT CHECK — {body}")
        print("Nothing below would mean anything, so nothing below ran.")
        return 2
    if "json" not in (ctype or "").lower():
        hs, hc, hb = get(base + "/healthz")
        if hs == 200 and "json" in (hc or "").lower():
            print(f"CANNOT CHECK — this IS the app but an OLDER build: "
                  f"/healthz answers and /api/version does not (HTTP {status}).")
            print("Nothing has been published to this host. Publish, then re-run me.")
        else:
            print(f"CANNOT CHECK — HTTP {status}, {ctype or 'no content-type'}. "
                  f"Not the app; probably a platform page while the deployment "
                  f"is not live.")
        return 2

    ver = json.loads(body)
    mine = local_fingerprint()
    theirs = ver.get("fingerprint", "unknown")
    same = theirs != "unknown" and theirs == mine

    print(f"  source fingerprint  host {theirs}   this tree {mine}")
    print(f"  running commit      {ver.get('short') or '?'}"
          f"   on its disk {str(ver.get('on_disk') or '?')[:7]}")
    print(f"  deployment          {'published deployment' if ver.get('is_deployment') else 'dev repl'}")
    db = ver.get("db") or {}
    print(f"  database            {db.get('dialect')} {db.get('target')}"
          f"  ({db.get('drafts', '?')} drafts)")
    print()

    # Two different staleness questions, and the fingerprint answers only one.
    # It is computed from files ON DISK at request time, so on a repl that was
    # pulled but not restarted it matches perfectly while the process serves
    # the code it imported at startup. `stale` is the endpoint comparing its
    # frozen import-time commit against disk, which is the only thing that
    # catches that — and it is the more common of the two.
    if ver.get("stale"):
        print("STOP — this host pulled the code but is still RUNNING the old "
              "build.")
        print(f"  imported at startup: {ver.get('short')}   on disk now: "
              f"{str(ver.get('on_disk') or '?')[:7]}")
        print("  The files are right and the process is not. Restart it.")
        print("  Nothing below ran: every check would read the new files off "
              "disk and report a pass the running process cannot deliver.")
        return 1

    if not same:
        print("STOP — this host is NOT running this tree.")
        if theirs == "unknown":
            print("  It could not compute a fingerprint, so I cannot tell what "
                  "it is running. That is not the same as it being stale.")
        else:
            print("  Every check below would report a fix as missing when the "
                  "fix is simply not deployed. Publish first.")
        print("  On Replit: a git pull updates the repl, NOT the published "
              "deployment. Press Deploy, and approve the publish step.")
        return 1

    print("This host is running this tree. Checking what it can show.\n")

    # ── 2. client-side: served bytes ────────────────────────────────────────
    st, ct, html = get(base + "/")
    print("CLIENT — visible on any draft after a hard refresh (Cmd/Ctrl-Shift-R)")
    if st is None or not html:
        print(f"  {DUNNO} could not fetch the page ({html})")
        missing = None
    else:
        missing = 0
        for name, needle in CLIENT_FIXES:
            hit = needle in html
            missing += 0 if hit else 1
            print(f"  {OK if hit else BAD} {name}")
    print()

    # ── 3. server read path: one real draft ─────────────────────────────────
    print("SERVER — visible on any draft on reload, no re-run needed")
    st, ct, raw = get(base + "/api/reviews")
    ids = []
    if st == 200:
        try:
            payload = json.loads(raw)
            rows = payload if isinstance(payload, list) else payload.get("reviews", [])
            ids = [r.get("id") for r in rows if r.get("id")][:1]
        except Exception:
            pass
    if not ids:
        print(f"  {DUNNO} no review to check against — the check did not run, "
              f"which is not the same as it failing")
    else:
        st, ct, raw = get(f"{base}/api/reviews/{ids[0]}")
        try:
            draft = (json.loads(raw) or {}).get("draft") or {}
        except Exception:
            draft = {}
        if not draft:
            print(f"  {DUNNO} {ids[0]} returned no draft")
        else:
            for key, label in (("guest_name_note", "guest name carries its reason"),
                               ("zendesk_ticket_ids", "ticket ids are sent to the page"),
                               ("support_interaction_notes", "facts/notes split is served")):
                print(f"  {OK if key in draft else BAD} {label}")
    print()

    # ── 4. pipeline: only on a re-run ───────────────────────────────────────
    print("PIPELINE — written when the review RAN. A draft generated before")
    print("this deploy will NEVER show these, however often you reload.")
    try:
        from server.prompts import RCA_PROMPT_VERSION
        print(f"  current prompt stamp: {RCA_PROMPT_VERSION}")
    except Exception as e:
        # Run from another directory the import fails, and a stamp that could
        # not be read must not be compared against — "(unreadable) != rca_v4"
        # would report every draft as stale.
        RCA_PROMPT_VERSION = None
        print(f"  {DUNNO} could not read this tree's prompt stamp ({e}); "
              f"the staleness comparison below did not run")
    stamps = {}
    if ids:
        st, ct, raw = get(f"{base}/api/reviews/{ids[0]}")
        try:
            d = (json.loads(raw) or {}).get("draft") or {}
            stamps[ids[0]] = d.get("rca_prompt_version") or "(none)"
        except Exception:
            pass
    for rid, stamp in stamps.items():
        if RCA_PROMPT_VERSION is None:
            print(f"  {DUNNO} {rid} was written by {stamp} — nothing to compare it to")
            continue
        fresh = stamp == RCA_PROMPT_VERSION
        print(f"  {OK if fresh else WAIT} {rid} was written by {stamp}")
        if not fresh:
            print(f"         → re-run this review to see the pipeline fixes")
    if not stamps:
        print(f"  {DUNNO} no draft to read a stamp from")
    print()
    for f in PIPELINE_FIXES:
        print(f"  {WAIT} {f}")
    print()
    print("To see them: open a review, press ↻ RCA only (or Re-run), and read")
    print("the confidence trail. Every one of the above is a line in it.")

    if missing:
        print(f"\n{missing} client fix(es) are NOT in the served page, on a host "
              f"whose fingerprint matches this tree. That is contradictory — "
              f"check for a CDN or service worker serving a cached index.html.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
