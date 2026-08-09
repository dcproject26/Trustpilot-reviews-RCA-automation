"""The whole card, end to end: what the model returned, what survived
validation, what the API projects, and what the dashboard will draw.

WHY THIS EXISTS. Every diagnostic so far reads ONE section. That found real
bugs, and it kept missing a whole class: a section that is fine in the data
and never reaches the screen, or reaches it from a key nothing writes. Both
render as an empty block, and an empty block is what a clean case looks like.

Three defects this session were exactly that shape and each was found by
accident rather than by looking:

  evidence[].time    read by the validator, absent from the prompt schema
  what_went_wrong.gaps
                     consumed by the validator, dropped on the way out, then
                     rebuilt from nothing by every card edit
  gaps again         specified in prose, missing from the JSON skeleton

So this walks the CHAIN for one review and says, per section, where the
content stops:

    model → validate → _draft_dict → the client's key → the DOM

IT DRIVES THE REAL PROJECTION. `_draft_dict` is imported and called — the
same function the endpoint returns — so a section that is empty here is empty
on the card. Rebuilding the projection would test the rebuild; that mistake
has cost two sessions already (trace_shaping.py, and trace_findings.py
re-deriving the fold).

THE CLIENT SIDE IS A SOURCE SCAN, and it is a scan on purpose: client-side
JavaScript has no test harness here, so reading index.html is the only way to
learn which keys are wired to a renderer. It is used ONLY to answer "does
anything read this", never to decide whether a section is correct — a static
diff of client keys against projection keys produces false positives (the
legacy `what_happened` block is guarded and labelled, and reads fine), so
that direction is reported as a question, not a verdict.

    python3 scripts/trace_card.py tp_abc123
    python3 scripts/trace_card.py --bid 32885089
    python3 scripts/trace_card.py tp_abc123 --verbose   # show every value

Read-only: reads the stored draft, projects in memory, prints. Nothing
written, no model call, no Zendesk.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLIENT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "client", "index.html")


# ── the manifest ───────────────────────────────────────────────────────────
#
# One row per thing the card draws. `path` is read off the PROJECTION, so it
# is what the endpoint actually returned. `empty_means` is the sentence that
# separates a legitimate empty from a broken one — the whole reason this file
# exists rather than a print of the JSON.
#
# `client` is a marker that must appear in index.html for the section to have
# a renderer at all. Missing means the data is produced and nobody shows it.
SECTIONS = [
    ("stated issue", "stated_issue", "rca.v3",
     "the model wrote no one-line summary"),
    ("§1 case findings", "rca_v3.what_went_wrong.case_findings",
     "case_findings",
     "the case has no recorded events — check trace_findings.py"),
    ("§2 guest issues", "rca_v3.what_went_wrong.guest_issues",
     "what_went_wrong.guest_issues",
     "the review made no separable claims"),
    ("§3 fixes", "rca_v3.what_went_wrong.fixes", "what_went_wrong.fixes",
     "nothing needs doing — rare, and worth reading twice"),
    ("gaps (Actions Taken source)", "rca_v3.what_went_wrong.gaps",
     "actionsTaken",
     "no unsolved gap — see trace_actions.py, which tells the three "
     "empties apart"),
    ("Actions Taken tabs", "actions_taken", "rca.actionsTaken",
     "no gap was routed to any team"),
    ("flags", "flags", "rca.v3",
     "nothing needed raising with a named team"),
    ("events timeline", "timeline", "rca.v3",
     "no events — check trace_timeline.py, a failed shaping call looks "
     "identical"),
    ("support contacts", "support_interaction_frames", "rca.supportFrames",
     "the guest never contacted support"),
    ("support notes (model)", "support_interaction_notes",
     "rca.supportNotes",
     "the model wrote nothing about the contacts"),
    ("SP interaction (model)", "sp_interaction_notes", "rca.spNotes",
     "the model wrote nothing about a supply partner. NOTE the projection's "
     "`sp_interaction` is a DIFFERENT thing — the frames, mapped for render"),
    ("SP records", "sp_interaction_frames", "rca.spFrames",
     "no SP exchange was recorded — legitimate when raised is No or N/A"),
    ("booking logs", "rca_v3.booking_logs", "v3.booking_logs",
     "no booking-system events were read"),
    ("takedown", "rca_v3.takedown", "v3.takedown",
     "no takedown call was made"),
    ("resolution", "resolution", "rca.resolution",
     "no resolution was recorded"),
    # THE REPLY IS READ WHERE THE CLIENT READS IT. `rca.reply` is
    # `final_response || suggested_response`, and `suggested_response` comes
    # through `_v4`, which prefers rca_v3 BY PRESENCE. So a blank here while
    # the column holds text is not a break — it is prompt rule 20 working:
    # the model returns null when no approved macro covers the issue, and
    # falling back to a stale column would put an unapproved reply one Send
    # from a public page. Pointing this row at the column would have called
    # that correct behaviour a bug.
    ("guest response (outgoing)", "english_view.outgoing", "rca.reply",
     "no reply is going out. Check the next row before reading it as broken"),
    ("reply decision (v3)", "rca_v3.suggested_response", "rca.reply",
     "KEY ABSENT here means the model never answered; a key present and "
     "EMPTY is rule 20 — no approved macro covers this issue, which is a "
     "DECISION and not a failure"),
    ("confidence trail", "confidence_trail", "trail",
     "nothing was coerced and nothing was judged — on a real run, unlikely"),
]


def _dig(blob, path):
    """Follow a dotted path, returning (value, reached) — reached is False
    when a key along the way is ABSENT, which is a different fact from a key
    present and empty."""
    cur = blob
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def _size(v):
    """How much CONTENT, not how many container slots.

    `actions_taken` is a dict of five tabs, and a card with nothing routed
    anywhere still has all five keys. `len()` on it returned 5, so a
    completely empty tab strip printed as "5  ok" — this file reporting a
    healthy card at exactly the moment the section had nothing in it, which is
    the defect it was written to catch. A dict whose values are all lists is
    counted by its rows.
    """
    if v is None:
        return 0
    if isinstance(v, dict) and v and all(isinstance(x, list) for x in v.values()):
        return sum(len(x) for x in v.values())
    if isinstance(v, (list, tuple, dict, str)):
        return len(v)
    return 1


def _client_reads(marker: str, src: str) -> bool:
    return marker in src


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("review_id", nargs="?", help="review id (tp_...)")
    ap.add_argument("--bid", help="booking id, if you do not have the review id")
    ap.add_argument("--verbose", action="store_true",
                    help="print the value of every section, not just its size")
    a = ap.parse_args(argv)
    if not a.review_id and not a.bid:
        ap.error("give a review id or --bid")

    from server.db import SessionLocal, RcaDraft
    from server.api import _draft_dict
    from server.prompts import prompt_stamp_state, RCA_PROMPT_VERSION

    s = SessionLocal()
    try:
        q = s.query(RcaDraft)
        d = (q.filter(RcaDraft.review_id == a.review_id).first() if a.review_id
             else next((r for r in q.all()
                        if str((r.booking or {}).get("id") or "") == str(a.bid)),
                       None))
        if not d:
            ids = [r.review_id for r in q.limit(8).all()]
            print("No draft found for that id.")
            if ids:
                print("Drafts that are here: " + ", ".join(ids))
            return 1

        state = prompt_stamp_state(d.rca_prompt_version)
        if state != "current":
            print("\n" + "=" * 74)
            print("  !!! THIS DRAFT WAS NOT WRITTEN BY THE RUNNING PROMPT !!!")
            print(f"  stamped {d.rca_prompt_version or '(none)'}")
            print(f"  running {RCA_PROMPT_VERSION}")
            print("  Every EMPTY below means 'not asked', not 'nothing found'.")
            print("  REGENERATE, then run this again.")
            print("=" * 74)

        # THE REAL PROJECTION, not a rebuild. This is the dict the endpoint
        # returns and the client consumes.
        proj = _draft_dict(d)
        try:
            client_src = open(CLIENT, encoding="utf-8").read()
        except OSError:
            client_src = ""
            print("\n(client/index.html not readable — the renderer column "
                  "will say UNKNOWN rather than guess)")

        print(f"\n=== THE CARD FOR {d.review_id} ===")
        print("  section                        data    renderer   verdict")
        print("  " + "-" * 70)

        empty, broken, orphan = [], [], []
        for label, path, marker, why in SECTIONS:
            val, reached = _dig(proj, path)
            n = _size(val)
            has_renderer = _client_reads(marker, client_src) if client_src else None
            if not reached:
                verdict = "KEY ABSENT"
                broken.append((label, path, why))
            elif n == 0:
                verdict = "empty"
                empty.append((label, why))
            else:
                verdict = "ok"
            if has_renderer is False:
                verdict = "NO RENDERER"
                orphan.append((label, marker))
            rend = {True: "yes", False: "NO", None: "?"}[has_renderer]
            print(f"  {label:<30} {n:>4}    {rend:<9}  {verdict}")

        # ── the three things that must not read alike ──────────────────────
        if broken:
            print("\n=== KEYS THE PROJECTION DOES NOT CARRY ===")
            print("  The client asks for these and gets undefined. On screen "
                  "that is an\n  empty section, which is what a clean case "
                  "looks like.")
            for label, path, why in broken:
                print(f"\n  {label}")
                print(f"    path      {path}")
                print(f"    if legit  {why}")

        if orphan:
            print("\n=== PRODUCED, AND NOTHING READS IT ===")
            print("  A question, not a verdict: this is a text scan of "
                  "index.html, and a\n  renderer can read a key by a name "
                  "this cannot see. Check before acting.")
            for label, marker in orphan:
                print(f"  {label:<30} looked for {marker!r}")

        if empty:
            print("\n=== EMPTY, AND WHAT THAT WOULD MEAN ===")
            print("  Each of these draws nothing. Read the reason before "
                  "reading the blank.")
            for label, why in empty:
                print(f"\n  {label}\n    {why}")

        if not broken and not orphan and not empty:
            print("\n  Every section carries content and has a renderer.")

        # ── the trail, which is where every judgement was recorded ─────────
        trail = proj.get("confidence_trail") or []
        warns = [t for t in trail if isinstance(t, dict)
                 and str(t.get("status") or "").lower() == "warn"]
        print(f"\n=== CONFIDENCE TRAIL: {len(trail)} entries, "
              f"{len(warns)} warn ===")
        print("  A `warn` is where the code CHANGED what the model said, or "
              "could not do\n  something and said so. These are the lines "
              "that explain a thin section.")
        for t in warns:
            print(f"\n  warn  {t.get('step') or t.get('label') or '?'}")
            print(f"        {' '.join(str(t.get('detail') or t.get('note') or '').split())}")
        if not warns:
            print("  No warnings. Nothing was coerced and nothing was "
                  "reported as undone.")

        if a.verbose:
            import json
            print("\n=== EVERY SECTION, IN FULL ===")
            for label, path, _m, _w in SECTIONS:
                val, reached = _dig(proj, path)
                print(f"\n--- {label} ({path}) ---")
                print("  (key absent)" if not reached
                      else json.dumps(val, indent=2, default=str)[:4000])
        else:
            print("\nRun again with --verbose to print every section's value.")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
