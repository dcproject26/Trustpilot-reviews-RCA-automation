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

    prompt = _prompts.zendesk_timeline_shape_prompt(
        {}, "", "", raw)
    print(f"\n  prompt is {len(prompt):,} chars")
    text = await _claude.shape_timeline_events(prompt)
    shaped = zd._safe_parse_events(text)
    print(f"  model returned {len(text):,} chars -> {len(shaped)} parsed row(s)")
    if not shaped:
        print("\n  PARSED NOTHING. The timeline would fall back to raw bodies.")
        print(f"  first 300 chars of the answer:\n  {_snip(text, 300)}")
        return 0

    print(f"\n=== SHAPED ROWS: {len(shaped)} ===")
    claimed = set()
    for r in shaped:
        idxs = r.get("idx_range") or []
        claimed.update(i for i in idxs if isinstance(i, int))
        keep = "" if r.get("keep", True) else "  <-- keep:false, DROPPED"
        print(f"  idx_range={str(idxs):<18} {_snip(r.get('label'), 46):<46}{keep}")

    missing = [e for e in raw if e.get("idx") not in claimed]
    print(f"\n=== RAW EVENTS NO ROW CLAIMS: {len(missing)} ===")
    if not missing:
        print("  none — every comment is accounted for in some row.")
    for e in missing:
        mark = "INTERNAL" if e.get("is_internal") else "public"
        print(f"  [{e.get('idx'):>3}] {e.get('time','?'):<16} {mark:<9} "
              f"{_snip(e.get('raw_body'))}")

    merged = [r for r in shaped if len(r.get("idx_range") or []) > 1]
    print(f"\n=== ROWS THAT MERGED MORE THAN ONE COMMENT: {len(merged)} ===")
    for r in merged:
        idxs = [i for i in (r.get("idx_range") or []) if isinstance(i, int)]
        kinds = ["INTERNAL" if any(e.get("idx") == i and e.get("is_internal")
                                   for e in raw) else "public" for i in idxs]
        mixed = "  <-- MIXED, rule 3 forbids this" if len(set(kinds)) > 1 else ""
        print(f"  {_snip(r.get('label'), 46):<46} {idxs} {kinds}{mixed}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("booking_id")
    a = ap.parse_args(argv)
    return asyncio.run(_run(a.booking_id))


if __name__ == "__main__":
    sys.exit(main())
