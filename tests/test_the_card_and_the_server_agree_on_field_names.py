"""What the dashboard sends is what the server stores — checked, not assumed.

THE CANONICAL BUG OF THIS PROJECT, in its purest form. `show_draft --bid` keyed
on `bookingId` while the warehouse writes `id`, and it answered "no draft found"
— the same sentence a genuinely absent row gets. A field-name mismatch between
the two halves of a request is invisible from either side: the client sends its
key, Pydantic drops what it does not declare, the endpoint returns 200, and the
card paints a ✓ saved tick over an edit that reached nothing.

Nothing checked the two lists against each other, so this does. It reads the
keys out of the client's own `saveDraft` call sites — that half is
CLIENT-SIDE JAVASCRIPT, which has no test harness here, so it is parsed rather
than executed — and then drives the REAL Pydantic models with them. The
assertion is about model behaviour, not about text appearing in a file.
"""
import re

import pytest
from fastapi.testclient import TestClient

from server.api import DraftPatchV2, ManualReview

HTML = open("client/index.html", encoding="utf-8").read()


def _keys_passed_to(fn: str, drop_first_arg: bool) -> dict:
    """Every object-literal key the card hands to `fn`, with line numbers."""
    out = {}
    for m in re.finditer(re.escape(fn) + r"\(", HTML):
        i, depth, j = m.end(), 1, m.end()
        while j < len(HTML) and depth:
            depth += (HTML[j] == "(") - (HTML[j] == ")")
            j += 1
        arg = HTML[i:j - 1]
        if drop_first_arg:
            arg = arg[arg.find(",") + 1:]
        for k in re.findall(r"[{,]\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", arg):
            out.setdefault(k, []).append(HTML[:i].count("\n") + 1)
    return out


SENT = _keys_passed_to("saveDraft", drop_first_arg=True)

# DERIVED, NEVER AUTHORED — so being dropped is correct, not a loss.
# `settle_scenarios()` computes `overlay_scenarios` from `scenarios`, which IS
# accepted, because three columns describing one ordered list is what put a
# chip on the card three times with a delete button that did nothing. The card
# still sends it; the server recomputes it either way.
DERIVED_SERVER_SIDE = {"overlay_scenarios"}


def test_the_call_sites_were_found_at_all():
    """A parser that silently matched nothing would make every assertion below
    vacuous — the "I ran and found nothing" failure, aimed at this file."""
    assert len(SENT) >= 10, SENT


@pytest.mark.parametrize("key", sorted(SENT))
def test_every_key_the_card_saves_is_one_the_server_accepts(key):
    if key in DERIVED_SERVER_SIDE:
        pytest.skip(f"{key} is derived server-side by settle_scenarios()")
    assert key in DraftPatchV2.model_fields, (
        f"the card sends {key!r} at line(s) {SENT[key]} and DraftPatchV2 does "
        f"not declare it, so the request returns 200 and stores nothing")


@pytest.mark.parametrize("key", sorted(DERIVED_SERVER_SIDE))
def test_a_derived_key_is_still_genuinely_derived(key):
    """If one of these is ever declared on the model it stops being derived,
    and the exemption above turns into a hole. Fails loudly instead."""
    assert key not in DraftPatchV2.model_fields, (
        f"{key} is now accepted by the model, so it is no longer derived — "
        f"remove it from DERIVED_SERVER_SIDE and let the parity test cover it")


def test_a_key_the_model_does_not_declare_is_silently_dropped():
    """The mechanism itself, stated once. This is WHY the parity test exists —
    Pydantic does not raise, so nothing downstream can tell."""
    p = DraftPatchV2.model_validate({"scenarios": ["kept"],
                                     "not_a_real_field": ["lost"]})
    assert getattr(p, "not_a_real_field", None) is None
    assert p.scenarios == ["kept"]


# ── the manual review form ─────────────────────────────────────────────────

def _manual_form_keys() -> set:
    """The keys the Add-manual-review form posts.

    SHORTHAND COUNTS. The form sends `{body, author, rating: n, ...}` — ES6
    shorthand for the first two — and a `key:` pattern misses exactly those,
    which is how the first version of this test "found" that two fields had
    moved when nothing had. A parser that reads half the object is worse than
    no parser: it reports a bug in the code under test.
    """
    i = HTML.index("fetch('/api/reviews/manual'")
    blk = HTML[i:i + 1200]
    body = blk[blk.index("JSON.stringify({") + len("JSON.stringify({"):]
    body = body[:body.index("})")]
    # Split on the object's own commas, then take each element's KEY — the
    # part before `:`, or the whole token when it is shorthand. Matching
    # `name:` or `name,` anywhere picks up VALUES too (`rating: selectedRating`
    # yields both), which is the second way this parser got it wrong.
    keys = set()
    for part in body.split(","):
        head = part.split(":")[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", head):
            keys.add(head)
    return keys


def test_every_key_the_manual_form_sends_is_accepted():
    """`POST /api/reviews/manual` had NO test at all — the one button on the
    card that creates a review from scratch."""
    sent = _manual_form_keys()
    form = {"body", "author", "rating", "reference_number"}
    assert form <= sent, f"the manual form's keys moved: {sorted(sent)}"
    for k in sent:
        assert k in ManualReview.model_fields, (
            f"the form posts {k!r} and ManualReview does not declare it")


@pytest.fixture()
def client(live_db, monkeypatch):
    from server.db import get_session
    from server.main import app
    import server.pipeline as P
    # The endpoint queues the real batch runner as a background task, which
    # TestClient executes for real. Stubbed so this tests the WRITE, not the
    # pipeline.
    monkeypatch.setattr(P, "run_batch_sync", lambda *a, **k: None)
    app.dependency_overrides[get_session] = lambda: live_db.SessionLocal()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_a_manually_added_review_is_actually_stored(live_db, client):
    r = client.post("/api/reviews/manual", json={
        "body": "The guide never turned up.", "author": "Ioan Popescu",
        "rating": 1, "reference_number": "32728059"})
    assert r.status_code == 200, r.text
    rid = r.json()["review_id"]

    got = client.get(f"/api/reviews/{rid}")
    assert got.status_code == 200, got.text
    rev = got.json()["review"]
    assert rev["body_original"] == "The guide never turned up."
    assert rev["author"] == "Ioan Popescu"
    assert rev["rating"] == 1
    assert rev["reference_number"] == "32728059"
    assert rev["status"] == "new"
    assert rev["received_at"], "a manual review with no arrival time cannot sort"


def test_adding_the_same_review_twice_does_not_make_two(live_db, client):
    """The card can double-submit on a slow network, and the retry in
    `saveDraft` makes that likelier, not rarer."""
    body = {"body": "Same text", "rating": 1, "slack_ts": "1720000000.1"}
    first = client.post("/api/reviews/manual", json=body).json()
    second = client.post("/api/reviews/manual", json=body).json()
    assert first["review_id"] == second["review_id"]
    assert second.get("duplicate") is True, second
