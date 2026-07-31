#!/usr/bin/env python3
"""
Re-run booking matching for one review and show every step.

    python3 tools/rematch.py --author Sven          # dry run, writes nothing
    python3 tools/rematch.py --author Sven --write  # keep the result

    python3 tools/rematch.py --review tp_1785270133_645899
    python3 tools/rematch.py --list

Why this exists: every review in the database was matched by an older build,
so its stored result says nothing about whether matching works now. Checking a
single service in isolation does not answer it either - the question is what
the whole cascade does with this review, in order, and where it stops.

This runs extraction with the current prompt and then walks the paths the
pipeline walks, printing what each one searched for and what it returned. It
writes nothing unless --write is passed.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BAR = "─" * 74


def _since_for(rv):
    """The date floor the pipeline uses, so this searches what it searches."""
    from datetime import datetime, timedelta
    from server.pipeline import SHORTLIST_LOOKBACK_DAYS
    base = (rv.received_at or datetime.utcnow()).date()
    return (base - timedelta(days=SHORTLIST_LOOKBACK_DAYS)).isoformat()


def _head(t):
    print(f"\n{BAR}\n{t}\n{BAR}")


async def run(review_id: str, write: bool):
    from server.db import SessionLocal, init_db, Review, RcaDraft
    from server.services import claude as cl, zendesk as zd, bigquery as bq
    from server.prompts import match_indicator_prompt
    from server.config import is_live

    init_db()
    db = SessionLocal()
    try:
        rv = db.query(Review).filter(Review.id == review_id).first()
        if not rv:
            print(f"no review {review_id}")
            return 1

        body = (rv.body_english or rv.body_original or "")
        orig = (rv.body_original or "")
        text = body if orig in body else f"{body}\n{orig}".strip()

        _head(f"{rv.id}  ·  {rv.author or '—'}  ·  {rv.received_at}")
        print(text[:600])

        # What is on the draft right now, so the re-run can be compared with
        # it. A match appearing here does not mean anything changed - the
        # review may always have matched and failed for another reason.
        d0 = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
        _head("0. what is stored on this review today")
        if not d0:
            print("  no draft — this review has never been processed.")
        else:
            stored = list(d0.candidates_list or [])
            print(f"  bucket           {d0.match_tier and f'tier {d0.match_tier}' or '—'}"
                  f"  ·  path {d0.match_method or '—'}")
            print(f"  candidates       {len(stored)}"
                  + (f"  ({', '.join(str(c.get('id')) for c in stored[:5])})"
                     if stored else ""))
            print(f"  confirmed BID    {d0.selected_candidate_bid or '—'}")
            print(f"  booking          {(d0.booking or {}).get('id') or '—'}")
            if not stored and not (d0.booking or {}).get("id"):
                print("  → this review is currently unmatched.")

        # ── 1. extraction ────────────────────────────────────────────────────
        _head("1. what the review says, as the current prompt reads it")
        pub = rv.received_at.date().isoformat() if rv.received_at else ""
        raw = await cl._call(match_indicator_prompt(text, pub,
                                                    reviewer_name=rv.author or ""),
                             max_tokens=400)
        ind = cl._extract_json_object(raw) or {}
        if not ind:
            print(f"extraction failed:\n{raw[:400]}")
            return 1
        for k, v in ind.items():
            print(f"  {k:<22} {v!r}")

        first = (rv.author or "").split()[0] if (rv.author or "").split() else ""
        last  = (rv.author or "").split()[-1] if len((rv.author or "").split()) > 1 else ""

        # ── 2. Zendesk, the way the pipeline calls it ────────────────────────
        _head("2. Zendesk — the indicator shortlist (the primary path)")
        if not is_live("zendesk"):
            print("  Zendesk is not live here; skipping.")
            short = []
        else:
            notes = []
            short = await zd.shortlist(ind, first, last,
                                       since=_since_for(rv), notes=notes)
            for n in notes:
                if n["kind"] == "truncated":
                    print(f"  [!] the {n['label']} search hit Zendesk's result "
                          f"cap — results below are incomplete")
                    print(f"      {n['detail']}")
                elif n["kind"] == "failed":
                    print(f"  [!] the {n['label']} search FAILED: {n['detail']}")
            if short:
                print(f"  {len(short)} candidate(s):\n")
                for s in short:
                    print(f"    BID {s.get('booking_id'):<12} "
                          f"{(s.get('guest_name') or '—')[:22]:22} "
                          f"{'weak' if s.get('weak') else 'match':5}  "
                          f"{', '.join(s.get('matched_on') or [])}")
                    # The visit date is what separates two bookings by the same
                    # guest — which is exactly the case this review produces.
                    print(f"        visit {s.get('visit_date') or '—':<12}"
                          f"ticket {s.get('ticket_id')} via {s.get('found_via')}")
                    print(f"        {s.get('experience') or '—'}")
            else:
                print("  nothing.")

        # ── 3. the support-anchored fallback ─────────────────────────────────
        _head("3. BigQuery — bookings whose guest contacted support (fallback)")
        if short:
            print("  not reached: Zendesk already produced candidates.")
        elif not is_live("bigquery"):
            print("  BigQuery is not live here; skipping.")
        else:
            # The pipeline resolves the guest's words into TGIDs before this
            # step; without them the search correctly declines to run, so the
            # tool has to do the same resolution or it would always report
            # nothing and look like a broken path.
            from server.services import venue_resolver
            hints = [h for h in (ind.get("experience_or_venue"),
                                 ind.get("city_or_country")) if h and str(h).strip()]
            tgids = []
            if hints:
                try:
                    tgids = await venue_resolver.resolve(hints) or []
                except Exception as e:
                    print(f"  venue resolution failed: {e}")
            print(f"  venue hints {hints or '—'} -> {len(tgids)} TGID(s)")
            sup = await bq.find_via_support(ind, tgids=tgids)
            if sup:
                for s in sup:
                    print(f"    BID {s.get('id'):<12} {(s.get('guestName') or '—')[:22]:22} "
                          f"visit {s.get('visitDate') or '—'}  "
                          f"{s.get('contact_count', 0)}x  {s.get('contact_tags', '')[:40]}")
                    print(f"        {s.get('experienceName') or '—'}")
            else:
                print("  nothing.")
            short = short or []

        # ── verdict ──────────────────────────────────────────────────────────
        _head("outcome")
        if short:
            print(f"  {len(short)} booking(s) to choose from — this review would "
                  f"land in Confirm, not Untraceable.")
        else:
            print("  Untraceable. Nothing in Zendesk or BigQuery matched what "
                  "this review says.")

        if write:
            d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
            if d:
                sigs = dict(d.extracted_signals or {})
                sigs["match_indicators"] = ind
                d.extracted_signals = sigs
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(d, "extracted_signals")
                db.commit()
                print("\n  [written] the re-extracted indicators are now stored "
                      "on this draft.")
        else:
            print("\n  (dry run — nothing written. Pass --write to keep the "
                  "re-extracted indicators.)")
        return 0
    finally:
        db.close()


async def batch(limit: int):
    """Re-run the indicator shortlist across stored reviews and report changes.

    Scope matters and getting it wrong produces a frightening, meaningless
    number. This re-runs ONE step - the Zendesk indicator shortlist. The stored
    result may have come from any step of the cascade: the shortlist, the
    Zendesk requester lookup after it, or the BigQuery venue+date paths after
    that. Comparing a full-cascade result against a shortlist-only re-run
    reports every review matched by a later path as "lost", which is how an
    earlier version of this reported five losses that had not happened.

    So only reviews whose stored match came from the shortlist are compared.
    Everything else is listed as not comparable, with the path that found it.

    Writes nothing.
    """
    from server.db import SessionLocal, init_db, Review, RcaDraft
    from server.services import claude as cl, zendesk as zd, bigquery as bq
    from server.prompts import match_indicator_prompt
    from server.config import is_live

    init_db()
    db = SessionLocal()
    rows = []
    try:
        reviews = (db.query(Review).order_by(Review.received_at.desc())
                   .limit(limit).all())
        print(f"re-running {len(reviews)} review(s). Nothing is written.\n")
        print(f"  {'review':<24} {'author':<16} {'now':>5}  {'after':>5}  what changes")
        print("  " + "─" * 84)

        skipped, truncations = [], []
        for rv in reviews:
            d = db.query(RcaDraft).filter(RcaDraft.review_id == rv.id).first()
            stored = len(list(d.candidates_list or [])) if d else 0
            has_booking = bool((d.booking or {}).get("id")) if d else False
            now_n = 1 if has_booking else stored

            # Only compare like with like. A review matched by the requester
            # lookup or by BQ venue+date is not evidence about the shortlist.
            path = (d.match_method or "") if d else ""
            if now_n and path != "indicator_shortlist":
                skipped.append((rv.id, rv.author or "—", path or "unknown path"))
                continue

            body = (rv.body_english or rv.body_original or "")
            orig = (rv.body_original or "")
            text = body if orig in body else f"{body}\n{orig}".strip()
            pub = rv.received_at.date().isoformat() if rv.received_at else ""
            try:
                raw = await cl._call(
                    match_indicator_prompt(text, pub, reviewer_name=rv.author or ""),
                    max_tokens=400)
                ind = cl._extract_json_object(raw) or {}
            except Exception as e:
                print(f"  {rv.id:<24} {(rv.author or '—')[:16]:<16} "
                      f"extraction failed: {str(e)[:30]}")
                continue

            parts = (rv.author or "").split()
            first = parts[0] if parts else ""
            last  = parts[-1] if len(parts) > 1 else ""
            after = []
            if is_live("zendesk"):
                try:
                    notes = []
                    after = await zd.shortlist(ind, first, last,
                                               since=_since_for(rv), notes=notes)
                    for n in notes:
                        truncations.append((rv.id, n["kind"], n["label"]))
                except Exception as e:
                    print(f"  {rv.id:<24} shortlist failed: {str(e)[:40]}")
                    continue
            # No BigQuery fallback here on purpose: it needs the resolved TGIDs
            # the pipeline computes, which this does not, so calling it would
            # only ever add a zero and make the comparison look worse.

            after_n = len(after)
            if now_n == 0 and after_n > 0:
                verdict = f"NEWLY MATCHED — {after_n} candidate(s)"
            elif now_n > 0 and after_n == 0:
                verdict = "LOST — matches today, would not after"
            elif now_n != after_n:
                verdict = f"{now_n} → {after_n} candidate(s)"
            else:
                verdict = "same"
            rows.append((rv.id, verdict))
            print(f"  {rv.id:<24} {(rv.author or '—')[:16]:<16} "
                  f"{now_n:>5}  {after_n:>5}  {verdict}")

        gained = sum(1 for _, v in rows if v.startswith("NEWLY"))
        lost   = sum(1 for _, v in rows if v.startswith("LOST"))
        moved  = sum(1 for _, v in rows if "→" in v)
        same   = sum(1 for _, v in rows if v == "same")
        print("\n  " + "─" * 84)
        print(f"  compared {len(rows)}: {same} unchanged · {gained} newly matched "
              f"· {moved} different candidate count · {lost} lost")
        if truncations:
            print(f"\n  {len(truncations)} incomplete search(es) — Zendesk "
                  f"returned only part of what matches:")
            for rid, kind, label in truncations[:12]:
                print(f"    {rid:<24} {kind:<10} {label}")
            print("    A review here may have a better booking that was never "
                  "in the results.")
        if skipped:
            print(f"\n  {len(skipped)} not compared — matched by a later step of "
                  f"the cascade, which this does not re-run:")
            for rid, author, path in skipped[:12]:
                print(f"    {rid:<24} {author[:18]:<18} {path}")
            if len(skipped) > 12:
                print(f"    … and {len(skipped) - 12} more")
        if lost:
            print(f"\n  {lost} review(s) matched by the shortlist today would not "
                  f"match now.\n  Reprocessing is not safe until that is "
                  f"understood — run --review on one to see where it goes.")
        elif gained or moved:
            print(f"\n  Reprocessing would change {gained + moved} review(s) and "
                  f"break none of the ones compared.")
        else:
            print("\n  No change among the reviews this can compare.")
        return 0
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--write", action="store_true",
                    help="store the re-extracted indicators on the draft")
    ap.add_argument("--all", action="store_true",
                    help="re-run every stored review and report what would change")
    ap.add_argument("--limit", type=int, default=50,
                    help="how many reviews --all covers (newest first)")
    args = ap.parse_args()

    if args.all:
        return asyncio.run(batch(args.limit))

    from server.db import SessionLocal, init_db, Review
    init_db()

    if args.list or (not args.review and not args.author):
        db = SessionLocal()
        try:
            rows = db.query(Review).order_by(Review.received_at.desc()).limit(30).all()
            print(f"{len(rows)} most recent review(s):\n")
            for rv in rows:
                print(f"  {rv.id:<24} {(rv.author or '—')[:20]:20} "
                      f"{(rv.body_english or rv.body_original or '')[:44]}")
            print("\nRun one:  python3 tools/rematch.py --review <id>")
        finally:
            db.close()
        return 0

    rid = args.review
    if not rid:
        db = SessionLocal()
        try:
            rv = (db.query(Review).filter(Review.author.ilike(f"%{args.author}%"))
                  .order_by(Review.received_at.desc()).first())
            if not rv:
                print(f"no review by an author matching {args.author!r}")
                return 1
            rid = rv.id
        finally:
            db.close()
    return asyncio.run(run(rid, args.write))


if __name__ == "__main__":
    sys.exit(main())
