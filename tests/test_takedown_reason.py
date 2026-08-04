"""Why we are asking for the review to come down.

The verdict select has always been there; the ground for it has not. A
takedown request with no recorded reason is one nobody downstream can check,
and "Yes" on its own is the same amount of information as no answer.

Three things this has to get right, and each is a way the control could look
finished and not be:

  * it appears on Yes and not otherwise — an empty reason under a No reads as
    a question nobody answered, when it does not apply;
  * the option VALUE is the stable key, not the display text, so renaming a
    ground in the copy file does not orphan a verdict already recorded;
  * changing the verdict must not silently discard the reason, which the old
    handler did by replacing the whole takedown object.

The options come from content/orm_macros.yaml through /api/taxonomy. The
server half is driven directly; the control is driven in a browser, because a
select that renders and saves nothing is the failure this suite exists for.
"""
import pytest


# ── the copy file is the source ────────────────────────────────────────────

def test_the_reasons_come_from_the_copy_file():
    from server.prompts import TAKEDOWN_REASONS
    assert TAKEDOWN_REASONS, "no takedown grounds at all — the control has nothing to show"
    for r in TAKEDOWN_REASONS:
        assert r["key"] and r["text"], r


def test_every_reason_carries_a_hint():
    """Two adjacent grounds with no explanation get picked at random."""
    from server.prompts import TAKEDOWN_REASONS
    for r in TAKEDOWN_REASONS:
        assert r["when"], f"{r['key']} has no hint saying when it applies"


def test_the_keys_are_stable_identifiers_not_prose():
    """A draft stores the key. If the key were the display text, editing the
    wording in the copy file would orphan every verdict already recorded."""
    from server.prompts import TAKEDOWN_REASONS
    for r in TAKEDOWN_REASONS:
        assert " " not in r["key"], r["key"]
        assert r["key"] == r["key"].lower(), r["key"]


def test_a_copy_file_with_no_reasons_block_still_yields_grounds():
    """Someone deleting the block while editing must not leave the control
    with nothing to offer — that renders as a takedown with no way to say
    why, which is where this started."""
    from server.prompts import _FALLBACK
    assert _FALLBACK["takedown"]["reasons"], \
        "the fallback has no grounds, so a broken copy file empties the control"


def test_the_api_serves_them():
    from fastapi.testclient import TestClient
    from server.main import app
    got = TestClient(app).get("/api/taxonomy").json()
    assert got.get("takedown_reasons"), \
        "the dashboard has no source for the grounds"
    assert {"key", "text", "when"} <= set(got["takedown_reasons"][0])


def test_the_client_does_not_hardcode_the_list():
    """A second copy of the list in the client is a second place to edit, and
    the one that will be missed. Negative assertion — a string that appears
    nowhere cannot be absent for the wrong reason."""
    from server.prompts import TAKEDOWN_REASONS
    client = open("client/index.html", encoding="utf-8").read()
    for r in TAKEDOWN_REASONS:
        assert f'"{r["when"]}"' not in client, \
            f"the hint for {r['key']} is hardcoded in the client"


# ── the control ────────────────────────────────────────────────────────────

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _set_verdict(page, value):
    page.select_option("[data-takedown-rec]", value)
    page.wait_for_timeout(900)


def _reason_select(page):
    return page.locator("[data-takedown-reason]")


def test_no_reason_control_on_a_no(page):
    try:
        _set_verdict(page, "No")
        assert _reason_select(page).count() == 0, \
            "a reason is being asked for on a takedown we are not requesting"
    finally:
        _set_verdict(page, "No")


def test_the_reason_control_appears_on_a_yes(page):
    """It has to appear on the same interaction. Saving Yes and showing no way
    to say why until the next load is a control nobody finds."""
    try:
        _set_verdict(page, "Yes")
        assert _reason_select(page).count() == 1, \
            "picking Yes offers no way to record the ground"
    finally:
        _set_verdict(page, "No")


