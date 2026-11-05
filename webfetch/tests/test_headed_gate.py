"""Headed gate behavioral regression tests."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import extract
import fetch
import state
import tiers


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    c = state.open_db()
    yield c
    c.close()


@pytest.fixture()
def attempted(monkeypatch):
    """Record which tiers the cascade actually RUNS, and fail them all.

    Every tier failing is what forces the cascade to walk its whole allowed
    list, which is exactly the sequence under test.
    """
    seen: list[str] = []

    def _fail(tier_name):
        return tiers.TierResult(ok=False, tier=tier_name, error="stub",
                                failure_class="empty_content")

    def http(url, **kw):
        seen.append("http")
        return _fail("http")

    def playwright(url, *, mode, **kw):
        seen.append(mode)
        return _fail(mode)

    def copilot(url, **kw):
        seen.append("copilot")
        return _fail("copilot")

    monkeypatch.setattr(tiers, "tier_http", http)
    monkeypatch.setattr(tiers, "tier_playwright", playwright)
    monkeypatch.setattr(tiers, "tier_copilot", copilot)
    return seen


def _locked_copilot_hint(conn):
    state.Blocklist(conn)._upsert("example.com", 3, [], 0, 0.0, "test")
    conn.execute("UPDATE blocklist SET locked=1 WHERE domain='example.com'")


def _run(conn, monkeypatch, *, headed_off=False, **kw):
    """A cascade run. `headed_off` drives the OPT-OUT; the default sets nothing.

    The polarity is the rewrite. This used to take `allow_headed` and set the
    opt-IN variable, so a test that wanted the rung in the list had to ask for
    it -- and could not tell a working default from a rung nobody could reach.
    """
    if headed_off:
        monkeypatch.setenv(tiers.HEADED_TIER_DISABLE_ENV, "1")
    else:
        monkeypatch.delenv(tiers.HEADED_TIER_DISABLE_ENV, raising=False)
    return fetch.cascade_fetch("https://example.com/", conn=conn,
                               no_cache=True, search_path="/stub/search.py", **kw)


# ---- the env predicate -------------------------------------------------------

@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes"])
def test_env_values_that_DISABLE(monkeypatch, value):
    """REWRITTEN 2026-09-04: same value set, opposite variable and answer.

    These five used to be the values that ENABLED an opt-in tier. They are now
    the values that turn an enabled one off.
    """
    monkeypatch.setenv(tiers.HEADED_TIER_DISABLE_ENV, value)
    assert tiers.headed_tier_allowed() is False


@pytest.mark.parametrize("value", ["", "0", "false", "no", "maybe", "on"])
def test_env_values_that_do_not_DISABLE(monkeypatch, value):
    """Anything not explicitly affirmative leaves the tier ON.

    `on` is in this list deliberately: a value that looks affirmative but is
    not in the accepted set must NOT disable the tier by accident. That was the
    reason it was here before the polarity flipped, and it survives it -- an
    accidental match is now an accidental loss of 8% of fetches instead of an
    accidental window, and R1 ranks that first.
    """
    monkeypatch.setenv(tiers.HEADED_TIER_DISABLE_ENV, value)
    assert tiers.headed_tier_allowed() is True


def test_unset_means_ON(monkeypatch):
    """The whole ruling, in one assertion. No default-off, no env gate."""
    monkeypatch.delenv(tiers.HEADED_TIER_DISABLE_ENV, raising=False)
    assert tiers.headed_tier_allowed() is True


@pytest.fixture()
def http_succeeds(monkeypatch):
    """Let tier 0 win, the way it does on roughly 60% of real fetches.

    EVERY OTHER FIXTURE HERE FAILS EVERY TIER, which walks the whole allowed
    list and only ever exercises the exhaustion path. That blind spot is why a
    warning riding on every successful fetch passed 26 tests and had to be
    caught by a live run instead.
    """
    seen = []

    def http(url, **kw):
        seen.append("http")
        return tiers.TierResult(ok=True, tier="http", content="x" * 500,
                                html="<html>" + "x" * 500 + "</html>",
                                status=200, bytes_=500)

    def boom(*a, **kw):
        raise AssertionError("no tier above http should have run")

    monkeypatch.setattr(tiers, "tier_http", http)
    monkeypatch.setattr(tiers, "tier_playwright", boom)
    monkeypatch.setattr(tiers, "tier_copilot", boom)
    return seen


def test_no_warning_when_a_lower_tier_succeeds(conn, monkeypatch, http_succeeds):
    """http won at tier 0, so this run never went near headed. Say nothing.

    The condition used to be "headed sits inside the start-to-max range", which
    is computed BEFORE any tier runs and so cannot know http will succeed. The
    warning therefore rode on almost every successful fetch on every machine --
    the same "teaches its reader to skip the line" failure as round 1's finding
    [3], reached from the opposite side.
    (Found end-to-end against the live cascade, 2026-08-29.)
    """
    env = _run(conn, monkeypatch, headed_off=True)
    assert env["tier"] == "http"
    assert http_succeeds == ["http"]
    assert env["warnings"] == [], \
        "a run that succeeded before reaching headed must not mention it"


# ---- the cascade ------------------------------------------------------------

def test_headed_runs_by_default(conn, monkeypatch, attempted):
    """NOTHING is set in the environment and the rung runs. That is the ruling.

    REPLACES `test_headed_is_skipped_by_default`, which asserted the exact
    opposite and was the file's headline test. Note there is no verdict stub
    here: these tiers fail with no evidence at all, which is a CONTINUE, and a
    CONTINUE reaches headed. Making R1 depend on the precision of captcha
    detection is what the old entry condition did.
    """
    _run(conn, monkeypatch)
    assert "headed" in attempted, attempted


def test_cascade_steps_over_disabled_headed_and_copilot_to_gated_cdp(conn, monkeypatch, attempted):
    """Both missing middle rungs are gaps, not a ceiling on evidence-gated CDP."""
    import safety

    monkeypatch.setattr(safety, "cdp_port_reachable", lambda *a, **k: True)
    monkeypatch.setattr(extract, "escalation_verdict", lambda **kw: "CAPTCHA_CHALLENGE")
    monkeypatch.setattr(tiers, "tier_cdp", lambda *a, **k: (
        attempted.append("cdp") or tiers.TierResult(ok=False, tier="cdp", error="stub")))
    _run(conn, monkeypatch, headed_off=True)
    assert attempted == ["http", "headless", "cdp"]


def test_the_opt_out_is_honoured_and_reversible(conn, monkeypatch, attempted):
    """The switch works in both directions, in one process.

    REPLACES `test_opt_in_restores_headed`, mirror image and same job: it keeps
    the opt-out from being a silent deletion of the tier rather than a switch
    over it, and it keeps the switch from being inert.

    THE DEFAULT ARM RUNS FIRST, and that ordering is load-bearing rather than
    stylistic: a run in which every tier fails records three failures against
    the domain, which PROMOTES it in the blocklist, and the next run in the
    same DB no longer starts at http. Written the other way round the second
    arm's tier list is the promotion's doing and not the variable's.
    """
    import safety

    monkeypatch.setattr(safety, "cdp_port_reachable", lambda *a, **k: True)
    monkeypatch.setattr(extract, "escalation_verdict", lambda **kw: "CAPTCHA_CHALLENGE")
    monkeypatch.setattr(tiers, "tier_cdp", lambda *a, **k: (
        attempted.append("cdp") or tiers.TierResult(ok=False, tier="cdp", error="stub")))
    _run(conn, monkeypatch)
    assert attempted == ["http", "headless", "headed", "cdp"]

    attempted.clear()
    _run(conn, monkeypatch, headed_off=True)
    assert "headed" not in attempted, attempted
    assert "cdp" in attempted, (
        "a GAP, not a ceiling -- evidence-gated cdp stays reachable: "
        f"{attempted}")




def test_a_ceiling_below_headed_excludes_the_rung(conn, monkeypatch, attempted):
    """The caller's ceiling keeps both windowed rungs out of the run."""
    _run(conn, monkeypatch, headed_off=True, max_tier="headless")
    assert attempted == ["http", "headless"]


