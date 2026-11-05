"""Tests for `_navigate_capture` -- the run-code response-capture path (2026-08-29).

WHAT THIS GUARDS, and why each test can actually go red:

The browser tiers used to call `is_real_content(md, html, 200)` with the status
HARD-CODED, so the non-2xx branch could never fire and `classify_failure` could
never distinguish `origin_4xx_block` from a content failure. `_navigate_capture`
navigates through playwright-cli's `run-code` verb, which -- unlike `goto` --
hands back a Response carrying the real status and headers.

Two properties matter more than the feature itself:

1. FAIL-SOFT. The fleet runs several playwright-cli versions. If `run-code` is
   missing or its output shape drifts, the tier MUST fall back to plain `goto`
   and keep working. A missing capability must never turn a working fetch into
   a failed one. `test_route_unavailable_*` and `test_fetch_html_falls_back`
   go red if that regresses.

2. NO DOUBLE NAVIGATION. When the route works but the NAVIGATION fails, the
   caller must NOT retry with `goto` -- that would double the latency of every
   failing browser fetch. `test_navigation_failure_is_not_route_failure` pins
   the distinction that makes this possible.

On Windows the playwright-cli .cmd shim runs through cmd.exe, which interprets
URL metacharacters as shell syntax. The URL must travel as encoded data;
`test_url_never_reaches_argv_raw` detects a regression to raw argv text.
"""

import json

import pytest

import tiers


def _result_block(payload: dict) -> str:
    """Build stdout in playwright-cli's shape: it JSON-encodes our returned string."""
    return "### Result\n" + json.dumps(json.dumps(payload)) + "\n### Ran Playwright code\n"


def _fake_run_pw(rc=0, out="", err="", record=None):
    def fake(args, timeout):
        if record is not None:
            record.append(list(args))
        return rc, out, err
    return fake


# ---- route availability ------------------------------------------------------

def test_route_unavailable_on_nonzero_rc(monkeypatch):
    """A playwright-cli with no `run-code` verb exits non-zero -> route is None."""
    monkeypatch.setattr(tiers, "_run_pw",
                        _fake_run_pw(rc=1, err="unknown command 'run-code'"))
    assert tiers._navigate_capture("s", "https://example.com", timeout=5) is None


def test_route_unavailable_on_unparseable_output(monkeypatch):
    """Output shape drift must degrade to the fallback, not raise."""
    monkeypatch.setattr(tiers, "_run_pw",
                        _fake_run_pw(rc=0, out="### Result\nnot-json-at-all\n"))
    assert tiers._navigate_capture("s", "https://example.com", timeout=5) is None


def test_route_unavailable_when_no_result_marker(monkeypatch):
    monkeypatch.setattr(tiers, "_run_pw",
                        _fake_run_pw(rc=0, out="### Error\nSyntaxError: nope\n"))
    assert tiers._navigate_capture("s", "https://example.com", timeout=5) is None


def test_overlong_url_declines_the_route(monkeypatch):
    """An over-long snippet would blow cmd.exe's ~8191 limit; decline instead."""
    called = []
    monkeypatch.setattr(tiers, "_run_pw", _fake_run_pw(rc=0, record=called))
    huge = "https://example.com/" + ("x" * tiers._RUNCODE_MAX_ARG)
    assert tiers._navigate_capture("s", huge, timeout=5) is None
    assert called == [], "must not even invoke playwright-cli for an over-long URL"


# ---- success path ------------------------------------------------------------

def test_captures_status_and_headers(monkeypatch):
    out = _result_block({"ok": True, "status": 404,
                         "headers": {"server": "cloudflare", "cf-ray": "abc"},
                         "url": "https://example.com/final"})
    monkeypatch.setattr(tiers, "_run_pw", _fake_run_pw(rc=0, out=out))
    got = tiers._navigate_capture("s", "https://example.com", timeout=5)
    assert got == {"ok": True, "status": 404,
                   "headers": {"server": "cloudflare", "cf-ray": "abc"},
                   "url": "https://example.com/final"}


def test_non_int_status_becomes_none(monkeypatch):
    """A drifted status type must not reach is_real_content's `<` comparison."""
    out = _result_block({"ok": True, "status": "200", "headers": {}, "url": "u"})
    monkeypatch.setattr(tiers, "_run_pw", _fake_run_pw(rc=0, out=out))
    assert tiers._navigate_capture("s", "https://x.test", timeout=5)["status"] is None


