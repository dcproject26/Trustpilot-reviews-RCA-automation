"""Area of improvement: pointers that can name where they came from.

HANDOFF §5. It emitted one paragraph welding five recommendations together,
with material that appears in no finding on the card. The rules:

  * one short pointer per line;
  * every point traces to an operational failure, an SOP gap or a flag;
  * nothing invented — the correction to a documented gap, not an opinion
    about a better world;
  * empty when nothing was found, not padded with generic advice.

Provenance is a CONSTRAINT ON THE MODEL, not decoration. The value is that it
forces the derivation: a point that cannot name its source is invented, and is
dropped before it is written — the same way `fix` is null when no evidence
entry shows a gap. So it is CHECKED here rather than trusted, and the drop is
counted, because a section that silently shrank is indistinguishable from a
model that had less to say.

Driven through `validate`, which is what both write paths call.
"""
import pytest

from server.services.rca_v4_validate import validate

FAILURE = "The fulfilment run failed three times with no alert to anyone"
GAP     = "Nothing checks that a booking with no ticket by T-2h is escalated"
FLAG    = "First reply to the guest came 40 minutes after the SLA"


def _card(**over):
    base = {
        "l1": "Operations Issue", "l2": "Ticket Issues",
        "what_went_wrong": {"guest_issues": [{
            "issue": "Voucher never delivered",
            "claim": "I waited two hours and nothing came.",
            "claim_accuracy": "Accurate",
            "root_cause": "The fulfilment run failed silently.",
            "operational_failure": FAILURE,
            "sop_gap": GAP,
            "evidence": [],
        }]},
        "flags": [{"team": "CO", "flag": FLAG, "evidence": "ZD-1 shows 15:41 → 16:21"}],
        "takedown": {"verdict": "No"},
    }
    base.update(over)
    return base


def _points(out):
    return [a["point"] for a in out["area_of_improving"]]


def test_a_point_that_names_a_real_failure_survives():
    out, notes = validate(_card(area_of_improving=[
        {"point": "Alert the on-call when a fulfilment run fails",
         "from": "operational_failure", "source": FAILURE}]))
    assert _points(out) == ["Alert the on-call when a fulfilment run fails"]
    assert out["area_of_improving"][0]["from"] == "operational_failure"
    assert not [n for n in notes if "area of improvement" in n], notes


@pytest.mark.parametrize("kind,source", [
    ("operational_failure", FAILURE),
    ("sop_gap",             GAP),
    ("flag",                FLAG),
])
def test_all_three_kinds_of_source_are_accepted(kind, source):
    out, _ = validate(_card(area_of_improving=[
        {"point": "Do the thing that would have caught it", "from": kind,
         "source": source}]))
    assert len(out["area_of_improving"]) == 1, (kind, out["area_of_improving"])


def test_a_point_whose_source_matches_nothing_is_dropped_and_counted():
    """The whole mechanism. An invented point reads exactly like a derived one
    once it is on the card, so it has to be stopped before it renders."""
    out, notes = validate(_card(area_of_improving=[
        {"point": "Move the whole team onto a new ticketing system",
         "from": "sop_gap",
         "source": "Our vendor management strategy is not fit for purpose"}]))
    assert out["area_of_improving"] == []
    assert any("matches no operational failure" in n for n in notes), notes
    assert any("dropped as invented" in n for n in notes), notes


def test_a_point_with_no_source_at_all_is_dropped_and_said_differently():
    """Two different failures, deliberately not one message. A point with no
    source never derived anything; a point with a source that matches nothing
    claims a derivation it does not have. Merging them would hide which."""
    out, notes = validate(_card(area_of_improving=[
        {"point": "Improve communication with guests"}]))
    assert out["area_of_improving"] == []
    said = " ".join(n for n in notes if "area of improvement" in n)
    assert "named no operational failure" in said, notes
    assert "dropped as invented" not in said, (
        "an unsourced point is being reported as an invented one — different "
        "faults, and only one of them is the model overreaching")


def test_a_bare_string_is_the_pre_provenance_shape_and_does_not_survive():
    """Keeping it would be keeping exactly the points this rule removes: a bare
    string is a point with no derivation at all."""
    out, notes = validate(_card(area_of_improving=[
        "Surface the delivery window at checkout"]))
    assert out["area_of_improving"] == []
    assert any("named no operational" in n for n in notes), notes


def test_a_point_must_match_the_kind_it_claims():
    """A point that says it comes from the SOP gap and matches only a flag is
    not derived from what it claims, and the claim is the thing being relied
    on."""
    out, notes = validate(_card(area_of_improving=[
        {"point": "Escalate before the SLA expires", "from": "sop_gap",
         "source": FLAG}]))
    assert out["area_of_improving"] == []
    assert any("dropped as invented" in n for n in notes), notes


