"""Tier-0 HTTP status reporting, and the promotion containment that ships with it.

W1: `safe_opener()` built a bare OpenerDirector with no HTTPDefaultErrorHandler, so
urllib's non-2xx -> HTTPError conversion was gone. Every 404/403/429/5xx came back
as a literal None and was reported as "likely unsafe redirect blocked" -- 489 of the
497 rows in the state.db "SSRF" bucket, against 7 genuine blocks.

M2, AND ITS REVERSAL ON 2026-09-04: restoring the status makes tier 0 able to name a
PERSISTENT failure class, and `Blocklist.record_failure` fires once per TIER ATTEMPT,
so a single cascade supplies a whole promotion streak. That was originally CONTAINED
-- tier 0 recorded a literal "other" -- on the ground that a browser window on the
owner's screen outranked a domain reaching the tier that can serve it. THE OWNER
REJECTED THAT ORDERING: the fetch succeeding is priority one, and where a window is
placed is priority two and never decides whether a tier may run. The containment
did not merely withhold tier-0 evidence, it starved the whole streak, and a
4xx-walled domain sat at `http` for good.
`test_tier0_4xx_promotes_toward_a_tier_that_can_serve_it` is the measurement now.

AND THE DEFECT THE REVERSAL EXPOSED UNDERNEATH IT, fixed in state.py on
2026-09-04. `record_failure` fires once per TIER ATTEMPT, so the streak it
counted was supplied by a SINGLE cascade: http, headless and headed each
appended `origin_4xx_block` and ONE 404 promoted the whole domain off tier 0 --
the only tier that can read a PDF or return a verbatim non-HTML body. The streak
counts CASCADES now, and a 404 or 410 does not promote at all. Same test below,
new cells.

RV-5: the same promotion story from the other end -- not what tier 0 can NAME
but what it HANDS OVER. All three tier call sites passed classify_failure only
html[:4096], which starved every arm of the predicate.

Hermetic: a loopback http.server, no browser, no external network.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import http.server
import sqlite3
import threading

import pytest

import safety
import state
import tiers


# ---- loopback origin ---------------------------------------------------------

@pytest.fixture()
def origin():
    """Serve a fixed status/body on 127.0.0.1. Yields a `url_for(status)` callable."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status = int(self.path.strip("/") or "200")
            body = f"<html><title>t</title><body>status {status}</body></html>".encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]
    yield lambda status: f"http://127.0.0.1:{port}/{status}"
    srv.shutdown()
    srv.server_close()


@pytest.mark.parametrize("status", [403, 404, 429, 500, 503])
def test_safe_opener_raises_httperror_on_non_2xx(origin, status):
    """The one-line fix. Before it, `open()` returned literal None for all of these."""
    import urllib.request
    opener = safety.safe_opener()
    with pytest.raises(Exception) as exc:
        opener.open(urllib.request.Request(origin(status), method="GET"))
    assert getattr(exc.value, "code", None) == status


def test_safe_opener_still_returns_2xx(origin):
    import urllib.request
    resp = safety.safe_opener().open(urllib.request.Request(origin(200), method="GET"))
    assert resp is not None and resp.status == 200


@pytest.mark.parametrize("status", [403, 404, 429, 503])
def test_tier_http_reports_the_real_status(origin, status):
    """tier_http must name the origin's answer, not assert an SSRF block it did not see."""
    r = tiers.tier_http(origin(status), allow_private=True)
    assert not r.ok
    assert r.status == status
    assert f"HTTP {status}" in r.error
    assert "unsafe redirect" not in r.error


# ---- M2: promotion containment ----------------------------------------------

def _fresh_blocklist():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(state._DDL)
    return state.Blocklist(conn)


# Classes tiers 1-4 record for a 4xx URL, measured against the live state.db:
# headless/headed see the status and classify origin_4xx_block; copilot fails its
# output contract and classifies `other`; cdp sees the status again.
_UPPER_TIER_CLASSES = ["origin_4xx_block", "origin_4xx_block", "other", "origin_4xx_block"]


