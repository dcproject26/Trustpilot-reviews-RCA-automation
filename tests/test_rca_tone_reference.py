import re
"""The approved macro reaches the model as THE REPLY, and is adapted, not copied
blind and not paraphrased away.

THIS FILE USED TO ASSERT THE OPPOSITE. The macros were passed as "voice only,
never content", because a reply nobody approved is indistinguishable on the card
from one that was, and a tone example next to a "write a reply" instruction is
the easiest way to get exactly that. The protection was the wrong one: what it
actually produced was the model writing its own reply in the approved register —
the very thing it was guarding against — while the approved text went unused.

What makes the macro safe to send now is structural rather than instructional.
It is selected for THIS review by a model that read it, and the candidate set is
first gated on the remedy the DSS named (services/reply_macro.py), so a macro
promising a refund cannot even be offered unless the playbook named one. The
prompt no longer has to be trusted not to carry a remedy across; the remedy
never reaches it.

So the guarantees asserted here changed shape: the macro's own sentences must
survive, the guest's specifics must be addressed, and nothing beyond what the
macro promises may be offered. Checked at source rather than by calling the
model — what matters is that the text arrives labelled as the reply, under rules
that say keep it, adapt it, and do not exceed it.
"""
import server.prompts as prompts


CLIENT = open("client/index.html", encoding="utf-8").read()
PIPE   = open("server/pipeline.py", encoding="utf-8").read()


CANNED = [
    {"situation": "Ticket delivered late", "response":
     "I'm so sorry your tickets were late — that's not the start to the day "
     "we wanted for you. I've refunded the full amount today."},
    {"situation": "Guide did not show", "response":
     "I'm sorry no one met you at the gate. I've put the full amount back on "
     "your card and it will land within five working days."},
]


def _prompt(**over):
    kw = dict(review_text="the voucher never came", booking={}, timeline=[],
              insights={}, dss_rec={}, l1="Operations Issue", l2="Ticket Issues",
              sub_theme="C. Ticket Delayed", support_summary="", checklist={},
              review_id="tp_tone_1")
    kw.update(over)
    return prompts.rca_v3_prompt(**kw)


# ── the token exists, is filled, and is labelled ────────────────────────────

def test_the_template_carries_a_tone_token():
    assert "<<CANNED_TONE>>" in prompts.RCA_V4_TEMPLATE
    assert "APPROVED REPLY FOR THIS CASE" in prompts.RCA_V4_TEMPLATE


def test_every_token_is_substituted():
    """A token left unreplaced ships '<<CANNED_TONE>>' to the model as text."""
    out = _prompt(canned_list=CANNED)
    assert "<<" not in out, f"unsubstituted token: {out[out.find('<<'):][:40]!r}"


def test_the_approved_replies_reach_the_model():
    out = _prompt(canned_list=CANNED)
    assert "I've refunded the full amount today." in out
    assert "Ticket delivered late" in out


def test_only_the_selected_macro_goes_in():
    """ONE macro, not three. Three of them was a pattern to blend, which is how
    the model came to write its own reply in their register instead of sending
    one of them; the selector has already chosen which applies to this review,
    so the others are noise that invites a merge."""
    many = [{"situation": f"S{i}", "response": f"UNIQUE_BODY_{i}"} for i in range(6)]
    out = _prompt(canned_list=many)
    assert "UNIQUE_BODY_0" in out
    assert "UNIQUE_BODY_1" not in out, "a second macro reached the prompt to blend with"


