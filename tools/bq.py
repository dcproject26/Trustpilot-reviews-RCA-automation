#!/usr/bin/env python3
"""
Run a BigQuery query from the shell and print the rows as a table.

    python3 tools/bq.py "SELECT 1 AS one"
    python3 tools/bq.py -f query.sql
    cat query.sql | python3 tools/bq.py

Read-only by intent: anything that is not a SELECT or WITH is refused, so a
pasted statement cannot modify the warehouse.

This exists because SQL pasted straight into a shell is interpreted by the
shell - backticks run commands, parentheses are syntax errors, and the useful
half of the error message is about bash rather than the query. Wrapping it in
one argument avoids all of that.

    --whoami which GCP project runs these jobs, and as whom
    --dry    validate and report bytes scanned without running
    --limit  rows to print (default 50; the query is not modified)

Quote SQL with SINGLE quotes. Backticks around a table name are command
substitution inside double quotes, so the shell eats the table name before
Python sees it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services import bq_connector as BQ   # noqa: E402


def read_sql(argv):
    if "-f" in argv:
        with open(argv[argv.index("-f") + 1]) as fh:
            return fh.read()
    positional = [a for i, a in enumerate(argv)
                  if not a.startswith("-")
                  and (i == 0 or argv[i - 1] not in ("-f", "--limit"))]
    if positional:
        return " ".join(positional)
    # Only read stdin when it is actually a pipe. isatty() alone is false in
    # any non-interactive context - a CI step, a subprocess - and read() then
    # blocks forever with no output to explain why.
    import stat
    try:
        if stat.S_ISFIFO(os.fstat(sys.stdin.fileno()).st_mode):
            return sys.stdin.read()
    except (OSError, ValueError):
        pass
    return ""


def render(rows, limit):
    if not rows:
        print("(no rows)")
        return
    cols = list(rows[0].keys())
    width = {c: max(len(str(c)),
                    *(len(str(r.get(c, ""))) for r in rows[:limit])) for c in cols}
    width = {c: min(w, 60) for c, w in width.items()}
    print("  ".join(str(c).ljust(width[c]) for c in cols))
    print("  ".join("-" * width[c] for c in cols))
    for r in rows[:limit]:
        print("  ".join(str(r.get(c, ""))[:width[c]].ljust(width[c]) for c in cols))
    if len(rows) > limit:
        print(f"... {len(rows) - limit} more rows")


def main():
    argv = sys.argv[1:]

    if "--whoami" in argv:
        if not BQ.available():
            print("No BigQuery connection on this machine.\n"
                  "Run this on Replit, where the connector is bound.")
            return 2
        # Which project runs and pays for these jobs. It comes from the Replit
        # connector at runtime, not from anything in this repo, so the only
        # honest way to answer "which project" is to ask the session.
        render(BQ.run_query("SELECT @@project_id AS runs_in_project, "
                            "SESSION_USER() AS acting_as"), 10)
        return 0

    sql = read_sql(argv).strip().rstrip(";")
    if not sql:
        print(__doc__)
        return 1

    head = sql.lstrip().lstrip("(").split(None, 1)[0].upper()
    if head not in ("SELECT", "WITH", "DECLARE"):
        print(f"Refused: statement starts with {head!r}. "
              "This runs SELECT / WITH / DECLARE only.")
        return 1

    if not BQ.available():
        print("No BigQuery connection on this machine.\n"
              "Run this on Replit, where the connector is bound.")
        return 2

    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 50

    if "--dry" in argv:
        res = BQ.dry_run(sql)
        if res["ok"]:
            print(f"valid - would scan {res['bytes'] / 1e9:.2f} GB")
            return 0
        print(f"INVALID: {res['error']}")
        return 1

    try:
        render(BQ.run_query(sql), limit)
    except Exception as e:
        print(f"query failed: {str(e)[:600]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
