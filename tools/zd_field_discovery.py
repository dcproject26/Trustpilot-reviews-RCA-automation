"""
Dump a Zendesk ticket's custom fields so the booking-id field can be confirmed
against the live instance rather than assumed from config.

Usage:  python3 tools/zd_field_discovery.py 33979875
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services.zendesk import _get_client, booking_id_from_ticket, _BOOKING_FIELD
from server.config import ZENDESK_BOOKING_FIELD_ID, ZENDESK_TGID_FIELD, ZENDESK_TID_FIELD


def main(ticket_id):
    z = _get_client()
    if z is None:
        print("Zendesk not live — check credentials / MOCK_MODE")
        return
    t = z.tickets(id=int(ticket_id))
    print(f"ZD-{ticket_id}  subject: {getattr(t, 'subject', '')}")
    print(f"requester_id: {getattr(t, 'requester_id', None)}")
    print()
    print(f"configured booking field : {ZENDESK_BOOKING_FIELD_ID}  (parsed: {_BOOKING_FIELD})")
    print(f"configured tgid field    : {ZENDESK_TGID_FIELD}")
    print(f"configured tid field     : {ZENDESK_TID_FIELD}")
    print()
    print("ALL non-empty custom fields on this ticket:")
    for f in (getattr(t, "custom_fields", None) or []):
        fid = f.get("id") if isinstance(f, dict) else getattr(f, "id", None)
        val = f.get("value") if isinstance(f, dict) else getattr(f, "value", None)
        if val in ("", None):
            continue
        mark = ""
        if fid == _BOOKING_FIELD:
            mark = "   <-- configured BOOKING field"
        elif str(val).strip().isdigit() and 7 <= len(str(val).strip()) <= 12:
            mark = "   <-- looks like a booking id"
        print(f"  {fid}: {val!r}{mark}")
    print()
    print(f"booking_id_from_ticket() -> {booking_id_from_ticket(t)}")
    print()
    print("If the configured field is empty but another field holds the booking id,")
    print("set ZENDESK_BOOKING_FIELD_ID to that field id in Replit Secrets.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
