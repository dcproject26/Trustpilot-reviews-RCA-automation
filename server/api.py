"""
REPLACES existing server/api.py

Keeps the original endpoints (health, signals, list, manual, get, patch, send, reporting)
and adds the demo-parity endpoints:

  POST   /api/reviews/{id}/select-candidate — associate confirms a Tier 2 candidate
  POST   /api/reviews/{id}/connect-dss      — pull DSS on demand
  POST   /api/reviews/{id}/flag-to-biz      — draft + send Slack flag
  PATCH  /api/reviews/{id}/action           — add/edit/delete a single actions_taken row
  PATCH  /api/reviews/{id}/draft-v2         — save v2 fields (bullets, frames, resolution, etc.)
  GET    /api/reviews/{id}/similar          — fetch similar complaints on demand
  GET    /api/taxonomy                      — return L1/L2/checks catalogue (dashboard uses this)
"""
import asyncio, logging, os, subprocess, time
from datetime import datetime

log = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
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

def _looks_like_hash(s: str) -> bool:
    """True for opaque tokens we should never show as a guest name
    (long hex strings, no spaces)."""
    s = (s or "").strip()
    if not s or " " in s:
        return False
    return len(s) >= 16 and all(c in "0123456789abcdefABCDEF-" for c in s)


_ABSENT = object()


