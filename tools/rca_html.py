#!/usr/bin/env python3
"""
Write one review's whole RCA to a standalone HTML file, for auditing.

    python3 tools/rca_html.py --review david
    python3 tools/rca_html.py --base http://localhost:5001 --review david
    python3 tools/rca_html.py --review tp_1784722373_497379 -o /tmp/rca.html

--review takes a review id, or any part of an author's name - "david" finds
the review David wrote, so the id does not have to be looked up first.

There is no RCA document anywhere in this system. The dashboard assembles it
in the browser from /api/reviews/<id>, so it exists only on screen, only while
that tab is open, and only for the fields the renderer happens to draw. That
makes it impossible to hand to someone, and impossible to audit: a field the
pipeline never filled looks the same as a field the renderer never drew.

So this does not screenshot the dashboard. It reads the API and prints every
field the draft carries, marking each one:

    value     the pipeline produced something
    empty     the field exists and is blank - the pipeline ran and had nothing
    MISSING   the API did not return the key at all - a different failure

That distinction is the point. "Empty" is usually a finding about the review;
"missing" is usually a finding about the code.

The output is one self-contained file. No network, no assets, opens anywhere.
"""
import argparse
import html
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Grouped the way the pipeline runs, so a gap shows up at the stage that
# caused it rather than halfway down an alphabetical list.
GROUPS = [
    ("Match", [
        "booking", "guest_name", "booking_status", "match_tier",
        "match_confidence", "match_method", "bid_source", "candidate_state",
        "candidates_list", "confidence_trail", "narrowing_attempts",
        "extracted_signals",
    ]),
    ("Classification", [
        "stated_issue", "l1", "l2", "sub_theme", "l1_reasoning",
        "primary_scenario", "overlay_scenarios", "wwr_scenarios",
    ]),
    ("Root cause", [
        "tldr", "what_went_wrong_bullets", "wwr_chain", "diagnostic_checks",
        "evidence", "issue_specific_answers", "checklist_answers",
        "area_of_improving", "prevention",
    ]),
    ("Support and events", [
        "timeline", "timeline_raw", "support_interaction_frames",
        "support_summary", "sp_interaction_frames", "zendesk_ticket_ids",
        "ticket_facts", "slack_mentions", "slack_thread_override",
        "similar_support", "similar_reviews",
    ]),
    ("Outcome", [
        "resolution", "actions_taken", "dss_rec", "dss_connected_at",
        "flag_to_biz_state", "flag_to_biz_message",
        "suggested_response", "final_response", "generated_at", "sent_at",
    ]),
]

CSS = """
*{box-sizing:border-box}
body{margin:0;padding:28px;background:#14161a;color:#e6e8ec;
     font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
h1{font-size:19px;margin:0 0 4px}
h2{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:#8b93a1;
   margin:30px 0 10px;border-bottom:1px solid #262a31;padding-bottom:6px}
.sub{color:#8b93a1;font-size:12px;margin-bottom:20px}
.row{display:grid;grid-template-columns:230px 1fr;gap:14px;
     padding:7px 0;border-bottom:1px solid #1d2026;align-items:start}
.k{color:#8b93a1;word-break:break-word}
.v{white-space:pre-wrap;word-break:break-word}
.empty{color:#6b7280;font-style:italic}
.missing{color:#e0a33e;font-weight:600}
.warn{background:#2a1f14;border-left:3px solid #e0a33e;padding:10px 13px;margin:14px 0}
.bad{background:#2a1618;border-left:3px solid #e05c5c;padding:10px 13px;margin:14px 0}
.ok{background:#152318;border-left:3px solid #4ea36a;padding:10px 13px;margin:14px 0}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border:1px solid #262a31;padding:6px 9px;text-align:left;vertical-align:top}
th{color:#8b93a1;font-weight:600}
pre{background:#0f1115;border:1px solid #262a31;padding:13px;overflow-x:auto;
    border-radius:5px;font-size:12px}
details>summary{cursor:pointer;color:#8b93a1;margin:16px 0 6px}
.quote{background:#0f1115;border-left:3px solid #3a4150;padding:12px 15px;
       white-space:pre-wrap;margin:10px 0}
"""


