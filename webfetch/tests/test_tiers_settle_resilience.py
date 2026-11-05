"""`tiers.py`'s settle loop must not destroy evidence, and must not run on a page.

Three defects from the 2026-09-04 adversarial review, all in `tiers.py`:

* R5, HIGH -- A FAILED POLL DESTROYED THE DIAGNOSIS. `_eval_page_observation` raises
  RuntimeError on any rc != 0 and `_run_pw` reports a subprocess timeout as
  rc=-1, so ONE slow or flaky poll propagated out of `fetch_html` carrying the
  interstitial already in hand. `tier_playwright` then returned empty html, no
  status and no headers, `escalation_verdict` answered CONTINUE at its
  empty-headers guard, and both the captcha class and the CDP ask were lost.
  The loop must keep the last document that a read actually returned.

* R1, MEDIUM -- THE SETTLE PREDICATE DID NOT MATCH THE GATE'S. The loop called
  `_challenge_reason(page_served=False)` whatever status had been captured, so
  it scanned `_CHALLENGE_MARKUP + _EMBEDDABLE_CHALLENGE_MARKUP` on a document
  the gate scans with `_CHALLENGE_MARKUP` alone. On this repo's own documented
  false positive -- a captured 200 article carrying one cf-turnstile mount on
  its contact form -- the loop burned the whole budget on a page that had
  already succeeded.

* R5, MEDIUM -- IMPORT-TIME CRASH ON A MALFORMED ENVIRONMENT VALUE.
  `CHALLENGE_SETTLE_BUDGET = float(os.environ.get(...))` at import meant a
  non-numeric `WEBFETCH_CHALLENGE_WAIT` killed every fetch on the machine
  with a raw ValueError out of `import tiers` -- no envelope, no class, no
  sentence naming the variable.

NO TEST HERE MAY REACH A BROWSER. `_run_pw` is the only subprocess boundary on
the fetch path and every test replaces it, along with `_list_sessions` and
`_claim_profile_dir`. The environment tests spawn `python -c "import tiers"`,
which opens nothing.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

import pytest

import state
import tiers


URL = "https://example.com/article"

HEADLESS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) HeadlessChrome/151.0.0.0 Safari/537.36")

# A Cloudflare managed challenge as served: the `/h/` interstitial path that
# `_CHALLENGE_MARKUP` pins, plus the title every captured wall carries.
INTERSTITIAL = (
    "<html><head><title>Just a moment...</title></head><body>"
    '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1">'
    "</script>"
    "<div id='challenge-running'>Checking your browser before you continue.</div>"
    "</body></html>")

_PARA = ("Wide-body aircraft reached transatlantic routes in the early "
         "nineteen seventies and rewrote the unit economics of the sector, "
         "because a seat-mile got cheaper faster than a landing slot got "
         "scarce. ")


def article_html(n_chars: int = 2000, *, mount: bool = False) -> str:
    """`n_chars` of prose. `mount` adds the site's OWN cf-turnstile widget.

    The mount is the repo's documented false positive: a real article the
    origin served at 200 whose contact form carries a Turnstile. It is in
    `_EMBEDDABLE_CHALLENGE_MARKUP` and NOT in `_CHALLENGE_MARKUP`, which is the
    whole distinction `page_served` exists to make.
    """
    body = (_PARA * (n_chars // len(_PARA) + 1))[:n_chars]
    widget = ('<form><div class="cf-turnstile" data-sitekey="0x4"></div></form>'
              if mount else "")
    return ("<html><head><title>Wide-body economics</title></head><body>"
            "<article><h1>Wide-body economics</h1><p>" + body
            + "</p></article>" + widget + "</body></html>")


class EvalScript:
    """Stub for `_run_pw`: serves a scripted sequence of documents.

    Answers atomic page-observation reads and `_ensure_open`'s user-agent probe.
    List entries supply [html, url, candidates]; strings supply plain HTML.

    An entry of the form `("fail", stderr)` in `docs` makes THAT outerHTML read
    return rc=-1, which is exactly what `_run_pw` returns for a
    `subprocess.TimeoutExpired`. Everything else is a document. Once the list
    runs out the last entry repeats, which is how "it never clears" and "it
    never recovers" are both expressed.
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
                entry = self.docs[min(self.outer_html_calls - 1,
                                      len(self.docs) - 1)]
                if isinstance(entry, tuple) and entry[0] == "fail":
                    return (-1, "", entry[1])
                observation = entry if isinstance(entry, list) else [entry, URL, []]
                return (0, "### Result\n" + json.dumps(observation)
                        + "\n### Ran Playwright code\n", "")
            return (0, '### Result\n"%s"\n' % HEADLESS_UA, "")
        return (0, "", "")


