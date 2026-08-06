"""The what-went-wrong section of the Slack thread post.

ONE composer. `services/slack.py` calls it, and the dashboard renders the
string this produces rather than building its own — see `_draft_dict`'s
`wwr_slack_text`. That is the whole point of this module existing: there used
to be two composers, this section was written twice in two languages, and
"• Fix: [object Object]" reached a real post from the client half while the
server half was correct. A defect that can only appear in one of two renderers
is a defect nothing on the server can test.

The five headings are MANDATED by the user and always appear. Sub-points
a/b/c are INDICATIVE — only the ones this issue actually has are printed.
Everything else the card shows — evidence rows, the verbatim guest quote,
`pattern`, `backs_claim`, the owner chip, the accuracy note's prose — stays on
the dashboard and is deliberately NOT in the post.
"""
from server.checklist import WHAT_WENT_WRONG_STRUCTURE

# The headings come from checklist.WHAT_WENT_WRONG_STRUCTURE, which is the
# source of truth for their wording — the same list the RCA prompt is built
# from. Spelling them again here is how the post and the instruction the model
# was given drift apart, and then the post claims a structure the model was
# never asked for.
#
# Each entry is "<heading> — <guidance for the model>". The post wants the
# heading; the guidance is for the prompt. A line with no em-dash keeps its
# whole text rather than silently losing anything, and `headings()` is driven
# by a test that pins all five, so a re-wording fails loudly there instead of
# quietly putting a paragraph of prompt guidance into a Slack post.
# The count the composer is written against. A NUMBER, checked at runtime,
# because the block builder indexes heads[0..3] — if the structure grows or
# shrinks the indexing is silently wrong, and a post with a missing heading
# looks like a model that had nothing to say under it.
MANDATED_HEADINGS = 4


def headings() -> list[str]:
    return [line.split(" — ", 1)[0].strip() for line in WHAT_WENT_WRONG_STRUCTURE]


# The model's verdict vocabulary is four values (see rca_v4_validate
# .CLAIM_ACCURACY). The user's heading-2 vocabulary is three. Mapping the three
# that correspond is straightforward; `Unknown` is the one that matters.
#
# `Unknown` MUST NOT print as "No". "No" is a finding — we checked and the
# guest was wrong — and a coercion that turns "we could not establish this"
# into it puts a verdict on a guest's claim that nobody reached. It prints as
# a phrase that is visibly NOT one of the three, so a reader can never mistake
# it for an answer.
ACCURACY_TO_POST = {
    "Accurate":        "Yes",
    "Partly accurate": "Partially True",
    "Inaccurate":      "No",
}

# The two kinds of Unknown, told apart by whether a reason was recorded.
# `claim_accuracy_note` is required on every verdict by the prompt, so its
# ABSENCE on an Unknown is itself the signal: a verdict with a reason is a
# check that ran and came back empty, one without is a check that left no
# trace of having run. The note's prose stays off the post — only which of the
# two this is reaches the reader.
UNKNOWN_CHECKED     = "Not established (checked; the record cannot settle it)"
UNKNOWN_NO_REASON   = "Not established (no reason was recorded for this verdict)"
UNKNOWN_NO_VERDICT  = "Not established (the RCA recorded no verdict for this claim)"


def accuracy_line(g: dict) -> str:
    """What heading 2 prints for one guest issue."""
    raw = (g.get("claim_accuracy") or "").strip()
    if not raw:
        return UNKNOWN_NO_VERDICT
    if raw in ACCURACY_TO_POST:
        return ACCURACY_TO_POST[raw]
    # Unknown, or any value a draft written under an older enum still carries.
    # Falling through to the guest's-vocabulary words would be a coercion; the
    # value is named so the reader can see it is outside the three.
    note = str(g.get("claim_accuracy_note") or "").strip()
    if raw == "Unknown":
        return UNKNOWN_CHECKED if note else UNKNOWN_NO_REASON
    return f"Not established (verdict recorded as “{raw}”, which is outside Yes / Partially True / No)"


