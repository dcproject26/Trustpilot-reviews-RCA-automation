"""
REPLACES existing server/services/dss.py

DSS Retool webhook. In mock mode (or when DSS_WEBHOOK_URL is empty), returns
a policy recommendation keyed by L1/L2 from the taxonomy.

STAKEHOLDER INPUT NEEDED — DSS Owner:
  - Confirm the webhook request payload shape (currently sends booking + L1 + L2)
  - Confirm the response payload shape (currently expects: policy, compensation,
    coverage, action, escalateTo, policyId)
"""
import logging
import httpx
from server.config import DSS_WEBHOOK_URL, is_live
from server.services.mock_data import MOCK_DSS

log = logging.getLogger(__name__)


# Mock policy lookup by L1/L2 — placeholder until real DSS taxonomy provided.
_MOCK_POLICY_LOOKUP = {
    ("SP issue", "Venue closure"): {
        "policy": "SP venue closure",
        "compensation": "Refund + 25% HOC",
        "coverage": "Yes — SP-side failure with no advance notice",
        "action": "Full refund + 25% HOC credit.",
        "escalateTo": "[Biz handle placeholder]",
    },
    ("SP issue", "SP system outage"): {
        "policy": "SP outage",
        "compensation": "Refund + 25% HOC",
        "coverage": "Yes",
        "action": "Full refund + 25% HOC credit.",
        "escalateTo": "[Biz handle placeholder]",
    },
    ("Customer error", "Misunderstood inclusions (upsell not selected)"): {
        "policy": "Customer error",
        "compensation": "No comp · goodwill optional",
        "coverage": "No — guest error",
        "action": "No compensation. Optional goodwill HOC up to €10 at CE discretion.",
        "escalateTo": "",
    },
}


async def get_recommendation(booking: dict, review_id: str = None,
                              l1: str = "", l2: str = "") -> dict:
    if not is_live("dss"):
        # Fallback: use L1/L2 lookup first, then legacy MOCK_DSS by review_id
        rec = _MOCK_POLICY_LOOKUP.get((l1, l2))
        if rec:
            return {**rec, "l1": l1, "l2": l2}
        return MOCK_DSS.get(review_id, {})

    payload = {
        "booking_id": booking.get("id"),
        "tgid":       booking.get("tgid"),
        "tid":        booking.get("tid"),
        "vid":        booking.get("vid"),
        "amount":     booking.get("amount"),
        "l1":         l1,
        "l2":         l2,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(DSS_WEBHOOK_URL, json=payload)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.exception(f"DSS call failed: {e}")
        return {}
