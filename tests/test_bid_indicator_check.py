"""A verified BID must also agree with what the review says.

"i had told you to make sure that the review text and the bid given should
match in indicators too."

verify_bid returning a row proves the ID is real. It proves nothing about
whose booking it is, and for a Tier 1 BID nothing else ever asks: the pipeline
skips indicator extraction when the id already found the booking, so venue,
city, date and guest name are compared to nothing at all.

MOST OF THIS FILE IS ABOUT NOT FIRING. That is the design, not timidity: a
false flag sends an associate to re-match a correct booking, and enough of
those teach them to ignore the flag — at which point it is worse than absent.
So every ambiguity has to come back "unchecked", which is a third answer and
must never be read as agreement.
"""
from datetime import date, datetime

import pytest

from server.bid_indicator_check import check, trail_entry


def _b(**kw):
    b = {"id": "33118844", "experienceName": "", "date_of_visit": "",
         "primary_guest_name": ""}
    b.update(kw)
    return b


REF = date(2026, 8, 1)


# ── it fires on a clear contradiction ──────────────────────────────────────

def test_a_different_city_is_a_mismatch():
    got = check("The Eiffel Tower queue was two hours, awful",
                _b(experienceName="Colosseum Skip-the-Line Tour"),
                received_at=REF)
    assert got["state"] == "mismatch"
    assert "city" in got["contradictions"]


def test_a_different_venue_in_the_same_city_is_a_mismatch():
    """Same city, different product. The family check cannot see this one —
    both are tours in Rome — and it is the commonest wrong-reference shape."""
    got = check("We booked the Vatican Museums and were turned away",
                _b(experienceName="Colosseum Skip-the-Line Tour"),
                received_at=REF)
    assert got["state"] == "mismatch"
    assert "venue" in got["contradictions"]


def test_a_visit_date_months_from_the_stated_one_is_a_mismatch():
    got = check("We went on 3 March 2026 and nobody was there",
                _b(date_of_visit="2026-07-18"), received_at=REF)
    assert got["state"] == "mismatch"
    assert "date" in got["contradictions"]


def test_the_reason_names_both_sides():
    why = check("our day in Paris was ruined",
                _b(experienceName="Rome Colosseum Tour"), received_at=REF)["why"]
    assert "Paris" in why and "Rome" in why


# ── it stays quiet when it should ──────────────────────────────────────────

def test_agreement_is_a_match_not_a_silence():
    got = check("The Colosseum tour in Rome was a shambles",
                _b(experienceName="Colosseum Skip-the-Line Tour",
                   date_of_visit="2026-07-18"), received_at=REF)
    assert got["state"] == "match"
    assert "venue" in got["agreements"] and "city" in got["agreements"]


def test_a_review_naming_nothing_is_unchecked_not_a_match():
    got = check("Terrible. Never again.", _b(experienceName="Colosseum Tour"),
                received_at=REF)
    assert got["state"] == "unchecked"
    assert got["checked"] == 0
    assert "could be compared" in got["why"]


def test_a_booking_naming_nothing_is_unchecked():
    got = check("The Colosseum was closed", _b(experienceName=""), received_at=REF)
    assert got["state"] == "unchecked"


def test_two_cities_in_the_review_is_unchecked():
    """A transfer, or a multi-city trip. Deciding which city the review is
    ABOUT is exactly the judgement this check must not make."""
    got = check("We flew into Rome then took the train to Florence; the "
                "transfer never came", _b(experienceName="Florence Uffizi Tour"),
                received_at=REF)
    assert got["state"] != "mismatch"
    city = next(s for s in got["signals"] if s["name"] == "city")
    assert city["state"] == "unchecked"
    assert "more than one city" in city["why"]


def test_a_combo_booking_naming_two_venues_is_unchecked():
    got = check("The Colosseum part was fine",
                _b(experienceName="Vatican Museums + Colosseum Combo"),
                received_at=REF)
    venue = next(s for s in got["signals"] if s["name"] == "venue")
    assert venue["state"] == "unchecked"
    assert "more than one venue" in venue["why"]


def test_a_month_inside_the_tolerance_is_not_a_contradiction():
    """"we went in July" against a visit on 18 July. A month with no day is
    read as mid-month, so the arithmetic has to allow most of a month."""
    got = check("We went in July and the guide never showed",
                _b(date_of_visit="2026-07-30"), received_at=REF)
    d = next(s for s in got["signals"] if s["name"] == "date")
    assert d["state"] == "match"


