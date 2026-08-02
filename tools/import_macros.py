#!/usr/bin/env python3
"""Refresh the checked-in macros from a Google Sheets HTML export.

    File > Download > Web page (.html) on the sheet, then:

    python3 tools/import_macros.py ~/Downloads/ORM_Macro.zip
    python3 tools/import_macros.py ~/Downloads/unzipped_folder/

The macros are the source of truth for the reply's voice and they live in the
repo, not behind a Google login. That is deliberate: the live-sheet path failed
four different ways — an unshared service account, an ambiguous sheet id, a CSV
export that can only reach one of nine tabs, and a network that could not reach
docs.google.com at all — and every one of them arrived as the same empty list,
which the pipeline could not tell from "no approved reply matches".

This writes server/data/canned_macros.json. Commit the result; the diff is
reviewable, which a Google Sheet is not.
"""
import argparse
import html as H
import os
import json
import pathlib
import re
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Overridable so a test can drive the whole script without overwriting the
# macros the app actually ships with.
OUT = pathlib.Path(os.environ.get("CANNED_MACROS_OUT")
                   or ROOT / "server" / "data" / "canned_macros.json")


def _cells(row_html: str) -> list[str]:
    # <br> and block ends are real newlines in a macro. Dropping them runs the
    # greeting into the body and every reply comes out as one paragraph.
    r = re.sub(r"<br\s*/?>", "\n", row_html, flags=re.I)
    r = re.sub(r"</(p|div)>", "\n", r, flags=re.I)
    return [H.unescape(re.sub(r"<[^>]+>", "", c)).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]


def parse_dir(d: pathlib.Path) -> dict:
    tabs = {}
    files = sorted(d.rglob("*.html"))
    if not files:
        raise SystemExit(f"no .html files under {d} — is this a Sheets "
                         f"'Web page' export?")
    for f in files:
        t = f.read_text(encoding="utf-8", errors="replace")
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)
        body = [c for c in (_cells(r) for r in rows) if any(x for x in c)]
        # Row 0 is the spreadsheet's A/B/C ruler and column 0 its row numbers.
        tabs[f.stem.strip()] = [r[1:] for r in body[1:]] if len(body) > 1 else []
    return tabs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="the .zip or the unzipped folder")
    a = ap.parse_args()
    src = pathlib.Path(a.source).expanduser()
    if not src.exists():
        raise SystemExit(f"{src} does not exist")

    if src.is_file() and src.suffix == ".zip":
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(src) as z:
                z.extractall(tmp)
            tabs = parse_dir(pathlib.Path(tmp))
    else:
        tabs = parse_dir(src)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(tabs, indent=1, ensure_ascii=False),
                   encoding="utf-8")

    # Report what the app will actually make of it, not just what was written —
    # a tab that parses to zero replies is the thing worth knowing, and it is
    # invisible from a row count.
    sys.path.insert(0, str(ROOT))
    from server.services.canned import _parse_tab
    try:
        shown = OUT.relative_to(ROOT)
    except ValueError:
        shown = OUT          # an override outside the tree, e.g. under test
    print(f"wrote {shown}\n")
    total = 0
    for name, raw in tabs.items():
        rows, why = _parse_tab(name, raw)
        total += len(rows)
        print(f"  {len(rows):4d} replies  {name}" if rows
              else f"     -  skipped   {why}")
    print(f"\n{total} replies the pipeline can use.")
    if not total:
        raise SystemExit("nothing usable was imported — the tone reference "
                         "would be empty. Not writing this off as success.")


if __name__ == "__main__":
    main()
