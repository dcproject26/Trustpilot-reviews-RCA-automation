"""Score the LIVE classifier against human labels held in a Google Sheet.

This is the measurement half of the training loop. A round is: label a batch,
run the classifier we actually ship over it, and count where it disagrees with
the labels — bucketed by WHAT would fix each miss, because a wrong L1 and a
wrong sub-theme are repaired by different people in different files.

Two things this module refuses to let blur together, both instances of the
first rule in CLAUDE.md:

  * a review that was SCORED and matched nothing (a real miss) versus a review
    that could NOT be scored (no label on file, or the model call failed). The
    summary counts them in separate lines. A run where the model was down and a
    run where the model was perfect must not both report "0 misses".

  * a sub-theme the model got WRONG versus a sub-theme it could never have got
    right because the label CX uses has no home in the taxonomy. The first is a
    model/example gap; the second is a taxonomy or validator gap, and calling it
    a model miss sends the fix to the wrong place.

Everything here is driveable without a live sheet or a live model: the sheet
IO and the classifier are injected. The scoring, the header detection and the
write-back plan are pure, because docs.google.com and the Anthropic endpoint
are both unreachable from some of the environments this has to be tested in.
"""
from __future__ import annotations

import logging
from datetime import datetime

from server.taxonomy import has_sub_theme_framework, is_valid_sub_theme

log = logging.getLogger(__name__)


# ── the sheet's column contract ─────────────────────────────────────────────
#
# Detected by header NAME, not by position, and case/space-insensitively, so a
# sheet a human laid out ("Sub Theme", "L1 ") is read without them having to
# match an internal spelling. Each logical field accepts a few honest aliases;
# the first column whose normalised name is in the alias set wins.

_ALIASES = {
    "review_id":  ("review_id", "reviewid", "id", "tp_id"),
    "review":     ("review", "review_text", "reviewtext", "text", "body",
                   "review_summary", "reviewsummary", "summary"),
    "l1":         ("l1", "l1_category", "l1category"),
    "l2":         ("l2", "l2_category", "l2category", "sub_category",
                   "subcategory"),
    "sub_theme":  ("sub_theme", "subtheme", "sub theme", "sub_themes",
                   "subthemes"),
    "booking":    ("booking", "booking_json", "bookingjson"),
    "timeline":   ("timeline", "timeline_json", "timelinejson"),
}

# What the audit writes back. Appended to the right of whatever the sheet holds,
# under these names, so an Apps Script (or a person) reads results off columns
# it can find by header rather than by guessing an offset.
RESULT_COLS = ["pred_l1", "pred_l2", "pred_sub_theme",
               "l1_ok", "l2_ok", "sub_ok", "miss_bucket",
               "pred_warnings", "audited_at"]

# The four places a miss can be fixed. Assigned from the data alone — which can
# tell a boundary error from a taxonomy hole, but CANNOT split "write a rule"
# from "add an example" inside a boundary, because that is a human judgement
# about whether the distinction is one anybody can put into words. So the
# boundary buckets say "boundary" and leave that call to the person reading.
BUCKET_L1L2      = "l1l2-boundary"     # wrong L1 or L2 — needs a rule or example
BUCKET_SUB       = "sub-boundary"      # right L1/L2, wrong sub — example gap
BUCKET_TAXONOMY  = "taxonomy-gap"      # the label has no framework to live in
BUCKET_VALIDATOR = "validator-gap"     # framework exists but rejects CX's label
BUCKET_NONE      = ""                  # nothing wrong


def _norm(s) -> str:
    """Compare labels without tripping on case or incidental whitespace.

    "A. AG Language Issues" and "a.  ag language issues " are the same label;
    a raw `==` would score the second as a miss and send someone hunting for a
    model error that is really two spaces."""
    return " ".join(str(s or "").strip().split()).lower()


def _canon(s) -> str:
    """A header name reduced to its logical key: lowercased, whitespace
    collapsed, and spaces folded to underscores so "Sub Theme", "sub_theme"
    and "sub  theme" are one column. Kept apart from `_norm` because label
    comparison must NOT fold spaces into a review's own words."""
    return _norm(s).replace(" ", "_")


