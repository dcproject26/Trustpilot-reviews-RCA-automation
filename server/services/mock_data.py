"""
Mock fixtures based on the two real RCA examples:
  - Mathilde / French / Wieliczka Salt Mine
  - Mariusz / Polish / Vatican Museums
Used when MOCK_MODE=true or when a service credential is missing.
"""

MOCK_REVIEWS = [
    {
        "id": "tp_001",
        "slack_ts": "1714039440.000200",
        "slack_channel": "C_MOCK_ORM",
        "rating": 1,
        "language": "fr",
        "author": "MATHILDE VALET",
        "body_original": "Pour le moment c'est catastrophique.\n\nJ'ai réservé 3 billets à un horaire, qui d'après eux n'est finalement plus disponible, tout comme n'importe quel autre horaire. Impossible de demander un remboursement. Mail envoyé, j'attends une réponse.\n\nÇa sent l'arnaque encore.",
        "body_english": "Right now, it's a complete disaster.\n\nI booked three tickets for a specific time slot — which, according to them, turns out to be unavailable after all, just like every other time slot. It is impossible to request a refund. I've sent an email and am waiting for a response.\n\nIt smells like a scam yet again.",
        "reference_number": None,
        "received_at": "2026-04-24T14:44:00",
        "status": "new",
    },
    {
        "id": "tp_002",
        "slack_ts": "1714707180.000100",
        "slack_channel": "C_MOCK_ORM",
        "rating": 1,
        "language": "pl",
        "author": "MARIUSZ",
        "body_original": "Właśnie dziś, mimo wcześniej zakupionych biletów za pośrednictwem Headout, nie weszliśmy do Muzeum watykańskiego. Na umówionym spotkaniu z grupą zwiedzających, przewodnik poinformował nas, że podobno wszystkie wejścia w dniu dzisiejszym są odwołane.",
        "body_english": "Headout failed to fulfill the contract and we couldn't enter the Vatican Museums. Despite tickets purchased in advance through Headout, we were not allowed in today. At the scheduled meeting point, the guide informed us that all tours for today had been cancelled.",
        "reference_number": None,
        "received_at": "2026-05-03T04:33:00",
        "status": "new",
    },
]

MOCK_BOOKINGS = {
    "tp_001": {
        "id": "31246072",
        "experienceName": "Wieliczka Salt Mine — Skip the Line Tickets (French)",
        "tgid": "15757", "tid": "29486", "vid": "2709",
        "vidName": "Wieliczka Salt Mine (Selenium)",
        "bookedOn": "2026-04-24", "visitDate": "2026-05-25",
        "pax": 3, "amount": "€57.00", "status": "Fulfilled (after delay)",
        "partner": "Wieliczka Salt Mine",
        "_match": {"tier": 2, "confidence": "med", "method": "Fuzzy name + experience match"},
    },
    "tp_002": {
        "id": "30837082",
        "experienceName": "Vatican Museums & Sistine Chapel — Priority Entrance",
        "tgid": "6732", "tid": "17521", "vid": "4701",
        "vidName": "EU Travel Group",
        "bookedOn": "2026-04-03", "visitDate": "2026-05-02",
        "pax": 2, "amount": "€84.00", "status": "Cancelled by SP — Refunded",
        "partner": "EU Travel Group",
        "_match": {"tier": 2, "confidence": "high", "method": "Fuzzy name + experience match"},
    },
}