def test_it_offers_every_ground_the_copy_file_defines(page):
    from server.prompts import TAKEDOWN_REASONS
    try:
        _set_verdict(page, "Yes")
        values = page.evaluate(
            "() => [...document.querySelectorAll('[data-takedown-reason] option')]"
            ".map(o => o.value).filter(Boolean)")
        assert values == [r["key"] for r in TAKEDOWN_REASONS], values
    finally:
        _set_verdict(page, "No")


def test_the_option_value_is_the_key_and_the_label_is_the_text(page):
    from server.prompts import TAKEDOWN_REASONS
    try:
        _set_verdict(page, "Yes")
        pairs = page.evaluate(
            "() => [...document.querySelectorAll('[data-takedown-reason] option')]"
            ".map(o => [o.value, o.textContent.trim()]).filter(p => p[0])")
        assert pairs == [[r["key"], r["text"]] for r in TAKEDOWN_REASONS], pairs
    finally:
        _set_verdict(page, "No")


def test_it_starts_unset_rather_than_defaulting_to_the_first_ground(page):
    """A pre-selected ground is one nobody chose, and it would be recorded as
    though somebody had."""
    try:
        _set_verdict(page, "Yes")
        assert _reason_select(page).input_value() == "", \
            "a ground was pre-selected for the associate"
    finally:
        _set_verdict(page, "No")


def test_picking_a_ground_saves_and_survives_a_reload(page):
    from server.prompts import TAKEDOWN_REASONS
    key = TAKEDOWN_REASONS[0]["key"]
    try:
        _set_verdict(page, "Yes")
        page.select_option("[data-takedown-reason]", key)
        page.wait_for_timeout(1200)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(900)
        page.locator(".review-item").first.click()
        page.wait_for_timeout(1500)
        assert _reason_select(page).input_value() == key, \
            "the ground was accepted and is not there on reload"
    finally:
        _set_verdict(page, "No")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(900)
        page.locator(".review-item").first.click()
        page.wait_for_timeout(1500)


def test_the_hint_follows_the_chosen_ground(page):
    from server.prompts import TAKEDOWN_REASONS
    r = TAKEDOWN_REASONS[0]
    try:
        _set_verdict(page, "Yes")
        page.select_option("[data-takedown-reason]", r["key"])
        page.wait_for_timeout(900)
        assert r["when"] in page.locator(".takedown-reason-hint").inner_text()
    finally:
        _set_verdict(page, "No")


def test_switching_the_verdict_away_and_back_does_not_keep_a_stale_ground(page):
    """A ground recorded for a takedown we then decided against must not
    survive as though it were still the reason."""
    from server.prompts import TAKEDOWN_REASONS
    key = TAKEDOWN_REASONS[0]["key"]
    try:
        _set_verdict(page, "Yes")
        page.select_option("[data-takedown-reason]", key)
        page.wait_for_timeout(1000)
        _set_verdict(page, "No")
        stored = page.evaluate(
            "() => (REVIEWS.find(x => x.id === state.selected)"
            ".rca.v3.takedown || {}).reason || null")
        assert stored is None, f"a ground survived the verdict changing: {stored}"
    finally:
        _set_verdict(page, "No")


def test_the_verdict_change_does_not_wipe_the_rest_of_the_object(page):
    """The handler used to replace the whole takedown object with
    {verdict}. Harmless while verdict was the only key."""
    try:
        page.evaluate("""() => {
          const r = REVIEWS.find(x => x.id === state.selected);
          r.rca.v3.takedown = {...(r.rca.v3.takedown || {}), note: 'keep me'}; }""")
        _set_verdict(page, "Yes")
        kept = page.evaluate(
            "() => (REVIEWS.find(x => x.id === state.selected)"
            ".rca.v3.takedown || {}).note || null")
        assert kept == "keep me", "the verdict handler discarded the rest of the object"
    finally:
        _set_verdict(page, "No")


def test_the_page_is_still_healthy(page):
    assert page.errors == [], page.errors
