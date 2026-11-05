"""Slice A of the escalation gate: TierResult carries the evidence a verdict needs."""

import email.message
import sys
import types
import urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import fetch
import safety
import state
import tiers


# A body long enough to clear the content gate, so the 2xx tests assert
# population on a SUCCESSFUL result rather than on a rejected one.
PROSE = ("<html><head><title>An ordinary page</title></head><body>"
         + "<p>Sentences of ordinary prose that the extractor keeps. </p>" * 40
         + "</body></html>")


def _message(pairs):
    """An `email.message.Message`, which is what urllib hands back as `.headers`
    on both the response and the HTTPError. Duplicate names are preserved in it
    and lost by `.items()` -- that asymmetry is the point of half this file."""
    m = email.message.Message()
    for k, v in pairs:
        m[k] = v
    return m


class _Resp:
    """Shaped like http.client.HTTPResponse exactly where `_tier_http_urllib`
    touches it."""

    def __init__(self, url, status=200, headers=(), body=b""):
        self._url = url
        self.status = status
        self.headers = _message(headers)
        self._body = body
        self.closed = False

    def geturl(self):
        return self._url

    def read(self, amt=None):
        body, self._body = self._body, b""
        return body

    def close(self):
        self.closed = True


@pytest.fixture()
def opener(monkeypatch):
    """Replace `safe_opener()`. `box['response']` is returned, `box['raises']`
    is raised, and `box['chain']` -- [(code, newurl), ...] -- is driven through
    the REAL `safety._SafeRedirectHandler` first."""
    box = {"raises": None, "response": None, "chain": []}

    class _Opener:
        def open(self, req, timeout=None):
            # urllib calls redirect_request from inside
            # HTTPRedirectHandler.http_error_302, which cannot be reached
            # without a socket. This reproduces the one call it makes, against
            # the REAL handler -- so the hop recording under test is the shipped
            # code and not a copy of it.
            handler = safety._SafeRedirectHandler()
            for code, newurl in box["chain"]:
                nxt = handler.redirect_request(
                    req, None, code, "Found",
                    _message([("Location", newurl)]), newurl)
                assert nxt is not None, "the real handler refused a GET redirect"
                req = nxt
            if box["raises"] is not None:
                raise box["raises"]
            return box["response"]

    monkeypatch.setattr(tiers, "safe_opener", lambda: _Opener())
    return box


# ---- tier 0, urllib: the 2xx path -------------------------------------------

def test_tier0_2xx_stores_the_response_headers(opener):
    opener["response"] = _Resp(
        "http://origin.test/",
        headers=[("Content-Type", "text/html; charset=utf-8"),
                 ("Server", "nginx"),
                 ("X-Datadome", "protected")],
        body=PROSE.encode())
    r = tiers._tier_http_urllib("http://origin.test/", allow_private=True)
    assert r.ok, r.error
    assert r.headers.get("Server") == "nginx"
    assert r.headers.get("X-Datadome") == "protected"


def test_tier0_2xx_keeps_every_set_cookie_line_separate(opener):
    """A vendor cookie arrives beside two ordinary ones and must not be the only
    survivor -- nor the only casualty. `.items()` keeps ONE line; `.get_all()`
    keeps all three, and the vendor arms of the gate read the raw lines."""
    lines = ["sid=abc; Path=/; HttpOnly",
             "datadome=XYZ; Max-Age=31536000; Domain=.origin.test",
             "_pxvid=9f2; Path=/"]
    opener["response"] = _Resp(
        "http://origin.test/",
        headers=([("Content-Type", "text/html")]
                 + [("Set-Cookie", v) for v in lines]),
        body=PROSE.encode())
    r = tiers._tier_http_urllib("http://origin.test/", allow_private=True)
    assert r.set_cookie == lines
    # The premise, asserted rather than assumed: the flat dict really does lose
    # two of the three, which is why a separate field exists at all.
    assert list(r.headers).count("Set-Cookie") <= 1


# ---- tier 0, urllib: the HTTPError path -- the one that matters -------------

def test_tier0_httperror_stores_headers_including_www_authenticate(opener):
    """401 + WWW-Authenticate is the clearest login signal a server can send and
    it was unreachable: this branch read `e.code` and `e.reason` and nothing
    else."""
    hdrs = _message([("WWW-Authenticate", 'Basic realm="git-private"'),
                     ("Content-Type", "text/html")])
    opener["raises"] = urllib.error.HTTPError(
        "http://origin.test/", 401, "Unauthorized", hdrs, None)
    r = tiers._tier_http_urllib("http://origin.test/", allow_private=True)
    assert not r.ok and r.status == 401
    assert r.headers.get("WWW-Authenticate") == 'Basic realm="git-private"'