MOCK_TIMELINES = {
    "tp_001": [
        {"time": "24 Apr 02:56", "actor": "guest",  "actor_label": "Guest",     "summary": "Booking made, 11:00 AM slot, 25 May."},
        {"time": "24 Apr 03:21", "actor": "system", "actor_label": "Selenium",  "summary": "Auto-FF retry triggered. Slot returned sold out.", "flag": "low inventory"},
        {"time": "24 Apr 04:44", "actor": "system", "actor_label": "Selenium",  "summary": "Auto-FF failed after retries. Slack alert posted.", "flag": "TAT breach"},
        {"time": "24 Apr 05:17", "actor": "co",     "actor_label": "CO Agent",  "summary": "RO intervened. Alternative slots shared with guest."},
        {"time": "24 Apr 12:36", "actor": "guest",  "actor_label": "Guest",     "summary": "Guest opened refund request email.", "flag": "frustration"},
        {"time": "24 Apr 13:33", "actor": "co",     "actor_label": "CO Agent",  "summary": "Re-attempted fulfilment for 11:40 AM slot. Successful."},
        {"time": "24 Apr 13:35", "actor": "co",     "actor_label": "CO Agent",  "summary": "Tickets emailed. Guest acknowledged receipt."},
    ],
    "tp_002": [
        {"time": "03 May 00:38", "actor": "guest",  "actor_label": "Guest",     "summary": "Email — tour cancelled on-site; bank chargeback initiated.", "flag": "chargeback"},
        {"time": "03 May 00:51", "actor": "co",     "actor_label": "CO Agent",  "summary": "CE raised refund request with EU Travel Group."},
        {"time": "03 May 01:01", "actor": "sp",     "actor_label": "EU Travel Group", "summary": "SP confirmed refund."},
        {"time": "03 May 01:12", "actor": "system", "actor_label": "Minded AI", "summary": "AI offered 25% HOC. Guest rejected.", "flag": "AI mishandle"},
        {"time": "03 May 02:30", "actor": "co",     "actor_label": "CO Agent",  "summary": "Full refund processed. 25% HOC issued. Apology sent."},
    ],
}

MOCK_INSIGHTS = {
    "tp_001": {"tgidRating": "4.5", "tidVidRating": "4.2", "vidCompletionRate": "92%", "sameDaySameVidIssues": "2 of 18", "similarOpenTickets": "3"},
    "tp_002": {"tgidRating": "4.14", "tidVidRating": "1.80", "vidCompletionRate": "77.4%", "sameDaySameVidIssues": "4-5 of 12", "similarOpenTickets": "5"},
}

MOCK_DSS = {
    "tp_001": {"issueType": "Delayed FF", "compensation": "30% HOC", "action": "Send tickets + apology + 30% HOC (CX-FF-002).", "escalateTo": "@inv-ops-on-call", "policyId": "CX-FF-002"},
    "tp_002": {"issueType": "SP cancellation", "compensation": "Full refund + 25% HOC", "action": "Refund + stop sales VID 4701.", "escalateTo": "@bizops-italy-swiss-malta-mex", "policyId": "CX-CANCEL-001"},
}

MOCK_CANNED = """
[DELAYED FULFILMENT]
We sincerely apologise for the inconvenience with your booking. We experienced a technical issue with ticket fulfilment and are sorry for the delay in getting this resolved for you.

[SP CANCELLATION]
We're truly sorry your experience was cancelled at the venue. This is not the standard we hold our partners to, and we understand how disappointing this must have been.

[REFUND CONFIRMATION]
We've processed your full refund and it should appear within 5-7 business days. We've also added a Headout Credit as a gesture of goodwill.

[GENERAL APOLOGY]
We sincerely apologise for the experience you had. We take all feedback seriously and are looking into this with our team.
"""

