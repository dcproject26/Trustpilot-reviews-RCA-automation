"""
REPLACES existing server/api.py

Keeps the original endpoints (health, signals, list, manual, get, patch, send, reporting)
and adds the demo-parity endpoints:

  POST   /api/reviews/{id}/select-candidate — associate confirms a Tier 2 candidate
  POST   /api/reviews/{id}/close            — move a review to Sent with nothing
                                              to post (untraceable, unconfirmed,
                                              or never processed). /send needs a
                                              draft AND an RCA; this needs
                                              neither, and posts nothing.
  POST   /api/reviews/{id}/connect-dss      — pull DSS on demand
  POST   /api/reviews/{id}/flag-to-biz      — draft + send Slack flag
  PATCH  /api/reviews/{id}/action           — add/edit/delete a single actions_taken row
  PATCH  /api/reviews/{id}/draft-v2         — save v2 fields (bullets, frames, resolution, etc.)
  GET    /api/reviews/{id}/similar          — fetch similar complaints on demand
  GET    /api/taxonomy                      — return L1/L2/checks catalogue (dashboard uses this)
"""
import asyncio, copy, logging, os, re, subprocess, time
from datetime import datetime

log = logging.getLogger(__name__)

from fastapi import (APIRouter, HTTPException, Depends, BackgroundTasks,
                     Header, Response)
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from datetime import timezone as _tz
_STARTED_AT = datetime.now(_tz.utc).isoformat()   # when THIS process booted

from server.db import get_session, Review, RcaDraft, ReviewMetric
from server.taxonomy import L1_CATEGORIES, L2_OPTIONS, DIAGNOSTIC_CHECKS, ACTION_TABS, SUB_THEME_REGISTRY
from server.checklist import SCENARIO_CHECKS
from server.prompts import TAKEDOWN_REASONS
from server.config import status_summary, is_live, MOCK_MODE
from server.services.slack import format_rca_slack, post_to_thread
from server.services.claude import flag_to_biz_message
from server.services.bigquery_patch import get_similar_complaints
from server.services import dss as dss_svc

_START_TIME = time.time()

router = APIRouter()


# ── Pydantic models ─────────────────────────────────────────────────────────

def _received_at_from(slack_ts, rid="", published_at=None, published_src=""):
    """When the review was POSTED, falling back to when it reached the channel.

    THREE DIFFERENT FACTS, and the column used to hold whichever one it could
    get without saying which:

      1. the Trustpilot publish time, from the payload — what "Review date"
         and the timeline's "Review posted" claim to be;
      2. the Slack message timestamp — when the integration relayed it, which
         is minutes later on a good day and hours later on a bad one;
      3. the ingest moment — when we happened to run, which is not a fact
         about the review at all.

    All three rendered identically. A reader comparing the review against a
    ticket raised the same morning was reading a gap that could be wrong by
    the whole relay delay, and nothing on screen said so.

    The publish date wins whenever the payload carries one. Every fallback is
    logged with the source, so "we used the arrival time" is never inferred
    from a column that looks slightly off.
    """
    if published_at is not None:
        log.info(f"[ingest] {rid}: review publish date {published_at} "
                 f"(from {published_src or 'the payload'})")
        return published_at
    log.info(f"[ingest] {rid}: the payload carries no Trustpilot publish date "
             f"— using the Slack arrival time, which is later by however long "
             f"the integration took")
    # `datetime` is module-level; importing it here too would make it a
    # LOCAL for this whole function and break any use above this line.
    from datetime import timezone
    try:
        ts = float(str(slack_ts).strip())
        if 1e9 < ts < 4e9:                      # 2001-2096: a real message
            return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
        log.warning(f"[ingest] {rid}: slack_ts {slack_ts!r} is outside any "
                    f"plausible date range - using the ingest time instead")
    except (TypeError, ValueError):
        log.warning(f"[ingest] {rid}: slack_ts {slack_ts!r} is not a timestamp "
                    f"- using the ingest time instead")
    return datetime.utcnow()


class ManualReview(BaseModel):
    body: str
    rating: int = 1
    author: str | None = None
    reference_number: str | None = None
    slack_channel: str = "C_MANUAL"
    slack_ts: str | None = None


class DraftPatchV1(BaseModel):
    rca_fields:     dict | None = None
    signals:        list | None = None
    final_response: str  | None = None


class DraftPatchV2(BaseModel):
    """Partial update for any of the structured v2 or v3 fields."""
    stated_issue:               str  | None = None
    l1:                         str  | None = None
    l2:                         str  | None = None
    sub_theme:                  str  | None = None
    sub_themes:                 list | None = None
    scenarios:                  list | None = None
    l1_reasoning:               str  | None = None
    primary_scenario:           str  | None = None
    diagnostic_checks:          list | None = None
    what_went_wrong_bullets:    list | None = None
    support_interaction_frames: list | None = None
    support_summary:            str  | None = None
    sp_interaction_frames:      list | None = None
    area_of_improving:          list | None = None
    actions_taken:              dict | None = None
    resolution:                 str  | None = None
    final_response:             str  | None = None
    wwr_chain:                  list | None = None
    wwr_scenarios:              list | None = None
    prevention:                 str  | None = None
    evidence:                   list | None = None
    # v4 made this an array of {question, verdict, evidence, source, ref};
    # v3 sent {question: answer}. Accept either so a client mid-deploy and a
    # draft written before it both still save.
    issue_specific_answers:     list | dict | None = None
    checklist_answers:          list | None = None
    slack_thread_override:      str  | None = None
    # The dashboard edits flags / booking logs / takedown in place, and those
    # live inside the rca_v3 object rather than in columns of their own.
    rca_v3:                     dict | None = None

    # ── RCA v4 ──
    # Editable sections. These are NOT written to the columns of the same name:
    # those are pipeline-written projections of rca_v3, and a second writer is
    # how the two stores drift. A patch here edits rca_v3 - see _V4_SECTIONS.
    #
    # suggested_response is deliberately absent. It is the model's draft; the
    # human's version is final_response, which is already patchable. Two
    # editable stores for one piece of text is the same bug in miniature.
    guest_issues:               list | None = None
    booking_logs:               list | None = None
    flags:                      list | None = None
    takedown:                   dict | None = None
    dss:                        dict | None = None


class CandidateSelect(BaseModel):
    bid: str  # the chosen candidate's booking ID


class ActionPatch(BaseModel):
    tab: str                      # sp | customer | business | product | ce
    op: str                       # add | update | delete
    index: int | None = None      # required for update / delete
    action: dict | None = None    # required for add / update


class FlagToBiz(BaseModel):
    # No channel. This always goes into the review's own Slack thread, the
    # same thread the RCA goes into.
    tag: str        | None = None  # who to tag
    message: str    | None = None  # editable draft
    send: bool = False             # False = save draft; True = send now
    # The facts Biz acts on. Sent as fields rather than buried in the message
    # so they arrive in a fixed shape and can be corrected before sending
    # without editing prose around them.
    completion_rate: float | None = None
    tgid: str       | None = None
    tid: str        | None = None
    vid: str        | None = None


# ── Utility ─────────────────────────────────────────────────────────────────

# Task #4 (sub_theme wiring) — DONE: _draft_dict() returns sub_theme,
# DraftPatchV2 accepts it, and the patch loop persists it. The dashboard
# renders a taxonomy-driven Sub-theme row (options from /api/taxonomy
# sub_theme_frameworks) in the Issue Classification block.

def _scrub_candidate_names(cands):
    """Candidates with any unreadable guest name replaced or removed.

    Uses the SAME `_looks_like_hash` the booking card uses — one rule, so the
    picker and the card can never disagree about whether a name is readable.
    """
    out = []
    for c in (cands or []):
        if not isinstance(c, dict):
            continue
        c = dict(c)
        for key in ("primary_guest_name", "guestName", "guest_name"):
            v = (c.get(key) or "").strip()
            if v and _looks_like_hash(v):
                # The Zendesk copy is a real name where the warehouse holds a
                # digest — that is why the shortlist carries it separately.
                zd = (c.get("zendesk_guest_name") or "").strip()
                c[key] = zd if zd and not _looks_like_hash(zd) else ""
        out.append(c)
    return out


def _looks_like_hash(s: str) -> bool:
    """True for opaque tokens we should never show as a guest name.

    HEX WAS NOT ENOUGH. The warehouse also stores BASE64 digests —
    "jVwe+fjfm48WSok1xEK+I/8fnIoV+kY8P8z7xxk+NM8=" — and this returned False
    for every one of them, so they rendered as the guest's name on the
    candidate picker: the one field an associate recognises the right booking
    by, showing a digest. Base64 was never hex, so no amount of widening the
    hex alphabet would have caught it.

    A name is still a name: anything with a space is left alone, and so is
    anything short. The test is "long, unspaced, and drawn only from an
    encoding alphabet", which no person's name is.

    AND THAT LAST CLAUSE WAS WRONG HERE, LIVE. This required one of `+ / = _`
    before it would call a non-hex string a digest, so a plain ALPHANUMERIC
    digest was not caught — `ab24TSVenneb4T3CkHFUFaGM`, the very value
    `bigquery.py` records as having matched a guest called Sven, reached the
    candidate picker as the guest's name, and `_draft_dict` shipped it with no
    note. The matcher's copy of the rule caught it and this one did not, which
    is the two-implementations failure with a screen on the end of it.

    DELEGATES. See `names.looks_like_digest`.
    """
    from server.names import looks_like_digest
    return looks_like_digest(s)


_ABSENT = object()


def _v4(d, column: str, v3_path: str, default, v3=None):
    """A v4 field, preferring the edited rca_v3 over the denormalised column.

    Both hold the same thing. rca_v3 is what the dashboard writes when someone
    edits a field, and the column is what the pipeline wrote at generation - so
    rca_v3 wins, or an edit would be shadowed by the value it replaced. The
    column is the fallback, for a draft written before the v4 deploy.

    The fallback turns on PRESENCE, not truthiness, and that distinction is the
    whole point. The dangerous value is not a missing one, it is a deliberately
    emptied one: delete the last flag and the dashboard sends flags = [].
    Falling back on falsiness would let the populated column win, so the delete
    would appear to work and then undo itself on the next load. An empty list
    beats a populated column. So does an explicit null.

    `v3` overrides which blob to read. _draft_dict passes the SAME resolved
    blob it ships to the client, so the top-level field and the client's own
    copy cannot answer differently — they are one read, not two reads that
    happen to implement the same rule. See _resolve_v3_sections.
    """
    node = (v3 if isinstance(v3, dict) else d.rca_v3) or {}
    for part in v3_path.split("."):
        if not isinstance(node, dict) or part not in node:
            node = _ABSENT
            break
        node = node[part]
    if node is not _ABSENT:
        # Present-but-null means "there is nothing here", which is an answer.
        # Normalise it to the default's type so the renderer is not handed a
        # None where it expects a list.
        return default if node is None else node
    col = getattr(d, column, None)
    return default if col is None else col


