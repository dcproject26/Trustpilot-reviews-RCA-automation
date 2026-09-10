"""The RCA post carries the guest's review — TRANSLATED — for non-English
reviews only.

The post is otherwise guest-copy-free by design (format_rca_slack: "Never
includes the guest response copy"). This is the one deliberate exception: a
reader of a French/German/… case must see what the guest actually wrote without
leaving Slack. The translation itself already exists — pipeline step 1 stores it
as body_english — so this only SURFACES it; nothing new is translated.

Driven against the real helper and the real formatter, not a source grep. The
dashboard preview mirrors this in client/index.html::_genSlackText; that JS has
no harness here (CLAUDE.md rule 2), so this file is the tested copy and the two
are kept in step by hand.
"""
from types import SimpleNamespace

from server.services.slack import (translated_review_block, _is_non_english,
                                    format_rca_slack)

# Reuse the v3 draft fixture from the sibling test — same shape the formatter
# needs to reach the section list.
from tests.test_slack_v3_format import _draft


HEADING = "*Review (translated)*"


def _review(**over):
    base = dict(rating=1, author="David", language="fr",
                body_original="Les billets sont arrivés en retard.",
                body_english="The tickets arrived late.")
    base.update(over)
    return SimpleNamespace(**base)


# ── the pure helper: which reviews get the block, and what it says ──────────

def test_non_english_with_translation_returns_the_english_text():
    assert translated_review_block("fr", "The tickets arrived late.") \
        == "The tickets arrived late."
    assert translated_review_block("DE", "  Trimmed.  ") == "Trimmed."


def test_english_and_unknown_get_nothing():
    # English in any spelling -> "" so the caller drops the section.
    for lang in ("en", "EN", "eng", "English", "english"):
        assert translated_review_block(lang, "whatever") == ""
    # No language recorded is NOT non-English — it is unestablished, and must
    # not get a translation heading slapped on a possibly-English review.
    for lang in ("", None, "   ", "unknown", "UNKNOWN"):
        assert translated_review_block(lang, "whatever") == ""


def test_non_english_with_missing_translation_is_a_visible_marker_not_a_drop():
    # A foreign review whose translation failed/was refused (body_english empty)
    # is a GAP the reader must see — never a section that silently vanishes
    # (CLAUDE.md rule 1). It must NOT return "" (which would drop the section).
    for empty in ("", "   ", None):
        got = translated_review_block("fr", empty)
        assert got == "— translation unavailable —"
        assert got != ""


def test_is_non_english_predicate():
    assert _is_non_english("fr") is True
    assert _is_non_english("de") is True
    assert _is_non_english("en") is False
    assert _is_non_english("") is False
    assert _is_non_english(None) is False
    assert _is_non_english("unknown") is False


# ── the whole formatter: the section appears in the right place, or not ─────

def test_non_english_review_posts_the_translated_block():
    out = format_rca_slack(_review(), _draft())
    assert HEADING in out
    assert "The tickets arrived late." in out
    # The ORIGINAL foreign text is NOT what goes out — only the English.
    assert "Les billets sont arrivés en retard." not in out


def test_english_review_posts_no_translated_block():
    out = format_rca_slack(_review(language="en", body_english=""), _draft())
    assert HEADING not in out


def test_non_english_without_translation_posts_the_marker():
    out = format_rca_slack(_review(body_english=""), _draft())
    assert HEADING in out
    assert "— translation unavailable —" in out


def test_the_block_sits_after_booking_details_before_what_went_wrong():
    out = format_rca_slack(_review(), _draft())
    i_book = out.find("*Booking details*")
    i_rev  = out.find(HEADING)
    i_wwr  = out.find("*What went wrong*")
    assert -1 < i_book < i_rev < i_wwr, (i_book, i_rev, i_wwr)