MOCK_RCA_FIELDS = {
    "tp_001": {
        "queryIssueType": "Delayed FF — Did not receive tickets",
        "whatWentWrong": "Booking placed at 02:56 IST on 24 Apr. Selenium auto-fulfilment retried until 04:44 IST and failed because the originally-booked time slot at VID 2709 was sold out. The Slack failure alert was missed by the on-call team, delaying RO intervention by ~2h. Tickets were finally shared at 13:35 IST — 10h 39m after booking.",
        "customerInteractionCO": "Guest contacted via two email threads. Minded AI acknowledged escalation at 12:37 IST; human took over and resolved at 13:35 IST. TAT breached by ~2h 20m on initial action.",
        "spIssueInteraction": "None. Issue was internal — Selenium vs manual inventory at VID 2709.",
        "areaOfImproving": "Slack failure alerts must be monitored in real time with on-call escalation. Auto-retry for alternative slots should be prioritised.",
        "solutionOffered": "Tickets sent for 11:40 AM slot. Apology shared. 30% HOC per DSS policy CX-FF-002.",
        "raisedTeam1": "@inv-ops-on-call", "raisedTeam2": "",
        "bookingsImpacted": "2 of 18 bookings same VID same day",
        "similarQueries": "3 open tickets last 7d",
        "avgRating": "TGID 4.5 · TID-VID 4.2 (last 4w)",
        "followUpNeeded": "No", "reviewTakedownSent": "Yes", "dssCovers": "Yes",
        "otherComments": "VID 2709 completion 92%, 5% unfulfilled — flagged to Inv-Ops.",
        "signals": ["Selenium auto-FF failure","Manual inventory mismatch","Slot sold out","Slack alert missed","TAT breach","Reschedule delay","Frustration / threat to escalate","Inventory not visible at booking"],
    },
    "tp_002": {
        "queryIssueType": "Booking cancelled by SP at venue",
        "whatWentWrong": "EU Travel Group (VID 4701) cancelled all entries on 03 May due to a system outage. Guest learned of the cancellation only at the meeting point from the guide. Guest contacted Headout at 00:38 IST and immediately initiated a bank chargeback. Minded AI mishandled the email — it didn't recognise the SP-cancellation context and offered 25% HOC, which the guest rejected.",
        "customerInteractionCO": "Guest opened parallel email + chat at 00:38 IST. Chat correctly handled by CE; refund raised with SP within 13 min. Email mishandled by Minded AI; escalated to CE at 01:52 IST. Resolution ~1h 52m.",
        "spIssueInteraction": "EU Travel Group confirmed full refund within 10 min. SP cited an unexpected system outage.",
        "areaOfImproving": "Minded AI playbook needs a branch for SP-cancellation cases. VID 4701 has poor completion rate — sales should be stopped.",
        "solutionOffered": "Full refund processed. 25% HOC issued. Apology + explanation shared.",
        "raisedTeam1": "@bizops-italy-swiss-malta-mex", "raisedTeam2": "@minded-ai-team",
        "bookingsImpacted": "4-5 of 12 bookings same VID same day",
        "similarQueries": "5 open tickets last 7d for VID 4701",
        "avgRating": "TGID 4.14 · TID-VID 1.80 (last 4w) · 79.59% negative ratings",
        "followUpNeeded": "No", "reviewTakedownSent": "Yes", "dssCovers": "Yes",
        "otherComments": "Sales stopped for VID 4701. Moving bookings to alternate SP.",
        "signals": ["SP cancelled at venue","SP system outage","AI mishandle","Playbook missing branch","Chargeback initiated","Multiple complaints same VID","Poor SP completion rate","Negative on-site experience"],
    },
}

MOCK_RESPONSES = {
    "tp_001": "We're truly sorry for the difficulty you experienced with your Wieliczka Salt Mine tickets. Due to a technical issue with our fulfilment system, your original time slot was unfortunately unavailable — we should have resolved this far more quickly and we apologise for the delay. Your tickets have now been confirmed for the 11:40 AM slot, and we've added a 30% Headout Credit to your account as a gesture of goodwill. We hope to have the chance to make this right for you.",
    "tp_002": "We sincerely apologise for what happened at the Vatican Museums — having your experience cancelled on the day is unacceptable, and we completely understand your frustration. Your full refund has been processed and a 25% Headout Credit has been added to your account. We've also raised this with the tour operator and are taking steps to prevent this from happening again. We're sorry we let you down on this occasion.",
}
