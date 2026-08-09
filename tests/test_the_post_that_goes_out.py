"""What actually reaches the Slack thread.

TWO THINGS BIT THE SAME CARD.

The Customer / CE section carried eighteen rows — booking creation, payment
reminders, automation alerts, system pings — under a heading that says
interactions. The composer was fixed for that; this pins it, because the
fix lives in `split_contact_frames` and a composer that stopped calling it
would look exactly the same in a diff.

And the post that was being read was not the composer's at all.
`slack_thread_override` wins everywhere the post is used — the preview, the
post-to-thread call, the send — which is right, because a person's edit must
survive a re-render. But nothing recorded WHEN it was written, so a single
edit made every later fix invisible in the only text that goes out: a card
showing corrected contacts and a thread carrying the old ones, with no way to
tell from either.
"""
from datetime import datetime, timedelta

import pytest


def _frames():
    return [
        dict(thread="booking", actor="creation", ticket_id="",
             time="21 Jul 15:28", guestSaid="", weDid="",
             summary="Booking created"),
        dict(thread="api", actor="system", ticket_id="33978941",
             time="21 Jul 15:29", guestSaid="",
             weDid="System sent booking confirmation email"),
        dict(thread="api", actor="system", ticket_id="33978941",
             time="31 Jul 12:01", guestSaid="",
             weDid="System sent payment reminder email"),
        dict(thread="web", actor="co", ticket_id="33978941",
             time="02 Aug 15:28", guestSaid="", weDid="Agent marked NAR",
             promoted_from_internal=True),
        dict(thread="chat", actor="guest", ticket_id="34335318",
             time="02 Aug 15:36",
             guestSaid="Asked to revert to 08:30 or any pre-11:00 slot",
             weDid="Window closed; vendor number given"),
        dict(thread="web", actor="co", ticket_id="33978941",
             time="03 Aug 12:45", guestSaid="",
             weDid="ORM escalation; 25% credit"),
        dict(thread="review", actor="review", ticket_id="", time="02 Aug 17:36",
             guestSaid="", weDid="", summary="Review posted"),
    ]


def _seed(db, rid="tp_post", **cols):
    s = db.SessionLocal()
    s.add(db.Review(id=rid, rating=1, author="Roisin", body_original="b",
                    status="draft"))
    s.add(db.RcaDraft(id=f"d_{rid}", review_id=rid, booking={"id": "32885089"},
                      support_interaction_frames=_frames(),
                      rca_v3={"what_went_wrong": {"guest_issues": [],
                                                  "fixes": [], "gaps": []},
                              "support_interaction_notes": [
                                  {"zd_ref": "ZD-34335318",
                                   "time": "02 Aug 15:36", "channel": "chat",
                                   "summary": "Guest asked to revert to 08:30"}],
                              "flags": []},
                      **cols))
    s.commit(); s.close()
    return rid


def _section(db, rid):
    from server.services.slack import format_rca_slack
    s = db.SessionLocal()
    r = s.query(db.Review).filter_by(id=rid).first()
    d = s.query(db.RcaDraft).filter_by(review_id=rid).first()
    txt = format_rca_slack(r, d)
    s.close()
    i = txt.index("Customer / CE interactions")
    return txt[i:i + 900]


# ── the section carries conversations and nothing else ─────────────────────

def test_only_the_guest_contact_is_in_the_section(live_db):
    body = _section(live_db, _seed(live_db))
    assert "01." in body and "ZD-34335318" in body, body
    assert "02." not in body, f"a second contact was composed: {body}"


@pytest.mark.parametrize("noise", [
    "Booking created", "booking confirmation email", "payment reminder email",
    "Review posted", "Agent marked NAR", "ORM escalation",
])
def test_no_machinery_or_agent_only_row_reaches_the_post(live_db, noise):
    """Every one of these was a bullet in the section on a real card."""
    assert noise not in _section(live_db, _seed(live_db)), noise


def test_what_was_left_out_is_named_and_not_called_machinery(live_db):
    """An agent note is our side of the record, not a system event, and a
    reader told "6 system events moved" about their own agents' notes has been
    given the wrong fact."""
    body = _section(live_db, _seed(live_db))
    assert "4 system events moved to the timeline" in body, body
    assert "2 agent-side notes with no guest message" in body, body


# ── the hand-written post says when it predates the analysis ───────────────

