#!/usr/bin/env python3
"""
Which reviews can Experience Insights actually compute for?

    python3 tools/survey_drafts.py
    python3 tools/survey_drafts.py --base http://host:port --limit 40

Insights need tid and vid off the DRAFT. Without them get_insights returns
zeros - correct, and indistinguishable on the dashboard from an experience with
no history. If no review has them, every tile reads zero and the section looks
broken while the endpoint is working perfectly.

This answers that directly: for each review, does it have a booking id, did the
booking resolve to a tid and vid, and is an L2 set. It calls the same endpoint
the dashboard does, so it sees exactly what the dashboard sees.

Prints the ids that CAN exercise insights, so they can be passed to
test_insights_api.py --review.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request


def get(base, path, timeout=60):
    req = urllib.request.Request(base.rstrip("/") + path,
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5000")
    ap.add_argument("--limit", type=int, default=25)
    # Probing insights means one BigQuery round trip per review, tens of
    # seconds each. That made the default run look hung and it got killed
    # before it printed the row anyone cared about. The draft alone answers
    # "does this review have a booking id", which is the question, and it
    # answers it in milliseconds.
    ap.add_argument("--insights", action="store_true",
                    help="also call the insights endpoint per review (slow: "
                         "one BigQuery round trip each)")
    args = ap.parse_args()

    try:
        rows = get(args.base, "/api/reviews", timeout=20)
    except (urllib.error.URLError, OSError) as e:
        print(f"Cannot reach {args.base}: {e}")
        return 2

    # Same check the API test does. A stale process reports zeros for reasons
    # that were fixed hours ago, and every conclusion drawn from that is wrong.
    import subprocess
    try:
        v = get(args.base, "/api/version", timeout=15)
        local = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                               text=True, timeout=10).stdout.strip()
        if v.get("stale") or (local and v.get("commit") and v["commit"] != local):
            print(f"The server is running OLD code: {v['commit'][:7]} "
                  f"(up {v.get('uptime_s')}s), working tree is {local[:7]}.\n"
                  "RESTART THE SERVER - anything below would be from the old build.")
            return 1
    except Exception:
        print("(could not confirm the server version - if results look stale, "
              "restart it)\n")
    rows = rows if isinstance(rows, list) else (rows.get("reviews") or [])
    if not rows:
        print("No reviews on this server.")
        return 2

    n = min(args.limit, len(rows))
    print(f"{len(rows)} reviews; inspecting the first {n}"
          + ("" if args.insights else
             "   (drafts only - add --insights to probe the endpoint too)")
          + "\n")
    head = (f"{'review':<28}{'inbox':<8}{'bid':<12}{'tid':<8}{'vid':<8}"
            f"{'tgid':<8}{'L2':<28}")
    print(head + ("insights" if args.insights else "can compute?"))
    print("-" * (len(head) + 20))

    usable, reasons = [], {}
    for r in rows[:args.limit]:
        rid = r.get("id") or ""
        inbox = r.get("inbox") or r.get("match_tier") or "-"
        try:
            d = get(args.base, f"/api/reviews/{rid}")
        except Exception as e:
            print(f"{rid:<28}{inbox:<8}(draft fetch failed: {str(e)[:30]})")
            continue
        draft = (d or {}).get("draft") or {}
        b = draft.get("booking") or {}
        # "id" is the key verify_bid and the pipeline actually write. Reading
        # only bid/bookingId showed "-" for every row including ones that had a
        # booking, which pointed the investigation at the wrong thing.
        bid = str(b.get("id") or b.get("bid") or b.get("bookingId") or "")[:10]
        tid = str(b.get("tid") or "")[:6]
        vid = str(b.get("vid") or "")[:6]
        # tgid alone still answers the vendor-level half - the TGID rating and
        # both completion rates key on it - so it is not "cannot compute".
        tgid = str(b.get("tgid") or "")
        l2  = str(draft.get("l2") or "")[:26]

        if args.insights:
            try:
                ins = (get(args.base, f"/api/reviews/{rid}/insights",
                           timeout=180) or {}).get("insights") or {}
                why = ins.get("_zeroed_because") or ""
                state = "zeros: " + why[:34] if why else "COMPUTES"
            except Exception as e:
                state = f"error {str(e)[:22]}"
                why = "endpoint error"
        else:
            # What get_insights itself needs: a booking id is enough, because
            # it resolves tid/vid/tgid from one through verify_bid. Saying
            # "no booking id on the draft" here without calling anything is
            # the same answer the endpoint gives, minutes sooner.
            if bid:
                why, state = "", "yes - has a booking id"
            elif tid and vid:
                why, state = "", "yes - has tid and vid"
            elif tgid:
                why, state = "", "partly - tgid only"
            else:
                why = "no booking id on the draft"
                state = "no  - " + why

        if not why:
            usable.append(rid)
        else:
            reasons[why] = reasons.get(why, 0) + 1

        print(f"{rid:<28}{inbox:<8}{bid or '-':<12}{tid or '-':<8}{vid or '-':<8}"
              f"{tgid[:6] or '-':<8}{l2 or '-':<28}{state}")

    print("-" * (len(head) + 20))
    print(f"\n{len(usable)} of {n} can compute insights")
    for why, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {n:>3}  {why}")

    if usable:
        print("\nTest the window against one of these:\n"
              f"  python3 tools/test_insights_api.py --base {args.base} "
              f"--review {usable[0]}")
        if len(usable) > 1:
            print("  others: " + " ".join(usable[1:6]))
    else:
        print("\nNo review has a tid and vid on its draft, so every insights tile "
              "is\nlegitimately zero. That is upstream of insights - the booking "
              "is not\nbeing resolved onto the draft - and no change to the "
              "endpoint will fix it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
