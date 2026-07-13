"""
ADDS server/services/classifier.py

The classifier is called from pipeline.py step 11. It combines what were previously
separate calls (L1, L2, sub-theme) into ONE Claude call, then validates the output
against the taxonomy. On invalid output, falls back gracefully — never breaks
the pipeline.

This is the "resilience without multi-agent" pattern: single call for context
efficiency, validators for correctness, try/except in pipeline for isolation.
"""
import json
import logging
import re
from typing import Optional

from server import prompts
from server.taxonomy import (
    is_valid_l1, is_valid_l1_l2, is_valid_sub_theme,
    has_sub_theme_framework, sub_theme_framework,
    L1_PRIORITY_ORDER, L2_OPTIONS,
)

log = logging.getLogger(__name__)


class ClassificationResult:
    """Structured result — always populated, never raises."""
    def __init__(self, l1="", l2="", sub_theme=None, review_summary="",
                 reasoning="", warnings=None):
        self.l1             = l1
        self.l2             = l2
        self.sub_theme      = sub_theme
        self.review_summary = review_summary
        self.reasoning      = reasoning
        self.warnings       = warnings or []

    def to_dict(self):
        return {
            "l1":             self.l1,
            "l2":             self.l2,
            "sub_theme":      self.sub_theme,
            "review_summary": self.review_summary,
            "reasoning":      self.reasoning,
            "warnings":       self.warnings,
        }

    def is_valid(self):
        return bool(self.l1) and bool(self.l2) and not self.warnings


async def classify(review_text: str, booking: dict, timeline: list,
                    claude_call, review_id: str = None) -> ClassificationResult:
    """
    Runs classification in ONE Claude call, then validates.

    `claude_call` is a callable async fn (prompt: str) -> str.
    Passed in so this module isn't tightly coupled to services/claude.py —
    makes it easy to test with mocks.

    Returns ClassificationResult with warnings list explaining any validation
    failures. On complete failure, l1/l2 remain empty and pipeline continues
    with degraded output.
    """
    result = ClassificationResult()

    try:
        raw = await claude_call(
            prompts.classification_prompt(review_text, booking, timeline)
        )
    except Exception as e:
        log.exception(f"[classify {review_id}] Claude call failed: {e}")
        result.warnings.append(f"Claude call failed: {type(e).__name__}")
        return result

    parsed = _safe_parse(raw)
    if not parsed:
        result.warnings.append("Response was not valid JSON")
        return result

    l1 = (parsed.get("l1") or "").strip()
    l2 = (parsed.get("l2") or "").strip()
    st = parsed.get("sub_theme")
    if isinstance(st, str):
        st = st.strip() or None

    # ── L1 validation ────────────────────────────────────────────────────
    if not is_valid_l1(l1):
        log.warning(f"[classify {review_id}] Invalid L1: {l1!r}")
        result.warnings.append(f"Invalid L1 '{l1}' — dropped to empty")
        # Try to recover: if L2 is a real L2, use its L1
        if l2:
            recovered = _find_l1_for_l2(l2)
            if recovered:
                result.warnings.append(f"Recovered L1 to '{recovered}' from valid L2")
                l1 = recovered
            else:
                l1 = ""
        else:
            l1 = ""

    # ── L2 validation ────────────────────────────────────────────────────
    if l1 and not is_valid_l1_l2(l1, l2):
        log.warning(f"[classify {review_id}] Invalid L2 '{l2}' under L1 '{l1}'")
        result.warnings.append(f"L2 '{l2}' invalid under L1 '{l1}'")
        # Try to recover: if L2 matches ANY L1's options, keep it and correct L1
        recovered_l1 = _find_l1_for_l2(l2)
        if recovered_l1 and recovered_l1 != l1:
            result.warnings.append(f"Recovered L1 to '{recovered_l1}' based on L2 match")
            l1 = recovered_l1
        else:
            l2 = ""

    # ── Sub-theme validation ─────────────────────────────────────────────
    if l1 and l2:
        if has_sub_theme_framework(l1, l2):
            if st is None or st == "":
                result.warnings.append(f"Sub-theme framework exists for ({l1}, {l2}) but was empty")
            elif not is_valid_sub_theme(l1, l2, st):
                log.warning(f"[classify {review_id}] Invalid sub_theme '{st}' for ({l1}, {l2})")
                result.warnings.append(f"Sub-theme '{st}' not in framework for ({l1}, {l2})")
                # Try to salvage by prefix match
                st = _salvage_sub_theme(l1, l2, st)
                if st:
                    result.warnings.append(f"Salvaged sub_theme by code prefix: '{st}'")
        else:
            # No framework → sub_theme should be null
            if st not in (None, "", "N/A", "null"):
                result.warnings.append(f"Sub-theme '{st}' provided but no framework exists for ({l1}, {l2})")
                st = None

    result.l1             = l1
    result.l2             = l2
    result.sub_theme      = st if st else None
    result.review_summary = (parsed.get("review_summary") or "").strip()
    result.reasoning      = (parsed.get("reasoning") or "").strip()
    return result


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────

def _safe_parse(raw: str) -> Optional[dict]:
    """Strip fences + comments + trailing commas, then json.loads."""
    text = (raw or "").strip()
    if not text:
        return None

    text = text.replace("```json", "").replace("```", "").strip()

    # Strip // and /* */ comments outside JSON strings (agent's fix)
    text = _strip_comments_outside_strings(text)
    # Strip trailing commas
    text = re.sub(r',(\s*[}\]])', r'\1', text)

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        log.warning(f"JSON parse failed after cleanup: {e}")
        # Last-ditch: try to find the first {...} block
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return None


def _strip_comments_outside_strings(text: str) -> str:
    """String-aware comment stripper. Preserves URLs and commas inside strings."""
    out = []
    i = 0
    in_string = False
    escape = False
    n = len(text)
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        # Handle // line comment
        if c == '/' and i + 1 < n and text[i+1] == '/':
            while i < n and text[i] != '\n':
                i += 1
            continue
        # Handle /* block comment */
        if c == '/' and i + 1 < n and text[i+1] == '*':
            i += 2
            while i + 1 < n and not (text[i] == '*' and text[i+1] == '/'):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _find_l1_for_l2(l2: str) -> Optional[str]:
    """If Claude gave a valid L2 under the wrong L1, find the right L1."""
    for l1, opts in L2_OPTIONS.items():
        if l2 in opts:
            return l1
    return None


def _salvage_sub_theme(l1: str, l2: str, given: str) -> Optional[str]:
    """
    Sometimes Claude returns 'A' or 'A. Guide No Show!' or 'guide no show'.
    Try to salvage by prefix code (A/B/C) or by fuzzy name match.
    """
    fw = sub_theme_framework(l1, l2)
    if not fw:
        return None
    given_l = given.strip().lower()

    # Try code prefix like "A" or "A." or "A. anything"
    m = re.match(r'^([a-h])[\s\.:]*', given_l)
    if m:
        code = m.group(1).upper()
        for c, name, _ in fw["sub_themes"]:
            if c == code:
                return f"{c}. {name}"
        # Check exclusion label
        if fw["exclusion_label"].lower().startswith(code.lower() + "."):
            return fw["exclusion_label"]

    # Try fuzzy name match
    for c, name, _ in fw["sub_themes"]:
        if name.lower() in given_l or given_l in name.lower():
            return f"{c}. {name}"
    return None
