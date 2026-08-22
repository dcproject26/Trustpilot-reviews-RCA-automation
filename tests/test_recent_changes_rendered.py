"""The five most recent changes, watched rendering in a real browser.

All five are test-verified server-side and none had been seen on screen. That
gap has already cost this project twice: the content-family warning passed
every server test while a client mutation put it on nearly every review, and
`complete_booking_row` fixed a PARTIAL BOOKING ROW whose only symptom was four
dashes on a panel no test opened.

  (a) confirming a candidate populates Booking details and the RCA rather than
      leaving the column locked
  (b) Booking details shows fulfilment type, booking date, partnered vendor
      and lead time on an auto-promoted BID — what complete_booking_row fixed
  (c) a BID whose booking belongs to someone else draws the mismatch line
  (d) the close/Sent route is reachable from the untraceable panel AND the
      candidate picker
  (e) no raw epoch, no PII hash, no `None`, and zero JavaScript errors
      anywhere on the card

EVERY CHECK PROVES ITS SUBJECT EXISTS BEFORE IT PASSES. A sweep over an empty
card, a filter over an empty signal list, a scan of a column that never
rendered — each of those passes in silence, and a vacuous pass is the failure
mode this codebase punishes hardest. Where a check could be satisfied by
nothing being there, it asserts the something first and says NOT BUILT if it
is missing.

The module-scoped `page` fixture and CHROME come from test_rca_ui_rendered;
pytest instantiates a module-scoped fixture once per importing module, so this
file gets its own server and its own browser and its own error list.
"""
import re

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME, _rca_tab   # noqa: E402,F401


# ── (b) the four fields the matching query never selected ───────────────────
#
# Shaped exactly like _make_candidate's output: what the auto-promote paths
# hand through as the matched booking. Fulfilment type, created_at and
# is_partnered are absent because nothing asked for them.
PARTIAL_ROW = {
    "id": "33204378",
    "primary_guest_name": "Ana Ribeiro",
    "experience": "Pena Palace & Park",
    "experienceName": "Pena Palace & Park",
    "experience_name": "Pena Palace & Park",
    "date_of_visit": "2026-07-24",
    "visitDate": "2026-07-24",
    "vendorName": "Parques de Sintra",
    "tid": "43605", "tgid": "22238", "vid": "4040",
    "matched_on": ["venue", "date"], "narrowing_path": "bq_venue_date_30",
    "matchReasons": ["venue", "date"], "score": None,
    "booking_status": "COMPLETED", "tid_name": "Pena Palace Entry",
}

# What verify_bid selects and the matching query does not.
WAREHOUSE_ROW = {
    "id": "33204378",
    "fulfilment_type": "MOBILE_TICKET",
    "date_of_booking": "2026-07-18T09:14:00",
    "is_partnered": True,
    "experienceName": "Pena Palace & Park",
    "date_of_visit": "2026-07-24",
}


def _complete(lookup):
    from server.pipeline import complete_booking_row
    return complete_booking_row(dict(PARTIAL_ROW), lookup)


@pytest.fixture(autouse=True)
def _fresh_card(page):
    """Every test starts from the server's own answer, not from residue.

    `page` is module-scoped, so one browser serves the whole file, and
    `_render_booking` below deliberately WIPES `r.booking` and substitutes a
    booking of its own over the wire. That substitution has no
    `guestNameNote`, `guestName` or any other field the server computes
    alongside the booking — so after one call those fields are simply gone,
    for every later test.

    It cost a flake:
    `test_a_booking_with_a_hash_for_a_name_says_so_instead` reads the server's
    note BEFORE substituting a booking of its own, and passed alone and in a
    full run, and failed under a -k subset where a substituting test happened
    to run first. That is the worst kind of red — it looks like the code and
    it is the harness, and it teaches people to re-run rather than read.

    Reloading here rather than at the end of `_render_booking`: a test that
    inspects the page after calling it must still see what it put there.
    """
    page.evaluate("""async () => {
      // The helper swaps window.fetch and restores it, but a test that fails
      // mid-way leaves the stub installed. Put the real one back first, or
      // this reload reads the stub's answer and restores nothing.
      if (window.__realFetch) window.fetch = window.__realFetch;
      await loadDraftOverlays();
      renderReviewCol();
    }""")
    yield


def _render_booking(page, booking):
    """Put `booking` on the wire and let the REAL client mapping consume it.

    Not `r.booking = {...}`: the snake_case-to-camelCase mapping and the lead
    time computation both live in the load path, and both are things that can
    drop a field. Setting the rendered object directly would skip exactly the
    code the fix depends on.
    """
    return page.evaluate("""async (bk) => {
      // Start from an empty booking. The client's mapping keeps a previous
      // value when the incoming payload has none (`db.x || r.booking.x`), so
      // without this a case that renders a PARTIAL row after a full one would
      // still show the full one's fields and this helper would prove nothing.
      const rr = REVIEWS.find(x => x.id === state.selected);
      rr.booking = {};
      if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        const res = await window.__realFetch(url, opts);
        if (/\\/api\\/reviews\\/[^/?]+$/.test(u.split('?')[0])) {
          const body = await res.clone().json();
          if (body.draft) body.draft.booking = bk;
          return new Response(JSON.stringify(body), {status: 200});
        }
        return res;
      };
      await loadDraftOverlays();
      renderReviewCol();
      window.fetch = window.__realFetch;
      const rows = {};
      document.querySelectorAll('.detail-row').forEach(el => {
        const k = el.querySelector('.k'), v = el.querySelector('.v');
        if (k && v) rows[k.textContent.trim()] = v.textContent.trim();
      });
      return rows;
    }""", booking)


FOUR = ["Fulfilment type", "Booking date", "Partnered vendor", "Lead time"]


