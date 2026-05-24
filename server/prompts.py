"""
All Claude prompts. Edit these in Replit to tune output quality.

Three prompts:
  1. translation_prompt      — translate review to English, preserve tone
  2. rca_generation_prompt   — full RCA + signals from all context
  3. response_draft_prompt   — public Trustpilot reply from canned scenarios
"""
import json
from server.signals import ALL_SIGNALS


def translation_prompt(body: str, lang: str) -> str:
    return f"""Translate this Trustpilot review into clear English.
Preserve tone exactly — frustration, sarcasm, urgency. Translate, do not paraphrase.
Return ONLY the translation. No preamble, no label, no explanation.

Original ({lang}):
{body}"""


def rca_generation_prompt(review_text, booking, timeline, insights, dss) -> str:
    """
    Passes the full Zendesk timeline split three ways so Claude has the complete
    picture: full chronological log, guest-only messages, CO-only messages, SP-only.

    The customerInteractionCO field uses the exact format validated by the Headout
    team in the Retool workflow: chronological bullet points with date/time, actor,
    action, and resolution. No AI editorialising.
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

    sep          = "\n" + "─" * 40 + "\n"
    timeline_str = sep.join(all_events)    if all_events   else "(no Zendesk events found)"
    guest_str    = sep.join(guest_events)  if guest_events else "None found"
    co_str       = sep.join(co_events)     if co_events    else "None found"
    sp_str       = sep.join(sp_events)     if sp_events    else "No SP interaction found"

    return f"""You are writing a Root Cause Analysis (RCA) for a Trustpilot review at Headout.
Headout is a booking intermediary that sells tours and tickets operated by supply partners (SPs).

=== TRUSTPILOT REVIEW (English) ===
{review_text}

=== BOOKING ===
{json.dumps(booking or {}, indent=2)}

=== FULL ZENDESK TIMELINE (all actors, chronological) ===
{timeline_str}

=== GUEST MESSAGES ONLY ===
{guest_str}

=== CO / SUPPORT TEAM MESSAGES ONLY ===
{co_str}

=== SP / SUPPLY PARTNER MESSAGES ONLY ===
{sp_str}

=== INSIGHTS (last 90 days) ===
{json.dumps(insights or {}, indent=2)}

=== DSS POLICY RECOMMENDATION ===
{json.dumps(dss or {}, indent=2)}

---

RULES — follow exactly:
1. Only use facts from the data above. Do not invent anything not in the timeline or booking.
2. Use exact timestamps from the timeline. Do not paraphrase times.
3. If the review contradicts the timeline, note the discrepancy factually — do not take sides.
4. For "whatWentWrong": walk through events chronologically. State the root cause at the end.
   No adjectives, no judgement — only what happened and when.
5. For "customerInteractionCO": generate concise bullet points, one per interaction,
   listed chronologically. Each bullet must include:
   - Date and time (from the timeline — if not detectable, omit it)
   - What the customer said or did
   - How the support team responded and what solution was offered
   If Minded AI handled any interaction, name it explicitly.
   If no direct communication is found, write exactly:
   "No direct interaction found between the customer and the support team."
6. For "spIssueInteraction": only populate if the SP was directly involved.
   Otherwise write exactly: None
7. Signals must only come from this exact list — do not invent labels:
{json.dumps(ALL_SIGNALS, indent=2)}

Return ONLY a valid JSON object with these exact keys. No markdown, no fences, no preamble:
{{
  "queryIssueType":        "Short label e.g. 'Delayed FF — Did not receive tickets'",
  "whatWentWrong":         "Chronological account. Root cause stated at the end. Facts only.",
  "customerInteractionCO": "Chronological bullet points: date/time · actor · what was said/done · resolution.",
  "spIssueInteraction":    "SP involvement with timestamps. Or: None",
  "areaOfImproving":       "The specific process, system, or monitoring gap that caused this.",
  "solutionOffered":       "Exactly what was given — refund amount, HOC %, replacement tickets, credits.",
  "raisedTeam1":           "Primary team handle e.g. '@inv-ops-on-call'",
  "raisedTeam2":           "Secondary team handle or empty string",
  "bookingsImpacted":      "From insights — e.g. '2 of 18 bookings same VID same day'",
  "similarQueries":        "From insights — number of similar open tickets",
  "avgRating":             "From insights — e.g. 'TGID 4.5 · TID-VID 4.2 (last 90d)'",
  "followUpNeeded":        "Yes or No",
  "reviewTakedownSent":    "Yes or No or Pending",
  "dssCovers":             "Yes or No or Partial",
  "otherComments":         "Anything else worth flagging. Empty string if nothing.",
  "signals":               ["3 to 8 labels from the approved signal list only"]
}}"""


def response_draft_prompt(review_text: str, rca_issue_type: str,
                           solution: str, canned_responses: str,
                           guest_name: str = "") -> str:
    name_hint = f"The guest's name is {guest_name}." if guest_name else ""

    return f"""You are drafting a public reply to a Trustpilot review on behalf of Headout's CX team.