def _resolve_v3_sections(d) -> tuple:
    """rca_v3 with every v4 section resolved the way `_v4()` resolves it.

    TWO STORES FOR ONE FACT. The takedown verdict is the instance that was
    caught: the card's chip renders from `rca.v3.takedown` — the client's only
    copy, taken straight from the `rca_v3` blob — while `_draft_dict` serves a
    separate top-level `takedown` resolved through `_v4()`, which falls back to
    the column. Set the column and nothing else and the two disagree: the
    payload, Slack and the sheet export all say "Yes" and the chip on the card
    says "No". A test that set the column got a chip showing the other store.

    It was never only takedown. All six v4 sections are read by the client out
    of the `rca_v3` blob and by everything else through `_v4()`, so all six
    could diverge the same way — the same shape as the four copies of the team
    vocabulary that produced the owner bug.

    So: ONE store, `rca_v3`, and the column follows it. `project_v4()` already
    makes the column follow rca_v3 on the write path. This is the read path's
    half — where rca_v3 has no such section, the column's value is folded in
    before the blob goes out, so the client's copy and `_v4()`'s answer are the
    same value by construction rather than by two functions agreeing.

    Presence, not truthiness, exactly as `_v4()` does it: a section deliberately
    emptied to `[]` in rca_v3 must beat a populated column, or a delete undoes
    itself on the next load.

    Returns (resolved_v3, columns_folded_in). The second half is the account —
    an empty list means this ran and had nothing to fold, which is the healthy
    case and must not look like the resolver having never run at all.
    """
    src = d.rca_v3 if isinstance(d.rca_v3, dict) else {}
    v3 = copy.deepcopy(src)
    folded = []
    for column, path in _V4_SECTIONS.items():
        # Walk to the parent of the leaf. A missing parent means the section is
        # absent, which is the fallback case.
        node, reached = v3, True
        for part in path[:-1]:
            nxt = node.get(part) if isinstance(node, dict) else None
            if not isinstance(nxt, dict):
                reached = False
                break
            node = nxt
        if reached and isinstance(node, dict) and path[-1] in node:
            continue                     # rca_v3 has an answer; it wins
        col = getattr(d, column, None)
        if col is None:
            continue                     # neither store has it — nothing to fold
        node = v3
        for part in path[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[path[-1]] = copy.deepcopy(col)
        folded.append(column)
    return v3, folded


def _biz_facts(body) -> str:
    """The numbers Biz needs, on one line, in a fixed order.

    They come in as fields rather than as prose the associate has to edit
    around, so what reaches Slack is what was in the boxes.
    """
    bits = []
    if body.completion_rate is not None:
        bits.append(f"Completion {body.completion_rate:g}%")
    for label, val in (("TGID", body.tgid), ("TID", body.tid), ("VID", body.vid)):
        if val and str(val).strip():
            bits.append(f"{label} {str(val).strip()}")
    return " · ".join(bits)


def _bucket_of(d: RcaDraft) -> str:
    """The bucket for a draft whose Review row we already have loaded."""
    from server.tiers import classify
    return classify(getattr(d, "review", None), d)


def _scenario_routing(d: RcaDraft) -> dict:
    """Where the primary came from, and whether it still agrees with routing.

    Provenance and the removed-overlay list live in rca_v3 rather than in
    columns: they are draft state the dashboard writes, and the existing
    data-v3p saver already carries anything under rca_v3 without a migration.
    """
    from server import scenario_override as so
    v3 = d.rca_v3 if isinstance(d.rca_v3, dict) else {}
    sub = ", ".join(d.sub_themes or ([d.sub_theme] if d.sub_theme else []))
    return so.apply(
        d.l1, d.l2, sub or None,
        stored_primary=d.primary_scenario,
        source=v3.get("scenario_source"),
        ticket_facts=d.ticket_facts, booking=d.booking,
        removed=v3.get("overlays_removed"),
        guest_issues=((v3.get("what_went_wrong") or {}).get("guest_issues")
                      if isinstance(v3.get("what_went_wrong"), dict) else None),
        rca_scenarios=v3.get("scenarios"),
    )


def _content_match(d: RcaDraft) -> dict:
    """Whether the review's own words describe the booked experience.

    Additive and non-blocking: it is a line on the match card, never a gate.
    A wrong answer here must not be able to stop a draft rendering, so any
    failure returns the "unchecked" state rather than raising.
    """
    from server import booking_match_check as bmc
    try:
        r = getattr(d, "review", None)
        text = (getattr(r, "body_english", None)
                or getattr(r, "body_original", None) or "") if r else ""
        return bmc.check(text, d.booking)
    except Exception as e:              # never break the card over a hint
        log.warning(f"[content-match] skipped: {e}")
        return {"state": "unchecked", "review_family": None,
                "booking_family": None, "experience": "",
                "why": "the check did not run"}


def _indicator_match(d: RcaDraft) -> dict:
    """Whether the booking this id returned is the trip the review describes.

    A verified BID is where this check is MISSING, not where it is redundant:
    the pipeline skips indicator extraction entirely when a review carries its
    own booking id, so venue, city, date and name are never compared to
    anything. A guest quoting someone else's reference gets a real booking and
    a green trail.

    Same contract as _content_match: additive, non-blocking, a line on the
    match card and never a gate. Any failure returns "unchecked" rather than
    raising — a hint must not be able to stop a draft rendering.
    """
    from server import bid_indicator_check as bic
    try:
        r = getattr(d, "review", None)
        text = (getattr(r, "body_english", None)
                or getattr(r, "body_original", None) or "") if r else ""
        # ticket_facts.guest_full_name is the DATA-SOURCE SPEC's guest name —
        # "BQ guestName is a hash — do NOT use". The card has read it for a
        # while; this check was still comparing the reviewer against the hash,
        # so the strongest identifier after the booking id was thrown away on
        # exactly the rows where it was available.
        return bic.check(text, d.booking,
                         author=getattr(r, "author", None) if r else None,
                         received_at=getattr(r, "received_at", None) if r else None,
                         ticket_facts=getattr(d, "ticket_facts", None))
    except Exception as e:              # never break the card over a hint
        log.warning(f"[indicator-match] skipped: {e}")
        return {"state": "unchecked", "signals": [], "contradictions": [],
                "agreements": [], "checked": 0,
                "why": "the check did not run"}


def _wwr_slack_text(d, v3) -> str:
    """The Slack post's what-went-wrong section, for the dashboard to render.

    Wrapped so a composer failure cannot take the whole draft read with it:
    the card is how someone finds out anything at all about a review, and
    losing it to one malformed what_went_wrong node is worse than showing the
    section with an error in it. The error names the section, so it cannot be
    mistaken for an RCA that had nothing to say.
    """
    try:
        from server.services.wwr_post import compose, compose_legacy
        text = compose((v3 or {}).get("what_went_wrong"))
        if text:
            return text
        # No v4 node. A draft written before that shape still has its analysis
        # in the scenario blocks, and rendering nothing for it would make an
        # old RCA look like a broken composer rather than an old RCA.
        return compose_legacy(getattr(d, "wwr_scenarios", None),
                              getattr(d, "wwr_chain", None))
    except Exception as e:
        log.exception("[wwr] compose failed")
        return f"What went wrong could not be composed: {e}"


def _english_view(d) -> dict:
    """The English box's state, computed server-side so the card cannot reach
    a different answer from the send path about which text is current."""
    try:
        from server.services.reply_language import english_view
        return english_view(getattr(d, "review", None), d)
    except Exception as e:
        log.exception("[reply-language] english_view failed")
        return {"state": "unknown", "text": "", "outgoing": "",
                "why": f"the English view could not be resolved: {e}"}


def _response_language(d) -> dict:
    try:
        from server.services.reply_language import language_state
        return language_state(getattr(d, "review", None))
    except Exception as e:
        log.exception("[reply-language] language_state failed")
        return {"state": "unknown", "language": "",
                "why": f"the review language could not be resolved: {e}"}


def _scrub_timeline(rows, booking):
    """Timeline rows with the supply partner's identity taken out.

    The vendor's own name comes from the booking record rather than being
    guessed, so an unrelated proper noun cannot be eaten. Phone numbers and
    email addresses go regardless of whose they are — a number left in is the
    one thing on this card that could be dialled by mistake.
    """
    from server.ticket_notes import scrub_vendor
    vendor = str((booking or {}).get("vendorName")
                 or (booking or {}).get("vendor_name") or "")
    out = []
    for r in (rows or []):
        if not isinstance(r, dict):
            out.append(r)
            continue
        row = dict(r)
        for k in ("summary", "detail", "label", "raw_body"):
            if row.get(k):
                row[k] = scrub_vendor(row[k], vendor)
        out.append(row)
    return out


def _booking_details_text(d) -> str:
    """The Slack post's Booking details block, for the card's preview.

    Imported from the composer rather than rebuilt, so the preview and the
    posted text cannot disagree about the booking they name.
    """
    from server.services.slack import _booking_details_lines
    return _booking_details_lines(d, "\n")


def _override_is_stale(d) -> bool:
    """Whether the hand-written Slack post predates the analysis it describes.

    THE TRAP THIS OPENS UP. `slack_thread_override` wins over the composer
    everywhere the post is read — the preview, the post-to-thread call, the
    send. That is right: a person's edit must survive a re-render. But nothing
    said when it was written, so one edit made every LATER fix invisible in the
    only text that actually goes out. A card showing corrected contacts and a
    thread carrying the old ones, with no way to tell from either.

    Stale means the RCA moved after the override was saved. An override with
    no timestamp is one written before this column existed: reported stale,
    because "we cannot tell" and "it is current" must not read the same, and
    the cost of the wrong guess here is a re-press of a button.
    """
    if not str(getattr(d, "slack_thread_override", "") or "").strip():
        return False
    when = getattr(d, "slack_override_at", None)
    if not when:
        return True
    return any(t and t > when for t in (getattr(d, "generated_at", None),
                                        getattr(d, "rca_v3_edited_at", None)))


def _marked_frames(frames) -> list:
    """The support frames, each carrying `is_contact` — the server's verdict.

    ONE RULE, DECIDED ONCE. `split_contact_frames` already answers "is this an
    exchange the guest took part in", at both grains: `is_conversation` per
    frame, `guest_took_part` per ticket. Slack composes from it. The dashboard
    did not — it re-derived a weaker version in JavaScript — so the same
    internal note kept reaching the card as a contact after the Python was
    fixed, three separate times.

    The flag is ADDED, not substituted: every frame still ships, because the
    panel needs the excluded ones to say how many moved and where they went. A
    filtered list and a guest who never wrote in must not read the same, which
    is the reason the split returns two lists rather than one.
    """
    from server.services.zendesk import split_contact_frames, guest_words
    rows = [f for f in (frames or []) if isinstance(f, dict)]
    convo, _ = split_contact_frames(rows)
    keep = {id(f) for f in convo}
    # `guest_words` is stamped for the same reason `is_contact` is: the client
    # would otherwise pick between guestSaid and guestReply in JavaScript, in
    # five places, and that is the drift this function was created to end. The
    # server decides; the page renders what it is given.
    return [dict(f, is_contact=(id(f) in keep), guest_words=guest_words(f))
            for f in rows]


def _draft_dict(d: RcaDraft) -> dict:
    _tf = d.ticket_facts or {}
    _bk = d.booking or {}
    # ONE store for each v4 section. The client renders every one of them out
    # of this blob, so it has to be the same value `_v4()` below resolves —
    # see _resolve_v3_sections for the chip that disagreed with its own payload.
    _v3_resolved, _v3_folded = _resolve_v3_sections(d)
    # The scenario list, settled once. A draft written before the dedupe can
    # hold the same scenario twice, and the card renders the list and its own
    # tail as two rows — so an unsettled list is what put one chip on screen
    # three times. Derived here, together, from the one stored list.
    _scen, _primary_scen, _overlays = settle_scenarios(
        d.scenarios or ([d.primary_scenario] + list(d.overlay_scenarios or [])))

    def _first_name(*cands):
        """The first candidate that is a readable value. Not name-aware —
        booking_status uses it too."""
        for c in cands:
            c = (c or "").strip()
            if c and not _looks_like_hash(c):
                return c
        return ""

    def _internal_label_on(tf, bk):
        """The internal label a booking is recorded under, or ""."""
        from server.names import is_internal_booking_name
        for c in (tf.get("guest_full_name"), bk.get("guestName"),
                  bk.get("primary_guest_name"), bk.get("zendesk_requester_name")):
            c = (c or "").strip()
            if c and is_internal_booking_name(c):
                return c
        return ""

    def _first_guest_name(*cands):
        """The first candidate that is a PERSON'S NAME.

        THE THIRD COPY OF THIS RULE, and the only one that never learned it.
        `_first_name` rejects a warehouse hash and nothing else, so
        "Customer Ops Lead" — the label our own systems put on a desk-made or
        corporate booking, which no guest ever types — went straight through
        and rendered as the guest's name on the card. The indicator check and
        the Tier-1 gate both reject it; the renderer did not, so the card
        contradicted its own trail two panels down.
        """
        from server.names import is_internal_booking_name
        for c in cands:
            c = (c or "").strip()
            if c and not _looks_like_hash(c) and not is_internal_booking_name(c):
                return c
        return ""

    guest_name = _first_guest_name(
        _tf.get("guest_full_name"),
        _bk.get("guestName"),
        # `primary_guest_name` IS THE FIELD THE WAREHOUSE WRITES.
        # `bigquery_patch.verify_bid` builds the booking dict with
        # `primary_guest_name`; `guestName` and `guest_full_name` are the
        # other two spellings, and neither is on a booking that came from a
        # BID lookup. So this read every key except the one that was there and
        # returned "", and the card said no guest name was found on a booking
        # that had one. `_internal_label_on`, five lines above, checks the real
        # field — one function in this file knew and the other did not.
        _bk.get("primary_guest_name"),
        _bk.get("primaryGuestName"),
        # THE ZENDESK COPY, which the run already fetched. Where the warehouse
        # holds a PII hash the ticket is the only readable source, and the
        # pipeline asks it — then stored the answer nowhere, so this function
        # fell through to "the warehouse stores this as a hash — check the
        # Zendesk ticket", telling the reader to redo the lookup by hand.
        _bk.get("zendesk_guest_name"),
        _bk.get("zendesk_requester_name"),
    )
    # When there is no name, say WHICH source failed. "[Guest name in Zendesk
    # ticket]" was a sentence dressed as a value: it looked like data, and it
    # told the reader nothing about whether we had looked, found a hash, or
    # found no ticket at all.
    if guest_name:
        guest_name_note = ""
    elif any(_looks_like_hash((c or "").strip()) for c in
             (_tf.get("guest_full_name"), _bk.get("guestName"),
              _bk.get("primary_guest_name"), _bk.get("primaryGuestName"))):
        guest_name_note = ("the warehouse stores this as a hash — check the "
                           "Zendesk ticket")
    elif _internal_label_on(_tf, _bk):
        # A DIFFERENT FACT from a hash, and it says the RECORD needs fixing
        # rather than that our privacy policy got in the way. Naming the label
        # matters too: "Customer Ops Lead" tells a reader this is a desk-made
        # or corporate booking, which is the whole reason the guest name
        # disagreeing with the reviewer proves nothing.
        guest_name_note = (f"the booking is recorded under an internal label "
                           f"('{_internal_label_on(_tf, _bk)}'), not a guest "
                           f"name — no guest name is on this booking")
    elif d.zendesk_ticket_ids:
        # STILL UNCONDITIONAL, AND STILL WRONG — recorded here rather than left
        # to be rediscovered. `collect_tickets` now knows WHY the requester
        # name is empty (a raised `users()` call reads differently from a
        # ticket with no requester), but there is no column carrying that to
        # the draft, so this sentence cannot yet say which happened. Wiring it
        # needs a `requester_name_reason` column on RcaDraft — step 2 of the
        # guest-name work, not a line to fake here by reading a field that
        # does not exist.
        guest_name_note = "no requester name on the linked Zendesk ticket"
    else:
        guest_name_note = "no Zendesk ticket was matched to this booking"
    booking_status = _first_name(
        _tf.get("booking_status"),
        _bk.get("status"),
        _bk.get("bookingStatus"),
    )

    return {
        "booking":            d.booking,
        "guest_name":         guest_name,
        "guest_name_note":    guest_name_note,
        "booking_status":     booking_status,
        "match_tier":         d.match_tier,
        "match_confidence":   d.match_confidence,
        "match_method":       d.match_method,
        # SCRUBBED HERE, in the one place the picker reads. A digest in the
        # guest-name slot is worse on this card than anywhere else: it is the
        # field an associate recognises the right booking by, and there is no
        # other. `zendesk_guest_name` is the readable copy the shortlist
        # already carries for exactly this case; when neither is readable the
        # slot is left EMPTY rather than filled with the digest, because a
        # blank says "we have no name" and a digest says nothing at all.
        "candidates_list":    _scrub_candidate_names(d.candidates_list or []),
        "candidate_state":    d.candidate_state,
        "confidence_trail":   d.confidence_trail or [],
        # SCRUBBED AT RENDER, not by instruction. The prompt tells the model
        # never to name a vendor; an instruction can be ignored, and it does
        # nothing for the drafts already stored — so a card still read
        # "RAIL EUROPE-CHF contact +41 33 828 72 33". Applied here, it holds
        # whatever the model wrote and whether the draft is new or a year old.
        "timeline":           _scrub_timeline(d.timeline or [], _bk),
        "insights":           d.insights or {},
        "similar_support":    d.similar_support or [],
        "similar_reviews":    d.similar_reviews or [],
        "dss_rec":            d.dss_rec or {},
        "zendesk_ticket_ids": d.zendesk_ticket_ids or [],
        "timeline_raw":       d.timeline_raw or [],
        "dss_connected_at":   d.dss_connected_at.isoformat() if d.dss_connected_at else None,

        # TWO STORES, ONE FACT — the same shape as takedown, and it bit the
        # same way. The pipeline's standalone stated-issue step writes the
        # COLUMN; the RCA writes `rca_v3.stated_issue`; and `↻ RCA only`
        # (regenerate-rca) writes only the second. Reading the column meant the
        # card showed the previous run's sentence after every RCA re-run, and a
        # draft whose standalone step had failed rendered "Nothing was
        # extracted — see the confidence trail for whether the step ran" over
        # an RCA that plainly HAD stated the issue. That empty state is a claim
        # about the review, and it was false.
        #
        # Same presence rule as every other v4 field: rca_v3 wins when it has
        # an answer (including a deliberately emptied one), the column is the
        # fallback for a draft written before v4. It is also what makes the
        # card's edit box round-trip — the box writes rca_v3.stated_issue.
        "stated_issue":                _v4(d, "stated_issue", "stated_issue",
                                           None, v3=_v3_resolved),
        "l1":                          d.l1,
        "l2":                          d.l2,
        "sub_theme":                   d.sub_theme,
        # Lists are the source of truth for the dashboard; the scalars are
        # element 0 and stay in step for every existing consumer.
        "sub_themes":                  d.sub_themes or ([d.sub_theme] if d.sub_theme else []),
        "rca_posted_at":               d.rca_posted_at.isoformat() if d.rca_posted_at else None,
        "rca_v3_edited_at":            d.rca_v3_edited_at.isoformat() if d.rca_v3_edited_at else None,
        "primary_scenario":            _primary_scen or "",
        # Deduplicated on the way out too, so a row written by an older build
        # renders clean immediately instead of only after the next edit. Both
        # keys come from ONE settle, so the card cannot be handed a scenario
        # list and an overlay list that disagree.
        "scenarios":                   _scen,
        "overlay_scenarios":           _overlays,
        # How the primary got there, and how it stands against what routing
        # would say NOW. Computed here rather than in the client so the card,
        # the regenerate endpoint and the trail cannot each reach a slightly
        # different answer — which is how a TGID tile once showed TID+VID data.
        #
        # `diverged` is the whole point: a static "set by hand" tag is
        # provenance, and what actually bites is an override whose reason no
        # longer holds. The comparison fires the moment L1/L2 moves, while the
        # person still remembers why they set it.
        "scenario_routing":            _scenario_routing(d),
        # Does the review describe the experience this booking is FOR? A guest
        # who quotes the wrong reference number produces a match that passes
        # every other check — the id is real, the booking exists, the dates
        # line up — and describes a different product entirely.
        "content_match":               _content_match(d),
        # And is it the same TRIP? content_match compares product families;
        # this compares venue, city, date and guest name. A museum review
        # against a museum booking in another country passes the first check
        # cleanly and fails this one.
        "indicator_match":             _indicator_match(d),
        "wwr_scenarios":               d.wwr_scenarios or [],
        "l1_reasoning":                d.l1_reasoning,
        "diagnostic_checks":           d.diagnostic_checks or [],
        "what_went_wrong_bullets":     d.what_went_wrong_bullets or [],
        # EACH FRAME CARRIES THE SERVER'S VERDICT, so the client does not have
        # to reach one. index.html held a SECOND implementation of
        # `is_conversation` in JavaScript — the thread list and `is_internal`,
        # and none of the actor check, the promotion marker or the
        # guest-took-part rule. So a fix landing in Python left the card
        # rendering from the JS copy, and an agent's internal NAR note kept
        # appearing as "contact 01" through three rounds of "this is fixed".
        #
        # Two implementations of one rule is the defect this file's own
        # comments warn about repeatedly. There is one now, and it is here.
        "support_interaction_frames":  _marked_frames(d.support_interaction_frames),
        "support_summary":             d.support_summary,
        "sp_interaction_frames":       d.sp_interaction_frames or [],
        # rca_v3 wins by PRESENCE, like every other v4 field: the column is
        # the pipeline's projection, and an operator who deletes the last
        # improvement point produces [] - which a truthiness fallback would
        # lose to the stale column, so the delete would undo itself.
        "area_of_improving":           _v4(d, "area_of_improving", "area_of_improving", [], _v3_resolved),
        "actions_taken":               d.actions_taken or {"sp":[],"customer":[],"business":[],"product":[],"ce":[]},
        "resolution":                  d.resolution,

        "bucket":             _bucket_of(d),
        "bid_source":         d.bid_source,
        "extracted_signals":  d.extracted_signals or {},
        "narrowing_attempts": d.narrowing_attempts or [],

        "flag_to_biz_state":           d.flag_to_biz_state,
        "flag_to_biz_message":         d.flag_to_biz_message,

        "rca_v3":                      _v3_resolved,
        # Which sections came from the column because rca_v3 had none. An
        # EMPTY LIST is the healthy answer and says the resolver ran; the key
        # missing entirely would say it did not. Those are different facts and
        # a silent zero merges them.
        "v4_sections_from_column":     _v3_folded,

        # ── RCA v4 ──
        # rca_v3 is the source of truth: it is what the dashboard's data-v3p
        # editor writes to. The columns are the pipeline's queryable copy, so
        # they are the FALLBACK - reading them first would let a stale
        # denormalised value shadow an edit someone just made.
        # These now read out of the SAME resolved blob the client renders from,
        # so the two cannot answer differently.
        "guest_issues":     _v4(d, "guest_issues", "what_went_wrong.guest_issues", [], _v3_resolved),
        "booking_logs":     _v4(d, "booking_logs", "booking_logs", [], _v3_resolved),
        "flags":            _v4(d, "flags", "flags", [], _v3_resolved),
        "takedown":         _v4(d, "takedown", "takedown", {}, _v3_resolved),
        "dss":              _v4(d, "dss", "dss", {}, _v3_resolved),
        # Facts and interpretation, sent separately and merged by the renderer.
        # These are NOT two copies of one value like the six above, so _v4()
        # is the wrong tool: presence-based reading would let the model's
        # account displace Zendesk-derived facts.
        #
        #   *_frames  — the pipeline's rows, built from real tickets. Time,
        #               channel and ticket id are verifiable. This is the row
        #               list the UI renders.
        #   *_notes   — the model's summary / detail / ce_miss, joined by
        #               zd_ref. A note with no matching frame still renders,
        #               marked unverified: either the guest contacted us off
        #               Zendesk or the model invented a contact, and both are
        #               worth seeing rather than silently dropping.
        #
        # One frame per timeline EVENT but one note per contact, so the join is
        # many-frames-to-one-note; the renderer groups by zd_ref rather than
        # pairing row for row.
        "support_interaction":       d.support_interaction_frames or [],
        "support_interaction_notes": (d.rca_v3 or {}).get("support_interaction_notes") or [],
        "sp_interaction":            d.sp_interaction_frames or [],
        "sp_interaction_notes":      (d.rca_v3 or {}).get("sp_interaction_notes") or {},
        "wwr_chain":                   d.wwr_chain or [],
        "prevention":                  d.prevention,
        "evidence":                    d.evidence or [],
        "issue_specific_answers":      d.issue_specific_answers or {},
        "checklist_answers":           d.checklist_answers or [],

        "ticket_facts":        d.ticket_facts or {},
        "slack_thread_override": d.slack_thread_override or "",
        # The two dates the card compares to decide whether the hand-written
        # post predates the analysis it claims to describe.
        # getattr, like `_override_is_stale` beside it. Half this file's
        # callers pass a real row and half pass a stand-in; reading the
        # attribute directly broke eight tests that had every field the
        # composer needs and not this one.
        "slack_override_at":     (getattr(d, "slack_override_at", None).isoformat()
                                  if getattr(d, "slack_override_at", None) else None),
        "slack_override_stale":  _override_is_stale(d),
        "slack_mentions":        d.slack_mentions or [],
        # The what-went-wrong section of the Slack post, composed HERE and
        # rendered verbatim by the dashboard. The dashboard used to build this
        # section itself, in JavaScript, from the same rca_v3 — two composers
        # for one block of text, which is how "Fix: [object Object]" reached a
        # real post from the client while the server's version was fine.
        #
        # Served on every draft read AND on the PATCH response, so an inline
        # edit re-renders the preview from the server's composer rather than
        # from a second implementation that has to be kept in step by hand.
        "wwr_slack_text":     _wwr_slack_text(d, _v3_resolved),
        # THE BOOKING BLOCK, COMPOSED ONCE, for the same reason as the line
        # above. The card builds the rest of the Slack preview in JavaScript
        # while `format_rca_slack` builds it again in Python — two composers,
        # which is how the client once put "Fix: [object Object]" on a real
        # post while the server's copy was correct. The section LIST is still
        # duplicated; its CONTENT is not, and that is the half that renders.
        "booking_details_text": _booking_details_text(d),

        "template_name":      d.template_name or "",
        # Presence-based, like every other v4 field — and for the same reason,
        # only sharper. Prompt rule 20 has the model return NULL for the reply
        # when no approved macro covers the issue, so a blank here is a
        # decision. Read column-first, a stale 110-word reply from an earlier
        # run overrides that decision and puts an unapproved reply back on the
        # card, one Send away from a public review page. `"suggested_response"
        # in rca_v3` wins whatever the value — including "" and None.
        "suggested_response": _v4(d, "suggested_response",
                                  "suggested_response", "", _v3_resolved),
        "final_response":     d.final_response or "",
        # The English working view, and how far it can be trusted. The card
        # needs `english_state` more than it needs the text: `stale` means the
        # outgoing reply was edited directly afterwards, and showing this
        # English without saying so would present a superseded translation as
        # the reply that is about to go out.
        "response_english":   d.response_english or "",
        "english_view":       _english_view(d),
        # Which of english / translated / unknown this review is, and why. The
        # card draws ONE box for an English review and must not imply a
        # translation happened; `unknown` is NOT English — it is a review whose
        # inbound translation never recorded a language.
        "response_language":  _response_language(d),
        # Which prompt body wrote this row. It is what makes "did the new rule
        # run?" answerable, and a copied draft without it reads as the legacy
        # v3 shape — a migration that silently ages every row it moves.
        "rca_prompt_version": d.rca_prompt_version or "",
        "generated_at":       d.generated_at.isoformat() if d.generated_at else None,
        "sent_at":            d.sent_at.isoformat() if d.sent_at else None,

        "zendesk_requester_name": (d.booking or {}).get("zendesk_requester_name") or "",
    }


# ── Existing routes (unchanged) ─────────────────────────────────────────────

@router.get("/api/health")
def health():
    return status_summary()


def _sheet_blocked_by() -> str:
    """Why the sheet export cannot write, in words. "" when it can.

    WHY THIS IS NOT is_live("sheet_export"). That is bool(sheet_id and
    credential), and the sheet id has a hardcoded default — so it reduces to
    "the credential variable is non-empty", which a placeholder pasted verbatim
    satisfies. It reported the export as live on a box whose credential had
    never parsed.

    What it CANNOT say is whether the sheet is shared with the service account;
    that needs a request to Google, which a heartbeat must not make. So the
    empty return means "nothing wrong from here", not "the write will work" —
    the two are different claims and only the first is knowable without the
    network. (On the connector route there is no sharing step at all, so the
    gap between those two claims closes.)
    """
    from server.config import RCA_EXPORT_SHEET_ID
    from server.services.sheet_export import auth_source
    if not RCA_EXPORT_SHEET_ID:
        return "RCA_EXPORT_SHEET_ID is unset"
    return auth_source()[1]


@router.get("/api/export.csv")
def export_csv(x_export_key: str | None = Header(default=None),
               db: Session = Depends(get_session)):
    """Every review and its RCA, as a CSV file. Requires X-Export-Key.

    WHY THIS EXISTS. The sheet export needed a Google credential this project
    has never had, and spent a long time being the one feature that never ran.
    This needs nothing: the rows are the same COLUMNS, built by the same
    row_for(), and the file goes to whoever asks with the key.

    THE KEY IS OPTIONAL, AND WHICH MODE IT IS IN IS SAID OUT LOUD. Nothing
    else in this application authenticates — /api/reviews, the dashboard and
    every draft endpoint are open, and CORS is "*" — so demanding a key here
    and nowhere else was friction rather than protection: the same data is one
    unauthenticated endpoint away.

    So with RCA_EXPORT_KEY unset the file is served, like everything else. The
    part that is NOT copied from _vs_auth below is the silence. That helper
    reads `if expected and ...` and an outsider cannot tell a guarded endpoint
    from an unguarded one, which is this codebase's oldest failure wearing a
    security hat. Here the mode rides back on X-Export-Auth and an open serve
    logs a warning naming what would close it.
    """
    from server.services.sheet_export import rows_for_all, to_csv
    expected = os.environ.get("RCA_EXPORT_KEY", "")
    if expected and x_export_key != expected:
        raise HTTPException(401, "bad or missing X-Export-Key")
    if not expected:
        log.warning("[export] served with NO key — RCA_EXPORT_KEY is unset, so "
                    "anyone who can reach this host can take the file. Set it "
                    "to require X-Export-Key.")

    rows_in = [(r, r.draft) for r in
               db.query(Review).order_by(Review.received_at.desc()).all()]
    rows, failed = rows_for_all(rows_in)
    if failed:
        log.warning("[export] %s of %s row(s) could not be built; they are in "
                    "the file with export_error set", failed, len(rows))
    return Response(
        content=to_csv(rows),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="rca_export.csv"',
            # THE COUNTS TRAVEL WITH THE FILE. A reader who opens it in Excel
            # cannot tell 40 rows from 40-of-45, and the failures are already
            # visible per-row in export_error; this is the total, for anyone
            # scripting against it.
            "X-Export-Rows":   str(len(rows)),
            "X-Export-Failed": str(failed),
            # WHICH MODE, from the outside. "open" is not a failure and not a
            # secret — it is the honest description of an app where nothing
            # else authenticates either, and it is the only way to tell a
            # guarded endpoint from an unguarded one without the source.
            "X-Export-Auth":   "key" if expected else "open",
        })


