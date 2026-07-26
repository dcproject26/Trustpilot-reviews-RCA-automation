"""
BQ-backed venue hint → TGID resolver.
Returns the union of experience_ids matching any of the hint strings.
"""
import logging
from server.services import bq_connector as bq

log = logging.getLogger(__name__)

_TABLES = [
    "headout-analytics.analytics_reporting.dim_experiences",
    "headout-analytics.shivam_reporting.dim_experiences",
]
_FALLBACK_SQL = """
    SELECT DISTINCT experience_id
    FROM `headout-analytics.analytics_reporting.fct_bookings`
    WHERE LOWER(experience_name) LIKE CONCAT('%', @hint, '%')
    LIMIT 100
"""
_WORKING_TABLE: str | None = None


def _probe_table(table: str, hint: str) -> list[dict] | None:
    try:
        rows = bq.run_query(
            f"SELECT DISTINCT experience_id FROM `{table}` "
            f"WHERE LOWER(experience_name) LIKE CONCAT('%', @hint, '%') LIMIT 100",
            params={"hint": hint},
        )
        return rows
    except Exception:
        return None


async def resolve(venue_hints: list[str] | None) -> list[int] | None:
    """Resolve venue hints → sorted union of TGIDs. None if nothing resolved."""
    global _WORKING_TABLE
    if not venue_hints:
        return None
    all_tgids: set[int] = set()
    for raw_hint in venue_hints:
        hint = (raw_hint or "").lower().strip()
        if not hint:
            continue
        rows = None
        if _WORKING_TABLE:
            try:
                rows = bq.run_query(
                    f"SELECT DISTINCT experience_id FROM `{_WORKING_TABLE}` "
                    f"WHERE LOWER(experience_name) LIKE CONCAT('%', @hint, '%') LIMIT 100",
                    params={"hint": hint},
                )
            except Exception as e:
                log.warning(f"venue_resolver: cached table {_WORKING_TABLE} failed: {e}")
                _WORKING_TABLE = None
                rows = None

        if rows is None and _WORKING_TABLE is None:
            for tbl in _TABLES:
                rows = _probe_table(tbl, hint)
                if rows is not None:
                    _WORKING_TABLE = tbl
                    log.info(f"venue_resolver: dim_experiences found at {tbl}")
                    break
            if rows is None:
                try:
                    rows = bq.run_query(_FALLBACK_SQL, params={"hint": hint})
                    _WORKING_TABLE = "fallback:fct_bookings"
                    log.info("venue_resolver: using fct_bookings fallback for dim_experiences")
                except Exception as e2:
                    log.warning(f"venue_resolver: fallback also failed for '{hint}': {e2}")
                    rows = []

        for r in rows or []:
            eid = r.get("experience_id")
            if eid is not None:
                try:
                    all_tgids.add(int(eid))
                except (TypeError, ValueError):
                    pass

    return sorted(all_tgids) if all_tgids else None
