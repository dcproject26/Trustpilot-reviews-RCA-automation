"""Does the export actually work against the real sheet? Dry run by default.

WHY A PREFLIGHT AND NOT JUST "TRY IT". The write happens inside /refresh-slack
and /send, where a failure is caught, logged and deliberately ignored — the
review going out matters more than the row. That is right, and it means the
first sign of a misconfigured sheet is an empty spreadsheet and a line in a
log nobody is watching. This asks the same questions in the open.

FIVE THINGS CAN BE WRONG, and they fail in ways that read alike from the
outside — an empty sheet:

  the credential          GCP_SERVICE_ACCOUNT_JSON unset, malformed, or the
                          placeholder pasted verbatim. Decided with no network
                          at all, and reported on its own, because it used to
                          be reported as a sharing problem — see below.
  the sharing             the sheet not SHARED WITH THE SERVICE ACCOUNT as an
                          editor. The same credential already reads three other
                          sheets, so a working read proves nothing about this
                          one. Only claimed once the credential is known good;
                          a value that never parsed had no identity to share
                          the sheet with, and saying "share it" sends you to
                          fix something that was never broken.
  the tab                 the URL carries a gid, the API wants a name.
  the header              a column added in code and not in the sheet shifts
                          every value one place left and stays plausible.
  the row itself          arrival writes one shape, completion another, and
                          completion REFUSES to append.

Each is checked and named separately. Nothing is written unless you pass
--apply.

    python3 scripts/check_sheet.py                    # reachability + header
    python3 scripts/check_sheet.py tp_abc123          # what this review would write
    python3 scripts/check_sheet.py tp_abc123 --apply  # actually write it

Read-only without --apply. With it, one review's rows are written for real.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("review_id", nargs="?",
                    help="a review to plan (or write, with --apply)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without it nothing is changed.")
    ap.add_argument("--stage", choices=("received", "sent"), default="",
                    help="which phase to plan. Default: both.")
    a = ap.parse_args(argv)

    from server.config import (RCA_EXPORT_SHEET_ID, RCA_EXPORT_SHEET_TAB,
                               GCP_SERVICE_ACCOUNT_JSON, is_live)
    from server.services import sheet_export as X

    print("\n=== CONFIG ===")
    print(f"  sheet   {RCA_EXPORT_SHEET_ID or '(unset)'}")
    print(f"  tab     {RCA_EXPORT_SHEET_TAB or '(unset)'}"
          + ("  — a gid; it will be resolved to the tab name"
             if str(RCA_EXPORT_SHEET_TAB).strip().lstrip("-").isdigit() else ""))
    print(f"  creds   {'set' if GCP_SERVICE_ACCOUNT_JSON else 'NOT SET'}")
    print(f"  live    {is_live('sheet_export')}")
    if not is_live("sheet_export"):
        # WHAT IS ACTUALLY KNOWABLE FROM HERE, and no more.
        #
        # A first version of this compared against is_live("dss") and friends
        # on the theory that they share the credential — and running it showed
        # they do NOT test it: `"dss": bool(DSS_SHEET_ID)` is a sheet id with a
        # hardcoded default, true on a machine with no credentials at all. So
        # it reported "the credential is readable" on the machine that had
        # none. A diagnostic that guesses is worse than one that says it
        # cannot tell.
        print("\n  The export is INERT — nothing is written, nothing is "
              "broken.")
        if not GCP_SERVICE_ACCOUNT_JSON:
            print("  GCP_SERVICE_ACCOUNT_JSON is not readable IN THIS SHELL.")
            print("  That does not mean the server lacks it: Replit keeps "
                  "secrets in the run\n  environment and a plain shell often "
                  "cannot see them. Nothing here can\n  tell those apart.")
            print("  To find out, look at the DEPLOYMENT's log for '[sheet]' "
                  "lines after a\n  review is ingested — that is the process "
                  "that actually writes.")
        else:
            print("  Set RCA_EXPORT_SHEET_ID to turn it on.")
        return 1

    # THE CREDENTIAL, BEFORE THE NETWORK. `creds set` above is only "the
    # variable is non-empty" — a placeholder pasted verbatim satisfies it, and
    # used to fail later as COULD NOT READ THE SPREADSHEET with advice to go
    # share the sheet. Everything decidable without a request is decided here
    # so that a failure past this point really is the sheet or the sharing.
    print("\n=== THE CREDENTIAL ===")
    src, why = X.auth_source()
    if why:
        print(f"  UNUSABLE ({src}): {why}")
        print("\n  Nothing was asked of Google. The sharing is NOT implicated "
              "— there was no\n  identity for the sheet to be shared with.")
        return 1
    if src == "connector":
        # AND THE SHARING STEP IS GONE, which is worth saying out loud: the
        # rest of this script's advice is about a share that does not apply
        # on this route.
        print("  the Replit Google Sheets connector, OAuth as whoever "
              "connected it.")
        print("  NO SHARING STEP — it acts as that person, who already owns "
              "the sheet.")
    else:
        import json as _json
        print(f"  a service account: "
              f"{_json.loads(GCP_SERVICE_ACCOUNT_JSON)['client_email']}")
        print("  ^ THIS is the address the sheet must be shared with, as "
              "Editor.")

    io = X.SheetIO(RCA_EXPORT_SHEET_ID, RCA_EXPORT_SHEET_TAB)

    print("\n=== THE TAB ===")
    try:
        name = io.resolve_tab()
    except Exception as e:
        print(f"  COULD NOT READ THE SPREADSHEET: {e}")
        print("\n  The credential above is well-formed, so this is the SHARING "
              "or the sheet\n  id. Share it with the address printed above as "
              "an editor — reading three\n  other sheets with the same "
              "credential proves nothing, they are all\n  read-only.")
        return 1
    print(f"  writing to tab {name!r}")

    print("\n=== THE HEADER ===")
    try:
        header, ids = io.read_column_a_and_header()
    except Exception as e:
        print(f"  COULD NOT READ IT: {e}")
        return 1
    why = X.check_header(header)
    if not header:
        print(f"  The tab is EMPTY. The header will be written on the first "
              f"export:\n  {len(X.COLUMNS)} columns, starting "
              f"{', '.join(X.COLUMNS[:4])}…")
    elif why:
        print(f"  REFUSED: {why}")
        print("\n  Nothing will be written until this matches. That is "
              "deliberate — a column\n  added in code and not in the sheet "
              "shifts every value one place left,\n  and the sheet stays "
              "perfectly plausible with dates under 'author'.")
        return 1
    else:
        print(f"  Matches all {len(X.COLUMNS)} columns.")
    print(f"  {len(ids)} review row(s) already there.")
    dupes = X.duplicate_ids(ids)
    if dupes:
        print(f"  {len(dupes)} id(s) appear more than once: "
              f"{', '.join(dupes[:5])}")
        print("  An upsert updates the FIRST and leaves the rest looking "
              "current.")

    if not a.review_id:
        print("\n  Reachable and writable. Pass a review id to see what it "
              "would write.")
        return 0

    # ── what this review would do, through the real planner ────────────────
    from server.db import SessionLocal, RcaDraft, Review
    s = SessionLocal()
    try:
        r = s.query(Review).filter(Review.id == a.review_id).first()
        if not r:
            print(f"\nNo review {a.review_id!r} in this database.")
            return 1
        d = s.query(RcaDraft).filter(RcaDraft.review_id == a.review_id).first()

        stages = ([(a.stage, a.stage == "sent")] if a.stage
                  else [("received", False), ("sent", True)])
        for stage, require in stages:
            row = (X.arrival_row(r) if stage == "received"
                   else X.row_for(r, d, stage=X.DONE))
            updates, appends, orphans = X.plan(ids, [row], require_existing=require)
            print(f"\n=== PHASE {stage.upper()} ===")
            if updates:
                print(f"  UPDATE row {updates[0][0]}")
            elif appends:
                print("  APPEND a new row")
            elif orphans:
                print("  REFUSED — no arrival row for this review, so the "
                      "completed row is\n  NOT written. Appending would hide "
                      "a broken arrival hook and then race\n  a late arrival "
                      "into a duplicate. Run the arrival phase first.")
            filled = [(c, v) for c, v in zip(X.COLUMNS, X.to_cells(row)) if v]
            print(f"  {len(filled)} of {len(X.COLUMNS)} cells filled:")
            for c, v in filled[:12]:
                print(f"     {c:<18} {' '.join(str(v).split())[:52]}")
            if len(filled) > 12:
                print(f"     … and {len(filled) - 12} more")

        if a.apply:
            print("\n=== WRITING ===")
            out = X.on_review_arrived(r)
            print(f"  arrival:    {out}")
            if d:
                out2 = X.on_review_finished(r, d)
                print(f"  completion: {out2}")
            else:
                print("  completion: skipped — this review has no draft yet")
        else:
            print("\nDRY RUN — nothing was written. Re-run with --apply.")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
