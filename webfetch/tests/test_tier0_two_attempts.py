"""Tier 0's SECOND transport attempt, and the guard rails around it."""

import http.client
import sys
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import safety
import state
import tiers
from tiers import TierResult


# ---- the two transports, replaced together ----------------------------------

class _Transports:
    """Recorder for both tier-0 transports. `first` / `second` are what they return."""

    def __init__(self):
        self.first: TierResult | None = None
        self.second: TierResult | None = None
        self.urllib_calls: list[tuple] = []
        self.impersonate_calls: list[tuple] = []


@pytest.fixture()
def transports(monkeypatch):
    t = _Transports()

    def fake_urllib(url, **kw):
        t.urllib_calls.append((url, kw))
        return t.first

    def fake_impersonate(url, **kw):
        t.impersonate_calls.append((url, kw))
        return t.second

    monkeypatch.setattr(tiers, "_tier_http_urllib", fake_urllib)
    monkeypatch.setattr(tiers, "_tier_http_impersonate", fake_impersonate)
    return t


def ok_result(content="# a real page\n\nwith prose in it.", **kw):
    return TierResult(ok=True, tier="http", content=content, status=200,
                      elapsed_ms=11, **kw)


def failed_result(*, status=None, error="boom", failure_class="other", **kw):
    return TierResult(ok=False, tier="http", status=status, error=error,
                      failure_class=failure_class, elapsed_ms=7, **kw)


# ---- when the second attempt runs, and when it must not ---------------------

def test_urllib_success_never_reaches_the_second_transport(transports):
    """The common case. An ordinary page must cost exactly one request."""
    transports.first = ok_result()
    r = tiers.tier_http("http://origin.test/")
    assert r is transports.first
    assert transports.urllib_calls, "the first transport was not wired in"
    assert transports.impersonate_calls == []
    assert r.sub_attempts is None, "a one-attempt fetch keeps today's envelope"


def test_a_retryable_failure_gets_the_second_transport_and_its_success(transports):
    """forbes.com's shape: a wall on the first attempt, real content on the second."""
    transports.first = failed_result(status=403, error="HTTP 403 Forbidden")
    transports.second = ok_result(content="# Forbes\n\n" + "x" * 6000)
    r = tiers.tier_http("http://origin.test/")
    assert len(transports.impersonate_calls) == 1
    assert r.ok and r.content == transports.second.content
    assert [row["tier"] for row in r.sub_attempts] == ["http(urllib)",
                                                       "http(impersonate)"]
    assert [row["ok"] for row in r.sub_attempts] == [False, True]


def test_when_both_fail_the_urllib_result_is_what_comes_back(transports):
    """The first attempt's error is the more representative diagnosis, and it is
    what the blocklist has recorded for this tier since the skill existed."""
    transports.first = failed_result(status=403, error="HTTP 403 Forbidden",
                                     failure_class="cloudflare_challenge")
    transports.second = failed_result(status=500, error="HTTP 500 Server Error")
    r = tiers.tier_http("http://origin.test/")
    assert len(transports.impersonate_calls) == 1
    assert r is transports.first
    assert r.status == 403 and r.error == "HTTP 403 Forbidden"
    assert r.failure_class == "cloudflare_challenge"
    assert r.sub_attempts[1]["error"] == "HTTP 500 Server Error"


@pytest.mark.parametrize("status", [404, 410])
def test_gone_means_gone_no_second_attempt(transports, status):
    """9 of the 18 corpus URLs are plain 404s and both arms score identically on
    every one. Impersonation cannot conjure a page that is not there."""
    transports.first = failed_result(status=status, error=f"HTTP {status} x")
    r = tiers.tier_http("http://origin.test/")
    assert transports.urllib_calls, "the first transport was not wired in"
    assert transports.impersonate_calls == []
    assert r is transports.first


@pytest.mark.parametrize("failure_class", ["dns_fail", "connect_refused"])
def test_a_name_that_does_not_resolve_and_a_closed_port_are_not_retried(
        transports, failure_class):
    """A different TLS stack does not resolve a name or find a closed port."""
    transports.first = failed_result(failure_class=failure_class)
    r = tiers.tier_http("http://origin.test/")
    assert transports.urllib_calls, "the first transport was not wired in"
    assert transports.impersonate_calls == []
    assert r is transports.first


