"""Two things that were working and that I broke making the labels descriptive.

Both reached a card. Neither is what was asked for — the ask was to swap the
heading field and include internal notes.
"""
import re
from datetime import datetime

from server.services.zendesk import _clip
from tests.conftest import read_source


# ── 1. the label cap fired on every descriptive label ──────────────────────

def test_a_descriptive_label_is_not_clipped():
    """The cap was 60, set when a label was a category word ("Tickets sent").
    Descriptive labels are lines, so every one of them overflowed and the
    reader was shown "[…cut at 60 chars]" inside a header."""
    label = "Duplicate-booking cancellation request sent to the supply partner"
    assert len(label) > 60, "the fixture no longer exercises the old cap"
    assert _clip(label, 120) == label, _clip(label, 120)


def test_the_shaper_uses_the_raised_cap():
    """DRIVES THE SHAPER'S OWN CAP, not `_clip`. `_clip` takes the cap as an
    argument, so a test calling it with 120 passes happily against a call site
    still passing 60 — a mutation dropping the cap back survived exactly that
    test. `clip_shaped_text` holds the numbers the shaper actually uses."""
    from server.services.zendesk import clip_shaped_text
    label = "Duplicate-booking cancellation request sent to the supply partner"
    assert len(label) > 60, "the fixture no longer exercises the old cap"
    got = clip_shaped_text({"label": label, "summary": "x"})
    assert got["label"] == label, got["label"]
    assert "cut at" not in got["label"]


def test_the_shaper_still_cuts_a_runaway_label():
    from server.services.zendesk import clip_shaped_text
    got = clip_shaped_text({"label": "word " * 60, "summary": "x"})
    assert "cut at" in got["label"], got["label"]


def test_a_summary_keeps_its_own_larger_cap():
    """A summary is content, not a header. The two caps must not collapse."""
    from server.services.zendesk import clip_shaped_text, LABEL_CAP, SUMMARY_CAP
    assert SUMMARY_CAP > LABEL_CAP
    body = "x" * (LABEL_CAP + 50)
    assert clip_shaped_text({"label": "L", "summary": body})["summary"] == body


def test_a_runaway_label_is_still_cut_and_says_so():
    """The cap is not removed. A bare "…" is how a model trails off, so a cut
    that does not announce itself is indistinguishable from the model's own
    phrasing."""
    out = _clip("word " * 60, 120)
    assert "cut at 120 chars" in out, out
    assert len(out) < 200, out


# ── 2. the review sorted to the top of its own day ─────────────────────────

def test_the_review_timestamp_keeps_its_clock_time():
    """`received_at` is a full datetime and the pipeline formatted it to
    "%Y-%m-%d". The row therefore reached the timeline as a bare "04 Aug",
    the client reads a missing clock as 00:00, and the review sorted ABOVE a
    booking created at 02:43 the same morning.

    NEGATIVE source assertion — client-side ordering has no harness here, and
    unreachability cannot defeat "this string appears nowhere"."""
    src = read_source("server/pipeline.py")
    i = src.index("_zd_pub_date  = ")
    line = src[i:i + 200]
    assert '"%Y-%m-%d %H:%M"' in line, line
    assert '"%Y-%m-%d")' not in line.split("\n")[0], line


def test_the_bigquery_date_parameter_stays_a_date():
    """The OTHER copy is a BigQuery date param and must not grow a time —
    they are two different facts that happen to share a source."""
    src = read_source("server/pipeline.py")
    i = src.index("pub_date = (review.received_at")
    assert '.strftime("%Y-%m-%d")' in src[i:i + 120], src[i:i + 120]


def test_a_formatted_publish_time_carries_a_clock():
    """Driven: what the prompt actually interpolates for the review row."""
    from server.prompts import _fmt_bookend_time
    got = _fmt_bookend_time("2026-08-04 09:41")
    assert re.search(r"\d{2}:\d{2}", got), \
        f"the review row would sort at the start of its day: {got!r}"


def test_a_date_only_source_still_degrades_rather_than_inventing_a_time():
    """Where there genuinely is no clock we must not fabricate one — the row
    shows a date and the reader can see that is all we have."""
    from server.prompts import _fmt_bookend_time
    got = _fmt_bookend_time("2026-08-04")
    assert not re.search(r"\d{2}:\d{2}", got), got
