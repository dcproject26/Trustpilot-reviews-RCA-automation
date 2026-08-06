"""Was the guest charged for one ticket or two?

THE CIRCULAR ANSWER. A review said "booked one ticket, charged for 2". The RCA
answered Inaccurate because "Booking 32142070 records one adult, CHF 461.19
total, no add-ons" — the booking record confirming its own pax count. That
proves nothing: the question is not how many tickets the record says, it is
whether CHF 461.19 is the price of ONE of them or TWO.

Settling it needs a UNIT price, which the booking total and pax count cannot
supply on their own. ONE source is checked: the ZENDESK ticket text, where the
booking dump and confirmation emails routinely state per-person amounts.

THE EXPERIENCE PAGE IS DELIBERATELY NOT A SOURCE. It was going to be, and it
would have been wrong twice over: the page is keyed on TGID while the price
depends on the TID (the variant actually bought), so reading "the price" off
the page gives the wrong number for any non-default variant — which is exactly
the booking someone disputes. There is also no tid → variant-price mapping in
this repo and no route to the host from the runtime.

SO THE ANSWER WHEN ZENDESK DOES NOT SETTLE IT IS "we could not find this —
check manually", and it says that in those words. Not "no data was supplied",
not a list of fields that came back empty: those read as broken plumbing, and
a reader who thinks the check is broken stops reading it. The reasons live on
the trail, where someone fixing the check will look; the card gets the
sentence a human can act on.

A guest wrongly told they were not double-charged is worse than one told we
could not tell.
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


# DELIBERATELY NARROW. This gate can only ever DEMOTE a verdict to Unknown, so
# a false positive silently turns a settled answer into "check manually" — the
# expensive direction. It fires on the shapes a double-charge complaint
# actually takes and on nothing else: a claim merely containing "refund" and
# "two" is not one of them.
_OVERCHARGE_CLAIM = re.compile(
    r"\b(?:over[-\s]?charg\w+"
    r"|double[-\s]?(?:charg\w+|bill\w+|paid|payment)"
    r"|(?:charg\w+|bill\w+|paid|pay)\s+(?:me\s+|us\s+)?(?:twice|double)"
    r"|(?:charg\w+|bill\w+|paid)\s+(?:me\s+|us\s+)?for\s+(?:\d+|two|three|both)"
    r")", re.I)


def is_amount_claim(text: str) -> bool:
    """Whether this claim is one the unit price would settle."""
    return bool(_OVERCHARGE_CLAIM.search(str(text or "")))


def gate_amount_claim(claim, accuracy, booking, ticket_texts):
    """(new_accuracy, note) when a money verdict outran its evidence, else None.

    THE VERDICT THIS EXISTS TO STOP is the one that shipped: "Inaccurate —
    booking 32142070 records one adult, CHF 461.19 total", which contradicts a
    guest on the strength of the record repeating itself. Deciding an amount
    claim needs a UNIT price, and where the Zendesk case does not state one the
    honest verdict is Unknown.

    A RULE IN THE PROMPT CANNOT DO THIS. It was tried — rule 4g asks the model
    for exactly this behaviour, and asking is what stored drafts ignore. So the
    check runs here, in code, on the way out.

    Returns None when the claim is not about money, when the verdict is already
    Unknown, or when the case DOES settle it — the ordinary path, and it has to
    be distinguishable from the gate not running at all. `validate()` puts every
    returned note on the confidence trail as a warn, which is where a reader
    finds out a verdict was changed.
    """
    if not is_amount_claim(claim):
        return None
    if str(accuracy or "").strip().lower() in ("", "unknown"):
        return None                    # already unsettled; nothing to demote
    got = check_overcharge(booking, ticket_texts)
    if got["verdict"] != "unestablished":
        # The case settled it. If it settled it the OTHER WAY from the model,
        # that is not a coercion but it is worth a line: two answers to one
        # question, and the reader should know they disagreed.
        _guest_right = got["verdict"] == "charged_for_more"
        _model_says_guest_right = str(accuracy).strip().lower().startswith(
            ("accurate", "partly"))
        if _guest_right != _model_says_guest_right:
            return (accuracy,
                    f"claim_accuracy is {accuracy} but the Zendesk amounts say "
                    f"otherwise — {got['detail']} Verdict left as written; the "
                    f"two disagree and a human should settle it")
        return None
    return ("Unknown",
            f"claim_accuracy {accuracy} → Unknown — an amount claim is not "
            f"settled by the booking record agreeing with itself, and "
            f"{'; '.join(got['unsettled_because'])}. Could not verify this "
            f"from the Zendesk case — check manually")


# Pax as a LABELLED BOOKING FIELD only. A bare "2" beside "Adults" in prose is
# as likely to be the disputed quantity as the recorded one, and reading the
# wrong one inverts the answer. These are the shapes a booking dump uses.
_PAX_IN_TEXT = re.compile(
    r"(?:\bpax\b|no\.?\s*of\s*(?:guests?|pax|tickets?)|quantity|qty)\s*[:\-]\s*(\d{1,2})"
    r"|(\d{1,2})\s*x?\s*adults?\b", re.I)


def pax_from_text(texts) -> tuple:
    """(pax, why) — the recorded guest count, from a labelled field in a dump.

    THE WAREHOUSE HAS NO PAX COLUMN. Not "returns null sometimes" — no query in
    this repo selects one, so without this the arithmetic could never run on a
    real booking and the check would answer "check manually" forever. That is
    barely better than the dead code it replaced.
    """
    for t in (texts or []):
        m = _PAX_IN_TEXT.search(str(t or ""))
        if m:
            raw = m.group(1) or m.group(2)
            try:
                n = int(raw)
            except (TypeError, ValueError):
                continue
            if 0 < n < 50:
                return n, "pax read from a labelled field in the Zendesk case"
    return 0, ("no pax count — this build holds no pax column, and no Zendesk "
               "text states one as a labelled booking field")


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
    # `amountUSD` FIRST, because that is the key this warehouse actually
    # writes (`_get_booking_amount` -> price_payable_usd). The original list
    # held six plausible spellings and not the real one, so every live booking
    # reported "no booking total" — a lookup that never had a chance,
    # indistinguishable from a booking with no amount on it. Same defect as
    # `show_draft --bid` reading `bookingId` off a row keyed `id`.
    for k in ("amountUSD", "amount_usd", "amount", "total", "totalAmount",
              "total_amount", "gross_amount", "booking_amount"):
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
    # The booking record first when it has one; the case text only as a
    # fallback, and the reason records which it was — "we read this off a
    # ticket" is a judgement and the reader should be told one was made.
    pax_why = "pax from the booking record"
    if not pax:
        pax, pax_why = pax_from_text(ticket_texts)

    unit, cur, why = unit_price_from_text(ticket_texts)
    if total is None or not pax or unit is None:
        # WHAT THE READER GETS vs WHAT THE TRAIL GETS. The card says we looked
        # and could not find it, and names the next step. The reasons are real
        # and are kept — on `unsettled_because`, for whoever is fixing the
        # check — but they are not the sentence a reviewer reads, because
        # "no pax count; no booking total" reads as an outage rather than as
        # an answer.
        missing = []
        if total is None:
            missing.append("no booking total on the record")
        if not pax:
            # NOT a bare failed lookup. There is no pax column in any query in
            # this repo, so "we checked and it was empty" would be a lie about
            # a field we never hold. `pax_from_text` says which it is.
            missing.append(pax_why)
        if unit is None:
            missing.append(why)
        return {"verdict": "unestablished", "source": "",
                "unsettled_because": missing,
                "detail": ("Could not verify this from the Zendesk case — "
                           "check manually.")}

    expected = unit * pax
    # A tolerance, because fees and rounding move the total by small amounts.
    # Deliberately tight: a 2% drift is rounding, a 100% drift is a second
    # ticket, and nothing real sits between them.
    if abs(total - expected) <= max(0.02 * expected, 1.0):
        return {"verdict": "matches", "source": "zendesk",
                "unsettled_because": [],
                "detail": (f"{cur} {total:.2f} for {pax} at {cur} {unit:.2f} "
                           f"each — the total matches the pax count.")}
    mult = total / expected if expected else 0
    if mult >= 1.9:
        return {"verdict": "charged_for_more", "source": "zendesk",
                "unsettled_because": [],
                "detail": (f"{cur} {total:.2f} against {cur} {unit:.2f} each "
                           f"for {pax} — that is {mult:.1f}x what {pax} should "
                           f"cost.")}
    return {"verdict": "unestablished", "source": "zendesk",
            "unsettled_because": ["the total is neither the pax count nor a clean multiple of it"],
            "detail": (f"{cur} {total:.2f} against {cur} {unit:.2f} each for "
                       f"{pax} does not divide cleanly — it is neither the "
                       f"pax count nor a clean multiple of it, so this needs a "
                       f"human.")}
