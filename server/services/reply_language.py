"""The guest response, and the language it goes out in.

ONE STORE. `final_response or suggested_response` is the outgoing reply and it
is in the GUEST'S language — that is the text that is sent, copied and posted,
and nothing else on the card is. The English box is a PROJECTION of it, held
in `response_english` so it survives a reload, and it is never sent.

This module owns the rule so there is one place that knows it. The failure it
exists to prevent already happened once in the other direction: the reply was
stored in English, the guest's language existed only as a browser variable
that did not survive a reload, and Send/Copy read the English — so an Italian
review got an English reply while a card labelled the Italian text "what the
guest receives".
"""
import hashlib


def is_english(review) -> bool:
    """Is this review English — i.e. does the card draw ONE box?

    Deferred to `language_state()` on purpose. Two functions each deciding
    "is this English?" their own way is how the card draws one box while the
    send path believes it owes a translation, and neither is visibly wrong.
    An empty or defaulted language is NOT English here; see language_state.
    """
    return language_state(review)["state"] == "english"


def _was_translated_inbound(review) -> bool:
    """Did the inbound translation actually change this review's text?

    `body_english` is written by step 1 of the pipeline ONLY when the model
    returned something other than `ENGLISH_ALREADY`. So a body_english that
    differs materially from body_original is positive evidence that the review
    was not in English — read off the two fields the spec names, not from a
    second detection path.
    """
    orig = (getattr(review, "body_original", "") or "").strip()
    eng = (getattr(review, "body_english", "") or "").strip()
    return bool(orig and eng and orig != eng)


def language_state(review) -> dict:
    """Which case this review is in, said out loud.

    - `english`: the review is in English. ONE box, no translation, and
      nothing on the card may imply one happened.
    - `translated`: the language is recorded and is not English. Two boxes —
      the outgoing one and the English projection.
    - `unknown`: we cannot say which language to reply in. NOT
      English-by-default: the card says so rather than quietly picking one.

    THE `en` HERE IS OFTEN A DEFAULT, NOT A FINDING. `slack.parse_review()`
    hard-codes `"language": "en"` on every ingested review and nothing ever
    updates it — the inbound translation writes `body_english` and leaves
    `Review.language` alone. So `language == "en"` on a review whose text was
    demonstrably translated is the column's default showing through, and
    trusting it would send an English reply to a guest who did not write in
    English, silently, which is the whole failure this module exists to stop.

    When the two disagree, the disagreement IS the answer: we know the review
    was not English and we do not know what it was.
    """
    lang = (getattr(review, "language", "") or "").strip()
    translated_in = _was_translated_inbound(review)
    if not lang:
        return {"state": "unknown", "language": "",
                "why": "no language was recorded on this review, so this reply "
                       "has not been shown to be in the guest's language"}
    if lang.upper() == "EN":
        if translated_in:
            return {"state": "unknown", "language": "",
                    "why": "this review is filed as English but its text was "
                           "translated on the way in, so it is NOT English and "
                           "no language code was recorded for it — the reply "
                           "cannot be put into the guest's language "
                           "automatically. Set the review's language, or write "
                           "the reply in it directly."}
        return {"state": "english", "language": lang,
                "why": "the review is in English, so the reply goes out as "
                       "written and there is nothing to translate"}
    return {"state": "translated", "language": lang,
            "why": f"the review is in {lang}, so the reply goes out in {lang}"}


def outgoing(draft) -> str:
    """THE text that goes to the guest. The only reader of record.

    Everything that sends, copies or posts calls this. A caller that reaches
    for `response_english` is reaching for the working view, and the working
    view is not what the guest gets.
    """
    if draft is None:
        return ""
    return ((getattr(draft, "final_response", "") or "")
            or (getattr(draft, "suggested_response", "") or "")).strip()


def digest(text: str) -> str:
    """Fingerprint of an outgoing reply, so the English projection can say
    which version of it it matches without storing the reply twice."""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def english_view(review, draft) -> dict:
    """What the English box should show, and whether it is trustworthy.

    `state` is the honest part:
      - `same`     — the review is English; this IS the outgoing text, one box.
      - `current`  — the projection matches the outgoing text it was made from.
      - `stale`    — the outgoing text has been edited directly since; this
                     English is from before that edit and must not be presented
                     as the current reply.
      - `absent`   — no English projection has been made yet.
    """
    out = outgoing(draft)
    if is_english(review):
        # One box. There is no projection, and nothing here may suggest a
        # translation happened — the card renders a single field in this case.
        return {"state": "same", "text": out, "outgoing": out,
                "why": "the review is in English, so there is one response and "
                       "no translation"}
    eng = (getattr(draft, "response_english", "") or "").strip()
    if not eng:
        return {"state": "absent", "text": "", "outgoing": out,
                "why": "no English view has been made for this reply yet"}
    of = getattr(draft, "response_english_of", "") or ""
    if of and of == digest(out):
        return {"state": "current", "text": eng, "outgoing": out,
                "why": "this English matches the response that will go out"}
    return {"state": "stale", "text": eng, "outgoing": out,
            "why": "the outgoing response was edited directly after this "
                   "English view was made, so this English is behind it — "
                   "the outgoing response is what goes to the guest"}


