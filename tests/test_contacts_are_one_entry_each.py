"""A guest contact is ONE logged entry, and one composer writes it.

TWO DEFECTS, ONE SECTION.

The first is what the reader saw: a contact rendered its head line and then
re-listed every message underneath, so a nine-message chat became ten lines
saying the same thing at nine timestamps. The Events timeline is the per-event
view; this section is the per-contact one, and it was doing both jobs badly.

The second is why the dashboard preview looked WORSE than the posted text.
client/index.html composed this section itself, reading
`rca_v3["support_interaction"]` — a key the notes/frames split had renamed to
`support_interaction_notes`. A missing key is undefined, undefined is falsy, so
the ternary took its fallback arm on every v4 draft and dumped the raw frames:
one bullet per Zendesk event, with no conversation filter, so the booking thread
went out as something the guest said. Neither arm said a word about it — the
first rule of CLAUDE.md, in a renderer.

The fix for the second is structural rather than a corrected key: the section is
composed server-side and rendered verbatim, as `wwr_slack_text` and
`booking_details_text` already are. One composer cannot disagree with itself.
"""
import pathlib
import pytest

from server.services.slack import contacts_section


class _Draft:
    def __init__(self, frames):
        self.support_interaction_frames = frames


def _sec(frames, notes=None):
    v3 = {} if notes is None else {"support_interaction_notes": notes}
    return contacts_section(_Draft(frames), v3, "\n")


CHAT = {"ticket_id": "4491", "thread": "chat", "actor": "guest"}


# ── one entry per contact ───────────────────────────────────────────────────

def test_a_summarised_contact_does_not_relist_its_messages():
    """THE POINT. Four messages on one ticket produce one entry."""
    frames = [dict(CHAT, time=f"10 Aug 13:2{i}", guestSaid=f"message {i}",
                   weDid=f"reply {i}") for i in range(4)]
    out = _sec(frames, [{"zd_ref": "ZD-4491",
                         "summary": "Guest chased the voucher.",
                         "detail": "Guest asked where the tickets were; "
                                   "Tia resent them."}])
    assert out.count("• 01.") == 1
    assert "• 02." not in out
    for i in range(4):
        assert f"message {i}" not in out, (
            f"message {i} was re-listed under its own contact's summary")
        assert f"reply {i}" not in out
    assert "Guest asked where the tickets were; Tia resent them." in out


def test_several_chats_are_numbered_chronologically():
    """Three tickets, three entries, numbered in the order they happened."""
    frames = [{"ticket_id": "1", "thread": "chat", "time": "10 Aug 09:00",
               "guestSaid": "first"},
              {"ticket_id": "2", "thread": "chat", "time": "11 Aug 09:00",
               "guestSaid": "second"},
              {"ticket_id": "3", "thread": "chat", "time": "12 Aug 09:00",
               "guestSaid": "third"}]
    out = _sec(frames)
    assert out.index("• 01.") < out.index("• 02.") < out.index("• 03.")


def test_the_detail_is_the_account_of_the_whole_exchange():
    """`detail` is written about the contact, not about one message — it is
    what replaces the per-message list."""
    out = _sec([dict(CHAT, time="10 Aug 13:21", guestSaid="hi")],
               [{"zd_ref": "ZD-4491", "summary": "s",
                 "detail": "line one\nline two"}])
    assert "   line one" in out and "   line two" in out


# ── the failures a contact carries ──────────────────────────────────────────

def test_one_gap_on_four_messages_is_reported_once():
    """A gap is recorded per FRAME but the reader is being told about a
    CONTACT. Repeating the same label four times describes how chatty the
    exchange was, not how it failed."""
    frames = [dict(CHAT, time=f"10 Aug 13:2{i}", guestSaid=f"m{i}",
                   gap="Wrong policy applied") for i in range(4)]
    out = _sec(frames, [{"zd_ref": "ZD-4491", "summary": "s", "detail": "d"}])
    assert out.count("Wrong policy applied") == 1


def test_two_different_gaps_are_both_kept():
    """Deduping must not become swallowing — distinct failures are distinct."""
    frames = [dict(CHAT, time="10 Aug 13:21", guestSaid="a",
                   gap="Wrong policy applied"),
              dict(CHAT, time="10 Aug 13:22", guestSaid="b",
                   gap="CE escalation missing")]
    out = _sec(frames, [{"zd_ref": "ZD-4491", "summary": "s", "detail": "d"}])
    assert "Wrong policy applied" in out and "CE escalation missing" in out


