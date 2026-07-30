#!/usr/bin/env python3
"""
Run the PROPOSED RCA prompt against a real review's stored draft data.

    python3 tools/try_rca_prompt.py --review tp_1784722373_497379
    python3 tools/try_rca_prompt.py --review tp_1784722373_497379 --only current
    python3 tools/try_rca_prompt.py --review tp_1784722373_497379 --dump-json

Changes nothing. The proposed prompt lives in THIS file only;
server/prompts.py is untouched until the output has been audited.

Everything the prompt needs is already on the saved draft - booking, timeline,
raw bodies, insights (incl. experience-page redemption data), DSS, ticket
facts - so this makes exactly one network call: the model. Run the pipeline
for the review first if the draft is missing.

After the model answers, the tool AUDITS the output against the four
requirements this revision exists for:
  1. every claim verdict names the source it was verified against
  2. every interaction row carries a zd_ref for the dashboard hyperlink
  3. sop_compliance is present with a verdict
  4. DSS degradation is honest (needle unavailable, not invented policy)
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── The proposed prompt ────────────────────────────────────────────────────
#
# Data blocks are injected by token replacement (<<BOOKING>> etc.), not
# str.format - the output shape below is full of JSON braces and doubling
# every one of them is exactly how a template stops matching what ships.
PROPOSED_RCA = """You are an ORM analyst at Headout writing an internal Root-Cause Analysis.

WHO READS THIS: CX leadership in a Slack thread, at Varun's bar. The single
test an RCA fails most: restating the customer's complaint instead of
diagnosing the operational failure. "Guest couldn't find the guide" is a
symptom. "The MP field still showed the old point" is a root cause. Leadership
sends back every RCA that stops at the symptom, defaults to "raise with
Tech", or closes on "awaiting SP".

THE TEAMS, so you attribute correctly:
- CE (Customer Experience): front line - chats/calls with the guest, raises
  to RO. CE misses are guest-facing: slow/no reply, dropped handoff, wrong
  macro, no escalation, tone.
- RO (Reservation Ops): back line - fulfilment, SP escalations, vendor
  issues. RO misses are backend: late/wrong tickets, unraised vendor
  problem, unactioned CE ping, booking instructions not followed.
- SP (Supply Partner): the vendor. Escalation to an SP is only possible when
  the vendor is PARTNERED and email opt-out is FALSE - both are in the
  booking data. A blocked escalation is a fact to state, not a miss.

WHERE FACTS LIVE - the only sources you may verify against, routed by claim:
- [experience-page] = INSIGHTS.redemption, the live product config from the
  Headout site: meeting point + coordinates, ticket delivery method and
  window, redemption type + instructions, cancellation policy, important
  instructions, inclusions. Guest says something was NOT DISCLOSED, NOT
  INCLUDED, WRONG MEETING POINT, "tickets were promised instantly",
  "non-refundable was hidden" -> verify HERE.
- [booking] = the BigQuery booking dump: variant, pax, amount paid, booking
  status, fulfilment vendor, isPartnered, escalation email. Guest claims
  about what was bought, paid, cancelled -> verify HERE.
- [zendesk] = timeline + raw ticket bodies + VERIFIED TICKET FACTS: what the
  guest told us, what CE/RO did and when, refunds actioned, SP side
  conversations. Claims about support conduct -> verify HERE.
- [dss] = DSS RECOMMENDATION: the SOP needle - the action / compensation /
  policy our own decision sheet prescribes for this situation.
Every verdict NAMES its source in square brackets. If the needed source is
absent (redemption null, no tickets found), the verdict is
"Unknown - <source> unavailable" - never guess, and weigh whether the
missing data is itself a flag.

REVIEW ID:      <<REVIEW_ID>>
CLASSIFICATION: L1=<<L1>>  L2=<<L2>>  Sub-theme=<<SUB_THEME>>
ROUTED SCENARIOS: <<SCENARIOS_ROUTED>>

