"""A guest's reply is words the guest said, and it was rendered nowhere.

WHAT SHIPPED. A frame carries `guestSaid` for a message the guest opened with
and `guestReply` for one answering an agent. Both are correct and the prompt
asks for exactly that split. Every renderer then read `guestSaid` alone —
three places in slack.py, one in the prompt block, five in the page — so a
guest's reply drew as an EMPTY line under their own name:

    07 Aug 18:32 IST  guest    (blank)     guestReply: "Thanked agent"
    09 Aug 11:36 IST  guest    (blank)     guestReply: "Stated prices paid do
                                           not match those on official castle
                                           websites; acknowledged the credit"

`guestReply` reached the client's own normaliser and was rendered by nothing.

IT WAS MASKED FOR MONTHS. The model used to write "N/A — this is the guest's
reply event" into `guestSaid` — which was it telling us the content was in the
other field — and that sentence printed as though the guest had said it.
Blanking the commentary is what exposed the empty row underneath.
"""
import pytest

from server.services.zendesk import guest_words


def _f(**kw):
    base = {"actor": "guest", "thread": "email", "guestSaid": "",
            "weDid": "", "guestReply": ""}
    base.update(kw)
    return base


def test_a_reply_is_returned_when_that_is_where_the_words_are():
    assert guest_words(_f(guestReply="Thanked agent; no further questions.")) \
        == "Thanked agent; no further questions."


def test_an_opening_message_is_returned_from_guestSaid():
    assert guest_words(_f(guestSaid="Asked for the arrival time at Bran.")) \
        == "Asked for the arrival time at Bran."


def test_guestSaid_wins_when_a_frame_carries_both():
    """The opening message is what the row is ABOUT; the reply belongs to the
    next row. Preferring the reply would retitle the exchange."""
    assert guest_words(_f(guestSaid="Asked X.", guestReply="Thanked us.")) \
        == "Asked X."


def test_a_frame_with_neither_is_empty_not_a_placeholder():
    """An agent-side frame legitimately has no guest words, and "" is what
    lets the renderer draw nothing. A placeholder here would put a dash under
    the guest's name on every agent action."""
    assert guest_words(_f(weDid="Agent Shane confirmed the booking.")) == ""


def test_whitespace_only_counts_as_empty():
    """A field of spaces reads as content to `or` and draws a blank row that
    looks like a rendering fault rather than an absence."""
    assert guest_words(_f(guestSaid="   ", guestReply="Thanked us.")) \
        == "Thanked us."


@pytest.mark.parametrize("bad", [None, "", 42, [], "a string"])
def test_a_non_frame_is_empty_rather_than_an_exception(bad):
    """It runs inside the Slack composer and the card serialiser; raising here
    would take out a whole post over one malformed row."""
    assert guest_words(bad) == ""


# ── the wiring: every reader goes through it ────────────────────────────────

def test_the_server_stamps_the_resolved_value_for_the_page():
    """DRIVEN, not asserted from source. The client would otherwise pick
    between the two fields in JavaScript in five places, which is the drift
    `_marked_frames` was created to end — the same rule in Python and JS, and
    the JS copy is the one that renders."""
    from server.api import _marked_frames
    out = _marked_frames([_f(guestReply="Thanked agent."),
                          _f(actor="co", weDid="Agent replied.")])
    assert out[0]["guest_words"] == "Thanked agent."
    assert out[1]["guest_words"] == ""
    assert "guestReply" in out[0], "the raw fields must still ship"


def test_no_python_reader_reaches_for_guestSaid_alone():
    """NEGATIVE assertion, which CLAUDE.md allows: unreachability cannot
    defeat "this string appears nowhere". Any new `.get("guestSaid")` outside
    guest_words itself is a tenth call site waiting to drift."""
    import inspect
    from server.services import slack, zendesk
    from server import prompts, api
    for mod in (slack, prompts, api):
        src = inspect.getsource(mod)
        assert 'get("guestSaid")' not in src, \
            f"{mod.__name__} reads guestSaid directly again"
    assert 'get("guestSaid")' in inspect.getsource(zendesk.guest_words), \
        "guest_words no longer reads guestSaid at all"


def test_the_page_reads_the_stamped_field_first():
    """CLIENT-SIDE JAVASCRIPT, which has no test harness in this repo — the
    second case CLAUDE.md permits a source assertion for, said out loud here.
    The behaviour it stands in for is covered above, server-side, which is
    where the choice is now actually made."""
    page = open("client/index.html", encoding="utf-8").read()
    assert page.count("guest_words") >= 5, \
        "not every renderer reads the stamped field"
    # LINE BY LINE, not a lookbehind. The first version of this used a
    # variable-width lookbehind that silently matched nothing useful and
    # passed for the wrong reason — a check that cannot fail is the thing
    # this file exists to complain about.
    bad = [n for n, line in enumerate(page.splitlines(), 1)
           if ".guestSaid" in line and "guest_words" not in line]
    assert not bad, f"lines still read guestSaid without the stamped field: {bad}"
