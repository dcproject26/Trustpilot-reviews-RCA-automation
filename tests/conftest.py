"""Fixtures shared by tests that need to drive the pipeline for real.

The alternative — asserting that a call appears in server/pipeline.py — is the
spelling check CLAUDE.md forbids: the string stays in the file long after the
line has become unreachable. Driving process_review against a throwaway SQLite
database costs a second and cannot pass for that reason.
"""
import os
import signal
import tempfile
import time

import pytest


# ── the browser tests run LAST, and it is not tidiness ─────────────────────
#
# Playwright's SYNC api runs its asyncio loop IN THE MAIN THREAD, driven by
# greenlets. While `sync_playwright().start()` is alive,
# `asyncio.get_running_loop()` returns that loop from ordinary test code, so
# every `asyncio.run(...)` raises:
#
#     RuntimeError: asyncio.run() cannot be called from a running event loop
#
# `ui_browser` is session-scoped — deliberately, because per-module browsers
# meant 28 Chromium launches and wedged the run at 31% — so the loop stays
# running from the first browser test to the end of the session. Every
# `asyncio.run` test collected after ANY browser test therefore died, and the
# random collection order decided which ones. That is 141 call sites across
# 33 modules, and it is why the full suite reported 263 failures that every
# one of those modules passes on its own.
#
# Moving them apart is the whole fix. No module both drives the browser and
# calls `asyncio.run`, so a single partition separates them cleanly — the
# alternative was rewriting 141 call sites to hop onto a worker thread, which
# would then have handed SQLite sessions across threads to fix a problem that
# is really about ordering.
#
# trylast, because pytest-randomly shuffles in this same hook and a shuffle
# after this partition puts the browser tests back in the middle.
BROWSER_FIXTURES = {"page", "ui_browser", "ui_server"}


def _needs_browser(item) -> bool:
    return bool(BROWSER_FIXTURES & set(getattr(item, "fixturenames", ()) or ()))


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items):
    browser = [i for i in items if _needs_browser(i)]
    if not browser:
        return
    rest = [i for i in items if not _needs_browser(i)]
    items[:] = rest + browser
    # ANNOUNCED, not silent. A reordering that changes which tests can pass is
    # a judgement about the suite, and a run that quietly sorts itself gives a
    # reader no way to tell this from a suite that happened to be green.
    config._browser_last = (len(browser), len(rest))


def pytest_report_collectionfinish(config, items):
    n = getattr(config, "_browser_last", None)
    if not n:
        return []
    return [f"browser tests moved to the end: {n[0]} of {n[0] + n[1]} — "
            f"playwright's sync loop runs in the main thread and breaks "
            f"asyncio.run() for everything collected after it"]


