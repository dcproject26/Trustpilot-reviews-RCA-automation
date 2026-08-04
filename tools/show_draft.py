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
    python3 tools/show_draft.py --bid 32908218 --detail   # audit + the JSON worth reading
    python3 tools/show_draft.py --bid 32908218 --issue 2  # one guest issue in full

Without --json it prints an audit: which v4 fields the model filled, which it
left empty, and every value that sits outside its vocabulary. A field the
model never returns is the thing worth finding now rather than after the UI
is built against it.
"""
import argparse
import html as _html
import json
import re as _re
import sys

sys.path.insert(0, ".")

from server.db import SessionLocal, RcaDraft, Review          # noqa: E402
from server.prompts import RCA_PROMPT_FAMILY, RCA_PROMPT_VERSION  # noqa: E402
from server.services.rca_v4_validate import (                 # noqa: E402
    CONTACT_CHANNELS, CLAIM_ACCURACY, FLAG_TEAMS, ISA_VERDICT, OWNERS,
    SOURCES, TAKEDOWN,
)

# field → what the UI needs from it. Ordered as the RCA reads on screen.
TOP_LEVEL = [
    "stated_issue", "l1", "l2", "sub_themes", "scenarios",
    "overlay_scenarios", "what_went_wrong", "issue_specific_answers",
    "support_interaction_notes", "sp_interaction_notes",
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


def _bid(d) -> str:
    """The booking id on a draft.

    The warehouse writes it as `id`; nothing writes `bookingId`, which is what
    this tool looked for. --bid could therefore never match anything, and it
    said "no draft found" - the same answer it gives for a review that really
    has no draft. Both keys are read now because a stored booking is whatever
    the row happens to hold, not whatever the current code writes.
    """
    b = d.booking or {}
    for k in ("id", "bookingId", "booking_id"):
        if b.get(k):
            return str(b[k])
    return ""


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
    chk("takedown.verdict", (rca.get("takedown") or {}).get("verdict"), TAKEDOWN)
    for n, f in enumerate((rca.get("flags") or []), 1):
        chk(f"flags[{n}].team", (f or {}).get("team"), FLAG_TEAMS)
    _contacts = (rca.get("support_interaction_notes")
                 if rca.get("support_interaction_notes") is not None
                 else rca.get("support_interaction"))
    for n, c in enumerate((_contacts or []), 1):
        chk(f"support_interaction[{n}].channel", (c or {}).get("channel"),
            CONTACT_CHANNELS)
    return bad


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--review", help="review id")
    g.add_argument("--bid", help="booking id")
    g.add_argument("--latest", action="store_true", help="most recently generated draft")
    ap.add_argument("--json", action="store_true", help="print the raw rca_v3 blob and stop")
    ap.add_argument("--detail", action="store_true",
                    help="the audit, then the parts worth reading in full: each guest "
                         "issue's JSON, dss, sp_interaction_notes, and the trail")
    ap.add_argument("--issue", type=int, metavar="N",
                    help="print only guest issue N in full (implies --detail)")
    a = ap.parse_args()

    s = SessionLocal()
    try:
        q = s.query(RcaDraft)
        if a.review:
            d = q.filter(RcaDraft.review_id == a.review).first()
        elif a.bid:
            rows = q.all()
            d = next((x for x in rows if _bid(x) == str(a.bid)), None)
            if not d:
                # Naming what was searched. A bare "no draft found" is the same
                # answer for "this BID has no draft" and "the lookup is broken",
                # and this tool has already been the second one.
                known = sorted({_bid(x) for x in rows if _bid(x)})
                # The BID may be on the review even when matching left no
                # booking on the draft, so a near miss is worth pointing at.
                by_ref = s.query(Review).filter(
                    Review.reference_number == str(a.bid)).first()
                msg = [f"no draft has booking {a.bid}",
                       f"{len(rows)} draft(s) in this database, "
                       f"{len(known)} with a booking id"]
                if by_ref:
                    msg.append(f"review {by_ref.id} carries that reference number "
                               f"but its draft has no booking — try "
                               f"--review {by_ref.id}")
                elif known:
                    msg.append("known booking ids: " + ", ".join(known[:10])
                               + (" …" if len(known) > 10 else ""))
                sys.exit("\n".join(msg))
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
        print(f"booking  {_bid(d) or '—'}   "
              f"tier {d.match_tier or '—'}")
        ver = getattr(d, "rca_prompt_version", None)
        print(f"written  {d.generated_at}   by {ver or '(unstamped — predates the version stamp)'}")
        # The stamp is content-addressed. A row written by an older prompt body
        # is the difference between "the new clause did not work" and "this row
        # predates the clause", and reading timestamps against deploy times is
        # not an answer.
        if ver and ver != RCA_PROMPT_VERSION:
            print(f"{YEL}         the prompt has changed since this row was "
                  f"written (now {RCA_PROMPT_VERSION}) — re-run before judging "
                  f"any rule added since{OFF}")
        print(f"class    {d.l1 or '—'} / {d.l2 or '—'}")
        if rca.get("l1_raw"):
            print(f"{RED}         model said {rca['l1_raw']!r}/{rca.get('l2_raw')!r} — "
                  f"not in the taxonomy{OFF}")

        legacy = _v3_markers(rca, d)
        if legacy and not str(ver or "").startswith(RCA_PROMPT_FAMILY):
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
        for k in ("guest_issues", "booking_logs", "flags",
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
                print(f"  {YEL}{_html.unescape(t['text'].replace('<strong>RCA</strong> — ', ''))}{OFF}")

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
            # A claim-less issue is only legitimate on a routed-scenario
            # coverage row. Otherwise it renders as a numbered guest complaint
            # with an empty Claim block, and reads as something the guest said.
            if not _filled(i.get("claim")):
                print(f"     {RED}no claim — is this a guest issue, or our own "
                      f"process finding that belongs in flags?{OFF}")
        print()

        if not (a.detail or a.issue):
            print(f"{DIM}  --detail prints these in full, with dss, "
                  f"sp_interaction_notes and the trail{OFF}\n")
            return

        def _dump(label, obj):
            print(f"\n{DIM}── {label} ──{OFF}")
            print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))

        want = [a.issue] if a.issue else range(1, len(issues) + 1)
        for n in want:
            if 1 <= n <= len(issues):
                _dump(f"guest issue {n}", issues[n - 1])
            else:
                print(f"{RED}no guest issue {n} (there are {len(issues)}){OFF}")
        if not a.issue:
            _dump("flags", rca.get("flags"))
            _dump("dss", rca.get("dss"))
            _dump("sp_interaction_notes", rca.get("sp_interaction_notes"))
            _dump("support_interaction_notes", rca.get("support_interaction_notes"))
            print(f"\n{DIM}── confidence trail ──{OFF}")
            for t in (d.confidence_trail or []):
                mark = {"pass": "ok  ", "warn": "WARN", "fail": "FAIL"}.get(
                    t.get("mark"), "?   ")
                # Escaped for the dashboard, which renders it as HTML.
                # Printed raw it reads "claim_accuracy &#x27;Unknown&#x27;".
                txt = _html.unescape(_re.sub(r"<[^>]+>", "", t.get("text") or ""))
                col = RED if t.get("mark") == "fail" else (
                    YEL if t.get("mark") == "warn" else DIM)
                print(f"  {col}{mark}{OFF} {txt}")
            print(f"\n{DIM}── lengths (the two with ceilings) ──{OFF}")
            for k, cap in (("stated_issue", 60), ("suggested_response", 120)):
                words = len(str(rca.get(k) or "").split())
                col = RED if words > cap else GREEN
                print(f"  {col}{words:4d}{OFF} / {cap} words   {k}")
        print()
    finally:
        s.close()


if __name__ == "__main__":
    main()
