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


# ── the post names the booking it is about ─────────────────────────────────
#
# It named it nowhere. A reader in the thread got the analysis and had to open
# the dashboard to find out which booking, which experience, or which vendor
# it concerned — on a post whose subject is often the vendor's failure.

BOOKING = {"id": "32885089",
           "experienceName": "Auschwitz-Birkenau Guided Tour with Fast-Track "
                             "Tickets & Transfer Options",
           "tid_name": "English Guided Tour", "tgid": "15406", "tid": "19354",
           "vid": "4045", "vendorName": "Krakville",
           "fulfilmentType": "Vendor Api"}


def _post(db, rid="tp_bd", booking=BOOKING):
    from server.services.slack import format_rca_slack
    s = db.SessionLocal()
    s.add(db.Review(id=rid, rating=1, author="R", body_original="b",
                    status="draft"))
    s.add(db.RcaDraft(id=f"d_{rid}", review_id=rid, booking=booking,
                      rca_v3={"what_went_wrong": {"guest_issues": [],
                                                  "fixes": [], "gaps": []},
                              "flags": []}))
    s.commit()
    r = s.query(db.Review).filter_by(id=rid).first()
    d = s.query(db.RcaDraft).filter_by(review_id=rid).first()
    txt = format_rca_slack(r, d)
    s.close()
    return txt


def _details(txt):
    i = txt.index("Booking details")
    return txt[i:txt.index("____", i)]


@pytest.mark.parametrize("label,value", [
    ("Booking ID", "32885089"),
    ("Experience", "Auschwitz-Birkenau Guided Tour with Fast-Track"),
    ("TID name", "English Guided Tour"),
    ("TGID / TID", "15406 / 19354"),
    ("Vendor ID", "4045"),
    ("Vendor name", "Krakville"),
    ("Fulfilment type", "Vendor Api"),
])
def test_each_requested_field_is_in_the_post(live_db, label, value):
    body = _details(_post(live_db, rid=f"tp_bd_{label.replace(' ', '_').replace('/', '')}"))
    assert f"• {label}: " in body, body
    assert value in body, body


def test_the_section_comes_first(live_db):
    """A reader should know which booking before reading what went wrong."""
    txt = _post(live_db, rid="tp_bd_order")
    assert txt.index("Booking details") < txt.index("Customer / CE"), \
        "the booking is introduced after the analysis about it"


def test_a_field_the_warehouse_did_not_return_is_named_not_skipped(live_db):
    """Dropping the row makes a missing vendor id and a booking that never had
    one read the same — and on a post about a vendor's failure that is the
    field most worth knowing is absent."""
    body = _details(_post(live_db, rid="tp_bd_gap",
                          booking={"id": "32885089", "vendorName": "Krakville"}))
    assert "• Vendor ID: — not recorded" in body, body
    assert "• Vendor name: Krakville" in body, body


def test_tgid_and_tid_stay_on_one_row(live_db):
    """They are read together — a TGID with no TID is a product with no ticket
    type — so splitting them loses the pairing."""
    body = _details(_post(live_db, rid="tp_bd_pair",
                          booking={"id": "1", "tgid": "15406"}))
    assert "• TGID / TID: 15406 / —" in body, body


def test_the_alternate_spellings_are_read(live_db):
    """The warehouse and the BigQuery enrichment spell these differently and
    the client already reads both. Reading one would blank the field on half
    the drafts."""
    body = _details(_post(live_db, rid="tp_bd_alt",
                          booking={"id": "1", "vendor_name": "Krakville",
                                   "fulfilment_type": "Vendor Api",
                                   "experience_name": "A tour"}))
    assert "• Vendor name: Krakville" in body, body
    assert "• Fulfilment type: Vendor Api" in body, body
    assert "• Experience: A tour" in body, body


def test_no_booking_means_no_section(live_db):
    """A wall of seven dashes would say "no booking matched" a second time,
    louder, in a post that already says it."""
    txt = _post(live_db, rid="tp_bd_none", booking={})
    assert "Booking details" not in txt, txt[:400]


def test_the_card_gets_the_same_block_the_post_carries(live_db):
    """THE CARD COMPOSES THE PREVIEW IN JAVASCRIPT while format_rca_slack
    composes the posted text in Python — two composers for one post, which is
    how "Fix: [object Object]" once reached a real thread from the client half
    while the server's copy was correct.

    The section LIST is still duplicated. Its CONTENT is not: the card renders
    this string verbatim, exactly as it already does for What went wrong."""
    from server.api import _draft_dict
    s = live_db.SessionLocal()
    s.add(live_db.Review(id="tp_bd_tog", rating=1, author="R",
                         body_original="b", status="draft"))
    s.add(live_db.RcaDraft(id="d_tp_bd_tog", review_id="tp_bd_tog",
                           booking=BOOKING,
                           rca_v3={"what_went_wrong": {"guest_issues": [],
                                                       "fixes": [], "gaps": []},
                                   "flags": []}))
    s.commit()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_bd_tog").first()
    served = _draft_dict(d)["booking_details_text"]
    s.close()
    assert "• Booking ID: 32885089" in served, served
    assert "• Vendor name: Krakville" in served, served


def test_the_page_renders_that_block_rather_than_rebuilding_it():
    """NEGATIVE, on the client, which has no harness here. If the page starts
    reading booking fields to compose this itself, the two composers are back."""
    src = open("client/index.html", encoding="utf-8").read()
    assert "rca.bookingDetailsText" in src, \
        "the page is not rendering the server's block"
    i = src.index("['booking',   'Booking details'")
    assert "fulfilmentType" not in src[i:i + 400], \
        "the page is composing the booking block itself"
