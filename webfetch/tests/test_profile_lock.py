"""Profile lock behavioral regression tests."""

import os
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import tiers


PROFILE = r"C:\tmp\webfetch\playwright3"
OTHER = r"C:\tmp\webfetch\playwright9"


def rec(status, user_data_dir, name="webfetch-headless-4242"):
    return {"name": name, "status": status, "user_data_dir": user_data_dir}


# --- the original defect ------------------------------------------------------

def test_all_closed_records_do_not_claim_the_dir():
    """THE REGRESSION. A wall of closed records must not hold a dir.

    This is the exact shape measured in production. Before the fix it returned
    True and the dir was unusable forever.
    """
    sessions = [rec("closed", PROFILE, "webfetch-headless-%d" % i) for i in range(152)]
    assert tiers._playwright_session_uses_profile(PROFILE, sessions) is False


def test_open_record_does_claim_the_dir():
    assert tiers._playwright_session_uses_profile(PROFILE, [rec("open", PROFILE)]) is True


def test_open_record_on_a_different_dir_does_not_claim_ours():
    assert tiers._playwright_session_uses_profile(PROFILE, [rec("open", OTHER)]) is False


def test_one_open_among_many_closed_still_claims():
    sessions = [rec("closed", PROFILE) for _ in range(50)]
    sessions.insert(25, rec("open", PROFILE))
    assert tiers._playwright_session_uses_profile(PROFILE, sessions) is True


# --- liveness vocabulary: only "closed" is proof of death ---------------------

@pytest.mark.parametrize("status", ["closed", "CLOSED", "  closed  "])
def test_closed_is_the_only_definitively_dead_value(status):
    assert tiers._session_is_live({"status": status}) is False


@pytest.mark.parametrize("status", ["open", "OPEN", "  open  ", "Open"])
def test_open_is_live_case_and_space_insensitively(status):
    assert tiers._session_is_live({"status": status}) is True


@pytest.mark.parametrize("status", ["starting", "connecting", "degraded", "unknown"])
def test_unrecognised_status_is_treated_as_live(status):
    """Fail toward CLAIMED on a state upstream might add later.

    Guessing dead hands out a dir Chromium may still have open. An earlier
    revision wrote this as `status == "open"`, which reads as the cautious
    spelling and is the opposite -- this test is what catches that regression.
    """
    assert tiers._session_is_live({"status": status}) is True


@pytest.mark.parametrize("status", ["", "   ", None])
def test_blank_status_on_a_single_record_is_treated_as_live(status):
    """A blank on ONE record is an oddity, not evidence the format changed.

    Wholesale parse failure is caught upstream by `_playwright_sessions_summary`
    returning None, so this default no longer has to carry that case -- and
    guessing dead here was the unsafe direction.
    """
    assert tiers._session_is_live({"status": status}) is True


# --- UNKNOWN is distinct from empty ------------------------------------------

def test_unreadable_session_list_reports_the_dir_as_in_use():
    """`None` means we could not read the list. It must NOT read as 'nothing
    holds any dir' -- that licensed claiming every one of them."""
    assert tiers._playwright_session_uses_profile(PROFILE, None) is not False


def test_summary_returns_none_when_the_command_fails(monkeypatch):
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (1, "", "boom"))
    assert tiers._playwright_sessions_summary() is None


def test_summary_returns_none_on_format_drift(monkeypatch):
    """Records parsed but not one status understood -> the format moved."""
    out = "### Browsers\n- webfetch-a:\n  - browser-type: chrome\n  - user-data-dir: X\n"
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._playwright_sessions_summary() is None


def test_summary_parses_a_normal_listing(monkeypatch):
    out = ("### Browsers\n- webfetch-a:\n  - status: closed\n  - user-data-dir: X\n"
           "- webfetch-b:\n  - status: open\n  - user-data-dir: Y\n")
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    got = tiers._playwright_sessions_summary()
    assert got is not None and len(got) == 2
    assert [s["status"] for s in got] == ["closed", "open"]