@pytest.fixture(autouse=True)
def _clean_session_cache():
    """`get_session` caches per mode for the life of the PROCESS, so a session
    left behind by one test is adopted by the next and its launch stubs go out
    of scope."""
    tiers._SESSIONS.clear()
    yield
    tiers._SESSIONS.clear()


@pytest.fixture
def sleeps(monkeypatch):
    """Records every sleep the fetch path takes, and still sleeps."""
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


# ---- R5: a failed poll must not take the diagnosis with it -------------------

def test_a_failed_settle_poll_keeps_the_interstitial(stub_browser, monkeypatch):
    """The evidence already in hand survives a poll that fails.

    `_run_pw` returns rc=-1 for a `subprocess.TimeoutExpired`, and
    `_eval_page_observation` turns any rc != 0 into RuntimeError. Before the fix that
    exception left `fetch_html` entirely: `tier_playwright`'s blanket `except`
    produced a TierResult with empty html, `status=None`, no headers and
    `failure_class` from the EXCEPTION TYPE -- so the wall this fetch had
    already identified was reported as a generic transient, the envelope named
    nothing the owner could act on, and the CDP ask that a captcha earns was
    never reached.

    One flaky eval may cost the IMPROVEMENT a later read might have brought. It
    may not cost the diagnosis.
    """
    monkeypatch.setattr(tiers, "CHALLENGE_POLL_S", 0.01, raising=False)
    monkeypatch.setattr(tiers, "CHALLENGE_SETTLE_BUDGET", 1.0, raising=False)
    candidates = [{"visible": False, "text": "Subscription required", "controls": []}]
    rec = stub_browser(
        [[INTERSTITIAL, URL, candidates], ("fail", "playwright-cli timeout after 15s")],
        status=403)

    res = tiers.tier_playwright(URL, mode="headless", total_timeout=30.0)

    assert rec.outer_html_calls >= 2, (
        "precondition: the loop must have polled at least once past the first "
        "read, or the failing poll was never reached (%d read(s))"
        % rec.outer_html_calls)
    assert res.ok is False
    assert "cdn-cgi/challenge-platform" in (res.html or ""), (
        "the interstitial already read was discarded; html is %d chars"
        % len(res.html or ""))
    assert res.failure_class == "cloudflare_challenge", (
        "the failed poll replaced the wall's own diagnosis with the exception "
        "type: %r" % res.failure_class)
    assert res.status == 403, (
        "the captured status went with it: %r" % (res.status,))
    assert res.failure_class in state.PERSISTENT_FAILURE_CLASSES
    assert res.access_gate_candidates == candidates


