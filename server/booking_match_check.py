"""Does the review describe the experience the booking is for?

"if the review is talking about a city card but the experience with the
booking id is of a guided tour, to flag it in the booking match section. this
can happen when the guest/reviewer provided incorrect info."

A guest who quotes someone else's reference number, or mistypes their own,
produces a match that passes every check the pipeline makes — the booking id
is real, the booking exists, the dates line up — and describes a completely
different product. Nothing downstream can catch it, because every other check
is about whether the BOOKING is coherent, not about whether it is the one the
review is about.

DELIBERATELY CONSERVATIVE. A false flag here sends an associate to re-match a
correct booking, and doing that often enough teaches them to ignore the flag —
at which point it is worse than absent. So it fires only when the review names
one recognised product family and the booking names a DIFFERENT one. Anything
less certain returns "unchecked", which is a distinct answer and says so.

Three states, never two:

  match      both name the same family
  mismatch   both name a family and they disagree
  unchecked  one side named nothing recognisable — we did not check, which is
             NOT the same as checking and finding no problem

That third state is the whole reason this returns a dict rather than a bool.
"""
import re

# Product families, and the words that identify one. Ordered: the first family
# whose words appear wins, so the specific ones come before the generic.
#
# "ticket", "entry" and "admission" are deliberately NOT a family. Almost every
# review about almost every product says one of them, so a family built on
# them would match everything and disagree with everything.
FAMILIES = [
    ("city card",    (r"city\s*card", r"city\s*pass", r"\bcitycard\b",
                      r"\bcity\s+explorer\b", r"tourist\s*card")),
    ("guided tour",  (r"guided\s*tour", r"\bwith\s+a\s+guide\b", r"\bour\s+guide\b",
                      r"\btour\s+guide\b", r"walking\s*tour", r"\bguide\s+was\b")),
    ("cruise",       (r"\bcruise\b", r"\bboat\s*(?:tour|trip|ride)\b",
                      r"\briver\s*(?:tour|trip)\b", r"\bsailing\b")),
    ("transfer",     (r"\btransfer\b", r"\bairport\s*(?:pickup|drop)\b",
                      r"\bshuttle\b", r"\bprivate\s+car\b")),
    ("show",         (r"\bconcert\b", r"\btheatre\b", r"\btheater\b",
                      r"\bmusical\b", r"\bopera\b", r"\blive\s+show\b")),
    ("museum",       (r"\bmuseum\b", r"\bgallery\b", r"\bexhibition\b")),
    ("observation",  (r"observation\s*deck", r"\bsky\s*deck\b", r"\bviewing\s+platform\b")),
    ("food",         (r"\bfood\s*tour\b", r"\btasting\b", r"\bdinner\b",
                      r"\bcooking\s*class\b")),
]


def family_of(text):
    """The product family a piece of text names, or None."""
    t = " " + (text or "").lower() + " "
    for name, pats in FAMILIES:
        if any(re.search(p, t) for p in pats):
            return name
    return None


def check(review_text, booking) -> dict:
    """Whether the review and the booking describe the same product.

    Returns {"state", "review_family", "booking_family", "experience", "why"}.
    `state` is "match", "mismatch" or "unchecked" — never a bare boolean, so a
    caller cannot mistake "we did not check" for "we checked and it is fine".
    """
    booking = booking if isinstance(booking, dict) else {}
    exp = str(booking.get("experience") or booking.get("experienceName")
              or booking.get("experience_name") or "").strip()

    rf = family_of(review_text)
    bf = family_of(exp)

    if not exp:
        return {"state": "unchecked", "review_family": rf, "booking_family": None,
                "experience": "", "why": "the booking has no experience name to compare"}
    if rf is None:
        return {"state": "unchecked", "review_family": None, "booking_family": bf,
                "experience": exp,
                "why": "the review does not name a product we recognise"}
    if bf is None:
        return {"state": "unchecked", "review_family": rf, "booking_family": None,
                "experience": exp,
                "why": "the booked experience does not name a product we recognise"}
    if rf == bf:
        return {"state": "match", "review_family": rf, "booking_family": bf,
                "experience": exp, "why": f"both describe a {rf}"}
    return {"state": "mismatch", "review_family": rf, "booking_family": bf,
            "experience": exp,
            "why": f"the review describes a {rf}; this booking is a {bf}"}
