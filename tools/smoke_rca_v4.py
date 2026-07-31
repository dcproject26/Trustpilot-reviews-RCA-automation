#!/usr/bin/env python3
"""Every field in the v4 contract lands where the spec says it does.

The handoff's "Where each field lands in the UI" table is the contract. This
walks it: for each field it finds the element that should carry it and checks
the value is actually there. A field that is collected and dropped renders
identically to a field the model left empty, which is the whole reason this
exists rather than a screenshot comparison.

    python3 -m uvicorn server.main:app --port 8099   # in one shell
    python3 tools/smoke_rca_v4.py                    # in another
"""
import json
import sys

from playwright.sync_api import sync_playwright

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099


def main():
    rows, missing = [], 0
    with sync_playwright() as pw:
        b = pw.chromium.launch(
            executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
        pg = b.new_page(viewport={"width": 1600, "height": 1400})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
        pg.wait_for_timeout(1000)
        pg.locator(".review-item").first.click()
        pg.wait_for_timeout(1600)

        v3 = pg.evaluate(
            "() => (REVIEWS.find(x => x.id === state.selected).rca.v3) || {}")
        checks = pg.evaluate(r"""() => {
          const SEP = String.fromCharCode(10);
          const q = s => document.querySelector(s);
          // innerText only, and a collapsed .convo-body or an <input> value
          // reads as absent - which is the same answer a dropped field gives.
          // So: visible text PLUS every field value PLUS the markup of
          // sections that are collapsed by default.
          const deep = el => { if (!el) return '';
            const vals = [...el.querySelectorAll('input,textarea,select')]
              .map(i => i.value || '').join('\n');
            const hidden = [...el.querySelectorAll('.convo-body,.wwr-issue-body')]
              .map(x => x.textContent || '').join('\n');
            return (el.innerText || '') + '\n' + vals + '\n' + hidden; };
          const txt = s => { const e = q(s); return e ? deep(e) : null; };
          const rca = document.querySelector('#rca-col');
          const facts = document.querySelector('#review-col') || document.body;
          return {
            stated_issue:  deep(rca),
            tldr:          txt('#rca-tldr-section') || rca.innerText,
            classification: deep(facts),
            booking_logs:  txt('#rca-booking-logs-section') || '',
            booking_logs_col: q('#rca-booking-logs-section')
                              ? (q('#rca-booking-logs-section').closest('#rca-col') ? 'rca' : 'facts') : null,
            events_col:    q('#rca-events-timeline-section')
                              ? (q('#rca-events-timeline-section').closest('#rca-col') ? 'rca' : 'facts') : null,
            wwr:           txt('#rca-wwr5-section') || '',
            isa:           txt('#rca-issue-answers-section') || '',
            sop:           txt('#rca-sop-section') || '',
            contacts:      [...document.querySelectorAll('.convo-frame')].map(deep).join(SEP),
            flags:         txt('#rca-flags-section') || '',
            dss:           txt('.dss-block') || '',
            reply:         (q('.reply-text')||{}).value || '',
            slack:         (q('[data-slack-edit]')||{}).value || '',
            everything:    deep(document.body),
          }; }""")
        b.close()

    def check(field, value, where, hay, note=""):
        nonlocal missing
        if value in (None, "", [], {}):
            rows.append(("skip", field, where, "not in this payload"))
            return
        v = str(value)[:60].strip()
        ok = v and v[:40] in (checks.get(hay) or "")
        if not ok:
            missing += 1
        rows.append(("ok  " if ok else "MISS", field, where, note or v[:52]))

    gi = ((v3.get("what_went_wrong") or {}).get("guest_issues") or [])
    check("stated_issue", v3.get("stated_issue"), "RCA → Guest's stated issue", "stated_issue")
    check("tldr.our_mistake", (v3.get("tldr") or {}).get("our_mistake"), "RCA → TL;DR", "tldr")
    check("tldr.our_fix", (v3.get("tldr") or {}).get("our_fix"), "RCA → TL;DR", "tldr")
    check("l1", v3.get("l1"), "Facts → Classification", "classification")
    check("l2", v3.get("l2"), "Facts → Classification", "classification")
    for n, g in enumerate(gi, 1):
        check(f"issue[{n}].issue", g.get("issue"), "RCA → WWR issue title", "wwr")
        check(f"issue[{n}].claim", g.get("claim"), "RCA → Claim block", "wwr")
        check(f"issue[{n}].claim_accuracy", g.get("claim_accuracy"), "RCA → verdict chip", "wwr")
        check(f"issue[{n}].owner", g.get("owner"), "RCA → owner chip", "wwr")
        check(f"issue[{n}].claim_accuracy_note", g.get("claim_accuracy_note"),
              "RCA → analysis note", "wwr")
        for k in ("root_cause", "operational_failure", "sop_gap", "pattern", "fix"):
            check(f"issue[{n}].{k}", g.get(k), "RCA → analysis line", "wwr")
        for m, e in enumerate(g.get("evidence") or [], 1):
            if isinstance(e, dict):
                check(f"issue[{n}].evidence[{m}].text", e.get("text"), "RCA → evidence grid", "wwr")
                check(f"issue[{n}].evidence[{m}].source", e.get("source"), "RCA → source rail", "wwr")
    for n, a in enumerate(v3.get("issue_specific_answers") or [], 1):
        check(f"isa[{n}].question", a.get("question"), "RCA → Issue-specific answers", "isa")
        check(f"isa[{n}].evidence", a.get("evidence"), "RCA → ISA evidence line", "isa")
    sop = v3.get("sop_compliance") or {}
    for k in ("expected", "actual", "detail"):
        check(f"sop_compliance.{k}", sop.get(k), "RCA → SOP compliance", "sop")
    for n, c in enumerate(v3.get("support_interaction_notes") or [], 1):
        check(f"contact[{n}].summary", c.get("summary"), "RCA → Guest ↔ support", "contacts")
        check(f"contact[{n}].ce_miss", c.get("ce_miss"), "RCA → CE gap line", "contacts")
    sp = v3.get("sp_interaction_notes") or {}
    check("sp_interaction.reason", sp.get("reason"), "RCA → SP interaction", "everything")
    for n, f in enumerate(v3.get("flags") or [], 1):
        check(f"flag[{n}].flag", f.get("flag"), "RCA → Flags", "flags")
        check(f"flag[{n}].evidence", f.get("evidence"), "RCA → Flags evidence", "flags")
    for n, a in enumerate(v3.get("area_of_improving") or [], 1):
        check(f"area_of_improving[{n}]", a, "RCA → Area of improvement", "everything")
    check("resolution", v3.get("resolution"), "RCA → Resolution", "everything")
    check("takedown.verdict", (v3.get("takedown") or {}).get("verdict"),
          "RCA → Takedown select", "everything")
    check("dss.prescribes", (v3.get("dss") or {}).get("prescribes"),
          "RCA → DSS block", "dss")
    check("suggested_response", v3.get("suggested_response"),
          "RCA → Response to guest", "reply")

    w = max(len(r[1]) for r in rows)
    for mark, field, where, note in rows:
        print(f"  {mark}  {field:<{w}}  {where:<34} {note}")

    print()
    print(f"  booking timeline column : {checks['booking_logs_col']} "
          f"({'ok' if checks['booking_logs_col'] == 'facts' else 'WRONG'})")
    print(f"  events timeline column  : {checks['events_col']} "
          f"({'ok' if checks['events_col'] == 'rca' else 'WRONG'})")
    reply = v3.get("suggested_response") or ""
    leaked = bool(reply) and reply[:40] in (checks["slack"] or "")
    print(f"  reply in the Slack post : {'LEAKED' if leaked else 'no (correct)'}")
    print(f"  js errors               : {errs or 'none'}")
    print()
    print(f"  {len(rows)} fields checked · {missing} missing · "
          f"{sum(1 for r in rows if r[0] == 'skip')} not in this payload")
    return 1 if (missing or errs or leaked) else 0


if __name__ == "__main__":
    sys.exit(main())