def test_no_matching_macro_tells_the_model_to_return_null():
    """A blank block reads as "no voice to match", and the instruction that
    used to sit here — "write in plain, warm, direct English" — got a reply
    that read exactly like an approved one and was not. The model is told to
    return null instead, and rule 20 backs it up."""
    out = _prompt(canned_list=[])
    assert "NO APPROVED MACRO MATCHES THIS REVIEW" in out
    assert "Return null for suggested_response" in out
    # The prohibition, not only the instruction. "Return null for
    # suggested_response — write in plain, warm, direct English" still
    # contains the first half and means the opposite, which is exactly the
    # edit a well-meaning tidy-up makes.
    assert "Do NOT write one" in out
    assert "plain, warm, direct English" not in out, \
        "the old invent-a-reply instruction is back alongside the new one"
    assert "APPROVED REPLY FOR THIS CASE" in out
    assert "NO APPROVED MACRO, NO REPLY" in out, "rule 20 is not in the prompt"


def test_a_malformed_canned_row_does_not_break_the_prompt():
    out = _prompt(canned_list=[{"situation": None, "response": None}, {}])
    assert "<<" not in out


# ── the rule that makes the token safe ──────────────────────────────────────

def test_the_keep_adapt_and_do_not_exceed_rule_ships_with_the_macro():
    """The three clauses that make sending the macro safe. Asserted on the
    rule's own text, not on its number — the number surviving while a clause is
    gone is the failure this has to catch.

    The one that matters most is the last: the gate stops an unauthorised
    remedy reaching the prompt, and this stops the model inventing one past the
    macro it was given. Both, because they fail differently — the gate cannot
    see a sentence the model writes."""
    t = prompts.RCA_V4_TEMPLATE
    head = "19. `suggested_response` IS THE APPROVED MACRO"
    assert head in t, "the keep-adapt-do-not-exceed rule is missing or reworded"
    # Whitespace-normalised: a prompt gets rewrapped routinely, and a clause
    # that only fails because it now spans a line break is a test measuring
    # the wrap rather than the rule.
    rule = re.sub(r"\s+", " ", t[t.find(head):t.find("20.", t.find(head))])
    # KEEP the approved sentences.
    assert "Keep them." in rule, "the rule no longer says to keep the macro's sentences"
    assert "do not compress it to a summary" in rule
    # ADAPT to this guest.
    assert "ADAPT IT" in rule, "the rule no longer says to address this guest's specifics"
    assert "form letter" in rule
    # DO NOT EXCEED what it promises.
    assert "NEVER GO BEYOND WHAT THE MACRO PROMISES" in rule, \
        "the rule lost the clause that stops an unauthorised remedy being added"
    assert "do not turn that into a refund" in rule
    assert "add no compensation of any kind" in rule
    assert "only from this case's evidence" in rule


# ── the template line goes, rather than rendering empty ─────────────────────

def test_the_template_name_line_is_gone_from_the_reply_block():
    """There is no separate drafting call, so no template was used. An old
    draft still carries a template_name in its column — rendering it would put
    a stale provenance claim on a reply it had nothing to do with."""
    assert "templateName" not in CLIENT, \
        "the reply block still reads template_name; it can only be stale now"
    assert "tpl-name" not in CLIENT, "the Template: X element is still rendered"


def test_the_pipeline_stops_writing_a_template_name():
    assert "template_name" not in PIPE


def test_the_macro_is_labelled_as_the_reply_where_it_appears():
    """The label sits next to the text, not only in the rules block — the model
    reads the block in place. Labelled "example" beside a "write a reply"
    instruction is what produced a reply nobody approved."""
    out = _prompt(canned_list=CANNED)
    i = out.find("I've refunded the full amount today.")
    # The label leads the block and the instructions follow the text, so the
    # window spans both sides of the macro body.
    block = re.sub(r"\s+", " ", out[max(0, i - 900):i + 1400])
    assert "THIS IS THE APPROVED REPLY FOR THIS CASE" in block
    assert "backbone" in block
    assert "ADDRESS WHAT THIS GUEST ACTUALLY RAISED" in block


# ── two rules a live v3 row showed we needed ────────────────────────────────

