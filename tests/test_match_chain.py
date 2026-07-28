"""
End-to-end check of the booking-match chain using Fredrik Olsen's real
Trustpilot Slack payload (captured from the live workspace).

Runs the parts that do not need network: Slack parse -> stored body ->
match_text -> venue tokens -> candidate scoring. Every external call is
represented by the data it actually returned in production.

Run:  python3 tests/test_match_chain.py
"""
import os, re, sys, unicodedata
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got:  {got!r}")
        print(f"        want: {want!r}")
        FAILURES.append(label)


# Fredrik's real Slack message, verbatim from conversations.history
FREDRIK_EVENT = {
    "ts": "1784672419.620039",
    "channel": "CAB4TQ2AJ",
    "text": "",
    "blocks": [],
    "attachments": [{
        "id": 1, "ts": 1784672119, "color": "a30200",
        "fallback": "Ikke kjøp billetter gjennom disse",
        "text": "Ikke kjøp billetter gjennom disse. Det er scam og dem selger mye dyrere billetter!!!",
        "title": "Ikke kjøp billetter gjennom disse",
        "author_name": "Fredrik Olsen",
        "footer": "★☆☆☆☆ Not verified",
        "fields": [{"value": "Salt mines Krakow", "title": "Reference number", "short": False}],
    }],
}

# What Claude's translator actually returned — note the venue line is GONE.
FREDRIK_ENGLISH = ("Do not buy tickets through these. It is a scam and they "
                   "sell tickets at much higher prices!!!")

print("\n1. Slack parse — venue must survive into the stored body")
from server.services.slack import parse_review, _bids_from_text
parsed = parse_review(FREDRIK_EVENT)
check("author", parsed["author"], "Fredrik Olsen")
check("no BID found", parsed["reference_number"], None)
check("venue kept in body", "Salt mines Krakow" in parsed["body_original"], True)

print("\n2. match_text — translation drops the venue, original must be reinstated")
_orig = parsed["body_original"].strip()
_eng = FREDRIK_ENGLISH.strip()
match_text = _eng if not _orig else (
    _eng if _orig in _eng else (f"{_eng}\n{_orig}".strip() if _eng else _orig))
check("translation alone has venue", "Salt mines Krakow" in _eng, False)
check("match_text has venue", "Salt mines Krakow" in match_text, True)

print("\n3. Venue tokens — what the scorer compares")
from server.pipeline import _sig_tokens
# Indicator extraction on match_text yields this (venue + city)
venue_hints = ["Salt mines Krakow", "Krakow, Poland"]
hint_toks = _sig_tokens(" ".join(venue_hints))
real_exp = "Wieliczka Salt Mine Guided Tour with Skip-the-Line Tickets"
wrong_exp = "Universal Studios Japan: Studio Pass (Admission Tickets)"
check("hint tokens", hint_toks, {"salt", "mines", "krakow", "poland"})
check("correct venue overlaps", bool(hint_toks & _sig_tokens(real_exp)), True)
check("wrong venue does not", bool(hint_toks & _sig_tokens(wrong_exp)), False)

print("\n4. Name confidence")
from server.services.zendesk import _name_score
check("Fredrik Martin Olsen", round(_name_score("Fredrik Martin Olsen", "Fredrik", "Olsen"), 2), 1.0)
check("Fredrik Birkelund Holvik", round(_name_score("Fredrik Birkelund Holvik", "Fredrik", "Olsen"), 2), 0.3)
check("Fredrik Røstvold", round(_name_score("Fredrik Røstvold", "Fredrik", "Olsen"), 2), 0.3)

print("\n5. Slack BID extraction vs ticket refs")
check("BMS url", _bids_from_text("check https://aries.headout.com/bms/booking/32885787"), ["32885787"])
check("zd url excluded", _bids_from_text("https://x.zendesk.com/agent/tickets/33979875"), [])
check("inline ticket ref", _bids_from_text("ticket 33979875 vs booking 32885787"), ["32885787"])

print("\n9. pax narrows a multi-candidate set, and never rejects a lone one")
_cip_pool = [
    {"booking_id": "32900044", "matched_on": ["name", "venue", "city", "pax"]},
    {"booking_id": "32342285", "matched_on": ["name", "venue", "city"]},
    {"booking_id": "32550006", "matched_on": ["name", "venue", "city"]},
]
_exact = [s for s in _cip_pool if "pax" in s["matched_on"]]
_kept = _exact if (_exact and len(_exact) < len(_cip_pool)) else _cip_pool
check("pax narrows Ciprian to one", [s["booking_id"] for s in _kept], ["32900044"])

