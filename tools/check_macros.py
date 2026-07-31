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
    args = ap.parse_args()

    print(f"reading {PATH}\n")

    # 1. does it parse at all?
    try:
        import yaml
    except ImportError:
        print("PyYAML is not installed here: pip install pyyaml")
        return 2
    try:
        with open(PATH, encoding="utf-8") as f:
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

    # 3. what the app will actually load
    print("[ ok ] required fields present" if not _problems
          else f"[FAIL] {len(_problems)} problem(s) below")
    try:
        from server.prompts import (BRAND_VOICE, TAKEDOWN_LINES,
                                    UNTRACEABLE_REPLY, macro_tags)
        print(f"\n  brand voice      {len(BRAND_VOICE.splitlines())} lines")
        print(f"  takedown lines   {', '.join(sorted(TAKEDOWN_LINES))}")
        print(f"  untraceable      {len(UNTRACEABLE_REPLY.split())} words")
        print(f"  macro tags       trustpilot {len(macro_tags('trustpilot'))}, "
              f"social {len(macro_tags('social'))}, "
              f"twitter {len(macro_tags('twitter'))}")
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