def test_empty_listing_is_empty_not_unknown(monkeypatch):
    """No sessions at all is a real answer, and must stay distinct from None.

    Upstream prints this marker literally (program.js:347) and it is the ONLY
    positive evidence of emptiness.
    """
    out = "### Browsers\n  (no browsers)\n"
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._playwright_sessions_summary() == []


def test_header_format_drift_is_unknown_not_empty(monkeypatch):
    """Zero records parsed and no emptiness marker means we did not UNDERSTAND
    the output -- not that the machine is idle.

    If upstream changes the session-header syntax, nothing matches the record
    parser. Reporting `[]` there is indistinguishable from an idle machine and
    is enough to license claiming a dir a live Chromium still holds.
    """
    out = "### Browsers\n* webfetch-a [open] dir=X\n* webfetch-b [open] dir=Y\n"
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._playwright_sessions_summary() is None


def test_garbage_output_is_unknown(monkeypatch):
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, "wat\n", ""))
    assert tiers._playwright_sessions_summary() is None


# --- the OS lock --------------------------------------------------------------

def test_lock_excludes_a_second_claim(tmp_path, monkeypatch):
    """Two claims on one dir: the second must fail while the first holds."""
    monkeypatch.setattr(tiers, "_playwright_session_uses_profile",
                        lambda d, s=None: False)
    d = str(tmp_path / "playwright")
    os.makedirs(d, exist_ok=True)

    first = tiers._try_claim_profile_dir(d)
    assert first is not None, "first claim should succeed"
    try:
        assert tiers._try_claim_profile_dir(d) is None, "second claim must be refused"
    finally:
        tiers._release_profile_dir(d, first)


def test_release_makes_the_dir_claimable_again(tmp_path, monkeypatch):
    monkeypatch.setattr(tiers, "_playwright_session_uses_profile",
                        lambda d, s=None: False)
    d = str(tmp_path / "playwright")
    os.makedirs(d, exist_ok=True)

    fd = tiers._try_claim_profile_dir(d)
    assert fd is not None
    tiers._release_profile_dir(d, fd)

    again = tiers._try_claim_profile_dir(d)
    assert again is not None, "dir must be reusable after release"
    tiers._release_profile_dir(d, again)


def test_claim_refused_when_a_live_session_holds_the_dir(tmp_path, monkeypatch):
    """The lock cannot see a playwright daemon that outlived its parent, so the
    session check is the second guard and must still refuse."""
    monkeypatch.setattr(tiers, "_playwright_session_uses_profile",
                        lambda d, s=None: True)
    d = str(tmp_path / "playwright")
    os.makedirs(d, exist_ok=True)
    assert tiers._try_claim_profile_dir(d) is None


def test_refused_claim_does_not_strand_the_lock(tmp_path, monkeypatch):
    """A refusal must release the lock it took, or the dir is lost until exit."""
    calls = {"n": 0}

    def busy_once(d, s=None):
        calls["n"] += 1
        return calls["n"] == 1

    monkeypatch.setattr(tiers, "_playwright_session_uses_profile", busy_once)
    d = str(tmp_path / "playwright")
    os.makedirs(d, exist_ok=True)

    assert tiers._try_claim_profile_dir(d) is None      # refused, must unlock
    fd = tiers._try_claim_profile_dir(d)
    assert fd is not None, "lock was stranded by the refused claim"
    tiers._release_profile_dir(d, fd)


def test_release_without_an_fd_does_not_unlink(tmp_path):
    """Never remove a claim we cannot prove is ours -- that was the defect."""
    d = tmp_path / "playwright"
    d.mkdir()
    marker = d / ".webfetch_pid"
    marker.write_text("99999", encoding="utf-8")
    tiers._release_profile_dir(str(d), None)
    assert marker.exists(), "release with no fd must not delete another's marker"


# --- tri-state pid probe ------------------------------------------------------

