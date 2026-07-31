#!/usr/bin/env python3
"""Drive one review through every flow the dashboard offers, over HTTP.

    rm -f demo.db
    DATABASE_URL=sqlite:///./demo.db MOCK_MODE=true python3 tools/seed_demo.py
    DATABASE_URL=sqlite:///./demo.db MOCK_MODE=true \\
        python3 -m uvicorn server.main:app --port 8091 &
    python3 tools/e2e_smoke.py

Set E2E_BASE to point it somewhere else. It WRITES - it sends, flags and
posts - so run it against a scratch database, never a shared one.

Not a unit test - this talks to the running server exactly as the browser
does, so it catches what source assertions cannot: a route that 404s, a save
the server rejects, an action that repeats.
"""
import json
import sys
import urllib.request

import os
BASE = os.getenv("E2E_BASE", "http://127.0.0.1:8091")
PASS, FAIL = [], []


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=60) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw[:300]
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw[:300]
    except Exception as e:
        return 0, str(e)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def head(t):
    print(f"\n{'─' * 74}\n{t}\n{'─' * 74}")


# ── the inbox ───────────────────────────────────────────────────────────────
head("1. the inbox")
st, rows = call("GET", "/api/reviews")
check("GET /api/reviews returns 200", st == 200, f"status {st}")
check("all three seeded reviews are listed", isinstance(rows, list) and len(rows) == 3,
      f"got {len(rows) if isinstance(rows, list) else rows}")
buckets = sorted(r.get("bucket") for r in rows) if isinstance(rows, list) else []
check("all three buckets are represented", buckets == ["candidates", "identified", "untraceable"],
      str(buckets))

RID = next((r["id"] for r in rows if r.get("bucket") == "identified"), None)
CAND = next((r["id"] for r in rows if r.get("bucket") == "candidates"), None)
UNTR = next((r["id"] for r in rows if r.get("bucket") == "untraceable"), None)
print(f"\n  identified={RID}  candidates={CAND}  untraceable={UNTR}")

# ── the review a human opens ────────────────────────────────────────────────
head("2. opening a review")
st, d = call("GET", f"/api/reviews/{RID}")
check("GET one review returns 200", st == 200, f"status {st}")
draft = (d or {}).get("draft") or d or {}
for f in ("rca_v3", "confidence_trail", "actions_taken", "candidates_list",
          "rca_posted_at", "flag_to_biz_state", "sent_at"):
    check(f"the response carries {f}", f in draft, "missing" if f not in draft else "")

# ── editing: does a save actually stick? ────────────────────────────────────
head("3. editing the RCA")
st, _ = call("PATCH", f"/api/reviews/{RID}/draft-v2",
             {"stated_issue": "END TO END TEST VALUE"})
check("PATCH draft-v2 accepted", st == 200, f"status {st}")
st, d2 = call("GET", f"/api/reviews/{RID}")
got = ((d2 or {}).get("draft") or d2 or {}).get("stated_issue")
check("the edit persisted", got == "END TO END TEST VALUE", repr(got))

head("4. actions taken — the shape that used to be destroyed")
acts = {"sp": [{"with": "Vendor ops", "handle": "@ops", "time": "31 Jul 12:00",
                "context": "asked for the fulfilment log", "where": "slack.com/x/1"}],
        "customer": [], "business": [], "product": [], "ce": []}
st, _ = call("PATCH", f"/api/reviews/{RID}/draft-v2", {"actions_taken": acts})
check("PATCH actions_taken accepted", st == 200, f"status {st}")
st, d3 = call("GET", f"/api/reviews/{RID}")
back = ((d3 or {}).get("draft") or d3 or {}).get("actions_taken", {}).get("sp", [])
check("the action came back as an object", bool(back) and isinstance(back[0], dict),
      repr(back)[:120])
if back and isinstance(back[0], dict):
    for f in ("with", "handle", "time", "context", "where"):
        check(f"  {f} survived the round trip", back[0].get(f), repr(back[0].get(f)))

# ── the per-row action endpoint the UI ignores ──────────────────────────────
head("5. the per-row action endpoint")
st, r5 = call("PATCH", f"/api/reviews/{RID}/action",
              {"tab": "ce", "op": "add", "action": {"with": "CE", "context": "noted"}})
check("PATCH /action add works", st == 200, f"status {st} {str(r5)[:120]}")

