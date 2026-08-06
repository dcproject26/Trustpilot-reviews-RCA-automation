"""Splitting a guest's display name into something worth searching on.

This was `parts[0], parts[-1]` — first token and last token — written twice, in
two places in the pipeline. On "Bhayani Salim F" it produced first="Bhayani",
last="F", and the card said so: *Searched Zendesk as 'Bhayani F'*.

Two things wrong with that, and they compound:

  * "Salim" was thrown away. It is the second most distinctive token in the
    name and it took no part in the search.
  * "F" was used as the SURNAME. Surname carries 0.7 of the name score and
    gets its own `requester:` query, so the search was largely keyed on a
    single letter — which matches a great many people and ranks none of them.

The rule now: the surname is the last token that is not an initial, and every
token is kept for searching. An initial is still recorded, because "F." is
evidence when a candidate's surname begins with F, but it is never mistaken
for the name itself.

One module because there were two copies. They agreed at the time, which is
how a second copy always starts.
"""
from __future__ import annotations

import re


def _is_initial(tok: str) -> bool:
    """"F", "F." or "F-" — one letter standing in for a name.

    Not a name. Treating it as one puts a single letter where a surname
    belongs, and everything downstream weights surnames heavily.
    """
    t = re.sub(r"[^\w]", "", tok or "")
    return len(t) == 1 and t.isalpha()


# Words that are not the guest's name and must not be taken for a surname.
# Deliberately short: guessing wrongly here drops a real surname, which is
# worse than searching on one extra token.
_NOISE = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "jr", "sr",
          "ii", "iii", "iv", "and", "&", "family", "the"}


# Display names that are not names. Trustpilot lets a reviewer post as
# "customer", and the pipeline searched Zendesk for it exactly as it would
# search for "Bhayani": the search returned half the desk, got truncated, and
# the card offered three bookings ranked on visit date alone with no venue
# agreement. Weak candidates from a meaningless query read as a near-miss —
# the associate checks three bookings that were never evidence of anything.
#
# A placeholder is not a weak identifier. It is the ABSENCE of one, and the
# two have to end differently: absent means untraceable, weak means confirm.
#
# Kept to words that are never a real given name in this dataset. "Guest" and
# "Client" are surnames somewhere in the world, but not here, and a false
# negative costs one review routed to untraceable that an associate can still
# open — against a false positive that sends them hunting through candidates
# assembled from nothing.
_PLACEHOLDER = {
    "customer", "customers", "guest", "guests", "anonymous", "anon",
    "user", "username", "client", "visitor", "buyer", "traveller", "traveler",
    "reviewer", "review", "trustpilot", "someone", "somebody", "person",
    "none", "null", "na", "n/a", "unknown", "test", "testing", "name",
    # Articles, so "a customer" reads the same as "customer". Placed
    # here rather than in _NOISE because _NOISE is stripped before
    # tokens are counted, and "A" is a legitimate initial - "A Cariello"
    # must stay a real name with one placeholder token in it.
    "a", "an",
}


def is_placeholder(name: str) -> bool:
    """Whether a display name carries no identifier at all.

    True when every usable token is a placeholder — so "customer" and
    "a customer" are both placeholders, while "Customer Cariello" is not: one
    real token is enough to search on.

    Distinct from an EMPTY name, which parse_author already reports as
    (None, None). This is the case that looks like a name, parses like a name,
    and identifies nobody.
    """
    toks = name_tokens(name)
    if not toks:
        return False          # nothing there at all - a different fact
    return all(re.sub(r"[^\w]", "", t).lower() in _PLACEHOLDER for t in toks)



# Names that appear on OUR booking records and belong to no guest. A guest
# never types "Customer Ops Lead" — it is an internal label on a corporate or
# desk-made booking, and comparing it to a reviewer's name is comparing a
# reviewer to a job title.
#
# The mirror of _PLACEHOLDER, which covers the REVIEW side ("customer",
# "anonymous"). Two lists because they are two different vocabularies: the
# review side is what a guest types when they do not want to be named, this
# side is what our systems write when no guest name was captured.
#
# The consequence of missing this is not cosmetic. The guest name is the
# SECOND strongest identifier after the booking id — it is what separates two
# bookings at the same venue on the same date — so a comparison against an
# internal label produces a disagreement that means nothing, on the signal we
# lean on most.
_INTERNAL_BOOKING_NAMES = {
    "customer ops lead", "customer ops", "ops lead", "cx lead", "ce lead",
    "headout", "headout ops", "internal", "internal booking", "corporate",
    "corporate booking", "b2b", "b2b booking", "test booking", "test",
    "partner booking", "agent booking", "desk booking", "not provided",
    "no name", "unknown guest",
}


def is_internal_booking_name(name: str) -> bool:
    """Whether a booking's guest name is one of OUR labels rather than a guest.

    Matched on the whole normalised string, not token-by-token: "Lead" and
    "Ops" are real surnames somewhere, and a guest called Anna Ops must not be
    read as internal. Only the complete phrase counts.
    """
    t = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    return bool(t) and t in _INTERNAL_BOOKING_NAMES


def name_tokens(name: str) -> list[str]:
    """Every usable token in a display name, in order.

    Punctuation-only fragments, titles and suffixes are dropped; initials are
    kept, because they are still evidence.
    """
    raw = re.split(r"[\s,]+", str(name or "").strip())
    out = []
    for t in raw:
        t = t.strip(".,;:'\"()")
        if not t:
            continue
        if t.lower() in _NOISE:
            continue
        if not re.search(r"[^\W\d_]", t, re.UNICODE):
            continue                     # digits or punctuation only
        out.append(t)
    return out


def parse_author(name: str) -> tuple[str | None, str | None]:
    """(first, last) for searching and scoring.

    `last` is the last token that is NOT an initial, so "Bhayani Salim F"
    gives ("Bhayani", "Salim") rather than ("Bhayani", "F"). If every token
    after the first is an initial, `last` is None — no surname is better than
    a letter, because a letter is what the score would then be resting on.

    A single token is returned as a first name only when it looks like one:
    at least two characters, letters. A one-letter or numeric "name" is not
    something to search on and is reported as nothing rather than as a guess.
    """
    toks = name_tokens(name)
    if not toks:
        return None, None
    if len(toks) == 1:
        t = toks[0]
        ok = len(re.sub(r"[^\w]", "", t)) >= 2 and not t.isdigit()
        return (t, None) if ok else (None, None)
    first = toks[0]
    surname = next((t for t in reversed(toks[1:]) if not _is_initial(t)), None)
    return first, surname


def search_tokens(name: str) -> list[str]:
    """Every token worth putting in front of a search, most distinctive last
    dropped first — i.e. the middle names too.

    The old pair-of-tokens split meant a middle name never reached Zendesk at
    all. Initials are excluded here (searching `requester:F` returns everyone
    and ranks nobody) while still being kept by parse_author's caller for
    scoring.
    """
    return [t for t in name_tokens(name) if not _is_initial(t)]
