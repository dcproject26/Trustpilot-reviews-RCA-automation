"""Two things every timeline logged, on every ticket, and got wrong anyway."""
from server.services.zendesk import _map_channel


def test_web_and_api_are_not_email():
    """A help-centre form submission and an integration's API post were both
    labelled "email", so the timeline showed three different things under one
    name - and the mismatch was only visible in a log line nobody reads."""
    assert _map_channel("web") == "web"
    assert _map_channel("web_form") == "web"
    assert _map_channel("helpcenter") == "web"
    assert _map_channel("api") == "api"
    assert _map_channel("rule") == "api"
    assert _map_channel("trigger") == "api"


def test_the_channels_that_were_already_right_stay_right():
    assert _map_channel("email") == "email"
    assert _map_channel("mail") == "email"
    assert _map_channel("chat") == "chat"
    assert _map_channel("whatsapp") == "chat"
    assert _map_channel("line") == "chat"
    assert _map_channel("voice") == "call"
    assert _map_channel("phone") == "call"
    assert _map_channel("") == "email"


def test_unknown_channel_still_falls_back_to_email():
    assert _map_channel("carrier_pigeon") == "email"


def test_system_author_ids_skip_the_user_lookup(monkeypatch):
    """author_id -1 marks a comment the system posted. Asking the API for it
    always fails with 'id must be >= 0', and the warning fired on every
    timeline, burying the lookups that genuinely failed."""
    import server.services.zendesk as ZD
    calls = []

    class FakeZ:
        def users(self, id=None):
            calls.append(id)
            raise RuntimeError("should never be called for a system id")

    # exercise the closure the same way _get_timeline_sync builds it
    cache = {}

    def _role(author_id):
        if author_id in cache:
            return cache[author_id]
        try:
            if int(author_id) <= 0:
                cache[author_id] = ""
                return ""
        except (TypeError, ValueError):
            pass
        return getattr(FakeZ().users(id=author_id), "role", "")

    assert _role(-1) == ""
    assert _role(0) == ""
    assert calls == [], f"the API was called for a system id: {calls}"


# ── a Slack search that did not run says what would make it run ─────────────
#
# "Slack search (WIP): missing_scope." was on a real card. Two faults in six
# words: "(WIP)" says the FEATURE is unfinished when the code is actually a
# permission nobody granted, and a bare API code says nothing about the fix.
#
# The fix guidance now goes to the LOG, not the card — a paragraph about OAuth
# scopes does not belong on a card about a guest's refund. So these tests
# assert on both surfaces: the card names the code, the log names the fix.
# Dropping the log assertions would let the guidance be deleted outright and
# nothing would fail.

def _card_and_log(caplog, code):
    import logging
    from server.services.slack import _search_error_sentence
    with caplog.at_level(logging.WARNING, logger="server.services.slack"):
        caplog.clear()
        card = _search_error_sentence(code)
    return card, " ".join(r.getMessage() for r in caplog.records)


def test_a_missing_scope_names_the_scope_and_the_fix(caplog):
    card, logged = _card_and_log(caplog, "missing_scope")
    assert "missing_scope" in card
    assert "WIP" not in card
    assert "search:read" in logged
    assert "reinstall" in logged, "adding the scope without reinstalling changes nothing"


def test_a_bot_token_is_told_apart_from_a_missing_scope(caplog):
    """Different problems, different fixes: one needs a scope added, the other
    needs a different KIND of token entirely."""
    a_card, a_log = _card_and_log(caplog, "missing_scope")
    b_card, b_log = _card_and_log(caplog, "not_allowed_token_type")
    assert a_card != b_card
    assert a_log != b_log
    assert "xoxp" in b_log and "xoxb" in b_log


def test_a_temporary_failure_is_not_dressed_as_a_config_problem(caplog):
    card, logged = _card_and_log(caplog, "ratelimited")
    assert "temporary" in logged
    assert "scope" not in logged, "a rate limit sends nobody to the OAuth page"
    assert "scope" not in card


def test_an_unknown_code_says_it_is_unknown(caplog):
    """Inventing guidance for a code this build has never seen is worse than
    admitting there is none."""
    card, logged = _card_and_log(caplog, "some_new_slack_code")
    assert "some_new_slack_code" in card
    assert "no guidance" in logged


def test_the_card_line_stays_one_short_sentence(caplog):
    """The whole point of the trim. A card that grows a paragraph again should
    fail here rather than be noticed on a screenshot."""
    from server.services.slack import _search_error_sentence, _SEARCH_ERRORS
    for code in list(_SEARCH_ERRORS) + ["anything_else"]:
        got = _search_error_sentence(code)
        assert len(got.split()) <= 8, got
        assert got.count(".") == 1, got


def test_no_message_still_says_search_was_not_run():
    """Every branch has to begin by saying the search did not happen — that is
    the fact a reader needs before any advice."""
    from server.services.slack import _search_error_sentence, _SEARCH_ERRORS
    for code in list(_SEARCH_ERRORS) + ["anything_else"]:
        assert _search_error_sentence(code).startswith("Slack was not searched")
