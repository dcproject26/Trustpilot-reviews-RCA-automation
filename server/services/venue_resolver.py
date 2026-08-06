"""
BQ-backed venue hint → TGID resolver.
Returns the union of experience_ids matching any of the hint strings.
"""
import logging
import re
from server.services import bq_connector as bq

log = logging.getLogger(__name__)

_TABLES = [
    "headout-analytics.analytics_reporting.dim_experiences",
    "headout-analytics.shivam_reporting.dim_experiences",
]
_FALLBACK_SQL = """
    SELECT DISTINCT experience_id, experience_name
    FROM `headout-analytics.analytics_reporting.fct_bookings`
    WHERE LOWER(experience_name) LIKE CONCAT('%', @hint, '%')
    LIMIT 100
"""
_WORKING_TABLE: str | None = None

# Words that appear in half the catalogue and identify nothing. A hint
# resolved on "tickets" or "tour" matches every experience Headout sells,
# which is the same as matching none — except that it looks like a hit.
_GENERIC = {
    "ticket", "tickets", "tour", "tours", "entry", "entrance", "admission",
    "pass", "passes", "skip", "line", "queue", "guided", "premium", "premo",
    "standard", "combo", "and", "the", "with", "for", "from", "into", "our",
    "day", "hour", "hours", "visit", "experience", "experiences", "booking",
    "bookings", "adult", "child", "guide", "audio", "self", "access", "all",
    "inclusive", "package", "trip", "show", "museum",
}


def _place_words() -> set:
    """City and country names, from the ONE vocabulary that already exists.

    server/bid_indicator_check.py::CITIES is the city list this project keeps
    and extends from real misses. A second copy here would be a fifth team
    vocabulary — the defect that produced the owner bug — so it is read, not
    duplicated. Country names are added because the model writes
    "Rome, Italy".
    """
    out = {"italy", "france", "spain", "portugal", "germany", "austria",
           "netherlands", "belgium", "england", "scotland", "ireland",
           "greece", "turkey", "croatia", "hungary", "czechia", "poland",
           "switzerland", "denmark", "sweden", "norway", "finland", "iceland",
           "morocco", "egypt", "india", "thailand", "vietnam", "singapore",
           "japan", "china", "mexico", "brazil", "argentina", "peru",
           "australia", "canada", "america", "emirates", "dubai"}
    try:
        from server.bid_indicator_check import CITIES
        out |= {c.lower() for c in CITIES}
    except Exception:                       # never break matching over a list
        pass
    return out


_PLACES = _place_words()


def venue_tokens(hint: str) -> list[str]:
    """The words in a hint that could actually identify a venue.

    THE BUG. The resolver matched the WHOLE hint string against
    experience_name with LIKE '%...%'. Guests do not write venue names; they
    write sentences. "premo tickets for collosseum" never matched anything,
    and the card reported "Venues extracted but no TGIDs resolved" while
    ranking fell back to date proximity — which offered a German water park
    and a New York observatory for a review about the Colosseum.

    So: resolve on the significant words INSIDE the phrase. Generic travel
    vocabulary is dropped, because a hint resolved on "tickets" matches the
    entire catalogue, and short words are dropped because three or four
    characters inside a LIKE '%..%' matches by accident.

    Longest first: the most specific token is the one most likely to be the
    venue, and it is tried before the vaguer ones.
    """
    words = [w for w in re.findall(r"[a-z0-9]+", (hint or "").lower())
             if w not in _GENERIC]

    # ADJACENT PAIRS FIRST. Plenty of venues are two words and the second one
    # is short: "London Eye" tokenised to single words of five characters or
    # more leaves "london", which is a CITY — it would go looking for a TGID
    # called London and match a great many wrong products. The pair is a far
    # more specific probe than either half, so it is tried first.
    # A pair of two PLACE names is not a venue either — "rome italy" names a
    # country in a city, not a thing to book. Dropped, so the resolver reports
    # honestly that no venue was named rather than probing for a product
    # called Italy.
    pairs = [f"{a} {b}" for a, b in zip(words, words[1:])
             # At least one half must be substantial: "zoo spa" is two words
             # and identifies nothing, while "london eye" earns its place on
             # the strength of "london".
             if len(a) >= 3 and len(b) >= 3 and max(len(a), len(b)) >= 5
             # A word repeated is not a pair.
             and a != b
             and not (a in _PLACES and b in _PLACES)]

    # A BARE PLACE NAME IS NOT A VENUE. The city is extracted separately and
    # has its own use; resolving on it either misses or, worse, hits an
    # unrelated product that happens to carry the country's name. It is only
    # dropped as a LONE probe — "london eye" above keeps it, because there the
    # place name is qualified by the thing it names.
    singles = [w for w in words if len(w) >= 5 and w not in _PLACES]

    # Deduplicate; pairs before singles, longest first within each group.
    seen, out = set(), []
    for w in (sorted(pairs, key=len, reverse=True)
              + sorted(singles, key=len, reverse=True)):
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out