def test_a_document_we_could_not_read_buys_no_second_download(transports):
    """skip_browser_tiers means the BYTES arrived and our own extractor could not
    use them -- today only tier_http's PDF branch, where the status is 200 and
    pypdf got no text out of the file. A second transport re-downloads the same
    document, up to MAX_HTTP_BYTES = 10 MB of it, for pypdf to fail on
    identically: impersonation changes who the origin thinks we are, not what the
    file contains."""
    transports.first = failed_result(
        status=200, error="PDF text extraction failed: 0 chars",
        skip_browser_tiers=True)
    r = tiers.tier_http("http://origin.test/paper.pdf")
    assert transports.urllib_calls, "the first transport was not wired in"
    assert transports.impersonate_calls == []
    assert r is transports.first
    assert r.skip_browser_tiers is True, "and the signal still reaches the cascade"


def test_an_unsafe_url_is_not_retried_and_keeps_its_error(transports):
    """We never reached the network: our own SSRF guard refused the URL, and the
    second transport re-validates and would refuse it again a DNS lookup later."""
    transports.first = failed_result(
        error="unsafe URL: hostname '169.254.169.254' resolves to 169.254.169.254 "
              "(link-local); pass --allow-private to override")
    r = tiers.tier_http("http://169.254.169.254/latest/meta-data/")
    assert transports.urllib_calls, "the first transport was not wired in"
    assert transports.impersonate_calls == []
    assert r.error.startswith("unsafe URL: ")


def test_a_spent_budget_buys_no_second_attempt(transports):
    """total_timeout=0 is the whole budget already gone when the first attempt
    returns -- the second one would have nothing to spend."""
    transports.first = failed_result(status=403, error="HTTP 403 Forbidden")
    r = tiers.tier_http("http://origin.test/", total_timeout=0)
    assert transports.urllib_calls, "the first transport was not wired in"
    assert transports.impersonate_calls == []
    assert r is transports.first


def test_the_second_attempt_gets_only_what_is_left_of_the_budget(monkeypatch):
    """Two attempts share ONE total_timeout; the second cannot restart the clock.

    A frozen clock the first attempt advances by hand -- with both transports
    stubbed the real elapsed time is microseconds, which cannot tell a shared
    budget from a fresh one.
    """
    clock = {"t": 1000.0}
    monkeypatch.setattr(tiers.time, "monotonic", lambda: clock["t"])
    first = failed_result(status=403)
    calls = []

    def slow_urllib(url, **kw):
        clock["t"] += 12.0
        return first

    monkeypatch.setattr(tiers, "_tier_http_urllib", slow_urllib)
    monkeypatch.setattr(tiers, "_tier_http_impersonate",
                        lambda url, **kw: calls.append(kw) or failed_result())
    r = tiers.tier_http("http://origin.test/", total_timeout=30.0)
    assert calls[0]["total_timeout"] == pytest.approx(18.0)
    assert r.elapsed_ms == 12000, "the tier row reports both attempts, not one"


# ---- a fake curl_cffi, shaped like the real one -----------------------------

def _fake_exceptions() -> types.SimpleNamespace:
    """The real 0.16.2 hierarchy, which is what makes clause order load-bearing:
    every error is an OSError, ConnectTimeout is BOTH a ConnectionError and a
    Timeout, and DNSError and SSLError are both ConnectionErrors."""
    class CurlError(Exception):
        pass

    class RequestException(CurlError, OSError):
        pass

    class ConnectionError_(RequestException):
        pass

    class DNSError(ConnectionError_):
        pass

    class Timeout(RequestException):
        pass

    class ConnectTimeout(ConnectionError_, Timeout):
        pass

    class ReadTimeout(Timeout):
        pass

    class SSLError(ConnectionError_):
        pass

    return types.SimpleNamespace(
        CurlError=CurlError, RequestException=RequestException,
        ConnectionError=ConnectionError_, DNSError=DNSError, Timeout=Timeout,
        ConnectTimeout=ConnectTimeout, ReadTimeout=ReadTimeout, SSLError=SSLError,
    )


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b"", reason="OK",
                 chunks=None, chunk_log=None):
        self.status_code = status
        self.reason = reason
        self.headers = dict(headers or {})
        self._body = body
        self._chunks = chunks
        self.chunk_log = chunk_log if chunk_log is not None else {}
        self.closed = False

    def iter_content(self):
        if self._chunks is not None:
            for i, chunk in enumerate(self._chunks, start=1):
                self.chunk_log["pulled"] = i
                yield chunk
            self.chunk_log["exhausted"] = True
            return
        yield self._body

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, script, log, **kw):
        self._script = list(script)
        self._log = log
        self._log["session_kwargs"] = kw
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.closed = True
        return False

    def get(self, url, **kw):
        self._log["requests"].append((url, kw))
        # Fires AFTER kw is recorded, so a hook that advances a frozen clock
        # cannot alter the value under test on the hop that recorded it.
        hook = self._log.get("on_get")
        if hook:
            hook()
        if not self._script:
            raise AssertionError(f"unscripted request to {url}")
        nxt = self._script.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


