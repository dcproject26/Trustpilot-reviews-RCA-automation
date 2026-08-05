"""Structural guarantees for the RCA column that a browser check cannot pin.

Behaviour is verified by driving the real thing — `tools/check_rca_ui.py`
clicks every add, delete and chip-select in Chromium and reports what happened.
These are the assertions that survive being written as text: NEGATIVE ones,
where "this string appears nowhere" cannot be defeated by an unreachable
branch, plus the closed vocabularies, whose whole point is that no other value
can be written.
"""
import re

CLIENT = open("client/index.html", encoding="utf-8").read()


# ── the card-level blocks are gone (§2) ─────────────────────────────────────

def test_the_card_level_analysis_blocks_are_not_rendered_again():
    """Operational failure / SOP gap / Pattern / Fixes rendered four slightly
    different ways, detached from the issue they explained. They live on the
    issue now — a card-level block reappearing means two homes for one field."""
    for gone in ("<span class=\"wwr-blk-label\">Fixes</span>",
                 "<span class=\"wwr-blk-label\">Operational failure</span>",
                 "<span class=\"wwr-blk-label\">SOP gap</span>"):
        assert gone not in CLIENT, f"a card-level block is back: {gone}"


def test_legacy_card_level_content_is_still_rendered_somewhere():
    """Removing the blocks must not delete analysis a previous draft holds.
    It moves under the last issue, at its own v3 path so edits still save.

    The guard is asserted, not just the strings: a first version of this test
    checked only that the paths appeared in the file, and passed against a
    build where the whole block sat behind `false ?`. The rendering itself is
    checked by driving the page — tools/check_rca_ui.py.
    """
    assert "From an earlier draft — not yet attached to an issue" in CLIENT
    assert "what_went_wrong.what_happened.operational_failure" in CLIENT
    assert "what_went_wrong.fixes.actions" in CLIENT
    assert "const legacyExtras = (_legacyRows.length || _spLegacy) ? `" in CLIENT, \
        "the legacy block is behind a guard that can never be true"
    assert "${legacyExtras}" in CLIENT, "it is built but never placed in the section"


# ── closed vocabularies (§1, §9, §11) ───────────────────────────────────────

def _client_list(name):
    """The values of a `const NAME = [...]` array in the client."""
    import re
    m = re.search(rf"const {name}\s*=\s*\[(.*?)\]", CLIENT, re.S)
    assert m, f"the client has no {name} list"
    return re.findall(r"'([^']*)'", m.group(1))


def test_the_verdict_and_owner_are_selects_not_free_text():
    """A chip that renders whatever the model returns is how a sentence-length
    verdict crushed the issue title to one character per line. A select can
    only render its options.

    This used to assert the exact array literal, which is a spelling check: it
    broke the moment a fifth verdict was added legitimately, and it never
    checked the thing that matters. What matters is that the client's list is
    CLOSED and matches the server's — a verdict the validator can produce and
    the select cannot render is a value that silently becomes something else
    on screen.
    """
    from server.services.rca_v4_validate import CLAIM_ACCURACY, OWNERS
    assert _client_list("ACC") == list(CLAIM_ACCURACY), (
        f"the card offers {_client_list('ACC')} and the validator produces "
        f"{list(CLAIM_ACCURACY)} — a verdict one side knows and the other "
        f"does not is a value that changes meaning on the way to the screen")
    assert _client_list("OWNS") == list(OWNERS)
    assert "data-v3sel" in CLIENT, "the chip-selects have no save path"


def test_the_raw_verdict_is_kept_in_the_title_not_the_chip():
    i = CLIENT.find("const chipSel =")
    assert 0 < i and 'title="${esc(raw || v)}"' in CLIENT[i:i + 600]


def test_the_accuracy_and_owner_chips_cap_rather_than_fix_their_width():
    """Both size to content and clamp only on garbage, so a long team name is
    readable and a sentence cannot claim the row."""
    assert re.search(r"\.chip-acc-sel\s*\{\s*max-width:\s*140px", CLIENT)
    assert re.search(r"\.chip-owner-sel\s*\{\s*max-width:\s*26ch", CLIENT)


# ── evidence rows (§2b) ─────────────────────────────────────────────────────

def test_evidence_is_a_three_column_grid_with_a_fixed_source_rail():
    assert re.search(r"\.ev-row\s*\{[^}]*grid-template-columns:\s*62px 1fr 16px", CLIENT)


def test_the_source_rail_is_the_only_row_marker():
    """The old row had a leading em-dash AND a source; two markers for one
    row was the defect."""
    i = CLIENT.find("const evRow =")
    assert i > 0
    body = CLIENT[i:i + 1200]
    assert '<span class="dash">' not in body


# ── the claim (defect 2, and the claim-less empty state) ────────────────────