def test_the_booking_details_panel_is_on_screen_at_all(page):
    """NOT BUILT check for everything below. A panel that never rendered would
    let every assertion about its rows pass by inspecting nothing."""
    rows = _render_booking(page, dict(WAREHOUSE_ROW, **PARTIAL_ROW))
    assert rows, "no .detail-row rendered anywhere — NOT BUILT, not passing"
    missing = [k for k in FOUR if k not in rows]
    assert not missing, (
        f"the Booking details panel does not render {missing} at all — NOT "
        f"BUILT. Every check below would pass by finding nothing to check.")


def test_a_partial_row_is_what_the_bug_looked_like(page):
    """The control. Without it, the next test cannot tell a working merge from
    a panel that shows those four fields whatever it is given."""
    rows = _render_booking(page, dict(PARTIAL_ROW))
    dashes = {k: rows.get(k) for k in FOUR}
    assert dashes["Fulfilment type"] == "—", dashes
    assert dashes["Partnered vendor"] == "—", dashes
    assert dashes["Lead time"] == "—", dashes
    assert dashes["Booking date"] == "—", dashes


def test_complete_booking_row_fills_the_four_dashes_on_screen(page):
    """(b). The real function, the real client mapping, the real renderer.

    complete_booking_row is driven here rather than hand-merged, so a change
    to its merge direction shows up as four dashes on this panel — which is
    exactly how the bug presented.
    """
    merged, trail = _complete(lambda bid: dict(WAREHOUSE_ROW))
    assert merged is not PARTIAL_ROW and merged.get("fulfilment_type"), (
        "complete_booking_row returned a row with no fulfilment type — NOT "
        "BUILT")
    rows = _render_booking(page, merged)

    assert rows["Fulfilment type"] == "MOBILE_TICKET", rows
    assert rows["Partnered vendor"] == "Yes", rows
    assert rows["Booking date"].startswith("2026-07-18"), rows
    assert rows["Lead time"] == "6 days", rows
    assert trail and trail["mark"] == "pass", trail


def test_the_match_paths_own_values_are_not_overwritten(page):
    """Direction of the merge, checked where a reader would notice it.

    Matching decided which booking this is. A warehouse row disagreeing about
    the experience name must not rename the booking on the card.
    """
    merged, _ = _complete(lambda bid: dict(WAREHOUSE_ROW,
                                           experienceName="A DIFFERENT TOUR",
                                           vendorName="Someone Else"))
    rows = _render_booking(page, merged)
    assert rows["Experience"] == "Pena Palace & Park", rows
    assert rows["Vendor name"] == "Parques de Sintra", rows


def test_a_lookup_that_returned_nothing_does_not_read_as_an_empty_booking(page):
    """The dash has to keep meaning "we did not fetch it".

    Both failure shapes go back to the same four dashes on screen, so the
    difference has to be carried by the trail entry — and it is the entry, not
    the panel, that a reader checks when a field is blank.
    """
    merged, trail = _complete(lambda bid: None)
    rows = _render_booking(page, merged)
    assert rows["Fulfilment type"] == "—"
    assert trail and trail["mark"] == "warn", trail
    assert "not because the booking has none" in trail["text"], trail

    def _raises(bid):
        raise RuntimeError("BigQuery 503")

    merged2, trail2 = _complete(_raises)
    assert trail2 and trail2["mark"] == "warn", trail2
    assert "RuntimeError" in trail2["text"], trail2
    assert trail2["text"] != trail["text"], (
        "a lookup that raised and a lookup that returned nothing produce the "
        "same sentence — two different failures reading as one")


# ── (a) confirming a candidate populates the card ───────────────────────────

CANDIDATES = [{"id": "44556677", "experience": "Colosseum Guided Tour",
               "score": 4.0, "matchReasons": ["venue", "date"]}]


def _as_candidates(page):
    page.evaluate("""(cands) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      window.__phase = 'A';
      window.__keep = {type: r.type, cs: r.candidateState, cl: r.candidatesList};
      r.type = 'candidates';
      r.candidateState = true;
      r.candidatesList = cands.map(c => ({
        bid: c.id, score: c.score, matchReasons: c.matchReasons,
        experience: c.experience, tgid: '', tid: '', vendorName: '',
        experienceDate: '', creationDate: '', status: '', leadTime: '',
        guestName: '', contactCount: 0, contactTags: ''}));
      if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes('/select-candidate'))
          return new Response(JSON.stringify({ok: true}), {status: 200});
        if (u.includes('/progress'))
          return new Response(JSON.stringify({running: true, state: 'running',
            step: 3, total: 8, stage: 'zendesk', elapsed_s: 9,
            since_progress_s: 2, stalled_after_s: 600}), {status: 200});
        const res = await window.__realFetch(url, opts);
        if (/\\/api\\/reviews\\/[^/?]+$/.test(u.split('?')[0])) {
          const body = await res.clone().json();
          if (body.draft) {
            body.draft.candidate_state = false;
            body.draft.generated_at = window.__phase === 'A'
              ? '2026-08-01T00:00:00' : '2026-08-01T09:30:00';
          }
          return new Response(JSON.stringify(body), {status: 200});
        }
        if (u.split('?')[0].endsWith('/api/reviews')) {
          const rows = await res.clone().json();
          rows.forEach(row => {
            row.candidate_state = false;
            row.bucket = window.__phase === 'A' ? 'candidates' : 'identified';
            row.has_booking = window.__phase !== 'A';
            row.confirmed = true;
          });
          return new Response(JSON.stringify(rows), {status: 200});
        }
        return res;
      };
      renderReviewCol(); renderRcaCol();
    }""", CANDIDATES)


def _restore_candidates(page):
    page.evaluate("""() => {
      if (window.__realFetch) window.fetch = window.__realFetch;
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__keep) {
        r.type = window.__keep.type;
        r.candidateState = window.__keep.cs;
        r.candidatesList = window.__keep.cl;
        window.__keep = undefined;
      }
      renderReviewCol(); renderRcaCol();
    }""")


