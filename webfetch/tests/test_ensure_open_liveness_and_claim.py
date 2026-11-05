"""`_ensure_open` adopts only an OPEN session, and reuses a claim it still holds.

Two defects that a test building a fresh session per call cannot see, because
both need a session object that has already tried once.

A NAME COLLISION IS NOT PROOF THE RECORD IS OURS. Session names embed the
owning pid (`_SESSION_PID_RE`), and an earlier version of this docstring read
that as "same-process only". It is not: a pid can be REUSED, and a reused pid
produces the SAME session name -- `startup_reap` re-probes immediately before
acting for exactly that reason -- and a persistent daemon record outlives the
Python process that made it. What holds is narrower: two SIMULTANEOUSLY LIVE
owners cannot share a pid, so a record reporting `open` is a live session.
Anything else under our name may be the residue of a DIFFERENT, earlier
process, which is part of why adoption demands positive openness.

DEFECT A -- a session record outliving its browser was adopted as live.
`_list_sessions` returned NAMES with no status. Our sessions are launched
persistent, and upstream retains a persistent session's record after `close()`
-- so a `close()` that stopped Chrome but whose follow-up `delete-data` FAILED
leaves the record standing under our exact name. `_ensure_open` then set
`_opened = True` having launched nothing and claimed no profile dir; `tab-new`
failed, and every later attempt in that process took the same early return.

DEFECT B -- a retained profile claim was abandoned instead of reused. An
indeterminate launch failure KEEPS its claim on purpose (the `launched` flag),
because releasing one under a live browser costs the next claimant `Browser is
already in use`. But `_ensure_open` then claimed a FRESH dir on the next call,
overwrote `_profile_fd` and stranded the previous dir until process exit. The
pool is ten dirs and it is SHARED ACROSS PROCESSES.

TWO MORE, both found by cross-family review of the fixes above and both fixed
here, are pinned in the same file because each is a hazard the ORIGINAL fix
introduced or left standing:

DEFECT C -- the reuse could release a lock held for a live browser. `launched`
describes THIS invocation only, so on a reused claim a reported `open` failure
(`rc != 0`) drove the handler to release a dir an EARLIER invocation's browser
may still be running on -- and `Browser is already in use` is exactly the rc !=
0 such a browser produces. `claimed_here` gates the release: a reused claim is
never released inside `_ensure_open`.

DEFECT D -- a truncated `closed` status was adopted as open. A listing cut
mid-write inside `- status: closed` stores a non-blank fragment (`clo`), which
the all-record completeness guard then passed through; a predicate reading
"anything but `closed` is live" called it live. Adoption now demands a
POSITIVELY open record, and judges each record on its own -- that guard belongs
to the destructive callers and no longer runs on the adoption path.

NO TEST HERE MAY DRIVE A BROWSER, for the reason `test_tier1_real_chrome.py`
gives: `playwright-cli` on this box would reach whatever Chrome is running on
the user's desktop, and `--headed` must never become reachable from a test run.
`_run_pw`, `_claim_profile_dir`, `_release_profile_dir` and the session listing
are all stubbed; nothing here spawns a subprocess or touches the real state DB.

TWO ARMS AT THE END ARE THE EXCEPTION TO THE FIRST HALF OF THAT SENTENCE and
not to the second: they call the REAL `_run_pw` to check what it passes to the
process, with `subprocess.run` itself faked. Nothing spawns there either -- the
boundary just moves one layer down, which is the only level at which the claim
they make is checkable.
"""

import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import state
import tiers


# The REAL parser, captured at import before any fixture can stub it. One test
# below needs it back: what it exercises is the path from `_run_pw` THROUGH
# `_playwright_sessions_summary`, and the `launch` fixture stubs that parser
# out. Without this the test passes while never reaching the code it names --
# measured, it passed against the unfixed revision for exactly that reason.
REAL_SUMMARY = tiers._playwright_sessions_summary

def _capture_real_run_pw():
    """Bind the real `_run_pw` in a CLOSURE CELL and return the fixture that uses it.

    WHY A CLOSURE AND NOT A MODULE CONSTANT. conftest replaces `tiers._run_pw` with
    a stub that RAISES, and that stub is the suite's ONLY browser-launch tripwire:
    `_run_pw` is the chokepoint every spawn in this skill goes through. The two
    environment arms below need the real function, so they need a reference taken
    before that fixture runs -- and a module-level name holding it is a way PAST
    the tripwire. That is MEASURED, not theoretical: with the per-test fake
    removed, the captured object reached a real `playwright-cli.CMD` spawn, stack
    `tiers.py _run_pw -> subprocess.run -> Popen`. The argv there happened to be
    `list`; the argv is whatever the next arm writes, and this file's subject is
    the `open --headed` launch path.

    An earlier version of this file did exactly that and said in a comment that
    nothing may use the name directly. Cross-family review rejected it, correctly:
    a rule that lives in a comment is obeyed by whoever reads the comment. The
    capture now has no module-visible name at all, so the fixture is the only way
    to reach it -- and the fixture fakes `tiers.subprocess.run` BEFORE handing it
    out, which is what makes that statement true rather than aspirational.

    THE CAPTURE STILL HAPPENS AT IMPORT, which is what it has to do: this factory
    is CALLED at module level, below, before any fixture has run.

    WHAT IS NOT CLAIMED: this does not defeat deliberate introspection -- a closure
    cell is still readable through `__wrapped__.__closure__`. It closes the
    ordinary route, which is a name a test can type or copy from a nearby line.
    """
    captured = tiers._run_pw

    @pytest.fixture
    def real_run_pw(monkeypatch):
        """The real `_run_pw` with its spawn boundary already faked.

        Yields `(fn, seen)`, where `seen` is the kwargs dict the faked
        `subprocess.run` recorded, plus `argv`.
        """
        seen = {}

        class FakeCompleted:
            returncode, stdout, stderr = 0, "", ""

        def fake_run(argv, **kwargs):
            seen.update(kwargs)
            seen["argv"] = argv
            return FakeCompleted()

        monkeypatch.setattr(tiers.subprocess, "run", fake_run)
        return captured, seen

    return real_run_pw


real_run_pw = _capture_real_run_pw()


def session_record(name, status, truncated=False, user_data_dir="<in-memory>"):
    """One `_playwright_sessions_summary` record, in the shape it really emits.

    `truncated` is carried because the real parser sets it on EVERY record --
    False as it builds them, True on the last one when the listing does not end
    in a newline. A fixture missing a key the code reads is a fixture that tests
    a different function.
    """
    return {"name": name, "status": status, "user_data_dir": user_data_dir,
            "truncated": truncated}


def only_record(monkeypatch, session, status, truncated=False):
    """Install a listing holding exactly ONE record, under `session`'s own name.

    Stubbed at `_playwright_sessions_summary`, not at `_list_sessions`, so the
    real per-record openness predicate is the code under test rather than
    something the stub steps over. On the `open_only=True` path that predicate
    is the ONLY thing between this record and adoption -- the all-record
    completeness guards do not run there.
    """
    record = session_record(session.session_name, status, truncated)
    monkeypatch.setattr(tiers, "_playwright_sessions_summary",
                        lambda *, diagnose=False: [record])