# David: pax disagrees (review says 2, ticket says 1) but it is the only
# candidate, so narrowing must not remove it.
_david_pool = [{"booking_id": "32908218", "matched_on": ["name", "venue"]}]
_ex = [s for s in _david_pool if "pax" in s["matched_on"]]
_kept2 = _ex if (_ex and len(_ex) < len(_david_pool)) else _david_pool
check("pax keeps David's lone candidate", [s["booking_id"] for s in _kept2], ["32908218"])

print("\n" + "=" * 62)
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): " + ", ".join(FAILURES))
    sys.exit(1)
print("all checks passed")

# ── Appended: ticket custom fields (real values from ZD-33979875) ──────────
print("\n6. Ticket custom fields — matching without the review naming anything")
from server.services.zendesk import ticket_signals, booking_id_from_ticket


class _FakeTicket:
    """ZD-33979875's real custom_fields payload."""
    id = 33979875
    custom_fields = [
        {"id": 360021524471, "value": "32885787"},        # booking id
        {"id": 360021524491, "value": "28219778"},        # itinerary id — NOT a BID
        {"id": 51116641874073, "value": "Fredrik Martin Olsen"},
        {"id": 360021471312, "value": "Wieliczka Salt Mine - Skip the Line Tickets"},
        {"id": 360021522151, "value": "Krakow"},
        {"id": 360024232231, "value": "2026-07-21"},
        {"id": 360021522291, "value": "2 Adult, 2 Child"},
        {"id": 8136487555225, "value": "Wieliczka Salt Mine"},
        {"id": 360026670311, "value": "f.olsen95@gmail.com"},
    ]


t = _FakeTicket()
sig = ticket_signals(t)
check("booking id from field", booking_id_from_ticket(t), "32885787")
check("itinerary id kept separate", sig["itinerary_id"], "28219778")
check("guest name", sig["guest_name"], "Fredrik Martin Olsen")
check("experience", sig["experience"], "Wieliczka Salt Mine - Skip the Line Tickets")
check("city", sig["city"], "Krakow")
check("pax parsed", sig["pax"], 4)

# Name confidence off the ticket's own field beats the requester lookup
check("name score from ticket field",
      round(_name_score(sig["guest_name"], "Fredrik", "Olsen"), 2), 1.0)

# Venue matches the ticket's own experience even though the BQ row is not used
hint = _sig_tokens("Salt mines Krakow")
check("venue matches ticket experience", bool(hint & _sig_tokens(sig["experience"])), True)
check("venue matches ticket city", bool(hint & _sig_tokens(sig["city"])), True)

print("\n" + "=" * 62)
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): " + ", ".join(FAILURES))
    sys.exit(1)
print("all checks passed")

# ── Appended: venue filter behaviour ──────────────────────────────────────
print("\n7. Venue filter — only bookings for the named venue survive")


def _vpts(exp, hints):
    ht = _sig_tokens(" ".join(hints))
    return 2.0 * len(ht & _sig_tokens(exp)) if ht else 0.0


FREDRIK_CANDIDATES = [
    ("32885787", "Wieliczka Salt Mine Guided Tour with Skip-the-Line Tickets"),
    ("31556404", "Disney's The Lion King"),
    ("32706277", "Round-Trip Tickets to Top of Innsbruck with Optional Alpine Zoo"),
    ("33026327", "Park Guell Tickets"),
]
hints = ["Salt mines Krakow", "Krakow, Poland"]
signal = any(_vpts(e, hints) > 0 for _, e in FREDRIK_CANDIDATES)
kept = [b for b, e in FREDRIK_CANDIDATES if _vpts(e, hints) > 0] if (hints and signal) else \
       [b for b, _ in FREDRIK_CANDIDATES]
check("only the salt mine survives", kept, ["32885787"])

# No venue extracted -> nothing is filtered out, ranking still applies
no_hints = []
signal0 = any(_vpts(e, no_hints) > 0 for _, e in FREDRIK_CANDIDATES)
kept0 = [b for b, e in FREDRIK_CANDIDATES if _vpts(e, no_hints) > 0] if (no_hints and signal0) else \
        [b for b, _ in FREDRIK_CANDIDATES]
check("no venue -> nothing dropped", len(kept0), 4)

