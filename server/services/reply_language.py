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


# THE INGEST DEFAULT, NOT A FINDING. `slack.parse_review()` stamped `"en"` on
# every review and nothing ever updated it, so this exact string means "nobody
# has looked" — it is the one value that must never be read as English. Ingest
# now writes None; `"en"` is kept here because live rows still carry it.
#
# A DETECTED value is a NAME. `claude.detect_language()` answers "English",
# "Italian", "Spanish" — so a name present in this column is a name somebody
# established, and the two-letter code is the legacy default. That is the whole
# distinction, and it is why nothing may write `"en"` back into this column.
_NEVER_DETECTED = {"en", "eng", "auto", "unknown", "und", "n/a"}

# What the detector says when the guest wrote in English. One box, correctly.
_MEANS_ENGLISH = {"english"}


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

    # THE INGEST DEFAULT, DECIDED BY THE ONE SIGNAL WE HAVE FOR FREE.
    #
    # `"en"` and a blank both mean "nobody named the language" — but we do not
    # have to leave it there, and we must not turn EVERY such review into two
    # boxes: that made every genuinely English review (all of them carry the
    # `"en"` default from ingest) draw a translate panel it did not need, which
    # is the "responses are broken" a reader sees.
    #
    # `body_english` is written by the inbound step ONLY when it actually
    # translated something — it answers ENGLISH_ALREADY for English and writes
    # nothing. So body_english differing from body_original is positive, free
    # evidence the review was NOT English, and body_english empty/equal is
    # positive evidence it WAS. No model call, and it is right for every review
    # except one whose inbound translation crashed (body_english empty on a
    # non-English review) — which the detector then corrects on the next run.
    if not lang or lang.lower() in _NEVER_DETECTED:
        if _was_translated_inbound(review):
            # Translated on the way in, so not English, and the code did not
            # name what it is — a Spanish review filed as "en" is exactly this.
            # Two boxes, and the card resolves the language from body_original.
            return {"state": "unknown", "language": "",
                    "why": "this review was translated into English on the way "
                           "in, so it is NOT English, and no language was "
                           "recorded for it. The reply is kept in both boxes; "
                           "the card names the guest's language from their own "
                           "words so the reply can go out in it"}
        # Nothing was translated inbound, so the review IS English. One box.
        return {"state": "english", "language": "English",
                "why": "the review was not translated on the way in, so it is "
                       "in English and the reply goes out as written"}
    if lang.lower() in _MEANS_ENGLISH:
        if _was_translated_inbound(review):
            # A CONTRADICTION, RESOLVED TOWARDS TWO BOXES. The column says
            # English and the review's own text says otherwise: `body_english`
            # exists and DIFFERS from `body_original`, which only happens when
            # the inbound model actually translated something — it answers
            # ENGLISH_ALREADY for English and writes nothing.
            #
            # Detection is a model call and can be wrong. This is the record of
            # a translation that demonstrably happened, so it wins. The cost of
            # believing it wrongly is one unnecessary box; the cost of
            # believing the column wrongly is an English reply to a guest who
            # did not write in English.
            return {"state": "unknown", "language": "",
                    "why": "this review is recorded as English, but its text "
                           "was translated on the way in — so it is not "
                           "English and the recorded language cannot be "
                           "trusted. Both boxes are kept; name the guest's "
                           "language, or write the top box in it directly"}
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

    Returns a dict that keeps every outcome distinct, because "did not run"
    and "ran and could not tell" lead to different next steps for the reader:

        skipped_known    the language was already recorded — nothing to do
        detected         ran, and named it (`language`)
        undetected       ran, read the review, and could not name it
        unavailable      COULD NOT RUN — no Anthropic connection, or the
                         review has no stored text to read
        failed           the call raised

    `skipped_english` USED TO BE A FIFTH, and it was the bug: it concluded
    "this review is English" from `body_english` being empty, which is also
    what a crashed translate call leaves behind. It is gone. Nothing here
    reports English except the detector saying so.

    Only `detected` writes to the column. The other four leave it alone, and
    an unestablished language draws TWO response boxes — never one — so a
    guest who did not write in English cannot receive an English-only reply
    because a lookup was switched off.
    """
    lang = (getattr(review, "language", "") or "").strip()
    if lang and lang.lower() not in _NEVER_DETECTED:
        return {"outcome": "skipped_known", "language": lang,
                "why": f"the review already records {lang}"}

    # THE SHORTCUT THAT STOOD HERE CONCLUDED "ENGLISH" FROM A FAILED LOOKUP.
    # It was:
    #
    #     if not _was_translated_inbound(review):
    #         return {"outcome": "skipped_english", ...
    #                 "why": "the review text was not translated on the way
    #                         in, so it is English and there is nothing to
    #                         detect"}
    #
    # `_was_translated_inbound` is `body_original != body_english`, and
    # `body_english` is EMPTY in three different situations that the card
    # cannot tell apart: the model answered ENGLISH_ALREADY (genuinely
    # English), the translate call raised (pipeline.py catches and logs it),
    # or it returned nothing. Two of those three are failures, and all three
    # took this branch and were announced as "it is English".
    #
    # That is the first rule of CLAUDE.md with a screen on the end of it: an
    # Italian review whose translation failed drew one English box, offered no
    # way to reach the guest's language, and sent English to a guest who did
    # not write in English.
    #
    # So the detector runs whenever the language is unestablished, full stop.
    # It reads `body_original`, which is always there, and answers "English"
    # for an English review — one box, on evidence, rather than by default.
    #
    # THE TWO REASONS IT CANNOT EVEN RUN ARE CHECKED HERE, not inferred from
    # its answer. `claude.detect_language()` returns "" for FOUR different
    # situations — no Anthropic key, no text, the model said UNKNOWN, the model
    # returned junk — and a caller reading only "" cannot tell "the detector is
    # switched off on this server" from "it read the review and could not say".
    # Those need different sentences on the card: one is a deployment problem
    # an associate can escalate, the other is this review being genuinely hard.
    body = (getattr(review, "body_original", "") or "").strip()
    if not body:
        return {"outcome": "unavailable", "language": "",
                "why": "this review has no original text stored, so there is "
                       "nothing to read the language from. The reply keeps "
                       "both boxes"}

    from server.services import claude as claude_svc
    try:
        found = (await claude_svc.detect_language(body) or "").strip()
    except Exception as e:
        _log_translation_failure(getattr(review, "id", None), "detect", e)
        return {"outcome": "failed", "language": "",
                "why": f"the language check itself failed ({e}), so nothing "
                       f"was recorded. The reply keeps both boxes — this is a "
                       f"broken lookup, not a review that is English"}
    if not found:
        # NOT "it is English", and the two reasons for "" are told apart HERE
        # rather than gating the call above — a gate would sit in front of the
        # detector and could not be driven by a test that stubs it.
        #
        # `claude.detect_language()` answers "" for a switched-off Anthropic
        # AND for a review it read and could not place, which are different
        # problems with different next steps: one is a deployment fault to
        # escalate, the other is this review being genuinely hard. `is_live` is
        # consulted only to EXPLAIN the empty answer.
        from server.config import is_live
        if not is_live("anthropic"):
            return {"outcome": "unavailable", "language": "",
                    "why": "the language detector is not connected on this "
                           "server, so nothing was read — this is not a review "
                           "whose language is hard to place. The reply keeps "
                           "both boxes; write the guest's language in the top "
                           "box directly, or have Anthropic connected"}
        return {"outcome": "undetected", "language": "",
                "why": "the language could not be named from the review text, "
                       "so nothing was recorded rather than guessing one the "
                       "guest may not read — the reply keeps both boxes until "
                       "it is established"}
    if found.lower() in _NEVER_DETECTED:
        # THE INVARIANT THIS FILE STATES, NOW ENFORCED. The comment on
        # `_NEVER_DETECTED` says nothing may write `"en"` back into this
        # column, and nothing stopped it: `detect_language` filters UNKNOWN,
        # blanks, over-long answers and anything containing a space, but a
        # bare two-letter code passes every one of those.
        #
        # Storing it is worse than storing nothing. The outcome says
        # `detected`, the column looks filled, and `language_state` then reads
        # it straight back as UNESTABLISHED — so the card fires the language
        # check again on the next render, and the next, spending a model call
        # each time and never settling. A loop that reports success.
        _log_translation_failure(getattr(review, "id", None), "detect",
                                 f"answered {found!r}, which is a code rather "
                                 f"than a language name")
        return {"outcome": "undetected", "language": "",
                "why": f"the detector answered {found!r} — a language code, "
                       f"not a name — and a code cannot be told apart from "
                       f"the ingest default, so nothing was recorded. The "
                       f"reply keeps both boxes"}
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