def test_owner_is_never_a_guest():
    """The model returned owner: "Guest", trying to say no team is at fault.
    owner means who ACTS, and a guest cannot be assigned work — the answer is
    null, not a new enum member."""
    t = prompts.RCA_V4_TEMPLATE
    assert "`owner` is null — never \"Guest\"" in t, \
        "nothing tells the model what to do when no internal team owns the issue"
    # Found by regex, not by splitting on an exact `"owner": "<`. The owner
    # moved into the `fix` object, whose keys are aligned — so the literal
    # gained spaces and the split raised IndexError. A test that dies on
    # whitespace was never checking the enum.
    m = re.search(r'"owner":\s*"<([^>]*)>"', t)
    assert m, "the owner enum is not in the schema at all"
    assert "Guest" not in m.group(1), "Guest must not be in the owner enum"


def test_the_review_itself_is_never_a_support_contact():
    """The model wrote channel: "Trustpilot" — counting the review as a
    contact. Every review would then carry a phantom contact and read as if
    someone had handled the guest."""
    t = prompts.RCA_V4_TEMPLATE
    assert "The REVIEW ITSELF is" in t
    assert "Trustpilot" in t, "the rule must name the value the model reached for"


# ── rules a real David run showed we needed ─────────────────────────────────
#
# Asserted on the SUBSTITUTED prompt, not the raw template. For a prompt the
# text is the behaviour — there is no branch that could be unreachable — but
# "in the file" and "reaches the model" are still different claims, and only
# the second one matters.

def test_a_guest_issue_must_trace_to_the_guest():
    """The model returned "Out-of-policy refund granted without DSS-prescribed
    compensation path" as guest issue 04 with no claim. The guest never raised
    it; it renders as a numbered complaint with an empty Claim block, and
    leadership reads it as something the guest said."""
    out = _prompt()
    # THE RULE MOVED, THE GUARANTEE DID NOT. It used to read "must trace to
    # something the guest SAID OR IMPLIED", which tied every issue to the
    # REVIEW and sent anything found only in the tickets to `flags` — the
    # mechanism that deleted the modification request in the Bhayani case.
    # An issue must still trace to this guest; it may now do so through what
    # they asked support for, not only through what they wrote publicly.
    assert "must trace to something that happened TO THIS GUEST" in out
    # Our own gaps that touched no guest request are still flags.
    assert "remain flags and are not guest issues" in out
    # And the boundary the user drew: the ASK is always on the card; only the
    # avoidable miss is a flag.
    assert "COULD have and did not" in out
    assert "It is NOT a flag: nobody did" in out
    # The old rule barred a guest issue from restating a flag at all, which
    # meant one event could only be described once — so a request the guest
    # made and our failure to act on it could not both appear. Now only the
    # WORDING may not repeat.
    assert "Do not repeat the SAME SENTENCE in both" in out


def test_a_cause_and_its_consequence_are_one_issue():
    """"we did not disclose the window" and "the window clashed with their
    schedule" is one complaint split in two — rule 9's "do not invent a second
    issue when there is one"."""
    out = _prompt()
    assert "Splitting a cause from its consequence is inventing one" in out
    assert "belongs in that issue's `root_cause`" in out


def test_the_reply_length_is_expressed_structurally():
    """A word ceiling was honoured weakly — the model came back at 162 against
    120. Sentence counts are followed far more reliably, so the constraint is
    "4-6 short sentences" with the word count as the gloss. On the template
    line too: that is where the model looks while it writes the field."""
    out = _prompt()
    head = out.split('"suggested_response"')[1][:220]
    assert "4-6 SHORT SENTENCES" in head, \
        "the constraint is not on the field the model is filling in"
    assert "120 words" in head, "the word count is still worth stating as a gloss"
    assert "count the sentences, not the" in out
    assert "about 90 words" in out, "the approved replies are the length reference"


def test_the_stated_issue_carries_one_too():
    out = _prompt()
    assert "60 words MAX" in out.split('"stated_issue"')[1][:200]


