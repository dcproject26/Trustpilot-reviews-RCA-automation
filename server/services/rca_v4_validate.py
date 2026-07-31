"""Coerce the RCA v4 model output into the shape the dashboard renders.

The frontend renders anything that passes these rules with no special-casing,
so everything that can fail an enum has to be settled here. The alternative is
what v2 of the design exists to remove: a raw token rendered as a pill, a
sentence in a verdict chip, a "[booking]" prefix baked into a sentence.

Called once, on the model's parsed JSON, before it reaches the draft. It never
raises - a malformed field is coerced and recorded in `notes`, because losing
an entire RCA to one bad enum is worse than rendering it with one grey chip.
"""
import re

from server.taxonomy import L1_CATEGORIES, L2_OPTIONS

CLAIM_ACCURACY = ("Accurate", "Partly accurate", "Inaccurate", "Unknown")
ISA_VERDICT    = ("Yes", "No", "Unknown")
SOP_VERDICT    = ("followed", "deviated", "unknown")
SOURCES        = ("booking", "bms", "zendesk", "insights", "dss", "exp-page")
TAKEDOWN       = ("Yes", "No", "Untraceable")
OWNERS         = ("Content", "CE", "SP", "RO", "Product", "Biz", "Ops")
FLAG_TEAMS     = ("CE", "RO", "SP", "CONTENT", "PRODUCT", "BIZ", "TECH", "OTHER")
CHANNELS       = ("chat", "email", "call")

# Where an unclassifiable review lands. Not a guess dressed as a category -
# "Vague review" is the taxonomy's own name for "we could not tell".
CATCH_ALL = ("Miscellaneous Issue", "Vague review")

_NON_VALUE   = re.compile(r"^\s*(unknown|n/?a|tbd|none|null|-|—|\?)\s*$", re.I)
_LEAD_BULLET = re.compile(r"^\s*(?:[•\-–*]\s+|\d+[.)]\s+|[a-z][.)]\s+)")
# Internal names that must never reach a guest.
_INTERNAL    = re.compile(r"\b(selenium|minded|dss|bms|tgid|tid|vid|zd-\d+)\b", re.I)


def _clean(v):
    """A scalar string, or None when it is one of the non-values."""
    if not isinstance(v, str):
        return v
    v = _LEAD_BULLET.sub("", v).strip()
    if not v or _NON_VALUE.match(v):
        return None
    return v


def _obj(v):
    """A dict, whatever arrived. The model occasionally returns a string where
    the template asks for an object, and one bad branch must not cost the
    whole RCA."""
    return v if isinstance(v, dict) else {}


def _enum(value, allowed, fallback):
    """Match an enum case-insensitively; anything else takes the fallback."""
    if isinstance(value, str):
        for a in allowed:
            if value.strip().lower() == a.lower():
                return a
    return fallback


def _accuracy(raw):
    """The four-value verdict, from whatever the model wrote.

    Mapped by prefix rather than exact match because the v3 vocabulary
    ("Yes", "Partially True", "No") is still what older drafts hold, and a
    re-run of an old review should not produce a grey chip for a verdict the
    model actually gave.
    """
    if not isinstance(raw, str):
        return "Unknown", None
    head = re.split(r"\s*[—:–-]\s+", raw.strip(), maxsplit=1)
    first, tail = head[0].strip(), (head[1].strip() if len(head) > 1 else None)
    low = first.lower()
    if low.startswith(("accurate", "yes", "true")):
        return "Accurate", tail
    if low.startswith(("partly", "partial")):
        return "Partly accurate", tail
    if low.startswith(("inaccurate", "no", "false")):
        return "Inaccurate", tail
    return "Unknown", tail or (first or None)


def _evidence_rows(raw):
    """evidence[] as {text, source, ref}. Legacy strings are wrapped."""
    out = []
    for e in (raw if isinstance(raw, list) else []):
        if isinstance(e, str):
            txt = _clean(e)
            # "[booking] the finding" - the old shape. Lift the prefix into
            # source rather than rendering it inside the sentence.
            src = None
            m = re.match(r"^\[([a-z-]+)\]\s*(.+)$", txt or "", re.I)
            if m and m.group(1).lower() in SOURCES:
                src, txt = m.group(1).lower(), _clean(m.group(2))
            if txt:
                out.append({"text": txt, "source": src, "ref": None})
            continue
        if not isinstance(e, dict):
            continue
        txt = _clean(e.get("text"))
        if not txt:
            continue
        # A source or URL written into the sentence is the defect the
        # structured fields exist to remove.
        txt = re.sub(r"^\[[a-z-]+\]\s*", "", txt, flags=re.I).strip()
        out.append({
            "text":   txt,
            "source": _enum(e.get("source"), SOURCES, None),
            "ref":    _clean(e.get("ref")),
        })
    return out