@pytest.fixture()
def fake_curl(monkeypatch):
    """Install a fake `curl_cffi`. Returns a log; set log['script'] before the call."""
    log = {"requests": [], "script": []}
    exceptions = _fake_exceptions()

    module = types.ModuleType("curl_cffi")
    module.CurlOpt = types.SimpleNamespace(PROTOCOLS_STR="protocols",
                                           REDIR_PROTOCOLS_STR="redir_protocols")
    module.CurlECode = types.SimpleNamespace(COULDNT_CONNECT=7, RECV_ERROR=56)
    requests_mod = types.ModuleType("curl_cffi.requests")
    requests_mod.exceptions = exceptions
    requests_mod.Session = lambda **kw: FakeSession(log["script"], log, **kw)
    module.requests = requests_mod

    monkeypatch.setitem(sys.modules, "curl_cffi", module)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", requests_mod)
    log["exceptions"] = exceptions
    log["CurlECode"] = module.CurlECode
    return log


@pytest.fixture()
def fake_dns(monkeypatch):
    """Resolve a fixed table instead of asking a resolver, so the REAL
    validate_url_safety runs -- scheme check, IP classification and all -- with
    no DNS traffic. `internal.test` is the SSRF target: a name that looks
    ordinary and resolves to loopback."""
    table = {
        "origin.test": ["93.184.216.34"],
        "other.test": ["93.184.216.35"],
        "internal.test": ["127.0.0.1"],
        "metadata.test": ["169.254.169.254"],
    }

    def resolve(host):
        if host not in table:
            raise safety.UnsafeURL(f"DNS resolution failed for {host!r}: not in table")
        return table[host]

    monkeypatch.setattr(safety, "_resolve_all_ips", resolve)
    return table


HTML = (b"<html><head><title>A page</title></head><body><p>"
        + b"This is a real article with enough prose to be judged as content. " * 6
        + b"</p></body></html>")


# ---- the SSRF guarantee, reimplemented and therefore testable ---------------

def test_a_redirect_to_loopback_is_refused_on_the_impersonate_path(fake_curl, fake_dns):
    """THE HIGHEST-RISK PART OF THIS CHANGE. libcurl would follow this hop inside
    the C library, where safety._SafeRedirectHandler cannot see it."""
    fake_curl["script"] = [
        FakeResponse(status=302, headers={"Location": "http://internal.test/admin"}),
        FakeResponse(status=200, headers={"Content-Type": "text/html"}, body=HTML),
    ]
    r = tiers._tier_http_impersonate("http://origin.test/")
    assert not r.ok
    assert "unsafe redirect target http://internal.test/admin" in r.error
    assert r.failure_class == "other"
    assert len(fake_curl["requests"]) == 1, "the loopback hop was actually fetched"


def test_a_redirect_to_the_metadata_address_is_refused(fake_curl, fake_dns):
    """169.254.169.254 is the address the guard exists for. The check runs on the
    RESOLVED target, so a name that looks ordinary does not get past it."""
    fake_curl["script"] = [
        FakeResponse(status=301, headers={"Location": "http://metadata.test/latest/"}),
        FakeResponse(status=200, headers={"Content-Type": "text/html"}, body=HTML),
    ]
    r = tiers._tier_http_impersonate("http://origin.test/")
    assert not r.ok and "unsafe redirect target" in r.error
    assert len(fake_curl["requests"]) == 1


def test_a_safe_redirect_is_followed_and_recorded(fake_curl, fake_dns):
    """The positive control: refusing everything would pass the test above too."""
    fake_curl["script"] = [
        FakeResponse(status=302, headers={"Location": "/moved"}),
        FakeResponse(status=200, headers={"Content-Type": "text/html"}, body=HTML),
    ]
    r = tiers._tier_http_impersonate("http://origin.test/start")
    assert r.ok, r.error
    assert [u for u, _ in fake_curl["requests"]] == ["http://origin.test/start",
                                                     "http://origin.test/moved"]
    assert r.redirect_chain == ["http://origin.test/moved"]
    assert r.final_url == "http://origin.test/moved"


def test_the_hop_loop_is_bounded(fake_curl, fake_dns):
    """A redirect chain that never lands must end at OUR bound, not at libcurl's.

    The count is EXACT. `<= MAX_HOPS + 1` proved nothing: a loop that followed
    no redirects at all satisfies it with its single request.
    """
    fake_curl["script"] = [
        FakeResponse(status=302, headers={"Location": f"http://origin.test/{i}"})
        for i in range(40)
    ]
    r = tiers._tier_http_impersonate("http://origin.test/start")
    assert not r.ok
    assert r.error == f"exceeded {tiers._IMPERSONATE_MAX_HOPS} redirects"
    assert len(fake_curl["requests"]) == tiers._IMPERSONATE_MAX_HOPS + 1


