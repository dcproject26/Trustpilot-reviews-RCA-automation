"""The DSS lookup has to say which kind of nothing it found.

The card renders one sentence — "No DSS row was matched for this
classification." — and it was printed for four situations:

  * DSS is not configured on this server, so no sheet was opened;
  * the sheet was opened and returned no rows at all;
  * the lookup raised;
  * the tabs were read and genuinely nothing fits this case.

Only the last one is what that sentence says. In the other three the playbook
was unavailable, and the resolution underneath is being checked against
nothing while the card implies it was checked against everything.

Every other lookup in the pipeline already writes a line of this shape — the
Zendesk timeline, the tone reference, the classification, the stated issue.
The DSS was the one step that could fail in total silence, so this is the
missing member of that family rather than a new idea.

`dss_entry` is a pure function on purpose: the alternative is asserting that
`dss_entry(` appears in pipeline.py, which passes just as happily against a
build where the call is unreachable.
"""
import pytest

from server.pipeline import dss_entry
from tests.conftest import drop_temp_db

MATCHED = {"action": "Refund where tickets were sent too late.",
           "dss_type": "delay_fulfilment", "match_score": 7}
NO_MATCH = {"match_score": 0, "dss_type": "delay_fulfilment",
            "type_reason": "routed on L2", "filters": {},
            "fallback": "No DSS available, Please check with your lead/escalation team."}
OUT_OF_SCOPE = {"match_score": 0, "dss_type": "", "out_of_scope": True,
                "type_reason": "L2 'Payment Issues' has no DSS tab",
                "fallback": "No DSS available."}


def test_a_matched_row_writes_nothing():
    """A working lookup is not news. Only the ways it can come up empty are."""
    assert dss_entry(MATCHED, None, True, "Operations Issue", "Ticket Issues") is None


def _text(e):
    assert e is not None, "the lookup came up empty and said nothing at all"
    return e["text"]


def test_a_disconnected_sheet_is_not_reported_as_no_row_matched():
    e = dss_entry({}, None, False, "Operations Issue", "Ticket Issues")
    t = _text(e)
    assert e["mark"] == "warn"
    assert "not connected" in t
    assert "never opened" in t or "was not read" in t


def test_a_lookup_that_raised_names_the_failure():
    e = dss_entry({}, RuntimeError("sheet 403"), True, "Ops", "Tickets")
    t = _text(e)
    assert e["mark"] == "warn"
    assert "failed" in t
    assert "not 'no row matched'" in t, \
        "a thrown lookup reads as a playbook that had nothing to say"


def test_an_empty_sheet_is_a_problem_with_the_sheet():
    """`live` and no rows at all: the tabs loaded nothing. That is a broken
    share or an empty export, not a review the playbook is silent about."""
    e = dss_entry({}, None, True, "Operations Issue", "Ticket Issues")
    t = _text(e)
    assert e["mark"] == "warn"
    assert "no rows at all" in t
    assert "sheet" in t


def test_a_real_miss_says_the_playbook_was_available():
    e = dss_entry(NO_MATCH, None, True, "Operations Issue", "Ticket Issues")
    t = _text(e)
    assert "no row matched" in t
    assert "available" in t, \
        "a genuine miss must not read like an unavailable playbook"
    assert "Operations Issue / Ticket Issues" in t


def test_out_of_scope_is_a_pass_not_a_fault():
    """"This L2 has no tab" is the sheet's scope, correctly reported. Marking
    it warn would make a healthy run look faulty, which is the inverse bug."""
    e = dss_entry(OUT_OF_SCOPE, None, True, "Payment Issue", "Payment Issues")
    assert e["mark"] == "pass"
    assert "no tab" in e["text"]
    assert "Payment Issues" in e["text"]


def test_the_four_empties_do_not_share_a_sentence():
    """The whole point. If any two of them produce the same words, the reader
    cannot tell an unavailable playbook from one that simply does not cover
    this case."""
    texts = [
        dss_entry({}, None, False, "A", "B")["text"],
        dss_entry({}, RuntimeError("x"), True, "A", "B")["text"],
        dss_entry({}, None, True, "A", "B")["text"],
        dss_entry(NO_MATCH, None, True, "A", "B")["text"],
    ]
    assert len(set(texts)) == 4, "two of the four empty states read the same"


@pytest.mark.parametrize("rec,err,live", [
    ({}, None, False),
    ({}, RuntimeError("x"), True),
    ({}, None, True),
    (NO_MATCH, None, True),
    (OUT_OF_SCOPE, None, True),
])
def test_the_pipeline_puts_the_line_on_the_trail(rec, err, live, monkeypatch):
    """Driven through process_review, because a pure function nothing calls is
    the exact failure this file is about."""
    import asyncio
    import importlib
    import json
    from datetime import datetime
    import tempfile
    import os

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()

    from tests.test_pipeline_validates_its_rca import _stub, BASE
    _stub(monkeypatch, json.loads(json.dumps(BASE)))
    import sys
    pipe = sys.modules["server.pipeline"]
    from server.services import dss as dss_svc

    async def _rec(*a, **k):
        if err is not None:
            raise err
        return rec
    monkeypatch.setattr(dss_svc, "get_recommendation", _rec)
    monkeypatch.setattr(pipe, "is_live",
                        lambda s: True if s == "dss" and live else False)

    s = db.SessionLocal()
    s.add(db.Review(id="tp_dss", slack_ts="tp_dss", slack_channel="C1", rating=1,
                    author="David Smith", body_original="late tickets",
                    body_english="late tickets", status="new",
                    received_at=datetime.utcnow()))
    s.commit()
    s.close()

    asyncio.run(pipe.process_review("tp_dss"))

    s = db.SessionLocal()
    d = s.query(db.RcaDraft).filter_by(review_id="tp_dss").first()
    trail = [e.get("text", "") for e in (d.confidence_trail or [])]
    s.close()
    drop_temp_db(tmp.name)

    expected = dss_entry(rec, err, live, "Operations Issue", "Ticket Issues")
    assert expected is not None
    assert any(expected["text"][:60] in t for t in trail), \
        f"the DSS line never reached the trail:\n" + "\n".join(trail)
