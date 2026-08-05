"""A review posted as "customer" carries no name, and that is not a weak name.

Trustpilot lets a reviewer post under a placeholder. The pipeline searched
Zendesk for "customer" exactly as it would search for "Bhayani": the search
returned half the desk, Zendesk truncated it, the truncation warning printed
four times, and the card offered three bookings ranked on visit-date proximity
alone with "No venue agreement — treat as weak".

Every one of those steps behaved correctly. The input was the problem: a query
that identifies nobody produced candidates that look like a near-miss, and an
associate works through three bookings that were never evidence of anything.

A placeholder is the ABSENCE of an identifier, not a weak one, and the two
have to end differently — absent is untraceable, weak is confirm-before-use.
"""
import pytest

from server.names import is_placeholder, parse_author, search_tokens


@pytest.mark.parametrize("name", [
    "customer", "Customer", "CUSTOMER", "guest", "Guest", "anonymous",
    "Anonymous", "a customer", "an anonymous guest", "user", "traveller",
    "Trustpilot user", "n/a", "unknown", "Mr Customer",
])
def test_a_placeholder_is_recognised(name):
    assert is_placeholder(name) is True, f"{name!r} would be searched as a name"


@pytest.mark.parametrize("name", [
    "Thompson", "THOMPSON", "Bhayani Salim F", "A Cariello",
    "Customer Cariello",           # one real token is enough to search on
    "Guest Hernandez",
])
def test_a_real_name_is_not_a_placeholder(name):
    assert is_placeholder(name) is False, (
        f"{name!r} reads as a placeholder — a false positive here sends a "
        f"traceable review to untraceable")


def test_an_empty_name_is_not_a_placeholder():
    """Different facts. Empty is "no name was given"; placeholder is "a name
    was given and it identifies nobody". Merging them would report one as the
    other in the trail, which is the whole failure this file is about."""
    assert is_placeholder("") is False
    assert is_placeholder(None) is False
    assert is_placeholder("   ") is False


def test_the_placeholder_still_parses_as_a_name():
    """The guard is deliberately SEPARATE from parsing. parse_author has no
    opinion about meaning, and changing it would alter scoring everywhere.
    Anything relying on the old parse keeps working."""
    assert parse_author("customer") == ("customer", None)


def test_a_partly_placeholder_name_keeps_its_real_tokens():
    """"Customer Cariello" is searchable on Cariello. Discarding the whole
    name because one token is generic loses a real identifier."""
    assert "Cariello" in search_tokens("Customer Cariello")


def test_case_and_punctuation_do_not_defeat_it():
    assert is_placeholder("Customer.") is True
    assert is_placeholder("(guest)") is True


def test_the_list_holds_no_token_that_would_swallow_a_real_name():
    """A guard on the guard. One over-broad entry — a common given name —
    would route real reviews to untraceable wholesale, and the symptom would
    be indistinguishable from a matching failure."""
    from server.names import _PLACEHOLDER
    for common in ("john", "maria", "li", "ana", "mohammed", "sarah", "raj"):
        assert common not in _PLACEHOLDER, (
            f"{common!r} is in the placeholder list — every review by someone "
            f"with that name would go untraceable")
