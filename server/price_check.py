"""Was the guest charged for one ticket or two?

THE CIRCULAR ANSWER. A review said "booked one ticket, charged for 2". The RCA
answered Inaccurate because "Booking 32142070 records one adult, CHF 461.19
total, no add-ons" — the booking record confirming its own pax count. That
proves nothing: the question is not how many tickets the record says, it is
whether CHF 461.19 is the price of ONE of them or TWO.

Settling it needs a UNIT price, which the booking total and pax count cannot
supply on their own. Two sources can:

  * the ZENDESK ticket text, where the booking dump and confirmation emails
    routinely state the net and gross amounts;
  * the EXPERIENCE PAGE, for the list price of that variant.

Neither is guaranteed to be there, and "we could not establish it" is a
legitimate answer that must not be dressed up as either verdict. A guest
wrongly told they were not double-charged is worse than one told we could not
tell.
"""
import re

# "CHF 461.19", "PLN 606.00", "EUR 152.65", "USD 61.76", "£12.50", "€140.01"
_SYMBOL = {"£": "GBP", "€": "EUR", "$": "USD", "₹": "INR"}
_AMOUNT = re.compile(
    r"(?:(?P<code>[A-Z]{3})\s*(?P<a1>\d[\d,]*(?:\.\d{1,2})?)"
    r"|(?P<sym>[£€$₹])\s*(?P<a2>\d[\d,]*(?:\.\d{1,2})?))")

# What the amount beside it is. "net" is what we paid the partner and is NOT
# what the guest was charged — reading one for the other is how a card tells a
# guest they were charged 450 when they paid 606.
_NET = re.compile(r"\bnet\b", re.I)
_PER_UNIT = re.compile(r"\b(?:per\s+(?:person|adult|pax|ticket|head)|each|pp)\b", re.I)


def amounts_in(text: str) -> list:
    """[(currency, value, context)] for every money figure in a body."""
    out = []
    body = str(text or "")
    for m in _AMOUNT.finditer(body):
        cur = m.group("code") or _SYMBOL.get(m.group("sym") or "", "")
        raw = m.group("a1") or m.group("a2") or ""
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        window = body[max(0, m.start() - 40):m.end() + 40]
        out.append((cur, val, window))
    return out


def unit_price_from_text(texts) -> tuple:
    """(price, currency, why) — the per-ticket price stated in ticket text.

    Only an amount explicitly marked per-person is used. An unlabelled figure
    in a ticket is the TOTAL far more often than the unit price, and guessing
    wrong flips the answer.
    """
    for t in (texts or []):
        for cur, val, ctx in amounts_in(t):
            if _NET.search(ctx):
                continue          # what we paid the partner, not the guest
            if _PER_UNIT.search(ctx):
                return val, cur, "a per-person amount stated on the ticket"
    return None, "", ("no per-person amount is stated on any ticket — the "
                      "amounts there are totals")


def check_overcharge(booking: dict, ticket_texts=None) -> dict:
    """Whether the total matches the pax count, and WHICH source settled it.

    Returns {verdict, detail, source} where verdict is:
      "charged_for_more"  — the total is a clean multiple of the unit price
                            above the pax count. The guest is right.
      "matches"           — total ≈ unit × pax. The guest is wrong.
      "unestablished"     — no unit price anywhere. NOT a verdict either way,
                            and the card must say so rather than fall back on
                            the booking record agreeing with itself.
    """
    b = booking if isinstance(booking, dict) else {}
    total = None
    for k in ("amount", "total", "totalAmount", "total_amount", "gross_amount",
              "booking_amount"):
        try:
            v = float(str(b.get(k)).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if v > 0:
            total = v
            break
    try:
        pax = int(b.get("pax") or b.get("paxCount") or b.get("pax_count") or 0)
    except (TypeError, ValueError):
        pax = 0

    unit, cur, why = unit_price_from_text(ticket_texts)
    if total is None or not pax or unit is None:
        missing = []
        if total is None:
            missing.append("no booking total")
        if not pax:
            missing.append("no pax count")
        if unit is None:
            missing.append(why)
        return {"verdict": "unestablished", "source": "",
                "detail": ("The booking record cannot settle this on its own — "
                           "it says how many tickets we recorded, not what one "
                           "costs. " + "; ".join(missing) + ".")}

    expected = unit * pax
    # A tolerance, because fees and rounding move the total by small amounts.
    # Deliberately tight: a 2% drift is rounding, a 100% drift is a second
    # ticket, and nothing real sits between them.
    if abs(total - expected) <= max(0.02 * expected, 1.0):
        return {"verdict": "matches", "source": "zendesk",
                "detail": (f"{cur} {total:.2f} for {pax} at {cur} {unit:.2f} "
                           f"each — the total matches the pax count.")}
    mult = total / expected if expected else 0
    if mult >= 1.9:
        return {"verdict": "charged_for_more", "source": "zendesk",
                "detail": (f"{cur} {total:.2f} against {cur} {unit:.2f} each "
                           f"for {pax} — that is {mult:.1f}x what {pax} should "
                           f"cost.")}
    return {"verdict": "unestablished", "source": "zendesk",
            "detail": (f"{cur} {total:.2f} against {cur} {unit:.2f} each for "
                       f"{pax} does not divide cleanly — it is neither the "
                       f"pax count nor a clean multiple of it, so this needs a "
                       f"human.")}