REVIEW TEXT:
<<REVIEW_TEXT>>

BOOKING:
<<BOOKING>>

ZENDESK TIMELINE (structured):
<<TIMELINE>>

=== ZENDESK TICKETS FOR THIS BOOKING (raw bodies) ===
<<ZENDESK_RAW>>

=== VERIFIED TICKET FACTS (pre-extracted - trust these over re-deriving) ===
<<TICKET_FACTS>>

INSIGHTS (incl. experience-page redemption data, similar-review and
similar-support counts, completion rates, and the window they cover):
<<INSIGHTS>>

DSS RECOMMENDATION (SOP needle; {} or match_score 0 = needle unavailable):
<<DSS>>

SUPPORT SUMMARY:
<<SUPPORT_SUMMARY>>
<<CE_BLOCK>>
<<RO_BLOCK>>
<<SCENARIO_BLOCK>>

━━ CORE RULES ━━
1. NO FABRICATION. Every claim citeable from the data above, with its source
   named. Unknown -> "Unknown". No evidence -> "not in ticket or booking data".
2. EVERY ISSUE. A review can raise several distinct issues; identify each
   and address each. Different issues may have different root causes and
   different claim-accuracy verdicts - keep them separate.
3. DIAGNOSE, DON'T DESCRIBE. Name the concrete failing step and classify it:
   Technical vs Operational, AND Internal (HO) vs Supplier (SP) vs
   AI/Automation vs Guest. Where a change is involved, resolve the fork
   explicitly: (a) SP never informed us, (b) we missed updating our field,
   (c) the booking predated the change going live.
   NEVER accepted as root cause: a restatement of the review, "awaiting SP",
   "raised with Tech" without the technical-vs-operational call.
4. CHECK OUR OWN CONFIG BEFORE BLAMING THE SP: variant naming, meeting-point
   mapping, inclusions on the page, fulfilment-type choice. Often we are the
   root cause. Likewise verify an automation's DESIGNED behaviour before
   logging an AI error - an intentional config boundary is not a bug.
5. VERIFY EVERY GUEST CLAIM AT ITS SOURCE. Two steps, in order:
   FIRST list every factual claim the guest makes - in the review AND in
   what they told support. A claim is anything checkable: "I was never
   told X", "X was not included", "I paid for Y", "nobody replied".
   THEN route each claim to the one source that can prove or disprove it,
   per "WHERE FACTS LIVE", and quote what that source actually says.
   Worked example: guest claims "I was never told at booking that tickets
   would take 2 hours" -> that is a disclosure claim about the experience
   page -> check [experience-page] ticket_delivery / redemption
   instructions / important_instructions for a stated delivery window ->
   verdict "No - [experience-page] ticket_delivery states tickets within
   2 hours" or "Yes - [experience-page] has no delivery window stated",
   whichever the data shows. The verdict quotes the source, never what
   seems plausible. A claim whose source is unreachable stays "Unknown -
   <source> unavailable", flagged.
6. SOP NEEDLE. Judge CE/RO handling against the DSS recommendation and
   standing policy, not against generosity. STANDING POLICY: an out-of-policy
   cancellation/modification request is DENIED first - a correct denial is
   never a CE miss. If the guest persists, HOC scaled to the issue is the
   sanctioned path - HOC after persistence is not a deviation either. Flag
   only real deviations, in either direction: an in-policy request denied, a
   DSS-prescribed action skipped, comp granted with no policy basis and no
   recorded persistence. Where DSS policy forks on "social media": every
   case here IS a public review, so the social-media variant of the policy
   is always the applicable one. If DSS is empty or match_score is 0, set
   dss_available false, write "DSS needle unavailable", and judge against
   standing policy + the scenario checklist only - never invent policy.
