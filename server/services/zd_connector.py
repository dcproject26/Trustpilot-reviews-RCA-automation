"""
Zendesk via the Replit Zendesk connection (integration).

Auth is handled by the Replit connectors service — OAuth bearer token, no
email/API-token pair needed. Settings (access_token + subdomain) are fetched
from the connectors API and cached briefly; the connector refreshes tokens
upstream. Mirrors the bq_connector.py pattern.

Exposes get_client() which returns a Zenpy client authenticated with the
current OAuth token (rebuilt whenever the cached token rolls over).
"""
import os
import time
import logging
import threading

import requests

log = logging.getLogger(__name__)

_cache: dict = {"token": None, "subdomain": None, "fetched_at": 0.0, "client": None}
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


def _settings(force: bool = False) -> tuple[str, str]:
    """Returns (access_token, subdomain), cached for _TOKEN_TTL seconds."""
    with _lock:
        now = time.time()
        if not force and _cache["token"] and now - _cache["fetched_at"] < _TOKEN_TTL:
            return _cache["token"], _cache["subdomain"]
        host = os.environ["REPLIT_CONNECTORS_HOSTNAME"]
        r = requests.get(
            f"https://{host}/api/v2/connection?include_secrets=true&connector_names=zendesk",
            headers=_identity_header(), timeout=15)
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            raise RuntimeError("Zendesk connection not found — add it via Replit integrations")
        s = items[0]["settings"]
        token, subdomain = s["access_token"], s["subdomain"]
        if token != _cache["token"]:
            _cache["client"] = None  # token rolled over — rebuild Zenpy client
        _cache.update(token=token, subdomain=subdomain, fetched_at=now)
        return token, subdomain


def available() -> bool:
    """True when the Zendesk connection is bound to this Repl. Cached."""
    if not os.environ.get("REPLIT_CONNECTORS_HOSTNAME"):
        return False
    if _cache["token"]:
        return True
    try:
        _settings()
        return True
    except Exception as e:
        log.warning(f"Zendesk connector not available: {e}")
        return False


def get_client(force: bool = False):
    """Zenpy client authenticated with the connector's current OAuth token."""
    token, subdomain = _settings(force=force)
    with _lock:
        if _cache["client"] is None:
            from zenpy import Zenpy
            _cache["client"] = Zenpy(subdomain=subdomain, oauth_token=token)
        return _cache["client"]


def is_auth_error(exc: Exception) -> bool:
    """True when an exception looks like a 401/unauthorized from Zendesk."""
    msg = str(exc).lower()
    return "401" in msg or "unauthorized" in msg or "couldn't authenticate" in msg


def retry_client_on_auth_error():
    """Force a settings re-fetch and return a rebuilt client (401 recovery)."""
    log.info("[ZD connector] auth error — forcing token refresh and client rebuild")
    return get_client(force=True)
