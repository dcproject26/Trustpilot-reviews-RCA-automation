"""Every control in the RCA column does what it looks like it does.

Three of them did not, and all three failed the same way: the control
rendered, looked correct, and was bound to nothing. There is no visual
difference between a wired button and an unwired one, so none of them were
noticed until someone clicked.

  * the section collapse — step 5 put a hardcoded chevron in most section
    templates, and the post-render pass read "already has a chevron" as
    "already wired" and returned before binding;
  * the internal-events toggle — §13 moved the timeline into the RCA column
    and left the binding querying the review column;
  * "+ Add" — see below.

This file clicks things. It is the only kind of test that can tell a live
control from a dead one.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


SECTIONS = ["#rca-events-timeline-section", "#rca-flags-section",
            "#rca-wwr5-section", "#rca-tldr-section", "#rca-sop-section",
            "#rca-issue-answers-section"]


def _collapsed(page, sel):
    return page.evaluate(
        "(s) => { const e = document.querySelector(s);"
        "return e ? e.classList.contains('is-collapsed') : null; }", sel)


@pytest.mark.parametrize("sel", SECTIONS)
def test_every_section_label_collapses_when_clicked(page, sel):
    page.evaluate("(s) => { const e = document.querySelector(s);"
                  "e.classList.remove('is-collapsed'); }", sel)
    page.click(f"{sel} .section-label")
    page.wait_for_timeout(200)
    got = _collapsed(page, sel)
    page.click(f"{sel} .section-label")
    page.wait_for_timeout(200)
    back = _collapsed(page, sel)
    assert got is True, f"{sel} did not collapse — the chevron is decoration"
    assert back is False, f"{sel} collapsed and would not reopen"


def test_a_hardcoded_chevron_is_not_duplicated(page):
    """The guard that broke the binding was there to stop a second chevron
    being appended. Splitting the two jobs must not bring the duplicate back."""
    n = page.evaluate("""() => Math.max(...[...document.querySelectorAll(
      '#rca-col .section-label')].map(l => l.querySelectorAll('.section-chev').length))""")
    assert n == 1, f"a section label carries {n} chevrons"


def test_every_section_in_the_rca_column_is_collapsible(page):
    """Not a sample — all of them. The bug hit every section with a template
    chevron at once, so testing three would have looked like bad luck."""
    dead = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('#rca-col .section').forEach(sec => {
        const label = sec.querySelector('.section-label');
        if (!label) return;
        const before = sec.classList.contains('is-collapsed');
        label.click();
        if (sec.classList.contains('is-collapsed') === before)
          out.push((label.innerText || '').split('\\n')[0].trim());
        else label.click();
      });
      return out; }""")
    assert dead == [], f"these section labels are inert: {dead}"


# ── the + Add paths ─────────────────────────────────────────────────────────

ADDS = [
    ("[data-contact-add]", ".convo-frame"),
    ("[data-flag-add]", "#rca-flags-section .chk-row.chk-flag"),
    ("[data-aoi-add]", ".rca-point"),
    ("[data-sp-rec-add]", ".sp-frame"),
]


@pytest.mark.parametrize("btn,row", ADDS)
def test_every_add_button_produces_a_row(page, btn, row):
    """"+ Add" that silently does nothing is the same failure as a validator
    wired to nothing: the click is accepted, and the absence of a new row is
    indistinguishable from not having clicked."""
    if not page.evaluate("(s) => !!document.querySelector(s)", btn):
        pytest.skip(f"{btn} is not on this fixture")
    before = page.evaluate("(s) => document.querySelectorAll(s).length", row)
    page.click(btn)
    page.wait_for_timeout(700)
    after = page.evaluate("(s) => document.querySelectorAll(s).length", row)
    assert after == before + 1, \
        f"{btn} left the row count at {after} (was {before})"


def test_the_contact_count_matches_the_contacts_under_it(page):
    """The heading read "0 contacts" above eight rendered rows: it counted
    v3d.support_interaction while the rows came from the frames plus notes."""
    got = page.evaluate("""() => {
      const sec = [...document.querySelectorAll('#rca-col .section')].find(
        s => /guest . support/i.test(s.querySelector('.section-label')?.innerText || ''));
      if (!sec) return null;
      const hint = sec.querySelector('.hint');
      return {said: parseInt((hint?.innerText || '').trim()),
              shown: sec.querySelectorAll('.convo-frame').length}; }""")
    assert got, "the guest ↔ support section did not render"
    assert got["said"] == got["shown"], \
        f"the heading says {got['said']} contacts over {got['shown']} rows"


# ── an absent value says which lookup came up empty ─────────────────────────

def _guest_row(page):
    return page.evaluate("""() => {
      const rows = [...document.querySelectorAll('#review-col .detail-row')];
      const row = rows.find(r => /Primary guest/.test(r.querySelector('.k')?.innerText||''));
      if (!row) return null;
      const v = row.querySelector('.v');
      const abs = v.querySelector('.v-absent');
      return {text: v.innerText.trim(), absent: !!abs,
              colour: abs ? getComputedStyle(abs).color : null}; }""")


def _set_guest(page, name, note):
    page.evaluate("""([n, note]) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r._kG = r.booking.primaryGuestName; r._kN = r.booking.guestNameNote;
      r.booking.primaryGuestName = n; r.booking.guestNameNote = note;
      renderReviewCol(); }""", [name, note])


def _reset_guest(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r.booking.primaryGuestName = r._kG; r.booking.guestNameNote = r._kN;
      renderReviewCol(); }""")


def test_a_missing_guest_name_says_which_lookup_failed(page):
    """"[Guest name in Zendesk ticket]" was a sentence in the value column: it
    looked like data, and it made three situations identical — the warehouse
    holds a hash, the linked ticket has no requester, and no ticket was ever
    matched. Only the first two are worth opening Zendesk for."""
    _set_guest(page, "", "no Zendesk ticket was matched to this booking")
    got = _guest_row(page)
    _reset_guest(page)
    assert got, "the Primary guest row did not render"
    assert "[Guest name in Zendesk ticket]" not in got["text"]
    assert "no Zendesk ticket was matched" in got["text"]
    assert got["absent"], "the reason is styled as if it were the guest's name"


def test_the_reason_is_not_styled_as_a_value(page):
    _set_guest(page, "", "the warehouse stores this as a hash")
    absent = _guest_row(page)
    _set_guest(page, "Lewis MacAndrew", "")
    real = _guest_row(page)
    _reset_guest(page)
    assert absent["colour"] != real["colour"], \
        "an absence and a name are the same colour"
    assert not real["absent"]
    assert real["text"] == "Lewis MacAndrew"


def test_a_stale_placeholder_from_an_older_draft_is_not_shown_as_a_name(page):
    """Drafts written before this change hold the literal placeholder string in
    primaryGuestName. Rendering it verbatim would put the old bug back on
    screen for every one of them."""
    _set_guest(page, "[Guest name in Zendesk ticket]", "")
    got = _guest_row(page)
    _reset_guest(page)
    assert "[Guest name in Zendesk ticket]" not in got["text"], got
