"""Every bucket needs a way to reach Sent, and only one of them had it.

"check how will the reviews be sent to sent tab, like untraceable ones and the
rest, there is no way to do that now."

Two things blocked it. POST /send needs a review AND a draft, and 404s
otherwise — so a review whose run never wrote a draft could not be finished at
all. And the Send button lives in the RCA column header, which is REPLACED by
the candidate picker for a review in candidate state and by the ask-the-guest
panel for an untraceable one: the endpoint existed, the button did not.

So Close out is its own action. Sending means the RCA and the reply have gone;
closing out means there was nothing to send. Overloading one verb with both is
how the Sent tab stops meaning anything — and it would also have posted an
empty RCA shell into a channel leadership reads, which looks like an analysis
that came back blank rather than one that was never written.
"""
import tempfile
from datetime import datetime

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    monkeypatch.setenv("MOCK_MODE", "true")
    import importlib
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    import server.api as api
    importlib.reload(api)
    import server.main as main
    importlib.reload(main)

    posts = []

    async def _fake_post(channel, ts, text, as_user=False):
        posts.append({"channel": channel, "ts": ts, "text": text})
        return "1717.0001"

    monkeypatch.setattr(api, "post_to_thread", _fake_post)

    s = db.SessionLocal()
    s.query(db.RcaDraft).delete()
    s.query(db.Review).delete()
    s.commit()
    s.close()
    yield {"db": db, "api": api, "client": TestClient(main.app), "posts": posts}


def _review(db, rid, **kw):
    s = db.SessionLocal()
    kw.setdefault("slack_channel", "C1")
    s.add(db.Review(id=rid, slack_ts=rid, rating=1,
                    author="A", body_original="x", status="draft",
                    received_at=datetime.utcnow(), **kw))
    s.commit()
    s.close()


def _draft(db, rid, **kw):
    s = db.SessionLocal()
    s.add(db.RcaDraft(id=f"d_{rid}", review_id=rid, **kw))
    s.commit()
    s.close()


def _bucket(client, rid):
    rows = client.get("/api/reviews").json()
    return next(r["bucket"] for r in rows if r["id"] == rid)


# ── every bucket has a route ───────────────────────────────────────────────

def test_an_untraceable_review_can_be_closed(env):
    db, c = env["db"], env["client"]
    _review(db, "tp_u")
    _draft(db, "tp_u", booking={}, candidates_list=[])
    assert _bucket(c, "tp_u") == "untraceable"

    res = c.post("/api/reviews/tp_u/close", json={})
    assert res.status_code == 200, res.text
    assert res.json()["closed_from"] == "untraceable"
    assert _bucket(c, "tp_u") == "sent"


def test_a_candidate_review_can_be_closed(env):
    """The one the picker hid. Send ↑ is not on screen for this review at
    all, so before Close out it could not be finished without confirming a
    booking that may not be this guest's."""
    db, c = env["db"], env["client"]
    _review(db, "tp_c")
    _draft(db, "tp_c", booking={}, candidate_state=True,
           candidates_list=[{"id": "1"}])
    assert _bucket(c, "tp_c") == "candidates"

    res = c.post("/api/reviews/tp_c/close", json={})
    assert res.status_code == 200, res.text
    assert res.json()["closed_from"] == "candidates"
    assert _bucket(c, "tp_c") == "sent"


def test_a_review_with_no_draft_row_can_be_closed(env):
    """/send needs a draft and 404s without one, so the processing bucket —
    the one guaranteed not to have a draft — had no route at all."""
    db, c = env["db"], env["client"]
    _review(db, "tp_p")
    assert _bucket(c, "tp_p") == "processing"

    res = c.post("/api/reviews/tp_p/close", json={})
    assert res.status_code == 200, res.text
    assert res.json()["had_draft"] is False
    assert _bucket(c, "tp_p") == "sent"


def test_an_identified_review_can_still_be_closed(env):
    db, c = env["db"], env["client"]
    _review(db, "tp_i")
    _draft(db, "tp_i", booking={"id": "33118844"})
    assert _bucket(c, "tp_i") == "identified"
    assert c.post("/api/reviews/tp_i/close", json={}).json()["closed_from"] \
        == "identified"


# ── and closing posts nothing ──────────────────────────────────────────────

def test_closing_out_never_posts_to_slack(env):
    """The premise of the action is that there is nothing to post. A post here
    would put an empty RCA into the team channel."""
    db, c = env["db"], env["client"]
    _review(db, "tp_u2")
    _draft(db, "tp_u2", booking={})
    c.post("/api/reviews/tp_u2/close", json={})
    assert env["posts"] == [], f"close posted to Slack: {env['posts']}"


def test_the_reason_is_recorded_and_readable(env):
    db, c = env["db"], env["client"]
    _review(db, "tp_u3")
    _draft(db, "tp_u3", booking={})
    c.post("/api/reviews/tp_u3/close",
           json={"reason": "Guest never answered the reference request."})

    row = next(r for r in c.get("/api/reviews").json() if r["id"] == "tp_u3")
    assert row["close_reason"] == "Guest never answered the reference request."
    assert row["closed_at"], "a closed review with no timestamp cannot be told "\
                             "from one that was replied to"


def test_a_missing_reason_gets_the_one_for_that_bucket(env):
    """A blank reason on a Sent card cannot be told from a reason nobody was
    ever asked for."""
    db, c = env["db"], env["client"]
    _review(db, "tp_u4")
    _draft(db, "tp_u4", booking={})
    out = c.post("/api/reviews/tp_u4/close", json={}).json()
    assert "Untraceable" in out["reason"]
    assert "no reply to post" in out["reason"]