def test_a_failed_settle_poll_after_several_good_ones_keeps_the_latest(
        stub_browser, monkeypatch):
    """`html` must be the LAST DOCUMENT A READ RETURNED, not the first one.

    Distinct from the test above, and it is the assertion that pins WHICH
    document is kept: an implementation that snapshotted the pre-loop read and
    restored it on failure would pass that test and fail this one, having
    thrown away two successful reads' worth of progress.
    """
    monkeypatch.setattr(tiers, "CHALLENGE_POLL_S", 0.01, raising=False)
    monkeypatch.setattr(tiers, "CHALLENGE_SETTLE_BUDGET", 1.0, raising=False)
    first = INTERSTITIAL
    second = INTERSTITIAL.replace("challenge-running", "challenge-stage-two")
    rec = stub_browser([first, second, ("fail", "playwright-cli timeout")],
                       status=403)

    session = tiers.get_session("headless")
    _final_url, html, status, _headers, _candidates = session.fetch_html(
        URL, total_timeout=30.0)

    assert rec.outer_html_calls >= 3, rec.outer_html_calls
    assert "challenge-stage-two" in html, (
        "the second successful read was discarded in favour of the first")
    assert status == 403, (
        "no read cleared the challenge, so the captured status still stands; "
        "got %r" % (status,))


def test_a_failed_FIRST_read_still_raises(stub_browser, monkeypatch):
    """THE BOUNDARY. The rescue belongs to the settle loop, not its DOM reader.

    With nothing in hand there is no evidence to preserve, and a read that
    never succeeded must still fail loudly rather than hand up an empty
    document that the gate would then have to explain.
    """
    stub_browser([("fail", "playwright-cli timeout after 15s")], status=403)

    session = tiers.get_session("headless")
    with pytest.raises(RuntimeError) as exc:
        session.fetch_html(URL, total_timeout=30.0)
    assert "eval failed" in str(exc.value)


# ---- R1: the settle predicate must be the gate's ----------------------------

def test_a_captured_200_with_an_embedded_mount_never_enters_the_loop(
        stub_browser, monkeypatch, sleeps):
    """The repo's own documented false positive, at the loop's precondition.

    `tier_playwright` judges a CAPTURED 2xx with `page_served=True`, which
    scans `_CHALLENGE_MARKUP` only -- a `class="cf-turnstile"` a site put on its
    own contact form proves nothing about a page the origin agreed to serve.
    The loop hard-coded `page_served=False` and scanned both tuples, so it
    entered on a document the gate was about to ACCEPT and spent the entire
    budget re-reading a page that had already succeeded.

    The fetch succeeds either way; what the fix buys is the wall-clock and the
    ~12 evals. So the reads and the sleeps are what is asserted.
    """
    monkeypatch.setattr(tiers, "CHALLENGE_POLL_S", 0.02, raising=False)
    monkeypatch.setattr(tiers, "CHALLENGE_SETTLE_BUDGET", 0.5, raising=False)
    rec = stub_browser([article_html(mount=True)], status=200)

    res = tiers.tier_playwright(URL, mode="headless", total_timeout=30.0)

    assert res.ok is True, res.error
    assert rec.outer_html_calls == 1, (
        "a page the gate accepts must be read exactly once; got %d"
        % rec.outer_html_calls)
    assert sleeps == [], (
        "the settle loop ran on a document that was never a wall; slept %r"
        % (sleeps,))


def test_the_same_mount_on_a_403_still_settles(stub_browser, monkeypatch):
    """The other side of the same predicate: `page_served` is FALSE on a non-2xx.

    A Turnstile mount on a document the origin REFUSED is an interstitial, not
    someone's contact form, so the embeddable tuple is evidence again and the
    loop must still run. Without this the fix could be 'never scan the
    embeddable markers', which would silently stop waiting out the walls the
    loop exists for.
    """
    monkeypatch.setattr(tiers, "CHALLENGE_POLL_S", 0.02, raising=False)
    monkeypatch.setattr(tiers, "CHALLENGE_SETTLE_BUDGET", 1.0, raising=False)
    rec = stub_browser([article_html(mount=True), article_html()], status=403)

    session = tiers.get_session("headless")
    _final_url, html, status, _headers, _candidates = session.fetch_html(
        URL, total_timeout=30.0)

    assert rec.outer_html_calls > 1, (
        "a mount on a refused document must still be waited out; got %d read(s)"
        % rec.outer_html_calls)
    assert "cf-turnstile" not in html
    assert status is None, (
        "the interstitial's 403 must not follow the cleared document up")


