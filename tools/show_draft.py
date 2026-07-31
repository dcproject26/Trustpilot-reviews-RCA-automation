#!/usr/bin/env python3
"""Read a draft row as it actually sits in the database.

The v4 checkpoint: run one real review, then look at what the model produced
before anything is built against it. The dashboard is not the place to do that
— it renders the v3 shape, so a v4 field it does not know about looks the same
as a field the model left empty.

    python3 tools/show_draft.py --latest              # the most recent draft
    python3 tools/show_draft.py --review tp_123
    python3 tools/show_draft.py --bid 32908218
    python3 tools/show_draft.py --latest --json       # the raw rca_v3 blob

Without --json it prints an audit: which v4 fields the model filled, which it
left empty, and every value that sits outside its vocabulary. A field the
model never returns is the thing worth finding now rather than after the UI
is built against it.
"""
import argparse
import json
import sys

sys.path.insert(0, ".")

from server.db import SessionLocal, RcaDraft, Review          # noqa: E402
from server.services.rca_v4_validate import (                 # noqa: E402
    CHANNELS, CLAIM_ACCURACY, FLAG_TEAMS, ISA_VERDICT, OWNERS,
    SOP_VERDICT, SOURCES, TAKEDOWN,
)

# field → what the UI needs from it. Ordered as the RCA reads on screen.
TOP_LEVEL = [
    "stated_issue", "tldr", "l1", "l2", "sub_themes", "scenarios",
    "overlay_scenarios", "what_went_wrong", "issue_specific_answers",
    "sop_compliance", "support_interaction_notes", "sp_interaction_notes",
    "booking_logs",
    "flags", "area_of_improving", "resolution", "suggested_response",
    "takedown", "dss",
]

GREEN, RED, YEL, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _filled(v):
    return v not in (None, "", [], {}) and not (
        isinstance(v, dict) and not any(x not in (None, "", [], {}) for x in v.values()))


def _count(v):
    if isinstance(v, list):
        return f"{len(v)} item(s)"
    if isinstance(v, dict):
        return f"{len([k for k, x in v.items() if _filled(x)])}/{len(v)} keys"
    s = str(v).strip()
    return f"{len(s.split())} words"


def _v3_markers(rca, d):
    """Signs the row was written by the pre-v4 prompt.

    A stamped row needs none of this. It exists for the rows written before the
    stamp, where the only way to tell v3 from v4 is the shape - and where every
    enum violation is a v3 artefact rather than a validator failure. Reading one
    as a v4 checkpoint is a wasted re-run at best and a wrong conclusion about
    the prompt at worst.
    """
    m = []
    if isinstance(rca.get("issue_specific_answers"), dict):
        m.append("issue_specific_answers is the v3 {question: answer} map")
    w = rca.get("what_went_wrong") or {}
    for k in ("what_happened", "fixes", "sp_escalation"):
        if k in w:
            m.append(f"what_went_wrong.{k} is a v3 document-level heading")
    for i in ((w.get("guest_issues") or []) if isinstance(w, dict) else []):
        if isinstance(i, dict) and any(isinstance(e, str) for e in (i.get("evidence") or [])):
            m.append("evidence entries are bare strings, not {text, source, ref}")
            break
    # v4 always returns these; absent means the model was never asked for them.
    missing = [k for k in ("l1", "l2", "sub_themes", "stated_issue") if k not in rca]
    if missing:
        m.append(f"v4 always returns {', '.join(missing)} — absent here")
    if getattr(d, "suggested_response", None) and not rca.get("suggested_response"):
        m.append("the reply is in the column but not in the RCA — the standalone "
                 "drafter wrote it, and that call no longer exists")
    return m