class Recorder:
    """Stub for `_run_pw`. Records argv; scripts successive `open` results.

    THREE SHAPES EXPRESS AN INDETERMINATE FAILURE -- the one where the daemon may
    already be up and no exit status ever comes back -- and a test asserting on
    the claim must say which it means. A BaseException INSTANCE in
    `open_results` is RAISED rather than returned: `_run_pw` catches only
    TimeoutExpired and FileNotFoundError, so PermissionError and every other
    OSError out of `subprocess.run` reaches `_ensure_open` exactly this way. A
    scripted `(-1, ...)` is the second: it is what `_run_pw` itself returns when
    its own timeout fires, and `_ensure_open` reads -1 as indeterminate too.

    THE THIRD IS A SCRIPTED POSITIVE VALUE OUTSIDE 0..255, and it is the one that
    breaks the intuition this docstring used to give. Since the release arm
    narrowed to an ordinary exit status, a scripted 3221225477 or 256 is NOT an
    ordinary report -- it is an abnormal termination, indeterminate, and it KEEPS
    the claim. A scripted value INSIDE 0..255 is the ordinary report that releases.
    So the number in a scripted tuple now decides the case, where it once did not.
    """

    def __init__(self):
        self.calls = []
        self.open_results = []

    def __call__(self, args, timeout=None):
        self.calls.append(list(args))
        if "open" in args:
            if self.open_results:
                result = self.open_results.pop(0)
                if isinstance(result, BaseException):
                    raise result
                return result
            return (0, "", "")
        return (0, "", "")

    def opens(self):
        return [c for c in self.calls if "open" in c]


class Pool:
    """Stub for `_claim_profile_dir` / `_release_profile_dir`.

    Hands out the next dir per claim and records claims and releases, so a test
    can assert HOW MANY claims a launch sequence made -- not merely which dir it
    ended on. Defect B is a COUNT: the second claim was real, and the dir it
    replaced stayed locked with nothing pointing at it.
    """

    def __init__(self, dirs):
        self._dirs = list(dirs)
        self.claimed = []
        self.released = []

    def claim(self):
        d = self._dirs[len(self.claimed)]
        fd = 4200 + len(self.claimed)
        self.claimed.append((d, fd))
        return d, fd

    def release(self, profile_dir, fd=None):
        self.released.append((profile_dir, fd))


@pytest.fixture
def pool(tmp_path, monkeypatch):
    dirs = []
    for name in ("playwright", "playwright2", "playwright3"):
        d = tmp_path / name
        d.mkdir()
        dirs.append(str(d))
    p = Pool(dirs)
    monkeypatch.setattr(tiers, "_claim_profile_dir", p.claim)
    monkeypatch.setattr(tiers, "_release_profile_dir", p.release)
    return p


@pytest.fixture
def launch(tmp_path, pool, monkeypatch):
    """A stubbed launch: no subprocess, no browser, state DB under tmp_path.

    The listing defaults to EMPTY -- the tests that care about a record install
    their own. It is stubbed at `_playwright_sessions_summary` rather than at
    `_list_sessions`, so the real per-record predicate is the code under test.
    """
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    monkeypatch.delenv(tiers.HEADED_TIER_DISABLE_ENV, raising=False)
    monkeypatch.setattr(tiers, "_playwright_sessions_summary",
                        lambda *, diagnose=False: [])
    rec = Recorder()
    monkeypatch.setattr(tiers, "_run_pw", rec)
    return rec


# ---- DEFECTS A and D: only a POSITIVELY OPEN record may be adopted ----------

def test_a_closed_record_under_our_own_name_is_not_adopted(
        launch, pool, monkeypatch):
    """A record whose status is `closed` is a corpse, not a session.

    `close()` stops Chrome; the record survives it because our sessions are
    persistent, and it survives a FAILED `delete-data` for the life of the
    daemon. Adopting it marked the session opened with no browser behind it and
    no profile claim, and the early return then repeated that verdict forever.
    """
    session = tiers.PlaywrightSession("headless")
    only_record(monkeypatch, session, "closed")

    session._ensure_open()

    assert launch.opens(), "adopted the corpse: nothing was launched"
    assert len(pool.claimed) == 1, "a fall-through must claim a profile dir"
    assert session._profile_dir == pool.claimed[0][0], "the claim must be ours"
    assert session._opened is True


def test_a_status_cut_mid_write_is_not_adopted(launch, pool, monkeypatch):
    """DEFECT D. A fragment PARSES, which is what makes it worse than a blank.

    A listing cut inside `- status: closed` stores `clo` -- non-blank, so back
    when the adoption path still consulted the all-record completeness guard,
    that guard passed the record through as one it understood. A predicate
    reading "anything but `closed` is live" then called that corpse live and
    adopted it. This is the same cut-MID-VALUE shape already documented for
    `user-data-dir`: a fragment is a real value that simply does not match, so
    nothing downstream can tell it apart from one.

    The guard no longer runs on this path at all -- adoption judges each record
    on its own -- which is exactly why the predicate has to be the one that
    catches this. It tests for `open` rather than for the absence of `closed`,
    so every fragment falls out by construction rather than one at a time.
    """
    session = tiers.PlaywrightSession("headless")
    only_record(monkeypatch, session, "clo", truncated=True)

    session._ensure_open()

    assert launch.opens(), "adopted a truncated corpse: nothing was launched"
    assert len(pool.claimed) == 1, "a fall-through must claim a profile dir"
    assert session._opened is True


def test_a_truncated_open_status_is_not_adopted_either(
        launch, pool, monkeypatch):
    """OVER-REFUSAL ON PURPOSE, and pinned so nobody trims it as redundant.

    A status reading exactly `open` cannot be a fragment while the vocabulary is
    {open, closed} -- so today this arm refuses a record that is genuinely open,
    and costs one extra `open` attempt that would fail loudly with `Browser is
    already in use` if a browser really were there. That is the cheap direction.

    What it buys is the case that is NOT today's: upstream adding a longer
    status such as `opening`, cut down to exactly `open`. Then the fragment is
    byte-for-byte a complete value and the equality test alone cannot see it.
    The flag is already on the record, so refusing here costs nothing and closes
    the class rather than the instance.
    """
    session = tiers.PlaywrightSession("headless")
    only_record(monkeypatch, session, "open", truncated=True)

    session._ensure_open()

    assert launch.opens(), "a truncated record is not evidence of a browser"
    assert len(pool.claimed) == 1
    assert session._opened is True


def test_an_open_record_under_our_own_name_is_still_adopted(
        launch, pool, monkeypatch):
    """THE CONTROL, and the half that gives the tests above their meaning.

    Adoption is not the defect -- adopting a session that is not there is. A
    complete record reported `open` has a profile claim behind it, so re-opening
    it or claiming a second dir for it would both be wrong.

    This arm builds the case where the claim is NOT ours: the session object is
    fresh, so nothing is held and `_profile_dir` must stay None. The other
    reachable case -- adopting after this object's own retained attempt becomes
    visible, where the claim IS ours and is kept -- is not exercised here.
    """
    session = tiers.PlaywrightSession("headless")
    only_record(monkeypatch, session, "open")

    session._ensure_open()

    assert session._opened is True
    assert launch.opens() == [], "an open session must not be re-opened"
    assert pool.claimed == [], "claimed a dir for a session we did not open"
    assert session._profile_dir is None


# ---- DEFECT B: a retained claim is reused, not abandoned ---------------------