@router.get("/api/heartbeat")
def heartbeat(db: Session = Depends(get_session)):
    """Public monitoring endpoint — no auth required."""
    try:
        version = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        version = "unknown"

    checks = {
        "bq":        is_live("bigquery"),
        "zendesk":   is_live("zendesk"),
        "anthropic": is_live("anthropic"),
        "slack":     is_live("slack_outbound"),
        "dss":       is_live("dss"),
        "canned":    is_live("canned"),
        "checklist": is_live("checklist"),
        "sheet":     not _sheet_blocked_by(),
    }
    out = {
        "ok":        True,
        "uptime_s":  int(time.time() - _START_TIME),
        "mock_mode": MOCK_MODE,
        "version":   version,
        "checks":    checks,
    }
    # NAMED, NOT JUST FALSE. Every other check here is a boolean because the
    # fix is the same for all of them — set the secret. The sheet has three
    # distinct causes with three different fixes, and a bare false sent the
    # last reader to re-share a spreadsheet whose sharing was fine.
    if not checks["sheet"]:
        out["sheet_blocked_by"] = _sheet_blocked_by()
    else:
        # WHICH ONE. Two credentials can satisfy this and they fail
        # differently afterwards — a service account still needs the sheet
        # shared with it, the connector does not. "true" alone would not say
        # which of those futures you are in.
        from server.services.sheet_export import auth_source
        out["sheet_auth"] = auth_source()[0]
    return out


@router.get("/api/reviews")
def list_reviews(status: str | None = None, tab: str | None = None,
                  db: Session = Depends(get_session)):
    """
    tab: bid | possible_matches | processing | untraceable | sent
    Filters by the one bucket rule in server/tiers.py.
    """
    q = db.query(Review).order_by(Review.received_at.desc())
    if status:
        q = q.filter(Review.status == status)
    rows = q.limit(200).all()

    from server.tiers import (classify, tier_label, is_unverified,
                              TAB_TO_BUCKET, processing_state as _pstate)

    result = []
    for r in rows:
        draft   = r.draft
        tier    = draft.match_tier if draft else None
        cand_state = bool(draft and draft.candidate_state)
        bucket = classify(r, draft)

        # One rule for every tab. The old per-tab conditions disagreed with the
        # dashboard's own derivation, so a confirmed candidate could sit in
        # "possible matches" while the count said otherwise.
        want = TAB_TO_BUCKET.get(tab)
        if want and bucket != want:
            continue

        result.append({
            "id":          r.id,
            "author":      r.author,
            "rating":      r.rating,
            "language":    r.language,
            "status":      r.status,
            "snippet":     (r.body_english or r.body_original or "")[:120],
            "received_at": r.received_at.isoformat() if r.received_at else None,
            "match_tier":  tier,
            "candidate_state": cand_state,
            # The bucket is computed here so the dashboard never has to derive
            # it a second way. Everything on screen sorts on this.
            "bucket":      bucket,
            "tier_label":  tier_label(draft),
            "unverified":  is_unverified(draft),
            # For a review with no draft row: is the run going, or did it die?
            # Both render as an empty card and they need opposite responses —
            # wait, versus re-run and read the log.
            "processing_state":  _pstate(r, draft)[0],
            "processing_reason": _pstate(r, draft)[1],
            # The three facts the bucket rule turns on. Sent so a client can
            # reproduce the decision exactly rather than approximate it from
            # match_tier - approximating is what put confirmed candidates in
            # the wrong tab.
            "has_booking":    bool((draft.booking or {}).get("id")) if draft else False,
            "has_candidates": bool(draft.candidates_list) if draft else False,
            # The fact the processing bucket turns on. Without it a client
            # falling back to its own derivation cannot tell "no draft row"
            # from "a draft row with nothing in it", which is the whole
            # distinction.
            "has_draft":      draft is not None,
            "confirmed":      bool(draft.selected_candidate_bid) if draft else False,
            # Chip 1 is "reviews with a BID", so the inbox has to know whether
            # the id came from the review (attachment/manual/regex) or was
            # inferred from Zendesk. Without this the client cannot tell them
            # apart and every row falls through to chip 2.
            "bid_source":  draft.bid_source if draft else None,
            # Which kind of Sent. A review closed out and a review replied to
            # both have status "sent"; without this the tab shows them as one
            # thing and the count means two different pieces of work.
            "closed_at":   r.closed_at.isoformat() if r.closed_at else None,
            "close_reason": r.close_reason,
            "sent_route":  r.sent_route,
            "reference_number": r.reference_number,
            "experience":  (draft.booking or {}).get("experienceName") if draft else None,
        })
    return result


@router.post("/api/reviews/manual")
async def add_manual_review(
    data: ManualReview,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    from server.pipeline import run_batch_sync

    ts = data.slack_ts or str(time.time())
    review_id = f"tp_{ts.replace('.', '_')}"

    if db.query(Review).filter(Review.id == review_id).first():
        return {"ok": True, "review_id": review_id, "duplicate": True}

    # EXPLICIT, not the column default. A review added by hand IS arriving
    # now, so utcnow is the right answer here — but it is stated rather than
    # inherited, because a default that silently invents a date is how the
    # live webhook path stamped every review with the moment we happened to
    # process it.
    review = Review(
        id=review_id, slack_ts=ts, slack_channel=data.slack_channel,
        rating=data.rating, language=None,
        author=data.author or None, body_original=data.body,
        received_at=datetime.utcnow(),
        reference_number=data.reference_number, status="new",
    )
    db.add(review)
    db.commit()

    # Through the batch runner even for one review: it is the only path that
    # marks the review as queued, so the card can say "queued, not started"
    # instead of showing the blank of a review nobody ever asked about.
    background_tasks.add_task(run_batch_sync, [review_id], "manual-add")
    return {"ok": True, "review_id": review_id}


# Registered BEFORE /api/reviews/{review_id}. FastAPI matches routes in the
# order they are declared, so while this sat further down the file every call
# to it was captured by {review_id} with review_id="bulk-status", looked up as
# a review, and answered 404. The bulk reprocess progress indicator polls this
# endpoint, so it had never once worked - and nothing said so, because a 404
# in a background poll is invisible unless you are watching the console.
@router.get("/api/reviews/bulk-status")
def bulk_status():
    return _bulk_public()


@router.get("/api/reviews/{review_id}")
def get_review(review_id: str, db: Session = Depends(get_session)):
    r = db.query(Review).filter(Review.id == review_id).first()
    if not r:
        raise HTTPException(404, "Not found")
    return {
        "review": {
            "id":               r.id,
            "author":           r.author,
            "rating":           r.rating,
            "language":         r.language,
            "body_original":    r.body_original,
            "body_english":     r.body_english,
            "reference_number": r.reference_number,
            "status":           r.status,
            "slack_channel":    r.slack_channel,
            "slack_ts":         r.slack_ts,
            "received_at":      r.received_at.isoformat() if r.received_at else None,
            "closed_at":        r.closed_at.isoformat() if r.closed_at else None,
            "close_reason":     r.close_reason,
            "sent_route":       r.sent_route,
        },
        "draft": _draft_dict(r.draft) if r.draft else None,
    }


# ── NEW: Experience Insights endpoint ───────────────────────────────────────

@router.get("/api/reviews/{review_id}/insights")
async def get_review_insights(review_id: str, window: str = "",
                              db: Session = Depends(get_session)):
    """
    Experience Insights for a review's booking, over the chosen window.

    There were two handlers registered at this path. FastAPI dispatches to the
    first, so the second was unreachable - and the first ignored `window` and
    returned a bare dict while the window picker expected {"insights": ...}.
    The picker therefore changed the caption, silently failed its own guard,
    and left the numbers on whatever the default window had produced. One
    handler now, one response shape.

    Cached per (l2, window) for 24h. The window has to be part of the key: a
    result computed for 30d is not an answer to a question about 7d, and
    serving it would put a number under a label it does not belong to.
    """
    from datetime import timezone
    from server.services.insights import get_insights as _compute_insights, window_days

    r = db.query(Review).filter(Review.id == review_id).first()
    if not r:
        raise HTTPException(404, "Review not found")
    d = r.draft
    if not d:
        raise HTTPException(404, "Draft not found — run pipeline first")

    booking = d.booking or {}
    l1 = d.l1 or ""
    l2 = d.l2 or ""
    wd = window_days(window or None)

    # A draft carrying a booking id but no tid/vid is a BigQuery lookup that
    # never happened. get_insights resolves it anyway, but only for itself -
    # persisting it here means the booking card fills in too, and the next
    # request does not repeat the query.
    bid = str(booking.get("bid") or booking.get("bookingId")
              or booking.get("id") or "").strip()
    if bid.isdigit() and not (booking.get("tid") and booking.get("vid")):
        from server.services.bigquery_patch import verify_bid
        import asyncio as _aio
        try:
            resolved = await _aio.get_running_loop().run_in_executor(
                None, verify_bid, bid)
        except Exception as e:
            log.warning(f"[insights] verify_bid({bid}) failed: {e}")
            resolved = None
        if resolved:
            for k in ("tid", "vid", "tgid", "date_of_visit", "experienceName",
                      "vendorName", "booking_status", "tid_name"):
                if resolved.get(k) and not booking.get(k):
                    booking[k] = resolved[k]
            d.booking = booking
            flag_modified(d, "booking")
            db.commit()
            log.info(f"[insights] backfilled booking {bid} onto draft {review_id}")

    cached = d.insights or {}
    cache_valid = False
    # Never serve a cached zero. A zero means "could not compute" - no booking
    # resolved, BigQuery down, ids missing - and caching that for 24h means the
    # moment any of it is fixed, the fix cannot be seen. It has already
    # outlived two of them: rows written before tid/vid were resolvable are
    # still being served with the reason string from that build.
    #
    # Recomputing a zero is cheap: the paths that produce one return before
    # running a single query.
    if cached.get("_zeroed_because"):
        cached, cache_valid = {}, False
    elif cached.get("_build") != _BUILD_SHA:
        # Computed by a different build. The cache key covered l2 and window
        # but had no notion of the code that produced the row, so every field
        # added to the payload was invisible on any review that had been
        # viewed before - a current server serving a shape it no longer
        # produces, which is indistinguishable from the field never having
        # been added. Recomputing after a deploy costs one query set per
        # review actually opened.
        cached, cache_valid = {}, False
    elif cached.get("_computed_for_l2") == l2 and cached.get("_window_days") == wd:
        try:
            age = (datetime.utcnow().replace(tzinfo=timezone.utc)
                   - datetime.fromisoformat(cached["_computed_at"])).total_seconds()
            cache_valid = age < 86400  # 24 h
        except Exception:
            pass

    if cache_valid:
        return {"ok": True, "window": window or "30d", "insights": cached}

    try:
        result = await _compute_insights(booking, l1 or None, l2 or None,
                                         window=window or None)
    except Exception as e:
        log.warning(f"[insights] compute failed for {review_id}: {e}")
        raise HTTPException(502, "Insights query failed")

    result["_build"] = _BUILD_SHA
    d.insights = result
    flag_modified(d, "insights")
    db.commit()
    return {"ok": True, "window": window or "30d", "insights": result}


# ── NEW: taxonomy endpoint (dashboard fetches this to render dropdowns) ─────

def _read_head_sha() -> str:
    """
    The commit at the moment this module was imported.

    Read at import, NEVER per request. Reading .git/HEAD when the request
    arrives reports what is on DISK, which is the working tree - so a stale
    process happily reported the commit that had just been pulled into it and
    every staleness check built on this endpoint returned "matches" while
    serving code from hours earlier. The detector could not detect the thing it
    existed for.

    Read from .git directly rather than shelling out: the git binary is not
    always on PATH in the run context.
    """
    # `os` is already imported at module scope. A local import of the same
    # name makes it a LOCAL for this whole function.
    # Every failure has to end in a string. This runs at import and the result
    # is frozen into _BUILD_SHA, so anything raised here takes the whole app
    # down at startup - and .git/HEAD raises more than OSError: a truncated
    # "ref:" with nothing after it splits into one field and the lookup of the
    # second is an IndexError, which is not worth refusing to boot for when the
    # only thing lost is a version banner.
    # The opens are held by a context manager because the handles used to be
    # left to the garbage collector, and this is called again on every
    # /api/version request.
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".git", "HEAD")) as fh:
            head = fh.read().strip()
        if head.startswith("ref:"):
            with open(os.path.join(root, ".git",
                                   head.split(None, 1)[1])) as fh:
                return fh.read().strip() or "unknown"
        # An empty or blank HEAD is not an exception but is not a commit
        # either, and "" compares equal to "" - reporting it as a sha would
        # make the staleness check say "matches" for two unknowns.
        return head or "unknown"
    except Exception:
        return "unknown"


def _source_fingerprint() -> str:
    """A hash of the source this call can see, or "unknown".

    A deployment ships without .git, so the commit is unreadable there and no
    one can tell which code is serving. Both environments hash the same way,
    so equal fingerprints mean identical source - answerable without git, and
    without trusting a build label anyone can forget to bump.

    Never raises. A fingerprint that fails to compute must cost this one line,
    not the endpoint, and it returns "unknown" rather than "" so it cannot
    compare equal to another failure and read as a match.
    """
    try:
        import hashlib
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        h = hashlib.sha256()
        files = sorted(list((root / "server").rglob("*.py"))
                       + [root / "client" / "index.html"])
        for f in files:
            if "__pycache__" in str(f) or not f.is_file():
                continue
            h.update(f.name.encode())
            h.update(f.read_bytes())
        return h.hexdigest()[:12]
    except Exception:
        return "unknown"


_BUILD_SHA = _read_head_sha()   # frozen at import, like the code itself
# Frozen at import too, and for the same reason: this has to describe the code
# THIS PROCESS LOADED. Read per request, it described the files instead, and a
# server running month-old code beside a fresh checkout reported the checkout.
_BUILD_FINGERPRINT = _source_fingerprint()


def _webhook_health(window_hours: int = 72) -> dict:
    """Delivery health for the Slack ingest webhook, for /api/version.

    The question this answers is "has THIS server received Slack events?" — the
    one that could not be asked over HTTP before, so it got answered instead by
    running diagnose.py in the DEV repl and counting `helium/heliumdb`, a
    database Slack never posts to. Slack posts to the deployment (`neondb`), so
    the dev repl reads zero whether the webhook works or not. Exposed here, a
    caller can ask the production deployment itself.

    Rule 1, and it is the entire point: "zero events arrived" and "we could not
    read the table" must not render the same. A readable table returns an
    integer `recent_deliveries` and no `error`; an unreadable one returns
    `error` and leaves `recent_deliveries` null. `last_seen_at` is the most
    recent delivery of any age, so "no deliveries ever" (null) also reads
    differently from "deliveries, but not lately" (an old timestamp, count 0).
    """
    from datetime import datetime as _dt, timedelta as _td
    try:
        from server.db import SessionLocal, SlackEventSeen
        s = SessionLocal()
        try:
            cutoff = _dt.utcnow() - _td(hours=window_hours)
            recent = (s.query(SlackEventSeen)
                        .filter(SlackEventSeen.seen_at > cutoff).count())
            last = (s.query(SlackEventSeen)
                      .order_by(SlackEventSeen.seen_at.desc()).first())
            return {
                "window_hours": window_hours,
                "recent_deliveries": recent,
                "last_seen_at": last.seen_at.isoformat()
                                if last and last.seen_at else None,
            }
        finally:
            s.close()
    except Exception as e:
        # NOT recent_deliveries: 0 — we did not read a zero, we failed to read.
        # Collapsing the two is the exact bug this endpoint exists to avoid.
        return {
            "window_hours": window_hours,
            "recent_deliveries": None,
            "last_seen_at": None,
            "error": str(e)[:200],
        }


