"""
All Claude prompts. Edit these in Replit to tune RCA quality.

Three prompts:
  1. translation_prompt      — translate review to English
  2. rca_generation_prompt   — full RCA + signals from all context
  3. response_draft_prompt   — guest reply from canned responses
"""
import json
from server.signals import ALL_SIGNALS


def translation_prompt(body: str, lang: str) -> str:
    return f"""Translate this Trustpilot review into clear English.
Preserve tone exactly — frustration, sarcasm, urgency. Translate, do not paraphrase.
Return ONLY the translation, no preamble.

Original ({lang}):
{body}"""


def rca_generation_prompt(review_text, booking, timeline, insights, dss) -> str:
    """
    Passes the full Zendesk timeline (no truncation) split into:
    - Chronological full log (all actors)
    - Guest messages only
    - CO / support team messages only
    - SP messages only (if any)

    Claude is instructed to produce a verbatim interaction log for
    customerInteractionCO — no summarising, no editorial, just facts.
    """

    guest_events = []
    co_events    = []
    sp_events    = []
    all_events   = []

    for t in (timeline or []):
        time_str    = t.get("time", "")
        actor_label = t.get("actor_label", t.get("actor", "?"))
        text        = t.get("summary", "")
        flag        = f"  ⚑ {t['flag'].upper()}" if t.get("flag") else ""
        is_internal = not t.get("public", True)
        vis         = " [internal note]" if is_internal else ""

        line = f"{time_str}  [{actor_label}{vis}]{flag}\n  {text}"
        all_events.append(line)

        actor_key = t.get("actor", "")
        if actor_key == "guest":
            guest_events.append(line)
        elif actor_key in ("co", "system"):
            co_events.append(line)
        elif actor_key == "sp":
            sp_events.append(line)

    sep = "\n" + "─" * 40 + "\n"
    timeline_str = sep.join(all_events) if all_events else "(no Zendesk events found)"
    guest_str    = sep.join(guest_events)  if guest_events  else "None found"
    co_str       = sep.join(co_events)     if co_events     else "None found"
    sp_str       = sep.join(sp_events)     if sp_events     else "No SP interaction found"

    return f"""You are writing a Root Cause Analysis (RCA) for a Trustpilot review at Headout.
Headout is a booking intermediary that sells tours and tickets operated by supply partners (SPs).

=== TRUSTPILOT REVIEW (English) ===
{review_text}

=== BOOKING ===
{json.dumps(booking or {}, indent=2)}

=== FULL ZENDESK TIMELINE (all actors, chronological) ===
{timeline_str}

=== GUEST MESSAGES (extracted) ===
{guest_str}

=== CO / SUPPORT TEAM MESSAGES (extracted) ===
{co_str}

=== SP / SUPPLY PARTNER MESSAGES (extracted) ===
{sp_str}

=== INSIGHTS ===
{json.dumps(insights or {}, indent=2)}

=== DSS POLICY RECOMMENDATION ===
{json.dumps(dss or {}, indent=2)}

---

RULES — follow these exactly:
1. Use only facts from the data above. Do not invent or assume anything not in the timeline.
2. Use exact timestamps from the timeline. Do not paraphrase times.
3. If the review contradicts the timeline (e.g. guest says "no one responded" but timeline shows a reply), note the discrepancy factually — do not resolve it in favour of either side.
4. For "whatWentWrong": walk through what happened chronologically. State what broke and why at the end. No adjectives or judgement — just what happened.
5. For "customerInteractionCO": write a verbatim factual log of every touchpoint between the guest and the support team. Format: one line per event — timestamp, who said/did what, exactly what was said or done. Do not summarise. Do not add your own interpretation. Do not skip any touchpoint. Include Minded AI responses if present. Example format:
   "24 Apr 02:56 — Guest emailed: [exact content or close paraphrase of their message]
    24 Apr 05:17 — CO Agent replied: [exact content or close paraphrase]
    24 Apr 12:36 — Guest followed up: [exact content]
    24 Apr 12:37 — Minded AI sent automated acknowledgement: [exact content]
    24 Apr 13:33 — CO Agent internal note: [content]
    24 Apr 13:35 — CO Agent sent tickets and apology: [content]"
6. For "spIssueInteraction": only populate if the SP was directly involved. Otherwise write exactly: None
7. Signals must only come from this exact list. Do not invent new signal labels:
{json.dumps(ALL_SIGNALS, indent=2)}

Return ONLY a valid JSON object with these exact keys. No markdown, no fences, no preamble:
{{
  "queryIssueType":        "Short issue label e.g. 'Delayed FF — Did not receive tickets'",
  "whatWentWrong":         "Chronological account of what happened and why. Facts only, no adjectives.",
  "customerInteractionCO": "Verbatim factual log of every guest<>support touchpoint. Timestamp + actor + what was said/done. No summarising.",
  "spIssueInteraction":    "What the SP did or failed to do, with timestamps. Or: None",
  "areaOfImproving":       "The specific process, system, or monitoring gap that needs fixing.",
  "solutionOffered":       "Exactly what was given — refund amount, HOC percentage, replacement tickets, etc.",
  "raisedTeam1":           "Primary team handle e.g. '@inv-ops-on-call'",
  "raisedTeam2":           "Secondary team handle or empty string",
  "bookingsImpacted":      "From insights data e.g. '2 of 18 bookings same VID same day'",
  "similarQueries":        "From insights data — number of similar open tickets",
  "avgRating":             "From insights data e.g. 'TGID 4.5 · TID-VID 4.2 (last 4w)'",
  "followUpNeeded":        "Yes or No",
  "reviewTakedownSent":    "Yes or No or Pending",
  "dssCovers":             "Yes or No or Partial",
  "otherComments":         "Anything else worth flagging. Can be empty string.",
  "signals":               ["3 to 8 labels from the approved signal list only"]
}}"""


def response_draft_prompt(review_text: str, rca_issue_type: str,
                           solution: str, canned_responses: str) -> str:
    return f"""You are drafting a public reply to a Trustpilot review on behalf of Headout's CX team.
This reply will be posted publicly. Write it accordingly.

REVIEW:
{review_text}

WHAT HAPPENED:
Issue type: {rca_issue_type}
Solution offered: {solution}

CANNED RESPONSE LIBRARY:
{canned_responses}

Instructions:
- Use the most relevant canned response as a base.
- Personalise it — reference the specific issue and the resolution that was given.
- Acknowledge the guest's frustration directly. Do not be defensive or dismissive.
- Confirm what was done to resolve it.
- 3 to 5 sentences. No bullet points. No headings.
- Do NOT use placeholder text like [Name], [Date], [Booking ID].
- Do NOT promise anything that was not in the solution offered above.
- Return ONLY the reply text. Nothing else.
"""
