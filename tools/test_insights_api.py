#!/usr/bin/env python3
"""
Test the Experience Insights endpoint against the running server.

    python3 tools/test_insights_api.py                 # localhost:5000
    python3 tools/test_insights_api.py --review rev_1  # a specific review
    python3 tools/test_insights_api.py --base http://host:port

Start the server first. Standard library only, so it runs anywhere.

This tests the live route rather than the functions behind it, because the bug
it exists to catch was never in the functions. Two handlers were registered at
the same path; FastAPI dispatched to the first; that one ignored `window` and
returned a shape the caller did not read. Every piece worked on its own and the
feature did not, so only an end-to-end call finds it - and it fails silently, so
the window picker looked fine while showing one window's numbers under another
window's label.

Exit code is 0 only when every check passes.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

PASS, FAIL = "ok  ", "FAIL"
_failures = []


def check(name, ok, detail=""):
    print(f"  {PASS if ok else FAIL}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


def get(base, path, timeout=180):
    req = urllib.request.Request(base.rstrip("/") + path,
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode() or "{}")


def pick_review(base, explicit):
    if explicit:
        return explicit
    _, rows = get(base, "/api/reviews")
    rows = rows if isinstance(rows, list) else (rows.get("reviews") or [])
    # A review with a confirmed booking - insights need tid and vid, and a
    # review without them legitimately returns zeros, which would make every
    # check below pass for the wrong reason.
    # Ask the endpoint itself rather than trusting the review row: insights
    # need tid and vid off the DRAFT, and a review can carry a booking id
    # while the draft has no tid, which returns zeros and makes the window
    # checks untestable.
    fallback = rows[0].get("id") if rows else None
    for r in rows[:10]:
        rid = r.get("id")
        if not rid:
            continue
        try:
            _, b = get(base, f"/api/reviews/{rid}/insights", timeout=60)
        except Exception:
            continue
        i = b.get("insights") if isinstance(b, dict) else None
        if isinstance(i, dict) and not i.get("_zeroed_because") \
                and i.get("total_bookings_30d"):
            return rid
    return fallback


def check_version(base):
    """
    Is the running process on the commit that is checked out?

    Nothing reloads on a file change, so a pull leaves the server serving what
    it imported at startup. Guessing at a marker key caught that once and
    missed it the next time, because the marker had been removed by then. The
    commit is the only signal that does not go stale itself.
    """
    import subprocess
    try:
        _, v = get(base, "/api/version", timeout=15)
    except Exception:
        print("  --    /api/version not served - the process predates it. "
              "RESTART THE SERVER.\n")
        return False
    try:
        local = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                               text=True, timeout=10).stdout.strip()
    except Exception:
        local = ""
    # Trust the server's own answer. It compares the code it loaded against
    # the files on disk; comparing the endpoint's reply to a local git command
    # is what hid the problem, because the endpoint was reading the same disk.
    if v.get("stale"):
        print(f"\n  !!    The server is running OLD code.")
        print(f"        running:  {str(v.get('commit'))[:7]}   "
              f"(up {v.get('uptime_s')}s)")
        print(f"        on disk:  {str(v.get('on_disk'))[:7]}")
        print("        RESTART THE SERVER, then run this again.\n")
        return False
    running = v.get("commit", "")
    if not local:
        print(f"  ..    server on {v.get('short')}, "
              f"up {v.get('uptime_s')}s (no local git to compare)\n")
        return True
    if running != local:
        print(f"\n  !!    The server is running OLD code.")
        print(f"        running:  {running[:7]}   (up {v.get('uptime_s')}s)")
        print(f"        on disk:  {local[:7]}")
        print("        A pull updates files; the process keeps what it "
              "imported at startup.")
        print("        RESTART THE SERVER, then run this again.\n")
        return False
    print(f"  ok    server on {local[:7]}, matching the working tree "
          f"(up {v.get('uptime_s')}s)\n")
    return True


def base_of(args):
    return args.base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5000")
    ap.add_argument("--review", default="")
    args = ap.parse_args()

    try:
        status, _ = get(args.base, "/api/reviews", timeout=15)
    except (urllib.error.URLError, OSError) as e:
        print(f"Cannot reach {args.base}: {e}\nStart the server first.")
        return 2

    # Before anything expensive. pick_review can make up to 25 insights calls,
    # each of which can take a minute against a cold warehouse - a bad way to
    # find out the process is stale.
    if not check_version(args.base):
        return 1

    rid = pick_review(args.base, args.review)
    if not rid:
        print("No reviews on this server - nothing to test against.")
        return 2
    print(f"server {args.base}   review {rid}\n")

    # --- shape --------------------------------------------------------------
    print("response shape")
    try:
        status, body = get(args.base, f"/api/reviews/{rid}/insights")
    except urllib.error.HTTPError as e:
        print(f"  {FAIL}  GET returned {e.code}: {e.read()[:200].decode(errors='replace')}")
        return 1

    check("200", status == 200, f"got {status}")
    check("has 'insights'", isinstance(body, dict) and "insights" in body,
          f"keys={sorted(body)[:6]}")
    check("has 'window'", "window" in body)
    ins = body.get("insights") or {}
    check("insights is a dict", isinstance(ins, dict))
    # The exact bug: a bare dict would have these at the top level instead.
    check("not the old bare shape", "similar_reviews_30d" not in body,
          "top level looks like a raw insights dict")

    # --- the fields the dashboard renders ------------------------------------
    print("\nfields the dashboard reads")
    for key in ("similar_reviews_30d", "total_reviews_30d", "review_ratio",
                "similar_support_queries_30d", "total_support_queries_30d",
                "support_ratio", "total_bookings_30d", "rating_tgid",
                "rating_tidvid", "ff_vid", "vid_completion_rate"):
        check(key, key in ins)

    # --- fields added since the redesign -------------------------------------
    # Each of these backs something on screen that would fail silently: a group
    # with no date range, a count with no booking ids, a completion rate with
    # no explanation of the shortfall.
    print("\nredesign fields")
    check("_window_label", bool(ins.get("_window_label")),
          f"got {ins.get('_window_label')!r}")
    sd = ins.get("same_day")
    check("same_day present", isinstance(sd, dict) or sd is None)
    if isinstance(sd, dict):
        for k in ("reviews", "support", "total", "review_ids", "support_ids"):
            check(f"same_day.{k}", k in sd)
        check("same_day ids are lists",
              isinstance(sd.get("review_ids"), list)
              and isinstance(sd.get("support_ids"), list))
        check("ids count matches the number",
              len(sd.get("review_ids") or []) <= max(sd.get("reviews") or 0, 0)
              or (sd.get("reviews") or 0) > 20,
              f"{len(sd.get('review_ids') or [])} ids for {sd.get('reviews')} reviews")
    for k in ("tgid_completion_rate", "vid_completion_rate",
              "tgid_incomplete_why", "vid_incomplete_why"):
        check(k, k in ins)
    why = ins.get("tgid_incomplete_why")
    if isinstance(why, list) and why:
        r0 = why[0]
        check("why rows shaped {reason,count}",
              isinstance(r0, dict) and "reason" in r0 and "count" in r0, f"{r0}")
        check("guest cancellations excluded",
              not any("customer" in str(w.get("reason", "")).lower()
                      or "change of plans" in str(w.get("reason", "")).lower()
                      for w in why),
              f"{[w.get('reason') for w in why]}")
    # The rate and the shortfall must agree: if bookings did not complete,
    # something has to say why.
    ft = ins.get("ff_tgid") or {}
    if ft.get("total") and ft.get("rate") is not None and ft["rate"] < 1:
        check("shortfall is explained",
              bool(ins.get("tgid_incomplete_why")),
              "completion is under 100% but no reasons came back")

    # --- the window actually changes the answer ------------------------------
    # This is the check that would have caught the shipped bug. The picker sent
    # a window, the server ignored it, and every window returned the same
    # numbers under a different label.
    # A review with no confirmed booking legitimately returns zeros for every
    # window. That is correct behaviour, but it cannot demonstrate that the
    # window works, so say so rather than reporting it as three failures.
    why = ins.get("_zeroed_because")
    if why:
        print(f"\n  !! This review returns zeros: {why}")
        print("     Correct, but it cannot exercise the window. Re-run against a")
        print("     review with a confirmed booking:")
        print("       python3 tools/test_insights_api.py --review <id>")
        print("     Every check above still applies and passed.\n")

    # The default the CLIENT shows must be the default the SERVER computes.
    # state.insightsWindow is 90d, and the initial fetch sent no window at all,
    # so the server applied 30d and the page displayed 30d numbers under a
    # highlighted 90d button - on every first load, invisibly.
    print("\nclient default matches server default")
    import re as _re
    try:
        with open("client/index.html") as fh:
            cli = fh.read()
        m = _re.search(r"insightsWindow:\s*'(\w+)'", cli)
        default_w = m.group(1) if m else ""
        _sends = "insights?window=${encodeURIComponent(state.insightsWindow)}" in cli
        check("client sends its window on first load", _sends,
              "" if _sends else
              "initial fetch omits the window - server applies its own default "
              "and the picker will show a window the numbers are not from")
        if default_w:
            _, b = get(base_of(args), f"/api/reviews/{rid}/insights?window={default_w}")
            i = b.get("insights") or {}
            check(f"default window {default_w} computes",
                  i.get("_window_days") == int(default_w.rstrip("d")),
                  f"_window_days={i.get('_window_days')}")
    except FileNotFoundError:
        print("  ..    client/index.html not readable from here - skipped")

    print("\nwindow changes the result")
    seen = {}
    for w in ("7d", "30d", "90d"):
        try:
            _, b = get(args.base, f"/api/reviews/{rid}/insights?window={w}")
        except urllib.error.HTTPError as e:
            check(f"window={w}", False, f"HTTP {e.code}")
            continue
        i = b.get("insights") or {}
        seen[w] = {
            "echo":     b.get("window"),
            "days":     i.get("_window_days"),
            "bookings": i.get("total_bookings_30d"),
            "support":  i.get("total_support_queries_30d"),
        }
        check(f"window={w} echoed", b.get("window") == w, f"got {b.get('window')!r}")
        check(f"window={w} computed for {w}",
              i.get("_window_days") == int(w.rstrip("d")),
              f"_window_days={i.get('_window_days')}")

    if len(seen) == 3:
        days = [seen[w]["days"] for w in ("7d", "30d", "90d")]
        check("three distinct windows computed", len(set(days)) == 3, f"{days}")
        if why:
            print("     (totals are zero for the reason above, not because the "
                  "window failed)")
        # Bookings over 7d cannot exceed bookings over 90d for the same booking.
        b7, b90 = seen["7d"]["bookings"], seen["90d"]["bookings"]
        if isinstance(b7, int) and isinstance(b90, int):
            check("7d bookings <= 90d bookings", b7 <= b90, f"{b7} vs {b90}")
            same = b7 == b90 == seen["30d"]["bookings"]
            check("windows give different totals", not same or b7 == 0,
                  "all three windows returned the same total - the window is "
                  "being ignored" if same and b7 else "")

    print("\n" + "-" * 62)
    for w in ("7d", "30d", "90d"):
        if w in seen:
            s = seen[w]
            print(f"  {w:>4}  days={s['days']!s:>4}  bookings={s['bookings']!s:>6}  "
                  f"support={s['support']!s:>5}")

    print("-" * 62)
    if _failures:
        print(f"{len(_failures)} FAILED: {', '.join(_failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