@router.get("/api/version")
def get_version():
    """
    The commit this process is running, and when it started.

    Nothing here reloads on a file change - the run command has no --reload, by
    design - so a pull updates the files while the server keeps serving the
    code it imported at startup. That has now cost three debugging rounds, each
    spent reading correct code and wrong output.

    Read from .git directly rather than by shelling out to git: the binary is
    not always on PATH in the run context, and a version endpoint that fails is
    worse than none.
    """
    # `os` is module-level; a local import of the same name would make it a
    # LOCAL for this whole function. `timezone` is not — the module imports it
    # only as `_tz` — so that one is a genuine local import, not a shadow.
    from datetime import timezone

    sha = _BUILD_SHA
    on_disk = _read_head_sha()
    # Whether the comparison below can be made at all. Either side reading
    # "unknown" means there is nothing to compare, which is a different answer
    # from "compared, and they match".
    _build_known = "unknown" not in (sha, on_disk)

    # Dev repl or published deployment? A deployment is a frozen snapshot that
    # only changes when Deploy is pressed - a git pull in the repl does not
    # touch it. Reading a deployment URL while pulling into the repl looks
    # exactly like a fix that did not work, for every fix, indefinitely.
    # Replit names this var differently across runtimes, so check the lot.
    is_deploy = any(os.environ.get(k) for k in
                    ("REPLIT_DEPLOYMENT", "REPL_DEPLOYMENT",
                     "REPLIT_DEPLOYMENT_ID", "REPLIT_CLUSTER_DEPLOYMENT"))
    # WHICH DATABASE. The default DATABASE_URL is sqlite:///./local.db - a file
    # inside this container - so a published deployment and the dev repl each
    # have their OWN reviews and neither can see the other's. Two dashboards
    # then disagree, no amount of cache clearing changes it, and the difference
    # is invisible from the UI. Reported here, credentials stripped.
    db_info = {"dialect": "unknown", "target": "unknown", "shared": False}
    # WHAT THIS PROCESS IS RUNNING, and what is on disk beside it. This was
    # ONE number, recomputed from disk on every request, and so it described
    # the FILES rather than the running build. Two consequences, both bad:
    # comparing it across environments compared their disks, and it could
    # never show a process that had not picked up its own files - which is
    # exactly the failure the whole endpoint exists to surface.
    #
    # `fingerprint` is now frozen at import, like the code. `fingerprint_on_disk`
    # is read now. Different means this process has not loaded what is beside
    # it - answerable WITHOUT git, which is the only thing a deployment has.
    fingerprint = _BUILD_FINGERPRINT
    fp_on_disk = _source_fingerprint()

    # Two ways to answer "is this process behind its files", and the second
    # works where the first cannot. The commit is the better answer when it is
    # readable; the fingerprint is the one a deployment can give. Which one was
    # used is reported, because a judgement made silently is a judgement the
    # reader cannot check - the fingerprint compares SOURCE, so it cannot see
    # a commit that changed only tests or docs, and a reader told "current"
    # deserves to know that is what was compared.
    _fp_known = "unknown" not in (fingerprint, fp_on_disk)
    if _build_known:
        _stale, _stale_by = on_disk != sha, "commit"
        _stale_reason = ""
    elif _fp_known:
        _stale, _stale_by = fp_on_disk != fingerprint, "fingerprint"
        _stale_reason = (
            f"no .git here, so this compares SOURCE, not commits: the code "
            f"this process loaded ({fingerprint}) against the files on disk "
            f"now ({fp_on_disk}). A commit touching only tests or docs would "
            f"not show up. To compare environments, match `fingerprint` "
            f"against the repl's /api/version.")
    else:
        _stale, _stale_by = None, "nothing"
        _stale_reason = (
            f"neither the commit nor a source fingerprint could be read "
            f"(sha={sha}, on_disk={on_disk}, fingerprint={fingerprint}). "
            f"Nothing was compared - this is NOT a report that the build is "
            f"current. Restart the server and check the logs at startup.")
    try:
        # Review and RcaDraft are module-level; re-importing them here
        # would make them LOCALS for this whole function, so a failure of
        # this import would turn every later use into an UnboundLocalError
        # rather than the error that actually happened.
        from server.db import engine, SessionLocal
        url = engine.url
        db_info["dialect"] = url.get_backend_name()
        if url.get_backend_name().startswith("sqlite"):
            db_info["target"] = url.database or ":memory:"
            db_info["shared"] = False   # a file in this container only
        else:
            db_info["target"] = f"{url.host or '?'}/{url.database or '?'}"
            db_info["shared"] = True    # a server both environments can reach
            # The hostname does not identify the database: Replit reaches the
            # same Postgres through a workspace-local proxy under a different
            # name, so two environments can look split when they are not - and
            # can look identical when they are not either. system_identifier
            # is the cluster's own id, so comparing it across environments
            # settles it without writing a marker row.
            try:
                from sqlalchemy import text as _t
                with engine.connect() as _c:
                    db_info["identity"] = str(_c.execute(_t(
                        "SELECT system_identifier::text FROM pg_control_system()"
                    )).scalar())
            except Exception:
                try:
                    from sqlalchemy import text as _t
                    with engine.connect() as _c:
                        row = _c.execute(_t(
                            "SELECT current_database(), "
                            "coalesce(inet_server_addr()::text, 'local'), "
                            "pg_postmaster_start_time()::text")).first()
                    db_info["identity"] = "|".join(str(x) for x in row) if row else "unknown"
                except Exception:
                    db_info["identity"] = "unknown"
        _s = SessionLocal()
        try:
            # Report the parts, not a derived total. "untraceable" alone was
            # ambiguous: it mixed drafts with a null tier and reviews with no
            # draft at all, and the two need different fixes.
            reviews = _s.query(Review).count()
            drafts = _s.query(RcaDraft).count()
            matched = _s.query(RcaDraft).filter(RcaDraft.match_tier.isnot(None)).count()
            cands = _s.query(RcaDraft).filter(RcaDraft.candidate_state.is_(True)).count()
            db_info.update({
                "reviews": reviews,
                "drafts": drafts,
                "matched": matched,
                "candidates": cands,
                "no_draft_row": reviews - drafts,
                "untraceable": reviews - matched,
            })
        finally:
            _s.close()
    except Exception as e:
        db_info["error"] = str(e)[:200]

    # WHICH CONNECTORS THIS PROCESS HAS. Deployment secrets are a separate
    # store from workspace secrets, so a connector configured in the repl can
    # be absent in the published app — and the only visible symptom is every
    # review the deployment processes landing in Untraceable with "BigQuery is
    # not live on this server". That reads as a matching failure. From outside
    # the container there was no way to tell it apart from one, because the
    # only place the difference showed was a log nobody could reach.
    #
    # Booleans, not values: this endpoint is public to anyone with the URL.
    try:
        from server.config import is_live as _is_live
        connectors = {k: _is_live(k) for k in
                      ("bigquery", "zendesk", "anthropic", "slack_inbound",
                       "slack_outbound", "dss", "canned", "checklist",
                       "apps_script")}
    except Exception as e:                       # never fail the endpoint
        connectors = {"error": str(e)[:120]}

    return {
        "commit":     sha,
        "short":      sha[:7],
        "db":         db_info,
        # Slack webhook delivery health, so production can be asked directly
        # whether events are arriving — the question that was previously
        # answered against the wrong database. Read-only; never fails the
        # endpoint, and a broken read reads as `error`, not as zero.
        "webhook":    _webhook_health(),
        "connectors": connectors,
        # What is checked out right now. If it differs from commit, the files
        # have moved on and this process has not - which is the entire failure
        # mode this endpoint exists to catch, and which it previously hid by
        # reporting on_disk as though it were the running build.
        "on_disk":    on_disk,
        # THREE STATES, NOT TWO. This was `on_disk != sha and sha != "unknown"`,
        # so an environment where the commit could not be read at all - a
        # published deployment, which ships without .git - answered
        # `stale: false`. "We checked and you are current" and "we could not
        # check" came out as the same word, on the one endpoint whose entire
        # job is telling you which build you are talking to. It reported
        # in-sync to a deployment that was 24 commits behind.
        #
        # null means unknown. `stale_reason` says what could not be read and
        # what would answer it instead.
        "stale":      _stale,
        "stale_by":   _stale_by,
        "stale_reason": _stale_reason,
        # What this process LOADED, and what sits on disk beside it now.
        "fingerprint":         fingerprint,
        "fingerprint_on_disk": fp_on_disk,
        "environment": "deployment" if is_deploy else "dev",
        "reload":     os.environ.get("UVICORN_RELOAD", "") .lower() in ("1", "true", "yes"),
        "started_at": _STARTED_AT,
        "uptime_s":   int((datetime.now(timezone.utc)
                           - datetime.fromisoformat(_STARTED_AT)).total_seconds()),
    }


@router.get("/api/taxonomy")
def get_taxonomy():
    return {
        "l1_categories":       L1_CATEGORIES,
        "l2_options":          L2_OPTIONS,
        "diagnostic_checks":   DIAGNOSTIC_CHECKS,
        "action_tabs":         ACTION_TABS,
        "sub_theme_frameworks": {f"{k[0]}::{k[1]}": v for k, v in SUB_THEME_REGISTRY.items()},
        # The scenario vocabulary, so the dashboard can offer the real options
        # instead of a read-only chip. SCENARIO_CHECKS keys are the routing
        # targets; "general" is the routers' explicit fallback.
        "scenarios": sorted(SCENARIO_CHECKS) + ["general"],
        # The grounds for a takedown, from content/orm_macros.yaml. Served
        # rather than hardcoded in the client so the list stays a content
        # change.
        "takedown_reasons": TAKEDOWN_REASONS,
    }


# ── NEW: v2 draft patch ─────────────────────────────────────────────────────

# Where a v4 section lives inside rca_v3. The column of the same name is a
# read-only projection the pipeline writes at generation time; nothing here
# touches it.
_V4_SECTIONS = {
    "guest_issues":   ("what_went_wrong", "guest_issues"),
    "booking_logs":   ("booking_logs",),
    "flags":          ("flags",),
    "takedown":       ("takedown",),
    "dss":            ("dss",),
    # Area of improvement was read through _v4 - rca_v3 first, column as
    # fallback - and written to the COLUMN ONLY. So every edit landed in a
    # store the reader never consults: add a point, delete a point, rewrite a
    # point, all returned 200 and put a green tick on the field, and the next
    # load showed the pipeline's original list. `show_draft --bid` keying on
    # bookingId while the warehouse writes id, again.
    #
    # There is a test that walks _draft_dict's _v4 reads and fails if any of
    # them is missing here, because spotting this one by eye took a browser
    # clicking every control on the card.
    "area_of_improving": ("area_of_improving",),
    # Same shape, same reason. `stated_issue` became an rca_v3-first READ (so
    # the card's edit box round-trips) while the PATCH still wrote only the
    # column — which is the area_of_improving bug exactly: a 200, a green
    # tick, and a value the reader never consults again. The card itself edits
    # through `data-v3p`, so this closes the hole for any OTHER caller that
    # patches the field by name and would otherwise get a silent no-op.
    "stated_issue":      ("stated_issue",),
}


def _dss_for_prompt(d) -> dict:
    """What the RCA prompt should be told the playbook prescribes.

    `d.dss_rec` is what the lookup returned. `rca_v3.dss` is what the card
    shows and what an associate can CORRECT — the lookup matches the wrong row
    often enough that the edit control exists. A re-run that read the lookup
    would discard the correction it was asked to act on.

    The edited value wins only when it was actually edited: `by_hand` is set by
    the card when someone types into the field, so an untouched projection of
    the lookup does not shadow the lookup's own richer record.
    """
    v3dss = (d.rca_v3 or {}).get("dss") if isinstance(d.rca_v3, dict) else None
    if isinstance(v3dss, dict) and v3dss.get("by_hand") and v3dss.get("prescribes"):
        base = dict(d.dss_rec or {})
        base["dss"] = v3dss.get("prescribes")
        base["prescribes"] = v3dss.get("prescribes")
        base["corrected_by_hand"] = True
        return base
    return d.dss_rec or {}


def settle_scenarios(raw):
    """One ordered, deduplicated scenario list, plus the two scalars it implies.

    THREE STORES FOR ONE FACT, and they disagreed. `scenarios`,
    `primary_scenario` and `overlay_scenarios` are three columns describing one
    ordered list, written from two endpoints, and the card renders two of them
    side by side. The reported symptom was a Classification block showing
    `Refund issues` twice under Scenarios and a third time under Overlays, with
    a delete button that did nothing.

    Both halves came from the same place. `regenerate-rca` is sent
    `[...scenarios, ...overlayScenarios]` by the card — and `scenarios`
    ALREADY CONTAINS the overlays, so every scenario edit appended the overlays
    to the list a second time and wrote the result straight back over
    `d.scenarios`. That is why the chips multiplied, and why removing one
    looked dead: the removal was saved and then immediately overwritten by the
    union that still held it.

    So: ONE list. `primary_scenario` is its first element and
    `overlay_scenarios` is the rest, both DERIVED here and never authored
    independently.

    THE PRIMARY IS NOT ITS OWN OVERLAY. An overlay is an ADDITIONAL scenario
    layered on the primary, and a scenario cannot be additional to itself.
    A primary that also appeared in the overlays is what put one chip on the
    card three times.

    Returns (scenarios, primary, overlays).
    """
    seen, out = set(), []
    for s in (raw or []):
        if not isinstance(s, str):
            continue
        s = s.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out, (out[0] if out else None), out[1:]


@router.patch("/api/reviews/{review_id}/draft-v2")
def patch_draft_v2(review_id: str, patch: DraftPatchV2,
                    db: Session = Depends(get_session)):
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not d:
        raise HTTPException(404, "Draft not found")

    # THE GAPS AS THEY WERE BEFORE THIS REQUEST WROTE ANYTHING. The regroup at
    # the bottom needs them to tell a row somebody TYPED from a row the
    # previous rebuild derived, and by then `d.rca_v3` holds the new blob —
    # reading it there would compare the new gaps against themselves and
    # resurrect the pre-edit row whenever a gap was edited.
    #
    # THE COLUMN AS IT WAS, TOO, and for a second reason. Whatever the client
    # sends for `actions_taken` in this request is discarded — a second writer
    # is how the column and the card drift, which is what
    # test_the_client_cannot_write_the_column_behind_the_fixes guards. Reading
    # the keep off `d.actions_taken` after the field loop would let a
    # client-invented row in through the back door as "hand-typed".
    #
    # Both snapshots taken here, before anything is written. The two rules —
    # the server owns this column, and a row somebody typed survives — only
    # hold together if "somebody typed it" means it was ALREADY STORED.
    _gaps_before = ((d.rca_v3 or {}).get("what_went_wrong") or {}).get("gaps")
    _actions_before = dict(d.actions_taken or {})

    edits = 0
    for field in (
        "stated_issue", "l1", "l2", "sub_theme", "l1_reasoning",
        "primary_scenario", "sub_themes", "scenarios", "overlay_scenarios",
        "diagnostic_checks", "what_went_wrong_bullets",
        "support_interaction_frames", "support_summary",
        "sp_interaction_frames", "area_of_improving",
        "actions_taken", "resolution", "final_response",
        "wwr_chain", "wwr_scenarios", "prevention", "evidence",
        "issue_specific_answers", "checklist_answers", "slack_thread_override",
        "rca_v3",
    ):
        val = getattr(patch, field, None)
        if val is not None:
            setattr(d, field, val)
            # WHEN THE POST WAS HAND-WRITTEN, so a later RCA fix can be seen to
            # postdate it. An override with no timestamp shadows every
            # subsequent correction in silence, and the override is what gets
            # SENT — the card shows the fixed analysis, the thread carries the
            # old one. Cleared when the override is cleared: a stale stamp on
            # an empty override would report a hand edit nobody made.
            if field == "slack_thread_override":
                d.slack_override_at = (datetime.utcnow()
                                       if str(val).strip() else None)
            # JSON columns do not track in-place mutation; a dict assigned with
            # the same identity as the old one would not be written at all.
            try:
                flag_modified(d, field)
            except Exception:
                pass
            edits += 1

    # Keep the scalars in step with the lists. Writing only the list would
    # leave the prompt, DSS routing and the Slack post reading a stale scalar,
    # so an edit in the dashboard would change the chips and nothing else.
    if patch.sub_themes is not None:
        d.sub_theme = (patch.sub_themes or [None])[0]
    if patch.scenarios is not None:
        # The list the loop above just assigned is the raw one the card sent.
        # Settle it here so all three columns come out of one decision — the
        # loop wrote `scenarios` and `overlay_scenarios` independently, and
        # then this block used to overwrite the second from the first, so a
        # deleted overlay came back derived from a list that still held it.
        d.scenarios, d.primary_scenario, d.overlay_scenarios = \
            settle_scenarios(patch.scenarios)
        flag_modified(d, "scenarios")
        flag_modified(d, "overlay_scenarios")
    if patch.sub_theme is not None and patch.sub_themes is None:
        d.sub_themes = [patch.sub_theme] if patch.sub_theme else []
    if patch.primary_scenario is not None and patch.scenarios is None:
        d.scenarios = [s for s in ([patch.primary_scenario] + (d.overlay_scenarios or [])) if s]

    # ── who set the primary, and what happens when L1/L2 moves ──────────────
    # Setting a scenario from the dashboard is a JUDGEMENT about how the case
    # should be read, and it outranks routing from then on. Without recording
    # that, the next L1/L2 correction would silently overwrite it — and an
    # overwritten judgement leaves no trace that one was ever made.
    _sets_scenario = (patch.primary_scenario is not None
                      or patch.scenarios is not None)
    _sets_class = (patch.l1 is not None or patch.l2 is not None
                   or patch.sub_theme is not None or patch.sub_themes is not None)
    if _sets_scenario or (_sets_class and not _sets_scenario):
        from server import scenario_override as so
        _v3 = dict(d.rca_v3 or {})
        if _sets_scenario:
            _v3["scenario_source"] = so.MANUAL
        elif _v3.get("scenario_source") != so.MANUAL:
            # A routed primary follows the classification. Re-routed here
            # rather than left to the client, because the prompt, the DSS
            # lookup and the Slack post all read the stored scalar and would
            # otherwise carry a scenario for the OLD classification.
            _re = so.reconcile(d.primary_scenario, _v3.get("scenario_source"),
                               d.l1, d.l2,
                               ", ".join(d.sub_themes or []) or d.sub_theme)
            if _re["primary"] != d.primary_scenario:
                d.primary_scenario = _re["primary"]
                d.scenarios = [s for s in ([_re["primary"]]
                                           + (d.overlay_scenarios or [])) if s]
        d.rca_v3 = _v3
        flag_modified(d, "rca_v3")

    # A v4 section patch edits rca_v3, never the column of the same name. The
    # columns are the pipeline's projection; giving the client a second way to
    # write them is how the two stores drift apart, and then the reader has to
    # guess which one is current.
    #
    # Presence, not truthiness: model_fields_set distinguishes "the caller did
    # not mention flags" from "the caller cleared flags". Sending [] must clear
    # them, and sending null must too - otherwise the column resurrects what
    # was just deleted.
    _sent = patch.model_fields_set
    _v4_sent = [k for k in _V4_SECTIONS if k in _sent]
    if _v4_sent:
        # A new dict, not a mutation: SQLAlchemy compares JSON columns by
        # identity, so editing rca_v3 in place is a write it never sees.
        blob = dict(d.rca_v3 or {})
        for k in _v4_sent:
            path = _V4_SECTIONS[k]
            if len(path) == 1:
                blob[path[0]] = getattr(patch, k)
            else:
                outer = dict(blob.get(path[0]) or {})
                outer[path[1]] = getattr(patch, k)
                blob[path[0]] = outer
        d.rca_v3 = blob
        flag_modified(d, "rca_v3")
        edits += len(_v4_sent)

    # Mark the RCA body as human-touched, but ONLY for a real rca_v3 patch.
    # Routing changes and clearing the Slack override also come through here
    # and are not content edits; marking those would over-protect and make a
    # bulk re-run skip nearly everything.
    if patch.rca_v3 is not None or _v4_sent:
        d.rca_v3_edited_at = datetime.utcnow()

    # ── Actions Taken is a VIEW, so it is recomputed here, not sent ──────────
    #
    # THE DRIFT THIS CLOSES. §3's fixes live in `rca_v3`; `actions_taken` is a
    # COLUMN, and Slack reads that column (slack.py:772, :1128). This endpoint
    # writes `rca_v3` raw and never re-runs validate, so changing a fix's owner
    # on the card moved the fix and left the column — and the Slack post — on
    # the old routing. Two stores for one fact, reached from the other side.
    #
    # The server owns it: whatever the client sent for `actions_taken` in the
    # same request is overwritten, because a second writer is how the two
    # drift in the first place. Only the grouping is redone — no model call, so
    # nothing a human typed into a fix is re-judged.
    if patch.rca_v3 is not None or _v4_sent:
        try:
            from server.checklist import actions_from_gaps, hand_typed_actions
            _blob = d.rca_v3 or {}
            _wwr = _blob.get("what_went_wrong") or {}
            # A ROW SOMEBODY TYPED SURVIVES A CARD EDIT. This path passed no
            # `keep` at all, so the regroup wiped hand-typed rows on every
            # save — harmless while `gaps` was never stored and the tab was
            # always empty, and a live data-loss bug the moment gaps started
            # working. Editing the resolution field would delete a row an
            # associate had written, with nothing said.
            _keep, _ = hand_typed_actions(_actions_before, _gaps_before)
            _tabs, _rep = actions_from_gaps(_wwr.get("gaps"), keep=_keep)
            d.actions_taken = _tabs
            flag_modified(d, "actions_taken")
        except Exception as e:
            # NOT silent. A failure here leaves the column on its previous
            # value, which is stale rather than wrong-shaped — the reader has
            # to be able to find out that the regroup did not run.
            log.exception(f"[draft-v2] {review_id}: actions_taken regroup "
                          f"failed, column left on its previous value: {e}")

    m = db.query(ReviewMetric).filter(ReviewMetric.review_id == review_id).first()
    if m and edits:
        m.edit_count = (m.edit_count or 0) + edits
    db.commit()
    return {"ok": True, "draft": _draft_dict(d)}


