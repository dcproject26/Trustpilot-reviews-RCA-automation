"""Rule 10b: which conversations count as support contacts, and what to say
about each.

"customer interaction part - support - guest - will only consists of the
support tickets with the guest - that is chats, emails, call, web app, etc.
everything apart from Booking thread that is classified under task on zendesk
and internally raised tickets - like dss for follow up bot pings."

Two halves, and the first is the one that goes wrong quietly. A booking thread
pulled into this section raises the contact count, and a contact count that is
one too high reads as a guest who was handled when nobody spoke to them — the
section's whole job is to say how much contact there was.

Content assertions on a PROMPT, for the reason set out at the top of
test_rca_finding_rules.py: the prompt is data, its text is the deliverable,
and there is no reachability to be wrong about. The model is the only
enforcement, so a rule deleted from the prompt has no other symptom. What
those assertions must NOT do is stand in for the code that carries the answer
— so `test_support_contact_notes.py` drives the projection and
`test_contact_narrative_ui.py` drives the card.
"""
import pytest

from server.prompts import rca_v3_prompt


def _prompt():
    return rca_v3_prompt(
        review_text="x", booking={"id": "1"}, timeline=[], insights={},
        dss_rec={}, l1="Operations Issue", l2="Customer Support Issues",
        sub_theme="C. Ticket Delayed", support_summary="", checklist={},
        review_id="tp_1", timeline_raw=[], ticket_facts={},
        scenarios_routed=["Tickets sent late"], issue_questions=["Q?"],
        canned_list=[])


TEXT = _prompt()
FLAT = " ".join(TEXT.split())
SEG = TEXT[TEXT.index("`support_interaction_notes` — THE GUEST'S CONVERSATIONS"):]
SEG = SEG[:SEG.index("\n11. ")]


def test_the_section_was_found_at_all():
    """This file slices the rule out of the prompt and asserts against the
    slice. A slice that silently came back empty would pass every `not in`
    test below for the wrong reason."""
    assert len(SEG) > 800, f"the rule 10b slice is {len(SEG)} chars — it moved"


# ── what counts as a contact ───────────────────────────────────────────────

def test_the_channels_a_guest_can_reach_us_on_are_named():
    for ch in ("chat", "email", "call", "web", "app"):
        assert ch in SEG.lower(), ch


def test_the_booking_thread_is_excluded_and_told_where_it_goes():
    """Excluding it without saying where it belongs invites the model to drop
    it entirely, and the machinery IS wanted — in booking_logs."""
    assert "BOOKING THREAD" in SEG
    assert "task" in SEG, "the reason it is excluded is not given"
    assert "`booking_logs`" in SEG, "it is excluded with nowhere to go"


def test_internally_raised_tickets_are_excluded():
    assert "INTERNALLY RAISED TICKETS" in SEG
    for example in ("DSS", "bot ping"):
        assert example in SEG, example


def test_the_review_itself_is_excluded():
    """It is the artefact being analysed. Counting it as a contact makes
    every review look like a guest who reached out once."""
    assert "THE REVIEW ITSELF" in SEG


def test_it_says_what_a_wrong_inclusion_costs():
    """A rule with no consequence attached is one the model trades away
    against the others."""
    assert "raises the contact count" in " ".join(SEG.split())
    assert "handled when they were not" in " ".join(SEG.split())


# ── what to say about each ─────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["time", "channel", "summary"])
def test_every_field_the_projection_keeps_is_asked_for(field):
    """The other half of this pairing: CONTACT_FIELDS is what survives
    validation, and a field kept there but never requested is a column that
    is empty on every card for no reason anyone can see."""
    assert f"`{field}`" in SEG, field


def test_the_projection_and_the_prompt_ask_for_the_same_things():
    """Not a restatement of the test above — this one fails when the
    PROJECTION grows a field the prompt never mentions, which is the
    direction the parametrize cannot catch."""
    from server.services.rca_v4_validate import CONTACT_FIELDS
    for field in CONTACT_FIELDS:
        if field in ("zd_ref", "detail", "ce_miss"):
            continue                       # the join and the interpretation
        assert f"`{field}`" in SEG, (
            f"{field} survives validation and nothing ever asks the model "
            f"for it")


def test_skylar_is_identified_as_a_bot():
    """"we replied in 30 seconds" means something entirely different when it
    was the bot, and that difference is the point of the whole section."""
    assert "SKYLAR IS AN AI BOT" in SEG


def test_the_summary_covers_what_the_interaction_was():
    """The section is a SUMMARY, not a form. These are things the summary has
    to cover, not fields to fill in."""
    flat = " ".join(SEG.split())
    assert "WHAT THE SUMMARY COVERS" in flat
    for part in ("what the guest reached out with", "what we replied",
                 "what the guest said back", "RAISED INTERNALLY"):
        assert part in flat, part


def test_it_says_where_to_look_for_an_internal_escalation():
    """"how will you check if anything has been raised or not? you will look
    at internal notes or zendesk tickets by bots stating this for the same
    bid." Without the method the model infers it from what we PROMISED the
    guest, which is not evidence that anything happened."""
    flat = " ".join(SEG.split())
    assert "Do not infer it from what we promised" in flat
    assert "INTERNAL NOTE on the ticket" in flat
    assert "opened by a bot against the SAME BOOKING ID" in flat


def test_an_unverified_absence_is_not_reported_as_a_finding():
    flat = " ".join(SEG.split())
    assert "an absence you did not verify is not a finding" in flat


def test_it_asks_whether_dss_was_followed():
    assert "whether DSS was followed" in " ".join(SEG.split())


def test_the_entries_are_chronological():
    assert "Chronological" in SEG or "chronological" in SEG


def test_it_is_concise_bullets_in_single_sentences():
    flat = " ".join(SEG.split())
    assert "CONCISE BULLET POINTS, SINGLE SENTENCES, CHRONOLOGICAL" in flat
    assert "No paragraphs" in flat


# ── time and channel: precedence, not judgement ────────────────────────────

def test_the_model_is_told_the_ticket_wins_on_time_and_channel():
    """These two were struck from the schema once because the model filled
    them from the prose while the frame held the fact. They are back, so the
    precedence has to be stated or that returns."""
    assert "READ OFF THE TICKET, NOT JUDGED" in SEG
    assert "the FRAME's values, not yours" in " ".join(SEG.split())


def test_it_says_when_the_model_s_own_values_are_used():
    """Otherwise "the frame wins" reads as "never fill these", and an
    off-Zendesk contact loses its timestamp again."""
    flat = " ".join(SEG.split())
    assert "a contact Zendesk has no frame for" in flat
    assert "call the guest describes" in flat


def test_a_time_in_the_prose_is_not_a_substitute_for_the_field():
    assert "Never write a time into the prose" in " ".join(SEG.split())


# ── silence ────────────────────────────────────────────────────────────────

def test_an_undetectable_thing_is_left_out():
    assert "IF YOU CANNOT DETERMINE SOMETHING, WRITE NOTHING FOR IT" in SEG
    assert "worse than a blank" in " ".join(SEG.split())


def test_the_no_contact_sentence_is_given_verbatim():
    """The dashboard and the Slack post both match on it. A paraphrase
    renders as a data row saying nothing happened."""
    assert ('"No direct interaction found between the customer and the '
            'support team."') in SEG


def test_the_section_is_not_padded_to_avoid_an_empty_one():
    assert "Do not pad the section" in " ".join(SEG.split())
