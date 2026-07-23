"""
RCA Checklist service — baked-in data (Brief v7.1).

get_checklist() returns the full checklist dict from server/checklist.py.
No network fetch, no TTL cache, no Google Sheets dependency.
Function is async for pipeline compatibility.
"""
from typing import Optional
from server.checklist import GENERAL_GUIDELINES, CE_ERROR_CHECKS, RO_ERROR_CHECKS, SCENARIO_CHECKS


async def get_checklist(l1: Optional[str] = None, l2: Optional[str] = None) -> dict:
    """
    Returns the full baked-in checklist dict.
    l1 / l2 are accepted for signature compatibility but not used for filtering —
    the prompt selects applicable scenarios.
    """
    return {
        "general":   GENERAL_GUIDELINES,
        "ce":        CE_ERROR_CHECKS,
        "ro":        RO_ERROR_CHECKS,
        "scenarios": SCENARIO_CHECKS,
    }


async def warm_cache() -> None:
    """No-op — baked-in data needs no warming."""
    pass
