"""The RCA post tags @reviewteam and the picked-up-by owner for REAL.

The header carried the literal text "@reviewteam" — grey text that pinged
nobody — and never named the owner at all. Now it emits Slack mention markup
(<!subteam^ID> for the group, <@ID> for the assignee), built from the id map in
content/orm_macros.yaml. A name we have no id for is still shown (plain text, no
ping) rather than dropped, so "a person we cannot tag" and "a review nobody
picked up" stay different lines.

Driven against the real functions and the real copy file — not a source grep.
The dashboard preview builds the same markup in client/index.html; that JS has
no harness here (CLAUDE.md rule 2), so this file is the tested copy of the logic
and the two are kept in step by hand.
"""
from types import SimpleNamespace as NS

from server.services.slack import (reviewer_mention, review_team_mention,
                                    build_rca_header)
from server.prompts import (REVIEWER_SLACK_IDS, REVIEW_TEAM_SUBTEAM_ID,
                            _reviewer_slack_ids, _review_team_subteam_id)


# ── the id map loads from the copy file ─────────────────────────────────────

def test_the_roster_ids_loaded_from_the_yaml():
    assert REVIEWER_SLACK_IDS.get("Avi") == "U06T7NGGC2K"
    assert REVIEWER_SLACK_IDS.get("Swagatom") == "U070N49J2SG"
    assert REVIEW_TEAM_SUBTEAM_ID == "S01CEB05D4H"
    # "Test" is on the roster but has no id — it must not appear in the map.
    assert "Test" not in REVIEWER_SLACK_IDS


def test_blank_or_none_id_entries_are_dropped_not_mapped_to_None():
    m = _reviewer_slack_ids({"reviewer_slack_ids":
                             {"A": "U1", "B": None, "C": "  ", None: "U9"}})
    assert m == {"A": "U1"}          # B (None), C (blank), None-key all dropped
    assert _review_team_subteam_id({}) == ""     # unset -> empty, not "None"


# ── the person mention: three outcomes, told apart ──────────────────────────

def test_a_known_reviewer_becomes_a_real_mention():
    assert reviewer_mention("Avi") == "<@U06T7NGGC2K>"
    assert reviewer_mention(" Paul ") == "<@U03BA9B2HL1>"   # trimmed


def test_a_reviewer_with_no_id_is_shown_as_plain_text_not_dropped():
    assert reviewer_mention("Test") == "Test"          # on roster, no id
    assert reviewer_mention("Someone Who Left") == "Someone Who Left"


def test_no_owner_is_the_empty_string_distinct_from_a_name():
    assert reviewer_mention("") == ""
    assert reviewer_mention("   ") == ""
    assert reviewer_mention(None) == ""


# ── the team mention ────────────────────────────────────────────────────────

def test_the_team_tag_is_real_subteam_markup():
    assert review_team_mention() == "<!subteam^S01CEB05D4H>"
    assert "@reviewteam" not in review_team_mention()   # the old plain text is gone


# ── the assembled header ────────────────────────────────────────────────────

def _review(**kw):
    return NS(rating=kw.get("rating", 1), author=kw.get("author", "A B"),
              picked_up_by=kw.get("picked_up_by", None))


def test_header_tags_team_and_owner_for_real():
    h = build_rca_header(_review(picked_up_by="Swagatom"), {"id": "33231693"})
    assert "*RCA — <!subteam^S01CEB05D4H>*" in h
    assert "BID 33231693" in h
    assert "Picked up by <@U070N49J2SG>" in h


def test_header_has_no_owner_line_when_unassigned():
    h = build_rca_header(_review(picked_up_by=None), {"id": "1"})
    assert "Picked up by" not in h
    assert "<!subteam^S01CEB05D4H>" in h          # team is still tagged


def test_header_shows_an_unmapped_owner_as_plain_text():
    h = build_rca_header(_review(picked_up_by="Test"), {"id": "1"})
    assert "Picked up by Test" in h
    assert "Picked up by <@" not in h             # no fake mention