def test_two_indeterminate_launch_failures_claim_exactly_one_dir(launch, pool):
    """The retry runs on the dir we are still holding the lock for.

    The claim is deliberately KEPT when `_run_pw` RAISES, because an OSError out
    of `communicate()` arrives after the daemon may already be up. Claiming a
    second dir on the next attempt overwrote `_profile_fd`, so the first stayed
    locked with nothing pointing at it until the process exited -- out of a pool
    of ten shared with every other process on the box.

    Reusing it is sound precisely here: nobody else can have taken the dir,
    because we are still holding the OS lock on it.
    """
    launch.open_results = [PermissionError("cannot spawn playwright-cli"),
                           PermissionError("cannot spawn playwright-cli")]
    session = tiers.PlaywrightSession("headless")

    with pytest.raises(PermissionError):
        session._ensure_open()
    first_dir, first_fd = pool.claimed[0]
    assert session._profile_dir == first_dir, \
        "precondition: an indeterminate failure must KEEP its claim"

    with pytest.raises(PermissionError):
        session._ensure_open()

    assert len(pool.claimed) == 1, \
        "the retry took a second dir and stranded the first"
    assert session._profile_dir == first_dir, "the retry must reuse our dir"
    assert session._profile_fd == first_fd, \
        "the held descriptor must survive the retry, or the dir is unreleasable"
    assert pool.released == [], "released a dir a browser may still hold"
    assert len(launch.opens()) == 2, "precondition: both attempts really ran"


def test_a_reported_launch_failure_releases_and_the_next_claim_is_fresh(
        launch, pool):
    """THE CONTROL, twice over: for the reuse above, and for `claimed_here`.

    On a claim taken in THIS invocation -- which is the case built here -- an
    ORDINARY CLI FAILURE, the rc=1 scripted below, is positive evidence that no
    browser exists, so `launched` goes back to False and the handler releases.
    There is then nothing to reuse, and the next attempt must claim afresh
    rather than relaunch on a dir whose lock it gave away. The scoping is the
    point: the identical rc on a REUSED claim proves nothing at all.

    NOT EVERY non-zero rc CARRIES THAT EVIDENCE, and the arm under test reads
    `0 <= rc < 256` -- an ORDINARY exit status -- rather than any non-zero one.
    Two kinds of rc reach it having reported nothing. `rc == -1` is the ABSENCE
    of an exit status and HOLDS the claim;
    `test_a_launch_timeout_keeps_the_claim_a_daemon_may_still_hold` is that arm.
    And on win32 an abnormal termination arrives POSITIVE (3221225477 for an
    access violation, 3221225786 for a console kill), which the old `rc != -1`
    test admitted and released on. It no longer does:
    `test_an_access_violation_holds_the_claim_a_daemon_may_be_using` and its
    console-kill sibling below pin that, and the pair at 255/256 pins where the
    ordinary range ends. THIS test is what stops that fix going too far -- an
    rc the CLI really reported must still release.

    Read this against `test_a_reused_claim_survives_a_reported_failure`, which
    is the same rc != 0 landing on a REUSED claim and must NOT release. Without
    this arm, gating the release on `claimed_here` would look indistinguishable
    from never releasing at all.
    """
    launch.open_results = [(1, "", "Error: profile dir is locked"), (0, "", "")]
    session = tiers.PlaywrightSession("headless")

    with pytest.raises(RuntimeError):
        session._ensure_open()
    assert pool.released == [pool.claimed[0]], \
        "an ordinary CLI failure on a claim taken HERE means no browser"
    assert session._profile_dir is None and session._profile_fd is None

    session._ensure_open()

    assert len(pool.claimed) == 2, "nothing was held, so a fresh claim is due"
    assert session._profile_dir == pool.claimed[1][0]
    assert session._opened is True


def test_a_launch_timeout_keeps_the_claim_a_daemon_may_still_hold(launch, pool):
    """rc == -1 is not a report from `open`; it is the ABSENCE of one.

    `subprocess.run`'s timeout kills the CLI child and `_run_pw` returns -1 with
    no exit status in hand. The kill reaps THAT process only, and the playwright
    daemon it started is a GRANDCHILD that survives it, so a browser can still
    be coming up on this dir. THIS PROCESS STILL HOLDS THE LOCK at that moment
    -- the assertions below check exactly that -- so the harm is not losing the
    lock, it is giving it away: releasing hands the next claimant a directory
    Chromium may have open. That is the over-permission direction and the exact
    failure `claimed_here` was introduced to prevent.
    (`_try_claim_profile_dir`'s second guard covers the same shape one process
    later, where the daemon outlives its Python parent and no lock of ours
    remains at all. This is not that case; naming that function correctly here
    is a round-1 review correction.)

    THE CLAIM IS FRESH HERE, and that is the whole point of the arm. On a
    REUSED claim `claimed_here` gates the release off whatever `launched` says,
    so this is the only case in which the flag decides anything at all.

    Its control is `test_a_reported_launch_failure_releases_and_the_next_claim_
    is_fresh` above: same fresh claim, same raise, but a rc the process really
    reported, and that one must still release. A fix that stopped releasing
    altogether would pass this test and fail that one.
    """
    launch.open_results = [
        (-1, "", f"playwright-cli timeout after {tiers.PLAYWRIGHT_OPEN_TIMEOUT}s"),
    ]
    session = tiers.PlaywrightSession("headless")

    with pytest.raises(RuntimeError):
        session._ensure_open()

    assert len(launch.opens()) == 1, "precondition: the launch really ran"
    assert len(pool.claimed) == 1, "precondition: the claim was taken HERE"
    assert pool.released == [], \
        "released a dir a timed-out launch's daemon may still be holding"
    assert session._profile_dir == pool.claimed[0][0], \
        "the claim must stay ours, or the retry cannot reuse it"
    assert session._profile_fd == pool.claimed[0][1], \
        "and the held descriptor with it, or the dir is unreleasable"


# ---- M-LAUNCH-ABNORMAL-EXIT-RELEASES: an abnormal exit reported nothing -----
#
# On win32 `subprocess.run` hands back the UNSIGNED exit dword, so a process that
# died abnormally arrives here as a large POSITIVE number rather than as a signal.
# The old `rc != -1` test read every one of those as "the CLI reported a failure"
# and released the profile claim -- possibly out from under a daemon the dying CLI
# child had already started, which is the over-permission direction and costs the
# next claimant `Browser is already in use`.
#
# THE CLAIM IS FRESH IN ALL FOUR ARMS. On a REUSED claim `claimed_here` gates the
# release off whatever `launched` says, so these would pass for the wrong reason.


def test_an_access_violation_holds_the_claim_a_daemon_may_be_using(launch, pool):
    """0xC0000005 is a crash, not a report, and a crash can land after the spawn.

    MEASURED on win32 with controls that took effect (dword 3 -> rc 3, dword 1 ->
    rc 1): Python returns the unsigned dword, so an access violation is 3221225477.
    Nothing at this call site can tell which side of the spawn it fell on, so the
    claim is kept -- one pool slot the kernel drops at process exit, against handing
    a live Chromium's directory away.

    Its control is `test_a_reported_launch_failure_releases_and_the_next_claim_is_
    fresh`: same fresh claim, same raise, ordinary rc, and that one must still
    release. A fix that simply stopped releasing would pass this and fail that.
    """
    launch.open_results = [(3221225477, "", "")]
    session = tiers.PlaywrightSession("headless")

    with pytest.raises(RuntimeError):
        session._ensure_open()

    assert len(launch.opens()) == 1, "precondition: the launch really ran"
    assert len(pool.claimed) == 1, "precondition: the claim was taken HERE"
    assert pool.released == [], \
        "released a dir a crashed launch's daemon may already be holding"
    assert session._profile_dir == pool.claimed[0][0], "the claim must stay ours"
    assert session._profile_fd == pool.claimed[0][1], \
        "and the held descriptor with it, or the dir is unreleasable"


