"""An unusable DATABASE_URL must refuse with a sentence, not a stack trace.

THE REPORTED CASE. A documented command was run verbatim:

    DATABASE_URL='<production url>' python3 tools/purge_reviews.py --before ...

and it returned eight frames of SQLAlchemy ending in "Could not parse
SQLAlchemy URL from string '<production url>'". Accurate, and it says neither
that the placeholder was left in nor where the real value lives.

Checked in server/db.py because every tool imports it — one check covers all of
them, at the moment the mistake is made rather than deep inside a library.
"""
import os
import subprocess
import sys


def _run(url):
    env = {**os.environ, "PYTEST_CURRENT_TEST": ""}
    if url is None:
        env.pop("DATABASE_URL", None)
    else:
        env["DATABASE_URL"] = url
    return subprocess.run([sys.executable, "-c", "import server.db"],
                          capture_output=True, text=True, env=env)


def test_the_placeholder_is_named_as_a_placeholder():
    r = _run("<production url>")
    assert "REFUSING TO START" in r.stderr, r.stderr
    assert "placeholder" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, "it still dies with a stack trace"


def test_it_says_where_the_real_value_lives():
    """A refusal that does not say what would work is half an answer."""
    r = _run("<production url>")
    assert "Deployments" in r.stderr and "DATABASE_URL" in r.stderr, r.stderr
    assert "postgresql://" in r.stderr, "no example of a working URL"


def test_a_string_that_is_not_a_url_is_refused():
    r = _run("neondb")
    assert "REFUSING TO START" in r.stderr, r.stderr
    assert "scheme" in r.stderr, r.stderr


def test_an_unset_value_falls_back_rather_than_refusing():
    """Unset is not a mistake — config resolves it to this workspace's own
    sqlite database, which is how local development is meant to run."""
    r = _run(None)
    assert "REFUSING TO START" not in r.stderr, r.stderr
    assert r.returncode == 0, r.stderr


def test_a_real_url_still_starts_normally():
    """The inverse bug: a guard that refuses everything is worse than none."""
    r = _run("sqlite:///./local.db")
    assert "REFUSING TO START" not in r.stderr, r.stderr
    assert r.returncode == 0, r.stderr
