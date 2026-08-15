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
from tests.conftest import drop_temp_db


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
    drop_temp_db(tmp.name)


def _run(url, *args):
    env = dict(os.environ, DATABASE_URL=url)
    # show_draft.py emits utf-8 (box rules, em dashes). `text=True` alone decodes
    # with the parent's locale encoding — cp1252 on Windows — turning "──" into
    # "â”€â”€" and every trail assertion into a false failure. Decode utf-8 to
    # match what the tool emits, on either platform.
    r = subprocess.run([sys.executable, "tools/show_draft.py", *args],
                       capture_output=True, text=True, encoding="utf-8", env=env)
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


def test_the_tool_survives_a_cp1252_stdout(seeded):
    """The audit prints box rules and em dashes. On a Windows console — or any
    pipe whose encoding is cp1252 — those characters are unencodable, and the
    first such print raises UnicodeEncodeError and kills the tool mid-audit,
    showing nothing. Force the child's stdout to cp1252 (the Windows default the
    reader hits) and it must STILL finish and STILL print the rule, because
    main() reconfigures stdout to utf-8. Without that line this test sees the
    crash the reconfigure exists to prevent."""
    env = dict(os.environ, DATABASE_URL=seeded, PYTHONIOENCODING="cp1252")
    r = subprocess.run(
        [sys.executable, "tools/show_draft.py", "--bid", "32908218", "--detail"],
        capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr[-800:]
    assert "── dss ──" in r.stdout


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


def _fake_host(routes):
    """urlopen replacement. routes maps a path suffix to (status, ctype, body)."""
    import urllib.request

    class _Resp:
        def __init__(self, status, ctype, body):
            self.status = status
            self.headers = {"Content-Type": ctype}
            self._b = body.encode()
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _open(url, *a, **k):
        for suffix, spec in routes.items():
            if str(url).endswith(suffix):
                return _Resp(*spec)
        raise AssertionError(f"unrouted {url}")

    real = urllib.request.urlopen
    urllib.request.urlopen = _open
    return real


def _restore(real):
    import urllib.request
    urllib.request.urlopen = real


def _ask(routes):
    sys.path.insert(0, "tools")
    import which_build
    real = _fake_host(routes)
    try:
        return which_build.ask("http://example.invalid")
    finally:
        _restore(real)


def test_a_host_that_answers_with_html_is_not_read_as_a_build():
    """A platform page while a deploy is mid-publish. Neither endpoint is ours."""
    v, err = _ask({"/api/version": (200, "text/html", "<!doctype html>Deploying"),
                   "/healthz":     (404, "text/html", "<html>404</html>")})
    assert v is None, v
    assert "not the app" in err, err


def test_an_older_build_of_our_own_app_is_not_called_a_different_service():
    """The distinction that matters when a deploy has not landed. /healthz has
    been in this app far longer than /api/version, so answering there and not
    here means our app, an old one - a publish that never happened, not a wrong
    host. "answered, but not with JSON" said neither, and that one sentence was
    what made 17 hours of a stuck deploy look like a broken endpoint."""
    v, err = _ask({"/api/version": (404, "text/plain", "Not Found"),
                   "/healthz":     (200, "application/json", '{"status":"ok"}')})
    assert v is None
    assert "OLDER build" in err, err
    assert "Nothing new has been published" in err, err


def test_a_host_running_nothing_of_ours_is_named_as_such():
    v, err = _ask({"/api/version": (404, "text/plain", "nope"),
                   "/healthz":     (404, "text/plain", "nope")})
    assert v is None
    assert "not running the app at all" in err, err


def test_a_current_build_answers_with_its_commit():
    v, err = _ask({"/api/version": (200, "application/json", '{"commit":"cfd4869"}')})
    assert err is None, err
    assert v["commit"] == "cfd4869"


def test_json_that_is_not_json_is_not_read_as_a_build():
    v, err = _ask({"/api/version": (200, "application/json", "{oh dear")})
    assert v is None
    assert "claimed JSON" in err, err


def test_it_reports_the_working_trees_own_head():
    sys.path.insert(0, "tools")
    import which_build
    head = which_build.local_head()
    assert head, "the head is the empty string — blank where a commit should be"
    # The mutation harness runs the suite in a copy of the tree with no .git,
    # which is a legitimate "unknown" rather than a broken lookup.
    if head != "unknown":
        assert len(head) >= 7, head


def test_a_tree_with_no_git_says_unknown_rather_than_nothing():
    """`git rev-parse` outside a repository exits 128 with empty stdout, which
    went straight to the caller and printed a blank where a commit belongs — a
    failed call wearing the same face as a short answer."""
    sys.path.insert(0, "tools")
    import which_build
    with tempfile.TemporaryDirectory() as d:
        cwd = os.getcwd()
        try:
            os.chdir(d)
            assert which_build.local_head() == "unknown"
        finally:
            os.chdir(cwd)


# ── the deployment verifier ─────────────────────────────────────────────────
#
# It exists to answer "are the fixes reflecting", and it had no test of its
# own — the same gap it was written to close. The database identity is the
# load-bearing line: Replit's workspace and its published deployment keep
# separate secret stores, so a database migration that updates one leaves the
# other on the old instance, silently, until somebody compares them. Hostnames
# cannot settle it (the same Postgres is proxied under different names); the
# cluster's system_identifier can.

def _db_lines(db):
    sys.path.insert(0, "tools")
    import verify_fixes
    return "\n".join(verify_fixes.db_lines(db))


def test_a_postgres_identity_is_printed_so_two_hosts_can_be_compared():
    out = _db_lines({"dialect": "postgresql", "identity": "7401992"})
    assert "7401992" in out
    assert "OTHER url" in out, "it does not say the line is for comparing"


def test_an_unreadable_identity_is_not_silence():
    """No identity on a Postgres host means the comparison cannot be made.
    Printing nothing would read as "checked, they match"."""
    out = _db_lines({"dialect": "postgresql"})
    assert "could not be read" in out
    assert "I cannot tell" in out


def test_sqlite_says_it_shares_nothing_rather_than_failing_to_answer():
    """A legitimate n/a, not a broken lookup — sqlite is a file in one
    container, so the question does not apply. Merging it with the unreadable
    case would make a healthy setup look faulty."""
    out = _db_lines({"dialect": "sqlite", "target": "/tmp/x.db"})
    assert "n/a" in out
    assert "could not be read" not in out


def test_the_three_database_answers_never_read_the_same():
    assert len({_db_lines({"dialect": "postgresql", "identity": "1"}),
                _db_lines({"dialect": "postgresql"}),
                _db_lines({"dialect": "sqlite"})}) == 3


def test_main_actually_prints_the_identity_line():
    """db_lines() can be perfect and never be called — deleting the call from
    main() left every test above green. This drives main() end to end against
    a stubbed host and reads what a person would actually see."""
    sys.path.insert(0, "tools")
    import io
    import json as _json
    import contextlib
    import verify_fixes

    body = _json.dumps({
        "commit": "deadbeef", "short": "deadbee", "on_disk": "deadbeef",
        "stale": False, "fingerprint": "nope-not-this-tree",
        "db": {"dialect": "postgresql", "target": "h/d", "drafts": 3,
               "identity": "7401992"},
    })

    class _Resp:
        status = 200
        headers = {"Content-Type": "application/json"}
        def read(self): return body.encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request
    real = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: _Resp()
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = verify_fixes.main_for_test("http://example.invalid")
    finally:
        urllib.request.urlopen = real
    out = buf.getvalue()
    assert "7401992" in out, f"the identity never reached the screen:\n{out}"
    # A fingerprint that does not match must stop the run rather than let the
    # checks below report this tree's fixes as missing on someone else's build.
    assert "NOT running this tree" in out
    assert rc == 1


def test_the_zendesk_check_uses_the_app_s_own_auth_path():
    """It forced the email/API-token pair. The app authenticates through the
    Replit connector (OAuth) and that pair is unset, so the check got a 401 and
    printed BROKEN for a service that was fine — the diagnostic tool committing
    the exact bug it exists to find. It has to go through the same call the
    pipeline does, or a green check means nothing and a red one means less."""
    src = open("tools/doctor.py", encoding="utf-8").read()
    i = src.find("def _zendesk():")
    body = src[i:i + 1400]
    assert "_zd_get" in body, \
        "the check no longer goes through the app's own Zendesk call"
    assert "auth=(f\"{ZENDESK_EMAIL}/token\"" not in body, \
        "it is forcing the token pair again"
    assert "via {path}" in body, \
        "it does not say WHICH auth path answered, so a connector failure and " \
        "a token failure read the same"
