"""Fixtures shared by tests that need to drive the pipeline for real.

The alternative — asserting that a call appears in server/pipeline.py — is the
spelling check CLAUDE.md forbids: the string stays in the file long after the
line has become unreachable. Driving process_review against a throwaway SQLite
database costs a second and cannot pass for that reason.
"""
import os
import tempfile

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
