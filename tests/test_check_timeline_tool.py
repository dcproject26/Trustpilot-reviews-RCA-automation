"""The timeline diagnostic reports rather than reimplements.

A diagnostic that copies the logic it is checking can agree with itself while
the pipeline does something else — which is the failure mode a diagnostic
exists to prevent, wearing a hat. So what is asserted here is that it calls the
REAL decision functions, and that it refuses honestly when it cannot answer.
"""
import os
import subprocess
import sys

from tests.conftest import read_source

SRC = read_source("tools/check_timeline.py")


def test_it_uses_the_real_decision_functions():
    """Negative-assertion friendly: these names must appear because the tool
    delegates to them. A reimplementation would not need them."""
    for fn in ("other_booking_named", "_booking_cutoff", "_is_prior_trip",
               "collect_tickets", "_get_timeline_sync", "booking_id_from_ticket",
               "worker_liveness"):
        assert fn in SRC, f"the tool does not call {fn} — it may be reimplementing it"


def test_it_does_not_reimplement_the_filters():
    """The two patterns that decide exclusions live in zendesk.py. A copy here
    would drift silently."""
    assert "_SUBJECT_BID_RE = " not in SRC, "the subject pattern is duplicated"
    assert "_BID_LABEL = " not in SRC, "the label pattern is duplicated"


def _run_tool(env_extra=None, target="33543686"):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "tools/check_timeline.py", target],
                          capture_output=True, text=True, timeout=180, env=env)


def test_it_refuses_rather_than_guessing_without_zendesk():
    """Run for real: with Zendesk not live it must say so and exit non-zero,
    not print an empty timeline that reads like a clean result."""
    r = _run_tool()
    assert r.returncode != 0, "it reported success with no Zendesk configured"
    assert "not live" in r.stdout, r.stdout[:400]


def test_it_survives_a_database_with_no_tables(tmp_path):
    """THE CI FAILURE. The runner starts from a fresh database, so the very
    first query died on "no such table: rca_drafts" — a raw SQLAlchemy
    traceback, which reads as a broken tool rather than an empty database. The
    tool now migrates first (init_db is idempotent) and reaches its real
    checks.

    The local suite never caught this because the dev database already had the
    schema; only a fresh one exposes it, which is exactly what CI is."""
    r = _run_tool({"DATABASE_URL": f"sqlite:///{tmp_path}/fresh.db"})
    assert "no such table" not in r.stdout + r.stderr, \
        "the tool still dies on an unmigrated database"
    assert "Traceback" not in r.stderr, \
        f"a diagnostic must not exit with a stack trace:\n{r.stderr[-600:]}"
    # It got past the database and reached a real finding.
    assert "not live" in r.stdout or "could not be read" in r.stdout, r.stdout[:400]


def test_an_unreadable_database_is_a_finding_not_a_crash(tmp_path):
    """A database it cannot read is most of the answer when someone asks why a
    timeline is empty, so it is reported rather than thrown."""
    bad = tmp_path / "not-a-db.sqlite"
    bad.write_text("this is not a database")
    r = _run_tool({"DATABASE_URL": f"sqlite:///{bad}"})
    assert "Traceback" not in r.stderr, r.stderr[-600:]
    assert r.returncode != 0


def test_it_is_read_only_without_the_rerun_flag():
    """--rerun is the only thing that writes. Nothing else may enqueue."""
    body = SRC[SRC.find("def main("):]
    i = body.find("jobs.enqueue(")
    assert i != -1, "the re-run path is gone"
    # the enqueue must sit under the args.rerun guard
    assert "if args.rerun" in body[:i], \
        "enqueue is reachable without --rerun; the tool is not read-only"


