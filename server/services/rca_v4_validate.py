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

# MODULE LEVEL, and never re-imported inside a function. A function-local
# import of a module-level name shadows that name for the WHOLE function and
# raises UnboundLocalError on every earlier use — which is precisely how the
# Zendesk lookup went dark for two reviews, swallowed by an except.
from server import dss_check, price_check
from server.checklist import (ACTION_TEAMS, FLAG_TEAM_ALIASES, ACTION_TABS,
                              actions_raised, findings_text,
                              actions_from_fixes)
from server.taxonomy import L1_CATEGORIES, L2_OPTIONS

# Four. "Unknown" carries both "we checked and nothing can settle it" and "we
# did not check" — the difference lives in `claim_accuracy_note`, which the
# spec requires on every verdict. A fifth enum value was tried and removed:
# the note already has to say WHICH, so the verdict does not also need to.
CLAIM_ACCURACY = ("Accurate", "Partly accurate", "Inaccurate", "Unknown")
ISA_VERDICT    = ("Yes", "No", "Unknown")
SOURCES        = ("booking", "bms", "zendesk", "insights", "dss", "exp-page")

# WHAT AN EVIDENCE ROW MAY CITE — the same list WITHOUT `dss`.
#
# A what-went-wrong evidence row saying `dss — "DSS matched row is for 'Tour
# started late'; no row covers a system-initiated vendor reassignment"` reached
# a card. That is not evidence about the booking; it is a remark about our own
# decision sheet's coverage, and it appears in the reader's evidence list
# beside records of what actually happened.
#
# NARROWED DELIBERATELY. `dss` stays in SOURCES because it is still valid for
# `fix.source` — which records where a gap was READ and never renders — and for
# issue_specific_answers. DSS is still what you CHECK a gap against; it is no
# longer something the reader is shown as evidence.
EVIDENCE_SOURCES = tuple(s for s in SOURCES if s != "dss")
TAKEDOWN       = ("Yes", "No", "Untraceable")
# `fix.owner` names the team that closes the gap, so it has to be the SAME
# nine as Flags and Actions Taken. It was a THIRD vocabulary - Content, CE,
# SP, RO, Product, Biz, Ops - which meant a fix could name "RO" or "Ops",
# neither of which is a chip on the card: the reader is told who owns the fix
# and then cannot find them anywhere. Two of those seven had already been
# retired from the other two lists.
#
# Upper-case, like FLAG_TEAMS, because they are read together on the card and
# two spellings of one team is the defect this whole vocabulary exists to
# prevent. Legacy values are TRANSLATED through the same aliases as flags,
# not failed - a draft written under the old list names a real team, and
# dropping it to null would lose an owner the model correctly identified.
OWNERS         = tuple(t.upper() for t in ACTION_TEAMS)
# The nine teams, and nothing else. Flags and Actions Taken share one
# vocabulary because they are JOINED on it: a guideline action is raised only
# where a flag names the same team, and two spellings of one team would make
# that join match nothing — which looks exactly like a card with nothing to
# raise. OTHER is not a tenth team. It is the marker for a flag whose team
# could not be read, and it raises nothing; the model's word stays in team_raw.
FLAG_TEAMS     = tuple(t.upper() for t in ACTION_TEAMS) + ("OTHER",)
# The channels a GUEST can reach us on, and the exact vocabulary the frames
# already normalise to (server/services/zendesk.py::_map_channel), so a pill
# reads the same whether the frame supplied it or the model did. "api" is in
# that map and deliberately not here: it is machinery, and a contact row is for
# exchanges a person took part in.
CONTACT_CHANNELS = ("chat", "email", "call", "web", "app")

