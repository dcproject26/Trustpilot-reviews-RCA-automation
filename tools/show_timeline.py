#!/usr/bin/env python3
"""The booking timeline and Zendesk tickets for one review, in the shell.

    python3 tools/show_timeline.py "Lewis MacAndrew"
    python3 tools/show_timeline.py --bid 32994590
    python3 tools/show_timeline.py --review tp_1785414103_572109
    python3 tools/show_timeline.py "Lewis" --raw     # the ticket bodies too

A name is enough. It matches on any part of the author, case-insensitively,
and if more than one review matches it lists them rather than picking one — a
tool that silently chooses between two guests is worse than one that asks.

Everything here is READ from the stored draft. Nothing is re-fetched, so this
shows what the pipeline actually saved, which is the thing worth checking when
a section on the card looks wrong.

The distinction this tool exists to make: an empty section on the card can mean
the lookup ran and found nothing, or that it never ran, or that it ran and the
join failed. Those are three different bugs and one blank box. Every "empty"
printed below says which of the three it is.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DIM, OFF, B, Y = "\033[2m", "\033[0m", "\033[1m", "\033[33m"


def find(session, db, name=None, review=None, bid=None):
    """Reviews matching one of the three ways of naming one.

    Returns (rows, all_authors). The second is only used to say what WAS there
    when nothing matched — "no review matches 'Lewis'" and "this database is
    empty" are different facts and the caller should not have to guess.
    """
    q = session.query(db.Review)
    everyone = q.all()
    if review:
        rows = [r for r in everyone if r.id == review]
    elif name:
        rows = [r for r in everyone if name.lower() in (r.author or "").lower()]
    else:
        rows = [r for r in everyone
                if str(((r.draft.booking if r.draft else None) or {}).get("id")
                       or "") == str(bid)]
    return rows, sorted({r.author for r in everyone if r.author})


def booking_log_lines(rca_v3, stored_logs):
    """The model's booking timeline, or which kind of nothing it is.

    Presence, not truthiness: `"booking_logs" in rca_v3` is the only way to
    tell a model that returned an empty list from a draft written before the
    field existed. Both render as no rows, and only one is a bug.
    """
    rca_v3 = rca_v3 if isinstance(rca_v3, dict) else {}
    if "booking_logs" in rca_v3:
        logs, where = rca_v3["booking_logs"] or [], "rca_v3"
    elif stored_logs is not None:
        logs, where = stored_logs or [], "the booking_logs column"
    else:
        return ["  no booking_logs anywhere on this draft — the field was "
                "never written, so this is a run that did not reach it, not a "
                "booking with no history"]
    if not logs:
        return [f"  empty — {where} holds a list with nothing in it, so the "
                f"model was asked and returned no events"]

    out = []
    for l in logs:
        if isinstance(l, str):
            out.append(f"  {'—':>16}  {l}")
            continue
        t = str(l.get("time") or "").strip()
        # "undated" is the model complying with rule 10b, not a missing value.
        mark = f"{Y}{t}{OFF}" if t.lower() == "undated" else (t or "—")
        out.append(f"  {mark:>16}  {l.get('what') or ''}")
        if l.get("detail"):
            out.append(f"  {'':>16}  {DIM}{l['detail']}{OFF}")
    return out


def timeline_lines(ticket_ids, events):
    """The Zendesk events, or which kind of nothing.

    No tickets and no events is a lookup that correctly found nothing. Tickets
    but no events is a join that broke — the same blank box, a different bug,
    and the one worth waking up for.
    """
    ids, ev = list(ticket_ids or []), list(events or [])
    if not ids and not ev:
        return ["  no tickets linked to this booking, so there was nothing to "
                "build a timeline from — an empty result, not a failure"]
    if ids and not ev:
        return [f"  {len(ids)} ticket(s) linked ("
                + ", ".join(f"ZD-{i}" for i in ids) +
                ") but NO events were built from them — the tickets were found "
                "and the timeline step did not turn them into anything"]

    out = []
    seen = set()
    for e in ev:
        t = str(e.get("time") or "—").replace(" IST", "")
        flag = f" {Y}[internal]{OFF}" if e.get("is_internal") else ""
        tid = e.get("ticket_id")
        if tid:
            seen.add(str(tid))
        zd = f"  ZD-{tid}" if tid else ""
        out.append(f"  {t:>16}  {str(e.get('actor', '')):9s} "
                   f"{e.get('label', '')}{flag}{zd}")
        if e.get("summary"):
            out.append(f"  {'':>16}  {DIM}{e['summary']}{OFF}")
    missing = [i for i in ids if str(i) not in seen]
    if missing:
        out.append(f"  {Y}{len(missing)} linked ticket(s) contributed no "
                   f"events{OFF}: " + ", ".join(f"ZD-{i}" for i in missing))
    return out


def note_lines(notes, ticket_ids):
    """The model's reading of the support contacts, with the joins it missed.

    A note with no zd_ref is the model following the rule that says do not
    invent one. A note whose zd_ref matches no linked ticket is a broken join.
    Rendering both as plain rows makes a healthy run look faulty and a faulty
    one look fine.
    """
    notes = list(notes or [])
    if not notes:
        return []
    known = {str(i) for i in (ticket_ids or [])}
    out, unmatched = [], 0
    for n in notes:
        ref = str(n.get("zd_ref") or "").strip()
        digits = "".join(c for c in ref if c.isdigit())
        if not ref:
            shown = "(no ZD ref)"
        elif digits and digits in known:
            shown = ref
        else:
            shown = f"{Y}{ref} ✗{OFF}"
            unmatched += 1
        out.append(f"  {shown:<14} {n.get('summary') or ''}")
        if n.get("ce_miss"):
            out.append(f"  {'':<14} {Y}CE miss:{OFF} {n['ce_miss']}")
    if unmatched:
        out.append(f"  {unmatched} note(s) name a ticket that is not linked to "
                   f"this booking — shown above with ✗, not dropped")
    return out


def head(t):
    print(f"\n{B}── {t} ──{OFF}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", help="part of the review author's name")
    ap.add_argument("--review", help="review id")
    ap.add_argument("--bid", help="booking id")
    ap.add_argument("--raw", action="store_true",
                    help="also print the raw Zendesk ticket bodies")
    a = ap.parse_args(argv)
    if not (a.name or a.review or a.bid):
        ap.error("give a name, --review or --bid")

    import server.db as db
    s = db.SessionLocal()
    try:
        rows, everyone = find(s, db, a.name, a.review, a.bid)

        if not rows:
            what = a.review or a.bid or a.name
            print(f"no review matches {what!r}")
            if not everyone:
                print("this database holds no reviews at all — which is a "
                      "different problem from this one not being in it")
            else:
                print(f"{len(everyone)} author(s) here: "
                      + ", ".join(everyone[:12])
                      + (" ..." if len(everyone) > 12 else ""))
            return 1
        if len(rows) > 1:
            print(f"{len(rows)} reviews match {a.name!r} — name one:")
            for r in rows:
                print(f"  --review {r.id}    {r.author}   {r.received_at}")
            return 1

        r = rows[0]
        d = r.draft
        bk = (d.booking if d else None) or {}
        print(f"{B}{r.author}{OFF}   {r.id}")
        print(f"booking  {bk.get('id') or '—'}   tier {d.match_tier if d else '—'}")
        print(f"{DIM}{(r.body_english or r.body_original or '')[:300]}{OFF}")

        if not d:
            print("\nno draft row — this review has never been processed")
            return 1

        head("booking timeline (booking_logs)")
        for line in booking_log_lines(d.rca_v3, getattr(d, "booking_logs", None)):
            print(line)

        head("zendesk tickets")
        ids = d.zendesk_ticket_ids or []
        print("  " + (", ".join(f"ZD-{i}" for i in ids) if ids
                      else "none linked to this booking"))

        head("zendesk events (timeline)")
        for line in timeline_lines(ids, d.timeline):
            print(line)

        notes = (d.rca_v3 or {}).get("support_interaction_notes") or []
        if notes:
            head("support contact notes (the model's reading)")
            for line in note_lines(notes, ids):
                print(line)

        if a.raw:
            head("raw ticket bodies")
            raw = d.timeline_raw or []
            if not raw:
                print("  none stored")
            print(json.dumps(raw, indent=2, default=str)[:8000])
        elif d.timeline_raw:
            print(f"\n{DIM}--raw prints the {len(d.timeline_raw)} stored ticket "
                  f"bodies{OFF}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
