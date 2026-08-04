#!/usr/bin/env python3
"""Turn the DSS "New (Unified View)" sheet exports into content/dss_unified.json.

The DSS agents use is a live Google Sheet, and server/services/dss.py reads it
tab by tab. This imports a point-in-time export of the NEW unified view so the
new guidance is available without waiting on the live sheet, and so it can be
reviewed in a diff like any other content change.

WHERE THE TWO DISAGREE, THE NEW ONE WINS. That is the instruction, and it is
also the only rule that makes a partial import safe: a scenario present in
both must resolve to one answer, and silently keeping the older text would
give an agent guidance that this file was imported specifically to replace.

"Copy of <tab>" is skipped when <tab> is also present. They are snapshots of
the same tab and they DO differ — the copy of the meeting-point tab is missing
a sentence about explaining to the guest why proof is being requested. Taking
whichever the filesystem happened to list first would silently pick a version
nobody chose. Every skip is reported, because a quiet skip and a file that was
never there look identical.

    python3 tools/import_dss.py <dir-of-html-exports> [-o content/dss_unified.json]
"""
import argparse
import json
import pathlib
import re
import sys
from html.parser import HTMLParser


class _Table(HTMLParser):
    """Rows of cell text from a Google Sheets HTML export."""

    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "tr" and self._row is not None:
            if any(c.strip() for c in self._row):
                self.rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def read_rows(path) -> list[list[str]]:
    p = _Table()
    p.feed(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
    # Google's export leads every sheet with a column-letter row (A, B, C…) and
    # numbers every data row. Both are spreadsheet furniture, not content.
    out = []
    for r in p.rows:
        if r and re.fullmatch(r"[A-Z]{1,2}", r[0] or ""):
            continue
        out.append([c for c in (r[1:] if r and r[0].isdigit() else r)])
    return [r for r in out if any(c.strip() for c in r)]


# Which DSS type each exported tab belongs to. The keys are the type names
# server/services/dss.py already routes to, so an imported row lands in the
# same bucket as the live sheet's.
TAB_TYPES = [
    ("meeting point",   "meetingPointIssue"),
    ("vendor",          "supplyPartnerIssue"),
    ("service issues",  "supplyPartnerIssue"),
    ("delay",           "delay_fulfilment"),
    ("cancellation",    "cancelation"),
    ("booking modific", "cancelation"),
    ("other issues",    "other"),
]


def type_of(name: str) -> str:
    low = name.lower()
    for frag, t in TAB_TYPES:
        if frag in low:
            return t
    return "other"


def parse_tab(path) -> list[dict]:
    """Rows as {selector, dss, columns}. Column headers are kept verbatim so a
    variant ("More than 125$", "Non - Partnered") stays attached to its text
    rather than being flattened into one recommendation."""
    rows = read_rows(path)
    if not rows:
        return []
    # The first row with more than one non-empty cell is the header. Sheets in
    # this export carry a title row above it.
    header, start = None, 0
    for i, r in enumerate(rows):
        if len([c for c in r if c.strip()]) > 1:
            header, start = r, i + 1
            break
    if header is None:
        return []

    out = []
    for r in rows[start:]:
        cells = list(r) + [""] * (len(header) - len(r))
        selector = (cells[0] or "").strip()
        if not selector:
            continue
        for col in range(1, len(header)):
            text = (cells[col] or "").strip()
            if not text:
                continue
            out.append({
                "selector": selector,
                "column": (header[col] or "").strip(),
                "dss": text,
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="directory of the exported .html tabs")
    ap.add_argument("-o", "--out", default="content/dss_unified.json")
    a = ap.parse_args()

    files = sorted(pathlib.Path(a.src).glob("*.html"))
    if not files:
        print(f"no .html exports in {a.src}", file=sys.stderr)
        return 1

    names = {f.name for f in files}
    tabs, skipped = {}, []
    for f in files:
        # "Copy of X" is a snapshot of X. They differ, so picking by directory
        # order would quietly choose a version nobody decided on.
        m = re.match(r"^Copy of (.+)$", f.name)
        if m and m.group(1) in names:
            skipped.append(f"{f.name} — superseded by {m.group(1)}")
            continue
        rows = parse_tab(f)
        if not rows:
            skipped.append(f"{f.name} — no rows parsed")
            continue
        tabs[f.stem] = {"type": type_of(f.stem), "rows": rows}

    total = sum(len(t["rows"]) for t in tabs.values())
    payload = {
        "_source": "DSS (New Unified View) export",
        "_note": "Imported by tools/import_dss.py. Where a scenario appears "
                 "here and in the live sheet, THIS one wins.",
        "tabs": tabs,
    }
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")

    print(f"{len(tabs)} tab(s), {total} row(s) -> {out}")
    for name, t in sorted(tabs.items()):
        print(f"  {t['type']:20} {len(t['rows']):3} rows  {name}")
    # Said out loud: a skip nobody reports is indistinguishable from a file
    # that was never in the export.
    for s in skipped:
        print(f"  SKIPPED  {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