def test_a_self_redirect_stops_at_the_repeat_detector_not_the_hop_cap(fake_curl,
                                                                      fake_dns):
    """The other bound, which nothing reached before: a URL that redirects to
    ITSELF is the cheap infinite loop, and `visited.count(target)` stops it after
    _IMPERSONATE_MAX_REPEATS requests -- four -- well short of the 11 the hop cap
    would allow.

    test_the_hop_loop_is_bounded cannot enter this branch: its 40 targets are all
    distinct, so the count never passes 1.
    """
    fake_curl["script"] = [
        FakeResponse(status=302, headers={"Location": "http://origin.test/loop"})
        for _ in range(tiers._IMPERSONATE_MAX_HOPS + 1)
    ]
    r = tiers._tier_http_impersonate("http://origin.test/loop")
    assert not r.ok
    assert r.error == (f"redirect loop: http://origin.test/loop visited "
                       f"{tiers._IMPERSONATE_MAX_REPEATS} times")
    assert len(fake_curl["requests"]) == tiers._IMPERSONATE_MAX_REPEATS
    assert r.status == 302


# ---- the size cap, during the read and not after ----------------------------

def test_the_size_cap_stops_the_read_rather_than_measuring_it_afterwards(
        fake_curl, fake_dns, monkeypatch):
    """A cap checked after the loop has already bought the whole body. The
    generator here records how far it got, so "we stopped early" is observable
    rather than inferred."""
    monkeypatch.setattr(tiers, "MAX_HTTP_BYTES", 4096)
    chunk_log = {"pulled": 0, "exhausted": False}
    fake_curl["script"] = [FakeResponse(
        status=200, headers={"Content-Type": "text/html"},
        chunks=[b"x" * 1024] * 100, chunk_log=chunk_log)]
    r = tiers._tier_http_impersonate("http://origin.test/")
    assert not r.ok and "exceeded 4096 bytes" in r.error
    assert not chunk_log["exhausted"], "the whole body was read before refusing it"
    assert chunk_log["pulled"] == 5, "one chunk past the cap, then stop"


def test_a_declared_content_length_over_the_cap_is_refused_before_the_body(
        fake_curl, fake_dns, monkeypatch):
    monkeypatch.setattr(tiers, "MAX_HTTP_BYTES", 4096)
    chunk_log = {"pulled": 0, "exhausted": False}
    fake_curl["script"] = [FakeResponse(
        status=200, headers={"Content-Type": "text/html", "Content-Length": "99999"},
        chunks=[b"x" * 1024] * 100, chunk_log=chunk_log)]
    r = tiers._tier_http_impersonate("http://origin.test/")
    assert not r.ok and "Content-Length 99999 exceeds max 4096" in r.error
    assert chunk_log["pulled"] == 0


# ---- the budget the hop is actually given -----------------------------------

def test_a_total_timeout_smaller_than_the_read_timeout_clamps_BOTH_halves(
        fake_curl, fake_dns):
    """MEASURED 2026-08-30 by calling the INSTALLED
    curl_cffi.requests.utils.set_curl_options (0.16.2) with a recording curl
    object, stream=True, and reading back what it set:

        (5.0, 3.0) -> CONNECTTIMEOUT_MS 5000, LOW_SPEED_LIMIT 1,
                      LOW_SPEED_TIME 8, TIMEOUT_MS never set
        (5.0, 2.0) -> 5000 / 1 / 7        (2.0, 2.0) -> 2000 / 1 / 4

    So in stream mode there is no wall clock: the connect half is a real connect
    timeout, and the read half only feeds LOW_SPEED_TIME -- a stall detector,
    computed from ceil(connect + read), the SUM. (An earlier version of this
    docstring said LOW_SPEED_TIME came from the READ HALF; the numbers above
    refute that -- 8 seconds for a 3.0 read half.)

    The consequence this test holds: clamping the read half alone left the
    connect half at the caller's value, so read_timeout=15.0 with
    total_timeout=2.0 handed curl (5.0, 2.0) and a host that answers SYN and
    stalls in TLS spent 5 s of a 2 s budget. Our own checks cannot rescue that:
    a stalled socket yields no chunks, so the per-chunk deadline check never
    runs, and control never returns to the top of the hop loop either.
    """
    fake_curl["script"] = [FakeResponse(status=200,
                                        headers={"Content-Type": "text/html"},
                                        body=HTML)]
    tiers._tier_http_impersonate("http://origin.test/", connect_timeout=5.0,
                                 read_timeout=15.0, total_timeout=3.0)
    connect, read = fake_curl["requests"][0][1]["timeout"]
    assert 2.5 < connect <= 3.0, f"the connect half must be clamped too, got {connect}"
    assert 2.5 < read <= 3.0, f"the read half must be the remaining budget, got {read}"


