"""Reaper ordering: --reap-zombies must sweep the list it was invoked for.

`startup_reap()` ran unconditionally three lines above the `--reap-zombies`
handler, and it CLOSES AND DELETES the orphan records. So the explicit command
swept an already-empty list and printed "reaped 0 ... deleted 0" about a
condition it had just fixed itself -- while that command is the release valve the
profile-lock error message points users at.

Hermetic by construction: `_run_pw` is replaced with an in-memory fake, so no
playwright-cli process is started and no browser can be launched. The DB is a
tmp_path one.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

import pytest

import fetch
import state
import tiers

DEAD_PID = 999997          # not a live pid; startup_reap must judge it dead


@pytest.fixture()
def sessions(monkeypatch, tmp_path):
    """Fake playwright-cli session store. Returns the live dict of session names."""
    store = {}

    def fake_run_pw(args, timeout=None):
        if args == ["list"]:
            if not store:
                return 0, "(no browsers)\n", ""
            body = "".join(
                f"- {n}:\n    - status: {st}\n    - user-data-dir: X:/p\n"
                for n, st in store.items())
            return 0, body, ""
        name = args[0][3:] if args and args[0].startswith("-s=") else ""
        verb = args[1] if len(args) > 1 else ""
        if verb == "close":
            return (0, "", "") if name in store else (1, "", "no such session")
        if verb == "delete-data":
            store.pop(name, None)
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(tiers, "_run_pw", fake_run_pw)
    monkeypatch.setattr(tiers, "_STARTUP_REAPED", False)
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    return store


@pytest.fixture()
def isolated_state(monkeypatch, tmp_path):
    """Redirect the state dir, because `fetch.main()` opens and bootstraps a
    state DB BEFORE it reaches the admin handler. A test that drives `main()`
    without this writes to the operator's own
    `~/.local/share/webfetch/state.db`.

    MEASURED, straddling pair under a fabricated HOME: the three report tests
    below created a 49,152-byte `state.db` under it without this fixture, while
    the five tests above -- which get the same redirect from `sessions` --
    created nothing. The control is what makes the first number mean anything.

    Separate from `sessions` on purpose: these tests replace `reap_zombies`
    outright, so they need no session store and should not depend on one.
    """
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    return tmp_path


def _run(argv):
    sys.argv = ["fetch.py"] + argv
    return fetch.main()


def test_reap_zombies_reports_the_sessions_it_swept(sessions, capsys):
    """The bug: the count printed was 0 because the implicit sweep got there first.

    WHAT THIS PINS IS THE COUNT, not the sentence around it. The wording gained a
    truncation disclaimer when `--reap-zombies` stopped claiming a completeness it
    cannot establish (`M-LISTING-TRUNCATION-SWEEP`), so the literal phrase moved;
    the assertion below still fails if the count regresses to 0, which is the
    defect this test exists for.
    """
    sessions[f"webfetch-headless-{DEAD_PID}"] = "running"
    rc = _run(["--reap-zombies"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "reaped 1 of the 1 webfetch-* session(s)" in err, err
    assert "reaped 0" not in err
    assert sessions == {}


def test_reap_zombies_leaves_foreign_sessions_alone(sessions, capsys):
    """Reap zombies leaves foreign sessions alone."""
    sessions[f"webfetch-headless-{DEAD_PID}"] = "running"
    sessions["costco"] = "open"
    rc = _run(["--reap-zombies"])
    assert rc == 0
    assert list(sessions) == ["costco"]


def test_startup_reap_does_not_run_on_the_admin_path(sessions, monkeypatch):
    """The fix is a guard, not a reorder: the implicit sweep is off that path."""
    called = []
    monkeypatch.setattr(tiers, "startup_reap",
                        lambda: called.append(True) or 0)
    sessions[f"webfetch-headless-{DEAD_PID}"] = "running"
    _run(["--reap-zombies"])
    assert called == [], "startup_reap ran during --reap-zombies"


def test_startup_reap_still_runs_and_reports_on_other_paths(sessions, capsys):
    """The sweep's return value was discarded, so its work was invisible."""
    sessions[f"webfetch-headless-{DEAD_PID}"] = "running"
    rc = _run(["--clear-cache"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "startup reap: closed and deleted 1 orphan" in err, err
    assert sessions == {}


def test_startup_reap_spares_a_session_owned_by_a_live_pid(sessions, capsys):
    """Control. Uses the PARENT pid, not this process's, so the check exercises
    `_pid_alive` rather than the `owner_pid == my_pid` short-circuit."""
    ppid = os.getppid()
    if not tiers._pid_alive(ppid):
        pytest.skip(f"parent pid {ppid} is not live on this box")
    name = f"webfetch-headless-{ppid}"
    sessions[name] = "open"
    _run(["--clear-cache"])
    assert list(sessions) == [name]
    assert "startup reap:" not in capsys.readouterr().err


_SENTINEL = object()

# ---- the --reap-zombies report ----------------------------------------------
#
# Driven through `fetch.main` with `reap_zombies` replaced, so what is asserted
# is the text the OPERATOR sees, not a string that happens to be in the source.

def _run_reap(closed=0, deleted=0, problems=0, result=_SENTINEL):
    import io
    import contextlib
    import fetch
    import tiers as _t

    payload = result if result is not _SENTINEL else (closed, deleted, problems)
    buf = io.StringIO()
    real = _t.reap_zombies
    real_argv = sys.argv
    try:
        _t.reap_zombies = lambda: payload
        sys.argv = ["fetch.py", "--reap-zombies"]
        with contextlib.redirect_stderr(buf):
            rc = fetch.main()
    finally:
        _t.reap_zombies = real
        sys.argv = real_argv
    return buf.getvalue(), rc

def test_a_clean_sweep_does_not_claim_it_swept_everything(isolated_state):
    """`problems == 0` is the SUCCESS path, and it is the one that used to
    overclaim: it printed a bare count and exited 0, which reads as "every
    session is gone". It can only ever mean "nothing failed among the names the
    listing returned", because a listing cut on a record boundary is
    byte-for-byte a shorter valid listing (`M-LISTING-TRUNCATION-SWEEP`).

    THE CONTROL IS THE SECOND ASSERTION. Pinning only the disclaimer would
    pass against a command that had stopped reporting the counts at all, or
    that had started calling every sweep inconclusive -- which is the
    2026-08-29 outage this deliberately does not do. So the numbers must still
    be there and the exit code must still be 0.
    """
    out, rc = _run_reap(closed=3, deleted=3, problems=0)
    assert rc == 0, "a clean sweep still succeeds"
    assert "cannot be shown" in out, (
        "the success path must not claim a completeness it cannot establish")
    assert "reaped 3 of the 3 webfetch-* session(s)" in out and "deleted 3" in out, (
        "the counts must survive -- the fix is the claim, not the reporting")


def test_an_incomplete_sweep_still_fails_loudly(isolated_state):
    """THE OTHER CONTROL. The disclaimer must not soften the real failure path:
    a sweep with problems still exits 1 and still says INCOMPLETE.
    """
    out, rc = _run_reap(closed=1, deleted=1, problems=2)
    assert rc == 1
    assert "reap INCOMPLETE: 2 session(s)" in out


def test_an_unreadable_listing_is_still_distinct_from_a_clean_sweep(isolated_state):
    """`None` means the listing could not be read AT ALL, which is a different
    verdict from a listing that was read and may have been short. Both exit 1
    and 0 respectively today; this pins that the two messages stay distinct.
    """
    out, rc = _run_reap(result=None)
    assert rc == 1
    assert "could not list playwright sessions" in out
    assert "cannot be shown" not in out, (
        "the truncation disclaimer belongs to a sweep that RAN")


def test_a_failed_delete_does_not_inflate_the_reported_count(isolated_state):
    """THE REGRESSION FOR THE DENOMINATOR. `problems` is `failed + undeleted`,
    and a session that CLOSED while its record would not DELETE increments
    `closed` AND `undeleted` -- so reconstructing the listing size as
    `closed + problems` reported ONE listed session as "1 of the 2". The exact
    reconstruction is `deleted + problems`, because every listed name takes
    exactly one of three paths: close failed, close+delete succeeded, or close
    succeeded and delete failed.

    THIS STATE IS THE ONE THAT SEPARATES THEM and it is the reason the test
    exists: `deleted=0, problems=1` gives 1 under the correct rule and 2 under
    the wrong one. A clean sweep cannot tell them apart -- with `problems=0`
    both rules read `closed`.
    """
    out, rc = _run_reap(closed=1, deleted=0, problems=1)
    assert rc == 1, "a sweep with problems is still INCOMPLETE"
    assert "reaped 1 of the 1 webfetch-* session(s)" in out, (
        "ONE session was listed, so the denominator is 1. `closed + problems` "
        "read 2 here, which is the defect this pins out.")
    assert " of the 2" not in out, (
        "the negative arm: the double-counted total must not come back")