def _card(page):
    return page.evaluate("""() => ({
      locked:  !!document.querySelector('#rca-col .rca-gate'),
      details: [...document.querySelectorAll('.facts-head-title')]
                 .some(e => e.textContent.trim() === 'Booking details'),
      bid:     (() => { const rows = [...document.querySelectorAll('.detail-row')]
                          .filter(el => (el.querySelector('.k')||{}).textContent
                                        === 'Booking ID');
                        return rows.length ? rows[0].querySelector('.v').textContent.trim() : null; })(),
      rca:     document.querySelector('#rca-col').textContent.toUpperCase(),
    })""")


def test_confirming_a_candidate_unlocks_the_column_and_fills_the_panel(page):
    """(a). Before: the gate, and no Booking details panel at all. After: the
    analysis and a populated panel.

    The gate assertion alone is not enough — a build that lifts the gate onto
    an empty column reproduces the report just as well, and that is precisely
    what "appeared to do nothing" meant.
    """
    _as_candidates(page)
    try:
        before = _card(page)
        assert before["locked"], "fixture is wrong — the gate should be up first"
        assert not before["details"], (
            "Booking details is already on screen before confirming, so this "
            "test cannot show that confirming put it there")
        assert page.locator(".candidate-confirm-btn").count() == 1, (
            "no confirm button in the candidate picker — NOT BUILT")

        page.locator(".candidate-confirm-btn").click()
        page.wait_for_timeout(4200)
        assert _card(page)["locked"], (
            "the card left the gate while the run was still going")

        page.evaluate("() => { window.__phase = 'B'; }")
        page.wait_for_timeout(7000)
        after = _card(page)
    finally:
        _restore_candidates(page)

    assert not after["locked"], (
        "the run landed and the column still says locked — the poll stopped "
        "at the confirmation or refreshed a store the renderer does not read")
    assert after["details"], (
        "the gate lifted but Booking details never rendered — the column "
        "unlocked onto nothing, which is the report exactly")
    assert after["bid"], "the Booking details panel rendered with no booking id"
    # AREA OF IMPROVEMENT was the third heading here and is no longer on the
    # card — the section was removed from the dashboard (the backend keeps it;
    # see tests/test_area_of_improvement.py). What this test is actually for
    # is that the column populated rather than unlocking onto nothing, so the
    # heading is replaced by another one that still renders rather than the
    # check being dropped to two.
    for heading in ("WHAT WENT WRONG", "FLAGS", "ACTIONS TAKEN"):
        assert heading in after["rca"], (
            f"{heading} is missing after confirming — the RCA did not populate")


# ── (c) a BID whose booking belongs to someone else ─────────────────────────

SOMEONE_ELSES = {
    "state": "mismatch",
    "signals": [
        {"name": "city", "state": "mismatch", "review": "Paris", "booking": "Rome",
         "why": "the review is about Paris; this booking is in Rome"},
        {"name": "venue", "state": "mismatch", "review": "Louvre",
         "booking": "Colosseum",
         "why": "the review names the Louvre; this booking is the Colosseum"},
        {"name": "guest", "state": "match", "review": "A", "booking": "A",
         "why": "the names agree (1.0)"},
    ],
    "contradictions": ["city", "venue"], "agreements": ["guest"], "checked": 3,
    "why": "the review is about Paris; this booking is in Rome",
}


def _set_indicator(page, im):
    return page.evaluate("""(im) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__imKeep === undefined)
        window.__imKeep = r.indicatorMatch === undefined ? null : r.indicatorMatch;
      r.indicatorMatch = im;
      renderReviewCol();
      return [...document.querySelectorAll('.content-mismatch')]
        .map(e => e.textContent.replace(/\\s+/g, ' ').trim());
    }""", im)


def _restore_indicator(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__imKeep !== undefined) r.indicatorMatch = window.__imKeep;
      window.__imKeep = undefined;
      renderReviewCol(); }""")


def test_a_bid_belonging_to_someone_else_draws_the_mismatch_line(page):
    """(c). Both contradicting signals named, and the match not undone."""
    try:
        lines = _set_indicator(page, SOMEONE_ELSES)
        drawn = [t for t in lines if "does not match what the review says" in t]
    finally:
        _restore_indicator(page)

    assert len(drawn) == 1, (
        f"expected exactly one indicator mismatch line, got {len(drawn)}: {lines}")
    text = drawn[0]
    # The signal list only — the sentence after it legitimately contains the
    # word "guest" ("The guest may be quoting someone else's reference").
    signals = text.split("The guest may be")[0]
    assert "city" in signals and "venue" in signals, (
        f"the contradicting signals are not named: {text}")
    assert "guest:" not in signals, (
        f"an AGREEING signal was listed as a contradiction: {text}")
    assert "the names agree" not in signals, (
        f"the agreeing signal's reason was printed as a contradiction: {text}")
    assert "has NOT been undone" in text, (
        "the line does not say the match still stands, so a reader cannot tell "
        "whether the booking was dropped")
    assert "quoting someone else" in text, text


@pytest.mark.parametrize("state", ["match", "unchecked"])
def test_agreement_and_could_not_tell_stay_silent(page, state):
    """The other half, and the reason it matters: "unchecked" is the common
    answer, so drawing on it would put a warning on nearly every review."""
    try:
        lines = _set_indicator(page, dict(SOMEONE_ELSES, state=state))
        drawn = [t for t in lines if "does not match what the review says" in t]
    finally:
        _restore_indicator(page)
    assert drawn == [], f"state={state!r} drew a warning: {drawn}"


# ── (d) the route to Sent, from both panels ─────────────────────────────────

def _close_buttons(page):
    return page.evaluate("""() => [...document.querySelectorAll('[data-close-out]')]
        .map(b => ({text: b.textContent.trim(), reason: b.dataset.closeReason || ''}))""")


def test_the_untraceable_panel_reaches_close_out(page):
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      window.__t = r.type; r.type = 'untraceable'; renderRcaCol();
      const out = [...document.querySelectorAll('[data-close-out]')]
        .map(b => ({text: b.textContent.trim(), reason: b.dataset.closeReason || ''}));
      r.type = window.__t; renderRcaCol();
      return out; }""")
    assert got, "the untraceable panel offers no route to Sent — NOT BUILT"
    assert any("Close out" in b["text"] for b in got), got
    assert all(b["reason"] for b in got), (
        f"a close button records no reason: {got} — a closed review with no "
        f"reason is one nobody downstream can account for")


