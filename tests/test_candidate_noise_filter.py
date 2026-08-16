"""Possible matches are filtered per candidate, not per whole list.

`candidates_are_noise` rendered a whole-list verdict on a per-candidate
property: one candidate agreeing on venue kept the ENTIRE shortlist, so a review
naming a real venue (María Victoria's Sintra / Quinta de Regaleira) carried its
one real match plus every date-only booking beside it into the picker — the
population that scores on date proximity and belongs to someone else. Now the
filter drops the date-only rows and keeps the real ones, and says which.

Driven by calling `surviving_candidates` / `candidate_noise_verdict` — the
decision lives in functions, not inline in process_review.
"""
from server.pipeline import (surviving_candidates, candidate_noise_verdict,
                              candidates_are_noise)


def _agree(**kw):   # a candidate that agrees on a real signal
    d = {"score_venue": 0, "score_name": 0, "score_ticket": 0, "score_date": 0.1}
    d.update(kw)
    return d


def _date_only(date=0.9):
    return {"score_venue": 0, "score_name": 0, "score_ticket": 0, "score_date": date}


# ── surviving_candidates: per-candidate, not per-list ───────────────────────

def test_a_mixed_list_keeps_only_the_agreeing_candidate():
    """THE bug. One venue agreement + two date-only → keep exactly the one."""
    cands = [_agree(score_venue=8.0), _date_only(0.9), _date_only(0.8)]
    kept = surviving_candidates(cands)
    assert len(kept) == 1 and kept[0]["score_venue"] == 8.0


def test_venue_name_ticket_or_signal_each_keep_a_candidate():
    assert len(surviving_candidates([_agree(score_venue=1.0)])) == 1
    assert len(surviving_candidates([_agree(score_name=1.0)])) == 1
    assert len(surviving_candidates([_agree(score_ticket=1.0)])) == 1
    assert len(surviving_candidates([{"venue_signal": True}])) == 1


def test_a_date_only_candidate_with_subscores_is_dropped():
    assert surviving_candidates([_date_only(0.99)]) == []


def test_an_all_noise_list_survives_as_empty():
    assert surviving_candidates([_date_only(0.9), _date_only(0.8)]) == []


def test_a_candidate_with_no_subscores_is_never_dropped():
    """The escape hatch: a path that recorded no sub-scores cannot be shown to
    be noise, so dropping it would suppress somebody's only lead on a guess."""
    assert len(surviving_candidates([{"id": "a"}, {"id": "b"}])) == 2
    # and it survives even beside date-only rows that ARE dropped
    kept = surviving_candidates([{"id": "no_scores"}, _date_only(0.9)])
    assert [c.get("id") for c in kept] == ["no_scores"]


# ── candidate_noise_verdict: the three outcomes, said differently ───────────

def test_verdict_filtered_keeps_the_real_and_counts_the_withheld():
    v = candidate_noise_verdict([_agree(score_venue=8.0), _date_only(), _date_only()])
    assert v["state"] == "filtered"
    assert v["dropped"] == 2 and len(v["kept"]) == 1
    assert v["trail"] and "2 date-only" in v["trail"]["text"] and "1 kept" in v["trail"]["text"]


def test_verdict_all_noise_withholds_everything_with_the_old_wording():
    v = candidate_noise_verdict([_date_only(0.9), _date_only(0.8)])
    assert v["state"] == "all_noise" and v["kept"] == [] and v["dropped"] == 2
    assert v["trail"] and "none of" in v["trail"]["text"]


def test_verdict_clean_when_nothing_is_filler():
    v = candidate_noise_verdict([_agree(score_venue=8.0), _agree(score_name=2.0)])
    assert v["state"] == "clean" and v["trail"] is None and len(v["kept"]) == 2


def test_the_three_verdicts_do_not_read_the_same():
    filt = candidate_noise_verdict([_agree(score_venue=8.0), _date_only()])["trail"]["text"]
    noise = candidate_noise_verdict([_date_only(), _date_only()])["trail"]["text"]
    assert filt != noise                     # "some withheld" != "all withheld"
    assert candidate_noise_verdict([_agree(score_venue=8.0)])["trail"] is None  # != silence


# ── candidates_are_noise still means "the WHOLE list is noise" ──────────────

def test_candidates_are_noise_delegates_correctly():
    assert candidates_are_noise([_date_only(), _date_only()]) is True     # all noise
    assert candidates_are_noise([_agree(score_venue=8.0), _date_only()]) is False  # mixed keeps some
    assert candidates_are_noise([{"id": "no_scores"}]) is False           # escape hatch
    assert candidates_are_noise([]) is False                              # nothing to suppress