def test_current_process_is_alive():
    assert tiers._pid_state(os.getpid()) == "alive"
    assert tiers._pid_alive(os.getpid()) is True


@pytest.mark.parametrize("pid", [0, -1])
def test_nonsense_pids_are_dead(pid):
    assert tiers._pid_state(pid) == "dead"


def test_pid_alive_fails_toward_alive_on_unknown(monkeypatch):
    """Only a positive proof of absence may read as dead. An unknown probe must
    NOT let the reaper delete a live peer's session."""
    monkeypatch.setattr(tiers, "_pid_state", lambda p: "unknown")
    assert tiers._pid_alive(1234) is True


# --- cleanup only on a CONFIRMED close ---------------------------------------

def test_closer_skips_dead_records(monkeypatch):
    calls = []
    monkeypatch.setattr(tiers, "_run_pw",
                        lambda args, timeout=None: (calls.append(args), (0, "", ""))[1])
    sessions = [rec("closed", PROFILE, "dead-%d" % i) for i in range(10)]
    sessions.append(rec("open", PROFILE, "live-1"))
    sessions.append(rec("open", OTHER, "live-elsewhere"))

    tiers._close_playwright_sessions_on_profile(PROFILE, sessions)

    assert len(calls) == 1, "expected exactly one close, got %d" % len(calls)
    assert "live-1" in calls[0][0]


def test_reap_zombies_deletes_records_not_just_closes(monkeypatch):
    """`close` alone LEAKS the record of a persistent session, which is why the
    old --reap-zombies was a no-op against the condition its own error message
    recommended it for."""
    calls = []
    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: ["webfetch-headless-1"])
    monkeypatch.setattr(tiers, "_run_pw",
                        lambda args, timeout=None: (calls.append(args), (0, "", ""))[1])

    closed, deleted, problems = tiers.reap_zombies()

    verbs = [a[-1] for a in calls]
    assert "close" in verbs and "delete-data" in verbs
    assert (closed, deleted, problems) == (1, 1, 0)


def test_reap_reports_undeleted_records_as_a_problem(monkeypatch):
    """A record that will not delete keeps accumulating -- and accumulating
    records is the ORIGINAL OUTAGE'S PRECONDITION. Reporting a clean sweep here
    rebuilds the exact condition the command exists to clear."""
    def run(args, timeout=None):
        return (1, "", "denied") if args[-1] == "delete-data" else (0, "", "")

    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: ["webfetch-headless-1"])
    monkeypatch.setattr(tiers, "_run_pw", run)

    closed, deleted, problems = tiers.reap_zombies()
    assert (closed, deleted) == (1, 0)
    assert problems == 1, "an undeleted record must be reported as a problem"


def test_list_sessions_treats_header_drift_as_unknown(monkeypatch):
    """The SECOND parser needs the same rule as the first.

    Fixing only the rc != 0 half left the rc == 0 half turning format drift into
    a confident empty list, so --reap-zombies closed nothing, reported a clean
    sweep and exited zero while live records remained.
    """
    out = "### Browsers\n* webfetch-a [open]\n* webfetch-b [open]\n"
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._list_sessions() is None


def test_list_sessions_empty_on_the_marker(monkeypatch):
    """List sessions empty on the marker."""
    out = "### Browsers\n  (no browsers)\n"
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._list_sessions() == []



def test_list_sessions_empty_when_records_parse_but_none_are_ours(monkeypatch):
    """List sessions empty when records parse but none are ours."""
    out = """### Browsers
- costco:
  - status: open
  - browser-type: chrome
  - user-data-dir: <in-memory>
  - headed: false
- ctlens:
  - status: open
  - user-data-dir: <in-memory>
- em:
  - status: open
  - user-data-dir: <in-memory>
- sweep:
  - status: open
  - user-data-dir: <in-memory>
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._list_sessions() == []


def test_list_sessions_returns_only_ours_from_a_mixed_listing(monkeypatch):
    """Foreign sessions sitting alongside ours must never come back.

    Every caller relies on these names being OURS, but they use them
    differently and the difference matters: `reap_zombies` and
    `startup_reap` CLOSE and delete what they are handed, while
    `_ensure_open` only tests membership and may adopt a session it finds.
    So a false positive here is destructive on the cleanup paths and
    merely wrong on the open path."""
    out = """### Browsers
