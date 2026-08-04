"""Every <script> block in the dashboard actually parses.

A syntax error in the client is total: the page loads, the stylesheet applies,
and not one line of JavaScript runs. There is no partial failure to notice —
the inbox is empty, the RCA column is empty, and it looks exactly like a
server returning no reviews.

This exists because a hand-rolled check missed one. The check extracted only
the blocks matching a couple of known function names, concatenated them, and
ran `node --check` on the result — which passed, while the shipped page was
broken. A checker that inspects a subset and reports OK is the same defect
this codebase keeps naming: "I ran and found nothing" reading identically to
"I did not run".

So: EVERY block, each on its own, and a hard failure if no blocks were found
at all — a regex that silently stops matching would otherwise report a clean
bill of health for a file it never looked at.

Source-level by necessity. CLAUDE.md permits it for client-side JavaScript,
and parsing is the one property a source check can establish completely.
"""
import pathlib
import re
import shutil
import subprocess

import pytest

CLIENT = pathlib.Path("client/index.html")
BLOCKS = re.findall(r"<script[^>]*>(.*?)</script>", CLIENT.read_text(encoding="utf-8"), re.S)


def test_the_blocks_were_found_at_all():
    """The guard on the guard. If the extraction stops matching, every
    parametrized case below silently disappears and the file reports green."""
    assert len(BLOCKS) >= 1, "no <script> blocks extracted — the regex missed them"
    assert sum(len(b) for b in BLOCKS) > 50_000, \
        "the extracted script is far too small — the regex is matching a fragment"


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
@pytest.mark.parametrize("i", range(len(BLOCKS)))
def test_each_script_block_parses(i, tmp_path):
    """One block at a time. Concatenating them can mask an error — and can
    invent one, since a block is not required to be complete on its own the
    way a module is."""
    f = tmp_path / f"block{i}.js"
    f.write_text(BLOCKS[i], encoding="utf-8")
    r = subprocess.run(["node", "--check", str(f)],
                       capture_output=True, text=True)
    assert r.returncode == 0, (
        f"script block {i} does not parse — the whole dashboard is dead, not "
        f"just this feature:\n{r.stderr[:1500]}")


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_check_can_actually_fail(tmp_path):
    """A parser check that cannot fail is a spelling check. Proves the
    subprocess call reports a bad file, so a green run above means something.
    """
    f = tmp_path / "broken.js"
    f.write_text("const a = (() => { return 1; })()}\n", encoding="utf-8")
    r = subprocess.run(["node", "--check", str(f)],
                       capture_output=True, text=True)
    assert r.returncode != 0, "node --check accepted a syntactically broken file"
