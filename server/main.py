import asyncio, collections, logging, logging.handlers, os, time
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from server.config import MOCK_MODE, is_live
from server.db import init_db, SessionLocal, Review, RcaDraft, ReviewMetric
from server.webhook import router as webhook_router
from server.api     import router as api_router
from server.services.mock_data import (
    MOCK_REVIEWS, MOCK_BOOKINGS, MOCK_TIMELINES, MOCK_INSIGHTS,
    MOCK_DSS, MOCK_RCA_FIELDS, MOCK_RESPONSES,
)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)


# ── Small ring-buffer log handler to count recent ERROR-level records ─────────
class _ErrorRingBuffer(logging.Handler):
    """Keeps timestamps of ERROR+ records for the last 5 minutes."""
    def __init__(self, maxsize: int = 500):
        super().__init__(level=logging.ERROR)
        self._buf: collections.deque = collections.deque(maxlen=maxsize)

    def emit(self, record: logging.LogRecord) -> None:
        self._buf.append(time.time())

    def count_recent(self, window_s: float = 300.0) -> int:
        cutoff = time.time() - window_s
        return sum(1 for t in self._buf if t >= cutoff)


_error_buf = _ErrorRingBuffer()
logging.getLogger().addHandler(_error_buf)


def seed_mocks():
    db = SessionLocal()
    try:
        if db.query(Review).count() > 0:
            return
        log.info("Seeding mock reviews…")
        for r in MOCK_REVIEWS:
            review = Review(
                id=r["id"], slack_ts=r["slack_ts"], slack_channel=r["slack_channel"],
                rating=r["rating"], language=r["language"],
                author=r.get("author"),
                body_original=r["body_original"], body_english=r.get("body_english"),
                reference_number=r.get("reference_number"),
                received_at=datetime.fromisoformat(r["received_at"]),
                status=r["status"],
            )
            db.add(review)

            booking_raw = dict(MOCK_BOOKINGS.get(r["id"], {}))
            match_meta  = booking_raw.pop("_match", {})
            fields = dict(MOCK_RCA_FIELDS.get(r["id"], {}))
            signals = fields.pop("signals", [])

            draft = RcaDraft(
                id=f"draft_{r['id']}", review_id=r["id"],
                booking=booking_raw,
                match_tier=match_meta.get("tier"),
                match_confidence=match_meta.get("confidence"),
                match_method=match_meta.get("method"),
                timeline=MOCK_TIMELINES.get(r["id"], []),
                insights=MOCK_INSIGHTS.get(r["id"], {}),
                dss_rec=MOCK_DSS.get(r["id"], {}),
                rca_fields=fields, signals=signals,
                suggested_response=MOCK_RESPONSES.get(r["id"], ""),
                generated_at=datetime.utcnow(),
            )
            db.add(draft)

            db.add(ReviewMetric(
                review_id=r["id"],
                received_at=datetime.fromisoformat(r["received_at"]),
                channel=r["slack_channel"], rating=r["rating"],
                language=r["language"],
                match_tier=match_meta.get("tier"),
                match_confidence=match_meta.get("confidence"),
                auto_matched=match_meta.get("tier") in (1, 2),
                signals=signals, edit_count=0, sent=False,
            ))
        db.commit()
        log.info(f"Seeded {len(MOCK_REVIEWS)} mock reviews")
    finally:
        db.close()


async def _heartbeat_loop() -> None:
    """Logs a heartbeat line every 5 minutes."""
    _app_start = time.time()
    await asyncio.sleep(10)          # brief startup grace period
    while True:
        try:
            uptime = int(time.time() - _app_start)
            db = SessionLocal()
            try:
                cutoff = datetime.utcnow() - timedelta(minutes=5)
                recent = db.query(Review).filter(Review.received_at >= cutoff).count()
            finally:
                db.close()
            errs = _error_buf.count_recent(300)
            log.info(
                "[heartbeat] uptime=%ds mock=%s "
                "live={bq=%s,zd=%s,ant=%s,slack=%s,dss=%s,canned=%s,checklist=%s} "
                "recent_reviews=%d recent_errors=%d",
                uptime, MOCK_MODE,
                is_live("bigquery"), is_live("zendesk"), is_live("anthropic"),
                is_live("slack_outbound"), is_live("dss"), is_live("canned"), is_live("checklist"),
                recent, errs,
            )
        except Exception:
            log.exception("[heartbeat] loop error")
        await asyncio.sleep(300)     # 5 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("Database ready")
    if MOCK_MODE:
        seed_mocks()
    log.info(f"Mock mode: {'ON' if MOCK_MODE else 'OFF'}")
    log.info("AI provider: Replit AI Integrations — Anthropic Claude (no API key needed)")

    # Warm the RCA checklist cache
    try:
        from server.services.rca_checklist import warm_cache
        await warm_cache()
    except Exception:
        log.warning("RCA checklist warm-cache failed (non-fatal)")

    # Start 5-minute heartbeat background task
    hb_task = asyncio.create_task(_heartbeat_loop())
    yield
    hb_task.cancel()
    try:
        await hb_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Headout ORM RCA", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(webhook_router)
app.include_router(api_router)

CLIENT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "client")


def _dashboard():
    """
    index.html, explicitly not cached.

    The whole dashboard is one file, and the browser will hold on to it across
    a deploy unless told otherwise. That produced an afternoon of a new server
    talking to an old page: the API returned {ok, window, insights} while the
    cached page still read the old bare shape, so every tile rendered
    "undefined / undefined" and looked like a server fault. Nothing here is
    large enough for caching to be worth that.
    """
    return FileResponse(
        os.path.join(CLIENT_DIR, "index.html"),
        headers={"Cache-Control": "no-store, must-revalidate",
                 "Pragma": "no-cache", "Expires": "0"})


@app.get("/")
def root():
    return _dashboard()

@app.get("/review/{review_id}")
def review_page(review_id: str):
    return _dashboard()

@app.get("/reporting")
def reporting_page():
    return _dashboard()

@app.get("/healthz")
def healthz():
    return {"ok": True}

if os.path.isdir(os.path.join(CLIENT_DIR, "static")):
    app.mount("/static", StaticFiles(
        directory=os.path.join(CLIENT_DIR, "static")), name="static")

if __name__ == "__main__":
    import uvicorn
    # reload is OPT-IN. With it on, watchfiles restarts the app on any file
    # change under the workspace -- including __pycache__ and the sqlite file
    # the app itself writes on every request. That produced a restart every few
    # hundred milliseconds, and each restart killed the in-flight BackgroundTask,
    # so a re-run could never finish and the dashboard saw nothing change.
    _reload = os.getenv("UVICORN_RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run("server.main:app", host="0.0.0.0",
                # Default 5000, matching the .replit port mapping (5000 -> 80).
                # It used to default to 8000, so a shell-launched server bound a
                # different port from the Run button's, and the two could run at
                # once serving different code on different URLs. Same default
                # both ways means the second launch fails loudly on "address in
                # use" instead of quietly shadowing the first.
                port=int(os.getenv("PORT", "5000")),
                reload=_reload,
                # "*.db" alone is not enough. SQLite writes a rollback
                # journal beside the database and, in WAL mode, a -wal and a
                # -shm, and none of those match *.db. So every write during a
                # pipeline run tripped the reloader, the restart killed the
                # BackgroundTask mid-flight, and a re-run could never finish -
                # which reads as "the re-run is slow" rather than as a crash.
                reload_excludes=["*.db", "*.db-*", "*.sqlite", "*.sqlite-*",
                                 "*.log", "__pycache__/*", "*.pyc",
                                 ".git/*"] if _reload else None)