# ── posting the RCA to Slack, twice ─────────────────────────────────────────
head("6. posting the RCA to the Slack thread")
st, p1 = call("POST", f"/api/reviews/{RID}/post-rca")
check("first post succeeds", st == 200, f"status {st} {str(p1)[:120]}")
check("first post is not reported as a repeat",
      isinstance(p1, dict) and p1.get("already_posted") is False, str(p1)[:120])
st, p2 = call("POST", f"/api/reviews/{RID}/post-rca")
check("second post is refused as a repeat",
      isinstance(p2, dict) and p2.get("already_posted") is True, str(p2)[:120])
st, p3 = call("POST", f"/api/reviews/{RID}/post-rca?force=true")
check("an explicit repeat is allowed",
      isinstance(p3, dict) and p3.get("already_posted") is False, str(p3)[:120])

# ── flag to biz ─────────────────────────────────────────────────────────────
head("7. flag to biz")
st, f1 = call("POST", f"/api/reviews/{RID}/flag-to-biz",
              {"tag": "@biz", "message": "please review supply", "send": True,
               "completion_rate": 87.4, "tgid": "12345", "tid": "678", "vid": "9012"})
check("flag-to-biz send returns 200", st == 200, f"status {st} {str(f1)[:160]}")
st, d7 = call("GET", f"/api/reviews/{RID}")
dd = (d7 or {}).get("draft") or d7 or {}
check("the sent state is recorded", dd.get("flag_to_biz_state") == "sent",
      repr(dd.get("flag_to_biz_state")))
biz = (dd.get("actions_taken") or {}).get("business") or []
check("an action log entry was written", bool(biz), repr(biz)[:160])
if biz:
    ctx = biz[-1].get("context", "")
    check("  the numbers are in the log", "Completion 87.4%" in ctx and "TGID 12345" in ctx,
          repr(ctx)[:160])

# ── send ────────────────────────────────────────────────────────────────────
head("8. send")
st, s1 = call("POST", f"/api/reviews/{RID}/send")
check("send returns 200", st == 200, f"status {st} {str(s1)[:120]}")
st, d8 = call("GET", f"/api/reviews/{RID}")
dd8 = (d8 or {}).get("draft") or d8 or {}
check("sent_at is stamped", bool(dd8.get("sent_at")), repr(dd8.get("sent_at")))

# ── the untraceable review ──────────────────────────────────────────────────
head("9. the untraceable review")
st, u = call("GET", f"/api/reviews/{UNTR}")
check("it opens", st == 200, f"status {st}")
st, bid = call("POST", f"/api/reviews/{UNTR}/request-bid")
check("the ask-for-booking-reference reply is served", st == 200, f"status {st}")
if isinstance(bid, dict):
    tmpl = bid.get("template", "")
    check("  it comes from the macro copy file", "Headout" in tmpl or "booking" in tmpl.lower(),
          repr(tmpl)[:120])
    check("  it does not post anything", bid.get("posted") is False, str(bid.get("posted")))

# ── candidate confirmation ──────────────────────────────────────────────────
head("10. confirming a candidate")
st, c = call("GET", f"/api/reviews/{CAND}")
cd = (c or {}).get("draft") or c or {}
cands = cd.get("candidates_list") or []
check("the candidate list is served", bool(cands), f"{len(cands)} candidates")
if cands:
    for f in ("id", "experience", "matchReasons"):
        check(f"  candidates carry {f}", f in cands[0], repr(list(cands[0])[:8]))
    st, sel = call("POST", f"/api/reviews/{CAND}/select-candidate",
                   {"bid": str(cands[0]["id"])})
    check("selecting a candidate is accepted", st in (200, 202), f"status {st} {str(sel)[:120]}")

# ── bulk status ─────────────────────────────────────────────────────────────
head("11. bulk status (the route that 404'd)")
st, b = call("GET", "/api/reviews/bulk-status")
check("bulk-status returns 200", st == 200, f"status {st}")
check("  and returns progress state", isinstance(b, dict) and "running" in b, str(b)[:120])

# ── summary ─────────────────────────────────────────────────────────────────
print(f"\n{'═' * 74}")
print(f"  {len(PASS)} passed · {len(FAIL)} failed")
if FAIL:
    print("\n  FAILURES:")
    for f in FAIL:
        print(f"    {f}")
print("═" * 74)
sys.exit(1 if FAIL else 0)
