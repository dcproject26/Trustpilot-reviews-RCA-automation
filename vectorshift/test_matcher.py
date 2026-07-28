"""
Standalone check of matcher.py against real Zendesk data.

No network, no Replit imports — run it anywhere Python runs, including inside
VectorShift, to confirm the port behaves like the verified original.

    python3 vectorshift/test_matcher.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from matcher import FIELDS, shortlist, name_matches, build_queries

FAIL = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        FAIL.append(label)


def ticket(tid, created, bid, guest, exp, city, pax=None):
    """Build a Zendesk-shaped ticket from the fields matcher reads."""
    cf = [{"id": FIELDS["booking_id"], "value": bid},
          {"id": FIELDS["guest_name"], "value": guest},
          {"id": FIELDS["experience"], "value": exp},
          {"id": FIELDS["city"], "value": city}]
    if pax:
        cf.append({"id": FIELDS["pax"], "value": f"{pax} Adult"})
    return {"id": tid, "created_at": created, "custom_fields": cf}


print("\n1. Fredrik — name + venue + city")
fredrik = [
    ticket(1, "2026-07-21", "32885787", "Fredrik Martin Olsen",
           "Wieliczka Salt Mine - Skip the Line Tickets", "Krakow", 4),
    ticket(2, "2026-07-13", "32706277", "Fredrik Martin Olsen",
           "Top of Innsbruck: Round-Trip to Nordkettenbahn", "Innsbruck", 2),
    ticket(3, "2026-05-13", "31556404", "Fredrik Frydenborg-Olsen",
           "The Lion King", "London", 4),
    ticket(4, "2026-05-24", "31774438", "Fredrik Rostvold",
           "Wieliczka Salt Mine - Skip the Line Tickets", "Krakow", 6),
    ticket(5, "2026-08-10", "33036250", "Rafael Guzman Murillo",
           "From Krakow: Wieliczka Salt Mine Guided Tour", "Krakow", 2),
]
ind = {"guest_name": "Fredrik Olsen", "experience_or_venue": "Salt mines Krakow",
       "city_or_country": "Krakow", "pax": None}
got = shortlist(fredrik, ind)
check("only 32885787", [s["booking_id"] for s in got], ["32885787"])
check("matched on", got[0]["matched_on"], ["name", "venue", "city"])

print("\n2. David — generic 'palace' must not match")
david = [
    ticket(10, "2026-07-22", "32908218", "David Hultsman",
           "Palace of Culture and Science skip the line ticket", "Warsaw", 1),
    ticket(11, "2026-08-11", "33043457", "David Rivkin",
           "Pena Palace & Quinta da Regaleira Tickets", "Lisbon", 2),
    ticket(12, "2026-08-04", "33027989", "DAVID KOTLAN",
           "The Buckingham Palace Ticket", "London", 2),
    ticket(13, "2026-02-14", "29995275", "David Finch",
           "Kensington Palace Tickets", "London", 2),
]
ind = {"guest_name": "David", "experience_or_venue": "palace of culture and science",
       "city_or_country": "Poland", "pax": 2}
got = shortlist(david, ind)
check("only 32908218", [s["booking_id"] for s in got], ["32908218"])
check("country vs city not fatal", got[0]["matched_on"], ["name", "venue"])

print("\n3. Ciprian — pax 9 narrows")
ciprian = [
    ticket(20, "2026-07-24", "32900044", "Ciprian Rosu",
           "Oceanogràfic Tickets with Optional 4D Cinema", "Valencia", 9),
    ticket(21, "2026-06-27", "32342285", "Ciprian Toma",
           "Combo: Oceanogràfic + Hemisfèric Tickets", "Valencia", 8),
    ticket(22, "2026-07-08", "32550006", "Dumitru Ciprian Iscu",
           "Oceanogràfic & Science Museum Valencia", "Valencia", 3),
    ticket(23, "2026-07-25", "32978225", "Stefan Ciprian Neagu",
           "Acropolis & Parthenon Entrance Tickets", "Athens"),
]
ind = {"guest_name": "Ciprian",
       "experience_or_venue": "combo tickets for Oceanogràfic València",
       "city_or_country": "Valencia, Spain", "pax": 9}
got = shortlist(ciprian, ind)
check("pax narrows to 32900044", [s["booking_id"] for s in got], ["32900044"])
check("four indicators", got[0]["matched_on"], ["name", "venue", "city", "pax"])

print("\n4. C. Nauleau — initial matches full first name")
nauleau = [
    ticket(30, "2026-07-22", "32244357", "Catherine Nauleau",
           "Direct Entry Tickets to Louvre Museum", "Paris", 2),
    ticket(31, "2026-05-09", "31525525", "Sophie NAULEAU",
           "1/2/3/7-Day Pass: Venice ACTV Water Bus", "Venice"),
    ticket(32, "2025-11-08", "28390803", "NAULEAU Marie-Charlotte",
           "Entrance Ticket to Pompeii", "Naples"),
]
ind = {"guest_name": "C. Nauleau", "experience_or_venue": "Louvre entry tickets",
       "city_or_country": "Paris, France", "pax": None}
check("only 32244357", [s["booking_id"] for s in shortlist(nauleau, ind)], ["32244357"])

print("\n5. Joe — name only, Joseph matches, Christopher-as-first does not")
joe = [
    ticket(40, "2026-06-27", "32317283", "Joseph Christopher Newall",
           "Vienna FLEXI PASS", "Vienna", 2),
    ticket(41, "2026-06-24", "32102364", "Joseph Christopher",
           "MJ The Musical", "Perth", 2),
    ticket(42, "2026-08-23", "32318434", "Christopher McCardle",
           "The Play That Goes Wrong", "London", 2),
    ticket(43, "2026-07-03", "32077108", "Christopher E. Maclin",
           "Joe Turner's Come and Gone", "New York"),
    ticket(44, "2025-11-30", "28727032", "Joseph Christopher Stock",
           "Tickets To Christ the Redeemer", "Rio de Janeiro", 2),
]
ind = {"guest_name": "Joe Christopher", "experience_or_venue": None,
       "city_or_country": None, "pax": None}
got = [s["booking_id"] for s in shortlist(joe, ind)]
check("Joseph kept, Christopher-first rejected",
      sorted(got), sorted(["32317283", "32102364", "28727032"]))

print("\n6. Name matcher edge cases")
for cand, f, l, want in [
    ("Fredrik Martin Olsen", "Fredrik", "Olsen", True),
    ("Fredrik Rostvold", "Fredrik", "Olsen", False),
    ("Catherine Nauleau", "C", "Nauleau", True),
    ("Joseph Christopher", "Joe", "Christopher", True),
    ("Christopher McCardle", "Joe", "Christopher", False),
]:
    check(f"{cand} vs {f} {l or ''}", name_matches(cand, f, l), want)

print("\n7. Queries stay narrow enough to avoid Zendesk truncation")
check("name+venue", build_queries(
    {"guest_name": "Fredrik Olsen", "experience_or_venue": "Salt mines Krakow",
     "city_or_country": "Krakow"}),
    ['type:ticket requester:"Fredrik Olsen" order_by:created_at sort:desc',
     'type:ticket Fredrik Olsen Salt mines Krakow order_by:created_at sort:desc',
     'type:ticket Fredrik Olsen Krakow order_by:created_at sort:desc'])
check("name only", build_queries({"guest_name": "Joe Christopher"}),
      ['type:ticket requester:"Joe Christopher" order_by:created_at sort:desc',
       'type:ticket Joe Christopher order_by:created_at sort:desc'])

print("\n" + "=" * 64)
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("matcher.py verified — matches the live-tested behaviour")