def test_tier0_httperror_keeps_every_set_cookie_line(opener):
    """The challenge cookies land on the ERROR response, not the 200 -- which is
    exactly why reading them only on the success path would have been useless."""
    lines = ["datadome=Q1; Path=/", "aws-waf-token=77; Path=/"]
    hdrs = _message([("Content-Type", "text/html")]
                    + [("Set-Cookie", v) for v in lines])
    opener["raises"] = urllib.error.HTTPError(
        "http://origin.test/", 403, "Forbidden", hdrs, None)
    r = tiers._tier_http_urllib("http://origin.test/", allow_private=True)
    assert r.set_cookie == lines


def test_tier0_httperror_records_the_honest_failure_class(opener):
    """The branch this file reopens must NAME what stopped the fetch.

    A 404 the origin really sent is persistent, and recording the transient
    `other` instead told the blocklist that nothing had been seen -- which left
    a walled domain at `http` for good rather than letting it reach a tier that
    could serve it. Reading the headers and recording the class are still two
    separate acts; both now happen."""
    opener["raises"] = urllib.error.HTTPError(
        "http://origin.test/", 404, "Not Found",
        _message([("Content-Type", "text/html")]), None)
    r = tiers._tier_http_urllib("http://origin.test/", allow_private=True)
    assert r.failure_class == "origin_4xx_block", r.failure_class
    assert r.failure_class in state.PERSISTENT_FAILURE_CLASSES


# ---- tier 0, urllib: the hop list -------------------------------------------

def test_tier0_hops_carry_status_from_url_and_location(opener):
    """urllib follows redirects inside `open()`, so the tier sees only the final
    URL. `_SafeRedirectHandler` is the only thing in this process that sees each
    hop, and it already re-validates every one -- this records what it saw."""
    opener["chain"] = [(302, "http://origin.test/step"),
                       (301, "http://accounts.origin.test/login?next=/")]
    opener["response"] = _Resp("http://accounts.origin.test/login?next=/",
                               headers=[("Content-Type", "text/html")],
                               body=PROSE.encode())
    r = tiers._tier_http_urllib("http://origin.test/", allow_private=True)
    assert r.hops == [
        (302, "http://origin.test/", "http://origin.test/step"),
        (301, "http://origin.test/step", "http://accounts.origin.test/login?next=/"),
    ]


def test_tier0_hops_survive_a_redirect_chain_that_ends_in_an_error(opener):
    """The login-chain evidence is on the FAILING fetch. A chain recorded only
    when the fetch succeeds tells the gate nothing it needs."""
    opener["chain"] = [(302, "http://accounts.origin.test/login")]
    opener["raises"] = urllib.error.HTTPError(
        "http://accounts.origin.test/login", 403, "Forbidden",
        _message([("Content-Type", "text/html")]), None)
    r = tiers._tier_http_urllib("http://origin.test/", allow_private=True)
    assert r.hops == [(302, "http://origin.test/",
                       "http://accounts.origin.test/login")]


def test_redirect_chain_is_still_a_list_of_url_strings_beside_hops(opener):
    """`redirect_chain` KEEPS its type and meaning. fetch.py iterates it and
    re-validates every element against the SSRF guard; an element that stopped
    being a URL string would disable that check silently."""
    opener["chain"] = [(302, "http://origin.test/step")]
    opener["response"] = _Resp("http://origin.test/step",
                               headers=[("Content-Type", "text/html")],
                               body=PROSE.encode())
    r = tiers._tier_http_urllib("http://origin.test/", allow_private=True)
    assert r.redirect_chain == ["http://origin.test/step"]
    assert all(isinstance(x, str) for x in r.redirect_chain)
    # len FIRST: `all(...)` over an empty list is vacuously true, so without
    # this the type assertion below would confirm an unpopulated field.
    assert len(r.hops) == 1
    assert all(isinstance(x, tuple) and len(x) == 3 for x in r.hops)