@pytest.mark.parametrize("text", [
    "they said we may enter at any time before 17:30",
    "the march to the entrance took 20 minutes",
])
def test_a_month_word_used_as_an_ordinary_word_is_not_a_date(text):
    """"may" and "march" are English before they are months. Reading one as a
    date invents a visit in a different season and contradicts a correct
    booking — a false flag manufactured out of grammar."""
    got = check(text, _b(date_of_visit="2026-09-20"), received_at=REF)
    d = next(s for s in got["signals"] if s["name"] == "date")
    assert d["state"] == "unchecked", d
    assert got["state"] != "mismatch"


def test_a_month_word_pinned_to_a_day_is_a_date():
    """The guard above must not disable the signal entirely: "12 May" is a
    date by any reading."""
    got = check("we visited on 12 May", _b(date_of_visit="2026-09-20"),
                received_at=REF)
    d = next(s for s in got["signals"] if s["name"] == "date")
    assert d["state"] == "mismatch"


def test_two_dates_in_the_review_is_unchecked():
    got = check("booked on 2026-03-01 for 2026-03-04",
                _b(date_of_visit="2026-07-18"), received_at=REF)
    d = next(s for s in got["signals"] if s["name"] == "date")
    assert d["state"] == "unchecked"


def test_a_bare_month_with_no_reference_date_is_not_resolved():
    """Without the review's own date there is no honest year for "in July",
    and guessing one is a contradiction manufactured from nothing."""
    got = check("we went in July", _b(date_of_visit="2020-07-18"), received_at=None)
    d = next(s for s in got["signals"] if s["name"] == "date")
    assert d["state"] == "unchecked"


# ── the guest name never fires the flag on its own ─────────────────────────

def test_a_name_disagreement_alone_is_not_a_mismatch():
    """People book under a partner's name, a maiden name, a company name.
    Treating that as a wrong booking would flag a large share of correct
    matches, which is how a flag gets ignored."""
    got = check("the tour was awful", _b(primary_guest_name="Marta Ruiz"),
                author="David Green", received_at=REF)
    assert got["state"] != "mismatch"
    guest = next(s for s in got["signals"] if s["name"] == "guest")
    assert guest["state"] == "mismatch"
    assert "guest" in got["contradictions"]


def test_a_name_disagreement_is_still_reported_beside_a_real_one():
    got = check("our Paris trip", _b(experienceName="Rome Colosseum Tour",
                                     primary_guest_name="Marta Ruiz"),
                author="David Green", received_at=REF)
    assert got["state"] == "mismatch"
    assert "would not have been enough" in got["why"]


def test_a_matching_name_is_an_agreement():
    got = check("awful", _b(primary_guest_name="Fredrik Martin Olsen"),
                author="Fredrik Olsen", received_at=REF)
    assert got["state"] == "match"
    assert got["agreements"] == ["guest"]


def test_a_placeholder_author_is_not_compared():
    got = check("awful", _b(primary_guest_name="Marta Ruiz"),
                author="Customer", received_at=REF)
    guest = next(s for s in got["signals"] if s["name"] == "guest")
    assert guest["state"] == "unchecked"


def test_a_hashed_guest_name_is_not_compared():
    got = check("awful", _b(primary_guest_name="a3f9c1e77b2d4e8891ab"),
                author="David Green", received_at=REF)
    guest = next(s for s in got["signals"] if s["name"] == "guest")
    assert guest["state"] == "unchecked"


# ── shape guarantees ───────────────────────────────────────────────────────

def test_every_signal_is_reported_whatever_the_verdict():
    """A check that reports only its findings cannot be audited: an associate
    doubting the flag has to see what was compared and what was not."""
    got = check("x", _b(), received_at=REF)
    assert [s["name"] for s in got["signals"]] == ["venue", "city", "date", "guest"]
    assert all(s["state"] in ("match", "mismatch", "unchecked") for s in got["signals"])


