"""`what_went_wrong`, to the written spec.

The spec is the deliverable, so the checks are against what it requires rather
than against how it happens to be implemented. Four things it changes, each
with a failure it exists to prevent:

  * ONE BLOCK = ONE VERDICT AND ONE FIX. Splitting on causes produces three
    blocks for one gap; merging two fixes puts work on one desk that belongs
    on two.
  * `claim_accuracy` DECIDES HOW MUCH EXISTS. Inaccurate or Unknown means
    nothing to diagnose and nothing to fix — a root cause under an Inaccurate
    verdict is the shape of diligence with nothing behind it, and somebody
    acts on it.
  * `fix` IS AN OBJECT. Action, owner, the gap it closes, where that gap was
    read, and the count that sizes it. As one string beside a separate
    `owner`, nothing tied the action to its evidence and an invented fix read
    exactly like a derived one.
  * `backs_claim` IS THREE-VALUED. null is a real answer — the entry is not
    about the claim. Anything unrecognised lands there rather than on No,
    because a wrong No reads as settled.
"""
import pytest

from server.services.rca_v4_validate import CLAIM_ACCURACY, validate


def _issue(**over):
    base = {"issue": "Tickets not delivered at purchase",
            "claim": "I purchased two tickets, then got a notification",
            "claim_accuracy": "Accurate",
            "claim_accuracy_note": "The fulfilment run failed at 15:28.",
            "root_cause": "The Selenium run returned no ticket URLs.",
            "operational_failure": "Nothing watches Selenium runs.",
            "sop_gap": "No DSS path covers a same-day booking with a failed run.",
            "pattern": "4 similar reviews on TID 43605 in 30 days.",
            "fix": {"action": "Add a Selenium failure alert for same-day bookings",
                    "owner": "RO",
                    "because": "No alert fires when a run returns no tickets",
                    "source": "zendesk",
                    "sized_by": "4 similar reviews on TID 43605 in 30 days"},
            "evidence": [{"text": "The 15:28 run returned no ticket URLs",
                          "source": "zendesk", "ref": "ZD-34011333",
                          "backs_claim": "Yes"}]}
    base.update(over)
    out, notes = validate({"what_went_wrong": {"guest_issues": [base]}})
    rows = out["what_went_wrong"]["guest_issues"]
    return (rows[0] if rows else None), notes


# ── the four-value verdict ─────────────────────────────────────────────────

def test_the_verdict_is_the_specs_four_values():
    assert list(CLAIM_ACCURACY) == ["Accurate", "Partly accurate",
                                    "Inaccurate", "Unknown"]


def test_a_claim_that_cannot_be_settled_is_unknown_not_inaccurate():
    """"No record of this" starts with "no". Reading it as Inaccurate
    contradicts a guest on evidence that was never about them."""
    got, _ = _issue(claim_accuracy="No record of this")
    assert got["claim_accuracy"] == "Unknown"


# ── the verdict decides how much of the block exists ───────────────────────

@pytest.mark.parametrize("verdict", ["Inaccurate", "Unknown"])
@pytest.mark.parametrize("field", ["root_cause", "operational_failure",
                                   "sop_gap", "fix"])
def test_an_undiagnosable_verdict_nulls_the_causal_fields(verdict, field):
    got, _ = _issue(claim_accuracy=verdict)
    assert got[field] is None, (
        f"{field} survived a {verdict} verdict — the guest is wrong, or we "
        f"cannot tell, so there is nothing to diagnose and nothing to fix")


@pytest.mark.parametrize("verdict", ["Accurate", "Partly accurate"])
def test_a_diagnosable_verdict_keeps_them(verdict):
    got, _ = _issue(claim_accuracy=verdict)
    for f in ("root_cause", "operational_failure", "sop_gap"):
        assert got[f], f
    assert got["fix"]


def test_dropping_them_is_reported_not_silent():
    """A field the model wrote and the projection removed is a change to its
    answer, and the trail is where that is said."""
    _, notes = _issue(claim_accuracy="Inaccurate")
    assert any("leaves nothing to diagnose" in n for n in notes), notes


# ── fix is an object ───────────────────────────────────────────────────────

def test_the_fix_carries_all_five_parts():
    got, _ = _issue()
    for k in ("action", "owner", "because", "source", "sized_by"):
        assert k in got["fix"], k


def test_a_fix_with_no_action_is_not_a_fix():
    got, _ = _issue(fix={"owner": "RO", "because": "a gap"})
    assert got["fix"] is None


def test_an_invented_owner_is_dropped_and_reported():
    got, notes = _issue(fix={"action": "do it", "owner": "Marketing"})
    assert got["fix"]["owner"] is None
    assert any("fix.owner" in n for n in notes), notes


def test_an_invented_source_is_dropped():
    got, _ = _issue(fix={"action": "do it", "source": "guesswork"})
    assert got["fix"]["source"] is None


def test_a_pre_object_fix_string_keeps_its_words():
    """Drafts written before the split hold a bare string. Dropping it would
    delete an analyst's fix on every older review."""
    got, _ = _issue(fix="RO: watch the fulfilment queue")
    assert got["fix"]["action"] == "RO: watch the fulfilment queue"
    assert got["fix"]["owner"] is None


def test_owner_is_no_longer_a_top_level_issue_field():
    """It was in both places with nothing reconciling them — a model judgement
    about the ISSUE beside a keyword rule about each ACTION, free to disagree
    on the same row."""
    got, _ = _issue()
    assert "owner" not in got


# ── backs_claim ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("given,want", [
    ("Yes", "Yes"), ("No", "No"), (None, None), ("", None),
    ("maybe", None), ("probably not", None),
])
def test_backs_claim_is_three_valued_and_falls_to_null(given, want):
    """Anything unrecognised lands on null, never No. A wrong No reads as
    settled and contradicts a guest on evidence that was never about them."""
    got, _ = _issue(evidence=[{"text": "a row", "source": "booking",
                               "backs_claim": given}])
    assert got["evidence"][0]["backs_claim"] == want


def test_every_evidence_row_carries_the_key():
    """A key present on some rows and absent on others is how a renderer
    works on one review and throws on the next."""
    got, _ = _issue(evidence=[{"text": "a", "source": "booking"},
                              "[booking] a legacy string"])
    assert all("backs_claim" in e for e in got["evidence"])
