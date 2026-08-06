import asyncio, logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from server.config import MOCK_MODE, ORM_CHANNELS
from server.services.slack import verify_signature, is_trustpilot_message, parse_review
from server.db import SessionLocal, Review, SlackEventSeen
from server.pipeline import process_review

log = logging.getLogger(__name__)
router = APIRouter()


def _event_already_seen(db, event_id: str) -> bool:
    """Slack event dedupe — 24h lookback on event_id. Inserts if new."""
    if not event_id:
        return False
    cutoff = datetime.utcnow() - timedelta(hours=24)
    seen = (db.query(SlackEventSeen)
              .filter(SlackEventSeen.event_id == event_id,
                      SlackEventSeen.seen_at > cutoff)
              .first())
    if seen:
        return True
    db.merge(SlackEventSeen(event_id=event_id, seen_at=datetime.utcnow()))
    db.commit()
    return False


def _booking_has_active_draft(db, booking_id: str) -> bool:
    """Booking-level dedupe — an active review already exists for this BID."""
    if not booking_id:
        return False
    return (db.query(Review)
              .filter(Review.reference_number == booking_id,
                      Review.status.in_(("new", "draft", "sent")))
              .first()) is not None


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

    # ── A. Event-level dedupe (Slack retries / duplicate deliveries) ─────────
    if payload.get("type") == "event_callback":
        event_id = payload.get("event_id", "")
        db = SessionLocal()
        try:
            if _event_already_seen(db, event_id):
                log.info(f"[dedupe] slack event {event_id} already seen — skipped")
                return {"ok": True, "duplicate": True}
        finally:
            db.close()

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
            return {"ok": True, "duplicate": True}   # same-message dedup

        # ── B. Booking-level dedupe — same BID from a different Slack event ──
        bid = parsed.get("reference_number")
        if bid and _booking_has_active_draft(db, bid):
            log.info(f"[dedupe] booking {bid} already has an active draft — skipped")
            return {"ok": True, "duplicate": True, "booking_id": bid}

        # WHEN THE REVIEW CAME IN, not when our server got round to the
        # webhook. This row was created with no received_at at all, so the
        # column default fired and stamped datetime.utcnow() — the moment WE
        # processed the message. On screen that is two different reviews both
        # reading "05 Aug, 07:47", which is what a whole batch stamped in one
        # second looks like.
        #
        # This is the LIVE path — the one every real review arrives on — and
        # the batch importer beside it has guarded against exactly this since
        # it was written. One path fixed, the other left with the default:
        # the two disagreed and the live one was wrong.
        from server.api import _received_at_from
        _at = _received_at_from(parsed["slack_ts"], review_id,
                                parsed.get("published_at"),
                                parsed.get("published_at_source", ""))
        review = Review(
            id               = review_id,
            slack_ts         = parsed["slack_ts"],
            slack_channel    = parsed["slack_channel"],
            rating           = parsed["rating"],
            language         = parsed["language"],
            author           = parsed.get("author") or None,
            body_original    = parsed["body_original"],
            received_at      = _at,
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
