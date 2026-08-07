"""A sent review that was never matched was headed "Direct match".

`classify()` in server/tiers.py returns FIVE buckets and puts `sent` FIRST, on
purpose:

    "a sent review is done; it must not also appear under a working tab"

That is right for filing and says nothing about the booking. `_matchTitle`
knew four of the five values — candidates, processing, untraceable, and
"anything else means identified" — so a sent review fell past every test and
out of the bottom into the Tier 1 vocabulary. The card read:

    T2   Direct match

on a review with no confirmed booking, because `_bidFromReview` found a
reference number in the review text. A review matched exactly and a review
never matched at all rendered the identical title, and the tier badge beside
it contradicted the words.

CLIENT-SIDE JAVASCRIPT, which has no test harness here — CLAUDE.md's stated
exception. These drive the real functions through the browser rather than
reading the source, so an unreachable branch cannot pass them.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _title(page, **row):
    """Call the page's own `_matchTitle` on an api row."""
    base = {"status": "sent", "bucket": "sent", "match_tier": 2,
            "has_draft": True, "has_booking": False, "has_candidates": False,
            "reference_number": "48065892", "unverified": False,
            "candidate_state": False, "confirmed": False}
    base.update(row)
    return page.evaluate(
        "(r) => _matchTitle(r, r.match_tier, r.candidate_state)", base)


# ── the reported card ───────────────────────────────────────────────────────

def test_a_sent_review_with_no_booking_is_not_called_a_direct_match(page):
    """The exact combination on screen: sent, tier 2, nothing confirmed, and a
    reference number in the review text."""
    got = _title(page, has_booking=False)
    assert got != "Direct match", (
        "a review nobody matched is still headed with the strongest label in "
        "the vocabulary")
    assert "without a confirmed booking" in got, got


def test_a_sent_review_does_not_borrow_the_zendesk_wording_either(page):
    """"Matched from Zendesk" is the same claim with a different source. The
    fallthrough reached both branches, so fixing only the first would move the
    bug rather than remove it."""
    got = _title(page, has_booking=False, reference_number="")
    assert "Matched from Zendesk" not in got, got
    assert "without a confirmed booking" in got, got


def test_a_sent_review_that_WAS_matched_still_says_so(page):
    """The inverse, and the reason this is not simply "Sent". A confirmed
    booking is a real fact about the review and hiding it would make every
    sent review look unmatched — the same collapse, pointing the other way."""
    got = _title(page, has_booking=True)
    assert got == "Direct match", got


def test_a_sent_review_matched_without_a_reference_number_says_where_from(page):
    got = _title(page, has_booking=True, reference_number="", bid_source="")
    assert got == "Matched from Zendesk", got


def test_a_sent_review_is_never_asked_to_be_confirmed(page):
    """A shortlist can still be sitting on a closed review. "associate to
    confirm" asks for an action on something that is finished, so the sent
    card must not reach the candidate wording."""
    got = _title(page, has_booking=False, has_candidates=True,
                 candidate_state=True)
    # The candidate wording, not the substring "confirm" — "Sent without a
    # confirmed booking" legitimately contains it, and asserting on the
    # substring tested the spelling rather than the claim.
    assert "associate to confirm" not in got, got
    assert "Possible matches" not in got, got
    assert "without a confirmed booking" in got, got


# ── the buckets that were already handled stay handled ─────────────────────

@pytest.mark.parametrize("bucket,expect", [
    ("candidates",  "Possible matches"),
    ("untraceable", "Untraceable"),
])
def test_the_working_buckets_are_unchanged(page, bucket, expect):
    got = _title(page, status="draft", bucket=bucket)
    assert expect in got, (bucket, got)


def test_a_running_review_still_says_nothing_has_been_searched(page):
    got = _title(page, status="draft", bucket="processing")
    assert "searched" in got.lower() or "running" in got.lower(), got


def test_an_unverified_bid_still_says_it_is_unverified(page):
    """This branch sat below the three bucket tests and above the fallthrough,
    so it is the one most easily lost by reordering them."""
    got = _title(page, status="draft", bucket="identified", unverified=True)
    assert "not verified in BigQuery" in got, got


# ── the split that makes it work ───────────────────────────────────────────

def test_the_match_state_is_computable_without_the_sent_answer(page):
    """`_matchBucket` is the half of the old `_bucketFallback` that answers
    "what did the match find". The sent short-circuit lives above it now, so
    the title can ask the first question while the tab keeps asking the
    second."""
    got = page.evaluate("""() => ({
      booking:  _matchBucket({has_draft: true, has_booking: true}, 2, false),
      shortlist:_matchBucket({has_draft: true, has_candidates: true}, 2, false),
      nothing:  _matchBucket({has_draft: true}, 2, false),
      norun:    _matchBucket({has_draft: false}, null, false),
    })""")
    assert got == {"booking": "identified", "shortlist": "candidates",
                   "nothing": "untraceable", "norun": "processing"}, got


def test_the_tab_still_files_a_sent_review_under_sent(page):
    """The fix must not move sent reviews out of their tab — that is what the
    precedence in classify() exists for, and the two questions are answered by
    two different functions on purpose."""
    got = page.evaluate(
        "() => _bucketFallback({status: 'sent', has_draft: true, has_booking: true}, 1, false)")
    assert got == "sent", got