def test_an_unsafe_target_in_redirect_chain_still_fails_the_fetch(monkeypatch,
                                                                 tmp_path):
    """The TOCTOU re-validation in fetch.py's cascade reads `redirect_chain` and
    nothing else. Populating `hops` beside it must not move that check."""
    table = {"example.com": ["93.184.216.34"], "internal.test": ["127.0.0.1"]}

    def resolve(host):
        if host not in table:
            raise safety.UnsafeURL(f"DNS resolution failed for {host!r}")
        return table[host]

    monkeypatch.setattr(safety, "_resolve_all_ips", resolve)
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    conn = state.open_db()
    try:
        monkeypatch.setattr(tiers, "tier_http", lambda url, **kw: tiers.TierResult(
            ok=True, tier="http", content="x" * 800, status=200,
            redirect_chain=["http://internal.test/"],
            hops=[(302, "https://example.com/", "http://internal.test/")],
            headers={"Content-Type": "text/html"}))
        env = fetch.cascade_fetch("https://example.com/", conn=conn,
                                  no_cache=True, max_tier="http",
                                  search_path="/stub/search.py")
    finally:
        conn.close()
    assert "unsafe redirect target http://internal.test/" in (
        env["tier_attempts"][0]["error"])


# ---- tier 0, impersonate ----------------------------------------------------

class _CurlHeaders:
    """Shaped like `curl_cffi.requests.headers.Headers` where the tier touches
    it. Read off the installed 0.16.2 source: `.items()` JOINS duplicate names
    with ", ", and `.get_list(name)` is the only accessor that returns the raw
    lines."""

    def __init__(self, pairs):
        self._pairs = list(pairs)

    def items(self):
        out: dict[str, str] = {}
        for k, v in self._pairs:
            out[k] = f"{out[k]}, {v}" if k in out else v
        return out.items()

    def get_list(self, key, split_commas=False):
        kl = key.lower()
        return [v for k, v in self._pairs if k.lower() == kl]


class _CurlResp:
    def __init__(self, status=200, headers=(), body=b"", reason="OK"):
        self.status_code = status
        self.reason = reason
        self.headers = _CurlHeaders(headers)
        self._body = body

    def iter_content(self):
        yield self._body

    def close(self):
        pass


@pytest.fixture()
def fake_curl(monkeypatch):
    """Install a fake `curl_cffi` so these run on a box without it. Set
    `log['script']` to the responses the hop loop should see, in order."""
    log = {"script": [], "requests": []}

    class _Exc(Exception):
        pass

    class _Session:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            log["requests"].append(url)
            assert log["script"], f"unscripted request to {url}"
            return log["script"].pop(0)

    module = types.ModuleType("curl_cffi")
    module.CurlOpt = types.SimpleNamespace(PROTOCOLS_STR="p", REDIR_PROTOCOLS_STR="r")
    module.CurlECode = types.SimpleNamespace(COULDNT_CONNECT=7)
    requests_mod = types.ModuleType("curl_cffi.requests")
    requests_mod.exceptions = types.SimpleNamespace(
        DNSError=type("DNSError", (_Exc,), {}),
        Timeout=type("Timeout", (_Exc,), {}),
        SSLError=type("SSLError", (_Exc,), {}),
        ConnectionError=type("ConnectionError_", (_Exc,), {}))
    requests_mod.Session = lambda **kw: _Session(**kw)
    module.requests = requests_mod
    monkeypatch.setitem(sys.modules, "curl_cffi", module)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", requests_mod)
    return log


def test_impersonate_stores_headers_and_raw_set_cookie(fake_curl):
    """`.items()` would hand back one comma-joined string for the two cookies;
    `.get_list()` is what keeps them apart."""
    lines = ["datadome=Q1; Path=/", "sid=7; HttpOnly"]
    fake_curl["script"] = [_CurlResp(
        200,
        [("Content-Type", "text/html"), ("X-Datadome", "protected")]
        + [("Set-Cookie", v) for v in lines],
        PROSE.encode())]
    r = tiers._tier_http_impersonate("http://origin.test/", allow_private=True)
    assert r.headers.get("X-Datadome") == "protected"
    assert r.set_cookie == lines


def test_impersonate_hops_carry_status_from_url_and_location(fake_curl):
    """This arm already tracked a URL list for `redirect_chain`. The tuple list
    is new and additional -- the per-hop status and origin were never kept."""
    fake_curl["script"] = [
        _CurlResp(302, [("Location", "/login?next=%2F")]),
        _CurlResp(200, [("Content-Type", "text/html")], PROSE.encode()),
    ]
    r = tiers._tier_http_impersonate("http://origin.test/", allow_private=True)
    assert r.hops == [(302, "http://origin.test/",
                       "http://origin.test/login?next=%2F")]
    assert r.redirect_chain == ["http://origin.test/login?next=%2F"]
    assert all(isinstance(x, str) for x in r.redirect_chain)


