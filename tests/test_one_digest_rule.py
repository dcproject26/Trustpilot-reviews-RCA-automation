"""One rule for "is this a digest or a person's name", and one corpus.

THREE COPIES EXISTED AND THEY DISAGREED, live, in opposite directions:

    value                              api      matcher/warehouse
    FjpJxbSfpb65bnyQwErTyUiOpAsDfGhJ   name     digest      <- a real digest
    ab24TSVenneb4T3CkHFUFaGM           name     digest      <- a real digest
    Papadopoulopoulos                  name     digest      <- a real NAME

`api._looks_like_hash` required one of `+ / = _` before calling a non-hex
string a digest, so a plain ALPHANUMERIC digest walked through it — and
`_scrub_candidate_names` uses that same predicate, so the digest reached the
candidate picker, the one field an associate recognises the right booking by.
`0077864` ("The picker showed a digest where the name goes") is the commit
that claims to have fixed exactly that. Its test only ever used base64 and hex
fixtures, so it could not see the gap.

The first value above is `a8b6a10`'s OWN fixture for "a PII hash". The second
is the digest `bigquery.py` records as having matched a guest called Sven and
returned a Barcelona walking tour for a review about a musical.

NO TEST IN THE REPO IMPORTED TWO OF THE THREE PREDICATES. Nothing held them
together, which is how they drifted. That is what this file is for: one corpus,
every implementation, driven.
"""
import pytest

from server.api import _looks_like_hash, _scrub_candidate_names
from server.names import looks_like_digest
from server.pipeline import _is_hashed_name
from server.services.bigquery import is_hashed_name

# Every implementation of the rule that is reachable from anywhere.
PREDICATES = {"names": looks_like_digest, "api": _looks_like_hash,
              "pipeline": _is_hashed_name, "bigquery": is_hashed_name}

DIGESTS = [
    "FjpJxbSfpb65bnyQwErTyUiOpAsDfGhJ",              # a8b6a10's own fixture
    "ab24TSVenneb4T3CkHFUFaGM",                      # the Sven false match
    "jVwe+fjfm48WSok1xEK+I/8fnIoV+kY8P8z7xxk+NM8=",  # base64 with padding
    "aGVsbG8gd29ybGQgdGhpcw==".replace(" ", ""),
    "deadbeefcafebabe1234",                          # hex
    "a3f9c2d1b4e5f6a7b8c9d0e1",                       # hex
]

NAMES = [
    "Papadopoulopoulos",        # 17 chars, unspaced, and a real surname
    "Ramanujanathanswamy",
    "Christopherson",
    "McDonald",
    "O'BrienSmithVanDerBerg",   # an apostrophe is not in any encoding alphabet
    "Wolodarsky-Rosenbaum",
    "Bénédicte Depois",
    "Gianmarco Lucia",
    "Sven",
    "",
]


@pytest.mark.parametrize("value", DIGESTS)
@pytest.mark.parametrize("which", sorted(PREDICATES))
def test_every_implementation_calls_a_digest_a_digest(which, value):
    assert PREDICATES[which](value) is True, \
        f"{which} would show {value!r} as a guest's name"


@pytest.mark.parametrize("value", NAMES)
@pytest.mark.parametrize("which", sorted(PREDICATES))
def test_every_implementation_leaves_a_real_name_alone(which, value):
    """The inverse error puts an EMPTY field on the picker an associate is
    using to choose between bookings, which is worse than a digest."""
    assert PREDICATES[which](value) is False, \
        f"{which} would blank the real name {value!r}"


@pytest.mark.parametrize("value", DIGESTS + NAMES)
def test_the_implementations_cannot_drift_apart(value):
    """The guarantee that did not exist. Any one of these changing alone is
    the failure — not any particular verdict."""
    got = {name: fn(value) for name, fn in PREDICATES.items()}
    assert len(set(got.values())) == 1, f"{value!r}: {got}"


# ── the live regression ────────────────────────────────────────────────────

@pytest.mark.parametrize("value", DIGESTS)
def test_the_candidate_picker_shows_no_digest(value):
    """a8b6a10 removed the Primary guest ROW from the card. The digest kept
    flowing to the picker, which is a different screen and was never fixed."""
    out = _scrub_candidate_names([{"primary_guest_name": value}])
    assert out[0].get("primary_guest_name") == "", out[0]


def test_a_real_name_still_reaches_the_picker():
    """Paired with the above so the scrub cannot be made unconditional — a
    picker that blanks every name is not a fixed picker."""
    out = _scrub_candidate_names([{"primary_guest_name": "Gianmarco Lucia"}])
    assert out[0].get("primary_guest_name") == "Gianmarco Lucia", out[0]


# ── the shape of the rule ──────────────────────────────────────────────────

def test_a_short_token_is_never_a_digest():
    """Truncating the length test would blank ordinary surnames wholesale."""
    assert looks_like_digest("abc123") is False
    assert looks_like_digest("a1b2c3d4e5f6g7h") is False   # 15
    assert looks_like_digest("a1b2c3d4e5f6g7h8") is True   # 16


def test_a_spaced_value_is_a_name_however_long():
    assert looks_like_digest("Maria de los Angeles Fernandez Rodriguez") is False


def test_a_digit_gives_a_letters_and_digits_token_away():
    """No surname carries a digit, and that is what catches the alphanumeric
    digests the api predicate was letting through."""
    assert looks_like_digest("Papadopoulopoulos") is False
    assert looks_like_digest("Papadopoulopoulo5") is True


@pytest.mark.parametrize("name", ["VanDerBergVanHouten", "JeanPierreDeLaCroix",
                                  "MariaDeLosAngelesRodriguez",
                                  "McDonaldSonSmithers"])
def test_a_camel_cased_name_is_not_a_digest(name):
    """THE CLAUSE THAT WAS WRITTEN AND MEASURED AWAY, pinned so it does not
    come back. "Four or more case flips" reads as a clean way to catch a
    letters-only base64 digest, and it is not: these live-shaped names flip
    seven to nine times, while sampling 200,000 digests produced eight that
    were letters-only at all — 0.004%. Blanking these to catch four digests in
    a hundred thousand is the wrong trade, and the empty field lands on the
    screen an associate picks a booking from."""
    assert looks_like_digest(name) is False


def test_a_letters_only_digest_is_the_accepted_gap():
    """Stated rather than hidden. It reads as a name, fails the comparison it
    is then allowed into — which is what every digest did before any of this
    existed — and is 0.004% of digests."""
    assert looks_like_digest("aVusEdlMtmoVdRMvmrOENBdJCqQHwwmf") is False


def test_the_sql_copy_of_this_rule_has_not_come_back():
    """NEGATIVE source assertion — a shape unreachability cannot defeat, and
    the reason for it is the point.

    `NOT_A_HASH_SQL` held the rule a FOURTH time, in SQL, and was interpolated
    into no query: defined, and referenced by nothing. A guard shaped like
    protection and wired into no path is the first bullet of CLAUDE.md, and
    keeping it would have meant keeping a fourth copy in step with the other
    three for no running code's benefit."""
    import inspect

    from server.services import bigquery
    src = inspect.getsource(bigquery)
    assert "NOT_A_HASH_SQL =" not in src, \
        "the unused SQL copy of the digest rule is back"
