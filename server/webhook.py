import asyncio, logging
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from server.config import MOCK_MODE, ORM_CHANNELS
from server.services.slack import verify_signature, is_trustpilot_message, parse_review
from server.db import SessionLocal, Review
from server.pipeline import process_review

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhook/slack")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    ts  = request.headers.get("x-slack-request-timestamp", "0")
    sig = request.headers.get("x-slack-signature", "")

    if not MOCK_MODE and not verify_signature(body, ts, sig):
        raise HTTPException(401, "Invalid signature")

    payload = await request.json()

    # URL verification handshake (one-time when setting up the Slack app)
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    event = payload.get("event", {})
    if event.get("type") != "message":
        return {"ok": True}

    # Only process messages from the configured ORM channels (if set)
    if ORM_CHANNELS and event.get("channel") not in ORM_CHANNELS:
        return {"ok": True}

    if not is_trustpilot_message(event):
        return {"ok": True}

    parsed = parse_review(event)
    db = SessionLocal()
    try:
        review_id = f"tp_{parsed['slack_ts'].replace('.', '_')}"
        if db.query(Review).filter(Review.id == review_id).first():
            return {"ok": True, "duplicate": True}   # dedup

        review = Review(
            id               = review_id,
            slack_ts         = parsed["slack_ts"],
            slack_channel    = parsed["slack_channel"],
            rating           = parsed["rating"],
            language         = parsed["language"],
            body_original    = parsed["body_original"],
            reference_number = parsed["reference_number"],
        )
        db.add(review)
        db.commit()
    finally:
        db.close()

    # Run the pipeline in the background so Slack gets its 200 OK fast
    background_tasks.add_task(_run, review_id)
    return {"ok": True, "review_id": review_id}


@router.post("/webhook/test")
async def trigger_test(request: Request, background_tasks: BackgroundTasks):
    """Dev endpoint: re-run the pipeline for an existing review_id."""
    body = await request.json()
    rid  = body.get("review_id")
    if not rid:
        raise HTTPException(400, "review_id required")
    background_tasks.add_task(_run, rid)
    return {"ok": True, "review_id": rid}


def _run(review_id: str):
    try:
        asyncio.run(process_review(review_id))
    except Exception as e:
        log.exception(f"Pipeline error for {review_id}: {e}")