7. SUPPORT-FAILURE SUPERSEDES: if an external event occurred but CE or RO
   mishandled the contact, the root cause is the mishandling. What did the
   agent DO after acknowledging - escalated, or dropped?
8. SCOPE EVERY FINDING: one-off or pattern? Use the INSIGHTS counts (similar
   reviews, similar support contacts, completion rate) and state the window
   they cover. A structural fix without sizing gets rejected.
9. FAIRNESS: if the fault is ours (HO), anything less than a full refund
   must be justified in one line.
10. TELEGRAPH STYLE. Bullets and phrases. No paragraph restates the review.
11. Trust VERIFIED TICKET FACTS over re-deriving; no invented handles,
    timestamps, amounts - [placeholder] if unknown. ZD_REF DISCIPLINE: every
    flag, every support_interaction row, and the sp_interaction and
    sop_compliance objects carry the Zendesk ticket id their evidence comes
    from, as "ZD-<id>" - the dashboard renders it as a link to the ticket.
    "" only when no ticket is involved (booking-data evidence).

━━ OUTPUT ━━

"tldr" - Varun's two lines, verbatim shape:
    {"our_mistake": "<one line: what Headout did wrong - or 'none: <who/what>' >",
     "our_fix":     "<one line: what we are doing about it>"}

"what_went_wrong" - EXACTLY five headings; this block posts to Slack as-is.
Sub-points only where relevant.
  1. Guest issue - 1-2 concise pointers PER issue raised.
  2. Is the guest's claim accurate? - Yes / Partially True / No.
     Per issue when verdicts differ - and one entry per CLAIM when a
     single issue carries several checkable claims. Each verdict cites
     its deciding evidence WITH its source tag ([experience-page] /
     [booking] / [zendesk] / [dss]).
  3. What actually happened?
     a. Root cause per issue - the concrete failing step, classified
        (Technical|Operational + HO|SP|AI|Guest)
     b. Operational failure, if any - name the team, CE or RO
     c. SOP/process gap, if any - the missing safeguard: why wasn't this
        caught before the guest was affected
     d. Pattern check - one-off or recurring, with the insight counts and
        their window
  4. Supply Partner escalation
     a. Did CE/RO escalate to SP? Yes / No / N/A
     b. If No: why - not partnered / email opt-out / SP on DND / not
        warranted. If the SP has failed to respond or repeatedly failed us,
        say whether BDM escalation is raised. "Awaiting SP" is never the
        end state.
  5. Fixes
     a. Team(s)/stakeholder(s) to evaluate the gaps - CE / RO / Content /
        Product / Biz / Tech / Escalations, from the evidence
     b. Corrective actions taken or proposed, briefly
     c. Durable prevention where warranted - PSI, checkout content, ticket
        checker, config change - with an owner. Scope by ROI, not blanket.

"booking_logs" - numbered, one line per meaningful event, telegraph style,
  chronological: "1. 22 Jul 15:22 - booking-in-progress email; tickets
  promised in 2h". Machinery only where it explains the failure.

"flags" - run ALL CE checks, ALL RO checks, and the checklist(s) for EVERY
  routed scenario, silently. Return ONLY failures and items warranting
  attention: {"flag", "team": "CE|RO|SP|content|tech|other", "evidence",
  "zd_ref"}. A clean run returns []. Never return passing checks. A correct
  out-of-policy denial is NOT a flag (rule 6).

"support_interaction" - CE's half: each guest touchpoint with when, channel,
  what happened, any CE miss flagged inline, and the ticket it lives on.
  State explicitly if no guest contact was found.

"sp_interaction" - RO's half, from the side conversations: was the guest's
  issue raised with the SP, when, what came back, response time. If none:
  state first whether escalation was possible (partnered + opt-out) before
  calling it a gap.

"sop_compliance" - the needle check, one object:
  expected = what DSS/standing policy prescribed for this situation;
  actual = what CE/RO actually did per the timeline;
  verdict = followed | deviated | unknown. detail carries the one-line
  story - including denial -> persistence -> HOC when that is what happened,
  which is FOLLOWED, not a deviation.