- costco:
  - status: open
- webfetch-headless-4242:
  - status: open
- sweep:
  - status: open
- webfetch-headed-4242:
  - status: open
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._list_sessions() == ["webfetch-headless-4242",
                                      "webfetch-headed-4242"]


def test_list_sessions_does_not_count_indented_lines_as_records(monkeypatch):
    """The record test is anchored at column 0, and that anchor is load-bearing.

    The anchor now lives in `_playwright_sessions_summary`, which
    `_list_sessions` delegates to -- so this pins the behaviour THROUGH
    that delegation, which is the point of having one parser rather than
    two that agree only by comment.

    An attribute line reads `  - status: open` -- the same `- ` shape, indented.
    If drift ever strips the headers and leaves the attributes behind, counting
    those as records would manufacture precisely the confident empty list this
    function exists to refuse. Zero headers and no marker must stay UNKNOWN.
    """
    out = """### Browsers
  - status: open
  - user-data-dir: X
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._list_sessions() is None


def test_list_sessions_unknown_on_a_top_level_prose_bullet(monkeypatch):
    """A bullet that is not a session record must not license an empty answer.

    Cross-family review, 2026-08-29, and it was CRITICAL: an earlier version of
    this fix counted any unindented `- ` line as a record, so a footer or a
    diagnostic line made `_list_sessions` return [] -- and `reap_zombies` then
    reported a COMPLETE sweep of a listing it had never understood. Delegating
    to the sibling parser refuses it, because that one also requires at least
    one parsed `status:` before calling a listing understood.
    """
    out = """### Browsers
- listing unavailable
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._list_sessions() is None


def test_list_sessions_unknown_on_a_truncated_header_only_listing(monkeypatch):
    """Output cut off after a record header is not an empty machine.

    A header with no attributes beneath it means the stream stopped mid-record.
    Answering [] there claims we read a listing that was never finished, and
    sessions of ours could have followed the cut.
    """
    out = """### Browsers
- costco:
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._list_sessions() is None


def test_list_sessions_does_not_strip_hyphens_out_of_a_foreign_name(monkeypatch):
    """A foreign session named `--webfetch-...` is NOT one of ours.

    Cross-family review, must-fix: name extraction used `lstrip("- ")`, which
    strips a CHARACTER SET rather than a prefix, so the leading hyphens of the
    real name were eaten along with the record delimiter and the session came
    back as `webfetch-headless-4242`. The cleanup paths would then have closed
    and deleted somebody else's browser.
    """
    out = """### Browsers
- --webfetch-headless-4242:
  - status: open
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._list_sessions() == []


def test_list_sessions_unknown_when_one_record_is_truncated_after_a_valid_one(
        monkeypatch):
    """A complete record must not vouch for an incomplete one.

    Cross-family review round 2, and it was CRITICAL. The shared parser accepts
    a listing when ANY record carried a status -- fine for asking "is this dir
    in use", wrong for deciding what to destroy. Here `costco` parses fully and
    `webfetch-headless-4242` is a bare header because the stream was cut. The
    global check passed, so `_list_sessions` returned just the one name it could
    see, `reap_zombies` closed it and reported (1, 1, 0) -- a COMPLETE sweep --
    while any session beyond the cut kept running.

    Reporting a sweep as complete when it is not is precisely what the
    None-versus-[] distinction exists to prevent, so a statusless record now
    makes the whole listing UNKNOWN.
    """
    out = """### Browsers
- costco:
  - status: open
- webfetch-headless-4242:
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._list_sessions() is None


def test_summary_partial_listing_behaviour_is_unchanged_by_this_fix(monkeypatch):
    """A CHANGE DETECTOR for this patch, NOT an endorsement of the behaviour."""
    out = """### Browsers
