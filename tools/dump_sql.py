#!/usr/bin/env python3
"""
Print the insight queries as runnable SQL, with the parameters filled in.

    python3 tools/dump_sql.py --tid 43605 --vid 4040 --tgid 22238 \
                              --anchor 2026-07-22 --l2 "Ticket Issues"
    python3 tools/dump_sql.py ... --only incomplete_tgid
    python3 tools/dump_sql.py ... --list

Needs no BigQuery and no server: it runs get_insights with the executor
replaced by a recorder, so every query is built exactly as production builds
it, and nothing is executed.

The point is to be able to paste one into the BigQuery console. The code sends
named parameters (@tid, @l2v, @anchor), which the console will not resolve, so
each is substituted with the literal the server would have bound - a DATE
literal for a date, an array literal for a repeated parameter, a quoted string
otherwise. The SQL that comes out is what runs, not a paraphrase of it.
"""
import argparse
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# gather() order in get_insights. Kept in step with _RESULT_NAMES there.
NAMES = [
    "reviews", "reviews_total", "support", "support_total",
    "rating_tidvid", "rating_tgid", "bookings", "redemption",
    "completion_vid", "completion_tgid", "incomplete_tgid", "incomplete_vid",
    "same_day_reviews", "same_day_support", "same_day_total",
]

WHAT = {
    "reviews":          "A  negative reviews matching this L2, same TID+VID",
    "reviews_total":    "B  all negative reviews, same TID+VID",
    "support":          "C  support contacts matching this issue's tags",
    "support_total":    "D  all support contacts, same TID+VID",
    "rating_tidvid":    "E1 average rating, same TID+VID",
    "rating_tgid":      "E2 average rating, same TGID",
    "bookings":         "F  total bookings, same TID+VID",
    "redemption":       "G  redemption details from dim_vendor_tours",
    "completion_vid":   "H  booking completion, same VID",
    "completion_tgid":  "H2 booking completion, same TGID",
    "incomplete_tgid":  "   why bookings did not complete, same TGID",
    "incomplete_vid":   "   why bookings did not complete, same VID",
    "same_day_reviews": "I  same visit date, same issue - reviews",
    "same_day_support": "I  same visit date, same issue - support",
    "same_day_total":   "I  same visit date - all bookings",
}


def lit(v):
    """Render a bound parameter as the literal BigQuery would have received."""
    if isinstance(v, tuple) and len(v) == 2:
        kind, val = v
        if isinstance(val, list):
            return "[" + ", ".join(lit(x) for x in val) + "]"
        if kind == "DATE":
            return f"DATE '{val}'"
        return lit(val)
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def substitute(sql, params):
    """Replace @name with its literal, longest name first so @tid != @tgid."""
    for name in sorted(params, key=len, reverse=True):
        sql = re.sub(rf"@{name}\b", lit(params[name]), sql)
    left = sorted(set(re.findall(r"@(\w+)", sql)))
    return sql, left


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tid", default="43605")
    ap.add_argument("--vid", default="4040")
    ap.add_argument("--tgid", default="22238")
    ap.add_argument("--anchor", default="2026-07-22",
                    help="the booking's experience date - the window is "
                         "measured backwards from this, not from today")
    ap.add_argument("--l1", default="Experience Issues")
    ap.add_argument("--l2", default="Ticket Issues")
    ap.add_argument("--window", default="30d")
    ap.add_argument("--only", default="", help="one name from --list")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for n in NAMES:
            print(f"  {n:<18}{WHAT.get(n, '')}")
        return 0

    from server.services import insights as I

    seen = []

    async def _rec(sql, params):
        seen.append((sql, params))
        return []

    orig = (I._run, I.is_live, I.MOCK_MODE)
    I._run, I.is_live, I.MOCK_MODE = _rec, (lambda _: True), False
    try:
        asyncio.run(I.get_insights(
            {"id": "0", "tid": args.tid, "vid": args.vid, "tgid": args.tgid,
             "date_of_visit": args.anchor},
            l1=args.l1, l2=args.l2, window=args.window))
    finally:
        I._run, I.is_live, I.MOCK_MODE = orig

    # Queries are recorded in completion order, not gather order, so match by
    # content rather than by index - an off-by-one here would hand over the
    # wrong SQL, which is worse than handing over none.
    def classify(sql):
        s = sql
        if "shortfall_reason" in s or "completion_type" in s and "GROUP BY" in s:
            return "incomplete_tgid" if "@tgid" in s else "incomplete_vid"
        if "avg_rating" in s:
            return "rating_tgid" if "@tgid" in s else "rating_tidvid"
        if "dim_vendor_tours" in s:
            return "redemption"
        if "completed" in s and "unfulfilled" in s:
            return "completion_tgid" if "@tgid" in s else "completion_vid"
        if "fct_support_queries" in s:
            return "support" if ("@tags" in s or "@pat0" in s) else "support_total"
        if "fct_reviews" in s:
            return "reviews" if "@l2v" in s else "reviews_total"
        return "bookings"

    wanted = args.only.strip()
    printed = 0
    for sql, params in seen:
        name = classify(sql)
        if wanted and name != wanted:
            continue
        out, left = substitute(sql.strip(), params)
        print("-- " + "=" * 68)
        print(f"-- {name}   {WHAT.get(name, '')}")
        print(f"-- window {args.window}, anchored on {args.anchor}; "
              f"l1={args.l1!r} l2={args.l2!r}")
        if left:
            print(f"-- STILL PARAMETERISED: {', '.join('@' + x for x in left)} "
                  "- fill these in before running")
        print("-- " + "=" * 68)
        print(out.strip() + ";\n")
        printed += 1

    if not printed:
        print(f"nothing matched --only {wanted!r}. Names:")
        for n in NAMES:
            print(f"  {n:<18}{WHAT.get(n, '')}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