def test_the_candidate_picker_reaches_close_out(page):
    _as_candidates(page)
    try:
        got = _close_buttons(page)
    finally:
        _restore_candidates(page)
    assert got, "the candidate picker offers no route to Sent — NOT BUILT"
    assert any("Close out" in b["text"] for b in got), got
    assert all(b["reason"] for b in got), got


def test_close_out_arms_before_it_fires_and_then_calls_the_close_endpoint(page):
    """Reachable means the button actually gets to POST /close.

    A button that renders and does nothing is the failure one layer down, and
    it looks identical until it is clicked. fetch is recorded rather than let
    through, so the seeded review is not actually closed out from under the
    rest of this module.
    """
    _as_candidates(page)
    try:
        calls = page.evaluate("""async () => {
          window.__calls = [];
          const real = window.fetch;
          window.fetch = async (url, opts) => {
            const u = String(url);
            if (u.includes('/close')) {
              window.__calls.push({url: u, method: (opts||{}).method,
                                   body: (opts||{}).body});
              return new Response(JSON.stringify({ok: true, reason: 'x'}),
                                  {status: 200});
            }
            return real(url, opts);
          };
          const btn = document.querySelector('[data-close-out]');
          if (!btn) return null;
          btn.click();
          await new Promise(r => setTimeout(r, 200));
          const armed = btn.textContent.trim();
          btn.click();
          await new Promise(r => setTimeout(r, 800));
          return {armed: armed, calls: window.__calls}; }""")
        assert calls is not None, "no close button to click — NOT BUILT"
        assert "Click again" in calls["armed"], (
            f"the first click fired straight away: {calls['armed']!r} — a "
            f"mis-click takes the review out of every working tab")
        assert len(calls["calls"]) == 1, (
            f"the armed click did not reach /close: {calls['calls']}")
        call = calls["calls"][0]
        assert call["method"] == "POST", call
        assert "/close" in call["url"], call
        assert "reason" in (call["body"] or ""), (
            f"the close was sent with no reason: {call}")
    finally:
        _restore_candidates(page)
        page.reload(wait_until="load")
        page.wait_for_selector(".inbox-row", timeout=15000)
        page.locator(".inbox-row").first.click()
        page.wait_for_timeout(1400)


# ── (e) nothing machine-shaped reaches the reader ───────────────────────────

# A 10-digit integer starting 15-19 is an epoch in seconds for any date between
# 2017 and 2033. Booking ids and Zendesk ticket ids here are 8 digits, so this
# cannot collide with them.
EPOCH = re.compile(r"(?<!\d)1[5-9]\d{8}(?!\d)")
# "1.785791592E9" — what str() does to a large float. It reached the Booking
# date field once already.
SCI = re.compile(r"\d\.\d{4,}E\d", re.I)
# The PII hash BigQuery returns instead of a guest name: a long unbroken
# base64-ish token. Real prose has spaces; real names are short.
HASH = re.compile(r"(?<![\w/+=])(?=[^\s]{20,})[A-Za-z0-9+/]*[+/][A-Za-z0-9+/=]{19,}")
HEXHASH = re.compile(r"(?<![\w])[0-9a-fA-F]{32,}(?![\w])")
# Python's None, JavaScript's undefined, and the two stringification tells.
LEAKS = ("None", "undefined", "NaN", "[object Object]", "[object object]")


def _card_text(page):
    """What a reader actually sees, per column.

    A <select>'s innerText in Chromium is EVERY option, selected or not, so a
    raw read reports choices nobody is looking at — the Resolution control
    offers a literal "None" option, which is a deliberate user-facing choice
    and not a Python value that escaped. Each select is collapsed to the
    option currently showing, which is the only one on screen.
    """
    return page.evaluate("""() => {
      const visible = (root) => {
        if (!root) return '';
        const c = root.cloneNode(true);
        c.querySelectorAll('select').forEach(s => {
          const i = s.selectedIndex;
          let txt = i >= 0 && s.options[i] ? s.options[i].text : '';
          // The compensation-type control offers a literal "None" — one of
          // four hand-authored labels meaning "no compensation was given". It
          // is a domain value a person chose, not a Python None that escaped
          // into a sentence, and the sweep must not confuse the two. Named
          // rather than filtered by string, so a REAL None appearing here
          // would still be caught everywhere else on the card.
          if (s.hasAttribute('data-res-type')) txt = '[compensation-type]';
          s.replaceWith(document.createTextNode(' ' + txt + ' '));
        });
        // Off-screen only; a cloned tree has no layout, so innerText would
        // otherwise fall back to textContent and include hidden panels.
        document.body.appendChild(c);
        c.style.position = 'absolute'; c.style.left = '-99999px';
        const out = c.innerText;
        c.remove();
        return out;
      };
      return {rca: visible(document.querySelector('#rca-col')),
              facts: visible(document.querySelector('#review-col')
                          || document.querySelector('.facts-col'))}; }""")