# ── NEW: candidate selection ────────────────────────────────────────────────

@router.post("/api/reviews/{review_id}/select-candidate")
async def select_candidate(review_id: str, body: CandidateSelect,
                            background_tasks: BackgroundTasks,
                            db: Session = Depends(get_session)):
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not d:
        raise HTTPException(404, "Draft not found")
    if not d.candidate_state:
        raise HTTPException(400, "Not in candidate state")

    match = next((c for c in (d.candidates_list or []) if c["id"] == body.bid), None)
    if not match:
        raise HTTPException(400, f"BID {body.bid} not in candidates list")

    # Store the FULL BigQuery row, not the candidate dict. A candidate carries
    # only what the picker needs to display (id, experience, tgid/tid, vendor,
    # visit date); date_of_booking, fulfilment_type, booking_status and tid_name
    # are never in it, so confirming a candidate used to leave those fields
    # permanently blank on the booking card.
    full, lookup, why = None, "found", ""
    try:
        from server.services.bigquery_patch import verify_bid
        full = verify_bid(body.bid)
        if not full:
            lookup, why = "absent", "the warehouse has no booking with this id"
    except Exception as e:
        log.warning(f"[select-candidate] verify_bid({body.bid}) failed: {e}")
        lookup, why = "failed", f"the lookup did not complete ({type(e).__name__})"
    d.booking = {**match, **(full or {}), "id": body.bid} if full else dict(match)
    # WHICH OF THE THREE THINGS HAPPENED, ON THE DRAFT rather than in the log.
    #
    # This branch used to be `log.warning(...)` and nothing else, and the
    # booking it stored on the failing path was the candidate dict — which on
    # the shortlist path was an id and a row of empty strings. `classify()`
    # then reads `booking["id"]`, files the review under IDENTIFIED, and the
    # card renders a confirmed match with no experience, no date and no
    # vendor. The one record that anything went wrong was a line in a log
    # that, as the client's own comment says two panels away, "the people
    # reading these cards do not read".
    #
    # `absent` and `failed` are kept apart for the same reason as everywhere
    # else here: the first is a dead end an associate acts on, the second is
    # a re-run.
    d.booking["details_lookup"] = lookup
    if lookup != "found":
        log.warning(f"[select-candidate] {body.bid}: {why}; storing the "
                    f"candidate's own fields only")
        _trail = list(d.confidence_trail or [])
        _trail.append({"mark": "warn",
            "text": f"<strong>Confirmed BID {body.bid}, but its booking record "
                    f"was not read</strong> — {why}. What is shown below is "
                    f"what the Zendesk ticket carried, not the booking's own "
                    f"record"
                    + (", so re-run once the warehouse is reachable."
                       if lookup == "failed" else
                       ". Check the id before the RCA is built on it.")})
        d.confidence_trail = _trail
        flag_modified(d, "confidence_trail")
    flag_modified(d, "booking")
    d.selected_candidate_bid = body.bid
    d.candidate_state = False
    d.match_tier = 2
    d.match_confidence = "confirmed"
    d.match_method = "Associate confirmed candidate"
    db.commit()

    # Re-run pipeline to fetch Zendesk/insights/RCA for the confirmed booking.
    from server.pipeline import run_batch_sync
    background_tasks.add_task(run_batch_sync, [review_id], "candidate-confirmed")
    return {"ok": True, "draft": _draft_dict(d)}


class BookingIdSet(BaseModel):
    """A booking id an associate typed in, rather than picked from a list."""
    bid: str


@router.post("/api/reviews/{review_id}/set-booking-id")
async def set_booking_id(review_id: str, body: BookingIdSet,
                         background_tasks: BackgroundTasks,
                         db: Session = Depends(get_session)):
    """Set the booking from a booking id the associate found themselves.

    WHY THIS IS NOT select-candidate. That one takes a bid from
    `candidates_list` and 400s on anything else, which is right for the
    picker: it exists so a mis-click cannot invent a booking. But it leaves no
    route at all for the commonest recovery — the associate searches Zendesk
    or BMS, finds the booking the pipeline could not, and has nowhere to put
    it. The review then sits in candidates or untraceable holding a number the
    person reading it already knows.

    So this accepts ANY id, and works from any state:
      * in candidate state it behaves exactly like confirming a candidate;
      * on an already-matched review it OVERWRITES the match, because the
        pipeline being wrong is precisely when this is needed.

    IT VERIFIES FIRST. An id that BigQuery does not recognise is refused with
    the reason, rather than stored and left to render as a booking with every
    field blank — which looks like a lookup that failed rather than an id that
    was wrong. Where the warehouse is unreachable, that is said too and is a
    different sentence: refusing there would block the one recovery route on
    the day the warehouse is down.
    """
    r = db.query(Review).filter(Review.id == review_id).first()
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not r:
        raise HTTPException(404, f"No review {review_id}.")
    if not d:
        d = RcaDraft(id=f"draft_{review_id}", review_id=review_id)
        db.add(d)

    bid = re.sub(r"\D", "", str(body.bid or ""))
    if not bid:
        raise HTTPException(400, detail={
            "message": "That is not a booking id — enter the 7-12 digit "
                       "number, with or without spaces.",
            "kind": "not_a_number"})

    full, why = None, ""
    if not is_live("bigquery"):
        why = ("BigQuery is not connected on this server, so this id could "
               "not be checked against the warehouse — it has been set as "
               "given")
    else:
        try:
            from server.services.bigquery_patch import verify_bid
            full = verify_bid(bid)
        except Exception as e:
            log.warning(f"[set-booking-id] verify_bid({bid}) failed: {e}")
            why = (f"the warehouse lookup for this id failed "
                   f"({type(e).__name__}) — it has been set as given")
        if full is None and not why:
            # THE ID IS WRONG, and that is a different fact from the lookup
            # having broken. Refused, so a typo cannot become a booking.
            raise HTTPException(404, detail={
                "message": f"BigQuery has no booking {bid}. Check the number — "
                           f"a booking id is 7-12 digits and is not the "
                           f"Zendesk ticket number.",
                "kind": "not_found", "bid": bid})

    was = (d.booking or {}).get("id")
    d.booking = {**(full or {}), "id": bid}
    d.selected_candidate_bid = bid
    d.candidate_state = False
    d.candidates_list = []
    d.match_tier = 1 if full else 2
    d.match_confidence = "confirmed"
    d.match_method = "Associate set the booking id"
    d.bid_source = "manual"

    # ON THE TRAIL, because the trail is what a reader opens to find out how a
    # booking was arrived at. A match a person typed and a match the pipeline
    # found must not read the same — and an OVERWRITE must say what it
    # replaced, or the previous answer vanishes with nothing recording that it
    # ever existed.
    trail = list(d.confidence_trail or [])
    trail.append({"mark": "warn" if why else "pass",
                  "text": "<strong>Booking id set by the associate</strong> — "
                          + (f"replacing {was}. " if was and was != bid else "")
                          + (f"{why}." if why else
                             "verified against BigQuery.")
                          + " Everything below is regenerated from this id."})
    d.confidence_trail = trail
    flag_modified(d, "confidence_trail")
    db.commit()

    from server.pipeline import run_batch_sync
    background_tasks.add_task(run_batch_sync, [review_id], "bid-set-by-hand")
    return {"ok": True, "bid": bid, "verified": bool(full),
            "replaced": was if was and was != bid else None,
            "note": why, "draft": _draft_dict(d)}


# ── NEW: DSS on-demand connect ──────────────────────────────────────────────

@router.post("/api/reviews/{review_id}/connect-dss")
async def connect_dss(review_id: str, db: Session = Depends(get_session)):
    r = db.query(Review).filter(Review.id == review_id).first()
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not r or not d:
        raise HTTPException(404, "Not found")

    dss_rec = await dss_svc.get_recommendation(
        d.booking or {}, review_id,
        l1=d.l1, l2=d.l2,       # pass classification for policy lookup
    )
    d.dss_rec = dss_rec
    d.dss_connected_at = datetime.utcnow()

    # Prefill resolution textarea if empty
    if not d.resolution and dss_rec.get("compensation"):
        d.resolution = dss_rec["compensation"]

    m = db.query(ReviewMetric).filter(ReviewMetric.review_id == review_id).first()
    if m:
        m.dss_connected = True

    db.commit()
    return {"ok": True, "dss_rec": dss_rec, "resolution": d.resolution}


# ── Slack backfill ──────────────────────────────────────────────────────────
# The webhook is the live path, but it only works while Slack can reach this
# server: a redeployed Repl URL, a revoked token, or an outage means reviews
# posted in that window exist in Slack and nowhere else. Re-ingesting them by
# hand is not a workflow, so this reads recent channel history and pipelines
# anything with no Review row. Safe to call repeatedly - already-ingested
# messages are skipped by id.

@router.post("/api/reviews/refresh-slack")
async def refresh_slack(hours: int = 72, background_tasks: BackgroundTasks = None,
                        db: Session = Depends(get_session)):
    from datetime import timedelta, timezone as _tzz
    from server.config import SLACK_CHANNEL_ORM
    from server.services.slack import (
        _bot, _user, is_trustpilot_message, parse_review)

    client = _bot or _user
    if not client or not SLACK_CHANNEL_ORM:
        raise HTTPException(
            503, "Slack not configured (needs SLACK_BOT_TOKEN + SLACK_CHANNEL_ORM)")

    oldest = (datetime.now(_tzz.utc) - timedelta(hours=hours)).timestamp()
    try:
        res = await asyncio.to_thread(
            lambda: client.conversations_history(
                channel=SLACK_CHANNEL_ORM, oldest=str(oldest), limit=200))
    except Exception as e:
        raise HTTPException(502, f"Slack history read failed: {e}")

    msgs = res.get("messages") or []
    found = skipped = queued = 0
    ingested = []
    for m in msgs:
        ev = {**m, "channel": SLACK_CHANNEL_ORM}
        if not is_trustpilot_message(ev):
            continue
        found += 1
        parsed = parse_review(ev)
        rid = f"tp_{parsed['slack_ts'].replace('.', '_')}"
        if db.query(Review).filter(Review.id == rid).first():
            skipped += 1
            continue
        # WHEN THE REVIEW CAME IN, not when we happened to fetch it. This row
        # was created without received_at, so the column default fired and
        # every review in a batch got the same value - the ingest moment. On
        # screen that is fifteen reviews all stamped 05 Aug 07:47, which reads
        # as fifteen reviews posted in the same second.
        #
        # slack_ts is the message's own timestamp and it is already in hand -
        # the review id is built from it. It is when the review reached the
        # channel, which is the closest thing to the review's own date that
        # this pipeline ever sees; Trustpilot's publish time is not in the
        # payload.
        _at = _received_at_from(parsed["slack_ts"], rid,
                                parsed.get("published_at"),
                                parsed.get("published_at_source", ""))
        db.add(Review(
            id=rid, slack_ts=parsed["slack_ts"],
            slack_channel=parsed["slack_channel"], rating=parsed["rating"],
            language=parsed["language"], author=parsed.get("author") or None,
            body_original=parsed["body_original"], received_at=_at,
            reference_number=parsed["reference_number"], status="new"))
        db.commit()
        # PHASE ONE OF THE SHEET: the row exists from the moment the review
        # does, carrying its id, its arrival time and the Slack link. Every
        # later write is then an UPDATE, which is what makes this safe with
        # several people working at once — two appends racing is the dangerous
        # shape, and after this there are none.
        #
        # After the commit and never in front of it: a sheet that is unshared
        # or rate-limited must not cost us the review.
        try:
            from server.services.sheet_export import on_review_arrived
            on_review_arrived(db.query(Review).filter(Review.id == rid).first())
        except Exception as _e:
            log.warning(f"[ingest] {rid}: sheet arrival row not written: {_e}")
        queued += 1
        ingested.append(rid)

    # Pipelines run in the background so the button returns at once; the
    # dashboard's own poll fills each card in as its run finishes.
    #
    # ONE task for the whole batch, not one per review. Fifteen separate
    # BackgroundTasks are run by Starlette as `for task in self.tasks: await
    # task()` with no try/except: the first to raise drops every review behind
    # it, and a single wedged run holds the rest for as long as it lasts. A
    # fifteen-review ingest left thirteen reviews unstarted and unrecorded that
    # way. run_batch marks every review queued before the first one starts,
    # isolates each run, bounds it, and logs what it could not do.
    from server.pipeline import run_batch, run_batch_sync
    if background_tasks is not None:
        background_tasks.add_task(run_batch_sync, list(ingested), "slack-refresh")
    elif ingested:
        # Called outside a request (a script, a test): run inline rather
        # than silently ingesting rows whose pipeline never runs.
        await run_batch(list(ingested), "slack-refresh")

    log.info(f"[refresh-slack] {hours}h: {found} Trustpilot posts, "
             f"{skipped} already had rows, {queued} queued")
    return {"ok": True, "window_hours": hours, "messages_scanned": len(msgs),
            "trustpilot_found": found, "already_present": skipped,
            "queued": queued, "review_ids": ingested}


# ── Bulk re-run ─────────────────────────────────────────────────────────────
# Pipeline output is stored, not computed on read: a fix to matching, the
# prompt or the RCA shape changes nothing on screen until each review runs
# again. Doing that one card at a time does not scale past a handful, and
# "the change is not reflecting" is indistinguishable from "the change does
# not work". Runs SEQUENTIALLY - twenty concurrent pipelines would hammer
# BigQuery, Zendesk and the model all at once.

_BULK_MAX = 60


# Job state lives on the server, not in the browser: a ten-review re-run takes
# minutes, and a tab close or reload must not lose it or leave work orphaned.
_BULK: dict = {
    "running": False, "scope": "", "total": 0, "done": 0, "failed": 0,
    "current": "", "started_at": None, "finished_at": None,
    "results": [], "cancel": False,
}
_BULK_CONCURRENCY = 3


def _bulk_targets(db, scope: str, limit: int) -> list[str]:
    """Which reviews need re-running.

    "incomplete" is the one worth having: a review is incomplete when it has
    no draft row, no match and no candidates, or a draft with no RCA - which
    is what someone means by "refresh the broken ones", and it does not depend
    on which tab happens to be open.
    """
    rows = db.query(Review).order_by(Review.received_at.desc()).limit(300).all()
    ids = []
    for r in rows:
        d = r.draft
        tier = d.match_tier if d else None
        cand = bool(d and d.candidate_state)
        if scope == "incomplete":
            # Never re-run a sent review in bulk. Its work is finished, and a
            # re-run rewrites the RCA under a reply that has already gone to
            # the guest.
            if r.status == "sent":
                continue
            broken = (d is None
                      or (tier is None and not cand)
                      or not (d.rca_v3 or {}))
            if not broken:
                continue
        elif scope == "untraceable" and not (tier is None and not cand):
            continue
        elif scope == "possible_matches" and not cand:
            continue
        # No scope may overwrite an RCA a person has edited by hand. The
        # "incomplete" scope already excludes these (they have an rca_v3),
        # but the tab-scoped runs would have rewritten them.
        if d is not None and d.rca_v3_edited_at is not None:
            continue
        elif scope == "bid" and tier != 1:
            continue
        ids.append(r.id)
        if len(ids) >= limit:
            break
    return ids


async def _bulk_worker(ids: list[str]):
    from server.pipeline import process_review as _pipeline, RUN_TIMEOUT_S
    sem = asyncio.Semaphore(_BULK_CONCURRENCY)

    async def one(rid: str):
        if _BULK["cancel"]:
            return
        async with sem:
            if _BULK["cancel"]:
                return
            _BULK["current"] = rid
            try:
                # Bounded. This loop already survives a review that RAISES;
                # a review that never returns is the other way to stop a
                # queue, and three of them hold every semaphore slot for
                # ever with the job reporting itself as still running.
                await asyncio.wait_for(_pipeline(rid), RUN_TIMEOUT_S)
                _BULK["results"].append({"id": rid, "ok": True, "error": ""})
                log.info(f"[bulk] {rid} done ({_BULK['done'] + 1}/{_BULK['total']})")
            except (asyncio.TimeoutError, TimeoutError):
                _BULK["failed"] += 1
                _BULK["results"].append({
                    "id": rid, "ok": False,
                    "error": f"stopped after {RUN_TIMEOUT_S // 60} minutes — "
                             f"our budget, not a reported failure"})
                log.error(f"[bulk] {rid} timed out after {RUN_TIMEOUT_S}s")
            except Exception as e:
                # One bad review must never stop the queue, and the failure
                # must survive into the status so it is not just a log line.
                _BULK["failed"] += 1
                _BULK["results"].append({"id": rid, "ok": False,
                                         "error": f"{type(e).__name__}: {e}"[:300]})
                log.exception(f"[bulk] {rid} failed: {e}")
            finally:
                _BULK["done"] += 1

    try:
        await asyncio.gather(*(one(r) for r in ids))
    finally:
        _BULK["running"] = False
        _BULK["current"] = ""
        _BULK["finished_at"] = datetime.utcnow().isoformat()
        log.info(f"[bulk] finished: {_BULK['done']}/{_BULK['total']}, "
                 f"{_BULK['failed']} failed")


@router.post("/api/reviews/reprocess-all")
async def reprocess_all(tab: str = "incomplete", limit: int = _BULK_MAX,
                        background_tasks: BackgroundTasks = None,
                        db: Session = Depends(get_session)):
    """scope: incomplete (default) | untraceable | possible_matches | bid | all

    Returns immediately. Progress is at GET /api/reviews/bulk-status, so the
    browser can be closed and reopened without losing the run.
    """
    # Claim the slot BEFORE the target query. Checking the flag, then spending
    # a query, then setting it, leaves a window where two clicks both pass the
    # check and start two workers over the same reviews - each re-running the
    # other's rows and both writing the same drafts.
    if _BULK["running"]:
        return {"ok": False, "already_running": True, **_bulk_public()}
    _BULK["running"] = True
    try:
        limit = max(1, min(int(limit), _BULK_MAX))
        ids = _bulk_targets(db, tab, limit)
    except Exception:
        _BULK["running"] = False
        raise
    if not ids:
        _BULK["running"] = False
        return {"ok": True, "queued": 0, "scope": tab,
                "note": "nothing matched that scope"}

    _BULK.update({"running": True, "scope": tab, "total": len(ids), "done": 0,
                  "failed": 0, "current": "", "cancel": False, "results": [],
                  "started_at": datetime.utcnow().isoformat(),
                  "finished_at": None})
    if background_tasks is not None:
        background_tasks.add_task(lambda x: asyncio.run(_bulk_worker(x)), ids)
    else:
        await _bulk_worker(ids)
    log.info(f"[bulk] started: {len(ids)} review(s), scope={tab}, "
             f"concurrency={_BULK_CONCURRENCY}")
    return {"ok": True, "queued": len(ids), "scope": tab, "review_ids": ids,
            **_bulk_public()}


def _bulk_public() -> dict:
    d = {k: v for k, v in _BULK.items() if k != "cancel"}
    d["remaining"] = max(0, _BULK["total"] - _BULK["done"])
    if _BULK["running"] and _BULK["started_at"] and _BULK["done"]:
        started = datetime.fromisoformat(_BULK["started_at"])
        per = (datetime.utcnow() - started).total_seconds() / _BULK["done"]
        d["eta_s"] = int(per * d["remaining"])
    else:
        d["eta_s"] = None
    return d


@router.post("/api/reviews/bulk-cancel")
def bulk_cancel():
    """Stops after the in-flight reviews finish - a pipeline killed mid-run
    would leave a half-written draft."""
    if not _BULK["running"]:
        return {"ok": True, "note": "nothing running"}
    _BULK["cancel"] = True
    log.info("[bulk] cancel requested")
    return {"ok": True, "cancelling": True, **_bulk_public()}


# ── Scenario change → RCA regeneration ──────────────────────────────────────
# The scenario select exists so a wrong or incomplete routing can be fixed;
# fixing it only matters if the RCA is re-judged against the corrected
# scenario checklists. This re-runs ONLY the RCA step from the draft's stored
# data (booking, timeline, insights, DSS, ticket facts) - no refetching.

