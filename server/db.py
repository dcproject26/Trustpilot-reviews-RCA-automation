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


def _check_url(u: str) -> None:
    """Refuse an unusable DATABASE_URL with a sentence, not a stack trace.

    THE REPORTED CASE. Someone copied a documented command verbatim and ran
    `DATABASE_URL='<production url>' python3 tools/purge_reviews.py ...`. What
    came back was eight frames of SQLAlchemy ending in "Could not parse
    SQLAlchemy URL from string '<production url>'" — technically accurate, and
    it neither says the placeholder was left in nor where the real value
    lives. Every tool here imports this module, so one check covers all of
    them, at the moment the mistake is made rather than deep inside a library.
    """
    import sys
    # No empty-string branch: config._resolve_database_url() falls back to
    # sqlite when DATABASE_URL is unset, so an empty value never reaches here.
    # A guard for a state that cannot occur reads as protection and is not —
    # the same shape as a validator wired into nothing.
    bad = ""
    if u.strip().startswith("<") or u.strip().endswith(">"):
        bad = (f"DATABASE_URL is still the placeholder {u.strip()!r} — the "
               f"example was copied without substituting the real value")
    elif "://" not in u:
        bad = (f"DATABASE_URL {u.strip()[:60]!r} is not a connection URL "
               f"(no scheme://)")
    if not bad:
        return
    print(
        f"[db] REFUSING TO START — {bad}.\n"
        f"     A connection URL looks like:\n"
        f"       postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require\n"
        f"     The production value is on the deployment (Deployments -> "
        f"Settings -> Secrets -> DATABASE_URL), or in the Replit Database "
        f"pane under Production Database.\n"
        f"     Leave DATABASE_URL unset to use this workspace's own database.",
        file=sys.stderr)
    raise SystemExit(2)


