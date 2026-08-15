"""The throwaway-database teardown must work on Windows too.

POSIX lets you unlink a file another handle still holds open; Windows raises
PermissionError [WinError 32]. Fifteen fixture teardowns called `os.unlink`
directly, so on Windows every one of them raised — and a teardown that raises
is reported as an ERROR, not a failure. A machine with nothing wrong with it
came back with 314 errors and could not be used to check a change at all.

THESE TESTS RUN ON POSIX, WHERE THE ORIGINAL BUG CANNOT HAPPEN. So the Windows
condition is simulated: the lock is forced by patching `os.unlink` to raise the
error Windows raises. Asserting "it works here" would prove nothing about the
platform that was broken.
"""
import os
import tempfile

import pytest

from tests.conftest import drop_temp_db


def _tmpfile():
    t = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    t.close()
    return t.name


def test_the_file_is_actually_deleted():
    path = _tmpfile()
    assert drop_temp_db(path) is True
    assert not os.path.exists(path)


def test_nothing_is_disposed_when_the_delete_simply_works():
    """THE POSIX PATH MUST NOT CHANGE. Disposing up front closes the handle
    that keeps an unlinked file alive, and fixtures read through it after
    teardown — that version broke 14 tests. A plain delete must stay plain."""
    import server.db as db
    disposed = []
    real = db.engine.dispose
    try:
        db.engine.dispose = lambda *a, **k: disposed.append(True)
        drop_temp_db(_tmpfile())
    finally:
        db.engine.dispose = real
    assert disposed == [], "the pool was released on a delete that succeeded"


def test_a_locked_file_releases_the_pool_and_retries(monkeypatch):
    """THE WINDOWS PATH. Only when the OS refuses is the engine disposed —
    and then the delete is retried, so the file is really gone rather than
    merely not raising."""
    import server.db as db
    path = _tmpfile()
    calls, disposed = [], []
    real_unlink, real_dispose = os.unlink, db.engine.dispose

    def _locked_once(p):
        calls.append(p)
        if len(calls) == 1:                     # first try: Windows lock
            raise PermissionError(32, "used by another process")
        return real_unlink(p)                   # retry, after the dispose

    monkeypatch.setattr(os, "unlink", _locked_once)
    try:
        db.engine.dispose = lambda *a, **k: disposed.append(True)
        assert drop_temp_db(path) is True
    finally:
        db.engine.dispose = real_dispose
    assert disposed, "the pool was never released after the lock"
    assert len(calls) == 2, "the delete was not retried"


def test_a_windows_style_lock_does_not_raise(monkeypatch):
    """THE REGRESSION THIS FILE EXISTS FOR. A locked file must come back as
    False, never as an exception — an exception here is the 314-error run."""
    path = _tmpfile()

    def _locked(_p):
        raise PermissionError(
            32, "The process cannot access the file because it is being "
                "used by another process")

    monkeypatch.setattr(os, "unlink", _locked)
    assert drop_temp_db(path) is False        # said, not raised
    monkeypatch.undo()
    os.unlink(path)


def test_an_already_deleted_file_is_success_not_an_error():
    path = _tmpfile()
    os.unlink(path)
    # Gone is the outcome we wanted; reporting it as a failure would make a
    # clean teardown look broken.
    assert drop_temp_db(path) is True


def test_a_teardown_never_raises_whatever_the_filesystem_says(monkeypatch):
    def _boom(_p):
        raise OSError(16, "Device or resource busy")

    monkeypatch.setattr(os, "unlink", _boom)
    assert drop_temp_db("/nonexistent/whatever.db") is False


def test_no_teardown_still_calls_os_unlink_directly():
    """NEGATIVE, so unreachability cannot defeat it (CLAUDE.md rule 2).

    A new fixture copying the old pattern reintroduces the Windows failure
    silently, and it would pass every test on this platform.
    """
    import pathlib
    import re
    # A CALL, not a mention. This file and conftest both DESCRIBE the old
    # pattern in prose, and matching the bare string flagged them for saying
    # what not to do. A real call site is the whole statement on its own line.
    call = re.compile(r"^[ \t]*os\.unlink\(tmp\.name\)[ \t]*$", re.M)
    offenders = [
        p.name for p in pathlib.Path(__file__).parent.glob("*.py")
        if call.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        f"these teardowns will raise WinError 32 on Windows; use "
        f"drop_temp_db() instead: {offenders}")
