#!/usr/bin/env python3
"""
Run a PROPOSED timeline prompt against a real booking, and print both results.

    python3 tools/try_timeline_prompt.py --bid 32908218
    python3 tools/try_timeline_prompt.py --bid 32908218 --review tp_1784722373_497379
    python3 tools/try_timeline_prompt.py --bid 32908218 --only proposed

Changes nothing. It imports the pipeline's own functions read-only, builds the
proposed prompt from a copy that lives in THIS file, and prints what each
prompt produces so the two can be compared before anything ships.

Why a separate file: editing server/prompts.py to see what a change does means
the change is already live for every review the pipeline touches. The point of
this is to look first.

Raw events are not stored anywhere - the draft keeps the SHAPED timeline and the
raw bodies, but not the indexed raw events the prompt is built from - so this
re-fetches them from Zendesk. That is a read, and the only one it makes.

Needs the Zendesk and Anthropic connections, so it runs on the box the server
runs on, not on a laptop.
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── The proposed prompt ────────────────────────────────────────────────────
#
# AS OF NOW THIS IS WHAT SHIPPED. server/prompts.py carries the same text, so
# a --only current / --only proposed comparison shows no difference until this
# copy is edited. That is the intended state: this file is the bench where the
# next change gets tried before it goes near the pipeline.
#
# One principle: the model writes prose, and never states a fact.
#
# The current prompt asks it to infer the channel from the raw body ("Infer it
# from the raw body. Do NOT default everything to email"), to decide which
# events are internal noise, and to copy timestamps verbatim. Two of those
# three are facts Zendesk already recorded - via.channel says the channel, and
# the classifier says what is machinery - so the model is being asked to
# re-derive what is already known, and any disagreement silently wins over the
# truth.
#
# This version takes those decisions away from it and leaves it the two things
# a model is actually good at: writing a short label and one clear sentence.
PROPOSED = """You are shaping raw Zendesk support events into a clean, human-readable
timeline for an internal ORM dashboard. Headout CX analysts will read this - it must
be factual and concise.

=== BOOKING METADATA ===
{booking_json}

=== REVIEW ===
Published: {review_pub_date}
Body: {review_body}

=== RAW EVENTS (idx = sequential order) ===
{events_json}

=== WHAT THIS TIMELINE IS ===
A clear, human story of the guest's journey - the booking, any contact with
support, what we did in response, and how it ended. A CX analyst should read it
top-to-bottom and understand: did the guest reach out, HOW, WHY, WHAT we did,
and whether the booking was fulfilled or resolved.

=== WHAT YOU DECIDE, AND WHAT YOU MUST NOT TOUCH ===
You are writing two things and nothing else: a short LABEL and a one-sentence
SUMMARY for each event, plus which events collapse together.

These fields are facts recorded by Zendesk. Copy each one through EXACTLY as
given. Never infer, correct, reformat or fill one in:
    time, thread, actor, ticket_id, is_internal
If a value looks wrong to you, copy it anyway. A wrong value that survives is
findable; one you quietly corrected is not.

