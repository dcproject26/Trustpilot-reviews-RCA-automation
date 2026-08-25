#!/usr/bin/env python3
"""
Check content/orm_macros.yaml before it goes live.

    python3 tools/check_macros.py            # validate and show what loaded
    python3 tools/check_macros.py --preview  # also print a sample reply

Run this after editing the copy file. It tells you whether the file parses,
whether anything required is missing, and exactly what the app will say to a
guest. A malformed edit never reaches a guest - the server falls back to its
built-in copy and logs why - but a fallback is not what you meant to ship, so
this makes the failure visible while you can still fix it.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "content", "orm_macros.yaml")

_problems: list[str] = []
_notes: list[str] = []


def bad(msg):
    _problems.append(msg)


def note(msg):
    _notes.append(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true",
                    help="print a sample untraceable reply and takedown block")
    # Check a DRAFT before overwriting the live file. Without this the only
    # thing anyone could validate was the copy already in use, which means the
    # only way to find out an edit is broken was to ship it.
    ap.add_argument("--file", default=PATH,
                    help="check this file instead of the installed one")
    args = ap.parse_args()
    path = args.file
    other = os.path.abspath(path) != os.path.abspath(PATH)

    print(f"reading {path}\n")

    # 1. does it parse at all?
    try:
        import yaml
    except ImportError:
        print("PyYAML is not installed here: pip install pyyaml")
        return 2
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"FAIL  the file does not exist at {PATH}")
        return 1
    except yaml.YAMLError as e:
        print("FAIL  the file is not valid YAML.\n")
        print(f"      {e}\n")
        print("      The usual causes: a line lost its indentation, a colon")
        print("      inside unquoted text, or a block that should start with |")
        return 1
    if not isinstance(raw, dict):
        print("FAIL  the file parsed but is not a set of fields.")
        return 1
    print("[ ok ] file parses as YAML")

    # 2. required fields
    for key in ("brand_voice", "sign_off", "takedown", "untraceable_reply"):
        if not raw.get(key):
            bad(f"'{key}' is missing or empty - the built-in copy would be used")

    td = raw.get("takedown") or {}
    lines = (td.get("lines") or {}) if isinstance(td, dict) else {}
    if not lines:
        bad("takedown.lines is empty - no takedown line could ever be added")
    for k, v in lines.items():
        if not isinstance(v, dict) or not str(v.get("text", "")).strip():
            bad(f"takedown line '{k}' has no text")
        elif "[link]" not in v["text"]:
            note(f"takedown line '{k}' has no [link] placeholder - intended?")
        if isinstance(v, dict) and not str(v.get("when", "")).strip():
            note(f"takedown line '{k}' has no 'when' - the model has no basis "
                 f"to choose it over the others")
    if not str(td.get("suppress_when", "")).strip():
        note("takedown.suppress_when is empty - the guardrail about abusive "
             "tone and repeat escalations is not being applied")

    ur = str(raw.get("untraceable_reply") or "")
    if ur and "{first_name}" not in ur:
        bad("untraceable_reply has no {first_name} - every guest would be "
            "greeted the same way")
    if "<Name>" in ur or "<name>" in ur:
        bad("untraceable_reply still contains a literal <Name> placeholder")

    so = str(raw.get("sign_off") or "")
    if so and "Headout" not in so:
        note("sign_off does not mention Headout - intended?")
    if ur and so.strip() and so.strip() in ur:
        note("the sign-off appears inside untraceable_reply as well; it is "
             "appended automatically, so it would be duplicated")

    hon = raw.get("honorifics") or []
    if not hon:
        note("honorifics is empty - a name like 'Frau Nicole' would be "
             "greeted as 'Hey Frau,'")

    tags = raw.get("macro_tags") or {}
    for ch in ("trustpilot", "social", "twitter"):
        if not tags.get(ch):
            note(f"macro_tags.{ch} is empty")

    # The "Picked up by" roster. A MISSPELLED KEY IS THE REAL RISK: `reviewer:`
    # or `reviewers :` parses as perfectly valid YAML, this file validates
    # clean, and the dashboard quietly falls back to the free-text box it
    # replaced. Nobody finds out until someone types a name again. So the count
    # is printed either way, and a near-miss key is called out by name.
    revs = raw.get("reviewers")
    if revs is None:
        near = [k for k in raw
                if k != "reviewers" and k.strip().lower().rstrip("s") == "reviewer"]
        if near:
            bad(f"there is no `reviewers:` key, but there IS {near[0]!r} — the "
                f"dropdown reads `reviewers` and will fall back to a text box")
        else:
            note("no `reviewers:` key - the \"Picked up by\" dropdown falls back "
                 "to a free-text box, which is how one person becomes three")
    elif not revs:
        note("`reviewers:` is empty - same effect as leaving it out")
    else:
        blanks = [i for i, r in enumerate(revs, 1)
                  if r is None or not str(r).strip()]
        if blanks:
            bad(f"reviewers entr{'y' if len(blanks) == 1 else 'ies'} "
                f"{', '.join(map(str, blanks))} "
                f"{'is' if len(blanks) == 1 else 'are'} blank - a bare '- ' "
                f"line. Delete the line; it cannot become a name.")
        dupes = sorted({str(r).strip() for r in revs
                        if revs.count(r) > 1 and r is not None})
        if dupes:
            note(f"listed twice: {', '.join(dupes)} - the dropdown will show "
                 f"the name twice")

    # 3. what the app will actually load
    print("[ ok ] required fields present" if not _problems
          else f"[FAIL] {len(_problems)} problem(s) below")
    # THE BLOCK BELOW DESCRIBES THE INSTALLED FILE, because it reads what the
    # app loaded at import. Under --file that is a DIFFERENT file from the one
    # just checked, and a report that silently mixes two files is worse than
    # one that omits the section. So it is labelled, and the roster — the one
    # line an editor is here to confirm — is re-derived from the file actually
    # checked.
    if other:
        print("\n  (the counts below are the INSTALLED file, "
              f"{os.path.basename(PATH)}, not the one checked above)")
    try:
        from server.prompts import (BRAND_VOICE, TAKEDOWN_LINES,
                                    UNTRACEABLE_REPLY, macro_tags)
        print(f"\n  brand voice      {len(BRAND_VOICE.splitlines())} lines")
        print(f"  takedown lines   {', '.join(sorted(TAKEDOWN_LINES))}")
        print(f"  untraceable      {len(UNTRACEABLE_REPLY.split())} words")
        print(f"  macro tags       trustpilot {len(macro_tags('trustpilot'))}, "
              f"social {len(macro_tags('social'))}, "
              f"twitter {len(macro_tags('twitter'))}")
        # FROM THE FILE JUST CHECKED, not from the import. The editor came
        # here to confirm the name they added actually arrived, and reading
        # that off the installed copy would answer a question they did not ask.
        # Printed even when it is 0, and the names are listed rather than only
        # counted: a count of 7 does not tell you which seven.
        from server.prompts import _reviewers
        revs_now = _reviewers(raw)
        print(f"  reviewers        {len(revs_now)}"
              + (f" - {', '.join(revs_now)}" if revs_now
                 else " - the \"Picked up by\" dropdown falls back to a text box"))
    except Exception as e:
        bad(f"the app could not load the file: {type(e).__name__}: {e}")

    if args.preview:
        from server.prompts import UNTRACEABLE_REPLY, takedown_block
        print("\n" + "─" * 74)
        print("UNTRACEABLE REPLY, as a guest named Nicole would receive it:")
        print("─" * 74)
        print(UNTRACEABLE_REPLY.format(first_name="Nicole"))
        print("\n" + "─" * 74)
        print("TAKEDOWN INSTRUCTION, when takedown = Yes:")
        print("─" * 74)
        print(takedown_block("Yes"))

    print()
    for n in _notes:
        print(f"[note] {n}")
    for p in _problems:
        print(f"[FAIL] {p}")
    print()
    if _problems:
        print("Fix the FAIL lines above, then run this again. Until then the "
              "app uses its built-in copy for anything broken.")
        return 1
    print("Good to ship. Restart the server for it to take effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
