"""Tier1 real chrome behavioral regression tests."""

import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import state
import tiers


HEADLESS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) HeadlessChrome/151.0.0.0 Safari/537.36")
HEADFUL_UA = HEADLESS_UA.replace("HeadlessChrome/", "Chrome/")

CHANNEL_MISSING_ERR = (
    "Error: Daemon pid=26272: Daemon process exited with code 1\n"
    "[PlaywrightError: Chromium distribution 'chrome' is not found at "
    "C:\\Users\\example\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe\n"
    'Run "npx playwright install chrome"]')

# THE CONTROL, verbatim from arm I and NOT edited. A real playwright-cli failure
# that must NOT demote a box which asked for `chrome` -- and the exact trap the
# first predicate fell into, since 'chrome-beta' CONTAINS 'chrome'.
CHANNEL_MISSING_OTHER = (
    "Error: Daemon pid=26272: Daemon process exited with code 1\n"
    "[PlaywrightError: Chromium distribution 'chrome-beta' is not found at "
    "C:\\Users\\example\\AppData\\Local\\Google\\Chrome Beta\\Application\\chrome.exe\n"
    'Run "npx playwright install chrome-beta"]')


class Recorder:
    """Stub for `_run_pw`. Records argv and captures each `--config` as written.

    The config file is deleted on the way out of `_ensure_open`, so it has to be
    read HERE, while the notional launch is in flight -- which is also the only
    moment at which its content is what the browser would have seen.
    """

    def __init__(self, user_agent=HEADLESS_UA):
        self.calls = []
        self.configs = []
        # (rc, out, err) for successive `open` calls. A BaseException INSTANCE
        # in the list is RAISED rather than returned -- that is how an interrupt
        # arriving inside the subprocess call, with the browser already spawned,
        # is expressed. Returning is not enough: the window under test is the
        # one where `rc` never comes back at all.
        self.open_results = []
        self.user_agent = user_agent

    def __call__(self, args, timeout=None):
        self.calls.append(list(args))
        if "open" in args:
            for a in args:
                if a.startswith("--config="):
                    self.configs.append(json.loads(
                        Path(a.split("=", 1)[1]).read_text(encoding="utf-8")))
            if self.open_results:
                result = self.open_results.pop(0)
                if isinstance(result, BaseException):
                    raise result
                return result
            return (0, "", "")
        if "eval" in args:
            if self.user_agent is None:
                return (1, "", "eval failed")
            return (0, "### Result\n%s\n### Ran Playwright code\n"
                    % json.dumps(self.user_agent), "")
        return (0, "", "")

    def opens(self):
        return [c for c in self.calls if "open" in c]

    def channels(self):
        return [a for c in self.opens() for a in c if a.startswith("--browser=")]

    def context_options(self, i=0):
        return self.configs[i]["browser"]["contextOptions"]

    def reset(self):
        self.calls.clear()
        self.configs.clear()


@pytest.fixture
def profile_dir(tmp_path):
    d = tmp_path / "playwright"
    d.mkdir()
    return d


@pytest.fixture
def launch(tmp_path, profile_dir, monkeypatch):
    """A stubbed launch: no subprocess, no browser, state DB under tmp_path."""
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: [])
    monkeypatch.setattr(tiers, "_claim_profile_dir", lambda: (str(profile_dir), None))
    monkeypatch.delenv(tiers.HEADED_TIER_DISABLE_ENV, raising=False)
    rec = Recorder()
    monkeypatch.setattr(tiers, "_run_pw", rec)
    return rec


# ---- the channel on the argv -------------------------------------------------

def test_the_default_launch_asks_for_the_chrome_channel(launch):
    """`--browser=chrome` is the route to real Google Chrome (measurement 3.2).

    One argv token, no config file: the CLI documents `--browser` as "browser or
    chrome channel to use". The channel buys the brand list -- `Google Chrome`
    appears in `navigator.userAgentData.brands` on arms D/E/H and on no Chromium
    arm.
    """
    tiers.PlaywrightSession("headless")._ensure_open()
    assert launch.channels() == ["--browser=chrome"]


def test_a_headless_launch_never_passes_headed(launch):
    """The standing constraint: nothing this skill does may put a window up."""
    tiers.PlaywrightSession("headless")._ensure_open()
    for argv in launch.calls:
        assert "--headed" not in argv


