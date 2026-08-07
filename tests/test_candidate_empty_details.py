"""A candidate with no booking details says WHY, and never says "loading".

THE MESSAGE THIS REPLACES. "Booking details load on confirm" rendered whenever
a candidate had no experience, date or vendor. Nothing was loading: no request
was in flight and none would be until Confirm was pressed. A reader waited for
something that was never coming — "we found nothing" rendered as "we have not
finished yet", which is the failure this codebase opens with, in the UI.

WHAT CHANGED SINCE. The message used to be inferred from the PATH:
`indicator_shortlist` meant "this path never queries the warehouse". That
stopped being true when the shortlist started resolving its ids through
`verify_bid` — so a message keyed on the path became a message that had gone
stale silently while still reading as an explanation.

It is now keyed on `details_lookup`, which the server sets PER BOOKING, and
there are four cases rather than two:

  found   the warehouse answered and the row carries nothing — a gap in our
          data, and a reader should not go hunting in Zendesk for it
  absent  the warehouse does not have this id at all — the Zendesk ticket is
          everything we have, and that is a dead end an associate can act on
  failed  the lookup did not complete — nothing was ruled out, and the answer
          is a re-run, not a dead end
  (unset) a candidate from a path that records no answer — says it does not
          know, rather than picking one of the three above

`absent` and `failed` are the pair that must never share a sentence: one sends
someone to ask the guest for a reference, the other sends them to press
Re-run.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _inject(page, narrowing_path, details_lookup=None):
    """Drive the REAL ingest remap, not a hand-built candidate object.

    The remap builds a fixed shape and silently drops any field not named in
    it, so a message branching on a dropped field can never fire however
    correct the branch is. That is not hypothetical: `narrowing_path` was
    missing from it once and the branch keyed on it was dead.

    This reproduces the remap rather than calling it — it lives inside the
    draft-ingest function and is not separately reachable — which means THIS
    HELPER CANNOT CATCH THE REMAP DROPPING A FIELD. That is what
    `test_the_ingest_remap_names_the_field` at the bottom is for.
    """
    return page.evaluate("""([path, lookup]) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__cKeep === undefined)
        window.__cKeep = {t: r.type, cs: r.candidateState, cl: r.candidatesList};
      r.type = 'candidates';
      r.candidateState = true;
      // The server payload shape, through the client's own remap.
      const draft = {candidates_list: [
        {id: '32885089', narrowing_path: path, matchReasons: ['name'],
         details_lookup: lookup || undefined,
         score_venue: 0, score_date: 0, venue_signal: false}]};
      r.candidatesList = draft.candidates_list.map(c => ({
        bid: c.id || c.bid,
        score: c.score != null ? c.score : null,
        scoreVenue: c.score_venue != null ? c.score_venue : null,
        scoreDate: c.score_date != null ? c.score_date : null,
        venueSignal: c.venue_signal === true,
        matchReasons: c.matchReasons || c.match_reasons || [],
        experience: c.experience || c.experienceName || '',
        tgid: c.tgid || '', tid: c.tid || '',
        vendorName: c.vendorName || c.partner || '',
        experienceDate: c.experienceDate || c.visitDate || '',
        creationDate: c.creationDate || c.bookedOn || '',
        status: c.status || '', leadTime: c.leadTime || '',
        guestName: c.primary_guest_name || c.guestName || '',
        contactCount: c.contact_count || 0, contactTags: c.contact_tags || '',
        narrowing_path: c.narrowing_path || '',
        detailsLookup: c.details_lookup || '',
      }));
      renderReviewCol();
      const el = document.querySelector('.candidate-meta');
      return el ? el.textContent.trim() : '';
    }""", [narrowing_path, details_lookup])


def _restore(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__cKeep) {
        r.type = window.__cKeep.t; r.candidateState = window.__cKeep.cs;
        r.candidatesList = window.__cKeep.cl; window.__cKeep = undefined;
      }
      renderReviewCol(); }""")