def test_start_tier_headed_clamps_DOWN_to_headless(conn, monkeypatch, attempted):
    """The caller asked for a rung this machine will not offer. Go DOWN, not up.

    This is the test that let finding [2] through. It used to assert only that
    the cascade "ran something" -- true whether it fell to headless or jumped
    to copilot, and it was jumping. With headed excluded the allowed set is
    [0, 1, 3], so the old `start_idx > max(allowed_idxs)` test compared 2 > 3,
    found it False, and left start_idx pointing at a tier that was not in the
    set at all. Naming the tier is what makes this able to fail.

    IT NEEDS THE OPT-OUT NOW to reach the branch at all -- that branch fires
    only when the MACHINE removed headed -- which is the only thing that
    changed about it.
    (Cross-family review 2026-08-29, finding [2].)
    """
    env = _run(conn, monkeypatch, headed_off=True, start_tier="headed")
    assert "no_runnable_tiers" not in (env.get("error") or "")
    assert attempted == ["headless"], (
        "must fall to the highest allowed tier AT OR BELOW headed, then "
        "continue -- not skip over headless to copilot")


def test_machine_clamp_takes_the_HIGHEST_tier_below(conn, monkeypatch, attempted):
    """`--start-tier headed --raw-required` leaves [0, 1]; it must pick 1, not 0.

    Named for the branch it reaches. It used to claim to cover the
    `min(allowed_idxs)` fallback and did not: both allowed indexes sit BELOW
    the start, so `max(below)` runs and the test passed regardless of whether
    that fallback worked. There is no honest test of the fallback to write --
    it is unreachable, since nothing can exclude http or headless, so `below`
    is never empty. Enumerated across all 20 max_tier x raw_required x cdp
    combinations.
    (Cross-family review 2026-08-29, round 2 finding [5] / round 3 [2].)
    """
    env = _run(conn, monkeypatch, headed_off=True, start_tier="headed",
               raw_required=True)
    assert "no_runnable_tiers" not in (env.get("error") or "")
    assert attempted == ["headless"]


