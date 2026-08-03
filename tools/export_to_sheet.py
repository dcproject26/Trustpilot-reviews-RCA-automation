#!/usr/bin/env python3
"""Dump every review and its RCA into a Google Sheet.

    python3 tools/export_to_sheet.py --sheet <SHEET_ID>            # dry run
    python3 tools/export_to_sheet.py --sheet <SHEET_ID> --apply
    python3 tools/export_to_sheet.py --sheet <SHEET_ID> --apply --tab "RCAs"
    python3 tools/export_to_sheet.py --review tp_1785414103_572109 --apply

The sheet id is the long string in its URL:

    docs.google.com/spreadsheets/d/<THIS BIT>/edit

BEFORE IT WILL WORK: share the sheet with the service account as an EDITOR.
Reading a sheet needs it shared as a viewer; writing needs editor, and the
failure is a 403 that reads like the sheet does not exist. The address is the
`client_email` field inside GCP_SERVICE_ACCOUNT_JSON — this prints it for you
if the write is refused.

Safe to run repeatedly. Rows are keyed on review_id: a review already in the
sheet is UPDATED in place, so re-running an RCA replaces its row rather than
adding a second copy of the same review.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

B, OFF, DIM, Y = "\033[1m", "\033[0m", "\033[2m", "\033[33m"


def _service_account_email() -> str:
    try:
        from server.config import GCP_SERVICE_ACCOUNT_JSON
        return json.loads(GCP_SERVICE_ACCOUNT_JSON or "{}").get(
            "client_email", "(none — GCP_SERVICE_ACCOUNT_JSON is not set)")
    except Exception:
        return "(could not read GCP_SERVICE_ACCOUNT_JSON)"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", help="the sheet id from its URL; defaults to "
                                    "RCA_EXPORT_SHEET_ID")
    ap.add_argument("--tab", default="Sheet1", help="which tab to write into")
    ap.add_argument("--review", action="append",
                    help="only this review id (repeatable)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write; without it nothing is sent")
    a = ap.parse_args(argv)

    import os
    sheet = a.sheet or os.getenv("RCA_EXPORT_SHEET_ID", "")
    if not sheet:
        print("No sheet id. Pass --sheet <id>, or set RCA_EXPORT_SHEET_ID.")
        print("The id is the long string in the sheet's URL:")
        print("  docs.google.com/spreadsheets/d/<THIS BIT>/edit")
        return 2
    if "/" in sheet:
        # Pasting the whole URL is the obvious thing to do, so accept it
        # rather than failing on a value the person plainly meant.
        parts = [p for p in sheet.split("/") if p]
        sheet = next((p for p in parts if len(p) > 30), sheet)
        print(f"{DIM}read the sheet id out of that url: {sheet}{OFF}")

    import server.db as db
    from server.services import sheet_export as SX

    s = db.SessionLocal()
    try:
        q = s.query(db.Review)
        reviews = [r for r in q.all()
                   if not a.review or r.id in set(a.review)]
        rows = [SX.row_for(r, r.draft) for r in reviews]
    finally:
        s.close()

    if a.review:
        missing = set(a.review) - {r.id for r in reviews}
        if missing:
            # Naming what WAS there beats "not found" — a typo in an id and an
            # empty database produce the same silence otherwise.
            print(f"{Y}no review with id {', '.join(sorted(missing))}{OFF}")
    if not rows:
        print("Nothing to export — this database holds no matching reviews.")
        return 1

    print(f"{B}{len(rows)} review(s){OFF} -> sheet {sheet} tab {a.tab!r}")
    no_draft = sum(1 for r in reviews if r.draft is None)
    if no_draft:
        # A row with no RCA in it is not the same as a missing row, and the
        # sheet cannot say so on its own.
        print(f"  {Y}{no_draft} of them have no draft row{OFF} — their RCA "
              f"columns will be blank because there is no RCA, not because "
              f"the export dropped them")

    io = SX.SheetIO(sheet, a.tab)
    try:
        result = SX.export(io, rows, apply=a.apply)
    except Exception as e:
        msg = " ".join(str(e).split())[:200]
        print(f"\n{Y}could not reach the sheet{OFF} — {type(e).__name__}: {msg}")
        print(f"\nThe service account is:\n  {_service_account_email()}")
        print("Share the sheet with that address as an EDITOR. Viewer is "
              "enough to read and not to write, and the failure looks the "
              "same as the sheet not existing.")
        return 2

    if result["refused"]:
        print(f"\n{Y}REFUSED{OFF} — {result['refused']}")
        return 1
    if result["header_written"]:
        print("  the sheet was empty; the header row "
              + ("was written" if a.apply else "would be written"))
    if result["duplicates"]:
        print(f"  {Y}{len(result['duplicates'])} review id(s) appear more than "
              f"once in the sheet{OFF}: "
              + ", ".join(result["duplicates"][:6]))
        print("  The first of each is updated and the rest are left alone, so "
              "the stale copies will still look current. Delete them by hand.")

    print(f"\n  {result['updated']:5d} row(s) updated in place")
    print(f"  {result['appended']:5d} row(s) appended")
    if not a.apply:
        print(f"\nDRY RUN — nothing was written. Re-run with --apply.")
    else:
        print(f"\nhttps://docs.google.com/spreadsheets/d/{sheet}/edit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