def test_nothing_claims_to_be_loading(page):
    """The whole defect. No request is in flight and none is coming."""
    try:
        for lookup in ("found", "absent", "failed", None):
            got = _inject(page, "indicator_shortlist", lookup)
            assert "load on confirm" not in got.lower(), (lookup, got)
            assert "loading" not in got.lower(), (lookup, got)
    finally:
        _restore(page)


def test_a_booking_the_warehouse_does_not_have_says_that(page):
    """A dead end an associate can act on — they can ask the guest for a
    reference rather than waiting for a lookup that already happened."""
    try:
        got = _inject(page, "indicator_shortlist", "absent")
        assert "not in the warehouse" in got, got
        assert "Zendesk ticket is everything we have" in got, got
    finally:
        _restore(page)


def test_a_lookup_that_did_not_complete_says_to_re_run(page):
    """The opposite response to the one above, so it cannot share its
    sentence: nothing was ruled out and the booking may well be there."""
    try:
        got = _inject(page, "indicator_shortlist", "failed")
        assert "did not complete" in got, got
        assert "nothing here was ruled out" in got, got
        assert "Re-run" in got, got
    finally:
        _restore(page)


def test_a_booking_that_was_read_and_is_empty_says_so(page):
    """We DID look and the row carries nothing. A reader must not go hunting
    in Zendesk for something the warehouse simply lacks."""
    try:
        got = _inject(page, "zendesk_requester_candidates", "found")
        assert "was read and carries no" in got, got
        assert "Confirm to fetch" not in got, got
    finally:
        _restore(page)


def test_a_candidate_with_no_recorded_answer_admits_it(page):
    """A candidate from a path that does not record a lookup. It must not
    borrow one of the three answers above — saying "not in the warehouse"
    about a booking nobody asked about is the inverse of the original bug."""
    try:
        got = _inject(page, "some_other_path", None)
        assert "No booking details were read" in got, got
        assert "not in the warehouse" not in got, got
        assert "did not complete" not in got, got
    finally:
        _restore(page)


def test_the_four_answers_are_four_different_sentences(page):
    """The guarantee, stated once. Any two of these collapsing sends a reader
    to the wrong next action."""
    try:
        seen = {k: _inject(page, "indicator_shortlist", k)
                for k in ("found", "absent", "failed", None)}
        assert len(set(seen.values())) == 4, seen
    finally:
        _restore(page)


def test_the_message_no_longer_depends_on_which_path_found_it(page):
    """The same lookup answer must read the same whichever search produced the
    candidate. It used to be the reverse — the path decided the sentence and
    the actual answer was not consulted at all."""
    try:
        a = _inject(page, "indicator_shortlist", "absent")
        b = _inject(page, "zendesk_requester_candidates", "absent")
        assert a == b, (a, b)
    finally:
        _restore(page)


def test_the_ingest_remap_names_the_field():
    """A SOURCE ASSERTION, and CLAUDE.md's stated exception: this is
    client-side JavaScript with no harness that can reach it. The remap lives
    inside the draft-ingest function and is not separately callable, so
    `_inject` above reproduces it — which means `_inject` CANNOT catch the
    remap dropping the field. A mutation deleting it from the remap survived
    the whole file and proved exactly that.

    The remap builds a fixed shape and silently drops anything not named in
    it. `narrowing_path` was missing once, so the message keyed on it could
    never have rendered however correct the branch was; `details_lookup` is
    the field the message keys on now."""
    src = open("client/index.html").read()
    i = src.index("r.candidatesList = draft.candidates_list.map(c => ({")
    # Bounded by the END OF THE BLOCK, not a character count. A fixed slice
    # broke the moment three score fields were added ahead of this one —
    # a test that fails when the code around it grows is measuring the wrong
    # thing.
    remap = src[i:src.index("}));", i)]
    assert "detailsLookup:  c.details_lookup" in remap, \
        "the ingest remap drops details_lookup, so the empty-details message " \
        "cannot tell the four answers apart"