def _stale(db, rid):
    from server.api import _override_is_stale
    s = db.SessionLocal()
    d = s.query(db.RcaDraft).filter_by(review_id=rid).first()
    got = _override_is_stale(d)
    s.close()
    return got


def test_no_override_is_never_stale():
    from server.api import _override_is_stale

    class _D:
        slack_thread_override = ""
        slack_override_at = None
        generated_at = datetime.utcnow()
        rca_v3_edited_at = None
    assert _override_is_stale(_D()) is False


def test_an_override_older_than_the_analysis_is_stale(live_db):
    now = datetime.utcnow()
    rid = _seed(live_db, rid="tp_old_ovr",
                slack_thread_override="hand written",
                slack_override_at=now - timedelta(hours=1),
                generated_at=now)
    assert _stale(live_db, rid) is True


def test_an_override_newer_than_the_analysis_is_not(live_db):
    """The inverse. Warning on a deliberate edit that is up to date is how a
    warning stops being read."""
    now = datetime.utcnow()
    rid = _seed(live_db, rid="tp_new_ovr",
                slack_thread_override="hand written",
                slack_override_at=now,
                generated_at=now - timedelta(hours=1))
    assert _stale(live_db, rid) is False


def test_an_rca_edit_counts_as_the_analysis_moving(live_db):
    """`generated_at` only moves on a regenerate. Editing a finding on the
    card moves `rca_v3_edited_at`, and the post is just as stale for it."""
    now = datetime.utcnow()
    rid = _seed(live_db, rid="tp_edit_ovr",
                slack_thread_override="hand written",
                slack_override_at=now - timedelta(hours=1),
                generated_at=now - timedelta(hours=2),
                rca_v3_edited_at=now)
    assert _stale(live_db, rid) is True


def test_an_override_with_no_timestamp_is_reported_stale(live_db):
    """Written before the column existed. "We cannot tell" and "it is current"
    must not read the same, and the cost of the wrong guess here is one press
    of a button."""
    rid = _seed(live_db, rid="tp_nostamp_ovr",
                slack_thread_override="hand written",
                generated_at=datetime.utcnow())
    assert _stale(live_db, rid) is True


# ── the stamp is written, and cleared, by the endpoint ─────────────────────

@pytest.fixture()
def client(live_db):
    from fastapi.testclient import TestClient
    from server.main import app
    from server.db import get_session
    app.dependency_overrides[get_session] = lambda: live_db.SessionLocal()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _at(db, rid):
    s = db.SessionLocal()
    d = s.query(db.RcaDraft).filter_by(review_id=rid).first()
    got = d.slack_override_at
    s.close()
    return got


def test_saving_an_override_stamps_the_time(live_db, client):
    rid = _seed(live_db, rid="tp_stamp")
    r = client.patch(f"/api/reviews/{rid}/draft-v2",
                     json={"slack_thread_override": "hand written"})
    assert r.status_code == 200, r.text
    assert _at(live_db, rid) is not None, "no stamp — staleness is unknowable"


def test_clearing_it_clears_the_stamp(live_db, client):
    """Regenerate from RCA sends an empty override. A stamp left behind would
    report a hand edit nobody made."""
    rid = _seed(live_db, rid="tp_unstamp")
    client.patch(f"/api/reviews/{rid}/draft-v2",
                 json={"slack_thread_override": "hand written"})
    client.patch(f"/api/reviews/{rid}/draft-v2",
                 json={"slack_thread_override": ""})
    assert _at(live_db, rid) is None, _at(live_db, rid)


def test_the_card_is_told(live_db, client):
    rid = _seed(live_db, rid="tp_told")
    client.patch(f"/api/reviews/{rid}/draft-v2",
                 json={"slack_thread_override": "hand written"})
    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id=rid).first()
    d.generated_at = datetime.utcnow() + timedelta(minutes=1)
    s.commit(); s.close()
    # THE PATCH RESPONSE IS THE PROJECTION. There is no GET /draft route — the
    # card reads the draft off the reviews payload and off this response, so
    # this is the shape the page actually receives.
    r = client.patch(f"/api/reviews/{rid}/draft-v2",
                     json={"resolution": "unrelated edit"})
    assert r.status_code == 200, r.text
    blob = r.json()["draft"]
    assert blob["slack_override_stale"] is True, blob.get("slack_override_stale")
    assert blob["slack_thread_override"] == "hand written", blob
