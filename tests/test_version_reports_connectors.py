"""/api/version says which connectors THIS process has.

Deployment secrets are a separate store from workspace secrets. A BigQuery
credential present in the repl can be absent in the published app, and the
only symptom is every review the deployment processes landing in Untraceable
with "BigQuery is not live on this server, so no match was attempted."

That reads as a matching failure, which is what it was reported as. From
outside the container there was no way to tell the two apart: the reason was
recorded in a log nobody could reach and a database nobody could query. One
curl answers it now.
"""
import os
import tempfile

import pytest


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    monkeypatch.setenv("MOCK_MODE", "")
    monkeypatch.setenv("BIGQUERY_BOOKINGS_TABLE", "x.y.z")
    monkeypatch.setenv("GCP_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.delenv("ZENDESK_API_TOKEN", raising=False)
    monkeypatch.delenv("ZENDESK_SUBDOMAIN", raising=False)
    import importlib
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    import server.api as api
    importlib.reload(api)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as c:
        yield c
    os.unlink(tmp.name)


def test_the_connector_block_is_there(client):
    got = client.get("/api/version").json()
    assert "connectors" in got, \
        "there is no way to tell a missing credential from a matching failure"
    assert "bigquery" in got["connectors"]


def test_a_configured_connector_reads_live(client):
    got = client.get("/api/version").json()["connectors"]
    assert got["bigquery"] is True


def test_an_unconfigured_connector_reads_dead(client):
    """The whole point. False here is the answer to "why is everything
    untraceable" — and it must not be able to say True when it is not."""
    got = client.get("/api/version").json()["connectors"]
    assert got["zendesk"] is False


def test_no_credential_values_are_exposed(client):
    """This endpoint is reachable by anyone with the URL."""
    import json
    body = json.dumps(client.get("/api/version").json())
    for v in client.get("/api/version").json()["connectors"].values():
        assert isinstance(v, bool), "a connector reported something other " \
                                    "than yes/no, which may be a secret"
    assert "GCP_SERVICE_ACCOUNT" not in body


def test_the_untraceable_count_is_beside_it(client):
    """Two numbers that only mean something together: how many reviews could
    not be matched, and whether the thing that matches them was switched on."""
    got = client.get("/api/version").json()
    assert "untraceable" in got["db"]
    assert "bigquery" in got["connectors"]