@pytest.mark.parametrize("status", [404, 403])
def test_tier0_records_the_honest_class_for_a_4xx(origin, status):
    """R1, at the branch itself: the class must describe what happened.

    `failure_class` is the promotion signal, and a tier-0 wall recorded as the
    transient `other` tells the blocklist that nothing persistent was seen. The
    origin refusing the document IS persistent, and saying so is what lets the
    domain reach a tier that can serve it.
    """
    r = tiers.tier_http(origin(status), allow_private=True)
    assert r.status == status, "precondition: the status fix is in place"
    assert r.failure_class == "origin_4xx_block", (
        f"a {status} was recorded as {r.failure_class!r}")
    assert r.failure_class in state.PERSISTENT_FAILURE_CLASSES


@pytest.mark.parametrize("status", [404, 403])
def test_tier0_4xx_promotes_toward_a_tier_that_can_serve_it(origin, status):
    """The reversal of the old containment, measured end to end -- and, since
    2026-09-04, at the rate a fetch actually supplies evidence.

    The tier-0 class is taken from the real `tier_http` return, not asserted here,
    so this fails if the literal "other" is ever restored in tiers.py. Three whole
    cascades against an in-memory blocklist; the cells are where the NEXT fetch
    would start. With the containment in place every cell read `http`, because
    `other` at the head of each cascade broke the streak that PROMOTE_THRESHOLD
    counts and the domain never moved at all.

    TWO THINGS CHANGED IN state.py ON 2026-09-04 AND THIS ROW PINS BOTH.
      * A STREAK COUNTS CASCADES, NOT TIER ATTEMPTS. `record_failure` fires once
        per rung, so the rungs of ONE fetch used to meet PROMOTE_THRESHOLD by
        themselves and a single 4xx moved the domain off tier 0 immediately.
        Three fetches of evidence now buy exactly one rung: http, http, headless.
      * A 404 OR 410 DOES NOT PROMOTE AT ALL. That was the accepted COST of the
        line above until the owner ruled on it: no browser turns a missing page
        into a present one, so promoting on one spends the higher tiers -- and at
        the top of the cascade asks for a CDP browser -- over a page that is not
        there. A 403 is a wall a browser may clear, and still promotes.

    THE REASONS PASSED HERE ARE THE REAL ONES. Both statuses arrive under the one
    class name `origin_4xx_block`, so `state._reason_status` reads the status back
    off the reason text; a placeholder reason would make the 404 row indistinguish-
    able from the 403 row, i.e. green for the wrong reason.
    """
    import extract  # local, as elsewhere in this file

    tier0 = tiers.tier_http(origin(status), allow_private=True)
    assert tier0.status == status, "precondition: the status fix is in place"
    # What every browser tier reports for the same status, from its own producer.
    browser_reason = extract.is_real_content("some text", "<html></html>", status)[1]

    bl = _fresh_blocklist()
    seen = []
    for _ in range(3):
        for tier_idx, cls in enumerate([tier0.failure_class or "other"] + _UPPER_TIER_CLASSES):
            if tier_idx == 0:
                reason = tier0.error
            elif cls == "other":
                reason = "copilot output not JSON"
            else:
                reason = browser_reason
            bl.record_failure("example.org", tier_idx, cls, reason=reason)
        seen.append(state.TIERS[bl.get("example.org")["tier_idx"]])
    expected = {403: ["http", "http", "headless"],
                404: ["http", "http", "http"]}[status]
    assert seen == expected, (
        f"a {status} left the domain at {seen} (tier-0 class was "
        f"{tier0.failure_class!r}); expected {expected}")


def test_upper_tier_4xx_still_promotes():
    """Unchanged by the reversal, and kept as the control: a streak ABOVE tier 0
    promoted before and promotes now. What changed is only whether tier 0 is
    allowed to contribute to a streak of its own."""
    bl = _fresh_blocklist()
    for _ in range(3):
        bl.record_failure("example.org", 1, "cloudflare_challenge", reason="x")
    # The first failure seeds the row at the tier that reported it, so a streak at
    # tier 1 promotes to tier 2.
    assert state.TIERS[bl.get("example.org")["tier_idx"]] == "headed"


