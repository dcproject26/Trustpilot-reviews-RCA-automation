#!/usr/bin/env python3
"""
Audit a regenerated RCA against the writing rules the prompt now enforces.

    python3 tools/audit_rca.py --latest          # most recently generated draft
    python3 tools/audit_rca.py --id tp_17…       # a specific review
    python3 tools/audit_rca.py --latest --dump   # also print the words themselves

Checks, mechanically:
  - every entry inside the word ceiling (25) and none starting with its own
    bullet character
  - no verdict/advice prose (the banned-phrase list from the prompt)
  - takedown is one word - Yes / No / Untraceable - with no blurb keys
  - no prevention key anywhere (removed from the shape)
  - sp_interaction carries raised + records, not the old essay shape

The old-shape detections double as a build check: a re-run that still
produces {recommended, reason} or a prevention list ran on the OLD prompt,
which means the server that served it has not picked up the new code -
that is a deploy/restart problem, not a prompt problem, and this says so.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CEILING = 25
BULLET_RE = re.compile(r"^\s*[•·\-–▪]")
BANNED = [
    "structurally impossible", "meets the threshold", "purely defamatory",
    "the workflow should", "it is worth noting", "it appears that",
    "cannot be assessed", "is itself unverifiable",
    "impossible without", "not possible without", "action possible without",
]

PASS, FAIL, WARN = "PASS", "FAIL", "warn"
_results = []


def _migrate_first():
    """Bring the schema up to the models before querying them.

    These tools are what someone runs BEFORE restarting anything - that is
    the whole point of a diagnostic - so they cannot assume the server has
    already run the migration. Without this, the first command after a pull
    that adds a column dies on "column does not exist" and reads as data
    loss rather than a pending migration. init_db() is idempotent.
    """
    from server.db import init_db
    init_db()


def check(name, ok, detail=""):
    _results.append((PASS if ok else FAIL, name, detail))


def warn(name, ok, detail=""):
    """Style smells: printed, never fatal, never change the verdict."""
    if not ok:
        _results.append((WARN, name, detail))


def _entries(v3) -> list:
    """Every human-readable sentence the rules govern, with its path."""
    out = []

    def add(path, v):
        if isinstance(v, str) and v.strip():
            out.append((path, v.strip()))

    t = v3.get("tldr") or {}
    if isinstance(t, dict):
        add("tldr.our_mistake", t.get("our_mistake"))
        add("tldr.our_fix", t.get("our_fix"))
    w = v3.get("what_went_wrong") or {}
    for i, gi in enumerate(w.get("guest_issues") or []):
        add(f"guest_issues.{i}.issue", gi.get("issue"))
        add(f"guest_issues.{i}.root_cause", gi.get("root_cause"))
        # "claim" is the guest's own words quoted from the review, so the
        # word ceiling and the no-prose rules do not apply to it - it is
        # deliberately not added to the audited entries.
        ev = gi.get("evidence")
        for j, e in enumerate(ev if isinstance(ev, list) else [ev]):
            add(f"guest_issues.{i}.evidence.{j}", e)
    wh = w.get("what_happened") or {}
    for i, rc in enumerate(wh.get("root_causes") or []):
        add(f"root_causes.{i}.cause", rc.get("cause"))
    for key in ("operational_failure", "sop_gap"):
        v = wh.get(key)
        for j, e in enumerate(v if isinstance(v, list) else [v]):
            add(f"{key}.{j}", e)
    add("pattern", wh.get("pattern"))
    sx = w.get("sp_escalation") or {}
    dv = sx.get("detail")
    for j, e in enumerate(dv if isinstance(dv, list) else [dv]):
        add(f"sp_escalation.detail.{j}", e)
    fx = w.get("fixes") or {}
    av = fx.get("actions")
    for j, e in enumerate(av if isinstance(av, list) else [av]):
        add(f"fixes.actions.{j}", e)
    for i, f in enumerate(v3.get("flags") or []):
        add(f"flags.{i}.flag", f.get("flag"))
        add(f"flags.{i}.evidence", f.get("evidence"))
    for i, l in enumerate(v3.get("booking_logs") or []):
        if isinstance(l, dict):
            add(f"booking_logs.{i}.detail", l.get("detail"))
    for i, si in enumerate(v3.get("support_interaction") or []):
        if isinstance(si, dict):
            add(f"support_interaction.{i}.summary", si.get("summary"))
    sop = v3.get("sop_compliance") or {}
    for k in ("expected", "actual", "detail"):
        add(f"sop.{k}", sop.get(k))
    sp = v3.get("sp_interaction")
    if isinstance(sp, dict):
        for i, rec in enumerate(sp.get("records") or []):
            if isinstance(rec, dict):
                add(f"sp.records.{i}.summary", rec.get("summary"))
        # Old-shape prose still gets judged - it is where the worst verdict
        # writing lived.
        add("sp.reason_if_not", sp.get("reason_if_not"))
        dv = sp.get("detail")
        for j, e in enumerate(dv if isinstance(dv, list) else [dv]):
            add(f"sp.detail.{j}", e)
    add("takedown.reason", (v3.get("takedown") or {}).get("reason"))
    for j, a in enumerate(v3.get("area_of_improving") or []):
        add(f"aoi.{j}", a)
    return out


def main():
    _migrate_first()
    ap = argparse.ArgumentParser()
    ap.add_argument("--id")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()

    from server.db import SessionLocal, Review, RcaDraft
    s = SessionLocal()
    try:
        if args.id:
            d = s.query(RcaDraft).filter(RcaDraft.review_id == args.id).first()
        else:
            d = (s.query(RcaDraft).filter(RcaDraft.generated_at.isnot(None))
                 .order_by(RcaDraft.generated_at.desc()).first())
        if not d:
            print("no draft found")
            return 1
        rv = s.query(Review).filter(Review.id == d.review_id).first()
        v3 = d.rca_v3 or {}
        print(f"review  {d.review_id}  ({getattr(rv, 'author', '?')})")
        print(f"drafted {d.generated_at}\n")
        if not v3:
            print("rca_v3 is empty - nothing to audit")
            return 1

        entries = _entries(v3)
        long = [(p, t, len(t.split())) for p, t in entries
                if len(t.split()) > CEILING]
        check(f"all {len(entries)} entries <= {CEILING} words", not long,
              "; ".join(f"{p} ({n}w)" for p, t, n in long[:6]))

        bulleted = [p for p, t in entries if BULLET_RE.match(t)]
        check("no entry starts with its own bullet", not bulleted,
              ", ".join(bulleted[:6]))

        hits = [(p, b) for p, t in entries for b in BANNED if b in t.lower()]
        check("no verdict/advice prose", not hits,
              "; ".join(f"{p}: '{b}'" for p, b in hits[:5]))

        td = v3.get("takedown") or {}
        new_td = set(td.keys()) <= {"verdict"} and \
            td.get("verdict") in ("Yes", "No", "Untraceable")
        check("takedown is one word (Yes/No/Untraceable)", new_td,
              f"got {json.dumps(td)[:90]}")

        fx = (v3.get("what_went_wrong") or {}).get("fixes") or {}
        no_prev = "prevention" not in v3 and "prevention" not in fx
        check("no prevention key anywhere", no_prev)

        sp = v3.get("sp_interaction")
        sp_new = isinstance(sp, dict) and "records" in sp and \
            "possible" not in sp and "reason_if_not" not in sp
        check("sp_interaction is raised + records (new shape)", bool(sp_new),
              f"keys: {sorted(sp.keys()) if isinstance(sp, dict) else type(sp).__name__}")

        # Each issue owns its claim, owner and root cause. A draft with none
        # of them ran on the prompt before that change.
        gis = (v3.get("what_went_wrong") or {}).get("guest_issues") or []
        missing = [f"issue {i+1}: " + ", ".join(
                       k for k in ("claim", "owner", "root_cause") if not g.get(k))
                   for i, g in enumerate(gis)
                   if not all(g.get(k) for k in ("claim", "owner", "root_cause"))]
        check("every issue carries claim + owner + root_cause",
              bool(gis) and not missing, "; ".join(missing[:4]))

        # Rule 12: an absence is stated once, not re-derived per section.
        absence = [p for p, t in entries
                   if re.search(r"no (booking|support contact|guest contact|"
                                r"support ticket|contact record)", t.lower())]
        warn(f"same absence restated in {len(absence)} entries (rule 12: say it once)",
             len(absence) <= 3, ", ".join(absence[:8]))

        compound = [p for p, t in entries if "; " in t]
        warn("compound sentences (two ideas joined by ';' - split them)",
             not compound, ", ".join(compound[:6]))

        fails = [r for r in _results if r[0] == FAIL]
        for st, name, detail in _results:
            print(f"[{st}] {name}")
            if detail and st in (FAIL, WARN):
                print(f"       {detail}")

        old_shape = (not new_td and ("recommended" in td or "reason" in td)) \
            or not no_prev or (isinstance(sp, dict) and "possible" in sp)
        print()
        if old_shape:
            print("VERDICT: this draft came from the OLD prompt. The server that")
            print("generated it has not loaded the new code - restart the dev")
            print("server (or press Deploy for the published app) and re-run.")
        elif fails:
            print(f"VERDICT: new prompt ran, but {len(fails)} rule(s) above were")
            print("violated - that is prompt tuning, paste this output back.")
        else:
            print("VERDICT: all writing rules hold on this draft.")

        if args.dump:
            print("\n--- the words themselves ---")
            for p, t in entries:
                print(f"  {p}: {t}")
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
