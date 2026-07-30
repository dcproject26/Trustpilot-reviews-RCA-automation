#!/usr/bin/env python3
"""
Seed a local SQLite database with realistic reviews so the dashboard can be
looked at without production credentials.

    DATABASE_URL=sqlite:///./demo.db python3 tools/seed_demo.py

Three reviews, chosen to cover the three buckets the UI renders differently:
a confirmed Tier 1 booking with a full RCA, an unconfirmed candidate set, and
an untraceable review. A visual change that looks right on the rich card often
falls apart on the thin one, so all three need to exist.

This writes only to the database named by DATABASE_URL. It refuses to run
against anything that is not SQLite - seeding demo rows into the shared
Postgres would put fictional reviews in front of the team.
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RCA_FULL = {
    "tldr": {
        "our_mistake": "The experience page never stated that tickets arrive up to two hours before the slot, so the guest booked expecting them immediately.",
        "our_fix": "Content is adding the delivery window to the checkout page, and the guest was refunded in full.",
    },
    "what_went_wrong": {
        "guest_issues": [
            {"issue": "Tickets did not arrive at the time of booking.",
             "claim": "We booked tickets and they never arrived in time. Nobody told us at the time of booking that tickets take two hours to be delivered.",
             "claim_accuracy": "Accurate", "owner": "Content",
             "root_cause": "The page template has no field for fulfilment timing, so the two-hour window was never surfaced at checkout.",
             "evidence": ["The booking record shows tickets were issued 94 minutes after purchase.",
                          "No delivery window is stated anywhere on the experience page."]},
            {"issue": "Support did not explain the delay when first contacted.",
             "claim": "We were left standing outside with nothing.",
             "claim_accuracy": "Partly accurate", "owner": "CE",
             "root_cause": "The associate used the generic delay macro instead of checking the fulfilment log.",
             "evidence": ["The first reply quoted a generic wait time rather than the actual fulfilment state."]},
        ],
        "what_happened": {
            "root_causes": [],
        },
        "sp_escalation": {"escalated": "N/A", "reason": "Fulfilment is automated for this experience; no supply partner was involved."},
        "fixes": {
            "teams": ["Content", "CE"],
            "owner": "Content ops",
            "actions": ["Full refund issued on 12 March.",
                        "Delivery window added to the page template backlog."],
        },
    },
    "booking_logs": [
        {"time": "10 Mar 09:14", "what": "Booking confirmed", "detail": "Guest purchased two adult tickets for the 12 March slot."},
        {"time": "10 Mar 09:14", "what": "Booking-in-progress email sent", "detail": "The email did not state when tickets would arrive."},
        {"time": "10 Mar 10:48", "what": "Automated fulfilment completed", "detail": "Tickets issued 94 minutes after purchase, inside the internal window but never communicated."},
        {"time": "10 Mar 11:02", "what": "Guest opened chat", "detail": ""},
        {"time": "10 Mar 11:09", "what": "First reply sent", "detail": "A generic delay macro was used rather than the actual fulfilment state."},
        {"time": "12 Mar 18:30", "what": "Review published", "detail": "One star, citing the missing delivery information."},
    ],
    "flags": [
        {"flag": "The first reply quoted a generic wait time instead of the fulfilment log.",
         "team": "CE", "evidence": "Reply at 11:09 gives 24 hours; tickets had already been issued.", "zd_ref": "30994882"},
        {"flag": "The experience page states no delivery window for an automated-fulfilment product.",
         "team": "content", "evidence": "Checked the live page; no timing field exists in the template.", "zd_ref": ""},
    ],
    "sop_compliance": {
        "verdict": "deviated", "dss_available": True,
        "expected": "Check the fulfilment log before quoting a wait time to the guest.",
        "actual": "A generic 24-hour macro was sent while the tickets were already issued.",
        "detail": "The refund itself was inside policy; only the first reply deviated.",
        "zd_ref": "30994882",
    },
    "support_interaction": [
        {"channel": "chat", "who": "guest", "summary": "Asked where the tickets were, eleven minutes before they arrived.", "zd_ref": "30994882"},
        {"channel": "chat", "who": "agent", "summary": "Quoted a generic 24-hour delivery window.", "zd_ref": "30994882"},
    ],
    "sp_interaction": {"raised": "N/A", "records": []},
    "issue_specific_answers": {
        "Was the delivery window stated at checkout?": "No. The page template has no field for it.",
        "Were the tickets valid for the booked date?": "Yes. The tickets matched the 12 March slot.",
    },
    "area_of_improving": [
        "State the fulfilment window on the experience page for every automated-delivery product.",
        "Give associates a macro that reads the fulfilment log instead of quoting a fixed wait.",
    ],
    "takedown": {"verdict": "No"},
    "evidence": ["Booking record: tickets issued 10 Mar 10:48.", "Live experience page carries no delivery timing."],
}


def main():
    from server.db import Base, engine, SessionLocal, Review, RcaDraft
    if not str(engine.url).startswith("sqlite"):
        print(f"refusing to seed a non-SQLite database: {engine.url.get_backend_name()}")
        return 1
    Base.metadata.create_all(engine)
    s = SessionLocal()
    now = datetime.utcnow()
    try:
        s.query(RcaDraft).delete()
        s.query(Review).delete()
        s.commit()

        # 1. confirmed booking, full RCA
        s.add(Review(id="tp_demo_1", slack_ts="1.1", slack_channel="C1", rating=1,
                     language="EN", author="David Whitmore",
                     body_original="We booked tickets and they never arrived in time. Nobody told us at the time of booking that tickets take two hours to be delivered. We were left standing outside with nothing.",
                     reference_number="BID-8842119", received_at=now - timedelta(hours=2),
                     status="draft"))
        s.add(RcaDraft(id="d1", review_id="tp_demo_1", match_tier=1,
                       match_confidence="high", match_method="BID in review text",
                       booking={"id": "8842119", "experienceName": "Carrières des Lumières",
                                "vendorName": "Culturespaces", "date_of_visit": "2026-03-12",
                                "date_of_booking": "2026-03-10", "amountUSD": 96.0,
                                "fulfilmentType": "automated"},
                       selected_candidate_bid="8842119", rca_v3=RCA_FULL,
                       l1="Operations Issue", l2="Tickets not received",
                       sub_theme="Delayed fulfilment",
                       sub_themes=["Delayed fulfilment", "Instructions unclear"],
                       scenarios=["Tickets sent late"],
                       zendesk_ticket_ids=["30994882"],
                       suggested_response="Hi David, thank you for flagging this and I am sorry the tickets arrived later than you expected...",
                       generated_at=now))

        # 2. unconfirmed candidates - the thin card
        s.add(Review(id="tp_demo_2", slack_ts="1.2", slack_channel="C1", rating=1,
                     language="FR", author="Thierry Baeriswyl",
                     body_original="attention !!!! arnaque\n\nNous avons acheté des billets pour les carrières des lumières en France.\nla confirmation avait la date correcte mais les billets étaient datés d'une semaine plus tard (donc pas valides le jour de notre visite) !\nnous avons demandé le remboursement mais rien à faire.",
                     body_english="WARNING !!!! SCAM We bought tickets for the Carrières des Lumières in France. The confirmation had the correct date but the tickets were dated a week later (so not valid on the day of our visit)! We asked for a refund but nothing doing.",
                     received_at=now - timedelta(hours=5), status="draft"))
        s.add(RcaDraft(id="d2", review_id="tp_demo_2", match_tier=2,
                       match_confidence="medium", match_method="venue + date window",
                       candidate_state=True,
                       candidates_list=[
                           {"id": "8840012", "experienceName": "Carrières des Lumières",
                            "date_of_visit": "2026-03-08", "amountUSD": 74.0, "_score": 0.62},
                           {"id": "8839887", "experienceName": "Carrières des Lumières",
                            "date_of_visit": "2026-03-09", "amountUSD": 74.0, "_score": 0.55},
                       ],
                       confidence_trail=[{"mark": "pass", "text": "<strong>Venue:</strong> matched Carrières des Lumières"},
                                         {"mark": "warn", "text": "<strong>Name:</strong> no exact guest-name match"}],
                       rca_v3={}, l1="Operations Issue", l2="Ticket Issues",
                       generated_at=now))

        # 3. untraceable - the thinnest card
        s.add(Review(id="tp_demo_3", slack_ts="1.3", slack_channel="C1", rating=2,
                     language="EN", author="",
                     body_original="Overpriced and the queue was enormous. Would not book again.",
                     received_at=now - timedelta(days=1), status="draft"))
        s.commit()
        print("seeded 3 demo reviews into", engine.url)
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