def _issue(raw, notes):
    acc, tail = _accuracy(raw.get("claim_accuracy"))
    if raw.get("claim_accuracy") and acc == "Unknown":
        notes.append(f"claim_accuracy {raw.get('claim_accuracy')!r} → Unknown")
    note = _clean(raw.get("claim_accuracy_note")) or tail
    return {
        "issue":                _clean(raw.get("issue")) or "Untitled issue",
        # Verbatim guest words at whatever length they wrote - never trimmed.
        "claim":                raw.get("claim") if isinstance(raw.get("claim"), str)
                                and raw.get("claim").strip() else None,
        "claim_accuracy":       acc,
        "claim_accuracy_raw":   raw.get("claim_accuracy")
                                if raw.get("claim_accuracy") != acc else None,
        "claim_accuracy_note":  note,
        "owner":                _enum(raw.get("owner"), OWNERS, None),
        "root_cause":           _clean(raw.get("root_cause")),
        "operational_failure":  _clean(raw.get("operational_failure")),
        "sop_gap":              _clean(raw.get("sop_gap")),
        "pattern":              _clean(raw.get("pattern")),
        "fix":                  _clean(raw.get("fix")),
        "evidence":             _evidence_rows(raw.get("evidence")),
    }


def _answers(raw, notes):
    """issue_specific_answers as an array, whichever shape arrived."""
    rows = []
    if isinstance(raw, dict):
        # v3 stored {question: answer}. Keep the answer as evidence rather
        # than trying to read a verdict out of a free sentence.
        for q, a in raw.items():
            v = _enum(a, ISA_VERDICT, None)
            rows.append({"question": q, "verdict": v or "Unknown",
                         "evidence": None if v else _clean(a),
                         "source": None, "ref": None})
        return rows
    for r in (raw if isinstance(raw, list) else []):
        if not isinstance(r, dict):
            continue
        q = _clean(r.get("question"))
        if not q:
            continue
        verdict = _enum(r.get("verdict"), ISA_VERDICT, None)
        evidence = _clean(r.get("evidence"))
        if verdict is None:
            # "28 minutes (…)" is an evidence value, not a verdict. Moving it
            # keeps a raw token off the 82px chip, which is the whole point of
            # the fixed vocabulary.
            spilled = _clean(r.get("verdict"))
            if spilled:
                evidence = f"{spilled} {evidence}".strip() if evidence else spilled
                notes.append(f"ISA verdict {spilled[:40]!r} moved into evidence")
            verdict = "Unknown"
        rows.append({"question": q, "verdict": verdict, "evidence": evidence,
                     "source": _enum(r.get("source"), SOURCES, None),
                     "ref": _clean(r.get("ref"))})
    return rows


def _rows(raw, fields, notes=None, enums=None):
    out = []
    for r in (raw if isinstance(raw, list) else []):
        if not isinstance(r, dict):
            continue
        row = {k: _clean(r.get(k)) for k in fields}
        for k, allowed in (enums or {}).items():
            row[k] = _enum(row.get(k), allowed, None)
        if any(row.values()):
            out.append(row)
    return out


def _taxonomy(rca, notes):
    """l1/l2 against the real taxonomy, with the raw kept when it fails.

    An invented category is not a cosmetic problem: the Slack `Issue:` line and
    every downstream aggregation are built from it, so a plausible-looking
    fabrication is worse than the catch-all. The raw value stays in l1_raw/l2_raw
    so nothing the model actually said is lost.
    """
    l1, l2 = _clean(rca.get("l1")), _clean(rca.get("l2"))
    if l1 in L1_CATEGORIES and l2 in L2_OPTIONS.get(l1, []):
        return {"l1": l1, "l2": l2, "l1_raw": None, "l2_raw": None}
    notes.append(f"classification {l1!r}/{l2!r} is not in the taxonomy → "
                 f"{CATCH_ALL[0]}/{CATCH_ALL[1]}; needs human review")
    return {"l1": CATCH_ALL[0], "l2": CATCH_ALL[1], "l1_raw": l1, "l2_raw": l2}