def _probe_table(table: str, hint: str) -> list[dict] | None:
    try:
        rows = bq.run_query(
            f"SELECT DISTINCT experience_id, experience_name FROM `{table}` "
            f"WHERE LOWER(experience_name) LIKE CONCAT('%', @hint, '%') LIMIT 100",
            params={"hint": hint},
        )
        return rows
    except Exception:
        return None


# A guest misspelling a venue is the norm, not the exception ("collosseum",
# "sagrada familja", "eifel tower"). An exact LIKE will keep missing them.
#
# BUT A LOOSE VENUE MATCH IS WORSE THAN NONE: it produces a confident wrong
# booking instead of admitting defeat, and a wrong booking confirmed by an
# associate poisons the whole RCA. So the tolerance is deliberately narrow and
# is only ever a SECOND pass, after the exact match for that token found
# nothing.
FUZZY_MIN_LEN = 7          # "colosseum" qualifies; "italy", "paris" do not
FUZZY_MAX_EDITS = 2


# Why the last resolve() came back empty. Read by the pipeline so the card can
# say WHICH kind of nothing this was; a dict rather than a return value because
# resolve() already has a meaningful None and the callers read it positionally.
last_failure: dict = {"why": "", "tokens": [], "table": ""}

# The CATALOGUE SPELLING of whatever the last resolve() matched.
#
# The guest writes "premo tickets for collosseum". The spelling-tolerance pass
# reaches `Colosseum` in the catalogue and returns its TGID — and the corrected
# spelling, the single most useful thing that pass produced, was thrown away
# with the rest of the row, because the query selected experience_id alone.
# BigQuery then got the right venue and Zendesk got "collosseum", which appears
# in no ticket anybody wrote. The half of the search that needed the correction
# most never saw it.
#
# Bounded: a token can match a hundred rows, and a query naming a hundred
# experiences is not a query.
last_resolved_names: list = []
MAX_RESOLVED_NAMES = 5

# Set when EDIT_DISTANCE itself is unavailable — the exact path survives that,
# so without recording it the spelling pass is off with nothing to show for it.
_fuzzy_unavailable: str = ""


def explain_failure(probes: list, table, fuzzy_unavailable: str = "") -> str:
    """Which kind of nothing an empty resolve() was.

    A separate function because the classification is the part worth pinning
    and it cannot be reached through `resolve()` anywhere BigQuery is
    unreachable — the error handler clears `_WORKING_TABLE` first, so every
    branch below collapses into the second one and a test of resolve() would
    assert nothing while looking thorough. Driven directly instead.

    Order matters. Each branch rules out a reason the NEXT one would otherwise
    claim: no token means nothing was searched, so nothing about the table is
    relevant; no table means nothing was searched either way; the fallback
    table means the spelling pass never ran, which must not be reported as
    EDIT_DISTANCE having failed.
    """
    if not probes:
        return ("no usable venue token — the hint was a city, a bare place "
                "name or filler")
    if not table:
        return ("no experience table could be reached, so nothing was looked "
                "up at all")
    if str(table).startswith("fallback:"):
        return (f"on the fallback table ({table}), where the "
                f"spelling-tolerance pass does not run — a MISSPELLED venue "
                f"cannot resolve here, however close it is")
    if not any(fuzzy_budget(t) for t in probes):
        return ("every token is too short for the spelling pass, so only an "
                "exact match could have worked")
    if fuzzy_unavailable:
        return f"the spelling pass could not run: {fuzzy_unavailable}"
    return ("looked up exactly and with spelling tolerance; the catalogue has "
            "no such experience")


