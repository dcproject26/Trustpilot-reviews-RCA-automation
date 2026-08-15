"""Two ways a browser test stops being a test and becomes a hang.

BOTH OF THESE SHIPPED. The batch of 28 UI modules stalled at 15% while every
file passed on its own, which is the worst shape a failure can take: it looks
like a slow machine, not a bug, and the run that proves you wrong takes twenty
minutes to not finish.

NEGATIVE source assertions — the one form CLAUDE.md allows, because
unreachability cannot defeat "this string appears nowhere" — plus these are
client-side test files with no harness of their own.
"""
import pathlib
import re

from tests.conftest import read_source

FILES = sorted(pathlib.Path("tests").glob("test_*.py"))


SELF = "test_browser_tests_cannot_wedge.py"


def _browser_files():
    # This file names both patterns in order to describe them, so it cannot
    # scan itself without always failing.
    return [f for f in FILES
            if f.name != SELF and "(page)" in read_source(f)]


def test_no_browser_test_waits_for_networkidle():
    """The dashboard opens a poll, so `networkidle` never settles: the wait
    burns the full default timeout before failing, and inside an
    un-timeout-able evaluate it wedges the run outright. Wait for the element
    the test actually needs."""
    bad = [f.name for f in FILES
           if f.name != SELF and 'wait_until="networkidle"' in read_source(f)]
    assert not bad, (
        f"{bad} wait on networkidle. Use wait_until='load' plus an explicit "
        f"wait_for_selector for the thing the test needs.")


def test_no_test_awaits_an_async_client_function_through_evaluate():
    """`page.evaluate("() => someAsyncFn()")` implicitly returns the promise,
    and Playwright AWAITS it — `evaluate` takes no timeout. A fetch that is
    merely slow when 28 modules share one uvicorn then hangs forever.

    Wrap the call in braces so the arrow returns undefined:
    `page.evaluate("() => { someAsyncFn(); }")`, and wait explicitly.
    """
    src = read_source("client/index.html")
    async_fns = set(re.findall(r"async function ([A-Za-z_$][\w$]*)", src))
    assert async_fns, "no async client functions found — the scan is broken"

    # AST, not a regex over the whole file: a regex matches the pattern named
    # inside a docstring explaining the pattern, which is how the first version
    # of this guard reported three offenders of which one was a comment.
    # Only the actual string literals handed to .evaluate() are scanned.
    import ast
    offenders = []
    for f in _browser_files():
        try:
            tree = ast.parse(read_source(f))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "evaluate"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                continue
            js = node.args[0].value
            m = re.match(r"\s*\(\s*\)\s*=>\s*([A-Za-z_$][\w$]*)\s*\(", js)
            if m and m.group(1) in async_fns:
                offenders.append(f"{f.name}: {m.group(1)}()")
    assert not offenders, (
        "these await an async client function with no timeout: "
        + "; ".join(offenders))


def test_the_scan_would_actually_catch_one():
    """A guard that cannot fail is the thing this repo keeps shipping. Prove
    the matcher fires on the exact line that caused the stall."""
    src = read_source("client/index.html")
    async_fns = set(re.findall(r"async function ([A-Za-z_$][\w$]*)", src))
    assert "refreshAfterScenarioChange" in async_fns, \
        "the function that caused the stall is no longer async — update this"
    sample = 'page.evaluate("() => refreshAfterScenarioChange(state.selected)")'
    m = re.search(r'evaluate\(\s*"\(\)\s*=>\s*([A-Za-z_$][\w$]*)\(', sample)
    assert m and m.group(1) in async_fns, "the matcher would not have caught it"


# ── the third way: a browser test in the wrong PLACE ────────────────────────
#
# Playwright's sync API runs its asyncio loop in the MAIN THREAD. While the
# session-scoped browser is alive, `asyncio.get_running_loop()` returns that
# loop from ordinary test code, so every `asyncio.run(...)` raises
# "cannot be called from a running event loop". 263 tests across 33 modules
# died that way in a full run and passed one file at a time — the same shape
# as the two hangs above: it reads as a broken machine, not a bug.
#
# conftest partitions the collection so browser tests run last. These drive
# that partition rather than reading it: `_needs_browser` is a pure predicate
# and the ordering hook is three lines on top of it.

