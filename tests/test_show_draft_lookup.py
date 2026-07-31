"""The checkpoint tool has to find the row you name.

`--bid 32908218` answered "no draft found" for a booking that was there: the
lookup keyed on `bookingId`, and nothing writes that — the warehouse writes
`id`. The answer was also the same sentence a genuinely missing draft gets, so
there was nothing to tell a broken lookup from an absent row.

This is the tool the whole v4 checkpoint runs through, and it had no test.
"""
import importlib
import os
import subprocess
import sys
import tempfile
from datetime import datetime

import pytest

sys.path.insert(0, "tools")
import show_draft                                          # noqa: E402


class _D:
    def __init__(self, booking):
        self.booking = booking


# ── the key the warehouse actually writes ───────────────────────────────────

def test_the_booking_id_is_read_from_the_key_the_pipeline_writes():
    assert show_draft._bid(_D({"id": "32908218"})) == "32908218"


def test_older_spellings_still_resolve():
    """A stored booking is whatever the row happens to hold, not whatever the
    current code writes."""
    assert show_draft._bid(_D({"bookingId": "1"})) == "1"
    assert show_draft._bid(_D({"booking_id": "2"})) == "2"


def test_a_numeric_booking_id_is_not_lost_to_its_type():
    assert show_draft._bid(_D({"id": 32908218})) == "32908218"


def test_no_booking_is_the_empty_string_not_a_crash():
    assert show_draft._bid(_D(None)) == ""
    assert show_draft._bid(_D({})) == ""


# ── end to end, because the lookup lives in main() ──────────────────────────

@pytest.fixture()
def seeded(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    url = f"sqlite:///{tmp.name}"
    monkeypatch.setenv("DATABASE_URL", url)
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    s = db.SessionLocal()
    s.add(db.Review(id="tp_d", slack_ts="1", slack_channel="C1", rating=1,
                    author="David"))
    s.add(db.RcaDraft(id="d1", review_id="tp_d", booking={"id": "32908218"},
                      rca_prompt_version="rca_v4", generated_at=datetime(2026, 7, 31),
                      rca_v3={"l1": "x", "l2": "y", "sub_themes": [], "stated_issue": "z"}))
    # A review carrying the reference number whose draft never got a booking.
    s.add(db.Review(id="tp_o", slack_ts="2", slack_channel="C1", rating=1,
                    author="Other", reference_number="99999999"))
    s.add(db.RcaDraft(id="d2", review_id="tp_o", booking={}, rca_v3={}))
    s.commit()
    s.close()
    yield url
    os.unlink(tmp.name)


def _run(url, *args):
    env = dict(os.environ, DATABASE_URL=url)
    r = subprocess.run([sys.executable, "tools/show_draft.py", *args],
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def test_the_booking_the_checkpoint_runs_on_is_found(seeded):
    code, out = _run(seeded, "--bid", "32908218")
    assert code == 0, out
    assert "review   tp_d" in out
    assert "booking  32908218" in out, "the header printed a dash for a booking it has"


def test_a_missing_booking_says_what_it_looked_for(seeded):
    """"no draft found" was the same answer for an absent row and a broken
    lookup, which is how the broken one survived."""
    code, out = _run(seeded, "--bid", "11111111")
    assert code != 0
    assert "no draft has booking 11111111" in out
    assert "known booking ids: 32908218" in out


def test_a_reference_number_on_the_review_is_pointed_at(seeded):
    """Matching can leave the draft with no booking while the review still
    carries the number. Saying so beats making the reader guess."""
    code, out = _run(seeded, "--bid", "99999999")
    assert code != 0
    assert "--review tp_o" in out


def test_the_version_stamp_is_on_the_first_screen(seeded):
    _, out = _run(seeded, "--bid", "32908218")
    assert "by rca_v4" in out


def test_a_stamped_v4_row_gets_no_legacy_banner(seeded):
    _, out = _run(seeded, "--bid", "32908218")
    assert "THIS ROW IS THE OLD v3 SHAPE" not in out
