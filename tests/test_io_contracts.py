"""Every input reaches the server, and every output the server sends is read.

The bugs this guards against all look identical from the outside: a control
that responds, a spinner that finishes, a green tick — and nothing saved, or
saved in a shape that destroys what was there. Flag to Biz rendered "sent to
Slack" without calling anything for months. Actions Taken flattened five tabs
of structured entries to bare strings on every edit.

Source assertions rather than a browser: the client is one HTML file with no
build step, and a wrong answer here is silent data loss.
"""
import re

import pytest


API    = open("server/api.py", encoding="utf-8").read()
CLIENT = open("client/index.html", encoding="utf-8").read()


def _handler_after(marker: str, span: int = 2500) -> str:
    i = CLIENT.find(marker)
    assert i > 0, f"{marker!r} not found in the client"
    return CLIENT[i:i + span]


# ── Actions Taken: the shape that goes out must be the shape read back ──────

def test_actions_are_saved_as_objects_not_flattened_to_strings():
    """The renderer reads a.with, a.handle, a.time and a.where. Saving
    a.context alone blanked all four for EVERY action in EVERY tab on the next
    reload — triggered by adding, editing or deleting any single one."""
    body = _handler_after("const persistActions", 1800)
    for field in ("with:", "handle:", "time:", "context:", "where:"):
        assert field in body, f"{field} is dropped when actions are saved"
    assert ".map(a => (a.context || a.with || '').trim())" not in CLIENT, \
        "actions are being flattened to strings again"


def test_a_failed_action_save_is_not_swallowed():
    """An empty catch made a failed save look identical to a successful one:
    the edit stayed on screen and was gone on reload."""
    body = _handler_after("const persistActions", 1800)
    assert "saveDraft(" in body, "actions bypass the shared save path"

    # No RCA edit may PATCH draft-v2 inline again: fetch resolves on 4xx/5xx,
    # so an inline call with no res.ok check reports a rejected save as done.
    inline = CLIENT.count("await fetch(`/api/reviews/${r.id}/draft-v2`")
    checked = CLIENT.count("resp.ok") + CLIENT.count("res.ok")
    assert inline <= checked, (
        f"{inline} inline draft-v2 saves but only {checked} response checks — "
        f"a rejected save would look successful")
    assert "keep local edit */ }" not in CLIENT.split("function saveDraft")[-1], \
        "an edit handler is swallowing save failures again"


def test_the_client_can_read_back_what_it_writes():
    """toItems tolerates old string rows; it must keep doing so, because rows
    flattened by the old code are still in the database."""
    i = CLIENT.find("const toItems")
    assert "typeof s === 'string'" in CLIENT[i:i + 400], \
        "drafts already flattened by the old code would render as undefined"


# ── the modal inputs each reach the request ─────────────────────────────────

@pytest.mark.parametrize("field,key", [
    ("flag-tag",        "tag"),
    ("flag-message",    "message"),
    ("flag-completion", "completion_rate"),
    ("flag-tgid",       "tgid"),
    ("flag-tid",        "tid"),
    ("flag-vid",        "vid"),
])
def test_every_flag_modal_input_is_sent(field, key):
    """An input nobody reads is a control that lies about what it does."""
    assert f'id="{field}"' in CLIENT, f"{field} is not in the modal"
    body = _handler_after("#flag-send-btn", 2200)
    assert field in body, f"{field} is rendered but never read"
    assert key in body, f"{key} is read but never sent"


def test_every_field_the_flag_endpoint_accepts_is_used():
    """The reverse: a field the API declares and never reads is a promise the
    UI can keep making with no effect."""
    i = API.find("class FlagToBiz")
    model = API[i:i + 700]
    declared = set(re.findall(r"^\s{4}(\w+):", model, re.M))
    body = API[API.find("async def flag_to_biz"):API.find("async def flag_to_biz") + 4000]
    facts = API[API.find("def _biz_facts"):API.find("def _biz_facts") + 700]
    for f in declared:
        assert f"body.{f}" in body or f"body.{f}" in facts, \
            f"FlagToBiz.{f} is accepted and never read"


# ── outputs: state the server sends that the client must act on ─────────────

