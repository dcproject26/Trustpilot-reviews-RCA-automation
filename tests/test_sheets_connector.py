"""The Google Sheets connector, and which credential the export picks.

WHY THIS EXISTS. The export asked for GCP_SERVICE_ACCOUNT_JSON, a credential
this project has never had — BigQuery and Zendesk both authenticate through
Replit connectors instead. So the export was the one feature wired to a secret
nobody set, and the one feature that never wrote a row.

The failures guarded here are all the same shape: a 403 at write time with
three different causes behind it — no connection, a read-only grant, or a
service-account sheet that was never shared. They are told apart BEFORE the
write, or they are indistinguishable after it.
"""
import pytest

from server.services import sheets_connector as SC
from server.services import sheet_export as SX


@pytest.fixture(autouse=True)
def _clean():
    SC.reset_cache()
    yield
    SC.reset_cache()


class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _api(monkeypatch, payload):
    monkeypatch.setenv("REPLIT_CONNECTORS_HOSTNAME", "connectors.test")
    monkeypatch.setenv("REPL_IDENTITY", "tok")
    monkeypatch.setattr(SC.requests, "get",
                        lambda *a, **k: FakeResp(payload))


def _conn(**settings):
    return {"items": [{"settings": settings}]}


# ── reading the connection ──────────────────────────────────────────────────

def test_a_token_at_the_top_level_is_found():
    """bq_connector finds it here, so this shape is real."""
    assert SC._dig({"access_token": "T", "scope": "x"}) == ("T", "x")


def test_a_token_nested_under_oauth_credentials_is_also_found():
    """And the connectors API returns it here, which is the shape the probe
    against the live API actually showed. Reading only one shape would fail
    with an empty token and read as a revoked connection."""
    got = SC._dig({"oauth": {"credentials": {"access_token": "T",
                                             "scope": "s"}}})
    assert got == ("T", "s")


def test_a_connection_with_no_token_anywhere_raises_naming_the_keys():
    """Returning "" quietly would surface later as a 401 from Google and be
    read as a revoked connection, which is a different problem entirely."""
    with pytest.raises(RuntimeError) as e:
        SC._dig({"weird": 1, "other": 2})
    assert "no access_token" in str(e.value)
    assert "other" in str(e.value) and "weird" in str(e.value), \
        "it did not say what it actually saw"


def test_no_connectors_host_is_not_available_and_asks_nothing():
    """Outside Replit entirely. Must not raise, must not call out."""
    assert SC.available() is False


def test_an_empty_items_list_names_where_to_add_it(monkeypatch):
    _api(monkeypatch, {"items": []})
    assert SC.available() is False
    with pytest.raises(RuntimeError) as e:
        SC._settings()
    assert "Tools" in str(e.value) and "Connectors" in str(e.value), \
        "an error that does not say what would work"


# ── the scope, which decides whether a write can succeed ────────────────────

def test_a_write_scope_is_no_problem(monkeypatch):
    _api(monkeypatch, _conn(access_token="T",
                            scope="https://www.googleapis.com/auth/spreadsheets"))
    assert SC.scope_problem() == ""


def test_a_read_only_scope_is_named_before_the_write(monkeypatch):
    """THE POINT OF THE WHOLE FILE. spreadsheets.readonly satisfies every read
    this code does and fails the write with a 403 — the same 403 an unshared
    sheet gives. Caught here, it is one sentence; caught at write time it is
    indistinguishable from two other causes."""
    _api(monkeypatch, _conn(
        access_token="T",
        scope="https://www.googleapis.com/auth/spreadsheets.readonly"))
    why = SC.scope_problem()
    assert "READ-ONLY" in why
    assert "403" in why, "it did not say what the symptom would have been"


def test_a_missing_scope_is_reported_as_unknown_not_as_fine(monkeypatch):
    """The connectors API does not always return one. Unknown and adequate
    are different claims, and only one of them is honest here."""
    _api(monkeypatch, _conn(access_token="T"))
    why = SC.scope_problem()
    assert "unknown" in why
    assert "READ-ONLY" not in why, "it claimed to know something it does not"


def test_drive_scope_counts_as_write(monkeypatch):
    _api(monkeypatch, _conn(access_token="T",
                            scope="https://www.googleapis.com/auth/drive"))
    assert SC.scope_problem() == ""


def test_the_readonly_drive_scope_does_not_count_as_write(monkeypatch):
    """`drive.readonly` CONTAINS the string `drive`. A substring check that
    did not account for that would call a read-only grant writable."""
    _api(monkeypatch, _conn(
        access_token="T", scope="https://www.googleapis.com/auth/drive.readonly"))
    assert "READ-ONLY" in SC.scope_problem()


# ── which credential the export chooses ─────────────────────────────────────

def test_the_connector_is_preferred_over_a_service_account(monkeypatch):
    """Not arbitrary: the connector has NO SHARING STEP. A service account is
    a stranger to the Drive and needs the sheet shared with its client_email,
    which is the step misdiagnosed twice."""
    monkeypatch.setattr(SX, "_connector_available", lambda: True)
    monkeypatch.setattr(SC, "scope_problem", lambda: "")
    import server.config as cfg
    monkeypatch.setattr(cfg, "GCP_SERVICE_ACCOUNT_JSON", "{}")
    assert SX.auth_source() == ("connector", "")


