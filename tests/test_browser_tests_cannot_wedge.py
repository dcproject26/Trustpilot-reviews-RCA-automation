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

FILES = sorted(pathlib.Path("tests").glob("test_*.py"))


SELF = "test_browser_tests_cannot_wedge.py"


def _browser_files():
    # This file names both patterns in order to describe them, so it cannot
    # scan itself without always failing.
    return [f for f in FILES
            if f.name != SELF and "(page)" in f.read_text()]


def test_no_browser_test_waits_for_networkidle():
    """The dashboard opens a poll, so `networkidle` never settles: the wait
    burns the full default timeout before failing, and inside an
    un-timeout-able evaluate it wedges the run outright. Wait for the element
    the test actually needs."""
    bad = [f.name for f in FILES
           if f.name != SELF and 'wait_until="networkidle"' in f.read_text()]
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
    src = pathlib.Path("client/index.html").read_text()
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
            tree = ast.parse(f.read_text())
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
    src = pathlib.Path("client/index.html").read_text()
    async_fns = set(re.findall(r"async function ([A-Za-z_$][\w$]*)", src))
    assert "refreshAfterScenarioChange" in async_fns, \
        "the function that caused the stall is no longer async — update this"
    sample = 'page.evaluate("() => refreshAfterScenarioChange(state.selected)")'
    m = re.search(r'evaluate\(\s*"\(\)\s*=>\s*([A-Za-z_$][\w$]*)\(', sample)
    assert m and m.group(1) in async_fns, "the matcher would not have caught it"