def test_the_source_does_not_have_to_be_quoted_to_the_character():
    """A model asked to quote a field will paraphrase it. The check is a
    derivation check, not a string-equality check — demanding the second gets
    real points dropped, which is the inverse bug."""
    out, _ = validate(_card(area_of_improving=[
        {"point": "Alert on a failed fulfilment run",
         "from": "operational_failure",
         "source": "the fulfilment run failed three times, no alert"}]))
    assert len(out["area_of_improving"]) == 1


def test_an_empty_card_produces_an_empty_section_and_no_complaint():
    """"Empty when nothing was found, not padded with generic advice." A card
    with no failure, gap or flag has nothing to improve, and saying so is not
    an error worth a trail line."""
    out, notes = validate({
        "l1": "Miscellaneous Issue", "l2": "Vague review",
        "what_went_wrong": {"guest_issues": []}, "flags": [],
        "area_of_improving": [], "takedown": {"verdict": "No"}})
    assert out["area_of_improving"] == []
    assert not [n for n in notes if "area of improvement" in n], notes


def test_the_good_points_survive_the_bad_ones():
    """One invented point must not cost the derived ones — losing the whole
    section to one bad row is the failure this file's own module docstring
    warns about."""
    out, _ = validate(_card(area_of_improving=[
        {"point": "Alert the on-call when a fulfilment run fails",
         "from": "operational_failure", "source": FAILURE},
        {"point": "Rebuild the supply chain", "from": "flag",
         "source": "nothing on this card resembles this at all"},
        {"point": "Escalate a ticketless booking at T-2h", "from": "sop_gap",
         "source": GAP}]))
    assert _points(out) == ["Alert the on-call when a fulfilment run fails",
                            "Escalate a ticketless booking at T-2h"]


def test_a_point_is_one_line_with_its_bullet_stripped():
    """A bullet prepended to a block-level editable renders on its own line."""
    out, _ = validate(_card(area_of_improving=[
        {"point": "• Alert the on-call when a run fails",
         "from": "operational_failure", "source": FAILURE}]))
    assert _points(out) == ["Alert the on-call when a run fails"]


def test_the_shape_the_renderer_gets_is_always_the_same_three_keys():
    out, _ = validate(_card(area_of_improving=[
        {"point": "Alert the on-call", "from": "operational_failure",
         "source": FAILURE, "extra": "ignored"}]))
    assert set(out["area_of_improving"][0]) == {"point", "from", "source"}


def test_a_findings_free_card_cannot_smuggle_a_point_through():
    """The end-to-end shape of §5: with nothing on the card to derive from,
    every point is invented by construction."""
    out, notes = validate({
        "l1": "Operations Issue", "l2": "Ticket Issues",
        "what_went_wrong": {"guest_issues": []}, "flags": [],
        "takedown": {"verdict": "No"},
        "area_of_improving": [
            {"point": "Audit every listing quarterly", "from": "sop_gap",
             "source": "no quarterly audit exists"}]})
    assert out["area_of_improving"] == []
    assert any("area of improvement" in n for n in notes), notes


# ── the pointer reaches Slack as a pointer ─────────────────────────────────
#
# A mutation making the Slack composer stringify the point object instead of
# reading its text survived the whole suite: the thread would have carried
# "{'point': '...', 'from': ..., 'source': ...}" under a heading leadership
# reads, and nothing would have gone red.

POINT = {"point": "Alert the on-call when a fulfilment run fails",
         "from": "operational_failure", "source": FAILURE}


def _slack(**over):
    from server.services.slack import format_rca_slack
    from tests.test_slack_v3_format import REVIEW, _v4draft
    return format_rca_slack(REVIEW, _v4draft(**over))


def test_the_point_reaches_slack_as_its_text():
    out = _slack(rca_v3={"area_of_improving": [POINT]})
    assert "• Alert the on-call when a fulfilment run fails" in out


def test_no_python_repr_of_a_point_reaches_slack():
    """The negative half. A dict rendered into the post is not merely ugly —
    it is the internal shape of our validator on a page CX leadership reads."""
    out = _slack(rca_v3={"area_of_improving": [POINT]})
    assert "'point'" not in out and "{'" not in out, out[:400]
    assert "operational_failure" not in out, (
        "the provenance is a constraint on the model, not thread content")


def test_the_slack_composer_still_carries_a_legacy_string_point():
    out = _slack(rca_v3={"area_of_improving": ["Surface the window at checkout"]})
    assert "• Surface the window at checkout" in out


def test_an_empty_improvement_list_posts_the_empty_marker_not_a_blank_heading():
    out = _slack(rca_v3={"area_of_improving": []}, area_of_improving=[])
    i = out.find("Area of improvement")
    assert i > 0, "the section heading is gone from the post"
    assert "—" in out[i:i + 120], out[i:i + 120]