def test_explicit_copilot_refusal_precedes_the_cascade(
        conn, monkeypatch, attempted):
    """Policy rejection happens before any cascade effects."""
    env = _run(conn, monkeypatch, headed_off=True, start_tier="copilot")
    assert attempted == [] and env["error_code"] == "disabled"


def test_a_caller_excluded_tier_may_not_clamp_UP_ONTO_A_RUNG_NOBODY_NAMED(
        conn, monkeypatch, attempted):
    """A locked reserved slot cannot be clamped onto the logged-in browser."""
    import safety
    monkeypatch.setattr(safety, "cdp_port_reachable", lambda *a, **kw: True)
    monkeypatch.setattr(tiers, "tier_cdp",
                        lambda url, **kw: (attempted.append("cdp"),
                                           tiers.TierResult(ok=False, tier="cdp",
                                                            error="stub",
                                                            failure_class="other"))[1])
    _locked_copilot_hint(conn)
    _run(conn, monkeypatch, raw_required=True, max_tier="cdp")
    assert attempted[0] == "headless", (
        "the floor gives way rather than STARTING the cascade on the owner's "
        "logged-in Chrome -- and it gives way to headless, not back to http: "
        f"{attempted}")
    assert "cdp" not in attempted, (
        "and with no captcha diagnosed anywhere, nothing admits cdp from "
        f"below either: {attempted}")


