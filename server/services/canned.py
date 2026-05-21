"""
Reads canned responses from a Google Sheet.
Falls back to mock canned responses if credentials aren't set.

The Sheet should have two columns: Category | Response Text
The GCP service account needs Sheets API access (or make the sheet publicly readable).
"""
import logging
from server.config import is_live, CANNED_RESPONSES_SHEET_ID, GCP_SERVICE_ACCOUNT_JSON
from server.services.mock_data import MOCK_CANNED

log = logging.getLogger(__name__)
_cached = None


async def get_canned_responses() -> str:
    """Returns canned responses as a plain text block for the Claude prompt."""
    global _cached
    if _cached:
        return _cached

    if not is_live("canned") or not is_live("bigquery"):
        # Reuse GCP credentials for Sheets too
        return MOCK_CANNED

    try:
        import json
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_info(
            json.loads(GCP_SERVICE_ACCOUNT_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        result = service.spreadsheets().values().get(
            spreadsheetId=CANNED_RESPONSES_SHEET_ID,
            range="A:B"
        ).execute()
        rows = result.get("values", [])
        # Format as text block: [CATEGORY]\nResponse text\n\n
        lines = []
        for row in rows[1:]:  # skip header
            if len(row) >= 2:
                lines.append(f"[{row[0].upper()}]\n{row[1]}")
        _cached = "\n\n".join(lines) if lines else MOCK_CANNED
        return _cached
    except Exception as e:
        log.exception(f"Canned responses fetch failed: {e}")
        return MOCK_CANNED