def test_an_issues_operational_failure_must_match_its_owner():
    """The tell for a bad split: owner RO with an operational_failure about
    what CE did means the issue belongs to CE, or is CE's issue already."""
    out = _prompt()
    assert "must describe conduct by the team named in" in out
    assert "restates another issue's finding" in out


def test_a_disclosure_claim_triggers_a_sweep_of_all_guest_facing_copy():
    """Two hours in one email, one day before in another, delivered in 28
    minutes. Our own copy contradicting itself is a bigger finding than an
    omission, and the model stopped at the first source that settled it."""
    out = _prompt()
    assert "check EVERY piece of guest-facing copy" in out
    assert "Know Before You Go" in out
    assert "its own CONTENT flag" in out


# ── plain English ───────────────────────────────────────────────────────────
#
# Rule 9 caps LENGTH and says nothing about vocabulary, so a sentence could sit
# inside the word limit and still be unreadable. Both of these came off a real
# card and both obeyed every rule that existed:
#
#   "SP cancelled the guest's booking and the refund was denied despite the
#    cancellation being vendor-initiated, not guest-initiated."
#   "Confirm vendor-initiated cancellation from booking record and process full
#    refund per standing policy; flag vendor cancellation rate for RO review."

def test_the_prompt_carries_a_plain_english_rule():
    out = _prompt()
    assert "PLAIN ENGLISH" in out
    assert "WRITE IT THE WAY YOU WOULD SAY IT" in out


def test_the_rule_shows_the_rewrite_not_just_the_ban():
    """A list of banned words teaches avoidance. A before/after teaches the
    register — and these are the actual sentences that prompted it."""
    out = " ".join(_prompt().split())
    assert "vendor-initiated, not guest-initiated" in out, "the NO example is gone"
    assert "The vendor cancelled the booking, then we refused the refund." in out, \
        "the YES rewrite is gone, so the rule only says what not to do"


def test_the_passive_voice_ban_says_why():
    """"was denied" hides who did it, which is the one thing an RCA exists to
    show. A ban with no reason is the first thing dropped in an edit."""
    out = " ".join(_prompt().split())
    assert "hide the person responsible" in out


def test_the_worst_offenders_are_named():
    out = " ".join(_prompt().split())
    for phrase in ("per standing policy", "in a timely manner", "-initiated",
                   "process a refund", "flag for review"):
        assert phrase in out, f"{phrase!r} is not banned by name"


def test_internal_shorthand_is_allowed_where_the_reader_uses_it():
    """Over-correcting is the inverse bug: expanding SP and RO for an audience
    that says them daily adds the length this rule exists to remove."""
    out = " ".join(_prompt().split())
    assert "Internal shorthand is fine" in out
    assert "SP, RO, CE, DSS, BID, TGID" in out


def test_the_guest_facing_field_is_exempted_from_shorthand():
    out = " ".join(_prompt().split())
    assert "read by a GUEST, so none of it appears there" in out


def test_the_word_ceilings_came_down():
    out = " ".join(_prompt().split())
    assert "Target 8–14 words; 20 is the hard ceiling" in out


def test_the_plain_english_rewrite_is_still_shown():
    """The two worst sentences on the card were TL;DR fields, and that section
    has been removed — but the rewrite they motivated is the whole of rule 9b
    and applies to every string in the RCA. A rule that only says "write
    plainly" is one the model agrees with and then ignores; the before/after
    is what makes it actionable."""
    out = " ".join(_prompt().split())
    assert "The vendor cancelled the booking, then we refused the refund." in out
    assert "PLAIN ENGLISH" in out


def test_the_removed_sections_are_not_asked_for():
    """A section removed from the schema but still described in the rules gets
    returned anyway, and then dropped by the validator — the model spends
    tokens on it and nobody ever sees the result."""
    out = _prompt()
    assert "tldr" not in out
    assert "sop_compliance" not in out
    assert "TL;DR" not in out