def test_a_headed_launch_appends_exactly_one_headed_flag(monkeypatch, launch):
    """Moving the channel onto the argv must not duplicate or drop `--headed`.

    Nothing launches here -- `_run_pw` is the Recorder -- so this inspects argv
    and only argv.

    REWRITTEN 2026-09-04. It used to set WEBFETCH_ALLOW_HEADED first,
    because the tier was opt-IN. It is not: the headed rung is in the cascade
    and enabled, so the DEFAULT environment is what this must inspect, and the
    `launch` fixture already clears the opt-OUT variable.
    """
    tiers.PlaywrightSession("headed")._ensure_open()
    argv = launch.opens()[0]
    assert argv.count("--headed") == 1
    assert "--browser=chrome" in argv, "headed takes the channel from the same resolver"


# ---- the per-session config file ---------------------------------------------

def test_the_config_is_on_the_argv_and_carries_the_measured_dimensions(launch):
    tiers.PlaywrightSession("headless")._ensure_open()
    assert any(a.startswith("--config=") for a in launch.opens()[0])
    ctx = launch.context_options()
    assert ctx["viewport"] == {"width": 1280, "height": 720}
    assert ctx["screen"] == {"width": 1920, "height": 1080}


def test_the_config_has_no_launchOptions_key(launch):
    """A DELIBERATE ABSENCE, not an oversight -- two measurements, one key.

    `launchOptions.channel` is inert here: `--browser=` on the argv OVERRIDES it
    (arm B set `channel: "chrome"` in the file, kept `--browser=chromium`, and
    got Chromium; arm E set the identical channel with no `--browser` flag and
    got Google Chrome). Writing the channel here while keeping the argv flag
    ships a silent no-op whose only symptom is a brand list nobody checks.

    `launchOptions.args` is inert for the other half: `--screen-info` did not
    move `screen.width` (arm F, still 1280) while `contextOptions.screen` did
    (arms B/C, 1920).
    """
    tiers.PlaywrightSession("headless")._ensure_open()
    assert set(launch.configs[0]["browser"]) == {"contextOptions"}


def test_the_config_has_no_locale(launch):
    """A DELIBERATE ABSENCE. `locale` is a REGRESSION, not free consistency.

    With `locale: "en-US"` the wire carried `Accept-Language: en-US` and
    `navigator.languages === ["en-US"]` (arms B, C). Without it -- every other
    arm, including the shipped baseline -- both read `en-US,en;q=0.9` and
    `["en-US","en"]`, which is what real Chrome sends. The line strips the
    q-weighted fallback and makes this tier MORE distinguishable.
    """
    assert tiers.PlaywrightSession("headless")._ensure_open() is None
    assert "locale" not in launch.context_options()
    assert "locale" not in json.dumps(launch.configs[0])


def test_disable_blink_features_appears_nowhere(launch):
    """A DELIBERATE ABSENCE, and the worst regression available here.

    Upstream adds `--disable-blink-features=AutomationControlled` itself, but
    only `if (!args.some(a => a.includes("--disable-blink-features")))`. Passing
    ANY other one -- in the config or on the argv -- silently drops upstream's.
    Checked over the whole config text and the whole argv rather than the one key
    we would plausibly have used, because the guard upstream applies is a
    substring test over every arg.
    """
    tiers.PlaywrightSession("headless")._ensure_open()
    assert "--disable-blink-features" not in json.dumps(launch.configs[0])
    for argv in launch.calls:
        assert not any("--disable-blink-features" in a for a in argv)


def test_the_config_file_is_gone_after_a_successful_open(launch, tmp_path):
    tiers.PlaywrightSession("headless")._ensure_open()
    assert list(tmp_path.glob("*-config.json")) == []


def test_the_config_file_is_gone_after_a_failed_open(launch, tmp_path):
    """The claim is released on the exception path; the file goes with it."""
    launch.open_results = [(1, "", "Error: something else entirely")]
    with pytest.raises(RuntimeError):
        tiers.PlaywrightSession("headless")._ensure_open()
    assert list(tmp_path.glob("*-config.json")) == []


# ---- the channel fallback ----------------------------------------------------

