"""Every collapsible section collapses, in every column.

The chevron on BOOKING TIMELINE was drawn by its template and did nothing.
The collapse pass walked one render root and bound a click listener per
section it found — and §13 moved the booking timeline into the FACTS column,
which that pass never reaches. So the control rendered, looked identical to
every working one, and was dead.

That is the third control in this file to die the same way: markup written by
one render pass, handler bound by another. Both previous fixes were
delegation, and this is the third. A per-render binding is only ever as
complete as the root it was handed, and this file has three render functions
writing sections.

Driven in the browser against the real page: find every section that draws a
chevron, click its label, and assert it actually collapsed. A test that only
checked the RCA column would have passed throughout the bug.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _sections(page):
    """Every section on the page that offers a chevron, with its column."""
    return page.evaluate("""() => Array.from(
        document.querySelectorAll('.section'))
        .filter(s => s.querySelector('.section-label .section-chev'))
        .map(s => ({
          id: s.id || '',
          label: (s.querySelector('.section-label span') || {}).textContent || '',
        }))""")


def test_the_page_actually_has_collapsible_sections(page):
    """The guard on every test below. If the selector stops matching, they all
    pass by examining nothing — which is how the dead chevron survived."""
    got = _sections(page)
    assert len(got) >= 5, f"only {len(got)} collapsible section(s) found: {got}"


def test_every_chevron_section_collapses_when_its_label_is_clicked(page):
    """The bug: the booking timeline's chevron was drawn and unbound. Any
    section that SHOWS a chevron is promising it can be collapsed."""
    dead = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('.section').forEach(s => {
        if (!s.querySelector('.section-label .section-chev')) return;
        const label = s.querySelector('.section-label');
        const before = s.classList.contains('is-collapsed');
        label.click();
        const after = s.classList.contains('is-collapsed');
        if (before === after) out.push(s.id || (label.textContent || '').trim().slice(0, 40));
        else label.click();   // put it back
      });
      return out; }""")
    assert dead == [], f"chevron drawn but nothing happens on click: {dead}"


def test_the_booking_timeline_specifically_collapses(page):
    """Named, because it is the one that was broken and it lives in the column
    the old binding could not reach."""
    got = page.evaluate("""() => {
      const s = document.querySelector('#rca-booking-logs-section');
      if (!s) return 'section missing';
      const label = s.querySelector('.section-label');
      if (!label) return 'label missing';
      label.click();
      const collapsed = s.classList.contains('is-collapsed');
      label.click();
      return collapsed ? 'collapses' : 'dead'; }""")
    assert got == "collapses", got


def test_collapsing_survives_a_re_render(page):
    """State is keyed so a re-render does not reopen a section the reader
    closed. A toggle that works once and forgets is a different bug wearing
    the same face."""
    got = page.evaluate("""() => {
      const s = document.querySelector('#rca-booking-logs-section');
      s.querySelector('.section-label').click();
      renderReviewCol(); renderRcaCol();
      const after = document.querySelector('#rca-booking-logs-section');
      const still = after && after.classList.contains('is-collapsed');
      if (after) { after.querySelector('.section-label').click(); }
      return !!still; }""")
    assert got is True, "the section reopened on the next render"


def test_a_click_on_a_control_inside_the_head_does_not_collapse(page):
    """Section heads carry live controls. Swallowing their clicks would make
    the control look broken instead — the same defect pointing the other way.
    """
    got = page.evaluate("""() => {
      const btn = document.querySelector('.section-label button, .section-label select');
      if (!btn) return 'no control in any head';
      const sec = btn.closest('.section');
      const before = sec.classList.contains('is-collapsed');
      btn.click();
      return sec.classList.contains('is-collapsed') === before ? 'unchanged' : 'collapsed'; }""")
    assert got in ("unchanged", "no control in any head"), got