_check_url(db_url)
# pool_pre_ping tests a connection at CHECKOUT. That is not enough here: the
# pipeline holds one session open across BigQuery, Zendesk and four model
# calls - minutes - and Neon closes an idle connection inside that window, so
# the run dies on "SSL connection has been closed unexpectedly" AFTER the
# match succeeded. The review then has a good match that was never written
# and renders as untraceable. Recycling below Neon's idle timeout means the
# pool never hands out a connection old enough to have been reaped.
engine = create_engine(
    db_url,
    pool_pre_ping=True,
    pool_recycle=240,        # under Neon's ~5 min idle cutoff
    pool_size=5, max_overflow=10,
    connect_args=({"keepalives": 1, "keepalives_idle": 30,
                   "keepalives_interval": 10, "keepalives_count": 5,
                   "connect_timeout": 10}
                  if db_url.startswith("postgresql") else {}),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── SAY WHICH DATABASE, ONCE, TO EVERYTHING THAT OPENS ONE ──────────────────
#
# THE RECURRING FAILURE THIS ENDS. This project runs a Development database
# beside a Production one, and a tool connects to whichever DATABASE_URL the
# shell it was started in happens to carry. Nothing said which, so every number
# a tool printed was ambiguous, and the same mistake kept being made in a new
# costume:
#
#   * diagnose.py counted `SlackEventSeen` in the DEV database and the answer
#     was read as a fact about the deployment — "0 webhook deliveries in 72h"
#     became "the Slack webhook is broken", which was never established;
#   * purge_reviews_before.py, run in the dev repl, answered "no review
#     tp_1786790990_301059" — the same sentence an id that is genuinely wrong
#     gets, for a review that was simply in the other database;
#   * review counts disagreeing between two screens, repeatedly.
#
# The fix belongs HERE and not in the tools. Seven of them query this database
# and never name it, and patching them one at a time is what has been happening
# — reactively, after each incident, always one tool behind. Every one of them
# imports this module, so an announcement at CONNECT time covers all of them,
# including tools nobody has written yet. `init_db()` was the wrong hook: a
# script that only uses SessionLocal never calls it, which is precisely the
# case that kept going wrong.
#
# TO STDERR, not stdout: several of these tools have their stdout read or piped
# (diagnose writes a report, purge prints a list a human checks), and a banner
# in the middle of that is a different kind of unhelpful. Once per process, and
# silent under pytest — 3700 tests do not each need to be told.
def _describe_database() -> str:
    """Which database, and which environment thinks it owns it."""
    u = engine.url
    where = ("deployment" if (os.getenv("REPLIT_DEPLOYMENT", "").strip()
                              or os.getenv("REPLIT_DEPLOYMENT_ID", "").strip())
             else "dev/local")
    if u.get_backend_name().startswith("sqlite"):
        return (f"sqlite {u.database or ':memory:'} — a file in THIS container "
                f"[{where}]")
    return f"{u.get_backend_name()} {u.host or '?'}/{u.database or '?'} [{where}]"


_ANNOUNCED = {"done": False}


def _announce_once(*_a) -> None:
    if _ANNOUNCED["done"] or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    _ANNOUNCED["done"] = True
    try:
        import sys
        print(f"[db] connected to {_describe_database()}", file=sys.stderr)
    except Exception:
        pass          # a banner must never be able to stop the connection


try:
    from sqlalchemy import event as _sa_event
    _sa_event.listen(engine, "connect", _announce_once)
except Exception:     # pragma: no cover - a missing hook costs the banner only
    pass


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
    # NO DEFAULT. A default here does not leave the column empty — it INVENTS
    # a date, the moment this process happened to run, and nothing downstream
    # can tell an invented one from a real one. That is how the live webhook
    # path stamped every review with its own processing time while the batch
    # importer beside it read the message timestamp correctly.
    #
    # Every creation site now states it, and a site that forgets gets NULL,
    # which renders as "no date recorded" — visibly missing rather than
    # quietly wrong.
    received_at      = Column(DateTime, nullable=True)
    status           = Column(String, default="new")   # new|draft|sent
    # Closed out rather than replied to. An untraceable review reaches Sent by
    # someone deciding there is nothing more to do — no RCA, no posted reply —
    # and the Sent tab has to say which kind of Sent it is looking at. A single
    # `status` cannot: "sent" would then mean two different pieces of work.
    closed_at        = Column(DateTime, nullable=True)
    close_reason     = Column(Text, nullable=True)
    # HOW it reached Sent. Three different pieces of work end there and the
    # tab has to say which it is looking at: a review whose reply and RCA both
    # went out, one closed out with nothing to send, and one whose RCA was
    # posted to the thread and then marked finished from beside that button.
    # DERIVED SERVER-SIDE from what actually happened, never taken from the
    # caller — a route the client asserts is a route that can be wrong.
    sent_route       = Column(String, nullable=True)   # reply|rca_posted|closed|no_rca
    # WHO IS WORKING THIS REVIEW. There is no signed-in user in this app, so the
    # system cannot assert who took a review; the associate types their name.
    # Free text on purpose — reviews get handed over, so this must stay
    # editable, and adding auth later must not lock it. Nullable, and an empty
    # value renders as "unassigned" — an unfilled owner and an unclaimed queue
    # are different facts and the UI has to be able to tell them apart.
    picked_up_by     = Column(Text, nullable=True)
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
    # True once a booking is confirmed/changed and the RCA has NOT been rebuilt
    # for it. The RCA on a just-confirmed draft still reflects the OLD match —
    # for a review that was untraceable, that is the "we couldn't find your
    # booking" reply, and posting it to a confirmed booking is how a wrong reply
    # reached a public review page. Set at confirmation, cleared when the
    # pipeline finishes rebuilding; has_rca_to_post refuses to post while True.
    rca_stale        = Column(Boolean, default=False)

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
    # A case can sit under more than one sub-theme and more than one
    # scenario. The scalars below stay in step with element 0 of each list so
    # every existing consumer - the prompt, DSS routing, the Slack post -
    # keeps working unchanged; the lists are what the dashboard edits.
    sub_theme                   = Column(String, nullable=True)   # = sub_themes[0]
    sub_themes                  = Column(JSON, default=list)
    primary_scenario            = Column(String, nullable=True)   # = scenarios[0]
    scenarios                   = Column(JSON, default=list)
    overlay_scenarios           = Column(JSON, default=list)      # scenario overlays
    wwr_scenarios               = Column(JSON, default=list)      # stacked WWR blocks (per scenario)

    # ── Tier classification provenance ──
    bid_source         = Column(String, nullable=True)   # attachment | regex | manual | None
    extracted_signals  = Column(JSON, default=dict)      # venue_hints, author_first/last, review_pub_date
    narrowing_attempts = Column(JSON, default=list)      # [{path, params, result_count}, ...]

    # ── RCA v3 shape ──
    tldr                        = Column(Text, nullable=True)
    wwr_chain                   = Column(JSON, default=list)
    prevention                  = Column(Text, nullable=True)
    evidence                    = Column(JSON, default=list)
    # v4 made this an array of {question, verdict, evidence, source, ref}.
    # v3 stored {question: answer}; drafts written before the v4 deploy
    # still hold that shape, so every reader must handle both.
    issue_specific_answers      = Column(JSON, default=list)
    checklist_answers           = Column(JSON, default=list)

    # ── Ticket fact extraction (Zendesk → structured facts) ──
    ticket_facts                = Column(JSON, nullable=True)

    # ── Editable Slack thread post ──
    slack_thread_override       = Column(Text, nullable=True)
    # WHEN THAT OVERRIDE WAS WRITTEN. Without it a hand-edited post shadows
    # every later RCA fix silently: the card shows the corrected analysis and
    # the box still holds text composed from an older one, and the box is what
    # gets sent. Comparing this against generated_at / rca_v3_edited_at is the
    # only way to tell a deliberate edit from a forgotten one.
    slack_override_at           = Column(DateTime, nullable=True)

    # ── Slack pings mentioning this BID (RCA context, not matching) ──
    slack_mentions              = Column(JSON, default=list)

    # ── Flag to Biz ──
    flag_to_biz_state           = Column(String, default="off")  # off | drafted | sent
    flag_to_biz_message         = Column(Text, nullable=True)

    # The full RCA v3 object: tldr{our_mistake,our_fix}, what_went_wrong (the
    # 5 headings), booking_logs, flags, support/sp interaction, sop_compliance,
    # issue_specific_answers, area_of_improving, takedown, prevention.
    # Its own column, NOT rca_fields: that one holds the legacy v1 shape the
    # VS flow still writes, and writing v3 over it destroyed those fields.
    rca_v3             = Column(JSON, default=dict)

    # Which prompt wrote this RCA. Provenance, not content: null means the row
    # predates the stamp, and its shape has to be guessed from its keys - which
    # is exactly the ambiguity this removes.
    rca_prompt_version = Column(String, nullable=True)

    # ── RCA v4: projections of rca_v3 ──
    #
    # READ THIS BEFORE ADDING ANOTHER ONE. These are not independent fields.
    # Each is a flat copy of a key inside rca_v3, kept so reporting queries do
    # not have to parse JSON. There is exactly one writer - the pipeline, at
    # generation time - and exactly one editor - rca_v3, through
    # PATCH /draft-v2. The client must never write these columns; a second
    # writer is how two stores of one value drift apart, and then every reader
    # has to guess which is current.
    #
    # _draft_dict() reads rca_v3 first and falls back to the column ON
    # PRESENCE, not truthiness: an empty list in rca_v3 means someone deleted
    # the last row, and it has to beat a populated column or the delete undoes
    # itself on the next load.
    sop_compliance     = Column(JSON, default=dict)   # projection of rca_v3.sop_compliance
    booking_logs       = Column(JSON, default=list)   # projection of rca_v3.booking_logs
    flags              = Column(JSON, default=list)   # projection of rca_v3.flags
    takedown           = Column(JSON, default=dict)   # projection of rca_v3.takedown
    dss                = Column(JSON, default=dict)   # projection of rca_v3.dss
    guest_issues       = Column(JSON, default=list)   # projection of rca_v3.what_went_wrong.guest_issues

    # Which canned-response situation the drafter used as its tone reference,
    # or the explicit no-match marker. The dashboard already read
    # draft.template_name in three places; nothing ever wrote it, so the
    # "Template: X" chip and the no-match placeholder could never appear.
    template_name      = Column(String, nullable=True)

    # ── Legacy fields still used by the v1 flow ──
    rca_fields         = Column(JSON, default=dict)
    signals            = Column(JSON, default=list)
    # ── The guest response ──
    # ONE STORE for what goes out, and it is in the GUEST'S language.
    #
    # `suggested_response` is the machine draft and `final_response` the human
    # edit of it; the outgoing text is `final_response or suggested_response`,
    # which is the rule every caller already followed. Both are in the
    # review's language, because that is the only text that is ever sent,
    # copied or posted.
    #
    # It used to be the other way round: these held ENGLISH, and the guest's
    # language existed only as `state.replyTranslation` in the browser — memory
    # that did not survive a reload and that nothing on the send path read. The
    # reply that actually went out was therefore the English one, on a review
    # written in Italian.
    suggested_response = Column(Text, nullable=True)
    final_response     = Column(Text, nullable=True)
    # The English working view. A PROJECTION of the outgoing text, never the
    # thing that is sent — editing it runs a translation whose result becomes
    # the outgoing text above. Held rather than re-derived so the box survives
    # a reload without paying for a translation call on every card open.
    response_english   = Column(Text, nullable=True)
    # Which outgoing text this English is the projection OF — a digest, so the
    # reply is not stored a second time. When the outgoing text is edited
    # directly this stops matching, and the English box says it is behind
    # rather than presenting a stale translation as the current one.
    response_english_of = Column(String, nullable=True)

    generated_at   = Column(DateTime, nullable=True)
    rca_posted_at  = Column(DateTime, nullable=True)  # RCA posted to the Slack thread
    # Set when a HUMAN edits the RCA body through the dashboard. Every
    # inline edit lands in rca_v3, and a re-run replaces that column whole -
    # so without this marker a bulk run silently destroys the corrections
    # someone typed, which is the one thing a re-run must never do.
    rca_v3_edited_at = Column(DateTime, nullable=True)
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


class RunJob(Base):
    """A durable pipeline run, so a run survives the container that started it.

    Every run path used to be `background_tasks.add_task(run_batch_sync, ...)` —
    fire-and-forget, executed AFTER the response in that process. On an autoscale
    deployment the container is reclaimed once the request completes, so the run
    frequently never got CPU at all: 0 of 9 re-runs moved a single field. A row
    here outlives the request. Any instance's drain loop can claim it, and a
    claim that a reclaimed container never finished becomes reclaimable once its
    lease lapses.

    A new table, so create_all() picks it up with no column migration.

    status:  queued   waiting to be claimed
             running   claimed, lease live; `lease_expires_at` bounds the claim
             done      finished
             dead      failed `max_attempts` times; `last_error` says why
    """
    __tablename__ = "run_jobs"
    id               = Column(String, primary_key=True)
    review_id        = Column(String, index=True, nullable=False)
    reason           = Column(String, default="")
    force_candidates = Column(Boolean, default=False)
    status           = Column(String, default="queued", index=True)
    attempts         = Column(Integer, default=0)
    max_attempts     = Column(Integer, default=3)
    # The claim's deadline. A `running` job whose lease is in the past was
    # claimed by an instance that never finished it — reclaimable.
    lease_expires_at = Column(DateTime, nullable=True)
    claimed_by       = Column(String, nullable=True)
    # DB-backed progress: PIPELINE_PROGRESS is in-process, so a run on another
    # instance reads as "nothing in progress here". This is the copy every
    # instance can see.
    progress         = Column(JSON, nullable=True)
    last_error       = Column(Text, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at       = Column(DateTime, default=datetime.utcnow)


def _ensure_columns():
    """
    Idempotently add columns introduced after the initial schema so existing
    installations self-heal on deploy without a manual migration.
    create_all() only creates missing tables, never missing columns.

    Two tables now. `reviews` grew the close-out fields, and a per-table loop
    is the only version of this that can report which table it failed on —
    the earlier one named rca_drafts in every message because rca_drafts was
    the only thing it could be.
    """
    import logging
    log = logging.getLogger(__name__)
    is_pg = engine.dialect.name == "postgresql"
    _ensure_table_columns("rca_drafts", _WANTED_DRAFT_COLUMNS(is_pg), log)
    _ensure_table_columns("reviews", _WANTED_REVIEW_COLUMNS(is_pg), log)


def _ensure_table_columns(table: str, wanted: dict, log) -> None:
    from sqlalchemy import inspect as _inspect, text as _text
    try:
        existing = {c["name"] for c in _inspect(engine).get_columns(table)}
    except Exception as e:
        log.error(f"[db] cannot inspect {table}, skipping migration: {e}")
        return
    added, failed = [], []
    for col, coltype in wanted.items():
        if col in existing:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(_text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))
            added.append(col)
        except Exception as e:
            failed.append(f"{col}: {e}")
    if added:
        log.info(f"[db] migration added {table} columns: {', '.join(added)}")
    # A swallowed migration failure is not a small problem: the model declares
    # the column, so every SELECT on the table then fails with "no such
    # column", the reviews list returns nothing, and the dashboard shows every
    # review as untraceable - a symptom that looks nothing like its cause.
    # Loud, and re-checked, so the log names the missing column.
    if failed:
        log.error(f"[db] MIGRATION FAILED for {len(failed)} {table} column(s): "
                  f"{'; '.join(failed)}")
    try:
        now = {c["name"] for c in _inspect(engine).get_columns(table)}
        missing = sorted(set(wanted) - now)
        if missing:
            log.error(f"[db] {table} is MISSING declared columns {missing} - "
                      f"queries on this table will fail until they are added")
    except Exception:
        pass


def _WANTED_REVIEW_COLUMNS(is_pg: bool) -> dict:
    return {
        # Closing a review out is not the same as replying to it, and the
        # Sent tab has to be able to tell them apart. Without these two, an
        # untraceable review moved to Sent looks exactly like one that got a
        # full RCA and a posted reply.
        "closed_at":    "TIMESTAMP",
        "close_reason": "TEXT",
        # Which of the routes to Sent this review took. Without it the tab can
        # separate closed-out from sent, but not a review whose RCA was posted
        # and then marked finished from one whose reply went with it.
        "sent_route":   "TEXT",
        # Free text — the associate types their name. Not a claim button, and
        # not scoped by auth (there is none). Kept nullable so an unfilled
        # owner reads differently from an empty string typed and cleared.
        "picked_up_by": "TEXT",
    }


def _WANTED_DRAFT_COLUMNS(is_pg: bool) -> dict:
    return {
        "primary_scenario":       "VARCHAR",
        "sub_themes":             "JSONB" if is_pg else "JSON",
        "scenarios":              "JSONB" if is_pg else "JSON",
        "rca_posted_at":          "TIMESTAMP",
        "rca_v3_edited_at":       "TIMESTAMP",
        "overlay_scenarios":      "JSONB" if is_pg else "JSON",
        "wwr_scenarios":          "JSONB" if is_pg else "JSON",
        "ticket_facts":           "JSONB" if is_pg else "JSON",
        "slack_thread_override":  "TEXT",
        "slack_override_at":      "TIMESTAMP",
        "slack_mentions":         "JSONB" if is_pg else "JSON",
        "rca_v3":                 "JSONB" if is_pg else "JSON",
        "template_name":          "VARCHAR",
        # RCA v4 — see server/migrations/015_rca_v4.sql
        "sop_compliance":         "JSONB" if is_pg else "JSON",
        "booking_logs":           "JSONB" if is_pg else "JSON",
        "flags":                  "JSONB" if is_pg else "JSON",
        "takedown":               "JSONB" if is_pg else "JSON",
        "dss":                    "JSONB" if is_pg else "JSON",
        "guest_issues":           "JSONB" if is_pg else "JSON",
        "rca_prompt_version":     "VARCHAR",
        # The English working view of the guest response, and the digest of the
        # outgoing text it projects. The outgoing text itself stays in
        # final_response/suggested_response — there is no column here for it,
        # because a second column holding the same fact is the defect.
        "response_english":       "TEXT",
        "response_english_of":    "VARCHAR",
        # Stale-RCA guard: booking confirmed but RCA not yet rebuilt for it.
        "rca_stale":              "BOOLEAN",
    }


def assert_durable_on_deploy(backend: str = None):
    """Refuse to boot a DEPLOYMENT on a database that a redeploy will wipe.

    `backend` defaults to the live engine's backend; it is a parameter so the
    guard can be driven without rebuilding the module engine.

    THE FAILURE THIS TURNS FROM SILENT TO LOUD. The deployment is autoscale —
    stateless, multi-instance, a fresh container per instance and per deploy —
    and the DATABASE_URL fallback is a sqlite file INSIDE that container. So
    every redeploy started every instance on an empty database and the reviews
    ingested since the last deploy were gone, with only a log line to say so.
    A team using the dashboard daily would lose a day's work to a routine
    redeploy and have no way to see it coming.

    A warning was not enough — it happened anyway. So on a deployment
    (REPLIT_DEPLOYMENT is set by Replit only there, never in the dev repl) with
    a sqlite database, this RAISES and the deployment does not come up. The
    message says the one thing that fixes it: point DATABASE_URL at Postgres.

    The dev repl is untouched — it has no REPLIT_DEPLOYMENT — so local sqlite
    development keeps working. ALLOW_EPHEMERAL_DB=1 is a deliberate escape hatch
    for anyone who really wants a throwaway deployment, so this can never leave
    someone permanently unable to boot.
    """
    is_deploy = bool(os.getenv("REPLIT_DEPLOYMENT", "").strip()
                     or os.getenv("REPLIT_DEPLOYMENT_ID", "").strip())
    if not is_deploy:
        return
    if os.getenv("ALLOW_EPHEMERAL_DB", "").strip() in ("1", "true", "yes"):
        import logging
        logging.getLogger(__name__).error(
            "[db] ALLOW_EPHEMERAL_DB is set — running a DEPLOYMENT on an "
            "ephemeral database ON PURPOSE. Every redeploy will wipe it.")
        return
    if backend is None:
        backend = engine.url.get_backend_name()
    if backend.startswith("sqlite"):
        raise RuntimeError(
            "REFUSING TO START. This is a deployment (REPLIT_DEPLOYMENT is set) "
            "and DATABASE_URL is a sqlite file inside the container. Autoscale "
            "gives every "
            "instance and every redeploy a fresh empty copy, so ingested "
            "reviews are lost on the next deploy — which is what just happened. "
            "Provision Replit Postgres and set DATABASE_URL (or the PGHOST / "
            "PGDATABASE / PGUSER / PGPASSWORD / PGPORT it exports) on the "
            "deployment, then redeploy. To run a throwaway deployment on a "
            "disposable database anyway, set ALLOW_EPHEMERAL_DB=1.")


def init_db():
    assert_durable_on_deploy()
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    _warn_if_container_local()


def _warn_if_container_local():
    """Say it at startup when the database is a file inside this container.

    With the default DATABASE_URL (sqlite:///./local.db) a published deployment
    and the dev repl each keep their OWN reviews, and neither can see the
    other's. Two dashboards then disagree permanently and no amount of cache
    clearing or restarting changes it, because it is not a caching problem.
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        if engine.url.get_backend_name().startswith("sqlite"):
            log.warning(
                "[db] DATABASE_URL is %s - a file inside THIS container. A "
                "published deployment keeps a separate copy, so two dashboards "
                "will show different reviews. Set DATABASE_URL to a shared "
                "Postgres for both environments to see the same data.",
                engine.url.database or ":memory:")
        else:
            log.info("[db] shared database: %s/%s",
                     engine.url.host or "?", engine.url.database or "?")
    except Exception:
        pass


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
