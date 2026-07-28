"""
Run the implemented shortlist rule against every review and print what it picks.

This exercises the real zendesk.shortlist() the pipeline now uses -- indicator
extraction, indicator-driven Zendesk queries, the AND-filter, the name-only cap
and newest-first ordering. No BigQuery, matching the agreed design.

Usage:  python3 tools/shortlist_probe.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db import SessionLocal, Review
from server.prompts import match_indicator_prompt
from server.services import claude
from server.services.zendesk import shortlist

EXPECTED = {
    "Fredrik Olsen":   "32885787",
    "Ciprian":         "32900044",
    "C. Nauleau":      "32244357",
    "David":           "(has BID in review - should not need the search)",
    "Joe Christopher": "(name only - up to 5 most recent)",
}


def _parse(name):
    p = (name or "").strip().split()
    if not p:
        return None, None
    return (p[0], p[-1]) if len(p) > 1 else (p[0], None)


async def main():
    db = SessionLocal()
    for r in db.query(Review).order_by(Review.received_at.desc()).all():
        _orig = (r.body_original or "").strip()
        _eng  = (r.body_english or "").strip()
        mt = _eng if not _orig else (
            _eng if _orig in _eng else (f"{_eng}\n{_orig}".strip() if _eng else _orig))
        pub = r.received_at.date().isoformat() if r.received_at else ""
        raw = await claude._call(
            match_indicator_prompt(mt, pub, reviewer_name=r.author or ""), max_tokens=400)
        ind = claude._extract_json_object(raw) or {}

        first, last = _parse(ind.get("guest_name") or r.author or "")
        print("=" * 78)
        print(f"{r.author}    (review BID: {r.reference_number or 'none'})")
        print(f"  expected : {EXPECTED.get(r.author, '?')}")
        print(f"  indicators: venue={ind.get('experience_or_venue')!r} "
              f"city={ind.get('city_or_country')!r} pax={ind.get('pax')!r}")
        try:
            rows = await shortlist(ind, first, last)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue
        if not rows:
            print("  -> UNTRACEABLE (no ticket satisfies the indicators)")
            continue
        print(f"  -> {len(rows)} shortlisted:")
        for s in rows:
            print(f"     BID {s['booking_id']:<12} {s.get('guest_name','—')}")
            print(f"         {s.get('experience','—')}")
            print(f"         {s.get('city','—')} · visit {s.get('visit_date','—')} "
                  f"· pax {s.get('pax','—')} · matched_on={s.get('matched_on')}")


if __name__ == "__main__":
    asyncio.run(main())
