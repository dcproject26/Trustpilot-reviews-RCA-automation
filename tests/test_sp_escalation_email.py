"""The supply-partner escalation email: read from the booking record first,
the warehouse ESCALATIONS contact next, and - the failure this guards - never
report a blank we did not actually look for.

Every test drives the real functions; none asserts a string exists in source.
"""
from server.ticket_notes import (
    booking_record_escalation_email,
    resolve_sp_escalation_email,
)

# A booking-info dump shaped like the real one on booking 32908218: the
# intimation email is populated, the escalation email is blank, and the next
# label follows immediately.
_BLANK = ("Booking Intimation Email:palaceofculturewarsaw@veritt.com  "
          "Booking Intimation Number:  Booking Escalation Email:  "
          "Booking Escalation Number:   **Customer Details**")
_PRESENT = ("Booking Intimation Email:intim@vendor.com  "
            "Booking Escalation Email: escalations@vendor.com  "
            "Booking Escalation Number: +48 22 000")
_NO_FIELD = "**Booking Details** Booking_Id: 123  Status: CONFIRMED"


# ── the parser: four states a bare "" cannot tell apart ────────────────────

def test_a_populated_escalation_field_is_read():
    email, state = booking_record_escalation_email([_PRESENT])
    assert (email, state) == ("escalations@vendor.com", "present")


def test_a_blank_escalation_field_does_not_swallow_the_next_label():
    # The greedy version read "**Customer" as the address here.
    email, state = booking_record_escalation_email([_BLANK])
    assert email == ""
    assert state == "blank"


def test_the_intimation_email_is_not_taken_for_the_escalation_one():
    # Only the intimation email is present; escalation is a different address.
    email, _ = booking_record_escalation_email([_BLANK])
    assert email != "palaceofculturewarsaw@veritt.com"


def test_a_record_with_no_such_field_is_absent_not_blank():
    email, state = booking_record_escalation_email([_NO_FIELD])
    assert (email, state) == ("", "absent")


def test_no_booking_text_at_all_is_its_own_state():
    assert booking_record_escalation_email([]) == ("", "no_text")
    assert booking_record_escalation_email(None) == ("", "no_text")


def test_the_field_is_found_even_when_it_is_not_the_first_row():
    email, state = booking_record_escalation_email(["unrelated row", _PRESENT])
    assert (email, state) == ("escalations@vendor.com", "present")


# ── the resolver: precedence + the not_fetched/none_found distinction ───────

def test_the_booking_record_field_wins_over_the_warehouse():
    b = {"id": "1", "escalationEmail": "vendor-row@bq.com", "contactCount": 3}
    resolve_sp_escalation_email(b, [_PRESENT])
    assert b["escalationEmail"] == "escalations@vendor.com"
    assert b["escalationEmailSource"] == "booking_record"


def test_the_warehouse_contact_is_the_fallback_when_the_record_is_blank():
    b = {"id": "1", "escalationEmail": "vendor-row@bq.com", "contactCount": 3}
    resolve_sp_escalation_email(b, [_BLANK])
    assert b["escalationEmail"] == "vendor-row@bq.com"
    assert b["escalationEmailSource"] == "vendor_escalations"


def test_both_consulted_and_empty_is_none_found():
    # Warehouse ran (keys present) and found none; record read and blank.
    b = {"id": "1", "escalationEmail": "", "contactCount": 5}
    resolve_sp_escalation_email(b, [_BLANK])
    assert b["escalationEmail"] == ""
    assert b["escalationEmailSource"] == "none_found"


def test_enrichment_that_never_ran_is_not_fetched_not_blank():
    # THE BUG. david's booking carried only id/vendorName/fulfilmentType: the
    # warehouse enrichment never ran, so escalationEmail must read as a gap on
    # OUR side, never as "the SP has no escalation email".
    b = {"id": "32908218", "vendorName": "Palace of Culture and Science_NP"}
    resolve_sp_escalation_email(b, [_BLANK])
    assert b["escalationEmail"] == ""
    assert b["escalationEmailSource"] == "not_fetched"


def test_no_record_text_and_no_warehouse_is_not_fetched():
    b = {"id": "1"}
    resolve_sp_escalation_email(b, [])
    assert b["escalationEmailSource"] == "not_fetched"


def test_a_record_hit_still_wins_when_the_warehouse_never_ran():
    b = {"id": "1"}  # no warehouse enrichment
    resolve_sp_escalation_email(b, [_PRESENT])
    assert b["escalationEmail"] == "escalations@vendor.com"
    assert b["escalationEmailSource"] == "booking_record"


def test_the_resolver_is_a_noop_on_a_non_dict():
    # Must not raise; there is nothing to set.
    resolve_sp_escalation_email(None, [_PRESENT])
    resolve_sp_escalation_email("not a booking", [_PRESENT])


# ── the model-facing render: not_fetched must not read as "the SP has none" ──

from server.prompts import _readable_booking


def test_not_fetched_becomes_a_phrase_that_forbids_the_blank_claim():
    out = _readable_booking({"id": "1", "escalationEmail": "",
                             "escalationEmailSource": "not_fetched"})
    # The raw enum is not handed to the model.
    assert "escalationEmailSource" not in out
    status = out["escalation_email_status"].lower()
    assert "not retrieved" in status
    # It explicitly tells the model this is our gap, not a fact about the SP.
    assert "not" in status and "evidence" in status


def test_none_found_says_the_sp_genuinely_has_none():
    out = _readable_booking({"id": "1", "escalationEmail": "",
                             "escalationEmailSource": "none_found"})
    assert "escalationEmailSource" not in out
    assert "no escalation email" in out["escalation_email_status"].lower()


def test_a_present_address_is_left_alone_and_gets_no_status():
    out = _readable_booking({"id": "1", "escalationEmail": "esc@vendor.com",
                             "escalationEmailSource": "vendor_escalations"})
    assert out["escalationEmail"] == "esc@vendor.com"
    assert "escalation_email_status" not in out
    assert "escalationEmailSource" not in out


def test_a_booking_without_the_source_key_is_untouched():
    # Backward compatibility: drafts made before the resolver ran carry no
    # source, and must not sprout a status that claims we looked.
    out = _readable_booking({"id": "1", "escalationEmail": ""})
    assert "escalation_email_status" not in out
