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


def zd_key(v) -> str:
    """The digits of a ticket reference, whichever side wrote it.

    The pipeline's frames carry ticket_id "4491"; the model writes zd_ref
    "ZD-4491". Joining on the raw strings matches nothing, and a join that
    silently matches nothing is indistinguishable from a model that returned no
    notes - which is why every caller of this also has to report a miss.

    Lives here rather than in the renderer because the pipeline counts failed
    joins and Slack performs them, and two copies of a join key drift.
    """
    m = re.search(r"\d+", str(v or ""))
    return m.group(0) if m else ""


# The queryable columns and where each one reads from inside rca_v3. One list,
# because two write paths - the pipeline and regenerate-rca - were maintaining
# separate copies of this projection, and one of them fell behind.
V4_PROJECTION = {
    "guest_issues":           ("what_went_wrong", "guest_issues"),
    "booking_logs":           ("booking_logs",),
    "flags":                  ("flags",),
    "takedown":               ("takedown",),
    "dss":                    ("dss",),
    "issue_specific_answers": ("issue_specific_answers",),
}
_V4_EMPTY = {"guest_issues": list, "booking_logs": list,
             "flags": list, "takedown": dict, "dss": dict,
             "issue_specific_answers": list}


def project_v4(rca) -> dict:
    """The column values for one RCA: {column: value}.

    Pure, so the persist paths can be checked by driving this rather than by
    asserting that `draft.flags =` appears somewhere in pipeline.py. A source
    assertion of that shape is a spelling check - it passes just as happily
    against a build where the line it names is unreachable, which is how two
    guarantees in this very file turned out to be guarding nothing.

    resolution and suggested_response are deliberately absent: they are scalar
    columns with their own fallbacks at the call site, not projections.
    """
    rca = rca if isinstance(rca, dict) else {}
    out = {}
    for col, path in V4_PROJECTION.items():
        node = rca
        for part in path:
            node = node.get(part) if isinstance(node, dict) else None
        out[col] = node if node not in (None, "") else _V4_EMPTY[col]()
    return out


def contact_join_notes(support_frames, sp_frames, rca) -> list:
    """What the contact-note join could not do, as trail lines.

    Two different facts, deliberately not one message. A note carrying a
    zd_ref that matches no frame is a FAILED JOIN - the reference is wrong on
    one side and the reader should know the row is floating. A note with no
    zd_ref at all is a deliberate off-Zendesk contact, which is the model doing
    what rule 11 asks. Merging them would make a working run look faulty.

    Returns [] when everything joined, which is the only case that should be
    silent: a join that matches nothing looks exactly like a model that
    returned no notes, and that is the failure this exists to make visible.
    """
    keys = {zd_key(f.get("ticket_id"))
            for f in list(support_frames or []) + list(sp_frames or [])
            if isinstance(f, dict)}
    keys.discard("")

    rca = rca if isinstance(rca, dict) else {}
    notes = [n for n in (rca.get("support_interaction_notes") or [])
             if isinstance(n, dict)]
    notes += [r for r in ((rca.get("sp_interaction_notes") or {}).get("records") or [])
              if isinstance(r, dict)]

    unmatched = [n for n in notes if zd_key(n.get("zd_ref")) and
                 zd_key(n.get("zd_ref")) not in keys]
    off_zd = [n for n in notes if not zd_key(n.get("zd_ref"))]

    out = []
    if unmatched:
        refs = ", ".join(sorted({str(n.get("zd_ref")) for n in unmatched}))
        out.append(f"{len(unmatched)} model note(s) could not be joined to a "
                   f"Zendesk frame ({refs}) — rendered as unmatched, not dropped")
    if off_zd:
        out.append(f"{len(off_zd)} contact(s) reported with no Zendesk ticket — "
                   f"rendered as the guest's account, unverified")
    # Grouping frames without a ticket id by time is a judgement, not a fact.
    # It is a good default, but the reader is entitled to know one was made -
    # an unannounced guess is how a guessed number becomes a trusted one.
    untracked = [f for f in list(support_frames or []) + list(sp_frames or [])
                 if isinstance(f, dict) and not zd_key(f.get("ticket_id"))]
    if len(untracked) > 1:
        out.append(f"{len(untracked)} event(s) have no ticket id and were grouped "
                   f"into contacts by a 30-minute window")
    return out


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
    # Only when the value actually CHANGED. "Unknown" is a legitimate verdict,
    # and reporting "claim_accuracy 'Unknown' → Unknown" as a coercion puts a
    # warn on the trail for a model that did exactly what it was asked. That is
    # the inverse of the silent-failure bug and costs the same thing: a reader
    # who stops believing the trail.
    # Case-insensitive, for the same reason team_raw is: "accurate" matching
    # Accurate is a normalisation, not a change of meaning, and reporting it
    # would put a line on the trail for every well-formed RCA.
    _given = raw.get("claim_accuracy")
    if isinstance(_given, str) and _given.strip() and _given.strip().lower() != acc.lower():
        notes.append(f"claim_accuracy {_given!r} → {acc}")
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


