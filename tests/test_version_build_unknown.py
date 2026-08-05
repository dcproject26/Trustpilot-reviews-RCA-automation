"""An environment that cannot read its own commit must not report "current".

`stale` was `on_disk != sha and sha != "unknown"`. On a published Replit
deployment — which ships without `.git` — both sides come back "unknown", the
second clause fires, and the endpoint answers `stale: false`.

That is the exact failure this endpoint exists to catch, wearing the endpoint's
own reassurance. It told a deployment running code from 24 commits back that it
was in sync, and the only visible symptom was every fix appearing not to work.

Three states now: True, False, and None for "could not check". `stale_reason`
names what could not be read and what answers it instead — the fingerprint,
which both environments compute the same way and neither needs git for.
"""
import server.api as api


def _version(monkeypatch, sha, on_disk):
    monkeypatch.setattr(api, "_BUILD_SHA", sha)
    monkeypatch.setattr(api, "_read_head_sha", lambda: on_disk)
    return api.get_version()


# ── the case that shipped ──────────────────────────────────────────────────

def test_an_unknown_commit_is_not_reported_as_current(monkeypatch):
    """The deployment case, verbatim: sha and on_disk both "unknown"."""
    out = _version(monkeypatch, "unknown", "unknown")
    assert out["stale"] is None, (
        f"stale={out['stale']!r} — a build nobody could identify is being "
        f"reported as a build that was checked and found current")


def test_the_unknown_case_says_what_would_answer_it(monkeypatch):
    """"Unknown" with no next step is a dead end. The fingerprint is the
    answer, and it has to be named where the reader is."""
    out = _version(monkeypatch, "unknown", "unknown")
    reason = out["stale_reason"]
    assert reason, "stale is unknown and nothing says why"
    assert "fingerprint" in reason.lower(), reason
    assert out["fingerprint"] and out["fingerprint"] != "unknown", \
        "the reason points at a fingerprint the response does not carry"
    assert out["fingerprint"] in reason, \
        "the reason talks about the fingerprint without giving it"


def test_one_side_unknown_is_also_unknown(monkeypatch):
    """A process that knows its own commit but cannot read HEAD has still not
    made the comparison. Half an answer is not the answer."""
    assert _version(monkeypatch, "abc1234", "unknown")["stale"] is None
    assert _version(monkeypatch, "unknown", "abc1234")["stale"] is None


# ── and the two states that already worked keep working ────────────────────

def test_a_matching_pair_is_not_stale(monkeypatch):
    out = _version(monkeypatch, "abc1234def", "abc1234def")
    assert out["stale"] is False
    assert out["stale_reason"] == "", \
        "a settled comparison should not carry an explanation for not making one"


def test_a_diverging_pair_is_stale(monkeypatch):
    out = _version(monkeypatch, "abc1234def", "9999999999")
    assert out["stale"] is True
    assert out["stale_reason"] == ""


def test_false_and_none_are_distinguishable_to_a_caller(monkeypatch):
    """`if v.stale` treats both as "fine", which is how this got shipped. The
    values have to be distinguishable by identity, not by truthiness."""
    current = _version(monkeypatch, "abc1234def", "abc1234def")["stale"]
    cannot  = _version(monkeypatch, "unknown", "unknown")["stale"]
    assert current is False and cannot is None
    assert current is not cannot
