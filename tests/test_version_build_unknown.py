"""An environment that cannot check its build must not report "current".

`stale` was `on_disk != sha and sha != "unknown"`. On a published Replit
deployment — which ships without `.git` — both sides come back "unknown", the
second clause fires, and the endpoint answers `stale: false`.

That is the exact failure this endpoint exists to catch, wearing the
endpoint's own reassurance. It told a deployment running code from 24 commits
back that it was in sync, and the only visible symptom was every fix appearing
not to work.

Two things fixed here, and they are different:

1. There are now THREE outcomes — True, False, and None for "nothing was
   compared" — and `stale_by` names which comparison produced the answer.
2. A deployment can now usually get a real answer anyway. The source
   fingerprint is frozen at import, like the code, so it describes what this
   process LOADED; compared against the files on disk now, it catches a
   process that has not picked up its own files. That works without git,
   which is the only thing a deployment has.

The fingerprint compares SOURCE, so it cannot see a commit that touched only
tests or docs. That is a weaker guarantee than the commit comparison, so the
response says which one it used rather than presenting them as the same fact.
"""
import server.api as api


def _version(monkeypatch, sha, on_disk, fp=None, fp_disk=None):
    monkeypatch.setattr(api, "_BUILD_SHA", sha)
    monkeypatch.setattr(api, "_read_head_sha", lambda: on_disk)
    if fp is not None:
        monkeypatch.setattr(api, "_BUILD_FINGERPRINT", fp)
    if fp_disk is not None:
        monkeypatch.setattr(api, "_source_fingerprint", lambda: fp_disk)
    return api.get_version()


# ── the commit answer, when git is readable ────────────────────────────────

def test_a_matching_pair_is_not_stale(monkeypatch):
    out = _version(monkeypatch, "abc1234def", "abc1234def")
    assert out["stale"] is False
    assert out["stale_by"] == "commit"
    assert out["stale_reason"] == "", \
        "a settled comparison should not carry an explanation for not making one"


def test_a_diverging_pair_is_stale(monkeypatch):
    out = _version(monkeypatch, "abc1234def", "9999999999")
    assert out["stale"] is True
    assert out["stale_by"] == "commit"


def test_the_commit_is_preferred_when_both_could_answer(monkeypatch):
    """The commit sees every change; the fingerprint sees only source. When
    both are available the stronger one has to win, or a docs-only commit
    would report current."""
    out = _version(monkeypatch, "aaa", "bbb", fp="same", fp_disk="same")
    assert out["stale"] is True and out["stale_by"] == "commit"


# ── the deployment answer, when git is not ─────────────────────────────────

def test_no_git_still_answers_via_the_fingerprint(monkeypatch):
    """The whole point: a deployment gets a real answer instead of a shrug."""
    out = _version(monkeypatch, "unknown", "unknown", fp="aaa111", fp_disk="aaa111")
    assert out["stale"] is False
    assert out["stale_by"] == "fingerprint"


def test_a_process_behind_its_own_files_is_caught_without_git(monkeypatch):
    """Files updated, process not restarted — the case a per-request
    fingerprint could never see, because it described the files."""
    out = _version(monkeypatch, "unknown", "unknown", fp="OLD123", fp_disk="NEW456")
    assert out["stale"] is True
    assert out["stale_by"] == "fingerprint"


def test_the_fingerprint_answer_says_it_is_the_weaker_one(monkeypatch):
    """Announce the judgement. "Current" by fingerprint and "current" by
    commit are not the same claim, and the reader cannot tell them apart
    unless the response says so."""
    out = _version(monkeypatch, "unknown", "unknown", fp="aaa111", fp_disk="aaa111")
    r = out["stale_reason"].lower()
    assert r, "an answer from the weaker comparison explains nothing about it"
    assert "source" in r and ("tests" in r or "docs" in r), out["stale_reason"]


# ── and when nothing can answer at all ─────────────────────────────────────

def test_nothing_readable_is_none_not_false(monkeypatch):
    out = _version(monkeypatch, "unknown", "unknown",
                   fp="unknown", fp_disk="unknown")
    assert out["stale"] is None, (
        f"stale={out['stale']!r} — a build nobody could identify is being "
        f"reported as one that was checked and found current")
    assert out["stale_by"] == "nothing"


def test_the_dead_end_says_it_compared_nothing(monkeypatch):
    out = _version(monkeypatch, "unknown", "unknown",
                   fp="unknown", fp_disk="unknown")
    reason = out["stale_reason"]
    assert "not" in reason.lower() and "current" in reason.lower(), reason
    assert "restart" in reason.lower(), \
        "a dead end that names no next step is a dead end"


def test_one_side_unknown_falls_through_rather_than_guessing(monkeypatch):
    """A process that knows its own commit but cannot read HEAD has not made
    the commit comparison. It must not pretend it did."""
    out = _version(monkeypatch, "abc1234", "unknown", fp="a", fp_disk="a")
    assert out["stale_by"] != "commit"


# ── the shape callers depend on ────────────────────────────────────────────

def test_false_and_none_are_distinguishable_to_a_caller(monkeypatch):
    """`if v.stale` treats both as "fine", which is how this shipped. They
    have to differ by identity, not by truthiness."""
    current = _version(monkeypatch, "abc", "abc")["stale"]
    cannot = _version(monkeypatch, "unknown", "unknown",
                      fp="unknown", fp_disk="unknown")["stale"]
    assert current is False and cannot is None


def test_both_fingerprints_are_reported(monkeypatch):
    """One number could not distinguish "what I loaded" from "what is on
    disk". Two can, and the reader needs both to compare environments."""
    out = _version(monkeypatch, "unknown", "unknown", fp="LOADED", fp_disk="DISK")
    assert out["fingerprint"] == "LOADED"
    assert out["fingerprint_on_disk"] == "DISK"


def test_the_running_fingerprint_is_frozen_not_reread(monkeypatch):
    """If `fingerprint` were recomputed per request it would track the files
    and always equal fingerprint_on_disk — the comparison above would be
    between a value and itself, and always report current."""
    monkeypatch.setattr(api, "_BUILD_FINGERPRINT", "FROZEN")
    monkeypatch.setattr(api, "_source_fingerprint", lambda: "MOVED")
    out = api.get_version()
    assert out["fingerprint"] == "FROZEN", \
        "the reported build is being read from disk, not from what was loaded"
    assert out["fingerprint_on_disk"] == "MOVED"


def test_a_failed_fingerprint_is_unknown_not_empty():
    """"" == "" would make two failures compare equal and read as a match."""
    import pathlib
    real = pathlib.Path
    try:
        pathlib.Path = None                      # break it from the inside
        assert api._source_fingerprint() == "unknown"
    finally:
        pathlib.Path = real
