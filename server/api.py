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
import asyncio, time
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.db import get_session, Review, RcaDraft, ReviewMetric
from server.taxonomy import L1_CATEGORIES, L2_OPTIONS, DIAGNOSTIC_CHECKS, ACTION_TABS, SUB_THEME_REGISTRY
from server.config import status_summary
from server.services.slack import format_rca_slack, post_to_thread
from server.services.claude import flag_to_biz_message
from server.services.bigquery_patch import get_similar_complaints
from server.services import dss as dss_svc

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
    """Partial update for any of the structured v2 fields."""
    stated_issue:               str  | None = None
    l1:                         str  | None = None
    l2:                         str  | None = None
    sub_theme:                  str  | None = None
    l1_reasoning:               str  | None = None
    diagnostic_checks:          list | None = None
    what_went_wrong_bullets:    list | None = None
    support_interaction_frames: list | None = None
    support_summary:            str  | None = None
    sp_interaction_frames:      list | None = None
    area_of_improving:          list | None = None
    actions_taken:              dict | None = None
    resolution:                 str  | None = None
    final_response:             str  | None = None


class CandidateSelect(BaseModel):
    bid: str  # the chosen candidate's booking ID


class ActionPatch(BaseModel):
    tab: str                      # sp | customer | business | product | ce
    op: str                       # add | update | delete
    index: int | None = None      # required for update / delete
    action: dict | None = None    # required for add / update


class FlagToBiz(BaseModel):
    channel: str    | None = None  # Slack channel (default: from env)
    tag: str        | None = None  # who to tag
    message: str    | None = None  # editable draft
    send: bool = False             # False = save draft; True = send now


# ── Utility ─────────────────────────────────────────────────────────────────

# Task #4 (sub_theme wiring) — DONE: _draft_dict() returns sub_theme,
# DraftPatchV2 accepts it, and the patch loop persists it. The dashboard
# renders a taxonomy-driven Sub-theme row (options from /api/taxonomy
# sub_theme_frameworks) in the Issue Classification block.

def _draft_dict(d: RcaDraft) -> dict:
    return {
        "booking":            d.booking,
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
        "l1_reasoning":                d.l1_reasoning,
        "diagnostic_checks":           d.diagnostic_checks or [],
        "what_went_wrong_bullets":     d.what_went_wrong_bullets or [],
        "support_interaction_frames":  d.support_interaction_frames or [],
        "support_summary":             d.support_summary,
        "sp_interaction_frames":       d.sp_interaction_frames or [],
        "area_of_improving":           d.area_of_improving or [],
        "actions_taken":               d.actions_taken or {"sp":[],"customer":[],"business":[],"product":[],"ce":[]},
        "resolution":                  d.resolution,

        "bid_source":         d.bid_source,
        "extracted_signals":  d.extracted_signals or {},
        "narrowing_attempts": d.narrowing_attempts or [],

        "flag_to_biz_state":           d.flag_to_biz_state,
        "flag_to_biz_message":         d.flag_to_biz_message,

        "suggested_response": d.suggested_response or "",
        "final_response":     d.final_response or "",
        "generated_at":       d.generated_at.isoformat() if d.generated_at else None,
        "sent_at":            d.sent_at.isoformat() if d.sent_at else None,
    }


# ── Existing routes (unchanged) ─────────────────────────────────────────────

@router.get("/api/health")
def health():
    return status_summary()


