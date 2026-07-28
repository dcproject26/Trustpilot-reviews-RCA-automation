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
import asyncio
import os
import time
import logging
from types import SimpleNamespace

import requests

from server.config import MOCK_MODE

log = logging.getLogger(__name__)

_BQ_SEM = asyncio.Semaphore(5)

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


class ArrayQueryParameter:
    """Named parameter whose value is a homogeneous array (for UNNEST)."""
    def __init__(self, name: str, array_type: str, values: list):
        self.name, self.array_type, self.values = name, array_type, values


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


def _wire_params(params: list) -> list:
    """Our parameter shims -> the REST API's queryParameters wire format."""
    out = []
    for p in params:
        if isinstance(p, ArrayQueryParameter):
            out.append({
                "name": p.name,
                "parameterType": {
                    "type": "ARRAY",
                    "arrayType": {"type": p.array_type},
                },
                "parameterValue": {
                    "arrayValues": [{"value": str(v)} for v in p.values],
                },
            })
        else:
            out.append({
                "name": p.name,
                "parameterType": {"type": p.type_},
                "parameterValue": {"value": str(p.value)},
            })
    return out


class Client:
    """Minimal read-only client using the synchronous jobs.query REST endpoint."""

    def query(self, sql: str, job_config: QueryJobConfig | None = None) -> _QueryJob:
        token, project = _settings()
        body: dict = {"query": sql, "useLegacySql": False, "timeoutMs": 60000}
        params = (job_config.query_parameters if job_config else []) or []
        if params:
            body["parameterMode"] = "NAMED"
            body["queryParameters"] = _wire_params(params)

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


async def run_query_async(sql: str, params: dict | None = None) -> list[dict]:
    """
    Async wrapper around run_query with _BQ_SEM concurrency guard (cap 5).
    MOCK_MODE: returns [] immediately without acquiring the semaphore.
    """
    if MOCK_MODE:
        return []
    t0 = time.time()
    async with _BQ_SEM:
        waited = time.time() - t0
        if waited > 2.0:
            log.warning(f"[bq_connector] wait time exceeded 2s: {waited:.1f}s")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, run_query, sql, params)


def run_query(sql: str, params: dict | None = None) -> list[dict]:
    """
    Convenience wrapper: runs a named-param query and returns rows as plain dicts.
    `params` maps param name → (type_str, value) tuple, OR just value for STRING scalars.
    Array params: pass value as a list, type_str as 'INT64' etc. (auto-detected).
    """
    qp = _shim_params(params)
    job = Client().query(sql, QueryJobConfig(query_parameters=qp) if qp else None)
    return [vars(row) for row in job.result()]


def _shim_params(params: dict | None) -> list:
    qp = []
    for name, spec in (params or {}).items():
        if isinstance(spec, tuple):
            type_str, val = spec
        else:
            type_str, val = "STRING", spec
        if isinstance(val, list):
            qp.append(ArrayQueryParameter(name, type_str, val))
        else:
            qp.append(ScalarQueryParameter(name, type_str, val))
    return qp


def dry_run(sql: str, params: dict | None = None) -> dict:
    """
    Validate a query against the live schema without running it.

    BigQuery type-checks a dry run in full: every table, every column, and
    every operand type. It scans nothing and costs nothing, which makes it the
    cheap way to answer the only question that matters about a query written
    from a LookML paste - do these columns exist, and are they the types the
    SQL assumes?

    Returns {"ok": True, "bytes": int} or {"ok": False, "error": str}. It does
    not raise: the caller is usually checking a batch of queries and wants
    every result, not the first failure.
    """
    if MOCK_MODE:
        return {"ok": False, "error": "MOCK_MODE - no BigQuery connection"}
    try:
        token, project = _settings()
    except Exception as e:
        return {"ok": False, "error": f"no BigQuery connection: {e}"}

    body: dict = {"query": sql, "useLegacySql": False, "dryRun": True}
    qp = _shim_params(params)
    if qp:
        body["parameterMode"] = "NAMED"
        body["queryParameters"] = _wire_params(qp)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(f"{_BQ_API}/projects/{project}/queries",
                      headers=headers, json=body, timeout=60)
    if r.status_code == 401:
        token, project = _settings(force=True)
        headers["Authorization"] = f"Bearer {token}"
        r = requests.post(f"{_BQ_API}/projects/{project}/queries",
                          headers=headers, json=body, timeout=60)

    j = {}
    try:
        j = r.json()
    except ValueError:
        pass
    if r.status_code >= 400:
        err = (j.get("error") or {}).get("message") or r.text[:400]
        return {"ok": False, "error": err}
    return {"ok": True, "bytes": int(j.get("totalBytesProcessed") or 0)}


def column_types(table: str, columns: list[str] | None = None) -> dict:
    """
    Column name -> BigQuery type for one table, straight off INFORMATION_SCHEMA.

    `table` is the fully-qualified name. Used to report WHY a dry run failed -
    "tags is ARRAY<STRING>, not STRING" is actionable where BigQuery's own
    "No matching signature for operator IN" is not.
    """
    parts = table.replace("`", "").split(".")
    if len(parts) != 3:
        return {}
    project, dataset, name = parts
    sql = (f"SELECT column_name, data_type "
           f"FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS` "
           f"WHERE table_name = @t")
    try:
        rows = run_query(sql, {"t": name})
    except Exception as e:
        log.warning(f"[bq_connector] column_types({table}) failed: {e}")
        return {}
    got = {r["column_name"]: r["data_type"] for r in rows}
    if columns:
        return {c: got.get(c) for c in columns}
    return got
