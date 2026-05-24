"""
Reads canned responses from the Google Sheet.
Falls back to the embedded scenarios in prompts.py if the Sheet is unavailable.

Sheet format expected: two columns — Category | Response Text
The GCP service account needs Google Sheets API read access,
or make the sheet publicly readable (View access for anyone with the link).
"""
import logging
from server.config import is_live, CANNED_RESPONSES_SHEET_ID, GCP_SERVICE_ACCOUNT_JSON

log = logging.getLogger(__name__)
_cached: str = ""


async def get_canned_responses() -> str:
    """Returns canned responses as a text block for the Claude prompt."""
    global _cached
    if _cached:
        return _cached

    if not is_live("canned"):
        # Return empty string — claude.py will use EMBEDDED_CANNED as fallback
        return ""

    try:
        import json
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_info(
            json.loads(GCP_SERVICE_ACCOUNT_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        result  = service.spreadsheets().values().get(
            spreadsheetId=CANNED_RESPONSES_SHEET_ID,
            range="A:B",
        ).execute()
        rows = result.get("values", [])

        lines = []
        for row in rows[1:]:   # skip header
            if len(row) >= 2 and row[1].strip():
                lines.append(f"[{row[0].upper().strip()}]\n{row[1].strip()}")

        _cached = "\n\n".join(lines) if lines else ""
        log.info(f"[canned] loaded {len(lines)} scenarios from Sheet")
        return _cached

    except Exception as e:
        log.exception(f"Canned responses fetch failed: {e}")
        return ""
