"""The copy-file validator reports the roster, including when there isn't one.

WHY THIS MATTERS MORE THAN IT LOOKS. content/orm_macros.yaml tells CX to run
tools/check_macros.py after editing, and that tool is the only feedback they
get. A misspelled key — `reviewer:`, `reviewers :` — is perfectly valid YAML.
The file validates clean, the app loads an empty roster, and the "Picked up by"
dropdown silently falls back to the free-text box it replaced. Nobody finds
out until someone types a name again and the three-spellings-of-one-person
problem comes back.

So: the count is printed on every run, present or absent, and a near-miss key
is named. Driven as a subprocess against real files — the tool's whole job is
to be run by hand on a file, so that is how it is tested.
"""
import shutil
import subprocess
import sys


def _run(tmp_path, edit=None):
    """Run the checker against a copy of the real file, optionally edited."""
    src = "content/orm_macros.yaml"
    dst = tmp_path / "orm_macros.yaml"
    shutil.copy(src, dst)
    if edit:
        dst.write_text(edit(dst.read_text(encoding="utf-8")), encoding="utf-8")
    return subprocess.run([sys.executable, "tools/check_macros.py",
                           "--file", str(dst)],
                          capture_output=True, text=True, timeout=120)


def test_the_roster_is_reported_on_an_ordinary_run(tmp_path):
    r = _run(tmp_path)
    assert "reviewers" in r.stdout, r.stdout
    from server.prompts import REVIEWERS
    for name in REVIEWERS:
        assert name in r.stdout, f"{name} is in the file but not in the report"


def test_a_misspelled_key_is_named_rather_than_passing_silently(tmp_path):
    r = _run(tmp_path, lambda t: t.replace("\nreviewers:", "\nreviewer:"))
    assert "reviewer" in r.stdout, r.stdout
    assert "text box" in r.stdout, \
        "the consequence of the typo is not stated, only the typo"


def test_a_missing_roster_is_said_out_loud_not_left_blank(tmp_path):
    """Absent must not read the same as present-and-fine. Silence here is
    exactly the shape of a key nobody noticed was gone."""
    def _drop(t):
        i = t.find("\nreviewers:")
        return t[:i] if i > 0 else t
    r = _run(tmp_path, _drop)
    assert "reviewers" in r.stdout, r.stdout
    assert "text box" in r.stdout, r.stdout


def test_a_blank_entry_is_a_failure_not_a_person_called_none(tmp_path):
    """A bare "- " parses as None, and str(None) is "None" — four characters
    that would have shipped as an assignable person."""
    r = _run(tmp_path, lambda t: t.replace('    - "Paul"', '    - "Paul"\n    -'))
    assert "blank" in r.stdout, r.stdout
    assert "None" not in r.stdout.split("reviewers")[-1][:200], \
        "a blank entry was rendered as the name None"


def test_a_duplicate_name_is_flagged(tmp_path):
    r = _run(tmp_path, lambda t: t.replace('    - "Paul"', '    - "Paul"\n    - "Paul"'))
    assert "twice" in r.stdout, r.stdout


def test_an_unedited_file_still_ships(tmp_path):
    """The checker must not start failing the file that is actually in use."""
    r = _run(tmp_path)
    assert "Good to ship" in r.stdout, r.stdout