@router.get("/api/reviews")
def list_reviews(status: str | None = None, tab: str | None = None,
                  db: Session = Depends(get_session)):
    """
    tab: bid | possible_matches | untraceable | sent
    Filters by match_tier + candidate_state.
    """
    q = db.query(Review).order_by(Review.received_at.desc())
    if status:
        q = q.filter(Review.status == status)
    rows = q.limit(200).all()

    result = []
    for r in rows:
        draft   = r.draft
        tier    = draft.match_tier if draft else None
        cand_state = bool(draft and draft.candidate_state)

        if tab == "bid" and not (tier == 1):
            continue
        if tab == "possible_matches" and not cand_state:
            continue
        if tab == "untraceable" and not (tier is None and not cand_state and r.status != "sent"):
            continue
        if tab == "sent" and r.status != "sent":
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
        rating=data.rating, language="en",
        author=data.author or None, body_original=data.body,
        reference_number=data.reference_number, status="new",
    )
    db.add(review)
    db.commit()

    background_tasks.add_task(lambda rid: asyncio.run(_pipeline(rid)), review_id)
    return {"ok": True, "review_id": review_id}


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


# ── NEW: taxonomy endpoint (dashboard fetches this to render dropdowns) ─────

@router.get("/api/taxonomy")
def get_taxonomy():
    return {
        "l1_categories":       L1_CATEGORIES,
        "l2_options":          L2_OPTIONS,
        "diagnostic_checks":   DIAGNOSTIC_CHECKS,
        "action_tabs":         ACTION_TABS,
        "sub_theme_frameworks": {f"{k[0]}::{k[1]}": v for k, v in SUB_THEME_REGISTRY.items()},
    }


# ── NEW: v2 draft patch ─────────────────────────────────────────────────────

@router.patch("/api/reviews/{review_id}/draft-v2")
def patch_draft_v2(review_id: str, patch: DraftPatchV2,
                    db: Session = Depends(get_session)):
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not d:
        raise HTTPException(404, "Draft not found")

    edits = 0
    for field in (
        "stated_issue", "l1", "l2", "sub_theme", "l1_reasoning",
        "diagnostic_checks", "what_went_wrong_bullets",
        "support_interaction_frames", "support_summary",
        "sp_interaction_frames", "area_of_improving",
        "actions_taken", "resolution", "final_response",
    ):
        val = getattr(patch, field, None)
        if val is not None:
            setattr(d, field, val)
            edits += 1

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

    # Set the confirmed booking and re-run the pipeline from Zendesk onwards.
    d.booking = match
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
            "channel": body.channel or "#biz-supply-ops",
            "tag": body.tag or "[Biz handle placeholder]",
        }

    # Step 2: send
    if body.send:
        channel = body.channel or "#biz-supply-ops"
        tag = body.tag or ""
        full_msg = f"{tag}\n{body.message}".strip()
        try:
            ts = await post_to_thread(channel, None, full_msg, as_user=False)
            d.flag_to_biz_state = "sent"
            d.flag_to_biz_message = body.message

            # Log an entry in actions_taken.business
            actions = d.actions_taken or {"sp":[],"customer":[],"business":[],"product":[],"ce":[]}
            actions["business"].append({
                "with": "Biz team — raise completion to market rate",
                "handle": tag or "[Biz handle placeholder]",
                "time": datetime.utcnow().strftime("%d %b %H:%M"),
                "context": body.message[:200],
                "where": f"slack.com/{channel.lstrip('#')}/{ts}",
            })
            d.actions_taken = actions

            m = db.query(ReviewMetric).filter(ReviewMetric.review_id == review_id).first()
            if m:
                m.flagged_to_biz = True
            db.commit()
            return {"ok": True, "state": "sent", "ts": ts}
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

    actions = d.actions_taken or {"sp":[],"customer":[],"business":[],"product":[],"ce":[]}
    tab_list = actions.get(body.tab, [])

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

    rca_text = format_rca_slack(r, d)
    try:
        ts = await post_to_thread(r.slack_channel, r.slack_ts, rca_text, as_user=True)
        d.sent_at = datetime.utcnow()
        r.status  = "sent"
        m = db.query(ReviewMetric).filter(ReviewMetric.review_id == review_id).first()
        if m:
            if r.received_at:
                m.minutes_to_send = (datetime.utcnow() - r.received_at).total_seconds() / 60
            m.sent = True
        db.commit()
        return {"ok": True, "ts": ts}
    except Exception as e:
        raise HTTPException(500, str(e))


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