def test_an_uncaptured_status_still_scans_both_tuples(stub_browser, monkeypatch):
    """`_navigate_capture` returning None is not a 2xx claim.

    `tier_playwright` supplies a PLACEHOLDER 200 on that path and passes
    `status_is_real=False`, which withholds the embeddable-marker exemption. The
    loop must make the same judgement from the same input -- a `nav_status` of
    None -- or the fallback path and the captured path would disagree about one
    document.
    """
    monkeypatch.setattr(tiers, "CHALLENGE_POLL_S", 0.02, raising=False)
    monkeypatch.setattr(tiers, "CHALLENGE_SETTLE_BUDGET", 1.0, raising=False)
    monkeypatch.setattr(tiers, "_navigate_capture",
                        lambda session_name, url, timeout: None)
    rec = EvalScript([article_html(mount=True), article_html()])
    monkeypatch.setattr(tiers, "_run_pw", rec)

    session = tiers.get_session("headless")
    _final_url, html, status, _headers, _candidates = session.fetch_html(
        URL, total_timeout=30.0)

    assert rec.outer_html_calls > 1, (
        "with no status read, both marker tuples are evidence and the loop must "
        "run; got %d read(s)" % rec.outer_html_calls)
    assert "cf-turnstile" not in html
    assert status is None


# ---- R5: a malformed environment value must not kill the import -------------

_IMPORT_PROBE = (
    "import sys; sys.path.insert(0, %r); import tiers; "
    "print(tiers.CHALLENGE_SETTLE_BUDGET)" % str(SKILL_DIR)
)


def _import_tiers_with(value):
    """Import `tiers` in a FRESH interpreter with the variable set.

    A subprocess and not `importlib.reload`, because the defect is at IMPORT:
    the module object a reload starts from already exists, so a reload cannot
    show what a first import on a misconfigured box does. Nothing is launched
    -- importing `tiers` opens no browser and spawns no `playwright-cli`.
    """
    env = dict(os.environ)
    env["WEBFETCH_CHALLENGE_WAIT"] = value
    return subprocess.run([sys.executable, "-c", _IMPORT_PROBE],
                          env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("value", ["twelve", "12s", "  ", "1,5", "None"])
def test_a_malformed_settle_budget_does_not_kill_every_fetch(value):
    """The purest R5 violation there is: no envelope, no class, no instruction.

    Before the fix `float(os.environ.get(...))` ran at module scope, so a typo
    in a shell profile made `import tiers` raise ValueError and EVERY fetch on
    the machine died with a raw traceback -- naming `float()`, not the variable
    that caused it. The import must survive, fall back to the documented
    default, and say on stderr which variable is wrong and what a good value
    looks like.
    """
    proc = _import_tiers_with(value)
    assert proc.returncode == 0, (
        "import tiers died on WEBFETCH_CHALLENGE_WAIT=%r:\n%s"
        % (value, proc.stderr[-2000:]))
    assert proc.stdout.strip() == "12.0", proc.stdout
    assert "WEBFETCH_CHALLENGE_WAIT" in proc.stderr, (
        "the warning must NAME the variable, or the reader cannot act on it: %r"
        % proc.stderr)


@pytest.mark.parametrize("value,expected", [("3.5", "3.5"), ("20", "20.0")])
def test_a_valid_settle_budget_is_still_honoured(value, expected):
    """THE CONTROL. The fallback must not swallow the values that do parse."""
    proc = _import_tiers_with(value)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == expected
    assert "WEBFETCH_CHALLENGE_WAIT" not in proc.stderr, (
        "a usable value must not produce a warning: %r" % proc.stderr)