class ScenarioRegen(BaseModel):
    # Empty = keep the draft's stored scenarios. That turns this endpoint
    # into the cheap prompt-iteration path: one model call over stored data,
    # no re-matching, no BigQuery, no Zendesk.
    scenarios: list[str] = []


@router.post("/api/reviews/{review_id}/regenerate-rca")
async def regenerate_rca(review_id: str, body: ScenarioRegen,
                         db: Session = Depends(get_session)):
    r = db.query(Review).filter(Review.id == review_id).first()
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not r or not d:
        raise HTTPException(404, "Not found")

    # THE ZENDESK EVENTS THAT WOULD NEVER LOAD, no matter how many times this
    # is clicked. regenerate-rca is the CHEAP path — it reuses stored data and
    # does not touch Zendesk. That is right when the timeline is already there.
    # But a booking confirmed AFTER the first run leaves the timeline empty and
    # stored, and reusing an empty timeline means the events never appear: the
    # associate re-runs, sees nothing, re-runs again — the "multiple reruns"
    # with no result.
    #
    # When there IS a booking id now and the stored timeline is empty, this is
    # that exact state. Route to the FULL pipeline once — it fetches Zendesk,
    # summarises the support frames and rebuilds the RCA on real events, which
    # a partial refetch here could not do (the frame summarisation lives in the
    # pipeline). Guarded so it is a no-op in every other case: a populated
    # timeline is left alone, an untraceable review has no id to search, and a
    # server with Zendesk not connected would only re-empty it.
    _bid_for_zd = (d.booking or {}).get("id") or getattr(r, "reference_number", None)
    if _bid_for_zd and not (d.timeline or []) and is_live("zendesk"):
        log.info(f"[regenerate-rca] {review_id}: booking {_bid_for_zd} has no "
                 f"stored timeline — running the full pipeline once so the "
                 f"Zendesk events load, rather than reusing an empty timeline")
        from server.pipeline import process_review
        try:
            await process_review(review_id)
        except Exception as e:
            log.exception(f"[regenerate-rca] full reprocess failed: {e}")
            raise HTTPException(502, f"The re-run could not fetch the Zendesk "
                                     f"events ({type(e).__name__}: {e}). The "
                                     f"draft is unchanged."[:300])
        d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
        return {"ok": True, "draft": _draft_dict(d) if d else None,
                "reprocessed": True,
                "note": "The booking had no events stored, so the full pipeline "
                        "ran and fetched them from Zendesk."}

    scenarios, _p, _o = settle_scenarios(
        [s for s in (body.scenarios or []) if s in SCENARIO_CHECKS])
    if scenarios:
        # Deduplicated BEFORE it is written back. The card sends
        # `[...scenarios, ...overlayScenarios]` and the overlays are already
        # in `scenarios`, so writing the body verbatim doubled the list on
        # every scenario edit and undid whatever had just been deleted.
        d.primary_scenario  = _p
        d.overlay_scenarios = _o
        d.scenarios         = scenarios
    else:
        # RCA-only re-run: keep whatever routing the draft already has.
        scenarios = [s for s in (d.scenarios or
                                 [d.primary_scenario] + (d.overlay_scenarios or [])) if s]

    from server.services import claude as claude_svc
    from server.services.rca_checklist import get_checklist
    from server.checklist import issue_questions_for
    checklist = await get_checklist(d.l1, d.l2)
    # Same voice reference the pipeline passes, or a regenerated reply drifts
    # out of the approved register while the pipeline's stays in it.
    try:
        from server.services.canned import get_canned_responses
        # `untraceable` too, or the cheap re-run path silently drops the
        # approved unable-to-trace macro that the full pipeline uses. Two
        # implementations of one decision is how they drift; this endpoint had
        # already drifted before anyone looked.
        canned_list = await get_canned_responses(
            d.l1, d.l2, d.sub_theme, r.body_english or r.body_original or "",
            untraceable=not (d.booking or {}))
    except Exception as e:
        canned_list = []
        log.warning(f"[regenerate-rca] canned tone lookup failed: {e}")
    # The model call can raise as well as return nothing, and only the second
    # was handled. An unconfigured or unreachable model came back as a bare
    # 500 "Internal Server Error" — which tells the associate their re-run
    # failed and nothing about why, and looks identical to a bug in the RCA
    # itself. Named here so the reason reaches the person who has to decide
    # whether to try again.
    try:
        rca_v3 = await claude_svc.generate_rca_v3(
            review_text=r.body_english or r.body_original or "",
            booking=d.booking or {},
            timeline=d.timeline or [],
            insights=d.insights or {},
            # THE EDITED DSS WINS. The lookup can match the wrong row, and
            # an associate who corrects it must not have the correction
            # ignored by the very re-run they corrected it for. rca_v3.dss is
            # where the card's edit lands; d.dss_rec is what the lookup found.
            # Two stores for one fact, and the prompt was reading the one the
            # person could not change.
            dss_rec=_dss_for_prompt(d),
            l1=d.l1 or "", l2=d.l2 or "",
            sub_theme=", ".join(d.sub_themes or ([d.sub_theme] if d.sub_theme else [])),
            support_summary=d.support_summary or "",
            checklist=checklist,
            review_id=review_id,
            timeline_raw=d.timeline_raw or [],
            ticket_facts=d.ticket_facts or {},
            scenarios_routed=scenarios,
            issue_questions=issue_questions_for(scenarios),
            canned_list=canned_list,
            support_frames=d.support_interaction_frames or [],
        )
    except Exception as e:
        log.exception(f"[regenerate-rca] model call failed: {e}")
        raise HTTPException(502, f"RCA regeneration failed - draft unchanged "
                                 f"({type(e).__name__}: {e})"[:300])
    if not rca_v3:
        raise HTTPException(502, "RCA regeneration returned nothing - draft unchanged")

    # The same validation the pipeline runs. Skipping it here was how an
    # unvalidated RCA reached the screen: re-run from the dashboard and the
    # coercions the pipeline applies simply did not happen.
    rca_notes: list = []
    try:
        from server.services.rca_v4_validate import validate as _validate_rca
        # The rows already on this draft, so a row an associate typed
        # survives a regenerate. Read here because the projection below
        # overwrites the column a few lines later.
        _confirmed = bool((d.booking or {}).get("id") and not d.candidate_state)
        # ONLY THE ROWS A PERSON TYPED. Passing the whole column carried every
        # model row the previous run produced forward forever — four
        # recommendation-shaped rows sat on a CO tab that no current gap
        # explained. `d.rca_v3` is still the PREVIOUS blob here; the projection
        # below overwrites it.
        from server.checklist import hand_typed_actions
        _keep, _unattributed = hand_typed_actions(
            d.actions_taken,
            ((d.rca_v3 or {}).get("what_went_wrong") or {}).get("gaps"))
        rca_v3, rca_notes = _validate_rca(rca_v3, scenarios,
                                          keep_actions=(_keep or None),
                                          booking_confirmed=_confirmed,
                                          events=(d.timeline or []),
                                          keep_unattributed=_unattributed)
        for _n in rca_notes:
            log.warning(f"[regenerate-rca] {review_id}: {_n}")
    except Exception as e:
        log.exception(f"RCA validation failed, keeping raw output: {e}")

    # Same projection the pipeline does on save.
    d.rca_v3 = rca_v3
    from server.prompts import RCA_PROMPT_VERSION
    d.rca_prompt_version = RCA_PROMPT_VERSION
    _prev = rca_v3.get("prevention")
    if isinstance(_prev, list):
        _prev = "\n".join(f"• {p}" for p in _prev if p)
    d.prevention             = _prev or d.prevention
    _aoi = rca_v3.get("area_of_improving")
    if _aoi:
        d.area_of_improving  = _aoi if isinstance(_aoi, list) else [_aoi]
    d.checklist_answers      = []
    # v4 columns — the queryable copy. Left unwritten, a re-run updated rca_v3
    # and left these holding the previous run's answer. The SAME projection the
    # pipeline uses: written out twice, the two paths drift, and the drift is
    # invisible because both look like working code.
    from server.services.rca_v4_validate import project_v4
    for _col, _val in project_v4(rca_v3).items():
        setattr(d, _col, _val)
    # v4 writes the reply and the resolution as part of the RCA. final_response
    # holds any human edit, so refreshing these does not overwrite one.
    if rca_v3.get("resolution"):
        d.resolution         = rca_v3["resolution"]

    # An untraceable review sends the approved macro verbatim, exactly as the
    # full pipeline does. Same reason: there is nothing to personalise, and a
    # rewritten macro is an unapproved paraphrase of approved copy.
    _verbatim = next((c for c in (canned_list or []) if c.get("why")), None)
    if _verbatim and _verbatim.get("response"):
        _reply = _verbatim["response"]
        _who = (r.author or "").strip().split(" ")[0]
        if _who:
            _reply = _reply.replace("<first name>", _who)
        rca_v3["suggested_response"] = _reply

    # PRESENCE, not truthiness. `if rca_v3.get("suggested_response")` left the
    # PREVIOUS reply in the column whenever the model returned null — which is
    # what prompt rule 20 asks it to do when no approved macro covers the
    # issue. The deliberate blank was overwritten by a stale reply on every
    # re-run, and the card showed an unapproved reply one Send from a public
    # review page. Same bug the pipeline had, in the endpoint people actually
    # press.
    # None when the model returned no reply key at all, which is a different
    # thing from a reply that failed to translate — the trail gets a line only
    # when there was actually a reply to put in a language.
    _lang_note = None
    if "suggested_response" in rca_v3:
        # THE REPLY GOES OUT IN THE REVIEW'S LANGUAGE, here too. This endpoint
        # is a second write path for the same field, and leaving it alone would
        # mean "↻ RCA only" quietly replaced a translated reply with the
        # English the model just wrote — the outgoing text reverting to a
        # language the guest may not read, with nothing on the card saying so.
        from server.services.reply_language import translate_outgoing
        _out, _proj, _of, _lang_note = await translate_outgoing(
            rca_v3["suggested_response"] or "", r, review_id)
        rca_v3["suggested_response"] = _out
        d.suggested_response  = _out
        d.response_english    = _proj or None
        d.response_english_of = _of

    # THE TRAIL. This endpoint wrote none, so a draft regenerated here kept the
    # trail from whenever it was last matched — and every disclosure the
    # analysis produces was simply absent. Absent reads as "nothing to report".
    #
    # Seen on a real draft: rca_v3 rewritten by the current prompt, stamp
    # updated, generated_at updated, stated_issue 61 words over its 60-word
    # ceiling — and a three-line trail carrying only the match, with no
    # coercion note and no reply-voice line anywhere.
    #
    # The matching half is kept: this endpoint did not re-match, so those
    # entries are still true. The RCA half is rebuilt, because it just was.
    import html as _html
    # THE PRE-CONFIRMATION BLOCK GOES FIRST. The filter below is right for one
    # run's worth of trail and wrong for two stacked: it drops RCA and reply
    # lines, so a superseded run's ZENDESK lines survive every regenerate.
    # That left "Zendesk was not searched — the lookup that never ran" above
    # "Zendesk contacts for 32885089: 4 found", both marked current.
    #
    # This endpoint does NOT re-fetch Zendesk, so the CURRENT run's Zendesk
    # lines are still true and are kept — which is why it cannot use the
    # pipeline's `matching_history` cut, and needs the bounded block instead.
    from server.pipeline import drop_superseded_block, superseded_trail_row
    _trail_now, _cut = drop_superseded_block(d.confidence_trail)
    _kept = [t for t in _trail_now
             if not str((t or {}).get("text", "")).startswith("<strong>RCA</strong>")
             # The reply was just rewritten and re-translated, so the previous
             # run's language line is about a reply that no longer exists.
             # Kept, it would report the OLD outcome beside the new text — a
             # stale "translated to IT" over a reply that fell back to English.
             and not str((t or {}).get("text", "")).startswith("<strong>Reply language")
             and not str((t or {}).get("text", "")).startswith("<strong>Reply voice")
             and not str((t or {}).get("text", "")).startswith("<strong>No approved macro")
             and not str((t or {}).get("text", "")).startswith("<strong>The reply is an approved macro")
             and "This run has not finished" not in str((t or {}).get("text", ""))]
    _sup = superseded_trail_row(_cut)
    if _sup:
        _kept.append(_sup)
    for _n in (rca_notes or []):
        _kept.append({"mark": "warn",
                      "text": f"<strong>RCA</strong> — {_html.escape(str(_n))}"})
    # Which language this endpoint left the reply in, and why. Without it a
    # regenerate that fell back to English looks exactly like one that
    # translated cleanly.
    if _lang_note:
        _kept.append(_lang_note)
    try:
        from server.pipeline import tone_entry
        from server.services.canned import (last_failure_reason, last_source,
                                            source_is_degraded)
        _te = tone_entry(canned_list or [], d.l1, d.l2, None,
                         last_failure_reason(),
                         last_source() if source_is_degraded() else "")
        if _te:
            _kept.append(_te)
    except Exception as _e:
        log.warning(f"[regenerate-rca] tone trail entry skipped: {_e}")
    d.confidence_trail = _kept
    d.generated_at           = datetime.utcnow()
    for _col in ("rca_v3", "overlay_scenarios", "issue_specific_answers",
                 "checklist_answers", "area_of_improving",
                 "guest_issues", "booking_logs", "flags",
                 # Belt and braces, and NOT load-bearing: the assignment
                 # above builds a new list, and SQLAlchemy detects a new
                 # object without help. It would be load-bearing the moment
                 # someone appends to the existing trail instead, which is the
                 # obvious next edit — a JSON column mutated in place is
                 # dropped on commit with no error.
                 # Mutation testing caught the earlier comment claiming this
                 # was what made the write stick. It was not.
                 "confidence_trail",
                 "takedown", "dss"):
        flag_modified(d, _col)
    db.commit()
    return {"ok": True, "rca_v3": rca_v3,
            "primary_scenario": d.primary_scenario,
            "overlay_scenarios": d.overlay_scenarios,
            "prevention": d.prevention,
            "issue_specific_answers": d.issue_specific_answers,
            "suggested_response": d.suggested_response or "",
            "resolution": d.resolution or "",
            "validation_notes": rca_notes,
            "area_of_improving": d.area_of_improving or []}


# ── NEW: Flag to Biz (two-step: draft, then send) ───────────────────────────

@router.post("/api/reviews/{review_id}/flag-to-biz")
async def flag_to_biz(review_id: str, body: FlagToBiz,
                       db: Session = Depends(get_session)):
    r = db.query(Review).filter(Review.id == review_id).first()
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not r or not d:
        raise HTTPException(404, "Not found")

    booking  = d.booking or {}
    insights = d.insights or {}
    vendor   = booking.get("vendorName") or booking.get("partner", "unknown")
    vid      = booking.get("vid", "?")
    completion = insights.get("vidCompletionRate", "?")

    # Where this will actually go. The draft step used to answer
    # "#biz-supply-ops" while the send step posted into the review's own Slack
    # thread - so the confirmation named a channel the message would never
    # reach, and anyone trusting it would assume the Biz team had been told.
    # One resolver, called by both steps, so the promise and the delivery
    # cannot drift apart.
    # There is one destination: the review's own Slack thread, the same thread
    # the RCA goes into. #biz-supply-ops was invented as a fallback here and
    # does not exist in the workspace, so a message sent to it went nowhere
    # while the UI reported success.
    thread_ch = (getattr(r, "slack_channel", "") or "")
    thread_ts = (getattr(r, "slack_ts", "") or "")
    if not (thread_ch and thread_ts) or thread_ch == "C_MANUAL":
        raise HTTPException(400, "This review has no Slack thread, so there is "
                                 "nowhere to flag it. Copy the message instead.")
    dest_channel, dest_parent = thread_ch, thread_ts
    dest_label = f"the review's Slack thread in #{thread_ch.lstrip('#')}"

    # Step 1: draft the message if not supplied
    if not body.message:
        drafted = await flag_to_biz_message(
            vendor_name=vendor, vid=vid,
            completion_pct=completion, market_avg="[market avg]",
            l1=d.l1 or "", l2=d.l2 or "",
            review_bid=(booking.get("id") or r.reference_number or "?"),
        )
        d.flag_to_biz_message = drafted
        d.flag_to_biz_state = "drafted"
        db.commit()
        return {
            "ok": True, "state": "drafted",
            "message": drafted,
            # The real destination, not a default that the send step overrides.
            "channel": dest_channel,
            "destination": dest_label,
            "in_thread": bool(dest_parent),
            "tag": body.tag or "",
        }

    # Step 2: send
    if body.send:
        # Into the review's own Slack thread, where the review was posted and
        # where whoever is watching it will see it. Posting a bare message to
        # a channel loses that context - it arrives as an orphan mentioning a
        # booking id, with the review it came from nowhere in sight.
        channel, parent = dest_channel, dest_parent
        tag = body.tag or ""
        full_msg = "\n".join(x for x in (tag, _biz_facts(body), body.message)
                             if x and x.strip()).strip()
        try:
            ts = await post_to_thread(channel, parent, full_msg)
            d.flag_to_biz_state = "sent"
            d.flag_to_biz_message = body.message

            # NO actions_taken ENTRY, by request. Flagging to Biz is a real
            # thing somebody did, but Actions Taken is the fixes and the rows
            # a person typed — a second writer into that column is how it came
            # to hold four different kinds of row under one heading. The flag
            # is recorded on the draft's own flag_to_biz state and in the
            # Slack thread it was posted to.

            m = db.query(ReviewMetric).filter(ReviewMetric.review_id == review_id).first()
            if m:
                m.flagged_to_biz = True
            db.commit()
            # Say where it landed. "sent" alone left the caller to assume
            # the channel the draft step had named.
            return {"ok": True, "state": "sent", "ts": ts,
                    "channel": channel, "destination": dest_label,
                    "in_thread": bool(parent)}
        except Exception as e:
            raise HTTPException(500, str(e))

    # Just save the edited draft
    d.flag_to_biz_message = body.message
    db.commit()
    return {"ok": True, "state": d.flag_to_biz_state}


# ── NEW: Action Taken add/update/delete ─────────────────────────────────────

@router.patch("/api/reviews/{review_id}/action")
def patch_action(review_id: str, body: ActionPatch,
                  db: Session = Depends(get_session)):
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not d:
        raise HTTPException(404, "Draft not found")
    if body.tab not in ACTION_TABS:
        raise HTTPException(400, f"Unknown tab: {body.tab}")

    # Copied for the same reason as in flag_to_biz: an in-place mutation of a
    # JSON column is invisible to SQLAlchemy, so every add, update and delete
    # through this endpoint reported success and saved nothing.
    actions = {k: list(v) for k, v in (d.actions_taken or {}).items()}
    for _t in ("sp", "customer", "business", "product", "ce"):
        actions.setdefault(_t, [])
    tab_list = list(actions.get(body.tab, []))

    if body.op == "add":
        if not body.action:
            raise HTTPException(400, "Missing 'action' payload")
        tab_list.append(body.action)
    elif body.op == "update":
        if body.index is None or body.index >= len(tab_list):
            raise HTTPException(400, "Bad index")
        tab_list[body.index] = {**tab_list[body.index], **(body.action or {})}
    elif body.op == "delete":
        if body.index is None or body.index >= len(tab_list):
            raise HTTPException(400, "Bad index")
        tab_list.pop(body.index)
    else:
        raise HTTPException(400, f"Unknown op: {body.op}")

    actions[body.tab] = tab_list
    d.actions_taken = actions
    flag_modified(d, "actions_taken")
    db.commit()
    return {"ok": True, "actions_taken": actions}


# ── NEW: Similar complaints refresh ─────────────────────────────────────────

@router.get("/api/reviews/{review_id}/similar")
async def similar(review_id: str, db: Session = Depends(get_session)):
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not d or not d.booking:
        raise HTTPException(404, "No booking")
    support, reviews = await get_similar_complaints(d.booking)
    d.similar_support = support
    d.similar_reviews = reviews
    db.commit()
    return {"similar_support": support, "similar_reviews": reviews}