def test_the_ce_miss_is_on_the_contact():
    out = _sec([dict(CHAT, time="10 Aug 13:21", guestSaid="hi")],
               [{"zd_ref": "ZD-4491", "summary": "s", "detail": "d",
                 "ce_miss": "No escalation raised."}])
    assert "⚠ CE miss: No escalation raised." in out


# ── an unsummarised contact must not look like a summarised one ─────────────

def test_a_contact_with_no_note_says_so_before_falling_back():
    """CLAUDE.md rule 1. A contact the model never summarised and one it
    summarised in a line must not render identically — that is a failed zd_ref
    join reading as a terse model."""
    frames = [dict(CHAT, time=f"10 Aug 13:2{i}", guestSaid=f"m{i}")
              for i in range(3)]
    out = _sec(frames)                      # no notes at all
    assert "no summary generated for this contact" in out
    assert "3 raw events" in out
    for i in range(3):
        assert f"m{i}" in out, "an event was swallowed by the grouping"


def test_the_fallback_is_not_used_when_a_note_exists():
    out = _sec([dict(CHAT, time="10 Aug 13:21", guestSaid="hi")],
               [{"zd_ref": "ZD-4491", "summary": "s", "detail": "d"}])
    assert "no summary generated" not in out


def test_machinery_is_still_filtered_out():
    """The booking thread is not a guest contact. This is the line the client's
    fallback arm never applied, which is how a booking dump was posted as
    something the guest said."""
    frames = [{"thread": "booking", "time": "10 Aug 13:21", "actor": "system",
               "guestSaid": "Montserrat Monastery; 2 Adults; vendor API"},
              dict(CHAT, time="10 Aug 13:30", guestSaid="a real question")]
    out = _sec(frames)
    assert "vendor API" not in out
    assert "a real question" in out


def test_no_contact_at_all_is_a_sentence_not_a_blank():
    assert "No guest contact found on this booking" in _sec([])


# ── one composer ────────────────────────────────────────────────────────────

V4_NOTES = {"support_interaction_notes": [
    {"zd_ref": "ZD-4491", "summary": "Guest chased the voucher.",
     "detail": "The whole exchange, in prose.",
     "ce_miss": "No escalation raised."}]}


def test_the_api_serves_the_composed_section():
    """THE WIRING, not the composer.

    This test used to call `_contacts_slack_text()` directly, and passed
    happily against a build with the payload line deleted — mutation testing
    caught it, and it is CLAUDE.md rule 1 word for word: a composer wired into
    no payload looks exactly like one that works. The card would have shown the
    "not served by this build" fallback with a green suite behind it.

    So it drives `_draft_dict`, the real serialiser the endpoint returns.
    """
    from server.api import _draft_dict
    from tests.test_wwr_one_composer import _draft

    d = _draft(support_interaction_frames=[
        dict(CHAT, time="10 Aug 13:21", guestSaid="hi")])
    served = _draft_dict(d)
    assert "contacts_slack_text" in served, (
        "the draft payload carries no contacts_slack_text — the dashboard "
        "renders this key, so the section is gone from the card")
    assert "• 01." in served["contacts_slack_text"]


def test_the_card_and_the_post_carry_the_same_contacts_text():
    """The agreement guarantee, as test_wwr_one_composer makes it for its own
    section: the string the dashboard renders IS the string that gets posted,
    because there is one composer rather than two kept in step by hand."""
    from server.api import _draft_dict
    from server.services.slack import format_rca_slack
    from tests.test_wwr_one_composer import _draft, REVIEW as _REV

    d = _draft(support_interaction_frames=[
        dict(CHAT, time="10 Aug 13:21", guestSaid="hi", weDid="resent")],
        rca_v3=dict(V4_NOTES))
    served = _draft_dict(d)["contacts_slack_text"]
    assert served, "the draft served no contacts text at all"
    assert served in format_rca_slack(_REV, d), (
        "the text the dashboard renders is not the text that gets posted:\n"
        f"--- served ---\n{served}")


def test_a_composer_failure_names_itself_rather_than_killing_the_card():
    """The card is how anyone finds out anything about a review. Losing all of
    it to one malformed note is worse than a section carrying an error."""
    from server.api import _contacts_slack_text

    class _Boom:
        @property
        def support_interaction_frames(self):
            raise ValueError("boom")

    out = _contacts_slack_text(_Boom(), {})
    assert "could not be composed" in out and "boom" in out