def test_non_dict_headers_become_empty(monkeypatch):
    out = _result_block({"ok": True, "status": 200, "headers": "nope", "url": "u"})
    monkeypatch.setattr(tiers, "_run_pw", _fake_run_pw(rc=0, out=out))
    assert tiers._navigate_capture("s", "https://x.test", timeout=5)["headers"] == {}


# ---- navigation failure is NOT route failure ---------------------------------

def test_navigation_failure_is_not_route_failure(monkeypatch):
    """ok=False must be distinguishable from None, or the caller re-navigates."""
    out = _result_block({"ok": False, "error": "net::ERR_NAME_NOT_RESOLVED",
                         "url": "about:blank"})
    monkeypatch.setattr(tiers, "_run_pw", _fake_run_pw(rc=0, out=out))
    got = tiers._navigate_capture("s", "https://nope.test", timeout=5)
    assert got is not None, "a failed navigation must NOT be reported as an absent route"
    assert got["ok"] is False
    assert "ERR_NAME_NOT_RESOLVED" in got["error"]


# ---- the cmd.exe metacharacter fix -------------------------------------------

def test_url_never_reaches_argv_raw(monkeypatch):
    """`&` in a URL must never reach cmd.exe -- encode as char codes."""
    called = []
    monkeypatch.setattr(tiers, "_run_pw", _fake_run_pw(rc=1, record=called))
    url = "https://api.github.com/x?per_page=1&anon=false"
    tiers._navigate_capture("s", url, timeout=5)
    assert called, "expected one playwright-cli invocation"
    argv = called[0]
    joined = " ".join(argv)
    assert url not in joined, "raw URL in argv: cmd.exe will split it on '&'"
    assert "&" not in joined, "an ampersand reached the argv"
    assert "String.fromCodePoint(" in joined
    # the codes for "https" must be present, proving the URL is carried
    assert ",".join(str(ord(c)) for c in "https") in joined


def test_astral_characters_survive_the_encoding(monkeypatch):
    """A non-BMP character must round-trip through the URL encoding.

    `String.fromCharCode` truncates to a 16-bit code unit, so ord() = 128512
    (U+1F600) came back as U+F600 -- a different character, and therefore a
    different URL. MEASURED against the live run-code sandbox 2026-08-29:
    fromCharCode gave 'https://example.com/\uf600', fromCodePoint gave the
    emoji unchanged. This test fails if anyone reverts to fromCharCode.
    """
    called = []
    monkeypatch.setattr(tiers, "_run_pw", _fake_run_pw(rc=1, record=called))
    url = "https://example.com/\U0001F600/page"
    tiers._navigate_capture("s", url, timeout=5)
    joined = " ".join(called[0])
    assert "String.fromCodePoint(" in joined, "fromCharCode truncates astral code points"
    assert "128512" in joined, "the full code point must be emitted, not a truncated unit"
    # and the reconstruction must be exact: every code point, in order
    codes = ",".join(str(ord(c)) for c in url)
    assert codes in joined


# ---- integration: fetch_html degrades to goto --------------------------------

def test_fetch_html_falls_back_to_goto(monkeypatch):
    """run-code absent -> goto is used, fetch still succeeds, status is None."""
    seen = []
    html_payload = "<html><body><p>hello</p></body></html>"

    def fake(args, timeout):
        verb = args[1] if len(args) > 1 else ""
        seen.append(verb)
        if verb == "run-code":
            return 1, "", "unknown command"
        if verb == "eval":
            js = args[2]
            if "outerHTML" in js and "location.href" in js:
                observation = [html_payload, "https://example.com/", []]
                return 0, "### Result\n" + json.dumps(observation) + "\n", ""
            raise AssertionError("unexpected non-atomic page read")
        return 0, "", ""

    monkeypatch.setattr(tiers, "_run_pw", fake)
    sess = tiers.PlaywrightSession("headless")
    sess._opened = True  # skip real browser launch and profile claim

    final_url, html, status, headers, candidates = sess.fetch_html(
        "https://example.com", total_timeout=30)
    assert html_payload in html
    assert status is None, "unknown status must stay None, not be invented as 200"
    assert headers is None
    assert candidates == []
    assert final_url == "https://example.com/"
    assert "run-code" in seen and "goto" in seen, "must try run-code, then fall back"
    assert seen.count("eval") == 1, "one task must read HTML, URL and candidates"