def test_the_service_account_is_used_when_there_is_no_connector(monkeypatch):
    monkeypatch.setattr(SX, "_connector_available", lambda: False)
    import server.config as cfg
    monkeypatch.setattr(cfg, "GCP_SERVICE_ACCOUNT_JSON", GOOD)
    assert SX.auth_source() == ("service_account", "")


def test_neither_names_both_routes(monkeypatch):
    """The env var is the HARDER route. An error naming only it sends the
    reader into GCP for a key they do not need."""
    monkeypatch.setattr(SX, "_connector_available", lambda: False)
    import server.config as cfg
    monkeypatch.setattr(cfg, "GCP_SERVICE_ACCOUNT_JSON", "")
    src, why = SX.auth_source()
    assert src == "none"
    assert "Connectors" in why and "GCP_SERVICE_ACCOUNT_JSON" in why
    assert "no sharing step" in why


def test_a_read_only_connector_blocks_rather_than_falling_back(monkeypatch):
    """FALLING BACK WOULD BE WORSE. A connection that exists but cannot write
    is a thing to fix, not a thing to route around — silently using a service
    account instead would leave the broken connection in place and make the
    next person debug the wrong credential."""
    monkeypatch.setattr(SX, "_connector_available", lambda: True)
    monkeypatch.setattr(SC, "scope_problem", lambda: "the connection is READ-ONLY")
    import server.config as cfg
    monkeypatch.setattr(cfg, "GCP_SERVICE_ACCOUNT_JSON", GOOD)
    src, why = SX.auth_source()
    assert src == "connector" and "READ-ONLY" in why


def test_the_auth_header_uses_the_connector_token(monkeypatch):
    """Driving the call site. _hdr() must take the connector branch without
    touching google-auth — which is not installed everywhere this runs."""
    monkeypatch.setattr(SX, "_connector_available", lambda: True)
    monkeypatch.setattr(SC, "scope_problem", lambda: "")
    monkeypatch.setattr(SC, "token", lambda force=False: "TOKEN123")
    io = SX.SheetIO("sid", "0")
    assert io._hdr() == {"Authorization": "Bearer TOKEN123"}


GOOD = ('{"type":"service_account","client_email":"a@b.iam",'
        '"private_key":"-----BEGIN PRIVATE KEY-----\\nx\\n-----END PRIVATE KEY-----",'
        '"token_uri":"https://oauth2.googleapis.com/token"}')


# ── an unusable key must not be able to stop the server ─────────────────────

def test_a_malformed_key_does_not_crash_bigquery_at_import(monkeypatch):
    """WHAT HAPPENED. bigquery.py branches at IMPORT on
    `if GCP_SERVICE_ACCOUNT_JSON:` — present, not usable. A placeholder pasted
    into .env is present, so it took the service-account branch, reached
    json.loads and raised out of module import. The whole application died on
    boot with a JSONDecodeError while BigQuery itself was working perfectly
    through the Replit connector, which needs no key at all.

    Driving the real import rather than the helper: the bug was in which
    branch module-level code chose, and a test of credential_problem() alone
    would have passed the entire time the app was refusing to start.
    """
    import importlib
    import server.config as cfg
    monkeypatch.setattr(cfg, "GCP_SERVICE_ACCOUNT_JSON",
                        '{"type":"service_account",...}')
    monkeypatch.setattr(cfg, "is_live", lambda svc: svc == "bigquery")
    import server.services.bigquery as BQ
    called = []
    import server.services.bq_connector as BQC
    monkeypatch.setattr(BQC, "Client", lambda *a, **k: called.append(1) or object())
    importlib.reload(BQ)          # must not raise
    assert called == [1], "it did not fall back to the connector"


def test_a_usable_key_still_takes_the_service_account_branch(monkeypatch):
    """The converse. A fallback that fires for every key would silently ignore
    a perfectly good credential, which is the inverse bug and just as quiet."""
    import server.services.bigquery as BQ
    assert BQ.credential_problem(GOOD) == "", \
        "a good key reads as unusable, so the fallback would always fire"


# ── two mutation survivors ──────────────────────────────────────────────────

def test_is_live_says_the_export_is_on_with_only_a_connector(monkeypatch):
    """SURVIVED A MUTATION: reverting is_live("sheet_export") to require
    GCP_SERVICE_ACCOUNT_JSON killed nothing.

    That reversion is the whole bug being fixed — with a connector bound and
    no key, is_live would be False, _write() would log "not exporting … is
    unset" and return {"skipped"}, and the export would sit inert next to a
    perfectly good connection. Nothing asserted otherwise, so the clause could
    be deleted in silence.
    """
    import server.config as cfg
    monkeypatch.setattr(cfg, "RCA_EXPORT_SHEET_ID", "sheet123")
    monkeypatch.setattr(cfg, "GCP_SERVICE_ACCOUNT_JSON", "")
    monkeypatch.setattr(cfg, "_sheets_connector_available", lambda: True)
    monkeypatch.setattr(cfg, "MOCK_MODE", False)
    assert cfg.is_live("sheet_export") is True


def test_is_live_says_the_export_is_off_with_neither(monkeypatch):
    """The converse, or the test above passes against a clause hardcoded True."""
    import server.config as cfg
    monkeypatch.setattr(cfg, "RCA_EXPORT_SHEET_ID", "sheet123")
    monkeypatch.setattr(cfg, "GCP_SERVICE_ACCOUNT_JSON", "")
    monkeypatch.setattr(cfg, "_sheets_connector_available", lambda: False)
    monkeypatch.setattr(cfg, "MOCK_MODE", False)
    assert cfg.is_live("sheet_export") is False