def fuzzy_budget(token: str) -> int:
    """How many edits this token may differ by, or 0 for none at all.

    Short words are excluded outright: at five characters an edit distance of
    two reaches a large part of the dictionary, and "roma"/"rome"/"rope" are
    all one apart. Long words carry enough signal that two edits is still a
    strong claim.
    """
    n = len(token or "")
    if n < FUZZY_MIN_LEN:
        return 0
    # Never more than a quarter of the word, so the tolerance grows with the
    # evidence rather than with our optimism.
    return max(1, min(FUZZY_MAX_EDITS, n // 4))


_FUZZY_SQL = """
    SELECT DISTINCT experience_id, experience_name
    FROM `{table}`
    WHERE EXISTS (
      SELECT 1 FROM UNNEST(SPLIT(LOWER(experience_name), ' ')) w
      WHERE LENGTH(w) >= @minlen AND EDIT_DISTANCE(w, @hint) <= @budget
    )
    LIMIT 100
"""


def _fuzzy_rows(table: str, hint: str) -> list | None:
    """Second-pass lookup for a misspelled token. None when it cannot run.

    None and [] are different answers and the caller treats them differently:
    None is "this pass did not happen", [] is "it ran and matched nothing".
    """
    budget = fuzzy_budget(hint)
    if not budget or not table or table.startswith("fallback:"):
        return None
    try:
        return bq.run_query(
            _FUZZY_SQL.format(table=table),
            params={"hint": hint, "budget": budget,
                    "minlen": max(FUZZY_MIN_LEN - 2, 4)},
        )
    except Exception as e:
        # EDIT_DISTANCE is not available on every BigQuery edition. A missing
        # function must not take the exact-match path down with it.
        global _fuzzy_unavailable
        _fuzzy_unavailable = str(e)[:120]
        log.info(f"venue_resolver: fuzzy pass unavailable for {hint!r}: {e}")
        return None


async def resolve(venue_hints: list[str] | None) -> list[int] | None:
    """Resolve venue hints → sorted union of TGIDs. None if nothing resolved.

    Each hint is broken into candidate tokens (see venue_tokens) and each
    token is queried, rather than the whole phrase being matched verbatim.
    A token that resolves to an implausible number of experiences is DROPPED
    rather than unioned in: it is not identifying a venue, it is matching the
    catalogue, and letting it through is how a shortlist fills with bookings
    from the wrong continent.
    """
    global _WORKING_TABLE
    # Cleared per call. A catalogue spelling left over from the previous
    # review would be searched against this one — a wrong venue presented with
    # the confidence of a resolved one.
    last_resolved_names.clear()
    if not venue_hints:
        return None
    all_tgids: set[int] = set()
    probes: list[str] = []
    for raw_hint in venue_hints:
        probes.extend(venue_tokens(raw_hint))
    # Falling back to the whole phrase would reintroduce the bug for any hint
    # whose only words are generic, and those hints identify nothing anyway.
    for hint in probes:
        if not hint:
            continue
        rows = None
        if _WORKING_TABLE:
            try:
                rows = bq.run_query(
                    f"SELECT DISTINCT experience_id, experience_name FROM `{_WORKING_TABLE}` "
                    f"WHERE LOWER(experience_name) LIKE CONCAT('%', @hint, '%') LIMIT 100",
                    params={"hint": hint},
                )
            except Exception as e:
                log.warning(f"venue_resolver: cached table {_WORKING_TABLE} failed: {e}")
                _WORKING_TABLE = None
                rows = None

        if rows is None and _WORKING_TABLE is None:
            for tbl in _TABLES:
                rows = _probe_table(tbl, hint)
                if rows is not None:
                    _WORKING_TABLE = tbl
                    log.info(f"venue_resolver: dim_experiences found at {tbl}")
                    break
            if rows is None:
                try:
                    rows = bq.run_query(_FALLBACK_SQL, params={"hint": hint})
                    _WORKING_TABLE = "fallback:fct_bookings"
                    log.info("venue_resolver: using fct_bookings fallback for dim_experiences")
                except Exception as e2:
                    log.warning(f"venue_resolver: fallback also failed for '{hint}': {e2}")
                    rows = []

        # A token matching most of the catalogue is not a venue signal. The
        # LIMIT is 100, so 100 rows back means "at least 100" — indistinguishable
        # from a wildcard, and treated as one.
        _ids, _names = [], []
        for r in rows or []:
            eid = r.get("experience_id")
            if eid is not None:
                try:
                    _ids.append(int(eid))
                except (TypeError, ValueError):
                    pass
            nm = str(r.get("experience_name") or "").strip()
            if nm:
                _names.append(nm)
        if len(_ids) >= 100:
            log.info(f"venue_resolver: token {hint!r} matched {len(_ids)}+ "
                     f"experiences — not discriminating, dropped")
            continue
        if not _ids:
            # Exact match found nothing. Try the narrow misspelling pass.
            _fz = _fuzzy_rows(_WORKING_TABLE, hint)
            for r in _fz or []:
                eid = r.get("experience_id")
                if eid is not None:
                    try:
                        _ids.append(int(eid))
                    except (TypeError, ValueError):
                        pass
                nm = str(r.get("experience_name") or "").strip()
                if nm:
                    _names.append(nm)
            if _ids:
                log.info(f"venue_resolver: {hint!r} resolved only by spelling "
                         f"tolerance (budget {fuzzy_budget(hint)}) → {len(_ids)} tgid(s)"
                         + (f", catalogue spelling {_names[0]!r}" if _names else ""))
            if len(_ids) >= 100:
                continue
        all_tgids.update(_ids)
        # Only the names of tokens that actually RESOLVED. Collecting them
        # before the two `continue`s above would hand Zendesk the name of a
        # match this function decided not to trust.
        for nm in _names:
            if nm not in last_resolved_names:
                last_resolved_names.append(nm)

    if not all_tgids:
        # WHY nothing resolved — see explain_failure. "Venues extracted but
        # no TGIDs resolved" was
        # one sentence for three different situations, and only the first is a
        # genuine miss:
        #   - exact found nothing AND the spelling pass ran and found nothing
        #   - the spelling pass NEVER RAN, because we are on the fallback
        #     table, where _fuzzy_rows returns None by design
        #   - the spelling pass FAILED, because EDIT_DISTANCE is not available
        #     on this BigQuery edition
        # The last two are the mechanism being off, not the venue being
        # absent, and a card that cannot tell them apart costs an afternoon.
        last_failure["tokens"] = probes
        last_failure["table"] = _WORKING_TABLE or "none found"
        last_failure["why"] = explain_failure(probes, _WORKING_TABLE,
                                              _fuzzy_unavailable)
    return sorted(all_tgids) if all_tgids else None