def test_the_clamp_LANDS_on_no_windowed_rung_from_either_direction(
        conn, monkeypatch, attempted):
    """Machine opt-out and a reserved Copilot hint both land on headless."""
    import safety
    _run(conn, monkeypatch, headed_off=True, start_tier="headed")
    machine_case = list(attempted)

    attempted.clear()
    monkeypatch.setattr(safety, "cdp_port_reachable", lambda *a, **kw: True)
    monkeypatch.setattr(tiers, "tier_cdp",
                        lambda url, **kw: (attempted.append("cdp"),
                                           tiers.TierResult(ok=False, tier="cdp",
                                                            error="stub",
                                                            failure_class="other"))[1])
    # The DEFAULT environment is what makes this input discriminating: both
    # windowed rungs are then in `allowed`, one BELOW the hint and one ABOVE
    # it, so a landing rule that consults either direction without excluding
    # them lands on one of the two.
    _locked_copilot_hint(conn)
    _run(conn, monkeypatch, raw_required=True, max_tier="cdp")
    caller_case = list(attempted)

    assert machine_case[0] == "headless", machine_case
    assert caller_case[0] == "headless", caller_case
    assert "cdp" not in caller_case, caller_case


def test_nothing_above_falls_back_rather_than_failing(conn, monkeypatch, attempted):
    """A persisted reserved hint still tries the highest allowed unwindowed rung."""
    _locked_copilot_hint(conn)
    env = _run(conn, monkeypatch, raw_required=True)
    assert "no_runnable_tiers" not in (env.get("error") or "")
    assert attempted[0] == "headless", attempted


@pytest.fixture()
def headless_succeeds(monkeypatch):
    """http fails, headless wins. The case the clamp lands a caller in."""
    seen = []

    def http(url, **kw):
        seen.append("http")
        return tiers.TierResult(ok=False, tier="http", error="stub",
                                failure_class="empty_content")

    def playwright(url, *, mode, **kw):
        seen.append(mode)
        return tiers.TierResult(ok=True, tier=mode, content="x" * 500,
                                html="<html>" + "x" * 500 + "</html>",
                                status=200, bytes_=500)

    def boom(*a, **kw):
        raise AssertionError("nothing above headless should have run")

    monkeypatch.setattr(tiers, "tier_http", http)
    monkeypatch.setattr(tiers, "tier_playwright", playwright)
    monkeypatch.setattr(tiers, "tier_copilot", boom)
    return seen


def test_an_opted_out_headed_start_can_succeed_at_headless(
        conn, monkeypatch, headless_succeeds):
    """The disabled requested rung falls down to an allowed successful tier."""
    env = _run(conn, monkeypatch, headed_off=True, start_tier="headed")
    assert env["tier"] == "headless" and not env.get("error")


def test_a_blocklist_start_at_headed_is_capped_at_headless(
        conn, monkeypatch, headless_succeeds):
    """A learned headed hint cannot bypass the unwindowed starting cap."""
    conn.execute(
        "INSERT OR REPLACE INTO blocklist"
        " (domain, tier_idx, tier, reason, locked, recent_failures,"
        "  recent_successes, updated_at) VALUES (?,?,?,?,0,'[]',0,0.0)",
        ("example.com", 2, "headed", "test"))
    conn.commit()
    env = _run(conn, monkeypatch, headed_off=True)
    assert env["tier"] == "headless"




def test_a_reserved_hint_falls_to_headless_without_entering_headed(
        conn, monkeypatch, headless_succeeds):
    """A disabled persisted hint safely selects the allowed unwindowed start."""
    _locked_copilot_hint(conn)
    env = _run(conn, monkeypatch, headed_off=True, raw_required=True, max_tier="headed")
    assert env["tier"] == "headless" and not env.get("error")


def test_a_caller_ceiling_below_headed_clamps_the_start_to_http(
        conn, monkeypatch, attempted):
    """An excluded requested start cannot raise the caller's ceiling."""
    _run(conn, monkeypatch, headed_off=True, start_tier="headed",
               max_tier="http")
    assert attempted == ["http"]


# ---- the exhaustion warning tells the truth about WHY the run ended ----------