def test_the_claim_editable_is_inline_inside_the_quote():
    """.editable-text is inline-block globally: one atomic box that takes the
    full width the moment it wraps, which put the “ and ” on their own lines.
    Scoped to the claim, it has to be a plain inline."""
    assert ".wwr-claim-q .editable-text { display: inline;" in CLIENT


def test_an_absent_claim_is_never_quote_marks_around_nothing():
    """A blank quote reads as a guest who said "". The neutral empty state
    says what actually happened, and "+ Claim" recreates the field."""
    i = CLIENT.find('<span class="wwr-blk-label">Claim</span>')
    assert i > 0
    body = CLIENT[i:i + 1400]
    assert "does not state this in the guest's own words" in body
    assert "data-claim-add" in body
    assert '“${edSpan(`${bp}.claim`, \'\')}”' not in body, \
        "an empty claim is being rendered as empty quote marks again"


# ── one delete treatment (§2b, §8) ──────────────────────────────────────────

def test_every_delete_uses_the_one_class():
    """Delete controls never vary in colour between rows or sections."""
    assert re.search(r"\.x-del\s*\{[^}]*color:\s*var\(--dim\)", CLIENT)
    assert re.search(r"\.x-del:hover\s*\{\s*color:\s*var\(--red\)", CLIENT)


def test_root_cause_has_no_delete():
    """An issue with no root cause is not an RCA."""
    i = CLIENT.find("Root cause:</span>")
    assert i > 0
    assert "data-aline-del" not in CLIENT[i:i + 400]


# ── parity (§10) ────────────────────────────────────────────────────────────

def test_persistence_never_branches_on_whether_a_row_was_ai_generated():
    """A real bug class: the flag team-select once wrote only to
    operator-added rows, so on AI-generated flags the choice snapped back."""
    for smell in ("isGenerated", "is_generated", "wasAdded", "userAdded",
                  "aiGenerated"):
        assert smell not in CLIENT, f"persistence branches on origin: {smell}"


def test_no_new_colours_were_introduced():
    """Light theme only; every value comes from the :root tokens."""
    i, j = CLIENT.find("/* ── RCA v2: WWR issue internals"), CLIENT.find("</style>")
    block = CLIENT[i:j]
    assert i > 0 and block
    hexes = set(re.findall(r"#[0-9a-fA-F]{3,8}", block))
    assert not hexes, f"new literal colours in the v2 block: {sorted(hexes)}"


# ── interactions: facts from Zendesk, interpretation from the model ─────────

def test_contacts_are_grouped_not_one_row_per_event():
    """The Events timeline is the per-event view. One row per frame here would
    make the contact count report events, and a count nobody trusts is worse
    than none."""
    assert "const _contacts = (frames) => {" in CLIENT
    assert "30 * 60 * 1000" in CLIENT, "the time-window fallback is gone"


def test_the_frame_is_read_first_for_a_known_contact_s_time_and_channel():
    """This used to assert the model's time and channel appeared nowhere in
    the row at all. That cost an off-Zendesk contact — one with no frame — the
    only timestamp anybody had for it, and drew a dash instead.

    The guard is now precedence: the frame is consulted FIRST and the model's
    value is the `||` fallback. Written the other way round the model's value
    would displace a verifiable one, which is the failure the old assertion
    was really about.

    A source assertion, because this is client-side JavaScript — but only the
    ordering is checked here. That the frame's time actually WINS on screen is
    driven in a browser, in tests/test_contact_narrative_ui.py.
    """
    i = CLIENT.find("const _contactRow = (row) => {")
    assert i > 0
    # To the end of the function, not a fixed character count. A window of
    # 3000 chars was what this used to take, and adding a comment inside the
    # function pushed `first.time` past the edge — the string was present and
    # the test reported it missing, which is this repo's own rule turned on
    # its own suite.
    j = CLIENT.find("const sup = _rows.length", i)
    assert j > i, "the slice lost its end marker — it would read the whole file"
    body = CLIENT[i:j]
    assert "first.thread" in body and "first.time" in body, \
        "the pill and time are not being read from the Zendesk frame"
    for fact, label in (("thread", "channel"), ("time", "time")):
        frame_at = body.find(f"first.{fact}")
        model_at = body.find(f"(row.note || {{}}).{label}")
        assert model_at > frame_at > 0, (
            f"the model's {label} is consulted before the frame's — the "
            f"unverifiable value would displace the verifiable one")


def test_an_orphan_note_still_renders_and_says_why():
    """Either the guest reached us off Zendesk or the model invented a
    contact. Both are worth seeing; a zd_ref that matched nothing reads
    differently from no zd_ref at all."""
    assert "unmatched ZD reference" in CLIENT
    assert "guest's account, unverified" in CLIENT


