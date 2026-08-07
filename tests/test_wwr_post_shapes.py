"""The Slack post composes for every shape `what_went_wrong.fixes` has held.

WHAT BROKE. In v3 that key was an OBJECT — {teams, actions} — and §3 made it a
LIST of {action, owner, because}. `compose` read `.get("teams")` on whatever
was there, so every post written since §3 landed came out as

    What went wrong could not be composed: 'list' object has no attribute 'get'

on the card. The composer is wrapped in a try/except that turns any exception
into that sentence, which is why it failed loudly in the dashboard and
silently everywhere else: nothing in the suite drove compose() with the shape
the same commit had just started producing.
"""
import pytest

from server.services.wwr_post import compose
from server.services.rca_v4_validate import validate

ISSUE = {"issue": "Tickets arrived late", "claim": "They came two hours late",
         "claim_accuracy": "Accurate", "root_cause": "The run failed silently"}


def test_the_shape_validate_actually_produces_composes():
    """Driven through validate, so the test cannot drift from what is stored."""
    out, _ = validate({"what_went_wrong": {
        "guest_issues": [ISSUE],
        "fixes": [{"action": "Alert on failed fulfilment", "owner": "TECH"}]}})
    text = compose(out["what_went_wrong"])
    assert text, "the composer returned nothing for the shape it is given"
    assert "could not be composed" not in text, text


def test_the_v3_object_shape_still_composes():
    """Old drafts hold {teams, actions} and are reposted from the dashboard."""
    text = compose({"guest_issues": [ISSUE],
                    "fixes": {"teams": ["CO"], "actions": ["Resend tickets"]}})
    assert text and "could not be composed" not in text, text
    assert "@CO" in text and "Resend tickets" in text, text


def test_a_list_fix_reaches_the_post():
    text = compose({"guest_issues": [ISSUE],
                    "fixes": [{"action": "Alert on failed fulfilment",
                               "owner": "TECH"}]})
    assert "@TECH" in text, text
    assert "Alert on failed fulfilment" in text, text


def test_an_unowned_fix_still_reaches_the_post():
    """A fix omitted here is a fix nobody sees at all — the state the Unrouted
    tab exists to make visible."""
    text = compose({"guest_issues": [ISSUE],
                    "fixes": [{"action": "Nobody owns this"}]})
    assert "Nobody owns this" in text, text


@pytest.mark.parametrize("fixes", [None, [], {}, "", [None], ["nope"], [{}]])
def test_no_shape_of_fixes_can_break_the_post(fixes):
    """The except turns ANY exception into a sentence on the card, so a crash
    here is never seen as a crash — it is seen as the RCA having nothing to
    say. Every shape has to survive."""
    text = compose({"guest_issues": [ISSUE], "fixes": fixes})
    assert text, fixes
    assert "could not be composed" not in text, (fixes, text)


def test_case_findings_alongside_does_not_break_it():
    """§1 added another list under the same node."""
    text = compose({"guest_issues": [ISSUE],
                    "fixes": [{"action": "a", "owner": "TECH"}],
                    "case_findings": [{"text": "t", "source": "bms",
                                       "time": None, "ref": None}]})
    assert "could not be composed" not in text, text


# ── the CE section is a conversation, not a machine log ────────────────────

class _Draft:
    """A draft stub that answers to anything.

    Enumerating the attributes `format_rca_slack` touches makes the test fail
    every time the composer reads one more field — which is a test measuring
    the composer's field list, not the behaviour under test.
    """

    def __init__(self, frames):
        self.support_interaction_frames = frames
        self.sp_interaction_frames = []
        self.booking = {"id": "1"}
        self.match_tier = 1

    def __getattr__(self, name):          # only for what __init__ did not set
        return None


def _draft(frames):
    return _Draft(frames)


CHAT = {"thread": "chat", "actor": "guest", "time": "05 Aug 08:12",
        "guestSaid": "Refund on unused child ticket",
        "weDid": "Declined, offered credits"}
API = {"thread": "api", "actor": "system", "time": "03 Aug 15:53",
       "guestSaid": "", "weDid": "Booking details submitted to vendor API"}
BOOKING = {"thread": "booking", "actor": "creation", "time": "03 Aug 15:53",
           "guestSaid": "", "weDid": "General Admission, 2 Adults"}


def test_only_exchanges_a_person_took_part_in_reach_the_ce_section():
    """The post carried booking dumps and vendor-API rows under a heading
    that says "Customer / CE interactions" — machinery rendered as a
    conversation with the guest. The card has filtered these for a while; the
    post is the OTHER composer for the same section and never learned."""
    import server.services.slack as sl
    text = sl.format_rca_slack({"id": "r1"}, _draft([CHAT, API, BOOKING]))
    i = text.index("Customer / CE interactions")
    block = text[i:i + 600]
    assert "Refund on unused child ticket" in block, block
    assert "vendor API" not in block, block
    assert "General Admission" not in block, block


def test_the_machinery_is_counted_not_silently_dropped():
    """A section that quietly shrinks reads as a guest nobody spoke to, which
    is the opposite of what happened."""
    import server.services.slack as sl
    text = sl.format_rca_slack({"id": "r1"}, _draft([CHAT, API, BOOKING]))
    i = text.index("Customer / CE interactions")
    assert "moved to the timeline" in text[i:i + 600], text[i:i + 600]


def test_a_card_with_only_machinery_does_not_claim_a_conversation():
    import server.services.slack as sl
    text = sl.format_rca_slack({"id": "r1"}, _draft([API, BOOKING]))
    i = text.index("Customer / CE interactions")
    block = text[i:i + 400]
    assert "vendor API" not in block, block