def test_a_console_kill_holds_the_claim_too(launch, pool):
    """CTRL_C_EXIT (0xC000013A) is the second abnormal code, and the same case.

    DERIVED, NOT MEASURED, and the sibling above says so about its own value: no
    abnormal termination of playwright-cli has ever been observed in this project.
    3221225786 is the NTSTATUS constant read through the same control that showed
    `subprocess.run` returns the unsigned dword. `backlog.md` records both codes as
    an author report whose probe was not retained. What the arm pins is the
    PREDICATE's treatment of such a value, which does not depend on the derivation
    being right.

    It is here as a SEPARATE arm rather than a parametrize case because the two
    codes reach the process by different routes -- a fault raised by the program
    against a kill delivered by the console -- and a repair that happened to cover
    only the fault range would pass the arm above and fail this one.
    """
    launch.open_results = [(3221225786, "", "")]
    session = tiers.PlaywrightSession("headless")

    with pytest.raises(RuntimeError):
        session._ensure_open()

    assert len(launch.opens()) == 1, "precondition: the launch really ran"
    assert len(pool.claimed) == 1, "precondition: the claim was taken HERE"
    assert pool.released == [], "released a dir a killed launch's daemon may hold"
    assert session._profile_dir == pool.claimed[0][0], "the claim must stay ours"
    assert session._profile_fd == pool.claimed[0][1], \
        "and the held descriptor with it, or the dir is unreleasable"


def test_the_top_of_the_ordinary_range_still_releases(launch, pool):
    """255 is an exit status a process can really report, so it must RELEASE.

    THE BOUNDARY CONTROL, and the one that stops the fix becoming "never release".
    255 is the largest value a POSIX waitstatus can carry and is far above anything
    this CLI emits (measured: 0 or 1), so admitting it costs nothing -- while a
    bound drawn too tight silently converts ordinary failures into held slots.
    """
    launch.open_results = [(255, "", "Error: profile dir is locked")]
    session = tiers.PlaywrightSession("headless")

    with pytest.raises(RuntimeError):
        session._ensure_open()

    assert len(launch.opens()) == 1, "precondition: the launch really ran"
    assert pool.released == [pool.claimed[0]], \
        "an ordinary exit status on a claim taken HERE means no browser"
    assert session._profile_dir is None and session._profile_fd is None


def test_the_first_value_above_the_ordinary_range_holds(launch, pool):
    """256 is the other side of the same boundary, and it must HOLD.

    Paired with the 255 arm this fixes WHERE the range ends. Either arm alone is
    satisfied by a bound one step out; together they are not. 256 is not a code
    anything has been observed to return -- it is the boundary itself, which is
    the only value that can pin it.
    """
    launch.open_results = [(256, "", "")]
    session = tiers.PlaywrightSession("headless")

    with pytest.raises(RuntimeError):
        session._ensure_open()

    assert len(launch.opens()) == 1, "precondition: the launch really ran"
    assert len(pool.claimed) == 1, "precondition: the claim was taken HERE"
    assert pool.released == [], "released on a value outside the ordinary range"
    assert session._profile_dir == pool.claimed[0][0], "the claim must stay ours"
    assert session._profile_fd == pool.claimed[0][1], \
        "and the held descriptor with it, or the dir is unreleasable"




def test_run_pw_suppresses_the_update_notifier(real_run_pw):
    """The suppression variable must reach the subprocess, or the range above lies.

    WHAT THIS DOES AND DOES NOT COVER, because the green is easy to overread. It
    pins that `_run_pw` puts the key in the environment it hands `subprocess.run`.
    It does NOT pin the property that makes the range exact -- that the CLI still
    HONOURS the variable -- because that lives in the CLI's own entry script and
    nothing here may execute it. If a future @playwright/cli moves that gate, this
    stays green while `0 <= rc < 256` quietly goes back to being wrong. The comment
    at `_run_pw` records that exposure; this arm cannot.
    """
    run_pw, seen = real_run_pw
    rc, _out, _err = run_pw(["list"], timeout=10)

    assert rc == 0, "precondition: the faked spawn was actually reached"
    assert "env" in seen, "no environment was passed, so the update check is live"
    assert seen["env"].get("NO_UPDATE_NOTIFIER") == "1", \
        "the update check is not suppressed: a successful run can return 0xC0000409"


def test_run_pw_passes_the_rest_of_the_environment_through(real_run_pw, monkeypatch):
    """THE CONTROL THAT MUST NOT FIRE: suppressing the check is not a reset.

    Replacing the environment rather than extending it would strip PATH, TEMP and
    every variable this skill reads for the profile root and the Chrome override --
    a far larger failure than the one being fixed, and one no other test here would
    catch, since every arm above stubs `_run_pw` out entirely.

    ONE NON-EQUIVALENCE IS REAL AND IS NOT A DEFECT, and it is asserted below in
    the form that can actually see it. Before this change `subprocess.run` got no
    `env` at all and the child inherited the parent block verbatim; it now gets an
    explicit dict built from `os.environ`, which UPPER-CASES keys on Windows. So a
    mixed-case variable reaches the child under an uppercased name -- measured, 12
    of 131 on this box, none lost and no value changed. It is harmless because
    Win32 and Node both look environment variables up case-insensitively. The
    assertion therefore checks that no variable is LOST, case-insensitively, rather
    than that its exact spelling survived: asserting the spelling would fail, and
    asserting only an already-uppercase name -- which an earlier draft did -- cannot
    see this class at all.
    """
    run_pw, seen = real_run_pw
    monkeypatch.setenv("AshWebfetchMixedCanary", "present")
    run_pw(["list"], timeout=10)

    assert "env" in seen, "no environment was passed, so nothing below is meaningful"
    env = seen["env"]
    assert "PATH" in env, "PATH was dropped; playwright-cli would not resolve"
    assert any(k.upper() == "WEBFETCHMIXEDCANARY" and v == "present"
               for k, v in env.items()), \
        "a caller-set variable was lost, not merely renamed"


# ---- The CLI's OTHER notice: silenced by the working directory, not by env ---
#
# `checkInstalledSkills()` writes a framed box to stderr when a deployed skill
# file differs from the one the CLI ships. It is gated by NOTHING -- no
# environment variable reaches it -- and it runs BEFORE the update check the two
# arms above cover. Eight call sites in `tiers.py` build the error they report
# from these streams PREFERRING stderr, so that box would BECOME the reported
# error while the real diagnosis sat unread on stdout.
#
# The only lever is the working directory: the check joins the two paths it
# compares onto `process.cwd()` and skips any target that is absent.


def test_run_pw_pins_a_working_directory_the_skill_check_cannot_fire_in(
        real_run_pw, monkeypatch, tmp_path):
    """A cwd must reach the spawn, and it must hold neither skill tree.

    WHAT THIS COVERS AND WHAT IT CANNOT, because the green is easy to overread.
    It pins that `_run_pw` hands `subprocess.run` a working directory, that the
    directory is the one `state.state_dir()` names, and that the two paths the
    CLI would compare are absent from it. It does NOT pin that the CLI still
    reads `process.cwd()` to find them -- that lives in the CLI's own
    `skillCheck.js` and nothing here may execute it. If a future @playwright/cli
    resolves those paths some other way, this stays green while the box comes
    back. The comment at `_run_pw` records that exposure; this arm cannot.

    `state.state_dir` is redirected at the module the callee reads it through,
    so the operator's real `~/.local/share/webfetch` is never touched and
    never created. The sibling arm below covers the create-if-missing property
    with a fabricated home instead.
    """
    run_pw, seen = real_run_pw
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)

    rc, _out, _err = run_pw(["list"], timeout=10)

    assert rc == 0, "precondition: the faked spawn was actually reached"
    assert "cwd" in seen, \
        "no working directory was passed: the skill-check box can fire"
    assert seen["cwd"] == str(tmp_path), \
        "the working directory is not the one state.state_dir() names"
    cwd = Path(seen["cwd"])
    for tree in (".claude", ".agents"):
        probe = cwd / tree / "skills" / "playwright-cli" / "SKILL.md"
        assert not probe.exists(), \
            "%s is present, so the CLI would compare it and may print the box" % probe


