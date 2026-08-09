"""
Config. Reads env vars. Every integration has an is_live() check —
when credentials are missing it falls back to mock data automatically.

Claude runs via Replit AI Integrations (no API key needed).
Anthropic is already toggled on in the Headout Replit workspace.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Set to "true" to force all mock data regardless of credentials
# MOCK_MODE means: is_live() reports every service as down, so the code
# paths that ask before calling out will not call out. It is NOT a
# blanket network kill switch, and treating it as one has bitten:
#
#   Slack    - post_to_thread checked only whether a client existed, so
#              a mock run posted to the real API. Fixed; it checks this
#              flag first now.
#   Anthropic- claude._call deliberately calls the model on the mock
#              path, which is how a demo produces a real RCA. Still
#              true, and intended - but it does mean MOCK_MODE costs
#              tokens.
#
# "1", "yes" and "on" all mean true here. Accepting only the literal
# string "true" meant MOCK_MODE=1 silently ran against real services -
# it looks set, the app reports mock_mode false, and the difference only
# shows up as a confusing failure further along.
MOCK_MODE = os.getenv("MOCK_MODE", "false").strip().lower() in (
    "true", "1", "yes", "on")

# Claude model — Replit AI Integrations injects credentials automatically
# Default model: claude-sonnet-4-6 is supported by Replit AI Integrations.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Slack
# .strip() — pasted secrets sometimes carry trailing whitespace/newlines
SLACK_SIGNING_SECRET  = os.getenv("SLACK_SIGNING_SECRET", "").strip()
SLACK_BOT_TOKEN       = os.getenv("SLACK_BOT_TOKEN", "").strip()
SLACK_USER_TOKEN      = os.getenv("SLACK_USER_TOKEN", "").strip()
SLACK_CHANNEL_ORM     = os.getenv("SLACK_CHANNEL_ORM", "").strip()    # #team-orm-trustpilot-social
SLACK_CHANNEL_ALERT   = os.getenv("SLACK_CHANNEL_ALERT", "").strip()  # optional — unused for ingestion
# Optional — only set this if you want to filter by a specific bot/app user.
# Without it, the app detects reviews by star rating symbols (★/☆) in the message.
TRUSTPILOT_BOT_USER_ID = os.getenv("TRUSTPILOT_BOT_USER_ID", "").strip()

# BigQuery
GCP_SERVICE_ACCOUNT_JSON  = os.getenv("GCP_SERVICE_ACCOUNT_JSON", "")
BIGQUERY_BOOKINGS_TABLE   = os.getenv("BIGQUERY_BOOKINGS_TABLE",
                                       "headout-analytics.analytics_reporting.fct_bookings")
BIGQUERY_REVIEWS_TABLE    = os.getenv("BIGQUERY_REVIEWS_TABLE",
                                       "headout-analytics.analytics_reporting.fct_reviews")
BIGQUERY_FULFILMENTS_TABLE = os.getenv("BIGQUERY_FULFILMENTS_TABLE",
                                        "headout-analytics.analytics_reporting.fct_fulfilments")
BIGQUERY_SUPPORT_TABLE     = os.getenv("BIGQUERY_SUPPORT_TABLE",
                                        "headout-analytics.analytics_reporting.fct_support_queries")

# Zendesk
ZENDESK_SUBDOMAIN        = os.getenv("ZENDESK_SUBDOMAIN", "headout")
ZENDESK_EMAIL            = os.getenv("ZENDESK_EMAIL", "")
ZENDESK_API_TOKEN        = os.getenv("ZENDESK_API_TOKEN", "")
ZENDESK_BOOKING_FIELD_ID = os.getenv("ZENDESK_BOOKING_FIELD_ID", "360021524471")
# Custom field IDs confirmed from the 2024 Retool workflow
# Confirmed against a live ticket (ZD-33979875) via tools/zd_field_discovery.py.
# These carry the booking's own facts, so matching does not depend on the guest
# having named the venue in the review.
ZENDESK_FIELD_GUEST_NAME  = os.getenv("ZENDESK_FIELD_GUEST_NAME",  "51116641874073")
ZENDESK_FIELD_GUEST_EMAIL = os.getenv("ZENDESK_FIELD_GUEST_EMAIL", "360026670311")
ZENDESK_FIELD_EXPERIENCE  = os.getenv("ZENDESK_FIELD_EXPERIENCE",  "360021471312")
ZENDESK_FIELD_CITY        = os.getenv("ZENDESK_FIELD_CITY",        "360021522151")
ZENDESK_FIELD_VISIT_DATE  = os.getenv("ZENDESK_FIELD_VISIT_DATE",  "360024232231")
ZENDESK_FIELD_PAX         = os.getenv("ZENDESK_FIELD_PAX",         "360021522291")
ZENDESK_FIELD_VENDOR_NAME = os.getenv("ZENDESK_FIELD_VENDOR_NAME", "8136487555225")
# NOT a booking id — the itinerary / payment id. Must never be harvested as one.
ZENDESK_FIELD_ITINERARY   = os.getenv("ZENDESK_FIELD_ITINERARY",   "360021524491")

ZENDESK_TGID_FIELD = int(os.getenv("ZENDESK_TGID_FIELD", "360024198092"))
ZENDESK_TID_FIELD  = int(os.getenv("ZENDESK_TID_FIELD",  "360024232711"))
# Optional brand split (brand IDs). If either is unset, all tickets → guest timeline.
ZENDESK_BRAND_GUEST = os.getenv("ZENDESK_BRAND_GUEST", "").strip()
ZENDESK_BRAND_SP    = os.getenv("ZENDESK_BRAND_SP", "").strip()
# Ticket tags identifying bot/AI actors (comma-separated)
ZENDESK_BOT_TAGS = [t.strip() for t in
                    os.getenv("ZENDESK_BOT_TAGS", "minded_ai").split(",") if t.strip()]

# Google Apps Script — fetches full comments for multiple Zendesk tickets in one call
APPS_SCRIPT_URL = os.getenv(
    "APPS_SCRIPT_URL",
    "https://script.google.com/macros/s/AKfycbyfYMqIcsihcxRVBAznpyrXtyZMvlITCDpPB7Uarc_cMz8mWgCCbg_O9WZQMJeFjCFqOA/exec"
)

# BMS + TGID deeplink URL patterns (env-overridable)
BMS_URL_PATTERN  = os.getenv("BMS_URL_PATTERN",  "https://aries.headout.com/bms/booking/{bid}")
TGID_URL_PATTERN = os.getenv("TGID_URL_PATTERN", "https://www.headout.com/tour/{tgid}")

# DSS — the "DSS All in One" sheet the Retool app reads (one tab per type)
DSS_WEBHOOK_URL         = os.getenv("DSS_WEBHOOK_URL", "")        # legacy, unused
DSS_SHEET_ID            = os.getenv("DSS_SHEET_ID",
                                     "13PpmkVW5mvLbpW5wtSUOZBhRQfQUsOJtLys2osQnvR0")
DSS_SHEET_TAB           = os.getenv("DSS_SHEET_TAB", "")          # legacy, unused

# Every review and its RCA, as rows. Written in TWO phases: a row on arrival
# carrying the id, the time and the Slack link, and the same row filled in when
# the RCA is sent or the review is closed out. The tab is the gid from the
# sheet's URL — that is what anyone pastes — and SheetIO resolves it to the
# name the API wants.
#
# WRITING NEEDS THE SHEET SHARED WITH THE SERVICE ACCOUNT as an editor. The
# same credential reads three other sheets already; none of those are writable,
# so a read that works proves nothing about this.
RCA_EXPORT_SHEET_ID     = os.getenv("RCA_EXPORT_SHEET_ID",
                                    "19Im-BbgWq6idQqP6SgWoEs-cx8sBwIEmimAbwAq9aBU")
RCA_EXPORT_SHEET_TAB    = os.getenv("RCA_EXPORT_SHEET_TAB", "0")

# Canned responses — OPTIONAL live refresh of server/data/canned_macros.json.
#
# No default, deliberately. There used to be one here while .env.example named
# a DIFFERENT sheet and called that one "the confirmed Sheet ID", so whether an
# unset secret read the right document was unanswerable from either file. The
# approved macros are checked in now; this only says whether to try refreshing
# them from a live sheet, and unset means "don't" rather than "guess".
CANNED_RESPONSES_SHEET_ID = os.getenv("CANNED_RESPONSES_SHEET_ID", "")

# RCA Checklist — Google Sheet
RCA_CHECKLIST_SHEET_ID = os.getenv("RCA_CHECKLIST_SHEET_ID",
                                    "1RpvQCz35_pOTnWGrYL0rfPKUXejmt5KeBppgI9QTYJ8")
RCA_CHECKLIST_GID      = os.getenv("RCA_CHECKLIST_GID", "1186061023")

# Google Sheets API key (optional — used only to resolve tab names via metadata)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()

# Database (auto-injected by Replit Postgres)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local.db")

# Ingestion accepts events ONLY from SLACK_CHANNEL_ORM.
# SLACK_CHANNEL_ALERT is optional and intentionally unused for ingestion.
ORM_CHANNELS = [c for c in [SLACK_CHANNEL_ORM] if c]


def _bq_connector_available() -> bool:
    """Replit BigQuery integration (no service-account key needed)."""
    try:
        from server.services.bq_connector import available
        return available()
    except Exception:
        return False


def _sheets_connector_available() -> bool:
    """Replit Google Sheets integration (OAuth — no service-account key)."""
    try:
        from server.services.sheets_connector import available
        return available()
    except Exception:
        return False


def _zd_connector_available() -> bool:
    """Replit Zendesk integration (OAuth — no email/API-token pair needed)."""
    try:
        from server.services.zd_connector import available
        return available()
    except Exception:
        return False


def is_live(service: str) -> bool:
    if MOCK_MODE:
        return False
    checks = {
        "anthropic":      bool(
                              (os.getenv("AI_INTEGRATIONS_ANTHROPIC_BASE_URL") and
                               os.getenv("AI_INTEGRATIONS_ANTHROPIC_API_KEY"))
                              or os.getenv("ANTHROPIC_API_KEY")),
        "slack_inbound":  bool(SLACK_SIGNING_SECRET and SLACK_BOT_TOKEN),
        "slack_outbound": bool(SLACK_USER_TOKEN),
        "bigquery":       bool(BIGQUERY_BOOKINGS_TABLE and
                               (GCP_SERVICE_ACCOUNT_JSON or _bq_connector_available())),
        "zendesk":        bool((ZENDESK_SUBDOMAIN and ZENDESK_API_TOKEN)
                               or _zd_connector_available()),
        "apps_script":    bool(APPS_SCRIPT_URL),
        "dss":            bool(DSS_SHEET_ID),
        "canned":         bool(CANNED_RESPONSES_SHEET_ID),
        "checklist":      bool(RCA_CHECKLIST_SHEET_ID),
        "sheet_export":   bool(RCA_EXPORT_SHEET_ID and
                               (GCP_SERVICE_ACCOUNT_JSON or
                                _sheets_connector_available())),
    }
    return checks.get(service, False)


def status_summary() -> dict:
    return {
        "mock_mode":   MOCK_MODE,
        "ai_provider": "Replit AI Integrations — Anthropic Claude",
        "services": {k: is_live(k) for k in
                     ["anthropic","slack_inbound","slack_outbound",
                      "bigquery","zendesk","dss","canned","checklist"]},
    }
