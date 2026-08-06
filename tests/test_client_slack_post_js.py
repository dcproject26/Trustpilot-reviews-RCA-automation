"""Run the client-side Slack-post harness as part of the suite.

`tests/slack_post_format.test.js` extracts the REAL `_genSlackText` from
client/index.html and runs it under node. It is the only thing in this repo
that executes client JavaScript, and it is what makes the one-composer rule
checkable on the half CLAUDE.md §2 otherwise exempts from testing.

It needed wiring in. Nothing ran it: it sat in tests/ where it looked like
part of the suite, crashed on a missing helper before reaching its first
assertion, and asserted the five mandated headings against a client that never
produced them. A harness nobody executes looks exactly like one that passes —
the failure mode this project is built around, in its own test directory.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "slack_post_format.test.js"


def _node():
    return shutil.which("node")


def test_the_harness_is_present():
    """A missing file must fail loudly rather than skip into silence."""
    assert HARNESS.exists(), f"{HARNESS} is gone — the client half is unguarded"


def test_the_client_renders_the_server_composed_wwr():
    """The one-composer guarantee, on the client side.

    Asserts the dashboard reproduces `wwr_slack_text` verbatim and rebuilds
    none of the section from rca.v3. The server-side half of the same
    guarantee is test_wwr_one_composer.py.
    """
    node = _node()
    if not node:
        pytest.skip("node is not installed in this environment")
    proc = subprocess.run([node, str(HARNESS)], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (
        "the client-side Slack post harness failed:\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")


def test_the_harness_fails_when_the_client_composes_its_own_section():
    """The harness is only worth running if it can fail.

    A green harness that cannot go red is the same defect as one nobody runs,
    so this rewrites the client's WWR line in a COPY of the tree to rebuild
    the section from rca.v3 — the exact regression the one-composer change
    removed — and requires a non-zero exit. The real tree is never touched.
    """
    node = _node()
    if not node:
        pytest.skip("node is not installed in this environment")
    import tempfile

    src = (ROOT / "client" / "index.html").read_text(encoding="utf-8")
    anchor = "    const wwrText = rca.wwrSlackText || '';"
    assert src.count(anchor) == 1, (
        "the client no longer renders the server text through the line this "
        "check rewrites — re-anchor it rather than letting it pass vacuously")
    broken = src.replace(
        anchor,
        "    const wwrText = (((rca.v3||{}).what_went_wrong||{}).guest_issues||[])"
        ".map(g => '* ' + g.issue + ' *' + (g.claim ? '\\n\\u2022 Guest: ' + g.claim : ''))"
        ".join('\\n');")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "client").mkdir()
        (tmp / "tests").mkdir()
        (tmp / "client" / "index.html").write_text(broken, encoding="utf-8")
        shutil.copy(HARNESS, tmp / "tests" / HARNESS.name)
        proc = subprocess.run([node, str(tmp / "tests" / HARNESS.name)],
                              cwd=tmp, capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0, (
        "a client that composes the what-went-wrong section itself passed the "
        "harness — the harness is not guarding anything.\n"
        f"stdout:\n{proc.stdout}")
    assert "composing the what-went-wrong section itself" in proc.stderr, (
        f"the harness failed for the wrong reason:\n{proc.stderr}")