- costco:
  - status: open
- webfetch-headless-4242:
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    got = tiers._playwright_sessions_summary()
    assert got is not None and [s["name"] for s in got] == [
        "costco", "webfetch-headless-4242"]
    assert tiers._list_sessions() is None, "the strict caller still refuses it"


def test_our_dirless_session_blocks_every_claim(monkeypatch):
    """OURS, live, reporting no directory -> we cannot rule out that it holds
    the candidate, so nothing may be claimed this pass."""
    out = """- webfetch-headless-4242:
  - status: open
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    sessions = tiers._playwright_sessions_summary()
    assert tiers._playwright_session_uses_profile(PROFILE, sessions) is True


def test_a_foreign_dirless_session_does_not_block_any_claim(monkeypatch):
    """AND THIS IS THE ONE THAT KEEPS THE FIX FROM BEING AN OUTAGE."""
    out = """- costco:
  - status: open
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    sessions = tiers._playwright_sessions_summary()
    assert tiers._playwright_session_uses_profile(PROFILE, sessions) is False


def test_a_foreign_attached_session_does_not_block_any_claim(monkeypatch):
    """Same requirement, with the attach marker actually present."""
    out = """- costco:
  - status: open
  - browser-type: chrome (attached)
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    sessions = tiers._playwright_sessions_summary()
    assert tiers._playwright_session_uses_profile(PROFILE, sessions) is False


def test_a_reported_dir_still_decides_regardless_of_owner(monkeypatch):
    """Ownership gates only the DIR-LESS case. A record that reports a directory
    is judged on that directory, whoever owns the session."""
    out = """- costco:
  - status: open
  - user-data-dir: %s
""" % PROFILE
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    sessions = tiers._playwright_sessions_summary()
    assert tiers._playwright_session_uses_profile(PROFILE, sessions) is True


def test_our_own_cdp_session_does_not_block_any_claim(monkeypatch):
    """`webfetch-cdp-*` IS ours and still must not block a numbered profile."""
    out = """- webfetch-cdp-4242-1:
  - status: open
  - browser-type: chrome (attached)
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    sessions = tiers._playwright_sessions_summary()
    assert sessions is not None and sessions[0]["name"] == "webfetch-cdp-4242-1"
    assert tiers._playwright_session_uses_profile(PROFILE, sessions) is False


def test_a_header_cut_mid_name_is_an_unknown_listing(monkeypatch):
    """`- webf` is not a session called `webf`. It is a severed line."""
    out = """- costco:
  - status: open
  - user-data-dir: C:/Temp/other
- webf"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    assert tiers._playwright_sessions_summary() is None
    # UNKNOWN must refuse, not permit.
    assert tiers._playwright_session_uses_profile(PROFILE) is True


def test_a_complete_header_is_still_accepted(monkeypatch):
    """The colon check must not reject well-formed listings."""
    out = """- costco:
  - status: open
  - user-data-dir: C:/Temp/other