def test_the_card_has_text_to_sweep(page):
    """NOT BUILT guard for the whole sweep. Every pattern below matches nothing
    against an empty string, so the sweep must first prove there is a card."""
    t = _card_text(page)
    assert len(t["rca"]) > 400, (
        f"the RCA column rendered {len(t['rca'])} characters — NOT BUILT, and "
        f"every scan below would pass by finding nothing")
    assert len(t["facts"]) > 200, (
        f"the facts column rendered {len(t['facts'])} characters — NOT BUILT")
    assert "PENA" in t["facts"].upper() or "BOOKING" in t["facts"].upper(), (
        "the facts column has text but no booking on it, so the sweep is not "
        "looking at a real card")


@pytest.mark.parametrize("col", ["rca", "facts"])
def test_no_raw_epoch_reaches_the_reader(page, col):
    text = _card_text(page)[col]
    assert not EPOCH.search(text), (
        f"a raw epoch reached the {col} column: "
        f"{EPOCH.search(text).group(0)!r} — a time the reader cannot read")
    assert not SCI.search(text), (
        f"a float in scientific notation reached the {col} column: "
        f"{SCI.search(text).group(0)!r}")


@pytest.mark.parametrize("col", ["rca", "facts"])
def test_no_pii_hash_is_printed_as_a_value(page, col):
    text = _card_text(page)[col]
    for rx, what in ((HASH, "base64 PII hash"), (HEXHASH, "hex digest")):
        m = rx.search(text)
        assert not m, f"a {what} reached the {col} column: {m.group(0)[:40]!r}"


@pytest.mark.parametrize("col", ["rca", "facts"])
def test_no_python_or_javascript_placeholder_reaches_the_reader(page, col):
    text = _card_text(page)[col]
    for leak in LEAKS:
        assert not re.search(rf"(?<![\w]){re.escape(leak)}(?![\w])", text), (
            f"{leak!r} is rendered in the {col} column — a language's word for "
            f"absence, in a sentence a person reads")


def test_the_placeholder_sweep_can_actually_fail(page):
    """A scan that cannot fail is decoration.

    One control is excluded by name above, so this proves the exclusion did not
    quietly disarm the whole check: a Python None rendered into an ordinary
    field is still found.
    """
    caught = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      // BOTH stores. The box renders rca.v3.stated_issue when that key is
      // present and falls back to this snapshot, so injecting the leak into
      // the snapshot alone never reaches the DOM — and this guard would then
      // report "cannot fail" for the one reason it exists to rule out.
      const keep = r.statedIssue, keepV = r.rca.v3.stated_issue;
      r.statedIssue = 'The last contact was None';      // what a leak looks like
      r.rca.v3.stated_issue = 'The last contact was None';
      renderRcaCol();
      const c = document.querySelector('#rca-col').cloneNode(true);
      c.querySelectorAll('select').forEach(s => s.remove());
      const hit = /(?<![\\w])None(?![\\w])/.test(c.innerText);
      r.statedIssue = keep; r.rca.v3.stated_issue = keepV; renderRcaCol();
      return hit; }""")
    assert caught, (
        "a Python None rendered into the stated issue was NOT detected — the "
        "sweep above is passing because it cannot fail")


def test_a_booking_with_a_hash_for_a_name_says_so_instead(page):
    """The sweep above only proves this fixture is clean. This proves the card
    HANDLES a hash, which is what the warehouse actually returns.

    WHICH reason is rendered is the SERVER's answer, not this page's — see
    `guest_name_note` in `_draft_dict` and its cases in
    test_missing_classification_is_said.py. That was not always true: the card
    read the note off `draft.booking`, where it has never lived, so the read
    was `undefined` and every card fell through to one hard-coded sentence.
    This test asserted that sentence, which is why it could not tell the
    working build from the broken one.

    `_render_booking` swaps the booking on the wire and leaves the rest of the
    payload alone, so the note here is the one the server computed for the
    fixture. What this test owns is the rest: the hash is not printed, the row
    falls through to the absent state, and the row carries the server's
    sentence rather than a client-side stand-in.
    """
    served = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      return r.booking.guestNameNote; }""")
    rows = _render_booking(page, dict(
        PARTIAL_ROW, primary_guest_name="FjpJxbSfpb65bnyQwErTyUiOpAsDfGhJ"))
    # THE ROW IS GONE, by request — see client/index.html where it was. The
    # assertion that mattered survives it and gets stronger: the hash must not
    # appear ANYWHERE, not merely fall through to the absent state in one row.
    assert "Primary guest" not in rows, (
        "the Primary guest row is back — it was removed deliberately")
    body = page.evaluate("() => document.body.innerText")
    assert "FjpJxbSfpb65" not in body, "the PII hash is rendered on the card"
    assert served is not None, (
        "the server no longer computes guestNameNote — the row can be brought "
        "back as a template change only while this still ships")
    assert served, "the server sent no reason for the missing name at all"


def test_a_raw_epoch_booking_date_is_rendered_as_a_date(page):
    """The other half of the epoch check, driven rather than observed.

    BigQuery hands a TIMESTAMP back as epoch seconds. A card that simply never
    received one would pass the sweep above for the wrong reason.
    """
    rows = _render_booking(page, dict(PARTIAL_ROW, date_of_booking=1785791592))
    got = rows.get("Booking date", "")
    assert got, "the Booking date row did not render — NOT BUILT"
    assert not EPOCH.search(got) and not SCI.search(got), (
        f"the epoch reached the Booking date field verbatim: {got!r}")


def test_the_whole_card_raised_no_javascript_error(page):
    """Last in the module on purpose: page.errors accumulates over everything
    above, so this covers every interaction this file performed — confirming a
    candidate, arming and firing close out, six re-renders of the booking
    panel and both mismatch states."""
    assert page.errors == [], f"JavaScript errors on the card: {page.errors}"