This reply will appear publicly on Trustpilot. Write it accordingly — professional, warm, specific.

REVIEW:
{review_text}

WHAT HAPPENED (from RCA):
Issue type: {rca_issue_type}
Solution offered: {solution}
{name_hint}

CANNED RESPONSE SCENARIOS:
{canned_responses}

INSTRUCTIONS:
1. Match the issue to the most relevant canned scenario from the library above.
2. Use that scenario as your base — do not invent a response from scratch.
3. Personalise it: reference the specific issue and the resolution that was actually given.
4. Acknowledge the guest's frustration genuinely. Do not be defensive.
5. Fill in ALL placeholders with real values:
   - <Name> or <first name> → use the guest's actual name if known, otherwise remove it
   - {{date}} or <DATE> → use the date from the solution or review context
   - <X%> or <X> → use the actual percentage or amount from the solution offered
   - <ETA> → use the actual timeframe from the solution
   - <$X> or <amount> → use the actual credit/refund amount
   - If any placeholder value is genuinely unknown, remove the placeholder entirely
   - NEVER leave a placeholder like <Name> or {{date}} in the final output
6. Keep it 3–5 sentences. No bullet points. No headings.
7. Do NOT promise anything that was not in the solution offered.
8. Return ONLY the reply text. Nothing else.
"""


# ── Embedded canned scenarios (fallback when Sheet is unavailable) ──────────
# These are the 10 confirmed scenarios from the ORM Response Generator,
# cleaned and deduplicated. The Sheet version takes priority when available.
EMBEDDED_CANNED = """
[UNABLE TO TRACE BOOKING]
Hey <Name>, I'm sorry things didn't go as planned, and I'd love to fix this for you right away. Please share your email ID or the booking ID used for your booking here — https://bit.ly/hedout. Once we have your details, our team will dive right in to resolve it ASAP. Thank you so much for your understanding and patience. I'll make sure we turn this around for you!

[TICKETS ALREADY SENT — RESEND]
Hey <first name>, oh no, I'm sorry to hear you didn't receive your tickets. I've just checked our system, which confirms the tickets were sent to you on {date}. Please could you check your Spam folder, just in case? I've resent them now — you should receive them shortly. I hope you have a wonderful experience!

[DELAYED TICKETS — NOT YET SENT]
Hey <Name>, I'm so sorry for the delay. Due to a technical constraint, we weren't able to send your tickets as quickly as we usually do. Not to worry — our team is working on it and you should receive your tickets via email within the next <ETA>. I've also added a coupon for <X%> off your next experience with us as an apology.

[WRONG / INVALID TICKETS — REFUND]
Hey <first name>, I sincerely apologise for the oversight on our part. This is definitely not the Headout experience we'd want you to have. I've refunded the full amount to your original payment method — this will reflect within 2–3 business days depending on your bank. I've also emailed you a coupon for <X%> off your next Headout experience.

[OUR ERROR — REFUND + CREDITS]
Hey <first name>, I'm very sorry for what happened. This is not the Headout experience we strive to provide, and I've personally flagged this with all concerned teams. I've refunded the full amount to your original payment method and added <$X> in Headout credits to your account as compensation. You'll receive email confirmation of both shortly.

[SP CANCELLED LAST MINUTE]
Hey <first name>, I've processed a full refund for your booking and added 25% Headout credits to your account as an apology for the cancellation. I'm truly sorry for the disruption to your plans — this is not the standard we hold our partners to, and we're addressing this with them directly.

[OVERCROWDED / VENUE ISSUE]
Hey <name>, I'm very sorry for the experience you had with us. During peak seasons, the venue does open entry to a larger number of people, which we know can affect your experience. We'll work with the venue on improving crowd management going forward. I've emailed you a coupon for <X%> off your next experience with us. I hope this helps brighten your day!

[REFUND DELAY]
Hi <name>, I'm very sorry to hear that you haven't received the refund yet. I can confirm that the refund was processed from our end on {date}. The Acquirer Reference Number (ARN) for your refund is: <ARN>. Please have a chat with your bank quoting this reference — they'll be able to confirm the status. Apologies for the inconvenience.

[CUSTOMER ERROR / MISSED TOUR]
Hey <first name>, I'm sorry to hear you missed your experience. I've added <$X> in Headout credits to your account for your next booking with us. I hope we get the chance to give you the experience you deserve soon!

[GENERAL COMPLAINT — INVESTIGATION]
Hey <Name>, I really appreciate you sharing your feedback with us. I'm sorry your experience didn't meet expectations — this is not the standard we strive to provide. I've shared this with our team and we're looking into it. If there's anything specific I can help you with, please don't hesitate to reach out.
"""