def test_tier0_2xx_challenge_still_promotes():
    """A 200-status Cloudflare challenge seen by tier 0 keeps its persistent class.
    That path never went through the HTTPError branch, so it was never contained
    and the reversal leaves it exactly as it was."""
    bl = _fresh_blocklist()
    for _ in range(3):
        bl.record_failure("example.org", 0, "cloudflare_challenge", reason="x")
    assert state.TIERS[bl.get("example.org")["tier_idx"]] == "headless"


# ---- RV-5: the tiers must hand classify_failure the WHOLE document ----------
#
# The reviewer's finding, and the shape it names: a 200 shell with more than
# 4,096 bytes of CSS ahead of its <noscript>You need to enable JavaScript
# </noscript>. is_real_content rejects it -- it reads the EXTRACTED text, where
# the marker is the only content there is. classify_failure was handed
# html[:4096], never reached the marker, and returned `other` (transient), so
# the domain missed the persistent promotion the shared _JS_SHELL_MARKERS tuple
# was supposed to guarantee.
#
# The truncation was never specific to the JS arm: challenge markup, the <title>
# match and the client-demand scan were all confined to the same 4 KiB.

def _shell(pad_rules: int) -> str:
    """A React-style shell with `pad_rules` CSS rules ahead of its <noscript>."""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<title>Console</title><style>'
            + "div.a{color:#000;margin:0}\n" * pad_rules
            + '</style></head><body>'
            '<noscript>You need to enable JavaScript to run this app.</noscript>'
            '<div id="root"></div></body></html>')


# The reviewer's shape: marker just past 4,096.
SHELL_WITH_MARKER_PAST_4096 = _shell(170)


@pytest.fixture()
def body_origin():
    """Serve a fixed body as text/html at 200. Yields url_for(body)."""
    holder = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = holder["body"]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def url_for(body: str):
        holder["body"] = body.encode("utf-8")
        return f"http://127.0.0.1:{port}/"

    yield url_for
    srv.shutdown()
    srv.server_close()


def test_the_fixture_actually_has_its_marker_past_4096():
    """A fixture that cannot exercise the fault proves nothing. This is the whole
    point of the shape, so it is asserted rather than assumed."""
    import extract

    at = SHELL_WITH_MARKER_PAST_4096.lower().find("enable javascript")
    assert at > 4096, f"marker at {at}: the truncation would still have reached it"
    assert extract._script_byte_share(SHELL_WITH_MARKER_PAST_4096) < 0.90, (
        "the script-share arm must not be what rejects this")


@pytest.mark.parametrize("pad_rules", [
    pytest.param(170, id="marker-just-past-4KiB-the-reviewers-shape"),
    pytest.param(4600, id="marker-past-120KiB-defeats-a-bigger-cap-too"),
])
def test_tier0_hands_the_classifier_the_whole_document(body_origin, pad_rules):
    """The call site, end to end against a real origin. With html[:4096] this
    records `other` -- transient -- and the domain never promotes.

    Two sizes deliberately. With only the 4 KiB shape a mutation to html[:8192]
    passed, i.e. the test bound to "the cap is not 4096" rather than to "there
    is no cap". The 120 KiB case closes that."""
    shell = _shell(pad_rules)
    at = shell.lower().find("enable javascript")
    assert at > 4096, f"precondition: marker at {at}"
    r = tiers.tier_http(body_origin(shell), allow_private=True)
    assert not r.ok, r.content[:200]
    assert r.failure_class == "js_only_spa", f"marker at {at}: {r.failure_class}"
    assert r.failure_class in state.PERSISTENT_FAILURE_CLASSES


def test_browser_tier_hands_the_classifier_the_whole_document(monkeypatch):
    """The second call site. get_session is the seam where a browser would be
    launched, so replacing it exercises all of tier_playwright and none of
    Chrome."""
    class _FakeSession:
        def fetch_html(self, url, total_timeout):
            return url, SHELL_WITH_MARKER_PAST_4096, 200, {}, []

    monkeypatch.setattr(tiers, "get_session", lambda mode: _FakeSession())
    r = tiers.tier_playwright("https://example.com/app", mode="headless")
    assert not r.ok
    assert r.failure_class == "js_only_spa", r.failure_class