# ── a re-run that matches a DIFFERENT booking ───────────────────────────────
#
# Found by the harness above rather than reported: rendering a partial row
# after a full one still showed the full one's fields, because every line of
# the client's booking mapping keeps what is on screen when the incoming value
# is blank. That is right for a refresh of the same booking and wrong the
# moment the matched booking changes — the old booking's fulfilment type sits
# under the new booking's id, and a value belonging to a booking nobody is
# looking at any more is worse than a dash. The dash says we do not know; the
# stale value asserts something false.

def test_a_rerun_onto_a_different_booking_does_not_keep_the_old_ones_fields(page):
    full, _ = _complete(lambda bid: dict(WAREHOUSE_ROW))
    rows = _render_booking(page, full)
    assert rows["Fulfilment type"] == "MOBILE_TICKET", (
        f"the first booking did not render — NOT BUILT: {rows}")

    # A different BID, partial the way an auto-promote path used to hand it on.
    other = dict(PARTIAL_ROW, id="99887766", experience="Sintra Palace Tour",
                 experienceName="Sintra Palace Tour",
                 experience_name="Sintra Palace Tour")
    rows2 = page.evaluate("""async (bk) => {
      if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        const res = await window.__realFetch(url, opts);
        if (/\\/api\\/reviews\\/[^/?]+$/.test(u.split('?')[0])) {
          const body = await res.clone().json();
          if (body.draft) body.draft.booking = bk;
          return new Response(JSON.stringify(body), {status: 200});
        }
        return res;
      };
      await loadDraftOverlays();
      renderReviewCol();
      window.fetch = window.__realFetch;
      const out = {};
      document.querySelectorAll('.detail-row').forEach(el => {
        const k = el.querySelector('.k'), v = el.querySelector('.v');
        if (k && v) out[k.textContent.trim()] = v.textContent.trim();
      });
      return out; }""", other)

    assert rows2["Booking ID"] == "99887766", (
        f"the new booking id did not take: {rows2}")
    assert rows2["Fulfilment type"] == "—", (
        f"the previous booking's fulfilment type is showing under the new "
        f"booking's id: {rows2['Fulfilment type']!r}")
    assert rows2["Partnered vendor"] == "—", rows2
    assert rows2["Lead time"] == "—", rows2
    assert rows2["Experience"] == "Sintra Palace Tour", rows2


def test_a_refresh_of_the_SAME_booking_still_keeps_what_it_has(page):
    """The other half, and the reason the fallback exists.

    Clearing on every partial payload would blank the panel on an ordinary
    refresh — which is the bug the `db.x || r.booking.x` fallback was written
    to prevent, and re-introducing it would be a straight trade of one defect
    for another.
    """
    full, _ = _complete(lambda bid: dict(WAREHOUSE_ROW))
    assert _render_booking(page, full)["Fulfilment type"] == "MOBILE_TICKET"

    same_but_partial = dict(PARTIAL_ROW)              # same id, fewer fields
    rows = page.evaluate("""async (bk) => {
      if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        const res = await window.__realFetch(url, opts);
        if (/\\/api\\/reviews\\/[^/?]+$/.test(u.split('?')[0])) {
          const body = await res.clone().json();
          if (body.draft) body.draft.booking = bk;
          return new Response(JSON.stringify(body), {status: 200});
        }
        return res;
      };
      await loadDraftOverlays();
      renderReviewCol();
      window.fetch = window.__realFetch;
      const out = {};
      document.querySelectorAll('.detail-row').forEach(el => {
        const k = el.querySelector('.k'), v = el.querySelector('.v');
        if (k && v) out[k.textContent.trim()] = v.textContent.trim();
      });
      return out; }""", same_but_partial)

    assert rows["Booking ID"] == "33204378", rows
    assert rows["Fulfilment type"] == "MOBILE_TICKET", (
        f"a partial refresh of the SAME booking blanked the panel: {rows}")


# ── the DSS row is correctable, and a correction says it is one ─────────────

def _dss(page):
    return page.evaluate("""() => {
      const b = document.querySelector('.dss-block');
      if (!b) return null;
      return {editables: b.querySelectorAll('[data-v3p]').length,
              mark: !!b.querySelector('.dss-hand'),
              btn: (b.querySelector('[data-dss-edit]') || {}).textContent || '',
              text: b.textContent}; }""")


def test_the_dss_block_exists_and_toggles_into_edit(page):
    """NOT BUILT guard plus the toggle. Read-only by default: it is reference
    data, and an always-editable field invites accidental edits to a row
    nobody meant to change."""
    _rca_tab(page, "res")   # the DSS block lives in the Resolution tab now
    before = _dss(page)
    assert before, "there is no DSS block on the card — NOT BUILT"
    assert before["editables"] == 0, (
        "the DSS row is editable before anyone asked")
    page.click("[data-dss-edit]")
    page.wait_for_timeout(300)
    during = _dss(page)
    assert during["editables"] >= 1, (
        "✎ Edit did not produce an editable field — the associate is looking "
        "at a possibly-wrong row with no way to correct it")
    page.click("[data-dss-edit]")
    page.wait_for_timeout(300)
    assert _dss(page)["editables"] == 0


def test_an_unmatched_row_is_still_writable(page):
    """The case the control exists for. Rendering the empty state with nothing
    to type into left a miss uncorrectable."""
    got = page.evaluate("""async () => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = JSON.parse(JSON.stringify(r.rca.v3.dss || {}));
      r.rca.v3.dss = {};
      state.dssEdit = true; renderRcaCol();
      const b = document.querySelector('.dss-block');
      const out = {editables: b.querySelectorAll('[data-v3p]').length,
                   ph: (b.querySelector('[data-v3p]') || {}).dataset
                       ? b.querySelector('[data-v3p]').dataset.ph : ''};
      r.rca.v3.dss = keep; state.dssEdit = false; renderRcaCol();
      return out; }""")
    assert got["editables"] >= 1, (
        "a DSS row that matched nothing cannot be written — NOT BUILT")
    assert "playbook" in (got["ph"] or ""), got