@pytest.fixture()
def http_hard_blocks(monkeypatch):
    """Tier 0 answers 451, which `escalation_verdict` reads as HARD_BLOCK.

    Terminal by ruling: a 451 is a legal block found again at the same rung on
    every retry, so the cascade breaks THERE and no rung above it runs. That is
    the input the exhaustion warning gets wrong.
    """
    seen: list[str] = []

    def http(url, **kw):
        seen.append("http")
        return tiers.TierResult(
            ok=False, tier="http", status=451, error="unavailable for legal reasons",
            failure_class="origin_4xx_block", html="<html>blocked</html>")

    def boom(*a, **kw):
        raise AssertionError("the cascade must break at the HARD_BLOCK")

    monkeypatch.setattr(tiers, "tier_http", http)
    monkeypatch.setattr(tiers, "tier_playwright", boom)
    monkeypatch.setattr(tiers, "tier_copilot", boom)
    return seen


@pytest.fixture()
def http_rate_limits(monkeypatch):
    """Tier 0 answers 429 and every rung above it fails ordinarily.

    RATE_LIMITED stopped being terminal on 2026-09-04, so the cascade CLIMBS
    and runs out of tiers. The diagnosis is then promoted at the exhaustion
    block, which sets `gate_stop` -- after the loop, on a run that exhausted.
    """
    seen: list[str] = []

    def http(url, **kw):
        seen.append("http")
        return tiers.TierResult(
            ok=False, tier="http", status=429, error="too many requests",
            failure_class="rate_limited", html="<html>slow down</html>")

    def playwright(url, *, mode, **kw):
        seen.append(mode)
        return tiers.TierResult(ok=False, tier=mode, error="stub",
                                failure_class="empty_content")

    monkeypatch.setattr(tiers, "tier_http", http)
    monkeypatch.setattr(tiers, "tier_playwright", playwright)
    return seen


def test_a_hard_block_stops_below_headed_with_opt_out_set(
        conn, monkeypatch, http_hard_blocks):
    """A terminal origin block stops the cascade before browser entry."""
    _run(conn, monkeypatch, headed_off=True)
    assert http_hard_blocks == ["http"], "a HARD_BLOCK must break the cascade"


def test_an_opted_out_run_exhausts_only_allowed_unwindowed_rungs(
        conn, monkeypatch, attempted):
    """Removing headed leaves HTTP and headless below the explicit ceiling."""
    _run(conn, monkeypatch, headed_off=True, max_tier="headed")
    assert attempted == ["http", "headless"]


def test_an_opted_out_exhaustion_preserves_carried_rate_limit(
        conn, monkeypatch, http_rate_limits):
    """A carried rate limit survives exhaustion without entering headed."""
    env = _run(conn, monkeypatch, headed_off=True, max_tier="headed")
    assert http_rate_limits == ["http", "headless"], "RATE_LIMITED must not break"
    assert (env.get("failure_class") or "") == "rate_limited", (
        "the promotion must have run -- without it `gate_stop` is unset and "
        "this arm cannot tell the two candidate fixes apart")


# ---- the launch site ---------------------------------------------------------

def test_launching_headed_directly_is_ALLOWED_by_default(monkeypatch):
    """REPLACES `test_launching_headed_directly_is_refused`.

    That test asserted the launch site refused a headed session whenever
    nothing had opted in, and it named the refusal "the gate". There is no
    gate: the rung is enabled, so a direct `PlaywrightSession("headed")` opens.
    Nothing launches here either -- `_run_pw` is stubbed -- so this inspects
    the code path and only the code path.
    """
    monkeypatch.delenv(tiers.HEADED_TIER_DISABLE_ENV, raising=False)
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, "", ""))
    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: [])
    monkeypatch.setattr(tiers, "_claim_profile_dir", lambda: ("/tmp/p", None))

    sess = tiers.PlaywrightSession("headed")
    sess._ensure_open()
    assert sess._opened is True