_STOP = {"the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "was",
         "were", "is", "are", "not", "with", "as", "that", "this", "by", "at",
         "per", "no", "without", "after", "before", "its", "it"}


def _tokens(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in _STOP}


def _overlaps(a, b, ratio=0.6):
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta) and len(ta & tb) >= ratio * len(ta)


def _demote_findings(issues, flags, notes, routed=None):
    """Move our own process findings out of guest_issues and into flags.

    A run returned "Out-of-policy refund issued after booking was non-refundable"
    as numbered guest issue 04. The guest never raised it - it renders as a
    complaint with an empty Claim block, and leadership reads it as something
    the guest said. The prompt now forbids it, but the signature is
    deterministic enough to settle here rather than trusting adherence:
    no claim, no owner, no operational_failure is a finding about us, not a
    complaint from them. Three nulls, no semantic matching needed.

    It becomes a flag unless an existing flag already says the same thing, in
    which case it is dropped - the model tends to raise the same finding twice,
    once in each section. Either way the move is reported: silently rewriting
    what the model returned is the thing the trail exists to prevent.

    Rule 13 coverage rows are exempt. A routed scenario the data does not
    support is REQUIRED to come back as a claim-less issue, and it will often
    have no owner and no operational failure either - the same three nulls for
    the opposite reason. Demoting one would delete the audit trail for a
    scenario the router asked about.
    """
    _routed = [str(r).lower() for r in (routed or []) if r]
    kept, moved = [], []
    for i in issues:
        if (i.get("claim") or i.get("owner") or i.get("operational_failure")):
            kept.append(i)
            continue
        _text = f"{i.get('issue') or ''} {i.get('root_cause') or ''}".lower()
        if any(r in _text or _overlaps(r, _text) for r in _routed):
            kept.append(i)
            continue
        moved.append(i)
    if not moved:
        return issues, flags

    flags = list(flags or [])
    for i in moved:
        title = i.get("issue") or ""
        t = _tokens(title) | _tokens(i.get("root_cause"))
        dupe = next((f for f in flags
                     if t and len(t & (_tokens(f.get("flag")) | _tokens(f.get("evidence"))))
                     >= 0.6 * len(t)), None)
        if dupe:
            notes.append(f"guest issue {title!r} had no claim, owner or operational "
                         f"failure and duplicated an existing flag — dropped")
            continue
        ev = (i.get("evidence") or [{}])[0]
        flags.append({
            "team": "OTHER",
            "flag": title,
            "evidence": i.get("root_cause") or (ev.get("text") if isinstance(ev, dict) else None),
            "zd_ref": ev.get("ref") if isinstance(ev, dict) else None,
            "team_raw": None,
        })
        notes.append(f"guest issue {title!r} had no claim, owner or operational "
                     f"failure — it is our finding, not the guest's, and was "
                     f"moved to flags")
    return kept, flags


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
    """Rows with their enum columns closed, and the raw kept when one fails.

    `enums` maps column -> (allowed, fallback). The raw value goes to
    `<column>_raw` so nothing the model said is lost: the UI renders the
    closed value, and the raw is there for whoever asks why.
    """
    out = []
    for r in (raw if isinstance(raw, list) else []):
        if not isinstance(r, dict):
            continue
        row = {k: _clean(r.get(k)) for k in fields}
        for k, (allowed, fallback) in (enums or {}).items():
            given = row.get(k)
            matched = _enum(given, allowed, None)
            row[k] = matched if matched is not None else fallback
            # The raw is kept only when the value genuinely failed the
            # vocabulary. "ce" matching CE is a case fix, not a failure -
            # keeping a raw for that would put noise on every row and make
            # team_raw useless as a signal.
            missed = given and matched is None
            row[f"{k}_raw"] = given if missed else None
            if missed and notes is not None:
                notes.append(f"{k} {given!r} → {row[k] if row[k] else 'null'}")
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

    # The ceilings are instructions, and the model overshot the reply by 35%
    # on a real run. Not truncated - cutting a guest-facing apology mid-sentence
    # is worse than a long one - but counted and said, so "too long" is a fact
    # on the trail rather than something a reader has to notice.
    for _f, _cap in (("suggested_response", 120), ("stated_issue", 60)):
        _v = rca.get(_f)
        if isinstance(_v, str):
            _w = len(_v.split())
            if _w > _cap:
                notes.append(f"{_f} is {_w} words, over the {_cap}-word ceiling — "
                             f"trim before it goes out")

    dss = _obj(rca.get("dss"))
    # Accept the pre-split key so a draft written before this change, and a
    # model still reaching for the old name, both keep their interpretation.
    sp  = _obj(rca.get("sp_interaction_notes") or rca.get("sp_interaction"))
    contacts = (rca.get("support_interaction_notes")
                if isinstance(rca.get("support_interaction_notes"), list)
                else rca.get("support_interaction"))

    # Findings about us, wrongly filed as complaints from the guest, move to
    # flags before anything downstream counts them as guest issues.
    _flags = _rows(rca.get("flags"), ("team", "flag", "evidence", "zd_ref"),
                   notes, enums={"team": (FLAG_TEAMS, "OTHER")})
    issues, _flags = _demote_findings(issues, _flags, notes, scenarios_routed)

    return {
        "stated_issue":      _clean(rca.get("stated_issue")),
        **_taxonomy(rca, notes),
        "sub_themes":        [s for s in (rca.get("sub_themes") if isinstance(rca.get("sub_themes"), list) else []) if _clean(s)],
        "scenarios":         scenarios,
        "overlay_scenarios": overlays,
        "what_went_wrong":   {"guest_issues": issues},
        "issue_specific_answers": _answers(rca.get("issue_specific_answers"), notes),
        # INTERPRETATION, not facts. The rows the UI renders come from the
        # pipeline's Zendesk-derived frames - their time, channel and ticket id
        # are verifiable. What the model adds is summary / detail / ce_miss,
        # joined to a contact by zd_ref. Keeping them under a distinct key is
        # what stops presence-based reading reversing that precedence: these
        # can never collide with the frames because they are not the same key.
        #
        # A "contact" whose only content is that no contact exists must not be
        # dressed as a data row - it renders as a numbered frame with an
        # UNKNOWN channel pill. The empty state says it better.
        "support_interaction_notes": [
            # channel falls back to null, not to a token: the UI then renders
            # no pill at all. "UNKNOWN" dressed as a channel pill is one of the
            # defects the closed vocabulary exists to remove.
            # No channel or time: those are facts, they live on the frames,
            # and a field the model must not fill is a field it will fill. On a
            # real run both came back null while the prose said "chat at 15:41"
            # - the schema invited the mistake, so the schema is the fix.
            r for r in _rows(contacts,
                             ("zd_ref", "summary", "detail", "ce_miss"), notes)
            if not re.search(r"\bno (guest )?contact\b|never (reached|contacted)",
                             (r.get("summary") or ""), re.I)
        ],
        "sp_interaction_notes": {
            "raised":  _enum(sp.get("raised"), ("Yes", "No", "N/A"), "N/A"),
            # "N/A" with no records and no reason is indistinguishable from a
            # section the model skipped. The reason is what makes it an answer.
            "reason":  _clean(sp.get("reason")),
            "records": _rows(sp.get("records"), ("zd_ref", "summary")),
        },
        "booking_logs":      _rows(rca.get("booking_logs"), ("time", "what", "detail")),
        # team falls back to OTHER, not null: the UI renders it as a
        # chip-select over the closed list, and a null would either blank the
        # control or add a stray option to it. OTHER is a real member.
        "flags":             _flags,
        "area_of_improving": [c for c in (_clean(x) for x in
                                          (rca.get("area_of_improving") if isinstance(rca.get("area_of_improving"), list) else [])) if c],
        "resolution":         _clean(rca.get("resolution")),
        "suggested_response": _clean(reply),
        "takedown": {"verdict": _enum(_obj(rca.get("takedown")).get("verdict"),
                                      TAKEDOWN, "Untraceable")},
        "dss": {"prescribes": _clean(dss.get("prescribes")),
                "ref":        _clean(dss.get("ref"))},
    }, notes