def test_a_corrected_row_is_marked_and_a_matched_one_is_not(page):
    """A row a person corrected and a row the scorer picked must not render
    identically."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = JSON.parse(JSON.stringify(r.rca.v3.dss || {}));
      r.rca.v3.dss = {prescribes: 'Matched by the lookup'};
      renderRcaCol();
      const matched = !!document.querySelector('.dss-block .dss-hand');
      r.rca.v3.dss = {prescribes: 'Written by a person', by_hand: true};
      renderRcaCol();
      const el = document.querySelector('.dss-block .dss-hand');
      const corrected = el ? {text: el.textContent.trim(),
                              title: el.getAttribute('title') || ''} : null;
      r.rca.v3.dss = keep; renderRcaCol();
      return {matched, corrected}; }""")
    assert got["matched"] is False, (
        "a row the lookup matched is marked as hand-corrected")
    assert got["corrected"], (
        "a hand-corrected row renders identically to a matched one")
    assert got["corrected"]["text"] == "by hand"
    assert "not the row the lookup matched" in got["corrected"]["title"]


def test_the_marker_is_not_dressed_as_a_warning(page):
    """It is provenance, not a problem. Amber and red carry fixed meanings on
    this card and a quiet marker must not borrow either."""
    got = page.evaluate("""() => {
      const mk = c => { const e = document.createElement('span'); e.className = c;
                        document.body.append(e); return e; };
      const hand = mk('dss-hand'), stale = mk('stale-bar'), err = mk('rca-empty err');
      const out = [hand, stale, err].map(e => getComputedStyle(e).color);
      [hand, stale, err].forEach(e => e.remove());
      return out; }""")
    assert got[0] != got[1], "the by-hand marker shares the stale/amber colour"
    assert got[0] != got[2], "the by-hand marker shares the broken/red colour"


# ── the scenario chips: one list, rendered once, and a live × ───────────────

def _chips(page):
    return page.evaluate("""() => {
      const rows = {};
      document.querySelectorAll('.chip-row').forEach(r => {
        const label = (r.querySelector('.chip-row-label') || {}).textContent || '';
        rows[label.trim()] = [...r.querySelectorAll('.chip')].map(
          c => c.textContent.replace('×', '').trim());
      });
      return rows; }""")


def test_one_scenario_renders_exactly_once(page):
    """The report: `Refund issues ×` twice under Scenarios and again under
    Overlays. The rows are two views of ONE list now — the primary and its
    tail — so nothing can appear in both."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = [r.rca.scenarios, r.rca.overlayScenarios];
      r.rca.scenarios = ['Refund issues'];
      r.rca.overlayScenarios = ['Refund issues'];
      renderRcaCol();
      const rows = {};
      document.querySelectorAll('.chip-row').forEach(x => {
        const l = (x.querySelector('.chip-row-label') || {}).textContent || '';
        rows[l.trim()] = [...x.querySelectorAll('.chip')].map(
          c => c.textContent.replace('×', '').trim());
      });
      [r.rca.scenarios, r.rca.overlayScenarios] = keep;
      renderRcaCol();
      return rows; }""")
    assert "Primary" in got, f"the scenario rows did not render — NOT BUILT: {got}"
    all_chips = got.get("Primary", []) + got.get("Overlays", [])
    assert all_chips.count("Refund issues") == 1, (
        f"one scenario renders {all_chips.count('Refund issues')} times: {got}")


