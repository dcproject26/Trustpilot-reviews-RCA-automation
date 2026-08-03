"""The dashboard's fallback rule must match the server's, case for case.

The bucket is decided in server/tiers.py and sent with every row, so the
client's copy only runs against an older build. It still has to agree: two
differently-worded copies of this rule is exactly what put confirmed
candidates under "possible matches" and Zendesk-found bookings under a tab
with nothing to pick.

The client function is extracted from client/index.html at run time, so this
cannot pass against a stale copy of it.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_tier_sorting import CASES   # noqa: E402


def _client_fallback_source() -> str:
    html = open(os.path.join(os.path.dirname(__file__), "..",
                             "client", "index.html")).read()
    i = html.index("function _bucketFallback(row, tier, candState) {")
    j = html.index("\n}", i) + 2
    return html[i:j]


def test_client_fallback_matches_the_server_rule():
    rows = []
    for name, review, draft, expected in CASES:
        b = (getattr(draft, "booking", None) or {}) if draft else {}
        rows.append({"name": name, "expected": expected, "row": {
            "status": review.status,
            "match_tier": getattr(draft, "match_tier", None) if draft else None,
            "candidate_state": bool(getattr(draft, "candidate_state", False)) if draft else False,
            "has_booking": bool(b.get("id")),
            "has_candidates": bool(getattr(draft, "candidates_list", None)) if draft else False,
            "confirmed": bool(getattr(draft, "selected_candidate_bid", None)) if draft else False,
            # The fact the processing bucket turns on. Left out, the client
            # sees `undefined`, `undefined === false` is false, and a queued
            # review falls through to untraceable — which is the bug, back,
            # in the one place that is supposed to catch it.
            "has_draft": draft is not None,
        }})

    script = _client_fallback_source() + "\nconst cases = " + json.dumps(rows) + ";\n" + """
    const bad = [];
    for (const c of cases) {
      const got = _bucketFallback(c.row, c.row.match_tier, c.row.candidate_state);
      if (got !== c.expected) bad.push(c.name + ': client=' + got + ' server=' + c.expected);
    }
    console.log(JSON.stringify(bad));
    """
    out = subprocess.run([_node(), "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    disagreements = json.loads(out.stdout.strip().splitlines()[-1])
    assert not disagreements, "client and server disagree:\n  " + "\n  ".join(disagreements)


def test_the_api_sends_every_fact_the_fallback_needs():
    """If the payload stops carrying one of these, the fallback silently
    degrades to guessing."""
    api = open(os.path.join(os.path.dirname(__file__), "..", "server", "api.py")).read()
    for field in ('"bucket"', '"has_booking"', '"has_candidates"', '"confirmed"',
                  '"tier_label"', '"unverified"', '"has_draft"',
                  '"processing_state"'):
        assert field in api, f"the reviews list no longer sends {field}"


def _node() -> str:
    for cand in ("node", "nodejs"):
        try:
            subprocess.run([cand, "--version"], capture_output=True, check=True)
            return cand
        except Exception:
            continue
    import pytest
    pytest.skip("node is not available on this machine")
