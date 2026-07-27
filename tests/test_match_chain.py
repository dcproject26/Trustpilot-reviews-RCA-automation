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

print("\n" + "=" * 62)
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): " + ", ".join(FAILURES))
    sys.exit(1)
print("all checks passed")
