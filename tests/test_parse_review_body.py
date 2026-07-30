"""Ingestion must never store a partial review.

A review that arrives truncated can never be matched or RCA'd on what is
missing, and the loss is invisible: the card looks like a short review.
"""
from server.services.slack import parse_review


def _ev(**over):
    base = {"ts": "1.0", "channel": "C1", "text": "", "blocks": [], "attachments": []}
    base.update(over)
    return base


def test_multiple_section_blocks_are_all_kept():
    ev = _ev(blocks=[
        {"type": "section", "text": {"text": "*Jessica Laes*"}},
        {"type": "section", "text": {"text": "Booked package cancelled 24 hours beforehand."}},
        {"type": "section", "text": {"text": "We booked tours for St. Mark's Basilica and "
                                            "the Doge's Palace in Venice through Headout."}},
    ])
    out = parse_review(ev)
    assert out["author"] == "Jessica Laes"
    body = out["body_original"]
    assert "cancelled 24 hours beforehand" in body, body
    assert "Doge's Palace" in body, "the LAST block survived but earlier ones were dropped"
    assert "St. Mark's Basilica" in body


def test_text_beside_the_author_in_one_block_survives():
    ev = _ev(blocks=[
        {"type": "section", "text": {"text": "*Jessica Laes* left a 1-star review "
                                             "about the Venice tour"}},
    ])
    out = parse_review(ev)
    assert out["author"] == "Jessica Laes"
    assert "Venice tour" in out["body_original"], (
        "the author block was discarded whole, taking its sentences with it")


def test_never_returns_less_than_the_flat_text():
    long_text = "A very long flattened review body " * 6
    ev = _ev(text=long_text, blocks=[{"type": "section", "text": {"text": "short"}}])
    out = parse_review(ev)
    assert len(out["body_original"]) >= len(long_text) - 1, (
        "block reconstruction must never shrink the body below message text")


def test_headline_from_attachment_is_prepended():
    ev = _ev(attachments=[{"title": "Overpriced Acropolis tickets",
                           "text": "The queue was two hours long."}])
    out = parse_review(ev)
    assert out["body_original"].startswith("Overpriced Acropolis tickets")
    assert "queue was two hours" in out["body_original"]


def test_stars_and_bid_still_extracted():
    ev = _ev(text="booking 32908218",
             attachments=[{"footer": "★✩✩✩✩ Not verified"}])
    out = parse_review(ev)
    assert out["rating"] == 1
    assert out["reference_number"] == "32908218"
