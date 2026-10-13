"""Case assignment — kept in sync between the dashboard and the Slack channel.

One case can be claimed two ways, and they must agree:
  * a :wip: reaction on the review's Slack message, or
  * the "Picked up by" roster on the dashboard.

Claiming either way writes `picked_up_by` + `picked_up_at` on the Review, and
mirrors to the other side (dashboard claim -> bot adds :wip: + a thread note;
Slack :wip: -> the dashboard owner updates). Solving the case (sent/closed) adds
a :white_check_mark: WITHOUT removing :wip:, so the channel shows the full
lifecycle.

THE LOGIC IS SEPARATED FROM THE SLACK CALLS ON PURPose. `decide_reaction()` and
`apply_claim()`/`apply_release()` are pure — they take a Review and return what
should change — so every edge case is unit-tested without a Slack round trip.
The webhook and the PATCH endpoint call these, commit, then fire the (best-
effort, failure-tolerant) Slack side effects.

Edge cases this module is built to get right (all tested):
  * first-picker-wins: a :wip: on an already-owned case does NOT steal it.
  * the bot's OWN :wip: (added when the dashboard claims) must not loop back
    into a second assignment — the reactor is the bot, so it is ignored.
  * a reaction on a review we do not have (an old message) is a no-op, logged.
  * an unmapped Slack user falls back to their display name AND is logged, so a
    claim is never silently dropped.
  * reacting on an already-solved case is ignored (you cannot pick up done work).
  * un-reacting releases the case back to unpicked only if it was that person's.
"""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)

WIP_EMOJI = "wip"                 # :wip: — "I'm on this"
DONE_EMOJI = "white_check_mark"   # :white_check_mark: — added on solve, wip kept


# ── identity: Slack user id <-> roster name ─────────────────────────────────

def _slack_id_to_name() -> dict:
    """Invert the roster's name->slack_id map (content/orm_macros.yaml). Read
    live rather than cached at import, so editing the roster takes effect without
    a restart."""
    from server.prompts import REVIEWER_SLACK_IDS
    return {sid: name for name, sid in (REVIEWER_SLACK_IDS or {}).items() if sid}


def reviewer_for_slack_id(uid: str) -> tuple[str | None, bool]:
    """(name, mapped). `mapped` is False when the user is not on the roster —
    the caller then falls back to a display name and logs it, so an off-roster
    reaction still assigns rather than vanishing."""
    name = _slack_id_to_name().get((uid or "").strip())
    return (name, True) if name else (None, False)


_BOT_UID: str | None = None

def bot_user_id() -> str | None:
    """The ORM bot's own Slack user id, fetched once and cached — the loop
    guard. When the dashboard claims a case the bot adds :wip:, which Slack
    echoes back as a reaction_added by the bot; that echo must not become a
    second claim. TRUSTPILOT_BOT_USER_ID in config is the Trustpilot app, NOT
    us, so it cannot stand in for this."""
    global _BOT_UID
    if _BOT_UID is not None:
        return _BOT_UID or None
    try:
        from server.services.slack import _bot
        if _bot is not None:
            _BOT_UID = (_bot.auth_test() or {}).get("user_id", "") or ""
        else:
            _BOT_UID = ""
    except Exception as e:
        log.warning(f"[assign] could not resolve bot user id (loop guard): {e}")
        _BOT_UID = ""
    return _BOT_UID or None


# ── review lookup ───────────────────────────────────────────────────────────

def review_id_for_ts(ts: str) -> str:
    """The Review id the ingest builds from a Slack message ts — the same
    `tp_<ts with . -> _>` the webhook writes, so a reaction's item.ts finds the
    exact review."""
    return f"tp_{(ts or '').replace('.', '_')}"


def _is_solved(review) -> bool:
    return (getattr(review, "status", "") or "") == "sent"


# ── pure decisions (no DB commit, no Slack) ─────────────────────────────────

def apply_claim(review, name: str, at: datetime | None = None) -> str:
    """First-picker-wins claim. Mutates the review in memory and returns:
      'claimed'       — it was unowned, now owned by `name`
      'already_mine'  — already owned by `name` (idempotent re-claim)
      'already_owned' — owned by someone ELSE; left untouched (first wins)
    The caller decides what to mirror to Slack from the return value."""
    cur = (getattr(review, "picked_up_by", None) or "").strip()
    want = (name or "").strip()
    if not want:
        return "already_owned"              # a claim with no name is not a claim
    if not cur:
        review.picked_up_by = want
        review.picked_up_at = at or datetime.utcnow()
        return "claimed"
    if cur == want:
        # Idempotent: keep the original pickup time, do not reset the clock.
        return "already_mine"
    return "already_owned"


def apply_release(review, name: str | None, force: bool = False) -> str:
    """Release a claim back to unpicked.
      force=True  (dashboard set owner to empty) — always clears.
      force=False (a :wip: removed on Slack) — clears ONLY if `name` is the
                   current owner, so one person un-reacting cannot drop another
                   person's case.
    Returns 'released' or 'kept'."""
    cur = (getattr(review, "picked_up_by", None) or "").strip()
    if not cur:
        return "kept"                        # already unpicked
    if force or cur == (name or "").strip():
        # EMPTY STRING, not None — the codebase distinguishes "" (picked then
        # released) from None (never touched); `test_case_header` and the model
        # comment both depend on it. The pickup TIME is cleared either way.
        review.picked_up_by = ""
        review.picked_up_at = None
        return "released"
    return "kept"


