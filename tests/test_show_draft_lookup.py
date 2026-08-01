"""The checkpoint tool has to find the row you name.

`--bid 32908218` answered "no draft found" for a booking that was there: the
lookup keyed on `bookingId`, and nothing writes that — the warehouse writes
`id`. The answer was also the same sentence a genuinely missing draft gets, so
there was nothing to tell a broken lookup from an absent row.

This is the tool the whole v4 checkpoint runs through, and it had no test.
"""
import importlib
import os
import subprocess
import sys
import tempfile
from datetime import datetime

import pytest

sys.path.insert(0, "tools")
import show_draft                                          # noqa: E402


class _D:
    def __init__(self, booking):
        self.booking = booking


# ── the key the warehouse actually writes ───────────────────────────────────

def test_the_booking_id_is_read_from_the_key_the_pipeline_writes():
    assert show_draft._bid(_D({"id": "32908218"})) == "32908218"


def test_older_spellings_still_resolve():
    """A stored booking is whatever the row happens to hold, not whatever the
    current code writes."""
    assert show_draft._bid(_D({"bookingId": "1"})) == "1"
    assert show_draft._bid(_D({"booking_id": "2"})) == "2"


def test_a_numeric_booking_id_is_not_lost_to_its_type():
    assert show_draft._bid(_D({"id": 32908218})) == "32908218"


def test_no_booking_is_the_empty_string_not_a_crash():
    assert show_draft._bid(_D(None)) == ""
    assert show_draft._bid(_D({})) == ""


# ── end to end, because the lookup lives in main() ──────────────────────────