def _sub(letter: str, text: str) -> str:
    return f"   {letter}. {text}"


def _join_points(v) -> str:
    """A pre-v4 document-level field that may be a string, a list, or a list of
    {point: ...} dicts. Joining the raw value would put a Python repr of a dict
    into a Slack post, which is the same class of defect as the stringified fix.
    """
    if isinstance(v, dict):
        v = [v]
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            t = str((x.get("point") or x.get("text") or "") if isinstance(x, dict)
                    else (x or "")).strip()
            if t:
                out.append(t)
        return "; ".join(out)
    return str(v or "").strip()


def _fix_lines(g: dict, doc_fixes: dict) -> list[str]:
    """Heading 5: the teams to tag, and the corrective action.

    The issue's own fix wins over the document-level one. `fix` is an OBJECT —
    action, owner, because — and the client half of the old two-composer setup
    dropped it into a string concatenation, which is how "• Fix: [object
    Object]" went out on a real post. Read field by field here so there is no
    stringification to get wrong.
    """
    fx = g.get("fix") if isinstance(g.get("fix"), dict) else None
    lines = []
    if fx and str(fx.get("action") or "").strip():
        owner = str(fx.get("owner") or "").strip()
        if owner:
            lines.append(_sub("a", f"@{owner}"))
        else:
            lines.append(_sub("a", "No team tagged — the fix names no owner"))
        lines.append(_sub("b", str(fx["action"]).strip()))
        return lines
    if isinstance(g.get("fix"), str) and g["fix"].strip():
        lines.append(_sub("a", "No team tagged — this draft predates fix owners"))
        lines.append(_sub("b", g["fix"].strip()))
        return lines
    # Nothing on the issue. The document-level node is where a pre-v4 draft
    # keeps its fixes, so an old RCA reposted from the dashboard still says
    # something under a heading that must appear either way.
    teams = [t for t in (doc_fixes or {}).get("teams") or [] if str(t).strip()]
    actions = [str(a).strip() for a in (doc_fixes or {}).get("actions") or []
               if str(a).strip()]
    if teams:
        lines.append(_sub("a", ", ".join(f"@{t}" for t in teams)))
    if actions:
        lines.append(_sub("b", "; ".join(actions)))
    if not lines:
        lines.append(_sub("a", "No fix recorded for this issue, and no team tagged"))
    return lines


def _happened_from_document(wh: dict) -> dict:
    """The document-level analysis, in the per-issue field names.

    A draft can carry guest issues that name the complaint but keep the
    analysis at document level — that is exactly the shape of the drafts
    written between the v3 and v4 prompts. Reading only the issue's own fields
    prints "No root cause recorded" over an RCA that recorded three, which is
    the inverse bug: a healthy run made to look faulty.
    """
    wh = wh or {}
    causes = [c for c in (wh.get("root_causes") or []) if isinstance(c, dict)]
    return {
        "root_cause": "; ".join(
            f"{(c.get('issue') + ': ') if c.get('issue') else ''}{c.get('cause', '')}".strip()
            for c in causes if str(c.get("cause") or "").strip()),
        "operational_failure": _join_points(wh.get("operational_failure")),
        "sop_gap": _join_points(wh.get("sop_gap")),
    }