def test_the_clamp_is_recomputed_for_every_hop(fake_curl, fake_dns, monkeypatch):
    """"Fresh for EVERY hop" and not "once before the loop": a redirect chain
    spends the budget as it goes, so a value computed once would hand the last
    hop the seconds the first one had.

    A frozen clock the transport advances by hand -- with the network faked the
    real elapsed time is microseconds, which cannot tell a per-hop clamp from a
    single one.
    """
    clock = {"t": 500.0}
    monkeypatch.setattr(tiers.time, "monotonic", lambda: clock["t"])
    fake_curl["on_get"] = lambda: clock.__setitem__("t", clock["t"] + 2.0)
    fake_curl["script"] = [
        FakeResponse(status=302, headers={"Location": "http://origin.test/b"}),
        FakeResponse(status=302, headers={"Location": "http://origin.test/c"}),
        FakeResponse(status=200, headers={"Content-Type": "text/html"}, body=HTML),
    ]
    r = tiers._tier_http_impersonate("http://origin.test/a", connect_timeout=5.0,
                                     read_timeout=15.0, total_timeout=10.0)
    assert r.ok, r.error
    assert [kw["timeout"][1] for _, kw in fake_curl["requests"]] == [10.0, 8.0, 6.0]
    # The clamp is min(), not an assignment: it bites only where the budget is
    # the smaller number. With 10/8/6 seconds left, a 5.0 connect_timeout is
    # still 5.0 on every hop -- the sibling test above is the one that pins the
    # other direction.
    assert [kw["timeout"][0] for _, kw in fake_curl["requests"]] == [5.0, 5.0, 5.0]


# ---- what we send, and what we do not ---------------------------------------

def test_we_send_no_headers_of_our_own(fake_curl, fake_dns):
    """MEASURED in both directions: forcing DEFAULT_HEADERS on top of the
    impersonation drops the corpus from 5/18 to 3/18 and destroys BOTH rescues,
    because anything we set is appended after the profile's `priority` header --
    a position no real Chrome produces."""
    fake_curl["script"] = [FakeResponse(status=200,
                                        headers={"Content-Type": "text/html"},
                                        body=HTML)]
    r = tiers._tier_http_impersonate("http://origin.test/")
    assert r.ok, r.error
    _, kw = fake_curl["requests"][0]
    assert not kw.get("headers"), f"we sent headers of our own: {kw.get('headers')}"
    assert tiers.CHROME_UA not in repr(kw)
    assert kw["impersonate"] == tiers.IMPERSONATE_TARGET == "chrome150"
    assert kw["allow_redirects"] is False, "we drive the hops, so we can validate them"
    assert kw["stream"] is True


def test_protocols_are_restricted_to_http_and_https(fake_curl, fake_dns):
    """safe_opener() provided this by OMITTING FileHandler; nothing of that
    survives the transport change, so it is restored as a curl option -- on the
    request and on redirects both."""
    fake_curl["script"] = [FakeResponse(status=200,
                                        headers={"Content-Type": "text/html"},
                                        body=HTML)]
    tiers._tier_http_impersonate("http://origin.test/")
    opts = fake_curl["session_kwargs"]["curl_options"]
    assert opts["protocols"] == "http,https"
    assert opts["redir_protocols"] == "http,https"


# ---- exception mapping -------------------------------------------------------

def _raise(exc_name, log, code=None):
    e = getattr(log["exceptions"], exc_name)("network said no")
    if code is not None:
        e.code = code
    return e


def test_dns_failure_is_the_one_clause_whose_position_changes_the_answer(fake_curl,
                                                                         fake_dns):
    """The only ordering constraint in the except chain that changes an answer.

    DNSError is a ConnectionError subclass, so with its own clause deleted -- or
    with the ConnectionError clause moved above it -- whichever handler catches
    it reports type(e).__name__ = "DNSError", a name classify_failure has no arm
    for, and a host that does not resolve comes back as `other`.
    """
    fake_curl["script"] = [_raise("DNSError", fake_curl)]
    r = tiers._tier_http_impersonate("http://origin.test/")
    assert not r.ok
    assert r.failure_class == "dns_fail"