def validate(rca: dict, scenarios_routed=None) -> tuple[dict, list]:
    """Return (coerced rca, notes). Never raises."""
    notes: list[str] = []
    if not isinstance(rca, dict):
        return {}, ["model returned no JSON object"]

    wwr = _obj(rca.get("what_went_wrong"))
    _gi = wwr.get("guest_issues")
    issues = [_issue(i, notes) for i in (_gi if isinstance(_gi, list) else [])
              if isinstance(i, dict)]

    scenarios = [s for s in (rca.get("scenarios") if isinstance(rca.get("scenarios"), list) else []) if _clean(s)]
    overlays = [s for s in (rca.get("overlay_scenarios") if isinstance(rca.get("overlay_scenarios"), list) else [])
                if _clean(s) and s not in scenarios]
    if len(overlays) != len([s for s in (rca.get("overlay_scenarios") if isinstance(rca.get("overlay_scenarios"), list) else []) if _clean(s)]):
        notes.append("overlay_scenarios overlapped scenarios; duplicates dropped")

    # A routed scenario with no covering issue is a visible gap, not a silent
    # drop: the chip still renders in Classification with no issue behind it.
    covered = " ".join((i.get("issue") or "") + " " + (i.get("root_cause") or "")
                       for i in issues).lower()
    for s in (scenarios_routed or []):
        if s and s.lower() not in covered and s not in scenarios:
            notes.append(f"routed scenario not covered by any guest issue: {s}")

    reply = rca.get("suggested_response")
    if isinstance(reply, str) and _INTERNAL.search(reply):
        notes.append("suggested_response contains an internal name or id — "
                     "needs human review before send")

    sop = _obj(rca.get("sop_compliance"))
    dss = _obj(rca.get("dss"))
    sp  = _obj(rca.get("sp_interaction"))

    return {
        "stated_issue":      _clean(rca.get("stated_issue")),
        "tldr": {
            "our_mistake": _clean(_obj(rca.get("tldr")).get("our_mistake")),
            "our_fix":     _clean(_obj(rca.get("tldr")).get("our_fix")),
        },
        **_taxonomy(rca, notes),
        "sub_themes":        [s for s in (rca.get("sub_themes") if isinstance(rca.get("sub_themes"), list) else []) if _clean(s)],
        "scenarios":         scenarios,
        "overlay_scenarios": overlays,
        "what_went_wrong":   {"guest_issues": issues},
        "issue_specific_answers": _answers(rca.get("issue_specific_answers"), notes),
        "sop_compliance": {
            "verdict":  _enum(sop.get("verdict"), SOP_VERDICT, "unknown"),
            "expected": _clean(sop.get("expected")),
            "actual":   _clean(sop.get("actual")),
            "detail":   _clean(sop.get("detail")),
            "zd_ref":   _clean(sop.get("zd_ref")),
        },
        # A "contact" whose only content is that no contact exists must not be
        # dressed as a data row - it renders as a numbered frame with an
        # UNKNOWN channel pill. The empty state says it better.
        "support_interaction": [
            r for r in _rows(rca.get("support_interaction"),
                             ("channel", "time", "summary", "detail",
                              "zd_ref", "ce_miss"),
                             enums={"channel": CHANNELS})
            if not re.search(r"\bno (guest )?contact\b|never (reached|contacted)",
                             (r.get("summary") or ""), re.I)
        ],
        "sp_interaction": {
            "raised":  _enum(sp.get("raised"), ("Yes", "No", "N/A"), "N/A"),
            "records": _rows(sp.get("records"), ("time", "summary", "zd_ref")),
        },
        "booking_logs":      _rows(rca.get("booking_logs"), ("time", "what", "detail")),
        "flags":             _rows(rca.get("flags"), ("team", "flag", "evidence", "zd_ref"),
                                   enums={"team": FLAG_TEAMS}),
        "area_of_improving": [c for c in (_clean(x) for x in
                                          (rca.get("area_of_improving") if isinstance(rca.get("area_of_improving"), list) else [])) if c],
        "resolution":         _clean(rca.get("resolution")),
        "suggested_response": _clean(reply),
        "takedown": {"verdict": _enum(_obj(rca.get("takedown")).get("verdict"),
                                      TAKEDOWN, "Untraceable")},
        "dss": {"prescribes": _clean(dss.get("prescribes")),
                "ref":        _clean(dss.get("ref"))},
    }, notes