def test_a_missing_channel_falls_back_to_chromium(launch):
    """A missing channel falls back to chromium."""
    launch.open_results = [(1, "", CHANNEL_MISSING_ERR), (0, "", "")]
    tiers.PlaywrightSession("headless")._ensure_open()
    assert launch.channels() == ["--browser=chrome", "--browser=chromium"]


def test_the_fallback_is_detected_on_stdout_too(launch):
    """Which stream carries the daemon's crash dump is not ours to guarantee."""
    launch.open_results = [(1, CHANNEL_MISSING_ERR, ""), (0, "", "")]
    tiers.PlaywrightSession("headless")._ensure_open()
    assert launch.channels() == ["--browser=chrome", "--browser=chromium"]


def test_the_demotion_is_persisted_so_the_next_process_does_not_re_probe(launch):
    """A box without Chrome pays the failed launch once, not once per fetch."""
    launch.open_results = [(1, "", CHANNEL_MISSING_ERR), (0, "", "")]
    tiers.PlaywrightSession("headless")._ensure_open()
    assert tiers._config_get(tiers.CHANNEL_CONFIG_KEY) == "chromium"

    launch.reset()
    tiers.PlaywrightSession("headless")._ensure_open()
    assert launch.channels() == ["--browser=chromium"], \
        "a persisted demotion must not re-probe the chrome channel"


def test_an_unrelated_failure_does_not_demote_the_channel(launch):
    """THE CONTROL, and it is the half that matters.

    The demotion is persisted and never re-probed, so a predicate that fired on
    any non-zero rc would cost a box its real-Chrome tier permanently on one
    locked profile dir or one timeout. A guard that only shows it is strict has
    shown the wrong thing.
    """
    launch.open_results = [(1, "", "Error: profile dir is locked by another process")]
    with pytest.raises(RuntimeError):
        tiers.PlaywrightSession("headless")._ensure_open()
    assert len(launch.opens()) == 1, "no retry on an unrelated failure"
    assert tiers._config_get(tiers.CHANNEL_CONFIG_KEY) is None


@pytest.mark.parametrize("text,expected", [
    ("Chromium distribution 'chrome' is not found at C:/x", True),
    ("chromium distribution 'chrome' IS NOT FOUND at C:/x", True),
    ("Chromium distribution chrome is not found at C:/x", True),
    ('Chromium distribution "chrome" is not found at C:/x', True),
    ("Chromium distribution 'chrome' was rejected", False),
    ("the profile is not found", False),
    ("", False),
    # REAL failures about a DIFFERENT distribution. Both phrase words are
    # present in each, so only the channel binding rejects them -- and
    # 'chrome-beta' and 'chrome-canary' both CONTAIN 'chrome'.
    (CHANNEL_MISSING_OTHER, False),
    ("Chromium distribution 'chrome-canary' is not found at C:/x", False),
    ("Chromium distribution 'msedge-beta' is not found at C:/x", False),
    # THE CASE THAT BROKE THE FIRST FIX, and the reason the channel is bound to
    # its slot rather than merely present as a whole token: the install path in
    # the same sentence carries `chrome` twice on its own, after a backslash and
    # before a dot. This is chrome-canary's real path, from the 2026-08-30 run.
    ("Chromium distribution 'chrome-canary' is not found at "
     "C:\\Users\\example\\AppData\\Local\\Google\\Chrome SxS\\Application\\chrome.exe",
     False),
    # Two unrelated lines that between them carry both phrase words and never
    # name the channel at all.
    ("no distribution here\nthe profile is not found", False),
    # The words in the wrong order, i.e. two diagnostics run together.
    ("is not found; separately, distribution chrome was requested", False),
    # NO BOUNDARY AFTER `found` was round 2 finding [2]. Each of these returned
    # True and would have permanently demoted a Chrome-equipped box on an
    # unrelated diagnostic -- silently, since the demotion is never re-probed.
    ("Chromium distribution chrome is not foundry", False),
    ("Chromium distribution 'chrome' is not foundry at C:/x", False),
    ("Chromium distribution 'chrome' is not found-yet at C:/x", False),
    # ...and the same class at the other end of the phrase.
    ("Chromium redistribution 'chrome' is not found at C:/x", False),
])
def test_the_channel_missing_predicate_is_bound_to_the_requested_channel(
        text, expected):
    assert tiers._is_channel_missing(text, "chrome") is expected