def test_a_page_test_is_recognised_as_a_browser_test():
    from tests.conftest import _needs_browser
    from types import SimpleNamespace
    assert _needs_browser(SimpleNamespace(fixturenames=("page", "tmp_path")))


def test_a_test_that_drives_the_browser_directly_is_recognised_too():
    """`page` is a convenience over `ui_browser`. A test that takes the
    browser without it starts playwright just the same, so the partition must
    not key on the helper."""
    from tests.conftest import _needs_browser
    from types import SimpleNamespace
    assert _needs_browser(SimpleNamespace(fixturenames=("ui_browser",)))


def test_a_test_that_takes_only_the_seeded_server_is_recognised_too():
    """`ui_server` does not start playwright — but it OWNS the shared fixture
    database, and `reseed()` only runs between the modules inside the browser
    block. A test that mutates those rows from outside the block has nothing
    putting them back, and the module that reads them next fails for a reason
    that has nothing to do with what it asserts."""
    from tests.conftest import _needs_browser
    from types import SimpleNamespace
    assert _needs_browser(SimpleNamespace(fixturenames=("ui_server",)))


def test_an_ordinary_test_is_left_where_it_is():
    """The inverse. If everything counted as a browser test the partition
    would be a no-op that still reported itself as having run."""
    from tests.conftest import _needs_browser
    from types import SimpleNamespace
    assert not _needs_browser(SimpleNamespace(fixturenames=("live_db", "monkeypatch")))
    assert not _needs_browser(SimpleNamespace(fixturenames=()))
    assert not _needs_browser(SimpleNamespace())


def test_the_partition_puts_every_browser_test_after_every_other_one():
    """Drives the hook itself. Ordering is the whole guarantee, so a hook that
    partitions into the wrong halves — or drops one — has to fail here."""
    from tests.conftest import pytest_collection_modifyitems
    from types import SimpleNamespace

    def _item(name, fixtures):
        return SimpleNamespace(name=name, fixturenames=fixtures)

    a, b = _item("unit_a", ("live_db",)), _item("unit_b", ())
    x, y = _item("ui_x", ("page",)), _item("ui_y", ("ui_browser",))
    items = [x, a, y, b]
    cfg = SimpleNamespace()
    pytest_collection_modifyitems(None, cfg, items)
    names = [i.name for i in items]
    assert names == ["unit_a", "unit_b", "ui_x", "ui_y"], names
    assert len(items) == 4, "the partition dropped an item"
    assert cfg._browser_last == (2, 2), cfg._browser_last


def test_a_run_with_no_browser_tests_is_left_completely_alone():
    """`items[:] = rest + browser` on an all-unit run is a no-op, but the
    ANNOUNCEMENT is not: reporting "0 moved" on every unit run is the noise
    that makes a reader stop reading the counts that matter."""
    from tests.conftest import pytest_collection_modifyitems, pytest_report_collectionfinish
    from types import SimpleNamespace
    items = [SimpleNamespace(name="a", fixturenames=()),
             SimpleNamespace(name="b", fixturenames=("live_db",))]
    cfg = SimpleNamespace()
    pytest_collection_modifyitems(None, cfg, list(items) and items)
    assert [i.name for i in items] == ["a", "b"]
    assert pytest_report_collectionfinish(cfg, items) == []


def test_the_reordering_announces_itself():
    """A run that quietly sorts itself gives a reader no way to tell this from
    a suite that happened to be green in the order it was collected."""
    from tests.conftest import pytest_collection_modifyitems, pytest_report_collectionfinish
    from types import SimpleNamespace
    items = [SimpleNamespace(name="ui", fixturenames=("page",)),
             SimpleNamespace(name="unit", fixturenames=())]
    cfg = SimpleNamespace()
    pytest_collection_modifyitems(None, cfg, items)
    line = pytest_report_collectionfinish(cfg, items)
    assert line and "1 of 2" in line[0], line
    assert "asyncio.run" in line[0], "the line does not say what it is for"