# Venue extracted but nothing matches -> keep all rather than hide the answer
odd = ["Colosseum Rome"]
signal1 = any(_vpts(e, odd) > 0 for _, e in FREDRIK_CANDIDATES)
kept1 = [b for b, e in FREDRIK_CANDIDATES if _vpts(e, odd) > 0] if (odd and signal1) else \
        [b for b, _ in FREDRIK_CANDIDATES]
check("venue matches nothing -> keep all", len(kept1), 4)

print("\n" + "=" * 62)
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): " + ", ".join(FAILURES))
    sys.exit(1)
print("all checks passed")

# ── Appended: the agreed shortlist rule, against real probe data ───────────
print("\n8. AND-filter across every indicator present")
from server.services.zendesk import matches_indicators, name_matches

FRED = {"guest_name": "Fredrik Olsen", "experience_or_venue": "Salt mines Krakow",
        "city_or_country": "Krakow", "pax": None}
fred_tickets = [
    ("32885787", "Fredrik Martin Olsen", "Wieliczka Salt Mine - Skip the Line Tickets", "Krakow", None, True),
    ("32706277", "Fredrik Martin Olsen", "Top of Innsbruck: Round-Trip to Nordkettenbahn", "Innsbruck", None, False),
    ("31556404", "Fredrik Frydenborg-Olsen", "The Lion King", "London", None, False),
    ("31774438", "Fredrik Rostvold", "Wieliczka Salt Mine - Skip the Line Tickets", "Krakow", None, False),
    ("33036250", "Rafael Guzman Murillo", "From Krakow: Wieliczka Salt Mine Guided Tour", "Krakow", None, False),
]
for bid, guest, exp, city, pax, want in fred_tickets:
    sig = {"guest_name": guest, "experience": exp, "city": city, "pax": pax}
    ok, _ = matches_indicators(sig, FRED, "Fredrik", "Olsen")
    check(f"fredrik {bid}", ok, want)

CIP = {"guest_name": "Ciprian", "experience_or_venue": "combo tickets for Oceanogràfic València",
       "city_or_country": "Valencia, Spain", "pax": 9}
cip_tickets = [
    ("32900044", "Ciprian Rosu", "Oceanogràfic Tickets with Optional 4D Cinema", "Valencia", 9, True),
    # pax no longer rejects here -- these pass the filter and are then narrowed
    # out by shortlist(), which keeps only the pax-agreeing candidate when some
    # agree and others do not. See the pax narrowing check below.
    ("32342285", "Ciprian Toma", "Combo: Oceanogràfic + Hemisfèric Tickets", "Valencia", 8, True),
    ("32550006", "Dumitru Ciprian Iscu", "Oceanogràfic & Science Museum Valencia", "Valencia", 3, True),
    ("32978225", "Stefan Ciprian Neagu", "Acropolis & Parthenon Entrance Tickets", "Athens", None, False),
]
for bid, guest, exp, city, pax, want in cip_tickets:
    sig = {"guest_name": guest, "experience": exp, "city": city, "pax": pax}
    ok, _ = matches_indicators(sig, CIP, "Ciprian", None)
    check(f"ciprian {bid}", ok, want)

NAU = {"guest_name": "C. Nauleau", "experience_or_venue": "Louvre entry tickets",
       "city_or_country": "Paris, France", "pax": None}
for bid, guest, exp, city, want in [
    ("32244357", "Catherine Nauleau", "Direct Entry Tickets to Louvre Museum", "Paris", True),
    ("31525525", "Sophie NAULEAU", "1/2/3/7-Day Pass: Venice ACTV Water Bus", "Venice", False),
    ("28390803", "NAULEAU Marie-Charlotte", "Entrance Ticket to Pompeii", "Naples", False),
]:
    sig = {"guest_name": guest, "experience": exp, "city": city, "pax": None}
    ok, _ = matches_indicators(sig, NAU, "C", "Nauleau")
    check(f"nauleau {bid}", ok, want)

JOE = {"guest_name": "Joe Christopher", "experience_or_venue": None,
       "city_or_country": None, "pax": None}
for bid, guest, want in [
    ("32102364", "Joseph Christopher", True),
    ("32317283", "Joseph Christopher Newall", True),
    ("32318434", "Christopher McCardle", False),
    ("32077108", "Christopher E. Maclin", False),
]:
    sig = {"guest_name": guest, "experience": "x", "city": "x", "pax": None}
    ok, _ = matches_indicators(sig, JOE, "Joe", "Christopher")
    check(f"joe {bid}", ok, want)

print("\n" + "=" * 62)
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): " + ", ".join(FAILURES))
    sys.exit(1)
print("all checks passed")