CLIENT = pathlib.Path("client/index.html").read_text(encoding="utf-8")

# Comments stripped — both `//` and `<!-- -->`, because this file DOCUMENTS the
# key that caused the defect, in two places: the composer's own scar, and an
# older one where the same key made the heading say "0 contacts" above eight
# rendered rows. A bare substring search cannot tell the warning from the crime,
# and a guard that trips on its own explanation gets the explanation deleted.
import re as _re
CLIENT_CODE = "\n".join(
    ln for ln in _re.sub(r"<!--.*?-->", "", CLIENT, flags=_re.S).split("\n")
    if not ln.strip().startswith("//"))


def test_the_client_no_longer_composes_this_section():
    """NEGATIVE source assertion on client-side JavaScript, which has no test
    harness here (CLAUDE.md rule 2 permits both).

    `v3d.support_interaction` is the key that silently did not exist. Reading it
    again — by restoring this composer or by copying the pattern — reintroduces
    the exact defect, and every Python test above would still pass.
    """
    assert "v3d.support_interaction" not in CLIENT_CODE, (
        "the client composes the contacts section again, from the key whose "
        "absence made it fall through to an unfiltered per-frame dump")


def test_the_client_renders_the_servers_string():
    """Source assertion, client-side JS, no harness — as above."""
    assert "rca.contactsSlackText" in CLIENT_CODE
    assert "draft.contacts_slack_text" in CLIENT_CODE


# ── the card, driven ────────────────────────────────────────────────────────
#
# Not a source assertion: the reorder and the dedup below are behaviour, and
# behaviour that only a rendered page can confirm. The rows really are built by
# _contactRow here, so an unreachable branch cannot pass these.

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _render_with_frames(page, frames, note):
    """Put one contact's frames and note on the selected review and redraw."""
    page.evaluate("""([frames, note]) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__gKeep === undefined)
        window.__gKeep = JSON.parse(JSON.stringify(
          [r.rca.supportFrames, r.rca.supportNotes]));
      r.rca.supportFrames = frames;
      r.rca.supportNotes  = [note];
      r.rca.v3.support_interaction_notes = [note];
      renderRcaCol();
      // open every contact so the body is in the DOM
      document.querySelectorAll('[data-ix-toggle]').forEach(e => e.click());
    }""", [frames, note])
    page.wait_for_timeout(400)


def _restore_frames(page):
    page.evaluate("""() => {
      if (window.__gKeep === undefined) return;
      const r = REVIEWS.find(x => x.id === state.selected);
      r.rca.supportFrames = window.__gKeep[0];
      r.rca.supportNotes  = window.__gKeep[1];
      r.rca.v3.support_interaction_notes = window.__gKeep[1];
      window.__gKeep = undefined;
      renderRcaCol(); }""")
    page.wait_for_timeout(200)


_FR = [{"ticket_id": "4491", "thread": "chat", "is_contact": True,
        "time": f"10 Aug 13:2{i}", "guest_words": f"m{i}", "weDid": f"w{i}",
        "gap": "Wrong policy applied"} for i in range(4)]
_NOTE = {"zd_ref": "ZD-4491", "summary": "Guest chased the voucher.",
         "detail": "The whole exchange, in prose.",
         "ce_miss": "No escalation raised."}


def test_the_card_reports_one_gap_once_not_once_per_message(page):
    """Four messages carrying one label were four identical red boxes stacked
    down the card. The contact failed one way, not four."""
    try:
        _render_with_frames(page, _FR, _NOTE)
        html = page.evaluate(
            "() => [...document.querySelectorAll('.interactions')]"
            ".map(e => e.innerHTML).join('')")
        assert html.count("Wrong policy applied") == 1, (
            "the gap was repeated once per message")
    finally:
        _restore_frames(page)


def test_the_card_leads_with_the_account_then_the_failures(page):
    """The summarised account and the misses come first; the raw messages sit
    beneath as the drill-down. Reading order should match the Slack post."""
    try:
        _render_with_frames(page, _FR, _NOTE)
        html = page.evaluate(
            "() => [...document.querySelectorAll('.interactions')]"
            ".map(e => e.innerHTML).join('')")
        assert (html.index("The whole exchange, in prose.")
                < html.index("Wrong policy applied")
                < html.index("CE miss")
                < html.index("m0")), "the card's reading order regressed"
    finally:
        _restore_frames(page)
