"""Everything downstream of the "What went wrong" shape, pinned in one place.

WHY THIS FILE EXISTS. The WWR structure is about to be rewritten — three
sections instead of four, claim accuracy folded into what-went-wrong, fixes
carrying their owning team. Six things read that shape and none of them would
fail loudly if it changed underneath them: they would render an empty section,
route nothing, or post a Slack message missing a heading, all of which look
like a quiet case rather than a broken build.

Each test below drives the consumer rather than reading source, so a change
that breaks the wiring fails HERE with a name, instead of on a card.
"""
import json

from server.checklist import WHAT_WENT_WRONG_STRUCTURE, ACTION_TEAMS
from server.services import wwr_post
from server.services.rca_v4_validate import V4_PROJECTION, project_v4, validate


def _rca(**kw):
    issue = {"issue": "Tickets never arrived",
             "claim": "I waited two hours and nothing came",
             "claim_accuracy": "Accurate",
             "root_cause": "The fulfilment run failed silently",
             "operational_failure": "Nobody watched the fulfilment queue",
             "fix": {"action": "Add an alert on failed fulfilment",
                     "owner": "TECH"}}
    issue.update(kw)
    return {"what_went_wrong": {"guest_issues": [issue],
                                # Actions Taken is built from the case's
                                # unsolved GAPS now, not from §3's fixes.
                                "gaps": [{"gap": "Add an alert on failed "
                                                 "fulfilment", "team": "TECH",
                                          "source_ref": "ZD-1"}]},
            "flags": [{"team": "TECH", "flag": "No alert on failed fulfilment"}],
            "dss": {"prescribes": "Resend or refund"}}


# ── 1. the mandated headings and the Slack post agree ──────────────────────

def test_the_slack_post_asks_for_exactly_as_many_headings_as_exist():
    """`MANDATED_HEADINGS` is a hardcoded count beside a list that can change
    length. If the list shrinks and the count does not, the post indexes past
    the end — silently, because a missing heading reads as a quiet case."""
    assert wwr_post.MANDATED_HEADINGS == len(WHAT_WENT_WRONG_STRUCTURE), (
        f"the structure has {len(WHAT_WENT_WRONG_STRUCTURE)} headings and "
        f"wwr_post expects {wwr_post.MANDATED_HEADINGS}")


def test_every_mandated_heading_reaches_the_post():
    got = wwr_post.headings()
    assert len(got) == len(WHAT_WENT_WRONG_STRUCTURE), got
    assert all(str(h).strip() for h in got), got


# ── 2. the projection still finds what it projects ─────────────────────────

def test_every_projected_column_is_reachable_in_a_validated_rca():
    """The projection walks a path into rca_v3. Move a section and the walk
    finds nothing — which stores an empty column that reads as data."""
    out, _ = validate(_rca())
    cols = project_v4(out)
    for col in V4_PROJECTION:
        assert col in cols, f"{col} is projected but the walk found nothing"


def test_the_guest_issues_projection_actually_carries_the_issue():
    out, _ = validate(_rca())
    cols = project_v4(out)
    assert cols["guest_issues"], "the issue vanished between validate and the column"
    assert cols["guest_issues"][0]["issue"] == "Tickets never arrived"


# ── 3. Actions Taken is built from the WWR shape ───────────────────────────

def test_a_fix_reaches_its_team_tab():
    """Driven through validate, which is what actually builds the section —
    the case's unsolved gaps, routed to the team that owns each."""
    out, _ = validate(_rca())
    assert "Add an alert on failed fulfilment" in out["actions_taken"]["tech"], \
        out["actions_taken"]


def test_actions_taken_is_computed_not_left_empty():
    """An empty Actions Taken on a card with findings is the failure mode:
    it looks like a clean case."""
    out, _ = validate(_rca())
    assert any(out["actions_taken"].get(t) for t in ACTION_TEAMS), \
        out["actions_taken"]


def test_every_action_tab_is_a_team_or_the_unrouted_tab():
    """Unrouted is a TAB, not a tenth team — it is deliberately absent from
    ACTION_TEAMS, which is what flags and fix owners are validated against."""
    from server.checklist import ACTION_TAB_ORDER, UNROUTED
    out, _ = validate(_rca())
    assert set(out["actions_taken"]) == set(ACTION_TAB_ORDER), out["actions_taken"]
    assert UNROUTED not in ACTION_TEAMS, "unrouted must not be a valid team"


# ── 4. the card's own fields survive validation ────────────────────────────

def test_the_fields_the_card_renders_are_all_present():
    """The client reads these by name. A rename lands as a blank block."""
    out, _ = validate(_rca())
    issue = out["what_went_wrong"]["guest_issues"][0]
    for field in ("issue", "claim", "claim_accuracy", "root_cause",
                  "operational_failure", "sop_gap", "pattern", "fix",
                  "evidence"):
        assert field in issue, f"the card renders {field} and validate dropped it"


def test_the_dss_block_carries_its_three_fields():
    out, _ = validate(_rca())
    for field in ("prescribes", "ref", "followed"):
        assert field in out["dss"], f"dss.{field} is missing"


# ── 5. the whole thing is still JSON-serialisable to the column ────────────

def test_a_validated_rca_round_trips_through_json():
    """It is stored as JSON. A value that will not serialise loses the row."""
    out, _ = validate(_rca())
    assert json.loads(json.dumps(out))["what_went_wrong"]["guest_issues"]
