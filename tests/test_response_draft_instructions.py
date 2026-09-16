"""The response-draft prompt carries rules that close the gap between
AI-suggested replies and the team's actual macro responses.

Every test here calls the function and checks its return value — the
prompt string that reaches the model. These are output assertions, not
source assertions (CLAUDE.md §2).
"""
import server.prompts as prompts


def _draft(**over):
    kw = dict(
        review_text="My tickets for the Colosseum tour on July 5 never arrived.",
        l1="Operations Issue", l2="Ticket Issues",
        resolution="Full refund initiated",
        guest_name="Marco",
    )
    kw.update(over)
    return prompts.response_draft_prompt(**kw)


# ── prospective language ───────────────────────────────────────────────────

def test_the_prompt_tells_the_model_to_use_prospective_language():
    """The team writes "is being processed" for actions not yet confirmed.
    The AI was writing "has been refunded" even when the resolution was still
    in progress. The prompt must carry a rule about tense."""
    out = _draft()
    assert "PROSPECTIVE" in out.upper()
    assert "is being processed" in out


def test_the_prompt_says_past_tense_needs_confirmation():
    out = _draft()
    assert "RESOLUTION" in out
    low = out.lower()
    assert "past" in low and "confirm" in low or "past-tense" in low


# ── case-specificity ───────────────────────────────────────────────────────

def test_the_prompt_requires_a_concrete_detail():
    """A reply that could go to any guest unchanged was the single most common
    divergence from the team standard."""
    out = _draft()
    low = out.lower()
    assert "concrete detail" in low or "specific detail" in low or \
        "experience name" in low or "could not be sent unchanged" in low


# ── DSS remedy alignment ──────────────────────────────────────────────────

def test_the_prompt_says_to_follow_the_dss_remedy():
    out = _draft(dss_rec={"action": "full refund"})
    low = out.lower()
    assert "dss" in low
    assert "remedy" in low or "prescribes" in low


def test_the_prompt_says_not_to_repeat_support_missteps():
    """When the DSS says full refund but earlier comms offered partial, the
    response should follow the DSS, not echo the partial."""
    out = _draft()
    low = out.lower()
    assert "misstep" in low or "do not repeat" in low


# ── chargeback awareness ──────────────────────────────────────────────────

def test_the_prompt_mentions_chargeback_handling():
    out = _draft()
    low = out.lower()
    assert "chargeback" in low or "bank dispute" in low or "payment reversal" in low


# ── brand voice case-specificity hard rule ─────────────────────────────────

def test_brand_voice_carries_the_case_specificity_hard_rule():
    """Added to orm_macros.yaml — the brand voice that is injected into every
    reply prompt must say a generic reply is not ready."""
    bv = prompts.BRAND_VOICE
    assert "sent unchanged" in bv or "could be sent unchanged" in bv


def test_the_brand_voice_reaches_the_draft_prompt():
    """The brand voice block must actually appear inside the assembled prompt."""
    out = _draft()
    assert "HEADOUT VOICE AND TONE" in out
    assert "HARD RULES" in out