# ---- the user agent ----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (HEADLESS_UA, HEADFUL_UA),
    (HEADFUL_UA, HEADFUL_UA),          # already clean -> unchanged
    ("", None),
    (None, None),
    ("   ", None),
    ("### Ran Playwright code", None),  # unparseable -> no override, not a mangled one
])
def test_dehead_user_agent(raw, expected):
    assert tiers._dehead_user_agent(raw) == expected


def test_the_user_agent_is_absent_when_cold_and_present_when_warm(launch):
    """The first process on a fresh machine runs with the browser's native UA.

    That is a one-time, bounded leak, accepted deliberately: the override can
    only be learned by opening the browser, and the alternative is closing and
    reopening it mid-launch. Every later process reads the cache.
    """
    tiers.PlaywrightSession("headless")._ensure_open()
    assert "userAgent" not in launch.context_options(), "cold: nothing to write yet"
    assert tiers._cached_user_agent("chrome") == HEADFUL_UA

    launch.reset()
    tiers.PlaywrightSession("headless")._ensure_open()
    assert launch.context_options()["userAgent"] == HEADFUL_UA


def test_the_user_agent_cache_is_keyed_by_channel(launch):
    """A UA whose major disagrees with the running browser is a worse tell.

    Real Chrome and bundled Chromium announce different versions, so a demoted
    box must not inherit the chrome-channel string.
    """
    launch.open_results = [(1, "", CHANNEL_MISSING_ERR), (0, "", "")]
    tiers.PlaywrightSession("headless")._ensure_open()
    assert tiers._cached_user_agent("chromium") == HEADFUL_UA
    assert tiers._cached_user_agent("chrome") is None


def test_an_unreadable_user_agent_stores_no_override(launch, monkeypatch):
    """A failed probe must leave the cache empty, not poison it."""
    monkeypatch.setattr(tiers, "_run_pw", Recorder(user_agent=None))
    tiers.PlaywrightSession("headless")._ensure_open()
    assert tiers._config_get(tiers.USER_AGENT_CONFIG_KEY_PREFIX + "chrome") is None


def test_the_user_agent_is_read_with_the_function_form(launch):
    """`()=>navigator.userAgent` has no space, so list2cmdline leaves it unquoted
    and cmd.exe -- playwright-cli is a .cmd shim on Windows -- eats the `>`.
    The `function(){...}` form contains a space, is quoted, and arrives intact.
    VERIFIED against playwright-cli 0.1.18 on 2026-08-30.
    """
    tiers.PlaywrightSession("headless")._ensure_open()
    evals = [c for c in launch.calls if "eval" in c]
    assert evals and evals[0][-1] == "function(){return navigator.userAgent}"
    assert "=>" not in evals[0][-1]


def test_parse_eval_string_result_ignores_the_trailer():
    out = '### Result\n"%s"\n### Ran Playwright code\n```js\nnope\n```' % "ua-here"
    assert tiers._parse_eval_string_result(out) == "ua-here"
    assert tiers._parse_eval_string_result("") is None
    assert tiers._parse_eval_string_result("### Result\nnot json\n") is None


# ---- the probe must never cost a LIVE browser its profile claim ---------------

def test_a_raising_user_agent_probe_does_not_release_a_live_claim(
        launch, profile_dir, monkeypatch):
    """`open` has ALREADY succeeded by the time the probe runs.

    THE DEFECT, IN THE PAST TENSE. Round 1 ran the probe INSIDE the launch
    handler with `self._opened` still False, so an exception out of it unwound
    into `except BaseException`, releasing a profile beneath a live browser.
    A second process could then claim an already active profile.

    WHAT THIS PINS NOW: the probe sits OUTSIDE that handler and `_opened` is
    already True when it runs, so what is under test is containment of an
    ORDINARY probe failure after the browser is up. The fetch must survive it
    and keep both the claim and the open state.

    Reachable rather than theoretical: `_run_pw` catches only TimeoutExpired and
    FileNotFoundError, so PermissionError and every other OSError raised by
    `subprocess.run` comes straight out of `_read_user_agent`.
    (Cross-family review 2026-08-30 round 1 finding [2], round 3 finding [3].)
    """
    released = []
    monkeypatch.setattr(tiers, "_release_profile_dir",
                        lambda d, fd: released.append(d))

    def boom(*_a, **_k):
        raise PermissionError("cannot spawn playwright-cli")

    monkeypatch.setattr(tiers, "_read_user_agent", boom)

    session = tiers.PlaywrightSession("headless")
    session._ensure_open()                       # must NOT raise

    assert session._opened is True, "the browser is open -- say so"
    assert session._profile_dir == str(profile_dir), "the claim is still ours"
    assert released == [], "released a LIVE browser's profile dir"
    assert tiers._cached_user_agent("chrome") is None, "nothing learned to cache"