def test_impersonate_non_2xx_stores_headers_and_names_the_status(fake_curl):
    """The second tier-0 transport, fixed alongside the first so the two arms
    cannot answer differently about one tier. The body is unread here
    (`stream=True`, nothing has consumed it), so the STATUS alone decides and a
    401 is `origin_4xx_block`. This class never reaches `record_failure` --
    `tier_http` returns the FIRST attempt whenever the second fails -- so what it
    fixes is the `sub_attempts` row the envelope shows the reader."""
    fake_curl["script"] = [_CurlResp(
        401, [("WWW-Authenticate", 'Bearer realm="api"'),
              ("Set-Cookie", "aws-waf-token=77; Path=/")],
        b"nope", reason="Unauthorized")]
    r = tiers._tier_http_impersonate("http://origin.test/", allow_private=True)
    assert not r.ok and r.status == 401
    assert r.headers.get("WWW-Authenticate") == 'Bearer realm="api"'
    assert r.set_cookie == ["aws-waf-token=77; Path=/"]
    assert r.failure_class == "origin_4xx_block", r.failure_class


# ---- tier 1/2 ---------------------------------------------------------------

@pytest.fixture()
def session(monkeypatch):
    """Replace `get_session`. `box['ret']` is the 5-tuple `fetch_html` returns.

    NO BROWSER IS REACHABLE FROM THIS FILE: `get_session` is the only door to
    one on this path, and it is shut here before `tier_playwright` is called."""
    box = {"ret": None}

    class _Session:
        def fetch_html(self, url, total_timeout):
            return box["ret"]

    monkeypatch.setattr(tiers, "get_session", lambda mode: _Session())
    return box


def test_tier1_stores_the_captured_nav_headers(session):
    session["ret"] = ("http://origin.test/", PROSE, 200,
                      {"content-type": "text/html", "x-datadome": "protected"}, [])
    r = tiers.tier_playwright("http://origin.test/", "headless", allow_private=True)
    assert r.headers == {"content-type": "text/html", "x-datadome": "protected"}


def test_tier1_splits_playwright_joined_set_cookie_into_lines(session):
    """`allHeaders()` flattens duplicates into one newline-joined value. Splitting
    it back is the most we can recover, and the plan's § 8.6 note says a lost
    cookie can only make VENDOR_PRESENT false -- i.e. no window. Never invent."""
    session["ret"] = ("http://origin.test/", PROSE, 403,
                      {"content-type": "text/html",
                       "set-cookie": "datadome=Q1; Path=/\n_pxvid=9f2; Path=/"}, [])
    r = tiers.tier_playwright("http://origin.test/", "headless", allow_private=True)
    assert r.set_cookie == ["datadome=Q1; Path=/", "_pxvid=9f2; Path=/"]


def test_tier1_single_set_cookie_is_a_one_element_list(session):
    session["ret"] = ("http://origin.test/", PROSE, 200,
                      {"Set-Cookie": "sid=abc; Path=/"}, [])
    r = tiers.tier_playwright("http://origin.test/", "headless", allow_private=True)
    assert r.set_cookie == ["sid=abc; Path=/"]


def test_tier1_goto_fallback_stores_an_empty_dict_not_a_fabricated_one(session):
    """`_navigate_capture` returns None when the run-code route is unavailable
    and the tier falls back to plain `goto`. There are no headers on that path.
    The status is already a documented PLACEHOLDER 200 there; the headers must
    not acquire a matching fiction, or the gate opens a window on evidence
    nobody collected (plan § 8.4).

    THIS ONE CANNOT FAIL-FIRST ON AN ABSENT FIX, because empty IS the unfixed
    value. It is a guard against the WRONG fix -- one that fabricates headers
    here -- so the status assertion carries the weight: it proves the fallback
    path really ran rather than the tier having returned early.
    """
    session["ret"] = ("http://origin.test/", PROSE, None, None, [])
    r = tiers.tier_playwright("http://origin.test/", "headless", allow_private=True)
    assert r.status == 200, "the documented placeholder: this IS the goto path"
    assert r.headers == {}
    assert r.set_cookie == []
    assert r.hops == []


def test_tier_result_defaults_are_independent_per_instance():
    """One tier's mutable response/UI evidence must never leak to the next."""
    a, b = tiers.TierResult(ok=True, tier="http"), tiers.TierResult(ok=True, tier="http")
    a.headers["x"] = "1"
    a.hops.append((302, "u", "v"))
    a.set_cookie.append("c=1")
    a.access_gate_candidates.append({"visible": True, "text": "wall", "controls": []})
    assert (b.headers, b.hops, b.set_cookie) == ({}, [], [])
    assert b.access_gate_candidates == []