def _issue_block(g: dict, sx: dict, doc_fixes: dict, heads: list[str],
                 doc_happened: dict = None) -> str:
    # `sx` is the sp_escalation node. It is accepted and NOT used: escalation
    # moved to sp_interaction_notes, and the parameter stays so every caller
    # keeps working. Removing it silently would be a signature change nobody
    # asked for; leaving it undocumented would read as a bug.
    lines = []

    # 1. Guest issue
    lines.append(heads[0])
    summary = str(g.get("issue") or "").strip()
    lines.append(_sub("a", summary or "Not recorded — the RCA named no issue here"))

    # 2. Is the guest's claim accurate?
    lines.append(f"{heads[1]} {accuracy_line(g)}")

    # 3. What actually happened?
    lines.append(heads[2])
    keys = (("a", "root_cause", "Root cause"),
            ("b", "operational_failure", "Operational failure"),
            ("c", "sop_gap", "SOP/process gap"))
    happened = [_sub(letter, f"{label}: {str(g.get(key)).strip()}")
                for letter, key, label in keys if str(g.get(key) or "").strip()]
    if not happened and doc_happened:
        # The issue names the complaint and the analysis sits at document
        # level. Using it beats printing "no root cause recorded" over an RCA
        # that recorded one — and the reader is told it is case-level, because
        # with several issues the same analysis appears under each and that
        # would otherwise read as three separate findings that happen to match.
        happened = [_sub(letter, f"{label}: {str(doc_happened.get(key)).strip()}")
                    for letter, key, label in keys
                    if str(doc_happened.get(key) or "").strip()]
        if happened:
            happened.append("   (recorded for the case, not for this issue "
                            "alone — this RCA predates per-issue analysis)")
    if happened:
        lines.extend(happened)
    else:
        # Sub-points are indicative, so b and c being absent is normal and
        # silent. All three absent is not: heading 3 is mandatory and would
        # otherwise print as a bare line, which reads exactly like a renderer
        # that dropped its body.
        lines.append(_sub("a", "No root cause recorded — nothing was written "
                               "under this heading"))

    # 4. Fixes
    #
    # SUPPLY PARTNER ESCALATION USED TO BE HEADING 4. It is not dropped — it
    # lives in `sp_interaction_notes`, which already carries whether it was
    # raised, why not when it was not, and what came back. Repeating it here
    # printed "Did CE escalate to SP? Not recorded" under every issue on every
    # card, including the many with no supply partner involved at all.
    lines.append(heads[3])
    lines.extend(_fix_lines(g, doc_fixes))

    return "\n".join(lines)


def compose_legacy(wwr_scenarios, wwr_chain) -> str:
    """The same five headings, for a draft written before the v4 shape.

    A pre-v4 draft has no `what_went_wrong` node at all: its analysis is a
    list of scenario blocks (each with its own accuracy, why and fix) or, older
    still, a numbered chain. Without this the dashboard would render an empty
    what-went-wrong for those drafts — a section that silently disappears,
    which reads exactly like a composer that broke rather than like an RCA
    written in an older shape.

    Mapped into synthetic guest issues so there is still only ONE thing that
    knows the mandated heading format.
    """
    heads = headings()
    if len(heads) != MANDATED_HEADINGS:
        return ""
    issues = []
    for sc in (wwr_scenarios or []):
        if not isinstance(sc, dict):
            continue
        issues.append({
            "issue": str(sc.get("scenario_name") or "").strip(),
            # The legacy vocabulary is free text, not the four-value enum, so
            # it goes through the same mapping and lands outside Yes /
            # Partially True / No when it does not correspond — named, never
            # coerced into one of the three.
            "claim_accuracy": str(sc.get("accuracy") or "").strip(),
            "claim_accuracy_note": str(sc.get("accuracy_explanation") or "").strip(),
            "root_cause": str(sc.get("why") or "").strip(),
            "fix": sc.get("fix"),
        })
    if not issues:
        for st in (wwr_chain or []):
            if not isinstance(st, dict):
                continue
            issues.append({
                "issue": str(st.get("what") or "").strip(),
                "claim_accuracy": "",
                "root_cause": str(st.get("why") or "").strip(),
                "fix": None,
            })
    if not issues:
        return ""
    lead = ("This RCA was written before the current shape, so the headings "
            "below are filled from its scenario blocks.")
    if len(issues) == 1:
        return lead + "\n\n" + _issue_block(issues[0], {}, {}, heads)
    blocks = [f"*Guest issue {n} of {len(issues)}*\n"
              + _issue_block(g, {}, {}, heads)
              for n, g in enumerate(issues, 1)]
    return lead + "\n\n" + "\n\n".join(blocks)