"""
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    got = tiers._playwright_sessions_summary()
    assert got is not None and [s["name"] for s in got] == ["costco"]


def test_a_record_cut_mid_path_blocks_every_claim(monkeypatch):
    """A LISTING CUT INSIDE user-data-dir, which is worse than one cut before it."""
    fragment = PROFILE[:-1]          # `...playwright3` -> `...playwright`
    assert PROFILE.startswith(fragment) and fragment != PROFILE
    out = ("- costco:\n  - status: open\n  - user-data-dir: C:/Temp/other\n"
           "- webfetch-headless-4242:\n  - status: open\n"
           "  - user-data-dir: " + fragment)      # NO trailing newline: cut
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    sessions = tiers._playwright_sessions_summary()
    assert sessions is not None
    assert sessions[-1]["truncated"] is True, "the cut record must be marked"
    assert tiers._playwright_session_uses_profile(PROFILE, sessions) is True


def test_a_newline_terminated_listing_marks_nothing_truncated(monkeypatch):
    """AND THIS IS THE ARM THAT KEEPS THE GUARD FROM BEING AN OUTAGE.

    A complete listing must mark nothing, and a live session of ours sitting on
    a DIFFERENT dir must not block the candidate. Get this wrong and one live
    headless session refuses the whole series -- the 2026-08-23 shape.
    """
    out = ("- costco:\n  - status: open\n  - user-data-dir: C:/Temp/other\n"
           "- webfetch-headless-4242:\n  - status: open\n"
           "  - user-data-dir: " + OTHER + "\n")   # terminated: complete
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    sessions = tiers._playwright_sessions_summary()
    assert sessions is not None
    assert all(s["truncated"] is False for s in sessions)
    assert tiers._playwright_session_uses_profile(PROFILE, sessions) is False


def test_a_foreign_record_cut_mid_path_blocks_nothing(monkeypatch):
    """Somebody else's truncated record must never wedge our series.

    The marking happens for every last record; the REFUSAL is gated on
    ownership. Without that gate, any tool on the box whose session landed last
    in a cut listing would make every one of our dirs unclaimable.
    """
    out = ("- costco:\n  - status: open\n  - user-data-dir: C:/Temp/other\n"
           "- ctlens:\n  - status: open\n  - user-data-dir: " + PROFILE[:-1])
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    sessions = tiers._playwright_sessions_summary()
    assert sessions is not None and sessions[-1]["truncated"] is True
    assert tiers._playwright_session_uses_profile(PROFILE, sessions) is False


def test_our_cdp_record_cut_mid_path_blocks_nothing(monkeypatch):
    """`webfetch-cdp-*` is ours and still must not block a numbered dir.

    It attaches to the user's own Chrome and never holds one of our dirs, so a
    truncated CDP record is not evidence about this series. Matching the bare
    `webfetch-` prefix here would let a single live CDP fetch kill both browser
    tiers -- the outage reached from inside our own session namespace.
    """
    out = ("- costco:\n  - status: open\n  - user-data-dir: C:/Temp/other\n"
           "- webfetch-cdp-4242-1:\n  - status: open\n"
           "  - user-data-dir: " + PROFILE[:-1])
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    sessions = tiers._playwright_sessions_summary()
    assert sessions is not None and sessions[-1]["truncated"] is True
    assert tiers._playwright_session_uses_profile(PROFILE, sessions) is False


def test_release_does_not_unlink_the_lock_file(tmp_path, monkeypatch):
    """THE LOCK FILE MUST SURVIVE RELEASE. Unlinking it reopens the original
    race in a subtler form: A closes its fd, B opens and locks the same inode,
    A's unlink removes B's pathname, C creates a NEW file there and locks that
    -- a different inode, so B's and C's locks cannot see each other and both
    own the same profile. A lock file's identity IS the mechanism.
    """
    monkeypatch.setattr(tiers, "_playwright_session_uses_profile",
                        lambda d, s=None: False)
    d = tmp_path / "playwright"
    d.mkdir()
    fd = tiers._try_claim_profile_dir(str(d))
    assert fd is not None
    marker = tiers._lock_path_for(str(d))
    assert marker.exists()
    tiers._release_profile_dir(str(d), fd)
    assert marker.exists(), "release must NOT unlink the lock file"


def test_lock_lives_outside_the_profile_dir(tmp_path, monkeypatch):
    """LINUX PREREQ. A lock kept inside the dir it guards is destroyed when that
    dir is deleted -- a temp cleaner or `rm -rf` then lets a second process
    create and lock a NEW inode at the same pathname, so two live processes hold
    locks that cannot see each other and both own the same profile.
    """
    monkeypatch.setattr(tiers, "_playwright_session_uses_profile",
                        lambda d, s=None: False)
    d = tmp_path / "playwright"
    d.mkdir()
    lock = tiers._lock_path_for(str(d))
    assert d not in lock.parents, "lock must not live inside the profile dir"
    assert lock.parent.name == "locks"

    fd = tiers._try_claim_profile_dir(str(d))
    assert fd is not None
    try:
        # Destroy and recreate the profile dir under the live holder.
        shutil.rmtree(d)
        d.mkdir()
        # The lock must have survived, so a second claim is still refused.
        assert tiers._try_claim_profile_dir(str(d)) is None, \
            "profile dir was recreated and the claim split in two"
    finally:
        tiers._release_profile_dir(str(d), fd)


def test_lock_path_is_stable_and_unique():
    a1 = tiers._lock_path_for(r"C:\tmp\webfetch\playwright")
    a2 = tiers._lock_path_for(r"C:\tmp\webfetch\playwright")
    b = tiers._lock_path_for(r"C:\tmp\webfetch\playwright2")
    assert a1 == a2, "same dir must map to the same lock across calls"
    assert a1 != b, "different dirs must not share a lock"


def test_default_profile_base_is_per_user(tmp_path, monkeypatch):
    """The public cache path is home-relative on every supported platform."""
    monkeypatch.setattr(tiers.Path, "home", lambda: tmp_path)
    assert Path(tiers._default_profile_base()) == tmp_path / ".cache" / "webfetch" / "playwright"


def test_claim_rechecks_against_a_fresh_listing(tmp_path, monkeypatch):
    """A snapshot may REFUSE a claim but must never AUTHORISE one.

    The interleaving: the snapshot is taken, another process then opens a
    session on this dir and DIES -- dropping its OS lock while its playwright
    daemon keeps Chromium alive on the profile. The lock looks free and the
    stale snapshot is silent, so only a fresh check catches it.
    """
    seen = []

    def uses(d, s=tiers._SESSIONS_UNSET):
        seen.append("snapshot" if s is not tiers._SESSIONS_UNSET else "fresh")
        return s is tiers._SESSIONS_UNSET   # stale snapshot silent, fresh says busy

    monkeypatch.setattr(tiers, "_playwright_session_uses_profile", uses)
    d = tmp_path / "playwright"
    d.mkdir()

    stale_snapshot = []
    assert tiers._try_claim_profile_dir(str(d), stale_snapshot) is None, \
        "a fresh check must override a silent stale snapshot"
    assert "fresh" in seen, "claim consulted only the snapshot"


def test_reap_returns_none_when_the_listing_fails(monkeypatch):
    """`--reap-zombies` is the release valve the profile-lock error points at.
    It must not do nothing, report success and exit zero on a broken listing."""
    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: None)
    assert tiers.reap_zombies() is None


def test_startup_reap_does_nothing_when_the_listing_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: None)
    monkeypatch.setattr(tiers, "_run_pw",
                        lambda args, timeout=None: (calls.append(args), (0, "", ""))[1])
    tiers._STARTUP_REAPED = False
    assert tiers.startup_reap() == 0
    assert calls == [], "must not act on an unreadable listing"


def test_list_sessions_returns_none_on_failure(monkeypatch):
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (1, "", "nope"))
    assert tiers._list_sessions() is None


def test_reap_zombies_does_not_delete_after_a_failed_close(monkeypatch):
    """A record may be the ONLY evidence that a live Chromium holds a dir.
    Deleting it after an unconfirmed close erases that evidence."""
    calls = []

    def run(args, timeout=None):
        calls.append(args)
        return (1, "", "timeout") if args[-1] == "close" else (0, "", "")

    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: ["webfetch-headless-1"])
    monkeypatch.setattr(tiers, "_run_pw", run)

    closed, deleted, problems = tiers.reap_zombies()

    assert "delete-data" not in [a[-1] for a in calls], "deleted despite failed close"
    assert (closed, deleted) == (0, 0), "must not report work it did not do"
    assert problems == 1, "a failed close must be reported as a problem"