def test_an_empty_guest_support_section_is_not_a_numbered_row():
    """Defect 4: "01 · UNKNOWN · Unknown · No guest contact found" was a
    nothing-found message dressed as a data row."""
    i = CLIENT.find("const sup = _rows.length")
    assert i > 0
    empty_branch = CLIENT[CLIENT.find("interactions-empty", i):][:400]
    for dressing in ("convo-num", "convo-type-pill", "convo-time"):
        assert dressing not in empty_branch, \
            f"the empty state renders a {dressing} — that is defect 4"


# ── §3 issue-specific answers: the section is gone ──────────────────────────
#
# Negative assertions, which is the shape CLAUDE.md allows: an unreachable
# branch cannot make a string appear nowhere. That the questions still reach
# the PROMPT is a behaviour test and lives in test_rca_finding_rules.py.

def test_the_issue_specific_answers_section_is_gone_from_the_card():
    """The questions did not go away — they are checks the RCA writes against
    now, and what one surfaces is written as an operational failure or an SOP
    gap. What went is the wall of verdict chips, which was read by nobody and
    invited the model to answer instead of diagnose."""
    for gone in ('id="rca-issue-answers-section"',
                 'data-v3sel="issue_specific_answers.',
                 'data-isa-key', 'data-isa-evidence',
                 'class="isa-row"', 'chip-isa-sel'):
        assert gone not in CLIENT, f"the answers section is still rendered: {gone}"


def test_no_handler_is_left_bound_to_markup_nothing_emits():
    """A handler for a control that no longer exists is dead code that reads as
    live, and the next person to touch this file has to prove it is dead."""
    assert "issueSpecificAnswers" not in CLIENT


# ── §9 SOP, §8 flag team, §12 DSS ───────────────────────────────────────────

def test_the_sop_section_is_gone_from_the_card():
    """Removed from the RCA. A section left rendering after its data stopped
    being produced shows an empty box forever, and an empty box is exactly
    what a broken section looks like."""
    assert 'data-v3sel="sop_compliance.verdict"' not in CLIENT
    assert 'id="rca-sop-section"' not in CLIENT
    assert 'id="rca-tldr-section"' not in CLIENT
    assert 'data-v3p="tldr.our_mistake"' not in CLIENT


def test_the_flag_team_is_a_closed_list_over_the_nine():
    """§2. Flags use the same nine teams as Actions Taken — one vocabulary,
    because the two are joined on it. The list is built from ACTION_TEAMS so
    the two cannot drift into two spellings of one team, which would make the
    join match nothing and empty the tabs."""
    assert 'data-v3sel="flags.${f._i}.team"' in CLIENT
    assert "const FLAG_TEAMS = ACTION_TEAMS.map(([t]) => t.toUpperCase()).concat(['OTHER']);" in CLIENT
    for team in ("guest", "sp", "content", "co", "tech", "inventory",
                 "product", "biz", "finance"):
        assert f"['{team}'," in CLIENT, f"{team} is not one of the nine tabs"
    # The five that are gone must not be back as tab keys.
    for gone in ("['customer',", "['ce',", "['business',"):
        assert gone not in CLIENT, f"an old tab key is still a tab: {gone}"


def test_the_dss_stub_is_gone():
    """"DSS row: — / —" told the reader nothing and looked broken."""
    assert "DSS row: ${esc(rca.issueL1" not in CLIENT
    assert "dss-block" in CLIENT
    assert "There is no DSS row to look up" in CLIENT or \
           "there is no DSS row to look up" in CLIENT


def test_dss_is_read_only_until_edit():
    """Reference data, not analysis — an operator reads it to check their
    resolution against the playbook.

    THIS USED TO ASSERT THE TERNARY'S EXACT SOURCE TEXT
    (`state.dssEdit ? edSpan('dss.prescribes'`), which is the spelling check
    CLAUDE.md forbids: it broke on a restructure that changed nothing about
    the behaviour, and it would have passed just as happily against a build
    where the branch had become unreachable. The BEHAVIOUR — read-only until
    ✎ Edit, editable after, and writable even when the lookup matched
    nothing — is driven in a real browser by
    tests/test_recent_changes_rendered.py::test_the_dss_block_exists_and_toggles_into_edit
    and ::test_an_unmatched_row_is_still_writable.

    What is left here is NEGATIVE, which unreachability cannot defeat: the
    panel must not be unconditionally editable.
    """
    i = CLIENT.find('<div class="dss-panel">')
    assert i > 0, "the DSS panel is gone from the client"
    panel = CLIENT[i:i + 900]
    assert "state.dssEdit" in panel, (
        "the DSS panel no longer consults the edit toggle at all, so it is "
        "either always or never editable")
