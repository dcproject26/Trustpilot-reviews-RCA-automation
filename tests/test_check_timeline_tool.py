"""The timeline diagnostic reports rather than reimplements.

A diagnostic that copies the logic it is checking can agree with itself while
the pipeline does something else — which is the failure mode a diagnostic
exists to prevent, wearing a hat. So what is asserted here is that it calls the
REAL decision functions, and that it refuses honestly when it cannot answer.
"""
import subprocess
import sys

from tests.conftest import read_source

SRC = read_source("tools/check_timeline.py")


def test_it_uses_the_real_decision_functions():
    """Negative-assertion friendly: these names must appear because the tool
    delegates to them. A reimplementation would not need them."""
    for fn in ("other_booking_named", "_booking_cutoff", "_is_prior_trip",
               "collect_tickets", "_get_timeline_sync", "booking_id_from_ticket"):
        assert fn in SRC, f"the tool does not call {fn} — it may be reimplementing it"


def test_it_does_not_reimplement_the_filters():
    """The two patterns that decide exclusions live in zendesk.py. A copy here
    would drift silently."""
    assert "_SUBJECT_BID_RE = " not in SRC, "the subject pattern is duplicated"
    assert "_BID_LABEL = " not in SRC, "the label pattern is duplicated"


def test_it_refuses_rather_than_guessing_without_zendesk():
    """Run for real: with Zendesk not live it must say so and exit non-zero,
    not print an empty timeline that reads like a clean result."""
    r = subprocess.run([sys.executable, "tools/check_timeline.py", "33543686"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0, "it reported success with no Zendesk configured"
    assert "not live" in r.stdout, r.stdout[:400]


def test_it_is_read_only_without_the_rerun_flag():
    """--rerun is the only thing that writes. Nothing else may enqueue."""
    body = SRC[SRC.find("def main("):]
    i = body.find("jobs.enqueue(")
    assert i != -1, "the re-run path is gone"
    # the enqueue must sit under the args.rerun guard
    assert "if args.rerun" in body[:i], \
        "enqueue is reachable without --rerun; the tool is not read-only"
