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
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"

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

# Retool DSS
DSS_WEBHOOK_URL = os.getenv("DSS_WEBHOOK_URL", "")

# Canned responses
CANNED_RESPONSES_SHEET_ID = os.getenv("CANNED_RESPONSES_SHEET_ID", "")

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
        "dss":            bool(DSS_WEBHOOK_URL),
        "canned":         bool(CANNED_RESPONSES_SHEET_ID),
    }
    return checks.get(service, False)


def status_summary() -> dict:
    return {
        "mock_mode":   MOCK_MODE,
        "ai_provider": "Replit AI Integrations — Anthropic Claude",
        "services": {k: is_live(k) for k in
                     ["anthropic","slack_inbound","slack_outbound",
                      "bigquery","zendesk","dss","canned"]},
    }
