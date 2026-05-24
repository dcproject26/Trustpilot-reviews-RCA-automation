"""
Three tables:
  reviews     — one row per review, cleared after sent (no long-term PII)
  rca_drafts  — the working draft
  metrics     — per-review stats retained for reporting (no PII)
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
    author           = Column(String, nullable=True)   # reviewer display name from Slack
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
    booking          = Column(JSON, nullable=True)
    match_tier       = Column(Integer, nullable=True)
    match_confidence = Column(String, nullable=True)
    match_method     = Column(String, nullable=True)
    timeline         = Column(JSON, nullable=True)
    insights         = Column(JSON, nullable=True)
    dss_rec          = Column(JSON, nullable=True)
    rca_fields       = Column(JSON, default=dict)
    signals          = Column(JSON, default=list)
    suggested_response  = Column(Text, nullable=True)
    final_response   = Column(Text, nullable=True)
    generated_at     = Column(DateTime, nullable=True)
    sent_at          = Column(DateTime, nullable=True)
    review           = relationship("Review", back_populates="draft")


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
    signals          = Column(JSON, default=list)
    edit_count       = Column(Integer, default=0)
    minutes_to_send  = Column(Float, nullable=True)
    sent             = Column(Boolean, default=False)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
