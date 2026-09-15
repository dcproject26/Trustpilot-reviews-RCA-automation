"""One line of defence for command-line tools that print non-ASCII.

WHY THIS EXISTS. Windows hands a Python process a cp1252 stdout by default.
The moment a tool prints a box-drawing rule, an arrow or a tick, the encode
raises UnicodeEncodeError and the process dies MID-REPORT: the reader gets a
half-printed diagnostic and a traceback, exactly where a clean "checked, found
nothing" should have been. `check_runs.py` and `check_timeline.py` both did
this, and because the crash happened after some output it read like a broken
database rather than a broken console.

That is rule 1 of CLAUDE.md pointed at the terminal: a tool that cannot print
its answer is indistinguishable from a tool that has no answer.

Call `utf8_stdio()` first thing in main(). It is a no-op where stdout is
already utf-8 (Linux, the Replit shell) and the fix where it is not.
"""
from __future__ import annotations

import sys


def utf8_stdio() -> None:
    """Force stdout/stderr to utf-8 so non-ASCII output cannot kill the tool."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
