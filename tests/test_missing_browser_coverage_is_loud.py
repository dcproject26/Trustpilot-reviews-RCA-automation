""""43 skipped" is 648 tests, and it does not look like it.

`pytest.importorskip("playwright.sync_api")` sits at MODULE level in 43 test
files. Without playwright, pytest skips each MODULE — so the summary reads "43
skipped" and the 648 tests inside them are never collected at all.

A second session ran this suite in a sandbox with no playwright, reported
"3605 passed / 43 skipped", and reasonably read that as a clean run. It was
missing 15% of the suite and ONE HUNDRED PERCENT of the UI coverage — every
test that drives client/index.html — and went on to consider a client-side task
it had no way to verify.

That is this project's first rule wearing a pytest hat: a number that makes "I
could not run 648 tests" look like "43 minor skips". Two different states, one
harmless-looking number.
"""
import subprocess
import sys
from pathlib import Path

from tests.conftest import _browser_gated_files


def test_the_gated_files_are_counted_from_the_tree_not_hardcoded():
    """A hardcoded list goes stale the first time a browser test is added, and
    then under-reports what did not run — which is the bug, again, quieter."""
    files = _browser_gated_files()
    assert files, "no playwright-gated files found; the detector is broken"
    here = Path(__file__).parent
    for name in files:
        assert 'importorskip("playwright' in (here / name).read_text(encoding="utf-8")


def test_every_gated_file_is_actually_found():
    """The counterpart: the detector must not MISS a file, or the warning
    under-states the gap it exists to state."""
    here = Path(__file__).parent
    expected = {f.name for f in here.glob("test_*.py")
                if 'importorskip("playwright' in f.read_text(encoding="utf-8")}
    assert set(_browser_gated_files()) == expected


def _run_without_playwright(tmp_path, args):
    """Run pytest with playwright made unimportable, as that sandbox has it."""
    (tmp_path / "playwright.py").write_text(
        'raise ImportError("simulated: playwright not installed")\n')
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args, "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, timeout=300, env=env,
        cwd=str(Path(__file__).parent.parent))


def test_the_terminal_says_the_browser_tests_did_not_run(tmp_path):
    """Driven for real, with playwright genuinely unimportable — a unit test of
    the hook would not prove the hook is registered."""
    r = _run_without_playwright(tmp_path, ["tests/test_a_contact_needs_a_guest.py"])
    out = r.stdout + r.stderr
    assert "BROWSER TESTS DID NOT RUN" in out, out[-1500:]


def test_it_names_the_fix_not_just_the_problem(tmp_path):
    """"Something is missing" with no remedy is a line a reader scrolls past."""
    r = _run_without_playwright(tmp_path, ["tests/test_a_contact_needs_a_guest.py"])
    out = r.stdout + r.stderr
    assert "pip install playwright" in out, out[-1500:]
    assert "playwright install chromium" in out


def test_it_says_a_green_run_is_not_a_green_run(tmp_path):
    """The whole point. Without this sentence the reader takes the pass count
    at face value, which is exactly what happened."""
    r = _run_without_playwright(tmp_path, ["tests/test_a_contact_needs_a_guest.py"])
    out = r.stdout + r.stderr
    assert "NOT a green run" in out, out[-1500:]
    assert "client/index.html" in out, \
        "it does not say WHICH coverage is missing, so nobody knows what is at risk"


def test_it_is_silent_when_playwright_is_present():
    """A warning on every healthy run is one nobody reads by the third time."""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_a_contact_needs_a_guest.py",
         "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, timeout=300,
        cwd=str(Path(__file__).parent.parent))
    assert "BROWSER TESTS DID NOT RUN" not in r.stdout + r.stderr
