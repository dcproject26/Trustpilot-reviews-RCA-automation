"""
tests/test_classifier_validators.py

Unit tests for the classifier's validation + fallback logic.

Proves the "resilience without multi-agent" pattern: when Claude hallucinates
or breaks, the classifier degrades gracefully instead of poisoning the pipeline.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services.classifier import classify, _safe_parse, _strip_comments_outside_strings


def mock_call(response_text):
    async def _call(prompt):
        return response_text
    return _call


def test(name, condition, note=""):
    mark = "✓" if condition else "✗"
    print(f"  {mark} {name}" + (f" — {note}" if note else ""))
    return condition


async def run_tests():
    passed = 0
    failed = 0

    def check(name, condition, note=""):
        nonlocal passed, failed
        if test(name, condition, note):
            passed += 1
        else:
            failed += 1

    # ═════════════════════════════════════════════════════════════════════
    print("\n[Group 1] Happy path — valid output")
    # ═════════════════════════════════════════════════════════════════════
    valid = json.dumps({
        "l1": "Operations Issue",
        "l2": "Ticket Issues",
        "sub_theme": "B. Ticket Not Received",
        "review_summary": "test",
        "reasoning": "test",
    })
    r = await classify("test review", {}, [], mock_call(valid))
    check("Valid triple accepted", r.l1 == "Operations Issue" and r.l2 == "Ticket Issues")
    check("Sub-theme preserved", r.sub_theme == "B. Ticket Not Received")
    check("No warnings on valid input", len(r.warnings) == 0)

    # ═════════════════════════════════════════════════════════════════════
    print("\n[Group 2] Hallucinated L1")
    # ═════════════════════════════════════════════════════════════════════
    bad_l1 = json.dumps({
        "l1": "SP Screwup",  # Invalid — not in taxonomy
        "l2": "Guide No Show",
        "sub_theme": "A. Guide No Show",
    })
    r = await classify("test", {}, [], mock_call(bad_l1))
    check("Invalid L1 salvaged via L2 lookup",
          r.l1 == "Supply Partner Issue" and r.l2 == "Guide No Show",
          f"got l1='{r.l1}' l2='{r.l2}'")
    check("Warning recorded for invalid L1", any("Invalid L1" in w for w in r.warnings))

    # ═════════════════════════════════════════════════════════════════════
    print("\n[Group 3] Hallucinated L2")
    # ═════════════════════════════════════════════════════════════════════
    bad_l2 = json.dumps({
        "l1": "Operations Issue",
        "l2": "Fake L2 Category",  # Not in Operations Issue L2s
        "sub_theme": None,
    })
    r = await classify("test", {}, [], mock_call(bad_l2))
    check("Invalid L2 recognized", any("L2" in w and "invalid" in w for w in r.warnings))
    check("L2 dropped to empty when unrecoverable", r.l2 == "")
    check("L1 preserved", r.l1 == "Operations Issue")

    # ═════════════════════════════════════════════════════════════════════
    print("\n[Group 4] Hallucinated sub-theme")
    # ═════════════════════════════════════════════════════════════════════
    bad_st = json.dumps({
        "l1": "Operations Issue",
        "l2": "Ticket Issues",
        "sub_theme": "Z. Made-up thing",
    })
    r = await classify("test", {}, [], mock_call(bad_st))
    check("Invalid sub-theme flagged", any("Sub-theme" in w for w in r.warnings))
    check("Rest of classification survives", r.l1 == "Operations Issue" and r.l2 == "Ticket Issues")

    # ═════════════════════════════════════════════════════════════════════
    print("\n[Group 5] Sub-theme salvage — code prefix only")
    # ═════════════════════════════════════════════════════════════════════
    just_code = json.dumps({
        "l1": "Operations Issue",
        "l2": "Ticket Issues",
        "sub_theme": "B",  # Just the code
    })
    r = await classify("test", {}, [], mock_call(just_code))
    check("Bare code 'B' salvaged to full name",
          r.sub_theme == "B. Ticket Not Received",
          f"got '{r.sub_theme}'")

    # ═════════════════════════════════════════════════════════════════════
    print("\n[Group 6] Sub-theme salvage — code with extra text")
    # ═════════════════════════════════════════════════════════════════════
    messy_code = json.dumps({
        "l1": "Supply Partner Issue",
        "l2": "Guide No Show",
        "sub_theme": "a. guide-no-show!!",  # lowercase, punctuation
    })
    r = await classify("test", {}, [], mock_call(messy_code))
    check("Messy 'a. guide-no-show!!' salvaged",
          r.sub_theme == "A. Guide No Show",
          f"got '{r.sub_theme}'")

    # ═════════════════════════════════════════════════════════════════════
    print("\n[Group 7] Sub-theme should be null when no framework")
    # ═════════════════════════════════════════════════════════════════════
    st_when_none = json.dumps({
        "l1": "Operations Issue",
        "l2": "Customer Support Issues",  # No framework yet for this L2
        "sub_theme": "A. Something",  # Wrong — should be null
    })
    r = await classify("test", {}, [], mock_call(st_when_none))
    check("Sub-theme forced to null when no framework", r.sub_theme is None)
    check("Warning recorded", any("no framework" in w for w in r.warnings))

    # ═════════════════════════════════════════════════════════════════════
    print("\n[Group 8] JSON parsing resilience")
    # ═════════════════════════════════════════════════════════════════════
    wrapped = f"""```json
{valid}
```"""
    r = await classify("test", {}, [], mock_call(wrapped))
    check("Markdown fences stripped", r.l1 == "Operations Issue")

    with_comments = """{
  "l1": "Operations Issue",  // this is a comment
  "l2": "Ticket Issues",
  /* block comment */
  "sub_theme": "B. Ticket Not Received",
  "review_summary": "test",
  "reasoning": "test",
}"""  # note trailing comma
    r = await classify("test", {}, [], mock_call(with_comments))
    check("Comments + trailing commas parsed",
          r.l1 == "Operations Issue" and r.l2 == "Ticket Issues")

    # URLs inside strings must survive comment stripping
    with_url = json.dumps({
        "l1": "Operations Issue",
        "l2": "Ticket Issues",
        "sub_theme": "B. Ticket Not Received",
        "review_summary": "See https://example.com/foo",
        "reasoning": "See https://example.com/bar",
    })
    r = await classify("test", {}, [], mock_call(with_url))
    check("URL in string survives comment stripper",
          "https://example.com" in r.review_summary,
          f"got summary='{r.review_summary}'")

    # ═════════════════════════════════════════════════════════════════════
    print("\n[Group 9] Total failures")
    # ═════════════════════════════════════════════════════════════════════
    r = await classify("test", {}, [], mock_call("this is not JSON at all"))
    check("Garbage response → empty result, no crash", r.l1 == "" and r.l2 == "")
    check("Warning recorded for parse failure", any("JSON" in w for w in r.warnings))

    async def broken_call(prompt):
        raise RuntimeError("simulated API failure")
    r = await classify("test", {}, [], broken_call)
    check("API exception → empty result, no crash", r.l1 == "" and r.l2 == "")
    check("Exception recorded in warnings", any("Claude call failed" in w for w in r.warnings))

    # ═════════════════════════════════════════════════════════════════════
    print("\n[Group 10] Standalone _strip_comments_outside_strings")
    # ═════════════════════════════════════════════════════════════════════
    txt = '{"url": "https://example.com/a//b", "note": "hi // world"}  // real comment'
    cleaned = _strip_comments_outside_strings(txt)
    check("Comment-in-string preserved",
          "https://example.com/a//b" in cleaned and "hi // world" in cleaned)
    check("Real comment stripped", "real comment" not in cleaned)

    # ═════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print(f"RESULT: {passed} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(run_tests())
    sys.exit(0 if ok else 1)
