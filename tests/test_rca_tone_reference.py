"""Approved replies reach the model as VOICE, and never as content.

Dropping the standalone drafter bought grounding at the cost of brand voice.
Passing the canned replies into the RCA call buys it back — but a tone example
sitting next to a "write a reply" instruction is the single easiest way to get
a canned answer with this guest's name pasted into it. The token is only safe
while output rule 18 is next to it, so both are asserted together.

The rendering is checked at source rather than by calling the model: what
matters is that the examples arrive labelled as voice, under a rule that says
copy none of their content.
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
    assert "APPROVED REPLY VOICE" in prompts.RCA_V4_TEMPLATE


def test_every_token_is_substituted():
    """A token left unreplaced ships '<<CANNED_TONE>>' to the model as text."""
    out = _prompt(canned_list=CANNED)
    assert "<<" not in out, f"unsubstituted token: {out[out.find('<<'):][:40]!r}"


def test_the_approved_replies_reach_the_model():
    out = _prompt(canned_list=CANNED)
    assert "I've refunded the full amount today." in out
    assert "Ticket delivered late" in out


def test_only_the_first_three_examples_go_in():
    """More than three starts reading like a pattern to match rather than a
    register to borrow."""
    many = [{"situation": f"S{i}", "response": f"UNIQUE_BODY_{i}"} for i in range(6)]
    out = _prompt(canned_list=many)
    assert "UNIQUE_BODY_2" in out
    assert "UNIQUE_BODY_3" not in out


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
    assert "APPROVED REPLY VOICE" in out
    assert "NO APPROVED MACRO, NO REPLY" in out, "rule 20 is not in the prompt"


def test_a_malformed_canned_row_does_not_break_the_prompt():
    out = _prompt(canned_list=[{"situation": None, "response": None}, {}])
    assert "<<" not in out


# ── the rule that makes the token safe ──────────────────────────────────────

def test_the_no_copying_rule_ships_with_the_examples():
    """Without the no-copying rule the token is a liability, not a feature.
    Asserted on the rule's own text, not on its number — the number surviving
    while the clause is gone is the failure this has to catch."""
    t = prompts.RCA_V4_TEMPLATE
    head = "19. `suggested_response` follows the voice of the APPROVED REPLY VOICE examples"
    assert head in t, "the no-copying output rule is missing or reworded"
    rule = t[t.find(head):]
    for phrase in ("Never copy a sentence", "never carry over a remedy",
                   "never use one as a template"):
        assert phrase in rule, f"the no-copying rule lost its {phrase!r} clause"
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


def test_the_examples_are_labelled_as_voice_where_they_appear():
    """The label sits next to the text, not only in the rules block — the
    model reads the block in place."""
    out = _prompt(canned_list=CANNED)
    i = out.find("I've refunded the full amount today.")
    assert "never content to copy" in out[max(0, i - 600):i]


# ── two rules a live v3 row showed we needed ────────────────────────────────

def test_owner_is_never_a_guest():
    """The model returned owner: "Guest", trying to say no team is at fault.
    owner means who ACTS, and a guest cannot be assigned work — the answer is
    null, not a new enum member."""
    t = prompts.RCA_V4_TEMPLATE
    assert "`owner` is null — never \"Guest\"" in t, \
        "nothing tells the model what to do when no internal team owns the issue"
    assert "Guest" not in t.split('"owner": "<')[1].split(">")[0], \
        "Guest must not be in the owner enum"


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
    assert "must trace to something the guest SAID OR IMPLIED" in out
    assert "go to `flags` and `sop_compliance`" in out
    assert "Do not repeat in `guest_issues` anything you have already raised in `flags`" in out


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
