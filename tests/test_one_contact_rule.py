"""The contact rule is decided once, on the server.

THE ROUND TRIP THIS ENDS. `is_conversation` lived in Python AND, separately,
in JavaScript at client/index.html. The JS copy tested the thread list and
`is_internal` and nothing else — no actor check, no `promoted_from_internal`,
no `guest_took_part`. So:

  * the Slack composer got every fix, because it imports the Python
  * `trace_contacts.py` reported one contact, because it drives the Python
  * the CARD kept showing an agent's internal NAR note as "contact 01",
    because it rendered from the JS

which is how the same bug was reported fixed three times and observed unfixed
three times. The two implementations were never going to converge; one of them
had to stop existing.

`_marked_frames` stamps `is_contact` on every frame from the same
`split_contact_frames` Slack uses, and the page reads that field.
"""
import pytest

from server.api import _marked_frames


def _f(**kw):
    base = {"thread": "email", "actor": "co", "ticket_id": "33978941",
            "time": "02 Aug 15:28", "guestSaid": "", "weDid": "did a thing",
            "is_internal": False, "internal_reason": ""}
    base.update(kw)
    return base


GUEST = _f(thread="chat", actor="guest", ticket_id="34335318",
           guestSaid="Asked to revert to 08:30", weDid="")
NAR = _f(thread="web", weDid="Agent marked NAR", promoted_from_internal=True)
ORM = _f(thread="web", time="03 Aug 12:45", weDid="ORM escalation; 25% credit")
SYS = _f(thread="api", actor="system", weDid="confirmation email")


def _by_label(rows):
    return {r.get("weDid") or r.get("guestSaid"): r["is_contact"] for r in rows}


def test_the_guest_chat_is_marked_a_contact():
    got = _by_label(_marked_frames([SYS, NAR, ORM, GUEST]))
    assert got["Asked to revert to 08:30"] is True, got


def test_the_agent_only_ticket_is_not():
    """THE ROW FROM THE CARD. Neither frame has a guest in it, so the whole
    ticket is excluded — and the ORM row carries no promotion marker, so this
    holds on the group rule alone."""
    got = _by_label(_marked_frames([SYS, NAR, ORM, GUEST]))
    assert got["Agent marked NAR"] is False, got
    assert got["ORM escalation; 25% credit"] is False, got


def test_machinery_is_not_a_contact():
    got = _by_label(_marked_frames([SYS, GUEST]))
    assert got["confirmation email"] is False, got


def test_every_frame_still_ships():
    """The panel needs the excluded ones to say how many moved and where they
    went. A filtered list and a guest who never wrote in must not read the
    same — which is why the split returns two lists rather than one."""
    rows = _marked_frames([SYS, NAR, ORM, GUEST])
    assert len(rows) == 4, rows
    assert all("is_contact" in r for r in rows)


def test_the_verdict_matches_the_one_slack_composes_from():
    """The point of the whole change. If these two ever disagree the card and
    the post are describing different cases again."""
    from server.services.zendesk import split_contact_frames
    frames = [SYS, NAR, ORM, GUEST]
    convo, _ = split_contact_frames(frames)
    marked = {r["weDid"] or r["guestSaid"] for r in _marked_frames(frames)
              if r["is_contact"]}
    assert marked == {f.get("weDid") or f.get("guestSaid") for f in convo}


def test_a_non_dict_frame_does_not_break_the_marking():
    rows = _marked_frames([GUEST, "junk", None])
    assert len(rows) == 1 and rows[0]["is_contact"] is True


def test_no_frames_is_an_empty_list_not_an_error():
    assert _marked_frames(None) == [] and _marked_frames([]) == []


# ── the page must not carry a second copy of the rule ──────────────────────
#
# NEGATIVE SOURCE ASSERTIONS, which per the working rules are the one kind
# that holds: unreachability cannot make a string appear nowhere. Client-side
# JavaScript has no test harness here, so this is the only way to pin it.

CLIENT = "client/index.html"


def test_the_page_reads_the_servers_verdict():
    src = open(CLIENT, encoding="utf-8").read()
    assert "fr.is_contact" in src, \
        "the page is not reading the field the server stamps"


def _client_code() -> str:
    """index.html with its comments stripped.

    The first version of the check below matched the COMMENT that explains why
    the rule moved to the server — so documenting the fix failed the test that
    guards it. The rule is about code, not prose."""
    import re
    src = open(CLIENT, encoding="utf-8").read()
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)      # HTML comments
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)       # /* block */
    src = re.sub(r"(?m)^\s*//.*$", "", src)               # whole-line //
    return src


@pytest.mark.parametrize("rule", ["promoted_from_internal", "guest_took_part",
                                  "_PERSON_ACTORS"])
def test_the_page_does_not_reimplement_the_rules_behind_it(rule):
    """Each of these is a server-side rule the JS copy was missing. If one
    turns up in the page, the second implementation is growing back."""
    assert rule not in _client_code(), \
        f"{rule} is being re-derived in the page — decide it on the server"


def test_the_comment_stripper_leaves_the_code_it_is_checking():
    """If it stripped too much, every assertion above would pass vacuously."""
    code = _client_code()
    assert "fr.is_contact" in code, "the stripper ate the line under test"
    assert "second implementation" not in code, "comments were not stripped"


def test_the_projection_ships_the_verdict(live_db):
    """SURVIVED A MUTATION. Every test above drives `_marked_frames` directly,
    so returning the raw column from `_draft_dict` — which is what the page
    actually receives — passed all of them. The function being right is not
    the same as the endpoint sending it."""
    from server.api import _draft_dict
    s = live_db.SessionLocal()
    s.add(live_db.Review(id="tp_mk", rating=1, author="A", body_original="b",
                         status="draft"))
    s.add(live_db.RcaDraft(id="d_tp_mk", review_id="tp_mk",
                           support_interaction_frames=[SYS, NAR, ORM, GUEST]))
    s.commit()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_mk").first()
    frames = _draft_dict(d)["support_interaction_frames"]
    s.close()
    assert len(frames) == 4, frames
    assert all("is_contact" in f for f in frames), \
        "the endpoint shipped frames with no verdict — the page re-derives"
    got = {(f.get("weDid") or f.get("guestSaid")): f["is_contact"] for f in frames}
    assert got["Asked to revert to 08:30"] is True, got
    assert got["Agent marked NAR"] is False, got