def test_the_two_predicates_agree_on_the_shell(body_origin):
    """The property the finding says is missing: one document, both predicates,
    same verdict. The gate rejects it and the classifier calls it persistent."""
    import extract

    md = extract.html_to_markdown(SHELL_WITH_MARKER_PAST_4096, "https://example.com/")
    ok, reason = extract.is_real_content(md, SHELL_WITH_MARKER_PAST_4096, 200)
    assert not ok and "demands JS" in reason, reason

    r = tiers.tier_http(body_origin(SHELL_WITH_MARKER_PAST_4096), allow_private=True)
    assert r.failure_class in state.PERSISTENT_FAILURE_CLASSES, (
        f"the gate rejected it as a shell but the tier recorded {r.failure_class!r}")


# ---- F6: assert the ARGUMENT, not a marker's position -----------------------
#
# The behavioural tests below prove a marker past some offset is still seen.
# They cannot prove the WHOLE document was passed: mutating tier_playwright
# alone to html[:8192] passes every one of them, because its fixture's marker
# sits just past 4 KiB and the 120 KiB parameter only ever exercised tier_http.
# The round-2 mutation was run across all three sites at once, so tier_http
# failing was enough to make it read as "fails correctly" while the browser call
# site sailed through.
#
# An exact-equality assertion on the argument cannot be fooled that way, at any
# cap, for any marker position.

def _spy_on_classify(monkeypatch):
    """Record what classify_failure is actually handed. Returns the record."""
    seen = {}
    real = tiers.classify_failure

    # THE SIGNATURE TRACKS THE REAL FUNCTION'S, and it has to: a double that
    # accepts fewer arguments than its original turns a call-site change into a
    # TypeError in a test that is not about the change. This one broke on the
    # CAPTURED-status path, where no behaviour moved at all.
    def spy(status, body, exception_type=None, *, extracted=None,
            status_unobserved=False):
        seen["status"] = status
        seen["body"] = body
        seen["extracted"] = extracted
        seen["status_unobserved"] = status_unobserved
        return real(status, body, exception_type, extracted=extracted,
                    status_unobserved=status_unobserved)

    monkeypatch.setattr(tiers, "classify_failure", spy)
    return seen


def test_browser_tier_passes_classify_failure_the_exact_document(monkeypatch):
    """F6. get_session is the seam where a browser would launch, so replacing it
    exercises all of tier_playwright and none of Chrome."""
    # ~120 KiB, NOT the 4.8 KiB shape. A slice html[:8192] of a 4.8 KiB document
    # returns the document unchanged, so the equality holds vacuously and the
    # assertion proves nothing -- which is the same fault F6 filed, and my first
    # version of this test had it.
    doc = _shell(4600)
    seen = _spy_on_classify(monkeypatch)

    class _FakeSession:
        def fetch_html(self, url, total_timeout):
            return url, doc, 200, {}, []

    monkeypatch.setattr(tiers, "get_session", lambda mode: _FakeSession())
    r = tiers.tier_playwright("https://example.com/app", mode="headless")

    assert not r.ok, "precondition: the gate must reject it, or nothing classifies"
    assert "body" in seen, "classify_failure was never called"
    assert seen["body"] == doc, (
        f"tier_playwright passed {len(seen['body'])} of {len(doc)} chars")
    assert seen["extracted"] is not None, (
        "tier_playwright must hand classify_failure the EXTRACTED text as well "
        "(see the http-tier test for why)")


def test_tier0_passes_classify_failure_the_exact_document(body_origin):
    """The same assertion at the http call site, against a real origin."""
    import pytest as _pytest  # local: monkeypatch is function-scoped elsewhere

    doc = _shell(4600)          # see the note above: it must exceed any cap
    mp = _pytest.MonkeyPatch()
    try:
        seen = _spy_on_classify(mp)
        r = tiers.tier_http(body_origin(doc), allow_private=True)
        assert not r.ok
        assert "body" in seen, "classify_failure was never called"
        assert seen["body"] == doc, (
            f"tier_http passed {len(seen['body'])} of {len(doc)} chars")
        assert seen["extracted"] is not None, (
            "tier_http must hand classify_failure the EXTRACTED text as well as the "
            "raw body: without it the cap that gates the title boundary is measured "
            "on script-stripped HTML in classify_failure and on extracted text in "
            "is_real_content, and the two disagree about the same document")
    finally:
        mp.undo()


