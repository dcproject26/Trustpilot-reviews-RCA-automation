"""
Claude wrapper using Replit AI Integrations.

When running on Replit with AI Integrations enabled (confirmed ON for Headout's
workspace), the Anthropic SDK picks up managed credentials automatically.
No ANTHROPIC_API_KEY needed in Secrets.

Falls back to mock data if MOCK_MODE is on or the SDK is unavailable.
"""
import json, logging
from server.config import MOCK_MODE, ANTHROPIC_MODEL
from server.services.mock_data import MOCK_RCA_FIELDS, MOCK_RESPONSES, MOCK_REVIEWS

log = logging.getLogger(__name__)

try:
    from anthropic import Anthropic
    _client    = Anthropic()   # No api_key — Replit injects credentials
    _available = True
except Exception as e:
    log.warning(f"Anthropic SDK unavailable: {e}")
    _client    = None
    _available = False


def _live():
    return _available and not MOCK_MODE


async def translate(body: str, lang: str, review_id: str = None) -> str:
    if not _live():
        for r in MOCK_REVIEWS:
            if r["id"] == review_id:
                return r.get("body_english") or body
        return body
    from server.prompts import translation_prompt
    try:
        msg = _client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=1500,
            messages=[{"role": "user", "content": translation_prompt(body, lang)}],
        )
        return "".join(b.text for b in msg.content if b.type == "text").strip()
    except Exception as e:
        log.exception(f"Translation failed: {e}")
        return body


async def generate_rca(review_text, booking, timeline,
                        insights, dss, review_id=None) -> dict:
    if not _live():
        if review_id and review_id in MOCK_RCA_FIELDS:
            return dict(MOCK_RCA_FIELDS[review_id])
        return {"queryIssueType": "Other", "whatWentWrong": "(mock)", "signals": []}

    from server.prompts import rca_generation_prompt
    text = ""
    try:
        msg = _client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=4000,
            messages=[{"role": "user", "content": rca_generation_prompt(
                review_text, booking, timeline, insights, dss)}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        # Strip markdown fences if Claude adds them despite instructions
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                stripped = part.strip()
                if stripped.startswith("{"):
                    text = stripped
                    break
        return json.loads(text.strip())
    except json.JSONDecodeError:
        log.error(f"Invalid JSON from Claude RCA. Raw (first 400): {text[:400]}")
        # Return a partial dict so the dashboard still shows something
        return {
            "queryIssueType": "Other",
            "whatWentWrong":  text[:800] if text else "(generation failed)",
            "signals": [],
        }
    except Exception as e:
        log.exception(f"RCA generation failed: {e}")
        return {"queryIssueType": "Other", "whatWentWrong": "(generation failed)", "signals": []}


async def draft_response(review_text: str, issue_type: str,
                          solution: str, canned: str,
                          review_id: str = None,
                          guest_name: str = "") -> str:
    if not _live():
        return MOCK_RESPONSES.get(
            review_id, "We apologise for your experience. Our team is looking into this.")

    from server.prompts import response_draft_prompt, EMBEDDED_CANNED
    # Use the Sheet canned responses if available, otherwise fall back to embedded ones
    canned_to_use = canned.strip() if canned and len(canned.strip()) > 100 else EMBEDDED_CANNED

    try:
        msg = _client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=600,
            messages=[{"role": "user", "content": response_draft_prompt(
                review_text, issue_type, solution, canned_to_use, guest_name)}],
        )
        return "".join(b.text for b in msg.content if b.type == "text").strip()
    except Exception as e:
        log.exception(f"Response draft failed: {e}")
        return MOCK_RESPONSES.get(review_id, "")