def detect_columns(header: list) -> tuple[dict, list]:
    """(field -> 0-based column index, list of problems in words).

    The problems list is the whole point of returning a tuple rather than a
    bare map. A header with no `l1` column and a header with an empty sheet
    both yield "nothing to score", and only this can say which — one is a
    sheet laid out wrong, the other is a sheet not filled in yet."""
    norm_header = [_canon(h) for h in (header or [])]
    found = {}
    for field, aliases in _ALIASES.items():
        for i, name in enumerate(norm_header):
            if name in aliases and field not in found:
                found[field] = i
    problems = []
    if "review" not in found and "review_id" not in found:
        problems.append(
            "no review column and no review_id column — there is nothing to "
            "send to the classifier. Add a column named one of: "
            + ", ".join(_ALIASES["review"][:4]))
    if "l1" not in found:
        problems.append(
            "no l1 column, so L1 accuracy cannot be measured at all. The run "
            "will classify but score nothing.")
    for opt in ("l2", "sub_theme"):
        if opt not in found:
            problems.append(
                f"no {opt} column — {opt} accuracy will not be measured "
                f"(this is a narrower run, not a broken one).")
    return found, problems


def truth_of(row: list, cols: dict) -> dict:
    """The human label on one row: {review_id, review, l1, l2, sub_theme, ...}.

    A short row (trailing empties that Sheets omits) must not IndexError — a
    labelled review with a blank sub-theme is the commonest row there is."""
    def cell(field):
        i = cols.get(field)
        return (row[i] if i is not None and i < len(row) else "") or ""
    return {f: str(cell(f)).strip() for f in _ALIASES}


# ── scoring ─────────────────────────────────────────────────────────────────

def score_one(truth: dict, pred: dict, ran: bool = True,
              fail_reason: str = "") -> dict:
    """One review's verdict. Levels not labelled score None, not False.

    None means "could not be scored here" — no label at this level, or the
    model never answered. False means "scored and wrong". Collapsing the two
    is exactly the bug this whole file exists to prevent: a column of Falses
    that is really a column of "no label" reads as a broken model."""
    v = {"l1_ok": None, "l2_ok": None, "sub_ok": None,
         "miss_bucket": BUCKET_NONE, "scored": False, "note": ""}

    if not ran:
        v["note"] = fail_reason or "classifier did not run on this row"
        v["miss_bucket"] = ""            # not a miss — an absence. Kept distinct.
        return v

    t_l1, t_l2, t_sub = truth["l1"], truth["l2"], truth["sub_theme"]
    p_l1 = pred.get("l1", "") or ""
    p_l2 = pred.get("l2", "") or ""
    p_sub = pred.get("sub_theme", "") or ""

    # L1 — only scorable if the row carries an L1 label.
    if _norm(t_l1):
        v["scored"] = True
        v["l1_ok"] = _norm(t_l1) == _norm(p_l1)

    # L2 — scorable only under a scored, correct L1.
    if v["l1_ok"] and _norm(t_l2):
        v["l2_ok"] = _norm(t_l2) == _norm(p_l2)

    # Sub-theme — scorable only under a correct L1+L2 AND a sub-theme label.
    if v["l2_ok"] and _norm(t_sub):
        v["sub_ok"] = _norm(t_sub) == _norm(p_sub)

    # ── bucket the miss ─────────────────────────────────────────────────
    if v["l1_ok"] is False or v["l2_ok"] is False:
        v["miss_bucket"] = BUCKET_L1L2
    elif v["sub_ok"] is False:
        # The L1/L2 are right, so we can ask the taxonomy where the LABEL was
        # meant to go — and distinguish a model error from a hole in the map.
        if not has_sub_theme_framework(t_l1, t_l2):
            v["miss_bucket"] = BUCKET_TAXONOMY
        elif not is_valid_sub_theme(t_l1, t_l2, t_sub):
            # A framework exists and CX's own label is not in it. The model
            # could not have produced it and been kept; the fix is the sheet
            # or the framework, not the prompt.
            v["miss_bucket"] = BUCKET_VALIDATOR
        else:
            v["miss_bucket"] = BUCKET_SUB
    return v