def test_run_pw_hands_over_a_directory_that_exists(
        real_run_pw, monkeypatch, tmp_path):
    """A MISSING cwd would be reported as a missing BINARY -- a false diagnosis.

    `subprocess.run` raises `FileNotFoundError` for a cwd that does not exist,
    and this function's own handler turns that into
    `playwright-cli not found on PATH`. That names the wrong cause entirely, and
    the ratified design's rule 5 counts a false instruction as worse than none.
    What prevents it is that `state.state_dir()` CREATES the directory, so this
    arm drives the REAL one with the home directory fabricated -- which is also
    what keeps the operator's own state dir out of the test run (a suite that
    reached it once created 49 KB under it).
    """
    run_pw, seen = real_run_pw
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    rc, _out, _err = run_pw(["list"], timeout=10)

    assert rc == 0, "precondition: the faked spawn was actually reached"
    assert "cwd" in seen, "no working directory was passed"
    cwd = Path(seen["cwd"])
    assert cwd.is_dir(), \
        "the cwd does not exist: the spawn raises and is reported as a missing binary"
    assert tmp_path in cwd.parents, \
        "the fabricated home was not honoured, so this ran against the real state dir"


def test_a_state_directory_failure_is_named_and_does_not_hold_a_claim(
        real_run_pw, monkeypatch):
    """The cwd is resolved BEFORE the try, so its failure says what it is.

    Two wrong outcomes are being excluded, and they pull in opposite directions.
    Resolved INSIDE the try, a `FileNotFoundError` from the directory would be
    caught by the arm that reports `playwright-cli not found on PATH` -- naming
    a missing BINARY for a directory problem, which the ratified design counts
    as worse than saying nothing. And any other `OSError` would propagate,
    which `_ensure_open` reads as an INDETERMINATE launch and answers by
    HOLDING a profile claim, one of ten shared across processes, for a browser
    that was never spawned -- this failure happens before `subprocess.run` is
    reached at all.

    `argv` absent from the recorder is what proves the second half: it is set by
    the faked `subprocess.run`, so its absence means nothing was launched.
    Found by cross-family review; the first draft of this change had the
    resolution inside the try.
    """
    run_pw, seen = real_run_pw

    def _unusable():
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(state, "state_dir", _unusable)

    rc, out, err = run_pw(["list"], timeout=10)

    assert "argv" not in seen, \
        "the spawn was reached; this failure happens before any child exists"
    assert 0 <= rc < 256, (
        "rc %r is outside the ordinary range, so _ensure_open reads an "
        "indeterminate launch and HOLDS a claim for a browser never spawned" % rc)
    assert "working directory" in err, \
        "the failure does not name the working directory: %r" % err
    assert "not found on PATH" not in err, \
        "a directory failure is being reported as a missing binary: %r" % err
    assert "PermissionError" in err, \
        "the underlying cause is not carried into the message: %r" % err
    assert out == "", "stdout should be empty when nothing ran: %r" % out


# Both arms below drive a SUBPROCESS rather than `importlib.reload`. The values
# under test are computed at import, and reloading `tiers` in-process would
# rebind `tiers._run_pw` to the real function -- destroying conftest's only
# browser-launch tripwire for the rest of that test. That is the exact hazard
# this suite has already been bitten by once.