=== INSTRUCTIONS ===
1. BOOKENDS - inject exactly two, not present in raw_events. They frame the
   timeline and are system markers, NOT guest or agent speech: copy their
   idx_range, time, thread, actor and label EXACTLY as written. Never a person
   actor, never a conversation thread, never a name or a quote.
   - FIRST - Booking created:
     {{"idx_range": [], "time": "{booking_date_fmt}",
       "thread": "booking", "actor": "creation",
       "label": "Booking created",
       "summary": "<WHAT the guest booked - variant / pax / options selected, and
       notably any upsell or add-on NOT selected at checkout. From the booking
       metadata. Do NOT write the full experience name.>", "keep": true}}
   - LAST - Review posted:
     {{"idx_range": [], "time": "{review_date_fmt}",
       "thread": "review", "actor": "review",
       "label": "Review posted",
       "summary": "Negative Trustpilot review posted, BID referenced.", "keep": true}}

2. KEEP EVERY EVENT. keep: false only for an event with no readable content at
   all - an empty body, a bare signature, a logo. Do NOT drop machinery:
   is_internal already marks it and the dashboard hides it behind a toggle that
   says how many it hid. An event you drop cannot be recovered or counted.

3. COLLAPSE consecutive events describing ONE action at one moment; list every
   collapsed idx in idx_range. Collapse only within the same thread and the
   same actor - merging a guest message into a system row destroys both. No
   "(xN)" in the label.

4. LABELS - short and plain, from this vocabulary:
   "Booking created", "Tickets sent", "Guest reached out", "Guest reply",
   "CE response", "SP response", "Refund issued", "Booking cancelled",
   "Escalated to SP", "Review posted".
   THE LABEL MUST MATCH THE ACTOR. This is not a style preference - a label
   naming someone who did not act is a false statement about a person, and it
   is the one error here that can end up quoted back to a customer.
     actor "guest"   -> and ONLY then: "Guest reached out" (first contact) or
                        "Guest reply" (any later message). Never write that the
                        guest contacted, asked, replied or complained unless
                        this event IS the guest's own words.
     actor "co"      -> "CE response"
     actor "sp"      -> "SP response"
     actor "system" / "ai" -> the machine action AND ITS OUTCOME: "Fulfilment
                        run failed", "Tickets sent", "Booking-in-progress email
                        sent", "Credentials generated". Name the specific
                        machine that ran: a fulfilment attempt is "Fulfilment
                        run ...", never the name of the email beside it.
                        internal_reason "booking-info" is NOT a run. It is the
                        booking dump Zendesk posts onto the ticket - pax,
                        price, vendor, instructions. Label it "Booking details
                        posted" and summarise the facts in it. Do not write
                        that anything ran or was attempted: naming a
                        fulfilment attempt that never happened invents the
                        event an RCA then goes looking for.
                        Say what happened, not what was tried -
                        "Fulfilment run attempted" leaves the reader to find
                        out whether it worked, and whether it worked is the
                        whole reason the row is here.
                        An automated email ABOUT the guest is a system event,
                        not the guest speaking.
     actor "system" on thread "chat" -> a chat TRANSCRIPT: ONE comment holding
                        the whole conversation, posted by Zendesk rather than
                        by either party, which is why its actor is system.
                        Label it "Guest chat". Do NOT label it as a transcript
                        or a log - the log is the container, the conversation
                        is the event, and calling it bookkeeping buries the
                        only record of what the guest said.
                        The summary carries what the guest raised and what they
                        were told, in that order. Attribute inside the summary
                        ("Guest asked ... ; agent said ...") - that is accurate
                        about a transcript in a way the actor field cannot be.
   LABEL EACH EVENT FROM ITS OWN BODY, never from the event beside it. On
   booking 32908218 the Selenium fulfilment blob and the booking-in-progress
   email - two different things one second apart - both came back
   "Booking-in-progress email sent". The fulfilment attempt took the label of
   the mail it sat next to and disappeared, and that attempt is often the
   whole root cause.
   Repeated labels are NOT automatically wrong. Three fulfilment retries are
   three events that each say "Fulfilment run failed", and forcing them to
   differ would invent a distinction the data does not have. Before you write
   a label, ask of the BODIES, not of the labels:
     - Same action recorded more than once at one moment? -> ONE event.
       Collapse under rule 3 and list every idx.
     - Same KIND of action happening again at a different time? -> SEPARATE
       events, and the same label on both is correct. Let the summary carry
       what differed - the attempt number, the outcome, what changed.
     - Different actions? -> different labels, each from its own body.
   No ticket IDs, no "[ZD-xxxxx]", no "(xN)".

5. SUMMARIES - ONE short sentence. Aim for 12-20 words, hard limit 120
   characters. A CX analyst scans this column; anything longer stops being
   scannable and becomes something to read.
   - Guest contact -> WHY they reached out.
   - Our response -> WHAT WE DID or OFFERED.
   - Fulfilment -> WHAT was delivered and WHEN.
   - Refund / outcome -> the amount and terms.
   - Machinery -> the outcome in as few words as possible. The label already
     says what ran, so the summary carries only what came back:
     "No ticket URLs returned." not "The Selenium run attempted ticket
     retrieval from the vendor site but returned no ticket URLs."
   Do not restate the label. "Guest reply / Guest replied asking about their
   tickets" wastes the whole line - the label already said it.
   Keep the specifics that let someone verify it: amounts, pax, reference
   numbers, validity dates, URLs in full. Drop everything else.
   Say only what the event evidences. If the body does not say why the guest
   contacted us, write what it does say - do not supply a motive.
   Strip HTML and signatures. Never quote raw JSON. Never adopt the guest's
   emotional wording.

6. ORDER - Booking created first, events as given, Review posted last. The
   input is already in order; do not re-sort it.

Return ONLY valid JSON - a list of shaped event objects, nothing else:
[
  {{"idx_range": [], "time": "...", "thread": "...", "actor": "...", "label": "...", "summary": "...", "keep": true}},
  ...
]"""


def build_proposed(booking, review_body, review_pub_date, raw_events):
    """Same inputs the live prompt builder gets, formatted into PROPOSED."""
    from server.prompts import _fmt_bookend_time
    bk = booking or {}
    summary = {k: v for k, v in bk.items() if k not in ("_match", "timeline_raw")}
    return PROPOSED.format(
        booking_json=json.dumps(summary, indent=2, default=str),
        review_pub_date=review_pub_date or "unknown",
        review_body=(review_body or "")[:600],
        events_json=json.dumps(raw_events or [], indent=2, default=str),
        booking_date_fmt=_fmt_bookend_time(
            bk.get("date_of_booking") or bk.get("creationDate") or ""),
        review_date_fmt=(_fmt_bookend_time(review_pub_date)
                         if review_pub_date else "unknown"),
    )


def table(events, title):
    print(f"\n{title}")
    print("-" * 118)
    print(f"{'time':<18}{'thread':<9}{'actor':<10}{'label':<24}{'tid':<10}{'int':<5}summary")
    print("-" * 118)
    for e in events:
        print(f"{str(e.get('time',''))[:17]:<18}"
              f"{str(e.get('thread',''))[:8]:<9}"
              f"{str(e.get('actor',''))[:9]:<10}"
              f"{str(e.get('label',''))[:23]:<24}"
              f"{str(e.get('ticket_id',''))[:9]:<10}"
              f"{'yes' if e.get('is_internal') else '':<5}"
              f"{str(e.get('summary',''))[:60]}")
    print("-" * 118)
    print(f"{len(events)} events, "
          f"{sum(1 for e in events if e.get('is_internal'))} internal")


async def run(args):
    from server.config import is_live
    from server.services import zendesk as ZD
    from server.services import claude as CL
    from server import prompts as P

    # A fixture run needs the model only - the events are constructed here, so
    # requiring Zendesk would refuse a run that has nothing to fetch.
    needed = ("anthropic",) if args.fixture else ("zendesk", "anthropic")
    for name in needed:
        if not is_live(name):
            print(f"{name} is not connected on this machine - this tool needs "
                  f"{' and '.join(needed)}. Run it where the server runs.")
            return 2

    booking, review_body, review_pub_date = {}, "", ""
    if args.review:
        # Read the draft for the real booking metadata and review text, so the
        # prompt sees exactly what the pipeline would have shown it.
        # Review lives in server.db, not a models module - there isn't one.
        from server.db import SessionLocal, Review
        db = SessionLocal()
        try:
            r = db.query(Review).filter(Review.id == args.review).first()
            if r:
                review_body = r.body_english or r.body_original or ""
                review_pub_date = (r.received_at.strftime("%Y-%m-%d")
                                   if r.received_at else "")
                if r.draft:
                    booking = r.draft.booking or {}
        finally:
            db.close()
    if not booking:
        from server.services.bigquery_patch import verify_bid
        booking = verify_bid(args.bid) or {"id": args.bid}

    if args.fixture == "retries":
        print("FIXTURE: three fulfilment failures then a success - constructed, "
              "not from Zendesk.\n"
              "Correct output is FOUR separate rows. Three of them may share a "
              "label; what must differ is\nthe summary, carrying attempt number "
              "and what each returned.")
        raw_events = [
            {"idx": i, "time": t, "time_sort": ts, "thread": "email",
             "actor": "system", "ticket_id": "34011333", "is_internal": True,
             "internal_reason": "selenium-run", "author_id": 19012011103,
             "author_role": "admin", "via_channel": "api",
             "requester_id": 60315278707, "raw_body": b}
            for i, (t, ts, b) in enumerate([
                ("22 Jul 15:25 IST", "2026-07-22T09:55:00+00:00",
                 "Selenium run attempted ticket retrieval from the vendor site. "
                 "Attempt 1 of 3. Returned no ticket URLs - vendor page timed out."),
                ("22 Jul 15:32 IST", "2026-07-22T10:02:00+00:00",
                 "Selenium run attempted ticket retrieval from the vendor site. "
                 "Attempt 2 of 3. Returned no ticket URLs - session expired."),
                ("22 Jul 15:44 IST", "2026-07-22T10:14:00+00:00",
                 "Selenium run attempted ticket retrieval from the vendor site. "
                 "Attempt 3 of 3. Returned no ticket URLs - vendor page timed out."),
                ("22 Jul 15:50 IST", "2026-07-22T10:20:00+00:00",
                 "Selenium run attempted ticket retrieval from the vendor site. "
                 "Attempt 4. Returned 2 ticket URLs, partner ref 1022394558263."),
            ])]
        meta = {"ticket_ids": ["34011333"]}
        booking = booking or {"id": args.bid}
    else:
        print(f"booking {args.bid}   review {args.review or '(none)'}")
        print(f"fetching raw events from Zendesk ...")
    # _get_timeline_sync, not get_timeline. get_timeline returns the SHAPED
    # timeline - it runs Claude itself - so calling it here would feed already
    # shaped events into the prompt and pay for an extra model call to do it.
    # The raw events are what a prompt is built from.
        _z = ZD._get_client()
        raw_events, _extracted, meta = await asyncio.get_running_loop().run_in_executor(
            None, ZD._get_timeline_sync, _z, args.bid)
        print(f"{len(raw_events)} raw events, tickets {meta.get('ticket_ids')}")
    if not raw_events:
        print("No Zendesk events for this booking - there is nothing for either "
              "prompt to shape, so any comparison would be of two empty lists.")
        return 1

    # Why each actor came out the way it did. An actor that looks wrong and an
    # actor derived from a failed lookup are different problems with the same
    # symptom, and only the inputs tell them apart.
    print("\nactor decision inputs")
    print("-" * 96)
    print(f"{'time':<18}{'via':<10}{'author':<12}{'role':<12}{'requester':<12}"
          f"{'-> actor':<10}{'internal':<12}")
    print("-" * 96)
    for e in raw_events:
        print(f"{str(e.get('time',''))[:17]:<18}"
              f"{str(e.get('via_channel',''))[:9]:<10}"
              f"{str(e.get('author_id',''))[:11]:<12}"
              f"{str(e.get('author_role',''))[:11]:<12}"
              f"{str(e.get('requester_id',''))[:11]:<12}"
              f"{str(e.get('actor','')):<10}"
              f"{str(e.get('internal_reason','') or '-'):<12}")
    print("-" * 96)

    if args.dump_raw:
        print(json.dumps(raw_events, indent=2, default=str)[:6000])

    async def shape(prompt_text, label):
        raw = await CL.shape_timeline_events(prompt_text)
        shaped = ZD._safe_parse_events(raw)
        if not shaped:
            print(f"\n{label}: response did not parse. First 600 chars:\n"
                  f"{raw[:600]}")
            return []
        # Same provenance re-attachment the pipeline does, so the two tables
        # are comparable. Without it the proposed run would look worse purely
        # because nothing put the facts back.
        by_idx = {e.get("idx"): e for e in raw_events}
        out = []
        for ev in shaped:
            if ev.get("keep") is False:
                continue
            src = None
            for i in (ev.get("idx_range") or []):
                if i in by_idx:
                    src = by_idx[i]
                    break
            merged = dict(ev)
            if src:
                for k in ("time", "thread", "actor", "ticket_id",
                          "is_internal", "internal_reason"):
                    if k in src:
                        merged[k] = src[k]
            merged.pop("idx_range", None)
            merged.pop("keep", None)
            out.append(merged)
        return out

    if args.only in ("", "current"):
        cur = P.zendesk_timeline_shape_prompt(
            booking, review_body, review_pub_date, raw_events)
        table(await shape(cur, "CURRENT"), "CURRENT prompt (what ships today)")

    if args.only in ("", "proposed"):
        prop = build_proposed(booking, review_body, review_pub_date, raw_events)
        table(await shape(prop, "PROPOSED"), "PROPOSED prompt (this file only)")

    print("\nNothing was written. server/prompts.py is untouched.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bid", required=True, help="booking id, e.g. 32908218")
    ap.add_argument("--review", default="", help="review id, for the review text")
    ap.add_argument("--only", default="", choices=["", "current", "proposed"])
    # A booking with a real retry sequence is the only way to check that
    # repeats survive as repeats. Most bookings do not have one, so this feeds
    # a constructed sequence through the same prompt: three fulfilment
    # failures minutes apart, then a success. If they come back as one row, or
    # as three rows with invented differences between them, the rule is wrong -
    # and a retry sequence collapsed into one line is the exact thing an RCA
    # most needs to see.
    ap.add_argument("--fixture", default="", choices=["", "retries"],
                    help="run the prompt against a constructed event sequence "
                         "instead of Zendesk (retries: 3 failures + a success)")
    ap.add_argument("--dump-raw", action="store_true",
                    help="print the raw events the prompt is built from")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
