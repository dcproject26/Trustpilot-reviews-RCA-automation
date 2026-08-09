"""
Google Sheets via the Replit Google Sheets connection (integration).

Auth is handled by the Replit connectors service — OAuth as the person who
connected it, no service-account key. Mirrors bq_connector.py / zd_connector.py,
which is how BigQuery and Zendesk already authenticate here.

WHY THIS EXISTS RATHER THAN A SERVICE-ACCOUNT KEY. The export is the only
thing in this project that ever wanted GCP_SERVICE_ACCOUNT_JSON, and it is the
only thing that has never worked. A service account is a stranger to your
Drive: the sheet has to be explicitly shared with its client_email as an
editor, and when that share is missing the write fails with a 403 that reads
exactly like a bad credential. Two of those misdiagnoses happened in one
afternoon. OAuth as the sheet's owner has no sharing step to get wrong.

THE SCOPE IS CHECKED, AND SAID. A read-only token fails the write with a 403
that also reads like a permission problem on the sheet. The connector decides
what it grants, not this code, so the granted scope is read back and reported —
and when the connectors API does not return one, that is said too rather than
assumed adequate.
"""
import os
import time
import logging
import threading

import requests

log = logging.getLogger(__name__)

CONNECTOR_NAME = "google-sheet"

# The write scope. `spreadsheets.readonly` and `drive.readonly` both satisfy a
# read and then fail the write; `spreadsheets` and full `drive` carry write.
_WRITE_SCOPES = ("https://www.googleapis.com/auth/spreadsheets",
                 "https://www.googleapis.com/auth/drive")

_cache: dict = {"token": None, "scope": None, "fetched_at": 0.0}
_lock = threading.Lock()
_TOKEN_TTL = 300  # re-fetch settings every 5 min; connector refreshes upstream


def _identity_header() -> dict:
    if os.environ.get("REPL_IDENTITY"):
        tok = "repl " + os.environ["REPL_IDENTITY"]
    elif os.environ.get("WEB_REPL_RENEWAL"):
        tok = "depl " + os.environ["WEB_REPL_RENEWAL"]
    else:
        raise RuntimeError("No Replit identity token available for connectors API")
    return {"Accept": "application/json", "X_REPLIT_TOKEN": tok}


def _dig(settings: dict) -> tuple[str, str]:
    """(access_token, scope) out of a connection's settings.

    TWO SHAPES ARE IN THE WILD and both are read rather than one being
    guessed: bq_connector finds the token at settings.access_token, while the
    connectors API also nests it under settings.oauth.credentials. A miss
    RAISES naming the keys that were actually present — an empty token
    returned quietly would surface later as a 401 and get read as a revoked
    connection.
    """
    oauth = ((settings.get("oauth") or {}).get("credentials") or {})
    token = settings.get("access_token") or oauth.get("access_token") or ""
    scope = settings.get("scope") or oauth.get("scope") or ""
    if not token:
        raise RuntimeError(
            f"the {CONNECTOR_NAME} connection carries no access_token; its "
            f"settings hold {sorted(settings)!r}")
    return token, scope


def _settings(force: bool = False) -> tuple[str, str]:
    """Returns (access_token, granted_scope), cached for _TOKEN_TTL seconds."""
    with _lock:
        now = time.time()
        if not force and _cache["token"] and now - _cache["fetched_at"] < _TOKEN_TTL:
            return _cache["token"], _cache["scope"]
        host = os.environ["REPLIT_CONNECTORS_HOSTNAME"]
        r = requests.get(
            f"https://{host}/api/v2/connection?include_secrets=true"
            f"&connector_names={CONNECTOR_NAME}",
            headers=_identity_header(), timeout=15)
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            raise RuntimeError(
                "Google Sheets connection not found — add it via Replit "
                "integrations (Tools → Connectors → Google Sheets)")
        token, scope = _dig(items[0].get("settings") or {})
        _cache.update(token=token, scope=scope, fetched_at=now)
        return token, scope


def available() -> bool:
    """True when the Google Sheets connection is bound to this Repl. Cached."""
    if not os.environ.get("REPLIT_CONNECTORS_HOSTNAME"):
        return False
    if _cache["token"]:
        return True
    try:
        _settings()
        return True
    except Exception as e:
        log.warning(f"Google Sheets connector not available: {e}")
        return False


def token(force: bool = False) -> str:
    """The current OAuth bearer token."""
    return _settings(force=force)[0]


def scope_problem() -> str:
    """Why the granted scope cannot write, in words. "" when it can.

    NOT FOLDED INTO available(). A connection that exists but was granted
    read-only is a DIFFERENT state from no connection at all, and the fixes
    differ — reconnect asking for write, versus connect at all. Collapsing
    them leaves a 403 at write time as the only evidence, which is the same
    403 an unshared sheet produces.
    """
    try:
        _, scope = _settings()
    except Exception as e:
        return str(e)
    if not scope:
        # SAID, NOT ASSUMED FINE. The connectors API does not always return a
        # scope; that is unknown, not adequate, and a write may still 403.
        return ("the connection granted a token but reported no scope, so "
                "whether it can write is unknown until a write is tried")
    # EXACT, NOT SUBSTRING. `spreadsheets` is a prefix of
    # `spreadsheets.readonly` and `drive` of `drive.readonly`, so a containment
    # test calls a read-only grant writable — the precise mistake this whole
    # check exists to catch. Scopes arrive space- or comma-separated.
    granted = set(scope.replace(",", " ").split())
    if not granted & set(_WRITE_SCOPES):
        return (f"the connection is READ-ONLY (scope {scope!r}). Reconnect it "
                f"granting write access, or the write fails with a 403 that "
                f"reads like the sheet is not shared.")
    return ""


def reset_cache() -> None:
    """Drop the cached token. For tests and for a reconnect mid-process."""
    with _lock:
        _cache.update(token=None, scope=None, fetched_at=0.0)
