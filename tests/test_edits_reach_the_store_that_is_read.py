"""An edit must land in the store the reader actually consults.

Area of improvement was read through `_v4` — rca_v3 first, the column as a
fallback — and written by the dashboard to the COLUMN ONLY. So add a point,
delete a point, rewrite a point: all three returned 200, all three put a green
tick on the field, and the next load showed the pipeline's original list. The
write went to a key nothing reads.

That is `show_draft --bid` keying on `bookingId` while the warehouse writes
`id`, and it is the third time in this file's history. The structural test
below is the answer: it walks _draft_dict's own `_v4` calls and fails if any
of those fields can be patched without being routed into rca_v3. Finding this
one by hand needed a browser clicking every control on the card.
"""
import ast
import pathlib

import pytest

API = pathlib.Path("server/api.py")


def _v4_read_fields():
    """Every field _draft_dict serves through _v4(), read off the AST.

    Not a grep: a grep matches the string in a comment, and the point of this
    test is that it cannot pass for a reason other than the code.
    """
    tree = ast.parse(API.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_v4" and len(node.args) >= 3):
            col, path = node.args[1], node.args[2]
            if isinstance(col, ast.Constant) and isinstance(path, ast.Constant):
                out.append((col.value, path.value))
    return out


def test_the_reads_were_found_at_all():
    """A test that silently found nothing to check is the failure mode this
    whole suite is about."""
    got = _v4_read_fields()
    assert len(got) >= 6, f"only found {len(got)} _v4 reads — the parse missed them"


@pytest.mark.parametrize("column,v3_path", _v4_read_fields())
def test_a_field_read_from_rca_v3_is_also_written_there(column, v3_path):
    """rca_v3 wins on read. So a patch that writes only the column is a write
    nobody will ever see."""
    from server.api import _V4_SECTIONS, DraftPatchV2

    patchable = set(DraftPatchV2.model_fields)
    if column not in patchable:
        return                     # not editable from the dashboard at all

    assert column in _V4_SECTIONS, (
        f"{column!r} is served from rca_v3 (path {v3_path!r}) and is patchable, "
        f"but is not in _V4_SECTIONS — a PATCH writes the column, the reader "
        f"reads rca_v3, and the edit vanishes on the next load")
    assert tuple(_V4_SECTIONS[column]) == tuple(v3_path.split(".")), (
        f"{column!r} is read from rca_v3.{v3_path} but written to "
        f"rca_v3.{'.'.join(_V4_SECTIONS[column])} — two paths, one field")


def test_area_of_improvement_specifically():
    """Named, because it is the one that shipped broken."""
    from server.api import _V4_SECTIONS
    assert _V4_SECTIONS.get("area_of_improving") == ("area_of_improving",)