# One contact: when, what type, and a SUMMARY of the interaction. The card
# shows the timestamp and a channel pill on the head row, and the summary
# underneath - so `summary` carries the account and there are no separate
# columns for what the guest said, what we said, or whether it was raised.
# Those are things the summary has to COVER, stated in the prompt.
#
# `time` and `channel` are here because a contact with no Zendesk frame has no
# frame to take them from, and striking them rendered a dash - the same dash a
# broken lookup renders. Where a frame exists, the frame wins.
CONTACT_FIELDS = ("zd_ref", "summary", "detail", "ce_miss", "time", "channel")

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
    # Actions Taken is projected like any other v4 section. It is NOT model
    # output: `validate` computes it from the routed scenarios (the DSS
    # guidelines) and the flags (what actually went wrong), which are the two
    # halves of the rule, and both are in hand exactly here. Projecting it the
    # same way every other section is projected is what keeps the pipeline and
    # regenerate-rca from each reaching a different answer.
    "actions_taken":          ("actions_taken",),
}
_V4_EMPTY = {"guest_issues": list, "booking_logs": list,
             "flags": list, "takedown": dict, "dss": dict,
             "issue_specific_answers": list, "actions_taken": dict}


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
    """The verdict, from whatever the model wrote.

    Mapped by prefix rather than exact match because the v3 vocabulary
    ("Yes", "Partially True", "No") is still what older drafts hold, and a
    re-run of an old review should not produce a grey chip for a verdict the
    model actually gave.

    Anything unrecognised falls to Unknown. The note is where the reader
    learns whether that means "checked, unsettleable" or "not checked".
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
    # BEFORE the Inaccurate branch, not after. "No record of this" and "Not
    # verifiable" both start with "no", so the generic prefix claimed them and
    # reported "we could not check" as "the guest is wrong" — the worst of the
    # three possible readings, because it contradicts a guest on no evidence.
    # "No record of this" and "Not verifiable" both start with "no", and both
    # mean we could not settle it — NOT that the guest is wrong. Caught before
    # the Inaccurate prefix, which would otherwise contradict a guest on no
    # evidence at all. They land on Unknown; the note says which kind.
    if low.startswith(("unverifiable", "not verifiable", "cannot verify",
                       "can't verify", "no record", "unconfirmable")):
        return "Unknown", tail
    if low.startswith(("inaccurate", "no", "false")):
        return "Inaccurate", tail
    return "Unknown", tail or (first or None)


def _evidence_rows(raw, notes=None):
    """evidence[] as {text, source, ref, backs_claim}.

    `backs_claim` is Yes / No / null, and NULL IS A REAL ANSWER: the entry is
    not about the claim at all — it establishes mechanism or sizes a pattern.
    Anything unrecognised lands on null rather than No, because a wrong No
    reads as settled and contradicts a guest on evidence that was never about
    them. Legacy strings are wrapped and get null.

    `dss` is NOT a valid evidence source. A row citing the decision sheet's
    own coverage is a remark about our paperwork, not a record of what
    happened to this booking, and it was appearing in the reader's evidence
    list beside records that are. A row that cites it keeps its TEXT and loses
    its source — dropping the row would delete a sentence the model wrote, and
    silently relabelling it would attribute the finding to a system it did not
    come from. The demotion is COUNTED and reported, because a source that
    quietly became null looks exactly like one the model never supplied.
    """
    notes = notes if notes is not None else []
    demoted = 0
    out = []
    for e in (raw if isinstance(raw, list) else []):
        if isinstance(e, str):
            txt = _clean(e)
            # "[booking] the finding" - the old shape. Lift the prefix into
            # source rather than rendering it inside the sentence.
            src = None
            m = re.match(r"^\[([a-z-]+)\]\s*(.+)$", txt or "", re.I)
            if m and m.group(1).lower() in SOURCES:
                # Matched against SOURCES, not EVIDENCE_SOURCES, so a legacy
                # "[dss] ..." prefix is still RECOGNISED and stripped. Leaving
                # it unmatched would render the bracket inline, which is the
                # defect the structured fields exist to remove.
                src, txt = m.group(1).lower(), _clean(m.group(2))
                if src not in EVIDENCE_SOURCES:
                    src = None
                    demoted += 1
            if txt:
                out.append({"text": txt, "source": src, "ref": None,
                            "backs_claim": None, "time": None})
            continue
        if not isinstance(e, dict):
            continue
        txt = _clean(e.get("text"))
        if not txt:
            continue
        # A source or URL written into the sentence is the defect the
        # structured fields exist to remove.
        txt = re.sub(r"^\[[a-z-]+\]\s*", "", txt, flags=re.I).strip()
        if str(e.get("source") or "").strip().lower() == "dss":
            demoted += 1
        out.append({
            "text":   txt,
            "source": _enum(e.get("source"), EVIDENCE_SOURCES, None),
            "ref":    _clean(e.get("ref")),
            "backs_claim": _enum(e.get("backs_claim"), ("Yes", "No"), None),
            # CARRIED, because §1 orders by event and these rows are §1's
            # content. Dropping it here left every merged finding undated, so
            # the section fell back to write-order and the chronology the
            # records DO support was thrown away in validation.
            "time":   _clean(e.get("time")),
        })
    if demoted:
        notes.append(
            f"{demoted} evidence row(s) cited the DSS sheet as their source — "
            f"the text is kept, the source is dropped. DSS is what a gap is "
            f"CHECKED against, not a record of what happened to this booking")
    return out


# `fix` is an object now: what to do, who does it, the gap it closes, where
# that gap was read, and the count that justifies it. It used to be one string
# beside a separate `owner`, so nothing tied the action to the evidence it came
# from — and an invented fix reads exactly like a derived one.
FIX_FIELDS = ("action", "owner", "because", "source")


def _fix_obj(raw, notes):
    """The fix, or None. A fix with no action is not a fix."""
    if isinstance(raw, str):
        # Pre-object drafts hold a bare string. Keep the words rather than drop
        # them; the missing halves stay null and read as missing, not as absent
        # on purpose.
        raw = {"action": raw}
    if not isinstance(raw, dict):
        return None
    out = {k: _clean(raw.get(k)) for k in FIX_FIELDS}
    if not out["action"]:
        return None
    # Same aliases as flags, and for the same reason: a draft written under the
    # old vocabulary names a REAL team, and failing it to null would lose an
    # owner the model got right. Translated, then reported - we changed what
    # the model said.
    _own_raw = str(out["owner"] or "").strip()
    _alias = FLAG_TEAM_ALIASES.get(_own_raw.lower())
    if _alias:
        notes.append(f"fix.owner {_own_raw!r} → {_alias.upper()} "
                     f"({ACTION_TABS[_alias]['label']})")
        out["owner"] = _alias.upper()
    out["owner"] = _enum(out["owner"], OWNERS, None)
    if raw.get("owner") and not out["owner"]:
        notes.append(f"fix.owner {raw.get('owner')!r} is not one of the nine "
                     f"teams → null (it would name an owner with no chip)")
    out["source"] = _enum(out["source"], SOURCES, None)
    return out



# "No DSS path governs a system-initiated vendor reassignment" is a remark
# about our own documentation. The reader of `sop_gap` owns an operation, not
# a spreadsheet: what they need is what nobody was required to DO.
_DSS_WORDED = re.compile(
    r"\b(dss|decision sheet)\b|\bno (?:such )?(?:row|path|needle)\b", re.I)


def _flag_dss_wording(issue, notes):
    """Say when a finding was written ABOUT the DSS instead of about the case.

    DSS is a LOOKUP THAT INFORMS THE ANSWER, never a subject the answer talks
    about. The model consults it to work out what the correct next escalation
    step would have been, and then writes THAT STEP. It must not be named in
    root_cause, operational_failure, sop_gap, fix or any evidence row — and if
    the sheet has no row for the scenario, the model reasons the next step from
    the playbook it does have rather than reporting the absence as the finding.
    The absence of a DSS row is an internal fact about our tooling; the guest's
    case is about what should have happened and did not.

    REPORTED, NOT REWRITTEN. The sentence is the model's analysis and there is
    no mechanical way to restate it correctly — deleting it would lose a real
    finding, and paraphrasing it would put words in the model's mouth. So the
    text stands and the trail says the field was written about the sheet
    rather than about the process, which is a thing a reader can act on.

    It has to be able to say it found nothing, and it does: no note at all
    when the wording is clean, which is the ordinary case.
    """
    fix = issue.get("fix") if isinstance(issue.get("fix"), dict) else {}
    fields = [("root_cause", issue.get("root_cause")),
              ("operational_failure", issue.get("operational_failure")),
              ("sop_gap", issue.get("sop_gap")),
              ("fix.action", fix.get("action")),
              ("fix.because", fix.get("because"))]
    fields += [(f"evidence[{i}]", e.get("text"))
               for i, e in enumerate(issue.get("evidence") or [])
               if isinstance(e, dict)]
    for field, value in fields:
        if value and _DSS_WORDED.search(str(value)):
            notes.append(
                f"{field} names the DSS or its coverage rather than the step "
                f"that should have been taken — kept as written, but DSS is "
                f"what you look the next escalation step UP in, not something "
                f"the finding talks about: {str(value)[:80]!r}")


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

    # THE VERDICT JUDGES THE REVIEW'S CLAIM. IT DOES NOT DECIDE WHETHER THERE
    # IS A DIAGNOSIS.
    #
    # This used to clear root_cause, operational_failure, sop_gap and fix on
    # any Inaccurate or Unknown verdict. The reasoning was sound about one
    # case — a root cause under a claim that does not hold is the shape of
    # thoroughness with nothing behind it — and wrong about another, which is
    # the one that reached a real card: a guest asked to move their booking,
    # was refused, and wrote a review about "strict policy". The public claim
    # scored Inaccurate, and the modification request they actually made was
    # deleted along with everything else. The card said the guest was wrong
    # and showed nothing.
    #
    # So the test is no longer the verdict alone. A diagnosis survives when
    # the ZENDESK CASE shows something happened — `case_side` — whatever the
    # review's claim turned out to be. With no case and a claim that does not
    # hold, there genuinely is nothing to diagnose and the fields still clear.
    _case_side = _clean(raw.get("case_side"))
    _diagnosable = acc in ("Accurate", "Partly accurate") or bool(_case_side)
    if not _diagnosable:
        for _f in ("root_cause", "operational_failure", "sop_gap", "fix"):
            if _clean(raw.get(_f)) or isinstance(raw.get(_f), dict):
                notes.append(f"{_f} dropped — {acc}, and the Zendesk case "
                             f"shows nothing on this issue, so there is "
                             f"nothing to diagnose")
                break
    elif acc not in ("Accurate", "Partly accurate"):
        # KEPT ON A CLAIM THAT DID NOT HOLD, which is a judgement worth
        # announcing: the reader is being shown a diagnosis beside a verdict
        # that says the guest was wrong, and the reason those coexist is the
        # case, not the review.
        notes.append(f"claim_accuracy is {acc} but the Zendesk case shows "
                     f"something happened, so the diagnosis is kept — the "
                     f"review's claim and the booking's problem are different "
                     f"questions")

    out = {
        "issue":                _clean(raw.get("issue")) or "Untitled issue",
        # THE TWO SIDES. Either may be null, and null is a finding rather than
        # a gap: no review_side means the case surfaced something the guest
        # never wrote about; no case_side means they never contacted support
        # and the review IS the case.
        "review_side":          _clean(raw.get("review_side")),
        "case_side":            _case_side,
        # Verbatim guest words at whatever length they wrote - never trimmed.
        "claim":                raw.get("claim") if isinstance(raw.get("claim"), str)
                                and raw.get("claim").strip() else None,
        "claim_accuracy":       acc,
        "claim_accuracy_raw":   raw.get("claim_accuracy")
                                if raw.get("claim_accuracy") != acc else None,
        "claim_accuracy_note":  note,
        # Owner lives on the fix now, not on the issue. It was in both places
        # and nothing reconciled them: a model judgement about who owns the
        # ISSUE beside a keyword rule about who owns each ACTION, free to
        # disagree on the same row with no way to tell which was meant.
        "root_cause":           _clean(raw.get("root_cause")) if _diagnosable else None,
        "operational_failure":  _clean(raw.get("operational_failure")) if _diagnosable else None,
        "sop_gap":              _clean(raw.get("sop_gap")) if _diagnosable else None,
        "pattern":              _clean(raw.get("pattern")),
        "fix":                  _fix_obj(raw.get("fix"), notes) if _diagnosable else None,
        "evidence":             _evidence_rows(raw.get("evidence"), notes),
    }
    _flag_dss_wording(out, notes)
    return out


_STOP = {"the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "was",
         "were", "is", "are", "not", "with", "as", "that", "this", "by", "at",
         "per", "no", "without", "after", "before", "its", "it"}


def _tokens(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in _STOP}


def _overlaps(a, b, ratio=0.6):
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta) and len(ta & tb) >= ratio * len(ta)


def _gate_amount_claims(issues, booking, events, notes):
    """Demote a money verdict the Zendesk case does not actually support.

    Reads the ticket bodies off `events` — `raw_body` is the Zendesk comment
    text, which is where a per-person amount is stated when it is stated at
    all. Falls back to `summary` so a build that only kept summaries still gets
    a look rather than silently finding nothing.

    Every change lands in `notes`, so the trail carries it as a warn: this
    rewrites a verdict the model returned, and a silent rewrite is the thing
    the trail exists to prevent.
    """
    texts = [str((e or {}).get("raw_body") or (e or {}).get("summary") or "")
             for e in (events or []) if isinstance(e, dict)]
    for i in issues:
        got = price_check.gate_amount_claim(
            i.get("claim"), i.get("claim_accuracy"), booking, texts)
        if not got:
            continue
        new_acc, note = got
        i["claim_accuracy"] = new_acc
        notes.append(note)


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
        # Owner moved onto the fix. Reading the old top-level key here made
        # EVERY issue look ownerless, so every one was demoted to flags and the
        # guest-issues list came back empty — the section silently emptied by a
        # field that had moved, not by anything the model did.
        _owner = (i.get("fix") or {}).get("owner") if isinstance(i.get("fix"), dict) \
            else i.get("owner")
        # `case_side` KEEPS AN ISSUE HERE. A problem the Zendesk case shows
        # and the review never mentioned has no claim by definition — the
        # guest did not write about it publicly — and demoting it to flags is
        # exactly how the modification request in the Bhayani case vanished
        # from the RCA. It is a thing that happened to this guest, so it is a
        # guest issue; our failure to act on it, if there was one, is the
        # flag beside it.
        if (i.get("claim") or i.get("case_side") or _owner
                or i.get("operational_failure")):
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


# Where an improvement point is allowed to come from, and the field on the card
# that has to carry it. Nothing else is a source: a policy someone would prefer
# and a pattern across other bookings are opinions, and this section is read as
# the correction to a documented gap.
AOI_SOURCES = ("operational_failure", "sop_gap", "flag")


def _improvements(raw, issues, flags, notes):
    """Area of improvement: one pointer per line, each tied to what it came from.

    PROVENANCE IS A CONSTRAINT ON THE MODEL, not decoration. The section used to
    weld five recommendations into one paragraph, half of it material that
    appeared in no finding on the card. Requiring each point to name the
    operational failure, SOP gap or flag it derives from is what makes an
    invented point impossible to write: there is nowhere to put a source it does
    not have.

    So it is CHECKED here, not trusted. A point whose stated source matches
    nothing on this card is dropped before it renders — the same way `fix` is
    null when no evidence entry shows a gap — and the drop is counted and named,
    because a section that silently shrank is indistinguishable from a model
    that had less to say.
    """
    rows = raw if isinstance(raw, list) else ([raw] if raw else [])
    # What this card can actually support, by kind.
    have = {
        "operational_failure": [i.get("operational_failure") for i in issues],
        "sop_gap":             [i.get("sop_gap") for i in issues],
        "flag":                [f"{f.get('flag') or ''} {f.get('evidence') or ''}"
                                for f in (flags or [])],
    }
    have = {k: [t for t in v if _clean(t)] for k, v in have.items()}

    out, unsourced, unmatched = [], 0, 0
    for r in rows:
        if isinstance(r, str):
            # The pre-provenance shape. Keeping it would be keeping exactly the
            # points this rule exists to remove, since a bare string is a point
            # with no derivation at all.
            if _clean(r):
                unsourced += 1
            continue
        if not isinstance(r, dict):
            continue
        point = _clean(r.get("point")) or _clean(r.get("text"))
        if not point:
            continue
        kind = _enum(r.get("from"), AOI_SOURCES, None)
        source = _clean(r.get("source"))
        if not kind or not source:
            unsourced += 1
            continue
        # Against its OWN kind. A point that says it comes from the SOP gap and
        # matches only a flag is not derived from what it claims, and the claim
        # is the thing being relied on.
        if not any(_overlaps(source, t, 0.5) or _overlaps(t, source, 0.5)
                   for t in have.get(kind, [])):
            unmatched += 1
            continue
        out.append({"point": point, "from": kind, "source": source})

    if unsourced:
        notes.append(f"area of improvement: {unsourced} point(s) named no "
                     f"operational failure, SOP gap or flag they came from — "
                     f"dropped, not rendered unsourced")
    if unmatched:
        notes.append(f"area of improvement: {unmatched} point(s) named a source "
                     f"that matches no operational failure, SOP gap or flag on "
                     f"this card — dropped as invented")
    return out


def _alias_flag_teams(raw, notes):
    """Legacy team codes, translated into the nine-team vocabulary.

    CE and RO were the two support-side chips and both are the CO team now. A
    draft written under the old vocabulary would otherwise fail the enum and
    land on OTHER, which raises nothing — a real flag against a real team,
    silently unrouted. The translation is REPORTED: we changed what the model
    said, and the trail is where this build says so.
    """
    out = []
    for f in (raw if isinstance(raw, list) else []):
        if isinstance(f, dict):
            code = str(f.get("team") or "").strip().lower()
            alias = FLAG_TEAM_ALIASES.get(code)
            if alias:
                notes.append(f"flag team {f.get('team')!r} → {alias.upper()} "
                             f"({ACTION_TABS[alias]['label']})")
                f = {**f, "team": alias.upper()}
        out.append(f)
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


def _booking_logs(raw, booking_confirmed, notes):
    """The booking timeline, or nothing when there is no booking yet.

    Dropped rows are COUNTED and reported. A section that empties itself
    silently is indistinguishable from a lookup that found nothing, and the
    difference here is the whole point: "no booking picked yet" and "this
    booking has no events" are different sentences for the reader.
    """
    rows = _rows(raw, ("time", "what", "detail"))
    if booking_confirmed or not rows:
        return rows
    notes.append(f"booking_logs: {len(rows)} row(s) dropped — no booking is "
                 f"confirmed yet, so there is no booking for a timeline to be "
                 f"about; the model narrated the guest's account instead")
    return []


def validate(rca: dict, scenarios_routed=None, keep_actions=None,
             booking_confirmed: bool = True, events=None,
             booking=None, review_at=None) -> tuple[dict, list]:
    """Return (coerced rca, notes). Never raises."""
    notes: list[str] = []
    if not isinstance(rca, dict):
        return {}, ["model returned no JSON object"]

    wwr = _obj(rca.get("what_went_wrong"))
    _gi = wwr.get("guest_issues")
    issues = [_issue(i, notes) for i in (_gi if isinstance(_gi, list) else [])
              if isinstance(i, dict)]
    _gate_amount_claims(issues, booking, events, notes)
    _dss_followed, _dss_note = dss_check.gate_dss_followed(
        _obj(rca.get("dss")).get("followed"), events, review_at)
    if _dss_note:
        notes.append(_dss_note)

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
    _flags = _rows(_alias_flag_teams(rca.get("flags"), notes),
                   ("team", "flag", "evidence", "zd_ref"),
                   notes, enums={"team": (FLAG_TEAMS, "OTHER")})
    issues, _flags = _demote_findings(issues, _flags, notes, scenarios_routed)

    # BEING LEFT UNANSWERED IS COMPUTED, NOT NOTICED. The timeline says what
    # happened; it cannot say what did not, and a guest who wrote three times
    # over two days before anyone replied is a failure living entirely in the
    # space between rows. The checklist has had the words since v7.1 and
    # nothing computed them — they were left for the model to spot in a list of
    # forty events, which it does when the gap is glaring and misses when it is
    # merely long.
    #
    # Added, never replacing: a flag the model raised about the same silence
    # stands, and the dedupe below drops the repeat rather than the finding.
    if events:
        from server.response_gaps import gap_flags
        _seen = {(str(f.get("team") or "").upper(),
                  str(f.get("flag") or "").strip().lower()) for f in _flags}
        _added = 0
        for _gf in gap_flags(events):
            if (_gf["team"], _gf["flag"].lower()) in _seen:
                continue
            _flags.append(_gf)
            _seen.add((_gf["team"], _gf["flag"].lower()))
            _added += 1
        if _added:
            # Said out loud: these are on the card for a different reason from
            # the rest, and a reader comparing two runs deserves to know which
            # were measured rather than judged.
            notes.append(f"flags: {_added} response-gap flag(s) raised from the "
                         f"timeline — measured, not read out of the model's answer")

    # ACTIONS TAKEN IS BUILT FROM WHAT THIS CASE FOUND.
    #
    # It used to be built from the DSS guideline sheet for the routed
    # scenario, then filtered by flagged team and word overlap — two filters
    # applied to the wrong source. Guideline rows are a PLAYBOOK for a
    # scenario, not things that happened on this booking, so "Share ARN number
    # for delayed refunds" reached a card with no delayed refund: a valid row
    # for the scenario, passing both filters, and still a statement about work
    # nobody did. The section is read as "this is what we did", which no
    # playbook row can honestly say.
    #
    # Six sources, all of them findings already on this card: the flags, each
    # issue's operational failure, its SOP gap, its fix (which names the owning
    # team), the provenance-checked improvement points, and the DSS MISS.
    #
    # DSS now contributes ONE thing: what the next escalation step should have
    # been where it did not happen. Not an anchor, not a definition, not a
    # comment — and no rows of its own.
    # Computed here rather than in the return dict, because Actions Taken
    # reads it. Two calls would be two lists — and the second would report its
    # dropped points a second time, so the trail would say twice over that a
    # point was discarded.
    _improve_rows = _improvements(rca.get("area_of_improving"),
                                  issues, _flags, notes)

    # §3 IS THE SOURCE FOR ACTIONS TAKEN. Same rows, grouped by owner — not a
    # second store. Merging six sources into its own array is how one
    # remediation reached a card twice, as the fix that closes a gap and again
    # as the flag that raised it.
    # ALWAYS from the fixes, including on a draft that predates the section —
    # `_fix_rows` migrates its per-issue fixes first. Branching on which shape
    # the draft happens to be would give two cards two different meanings for
    # one tab strip, which is worse than either meaning on its own.
    #
    # The five other sources this used to merge — flags, operational failures,
    # SOP gaps, improvements, DSS misses — are NOT lost. Each already renders
    # in its own section, and routing them here as well is what put one
    # remediation on a card twice.
    _findings = _case_findings(wwr.get("case_findings"), issues, notes)
    _fixes = _fix_rows(wwr.get("fixes"), issues, notes)
    _actions, _ar = actions_from_fixes(_fixes, keep=keep_actions)
    notes.extend(_ar["notes"])

    return {
        "stated_issue":      _clean(rca.get("stated_issue")),
        **_taxonomy(rca, notes),
        "sub_themes":        [s for s in (rca.get("sub_themes") if isinstance(rca.get("sub_themes"), list) else []) if _clean(s)],
        "scenarios":         scenarios,
        "overlay_scenarios": overlays,
        # `fixes` is stored beside the issues, not derived at render: Actions
        # Taken is a VIEW over it, and a view whose source lives only in a
        # local would be rebuilt differently by each read path. That is the
        # two-stores-for-one-fact defect, arrived at from the other direction.
        "what_went_wrong":   {"guest_issues": issues, "fixes": _fixes,
                              "case_findings": _findings},
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
            #
            # time and channel used to be struck from this list entirely, on the
            # reasoning that they are facts, they live on the frames, and a
            # field the model must not fill is a field it will fill - a real run
            # had both null while the prose said "chat at 15:41".
            #
            # That reasoning was right about precedence and wrong about
            # presence. A contact with no Zendesk frame - the guest's account of
            # a call, an off-Zendesk exchange - has no frame to take a time
            # from, so striking the field rendered a dash: the same dash a
            # broken lookup renders. The fix is PRECEDENCE, not absence. The
            # model may state them; the frame's value wins wherever a frame
            # exists, and the UI marks a model-supplied time as unverified.
            #
            # The narrative fields are the model's alone. Nothing in the
            # pipeline knows how long a guest waited for a human, what they said
            # back, or how the contact ended - a frame carries neither. Dropping
            # them here made rule 10b unenforceable in exactly the way CLAUDE.md
            # names: the model answers, the projection discards it, and the card
            # is indistinguishable from a model that never answered.
            r for r in _rows(contacts, CONTACT_FIELDS, notes,
                             enums={"channel": (CONTACT_CHANNELS, None)})
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
        # THE BOOKING'S TIMELINE, so there has to be a booking. Rule 10 tells
        # the model to narrate the guest's own account when the systems gave it
        # nothing — right once a booking is confirmed, wrong before, because
        # then the heading is about a booking that is still an open question.
        # A six-event sequence appeared under it on a card whose match had not
        # been picked, and nothing distinguished it from a real one.
        #
        # Enforced here as well as in the prompt: a rule the model can ignore
        # is a rule the card cannot rely on, and this one is checkable.
        "booking_logs":      _booking_logs(rca.get("booking_logs"),
                                           booking_confirmed, notes),
        # team falls back to OTHER, not null: the UI renders it as a
        # chip-select over the closed list, and a null would either blank the
        # control or add a stray option to it. OTHER is a real member.
        "flags":             _flags,
        # Computed, not copied: see actions_raised. The tabs are always all
        # nine keys, so an empty tab is a tab with nothing raised rather than a
        # tab the projection forgot.
        "actions_taken":     _actions,
        "area_of_improving": _improve_rows,
        "resolution":         _clean(rca.get("resolution")),
        "suggested_response": _clean(reply),
        "takedown": {"verdict": _enum(_obj(rca.get("takedown")).get("verdict"),
                                      TAKEDOWN, "Untraceable")},
        "dss": {"prescribes": _clean(dss.get("prescribes")),
                "ref":        _clean(dss.get("ref")),
                # Whether we TOOK the prescribed path, which `prescribes`
                # deliberately does not say. Only written where the timeline
                # shows the guest wrote in first — see `dss_check`.
                "followed":   _dss_followed},
    }, notes


def _fix_rows(raw, issues, notes) -> list:
    """§3's fixes: [{action, owner, because}].

    MIGRATES A PRE-RESTRUCTURE DRAFT rather than reading it as a case with no
    fixes. Before this section existed the fix lived on each issue as
    `issue.fix`; a draft written then has no `fixes` array, and returning []
    for it would render "Nothing to fix" beside issues that name a fix. The
    move is reported, because it is a rewrite of what was stored.

    A fix with no action is dropped — a row with an owner and no action tells
    a team they own something and does not say what.
    """
    rows, migrated = [], 0
    src = raw if isinstance(raw, list) else None
    if src is None:
        for i in (issues or []):
            f = i.get("fix") if isinstance(i.get("fix"), dict) else None
            if f and _clean(f.get("action")):
                rows.append(dict(f))
                migrated += 1
        if migrated:
            notes.append(f"{migrated} fix(es) read from the old per-issue "
                         f"shape — this draft predates the fixes section and "
                         f"was not regenerated")
        return rows

    for f in src:
        if not isinstance(f, dict):
            continue
        action = _clean(f.get("action"))
        if not action:
            continue
        owner_raw = str(f.get("owner") or "").strip()
        owner = FLAG_TEAM_ALIASES.get(owner_raw.lower(), owner_raw.lower())
        if owner and owner.upper() not in OWNERS:
            notes.append(f"fix.owner {owner_raw!r} is not one of the nine "
                         f"teams → unrouted (it would name an owner with no tab)")
            owner = ""
        rows.append({"action": action,
                     "owner": owner.upper() if owner else None,
                     "because": _clean(f.get("because"))})
    return rows


def _case_finding_key(text) -> str:
    """What makes two case findings the same finding.

    Wording, normalised — not identity. Two claims citing one fact routinely
    word it differently ("tickets sent 14:02, two hours after the slot" vs
    "the tickets arrived two hours late"), and a key on the exact string would
    keep both. Stop words and punctuation go, because they are where the
    variation lives.
    """
    return " ".join(sorted(_tokens(text)))


# How much of the shorter row's wording has to reappear in the longer one
# before they are the same fact said twice.
#
# 0.6 is a JUDGEMENT and it is applied where it can be seen: every collapse is
# counted on the notes, so a threshold set too high shows up as repeated rows
# on the card and one set too low shows up as a count that does not match what
# is on screen. Neither fails silently.
#
# It exists because `_case_finding_key` compares SORTED TOKENS and therefore
# only catches rows that say the same thing in the same words. The real
# repeats never did:
#
#   "Original 08:30 slot cancelled via API and rebooking sent to Krakville
#    at 11:00 AM on the same booking reference."
#   "Rebooking to Krakville at 11:00 sent 02 Aug 09:11; updated confirmation
#    emailed 02 Aug 09:13 with new deadline of 02 Aug 11:00."
#
# One event, two wordings, two different keys — so the exact-key check passed
# both through and the card showed eighteen findings for nine facts.
_SAME_FACT_OVERLAP = 0.6


# WHEN AN EVIDENCE ROW IS THE SAME EVENT AS A NARRATIVE ROW.
#
# Lower than the collapse threshold above, and deliberately, because the two
# do different things. Measured on tp_1785672694_664719:
#
#   0.55  [ 6] x [14]  the NAR note, twice          DUPLICATE
#   0.50  [ 7] x [13]  the agent closing the window DUPLICATE
#   0.45  [ 5] x [11]  the guest reporting 13:45    DUPLICATE
#   0.45  [ 4] x [11]  our email vs the guest's report   DIFFERENT
#   0.45  [ 1] x [ 9]  booking created vs cancelled     DIFFERENT
#
# There is NO GAP: 0.45 holds a duplicate and two non-duplicates. Stripping
# digits does not separate them either — both land at 0.375. Every row about
# one booking shares its times, its vendor and the word "guest", so word
# overlap cannot tell "one event described twice" from "two events on one
# booking".
#
# I set this to 0.45 first, on the reasoning that a mis-attributed ticket link
# is cheaper than a duplicate row. That was wrong: folding REMOVES the row, so
# a false positive does not mis-attribute a ref, it DELETES A REAL FINDING.
# The test caught it immediately —
#
#   "Updated confirmation email sent to guest showing 11:00 AM start"
#   "Guest reported the vendor sent a message showing 13:45 pickup, not 11:00"
#
# — two different events, 0.50 containment, and the duplicate pair
# [7] x [13] is ALSO 0.50. Measured, both of them. There is no value that
# folds one and keeps the other.
#
# So it sits at the collapse threshold, where a fold only happens on wording
# similar enough that the collapse rule would have fired anyway. That leaves
# some duplicates on the card. It never loses a finding, and between those two
# failures only one is recoverable by a reader.
#
# The real key is not wording at all: [6] and [14] are both the NAR note at
# 15:28, [7] and [13] are both the agent closing the window. Two rows are the
# same event when they describe the same MOMENT, and `time` is already carried
# on every finding for ordering. That is the fix worth making; it needs to be
# built against real `time` values rather than guessed at, which is what every
# threshold in this file has been.
_EVIDENCE_FOLD_OVERLAP = _SAME_FACT_OVERLAP


def _first_repeat_index(text, existing_token_sets, threshold):
    """Index of the first row this restates, or None.

    Same containment as `_is_repeat_of`, returning WHICH row matched so the
    caller can fold into it rather than only knowing that it should.
    """
    want = _tokens(text)
    if len(want) < 4:
        return None
    for i, got in enumerate(existing_token_sets):
        if not got or len(got) < 4:
            continue
        if len(want & got) / min(len(want), len(got)) >= threshold:
            return i
    return None


def _is_repeat_of(text, existing_token_sets) -> bool:
    """Does this row say something already on the list, in other words?

    Containment rather than Jaccard: an evidence row that proves a claim is
    frequently a SHORTER, sharper restatement of a narrative row, and Jaccard
    punishes the length difference exactly when the two are most alike. What
    matters is whether the shorter row's content already appears in the longer.

    A row with almost no significant tokens ("the guest complained") cannot be
    judged this way and is never collapsed on it — two short rows sharing
    their only two words are not necessarily one fact.
    """
    want = _tokens(text)
    if len(want) < 4:
        return False
    for got in existing_token_sets:
        if not got:
            continue
        overlap = len(want & got) / min(len(want), len(got))
        if overlap >= _SAME_FACT_OVERLAP:
            return True
    return False


def _case_findings(raw, issues, notes) -> list:
    """§1: the booking's story, evidenced — one ordered, deduplicated list.

    THE EVIDENCE ROWS MOVE HERE. They were per-issue, so a fact cited by two
    claims rendered twice, which is the single biggest source of repeated text
    on the card. They keep their claim association in the data — nothing is
    deleted — and are merged into this list for rendering, once each.

    ORDERED BY EVENT where a row carries a time, and stable otherwise: a plain
    list is the honest rendering of rows that carry no order, and inventing one
    would put a sequence on screen that the records do not support.

    An empty list is a legitimate answer and the card says so in words. What
    must not happen is a case nobody read looking like a case that was read and
    was clean.
    """
    rows, seen, token_sets = [], set(), []
    collapsed = 0

    def _add(text, source, time, why, ref=None, backs=None):
        nonlocal collapsed
        text = _clean(text)
        if not text:
            return
        key = _case_finding_key(text)
        if not key or key in seen:
            return
        # THE REWORDED REPEAT, which the key above cannot see. A narrative row
        # and the evidence row that proves a claim about the same moment are
        # the commonest pair, and they are never worded identically.
        if _is_repeat_of(text, token_sets):
            collapsed += 1
            return
        seen.add(key)
        token_sets.append(_tokens(text))
        rows.append({"text": text,
                     "source": _enum(source, EVIDENCE_SOURCES, None),
                     "time": _clean(time),
                     # `ref` IS RENDERED, unlike source and time. The handoff
                     # withholds those two by name; it says nothing about ref,
                     # and ref is what turns "41 negative reviews in the
                     # window" into a number with a range attached, and a
                     # ticket id into something you can open.
                     "ref": _clean(ref),
                     # WHICH CLAIM THIS PROVES, or None for a narrative row.
                     # It is what separates the two jobs this list does, and
                     # what keeps a moved evidence row routed to its claim.
                     "backs_claim": backs})

    for r in (raw if isinstance(raw, list) else []):
        if isinstance(r, str):
            _add(r, None, None, "string")
        elif isinstance(r, dict):
            _add(r.get("text"), r.get("source"), r.get("time"), "row",
                 r.get("ref"))

    # ── the evidence rows, which do a DIFFERENT JOB from the rows above ────
    #
    # §1 carries two kinds of pointer and the difference is what stops them
    # repeating each other:
    #
    #   narrative  what happened — the booking arrived, was it fulfilled, what
    #              did the guest hit, why did they contact us, what did we say,
    #              how did it end
    #   evidence   proves or disproves ONE claim the guest made, and carries
    #              the ZD ref it was read from
    #
    # They cannot restate one another while each is doing its own job. When
    # they do, `_is_repeat_of` collapses the second and the count says so —
    # because the pair that actually appeared on the card was a narrative row
    # and an evidence row describing the same moment in different words, which
    # the sorted-token key cannot see.
    #
    # A ROW THAT BACKS NO CLAIM IS NOT EVIDENCE. Evidence exists to settle a
    # claim; a row citing nothing is a timeline entry that wandered in, and
    # timeline entries are what made this section read as a second copy of the
    # events timeline. Dropped and COUNTED, never dropped quietly — a section
    # that silently shrinks is the failure this file opens with.
    merged = dropped = folded = 0
    for n, issue in enumerate(issues or []):
        for e in (issue.get("evidence") or []):
            if not isinstance(e, dict):
                continue
            if not _clean(e.get("text")):
                continue
            backs = e.get("backs_claim")
            backs = n if backs in (None, "") else backs
            if not _clean(issue.get("claim")):
                dropped += 1
                continue
            # FOLD, DO NOT DROP AND DO NOT REPEAT. An evidence row that
            # restates a narrative row is the same event written twice — but
            # it carries the ZD ref, which the narrative row does not. So its
            # REF and its claim routing move onto the row already there, and
            # no second row is written.
            _hit = _first_repeat_index(_clean(e.get("text")), token_sets,
                                       _EVIDENCE_FOLD_OVERLAP)
            if _hit is not None:
                _row = rows[_hit]
                if not _row.get("ref") and _clean(e.get("ref")):
                    _row["ref"] = _clean(e.get("ref"))
                if _row.get("backs_claim") is None:
                    _row["backs_claim"] = backs
                folded += 1
                continue
            before = len(rows)
            _add(e.get("text"), e.get("source"), e.get("time"), "evidence",
                 e.get("ref"), backs)
            if len(rows) > before:
                merged += 1
    if merged:
        notes.append(f"{merged} evidence point(s) moved into case findings "
                     f"from the claims they back")
    if dropped:
        notes.append(f"{dropped} evidence row(s) backed no claim and were "
                     f"NOT rendered — evidence settles a claim, and a row "
                     f"citing none is a timeline entry")
    if folded:
        notes.append(f"{folded} evidence point(s) said what a case finding "
                     f"already said; their ticket reference was added to that "
                     f"finding rather than a second row being written")
    if collapsed:
        notes.append(f"{collapsed} case finding(s) repeated a point already "
                     f"in the list in different words and were collapsed")

    # Rows carrying a time lead, in time order; the rest keep the order they
    # were written in. `sorted` is stable, so an undated row never jumps.
    #
    # The TIME IS NOT RENDERED — §1 is the reading of the case, the events
    # timeline is the record with the clock on it. It is kept because it is
    # the only thing that can order these rows, and an order the records
    # support beats the order the model happened to write them in.
    rows.sort(key=lambda r: (r["time"] is None, r["time"] or ""))
    return rows
