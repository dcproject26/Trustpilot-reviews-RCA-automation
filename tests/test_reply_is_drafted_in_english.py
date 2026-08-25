"""The RCA drafts the reply in ENGLISH; translating it is a later step.

THE BUG THIS CLOSES, seen on a French review. The card showed:

    RESPONSE TO GUEST · FRENCH   ->  English text   (this is what gets SENT)
    ENGLISH WORKING COPY         ->  French text    (this is NOT sent)

Swapped. The cause was output rule 16, which carried two opposite instructions
in one sentence — "write it in the guest's language ... the English draft goes
in `suggested_response`". The model followed the first clause and put FRENCH in
`suggested_response`. translate_outgoing then passed that French to a prompt
beginning "Translate this customer-service reply from English into French", and
a model handed French under that instruction returns ENGLISH.

So the guest received an English reply, and the box that says it is not sent
held the French one.

Asserted at source because it is a PROMPT rule — there is nothing to drive
without a live model, and the negative assertion ("this instruction appears
nowhere") is one a source read can make honestly. CLAUDE.md rule 2 permits
exactly that shape.
"""
from tests.conftest import read_source

PROMPTS = read_source("server/prompts.py")


def _rule_16() -> str:
    i = PROMPTS.find("16. `suggested_response` is guest-facing")
    assert i != -1, "output rule 16 is gone or renamed"
    return PROMPTS[i:PROMPTS.find("17.", i)]


def test_the_reply_is_required_to_be_english():
    assert "WRITE IT IN ENGLISH" in _rule_16(), \
        "nothing tells the model which language to draft the reply in"


def test_the_contradictory_instruction_is_gone():
    """The negative assertion, and the one that matters: this exact
    instruction is what produced the swap, and it must appear nowhere."""
    rule = _rule_16()
    assert "Write it in the guest's language where the review is not in" not in rule, \
        "the contradictory instruction is back — the reply will be drafted in " \
        "the guest's language and the translation step will invert it"


def test_the_rule_says_translation_is_a_later_step():
    """Without this the "always English" reads as "never translate", which
    would send English to every non-English guest."""
    rule = _rule_16()
    assert "separate, later step" in rule
    assert "SOURCE" in rule, \
        "the rule does not say this field is the translation's input"


def test_the_translation_prompt_still_expects_english_in():
    """The other half of the contract. If this prompt ever stops saying
    'from English', the rule above stops being load-bearing."""
    i = PROMPTS.find("def reply_translation_prompt")
    body = PROMPTS[i:i + 900]
    assert "from English into {lang}" in body, \
        "the translation prompt no longer states its input language"