def get(base, path, timeout=180):
    req = urllib.request.Request(base.rstrip("/") + path,
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def esc(x):
    return html.escape(str(x), quote=False)


def render_value(v):
    """
    A value, or the reason there is not one.

    Empty and missing are deliberately different words. A blank field means
    the pipeline ran and produced nothing, which is a fact about the review;
    an absent key means the API never sent it, which is a fact about the code.
    Collapsing them into one dash is what makes a dashboard impossible to
    audit.
    """
    if v is None:
        return '<span class="empty">null</span>'
    if isinstance(v, str):
        return esc(v) if v.strip() else '<span class="empty">empty string</span>'
    if isinstance(v, (int, float, bool)):
        return esc(v)
    if isinstance(v, list):
        if not v:
            return '<span class="empty">empty list</span>'
        # A list of uniform dicts is a table; anything else is prose.
        if all(isinstance(i, dict) for i in v):
            cols, seen = [], set()
            for i in v:
                for k in i:
                    if k not in seen:
                        seen.add(k)
                        cols.append(k)
            head = "".join(f"<th>{esc(c)}</th>" for c in cols)
            body = "".join(
                "<tr>" + "".join(
                    f"<td>{render_value(i.get(c))}</td>" for c in cols) + "</tr>"
                for i in v)
            return f"<table><tr>{head}</tr>{body}</table>"
        return "<br>".join(f"{n + 1}. {render_value(i)}" for n, i in enumerate(v))
    if isinstance(v, dict):
        if not v:
            return '<span class="empty">empty object</span>'
        return "".join(
            f'<div class="row" style="grid-template-columns:190px 1fr;'
            f'border:none;padding:3px 0"><div class="k">{esc(k)}</div>'
            f'<div class="v">{render_value(val)}</div></div>'
            for k, val in v.items())
    return esc(v)


def field_rows(draft, keys):
    out = []
    for k in keys:
        if k not in draft:
            out.append(f'<div class="row"><div class="k">{esc(k)}</div>'
                       f'<div class="v missing">MISSING - the API did not '
                       f'return this key</div></div>')
        else:
            out.append(f'<div class="row"><div class="k">{esc(k)}</div>'
                       f'<div class="v">{render_value(draft[k])}</div></div>')
    return "".join(out)


def resolve(base, wanted):
    """A review id, or part of an author's name."""
    rows = get(base, "/api/reviews", timeout=30)
    rows = rows if isinstance(rows, list) else (rows.get("reviews") or [])
    for r in rows:
        if r.get("id") == wanted:
            return wanted, rows
    w = wanted.strip().lower()
    hits = [r for r in rows
            if w in str(r.get("author") or "").lower()
            or w in str(r.get("guest") or "").lower()]
    if len(hits) == 1:
        return hits[0]["id"], rows
    if len(hits) > 1:
        print(f"{wanted!r} matches {len(hits)} reviews:")
        for r in hits:
            print(f"  {r.get('id')}   {r.get('author')}")
        print("Re-run with the id.")
        return None, rows
    return None, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5000")
    ap.add_argument("--review", required=True,
                    help="review id, or part of the author's name")
    ap.add_argument("--window", default="")
    ap.add_argument("-o", "--out", default="")
    args = ap.parse_args()

    try:
        rid, rows = resolve(args.base, args.review)
    except (urllib.error.URLError, OSError) as e:
        print(f"Cannot reach {args.base}: {e}\nStart the server first.")
        return 2
    if not rid:
        print(f"No review matches {args.review!r}. {len(rows)} on this server:")
        for r in rows[:20]:
            print(f"  {r.get('id')}   {r.get('author') or '-'}")
        return 1

    payload = get(args.base, f"/api/reviews/{rid}")
    review = (payload or {}).get("review") or {}
    draft = (payload or {}).get("draft")
    qs = f"?window={args.window}" if args.window else ""
    try:
        ins = (get(args.base, f"/api/reviews/{rid}/insights{qs}") or {}).get("insights") or {}
    except Exception as e:
        ins = {"_fetch_error": str(e)[:300]}

    if draft is None:
        body = ('<div class="bad">This review has NO DRAFT. The pipeline has '
                'not run for it, so there is no RCA to audit.</div>')
    else:
        # Anything worth an auditor's attention before they start reading.
        notes = []
        if ins.get("_failed_queries"):
            notes.append('<div class="bad">Insight queries FAILED: '
                         + esc(", ".join(ins["_failed_queries"]))
                         + "<br>" + esc(json.dumps(ins.get("_failed_detail") or {}))
                         + "<br>Numbers below are missing, not zero.</div>")
        if ins.get("_zeroed_because"):
            notes.append('<div class="warn">Insights returned zeros: '
                         + esc(ins["_zeroed_because"]) + "</div>")
        if ins.get("_partial_because"):
            notes.append('<div class="warn">Partly computed: '
                         + esc(ins["_partial_because"]) + "</div>")
        blank = [k for g, ks in GROUPS for k in ks
                 if k in draft and not draft[k] and draft[k] not in (0, False)]
        absent = [k for g, ks in GROUPS for k in ks if k not in draft]
        if absent:
            notes.append('<div class="bad">Keys the API never returned: '
                         + esc(", ".join(absent)) + "</div>")
        if blank:
            notes.append('<div class="warn">Fields the pipeline left empty ('
                         + str(len(blank)) + "): " + esc(", ".join(blank))
                         + "</div>")
        if not notes:
            notes.append('<div class="ok">Every field below was populated and '
                         "every insight query answered.</div>")

        groups = "".join(
            f"<h2>{esc(title)}</h2>{field_rows(draft, keys)}"
            for title, keys in GROUPS)
        ins_rows = "".join(
            f'<div class="row"><div class="k">{esc(k)}</div>'
            f'<div class="v">{render_value(v)}</div></div>'
            for k, v in sorted(ins.items()))
        body = ("".join(notes) + groups
                + f"<h2>Experience insights"
                  f"{' - ' + esc(ins.get('_window_label')) if ins.get('_window_label') else ''}"
                  f"</h2>{ins_rows}"
                + "<details><summary>Raw JSON - draft</summary><pre>"
                + esc(json.dumps(draft, indent=2, default=str))
                + "</pre></details>"
                + "<details><summary>Raw JSON - insights</summary><pre>"
                + esc(json.dumps(ins, indent=2, default=str))
                + "</pre></details>")

    rating = review.get("rating")
    doc = f"""<!doctype html>
<meta charset="utf-8">
<title>RCA audit - {esc(review.get('author') or rid)}</title>
<style>{CSS}</style>
<h1>RCA audit - {esc(review.get('author') or '(no author)')}</h1>
<div class="sub">
  {esc(rid)} &middot; {esc(rating if rating is not None else '?')}&#9733;
  &middot; ref {esc(review.get('reference_number') or '-')}
  &middot; status {esc(review.get('status') or '-')}
  &middot; received {esc(review.get('received_at') or '-')}
  &middot; language {esc(review.get('language') or '-')}<br>
  pulled from {esc(args.base)} at {datetime.now(timezone.utc).isoformat(timespec='seconds')}
</div>
<h2>The review</h2>
<div class="quote">{esc(review.get('body_english')
                       or review.get('body_original') or '(no body)')}</div>
{('<details><summary>Original language</summary><div class="quote">'
  + esc(review.get('body_original') or '') + '</div></details>')
 if review.get('body_original') and review.get('body_english')
 and review['body_original'] != review['body_english'] else ''}
{body}
"""
    out = args.out or f"rca_{rid}.html"
    with open(out, "w") as fh:
        fh.write(doc)
    print(f"wrote {out}  ({len(doc):,} bytes)")
    print(f"review {rid}   author {review.get('author') or '-'}")
    if draft is not None:
        print(f"{len([k for g, ks in GROUPS for k in ks if k in draft])} fields present, "
              f"{len([k for g, ks in GROUPS for k in ks if k not in draft])} missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
