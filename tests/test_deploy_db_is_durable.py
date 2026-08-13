"""A deployment must not run on a database a redeploy will wipe.

THE INCIDENT THIS GUARDS. The deployment is autoscale — a fresh container per
instance and per deploy — and DATABASE_URL fell back to a sqlite file inside
that container. Every redeploy started on an empty database and the reviews
ingested since the last deploy were gone, with only a log line to say so. A
warning did not stop it happening, so boot now refuses on that combination.
"""
import importlib

import pytest


def _resolve(monkeypatch, env):
    for k in ("DATABASE_URL", "PGHOST", "PGDATABASE", "PGUSER",
              "PGPASSWORD", "PGPORT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import server.config as cfg
    return cfg._resolve_database_url()


# ── DATABASE_URL resolution ────────────────────────────────────────────────

def test_an_explicit_url_wins(monkeypatch):
    got = _resolve(monkeypatch, {"DATABASE_URL": "postgresql://u@h/db",
                                 "PGHOST": "ignored"})
    assert got == "postgresql://u@h/db"


def test_it_is_built_from_the_PG_vars_replit_exports(monkeypatch):
    """The whole point: DATABASE_URL is not always propagated into an autoscale
    deployment even when Postgres is provisioned, but PGHOST/etc. are. Building
    the URL from them is what makes 'provision Postgres and redeploy' persist
    without hand-setting a URL."""
    got = _resolve(monkeypatch, {"PGHOST": "db.internal", "PGDATABASE": "orm",
                                 "PGUSER": "svc", "PGPASSWORD": "p@ss word",
                                 "PGPORT": "5433"})
    assert got == "postgresql://svc:p%40ss+word@db.internal:5433/orm", got


def test_the_password_and_user_are_url_encoded(monkeypatch):
    got = _resolve(monkeypatch, {"PGHOST": "h", "PGDATABASE": "d",
                                 "PGUSER": "a/b", "PGPASSWORD": "x:y@z"})
    assert "a%2Fb" in got and "x%3Ay%40z" in got, got


def test_the_sqlite_fallback_is_last(monkeypatch):
    """Only when there is nothing else — fine for the dev repl, and the boot
    guard is what stops it reaching an autoscale deployment."""
    assert _resolve(monkeypatch, {}) == "sqlite:///./local.db"


def test_partial_pg_vars_do_not_build_a_broken_url(monkeypatch):
    """PGHOST but no PGDATABASE is not enough — a half-built URL that fails to
    connect is worse than the honest sqlite fallback the guard will catch."""
    assert _resolve(monkeypatch, {"PGHOST": "h"}) == "sqlite:///./local.db"


# ── the boot guard ─────────────────────────────────────────────────────────

def _guard(monkeypatch, backend, env):
    for k in ("REPLIT_DEPLOYMENT", "REPLIT_DEPLOYMENT_ID", "ALLOW_EPHEMERAL_DB"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import server.db as db
    return db, backend


def test_a_deployment_on_sqlite_refuses_to_boot(monkeypatch):
    db, backend = _guard(monkeypatch, "sqlite", {"REPLIT_DEPLOYMENT": "1"})
    with pytest.raises(RuntimeError) as e:
        db.assert_durable_on_deploy(backend)
    msg = str(e.value)
    assert "REFUSING TO START" in msg
    assert "DATABASE_URL" in msg and "Postgres" in msg
    assert "ALLOW_EPHEMERAL_DB" in msg, "the escape hatch is not named"


def test_the_dev_repl_on_sqlite_is_fine(monkeypatch):
    """No REPLIT_DEPLOYMENT — local development on sqlite must keep working."""
    db, backend = _guard(monkeypatch, "sqlite", {})
    db.assert_durable_on_deploy(backend)      # must not raise


def test_a_deployment_on_postgres_is_fine(monkeypatch):
    db, backend = _guard(monkeypatch, "postgresql", {"REPLIT_DEPLOYMENT": "1"})
    db.assert_durable_on_deploy(backend)      # must not raise


def test_the_escape_hatch_lets_a_throwaway_deployment_boot(monkeypatch):
    db, backend = _guard(monkeypatch, "sqlite",
                {"REPLIT_DEPLOYMENT": "1", "ALLOW_EPHEMERAL_DB": "1"})
    db.assert_durable_on_deploy(backend)      # must not raise


def test_the_deployment_id_variant_also_triggers_it(monkeypatch):
    db, backend = _guard(monkeypatch, "sqlite",
                {"REPLIT_DEPLOYMENT_ID": "dep_abc"})
    with pytest.raises(RuntimeError):
        db.assert_durable_on_deploy(backend)