@pytest.mark.parametrize("exc_name,expected", [
    ("ConnectTimeout", "timeout"),
    ("ReadTimeout", "timeout"),
    ("SSLError", "other"),
])
def test_a_network_fault_reports_its_own_kind(fake_curl, fake_dns, exc_name, expected):
    """AN UNCHANGED-BEHAVIOUR GUARD, labelled as one: it does NOT detect the
    Timeout or SSLError clauses being deleted, and an earlier version of this
    test claimed it did. REFUTED 2026-08-30 by deleting each in turn -- the
    ConnectionError and residual handlers both fall back to type(e).__name__, and
    classify_failure keys on the SUBSTRING "timeout", so ConnectTimeout and
    ReadTimeout still answer `timeout` and SSLError still answers `other`.

    What it pins is the ANSWER: a timeout must never be reported as a connection
    refusal, and a certificate failure must land on `other` -- which is where
    urllib puts one too, since it wraps ssl.SSLError in URLError and
    classify_failure has no TLS arm. It is not vacuous against every mutation,
    only against deletion: today each of these three IS answered by its own
    clause, so changing the surrogate name that clause hands classify_failure
    ("TimeoutError", "SSLError") breaks it. Deleting the clause does not,
    because the fallback happens to reproduce the same answer.
    """
    fake_curl["script"] = [_raise(exc_name, fake_curl)]
    r = tiers._tier_http_impersonate("http://origin.test/")
    assert not r.ok
    assert r.failure_class == expected


def test_a_refusal_is_a_refusal_and_a_reset_is_not(fake_curl, fake_dns):
    """The ConnectionError clause earns its place by its BODY, not its position:
    COULDNT_CONNECT is the refusal, while RECV_ERROR, SEND_ERROR, GOT_NOTHING and
    WEIRD_SERVER_REPLY arrive as the SAME curl_cffi class and are not refusals, so
    the curl code decides and not the class.

    The two halves catch different mutations and both are needed. Delete the
    clause and only the first fails; widen `refused` to the class (hard-code it
    True) and only the second does. Neither covers clause ORDER -- this test
    survives moving ConnectionError to the top of the chain, which is
    test_dns_failure_is_the_one_clause_whose_position_changes_the_answer's job.
    """
    codes = fake_curl["CurlECode"]
    fake_curl["script"] = [_raise("ConnectionError", fake_curl,
                                  code=codes.COULDNT_CONNECT)]
    assert tiers._tier_http_impersonate("http://origin.test/").failure_class \
        == "connect_refused"
    fake_curl["script"] = [_raise("ConnectionError", fake_curl, code=codes.RECV_ERROR)]
    assert tiers._tier_http_impersonate("http://origin.test/").failure_class == "other"


# ---- "Raises nothing", which the type hierarchy quietly broke ---------------

def test_a_curl_error_is_a_failure_and_not_a_traceback(fake_curl, fake_dns):
    """curl_cffi's CurlError is NOT an OSError -- MEASURED on 0.16.2,
    CurlError.__mro__ is (CurlError, Exception, BaseException, object), and only
    RequestException is both. curl_cffi applies a Session's `curl_options`
    through set_curl_options -> Curl.setopt -> _check_error -> `raise CurlError`
    at requests/session.py:712, OUTSIDE its own `try: c.perform() except
    CurlError` guard at :828, and our PROTOCOLS_STR / REDIR_PROTOCOLS_STR pair is
    exactly a setopt libcurl can reject (CURLOPT_PROTOCOLS_STR needs libcurl
    >= 7.85). On a box with an older bundled libcurl it therefore walked out of a
    function documented "Raises nothing".

    The second half pins the residual clause's WIDTH: a TypeError is in no
    curl_cffi hierarchy at all and must be contained the same way.
    """
    e = fake_curl["exceptions"].CurlError("Failed to set option PROTOCOLS_STR")
    assert not isinstance(e, OSError), "the fake must reproduce the real MRO"
    fake_curl["script"] = [e]
    r = tiers._tier_http_impersonate("http://origin.test/")
    assert not r.ok
    assert "Failed to set option" in r.error
    assert r.failure_class == "other", "transient: a rejected option cannot promote"

    fake_curl["script"] = [TypeError("option value of the wrong type")]
    r = tiers._tier_http_impersonate("http://origin.test/")
    assert not r.ok and r.failure_class == "other"


def test_the_cascade_survives_a_transport_that_raises(fake_curl, fake_dns,
                                                      monkeypatch):
    """The consequence, at the level where it was paid. fetch.py calls tier_http
    unwrapped, so an escape here costs the four tiers that had not run yet; the
    first attempt's diagnosis must come back instead."""
    first = failed_result(status=403, error="HTTP 403 Forbidden")
    monkeypatch.setattr(tiers, "_tier_http_urllib", lambda url, **kw: first)
    fake_curl["script"] = [fake_curl["exceptions"].CurlError("Failed to set option")]
    r = tiers.tier_http("http://origin.test/")
    assert r is first
    assert r.status == 403
    assert r.sub_attempts[1]["ok"] is False