def _has_content(w: dict) -> bool:
    """Is there anything in this node worth composing?

    A what_went_wrong node is NOT absent just because the key is missing —
    `_resolve_v3_sections` folds the denormalised columns back in, so a draft
    with nothing to say still arrives here as `{"guest_issues": []}`. Testing
    the key's presence would call that a populated node, compose five headings
    of "not recorded" for it, and never fall through to the legacy shape a
    pre-v4 draft actually keeps its analysis in.
    """
    if not isinstance(w, dict):
        return False
    if [g for g in (w.get("guest_issues") or []) if isinstance(g, dict)]:
        return True
    wh = w.get("what_happened") or {}
    if any(wh.get(k) for k in ("root_causes", "operational_failure",
                               "sop_gap", "pattern")):
        return True
    if (w.get("sp_escalation") or {}).get("escalated"):
        return True
    fx = w.get("fixes") or {}
    return bool(fx.get("teams") or fx.get("actions"))


def compose(what_went_wrong: dict) -> str:
    """The full what-went-wrong section body for the Slack post.

    Returns the empty string when the node holds nothing — the caller decides
    whether to print a heading for a section with no data, and whether an
    older shape on the same draft has something to say instead.
    """
    w = what_went_wrong if isinstance(what_went_wrong, dict) else {}
    if not _has_content(w):
        return ""
    heads = headings()
    if len(heads) != MANDATED_HEADINGS:
        # The structure is the source of truth and it no longer has the
        # mandated number of entries. Saying so beats printing whatever number
        # it does have under a format that was specified exactly.
        return (f"Cannot compose: checklist.WHAT_WENT_WRONG_STRUCTURE has "
                f"{len(heads)} heading(s), and the mandated format has "
                f"{MANDATED_HEADINGS}.")

    issues = [g for g in (w.get("guest_issues") or []) if isinstance(g, dict)]
    sx = w.get("sp_escalation") or {}
    doc_fixes = w.get("fixes") or {}

    if not issues:
        # There IS content here — `_has_content` already established that —
        # but no guest issue carrying it. The five headings still print,
        # because they are mandatory, and the lead line says which of the two
        # empties this is rather than leaving a reader to guess.
        #
        # A PRE-v4 draft keeps its analysis at document level, under
        # `what_happened`, and has no guest_issues at all. Folding that node
        # into one synthetic issue means an old RCA reposted from the dashboard
        # still answers heading 3 from what it actually holds, instead of
        # printing "no root cause recorded" over an RCA that recorded several.
        folded = {"issue": "", "claim_accuracy": "", "fix": None,
                   **_happened_from_document(w.get("what_happened"))}
        lead = ("No guest issue was recorded on this RCA, so the headings below "
                "are answered from the case level only.")
        return lead + "\n\n" + _issue_block(folded, sx, doc_fixes, heads)

    # SEVERAL ISSUES REPEAT THE WHOLE STRUCTURE. Listing them under 1a was the
    # alternative and it is the flattening this project already fixed once:
    # two complaints with different root causes went into one list and the
    # reader could not tell which cause belonged to which. Each block is a
    # self-contained answer to all five headings.
    # The document-level analysis, used for any issue that carries none of its
    # own — see `_happened_from_document`.
    doc_happened = _happened_from_document(w.get("what_happened"))
    if len(issues) == 1:
        return _issue_block(issues[0], sx, doc_fixes, heads, doc_happened)
    blocks = []
    for n, g in enumerate(issues, 1):
        blocks.append(f"*Guest issue {n} of {len(issues)}*\n"
                      + _issue_block(g, sx, doc_fixes, heads, doc_happened))
    return "\n\n".join(blocks)