def test_the_close_is_written_onto_the_trail(env):
    """The trail is where a reader goes to find out what happened to a review.
    A review in Sent with no RCA and no explanation is the same ambiguity in a
    different hat."""
    db, c = env["db"], env["client"]
    _review(db, "tp_u5")
    _draft(db, "tp_u5", booking={}, confidence_trail=[])
    c.post("/api/reviews/tp_u5/close", json={})
    trail = c.get("/api/reviews/tp_u5").json()["draft"]["confidence_trail"]
    texts = " ".join(t["text"] for t in trail)
    assert "Closed out" in texts
    assert "Nothing was posted to Slack" in texts


def test_closing_a_review_that_does_not_exist_says_what_would_work(env):
    """The one genuine not-found. A missing DRAFT is not — it is the commonest
    state this endpoint serves."""
    res = env["client"].post("/api/reviews/tp_nope/close", json={})
    assert res.status_code == 404
    assert "/api/reviews" in res.json()["detail"], \
        "the error names no way forward"


# ── Send stops posting an RCA that does not exist ──────────────────────────

def test_send_does_not_post_when_there_is_no_rca(env):
    """Reaching Sent from a review with no analysis is now a supported route,
    so this path can be entered with nothing to say. An empty RCA shell in the
    team channel reads as an analysis that found nothing."""
    db, c = env["db"], env["client"]
    _review(db, "tp_s1")
    _draft(db, "tp_s1", booking={})
    out = c.post("/api/reviews/tp_s1/send").json()
    assert out["ok"] is True
    assert out["posted"] is False
    assert "no RCA" in out["why"]
    assert env["posts"] == [], f"an empty RCA was posted: {env['posts']}"


def test_send_still_posts_a_real_rca(env):
    """The guard must not disable the feature it guards."""
    db, c = env["db"], env["client"]
    _review(db, "tp_s2")
    _draft(db, "tp_s2", booking={"id": "1"}, l1="Operations Issue",
           l2="Ticket Issues",
           rca_v3={"stated_issue": "Tickets never arrived."})
    out = c.post("/api/reviews/tp_s2/send").json()
    assert out["posted"] is True, out
    assert len(env["posts"]) == 1


def test_send_reports_why_it_did_not_post(env):
    """{"ok": true, "ts": null} meant skipped, failed and never-attempted, and
    the caller had to guess which."""
    db, c = env["db"], env["client"]
    _review(db, "tp_s3", slack_channel="C_MANUAL")
    _draft(db, "tp_s3", booking={"id": "1"}, l1="Operations Issue")
    out = c.post("/api/reviews/tp_s3/send").json()
    assert out["posted"] is False
    assert "by hand" in out["why"]


def test_send_still_404s_without_a_review(env):
    assert env["client"].post("/api/reviews/tp_nope/send").status_code == 404


# ── the condition itself, driven ───────────────────────────────────────────

class _D:
    def __init__(self, **kw):
        for k in ("slack_thread_override", "rca_v3", "l1",
                  "what_went_wrong_bullets", "wwr_scenarios", "guest_issues"):
            setattr(self, k, kw.get(k))


def test_nothing_at_all_is_nothing_to_post(env):
    assert env["api"].has_rca_to_post(_D()) is False
    assert env["api"].has_rca_to_post(None) is False


def test_an_empty_rca_v3_is_nothing_to_post(env):
    """A draft row exists for every searched review, and rca_v3 defaults to
    {}. Truthiness on the column alone would call that an analysis."""
    assert env["api"].has_rca_to_post(_D(rca_v3={})) is False
    assert env["api"].has_rca_to_post(
        _D(rca_v3={"what_went_wrong": {"guest_issues": []}})) is False


@pytest.mark.parametrize("kw", [
    {"rca_v3": {"stated_issue": "Tickets never arrived."}},
    {"rca_v3": {"what_went_wrong": {"guest_issues": [{"issue": "x"}]}}},
    {"l1": "Operations Issue"},
    {"what_went_wrong_bullets": ["x"]},
    {"guest_issues": [{"issue": "x"}]},
])
def test_an_analysis_in_any_of_its_stores_is_something_to_post(env, kw):
    assert env["api"].has_rca_to_post(_D(**kw)) is True


def test_a_hand_written_thread_post_is_something_to_post(env):
    """Somebody typed it. That is the clearest possible statement that there
    is something to post, whatever the RCA columns hold."""
    assert env["api"].has_rca_to_post(_D(slack_thread_override="  hi  ")) is True
    assert env["api"].has_rca_to_post(_D(slack_thread_override="   ")) is False


def test_send_does_not_repost_an_rca_already_posted(env):
    """Send closes the review AND posts the RCA. "Post to thread" exists so the
    RCA can go to the team while the reply is still being edited — so using
    both, which is the documented workflow, put the same RCA in the thread
    twice.

    Driven, counting posts. This guarantee lived in
    tests/test_actions_do_not_repeat.py as `assert "not d.rca_posted_at" in
    body` and broke on a rewrite that changed nothing about the behaviour.
    """
    db, c = env["db"], env["client"]
    _review(db, "tp_s4")
    _draft(db, "tp_s4", booking={"id": "1"}, l1="Operations Issue",
           rca_posted_at=datetime.utcnow())
    out = c.post("/api/reviews/tp_s4/send").json()
    assert out["posted"] is False
    assert "already posted" in out["why"]
    assert env["posts"] == [], "the RCA went into the thread a second time"
