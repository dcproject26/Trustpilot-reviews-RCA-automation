import logging, httpx
from server.config import is_live, DSS_WEBHOOK_URL
from server.services.mock_data import MOCK_DSS, MOCK_BOOKINGS

log = logging.getLogger(__name__)


async def get_recommendation(booking: dict, review_id: str = None) -> dict:
    if not is_live("dss"):
        if review_id and review_id in MOCK_DSS:
            return MOCK_DSS[review_id]
        return {"issueType": "Unknown", "compensation": "TBD",
                "action": "Check DSS manually.", "escalateTo": "CX Lead"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.post(DSS_WEBHOOK_URL, json={
                "booking_id": booking.get("id"),
                "vid":  booking.get("vid"),
                "tgid": booking.get("tgid"),
                "amount": booking.get("amount"),
            })
            res.raise_for_status()
            return res.json()
    except Exception as e:
        log.exception(f"DSS call failed: {e}")
        return {"issueType": "Unknown", "compensation": "TBD",
                "action": "DSS unavailable — check manually.", "escalateTo": "CX Lead"}
