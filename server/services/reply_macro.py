"""Which approved macro may become this guest's reply.

THE PROBLEM THIS SOLVES. The macro list files the SAME scenario several times,
differing only by what it promises the guest:

    Customer error (Missed the tour) - Offering HOC
    Customer error (Missed the tour) - Issuing partial refund as exception
    Customer error (Missed the tour) - Issuing 100% HOC as exception
    Venue Closure - Partial refund as a part of the venue was closed
    Venue Closure - Full refund as guest couldnt take the experience

The guest's review is identical across those. Nothing in the review can choose
between them, and keyword overlap least of all — they share almost every word.
What decides is the DSS: it is the thing that says which remedy this case is
entitled to. So the macro is gated on the DSS the way the DSS rows are gated on
the app's own filters, and only then does a selector choose among what is left.

THE REMEDY IS READ FROM THE BODY, NOT THE LABEL. `macro_l1l2.json` exists
because "the words a macro is FILED under are not the words it contains" — and
here it is the contents that matter, for the mirror-image reason: the gate's
question is "does this reply PROMISE the guest a refund", and a promise lives in
the sentence the guest will read, not in the filing label. Measured on the live
list, the label carries a remedy word in 28 of 80 macros; the body classifies
all 80.

DSS IS READ AS A PERMISSIVE SUPERSET, DELIBERATELY. A DSS action is a decision
tree, not a single remedy — "If the guest's claim is False, issue a coupon; if
True, 50% HOC or the cost of service in credits". Resolving that branch needs
facts the pipeline does not have, and guessing it would be inventing policy. So
what is extracted is the set of remedies the DSS action MENTIONS AT ALL, and the
gate only ever answers the question it can answer honestly: never offer a remedy
the playbook never named. Choosing between two remedies the playbook did name
stays the associate's call, like the booking-value threshold.
"""
import re

# The one vocabulary. Both sides of the gate speak it, so a macro's promise and
# a DSS action's mention are comparable at all.
REMEDIES = ("refund_full", "refund_partial", "credit_hoc", "coupon", "reschedule")

# What a REPLY promises. Matched on the macro body — the words the guest reads.
_MACRO_PROMISE = (
    ("refund_full", re.compile(
        r"full refund|100% refund|refunded (?:the|your) (?:full|entire)"
        r"|processed a refund|initiated (?:a|the) refund|refund has been", re.I)),
    ("refund_partial", re.compile(
        r"partial refund|refund of \d+%|partially refund", re.I)),
    ("credit_hoc", re.compile(
        r"\bhoc\b|headout credit|credits? (?:to|in) your|added\b[^.\n]{0,20}credits?"
        r"|store credit", re.I)),
    ("coupon", re.compile(r"coupon|promo code|discount code|EXPLORE\d", re.I)),
    ("reschedule", re.compile(
        r"reschedul|resent|re-?sent|new tickets|sent\b[^.\n]{0,25}new", re.I)),
)

# What a DSS ACTION mentions. Looser than the macro side on purpose: this is a
# superset test ("did the playbook name this remedy anywhere"), and a miss here
# wrongly withholds an entitled remedy, which is the costlier direction.
_DSS_MENTION = (
    ("refund_full", re.compile(r"full refund|100% refund|\brefund\b", re.I)),
    ("refund_partial", re.compile(r"partial refund|\d+% refund", re.I)),
    ("credit_hoc", re.compile(r"\bhoc\b|\bcredits?\b", re.I)),
    ("coupon", re.compile(r"coupon|promo code|discount code", re.I)),
    ("reschedule", re.compile(r"reschedul|re-?issue|resend|new ticket", re.I)),
)


def macro_promises(body: str) -> set:
    """The remedies this macro's text promises the guest. Empty = promises none.

    An empty set is a real answer, not a failure: 31 of the 80 live TP macros
    promise nothing at all (acknowledgement, asking for information, an ETA),
    and those are exactly the ones that stay available when the DSS prescribes
    no remedy."""
    text = str(body or "")
    return {name for name, pat in _MACRO_PROMISE if pat.search(text)}


def dss_permits(dss_rec: dict | None) -> tuple:
    """(permitted_remedies, reason) — what the DSS action names, and why.

    THE TWO EMPTIES ARE NOT THE SAME, and the reason is what tells them apart.
    A DSS that never matched a row and a DSS row that prescribes no remedy both
    return an empty set and both narrow the reply to remedy-free macros — but
    one is a lookup that came up short and the other is the playbook being
    deliberately silent, and a reader deciding whether to trust the draft needs
    to know which. Reported rather than merged, per rule 1.
    """
    rec = dss_rec or {}
    action = str(rec.get("action") or "")
    if not action.strip():
        if rec.get("fallback") or rec.get("match_score") == 0:
            return set(), ("no DSS row matched this case, so no remedy is "
                           "prescribed and only replies that promise nothing "
                           "are available")
        return set(), ("the DSS lookup returned nothing at all, so no remedy "
                       "is prescribed — this is the playbook being unavailable, "
                       "not a case it is silent about")
    found = {name for name, pat in _DSS_MENTION if pat.search(action)}
    if not found:
        return set(), ("the matched DSS row names no remedy, so only replies "
                       "that promise nothing are available")
    return found, ""


def macro_is_permitted(promises: set, permitted: set) -> bool:
    """May a macro promising `promises` be sent when the DSS permits `permitted`?

    EVERY remedy the reply promises must be one the playbook named. Not "any
    overlap": a macro promising a full refund AND credits, matched against a DSS
    that named only credits, would pass an overlap test and put an unprescribed
    refund in front of the guest — the whole failure this gate exists to stop.

    A macro that promises nothing always passes: it commits us to nothing, so
    there is nothing for the playbook to have authorised.
    """
    if not promises:
        return True
    return promises.issubset(permitted or set())


def gate(macros: list, dss_rec: dict | None) -> tuple:
    """(kept, dropped, reason) — the macros this case's DSS allows.

    `macros` is [{"situation", "response", ...}]. Returns the same dicts, each
    with `_promises` attached so the caller and the trail can say what a chosen
    reply commits us to.

    THE DROPPED ARE COUNTED, not silently filtered. "No macro fits this review"
    and "eleven fitted and every one promised a remedy the playbook did not
    authorise" are the same empty list and completely different problems: the
    first is a gap in the macro sheet, the second is a case whose DSS and macro
    set disagree. Only the count tells them apart.
    """
    permitted, reason = dss_permits(dss_rec)
    kept, dropped = [], []
    for m in macros or []:
        promises = macro_promises(m.get("response", ""))
        row = {**m, "_promises": sorted(promises)}
        if macro_is_permitted(promises, permitted):
            kept.append(row)
        else:
            dropped.append(row)
    return kept, dropped, reason


def gate_note(kept: list, dropped: list, reason: str, permitted: set) -> str:
    """One sentence for the confidence trail, or "" when there is no news.

    Silence only when nothing was withheld AND the playbook named a remedy —
    any narrowing of what the associate may send is worth a line, because the
    alternative is a reply list that looks like the whole list.
    """
    if not dropped and not reason:
        return ""
    bits = []
    if dropped:
        bits.append(
            f"{len(dropped)} approved macro(s) were withheld because they "
            f"promise a remedy the DSS did not name "
            f"({', '.join(sorted({p for d in dropped for p in d['_promises']}))})")
    if reason:
        bits.append(reason)
    if permitted:
        bits.append(f"the playbook names: {', '.join(sorted(permitted))}")
    bits.append(f"{len(kept)} macro(s) remain available")
    return "; ".join(bits) + "."