async def resolve_language(review) -> dict:
    """Name the guest's language from the ORIGINAL review text, and record it.

    THE ASSOCIATE SHOULD NEVER BE TYPING THIS. The guest wrote the review in
    their own language and we still have that text in `body_original`; asking
    a human to identify it is asking them to re-derive something already sitting
    in the row.

    It was being asked because the detection was gated on the wrong thing. It
    ran inside `if not review.body_english:` — i.e. only when the inbound
    translation was performed on THIS run. Every review translated before the
    detection existed, and every re-run of any review, skipped it entirely:
    `body_english` was already cached, so the branch never opened, `language`
    kept the `"en"` that `parse_review` hard-codes, and the card fell back to
    asking. Gated on the translation being fresh, when the thing that matters
    is whether the LANGUAGE is unknown.

    Returns a dict that distinguishes all four outcomes, because "did not run"
    and "ran and could not tell" lead to different next steps for the reader:

        skipped_known    the language was already recorded — nothing to do
        skipped_english  the text was never translated, so it IS English
        detected         ran, and named it (`language`)
        undetected       ran, and could not tell — the column is left alone

    `undetected` deliberately does NOT write a guess. A wrong language sends
    the guest a reply they cannot read.
    """
    lang = (getattr(review, "language", "") or "").strip()
    if lang and lang.upper() != "EN":
        return {"outcome": "skipped_known", "language": lang,
                "why": f"the review already records {lang}"}
    if not _was_translated_inbound(review):
        return {"outcome": "skipped_english", "language": lang,
                "why": "the review text was not translated on the way in, so "
                       "it is English and there is nothing to detect"}

    from server.services import claude as claude_svc
    try:
        found = (await claude_svc.detect_language(
            getattr(review, "body_original", "") or "") or "").strip()
    except Exception as e:
        _log_translation_failure(getattr(review, "id", None), "detect", e)
        found = ""
    if not found:
        return {"outcome": "undetected", "language": "",
                "why": "the review text is known NOT to be English — it was "
                       "translated on the way in — but the language could not "
                       "be named, so nothing was recorded rather than guessing "
                       "one the guest may not read"}
    review.language = found
    return {"outcome": "detected", "language": found,
            "why": f"detected from the guest's own words as {found} "
                   f"(the review was filed as \"{lang or 'blank'}\", which was "
                   f"the ingest default rather than a finding)"}


async def translate_outgoing(english: str, review, review_id: str = None) -> tuple:
    """Turn the model's English draft into the reply that goes out.

    Returns `(outgoing, english_projection, digest_or_None, trail_entry)`.

    ONE implementation of the rule, because there are two write paths for the
    reply — the full pipeline and the "↻ RCA only" regenerate endpoint — and a
    rule applied in one of them is a reply that reverts to English every time
    someone presses the other button.

    `english_projection` is EMPTY whenever the outgoing text is not a
    translation of it. An English projection stored beside an outgoing reply
    that is the same English text would draw two boxes claiming a translation
    that never happened.

    `trail_entry` is None only when a translation genuinely succeeded or was
    genuinely not needed... except that both of those are announcements too, so
    it is never None for a non-empty reply: a reader has to be able to tell an
    English reply that is correct from one that is a failed translation.
    """
    english = (english or "").strip()
    st = language_state(review)
    if not english:
        # No reply at all. Not a language failure, and saying one happened
        # would put a warning on a card whose reply was deliberately blank.
        return "", "", None, None

    if st["state"] == "english":
        return english, "", None, {
            "mark": "pass",
            "text": "<strong>Reply language</strong> — the review is in English, "
                    "so the reply goes out as drafted and nothing was translated."}

    if st["state"] == "unknown":
        return english, "", None, {
            "mark": "warn",
            "text": "<strong>Reply language</strong> — no language is recorded on "
                    "this review, so the reply was left in English without "
                    "anything establishing that is the guest's language."}

    lang = st["language"]
    from server.services import claude as claude_svc
    try:
        out = (await claude_svc.translate_to(english, lang, review_id) or "").strip()
    except Exception as e:
        out = ""
        _log_translation_failure(review_id, lang, e)
    if out:
        return out, english, digest(out), {
            "mark": "pass",
            "text": f"<strong>Reply language</strong> — drafted in English and "
                    f"translated to {lang}; the {lang} text is what goes to the "
                    f"guest."}
    # The outgoing reply is ENGLISH on a review that is not. The projection is
    # left EMPTY so the card cannot show an English box implying the reply
    # above it is a translation of it.
    return english, "", None, {
        "mark": "warn",
        "text": f"<strong>Reply language</strong> — the review is in {lang} but the "
                f"translation returned nothing, so the drafted reply is still "
                f"ENGLISH. It has not been changed to look translated. Edit it in "
                f"{lang} before sending, or use the English box to retry."}


def _log_translation_failure(review_id, lang, e) -> None:
    import logging
    logging.getLogger(__name__).warning(
        "[reply-language] %s: translation to %s failed: %s", review_id, lang, e)


def set_english_projection(draft, english: str, translated: str) -> None:
    """Record a successful English→outgoing translation.

    Writes the outgoing text FIRST-CLASS (`final_response`, the human-edit
    field) and the English as its projection, stamped with the digest of the
    text it produced. Both or neither — a projection stamped against a reply
    that was not written is exactly the divergence this module exists to stop.
    """
    draft.final_response      = translated
    draft.response_english    = english
    draft.response_english_of = digest(translated)
