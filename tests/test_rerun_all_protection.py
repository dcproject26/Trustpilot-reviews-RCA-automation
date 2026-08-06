"""A field the machine filled in is not human work.

rerun_all.py refuses to rebuild a draft that carries "human work", so that a
re-run cannot overwrite something a person wrote and nobody can get back.
`resolution` was on that list.

But api.py PREFILLS `resolution` from the DSS recommendation's compensation
line whenever it is empty. So every draft that reached the DSS step carried
one, without a person ever touching it — and the tool reported

    protected (45) - human work would be lost:
      ...
    would delete and rebuild 0 draft(s)

on a database where no human had edited anything. Forty-five rows protected
from the tool whose only job is rebuilding them, and the message said the
opposite of what was true.

The distinguishing fact was available the whole time: compare the stored
resolution against what the prefill would have written.
"""
import importlib.util
import pathlib
import sys

import pytest

_spec = importlib.util.spec_from_file_location(
    "rerun_all", pathlib.Path("tools/rerun_all.py"))
rerun_all = importlib.util.module_from_spec(_spec)
sys.modules["rerun_all"] = rerun_all
_spec.loader.exec_module(rerun_all)


class _Draft:
    def __init__(self, **kw):
        self.final_response = ""
        self.slack_thread_override = ""
        self.resolution = ""
        self.dss_rec = None
        self.sent_at = None
        self.rca_v3_edited_at = None
        self.__dict__.update(kw)


class _Review:
    def __init__(self, status="draft"):
        self.status = status


PREFILL = "Refund the booking fee and apologise."


# ── the case that shipped ──────────────────────────────────────────────────

def test_a_prefilled_resolution_is_not_human_work():
    d = _Draft(resolution=PREFILL, dss_rec={"compensation": PREFILL})
    assert rerun_all._has_human_work(_Review(), d) == [], (
        "a resolution the DSS prefill wrote is being counted as something a "
        "person would lose — this protected all 45 drafts and rebuilt none")


def test_the_prefilled_case_is_reported_rather_than_silently_skipped():
    """The row LOOKS edited. A reader who saw "45 protected" yesterday and
    "0 protected" today deserves to know which rule moved."""
    d = _Draft(resolution=PREFILL, dss_rec={"compensation": PREFILL})
    assert rerun_all._prefilled_only(_Review(), d) is True


def test_whitespace_does_not_make_it_look_edited():
    d = _Draft(resolution=f"  {PREFILL}  ", dss_rec={"compensation": PREFILL})
    assert rerun_all._has_human_work(_Review(), d) == []


# ── and real human work is still protected ─────────────────────────────────

def test_an_edited_resolution_is_still_human_work():
    d = _Draft(resolution="Refunded in full, plus a voucher — agreed with Ops.",
               dss_rec={"compensation": PREFILL})
    assert "resolution" in rerun_all._has_human_work(_Review(), d)


def test_a_resolution_with_no_dss_prefill_behind_it_is_human_work():
    """Nothing to compare against means we cannot show the machine wrote it,
    and the safe reading of an unprovable case is to protect it."""
    d = _Draft(resolution="Called the guest and refunded.", dss_rec=None)
    assert "resolution" in rerun_all._has_human_work(_Review(), d)
    assert rerun_all._prefilled_only(_Review(), d) is False


def test_a_dss_rec_of_the_wrong_shape_does_not_crash_or_unprotect():
    d = _Draft(resolution="something", dss_rec="not a dict")
    assert "resolution" in rerun_all._has_human_work(_Review(), d)


def test_an_empty_compensation_does_not_match_an_empty_resolution():
    """"" == "" would report every draft as prefilled, which is the same bug
    pointing the other way — a written resolution rebuilt as if machine-made."""
    d = _Draft(resolution="", dss_rec={"compensation": ""})
    assert rerun_all._has_human_work(_Review(), d) == []
    assert rerun_all._prefilled_only(_Review(), d) is False, \
        "an absent resolution is being reported as a prefill that was found"


@pytest.mark.parametrize("field", ["final_response", "slack_thread_override"])
def test_the_other_human_fields_are_untouched_by_this(field):
    d = _Draft(**{field: "written by a person"})
    assert field in rerun_all._has_human_work(_Review(), d)


def test_a_sent_review_is_still_protected():
    assert "sent" in rerun_all._has_human_work(_Review(status="sent"), _Draft())


def test_an_edited_rca_body_is_still_protected():
    from datetime import datetime
    d = _Draft(rca_v3_edited_at=datetime.utcnow())
    assert "rca edited" in rerun_all._has_human_work(_Review(), d)


# ── the report has to name every bucket, including the failure one ──────────

def test_the_summary_prints_the_bucket_that_means_the_rebuild_failed(capsys):
    """This tool DELETES every draft row and rebuilds it. A review whose
    rebuild did not finish has no draft row, which is `processing` — and
    `processing` was the one bucket the summary did not print, while `(total)`
    counted it. Seven destroyed drafts read as a healthy line with arithmetic
    that did not add up, and the reader had to spot the difference."""
    from collections import Counter
    rerun_all._show("after", Counter({"identified": 3, "candidates": 1,
                                      "untraceable": 2, "sent": 1,
                                      "processing": 7}))
    line = capsys.readouterr().out
    assert "processing 7" in line, \
        f"the failure bucket is missing from the summary: {line.strip()!r}"


def test_every_bucket_the_rule_can_produce_is_named(capsys):
    """Not a fixed list — the one in the code went stale the moment a bucket
    was added. Driven off the rule's own vocabulary."""
    from collections import Counter
    from server.tiers import BUCKETS
    rerun_all._show("after", Counter({b: 1 for b in BUCKETS}))
    line = capsys.readouterr().out
    missing = [b for b in BUCKETS if f"{b} 1" not in line]
    assert not missing, f"{missing} never reach the summary: {line.strip()!r}"


def test_a_bucket_the_summary_does_not_know_is_printed_not_dropped(capsys):
    """An unfamiliar name is a finding. Silently omitting it is how the
    original list went stale without anything failing."""
    from collections import Counter
    rerun_all._show("after", Counter({"identified": 1, "some_new_bucket": 2}))
    line = capsys.readouterr().out
    assert "some_new_bucket 2" in line, \
        f"an unknown bucket vanished from the summary: {line.strip()!r}"
    assert "(total 3)" in line