def _v4(d, column: str, v3_path: str, default):
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
    """
    node = d.rca_v3 or {}
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


def _draft_dict(d: RcaDraft) -> dict:
    _tf = d.ticket_facts or {}
    _bk = d.booking or {}

    def _first_name(*cands):
        for c in cands:
            c = (c or "").strip()
            if c and not _looks_like_hash(c):
                return c
        return ""

    guest_name = _first_name(
        _tf.get("guest_full_name"),
        _bk.get("guestName"),
        _bk.get("zendesk_requester_name"),
    )
    # When there is no name, say WHICH source failed. "[Guest name in Zendesk
    # ticket]" was a sentence dressed as a value: it looked like data, and it
    # told the reader nothing about whether we had looked, found a hash, or
    # found no ticket at all.
    if guest_name:
        guest_name_note = ""
    elif any(_looks_like_hash((c or "").strip()) for c in
             (_tf.get("guest_full_name"), _bk.get("guestName"))):
        guest_name_note = ("the warehouse stores this as a hash — check the "
                           "Zendesk ticket")
    elif d.zendesk_ticket_ids:
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
        "candidates_list":    d.candidates_list or [],
        "candidate_state":    d.candidate_state,
        "confidence_trail":   d.confidence_trail or [],
        "timeline":           d.timeline or [],
        "insights":           d.insights or {},
        "similar_support":    d.similar_support or [],
        "similar_reviews":    d.similar_reviews or [],
        "dss_rec":            d.dss_rec or {},
        "zendesk_ticket_ids": d.zendesk_ticket_ids or [],
        "timeline_raw":       d.timeline_raw or [],
        "dss_connected_at":   d.dss_connected_at.isoformat() if d.dss_connected_at else None,

        "stated_issue":                d.stated_issue,
        "l1":                          d.l1,
        "l2":                          d.l2,
        "sub_theme":                   d.sub_theme,
        # Lists are the source of truth for the dashboard; the scalars are
        # element 0 and stay in step for every existing consumer.
        "sub_themes":                  d.sub_themes or ([d.sub_theme] if d.sub_theme else []),
        "rca_posted_at":               d.rca_posted_at.isoformat() if d.rca_posted_at else None,
        "rca_v3_edited_at":            d.rca_v3_edited_at.isoformat() if d.rca_v3_edited_at else None,
        "primary_scenario":            d.primary_scenario or "",
        "scenarios":                   d.scenarios or ([d.primary_scenario] if d.primary_scenario else []),
        "overlay_scenarios":           d.overlay_scenarios or [],
        "wwr_scenarios":               d.wwr_scenarios or [],
        "l1_reasoning":                d.l1_reasoning,
        "diagnostic_checks":           d.diagnostic_checks or [],
        "what_went_wrong_bullets":     d.what_went_wrong_bullets or [],
        "support_interaction_frames":  d.support_interaction_frames or [],
        "support_summary":             d.support_summary,
        "sp_interaction_frames":       d.sp_interaction_frames or [],
        # rca_v3 wins by PRESENCE, like every other v4 field: the column is
        # the pipeline's projection, and an operator who deletes the last
        # improvement point produces [] - which a truthiness fallback would
        # lose to the stale column, so the delete would undo itself.
        "area_of_improving":           _v4(d, "area_of_improving", "area_of_improving", []),
        "actions_taken":               d.actions_taken or {"sp":[],"customer":[],"business":[],"product":[],"ce":[]},
        "resolution":                  d.resolution,

        "bucket":             _bucket_of(d),
        "bid_source":         d.bid_source,
        "extracted_signals":  d.extracted_signals or {},
        "narrowing_attempts": d.narrowing_attempts or [],

        "flag_to_biz_state":           d.flag_to_biz_state,
        "flag_to_biz_message":         d.flag_to_biz_message,

        "rca_v3":                      d.rca_v3 or {},

        # ── RCA v4 ──
        # rca_v3 is the source of truth: it is what the dashboard's data-v3p
        # editor writes to. The columns are the pipeline's queryable copy, so
        # they are the FALLBACK - reading them first would let a stale
        # denormalised value shadow an edit someone just made.
        "guest_issues":     _v4(d, "guest_issues", "what_went_wrong.guest_issues", []),
        "booking_logs":     _v4(d, "booking_logs", "booking_logs", []),
        "flags":            _v4(d, "flags", "flags", []),
        "takedown":         _v4(d, "takedown", "takedown", {}),
        "dss":              _v4(d, "dss", "dss", {}),
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
        "slack_mentions":        d.slack_mentions or [],

        "template_name":      d.template_name or "",
        # Presence-based, like every other v4 field — and for the same reason,
        # only sharper. Prompt rule 20 has the model return NULL for the reply
        # when no approved macro covers the issue, so a blank here is a
        # decision. Read column-first, a stale 110-word reply from an earlier
        # run overrides that decision and puts an unapproved reply back on the
        # card, one Send away from a public review page. `"suggested_response"
        # in rca_v3` wins whatever the value — including "" and None.
        "suggested_response": _v4(d, "suggested_response",
                                  "suggested_response", ""),
        "final_response":     d.final_response or "",
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
    }
    return {
        "ok":        True,
        "uptime_s":  int(time.time() - _START_TIME),
        "mock_mode": MOCK_MODE,
        "version":   version,
        "checks":    checks,
    }


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
    from server.pipeline import process_review as _pipeline

    ts = data.slack_ts or str(time.time())
    review_id = f"tp_{ts.replace('.', '_')}"

    if db.query(Review).filter(Review.id == review_id).first():
        return {"ok": True, "review_id": review_id, "duplicate": True}

    review = Review(
        id=review_id, slack_ts=ts, slack_channel=data.slack_channel,
        rating=data.rating, language=None,
        author=data.author or None, body_original=data.body,
        reference_number=data.reference_number, status="new",
    )
    db.add(review)
    db.commit()

    background_tasks.add_task(lambda rid: asyncio.run(_pipeline(rid)), review_id)
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
    import os
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


_BUILD_SHA = _read_head_sha()   # frozen at import, like the code itself


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
    import os
    from datetime import timezone

    sha = _BUILD_SHA
    on_disk = _read_head_sha()

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
    # A deployment ships without .git, so commit/on_disk come back "unknown"
    # there and no one can tell which code is serving. Hash the source instead:
    # both environments compute it the same way, so equal fingerprints mean
    # identical code and different fingerprints mean the deployment is behind -
    # answerable without git, and without trusting a build label.
    fingerprint = "unknown"
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
        fingerprint = h.hexdigest()[:12]
    except Exception:
        pass
    try:
        from server.db import engine, SessionLocal, Review, RcaDraft
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
        "fingerprint": fingerprint,
        "db":         db_info,
        "connectors": connectors,
        # What is checked out right now. If it differs from commit, the files
        # have moved on and this process has not - which is the entire failure
        # mode this endpoint exists to catch, and which it previously hid by
        # reporting on_disk as though it were the running build.
        "on_disk":    on_disk,
        "stale":      on_disk != sha and sha != "unknown",
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
}


@router.patch("/api/reviews/{review_id}/draft-v2")
def patch_draft_v2(review_id: str, patch: DraftPatchV2,
                    db: Session = Depends(get_session)):
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not d:
        raise HTTPException(404, "Draft not found")

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
        d.primary_scenario = (patch.scenarios or [None])[0]
        d.overlay_scenarios = list(patch.scenarios or [])[1:]
    if patch.sub_theme is not None and patch.sub_themes is None:
        d.sub_themes = [patch.sub_theme] if patch.sub_theme else []
    if patch.primary_scenario is not None and patch.scenarios is None:
        d.scenarios = [s for s in ([patch.primary_scenario] + (d.overlay_scenarios or [])) if s]

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
    full = None
    try:
        from server.services.bigquery_patch import verify_bid
        full = verify_bid(body.bid)
    except Exception as e:
        log.warning(f"[select-candidate] verify_bid({body.bid}) failed: {e}")
    d.booking = {**match, **(full or {}), "id": body.bid} if full else match
    if not full:
        log.warning(f"[select-candidate] {body.bid}: BQ row unavailable, "
                    f"storing candidate fields only")
    d.selected_candidate_bid = body.bid
    d.candidate_state = False
    d.match_tier = 2
    d.match_confidence = "confirmed"
    d.match_method = "Associate confirmed candidate"
    db.commit()

    # Re-run pipeline to fetch Zendesk/insights/RCA for the confirmed booking.
    from server.pipeline import process_review as _pipeline
    background_tasks.add_task(lambda rid: asyncio.run(_pipeline(rid)), review_id)
    return {"ok": True, "draft": _draft_dict(d)}


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
        db.add(Review(
            id=rid, slack_ts=parsed["slack_ts"],
            slack_channel=parsed["slack_channel"], rating=parsed["rating"],
            language=parsed["language"], author=parsed.get("author") or None,
            body_original=parsed["body_original"],
            reference_number=parsed["reference_number"], status="new"))
        db.commit()
        queued += 1
        ingested.append(rid)

    # Pipelines run in the background so the button returns at once; the
    # dashboard's own poll fills each card in as its run finishes.
    from server.pipeline import process_review as _pipeline
    for rid in ingested:
        if background_tasks is not None:
            background_tasks.add_task(lambda x: asyncio.run(_pipeline(x)), rid)
        else:
            # Called outside a request (a script, a test): run inline rather
            # than silently ingesting rows whose pipeline never runs.
            await _pipeline(rid)

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
    from server.pipeline import process_review as _pipeline
    sem = asyncio.Semaphore(_BULK_CONCURRENCY)

    async def one(rid: str):
        if _BULK["cancel"]:
            return
        async with sem:
            if _BULK["cancel"]:
                return
            _BULK["current"] = rid
            try:
                await _pipeline(rid)
                _BULK["results"].append({"id": rid, "ok": True, "error": ""})
                log.info(f"[bulk] {rid} done ({_BULK['done'] + 1}/{_BULK['total']})")
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

    scenarios = [s for s in (body.scenarios or []) if s in SCENARIO_CHECKS]
    if scenarios:
        d.primary_scenario  = scenarios[0]
        d.overlay_scenarios = scenarios[1:]
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
    rca_v3 = await claude_svc.generate_rca_v3(
        review_text=r.body_english or r.body_original or "",
        booking=d.booking or {},
        timeline=d.timeline or [],
        insights=d.insights or {},
        dss_rec=d.dss_rec or {},
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
    )
    if not rca_v3:
        raise HTTPException(502, "RCA regeneration returned nothing - draft unchanged")

    # The same validation the pipeline runs. Skipping it here was how an
    # unvalidated RCA reached the screen: re-run from the dashboard and the
    # coercions the pipeline applies simply did not happen.
    rca_notes: list = []
    try:
        from server.services.rca_v4_validate import validate as _validate_rca
        rca_v3, rca_notes = _validate_rca(rca_v3, scenarios)
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
    if "suggested_response" in rca_v3:
        d.suggested_response = rca_v3["suggested_response"] or ""

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
    _kept = [t for t in (d.confidence_trail or [])
             if not str((t or {}).get("text", "")).startswith("<strong>RCA</strong>")
             and not str((t or {}).get("text", "")).startswith("<strong>Reply voice")
             and not str((t or {}).get("text", "")).startswith("<strong>No approved macro")
             and not str((t or {}).get("text", "")).startswith("<strong>The reply is an approved macro")
             and "This run has not finished" not in str((t or {}).get("text", ""))]
    for _n in (rca_notes or []):
        _kept.append({"mark": "warn",
                      "text": f"<strong>RCA</strong> — {_html.escape(str(_n))}"})
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
            ts = await post_to_thread(channel, parent, full_msg, as_user=False)
            d.flag_to_biz_state = "sent"
            d.flag_to_biz_message = body.message

            # Log an entry in actions_taken.business.
            #
            # Copied, not mutated in place. SQLAlchemy compares a JSON column
            # by identity, so appending to the list it already holds and
            # assigning the same object back is not a change it can see - the
            # endpoint returned {"ok": true} with the entry in the response
            # and wrote nothing. Verified against a running server: the reply
            # showed the entry, the reload showed null.
            actions = {k: list(v) for k, v in
                       (d.actions_taken or {}).items()} or {}
            for _t in ("sp", "customer", "business", "product", "ce"):
                actions.setdefault(_t, [])
            actions["business"].append({
                "with": "Biz team — raise completion to market rate",
                "handle": tag or "—",
                "time": datetime.utcnow().strftime("%d %b %H:%M"),
                # The facts first, so the log says what was actually raised
                # rather than only how it was worded.
                "context": " — ".join(x for x in (_biz_facts(body),
                                                  (body.message or "")[:200]) if x),
                "where": f"slack.com/{channel.lstrip('#')}/{ts}",
            })
            d.actions_taken = actions
            flag_modified(d, "actions_taken")

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

@router.post("/api/reviews/{review_id}/send")
async def send_review(review_id: str, db: Session = Depends(get_session)):
    r = db.query(Review).filter(Review.id == review_id).first()
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not r or not d:
        raise HTTPException(404, "Not found")

    d.sent_at = datetime.utcnow()
    r.status  = "sent"
    ts = None
    # Send posts the RCA as well as closing the review — but "Post to thread"
    # exists precisely so the RCA can go to the team while the reply is still
    # being edited, and using both put the same RCA in the thread twice. The
    # post here is for the case where nobody used that button.
    if r.slack_channel != "C_MANUAL" and not d.rca_posted_at:
        rca_text = (d.slack_thread_override or "").strip() or format_rca_slack(r, d)
        ts = await post_to_thread(r.slack_channel, r.slack_ts, rca_text, as_user=True)
        if ts:
            d.rca_posted_at = datetime.utcnow()
    m = db.query(ReviewMetric).filter(ReviewMetric.review_id == review_id).first()
    if m:
        if r.received_at:
            m.minutes_to_send = (datetime.utcnow() - r.received_at).total_seconds() / 60
        m.sent = True
    db.commit()
    return {"ok": True, "ts": ts}


@router.post("/api/reviews/{review_id}/translate-reply")
async def translate_reply(review_id: str, db: Session = Depends(get_session)):
    """
    Turn the edited English reply into the guest's own language.

    The draft is written and edited in English so the whole team can review
    it, but the guest wrote in their language and the reply goes back in it.
    Translating at the END means the edit is what gets translated, not a
    draft that was rewritten afterwards.
    """
    r = db.query(Review).filter(Review.id == review_id).first()
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not r or not d:
        raise HTTPException(404, "Not found")
    english = (d.final_response or d.suggested_response or "").strip()
    if not english:
        raise HTTPException(400, "There is no reply to translate yet.")
    lang = (r.language or "").strip()
    if not lang or lang.upper() == "EN":
        return {"ok": True, "language": "EN", "text": english,
                "note": "Review is in English - nothing to translate."}
    from server.services import claude as claude_svc
    out = await claude_svc.translate_to(english, lang, review_id)
    if not out:
        raise HTTPException(502, "Translation returned nothing - try again.")
    return {"ok": True, "language": lang, "text": out}


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
    ts = await post_to_thread(r.slack_channel, r.slack_ts, text, as_user=True)
    if ts is None and not MOCK_MODE:
        raise HTTPException(502, "Slack rejected the post - check the bot's "
                                 "channel membership and scopes.")
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
                if refreshed:
                    db.commit()
                    log.info(f"[reprocess] {review_id}: refreshed from Slack "
                             f"({len(new_body)} chars)")
        except Exception as e:
            log.warning(f"[reprocess] {review_id}: Slack refresh failed, "
                        f"re-running on stored text: {e}")

    from server.pipeline import process_review as _pipeline

    def _run(rid):
        # Background-task exceptions were swallowed entirely: the pipeline died,
        # generated_at never moved, and the dashboard polled for three minutes
        # before giving up — indistinguishable from "re-run did nothing".
        try:
            # Re-running a review whose booking was already confirmed means the
            # associate wants the choice back, so matching must not auto-promote
            # its way straight into another confirmed state.
            # An explicit Re-run always presents the options. Gating this on
            # "was a confirmation just cleared" was wrong: once the first re-run
            # cleared it, every later one saw nothing to clear, auto-promoted the
            # best match straight to Tier 1, and the associate never got the
            # picker back. Clicking Re-run IS the request to choose again.
            asyncio.run(_pipeline(rid, force_candidates=True))
        except Exception:
            log.exception(f"[reprocess] pipeline crashed for {rid}")

    background_tasks.add_task(_run, review_id)
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
    """
    from server.pipeline import PIPELINE_PROGRESS
    e = PIPELINE_PROGRESS.get(review_id)
    if not e:
        return {"running": False}
    import time as _t
    return {"running": True, "step": e["step"], "total": e["total"],
            "stage": e["stage"], "elapsed_s": int(_t.time() - e["started_at"])}


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