"issue_specific_answers" - ONLY questions about the guest's experience
  issue itself, drawn from the issue-type diagnostics (e.g. Meeting Point:
  did we know the MP changed / voucher MP vs variant name vs true MP;
  Tickets: delivery window disclosed at checkout, technical vs operational
  non-delivery; Guide: why absent, working SP contact on file). NOTHING
  about how the team handled it - handling lives in flags,
  support_interaction and sop_compliance. Each answer Yes/No/Unknown +
  source-tagged evidence.

"prevention" - ORM-ownable first; cross-team labelled with the team.

Return ONLY valid JSON:
{
  "tldr": {"our_mistake": "...", "our_fix": "..."},
  "what_went_wrong": {
    "guest_issues":  [{"issue": "...", "claim_accuracy": "Yes|Partially True|No", "evidence": "[source] ..."}],
    "what_happened": {"root_causes": [{"issue": "...", "cause": "...",
                       "classification": "Technical|Operational + HO|SP|AI|Guest"}],
                      "operational_failure": "...|null", "sop_gap": "...|null",
                      "pattern": "<one-off|recurring - counts + window>"},
    "sp_escalation": {"escalated": "Yes|No|N/A", "detail": "..."},
    "fixes":         {"teams": ["..."], "actions": ["..."],
                      "prevention": "...", "owner": "...|null"}
  },
  "booking_logs":         ["1. <time> - <event>; <outcome>", ...],
  "flags":                [{"flag": "...", "team": "...", "evidence": "...", "zd_ref": "ZD-... or ''"}],
  "support_interaction":  [{"time": "...", "channel": "...", "summary": "...", "ce_miss": "...|null", "zd_ref": "ZD-... or ''"}],
  "sp_interaction":       {"possible": true|false, "reason_if_not": "...",
                           "raised": "Yes|No|N/A", "detail": "...", "zd_ref": "ZD-... or ''"},
  "sop_compliance":       {"dss_available": true|false, "expected": "...", "actual": "...",
                           "verdict": "followed|deviated|unknown", "detail": "...", "zd_ref": "ZD-... or ''"},
  "issue_specific_answers": {"<experience question>": "Yes|No|Unknown ([source] <evidence>)"},
  "prevention": "..."
}"""


def _block(title: str, items, numbered=True) -> str:
    if not items:
        return ""
    lines = [f"\n━━ {title} ━━"]
    for i, c in enumerate(items):
        lines.append(f"{i+1}. {c}" if numbered else str(c))
    lines.append("━" * 40)
    return "\n".join(lines)


def build_proposed(d, review_body, review_id, checklist, scenarios_routed):
    booking = {k: v for k, v in (d.booking or {}).items()
               if k not in ("_match", "timeline_raw")}

    raw_lines = []
    for i, body in enumerate((d.timeline_raw or [])[:20]):
        if body and str(body).strip():
            raw_lines.append(f"[ticket_{i+1}] {str(body)[:600]}")

    tf = {k: v for k, v in (d.ticket_facts or {}).items()
          if v not in (None, "", [], {}, "Unknown")}

    # Scenario checks: only the routed scenarios go in - the prompt says
    # "EVERY routed scenario", so what is routed is what it must see.
    sc = checklist.get("scenarios", {})
    routed = {name: sc[name] for name in scenarios_routed if name in sc}
    sc_lines = []
    for name, items in routed.items():
        sc_lines.append(f"[{name}]")
        sc_lines.extend(f"  {i+1}. {it}" for i, it in enumerate(items))
    scenario_block = _block(
        "SCENARIO CHECKS - every routed scenario, run all", sc_lines,
        numbered=False) if sc_lines else ""

    out = PROPOSED_RCA
    for token, value in {
        "<<REVIEW_ID>>":        review_id,
        "<<L1>>":               d.l1 or "",
        "<<L2>>":               d.l2 or "",
        "<<SUB_THEME>>":        d.sub_theme or "",
        "<<SCENARIOS_ROUTED>>": ", ".join(scenarios_routed) or "(none routed)",
        "<<REVIEW_TEXT>>":      review_body,
        "<<BOOKING>>":          json.dumps(booking, default=str),
        "<<TIMELINE>>":         json.dumps((d.timeline or [])[:30], indent=2, default=str),
        "<<ZENDESK_RAW>>":      "\n".join(raw_lines) or "(no raw ticket bodies)",
        "<<TICKET_FACTS>>":     json.dumps(tf, indent=2, default=str) if tf else "(no structured facts extracted)",
        "<<INSIGHTS>>":         json.dumps(d.insights or {}, default=str),
        "<<DSS>>":              json.dumps(d.dss_rec or {}, default=str),
        "<<SUPPORT_SUMMARY>>":  d.support_summary or "(none)",
        "<<CE_BLOCK>>":         _block("CE ERROR CHECKS - run ALL every time", checklist.get("ce", [])),
        "<<RO_BLOCK>>":         _block("RO ERROR CHECKS - run ALL every time", checklist.get("ro", [])),
        "<<SCENARIO_BLOCK>>":   scenario_block,
    }.items():
        out = out.replace(token, str(value))
    return out


# ─── Output audit — the four requirements this revision exists for ──────────

_SOURCE_TAGS = ("[experience-page]", "[booking]", "[zendesk]", "[dss]")


def audit(rca: dict, dss_rec: dict) -> list[tuple[str, bool, str]]:
    checks = []

    issues = (rca.get("what_went_wrong") or {}).get("guest_issues") or []
    untagged = [i.get("issue", "?")[:40] for i in issues
                if not any(t in str(i.get("evidence", "")) for t in _SOURCE_TAGS)]
    checks.append((
        "every claim verdict names its source",
        bool(issues) and not untagged,
        f"{len(issues)} issue(s); untagged: {untagged or 'none'}"))

    si = rca.get("support_interaction") or []
    missing = sum(1 for row in si if isinstance(row, dict) and "zd_ref" not in row)
    checks.append((
        "every support_interaction row has zd_ref",
        bool(si) and missing == 0,
        f"{len(si)} row(s), {missing} missing zd_ref"))

    sp = rca.get("sp_interaction") or {}
    checks.append((
        "sp_interaction has zd_ref",
        isinstance(sp, dict) and "zd_ref" in sp,
        f"keys: {sorted(sp.keys()) if isinstance(sp, dict) else type(sp).__name__}"))

    flags = rca.get("flags") or []
    fmissing = sum(1 for f in flags if isinstance(f, dict) and "zd_ref" not in f)
    checks.append((
        "every flag has zd_ref",
        fmissing == 0,
        f"{len(flags)} flag(s), {fmissing} missing zd_ref"))

    sop = rca.get("sop_compliance") or {}
    checks.append((
        "sop_compliance present with verdict",
        isinstance(sop, dict) and sop.get("verdict") in ("followed", "deviated", "unknown"),
        f"verdict: {sop.get('verdict') if isinstance(sop, dict) else '(absent)'}"))

    dss_empty = not dss_rec or dss_rec.get("match_score", 1) == 0
    if dss_empty:
        honest = isinstance(sop, dict) and sop.get("dss_available") is False
        checks.append((
            "DSS degradation honest (needle unavailable, no invented policy)",
            honest,
            f"dss_rec was {'empty' if not dss_rec else 'match_score 0'}; "
            f"dss_available={sop.get('dss_available') if isinstance(sop, dict) else '?'}"))
    else:
        checks.append((
            "DSS needle was available and used",
            isinstance(sop, dict) and sop.get("dss_available") is True,
            f"match_score={dss_rec.get('match_score')}"))
    return checks


def show(rca: dict):
    def sec(t):
        print(f"\n── {t} " + "─" * max(0, 70 - len(t)))

    tldr = rca.get("tldr") or {}
    sec("TLDR")
    print(f"our mistake: {tldr.get('our_mistake', '?')}")
    print(f"our fix:     {tldr.get('our_fix', '?')}")

    wwr = rca.get("what_went_wrong") or {}
    sec("1. GUEST ISSUES + 2. CLAIM ACCURACY")
    for i in wwr.get("guest_issues") or []:
        print(f"• {i.get('issue')}\n    accuracy: {i.get('claim_accuracy')}  |  {i.get('evidence')}")
    wh = wwr.get("what_happened") or {}
    sec("3. WHAT ACTUALLY HAPPENED")
    for rc in wh.get("root_causes") or []:
        print(f"• [{rc.get('classification')}] {rc.get('issue')}: {rc.get('cause')}")
    print(f"operational failure: {wh.get('operational_failure')}")
    print(f"sop gap:             {wh.get('sop_gap')}")
    print(f"pattern:             {wh.get('pattern')}")
    spx = wwr.get("sp_escalation") or {}
    sec("4. SP ESCALATION")
    print(f"escalated: {spx.get('escalated')}  |  {spx.get('detail')}")
    fx = wwr.get("fixes") or {}
    sec("5. FIXES")
    print(f"teams: {fx.get('teams')}  owner: {fx.get('owner')}")
    for a in fx.get("actions") or []:
        print(f"• {a}")
    print(f"prevention: {fx.get('prevention')}")

    sec("BOOKING LOGS")
    for line in rca.get("booking_logs") or []:
        print(line)

    sec("FLAGS (failures only)")
    flags = rca.get("flags") or []
    if not flags:
        print("(clean run)")
    for f in flags:
        print(f"• [{f.get('team')}] {f.get('flag')}  ({f.get('zd_ref') or 'no ticket'})\n    {f.get('evidence')}")

    sec("SUPPORT INTERACTION")
    for row in rca.get("support_interaction") or []:
        miss = f"  CE MISS: {row.get('ce_miss')}" if row.get("ce_miss") else ""
        print(f"• {row.get('time')} [{row.get('channel')}] {row.get('summary')} "
              f"({row.get('zd_ref') or 'no ticket'}){miss}")

    sp = rca.get("sp_interaction") or {}
    sec("SP INTERACTION")
    print(f"possible: {sp.get('possible')}  raised: {sp.get('raised')}  "
          f"({sp.get('zd_ref') or 'no ticket'})")
    print(sp.get("detail") or sp.get("reason_if_not") or "")

    sop = rca.get("sop_compliance") or {}
    sec("SOP COMPLIANCE (DSS needle)")
    print(f"dss_available: {sop.get('dss_available')}  verdict: {sop.get('verdict')}")
    print(f"expected: {sop.get('expected')}")
    print(f"actual:   {sop.get('actual')}")
    print(f"detail:   {sop.get('detail')}  ({sop.get('zd_ref') or 'no ticket'})")

    sec("ISSUE-SPECIFIC ANSWERS (experience only)")
    for k, v in (rca.get("issue_specific_answers") or {}).items():
        print(f"• {k}: {v}")

    sec("PREVENTION")
    print(rca.get("prevention") or "")


async def run(args):
    from server.config import is_live
    from server.services import claude as CL
    from server import prompts as P
    from server.db import SessionLocal, Review
    from server.services.rca_checklist import get_checklist

    if not is_live("anthropic"):
        print("anthropic is not connected on this machine - run this where the "
              "server runs.")
        return 2

    db = SessionLocal()
    try:
        r = db.query(Review).filter(Review.id == args.review).first()
        if not r or not r.draft:
            print(f"No draft stored for {args.review} - run the pipeline for it "
                  f"first; this tool reads the saved draft, it does not refetch.")
            return 1
        d = r.draft
        review_body = r.body_english or r.body_original or ""

        for name, val in (("booking", d.booking), ("timeline", d.timeline)):
            if not val:
                print(f"Draft has no {name} - the prompt would be reasoning "
                      f"about nothing. Re-run the pipeline first.")
                return 1

        checklist = await get_checklist(d.l1, d.l2)

        from server.checklist import scenarios_for, SCENARIO_CHECKS
        routed = []
        if d.primary_scenario:
            routed.append(d.primary_scenario)
        else:
            routed.append(scenarios_for(d.l1, d.l2, d.sub_theme)["primary"])
        routed += [s for s in (d.overlay_scenarios or []) if s not in routed]
        routed = [s for s in routed if s in SCENARIO_CHECKS]

        print(f"review {args.review}  bid {(d.booking or {}).get('id')}")
        print(f"L1={d.l1!r} L2={d.l2!r} sub_theme={d.sub_theme!r}")
        print(f"routed scenarios: {routed or '(none)'}")
        print(f"timeline {len(d.timeline or [])} events, "
              f"raw bodies {len(d.timeline_raw or [])}, "
              f"ticket_facts {'yes' if d.ticket_facts else 'no'}, "
              f"insights.redemption "
              f"{'yes' if (d.insights or {}).get('redemption') else 'NO - experience-page verification will be Unknown'}, "
              f"dss match_score {(d.dss_rec or {}).get('match_score', '(empty)')}")

        async def call_and_parse(prompt_text, label):
            print(f"\ncalling model for {label} ({len(prompt_text)} chars) ...")
            raw = await CL._call(prompt_text, max_tokens=6000)
            try:
                return json.loads(CL._strip_fences(raw))
            except Exception:
                print(f"{label}: response did not parse. First 600 chars:\n{raw[:600]}")
                return None

        if args.only in ("", "current"):
            cur_prompt = P.rca_v3_prompt(
                review_body, d.booking, d.timeline, d.insights, d.dss_rec,
                d.l1 or "", d.l2 or "", d.sub_theme or "",
                d.support_summary or "", checklist, args.review,
                timeline_raw=d.timeline_raw, ticket_facts=d.ticket_facts)
            cur = await call_and_parse(cur_prompt, "CURRENT")
            if cur:
                print("\n════ CURRENT prompt (what ships today) ════")
                print(f"tldr: {cur.get('tldr')}")
                print(f"wwr_chain: {len(cur.get('wwr_chain') or [])} steps, "
                      f"checklist_answers: {len(cur.get('checklist_answers') or [])} "
                      f"(all checks, passes included)")

        if args.only in ("", "proposed"):
            prop_prompt = build_proposed(d, review_body, args.review, checklist, routed)
            if args.dump_prompt:
                print(prop_prompt)
            prop = await call_and_parse(prop_prompt, "PROPOSED")
            if prop:
                print("\n════ PROPOSED prompt (this file only) ════")
                show(prop)
                print("\n════ AUDIT - the four requirements ════")
                ok = True
                for name, passed, detail in audit(prop, d.dss_rec or {}):
                    mark = "PASS" if passed else "FAIL"
                    ok = ok and passed
                    print(f"[{mark}] {name}\n       {detail}")
                if args.dump_json:
                    print("\n" + json.dumps(prop, indent=2))
                print("\nNothing was written. server/prompts.py is untouched.")
                return 0 if ok else 3
    finally:
        db.close()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", required=True, help="review id, e.g. tp_1784722373_497379")
    ap.add_argument("--only", default="", choices=["", "current", "proposed"])
    ap.add_argument("--dump-prompt", action="store_true",
                    help="print the full proposed prompt before calling the model")
    ap.add_argument("--dump-json", action="store_true",
                    help="print the proposed output as raw JSON after the sections")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