def _import_tiers_and_print(expr, cwd, extra_env):
    """Import `tiers` in a clean child and print one expression from it."""
    import os as _os
    import subprocess as _sp
    import sys as _sys
    skill = str(Path(__file__).resolve().parent.parent)
    env = {**_os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": skill}
    env.update(extra_env)
    r = _sp.run([_sys.executable, "-c", "import tiers; print(%s)" % expr],
                cwd=str(cwd), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, \
        "the child failed, so nothing below is meaningful:\n%s%s" % (r.stdout, r.stderr)
    return r.stdout.strip()


def test_a_relative_profile_override_is_resolved_at_import(tmp_path):
    """A relative `--profile` would mean a different directory after the pin.

    `WEBFETCH_PLAYWRIGHT_PROFILE` is read straight out of the environment,
    so a relative value stays relative and `--profile=` is then resolved by the
    CHILD against its working directory -- which this change moves. Resolving it
    at import, against the cwd the caller started in, is what keeps the value
    meaning what it meant before. Found by cross-family review; my own claim
    that nothing we pass is cwd-relative was false exactly here.
    """
    got = _import_tiers_and_print(
        "tiers.PROFILE_BASE", tmp_path,
        {"WEBFETCH_PLAYWRIGHT_PROFILE": "relative-profiles/pw"})

    assert Path(got).is_absolute(), \
        "a relative override survived import: %r" % got
    assert str(tmp_path.resolve()) in str(Path(got).resolve()), (
        "it was resolved against something other than the import-time cwd, so "
        "the value no longer means what it meant before: %r" % got)


def _plant_cli_shim(directory):
    """Create a `playwright-cli` the platform's own lookup will actually find.

    POSIX IS A REAL CODE PATH HERE, NOT A ROADMAP CASE, and the first version of
    the arm below planted `playwright-cli.CMD` unconditionally. Windows finds
    that through PATHEXT; POSIX applies no PATHEXT at all and additionally
    requires the execute bit, so there the lookup found nothing, the precondition
    assertion fired, and the arm FAILED instead of testing the guard.
    Cross-family review round 3 rated that must-fix and was right.
    """
    import sys as _sys
    if _sys.platform == "win32":
        shim = directory / "playwright-cli.CMD"
        shim.write_text("@echo this is never executed\n", encoding="utf-8")
    else:
        shim = directory / "playwright-cli"
        shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        shim.chmod(0o755)
    return shim


def test_an_empty_path_component_is_a_SUCCESSFUL_lookup_and_must_be_resolved(
        tmp_path):
    """A successful lookup can be separator-free, so a separator test is wrong.

    An EMPTY PATH ENTRY means "the current directory". `shutil.which` then
    returns `os.path.join("", name)` -- a bare name carrying no separator at
    all, which is nonetheless a cwd-relative RESULT rather than the fallback.
    The first guard tested for a separator and left this case bare, so the
    pinned working directory would have moved it. What separates the two is
    `which` returning `None`, not the shape of the string, which is why the
    guard now keeps that `None` instead of collapsing it with `or`.

    HOW TO BUILD THE CASE, measured, because the obvious construction does not
    work: `PATH=""` is NOT an empty entry -- `shutil.which` treats an empty
    PATH as unset and falls back to `os.defpath`, returning `None`. What
    produces one is a LEADING or TRAILING separator. Measured on 3.12.3 with a
    shim in the cwd: `PATH=""` -> None, `PATH=";<dir>"` -> `playwright-cli.CMD`
    (separator-free), `PATH="."` -> `.\\playwright-cli.CMD` (separator
    present). An earlier version of this arm used `PATH=""` and failed against
    the correct guard, which is the reverse of a vacuous test and just as
    useless.

    The sibling arm below cannot see this case: putting `.` on the path yields
    a result that DOES carry a separator. Cross-family review round 3 found it.
    """
    import os as _os

    _plant_cli_shim(tmp_path)
    other = tmp_path / "nowhere"
    other.mkdir()

    found = _import_tiers_and_print(
        "tiers.PLAYWRIGHT_BIN", tmp_path, {"PATH": _os.pathsep + str(other)})

    assert found != "playwright-cli", (
        "a successful lookup was left bare, so it is indistinguishable from the "
        "fallback and the pinned cwd moves it: %r" % found)
    assert Path(found).is_absolute(), \
        "a successful lookup survived import un-absolutised: %r" % found
    assert Path(found).resolve().parent == tmp_path.resolve(), (
        "precondition: the empty PATH component must have resolved to the shim "
        "planted in the cwd, not to something else: %r" % found)


def test_a_relative_executable_is_resolved_but_a_bare_name_is_not(tmp_path):
    """`shutil.which` can hand back a RELATIVE path, and a bare name must survive.

    THE PRECONDITION IS THE WHOLE DIFFICULTY, and an earlier version of this arm
    did not establish it. MEASURED on CPython 3.12.3: with an ordinary inherited
    PATH, `which("playwright-cli")` returns the REAL absolute binary
    (`...\\npm\\playwright-cli.CMD`) and never looks at a shim beside the
    process -- `_win_path_needs_curdir` gates the current-directory entry rather
    than always prepending it. So that version asserted `is_absolute()` on a
    value that was absolute with or without the guard, and a mutation reverting
    the guard SURVIVED it. What actually produces a relative result is `.` being
    on the search path, which is why this arm puts it there: measured,
    `which(path='.' + empty)` returns `.\\playwright-cli.CMD`.

    The bare-name half is the opposite requirement. It must NOT be made
    absolute, because `<cwd>/playwright-cli` names a file that does not exist
    and would destroy the PATH lookup the fallback exists for.
    """
    import os as _os

    _plant_cli_shim(tmp_path)
    empty = tmp_path / "nowhere"
    empty.mkdir()

    found = _import_tiers_and_print(
        "tiers.PLAYWRIGHT_BIN", tmp_path,
        {"PATH": _os.pathsep.join([_os.curdir, str(empty)])})

    assert Path(found).resolve().parent == tmp_path.resolve(), (
        "precondition: the PLANTED shim must be what was resolved, or this arm "
        "is measuring the real binary on PATH and proves nothing: %r" % found)
    assert Path(found).is_absolute(), \
        "a which()-resolved relative path survived import: %r" % found

    bare = _import_tiers_and_print(
        "tiers.PLAYWRIGHT_BIN", empty, {"PATH": str(empty)})

    assert bare == "playwright-cli", (
        "the bare-name fallback was rewritten to %r, which names a file that "
        "does not exist and defeats the PATH lookup it exists for" % bare)


def test_a_spawn_time_directory_failure_propagates_and_that_is_accepted(
        real_run_pw, monkeypatch, tmp_path):
    """THE OTHER HALF OF THE RESIDUAL, pinned so it cannot be "fixed" silently.

    The working directory is resolved before the `try`, but it is CONSUMED
    inside it, so the spawn can still fail on the directory -- permissions
    revoked, or a path component replaced by a file, between two adjacent
    statements. `PermissionError` and `NotADirectoryError` are not caught here,
    so they PROPAGATE, and `_ensure_open` reads a raised `OSError` as an
    indeterminate launch and HOLDS a profile claim -- for a browser that cannot
    exist, since `subprocess.run` raising means no child was ever created.

    THAT IS ACCEPTED, NOT OVERLOOKED. It is the same bounded over-refusal the
    design already documents for a crash before the daemon started: one slot of
    ten, released at process exit. Converting it would mean catching `OSError`
    around the spawn, which is exactly what `_run_pw_cleanup` exists NOT to do
    on the launch path -- there the raise is what keeps a claim under a browser
    that may be live.

    So this arm pins the CURRENT behaviour deliberately. If someone later wraps
    the spawn to convert these, the claim semantics change and this goes red,
    which is the point. Cross-family review round 2 raised the residual as
    larger than my first disclosure, and it was right.
    """
    run_pw, _seen = real_run_pw
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)

    def _cwd_unusable_at_spawn(argv, **kwargs):
        assert "cwd" in kwargs, "precondition: the spawn was handed a cwd at all"
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(tiers.subprocess, "run", _cwd_unusable_at_spawn)

    with pytest.raises(PermissionError):
        run_pw(["list"], timeout=10)


def test_the_launch_arguments_carry_no_relative_path(launch, pool):
    """THE CONTROL FOR PINNING cwd: nothing we send may depend on inheriting it.

    Pinning the working directory is safe only while every path we hand the CLI
    is absolute. `--profile` comes from PROFILE_BASE, built from
    the public home cache, and `--config` is that path plus a suffix, so both
    are absolute today. If either is ever made relative, the pinned directory
    silently changes what it names -- and no other arm in this suite would see
    it, because they all assert on the claim rather than on the argv.
    """
    session = tiers.PlaywrightSession("headless")

    session._ensure_open()

    opens = launch.opens()
    assert opens, "precondition: a launch was attempted"
    checked = 0
    for arg in opens[0]:
        for flag in ("--profile=", "--config="):
            if arg.startswith(flag):
                checked += 1
                assert Path(arg[len(flag):]).is_absolute(), \
                    "%s is relative, so the pinned cwd changes what it names" % arg
    assert checked == 2, \
        "expected a --profile and a --config to check, saw %d" % checked


# ---- DEFECT C: a reused claim is never released inside _ensure_open ---------

def test_a_reused_claim_survives_a_reported_failure(launch, pool):
    """`launched` describes THIS invocation, so it cannot clear an older browser.

    The sequence the reuse made reachable: call 1 raises with `launched` True
    and KEEPS dir A, because a browser may be live on it; call 2 reuses A and
    `open` returns rc != 0. `launched` goes back to False on the reasoning that
    a reported failure proves no browser exists -- sound for an ORDINARY CLI
    failure on a claim taken in that invocation, and INVERTED on a reused one,
    where `Browser is already in use`
    is exactly the rc != 0 a live browser on that dir produces. The handler then
    released A out from under call 1's browser, allowing a second claimant to
    acquire a directory whose browser might still be alive.

    `claimed_here` gates the release, so a reused claim is not released here at
    all. `close()` cannot release it either -- it returns early while `_opened`
    is False -- so it is held until a later `_ensure_open` succeeds or adopts,
    and otherwise until the kernel drops it at process exit. That residual is
    deliberate; releasing would need evidence no browser remains, which is what
    this path cannot get.
    """
    launch.open_results = [PermissionError("cannot spawn playwright-cli"),
                           (1, "", "Error: Browser is already in use")]
    session = tiers.PlaywrightSession("headless")

    with pytest.raises(PermissionError):
        session._ensure_open()
    first_dir, first_fd = pool.claimed[0]
    assert session._profile_dir == first_dir, \
        "precondition: an indeterminate failure must KEEP its claim"

    with pytest.raises(RuntimeError):
        session._ensure_open()

    assert pool.released == [], \
        "released a reused claim: call 1's browser may still be on that dir"
    assert session._profile_dir == first_dir, "the claim must stay ours"
    assert session._profile_fd == first_fd, \
        "the held descriptor must stay ours, or the dir can never be released"
    assert len(pool.claimed) == 1, "and no second dir may be taken either"


# ---- the cleanup callers keep the old, unfiltered view ----------------------

def test_the_cleanup_callers_still_see_closed_sessions(monkeypatch):
    """The cleanup callers still see closed sessions."""
    listing = [session_record("webfetch-headless-4242", "open"),
               session_record("webfetch-headed-4242", "closed")]
    monkeypatch.setattr(tiers, "_playwright_sessions_summary",
                        lambda *, diagnose=False: listing)

    assert tiers._list_sessions() == ["webfetch-headless-4242",
                                      "webfetch-headed-4242"]
    assert tiers._list_sessions(open_only=True) == ["webfetch-headless-4242"]


# ---- DEFECT E: the destructive path refuses a detectably truncated listing ---
#
# These three feed RAW TEXT through `_run_pw`, not a hand-built summary. The
# whole predicate is "did the output end in a newline", which only the real
# parser can decide -- setting `truncated` by hand would pin the guard while
# proving nothing about what sets its input.

def test_a_listing_cut_mid_line_is_refused(monkeypatch):
    """The `incomplete` guard cannot see this cut, and `reap_zombies` destroys.

    Cut a listing inside `- user-data-dir:` and every status present is still
    non-blank, so the statusless check finds nothing and the listing vouches for
    itself. `reap_zombies` then closes and deletes the names it can see and
    reports a COMPLETE sweep, while whatever followed the cut never arrived --
    the same false completeness the None/[] distinction exists to prevent,
    reached by a route the status check does not cover.

    MID-LINE IS THE WHOLE SCOPE, and the name says so because the boundary is
    load-bearing: the only signal is the missing final newline. A cut landing ON
    a newline -- between records, or between two fields of one record -- is
    byte-for-byte a shorter valid listing and passes every guard. That residual
    is stated in `_list_sessions` and is not fixable without a completeness
    signal upstream does not emit.
    """
    out = ("### Browsers\n"
           "- webfetch-headless-4242:\n"
           "  - status: open\n"
           "- costco:\n"
           "  - status: open\n"
           "  - user-data-dir: C:\\tmp\\webfetch")   # cut mid-value, no newline
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))

    assert tiers._list_sessions() is None