@pytest.fixture()
def seeded(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    url = f"sqlite:///{tmp.name}"
    monkeypatch.setenv("DATABASE_URL", url)
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    s = db.SessionLocal()
    s.add(db.Review(id="tp_d", slack_ts="1", slack_channel="C1", rating=1,
                    author="David"))
    s.add(db.RcaDraft(id="d1", review_id="tp_d", booking={"id": "32908218"},
                      rca_prompt_version="rca_v4", generated_at=datetime(2026, 7, 31),
                      confidence_trail=[{"mark": "warn",
                                         "text": "<strong>RCA</strong> — a coercion fired"}],
                      rca_v3={"l1": "x", "l2": "y", "sub_themes": [], "stated_issue": "z",
                              "dss": {"prescribes": "refund", "ref": None},
                              "sp_interaction_notes": {"raised": "N/A", "records": []},
                              "suggested_response": "word " * 200,
                              "what_went_wrong": {"guest_issues": [
                                  {"issue": "the guest complained",
                                   "claim": "they said so", "root_cause": "we erred"},
                                  {"issue": "our own process finding",
                                   "root_cause": "policy gap"}]}}))
    # A review carrying the reference number whose draft never got a booking.
    s.add(db.Review(id="tp_o", slack_ts="2", slack_channel="C1", rating=1,
                    author="Other", reference_number="99999999"))
    s.add(db.RcaDraft(id="d2", review_id="tp_o", booking={}, rca_v3={}))
    s.commit()
    s.close()
    yield url
    os.unlink(tmp.name)


def _run(url, *args):
    env = dict(os.environ, DATABASE_URL=url)
    r = subprocess.run([sys.executable, "tools/show_draft.py", *args],
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def test_the_booking_the_checkpoint_runs_on_is_found(seeded):
    code, out = _run(seeded, "--bid", "32908218")
    assert code == 0, out
    assert "review   tp_d" in out
    assert "booking  32908218" in out, "the header printed a dash for a booking it has"


def test_a_missing_booking_says_what_it_looked_for(seeded):
    """"no draft found" was the same answer for an absent row and a broken
    lookup, which is how the broken one survived."""
    code, out = _run(seeded, "--bid", "11111111")
    assert code != 0
    assert "no draft has booking 11111111" in out
    assert "known booking ids: 32908218" in out


def test_a_reference_number_on_the_review_is_pointed_at(seeded):
    """Matching can leave the draft with no booking while the review still
    carries the number. Saying so beats making the reader guess."""
    code, out = _run(seeded, "--bid", "99999999")
    assert code != 0
    assert "--review tp_o" in out


def test_the_version_stamp_is_on_the_first_screen(seeded):
    _, out = _run(seeded, "--bid", "32908218")
    assert "by rca_v4" in out


def test_the_stamp_is_content_addressed():
    """Two prompt bodies must not share a stamp — that is what made "did the
    new clause run?" unanswerable."""
    from server.prompts import RCA_PROMPT_VERSION, RCA_PROMPT_FAMILY, _prompt_digest
    assert RCA_PROMPT_VERSION.startswith(RCA_PROMPT_FAMILY + "+")
    assert _prompt_digest("a") != _prompt_digest("b")
    assert len(RCA_PROMPT_VERSION.split("+")[1]) == 8


def test_a_stamped_v4_row_gets_no_legacy_banner(seeded):
    _, out = _run(seeded, "--bid", "32908218")
    assert "THIS ROW IS THE OLD v3 SHAPE" not in out


# ── the two things the audit has to point at ────────────────────────────────

def test_a_claim_less_issue_is_questioned(seeded):
    """"Out-of-policy refund granted…" arrived as guest issue 04 with no claim.
    The guest never raised it. Unflagged, it reads as something they said."""
    _, out = _run(seeded, "--bid", "32908218")
    assert "no claim — is this a guest issue" in out
    assert out.count("no claim") == 1, "the issue WITH a claim was flagged too"


def test_detail_prints_what_a_reviewer_asks_for(seeded):
    """The follow-up questions were all "send me the JSON for X". One flag."""
    code, out = _run(seeded, "--bid", "32908218", "--detail")
    assert code == 0, out
    for section in ("guest issue 1", "guest issue 2", "dss",
                    "sp_interaction_notes", "confidence trail"):
        assert f"── {section} ──" in out, f"--detail omits {section}"
    assert "a coercion fired" in out, "the trail is not printed"
    assert '"root_cause": "we erred"' in out, "the issue JSON is not printed"


def test_detail_measures_the_two_fields_with_ceilings(seeded):
    """198 words got past review once because nothing counted."""
    _, out = _run(seeded, "--bid", "32908218", "--detail")
    assert "/ 120 words   suggested_response" in out
    assert "200" in out, "a 200-word reply is not reported over its ceiling"


def test_a_single_issue_can_be_asked_for(seeded):
    _, out = _run(seeded, "--bid", "32908218", "--issue", "2")
    assert "── guest issue 2 ──" in out
    assert "── guest issue 1 ──" not in out


def test_the_short_form_says_detail_exists(seeded):
    _, out = _run(seeded, "--bid", "32908218")
    assert "--detail prints these in full" in out
    assert "── dss ──" not in out


def test_a_row_from_an_older_prompt_body_is_called_out(seeded):
    """"rca_v4" was the same stamp before and after a prompt change that added
    rules, so a finding could not be told from a row that predates the clause.
    The stamp is content-addressed now."""
    import server.db as db
    s = db.SessionLocal()
    d = s.query(db.RcaDraft).filter(db.RcaDraft.id == "d1").first()
    d.rca_prompt_version = "rca_v4+deadbeef"
    s.commit(); s.close()
    _, out = _run(seeded, "--bid", "32908218")
    assert "the prompt has changed since this row was written" in out
    assert "THIS ROW IS THE OLD v3 SHAPE" not in out, \
        "an older v4 body is not the v3 shape; the two must not read the same"


def test_a_current_row_is_not_called_out(seeded):
    import server.db as db
    from server.prompts import RCA_PROMPT_VERSION
    s = db.SessionLocal()
    d = s.query(db.RcaDraft).filter(db.RcaDraft.id == "d1").first()
    d.rca_prompt_version = RCA_PROMPT_VERSION
    s.commit(); s.close()
    _, out = _run(seeded, "--bid", "32908218")
    assert "the prompt has changed" not in out


def test_the_trail_is_readable_in_a_terminal(seeded):
    """The trail text is HTML-escaped for the dashboard. Printed raw it read
    "claim_accuracy &#x27;Unknown&#x27;"."""
    import server.db as db
    s = db.SessionLocal()
    d = s.query(db.RcaDraft).filter(db.RcaDraft.id == "d1").first()
    d.confidence_trail = [{"mark": "warn",
                           "text": "<strong>RCA</strong> — claim_accuracy &#x27;X&#x27; → Unknown"}]
    s.commit(); s.close()
    _, out = _run(seeded, "--bid", "32908218", "--detail")
    assert "claim_accuracy 'X' → Unknown" in out
    assert "&#x27;" not in out


# ── which build is a host running ───────────────────────────────────────────
#
# `curl -s` on a dead port prints nothing, and "nothing" parses as a broken
# endpoint rather than as a refused connection. Same class as every other bug
# this month: two very different failures producing one answer.

def test_a_refused_connection_says_refused_not_nothing():
    sys.path.insert(0, "tools")
    import which_build
    v, err = which_build.ask("http://127.0.0.1:9")   # discard port, always dead
    assert v is None
    assert "cannot reach it" in err, err


def test_a_host_that_answers_with_html_is_not_read_as_a_build():
    sys.path.insert(0, "tools")
    import which_build
    import json as _json
    import urllib.request

    class _Fake:
        def read(self): return b"<!doctype html><html></html>"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    real = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: _Fake()
    try:
        v, err = which_build.ask("http://example.invalid")
    finally:
        urllib.request.urlopen = real
    assert v is None and "not with JSON" in err, (v, err)


def test_it_reports_the_working_trees_own_head():
    sys.path.insert(0, "tools")
    import which_build
    head = which_build.local_head()
    assert head and head != "unknown", "cannot tell which commit this tree is on"