def test_timeout_is_navigation_failure_not_absent_route(monkeypatch):
    """A TIMEOUT must not read as 'route unavailable'.

    `_run_pw` reports TimeoutExpired as rc=-1 with "timeout" in the message --
    the SAME rc it uses for a missing binary. Collapsing both to None sends the
    caller into a second navigation via `goto`, doubling the cost of every slow
    fetch and overrunning total_timeout. Cross-model review finding, 2026-08-29.
    """
    monkeypatch.setattr(tiers, "_run_pw",
                        _fake_run_pw(rc=-1, err="playwright-cli timeout after 30s"))
    got = tiers._navigate_capture("s", "https://slow.test", timeout=5)
    assert got is not None, "a timeout must NOT be reported as an absent route"
    assert got["ok"] is False
    assert "timeout" in got["error"].lower()


def test_missing_binary_is_still_an_absent_route(monkeypatch):
    """The control for the test above: rc=-1 WITHOUT 'timeout' still means absent.

    Without this pair, widening the timeout check to all of rc=-1 would pass
    the test above while silently killing the fallback.
    """
    monkeypatch.setattr(tiers, "_run_pw",
                        _fake_run_pw(rc=-1, err="playwright-cli not found on PATH"))
    assert tiers._navigate_capture("s", "https://x.test", timeout=5) is None


def test_fetch_html_does_not_retry_goto_after_timeout(monkeypatch):
    """End-to-end: a run-code timeout must not cause a second navigation."""
    seen = []

    def fake(args, timeout):
        verb = args[1] if len(args) > 1 else ""
        seen.append(verb)
        if verb == "run-code":
            return -1, "", "playwright-cli timeout after 30s"
        return 0, "", ""

    monkeypatch.setattr(tiers, "_run_pw", fake)
    sess = tiers.PlaywrightSession("headless")
    sess._opened = True

    with pytest.raises(RuntimeError, match="timeout"):
        sess.fetch_html("https://slow.test", total_timeout=30)
    assert "goto" not in seen, "must NOT re-navigate with goto after a timeout"


def test_fetch_html_surfaces_navigation_failure_without_retrying(monkeypatch):
    """A real navigation failure must raise, not silently re-navigate via goto."""
    seen = []
    out = _result_block({"ok": False, "error": "net::ERR_CONNECTION_REFUSED",
                         "url": "about:blank"})

    def fake(args, timeout):
        verb = args[1] if len(args) > 1 else ""
        seen.append(verb)
        if verb == "run-code":
            return 0, out, ""
        return 0, "", ""

    monkeypatch.setattr(tiers, "_run_pw", fake)
    sess = tiers.PlaywrightSession("headless")
    sess._opened = True

    with pytest.raises(RuntimeError, match="ERR_CONNECTION_REFUSED"):
        sess.fetch_html("https://nope.test", total_timeout=30)
    assert "goto" not in seen, "must NOT re-navigate after a real navigation failure"


@pytest.mark.parametrize("stdout", [
    '### Result\n["html", "https://example.com/", null]\n',
    '### Result\n["html", "https://example.com/"]\n',
    '### Result\nnot-json\n',
    '### Error\nno page result\n',
])
def test_invalid_observation_output_raises_without_falling_back_or_renavigating(monkeypatch, stdout):
    seen = []

    def fake(args, timeout):
        verb = args[1]
        seen.append(verb)
        if verb == "eval":
            return 0, stdout, ""
        return 0, "", ""

    monkeypatch.setattr(tiers, "_run_pw", fake)
    monkeypatch.setattr(tiers, "_navigate_capture", lambda *args, **kwargs: {
        "ok": True, "status": 200, "headers": {}, "url": "https://example.com/"})
    session = tiers.PlaywrightSession("headless")
    session._opened = True

    with pytest.raises(tiers._CDPError, match="page read"):
        session.fetch_html("https://example.com/", total_timeout=30)

    assert seen == ["tab-new", "eval", "tab-close"]