@pytest.fixture()
def live_db(monkeypatch):
    """A throwaway SQLite database carrying the real schema."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    import importlib
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    yield db
    os.unlink(tmp.name)


# ── the browser-driven UI tests ────────────────────────────────────────────
# THE HANG THIS REPLACES. The `page` fixture was module-scoped and defined in
# tests/test_rca_ui_rendered.py, then imported by 27 other test modules. An
# imported fixture is a SEPARATE DEFINITION in each importing module, so
# "module scope" meant 28 Chromium launches and 28 uvicorn servers in one run,
# not one. Teardown called srv.terminate() and never waited, so the dying
# servers piled up on top of the live ones: a six-file run degraded from 7s to
# 14s per file, and the full 28 wedged at 31% with 10 chrome and 6 uvicorn
# processes alive and zero CPU.
#
# In conftest they are ONE definition, so session scope means what it says.
# The page stays module-scoped — each module still gets a clean page, which is
# the isolation the tests were relying on — but it now costs a new tab rather
# than a browser and a web server.
import socket
import subprocess
import sys

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def _free_port():
    """A port that was free A MOMENT AGO, which is not the same as free.

    THE RACE THIS LEAVES OPEN, AND WHY IT MATTERS HERE. The socket is closed
    before uvicorn binds, so anything else on the machine can take the number
    in between — including a SECOND pytest run, which is exactly the situation
    a developer creates by running the suite while another one is going.

    Two runs then seed IDENTICAL fixture data, so a browser that reaches the
    wrong server sees plausible rows and never notices, while that run's
    per-module `reseed()` rewrites the data underneath it mid-assertion. The
    observed shape was 17 failures in test_rca_ui_rendered.py on a busy
    machine and 65/65 passing on a quiet one.

    The number cannot be made safe here — `ui_server` detects the loss
    instead, by checking that ITS OWN uvicorn is still alive before trusting
    whatever answered on the port.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _serve_on(port, env):
    """Start uvicorn, and return it only once IT is the thing listening."""
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app",
                            "--port", str(port), "--log-level", "warning"],
                           env=dict(env, SEED_MOCK="0"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        # OUR PROCESS FIRST, THE PORT SECOND. uvicorn exits immediately when
        # the port is already taken, so a dead child means we lost the race —
        # and the connection below would then succeed against SOMEBODY ELSE'S
        # server. Connecting first and asking questions later is how a run
        # ends up driving another run's database.
        if srv.poll() is not None:
            return None
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return srv
        except OSError:
            time.sleep(0.25)
    # Ran out of patience rather than lost the race: kill it, say which.
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except subprocess.TimeoutExpired:
        srv.kill()
    return None


@pytest.fixture(scope="session")
def ui_browser():
    """One Chromium for the whole run."""
    pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    if not os.path.exists(CHROME):
        pytest.skip("bundled chromium not present")
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    # --no-sandbox because this runs as root in a container, where the sandbox
    # cannot initialise and the launch hangs rather than failing.
    br = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    try:
        yield br
    finally:
        # try/finally, because the old teardown sat after a bare `yield` and
        # a fixture that raised mid-setup leaked both the browser and the
        # server for the rest of the run.
        try:
            br.close()
        finally:
            pw.stop()


class _UiServer:
    """The shared server, plus the reseed the per-module page fixture needs.

    THE SERVER IS SHARED AND THE DATA IS NOT. Sharing both made one module's
    inline edits visible to the next: test_inbox_search stopped finding its
    review by classification because an earlier module had edited it. The
    expensive things — Chromium, uvicorn — are worth sharing; the four rows
    in the database are not, and `reseed()` costs about as much as a request.
    """

    def __init__(self, port, url, env):
        self.port, self.url, self.env = port, url, env

    def reseed(self):
        """Put the fixture rows back. Called BY THE `page` FIXTURE, per module.

        It did not used to be, and the reason it now can is that it no longer
        spawns an interpreter: `seed_db` writes through an engine in this
        process. The old version shelled out, re-importing the whole server
        stack 28 times while the live uvicorn held the SQLite file, and wedged
        the run at 62%. The cost was the interpreter, not the write.

        Per-module reseeding became necessary the moment the browser tests
        were moved into one contiguous block. Before that they were scattered
        between non-browser modules and a mutation usually had somebody else's
        run in between; now they follow each other directly, so
        test_rca_edits' added contact was still on the card when
        test_rca_ui_rendered counted the unverified ones, and nine tests
        failed for a reason that had nothing to do with what they assert.
        """
        from tests.test_rca_ui_rendered import seed_db
        seed_db(self.url)

    def __int__(self):        # so f"...:{ui_server}" style uses still work
        return self.port

    def __str__(self):
        return str(self.port)


@pytest.fixture(scope="session")
def ui_server():
    """One seeded database and one uvicorn for the whole run."""
    from tests.test_rca_ui_rendered import seed_script
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    url = f"sqlite:///{tmp.name}"
    env = dict(os.environ, DATABASE_URL=url, MOCK_MODE="true")
    subprocess.run([sys.executable, "-c", seed_script(url)],
                   check=True, capture_output=True, env=env)

    # RETRIED, because losing the port is not a reason to give up — it is a
    # reason to pick another number. A single attempt made the whole browser
    # block fail whenever a second pytest happened to be running, which reads
    # as a regression and is not one.
    srv = port = None
    for _ in range(5):
        port = _free_port()
        srv = _serve_on(port, env)
        if srv is not None:
            break
    if srv is None:
        pytest.skip("could not get a port to ourselves after 5 attempts — "
                    "another server kept taking it, or uvicorn would not start")
    try:
        yield _UiServer(port, url, env)
    finally:
        srv.terminate()
        try:
            # WAITED FOR. terminate() only asks; without this the process is
            # still shutting down while the next one starts, which is how six
            # servers were alive at once.
            srv.wait(timeout=10)
        except subprocess.TimeoutExpired:
            srv.kill()
            srv.wait(timeout=5)
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ── why there is no per-test timeout hook here ─────────────────────────────
# A SIGALRM-based `pytest_runtest_call` wrapper was written and REMOVED. It
# does not work: Playwright's sync API runs inside a greenlet on the main
# thread, and while that greenlet is blocked waiting on CDP the signal does
# not unwind it. A deliberate `page.evaluate("() => new Promise(() => {})")`
# sat there well past the alarm, exactly as before. Shipping it would have
# added a guard that guards nothing — the failure this codebase opens with,
# and it was only caught because the guard was tested against a real hang
# instead of being assumed to work.
#
# What DOES bound a hang is running each browser file in its own process with
# a timeout: scripts/run_browser_tests.sh. A wedged file is killed and named,
# and the rest of the batch still runs.
