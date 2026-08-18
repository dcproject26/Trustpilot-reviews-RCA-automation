"""Five changes to what the thread carries and what an associate can record.

  1. A contact's head line no longer repeats its own detail.
  2. Minded AI gaps are gone from the taxonomy, so no Zendesk frame can carry one.
  3. Area of improvement is posted again.
  4. Flags are not posted (they stay on the card).
  5. "More time" and "More info" are recordable resolutions.

3 and 4 are a pair. The chip row and the server composer kept two separate
section lists, and they had drifted: the client had dropped Area of improvement
while the server went on emitting it, so the preview and the posted text
disagreed about whether the section existed at all.
"""
import pathlib
import re

import pytest

from server.services.slack import _head_summary, contacts_section, format_rca_slack
from server.taxonomy import GAP_TAXONOMY
from tests.test_slack_v3_format import REVIEW, V3, _draft


class _D:
    def __init__(self, frames):
        self.support_interaction_frames = frames


FR = [{"ticket_id": "4491", "thread": "email", "actor": "guest",
       "time": "17 Aug 03:16", "guestSaid": "reschedule please"}]
LONG = ("Guest emailed requesting reschedule of both bookings due to illness. "
        "Taylor collected booking IDs, then said one was non-reschedulable. "
        "Guest later reported he was also ill and asked for one more night.")


def _sec(notes):
    return contacts_section(_D(FR), {"support_interaction_notes": notes}, "\n")


# ── 1. the head does not repeat the detail ──────────────────────────────────

def test_a_long_summary_is_cut_to_one_sentence_when_a_detail_follows():
    """THE POINT. The head printed the whole summary and the detail printed the
    whole account of the same exchange, so a long contact rendered the same
    paragraph twice."""
    out = _sec([{"zd_ref": "ZD-4491", "summary": LONG,
                 "detail": "Taylor never checked the reserved timeslot."}])
    head = out.split("\n")[0]
    assert "Guest emailed requesting reschedule of both bookings due to illness." in head
    assert "Taylor collected booking IDs" not in head, "the head kept the whole summary"
    assert "Taylor never checked the reserved timeslot." in out


def test_a_detail_that_restates_the_summary_wins_and_the_head_drops_it():
    """A model that opens its detail by repeating its summary is common. Then
    the head's copy is pure repetition and the detail is the better version."""
    s = "Guest asked to move both bookings."
    out = _sec([{"zd_ref": "ZD-4491", "summary": s,
                 "detail": s + " Taylor replied without checking the slot."}])
    head = out.split("\n")[0]
    assert s not in head
    assert head.rstrip().endswith("(ZD-4491)"), head


def test_a_contact_with_no_detail_keeps_its_whole_summary():
    """Nothing under it carries the rest, so trimming would lose the only
    telling. The opposite failure to the one above, and just as bad."""
    out = _sec([{"zd_ref": "ZD-4491", "summary": LONG}])
    assert "Guest later reported he was also ill" in out


def test_the_trim_is_never_a_mid_word_cut():
    out = _sec([{"zd_ref": "ZD-4491", "summary": "x" + " word" * 60,
                 "detail": "d"}])
    head = out.split("\n")[0]
    assert "…" in head and not re.search(r"\bwor…", head)


def test_head_summary_is_driveable_on_its_own():
    assert _head_summary("", "d") == ""
    assert _head_summary("Short one.", "") == "Short one."
    assert _head_summary("First. Second.", "detail") == "First."


# ── 2. no Minded AI gaps ────────────────────────────────────────────────────

def test_no_minded_ai_gap_can_be_assigned():
    """The model picks gaps from this list and nothing else, so removing them
    here is what stops a Zendesk frame carrying one. A bot speaking first is a
    step in the flow, not a party to the case."""
    assert not [g for g in GAP_TAXONOMY if "minded" in g.lower()], GAP_TAXONOMY


def test_the_gap_list_did_not_become_empty():
    """Deleting two entries must not read as deleting the mechanism."""
    assert len(GAP_TAXONOMY) >= 5
    assert "CE escalation missing" in GAP_TAXONOMY