def test_a_datetime_is_accepted_as_the_reference():
    """review.received_at is a datetime, not a date. Silently failing to
    resolve "in July" would turn the date signal off in production while every
    test using a date object passed."""
    got = check("we went in July", _b(date_of_visit="2026-07-18"),
                received_at=datetime(2026, 8, 1, 9, 30))
    d = next(s for s in got["signals"] if s["name"] == "date")
    assert d["state"] == "match"


def test_a_junk_booking_does_not_raise():
    for bad in (None, "", 5, []):
        assert check("x", bad, received_at=REF)["state"] == "unchecked"


# ── the trail says which of the three answers it got ───────────────────────

def test_the_trail_line_for_unchecked_says_nothing_was_compared():
    """The whole point. An unchecked result that writes no line, or writes a
    green tick, is a check that ran and found nothing looking exactly like a
    check that never ran."""
    e = trail_entry(check("nothing here", _b(), received_at=REF))
    assert e["mark"] == "warn"
    assert "could not be checked" in e["text"]
    assert "not agreement" in e["text"]


def test_the_trail_line_for_a_mismatch_says_it_did_not_unmatch():
    e = trail_entry(check("our Paris trip", _b(experienceName="Rome Colosseum Tour"),
                          received_at=REF))
    assert e["mark"] == "warn"
    assert "Not unmatched" in e["text"]


def test_the_trail_line_for_a_match_is_a_pass():
    e = trail_entry(check("the Colosseum in Rome",
                          _b(experienceName="Colosseum Tour"), received_at=REF))
    assert e["mark"] == "pass"
    assert "agree" in e["text"]


def test_the_three_trail_lines_are_distinguishable():
    texts = {trail_entry(check(t, b, received_at=REF))["text"] for t, b in [
        ("nothing", _b()),
        ("our Paris trip", _b(experienceName="Rome Colosseum Tour")),
        ("the Colosseum in Rome", _b(experienceName="Colosseum Tour")),
    ]}
    assert len(texts) == 3


def test_a_name_that_disagrees_alone_does_not_report_as_nothing_compared():
    """The inversion this whole file exists to avoid. The guest name WAS
    compared and came back negative; saying "nothing could be compared" turns a
    negative result into a claim that no check ran.

    THE EXAMPLE MOVED, THE GUARANTEE DID NOT. This used to be written against
    'Customer Ops Lead', which is not a guest name at all — it is the label our
    own systems put on a desk-made booking, so comparing it to a reviewer's
    name produced a disagreement that means nothing. That case is now its own
    branch and its own file (test_internal_booking_names.py), and it reports
    'unchecked' with NOTHING contradicted, which is why this test had to stop
    using it as its example.

    'Fredrik Andersson' is the case this test is actually about: a real person
    who is not the reviewer. Common enough that it must not raise the flag —
    bookings are legitimately made under a partner's or a colleague's name —
    and specific enough that the sentence has to be true about it.
    """
    got = check("terrible experience", _b(primary_guest_name="Fredrik Andersson"),
                author="Ioan Popescu", received_at=REF)
    assert got["state"] == "unchecked"
    assert got["contradictions"] == ["guest"]
    assert "nothing in this review could be compared" not in got["why"], got["why"]
    assert "Ioan" in got["why"] and "Fredrik Andersson" in got["why"]
    assert "not on its own evidence of a wrong booking" in got["why"]


def test_an_internal_label_and_a_real_disagreement_are_told_apart():
    """The two must not collapse into each other. A real name that disagrees
    is a comparison that RAN and came back negative; an internal label is a
    comparison that could not run, because there is no guest name on the
    booking to run it against. Reporting the second as the first invents a
    disagreement with a person who does not exist."""
    real = check("terrible", _b(primary_guest_name="Fredrik Andersson"),
                 author="Ioan Popescu", received_at=REF)
    label = check("terrible", _b(primary_guest_name="Customer Ops Lead"),
                  author="Ioan Popescu", received_at=REF)
    assert real["contradictions"] == ["guest"]
    assert label["contradictions"] == [], label
    assert real["why"] != label["why"]


def test_the_trail_line_carries_that_distinction_too():
    a = trail_entry(check("terrible", _b(primary_guest_name="Fredrik Andersson"),
                          author="Ioan Popescu", received_at=REF))["text"]
    b = trail_entry(check("terrible", _b(), received_at=REF))["text"]
    assert a != b, ("a name that disagreed and a booking with nothing to "
                    "compare produce the same sentence")
