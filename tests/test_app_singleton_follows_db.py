"""server.main must follow the CURRENT database, not the one it imported against.

THE REGRESSION THIS PINS. A `client`-fixture test reloads server.db onto its own
throwaway file and, at teardown, deletes it. server/main.py used to bind
`init_db`/`SessionLocal` BY VALUE at import, so the app singleton went on opening
the deleted file. Any later harness that stood the app up itself — `_draft_with`
in test_guest_name_and_review_date — inherited the dead binding and died in the
lifespan with `no such table: reviews`.

It surfaced only on Linux, and the reason is worth writing down: the failing
lifespan path is `seed_mocks`, which runs ONLY under MOCK_MODE. The full Linux
run-up had MOCK_MODE set by an earlier module where the Windows run did not, so
the same stale binding was harmless on one platform and fatal on the other —
exactly the kind of order-and-mode coincidence a test must not depend on. So
this makes both triggers explicit: a stale, deleted binding AND MOCK_MODE on.

The fix is that main resolves the database through the module (`_db.SessionLocal()`)
at call time, so `app` follows whatever database is live now and no fixture has
to reload server.main. Revert that and this test sees the exact lifespan crash.

This test reloads server.config/server.db itself, so it restores BOTH the
environment and the module bindings on the way out — leaving them as it found
them. A test about a module-state leak that leaks module state would be the
same bug wearing a lab coat.
"""
import importlib
import os

from fastapi.testclient import TestClient

from tests.conftest import drop_temp_db


def _point_at(url):
    """Reload config + db onto `url`, schema created — what live_db does."""
    os.environ["DATABASE_URL"] = url
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    return db


def _rebind_from_env():
    """Reload config, db AND main against whatever DATABASE_URL currently says —
    used to put the modules back the way we found them. main is included because
    this test reloads it (to bind the app to the soon-deleted `gone`), and a
    left-behind reloaded `app` is itself module state leaking into later tests."""
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    import server.main as main
    importlib.reload(main)


def test_app_lifespan_follows_the_current_db_after_a_reload(tmp_path):
    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "MOCK_MODE")}
    try:
        # MOCK_MODE on so the lifespan actually opens a session (seed_mocks) —
        # the query that crashed. Without it the lifespan only create_all's and
        # the bug hides, which is precisely why it hid on Windows.
        os.environ["MOCK_MODE"] = "true"

        # (1) a prior client-style test: bind main to a temp db, run the app
        #     once, then delete the file — the state a client teardown leaves.
        gone = tmp_path / "gone.db"
        _point_at(f"sqlite:///{gone}")
        import server.main as main
        importlib.reload(main)                 # main is now bound to `gone`
        with TestClient(main.app):
            pass
        drop_temp_db(str(gone))                # teardown removes the database

        # (2) a later harness stands up its OWN fresh database — and, like
        #     `_draft_with`, never reloads server.main.
        db = _point_at(f"sqlite:///{tmp_path / 'live.db'}")

        # (3) the app must come up against `live`, not the deleted `gone`.
        #     Before the lazy fix, seed_mocks opened `gone` and raised
        #     `no such table: reviews` right here, at TestClient(app).__enter__.
        with TestClient(main.app) as c:
            assert c.get("/healthz").status_code == 200
        # and the live database is readable through the same current binding
        assert db.SessionLocal().query(db.Review).count() >= 0
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # Rebind the modules to the restored environment so the deleted temp
        # does not follow us into the next test — the leak this file exists for.
        _rebind_from_env()