def _enum_problems(rca):
    """Values sitting outside their vocabulary. After validation there should
    be none — anything here means the row was written by an older build, or
    the validator did not run on the path that wrote it."""
    bad = []

    def chk(where, value, allowed):
        if value not in (None, "") and value not in allowed:
            bad.append(f"{where} = {value!r} (not in {'|'.join(allowed)})")

    for n, i in enumerate(((rca.get("what_went_wrong") or {}).get("guest_issues") or []), 1):
        if not isinstance(i, dict):
            bad.append(f"guest_issues[{n}] is {type(i).__name__}, not an object")
            continue
        chk(f"guest_issues[{n}].claim_accuracy", i.get("claim_accuracy"), CLAIM_ACCURACY)
        chk(f"guest_issues[{n}].owner", i.get("owner"), OWNERS)
        for m, e in enumerate((i.get("evidence") or []), 1):
            if not isinstance(e, dict):
                bad.append(f"guest_issues[{n}].evidence[{m}] is a bare string, not a row")
                continue
            chk(f"guest_issues[{n}].evidence[{m}].source", e.get("source"), SOURCES)
    isa = rca.get("issue_specific_answers")
    if isinstance(isa, dict):
        bad.append("issue_specific_answers is the v3 {question: answer} map, not an array")
    for n, a in enumerate((isa or []) if isinstance(isa, list) else [], 1):
        chk(f"issue_specific_answers[{n}].verdict", (a or {}).get("verdict"), ISA_VERDICT)
    chk("sop_compliance.verdict", (rca.get("sop_compliance") or {}).get("verdict"), SOP_VERDICT)
    chk("takedown.verdict", (rca.get("takedown") or {}).get("verdict"), TAKEDOWN)
    for n, f in enumerate((rca.get("flags") or []), 1):
        chk(f"flags[{n}].team", (f or {}).get("team"), FLAG_TEAMS)
    _contacts = (rca.get("support_interaction_notes")
                 if rca.get("support_interaction_notes") is not None
                 else rca.get("support_interaction"))
    for n, c in enumerate((_contacts or []), 1):
        chk(f"support_interaction[{n}].channel", (c or {}).get("channel"), CHANNELS)
    return bad


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--review", help="review id")
    g.add_argument("--bid", help="booking id")
    g.add_argument("--latest", action="store_true", help="most recently generated draft")
    ap.add_argument("--json", action="store_true", help="print the raw rca_v3 blob and stop")
    a = ap.parse_args()

    s = SessionLocal()
    try:
        q = s.query(RcaDraft)
        if a.review:
            d = q.filter(RcaDraft.review_id == a.review).first()
        elif a.bid:
            d = next((x for x in q.all()
                      if str((x.booking or {}).get("bookingId") or "") == str(a.bid)), None)
        else:
            d = q.order_by(RcaDraft.generated_at.desc()).first()
        if not d:
            sys.exit("no draft found")

        rca = d.rca_v3 or {}
        if a.json:
            print(json.dumps(rca, indent=2, ensure_ascii=False, default=str))
            return

        r = s.query(Review).filter(Review.id == d.review_id).first()
        print(f"\nreview   {d.review_id}   {(r.author if r else '') or ''}")
        print(f"booking  {(d.booking or {}).get('bookingId') or '—'}   "
              f"tier {d.match_tier or '—'}")
        ver = getattr(d, "rca_prompt_version", None)
        print(f"written  {d.generated_at}   by {ver or '(unstamped — predates the version stamp)'}")
        print(f"class    {d.l1 or '—'} / {d.l2 or '—'}")
        if rca.get("l1_raw"):
            print(f"{RED}         model said {rca['l1_raw']!r}/{rca.get('l2_raw')!r} — "
                  f"not in the taxonomy{OFF}")

        legacy = _v3_markers(rca, d)
        if legacy and ver != "rca_v4":
            print(f"\n{RED}{'━' * 68}{OFF}")
            print(f"{RED}  THIS ROW IS THE OLD v3 SHAPE. It is not a test of v4.{OFF}")
            print(f"{RED}  Everything below will report v3 artefacts as failures, because")
            print(f"  nothing that existed when this row was written could validate it.")
            print(f"  Re-run the review, then read it again.{OFF}")
            for m in legacy:
                print(f"{RED}    · {m}{OFF}")
            print(f"{RED}{'━' * 68}{OFF}")

        print(f"\n{DIM}── what the model returned ──{OFF}")
        for k in TOP_LEVEL:
            v = rca.get(k)
            if k not in rca:
                print(f"  {RED}absent {OFF} {k}")
            elif _filled(v):
                print(f"  {GREEN}filled{OFF} {k:24} {DIM}{_count(v)}{OFF}")
            else:
                print(f"  {YEL}empty {OFF} {k}")
        extra = [k for k in rca if k not in TOP_LEVEL and k not in ("l1_raw", "l2_raw")]
        if extra:
            print(f"  {DIM}also present (not in the v4 template): "
                  f"{', '.join(sorted(extra))}{OFF}")

        print(f"\n{DIM}── the columns the pipeline writes alongside it ──{OFF}")
        for k in ("guest_issues", "sop_compliance", "booking_logs", "flags",
                  "takedown", "dss", "issue_specific_answers",
                  "resolution", "suggested_response"):
            v = getattr(d, k, None)
            mark = f"{GREEN}filled{OFF}" if _filled(v) else f"{YEL}empty {OFF}"
            print(f"  {mark} {k:24} {DIM}{_count(v) if _filled(v) else ''}{OFF}")

        bad = _enum_problems(rca)
        print(f"\n{DIM}── vocabulary ──{OFF}")
        if bad and legacy:
            print(f"  {DIM}{len(bad)} value(s) outside their vocabulary — expected on a "
                  f"v3 row, and not a v4 result:{OFF}")
            for b in bad:
                print(f"    {DIM}{b}{OFF}")
        elif bad:
            print(f"  {RED}{len(bad)} value(s) outside their vocabulary — the validator "
                  f"did not run on whatever wrote this row:{OFF}")
            for b in bad:
                print(f"    {b}")
        else:
            print(f"  {GREEN}every enum is in range{OFF}")

        trail = [t for t in (d.confidence_trail or [])
                 if "<strong>RCA</strong>" in (t.get("text") or "")]
        if trail:
            print(f"\n{DIM}── what validation changed ──{OFF}")
            for t in trail:
                print(f"  {YEL}{t['text'].replace('<strong>RCA</strong> — ', '')}{OFF}")

        issues = (rca.get("what_went_wrong") or {}).get("guest_issues") or []
        print(f"\n{DIM}── guest issues ({len(issues)}) ──{OFF}")
        for n, i in enumerate(issues, 1):
            i = i if isinstance(i, dict) else {}
            print(f"  {n}. {i.get('issue') or '(untitled)'}")
            print(f"     accuracy {i.get('claim_accuracy') or '—':16} "
                  f"owner {i.get('owner') or '—'}")
            for f in ("claim", "root_cause", "operational_failure", "sop_gap",
                      "pattern", "fix"):
                mark = f"{GREEN}·{OFF}" if _filled(i.get(f)) else f"{YEL}○{OFF}"
                print(f"     {mark} {f}")
            print(f"     evidence: {len(i.get('evidence') or [])} row(s)")
        print()
    finally:
        s.close()


if __name__ == "__main__":
    main()