def test_a_failing_open_still_releases_the_claim(launch, profile_dir, monkeypatch):
    """THE CONTROL for the test above, and the half that matters.

    Containment must not reach the case where the browser never opened at all --
    a guard that only demonstrates permissiveness has demonstrated the wrong
    direction.
    """
    released = []
    monkeypatch.setattr(tiers, "_release_profile_dir",
                        lambda d, fd: released.append(d))
    launch.open_results = [(1, "", "Error: profile dir is locked")]
    with pytest.raises(RuntimeError):
        tiers.PlaywrightSession("headless")._ensure_open()
    assert released == [str(profile_dir)]


# ---- the cached user agent expires -------------------------------------------

def test_a_stale_user_agent_is_re_probed_and_rewritten(launch):
    """It tracks a browser that updates ITSELF, so a permanent cache would pin
    `Chrome/151` while the brand list moved to 152 -- a sharper tell than the
    `HeadlessChrome` token the override exists to remove, and nothing in the
    system would ever clear it.
    (Cross-family review 2026-08-30 round 1 finding [3].)
    """
    stale = time.time() - tiers.USER_AGENT_MAX_AGE_S - 1
    tiers._config_set(tiers.USER_AGENT_CONFIG_KEY_PREFIX + "chrome",
                      json.dumps({"ua": "Mozilla/5.0 stale/1", "ts": stale}))
    assert tiers._cached_user_agent("chrome") is None

    tiers.PlaywrightSession("headless")._ensure_open()
    assert "userAgent" not in launch.context_options(), \
        "a stale value must not ship; this launch is the one that refreshes it"
    assert tiers._cached_user_agent("chrome") == HEADFUL_UA


def test_a_user_agent_inside_the_window_is_used_and_costs_no_probe(launch):
    """A user agent inside the window is used and costs no probe."""
    tiers._config_set(tiers.USER_AGENT_CONFIG_KEY_PREFIX + "chrome",
                      json.dumps({"ua": HEADFUL_UA, "ts": time.time() - 60}))
    tiers.PlaywrightSession("headless")._ensure_open()
    assert launch.context_options()["userAgent"] == HEADFUL_UA
    assert not [c for c in launch.calls if "eval" in c], "no probe while warm"


@pytest.mark.parametrize("stored", [
    "Mozilla/5.0 legacy-bare-string",   # the pre-expiry storage format
    "{not json",
    '{"ua": "", "ts": 0}',
    '"a bare json string"',
    "123",
    # ROUND 2 FINDING [3]: each of these passed the old truthiness test. The
    # first returned the integer 123, which would have been written straight
    # into the config as a numeric `contextOptions.userAgent`.
    '{"ua": 123, "ts": 99999999999}',
    '{"ua": ["Mozilla/5.0 x"], "ts": 99999999999}',
    '{"ua": {"v": "Mozilla/5.0 x"}, "ts": 99999999999}',
    '{"ua": true, "ts": 99999999999}',
    # A timestamp that is not a number, and a record with no timestamp at all.
    '{"ua": "Mozilla/5.0 x", "ts": "not-a-number"}',
    '{"ua": "Mozilla/5.0 x"}',
    '{"ua": "Mozilla/5.0 x", "ts": null}',
])
def test_an_unusable_stored_user_agent_reads_as_absent(launch, stored):
    """Corrupt, legacy and wrong-shaped values all take the cold path rather
    than shipping something unparsed as a userAgent. Round 2 finding [3] was
    that this docstring described a behaviour the code did not have."""
    tiers._config_set(tiers.USER_AGENT_CONFIG_KEY_PREFIX + "chrome", stored)
    assert tiers._cached_user_agent("chrome") is None


