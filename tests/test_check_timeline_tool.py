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
               "collect_tickets", "_get_timeline_sync", "booking_id_from_ticket"):
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
