"""A function-local import of a module-level name breaks the whole function.

`from server.services import zendesk` was added inside one branch of
`process_review`, and `zendesk` is already imported at module scope. Python
then treats `zendesk` as a LOCAL for the ENTIRE function, so every other use
of it — the Zendesk shortlist, both BID searches, and the timeline fetch —
raised:

    cannot access local variable 'zendesk' where it is not associated with a
    value

on any review that did not enter that branch. Which is most reviews. And each
of those call sites sits inside a try/except that logs and carries on, so
matching quietly lost its entire Zendesk half and every test stayed green.

That is the exact shape CLAUDE.md opens with: `validate()` disabled by an
unbound local inside a try, indistinguishable from a validator that worked.

The whole tree is swept rather than the one function, because the trap has
nothing to do with which function it happens in.
"""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "server"
FILES = sorted(ROOT.rglob("*.py"))


def _shadowing(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_names = {
        (a.asname or a.name.split(".")[0])
        for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    out = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for n in ast.walk(fn):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    name = a.asname or a.name.split(".")[0]
                    if name in module_names:
                        out.append(f"{path.name}:{n.lineno} — '{name}' inside "
                                   f"{fn.name}() shadows the module-level import")
    return out


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_function_imports_a_name_the_module_already_imports(path):
    """A local import that shadows a module-level one turns every OTHER use of
    that name in the function into an UnboundLocalError — including uses that
    ran fine before the line was added, and including paths that never reach
    it."""
    bad = _shadowing(path)
    assert not bad, "\n".join(bad)


def test_the_sweep_is_actually_looking_at_something():
    """NOT BUILT guard. An empty file list would make every case above pass by
    inspecting nothing."""
    assert len(FILES) > 10, f"only {len(FILES)} server files found"


def test_the_detector_catches_the_shape_it_was_written_for(tmp_path):
    """Drives the detector against the real defect, so a rewrite that quietly
    stops detecting anything fails here rather than going green everywhere."""
    p = tmp_path / "sample.py"
    p.write_text(
        "from server.services import zendesk\n"
        "async def run():\n"
        "    await zendesk.shortlist()\n"
        "    if x:\n"
        "        from server.services import zendesk\n"
        "        await zendesk.other()\n")
    assert _shadowing(p), "the detector does not catch a shadowing local import"


def test_an_ordinary_local_import_is_not_flagged(tmp_path):
    """Local imports are used all over this codebase to break cycles and defer
    cost. Flagging them all would make this test noise, and noise gets
    silenced."""
    p = tmp_path / "sample.py"
    p.write_text(
        "import os\n"
        "def run():\n"
        "    from server.services import zendesk\n"
        "    return zendesk\n")
    assert _shadowing(p) == []


# ── and the behaviour it cost, driven ──────────────────────────────────────

def test_the_zendesk_shortlist_is_reachable_for_a_review_with_no_bid(
        live_db, monkeypatch):
    """THE BUG THIS FILE EXISTS FOR, as a user saw it.

    A review with no booking id in its text skips the Tier-1 branch — which is
    where the shadowing import sat — so `zendesk` was never bound, and the
    shortlist call raised UnboundLocalError inside a try/except that logged
    and carried on. `_short` came back [], the review was filed Untraceable,
    and the card said "no usable name or venue signal to search with" about a
    review with a perfectly good name.

    The static sweep above catches the SHAPE. This catches the CONSEQUENCE:
    the shortlist has to actually be reached and called.
    """
    import asyncio
    import importlib
    import server.pipeline as P
    importlib.reload(P)

    called = {}

    async def _shortlist(indicators, first, last, **kw):
        called["hit"] = True
        return []

    async def _call(*a, **k):
        return ('{"experience_or_venue": null, "city_or_country": null, '
                '"visit_date_hint": null, "issue_terms": ["wrong ticket"]}')

    monkeypatch.setattr(P.claude, "_call", _call)
    monkeypatch.setattr(P.zendesk, "shortlist", _shortlist)
    monkeypatch.setattr(P, "is_live", lambda name: name in ("bigquery", "zendesk"))

    from datetime import datetime
    s = live_db.SessionLocal()
    s.add(live_db.Review(
        id="tp_nobid", slack_ts="tp_nobid", slack_channel="C_MOCK_ORM",
        rating=1, author="Mariana Campos", status="new",
        received_at=datetime(2026, 8, 1),
        body_original="I bought 4 tickets for Universal and the ticket was "
                      "the wrong one."))
    s.commit()
    s.close()

    try:
        asyncio.run(P.process_review("tp_nobid"))
    except Exception:
        pass

    assert called.get("hit"), (
        "the Zendesk shortlist was never called for a review with no booking "
        "id in its text — the search that finds these reviews did not run")


def test_the_trail_does_not_silently_swallow_a_shortlist_crash(
        live_db, monkeypatch):
    """The other half. The call site catches every exception and logs, so a
    crash and a genuinely empty search produced the same card. A reader has to
    be able to tell "we searched and found nothing" from "the search died"."""
    import asyncio
    import importlib
    import server.pipeline as P
    importlib.reload(P)

    async def _boom(*a, **k):
        raise RuntimeError("shortlist exploded")

    async def _call(*a, **k):
        return ('{"experience_or_venue": null, "city_or_country": null, '
                '"visit_date_hint": null, "issue_terms": ["wrong ticket"]}')

    monkeypatch.setattr(P.claude, "_call", _call)
    monkeypatch.setattr(P.zendesk, "shortlist", _boom)
    monkeypatch.setattr(P, "is_live", lambda name: name in ("bigquery", "zendesk"))

    from datetime import datetime
    s = live_db.SessionLocal()
    s.add(live_db.Review(
        id="tp_boom", slack_ts="tp_boom", slack_channel="C_MOCK_ORM",
        rating=1, author="Mariana Campos", status="new",
        received_at=datetime(2026, 8, 1),
        body_original="I bought 4 tickets for Universal."))
    s.commit()
    s.close()

    try:
        asyncio.run(P.process_review("tp_boom"))
    except Exception:
        pass

    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_boom").first()
    trail = " ".join(t.get("text", "") for t in ((d.confidence_trail or []) if d else []))
    s.close()
    assert "search" in trail.lower(), trail[:400]
    assert ("failed" in trail.lower() or "did not run" in trail.lower()
            or "could not" in trail.lower()), (
        "a shortlist that CRASHED leaves the same trail as one that searched "
        "and found nothing:\n" + trail[:600])
