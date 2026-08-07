"""A candidate with no booking details says WHY, and never says "loading".

THE MESSAGE THIS REPLACES. "Booking details load on confirm" rendered whenever
a candidate had no experience, date or vendor. Nothing was loading: no request
was in flight and none would be until Confirm was pressed. A reader waited for
something that was never coming — "we found nothing" rendered as "we have not
finished yet", which is the failure this codebase opens with, in the UI.

The two reasons are DIFFERENT FACTS and must not share a sentence:
  indicator_shortlist — built from Zendesk ticket signals alone; the warehouse
                        was never queried, so there is no row to be empty.
  anything else       — the warehouse WAS queried and the row carries nothing.
                        That is a gap in our data, and a reader should not go
                        looking for it in Zendesk.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _inject(page, narrowing_path):
    """Drive the REAL ingest remap, not a hand-built candidate object.

    The remap builds a fixed shape and silently drops any field not named in
    it — `narrowing_path` was missing from it, so a message branching on that
    field could never have fired however correct the branch was. Going through
    the remap is the only way this test can catch that.
    """
    return page.evaluate("""(path) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__cKeep === undefined)
        window.__cKeep = {t: r.type, cs: r.candidateState, cl: r.candidatesList};
      r.type = 'candidates';
      r.candidateState = true;
      // The server payload shape, through the client's own remap.
      const draft = {candidates_list: [
        {id: '32885089', narrowing_path: path, matchReasons: ['name'],
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
      }));
      renderReviewCol();
      const el = document.querySelector('.candidate-meta');
      return el ? el.textContent.trim() : '';
    }""", narrowing_path)


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
        for path in ("indicator_shortlist", "zendesk_requester_candidates"):
            got = _inject(page, path)
            assert "load on confirm" not in got.lower(), (path, got)
            assert "loading" not in got.lower(), (path, got)
    finally:
        _restore(page)


def test_a_zendesk_only_candidate_says_no_record_was_read(page):
    """`indicator_shortlist` never queries the warehouse, so there is no row
    to be empty — and Confirm is what fetches it."""
    try:
        got = _inject(page, "indicator_shortlist")
        assert "No booking record was read" in got, got
        assert "Confirm to fetch it" in got, got
    finally:
        _restore(page)


def test_a_warehouse_candidate_says_the_record_was_read_and_is_empty(page):
    """A different fact: we DID look, and the row carries nothing. A reader
    must not go hunting in Zendesk for something the warehouse simply lacks."""
    try:
        got = _inject(page, "zendesk_requester_candidates")
        assert "was read and carries no" in got, got
        assert "Confirm to fetch" not in got, got
    finally:
        _restore(page)


def test_the_two_reasons_do_not_share_a_sentence(page):
    try:
        a = _inject(page, "indicator_shortlist")
        b = _inject(page, "zendesk_requester_candidates")
        assert a != b, a
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
    it. `narrowing_path` was missing, so the message keyed on it could never
    have rendered however correct the branch was."""
    src = open("client/index.html").read()
    i = src.index("r.candidatesList = draft.candidates_list.map(c => ({")
    # Bounded by the END OF THE BLOCK, not a character count. A fixed slice
    # broke the moment three score fields were added ahead of this one —
    # a test that fails when the code around it grows is measuring the wrong
    # thing.
    remap = src[i:src.index("}));", i)]
    assert "narrowing_path: c.narrowing_path" in remap, \
        "the ingest remap drops narrowing_path, so the empty-details message " \
        "cannot tell the two reasons apart"
