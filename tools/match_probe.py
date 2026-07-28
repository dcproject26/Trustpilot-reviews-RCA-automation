"""
Show which Zendesk tickets each review's indicators actually find.

Extracts indicators, searches Zendesk with them, and prints every ticket found
with the booking facts carried on its own custom fields. No BigQuery — the
booking id is on the ticket, and BQ is only needed once an associate confirms.

Usage:  python3 tools/match_probe.py
"""
import asyncio, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db import SessionLocal, Review
from server.prompts import match_indicator_prompt
from server.services import claude
from server.services.zendesk import (
    _get_client, _search_with_retry, ticket_signals, _name_score,
)


async def indicators_for(r):
    _orig = (r.body_original or "").strip()
    _eng  = (r.body_english or "").strip()
    mt = _eng if not _orig else (
        _eng if _orig in _eng else (f"{_eng}\n{_orig}".strip() if _eng else _orig))
    pub = r.received_at.date().isoformat() if r.received_at else ""
    raw = await claude._call(
        match_indicator_prompt(mt, pub, reviewer_name=r.author or ""), max_tokens=400)
    return claude._extract_json_object(raw) or {}


def _queries(ind, author):
    """Every indicator present contributes a query. Absent ones are skipped."""
    name  = (ind.get("guest_name") or author or "").strip()
    venue = (ind.get("experience_or_venue") or "").strip()
    city  = (ind.get("city_or_country") or "").split(",")[0].strip()
    qs = []
    if name:            qs.append((f'type:ticket requester:"{name}"',  "name (requester)"))
    if name:            qs.append((f'type:ticket {name}',              "name (free text)"))
    if venue:           qs.append((f'type:ticket "{venue}"',           "venue"))
    if name and venue:  qs.append((f'type:ticket {name} {venue}',      "name + venue"))
    if name and city:   qs.append((f'type:ticket {name} {city}',       "name + city"))
    return qs


async def main():
    z = _get_client()
    if z is None:
        print("Zendesk not live — check credentials / MOCK_MODE")
        return
    db = SessionLocal()
    loop = asyncio.get_running_loop()

    for r in db.query(Review).order_by(Review.received_at.desc()).all():
        ind = await indicators_for(r)
        print("=" * 78)
        print(f"{r.author}")
        print(f"  indicators: name={ind.get('guest_name')!r} "
              f"venue={ind.get('experience_or_venue')!r} "
              f"city={ind.get('city_or_country')!r} pax={ind.get('pax')!r}")

        seen, rows = set(), []
        for q, label in _queries(ind, r.author or ""):
            try:
                hits = await loop.run_in_executor(None, lambda qq=q: _search_with_retry(z, qq))
            except Exception as e:
                print(f"    ! {label}: {e}")
                continue
            for t in (hits or [])[:15]:
                tid = str(getattr(t, "id", ""))
                if tid in seen:
                    continue
                seen.add(tid)
                sig = ticket_signals(t)
                if not sig.get("booking_id"):
                    continue
                rows.append((sig, label,
                             _name_score(sig.get("guest_name", ""),
                                         *( (r.author or "").split()[:1] + [(r.author or "").split()[-1]]
                                            if len((r.author or "").split()) > 1
                                            else [(r.author or ""), None] ))))
        if not rows:
            print("    NO TICKETS FOUND -> untraceable")
            continue
        print(f"    {len(rows)} booking(s) found:")
        for sig, label, ns in rows:
            print(f"      BID {sig['booking_id']:<12} name={ns:.2f} via={label}")
            print(f"          guest : {sig.get('guest_name') or '—'}")
            print(f"          exp   : {sig.get('experience') or '—'}")
            print(f"          city  : {sig.get('city') or '—'}   visit: {sig.get('visit_date') or '—'}"
                  f"   pax: {sig.get('pax') or '—'}")


if __name__ == "__main__":
    asyncio.run(main())