def test_the_prompt_offers_no_minded_ai_gap():
    """Driven through the real prompt builder — the list reaches the model
    through it, and a stale copy there would defeat the taxonomy change."""
    from server import prompts
    assert "Minded AI" not in prompts.classification_prompt("x", {}, [])


# ── 3 + 4. what the post carries ────────────────────────────────────────────

def test_area_of_improvement_is_posted():
    d = _draft(area_of_improving=["Surface the delivery window at checkout."])
    out = format_rca_slack(REVIEW, d)
    assert "*Area of improvement*" in out
    assert "Surface the delivery window at checkout." in out


def test_flags_are_not_posted_even_when_present():
    d = _draft(rca_v3={**V3, "flags": [
        {"team": "content", "flag": "The window is not on the page.",
         "evidence": "redemption is null"}]})
    out = format_rca_slack(REVIEW, d)
    assert "*Flags*" not in out
    assert "The window is not on the page." not in out


CLIENT = pathlib.Path("client/index.html").read_text(encoding="utf-8")
CODE = "\n".join(
    ln for ln in re.sub(r"<!--.*?-->", "", CLIENT, flags=re.S).split("\n")
    if not ln.strip().startswith("//"))


def test_the_chip_row_and_the_server_agree_on_both_sections():
    """NEGATIVE + presence on client-side JS, which has no harness here
    (CLAUDE.md rule 2 permits both, and this says so).

    The drift these two lines catch is the actual defect: the client dropped
    Area of improvement while the server kept emitting it.
    """
    assert "'Area of improvement'" in CODE, "the chip row lost the section again"
    assert "['flags'," not in CODE, "Flags is back in the client SECTIONS list"


def test_flags_are_still_editable_on_the_card():
    """Removing the SECTION must not remove the FIELD. Flags carry the owning
    team and are worked on the card; only the channel post loses them."""
    assert "data-flag-idx" in CODE


# ── 5. the resolution types ─────────────────────────────────────────────────

def test_more_time_and_more_info_are_recordable():
    """Both were happening and neither could be logged, so those cases were
    recorded as "None" and read as a guest who got nothing."""
    assert "'More time'" in CODE and "'More info'" in CODE


def test_the_existing_resolution_types_survive():
    for t in ("'HOC'", "'Refund to card'", "'Discount code'", "'None'"):
        assert t in CODE, f"{t} was dropped from the resolution types"


def test_the_amount_box_is_gated_on_a_unit_not_on_none():
    """An extended validity is a date and information given is neither a
    percentage nor a sum, so neither takes an amount. Gating on `!== 'None'`
    would put an unlabelled number box on both."""
    assert "const hasAmount = type !== 'None' && unit !== ''" in CODE


# ── 6. the detail narrates, the miss judges ─────────────────────────────────

def test_the_prompt_separates_the_account_from_the_verdict():
    """The rule this pins, and why a source assertion is the honest test here.

    `detail` came back editorialising — "Taylor replied that the tickets stayed
    valid WITHOUT CHECKING whether a slot was reserved" — which is the criticism
    wearing the account's clothes. The same finding then sat on the card twice:
    once unlabelled inside the narration, once as the ce_miss, and the
    unlabelled copy is the one a reader takes for fact.

    This asserts the INSTRUCTION reaches the model, which is all that can be
    checked without a live call: the behaviour is the model's, and the prompt
    is the only lever the code has on it. It is not a claim that the model
    obeys — a run is what shows that.
    """
    from server.prompts import RCA_V4_TEMPLATE
    t = RCA_V4_TEMPLATE
    assert "`detail` NARRATES. `ce_miss` JUDGES." in t
    for banned in ("without checking", "failed to", "should have"):
        assert banned in t, (
            f"the banned-phrase list lost {banned!r}, so the rule no longer "
            f"names what it is ruling out")
    assert "Do not manufacture a miss to fill the field." in t, (
        "the rule bans editorialising in detail without saying that an "
        "unremarkable contact may have no miss — which invites one to be "
        "invented instead")
