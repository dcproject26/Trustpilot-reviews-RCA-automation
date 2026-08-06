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