def test_the_same_listing_complete_returns_its_names(monkeypatch):
    """THE CONTROL, and without it the guard above proves nothing.

    Byte-for-byte the listing above plus the terminating newline a finished
    listing always carries. A guard that refused this too would be the
    2026-08-29 outage again, in which `--reap-zombies` exited 1 on a healthy
    machine -- so the refusal has to be visibly narrower than "any listing".
    """
    out = ("### Browsers\n"
           "- webfetch-headless-4242:\n"
           "  - status: open\n"
           "- costco:\n"
           "  - status: open\n"
           "  - user-data-dir: C:\\tmp\\webfetch\n")
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))

    assert tiers._list_sessions() == ["webfetch-headless-4242"]


def test_a_complete_foreign_only_listing_is_still_empty_not_unknown(monkeypatch):
    """A complete foreign only listing is still empty not unknown."""
    out = ("### Browsers\n"
           "- costco:\n"
           "  - status: open\n"
           "- sweep:\n"
           "  - status: open\n")
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))

    assert tiers._list_sessions() == []


# ---- DEFECT F: adoption judges OUR record, not the whole listing ------------

def test_a_foreign_malformed_record_does_not_veto_our_open_one(
        launch, pool, monkeypatch):
    """One broken record belonging to somebody else used to cost us the fetch.

    The all-record completeness guard returns None if ANY record has a blank
    status, and its own docstring justifies that by the callers who CLOSE AND
    DELETE what they are handed. `_ensure_open` does neither. So a single
    malformed FOREIGN record discarded our own complete, untruncated `open`
    record: adoption failed, a dir was claimed, `open` failed against the
    still-live session, and the fetch was lost -- every time, for as long as
    that foreign record stayed malformed.

    Adoption now reads its own record, and no other PARSED record votes.

    SCOPE, because the name is broader than the coverage: this fixture hands
    `_list_sessions` a foreign record that PARSED and merely carried no status.
    A foreign record that stops the PARSER -- a header cut before its colon,
    say -- still collapses the whole listing to None and still vetoes ours.
    That residual is stated on `_list_sessions`' `open_only` branch and is NOT
    covered here or anywhere else in this file.
    """
    session = tiers.PlaywrightSession("headless")
    listing = [session_record("costco", ""),            # foreign, no status
               session_record(session.session_name, "open")]
    monkeypatch.setattr(tiers, "_playwright_sessions_summary",
                        lambda *, diagnose=False: listing)

    assert tiers._list_sessions(open_only=True) == [session.session_name], \
        "our complete record must survive somebody else's broken one"

    session._ensure_open()

    assert session._opened is True
    assert launch.opens() == [], "the session is up: nothing may be launched"
    assert pool.claimed == [], "and no dir may be claimed for it"


def test_our_own_statusless_record_is_still_not_adopted(
        launch, pool, monkeypatch):
    """THE CONTROL: relaxing the guard must not relax the predicate.

    Skipping the all-record guards cannot over-permit, and this is what makes
    that checkable. A record of OURS with no status is not positively open, so
    `_session_is_open` excludes it on its own evidence -- the guard was never
    what kept it out. We fall through, claim, and launch, exactly as we would
    have when the guard discarded the whole listing.
    """
    session = tiers.PlaywrightSession("headless")
    listing = [session_record("costco", "open"),        # foreign, fine
               session_record(session.session_name, "")]   # OURS, no status
    monkeypatch.setattr(tiers, "_playwright_sessions_summary",
                        lambda *, diagnose=False: listing)

    assert tiers._list_sessions(open_only=True) == [], \
        "a statusless record of ours is not evidence of a browser"

    session._ensure_open()

    assert launch.opens(), "not adopted, so it must launch"
    assert len(pool.claimed) == 1, "and claim a dir"
    assert session._opened is True


# ---- DEFECT G: a listing that cannot be RUN answers None, it does not raise --
#
# `_run_pw` catches only TimeoutExpired and FileNotFoundError, so PermissionError
# and every other OSError out of `subprocess.run` reached the four functions
# below -- each of which documents None as its "could not enumerate" answer.
# Nothing here spawns anything: the stub raises in place of the subprocess.

def pw_raising_on_list(exc, passthrough=None):
    """A `_run_pw` that raises for the `list` invocation and only that one.

    Scoped on purpose. A stub that raised for EVERY invocation would fail these
    tests at whichever call came first and prove nothing about which one the
    contract covers -- and `_ensure_open` could never reach its claim.
    """
    def run(args, timeout=None):
        if "list" in args:
            raise exc
        if passthrough is not None:
            return passthrough(args, timeout=timeout)
        return (0, "", "")
    return run


def test_an_unspawnable_list_reads_as_unknown_not_as_a_crash(monkeypatch):
    """The parser is where the contract is honoured, so it is where it starts.

    A PermissionError on the spawn is not a statement about how many sessions
    exist. None is this function's word for that, and it is the word all four
    callers were written against.
    """
    monkeypatch.setattr(tiers, "_run_pw",
                        pw_raising_on_list(PermissionError("denied")))

    assert tiers._playwright_sessions_summary() is None


def test_both_list_sessions_paths_report_unknown_on_an_unspawnable_list(
        monkeypatch):
    """BOTH paths, because they no longer share a body.

    `open_only=True` returns before the guards the default path runs, so a fix
    that only covered one arm would leave the other raising -- and the adoption
    arm is the one on every fetch.
    """
    monkeypatch.setattr(tiers, "_run_pw",
                        pw_raising_on_list(PermissionError("denied")))

    assert tiers._list_sessions() is None
    assert tiers._list_sessions(open_only=True) is None