def test_the_opt_out_is_honoured_at_the_launch_site(monkeypatch):
    """The opt-out must hold for callers that never go through the cascade.

    Shaping fetch.py's tier list keeps the cascade honest and stops nothing
    else. On a box that genuinely has no display, a direct
    `PlaywrightSession("headed")` -- a future caller, a test, a one-liner
    someone pastes while debugging -- walks past the cascade and tries to
    launch anyway. `_run_pw` is patched to a tripwire: if the guard is removed,
    the test fails on the recorded launch rather than on the exception, so it
    cannot pass for the wrong reason.
    (Cross-family review 2026-08-29, finding [1]; polarity flipped 2026-09-04.)
    """
    monkeypatch.setenv(tiers.HEADED_TIER_DISABLE_ENV, "1")
    launched = []
    monkeypatch.setattr(tiers, "_run_pw",
                        lambda a, timeout=None: (launched.append(a), (0, "", ""))[1])
    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: [])

    with pytest.raises(RuntimeError) as exc:
        tiers.PlaywrightSession("headed")._ensure_open()

    assert tiers.HEADED_TIER_DISABLE_ENV in str(exc.value), \
        "the refusal must name the variable that lifts it"
    assert not launched, "no playwright-cli invocation may happen at all"


def test_launching_headless_directly_is_not_refused(monkeypatch):
    """The guard must be aimed at headed only, or it breaks the working tier."""
    monkeypatch.setenv(tiers.HEADED_TIER_DISABLE_ENV, "1")
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, "", ""))
    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: [])
    monkeypatch.setattr(tiers, "_claim_profile_dir", lambda: ("/tmp/p", None))

    sess = tiers.PlaywrightSession("headless")
    sess._ensure_open()
    assert sess._opened is True


def test_a_warmed_headed_session_is_refused_once_the_opt_OUT_is_set(monkeypatch):
    """Opening it while allowed must not license using it once disallowed.

    `_SESSIONS` caches one session per mode for the life of the process, so a
    headed session opened before the variable was set stayed usable after it
    was set: `_ensure_open` returned early on `_opened` and the caller went on
    to open a tab on the live browser, reactivating the window. No new process
    starts, so nothing resembles a launch. That is why the check must sit ABOVE
    the `_opened` early return.

    EVERY OTHER TEST IN THIS FILE BUILDS A FRESH SESSION and therefore cannot
    see this. That is the whole reason this one exists -- it opens the session
    first, then takes the permission away.
    (Cross-family review 2026-08-29, round 2 finding [0]; polarity flipped
    2026-09-04.)
    """
    monkeypatch.setattr(tiers, "_run_pw", lambda a, timeout=None: (0, "", ""))
    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: [])
    monkeypatch.setattr(tiers, "_claim_profile_dir", lambda: ("/tmp/p", None))

    monkeypatch.delenv(tiers.HEADED_TIER_DISABLE_ENV, raising=False)
    sess = tiers.PlaywrightSession("headed")
    sess._ensure_open()
    assert sess._opened is True, "precondition: the session really did open"

    monkeypatch.setenv(tiers.HEADED_TIER_DISABLE_ENV, "1")
    with pytest.raises(RuntimeError) as exc:
        sess._ensure_open()
    assert tiers.HEADED_TIER_DISABLE_ENV in str(exc.value)


def test_a_warmed_headless_session_is_still_reusable(monkeypatch):
    """Moving the check above the early return must not break session reuse."""
    monkeypatch.setenv(tiers.HEADED_TIER_DISABLE_ENV, "1")
    calls = []
    monkeypatch.setattr(tiers, "_run_pw",
                        lambda a, timeout=None: (calls.append(a), (0, "", ""))[1])
    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: [])
    monkeypatch.setattr(tiers, "_claim_profile_dir", lambda: ("/tmp/p", None))

    sess = tiers.PlaywrightSession("headless")
    sess._ensure_open()
    n = len(calls)
    sess._ensure_open()
    assert len(calls) == n, "a warmed session must not re-open"
