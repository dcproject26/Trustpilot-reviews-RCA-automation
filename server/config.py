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
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

# Slack
SLACK_SIGNING_SECRET  = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_BOT_TOKEN       = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_USER_TOKEN      = os.getenv("SLACK_USER_TOKEN", "")
SLACK_CHANNEL_ORM     = os.getenv("SLACK_CHANNEL_ORM", "")    # #team-orm-online-reputation
SLACK_CHANNEL_ALERT   = os.getenv("SLACK_CHANNEL_ALERT", "")  # #alert-customerlove
# Optional — only set this if you want to filter by a specific bot/app user.
# Without it, the app detects reviews by star rating symbols (★/☆) in the message.
TRUSTPILOT_BOT_USER_ID = os.getenv("TRUSTPILOT_BOT_USER_ID", "")

# BigQuery
GCP_SERVICE_ACCOUNT_JSON  = os.getenv("GCP_SERVICE_ACCOUNT_JSON", "")
BIGQUERY_BOOKINGS_TABLE   = os.getenv("BIGQUERY_BOOKINGS_TABLE",
                                       "headout-analytics.analytics_reporting.fct_bookings")
BIGQUERY_REVIEWS_TABLE    = os.getenv("BIGQUERY_REVIEWS_TABLE",
                                       "headout-analytics.analytics_reporting.fct_reviews")
BIGQUERY_FULFILMENTS_TABLE = os.getenv("BIGQUERY_FULFILMENTS_TABLE",
                                        "headout-analytics.analytics_reporting.fct_fulfilments")

# Zendesk
ZENDESK_SUBDOMAIN        = os.getenv("ZENDESK_SUBDOMAIN", "headout")
ZENDESK_EMAIL            = os.getenv("ZENDESK_EMAIL", "")
ZENDESK_API_TOKEN        = os.getenv("ZENDESK_API_TOKEN", "")
ZENDESK_BOOKING_FIELD_ID = os.getenv("ZENDESK_BOOKING_FIELD_ID", "360021524471")

# Google Apps Script — fetches full comments for multiple Zendesk tickets in one call
APPS_SCRIPT_URL = os.getenv(
    "APPS_SCRIPT_URL",
    "https://script.google.com/macros/s/AKfycbyfYMqIcsihcxRVBAznpyrXtyZMvlITCDpPB7Uarc_cMz8mWgCCbg_O9WZQMJeFjCFqOA/exec"
)

# Retool DSS
DSS_WEBHOOK_URL = os.getenv("DSS_WEBHOOK_URL", "")

# Canned responses
CANNED_RESPONSES_SHEET_ID = os.getenv("CANNED_RESPONSES_SHEET_ID", "")

# Database (auto-injected by Replit Postgres)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local.db")

ORM_CHANNELS = [c for c in [SLACK_CHANNEL_ORM, SLACK_CHANNEL_ALERT] if c]


def _bq_connector_available() -> bool:
    """Replit BigQuery integration (no service-account key needed)."""
    try:
        from server.services.bq_connector import available
        return available()
    except Exception:
        return False


def is_live(service: str) -> bool:
    if MOCK_MODE:
        return False
    checks = {
        "anthropic":      True,  # Always live — Replit AI Integrations
        "slack_inbound":  bool(SLACK_SIGNING_SECRET and SLACK_BOT_TOKEN),
        "slack_outbound": bool(SLACK_USER_TOKEN),
        "bigquery":       bool(BIGQUERY_BOOKINGS_TABLE and
                               (GCP_SERVICE_ACCOUNT_JSON or _bq_connector_available())),
        "zendesk":        bool(ZENDESK_SUBDOMAIN and ZENDESK_API_TOKEN),
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
