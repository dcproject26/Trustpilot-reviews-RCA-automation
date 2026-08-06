"""Fixtures shared by tests that need to drive the pipeline for real.

The alternative — asserting that a call appears in server/pipeline.py — is the
spelling check CLAUDE.md forbids: the string stays in the file long after the
line has become unreachable. Driving process_review against a throwaway SQLite
database costs a second and cannot pass for that reason.
"""
import os
import tempfile
import time

import pytest


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
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


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
        # NOT called per module. Doing so spawned a fresh interpreter that
        # re-imported the whole server stack 28 times AND fought the live
        # uvicorn for the SQLite write lock, which wedged the run at 62%.
        # A module that mutates shared rows restores them itself instead.
        from tests.test_rca_ui_rendered import seed_script
        subprocess.run([sys.executable, "-c", seed_script(self.url)],
                       check=True, capture_output=True, env=self.env)

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

    port = _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app",
                            "--port", str(port), "--log-level", "warning"],
                           env=dict(env, SEED_MOCK="0"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.25)
        else:
            pytest.skip("server did not start")
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