def test_the_overlay_row_is_the_tail_of_the_one_list(page):
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = [r.rca.scenarios, r.rca.overlayScenarios];
      r.rca.scenarios = ['Refund issues', 'Tickets sent late'];
      r.rca.overlayScenarios = ['Tickets sent late'];
      renderRcaCol();
      const rows = {};
      document.querySelectorAll('.chip-row').forEach(x => {
        const l = (x.querySelector('.chip-row-label') || {}).textContent || '';
        rows[l.trim()] = [...x.querySelectorAll('.chip')].map(
          c => c.textContent.replace('×', '').trim());
      });
      [r.rca.scenarios, r.rca.overlayScenarios] = keep;
      renderRcaCol();
      return rows; }""")
    assert got["Primary"] == ["Refund issues"], got
    assert got["Overlays"] == ["Tickets sent late"], got


def test_the_scenario_delete_removes_it_from_the_one_list(page):
    """The × was dead because the removal was saved and then overwritten by a
    union that still held it. Driven through the real delegated handler, with
    the network stubbed so the regen round trip is deterministic."""
    got = page.evaluate("""async () => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = [r.rca.scenarios, r.rca.overlayScenarios];
      if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
      const sent = [];
      window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes('/regenerate-rca')) {
          sent.push(JSON.parse((opts || {}).body || '{}'));
          return new Response(JSON.stringify({rca_v3: r.rca.v3}), {status: 200});
        }
        if (u.includes('/draft-v2') || u.includes('/insights'))
          return new Response(JSON.stringify({ok: true}), {status: 200});
        if (/\\/api\\/reviews\\/[^/?]+$/.test(u.split('?')[0]))
          return new Response(JSON.stringify({draft: null, review: null}), {status: 200});
        return window.__realFetch(url, opts);
      };
      r.rca.scenarios = ['Refund issues', 'Tickets sent late'];
      r.rca.overlayScenarios = ['Tickets sent late'];
      renderRcaCol();
      document.querySelector('[data-overlay-remove]').click();
      await new Promise(x => setTimeout(x, 900));
      const after = [...(r.rca.scenarios || [])];
      window.fetch = window.__realFetch;
      [r.rca.scenarios, r.rca.overlayScenarios] = keep;
      renderRcaCol();
      return {after, sent}; }""")
    assert got["after"] == ["Refund issues"], (
        f"removing the overlay left it in the one list: {got['after']}")
    assert got["sent"], "the delete never reached regenerate-rca"
    assert got["sent"][-1]["scenarios"] == ["Refund issues"], (
        f"the request that overwrites the stored list still carried the "
        f"deleted scenario: {got['sent'][-1]['scenarios']}")


def test_the_primary_delete_is_live_too(page):
    got = page.evaluate("""async () => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = [r.rca.scenarios, r.rca.overlayScenarios];
      if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes('/regenerate-rca'))
          return new Response(JSON.stringify({rca_v3: r.rca.v3}), {status: 200});
        if (u.includes('/draft-v2') || u.includes('/insights'))
          return new Response(JSON.stringify({ok: true}), {status: 200});
        if (/\\/api\\/reviews\\/[^/?]+$/.test(u.split('?')[0]))
          return new Response(JSON.stringify({draft: null, review: null}), {status: 200});
        return window.__realFetch(url, opts);
      };
      r.rca.scenarios = ['Refund issues', 'Tickets sent late'];
      r.rca.overlayScenarios = ['Tickets sent late'];
      renderRcaCol();
      document.querySelector('[data-scenario-remove]').click();
      await new Promise(x => setTimeout(x, 900));
      const after = [...(r.rca.scenarios || [])];
      window.fetch = window.__realFetch;
      [r.rca.scenarios, r.rca.overlayScenarios] = keep;
      renderRcaCol();
      return after; }""")
    assert got == ["Tickets sent late"], (
        f"removing the primary did not promote the overlay: {got}")


# ── the Resolution & takedown block is one treatment, not three ─────────────
#
# Measured, not spelled. A test asserting `class="chip-select"` appears in the
# file passes against a build where the rule it names was deleted — these read
# the computed box off real elements, in the spirit of the stale-notice test
# that asserts three states do NOT share a colour.

def _res_block(page):
    _rca_tab(page, "res")   # the block lives in the Resolution tab
    return page.evaluate("""() => {
      const sec = [...document.querySelectorAll('#rca-col .section')].find(
        s => /resolution/i.test((s.querySelector('.section-label')||{}).textContent || ''));
      if (!sec) return null;
      const box = el => { const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
        return {h: Math.round(r.height), radius: cs.borderTopLeftRadius,
                border: cs.borderTopColor, bw: cs.borderTopWidth,
                pad: cs.paddingTop + '/' + cs.paddingLeft}; };
      return {
        selects: [...sec.querySelectorAll('select')].map(box),
        buttons: [...sec.querySelectorAll('.btn')].map(box),
        labels:  [...sec.querySelectorAll('.res-label, .dss-label')].map(
                   el => getComputedStyle(el).color),
        inline:  [...sec.querySelectorAll('[style]')].map(el => el.getAttribute('style')),
      }; }""")


def test_the_block_was_found_and_has_controls(page):
    """NOT BUILT guard. An empty control list makes every comparison below
    vacuously true."""
    got = _res_block(page)
    assert got, "the Resolution & takedown section did not render — NOT BUILT"
    assert len(got["selects"]) >= 3, (
        f"only {len(got['selects'])} selects found; the block has the "
        f"resolution type, the takedown verdict and the ground picker")
    assert got["buttons"], "no button in the block — the DSS Edit control is gone"


def test_every_control_in_the_block_is_the_same_height(page):
    """Three heights in one block is what made it read as unfinished."""
    _rca_tab(page, "res")   # heights need the Resolution tab visible
    got = _res_block(page)
    heights = {c["h"] for c in got["selects"] + got["buttons"] if c["h"]}
    assert len(heights) == 1, (
        f"the controls in this block have {len(heights)} different heights: "
        f"{sorted(heights)}")


def test_every_control_shares_one_border_and_one_radius(page):
    got = _res_block(page)
    ctrls = got["selects"] + got["buttons"]
    assert len({c["radius"] for c in ctrls}) == 1, (
        f"mixed corner radii: {sorted({c['radius'] for c in ctrls})}")
    assert len({c["border"] for c in ctrls}) == 1, (
        f"mixed border colours: {sorted({c['border'] for c in ctrls})}")
    assert len({c["bw"] for c in ctrls}) == 1, (
        f"mixed border widths: {sorted({c['bw'] for c in ctrls})}")


def test_the_two_labels_in_the_block_are_one_grey(page):
    """DSS used --dim while the resolution labels used --muted-2 — two greys
    six lines apart in the same block."""
    got = _res_block(page)
    assert len(got["labels"]) >= 2, f"only {len(got['labels'])} labels found"
    assert len(set(got["labels"])) == 1, (
        f"the block's labels are {len(set(got['labels']))} different colours: "
        f"{set(got['labels'])}")


def test_no_control_opts_out_with_an_inline_style(page):
    """An inline font-size and padding is a control leaving the design system
    by definition, and it is how the block drifted in the first place."""
    got = _res_block(page)
    offenders = [st for st in got["inline"]
                 if any(k in (st or "") for k in
                        ("font-size", "padding", "border-radius", "height"))]
    assert not offenders, f"inline control styling in the block: {offenders}"


def test_the_takedown_verdict_keeps_its_colour_roles(page):
    """Uniformity must not flatten meaning. Yes/Untraceable carry green and
    amber and those are fixed roles on this card."""
    got = page.evaluate("""() => {
      const s = document.querySelector('[data-takedown-rec]');
      if (!s) return null;
      const out = {};
      for (const v of ['No', 'Yes', 'Untraceable']) {
        s.className = 'chip-select td-' + v.toLowerCase();
        out[v] = getComputedStyle(s).backgroundColor;
      }
      s.className = 'chip-select td-no';
      return out; }""")
    assert got, "the takedown verdict control is gone — NOT BUILT"
    assert len(set(got.values())) == 3, (
        f"the three verdicts no longer read apart: {got}")