# ── Existing send + reporting (unchanged shape) ─────────────────────────────

def has_rca_to_post(d) -> bool:
    """Is there an analysis on this draft worth putting in a Slack thread?

    Send used to post unconditionally. For an untraceable or an unconfirmed
    review that means posting the SHELL of an RCA — a header, a booking id of
    "—", and a dozen empty sections — into a channel leadership reads, which
    is worse than posting nothing: it looks like an analysis that found
    nothing rather than one that was never written.

    A hand-written thread override counts. Somebody typed it; that is the
    clearest possible statement that there is something to post.

    Driveable on purpose. The condition matters more than the endpoint around
    it, and a source assertion on `if has_rca` cannot tell a reachable guard
    from an unreachable one.
    """
    if d is None:
        return False
    if (getattr(d, "slack_thread_override", None) or "").strip():
        return True
    v3 = getattr(d, "rca_v3", None)
    if isinstance(v3, dict):
        # what_went_wrong is a WRAPPER — {"guest_issues": [...]} — and the
        # validator writes the wrapper whether or not there are issues in it.
        # A truthiness test on the key alone calls an empty analysis an
        # analysis, which is exactly the shell this guard exists to stop.
        wwr = v3.get("what_went_wrong")
        if isinstance(wwr, dict):
            if wwr.get("guest_issues"):
                return True
        elif wwr:
            return True
        if any(v3.get(k) for k in ("stated_issue", "tldr", "l1", "flags",
                                   "resolution")):
            return True
    return bool(getattr(d, "l1", None)
                or getattr(d, "what_went_wrong_bullets", None)
                or getattr(d, "wwr_scenarios", None)
                or getattr(d, "guest_issues", None))


@router.post("/api/reviews/{review_id}/send")
async def send_review(review_id: str, db: Session = Depends(get_session)):
    r = db.query(Review).filter(Review.id == review_id).first()
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not r or not d:
        raise HTTPException(404, "Not found")

    d.sent_at = datetime.utcnow()
    r.status  = "sent"
    ts = None
    posted_why = ""
    # WHICH ROUTE THIS IS, read BEFORE the post below can change it. Marking a
    # review sent from beside the Post-to-thread button and sending it from
    # the header are the same action with the same guard — what differs is
    # whether the RCA was already in the thread when we got here, and that is
    # a fact we can observe rather than a claim the caller makes.
    _already_posted = bool(d.rca_posted_at)
    # Send posts the RCA as well as closing the review — but "Post to thread"
    # exists precisely so the RCA can go to the team while the reply is still
    # being edited, and using both put the same RCA in the thread twice. The
    # post here is for the case where nobody used that button.
    #
    # And it only fires when there IS an RCA. Reaching Sent from a review with
    # no analysis is now a supported route (see /close), so this path can be
    # entered with nothing to say — and an empty RCA posted into the team
    # channel reads as an analysis that came up blank.
    if r.slack_channel == "C_MANUAL":
        posted_why = "added by hand — no Slack thread to post into"
    elif d.rca_posted_at:
        posted_why = "already posted to the thread"
    elif not has_rca_to_post(d):
        posted_why = ("no RCA on this draft — nothing was posted to Slack. "
                      "This review was closed out, not analysed.")
        log.info(f"[send] {review_id}: no RCA to post, marked sent only")
    else:
        rca_text = (d.slack_thread_override or "").strip() or format_rca_slack(r, d)
        ts = await post_to_thread(r.slack_channel, r.slack_ts, rca_text)
        if ts:
            d.rca_posted_at = datetime.utcnow()
        else:
            posted_why = "Slack did not accept the post"
    # Recorded so the Sent tab can tell the three apart. `no_rca` is its own
    # value rather than folded into `reply`: a review sent with nothing to
    # post is a different outcome from one whose analysis went out, and
    # merging them is the silent-zero bug wearing a status field.
    r.sent_route = ("rca_posted" if _already_posted
                    else "no_rca" if not has_rca_to_post(d)
                    else "reply")
    m = db.query(ReviewMetric).filter(ReviewMetric.review_id == review_id).first()
    if m:
        if r.received_at:
            m.minutes_to_send = (datetime.utcnow() - r.received_at).total_seconds() / 60
        m.sent = True
    db.commit()
    # PHASE TWO OF THE SHEET: the arrival row is filled in. It NEVER appends —
    # arrival created the row, so a completion that finds none means that hook
    # did not run, and appending here would hide it and race a late arrival
    # into a duplicate. Logged, not raised: the review is finished either way.
    try:
        from server.services.sheet_export import on_review_finished
        on_review_finished(r, d)
    except Exception as _e:
        log.warning(f"[sheet] {review_id}: completed row not written: {_e}")
    # `posted` is a separate fact from `ok`. The caller used to get {"ok":
    # true, "ts": null} for a post that was skipped, for one that failed and
    # for one that was never attempted, and had to guess which.
    return {"ok": True, "ts": ts, "posted": bool(ts), "why": posted_why,
            "sent_route": r.sent_route}


class CloseOut(BaseModel):
    """Why this review is being closed without a reply."""
    reason: str | None = None


# Default reasons per bucket. Not decoration: a Sent review whose reason is
# blank cannot be told from one whose reason was never asked for.
_CLOSE_REASONS = {
    "untraceable": "Untraceable — asked the guest for a booking reference; "
                   "there is no RCA and no reply to post.",
    "candidates":  "Closed without confirming a booking — none of the "
                   "candidates was this guest's.",
    "processing":  "Closed before the pipeline produced a draft.",
    "identified":  "Closed without posting an RCA.",
    "sent":        "Already sent; closed again.",
}


@router.post("/api/reviews/{review_id}/close")
async def close_review(review_id: str, body: CloseOut | None = None,
                       db: Session = Depends(get_session)):
    """Move a review to Sent WITHOUT posting anything.

    THE GAP THIS FILLS. /send needs a review AND a draft and 404s otherwise,
    and the Send button lives in the RCA column header — which is replaced by
    the candidate picker for a review in candidate state and by the
    ask-the-guest panel for an untraceable one. So two whole buckets had no
    route to Sent at all: the work was finished and the card could not be put
    down.

    Its own action rather than a flag on /send, because it is a different
    piece of work. Sending means "the reply and the RCA have gone"; closing
    out means "there is nothing to send and this is finished". Overloading one
    verb with both is how a Sent tab stops meaning anything.

    Never posts to Slack. There is nothing to post — that is the premise.
    """
    r = db.query(Review).filter(Review.id == review_id).first()
    if not r:
        # The one genuine not-found. A missing DRAFT is not: it is the
        # commonest state this endpoint exists to serve.
        raise HTTPException(404, f"No review {review_id}. The id comes from "
                                 f"the inbox; try GET /api/reviews to list them.")
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()

    from server.tiers import classify
    bucket = classify(r, d)
    reason = ((body.reason if body else None) or "").strip() or \
        _CLOSE_REASONS.get(bucket, "Closed out.")

    now = datetime.utcnow()
    r.status       = "sent"
    r.closed_at    = now
    r.close_reason = reason
    r.sent_route   = "closed"
    if d is not None:
        d.sent_at = now
        # On the trail, because the trail is what a reader opens to find out
        # what happened to a review. A review that appears in Sent with no
        # RCA and no explanation is the "did it run?" ambiguity again, wearing
        # a different hat.
        trail = list(d.confidence_trail or [])
        trail.append({"mark": "warn",
                      "text": f"<strong>Closed out</strong> from {bucket} — "
                              f"{reason} Nothing was posted to Slack."})
        d.confidence_trail = trail
        flag_modified(d, "confidence_trail")

    m = db.query(ReviewMetric).filter(ReviewMetric.review_id == review_id).first()
    if m:
        if r.received_at:
            m.minutes_to_send = (now - r.received_at).total_seconds() / 60
        m.sent = True
    db.commit()
    # PHASE TWO OF THE SHEET: the arrival row is filled in. It NEVER appends —
    # arrival created the row, so a completion that finds none means that hook
    # did not run, and appending here would hide it and race a late arrival
    # into a duplicate. Logged, not raised: the review is finished either way.
    try:
        from server.services.sheet_export import on_review_finished
        on_review_finished(r, d)
    except Exception as _e:
        log.warning(f"[sheet] {review_id}: completed row not written: {_e}")
    log.info(f"[close] {review_id}: closed from {bucket} — {reason}")
    return {"ok": True, "closed_from": bucket, "reason": reason,
            "posted": False, "had_draft": d is not None}


class EnglishReplyBody(BaseModel):
    """The English working text an associate just edited."""
    english: str
    # The guest's language, when the card had to ask for it. Sent only from
    # the unknown-language case, where the review is known NOT to be English
    # (its text was translated inbound) but nothing recorded which language it
    # was. Naming it here is what unblocks the rewrite.
    language: str | None = None


@router.post("/api/reviews/{review_id}/apply-english-reply")
async def apply_english_reply(review_id: str, body: EnglishReplyBody,
                              db: Session = Depends(get_session)):
    """
    Apply an edit made in the English box to the reply that goes to the guest.

    The outgoing reply is stored in the GUEST'S language and is the one store
    for what gets sent. The English box is a projection of it, so an edit
    there is not a save — it is a request to rewrite the outgoing reply
    through a translation.

    THE FAILURE CONTRACT, which is the whole point of this endpoint:
    if the translation fails, the outgoing reply is left EXACTLY as it was and
    the caller is told the English edit was not applied and why. A half-apply —
    English stored, outgoing untouched — would leave the card showing an edit
    that will never reach the guest, which is the failure this codebase
    punishes hardest. Nothing is written on any failure path here.
    """
    from server.services.reply_language import (is_english, outgoing,
                                                set_english_projection,
                                                language_state)
    r = db.query(Review).filter(Review.id == review_id).first()
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not r or not d:
        raise HTTPException(404, "Not found")

    english = (body.english or "").strip()
    if not english:
        raise HTTPException(400, "There is no English text to apply.")

    before = outgoing(d)
    st = language_state(r)

    # An English review has ONE box: the English text IS the outgoing reply,
    # no translation happens and nothing may imply one did.
    if is_english(r):
        d.final_response = english
        d.response_english = None
        d.response_english_of = None
        db.commit()
        return {"ok": True, "translated": False, "language": st["language"],
                "outgoing": english, "english": english,
                "english_state": "same",
                "note": "The review is in English, so this text is the reply "
                        "itself — nothing was translated."}

    # No language recorded. Translating to a language we do not know is not
    # possible, and picking English silently is how a guest gets a reply they
    # cannot read. Refuse, and say what would make it work.
    if st["state"] == "unknown":
        # THE CARD CAN NOW SUPPLY IT. The review is known not to be English —
        # its text was translated on the way in — and only the NAME of the
        # language was missing, which is a thing the associate reading the
        # review can see at a glance. Recorded on the review, so the next run
        # and every later render stop asking.
        named = (body.language or "").strip()
        if not named:
            # WHAT ACTUALLY WORKS FROM HERE, first. This used to lead with
            # "Name the guest's language on the card" — and the card has no
            # field to name it in. The language input was deliberately removed
            # when detection replaced it, so that sentence pointed the reader
            # at a control that does not exist, which is worse than saying
            # nothing. Editing the top box is the action that always works.
            raise HTTPException(
                409, "The outgoing reply was left unchanged: " + st["why"] +
                     " Type the reply in the guest's language in the top box "
                     "— it is the text that gets sent, and it saves as you "
                     "write. Re-running the review will try the language "
                     "check again.")
        log.info(f"[apply-english] {review_id}: language set to {named!r} by "
                 f"the associate — it was unrecorded, not English")
        r.language = named
        db.commit()
        st = language_state(r)
        if st["state"] != "translated":
            raise HTTPException(
                409, f"The outgoing reply was left unchanged: {named!r} was "
                     f"recorded but the review still does not read as "
                     f"translatable ({st['why']}).")

    lang = st["language"]
    from server.services import claude as claude_svc
    try:
        translated = await claude_svc.translate_to(english, lang, review_id)
    except Exception as e:
        log.exception("[apply-english] translation call failed")
        raise HTTPException(
            502, f"The English edit was NOT applied and the outgoing {lang} "
                 f"reply is unchanged: the translation call failed ({e}). "
                 f"Try again, or edit the {lang} reply directly.")
    translated = (translated or "").strip()
    if not translated:
        raise HTTPException(
            502, f"The English edit was NOT applied and the outgoing {lang} "
                 f"reply is unchanged: the translation returned nothing. Try "
                 f"again, or edit the {lang} reply directly.")

    set_english_projection(d, english, translated)
    db.commit()
    log.info(f"[apply-english] {review_id}: outgoing reply rewritten in {lang}")
    return {"ok": True, "translated": True, "language": lang,
            "outgoing": translated, "english": english,
            "english_state": "current",
            "replaced": before,
            "note": f"The {lang} reply below is what goes to the guest."}


@router.post("/api/reviews/{review_id}/resolve-reply-language")
async def resolve_reply_language(review_id: str,
                                 db: Session = Depends(get_session)):
    """Name the guest's language from their OWN words, and put the reply in it.

    Replaces a text box that asked the associate to type "Spanish". The guest's
    original review is in `body_original`; identifying its language is not a
    human's job when the text is right there, and every associate typing it is
    another chance to type it differently.

    Two steps, and the second only runs if the first succeeds:

      1. `resolve_language` reads the ORIGINAL text and records what it finds.
         It leaves the column alone when it cannot tell — a wrong language here
         sends the guest a reply they cannot read, so declining is the safe
         direction and the response says which happened.
      2. If the reply is still sitting in English on a review that is not, it
         is translated into the detected language and the English is kept as
         the working copy. That is the state the card wants to be in for a
         non-English review, and it is now reached without anyone pressing
         anything.

    NOTHING IS HALF-WRITTEN. If the translation fails, the language is still
    recorded (it is a true fact about the review and re-deriving it costs
    another model call) and the reply is left EXACTLY as it was, with
    `translated: false` and the reason. A reply that looks translated and is
    not is the failure this whole area exists to prevent.
    """
    from server.services.reply_language import (resolve_language, outgoing,
                                                english_view, language_state,
                                                set_english_projection)
    r = db.query(Review).filter(Review.id == review_id).first()
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not r or not d:
        raise HTTPException(404, "Not found")

    res = await resolve_language(r)
    if res["outcome"] == "detected":
        db.commit()
    if res["outcome"] in ("undetected", "unavailable", "failed"):
        # EVERY OUTCOME THAT LEFT THE LANGUAGE UNESTABLISHED, listed rather
        # than defaulted — `skipped_english` used to be in here and it was the
        # bug: it meant "body_english was empty, so the review is English",
        # which is equally what a crashed translate call leaves behind.
        #
        # Not an HTTP error. All three are answers: the detector is switched
        # off on this server, or it read the review and could not name the
        # language, or the call raised. `res["why"]` says which, the card
        # prints it, and the reply keeps BOTH boxes either way — an
        # unestablished language never collapses to a single English box.
        return {"ok": True, "outcome": res["outcome"], "translated": False,
                "language": res["language"], "note": res["why"],
                "response_language": language_state(r),
                "english_view": english_view(r, d),
                "outgoing": outgoing(d)}

    _st = language_state(r)
    if _st["state"] == "english":
        # DETECTED AS ENGLISH, SO THERE IS NOTHING TO TRANSLATE. Without this
        # the reply was sent to `translate_to(reply, "English")` — English into
        # English — which is a model call that costs money, round-trips the
        # associate's wording through a paraphrase, and stores the result as
        # though it were the guest's-language version.
        #
        # It could not happen before because an English review never reached
        # detection at all: the old shortcut returned `skipped_english` first.
        # Removing that shortcut is what exposed this, and it is the reason
        # the state is re-read here rather than only the language name.
        return {"ok": True, "outcome": res["outcome"], "translated": False,
                "language": _st["language"],
                "note": f"{res['why']}. The review is in English, so the reply "
                        f"goes out as written and there is nothing to "
                        f"translate.",
                "response_language": _st,
                "english_view": english_view(r, d),
                "outgoing": outgoing(d)}

    lang = _st["language"]
    before = outgoing(d)
    already = (getattr(d, "response_english", "") or "").strip()
    if not before or already:
        # Either there is no reply to translate, or the English working copy
        # already exists and the pair is whatever the associate last made it.
        # Re-translating over that would silently discard their edit.
        _why = (f"{res['why']}. There is no drafted reply to translate."
                if not before else
                f"{res['why']}. The reply already has an English working copy, "
                f"so it was left as it is — retranslating would discard "
                f"whatever was last edited there.")
        return {"ok": True, "outcome": res["outcome"], "translated": False,
                "language": lang,
                "note": _why,
                "response_language": language_state(r),
                "english_view": english_view(r, d),
                "outgoing": before}

    from server.services import claude as claude_svc
    try:
        translated = (await claude_svc.translate_to(before, lang, review_id) or "").strip()
    except Exception as e:
        log.exception("[resolve-language] translation call failed")
        translated = ""
    if not translated:
        return {"ok": True, "outcome": res["outcome"], "translated": False,
                "language": lang,
                "note": f"{res['why']}. The reply was NOT translated — the "
                        f"translation returned nothing, so it is still the "
                        f"English above, unchanged. Edit it in {lang}, or use "
                        f"the English box to retry.",
                "response_language": language_state(r),
                "english_view": english_view(r, d),
                "outgoing": before}

    set_english_projection(d, before, translated)
    db.commit()
    log.info(f"[resolve-language] {review_id}: detected {lang}, reply translated")
    return {"ok": True, "outcome": res["outcome"], "translated": True,
            "language": lang,
            "note": f"{res['why']}. The reply was drafted in English and "
                    f"translated into {lang}; the {lang} text is what goes to "
                    f"the guest.",
            "response_language": language_state(r),
            "english_view": english_view(r, d),
            "outgoing": translated}


class SlackPostBody(BaseModel):
    """The exact text to post, as the dashboard is showing it.

    Optional, because a caller with no opinion should still get the composed
    RCA. But when it IS sent it wins over everything, and that is the whole
    point: the dashboard lets an associate switch sections off, and the server
    has no way to know which — it was rebuilding the full RCA from the draft
    and posting that, so a post trimmed to one section arrived with all twelve.
    """
    text: str | None = None


@router.post("/api/reviews/{review_id}/post-rca")
async def post_rca_to_thread(review_id: str, force: bool = False,
                             body: SlackPostBody | None = None,
                             db: Session = Depends(get_session)):
    """
    Post the RCA into the review's own Slack thread, without marking the
    review sent.

    Send does two things at once - post the RCA and close the review - which
    is wrong for the common case: the RCA goes to the team for comment while
    the guest reply is still being edited, and the reply is pasted into
    Trustpilot by hand anyway. This posts the RCA and nothing else, so the
    two halves can happen in either order.
    """
    r = db.query(Review).filter(Review.id == review_id).first()
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not r or not d:
        raise HTTPException(404, "Not found")
    if r.slack_channel == "C_MANUAL" or not r.slack_ts:
        raise HTTPException(400, "This review was added by hand, so it has no "
                                 "Slack thread to post into. Copy the post instead.")
    # Posting twice is not a no-op: it drops a second copy of the RCA into a
    # thread people are reading, and nothing about the button said it had
    # already gone. A repeat has to be asked for.
    if d.rca_posted_at and not force:
        return {"ok": True, "already_posted": True, "ts": None,
                "posted_at": d.rca_posted_at.isoformat()}

    # What the caller is looking at beats what the server would compose. The
    # order is: the text sent with this request, then the saved override, then
    # a fresh render — each one a step further from what is on the associate's
    # screen, and only taken because the nearer one is absent.
    sent = ((body.text if body else None) or "").strip()
    text = sent or (d.slack_thread_override or "").strip() or format_rca_slack(r, d)
    if sent and sent != (d.slack_thread_override or "").strip():
        # Posting is also a save. Otherwise the thread and the dashboard show
        # different posts and neither is wrong about what it holds.
        d.slack_thread_override = sent
    ts = await post_to_thread(r.slack_channel, r.slack_ts, text)
    if ts is None and not MOCK_MODE:
        # NOTHING WAS POSTED, SO NOTHING IS MARKED. The review stays exactly
        # where it is — rca_posted_at is not set below, and the Sent tab never
        # learns about this. A Sent row for a post Slack refused would be a
        # lie, and the one place that lie is unrecoverable is the one place it
        # matters: the tab people use to decide what still needs doing.
        #
        # What the reader gets instead is the reason and whether to click
        # again. See slack.post_failure_sentence for the three verdicts.
        from server.services.slack import last_post_failure
        f = dict(last_post_failure)
        if not f.get("why"):
            # post_to_thread returned None without recording a reason. That is
            # a real gap, and saying so beats naming a cause we did not
            # establish — the previous message asserted channel membership on
            # every failure, including ones it could not have been.
            f = {"code": "", "verdict": "manual",
                 "why": "the post did not go through and Slack gave no reason",
                 "next": "copy the post into the thread by hand"}
        raise HTTPException(502, detail={
            "message": f"Not posted — {f['why']}. To fix: {f['next']}.",
            "slack_error": f.get("code") or "",
            "verdict": f.get("verdict"),
            "retryable": f.get("verdict") in ("retry", "fix"),
            "still_here": True,
        })
    d.rca_posted_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "already_posted": False, "ts": ts,
            "posted_at": d.rca_posted_at.isoformat()}