def _tool_module():
    """Import the tool as a module — tools/ is not a package."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "check_timeline_tool", "tools/check_timeline.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_resolve_reports_a_query_failure_rather_than_raising(monkeypatch, capsys):
    """resolve()'s guard is UNREACHABLE from the CLI now — _migrate_first
    catches an unopenable database first — so a mutation narrowing its except
    clause survived. It is still a real defence: init_db can succeed against a
    schema that is stale in some other way (a pending column, an unreadable
    JSON value), and the query then fails with the tables plainly present.

    Driven directly, because that is the only way to reach it. An untested
    guard and a missing one look identical the day they are needed.
    """
    m = _tool_module()

    def _boom(*a, **kw):
        raise RuntimeError("column reviews.picked_up_by does not exist")
    monkeypatch.setattr(m, "_resolve", _boom)

    got = m.resolve("33543686")
    assert got == (None, "", ""), got
    out = capsys.readouterr().out
    assert "could not be read" in out, out
    assert "column reviews.picked_up_by does not exist" in out, \
        "the driver's own message is dropped; it is the part that says what to fix"


def _drive_main(monkeypatch, capsys, booked_on, target="33543686"):
    """Run main() far enough to print its header, with Zendesk not live.

    Driven rather than source-asserted: the header renders from the REAL
    _booking_cutoff, and what matters is the sentence a reader gets.
    """
    m = _tool_module()
    monkeypatch.setattr(sys, "argv", ["check_timeline.py", target])
    monkeypatch.setattr(m, "_migrate_first", lambda: "")
    monkeypatch.setattr(m, "resolve", lambda t: (target, booked_on, "tp_x"))
    import server.config as C
    monkeypatch.setattr(C, "is_live", lambda w: False)
    rc = m.main()
    return rc, capsys.readouterr().out


def test_the_warehouse_epoch_is_shown_as_a_date(monkeypatch, capsys):
    """THE OBSERVED OUTPUT: "booked on: 1.787097364E9". The warehouse stores
    booked-on as epoch seconds in scientific notation. _sort_key parses it, so
    the prior-trip filter DID run — but the headline field of the report read
    as garbage, which teaches a reader to distrust every section under it.

    Both are shown: the date because it is the fact, the raw string because a
    mis-parse could only be diagnosed from it.
    """
    _rc, out = _drive_main(monkeypatch, capsys, "1.787097364E9")
    assert "2026-" in out, f"the epoch was never rendered as a date:\n{out}"
    assert "1.787097364E9" in out, "the stored value is gone; a mis-parse " \
                                   "would be undiagnosable"
    assert "filter CANNOT RUN" not in out, \
        "a date that parses fine was reported as unparseable"


def test_an_unparseable_booking_date_still_says_the_filter_did_not_run(
        monkeypatch, capsys):
    """The inverse bug: rendering a date must not swallow the case where there
    is none to render. A filter that silently keeps everything reads exactly
    like one that found nothing to drop."""
    _rc, out = _drive_main(monkeypatch, capsys, "not-a-date")
    assert "CANNOT RUN" in out, out
    assert "could not be parsed" in out, \
        "a value we DID get and could not read was not reported as one"
    assert "not-a-date" in out, "the value that failed to parse is not named"


def test_the_two_empty_booking_dates_do_not_share_one_sentence(
        monkeypatch, capsys):
    """TWO DIFFERENT EMPTIES, and they send someone to two different places.
    No date at all means the booking never carried the field — a match-path or
    warehouse question. An unparseable one means we HAVE a value and cannot
    read it — a parsing question, and the value is the evidence.

    Asserted as a DIFFERENCE, not as two independent substrings: a build that
    prints the same generic reason for both passes any check that only asks
    "does it say the filter did not run", which is how these two collapsed
    into one sentence and nobody noticed.
    """
    _rc, missing = _drive_main(monkeypatch, capsys, "")
    _rc, unread = _drive_main(monkeypatch, capsys, "not-a-date")

    def _detail(out):
        i = out.find("booked on:")
        return out[i:out.find("[", i + 1) if out.find("[", i + 1) > 0 else len(out)]

    assert "CANNOT RUN" in missing and "CANNOT RUN" in unread
    assert _detail(missing) != _detail(unread), (
        "an absent booking date and an unreadable one print the same "
        f"sentence:\n{_detail(missing)}")
    assert "could not be parsed" not in missing, \
        "an absent date was reported as a parse failure"


def test_the_drain_verdict_is_not_handed_back_to_the_reader():
    """NEGATIVE assertion, which unreachability cannot defeat. The tool used
    to print "if this does not fall to 0 within a minute or two, the drain
    loop is not running" — handing back the one question the rows already
    answer. That it now delegates to worker_liveness is covered by
    test_it_uses_the_real_decision_functions above; what worker_liveness
    DECIDES is driven in tests/test_bulk_rerun_is_durable.py."""
    assert "If this does not fall to 0" not in SRC, "the guess is back"
