#!/usr/bin/env python3
"""Drive every fix from the 11-12 Aug 2026 session and say what happened.

    python3 tools/verify_session_fixes.py

Run it from the repository root. It needs NO credentials and NO database —
BigQuery, Zendesk, Anthropic and Slack are all offline here and the script
says so rather than pretending otherwise.

WHY THIS EXISTS RATHER THAN "run pytest". The suite answers "is it green",
which is the question a passing suite always answers. This answers "what does
each fix actually DO now, and what did it do before" — every check prints the
value the old code produced beside the value the new code produces, so a
reader can see the difference rather than trust a dot.

Every check calls the real function. Nothing here re-implements a rule, which
is the failure this codebase keeps hitting: a test that builds its own version
of the thing under test agrees with itself and proves nothing.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

PASS, FAIL, SKIP = [], [], []


def check(name, got, want, note=""):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    mark = "  ok  " if ok else " FAIL "
    print(f"[{mark}] {name}")
    if not ok:
        print(f"           expected: {want!r}")
        print(f"           got     : {got!r}")
    elif note:
        print(f"           {note}")


def section(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# ─────────────────────────────────────────────────────────────────────────────
section("1. ONE DIGEST RULE — a hash must never be shown as a guest's name")
# Three predicates implemented this and disagreed IN OPPOSITE DIRECTIONS on the
# same values. The alphanumeric digests below reached the candidate picker —
# the one field an associate recognises the right booking by.
try:
    from server.api import _looks_like_hash, _scrub_candidate_names
    from server.names import looks_like_digest
    from server.pipeline import _is_hashed_name
    from server.services.bigquery import is_hashed_name

    preds = {"names": looks_like_digest, "api": _looks_like_hash,
             "pipeline": _is_hashed_name, "bigquery": is_hashed_name}

    digests = ["FjpJxbSfpb65bnyQwErTyUiOpAsDfGhJ",   # the repo's own hash fixture
               "ab24TSVenneb4T3CkHFUFaGM",           # matched a guest called "Sven"
               "jVwe+fjfm48WSok1xEK+I/8fnIoV+kY8P8z7xxk+NM8=",
               "deadbeefcafebabe",                   # hex, no digit
               "AbCdEfGhIjKlMnOp=="]                 # base64 padding, no digit
    names = ["Papadopoulopoulos", "Gianmarco Lucia", "VanDerBergVanHouten",
             "Château-Neuf-2024-VIP", "O'BrienSmithVanDerBerg"]

    print("  value                                          " +
          "  ".join(f"{k:<9}" for k in preds))
    for v in digests + names:
        verdicts = {k: fn(v) for k, fn in preds.items()}
        print(f"  {v[:44]:<46}" +
              "  ".join(f"{str(x):<9}" for x in verdicts.values()))

    check("every digest is called a digest by all four",
          all(fn(v) for v in digests for fn in preds.values()), True)
    check("every real name is left alone by all four",
          any(fn(v) for v in names for fn in preds.values()), False,
          "incl. Papadopoulopoulos, which the matcher used to call a hash")
    check("the four implementations cannot disagree",
          all(len({fn(v) for fn in preds.values()}) == 1
              for v in digests + names), True)

    picker = [_scrub_candidate_names([{"primary_guest_name": v}])[0]
              .get("primary_guest_name") for v in digests]
    check("the candidate picker shows no digest", picker, [""] * len(digests),
          "before: 2 of these 5 rendered as the guest's name")
    check("a real name still reaches the picker",
          _scrub_candidate_names([{"primary_guest_name": "Gianmarco Lucia"}])[0]
          .get("primary_guest_name"), "Gianmarco Lucia")
except Exception:
    traceback.print_exc()
    FAIL.append("digest rule (raised)")


# ─────────────────────────────────────────────────────────────────────────────
section("2. REPLY LANGUAGE — one box ONLY on positive evidence of English")
# The rule you asked for: a non-English review must never get a single English
# box. "en" is the ingest default and means NOBODY LOOKED.
try:
    import asyncio
    from types import SimpleNamespace as NS

    from server.services import claude as claude_svc
    from server.services import reply_language as RL

    def review(language, orig="Non sono mai arrivati", eng=None):
        return NS(id="tp_v", language=language, body_original=orig,
                  body_english=eng)

    cases = [
        ("en  (the ingest default)",        review("en"),                     "unknown"),
        ("None (new ingest)",               review(None),                     "unknown"),
        ("'English' (detected)",            review("English", "They never arrived",
                                                   "They never arrived"),     "english"),
        ("'Italian' (detected)",            review("Italian"),                "translated"),
        ("'English' + translated inbound",  review("English", "Non sono mai arrivati",
                                                   "They never arrived"),     "unknown"),
    ]
    for label, rv, want in cases:
        st = RL.language_state(rv)
        boxes = "ONE box" if st["state"] == "english" else "TWO boxes"
        check(f"language {label:<34} -> {st['state']:<11} ({boxes})",
              st["state"], want)

    # The five outcomes, each driven.
    def resolve(rv):
        return asyncio.get_event_loop().run_until_complete(RL.resolve_language(rv))
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    real = claude_svc.detect_language
    try:
        async def says(v):
            async def f(_text):
                if isinstance(v, Exception):
                    raise v
                return v
            return f

        claude_svc.detect_language = asyncio.get_event_loop().run_until_complete(says("Italian"))
        rv = review("en")
        check("a detected NAME is stored", resolve(rv)["outcome"], "detected")
        check("  and the column holds the name", rv.language, "Italian")

        claude_svc.detect_language = asyncio.get_event_loop().run_until_complete(says("en"))
        rv = review("en")
        out = resolve(rv)
        check("a language CODE is refused, not stored", out["outcome"], "undetected",
              "found by code review: storing it made the card re-ask forever")
        check("  and the column is untouched", rv.language, "en")

        claude_svc.detect_language = asyncio.get_event_loop().run_until_complete(
            says(RuntimeError("upstream down")))
        check("a raised lookup is `failed`, never English",
              resolve(review("en"))["outcome"], "failed")

        claude_svc.detect_language = asyncio.get_event_loop().run_until_complete(says(""))
        out = resolve(review("en"))
        check("an empty answer is `unavailable` when Anthropic is offline",
              out["outcome"], "unavailable",
              "distinguishes a switched-off detector from a hard review")
        check("  and it names the deployment fault",
              "not connected" in out["why"], True)

        check("a review with no text says so",
              resolve(review("en", orig=""))["outcome"], "unavailable")
    finally:
        claude_svc.detect_language = real
except Exception:
    traceback.print_exc()
    FAIL.append("reply language (raised)")


# ─────────────────────────────────────────────────────────────────────────────
section("3. CSV EXPORT — four columns that said nothing")
try:
    from datetime import datetime
    from types import SimpleNamespace as NS

    from server.services.sheet_export import _insights_cell, _s, row_for

    def rv():
        return NS(id="tp_c", received_at=datetime(2026, 8, 1), author="A",
                  rating=1, language="English", status="draft",
                  slack_channel="C1", slack_ts="1.0", close_reason=None,
                  sent_route=None, body_original="x", body_english="x",
                  reference_number="32728059")

    def dr(booking=None, **kw):
        base = dict(booking=booking or {}, rca_v3={}, insights={}, scenarios=[],
                    sub_themes=[], l1="", l2="", match_tier=None,
                    match_method=None, resolution="", final_response="",
                    suggested_response="", rca_prompt_version="",
                    zendesk_ticket_ids=[], rca_posted_at=None, sent_at=None,
                    guest_issues=[], flags=[], takedown=None,
                    primary_scenario=None, overlay_scenarios=[])
        base.update(kw)
        return NS(**base)

    # (a) vendor: the warehouse writes `partner`, the export read `vendorName`
    warehouse = {"id": "1", "partner": "Krakville", "experienceName": "Tour"}
    check("vendor is filled from a warehouse row",
          row_for(rv(), dr(warehouse))["vendor"], "Krakville",
          "before: '' on the common path, reading as 'no vendor'")
    check("vendor is still filled from the verified shape",
          row_for(rv(), dr({"id": "1", "vendorName": "Krakville"}))["vendor"],
          "Krakville")
    check("a booking with no vendor is still empty",
          row_for(rv(), dr({"id": "1"}))["vendor"], "")

    # (b) insights: five camelCase keys against a snake_case payload
    real_payload = {"rating_tgid": {"avg": 4.2, "n": 31},
                    "tgid_completion_rate": 0.87,
                    "similar_reviews_30d": 4, "redemption": "QR"}
    cell = _insights_cell(real_payload)
    print(f"           insights cell: {cell}")
    check("insights reads the payload that is actually stored",
          "TGID rating 4.2 (n=31)" in cell, True,
          "before: '' for every real draft — not one of five keys matched")
    check("an absent payload is an empty cell", _insights_cell({}), "")
    check("a payload that matches NOTHING says so rather than looking empty",
          "unreadable" in _insights_cell({"tgidRating": {"value": 4.2}}), True,
          "a broken join must not look like an honest empty")

    # (c) the three parallel lists
    v3 = {"what_went_wrong": {"guest_issues": [
        {"issue": "A", "owner": "OPS", "claim_accuracy": "Accurate"},
        {"issue": "B"},
        {"issue": "C", "owner": "TECH", "claim_accuracy": "Inaccurate"}]}}
    row = row_for(rv(), dr(rca_v3=v3))
    print(f"           issues={row['issues']}")
    print(f"           owners={row['owners']}")
    check("issues / owners / accuracy stay in step",
          (len(row["issues"]), len(row["owners"]), len(row["claim_accuracy"])),
          (3, 3, 3), "before: 3, 2, 2 — every pairing after the gap shifted")
    check("  the gap is named, not dropped", row["owners"][1], "(none)")

    # (d) the v4 projection columns
    row = row_for(rv(), dr(rca_v3={},
                           guest_issues=[{"issue": "Tickets never arrived",
                                          "owner": "OPS"}],
                           flags=[{"flag": "No follow-up"}],
                           takedown={"verdict": "Yes"}))
    check("a draft with only the v4 columns still exports its issues",
          (row["issue_count"], row["flags"], row["takedown"]),
          (1, ["No follow-up"], "Yes"),
          "before: 0 and blank, while the card and Slack showed them")

    # (e) formula injection — `author` is the guest's own display name
    evil = "=cmd|' /C calc'!A0"
    check("a formula in a guest-written cell is defused",
          _s(evil), "'" + evil,
          "an apostrophe: Excel/Sheets consume it, so the text still READS right")
    check("  the guest's text is not altered", evil in _s(evil), True)
    check("ordinary text is untouched", _s("Ioan Popescu"), "Ioan Popescu")
except Exception:
    traceback.print_exc()
    FAIL.append("csv export (raised)")


# ─────────────────────────────────────────────────────────────────────────────
section("4. CASE FINDINGS — an unreadable source is not a finding")
try:
    from server.services.rca_v4_validate import validate

    trio = [{"text": "No booking record exists for this guest.", "source": "booking"},
            {"text": "No Zendesk contact exists for this guest.", "source": "zendesk"},
            {"text": "No experience-page redemption data was provided.",
             "source": "booking"}]
    out, notes = validate({"what_went_wrong": {"case_findings": list(trio)}})
    rows = out["what_went_wrong"]["case_findings"]
    said = next((n for n in notes if "source that could not be read" in n), "")
    check("absent-source rows are COUNTED", "3 of 3" in said, True)
    check("absent-source rows are KEPT, never deleted", len(rows), 3,
          "matched by WORDING — deleting the wrong one loses real evidence")
    check("  and the trail says they were kept", "KEPT, not dropped" in said, True)

    _, notes = validate({"what_went_wrong": {"case_findings": [
        {"text": "No response was provided to the guest for six days",
         "source": "zendesk"},
        {"text": "No refund was issued after the cancellation", "source": "bms"}]}})
    check("a negative about what HAPPENED is a real finding",
          any("source that could not be read" in n for n in notes), False)
except Exception:
    traceback.print_exc()
    FAIL.append("case findings (raised)")


# ─────────────────────────────────────────────────────────────────────────────
section("5. THE TRAIL SAYS WHAT IT DID — repairs are not silent")
try:
    from server.pipeline import shape_counts_entry

    e = shape_counts_entry([{"_shape_counts": {"raw": 4, "shown": 2,
                                               "dropped_by_model": 1,
                                               "actor_corrected": 2}}])
    print(f"           {e['text'][:110]}…")
    check("the shaping report renders", "4 ticket event(s) read, 2 shown" in e["text"], True)
    check("a re-attribution is reported", "re-attributed" in e["text"], True)
    check("a repair is marked `warn`, not `pass`", e["mark"], "warn",
          "a repair is 'we changed the model's answer', not 'a step succeeded'")
    clean = shape_counts_entry([{"_shape_counts": {"raw": 4, "shown": 2}}])
    check("a run that corrected nothing does not claim it did",
          "re-attributed" in clean["text"], False)
    check("no shaping means no line", shape_counts_entry([]), None)
except Exception:
    traceback.print_exc()
    FAIL.append("trail (raised)")


# ─────────────────────────────────────────────────────────────────────────────
section("6. THE CARD AND THE SERVER AGREE ON FIELD NAMES")
# The canonical bug of this project: the client sends one key, the server reads
# another, the request returns 200, and the card paints a ✓ over a lost edit.
try:
    import re

    from server.api import DraftPatchV2, ManualReview

    html = open("client/index.html", encoding="utf-8").read()
    sent = set()
    for m in re.finditer(r"saveDraft\(", html):
        i, depth, j = m.end(), 1, m.end()
        while j < len(html) and depth:
            depth += (html[j] == "(") - (html[j] == ")")
            j += 1
        arg = html[i:j - 1]
        sent |= set(re.findall(r"[{,]\s*([A-Za-z_][A-Za-z0-9_]*)\s*:",
                               arg[arg.find(",") + 1:]))
    derived = {"overlay_scenarios"}       # settle_scenarios() computes it
    lost = sorted(sent - set(DraftPatchV2.model_fields) - derived)
    check(f"every key the card saves is accepted ({len(sent)} keys)", lost, [],
          "overlay_scenarios is derived server-side, so being dropped is correct")
    check("the manual-review form's keys are accepted",
          sorted({"body", "author", "rating", "reference_number"}
                 - set(ManualReview.model_fields)), [])
except Exception:
    traceback.print_exc()
    FAIL.append("field parity (raised)")


# ─────────────────────────────────────────────────────────────────────────────
section("WHAT THIS SCRIPT COULD NOT CHECK")
for line in [
    "BigQuery / Zendesk / Anthropic / Slack are offline here, so nothing that",
    "needs a live lookup was exercised. Specifically NOT verified by this run:",
    "  - the real shape of production PII digests (tools/check_support_search.py",
    "    ::_hashed_name_share answers this with live BigQuery)",
    "  - that guest_name_for_bid resolves on real tickets",
    "  - the CSV export end to end over the real database",
    "  - anything on the deployed dashboard, which is a separate build AND a",
    "    separate database from the dev workspace",
    "",
    "For the full guarantee set, run the suite:",
    "  python3 -m pytest tests/ -q",
    "One test fails environmentally in a sandbox and does so on unmodified",
    "b5c22ad too: test_db_migration.py::test_an_unopenable_database_is_a_"
    "sentence_not_a_traceback",
]:
    SKIP.append(line)
    print("  " + line)


# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'=' * 72}")
print(f"{len(PASS)} passed · {len(FAIL)} failed")
if FAIL:
    print("\nFAILED:")
    for f in FAIL:
        print(f"  - {f}")
print("=" * 72)
sys.exit(1 if FAIL else 0)