# ---- a MANUFACTURED status is evidence of nothing -----------------------------

_TURNSTILE_LOGIN = (
    "<html><head><title>Sign in</title></head><body>"
    '<div class="cf-turnstile" data-sitekey="0x4"></div>'
    "<p>" + ("This is the site's own login form, with enough prose around it "
             "to clear the near-empty document floor comfortably. " * 8) + "</p>"
    "</body></html>"
)


@pytest.mark.parametrize("nav_status,expect_ok", [(200, True), (None, False)])
def test_the_embeddable_marker_exemption_needs_a_status_the_origin_SENT(
        monkeypatch, nav_status, expect_ok):
    """tier_playwright falls back to a literal 200 when _navigate_capture could
    not read a status -- an old playwright-cli, a rejected snippet, an over-long
    URL. The fallback must keep the non-2xx branch out of the way (a missing
    capability must not turn a working fetch into a failed one) WITHOUT also
    buying the embeddable-marker exemption, which is earned by a 2xx the origin
    actually sent.

    One document, two runs. `class="cf-turnstile"` on a real 200 is a site's own
    form and proves nothing. get_session is the seam where a browser would launch,
    so this exercises all of tier_playwright and none of Chrome.

    THE TWO JUDGES NOW DISAGREE, DELIBERATELY, and this test is where that is
    pinned. The paragraph here used to say they must agree, and the reasoning was
    that a status we invented is worthless to BOTH. Half of that survives:
    is_real_content still gets status_is_real=False, still scans both tuples, and
    still REJECTS this document -- `r.ok is False` below is unchanged in both
    arms, which is the verdict half.

    What changed is the CLASS. classify_failure now gets status_unobserved=True
    and drops the embeddable tuple, so the mount no longer records the PERSISTENT
    `cloudflare_challenge` against the domain. The asymmetry is the reason: a
    verdict is spent on THIS fetch, which then climbs; a class is spent on the
    whole DOMAIN, which loses tier 0 -- the only tier that reads a PDF or returns
    a JSON body verbatim -- for every fetch afterwards. Evidence too thin for the
    second can still be right for the first.

    MEASURED, and it is the sharpest statement of the defect: the SAME bytes
    classified `other` at an observed 200 and `cloudflare_challenge` at a status
    we could not read. The document never changed; only our capture did.
    """
    class _FakeSession:
        def fetch_html(self, url, total_timeout):
            return url, _TURNSTILE_LOGIN, nav_status, {}, []

    monkeypatch.setattr(tiers, "get_session", lambda mode: _FakeSession())
    r = tiers.tier_playwright("https://example.com/login", mode="headless")
    assert r.ok is expect_ok, r.error
    if not expect_ok:
        # THE VERDICT HALF, unchanged by the class change and asserted first.
        assert "cf-turnstile" in r.error
        assert r.failure_class == "other", (
            "a status nobody sent is not the origin refusing: the mount must not "
            f"make this persistent: {r.failure_class}")
    assert r.status == 200, "the fallback status is still what the envelope reports"


# THE tier_cdp EQUIVALENT NOW EXISTS, in test_cdp_transport.py --
# `test_tier_cdp_tells_the_classifier_the_status_was_never_read`.
#
# The paragraph that stood here said one was impractical, and it was true when
# written: reaching tier_cdp then needed a fake `_run_pw` answering session-open,
# tab-new, tab-select, goto and two evals. The tier was rebuilt onto raw CDP and
# speaks to a websocket now, and that file's `browser` fixture makes the same
# test five lines. A note recording a gap has to be re-read when the reason for
# the gap is removed, or it goes on excusing an absence nothing requires.


def test_a_shell_whose_marker_is_early_is_unaffected(body_origin):
    """The regression direction: shortening the padding must change nothing."""
    early = _shell(1)
    assert early.lower().find("enable javascript") < 4096, "precondition"
    r = tiers.tier_http(body_origin(early), allow_private=True)
    assert r.failure_class == "js_only_spa", r.failure_class
