import asyncio, time
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from server.db import get_session, Review, RcaDraft, ReviewMetric
from server.signals import SIGNAL_TAXONOMY
from server.config import status_summary
from server.services.slack import format_rca_slack, post_to_thread

router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────────────────────

class ManualReview(BaseModel):
    body: str
    rating: int = 1
    author: str | None = None
    reference_number: str | None = None
    slack_channel: str = "C_MANUAL"
    slack_ts: str | None = None


class DraftPatch(BaseModel):
    rca_fields:     dict | None = None
    signals:        list | None = None
    final_response: str  | None = None


# ── Utility ──────────────────────────────────────────────────────────────────

def _draft_dict(d: RcaDraft) -> dict:
    return {
        "booking":            d.booking,
        "match_tier":         d.match_tier,
        "match_confidence":   d.match_confidence,
        "match_method":       d.match_method,
        "timeline":           d.timeline or [],
        "insights":           d.insights or {},
        "dss_rec":            d.dss_rec or {},
        "rca_fields":         d.rca_fields or {},
        "signals":            d.signals or [],
        "suggested_response": d.suggested_response or "",
        "final_response":     d.final_response or "",
        "generated_at":       d.generated_at.isoformat() if d.generated_at else None,
        "sent_at":            d.sent_at.isoformat() if d.sent_at else None,
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/api/health")
def health():
    return status_summary()


@router.get("/api/signals")
def get_signals():
    return {"taxonomy": SIGNAL_TAXONOMY}


@router.get("/api/reviews")
def list_reviews(status: str | None = None, db: Session = Depends(get_session)):
    q = db.query(Review).order_by(Review.received_at.desc())
    if status:
        q = q.filter(Review.status == status)
    return [{
        "id":          r.id,
        "rating":      r.rating,
        "language":    r.language,
        "status":      r.status,
        "snippet":     (r.body_english or r.body_original or "")[:120],
        "received_at": r.received_at.isoformat() if r.received_at else None,
        "match_tier":  r.draft.match_tier if r.draft else None,
    } for r in q.limit(100)]


@router.post("/api/reviews/manual")
async def add_manual_review(
    data: ManualReview,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Associate pastes a review directly — pipeline runs exactly as via Slack."""
    from server.pipeline import process_review as _pipeline

    ts = data.slack_ts or str(time.time())
    review_id = f"tp_{ts.replace('.', '_')}"

    if db.query(Review).filter(Review.id == review_id).first():
        return {"ok": True, "review_id": review_id, "duplicate": True}

    review = Review(
        id=review_id,
        slack_ts=ts,
        slack_channel=data.slack_channel,
        rating=data.rating,
        language="en",
        author=data.author or None,
        body_original=data.body,
        reference_number=data.reference_number,
        status="new",
    )
    db.add(review)
    db.commit()

    def _run(rid):
        asyncio.run(_pipeline(rid))

    background_tasks.add_task(_run, review_id)
    return {"ok": True, "review_id": review_id}


@router.get("/api/reviews/{review_id}")
def get_review(review_id: str, db: Session = Depends(get_session)):
    r = db.query(Review).filter(Review.id == review_id).first()
    if not r:
        raise HTTPException(404, "Not found")
    return {
        "review": {
            "id":               r.id,
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


@router.patch("/api/reviews/{review_id}")
def patch_draft(
    review_id: str,
    patch: DraftPatch,
    db: Session = Depends(get_session),
):
    d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
    if not d:
        raise HTTPException(404, "Draft not found")

    edits = 0
    if patch.rca_fields is not None:
        d.rca_fields = {**(d.rca_fields or {}), **patch.rca_fields}
        edits += 1
    if patch.signals is not None:
        d.signals = patch.signals
        edits += 1
    if patch.final_response is not None:
        d.final_response = patch.final_response
        edits += 1

    m = db.query(ReviewMetric).filter(ReviewMetric.review_id == review_id).first()
    if m and edits:
        m.edit_count = (m.edit_count or 0) + edits

    db.commit()
    return {"ok": True, "draft": _draft_dict(d)}


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
    """Stats for the in-app reporting page. No PII."""
    metrics = (db.query(ReviewMetric)
               .order_by(ReviewMetric.received_at.desc())
               .limit(500).all())

    total        = len(metrics)
    sent         = sum(1 for m in metrics if m.sent)
    auto_matched = sum(1 for m in metrics if m.auto_matched)
    times        = [m.minutes_to_send for m in metrics if m.minutes_to_send]
    avg_mins     = round(sum(times) / len(times), 1) if times else None

    sig_count: dict = {}
    for m in metrics:
        for s in (m.signals or []):
            sig_count[s] = sig_count.get(s, 0) + 1
    top_signals = sorted(sig_count.items(), key=lambda x: -x[1])[:15]

    tier_counts: dict = {}
    for m in metrics:
        k = f"Tier {m.match_tier}" if m.match_tier else "No match"
        tier_counts[k] = tier_counts.get(k, 0) + 1

    by_rating: dict = {}
    for m in metrics:
        k = str(m.rating or "?")
        by_rating[k] = by_rating.get(k, 0) + 1

    return {
        "total":               total,
        "sent":                sent,
        "auto_matched":        auto_matched,
        "avg_minutes_to_send": avg_mins,
        "top_signals":         top_signals,
        "tier_breakdown":      tier_counts,
        "by_rating":           by_rating,
    }