def summarize(scored: list) -> dict:
    """Accuracy per level, plus everything that did NOT count and why.

    Each rate is over the rows SCORABLE at that level, and the denominator is
    reported next to it — 78% of 40 and 78% of 400 are not the same claim, and
    a rate with no denominator invites reading it as the second when it is the
    first."""
    total = len(scored)
    ran = [s for s in scored if s.get("scored")]
    could_not_run = [s for s in scored if not s.get("scored")
                     and s.get("note")]
    # Rows that ran but carried no label at any level — classified, unscorable.
    no_label = total - len(ran) - len(could_not_run)

    def rate(key):
        graded = [s for s in ran if s.get(key) is not None]
        hits = [s for s in graded if s[key]]
        return {"pct": (round(100 * len(hits) / len(graded), 1)
                        if graded else None),
                "hits": len(hits), "of": len(graded)}

    buckets = {}
    for s in ran:
        b = s.get("miss_bucket")
        if b:
            buckets[b] = buckets.get(b, 0) + 1

    return {
        "rows_total":        total,
        "rows_scored":       len(ran),
        "rows_no_label":     no_label,
        "rows_failed":       len(could_not_run),
        "failures":          [s.get("note") for s in could_not_run][:20],
        "l1":                rate("l1_ok"),
        "l1_l2":             rate("l2_ok"),
        "sub":               rate("sub_ok"),
        "miss_buckets":      buckets,
    }


# ── write-back plan ─────────────────────────────────────────────────────────

def _col_letter(idx0: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA. The Sheets A1 grammar, for a result column
    that lands past Z on any real export."""
    s, n = "", idx0
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            return s


def result_columns(header: list) -> dict:
    """RESULT_COLS -> 0-based column index, reusing any already in the header.

    A second audit run must overwrite the first run's result columns, not open
    a fresh block beside them — otherwise the sheet grows a new pred_l1 every
    time and no reader can tell which is current. Columns already present keep
    their place; only genuinely new ones are appended."""
    have = [_canon(h) for h in (header or [])]
    nxt = len(header or [])
    out = {}
    for c in RESULT_COLS:
        if _canon(c) in have:
            out[c] = have.index(_canon(c))
        else:
            out[c] = nxt
            nxt += 1
    return out


def cells_for(verdict: dict, pred: dict, now: datetime) -> dict:
    """One row's result cells, keyed by RESULT_COLS name.

    A None verdict renders "" (not "None"): the level was not scored, and a
    spreadsheet has no other way to say that than an empty cell — "None" reads
    as a value somebody typed."""
    def yn(v):
        return "" if v is None else ("yes" if v else "no")
    warnings = pred.get("warnings") or []
    return {
        "pred_l1":        pred.get("l1", "") or "",
        "pred_l2":        pred.get("l2", "") or "",
        "pred_sub_theme": pred.get("sub_theme", "") or "",
        "l1_ok":          yn(verdict.get("l1_ok")),
        "l2_ok":          yn(verdict.get("l2_ok")),
        "sub_ok":         yn(verdict.get("sub_ok")),
        "miss_bucket":    verdict.get("miss_bucket") or (
                              verdict.get("note") and "did-not-run" or ""),
        "pred_warnings":  "; ".join(warnings) or (verdict.get("note") or ""),
        "audited_at":     now.isoformat(sep=" ", timespec="minutes"),
    }


def plan_writeback(header: list, rows_verdicts: list, now: datetime,
                   defuse=lambda s: s) -> tuple[list, dict]:
    """(ranges to write, {result col -> A1 header cell}) for one batch.

    ranges is [(a1_range, [cells])] ready for a single batchUpdate: the header
    cells for the result columns, then one contiguous span per data row. The
    span is written whole (all result columns at once) because they are laid
    out contiguously, which keeps it one write per row instead of nine."""
    cols = result_columns(header)
    start = min(cols.values())
    end = max(cols.values())
    ordered = sorted(cols, key=lambda c: cols[c])   # RESULT_COLS by column pos

    ranges = []
    # Header row for the result columns.
    hdr_cells = [defuse(c) for c in ordered]
    ranges.append((f"{_col_letter(start)}1:{_col_letter(end)}1", hdr_cells))

    header_map = {c: f"{_col_letter(cols[c])}1" for c in RESULT_COLS}

    for data_row_idx, (verdict, pred) in enumerate(rows_verdicts):
        rownum = data_row_idx + 2      # +1 header, +1 to 1-based
        cmap = cells_for(verdict, pred, now)
        line = [defuse(str(cmap.get(c, ""))) for c in ordered]
        ranges.append(
            (f"{_col_letter(start)}{rownum}:{_col_letter(end)}{rownum}", line))
    return ranges, header_map