@router.post("/api/reviews/{review_id}/reprocess")
async def reprocess_review(
    review_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    r = db.query(Review).filter(Review.id == review_id).first()
    if not r:
        raise HTTPException(404, "Review not found")

    # Re-run means REDO THE MATCHING. A previous confirmation must not survive
    # it: the pipeline honours selected_candidate_bid and skips matching
    # entirely, so leaving it set made Re-run a no-op that reported
    # "Associate confirmed ... matching skipped" and extracted no indicators,
    # while the card still read "Possible matches — associate to confirm".
    _d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    _was_confirmed = bool(_d and _d.selected_candidate_bid)
    if _was_confirmed:
        log.info(f"[reprocess] {review_id}: clearing confirmation "
                 f"{_d.selected_candidate_bid} — re-run redoes matching and "
                 f"shows the options again")
        _d.selected_candidate_bid = None
        db.commit()

    # Re-fetch the original Slack message and re-parse it before re-running.
    # Reviews ingested by an older parser lost content that parser discarded —
    # the review headline, and any attachment field that was not a bare booking
    # id (Trustpilot's free-text "Reference number", where guests put the venue).
    # Re-running the pipeline over the stored text can never recover that, so
    # every re-run starts by pulling the message again.
    refreshed = False
    if r.slack_ts and r.slack_channel and r.slack_channel not in ("C_MANUAL", "VECTORSHIFT"):
        try:
            from server.services.slack import fetch_message, parse_review
            ev = await fetch_message(r.slack_channel, r.slack_ts)
            if ev:
                parsed = parse_review({**ev, "ts": r.slack_ts, "channel": r.slack_channel})
                new_body = (parsed.get("body_original") or "").strip()
                if new_body and new_body != (r.body_original or "").strip():
                    r.body_original = new_body
                    r.body_english  = None      # force re-translation of new text
                    refreshed = True
                if parsed.get("reference_number") and not r.reference_number:
                    r.reference_number = parsed["reference_number"]
                    refreshed = True
                # THE PUBLISH DATE, for reviews ingested before it was read.
                # Their received_at is the Slack arrival time, and without
                # this a re-run would leave every existing review showing the
                # relay date under "Review date" and on the timeline for ever
                # — the fix would apply only to reviews that had not arrived
                # yet, which is the half that was never wrong.
                _pub = parsed.get("published_at")
                if _pub and _pub != r.received_at:
                    log.info(f"[reprocess] {review_id}: review date corrected "
                             f"{r.received_at} → {_pub} (from "
                             f"{parsed.get('published_at_source') or 'the payload'})")
                    r.received_at = _pub
                    refreshed = True
                if refreshed:
                    db.commit()
                    log.info(f"[reprocess] {review_id}: refreshed from Slack "
                             f"({len(new_body)} chars)")
        except Exception as e:
            log.warning(f"[reprocess] {review_id}: Slack refresh failed, "
                        f"re-running on stored text: {e}")

    # Through the batch runner, like every other path. Background-task
    # exceptions were swallowed entirely: the pipeline died, generated_at never
    # moved, and the dashboard polled for three minutes before giving up —
    # indistinguishable from "re-run did nothing". The runner records the
    # failure on the draft and bounds the run, so a wedged re-run stops saying
    # it is working after RUN_TIMEOUT_S instead of for ever.
    #
    # force_candidates: re-running a review whose booking was already confirmed
    # means the associate wants the choice back, so matching must not
    # auto-promote its way straight into another confirmed state. An explicit
    # Re-run always presents the options. Gating this on "was a confirmation
    # just cleared" was wrong: once the first re-run cleared it, every later one
    # saw nothing to clear, auto-promoted the best match straight to Tier 1, and
    # the associate never got the picker back. Clicking Re-run IS the request to
    # choose again.
    from server.pipeline import run_batch_sync
    background_tasks.add_task(run_batch_sync, [review_id], "re-run", True)
    return {"ok": True, "review_id": review_id, "refreshed_from_slack": refreshed}


@router.get("/api/reviews/{review_id}/progress")
def review_progress(review_id: str):
    """
    Where the in-flight pipeline run for this review is, or idle.

    Re-run is a dozen sequential model calls plus the warehouse queries -
    minutes with no output. The button used to show a static spinner and
    nothing else, which made "working" and "dead" the same picture; the
    dashboard polls this instead and names the stage.

    running: False means no run in flight IN THIS PROCESS. That is also what
    a restart yields mid-run - the BackgroundTask dies with the process - so
    the client treats it as "no live signal" and falls back to its
    generated_at poll rather than declaring the run finished.

    `running` used to be `bool(entry)`, which answered a different question:
    whether an entry existed, not whether anything was still happening. A run
    blocked inside a synchronous model call keeps its entry untouched, so the
    button counted up for half an hour against a stage that had not moved
    since the first second. `state`, `since_progress_s` and `stalled_after_s`
    come from the one judgement in server/tiers.py, so the button and the
    inbox row cannot disagree about the same run.
    """
    from server.pipeline import PIPELINE_PROGRESS
    from server.tiers import liveness, STALL_AFTER_S
    e = PIPELINE_PROGRESS.get(review_id)
    if not e:
        return {"running": False, "state": "", "stalled_after_s": STALL_AFTER_S}
    import time as _t
    state, since = liveness(e)
    return {"running": state in ("running", "queued"), "state": state,
            "step": e["step"], "total": e["total"], "stage": e["stage"],
            "elapsed_s": int(_t.time() - e["started_at"]),
            "since_progress_s": since, "stalled_after_s": STALL_AFTER_S,
            "queue_position": e.get("queue_position"),
            "queue_size": e.get("queue_size")}


@router.get("/api/reporting")
def reporting(db: Session = Depends(get_session)):
    metrics = (db.query(ReviewMetric)
               .order_by(ReviewMetric.received_at.desc())
               .limit(500).all())

    total        = len(metrics)
    sent         = sum(1 for m in metrics if m.sent)
    auto_matched = sum(1 for m in metrics if m.auto_matched)
    dss_used     = sum(1 for m in metrics if m.dss_connected)
    biz_flagged  = sum(1 for m in metrics if m.flagged_to_biz)
    times        = [m.minutes_to_send for m in metrics if m.minutes_to_send]
    avg_mins     = round(sum(times) / len(times), 1) if times else None

    l1_counts, l2_counts, tier_counts, by_rating = {}, {}, {}, {}
    for m in metrics:
        if m.l1: l1_counts[m.l1] = l1_counts.get(m.l1, 0) + 1
        if m.l2: l2_counts[m.l2] = l2_counts.get(m.l2, 0) + 1
        k = f"Tier {m.match_tier}" if m.match_tier else "No match"
        tier_counts[k] = tier_counts.get(k, 0) + 1
        by_rating[str(m.rating or "?")] = by_rating.get(str(m.rating or "?"), 0) + 1

    return {
        "total":               total,
        "sent":                sent,
        "auto_matched":        auto_matched,
        "dss_used":            dss_used,
        "biz_flagged":         biz_flagged,
        "avg_minutes_to_send": avg_mins,
        "l1_breakdown":        sorted(l1_counts.items(), key=lambda x: -x[1]),
        "l2_breakdown":        sorted(l2_counts.items(), key=lambda x: -x[1]),
        "tier_breakdown":      tier_counts,
        "by_rating":           by_rating,
    }


# ── VectorShift bridge ───────────────────────────────────────────────────────
# VS can call Zendesk directly, but BigQuery needs OAuth token signing a VS API
# node can't do — so VS fetches both through these endpoints (this app already
# holds the credentials), and posts its finished RCA back to /api/vs-intake.
# Auth: set VS_API_KEY in env; callers pass it as the X-VS-Key header.
from fastapi import Header


def _vs_auth(x_vs_key: str | None):
    expected = os.environ.get("VS_API_KEY", "")
    if expected and x_vs_key != expected:
        raise HTTPException(401, "bad or missing X-VS-Key")


@router.get("/api/vs/booking/{bid}")
async def vs_booking(bid: str, x_vs_key: str | None = Header(default=None)):
    """Booking lookup for VectorShift (BigQuery verify_bid passthrough)."""
    _vs_auth(x_vs_key)
    from server.services.bigquery_patch import verify_bid
    row = await asyncio.get_running_loop().run_in_executor(None, verify_bid, bid)
    if not row:
        raise HTTPException(404, f"BID {bid} not found in BigQuery")
    return {"booking": row}


@router.get("/api/vs/zendesk/{bid}")
async def vs_zendesk(bid: str, x_vs_key: str | None = Header(default=None)):
    """Zendesk tickets for VectorShift: raw bodies + concatenated text."""
    _vs_auth(x_vs_key)
    from server.services import zendesk as zd
    timeline, extracted, meta = await zd.get_timeline(booking_id=bid)
    raw = [b for b in (meta.get("timeline_raw") or []) if b and str(b).strip()]
    return {
        "ticket_ids":              meta.get("ticket_ids", []),
        "zendesk_requester_name":  meta.get("zendesk_requester_name", ""),
        "tickets_text":            "\n\n---\n\n".join(str(b)[:2000] for b in raw[:20]),
        "raw_events_json":         [{"idx": i, "raw_body": str(b)[:2000]}
                                    for i, b in enumerate(raw[:20])],
        "extracted_booking_fields": extracted or {},
    }


@router.get("/api/vs/search")
async def vs_search(query: str, limit: int = 50,
                    x_vs_key: str | None = Header(default=None)):
    """
    Zendesk ticket search for VectorShift.

    VectorShift's Zendesk integration has no ticket-search action, and Zendesk
    is retiring API tokens (rollout began 2026-07-28), so a VS-side credential
    would mean registering an OAuth client purely for this. This app already
    holds an auto-refreshing Zendesk connector, so VS calls here instead.

    `query` may hold SEVERAL queries, one per line. All of them run and the
    tickets are returned as one deduplicated set. This matters because no single
    Zendesk query is reliable on its own: `requester:"Fredrik Olsen"` needs an
    exact name and misses "Fredrik Martin Olsen", while a free-text venue query
    misses a ticket whose experience is worded differently from the review. Each
    query recalls a different slice, and a guest whose name does not match
    exactly must still be findable.

    Running them here rather than in the pipeline is a transport detail: the
    VectorShift API node makes one call per execution. It is NOT a filter - the
    tickets come back raw and the matching rules stay in the pipeline, where
    ticket_signals() reads each ticket's own booking id, guest name, experience,
    city and party size. Zendesk only has to surface candidates; deciding which
    one the review is about happens downstream.

    Tickets are shaped the way the Zendesk REST API returns them - custom_fields
    as a list of {"id": int, "value": any} - because that is what the pipeline
    reads.
    """
    _vs_auth(x_vs_key)
    from server.services import zendesk as zd

    queries = [q.strip() for q in str(query or "").splitlines() if q.strip()]
    if not queries:
        raise HTTPException(400, "query is empty")

    z = zd._get_client()
    if z is None:
        raise HTTPException(503, "Zendesk is not configured on this deployment")

    def _fields(t):
        out = []
        for f in (getattr(t, "custom_fields", None) or []):
            fid = f.get("id") if isinstance(f, dict) else getattr(f, "id", None)
            val = f.get("value") if isinstance(f, dict) else getattr(f, "value", None)
            if fid is not None:
                out.append({"id": fid, "value": val})
        return out

    loop = asyncio.get_running_loop()
    cap = max(1, min(limit, 200))
    by_id, ran, failed = {}, [], []

    for q in queries:
        try:
            tickets = await loop.run_in_executor(None, zd._search_with_retry, z, q)
        except Exception as e:
            # One query failing must not lose the others. A too-broad query
            # trips Zendesk's result cap, and that is a reason to drop that
            # query, not to report the review as having no tickets.
            failed.append({"query": q, "error": str(e)[:200]})
            continue
        ran.append({"query": q, "count": len(tickets)})
        for t in tickets:
            tid = getattr(t, "id", None)
            if tid is None or tid in by_id:
                continue
            created = getattr(t, "created_at", None)
            by_id[tid] = {
                "id":            tid,
                "created_at":    str(created) if created else "",
                "subject":       getattr(t, "subject", "") or "",
                "custom_fields": _fields(t),
            }
            if len(by_id) >= cap:
                break

    # Every query failing is an outage, not an empty result set. Say so, or the
    # pipeline routes the review to untraceable and the cause is invisible.
    if failed and not ran:
        raise HTTPException(502, f"all Zendesk searches failed: {failed[0]['error']}")

    results = sorted(by_id.values(), key=lambda r: r["created_at"], reverse=True)
    return {"count": len(results), "results": results,
            "queries_run": ran, "queries_failed": failed}


@router.get("/api/vs/insights")
async def vs_insights(tid: str, vid: str, tgid: str = "", l1: str = "", l2: str = "",
                      window: str = "", visit_date: str = "",
                      x_vs_key: str | None = Header(default=None)):
    """
    Experience insights for VectorShift.

    Seven BigQuery queries run in parallel: similar and total reviews, similar
    and total support queries, the rating window, the vendor's completion rate,
    and same-day fulfilment issues for that vendor.

    Unlike the Zendesk proxy, this is not a thin passthrough and is not meant to
    become one. The queries depend on the L1/L2 taxonomy and its support-tag
    mapping, which live here; splitting the SQL from the taxonomy would mean
    keeping two copies of the mapping in step. VectorShift asks for insights on
    a booking, this app decides how to compute them.

    tid and vid come off the confirmed booking. Missing either returns zeros
    rather than an error, because a review with no booking yet is a normal
    state, not a failure.
    """
    _vs_auth(x_vs_key)
    from server.services.insights import get_insights

    booking = {"tid": tid, "vid": vid, "tgid": tgid, "visitDate": visit_date}
    data = await get_insights(booking, l1 or None, l2 or None, window or None)
    return {"insights": data}


class VsIntake(BaseModel):
    """The assembled RCA record produced by the VectorShift pipeline."""
    review: dict
    booking: dict = {}
    stated_issue: str = ""
    classification: dict = {}
    timeline: list = []
    ticket_facts: dict = {}
    rca: dict = {}
    actions_taken: dict = {}
    suggested_response: str = ""
    guest_name: str = ""


@router.post("/api/vs-intake")
def vs_intake(body: VsIntake, x_vs_key: str | None = Header(default=None),
              db: Session = Depends(get_session)):
    """
    Store a VectorShift-produced RCA so it shows on the dashboard like any
    other review (status=draft, source tagged in the id: vs_<bid>_<ts>).
    """
    _vs_auth(x_vs_key)
    rv = body.review or {}
    bid = str(rv.get("bid") or (body.booking or {}).get("id") or "").strip()
    rid = f"vs_{bid or 'nobid'}_{int(time.time())}"

    review = Review(
        id=rid,
        slack_ts=None,
        slack_channel="VECTORSHIFT",
        rating=int(rv.get("rating") or 1),
        language=rv.get("language") or "en",
        author=rv.get("author") or "",
        body_original=rv.get("body_original") or "",
        body_english=rv.get("body_english") or rv.get("body_original") or "",
        reference_number=bid or None,
        received_at=datetime.utcnow(),
        status="draft",
    )
    db.add(review)

    cls = body.classification or {}
    rca = body.rca or {}
    draft = RcaDraft(
        id=f"draft_{rid}", review_id=rid,
        booking=body.booking or {},
        match_tier=1 if bid else None,
        match_method="vectorshift",
        stated_issue=body.stated_issue or "",
        l1=cls.get("l1") or "", l2=cls.get("l2") or "",
        sub_theme=cls.get("sub_theme"),
        primary_scenario=cls.get("primary_scenario"),
        overlay_scenarios=cls.get("overlay_scenarios") or [],
        wwr_scenarios=rca.get("wwr_scenarios") or [],
        timeline=body.timeline or [],
        ticket_facts=body.ticket_facts or None,
        wwr_chain=rca.get("wwr_chain") or [],
        prevention=rca.get("prevention") or "",
        evidence=rca.get("evidence") or [],
        issue_specific_answers=rca.get("issue_specific_answers") or {},
        checklist_answers=rca.get("checklist_answers") or [],
        actions_taken=body.actions_taken or
            {"sp": [], "customer": [], "business": [], "product": [], "ce": []},
        suggested_response=body.suggested_response or "",
        generated_at=datetime.utcnow(),
    )
    db.add(draft)
    db.commit()
    return {"ok": True, "review_id": rid}


# ── Untraceable: one-click "ask for booking reference" reply ────────────────
@router.post("/api/reviews/{review_id}/mark-untraceable")
def mark_untraceable(review_id: str, db: Session = Depends(get_session)):
    """
    Move a Tier 2 "possible matches" review to Untraceable.

    The candidates were wrong — none of them is this guest's booking. Clearing
    them is the honest state: no booking, no tier, no picker. Keeping a wrong
    candidate list around invites someone to confirm one of them later.
    """
    r = db.query(Review).filter(Review.id == review_id).first()
    if not r:
        raise HTTPException(404, "Review not found")
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not d:
        raise HTTPException(404, "Draft not found")

    d.booking          = None
    d.candidates_list  = []
    d.candidate_state  = False
    d.match_tier       = None
    d.match_confidence = None
    d.match_method     = "Marked untraceable by associate"
    d.selected_candidate_bid = None
    trail = list(d.confidence_trail or [])
    trail.append({"mark": "warn",
                  "text": "<strong>Marked untraceable</strong> — associate rejected "
                          "all candidates"})
    d.confidence_trail = trail
    for f in ("booking", "candidates_list", "confidence_trail"):
        try:
            flag_modified(d, f)
        except Exception:
            pass
    db.commit()
    log.info(f"[api] {review_id} marked untraceable by associate")
    return {"ok": True, "review_id": review_id, "match_tier": None}


@router.post("/api/reviews/{review_id}/request-bid")
async def request_bid(review_id: str, db: Session = Depends(get_session)):
    """
    Returns the standard ask-for-booking-reference reply for the associate to
    copy into Trustpilot.

    This endpoint deliberately does NOT post anything. Guest-facing response
    copy is never sent to the Slack thread — it is copied out and pasted into
    Trustpilot by hand. It previously posted the template to the thread, which
    contradicted that.
    """
    r = db.query(Review).filter(Review.id == review_id).first()
    if not r:
        raise HTTPException(404, "Review not found")
    # The copy comes from content/orm_macros.yaml, the file CX edits. This
    # used to carry its own hardcoded sentence, so there were two versions of
    # the untraceable reply — the one the dashboard shows and the one this
    # returns — and editing the copy file only changed the first.
    from server.prompts import UNTRACEABLE_REPLY, MACROS, strip_honorifics
    _name = strip_honorifics(r.author or "")
    first = (_name.split()[0] if _name.split()
             else str(MACROS.get("fallback_first_name") or "there"))
    return {"ok": True, "posted": False,
            "template": UNTRACEABLE_REPLY.format(first_name=first)}
