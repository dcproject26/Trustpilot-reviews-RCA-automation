"""
BigQuery via the Replit Google BigQuery connection (integration).

Auth is handled by the Replit connectors service — no service-account key.
Tokens are fetched from the connectors API and cached briefly; the connector
refreshes them upstream. Exposes a minimal google-cloud-bigquery-compatible
surface (Client / QueryJobConfig / ScalarQueryParameter) so the existing
service code in bigquery.py / bigquery_patch.py works unchanged.

Read-only scope. Keep queries column-projected and date-scoped — BigQuery
bills per byte scanned.
"""
import os
import time
import logging
from types import SimpleNamespace

import requests

log = logging.getLogger(__name__)

_BQ_API = "https://bigquery.googleapis.com/bigquery/v2"

_cache: dict = {"token": None, "project": None, "fetched_at": 0.0}
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
    """Returns (access_token, project_id), cached for _TOKEN_TTL seconds."""
    now = time.time()
    if not force and _cache["token"] and now - _cache["fetched_at"] < _TOKEN_TTL:
        return _cache["token"], _cache["project"]
    host = os.environ["REPLIT_CONNECTORS_HOSTNAME"]
    r = requests.get(
        f"https://{host}/api/v2/connection?include_secrets=true&connector_names=bigquery",
        headers=_identity_header(), timeout=15)
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        raise RuntimeError("BigQuery connection not found — add it via Replit integrations")
    s = items[0]["settings"]
    _cache.update(token=s["access_token"], project=s["project_id"], fetched_at=now)
    return _cache["token"], _cache["project"]


def available() -> bool:
    """True when the BigQuery connection is bound to this Repl. Cached."""
    if not os.environ.get("REPLIT_CONNECTORS_HOSTNAME"):
        return False
    if _cache["token"]:
        return True
    try:
        _settings()
        return True
    except Exception as e:
        log.warning(f"BigQuery connector not available: {e}")
        return False


# ── google-cloud-bigquery-compatible shims ──────────────────────────────────

class ScalarQueryParameter:
    def __init__(self, name: str, type_: str, value):
        self.name, self.type_, self.value = name, type_, value


class QueryJobConfig:
    def __init__(self, query_parameters: list | None = None):
        self.query_parameters = query_parameters or []


class _QueryJob:
    def __init__(self, rows: list):
        self._rows = rows

    def result(self):
        return self._rows


def _convert(value, bq_type: str):
    if value is None:
        return None
    if bq_type in ("INTEGER", "INT64"):
        return int(value)
    if bq_type in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
        return float(value)
    if bq_type in ("BOOLEAN", "BOOL"):
        return value in (True, "true", "True")
    return value  # strings, dates, timestamps stay as strings


class Client:
    """Minimal read-only client using the synchronous jobs.query REST endpoint."""

    def query(self, sql: str, job_config: QueryJobConfig | None = None) -> _QueryJob:
        token, project = _settings()
        body: dict = {"query": sql, "useLegacySql": False, "timeoutMs": 60000}
        params = (job_config.query_parameters if job_config else []) or []
        if params:
            body["parameterMode"] = "NAMED"
            body["queryParameters"] = [{
                "name": p.name,
                "parameterType": {"type": p.type_},
                "parameterValue": {"value": str(p.value)},
            } for p in params]

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        r = requests.post(f"{_BQ_API}/projects/{project}/queries",
                          headers=headers, json=body, timeout=90)
        if r.status_code == 401:  # stale token — force refresh once
            token, project = _settings(force=True)
            headers["Authorization"] = f"Bearer {token}"
            r = requests.post(f"{_BQ_API}/projects/{project}/queries",
                              headers=headers, json=body, timeout=90)
        r.raise_for_status()
        j = r.json()

        fields = [(f["name"], f["type"]) for f in j.get("schema", {}).get("fields", [])]
        rows = [
            SimpleNamespace(**{
                name: _convert(cell.get("v"), typ)
                for (name, typ), cell in zip(fields, row.get("f", []))
            })
            for row in j.get("rows", [])
        ]

        # Follow pagination if the first page didn't finish / fit
        job_ref = j.get("jobReference", {})
        page_token = j.get("pageToken")
        while page_token and job_ref.get("jobId"):
            r = requests.get(
                f"{_BQ_API}/projects/{project}/queries/{job_ref['jobId']}",
                headers=headers,
                params={"pageToken": page_token,
                        "location": job_ref.get("location", "")},
                timeout=90)
            r.raise_for_status()
            j = r.json()
            rows += [
                SimpleNamespace(**{
                    name: _convert(cell.get("v"), typ)
                    for (name, typ), cell in zip(fields, row.get("f", []))
                })
                for row in j.get("rows", [])
            ]
            page_token = j.get("pageToken")

        return _QueryJob(rows)
