"""Raw Zendesk events vs the rows the shaper returned, side by side.

`trace_notes.py` proved the internal notes REACH the shaping call. This shows
what the call does with them: which raw comment ended up in which row, and —
the part that matters — which raw comments no row claims at all.

    python3 scripts/trace_shaping.py 32885089

Read-only. It runs the real shaping call, prints the mapping, and writes
nothing.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _snip(s, n=68):
    return " ".join(str(s or "").split())[:n]


async def _run(bid):
    from server.services import zendesk as zd
    from server import prompts as _prompts
    from server.services import claude as _claude

    _z = zd._get_client()
    if _z is None:
        print("Zendesk is not live here — run this where the pipeline runs.")
        return 2

    loop = asyncio.get_running_loop()
    raw, extracted, meta = await loop.run_in_executor(
        None, zd._get_timeline_sync, _z, bid)

    print(f"\n=== RAW EVENTS the shaper is given: {len(raw)} ===")
    for e in raw:
        mark = "INTERNAL" if e.get("is_internal") else "public  "
        print(f"  [{e.get('idx'):>3}] {e.get('time','?'):<16} "
              f"{e.get('actor','?'):<7} {mark}  {_snip(e.get('raw_body'))}")

    # DRIVE `_shape_via_claude`, DO NOT REBUILD IT.
    #
    # This assembled the prompt itself with an empty booking and an empty
    # review date, then parsed the answer itself — so it exercised a path the
    # pipeline does not run. It reported the bookends as "unknown" long after
    # the pipeline had learned to stamp them, because the stamp lives inside
    # `_shape_via_claude` and this never called it. A diagnostic that copies
    # the code it is diagnosing can only ever confirm its own copy.
    from server.db import SessionLocal, RcaDraft
    _s = SessionLocal()
    try:
        _d = next((r for r in _s.query(RcaDraft).all()
                   if str((r.booking or {}).get("id") or "") == str(bid)), None)
        booking = dict(_d.booking or {}) if _d else {}
        review = getattr(_d, "review", None) if _d else None
        pub = ""
        if review is not None and getattr(review, "received_at", None):
            pub = review.received_at.strftime("%Y-%m-%d %H:%M")
        body = (getattr(review, "body_english", "") or
                getattr(review, "body_original", "") or "") if review else ""
    finally:
        _s.close()
    print(f"\n  booking creationDate = {booking.get('creationDate') or booking.get('bookedOn') or '(none)'}")
    print(f"  review published     = {pub or '(none)'}")
    if not booking and not pub:
        print("  NOTE: no draft found for this booking id, so the bookends have")
        print("        nothing to be stamped from — that is this script's own")
        print("        lookup failing, not the pipeline's.")

    shaped = await zd._shape_via_claude(raw, booking, body, pub)
    print(f"  {len(shaped)} shaped row(s)")
    if not shaped:
        print("\n  NOTHING SHAPED. The timeline would fall back to raw bodies.")
        return 0
    if any(r.get("shaping_failed") for r in shaped):
        print("\n  SHAPING FAILED — these rows are the RAW bodies, not summaries.")

    print(f"\n=== SHAPED ROWS: {len(shaped)} ===")
    # NO idx_range HERE, and that is correct. `_shape_via_claude` strips it —
    # it is scaffolding the model uses to point back at raw events, not
    # something the card ever sees. Reporting it would mean rebuilding the
    # call to keep it, which is exactly what made this script report a fixed
    # bug as broken.
    for r in shaped:
        mark = " INTERNAL" if r.get("is_internal") else ""
        print(f"  {str(r.get('time') or '(none)'):<20}{mark:<10} "
              f"{_snip(r.get('label'), 52)}")

    # THE ORDER THE CARD WILL RENDER, and the value it sorts on. The client
    # sorts on the DISPLAYED time (one timezone frame for every row), falling
    # back to time_sort. A row whose `time` the parser cannot read sinks to the
    # end — which looks exactly like an event that happened last.
    import re as _re
    _M = {m: i for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"])}

    def _sv(row):
        t = " ".join(str(row.get("time") or "").split())
        m = _re.match(r"^(\d{1,2})\s+([A-Za-z]{3})[a-z]*(?:\s+(\d{1,2}):(\d{2}))?", t)
        if m and m.group(2).lower() in _M:
            return (_M[m.group(2).lower()], int(m.group(1)),
                    int(m.group(3) or 0), int(m.group(4) or 0))
        return None

    print(f"\n=== ORDER AS THE CARD SORTS IT ===")
    print("  the client sorts on the DISPLAYED time; an unparseable one sinks\n")
    rows = [(r, _sv(r)) for r in shaped]
    unparsed = [r for r, v in rows if v is None]
    ordered = sorted([x for x in rows if x[1] is not None], key=lambda x: x[1])
    prev = None
    for r, v in ordered:
        back = ""
        if prev is not None and v < prev:
            back = "   <-- OUT OF ORDER, earlier than the row above"
        prev = v
        print(f"  {str(r.get('time') or '(none)'):<20} {_snip(r.get('label'), 44)}{back}")
    for r in unparsed:
        print(f"  {str(r.get('time') or '(none)'):<20} {_snip(r.get('label'), 44)}"
              f"   <-- NO READABLE TIME, sinks to the end")

    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("booking_id")
    a = ap.parse_args(argv)
    return asyncio.run(_run(a.booking_id))


if __name__ == "__main__":
    sys.exit(main())
