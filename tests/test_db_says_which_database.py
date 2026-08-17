"""Every process that opens a connection is told which database it opened.

THE RECURRING FAILURE THIS ENDS. A Development database runs beside a
Production one, and a tool connects to whichever DATABASE_URL its shell
carried. Nothing said which, so every number a tool printed was ambiguous, and
the same mistake arrived repeatedly in a new costume: a webhook-delivery count
read from the dev database was reported as "the Slack webhook is broken"; a
purge answered "no review tp_1786790990_301059" for a review that was simply
in the other database.

Fixed at the layer the tools SHARE rather than in the tools. Seven of them
query this database and never name it; patching them one at a time is what had
been happening, always one tool behind. They all import server.db, so the
announcement hangs off the engine's connect event — which also covers tools
nobody has written yet.
"""
import os
import subprocess
import sys
import tempfile

from tests.conftest import drop_temp_db


def _run(code, db_url):
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env={**os.environ, "DATABASE_URL": db_url,
                                          "PYTEST_CURRENT_TEST": ""})


_OPEN = ("import server.db as d, sqlalchemy as sa\n"
         "s=d.SessionLocal(); s.execute(sa.text('select 1')); s.close()\n")


def test_opening_a_connection_says_which_database():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    try:
        r = _run(_OPEN, f"sqlite:///{tmp.name}")
        assert "[db] connected to" in r.stderr, r.stderr
        assert tmp.name in r.stderr, r.stderr
    finally:
        drop_temp_db(tmp.name)


def test_it_names_the_environment_too():
    """Which database AND which side thinks it owns it — dev/local vs
    deployment is the distinction every one of these incidents turned on."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    try:
        assert "[dev/local]" in _run(_OPEN, f"sqlite:///{tmp.name}").stderr
    finally:
        drop_temp_db(tmp.name)


def test_it_is_said_once_not_per_connection():
    """A banner per connection is noise, and noise is ignored.

    DISPOSING BETWEEN OPENS IS THE POINT. `SessionLocal()` hands back a POOLED
    connection, so four sessions fire the engine's connect event once and a
    test that only opened four sessions would pass even with the once-guard
    removed — mutation testing caught exactly that. `engine.dispose()` drops
    the pool, so the next open is a real connection and the guard is actually
    exercised.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    try:
        r = _run(_OPEN + ("import server.db as d\nd.engine.dispose()\n" + _OPEN) * 3,
                 f"sqlite:///{tmp.name}")
        assert r.stderr.count("[db] connected to") == 1, r.stderr
    finally:
        drop_temp_db(tmp.name)


def test_it_goes_to_stderr_so_it_never_corrupts_a_tool_s_output():
    """diagnose writes a report and purge prints a list a human checks; a
    banner in the middle of stdout is its own kind of unhelpful."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    try:
        r = _run(_OPEN, f"sqlite:///{tmp.name}")
        assert "[db] connected to" not in r.stdout, r.stdout
    finally:
        drop_temp_db(tmp.name)
