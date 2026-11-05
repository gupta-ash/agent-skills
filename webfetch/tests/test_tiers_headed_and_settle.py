"""The headed rung is ON by default, and a JS challenge is waited out.

Both halves are `tiers.py`'s own. The first is the predicate the cascade reads
(`headed_tier_allowed`) and the guard at the launch site that honours the
opt-out. The second is `PlaywrightSession.fetch_html`'s settle loop: a
Cloudflare interstitial or a non-interactive Turnstile clears itself SECONDS
AFTER `load` fires, so the single DOM read this tier used to do was guaranteed
to see the wall and never the page.

NO TEST HERE MAY REACH A BROWSER. `_run_pw` is the only subprocess boundary on
the launch path and every test replaces it, along with `_list_sessions` and
`_claim_profile_dir`. An agent doing testing on this machine runs headless or
with stubs -- that constraint is untouched by the tier being enabled for real
fetches.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import state
import tiers


URL = "https://example.com/article"

HEADLESS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) HeadlessChrome/151.0.0.0 Safari/537.36")

# A Cloudflare managed challenge as it is served: the interstitial path that
# `_CHALLENGE_MARKUP` pins (the `/h/` segment, not the bare prefix Bot Fight
# Mode injects into ordinary pages) plus the title every captured wall carries.
INTERSTITIAL = (
    "<html><head><title>Just a moment...</title></head><body>"
    '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1">'
    "</script>"
    "<div id='challenge-running'>Checking your browser before you continue.</div>"
    "</body></html>")


def article_html(n_chars: int = 2000) -> str:
    """A plain document with `n_chars` of prose and no challenge marker."""
    para = ("Wide-body aircraft reached transatlantic routes in the early "
            "nineteen seventies and rewrote the unit economics of the sector, "
            "because a seat-mile got cheaper faster than a landing slot got "
            "scarce. ")
    body = (para * (n_chars // len(para) + 1))[:n_chars]
    return ("<html><head><title>Wide-body economics</title></head><body>"
            "<article><h1>Wide-body economics</h1><p>" + body
            + "</p></article></body></html>")


class EvalScript:
    """Stub for `_run_pw`: serves a scripted sequence of documents.

    Answers atomic page-observation reads and `_ensure_open`'s user-agent probe.
    List entries supply [html, url, candidates]; strings supply plain HTML.

    The Nth outerHTML read gets docs[N-1]; once the list runs out the last
    document repeats, which is how "the challenge never clears" is expressed.
    """

    def __init__(self, docs):
        self.docs = list(docs)
        self.outer_html_calls = 0
        self.calls = []

    def __call__(self, args, timeout=None):
        self.calls.append(list(args))
        if "eval" in args:
            js = args[-1]
            if "outerHTML" in js and "location.href" in js:
                self.outer_html_calls += 1
                doc = self.docs[min(self.outer_html_calls - 1,
                                    len(self.docs) - 1)]
                observation = doc if isinstance(doc, list) else [doc, URL, []]
                return (0, "### Result\n" + json.dumps(observation)
                        + "\n### Ran Playwright code\n", "")
            return (0, '### Result\n"%s"\n' % HEADLESS_UA, "")
        return (0, "", "")

    def opens(self):
        return [c for c in self.calls if "open" in c]


@pytest.fixture(autouse=True)
def _clean_session_cache():
    """`get_session` caches per mode for the life of the PROCESS, so a session
    left behind by one test is adopted by the next and the launch stubs it was
    built with go out of scope."""
    tiers._SESSIONS.clear()
    yield
    tiers._SESSIONS.clear()


@pytest.fixture
def sleeps(monkeypatch):
    """Records every sleep the fetch path takes, and still sleeps.

    Recording rather than refusing: the settle loop is SUPPOSED to sleep, and a
    tripwire could only be installed in the one test that asserts it does not.
    """
    taken = []
    real = time.sleep

    def spy(seconds):
        taken.append(seconds)
        real(seconds)

    monkeypatch.setattr(tiers.time, "sleep", spy)
    return taken


@pytest.fixture
def stub_browser(tmp_path, monkeypatch):
    """A stubbed browser: no subprocess, no window, state DB under tmp_path."""
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: [])
    monkeypatch.setattr(tiers, "_claim_profile_dir",
                        lambda: (str(tmp_path / "profile"), None))
    monkeypatch.delenv("WEBFETCH_DISABLE_HEADED", raising=False)

    def install(docs, status=200, headers=None):
        rec = EvalScript(docs)
        monkeypatch.setattr(tiers, "_run_pw", rec)
        monkeypatch.setattr(
            tiers, "_navigate_capture",
            lambda session_name, url, timeout: {
                "ok": True, "status": status,
                "headers": dict(headers or {"server": "cloudflare"}),
                "url": url})
        return rec

    return install


# ---- the rung is on by default -----------------------------------------------

def test_headed_is_allowed_with_no_env_var(monkeypatch):
    """R3: no opt-in exists any more, so an ordinary box runs the headed rung.

    This is the tiers.py half of "headed runs by default": the cascade in
    fetch.py reads THIS predicate rather than owning a copy, so if it answers
    False here the rung is dropped from `allowed_idxs` before any verdict is
    consulted.
    """
    monkeypatch.delenv("WEBFETCH_DISABLE_HEADED", raising=False)
    monkeypatch.delenv("WEBFETCH_ALLOW_HEADED", raising=False)
    assert tiers.headed_tier_allowed() is True


@pytest.mark.parametrize("value,allowed", [
    ("1", False), ("true", False), ("TRUE", False), ("Yes", False),
    ("0", True), ("", True), ("no", True), ("maybe", True),
])
def test_only_the_opt_out_values_disable_the_rung(monkeypatch, value, allowed):
    monkeypatch.setenv("WEBFETCH_DISABLE_HEADED", value)
    assert tiers.headed_tier_allowed() is allowed


def test_the_default_headed_launch_is_not_blocked_at_the_launch_site(
        stub_browser, monkeypatch):
    """With no variable set the guard lets the launch through, and it is headed.

    Nothing launches: `_run_pw` is the stub, so this inspects argv and only
    argv.
    """
    monkeypatch.delenv("WEBFETCH_ALLOW_HEADED", raising=False)
    rec = stub_browser([article_html()])
    tiers.PlaywrightSession("headed")._ensure_open()
    argv = rec.opens()[0]
    assert argv.count("--headed") == 1


def test_the_opt_out_is_what_stops_a_direct_headed_session(tmp_path, monkeypatch):
    """The guard fires only on an explicit opt-out, and NAMES it.

    R5 at the launch site: a refusal that names the wrong variable tells the
    reader to set something that does not exist. `_run_pw` is a TRIPWIRE here,
    not a recorder -- if the guard ever stops firing on an opted-out box, the
    test must fail loudly rather than open a window.
    """
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: [])
    monkeypatch.setattr(tiers, "_claim_profile_dir",
                        lambda: (str(tmp_path / "profile"), None))

    def tripwire(args, timeout=None):
        raise AssertionError("a launch was attempted on an opted-out box: %r"
                             % (args,))

    monkeypatch.setattr(tiers, "_run_pw", tripwire)
    monkeypatch.setenv("WEBFETCH_DISABLE_HEADED", "1")
    with pytest.raises(RuntimeError) as exc:
        tiers.PlaywrightSession("headed")._ensure_open()
    assert "WEBFETCH_DISABLE_HEADED" in str(exc.value), (
        "the refusal must name the variable that is suppressing the tier, "
        "got: %s" % exc.value)


# ---- the challenge settle loop -----------------------------------------------

def test_headed_waits_out_a_self_clearing_challenge(stub_browser, monkeypatch,
                                                    sleeps):
    """R1: a challenge that clears itself must be waited out, not reported.

    `waitUntil:'load'` fires on the INTERSTITIAL. The document that replaces it
    a few seconds later is the page, and one read cannot see that happen.
    """
    monkeypatch.setattr(tiers, "CHALLENGE_POLL_S", 0.01, raising=False)
    monkeypatch.setattr(tiers, "CHALLENGE_SETTLE_BUDGET", 5.0, raising=False)
    rec = stub_browser([INTERSTITIAL, INTERSTITIAL, article_html()], status=403)

    res = tiers.tier_playwright(URL, mode="headed", total_timeout=30.0)

    assert res.ok is True, "the settled document is real content: %r" % res.error
    assert "Wide-body" in (res.content or "")
    assert rec.outer_html_calls > 1, (
        "one read can only ever see the interstitial; got %d read(s)"
        % rec.outer_html_calls)
    assert sleeps, "the loop must actually wait between reads"


def test_the_cleared_document_does_not_carry_the_interstitials_status(
        stub_browser, monkeypatch):
    """The captured 403 belongs to the wall, not to the document now in the tab.

    Reporting it would hand the content gate a non-2xx for a page that is real,
    and `is_real_content` rejects on status before it looks at anything else --
    so keeping the stale status would throw away the fetch the wait just won.
    """
    monkeypatch.setattr(tiers, "CHALLENGE_POLL_S", 0.01, raising=False)
    monkeypatch.setattr(tiers, "CHALLENGE_SETTLE_BUDGET", 5.0, raising=False)
    stub_browser([INTERSTITIAL, article_html()], status=403)

    session = tiers.get_session("headless")
    _final_url, html, status, _headers, _candidates = session.fetch_html(URL, total_timeout=30.0)

    assert "Wide-body" in html
    assert status is None, (
        "a status read off the interstitial must not be presented as the "
        "settled document's own; got %r" % (status,))


def test_an_unclearable_challenge_gives_up_inside_the_budget(
        stub_browser, monkeypatch):
    """An INTERACTIVE captcha cannot be waited out, so the wait must be bounded.

    Click-the-images is not solvable by any amount of polling. The tier has to
    fail, inside a budget, with the failure class that routes the URL onward to
    the CDP ask -- not hang on a document that will never change.
    """
    monkeypatch.setattr(tiers, "CHALLENGE_SETTLE_BUDGET", 2.0, raising=False)
    rec = stub_browser([INTERSTITIAL], status=403)

    t0 = time.monotonic()
    res = tiers.tier_playwright(URL, mode="headed", total_timeout=30.0)
    elapsed = time.monotonic() - t0

    assert res.ok is False
    assert res.failure_class == "cloudflare_challenge", res.failure_class
    assert rec.outer_html_calls > 1, (
        "the tier must have polled, not read once; got %d"
        % rec.outer_html_calls)
    assert elapsed < 2.0 + tiers.CHALLENGE_POLL_S + 1.0, (
        "the wait must be bounded by the budget; took %.1fs" % elapsed)


def test_an_ordinary_page_is_not_delayed(stub_browser, monkeypatch, sleeps):
    """THE CONTROL. The settle loop stays behind its precondition.

    Roughly 60% of fetches succeed on a document that is not a challenge, and
    none of them may pay a second eval or a single second of wall-clock for a
    wait that exists for the other 40%.
    """
    rec = stub_browser([article_html()], status=200)

    res = tiers.tier_playwright(URL, mode="headless", total_timeout=30.0)

    assert res.ok is True, res.error
    assert rec.outer_html_calls == 1, (
        "an ordinary page must be read exactly once; got %d"
        % rec.outer_html_calls)
    assert sleeps == [], "an ordinary page must not sleep; slept %r" % (sleeps,)


def test_headed_opt_out_is_an_explicit_unexecuted_prerequisite_even_for_an_open_session(
        monkeypatch):
    session = tiers.PlaywrightSession("headed")
    session._opened = True
    monkeypatch.setattr(tiers, "get_session", lambda mode: session)
    monkeypatch.setattr(tiers, "validate_url_safety", lambda *args, **kwargs: None)
    monkeypatch.setenv("WEBFETCH_DISABLE_HEADED", "1")

    def forbidden(*args, **kwargs):
        raise AssertionError("disabled headed must not launch or navigate")

    monkeypatch.setattr(tiers, "_run_pw", forbidden)

    result = tiers.tier_playwright(URL, mode="headed")

    assert result.error_code == "headed_disabled"
    assert result.ok is False and result.failure_class == "other"
    assert result.status is None and result.html == ""


@pytest.mark.parametrize("mode", ["headless", "headed"])
@pytest.mark.parametrize("rc,error,expected_code", [
    (-1, "playwright-cli not found on PATH", "browser_unavailable"),
    (1, "Chromium distribution 'chromium' is not found at /absent/browser", "browser_unavailable"),
    (-1, "playwright-cli timeout after 30s", ""),
    (1, "Browser is already in use", ""),
    (1, "unknown option --headed", ""),
    (1, "Chromium distribution 'chrome-beta' is not found at /unrelated/browser", ""),
])
def test_only_proven_unavailable_launch_prerequisites_report_no_page_attempt(
        stub_browser, monkeypatch, mode, rc, error, expected_code):
    # The existing not-found spawn signal means an unavailable prerequisite;
    # it does not distinguish an absent launcher from a concurrently deleted cwd.
    rec = stub_browser([article_html()])
    monkeypatch.setattr(tiers, "validate_url_safety", lambda *args, **kwargs: None)
    monkeypatch.setattr(tiers, "_resolve_browser_channel", lambda: "chromium")
    seen = []

    def run(args, timeout=None):
        seen.append(list(args))
        if "open" in args:
            return rc, "", error
        return rec(args, timeout)

    monkeypatch.setattr(tiers, "_run_pw", run)

    result = tiers.tier_playwright(URL, mode=mode)

    assert result.ok is False
    assert result.error_code == expected_code
    assert result.html == "" and result.status is None
    assert not any("tab-new" in args or "run-code" in args for args in seen)