def test_a_future_timestamp_reads_as_absent(launch):
    """The age is bounded at BOTH ends, and the lower bound is the load-bearing
    one: a record stamped ahead of the clock has its freshness extended by the
    whole skew, so one stamped a year ahead stays fresh for a year past the
    point it should have expired.
    (Cross-family review 2026-08-30 round 2 finding [3], round 3 finding [5].)
    """
    tiers._config_set(tiers.USER_AGENT_CONFIG_KEY_PREFIX + "chrome",
                      json.dumps({"ua": HEADFUL_UA, "ts": time.time() + 86400}))
    assert tiers._cached_user_agent("chrome") is None


def test_a_baseexception_in_the_probe_keeps_the_claim_and_the_open_state(
        launch, profile_dir, monkeypatch):
    """Round 1's containment caught `Exception`, and that was not enough.

    A KeyboardInterrupt is not an `Exception`, so under round 1's shape it
    reached the handler that releases the profile claim -- out from under a LIVE
    browser -- and the SIGINT path could not clean up after itself either,
    because `close()` returns early while `_opened` is False. The release is
    gated on positive evidence that no browser was launched now, rather than on
    an except clause -- so an interrupt anywhere after `open` returns 0 leaves
    the claim HELD, which is the safe direction.
    (Cross-family review 2026-08-30 round 2 finding [1], round 3 finding [0].)
    """
    released = []
    monkeypatch.setattr(tiers, "_release_profile_dir",
                        lambda d, fd: released.append(d))

    def interrupt(*_a, **_k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(tiers, "_read_user_agent", interrupt)

    session = tiers.PlaywrightSession("headless")
    with pytest.raises(KeyboardInterrupt):
        session._ensure_open()

    assert session._opened is True, "the browser opened -- close() depends on this"
    assert session._profile_dir == str(profile_dir), "the claim is still ours"
    assert released == [], "released a LIVE browser's profile dir"


# ---- an interrupt INSIDE the launch must not release a live browser's claim ---

def test_an_interrupt_inside_open_keeps_the_claim(launch, profile_dir, monkeypatch):
    """ROUND 3'S MUST-FIX. The window is INSIDE `_run_pw`, not after it.

    `open` spawns the daemon and then waits on it, so a KeyboardInterrupt
    delivered during that wait finds a browser ALREADY LIVE while `rc` has not
    come back and `_opened` is still False. Round 2's shape unwound that into a
    handler which released the profile dir out from under the browser, and the
    next claimant got `Browser is already in use`.

    The flag is set BEFORE the call for exactly this reason: "a browser may be
    live" is the default state for the whole launch, so releasing needs positive
    evidence of the opposite. Holding costs nothing on a dying process -- the
    kernel drops the lock when the holder exits.
    (Cross-family review 2026-08-30 round 3 finding [0].)
    """
    released = []
    monkeypatch.setattr(tiers, "_release_profile_dir",
                        lambda d, fd: released.append(d))
    monkeypatch.setattr(tiers, "_claim_profile_dir", lambda: (str(profile_dir), 4242))
    launch.open_results = [KeyboardInterrupt()]

    session = tiers.PlaywrightSession("headless")
    with pytest.raises(KeyboardInterrupt):
        session._ensure_open()

    assert released == [], "released a claim a live browser may still hold"
    assert session._profile_dir == str(profile_dir), "the claim must stay ours"
    assert session._profile_fd == 4242, "the held descriptor must not be cleared"


def test_an_interrupt_inside_the_fallback_open_keeps_the_claim(
        launch, profile_dir, monkeypatch):
    """An interrupt inside the fallback open keeps the claim."""
    released = []
    monkeypatch.setattr(tiers, "_release_profile_dir",
                        lambda d, fd: released.append(d))
    monkeypatch.setattr(tiers, "_claim_profile_dir", lambda: (str(profile_dir), 4242))
    launch.open_results = [(1, "", CHANNEL_MISSING_ERR), KeyboardInterrupt()]

    session = tiers.PlaywrightSession("headless")
    with pytest.raises(KeyboardInterrupt):
        session._ensure_open()

    assert launch.channels() == ["--browser=chrome", "--browser=chromium"], \
        "precondition: the interrupt must land on the FALLBACK open"
    assert released == [], "released a claim a live browser may still hold"
    assert session._profile_dir == str(profile_dir)
    assert session._profile_fd == 4242


def test_an_interrupted_launch_leaves_its_config_file_behind(
        launch, profile_dir, tmp_path, monkeypatch):
    """DELIBERATE, and pinned here so nobody 'fixes' it back.

    Cleanup is gated on the same flag as the release, so an interrupt that may
    have left a browser live leaves the config file too. Harmless: the next
    claimant of that slot overwrites it before it launches, and the alternative
    is deleting a file out from under a process that may still be starting.
    """
    monkeypatch.setattr(tiers, "_release_profile_dir", lambda d, fd: None)
    launch.open_results = [KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt):
        tiers.PlaywrightSession("headless")._ensure_open()
    assert list(tmp_path.glob("*-config.json")) != []


# ---- the two streams are matched separately, never concatenated ---------------

def test_the_streams_are_never_concatenated_before_matching(launch):
    """THE CONTROL for round 3's second must-fix, and the whole point of it.

    `err + out` puts the tail of one stream against the head of the other, so a
    phrase that exists in NEITHER can appear across the seam. The demotion it
    would trigger is persisted and never re-probed, so one fabricated match
    costs a Chrome-equipped box its real-Chrome tier until a human clears the
    key. A test that only showed the ordinary case still passing would have
    passed before the fix too.
    (Cross-family review 2026-08-30 round 3 finding [1].)
    """
    err = "playwright: internal diagnostic: distribution "
    out = "chrome is not found in page 3\n"

    # The seam really does match, or this control demonstrates nothing.
    assert tiers._is_channel_missing(err + out, "chrome") is True
    assert tiers._is_channel_missing(err, "chrome") is False
    assert tiers._is_channel_missing(out, "chrome") is False

    launch.open_results = [(1, out, err)]
    with pytest.raises(RuntimeError):
        tiers.PlaywrightSession("headless")._ensure_open()

    assert len(launch.opens()) == 1, "an unrelated failure must not retry"
    assert tiers._config_get(tiers.CHANNEL_CONFIG_KEY) is None, \
        "demoted the channel on a phrase neither stream contains"


# ---- a whitespace-only cached user agent is a corrupt record ------------------

def test_a_whitespace_only_cached_user_agent_reads_as_absent(launch):
    """It passed `isinstance(str)` AND truthiness, so round 2's check let it by.

    Written into the config it becomes a whitespace `contextOptions.userAgent` --
    a header no browser sends, i.e. a louder tell than the headless string the
    override exists to remove.
    (Cross-family review 2026-08-30 round 3 finding [2].)
    """
    tiers._config_set(tiers.USER_AGENT_CONFIG_KEY_PREFIX + "chrome",
                      json.dumps({"ua": "   ", "ts": time.time()}))
    assert tiers._cached_user_agent("chrome") is None


def test_a_whitespace_only_record_never_reaches_the_config(launch):
    """The unit check above is not enough on its own -- this is the launch path.

    What matters is not that the accessor returns None but that nothing
    whitespace-shaped is ever written as a `userAgent`.
    """
    tiers._config_set(tiers.USER_AGENT_CONFIG_KEY_PREFIX + "chrome",
                      json.dumps({"ua": "  \t ", "ts": time.time()}))
    tiers.PlaywrightSession("headless")._ensure_open()
    assert "userAgent" not in launch.context_options(), \
        "a corrupt record reached the browser as a user agent"


def test_a_padded_user_agent_is_returned_exactly_as_stored(launch):
    """REJECT, DO NOT REPAIR. The check strips; the return value does not.

    Stripping here would hand the browser a value that was never probed, and
    quietly. A record that needs repairing is a record to re-probe.
    """
    padded = "  " + HEADFUL_UA + "  "
    tiers._config_set(tiers.USER_AGENT_CONFIG_KEY_PREFIX + "chrome",
                      json.dumps({"ua": padded, "ts": time.time()}))
    assert tiers._cached_user_agent("chrome") == padded
