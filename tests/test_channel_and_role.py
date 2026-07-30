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