@pytest.mark.parametrize("field", [
    "rca_posted_at",      # else the RCA posts to the thread twice
    "flag_to_biz_state",  # else the flag button re-arms after a reload
    "sent_at",
])
def test_state_the_server_sends_is_read_by_the_client(field):
    assert f'"{field}"' in API, f"{field} is no longer sent"
    assert field in CLIENT, f"the client never reads {field}"


def _candidate_mapping() -> str:
    """The client's candidate remap, sliced to where it actually ENDS.

    It was sliced to `i + 1200` — a character count, which is not a boundary
    of anything. Three explanatory lines were added inside the mapping and
    `experienceDate` fell off the end of the window, so a test named "the
    picker no longer reads experienceDate" failed against a build where the
    picker reads it on the line after the cut. A source assertion is only
    worth having if it is anchored to the construct rather than to its
    length.

    CLIENT-SIDE JAVASCRIPT, which has no test harness here — the exception
    CLAUDE.md names for a positive source assertion.
    """
    i = CLIENT.find("r.candidatesList = draft.candidates_list.map")
    assert i > 0, "the candidate remap is gone from the client"
    end = CLIENT.find("}))", i)
    assert end > i, "the candidate remap has no closing }))"
    return CLIENT[i:end]


def test_candidate_fields_the_picker_needs_are_all_produced():
    """The picker reads these off each candidate. A path that builds a
    candidate without them renders a card with blanks in the fields an
    associate chooses between bookings on."""
    mapping = _candidate_mapping()
    for key in ("experienceDate", "guestName", "matchReasons", "experience"):
        assert key in mapping, f"the picker no longer reads {key}"
    # Every server-side candidate builder must feed that mapping.
    pipe = open("server/pipeline.py", encoding="utf-8").read()
    for builder in ("def _make_candidate", "def _shape_weak_bid"):
        j = pipe.find(builder)
        assert j > 0, f"{builder} is gone"
        block = pipe[j:j + 1400]
        assert "matchReasons" in block, f"{builder} produces no match reasons"
        assert "visitDate" in block or "date_of_visit" in block, \
            f"{builder} produces no visit date"


# ── things only running the app can catch ───────────────────────────────────

def test_literal_routes_are_registered_before_the_wildcard_one():
    """FastAPI matches routes in declaration order. /api/reviews/bulk-status
    sat below /api/reviews/{review_id}, so every call to it was captured as a
    review lookup and answered 404 — the bulk reprocess progress indicator had
    never worked, and nothing said so, because a 404 in a background poll is
    invisible unless you are watching the console."""
    wildcard = API.find('@router.get("/api/reviews/{review_id}")')
    assert wildcard > 0
    for literal in ('@router.get("/api/reviews/bulk-status")',):
        at = API.find(literal)
        assert at > 0, f"{literal} is gone"
        assert at < wildcard, (
            f"{literal} is declared after /api/reviews/{{review_id}} and will "
            f"be swallowed by it")


def test_the_default_tab_matches_the_tab_marked_active():
    """The markup renders All as the active tab; state.filter said
    'identified'. So the dashboard opened claiming to show everything, with
    the count reading 3, and listed one review — hiding the unconfirmed match
    and the untraceable one, which are the two that need a human."""
    i = CLIENT.find("const state = {")
    block = CLIENT[i:i + 700]
    m = re.search(r"filter:\s*'([a-z]+)'", block)
    assert m, "state.filter is gone"
    default = m.group(1)
    active = re.search(r'class="inbox-tab active" data-tab="([a-z]+)"', CLIENT)
    assert active, "no tab is marked active in the markup"
    assert default == active.group(1), (
        f"the dashboard opens on the '{active.group(1)}' tab but filters to "
        f"'{default}' — reviews in other buckets are invisible")


def test_mock_mode_stops_slack_posts_leaving_the_machine():
    """post_to_thread checked only whether a client object existed, and the
    tokens are present in any environment configured for real use — so a run
    started with MOCK_MODE=true posted to the live Slack API. It failed on
    demo data with an invalid channel and thread_ts; against a real thread it
    would have posted, from a run whose whole point was that it could not."""
    slack = open("server/services/slack.py", encoding="utf-8").read()
    i = slack.find("async def post_to_thread(")
    assert i > 0
    body = slack[i:i + 1400]
    guard = body.find("if MOCK_MODE:")
    call = body.find("chat_postMessage")
    assert guard > 0, "post_to_thread does not check MOCK_MODE"
    assert guard < call, "the MOCK_MODE guard is after the call it should prevent"
