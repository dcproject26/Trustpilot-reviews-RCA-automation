"""
REPLACES existing server/db.py

Same three tables, but RcaDraft now carries the demo-parity structured RCA fields.
Existing installations should run migrations/001_add_rca_v2_fields.sql first.
"""
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, String, Integer, Float,
    Text, DateTime, JSON, Boolean, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from server.config import DATABASE_URL

db_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
engine = create_engine(db_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Review(Base):
    __tablename__ = "reviews"
    id               = Column(String, primary_key=True)
    slack_ts         = Column(String, unique=True)
    slack_channel    = Column(String)
    rating           = Column(Integer)
    language         = Column(String)
    author           = Column(String, nullable=True)
    body_original    = Column(Text)
    body_english     = Column(Text, nullable=True)
    reference_number = Column(String, nullable=True)
    received_at      = Column(DateTime, default=datetime.utcnow)
    status           = Column(String, default="new")   # new|draft|sent
    draft            = relationship("RcaDraft", back_populates="review", uselist=False)


class RcaDraft(Base):
    __tablename__ = "rca_drafts"
    id               = Column(String, primary_key=True)
    review_id        = Column(String, ForeignKey("reviews.id"), unique=True)

    # ── Booking match ──
    booking          = Column(JSON, nullable=True)
    match_tier       = Column(Integer, nullable=True)
    match_confidence = Column(String, nullable=True)
    match_method     = Column(String, nullable=True)
    candidates_list  = Column(JSON, default=list)       # top BQ candidates for Tier 2/3
    candidate_state  = Column(Boolean, default=False)   # True = picker active
    selected_candidate_bid = Column(String, nullable=True)
    confidence_trail = Column(JSON, default=list)       # step-by-step signal-extraction trail

    # ── Context ──
    timeline         = Column(JSON, default=list)
    insights         = Column(JSON, default=dict)
    similar_support  = Column(JSON, default=list)
    similar_reviews  = Column(JSON, default=list)
    dss_rec          = Column(JSON, default=dict)
    dss_connected_at = Column(DateTime, nullable=True)
    zendesk_ticket_ids = Column(JSON, default=list)   # ["30994882", ...]
    timeline_raw       = Column(JSON, default=list)   # raw comment bodies, same length as timeline

    # ── Structured RCA (demo parity) ──
    stated_issue                = Column(Text, nullable=True)
    l1                          = Column(String, nullable=True)
    l2                          = Column(String, nullable=True)
    l1_reasoning                = Column(Text, nullable=True)
    diagnostic_checks           = Column(JSON, default=list)
    what_went_wrong_bullets     = Column(JSON, default=list)
    support_interaction_frames  = Column(JSON, default=list)
    support_summary             = Column(Text, nullable=True)
    sp_interaction_frames       = Column(JSON, default=list)
    area_of_improving           = Column(JSON, default=list)
    actions_taken               = Column(JSON, default=lambda: {
        "sp": [], "customer": [], "business": [], "product": [], "ce": []
    })
    resolution                  = Column(Text, nullable=True)
    sub_theme                   = Column(String, nullable=True)

    # ── Tier classification provenance ──
    bid_source         = Column(String, nullable=True)   # attachment | regex | manual | None
    extracted_signals  = Column(JSON, default=dict)      # venue_hints, author_first/last, review_pub_date
    narrowing_attempts = Column(JSON, default=list)      # [{path, params, result_count}, ...]

    # ── RCA v3 shape ──
    tldr                        = Column(Text, nullable=True)
    wwr_chain                   = Column(JSON, default=list)
    prevention                  = Column(Text, nullable=True)
    evidence                    = Column(JSON, default=list)
    issue_specific_answers      = Column(JSON, default=dict)
    checklist_answers           = Column(JSON, default=list)

    # ── Ticket fact extraction (Zendesk → structured facts) ──
    ticket_facts                = Column(JSON, nullable=True)

    # ── Flag to Biz ──
    flag_to_biz_state           = Column(String, default="off")  # off | drafted | sent
    flag_to_biz_message         = Column(Text, nullable=True)

    # ── Legacy fields still used by the v1 flow ──
    rca_fields         = Column(JSON, default=dict)
    signals            = Column(JSON, default=list)
    suggested_response = Column(Text, nullable=True)
    final_response     = Column(Text, nullable=True)

    generated_at = Column(DateTime, nullable=True)
    sent_at      = Column(DateTime, nullable=True)
    review       = relationship("Review", back_populates="draft")


class ReviewMetric(Base):
    """Stats retained permanently for reporting. No guest PII."""
    __tablename__ = "review_metrics"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    review_id        = Column(String, unique=True)
    received_at      = Column(DateTime)
    channel          = Column(String)
    rating           = Column(Integer)
    language         = Column(String)
    match_tier       = Column(Integer, nullable=True)
    match_confidence = Column(String, nullable=True)
    auto_matched     = Column(Boolean, default=False)
    l1               = Column(String, nullable=True)
    l2               = Column(String, nullable=True)
    signals          = Column(JSON, default=list)
    edit_count       = Column(Integer, default=0)
    minutes_to_send  = Column(Float, nullable=True)
    sent             = Column(Boolean, default=False)
    dss_connected    = Column(Boolean, default=False)
    flagged_to_biz   = Column(Boolean, default=False)


class SlackEventSeen(Base):
    """Slack event dedupe — event_id primary key, 24h lookback window."""
    __tablename__ = "slack_events_seen"
    event_id = Column(String, primary_key=True)
    seen_at  = Column(DateTime, default=datetime.utcnow, index=True)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