# ---- a machine without curl_cffi --------------------------------------------

def test_import_error_is_not_a_crash_and_not_a_new_failure(monkeypatch):
    """curl_cffi is not installed fleet-wide, and an ImportError means "no second
    attempt", never a failure. `None` in sys.modules is what an absent package
    looks like to `import`."""
    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    r = tiers._tier_http_impersonate("http://origin.test/")
    assert not r.ok
    assert "curl_cffi unavailable" in r.error
    assert r.failure_class == "other", "must be transient: it cannot promote a domain"


def test_the_cascade_is_unchanged_when_curl_cffi_is_absent(monkeypatch):
    """End to end through tier_http: the urllib result is returned untouched."""
    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    first = failed_result(status=403, error="HTTP 403 Forbidden")
    monkeypatch.setattr(tiers, "_tier_http_urllib", lambda url, **kw: first)
    r = tiers.tier_http("http://origin.test/")
    assert r is first
    assert r.status == 403 and r.error == "HTTP 403 Forbidden"
    assert r.failure_class == "other"
    assert r.sub_attempts[1]["ok"] is False


# ---- a curl_cffi whose SURFACE differs from ours -----------------------------
#
# An import that succeeds proves the package is PRESENT, not that it has the
# names we read off it. `except ImportError` alone did not cover that, and the
# lookups it did not cover sat two lines below it: `curl_requests.exceptions`
# and the CurlOpt pair in `curl_options`. MEASURED against the pre-fix tree by
# doctoring the module object -- nothing installed was touched -- a CurlOpt
# without PROTOCOLS_STR raised `AttributeError: 'types.SimpleNamespace' object
# has no attribute 'PROTOCOLS_STR'` straight out of a function documented
# "Raises nothing", and fetch.py does not wrap tier_http.
#
# The exception CLASSES belong in the same guarded block for a reason MEASURED
# separately: an AttributeError raised while EVALUATING an except clause is not
# caught by the later clauses of that same try. A two-clause probe (`except
# ns.DNSError` on a namespace lacking it, then `except Exception`) let the
# AttributeError escape, so the residual handler could not contain a rename.


def _doctored_curl_cffi(curlopt, exceptions):
    """A curl_cffi that imports cleanly and then differs from what we read."""
    module = types.ModuleType("curl_cffi")
    module.CurlOpt = curlopt
    module.CurlECode = types.SimpleNamespace(COULDNT_CONNECT=7)
    requests_mod = types.ModuleType("curl_cffi.requests")
    requests_mod.exceptions = exceptions
    requests_mod.Session = lambda **kw: pytest.fail(
        "a session was opened against a curl_cffi we could not read")
    module.requests = requests_mod
    return module, requests_mod


_FULL_EXCEPTIONS = _fake_exceptions()


@pytest.mark.parametrize("curlopt,exceptions,missing", [
    (types.SimpleNamespace(), _FULL_EXCEPTIONS, "CurlOpt.PROTOCOLS_STR"),
    (types.SimpleNamespace(PROTOCOLS_STR="protocols"), _FULL_EXCEPTIONS,
     "CurlOpt.REDIR_PROTOCOLS_STR"),
    (types.SimpleNamespace(PROTOCOLS_STR="protocols",
                           REDIR_PROTOCOLS_STR="redir_protocols"),
     types.SimpleNamespace(), "exceptions.DNSError"),
    (types.SimpleNamespace(PROTOCOLS_STR="protocols",
                           REDIR_PROTOCOLS_STR="redir_protocols"),
     types.SimpleNamespace(DNSError=_FULL_EXCEPTIONS.DNSError,
                          Timeout=_FULL_EXCEPTIONS.Timeout,
                          SSLError=_FULL_EXCEPTIONS.SSLError),
     "exceptions.ConnectionError"),
])
def test_a_surface_we_cannot_read_is_no_second_attempt_not_a_traceback(
        monkeypatch, curlopt, exceptions, missing):
    """Every missing name must take the ImportError path out: a failed lookup is
    "there was no second attempt", never a cascade-killing traceback.

    The last two cases are the ones an `except ImportError` around the import
    alone cannot reach even in principle -- the names are read when an exception
    is already being handled."""
    module, requests_mod = _doctored_curl_cffi(curlopt, exceptions)
    monkeypatch.setitem(sys.modules, "curl_cffi", module)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", requests_mod)
    r = tiers._tier_http_impersonate("http://origin.test/")
    assert not r.ok
    assert "curl_cffi unavailable" in r.error
    assert "AttributeError" in r.error, f"missing {missing} should be named: {r.error}"
    assert r.failure_class == "other", "must be transient: it cannot promote a domain"


