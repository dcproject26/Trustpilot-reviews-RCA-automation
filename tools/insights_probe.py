"""
Print Experience Insights for a booking across every window, side by side.

    python3 tools/insights_probe.py 32885787
    python3 tools/insights_probe.py 32885787 "Service Issues" "Guide Issues"

Looks the booking up in BigQuery first, so tid/vid/tgid and the visit date come
from the same place the dashboard gets them - a mismatch there is worth seeing
before you start comparing numbers against Looker.
"""
import asyncio
import sys

WINDOWS = ["7d", "30d", "90d"]


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, dict):
        if "rate" in v:
            r = "-" if v["rate"] is None else f"{v['rate'] * 100:.1f}%"
            b = v.get("rate_by_booking_status")
            b = "-" if b is None else f"{b * 100:.1f}%"
            flag = " !" if v.get("needs_attention") else ""
            # Two Looker views define booking_completion_rate over different
            # columns. Both are computed so a disagreement is visible; when
            # they agree, printing the number twice is just noise.
            rate = r if r == b else f"{r}/{b}"
            return f"{rate} n={v['total']}{flag}"
        if "avg" in v:
            return f"{v['avg']} (n={v['n']})" if v["avg"] is not None else f"- (n={v['n']})"
        if "issues" in v:
            return f"{v['issues']}/{v['total']}"
        return str(v)
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    bid = sys.argv[1]
    l1 = sys.argv[2] if len(sys.argv) > 2 else ""
    l2 = sys.argv[3] if len(sys.argv) > 3 else ""

    from server.services.bigquery_patch import verify_bid
    from server.services.insights import get_insights

    booking = await asyncio.get_running_loop().run_in_executor(None, verify_bid, bid)
    if not booking:
        print(f"BID {bid} not found in BigQuery")
        return 1

    print(f"\nBID {bid}")
    for k in ("tid", "tgid", "vid", "vendorName", "experienceName", "date_of_visit"):
        print(f"  {k:<16} {booking.get(k) or '-'}")
    print(f"  L1/L2            {l1 or '-'} / {l2 or '-'}")

    runs = await asyncio.gather(*[get_insights(booking, l1 or None, l2 or None, w)
                                  for w in WINDOWS])

    rows = [
        ("anchored on",        "_anchored_on"),
        ("issue reviews",      "similar_reviews_30d"),
        ("total neg reviews",  "total_reviews_30d"),
        ("review ratio",       "review_ratio"),
        ("issue queries",      "similar_support_queries_30d"),
        ("total queries",      "total_support_queries_30d"),
        ("support ratio",      "support_ratio"),
        ("total bookings",     "total_bookings_30d"),
        ("rating TGID",        "rating_tgid"),
        ("rating TID.VID",     "rating_tidvid"),
        # ff / booking_status  n=bookings   ! = needs attention
        ("FF rate VID",        "ff_vid"),
        ("FF rate TGID",       "ff_tgid"),
        ("FF same day",        "ff_same_day"),
        ("escalation",         "escalation"),
    ]

    # Width from the widest cell, not a guess. A fixed 18 was fine until the
    # FF cells grew a second rate and started running into each other.
    cells = {(label, i): _fmt(r.get(key))
             for label, key in rows for i, r in enumerate(runs)}
    w = max(len(label) for label, _ in rows) + 2
    cw = max([len(v) for v in cells.values()] + [len(x) for x in WINDOWS]) + 2

    print("\n" + " " * w + "".join(f"{x:>{cw}}" for x in WINDOWS))
    print(" " * w + "-" * (cw * len(WINDOWS)))
    for label, key in rows:
        print(f"{label:<{w}}"
              + "".join(f"{cells[(label, i)]:>{cw}}" for i in range(len(runs))))

    red = runs[1].get("redemption") or {}
    print(f"\nredemption details: {len(red)} fields" if red else "\nredemption details: none")
    for k in ("meeting_point_address", "ticket_redemption_method", "is_cancellable"):
        if k in red:
            print(f"  {k:<26} {str(red[k])[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