def apply_reassign(review, name: str, at: datetime | None = None) -> str:
    """Dashboard EXPLICIT override — the roster is a deliberate human choice, so
    it wins over an existing owner (unlike a Slack :wip:, which is first-wins).
    Empty name routes to a forced release. Returns 'reassigned' | 'released' |
    'unchanged'."""
    want = (name or "").strip()
    cur = (getattr(review, "picked_up_by", None) or "").strip()
    if not want:
        return "released" if apply_release(review, None, force=True) == "released" else "unchanged"
    if cur == want:
        return "unchanged"
    review.picked_up_by = want
    review.picked_up_at = at or datetime.utcnow()
    return "reassigned"


# ── Slack side effects (best-effort, never raise into the caller) ───────────

def _channel_ts(review):
    return (getattr(review, "slack_channel", "") or "",
            getattr(review, "slack_ts", "") or "")


def mirror_claim_to_slack(review, name: str) -> None:
    """Dashboard claimed -> add :wip: and a thread note on the review message.
    Failure is logged, never raised: the DB is the source of truth, and a Slack
    hiccup must not fail the assignment the person just made."""
    from server.services.slack import _bot
    ch, ts = _channel_ts(review)
    if not (_bot and ch and ts):
        return
    try:
        _bot.reactions_add(channel=ch, timestamp=ts, name=WIP_EMOJI)
    except Exception as e:
        # "already_reacted" is fine — the mark is already there.
        if "already_reacted" not in str(e):
            log.info(f"[assign] could not add :wip: for {getattr(review,'id','?')}: {e}")
    try:
        _bot.chat_postMessage(channel=ch, thread_ts=ts,
                              text=f":wip: Picked up by {name}")
    except Exception as e:
        log.info(f"[assign] could not post pickup note for {getattr(review,'id','?')}: {e}")


def mirror_release_to_slack(review) -> None:
    """Dashboard un-assigned -> remove :wip: so the channel shows it is back in
    the pool."""
    from server.services.slack import _bot
    ch, ts = _channel_ts(review)
    if not (_bot and ch and ts):
        return
    try:
        _bot.reactions_remove(channel=ch, timestamp=ts, name=WIP_EMOJI)
    except Exception as e:
        if "no_reaction" not in str(e):
            log.info(f"[assign] could not remove :wip: for {getattr(review,'id','?')}: {e}")


def mark_solved_on_slack(review) -> None:
    """Solve (sent/closed) -> add :white_check_mark:, KEEP :wip:. The lifecycle
    stays visible in the channel: claimed (:wip:) then done (✅)."""
    from server.services.slack import _bot
    ch, ts = _channel_ts(review)
    if not (_bot and ch and ts):
        return
    try:
        _bot.reactions_add(channel=ch, timestamp=ts, name=DONE_EMOJI)
    except Exception as e:
        if "already_reacted" not in str(e):
            log.info(f"[assign] could not add ✅ for {getattr(review,'id','?')}: {e}")


# ── the webhook entry point ─────────────────────────────────────────────────

def handle_reaction_event(event: dict, db) -> dict:
    """Process one Slack reaction_added / reaction_removed. Pure-logic decisions
    delegate to apply_claim/apply_release; this wires them to the event and the
    DB and names every outcome (never a silent drop)."""
    from server.db import Review

    if (event.get("reaction") or "") != WIP_EMOJI:
        return {"ok": True, "ignored": "not_wip"}

    item = event.get("item") or {}
    if item.get("type") != "message":
        return {"ok": True, "ignored": "not_a_message"}

    reactor = (event.get("user") or "").strip()
    # LOOP GUARD: the bot's own :wip: (added when the dashboard claimed) echoes
    # back here as a reaction by the bot. Ignore it, or it becomes a 2nd claim.
    if reactor and reactor == bot_user_id():
        return {"ok": True, "ignored": "own_reaction"}

    rid = review_id_for_ts(item.get("ts", ""))
    review = db.query(Review).filter(Review.id == rid).first()
    if not review:
        # Not a silent zero: a reaction on a message we never ingested is said.
        log.info(f"[assign] :wip: on {item.get('ts')} — no review {rid} in the DB; "
                 f"nothing to assign (old message, or ingest missed it).")
        return {"ok": True, "ignored": "no_such_review", "review_id": rid}

    name, mapped = reviewer_for_slack_id(reactor)
    if not mapped:
        name = _display_name(reactor) or reactor
        log.info(f"[assign] reactor {reactor} is not on the roster — using "
                 f"display name {name!r}. Add them to reviewer_slack_ids to "
                 f"keep attribution consistent.")

    etype = event.get("type")
    if etype == "reaction_added":
        if _is_solved(review):
            # Cannot pick up finished work; say so rather than silently assign.
            log.info(f"[assign] :wip: on already-solved {rid} by {name} — ignored.")
            return {"ok": True, "ignored": "already_solved", "review_id": rid}
        outcome = apply_claim(review, name)
        if outcome == "claimed":
            db.commit()
            log.info(f"[assign] {rid} claimed by {name} via :wip:")
        return {"ok": True, "action": outcome, "review_id": rid, "owner": review.picked_up_by}

    if etype == "reaction_removed":
        outcome = apply_release(review, name, force=False)
        if outcome == "released":
            db.commit()
            log.info(f"[assign] {rid} released by {name} (un-:wip:) — back in the pool")
        return {"ok": True, "action": outcome, "review_id": rid, "owner": review.picked_up_by}

    return {"ok": True, "ignored": f"unhandled_{etype}"}


def _display_name(uid: str) -> str:
    from server.services.slack import _bot
    if not (_bot and uid):
        return ""
    try:
        u = (_bot.users_info(user=uid) or {}).get("user") or {}
        return (u.get("profile") or {}).get("display_name") or u.get("real_name") or ""
    except Exception:
        return ""