def test_the_cascade_survives_a_curl_cffi_whose_surface_differs(monkeypatch):
    """The consequence at the level where it is paid: four tiers had not run."""
    module, requests_mod = _doctored_curl_cffi(types.SimpleNamespace(),
                                               _FULL_EXCEPTIONS)
    monkeypatch.setitem(sys.modules, "curl_cffi", module)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", requests_mod)
    first = failed_result(status=403, error="HTTP 403 Forbidden")
    monkeypatch.setattr(tiers, "_tier_http_urllib", lambda url, **kw: first)
    r = tiers.tier_http("http://origin.test/")
    assert r is first
    assert r.status == 403 and r.error == "HTTP 403 Forbidden"
    assert r.sub_attempts[1]["ok"] is False


# ---- the urllib arm: the same escape class, on the lines this diff rewrote ---
#
# http.client.HTTPException is NOT an OSError, and urllib's
# AbstractHTTPHandler.do_open converts only OSError to URLError. So
# BadStatusLine, LineTooLong and IncompleteRead walked straight out of
# _tier_http_urllib, which also documents "Raises nothing".


class _FakeHTTPResponse:
    """Shaped like http.client.HTTPResponse exactly where _tier_http_urllib
    touches it. A BaseException in `reads` is raised from that read() call."""

    def __init__(self, url, status=200, headers=None, reads=None):
        self._url = url
        self.status = status
        self.headers = dict(headers or {"Content-Type": "text/html"})
        self._reads = list(reads or [b""])
        self.closed = False

    def geturl(self):
        return self._url

    def read(self, amt=None):
        nxt = self._reads.pop(0) if self._reads else b""
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    def close(self):
        self.closed = True


@pytest.fixture()
def fake_opener(monkeypatch):
    """Replace safe_opener() so a fault can be injected at the exact point urllib
    would raise it. The REAL validate_url_safety still runs (pair with fake_dns),
    and so does the REAL read loop."""
    box = {"raises": None, "response": None}

    class _Opener:
        def open(self, req, timeout=None):
            if box["raises"] is not None:
                raise box["raises"]
            return box["response"]

    monkeypatch.setattr(tiers, "safe_opener", lambda: _Opener())
    return box


@pytest.mark.parametrize("exc", [
    http.client.BadStatusLine("\x16\x03\x01garbage"),
    http.client.LineTooLong("header line"),
])
def test_a_malformed_response_head_does_not_escape_the_urllib_arm(fake_opener,
                                                                  fake_dns, exc):
    """Both are HTTPException and neither is an OSError, so neither was in the
    except tuple this arm used. fetch.py does not wrap tier_http, so one of these
    took the whole five-tier cascade down with it."""
    assert not isinstance(exc, OSError), "the premise: HTTPException is not an OSError"
    fake_opener["raises"] = exc
    r = tiers._tier_http_urllib("http://origin.test/")
    assert not r.ok
    assert r.failure_class == "other"


def test_a_mid_read_failure_is_transient_and_never_a_persistent_class(fake_opener,
                                                                      fake_dns):
    """TWO defects on the one line. IncompleteRead is an HTTPException and not an
    OSError, so it escaped; and the status handed to classify_failure here is
    ALWAYS 2xx -- HTTPErrorProcessor turned every non-2xx into an HTTPError long
    before the read loop -- so the empty-body arm fired and a snapped connection
    was recorded as `empty_content`, which IS in state.PERSISTENT_FAILURE_CLASSES.
    Blocklist.record_failure runs once per tier attempt, so a few flaky resets
    walk the domain toward the browser tiers: windows on the operator's screen
    (C-3/C-4). VERIFIED in this tree: classify_failure(200, "", "IncompleteRead")
    == "empty_content" against "other" for the same exception with no status."""
    fake_opener["response"] = _FakeHTTPResponse(
        "http://origin.test/",
        reads=[b"<html><body>the first 19 bytes",
               http.client.IncompleteRead(b"the first 19 bytes")])
    r = tiers._tier_http_urllib("http://origin.test/")
    assert not r.ok
    assert r.failure_class == "other"
    assert r.failure_class not in state.PERSISTENT_FAILURE_CLASSES
    assert r.status == 200, "the origin's own answer is still reported"


def test_a_complete_read_is_untouched_by_that_containment(fake_opener, fake_dns):
    """The positive control: returning a failure for everything would satisfy the
    test above too."""
    fake_opener["response"] = _FakeHTTPResponse("http://origin.test/",
                                                reads=[HTML, b""])
    r = tiers._tier_http_urllib("http://origin.test/")
    assert r.ok, r.error
    assert r.status == 200 and r.failure_class == ""