def test_reap_zombies_reports_unknown_rather_than_crashing(monkeypatch):
    """`--reap-zombies` is the release valve the profile-lock error points at.

    Its documented answer for an unreadable listing is None, and a user reaching
    for it on a wedged box is exactly the person who should not get a traceback.
    """
    monkeypatch.setattr(tiers, "_run_pw",
                        pw_raising_on_list(PermissionError("denied")))

    assert tiers.reap_zombies() is None


def test_startup_reap_does_nothing_on_an_unspawnable_list(monkeypatch):
    """It runs on the way in to a fetch, so raising here kills the whole fetch
    before any tier is tried -- for a listing we merely could not read."""
    calls = []
    monkeypatch.setattr(
        tiers, "_run_pw",
        pw_raising_on_list(PermissionError("denied"),
                           passthrough=lambda args, timeout=None: (
                               calls.append(list(args)), (0, "", ""))[1]))
    monkeypatch.setattr(tiers, "_STARTUP_REAPED", False)

    assert tiers.startup_reap() == 0
    assert calls == [], "must not act on a listing it could not read"


def test_ensure_open_falls_through_and_claims_on_an_unspawnable_list(
        launch, pool, monkeypatch):
    """UNKNOWN is not 'no session', and the fall-through is the right answer.

    We cannot tell whether our session exists, so we claim a dir and launch. If
    it did exist, `open` fails loudly against it -- which beats both crashing
    and silently adopting a session we have no evidence for.

    THE FIXTURE'S PARSER STUB IS PUT BACK FIRST, and that line is the test. The
    `launch` fixture replaces `_playwright_sessions_summary`, so with it in
    place `_run_pw(["list"])` is never reached and this passes without touching
    the catch at all -- which is what it did against the unfixed revision.
    """
    monkeypatch.setattr(tiers, "_playwright_sessions_summary", REAL_SUMMARY)
    monkeypatch.setattr(tiers, "_run_pw",
                        pw_raising_on_list(PermissionError("denied"),
                                           passthrough=launch))

    session = tiers.PlaywrightSession("headless")
    session._ensure_open()

    assert launch.opens(), "unknown must launch, not crash and not adopt"
    assert len(pool.claimed) == 1
    assert session._opened is True


def test_the_same_paths_are_unaffected_when_the_spawn_succeeds(monkeypatch):
    """THE CONTROL, and without it the catch cannot be told from a swallow.

    Same stub shape, raising nothing. Every one of the four still returns its
    ordinary answer over a healthy listing -- so the arms above pin a catch on
    one specific failure rather than a blanket None.
    """
    out = ("### Browsers\n"
           "- webfetch-headless-4242:\n"
           "  - status: open\n")
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, out, ""))
    monkeypatch.setattr(tiers, "_STARTUP_REAPED", False)

    summary = tiers._playwright_sessions_summary()
    assert summary is not None and [s["name"] for s in summary] == [
        "webfetch-headless-4242"]
    assert tiers._list_sessions() == ["webfetch-headless-4242"]
    assert tiers._list_sessions(open_only=True) == ["webfetch-headless-4242"]
    assert tiers.reap_zombies() == (1, 1, 0), "a real sweep, not an unknown"


# ---- DEFECT H: the CLEANUP spawns honour the contract too --------------------
#
# The listing was only one of three `_run_pw` call sites in these functions.
# `close` and `delete-data` spawn as well, and an OSError from either escaped
# after a SUCCESSFUL listing -- so `reap_zombies` never returned its tuple, and
# `startup_reap`, which runs on the fetch path, aborted the fetch over a failed
# cleanup spawn. Two properties have to survive the fix, and they are the point:
# an unconfirmed close must NOT be followed by a delete, and an undeleted record
# must be counted as one.

HEALTHY_LISTING = ("### Browsers\n"
                   "- webfetch-headless-4242:\n"
                   "  - status: open\n")


def pw_raising_on(verb, listing=HEALTHY_LISTING):
    """`_run_pw` that lists successfully and raises on ONE cleanup verb.

    The listing MUST succeed: these arms are about what happens after
    enumeration, and a stub that raised on `list` too would stop at the guard
    already tested above and never reach the loop body.
    """
    def run(args, timeout=None):
        if "list" in args:
            return (0, listing, "")
        if verb in args:
            raise PermissionError("denied")
        return (0, "", "")
    return run


def test_an_unspawnable_close_is_an_unconfirmed_close_not_a_crash(monkeypatch):
    """And the record survives it, which is the half that matters.

    A record may be the ONLY evidence a live Chromium holds a profile dir.
    Deleting after a close we could not confirm erases that evidence -- and
    "could not confirm" is the same verdict whether the CLI refused or never
    ran at all. So the OSError lands in the `failed` bucket and the loop moves
    on, exactly as a non-zero rc does.
    """
    calls = []
    base = pw_raising_on("close")
    monkeypatch.setattr(tiers, "_run_pw",
                        lambda a, timeout=None: (calls.append(list(a)),
                                                 base(a, timeout))[1])

    assert tiers.reap_zombies() == (0, 0, 1), \
        "an unspawnable close is one problem, reported not raised"
    assert not any("delete-data" in c for c in calls), \
        "deleted a record after a close that never happened"


def test_an_unspawnable_delete_counts_as_an_undeleted_record(monkeypatch):
    """An undeleted record is the ORIGINAL OUTAGE'S PRECONDITION.

    The close succeeded, so the session really is closed and counted -- but the
    record is still there, and reporting a clean sweep over it rebuilds the
    152-record condition the command exists to clear. Unspawnable and rc != 0
    are the same fact here: the record was not deleted.
    """
    monkeypatch.setattr(tiers, "_run_pw", pw_raising_on("delete-data"))

    assert tiers.reap_zombies() == (1, 0, 1), \
        "closed one, deleted none, and said so"


def test_startup_reap_survives_an_unspawnable_cleanup_spawn(monkeypatch):
    """It runs on the FETCH path, so raising here costs the whole fetch.

    `startup_reap` is called from `fetch.main` before any tier is tried. An
    OSError on its `close` aborted the fetch outright -- over a cleanup step
    that is entirely optional to it.
    """
    listing = ("### Browsers\n"
               "- webfetch-headless-999999:\n"
               "  - status: closed\n")
    monkeypatch.setattr(tiers, "_run_pw", pw_raising_on("close", listing))
    monkeypatch.setattr(tiers, "_STARTUP_REAPED", False)
    monkeypatch.setattr(tiers, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(tiers, "_pid_state", lambda pid: "dead")

    assert tiers.startup_reap() == 0, "nothing reaped, and no exception"


def test_a_healthy_cleanup_still_closes_and_deletes(monkeypatch):
    """THE CONTROL for all three: the conversion must not swallow real work.

    Same listing, same loop, nothing raising. If `_run_pw_cleanup` turned every
    result into a failure the arms above would still pass and this would not.
    """
    calls = []
    monkeypatch.setattr(
        tiers, "_run_pw",
        lambda a, timeout=None: (calls.append(list(a)),
                                 (0, HEALTHY_LISTING, "") if "list" in a
                                 else (0, "", ""))[1])

    assert tiers.reap_zombies() == (1, 1, 0)
    verbs = [c[-1] for c in calls]
    assert "close" in verbs and "delete-data" in verbs
