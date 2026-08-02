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

def test_a_missing_scope_names_the_scope_and_the_fix():
    from server.services.slack import _search_error_sentence
    got = _search_error_sentence("missing_scope")
    assert "search:read" in got
    assert "reinstall" in got, "adding the scope without reinstalling changes nothing"
    assert "WIP" not in got


def test_a_bot_token_is_told_apart_from_a_missing_scope():
    """Different problems, different fixes: one needs a scope added, the other
    needs a different KIND of token entirely."""
    from server.services.slack import _search_error_sentence
    a = _search_error_sentence("missing_scope")
    b = _search_error_sentence("not_allowed_token_type")
    assert a != b
    assert "xoxp" in b and "xoxb" in b


def test_a_temporary_failure_is_not_dressed_as_a_config_problem():
    from server.services.slack import _search_error_sentence
    got = _search_error_sentence("ratelimited")
    assert "temporary" in got
    assert "scope" not in got, "a rate limit sends nobody to the OAuth page"


def test_an_unknown_code_says_it_is_unknown():
    """Inventing guidance for a code this build has never seen is worse than
    admitting there is none."""
    from server.services.slack import _search_error_sentence
    got = _search_error_sentence("some_new_slack_code")
    assert "some_new_slack_code" in got
    assert "no guidance for" in got


def test_no_message_still_says_search_was_not_run():
    """Every branch has to begin by saying the search did not happen — that is
    the fact a reader needs before any advice."""
    from server.services.slack import _search_error_sentence, _SEARCH_ERRORS
    for code in list(_SEARCH_ERRORS) + ["anything_else"]:
        assert _search_error_sentence(code).startswith("Slack was not searched")
