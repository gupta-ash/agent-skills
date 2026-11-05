"""tier_cdp over the raw DevTools protocol: ownership, cleanup and containment.

REPLACES the `DEFECT I` family that lived in test_ensure_open_liveness_and_claim.py
until 2026-09-04. Those tests injected failures through `_run_pw`, which was the
only subprocess boundary the tier had; the tier now speaks a websocket and has no
subprocess at all, so the injection point is gone. THE PROPERTIES ARE NOT GONE and
every one is carried over here against the new transport -- containment at the
tier boundary, cleanup that cannot mask the real error, cleanup that cannot fail a
completed fetch, teardown exactly once and before the socket closes.

THREE OF THESE TESTS EXIST BECAUSE REVIEW ROUND 1 FOUND REAL DEFECTS, and each is
written to fail against the code as it was, not merely to describe the code as it
is:
  - the target must be created in the DEFAULT context, because a disposable one
    inherits no cookies and this tier is entered only on NEEDS_LOGIN (measured,
    three arms, headless Chrome);
  - an IFRAME's status must not become the page's status;
  - closing our target must never close the browser's last page.

NOTHING HERE REACHES A BROWSER, enforced twice: `conftest.py` makes the discovery
call raise for every test in the suite, and each test installs its own fake.
"""
import json
import time

import pytest

import tiers


URL = "https://example.com/"
HTML = ("<html><head><title>A real page</title></head><body>"
        + ("word " * 200) + "</body></html>")
LOADER = "LOADER-1"
FRAME = "FRAME-1"

# The commands that must never reach a browser shared with other clients. Each
# breaks every other client on it. NOTE `Target.setDiscoverTargets` -- the
# SUBSCRIPTION -- and not `Target.getTargets`, which is a one-shot query that
# changes no browser state and which teardown legitimately uses.
FORBIDDEN = (
    "Browser.close",
    "Target.setAutoAttach",
    "Fetch.enable",
    "Target.setDiscoverTargets",
)


class FakeBrowser:
    """A DevTools endpoint that answers exactly what `tier_cdp` asks it."""

    def __init__(self, *, html=HTML, final_url=URL, status=200, fail_on=None,
                 raise_on=None, emit_load=True, page_targets=3,
                 extra_events=(), stall=False, order="reply_first",
                 pre_reply_events=(), post_load_events=(), final_loader=None,
                 candidates=None):
        self.html = html
        self.final_url = final_url
        self.candidates = [] if candidates is None else candidates
        self.status = status
        self.fail_on = fail_on
        self.raise_on = raise_on
        self.emit_load = emit_load
        self.stall = stall
        self.page_targets = page_targets      # how many pages the browser holds
        self.extra_events = list(extra_events)  # injected before our own
        # WHICH SIDE OF THE `Page.navigate` REPLY THE RESPONSE ARRIVES ON, and it
        # decides whether this harness can see the defect at all.
        #
        # `reply_first` is what this file did until 2026-09-06 and is kept as the
        # default so the tests written against it keep their meaning.
        #
        # BOTH ORDERS ARE LEGAL AND THE TIER MUST BE CORRECT UNDER EITHER: CDP
        # specifies no ordering between a command reply and an event. What was
        # measured is narrower and is the reason `event_first` exists -- over ten
        # navigations against Chrome 151 (two fast 200s, two real 404s, a 403, a
        # large page, two iframe-bearing pages and a repeat navigation on one
        # session), `Network.responseReceived` for the main document arrived
        # BEFORE the `Page.navigate` reply every single time, by exactly one wire
        # frame, because that reply is sent at navigation COMMIT and the response
        # necessarily precedes commit. So reply-first is not what that Chrome
        # did; it is not thereby forbidden, which is why both branches stay.
        #
        # Under `reply_first` the session learns its `loaderId` before any
        # response arrives, which is the one ordering in which the correlation
        # can succeed. That is why twenty-five tests here passed over a tier that
        # had never captured a status on a real page.
        self.order = order
        # Events queued BEFORE the reply, i.e. inside the window where the
        # navigation's identity is not yet known. `extra_events` cannot express
        # this: they are always queued after.
        self.pre_reply_events = list(pre_reply_events)
        # Events queued AFTER `Page.frameStoppedLoading`, i.e. after
        # `navigate()`'s pump loop has already ended. They are therefore drained
        # by the NEXT `call()` -- the first `Runtime.evaluate` -- which is the
        # only way to express a page that replaces itself WHILE THE TIER IS
        # READING THE DOM. `extra_events` cannot express it: they land before the
        # load event and are consumed inside `navigate()`.
        self.post_load_events = list(post_load_events)
        # WHAT `Page.getFrameTree` REPORTS AS THE TOP FRAME'S CURRENT LOADER.
        # `None` means "still the document we navigated to". Setting it is how a
        # test says the page replaced itself, INDEPENDENTLY of whether any event
        # announcing that was ever delivered -- which is the whole point, since
        # the tier no longer decides this from events.
        self.final_loader = final_loader
        self.sent = []
        self.closed = False
        self.closed_targets = []
        self.navigated = []
        self._out = []
        self._n = 0

    # -- websocket surface ----------------------------------------------------
    def send(self, raw):
        msg = json.loads(raw)
        method = msg["method"]
        params = msg.get("params") or {}
        self.sent.append(msg)
        if self.raise_on == method:
            raise OSError("the socket died during %s" % method)
        if self.fail_on == method:
            self._out.append({"id": msg["id"],
                              "error": {"message": "%s refused" % method}})
            return
        # `_result` has side effects (`navigated`, `_n`), so it is called here on
        # every path and at the same point as before -- only the POSITION of the
        # reply in the queue is what `order` changes.
        reply = {"id": msg["id"], "result": self._result(method, params)}
        if not (method == "Page.navigate" and params.get("url") != "about:blank"):
            self._out.append(reply)
            return
        document = None
        if self.status is not None:
            document = {
                "method": "Network.responseReceived",
                "params": {"type": "Document", "loaderId": LOADER,
                           "frameId": FRAME,
                           "response": {"status": self.status,
                                        "url": self.final_url}}}
        if self.order == "event_first":
            # THE MAIN DOCUMENT GOES FIRST AND `pre_reply_events` AFTER IT, and
            # the order within this window is what gives the iframe regression
            # its teeth. Queued the other way round, the buffer ends with our own
            # 404 and last-matching-wins returns it whether or not the resolver
            # checks identity at all -- MEASURED, the iframe test passed against
            # a `_resolve_pending` that admitted every buffered Document
            # unconditionally. With the main document first, that same broken
            # resolver returns the IFRAME's status and the test fails.
            #
            # It is also the causally honest order: a page's iframes cannot load
            # before the document that declares them.
            if document is not None:
                self._out.append(document)
            for ev in self.pre_reply_events:
                self._out.append(ev)
            self._out.append(reply)
        else:
            self._out.append(reply)
            if document is not None:
                self._out.append(document)
        # AFTER the main document, which is the ordering that matters and
        # the one a subframe really has: a page's iframes cannot load before
        # the document that declares them. Queuing these first would let a
        # "last Document response wins" rule pick the main document by
        # accident, and the test would pass against code with no correlation
        # at all -- measured, that is exactly what happened on the first run.
        for ev in self.extra_events:
            self._out.append(ev)
        if self.emit_load:
            self._out.append({"method": "Page.frameStoppedLoading",
                              "params": {"frameId": FRAME}})
        for ev in self.post_load_events:
            self._out.append(ev)

    def recv(self, timeout=None):
        if not self._out:
            # A REAL SOCKET CONSUMES THE TIMEOUT BEFORE IT GIVES UP, and for the
            # deadline tests that is the whole point: an instant raise lets the
            # tier finish inside its budget, so the over-budget path is never
            # reached and a test aimed at it passes against unfixed code.
            # Measured -- with an instant raise, both teardown mutations
            # SURVIVED.
            if self.stall and timeout:
                # timeout PLUS a margin, so the deadline is definitively CROSSED
                # rather than merely reached. Sleeping exactly the timeout lands
                # on it, and `>=` then decides the test on scheduler jitter --
                # measured, the mutation survived that way. A real socket can
                # also return late, so this is not an unrealistic shape.
                time.sleep(min(timeout, 2.0) + 0.15)
            raise TimeoutError("nothing queued")
        return json.dumps(self._out.pop(0))

    def close(self):
        self.closed = True

    # -- protocol answers -----------------------------------------------------
    def _result(self, method, params):
        self._n += 1
        if method == "Target.createTarget":
            return {"targetId": "TGT1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "SESS-for-%s" % params.get("targetId")}
        if method == "Target.getTargets":
            infos = [{"targetId": "OWNER%d" % i, "type": "page"}
                     for i in range(max(0, self.page_targets - 1))]
            infos.append({"targetId": "TGT1", "type": "page"})
            infos.append({"targetId": "SW1", "type": "service_worker"})
            return {"targetInfos": infos}
        if method == "Target.closeTarget":
            self.closed_targets.append(params.get("targetId"))
            return {}
        if method == "Page.navigate":
            self.navigated.append(params.get("url"))
            return {"frameId": FRAME, "loaderId": LOADER}
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": FRAME,
                                            "loaderId": self.final_loader or LOADER}}}
        if method == "Runtime.evaluate":
            expr = params.get("expression", "")
            # The combined read comes first: it contains both older needles.
            if "outerHTML" in expr and "location.href" in expr:
                # CDP serializes all three values, not a page-owned JSON hook.
                return {"result": {"value": [self.html, self.final_url, self.candidates]}}
            if "outerHTML" in expr:
                return {"result": {"value": self.html}}
            if "location.href" in expr:
                return {"result": {"value": self.final_url}}
            return {"result": {"value": ""}}
        return {}

    def methods(self):
        return [m["method"] for m in self.sent]

    def params_for(self, method):
        for m in self.sent:
            if m["method"] == method:
                return m.get("params") or {}
        return None


@pytest.fixture
def browser(monkeypatch):
    def install(**kw):
        fake = FakeBrowser(**kw)
        monkeypatch.setattr(tiers, "_cdp_browser_ws_url",
                            lambda port, timeout: "ws://127.0.0.1:9222/devtools/browser/x")
        monkeypatch.setattr(tiers, "_ws_connect", lambda url, **kwargs: fake)
        return fake

    return install


# ---- Ownership: the point of the rebuild ------------------------------------

def test_the_target_is_owned_by_the_id_the_browser_returned(browser):
    """Ownership is the returned targetId, not a position in a shared list.

    Index ownership failed because closing any earlier tab splices the list and
    a later tab inherits our index, with every index check still passing --
    measured, one agent received another's page, reported ok, and closed the
    other's tab. The assertion is that the session we evaluate through is bound
    to the target the browser said it created, and that no other id is named.
    """
    fake = browser()
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.ok is True, result.error
    assert len([m for m in fake.sent if m["method"] == "Target.createTarget"]) == 1
    assert fake.params_for("Target.attachToTarget")["targetId"] == "TGT1"
    evals = [m for m in fake.sent if m["method"] == "Runtime.evaluate"]
    assert evals, "the page must actually be read"
    for m in evals:
        assert m["sessionId"] == "SESS-for-TGT1", \
            "every evaluation must run in the session bound to OUR target"


def test_the_target_is_created_in_the_default_context(browser):
    """REGRESSION, review round 1 critical #1 -- and measured, not taken on trust.

    This first shipped creating the target in a DISPOSABLE browser context. A
    disposable context is off-the-record and inherits NO cookies: measured in
    headless Chrome across three arms, with a cookie set in the default context
    -- default sees it, a second default target sees it, the disposable context
    does not.

    This tier is entered ONLY on a NEEDS_LOGIN verdict and exists because the
    dedicated profile is logged in. In a disposable context every such page opens
    LOGGED OUT, so the tier would fetch the login page it was summoned to get
    past and return it as content.
    """
    fake = browser()
    tiers.tier_cdp(URL, total_timeout=5.0)

    assert "Target.createBrowserContext" not in fake.methods(), \
        "a disposable context has none of the profile's logins"
    made = fake.params_for("Target.createTarget")
    assert "browserContextId" not in made, \
        "the target must be created in the DEFAULT context, where the logins are"


def test_the_target_is_ordinary_and_never_hidden(browser):
    """A hidden target never paints, so it returns empty rather than erroring.

    Measured against real Chrome 151 with an ordinary-target control in every
    arm: 1 animation frame in 4 s against 241, no paint event, observers silent
    -- while `visibilityState` still reads `visible` and the DOM still looks
    plausible. That combination is what makes it dangerous.
    """
    fake = browser()
    tiers.tier_cdp(URL, total_timeout=5.0)

    made = fake.params_for("Target.createTarget")
    assert made.get("hidden") is not True, "hidden targets never paint"
    assert made.get("background") is True, "the new tab must not be foregrounded"


def test_nothing_activates_a_tab_or_subscribes_to_target_events(browser):
    """No activation, and no target-discovery SUBSCRIPTION.

    `tab-select` reached `Page.bringToFront` and raised the owner's window;
    `/json/activate` is its HTTP analogue. `Target.setDiscoverTargets` streams
    lifecycle events to every client and breaks the others. The one-shot
    `Target.getTargets` is neither and is used deliberately by teardown.
    """
    fake = browser()
    tiers.tier_cdp(URL, total_timeout=5.0)

    sent = set(fake.methods())
    assert "Page.bringToFront" not in sent
    assert "Target.activateTarget" not in sent
    for method in FORBIDDEN:
        assert method not in sent, "%s must never reach a shared browser" % method


def test_a_url_carrying_an_ampersand_survives_intact(browser):
    """There is no shell in this path, so nothing can split the URL.

    The old tier passed the URL raw to a `.cmd` shim, so subprocess ran it
    through cmd.exe, which treats `&` as a command separator: seven fetches died
    that way in this box's own fetch_log.
    """
    tricky = "https://example.com/?a=1&b=2&c=3"
    fake = browser(final_url=tricky)
    result = tiers.tier_cdp(tricky, total_timeout=5.0)

    assert result.ok is True, result.error
    assert tricky in fake.navigated


# ---- Status honesty and event correlation -----------------------------------

def test_an_observed_status_is_used_as_a_real_one(browser):
    """The tier could never classify a content-rich 404 before this."""
    browser(status=404)
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status == 404
    assert result.ok is False, "a 404 is not a successful fetch"


def test_an_iframes_status_does_not_become_the_pages_status(browser):
    """REGRESSION, review round 1 critical #2.

    EVERY IFRAME IS ALSO A `Document`, and iframes load AFTER the main frame --
    so a "last Document response wins" rule does not select the main document,
    it reliably selects an iframe on any page that has one. Here the main
    document 404s and an iframe 200s afterwards. Without correlation on the
    navigation's own loaderId, the 200 overwrites the 404 and a failed fetch is
    reported as content.
    """
    browser(status=404, extra_events=[{
        "method": "Network.responseReceived",
        "params": {"type": "Document", "loaderId": "SOME-IFRAME-LOADER",
                   "frameId": "IFRAME-9",
                   "response": {"status": 200, "url": "https://ads.example/x"}}}])
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status == 404, \
        "the main document's status must survive an iframe loading after it"
    assert result.ok is False


# A page whose ONLY challenge-shaped evidence is the mount point Cloudflare
# documents for a site's OWN form. Enough prose that no near-empty-document rule
# can be what decides it.
_OWN_FORM_MOUNT = (
    "<html><head><title>Our contact page</title></head><body><article><p>"
    + ("Reach the support desk by email or by phone during office hours; we "
       "answer most messages within one working day. " * 4)
    + "</p></article><form>"
    '<div class="cf-turnstile" data-sitekey="0x4"></div>'
    "</form></body></html>"
)


def test_tier_cdp_tells_the_classifier_the_status_was_never_read(browser):
    """THE CALL-SITE WIRING, and it was the uncovered half of the change.

    A predicate is only as good as what its CALLERS supply. Before this test
    THIS FILE CARRIED NO `failure_class` ASSERTION AT ALL -- every status=None
    test here ends at ok=True on the default HTML, so `fc = "" if ok else ...`
    short-circuits and the call site is never reached. tier_cdp's
    `status_unobserved=` could have been left off with the whole suite green.

    `status=None` emits no `Network.responseReceived` for the document, which is
    exactly the case the argument names: a document arrived, its status did not.

    MUTATION: drop `status_unobserved=` from the tier_cdp call site. Only the
    class assertion dies -- the verdict and the reported status do not, which is
    the split this change is built on.
    """
    browser(status=None, html=_OWN_FORM_MOUNT)
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status == 200, "the manufactured status is still reported"
    assert result.ok is False, "the VERDICT is unchanged: the mount still rejects"
    assert result.failure_class == "other", (
        "no refusal was OBSERVED, so a mount a site may put on its own form must "
        f"not by itself make the class persistent: {result.failure_class}")


def test_an_unobserved_status_is_declared_manufactured(browser):
    """No document response seen: fall back to 200 and grant it no weight.

    `status_is_real=False` is what stops an invented 2xx being read as an
    origin's own, which is the exemption the embeddable challenge-marker arm
    keys on.
    """
    fake = browser(status=None)
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status == 200
    assert result.ok is True, result.error
    assert "Network.enable" in fake.methods(), \
        "we must at least have asked for the status we did not get"


# ---- The real frame order ----------------------------------------------------
#
# THE TESTS BELOW EXERCISE THE EVENT-FIRST ORDER -- with one deliberate
# exception, `test_the_status_is_captured_under_either_frame_order`, which runs
# BOTH because order-independence is the fix's central claim. Measured over ten
# navigations against Chrome 151 (two fast 200s, two real 404s, a 403, a large
# page, two iframe-bearing pages, a repeat navigation on one session), the main
# document's `Network.responseReceived` preceded the reply every time, by exactly
# one wire frame: that reply is sent at navigation COMMIT and the response
# precedes commit. CDP itself orders nothing between a reply and an event, so
# reply-first remains legal and is not merely a legacy of this harness.
#
# The first test here FAILED against the code as it was, returning a manufactured
# 200 for a page that really 404s. That is the whole reason this block exists:
# `test_an_observed_status_is_used_as_a_real_one` above passes only because the
# default harness order hands the session its identity before any response
# arrives.

def test_the_real_status_survives_the_order_a_browser_actually_uses(browser):
    """REGRESSION, and it must fail against the unfixed tier.

    The identity of our navigation is learned from `Page.navigate`'s reply, and
    the response beats that reply to the socket. So the correlation used to run
    with `_loader_id`/`_frame_id` both None, fall through its fail-closed tail,
    and discard a status whose loaderId was byte-identical to the one arriving a
    frame later. Not a mismatch -- an identity that was not yet known.
    """
    browser(status=404, order="event_first")
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status == 404, \
        "the origin's own 404 must survive arriving before the navigate reply"
    assert result.ok is False, "a 404 is not a successful fetch"


def test_the_status_is_captured_under_either_frame_order(browser):
    """The fix's central claim is order-independence, so assert BOTH arms.

    A repair that merely swapped which ordering works would pass the test above
    and silently break every browser that replies first -- and CDP guarantees no
    ordering between a command reply and an event, so both are legal.
    """
    browser(status=503, order="event_first")
    event_first = tiers.tier_cdp(URL, total_timeout=5.0)
    browser(status=503, order="reply_first")
    reply_first = tiers.tier_cdp(URL, total_timeout=5.0)

    assert event_first.status == 503
    assert reply_first.status == 503
    assert event_first.status == reply_first.status, \
        "the captured status must not depend on which side of the reply it arrived"


def test_an_iframe_inside_the_identity_gap_does_not_become_the_pages_status(browser):
    """REGRESSION for the fix's own blast radius, not for the original defect.

    `test_an_iframes_status_does_not_become_the_pages_status` cannot catch this:
    it queues the iframe AFTER the reply, where the identity is already known and
    the shipped comparison handles it. The danger a deferred comparison
    introduces is an iframe arriving INSIDE the window where identity is unknown,
    which that test never produces.

    Measured against real Chrome, an iframe cannot land in that window -- our own
    document has not parsed yet, so it has declared no subframes; the main
    document arrived at wire frame 11 and 13 on two iframe-bearing pages while
    their iframes arrived at 193 and 761-766. This test asserts the guarantee
    anyway rather than resting on that timing, because CDP promises nothing about
    it and the cost of being wrong is a foreign status read as the origin's own.
    """
    browser(status=404, order="event_first", pre_reply_events=[{
        "method": "Network.responseReceived",
        "params": {"type": "Document", "loaderId": "SOME-IFRAME-LOADER",
                   "frameId": "IFRAME-9",
                   "response": {"status": 200, "url": "https://ads.example/x"}}}])
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status == 404, \
        "an iframe arriving before the reply must not be taken for the main document"
    assert result.ok is False


def test_a_foreign_document_before_the_reply_still_fails_closed(browser):
    """Fail-closed must still mean fail-closed after the comparison is deferred.

    A Document response whose ids match nothing of ours is evidence about some
    other navigation. Deferring the comparison must not turn "I could not tell"
    into "I will take it": the honest answer is the manufactured 200 carrying
    `status_is_real=False`, which grants it no evidentiary weight downstream.
    """
    browser(status=None, order="event_first", pre_reply_events=[{
        "method": "Network.responseReceived",
        "params": {"type": "Document", "loaderId": "NOT-OURS",
                   "frameId": "NOT-OURS-EITHER",
                   "response": {"status": 500, "url": "https://elsewhere.test/"}}}])
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status == 200, \
        "an unrelated document must not supply our status"
    assert result.ok is True, result.error


def test_no_document_response_at_all_is_still_an_honest_manufactured_200(browser):
    """The buffer must not invent a status when nothing ever arrived.

    Same guarantee as `test_an_unobserved_status_is_declared_manufactured`, under
    the real frame order -- an empty buffer resolves to nothing, not to a default.
    """
    browser(status=None, order="event_first")
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status == 200
    assert result.ok is True, result.error


def test_a_second_navigation_is_judged_on_its_own_identity(monkeypatch):
    """REGRESSION for the SECOND failure mode, measured 2026-09-06.

    `navigate()` resets `_document_status` and `_loaded` and used to leave
    `_frame_id`/`_loader_id` holding the PREVIOUS navigation's values. On a real
    browser, navigation 2's response arrived carrying loader `74EF0AA4...` while
    the session still held navigation 1's `27C453C2...` -- so the comparison DID
    run, genuinely mismatched, and lost the status a second and different way.

    No caller navigates twice today: `_tier_cdp_impl` builds a fresh session per
    fetch. This is a latent defect and the assertion is what keeps it closed.
    """
    class TwoNavigations:
        """Each navigation gets its own loaderId, response before reply."""

        def __init__(self):
            self.n = 0
            self._out = []

        def send(self, raw):
            msg = json.loads(raw)
            method = msg["method"]
            params = msg.get("params") or {}
            if method == "Page.navigate":
                self.n += 1
                loader = "LOADER-NAV-%d" % self.n
                frame = "FRAME-1"
                self._out.append({
                    "method": "Network.responseReceived",
                    "params": {"type": "Document", "loaderId": loader,
                               "frameId": frame,
                               "response": {"status": 400 + self.n,
                                            "url": URL}}})
                self._out.append({"id": msg["id"],
                                  "result": {"frameId": frame,
                                             "loaderId": loader}})
                self._out.append({"method": "Page.frameStoppedLoading",
                                  "params": {"frameId": frame}})
                return
            result = {}
            if method == "Target.createTarget":
                result = {"targetId": "TGT1"}
            elif method == "Target.attachToTarget":
                result = {"sessionId": "SESS1"}
            self._out.append({"id": msg["id"], "result": result})

        def recv(self, timeout=None):
            if not self._out:
                raise TimeoutError("nothing queued")
            return json.dumps(self._out.pop(0))

        def close(self):
            pass

    fake = TwoNavigations()
    monkeypatch.setattr(tiers, "_ws_connect", lambda url, **kwargs: fake)
    session = tiers._CDPSession("ws://fake", time.monotonic() + 10.0)
    session.open_target()

    first = session.navigate(URL)
    second = session.navigate(URL)

    assert first == 401, "navigation 1 must report its own status"
    assert second == 402, \
        "navigation 2 must be judged on ITS identity, not the one left by navigation 1"


def test_a_same_frame_renavigation_reports_the_final_documents_status(browser):
    """REGRESSION for review round 4's critical, and it is a regression THIS
    change introduced rather than a pre-existing one.

    A page can navigate its own top frame after the one we asked for -- a meta
    refresh, a JS `location =`, an SSO bounce, which is exactly what this tier
    meets, since it is entered on sign-in walls. That second navigation gets a
    NEW loaderId on the SAME frameId.

    `_is_our_navigation` rejects it: its first clause fires on the loaderId and
    returns the comparison, so the frameId fallback is never reached. But
    `Page.frameStoppedLoading` accepts by frameId and ends the wait, and the tier
    then evaluates the NEW document. So the caller receives the new document's
    content and URL carrying the OLD document's status.

    On `main` this was harmless because the capture never fired, so the status
    was always labelled `status_is_real=False` and carried no weight. Once the
    capture works, the same stale status is labelled REAL -- a wrong status read
    as the origin's own, which is the one direction the invariant forbids.
    """
    # `final_loader` must AGREE with the event. The renavigation is real, so the
    # browser's own answer about its top frame is the new document -- and since
    # review round 6 the tier asks it. Leaving the two to disagree describes a
    # browser state that cannot exist, and the tier would rightly withdraw a
    # status this test is here to say it keeps.
    browser(status=200, order="event_first", final_loader="LOADER-2-CLIENT-SIDE",
            extra_events=[{
                "method": "Network.responseReceived",
                "params": {"type": "Document", "loaderId": "LOADER-2-CLIENT-SIDE",
                           "frameId": FRAME,  # SAME top frame, new navigation
                           "response": {"status": 404,
                                        "url": "https://example.com/gone"}}}])
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status == 404, \
        "the FINAL document's status must be reported, not the one it replaced"
    assert result.ok is False, "a 404 final document is not a successful fetch"


def test_an_iframe_is_still_rejected_after_the_same_frame_rule(browser):
    """The same-frame rule must not become a door for iframes.

    Same shape as the test above, but the second Document carries a DIFFERENT
    frameId -- which is what an iframe is. It must NOT be adopted, so the main
    document's 404 has to survive a 200 iframe loading after it.
    """
    browser(status=404, order="event_first", extra_events=[{
        "method": "Network.responseReceived",
        "params": {"type": "Document", "loaderId": "SOME-IFRAME-LOADER",
                   "frameId": "IFRAME-9",     # a DIFFERENT frame: an iframe
                   "response": {"status": 200,
                                "url": "https://ads.example/x"}}}])
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status == 404, \
        "a different frameId is an iframe and must never supply our status"
    assert result.ok is False


def test_a_renavigation_during_the_reads_withdraws_the_stale_status(browser):
    """REGRESSION for the delta round's critical, and it is the SECOND half of a
    window this change already closed once.

    Round 4 closed the window BEFORE `navigate()` returns. This is the window
    AFTER it: `_tier_cdp_impl` copies the status into a local and then reads the
    DOM with two `Runtime.evaluate` calls, and every `call()` pumps events. A
    page that replaces its own top frame in that gap -- a meta refresh, a JS
    `location =`, an SSO bounce, all of which this tier meets, being entered on
    sign-in walls -- moves the SESSION's status underneath a value already taken.

    Here the first document is a 404 and the page then navigates itself to a real
    one. Unfixed, the tier reports the DEAD document's 404 over the LIVE
    document's HTML: a real page is rejected and recorded `origin_4xx_block`,
    which is PERSISTENT and walks the whole domain off tier 0 -- the only tier
    that reads a PDF or returns a JSON body verbatim.
    """
    browser(status=404, final_loader="LOADER-2-AFTER-LOAD", post_load_events=[{
        "method": "Network.responseReceived",
        "params": {"type": "Document", "loaderId": "LOADER-2-AFTER-LOAD",
                   "frameId": FRAME,          # SAME top frame, a new document
                   "response": {"status": 200,
                                "url": "https://example.com/real"}}}])
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status == 200, \
        "a status whose document has been replaced must not be reported as the page's"
    assert result.ok is True, result.error


def test_the_content_gate_is_told_a_replaced_status_is_not_real(browser, monkeypatch):
    """The delta round's own scenario in its own direction -- the DANGEROUS one.

    Document A is a 200; the page then replaces itself with a 404. Unfixed, the
    caller is handed the 404's HTML and URL under a 200 the origin did send, but
    FOR A DIFFERENT DOCUMENT, labelled `status_is_real=True` -- so the content
    gate grants it the evidentiary weight of an origin's own 2xx, and the
    embeddable-marker exemption that weight buys.

    `result.status` cannot express this: 200 is what the tier reports either way,
    because a withdrawn status falls back to the manufactured 200. What changes
    is the LABEL on it, so the label is what this asserts.
    """
    real = tiers.is_real_content
    seen = {}

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(tiers, "is_real_content", spy)
    browser(status=200, final_loader="LOADER-2-AFTER-LOAD", post_load_events=[{
        "method": "Network.responseReceived",
        "params": {"type": "Document", "loaderId": "LOADER-2-AFTER-LOAD",
                   "frameId": FRAME,
                   "response": {"status": 404,
                                "url": "https://example.com/gone"}}}])
    tiers.tier_cdp(URL, total_timeout=5.0)

    assert seen.get("status_is_real") is False, \
        ("a status whose document has been replaced is no longer the origin's "
         "answer about the page we actually read")


def test_a_redirect_hop_does_not_withdraw_the_status(browser):
    """THE CONTROL, and it is the arm that must NOT fire.

    Redirect hops share a loaderId, so they are the SAME navigation and the
    counter must not move for them -- otherwise every redirected fetch loses its
    real status and the withdrawal becomes the defect. Same shape as the test
    above, same `post_load_events` channel, differing only in the loaderId.
    """
    browser(status=404, post_load_events=[{
        "method": "Network.responseReceived",
        "params": {"type": "Document", "loaderId": LOADER,   # OURS: one navigation
                   "frameId": FRAME,
                   "response": {"status": 404,
                                "url": "https://example.com/hop"}}}])
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status == 404, \
        "a hop within our own navigation must keep the real status"


def test_a_replacement_nobody_announced_is_still_caught(browser, monkeypatch):
    """REGRESSION for review round 6's critical, and it is the reason the
    previous two fixes were not enough.

    Both earlier fixes were OBSERVATIONAL: they asked whether a replacement had
    been SEEN. `call()` returns on its matching reply and drains nothing after
    it, so a response frame that lands after the last evaluation reply is never
    read -- and an observational check then reports a document that was replaced
    as the one we paired the status with.

    HERE NO EVENT IS DELIVERED AT ALL. The only thing that says the page moved is
    the browser's own answer about its top frame, which is exactly the evidence a
    dropped frame cannot take away. If the tier still reports the old status as
    real, it is deciding this from events, which is the defect.
    """
    real = tiers.is_real_content
    seen = {}

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(tiers, "is_real_content", spy)
    browser(status=200, final_loader="LOADER-2-NOBODY-SAW-IT")   # no events at all
    tiers.tier_cdp(URL, total_timeout=5.0)

    assert seen.get("status_is_real") is False, \
        ("the status must be withdrawn on the browser's own answer, with no event "
         "having been delivered to notice the replacement")


def test_an_unanswerable_frame_tree_query_costs_the_status_and_not_the_fetch(browser, monkeypatch):
    """The query FAILS CLOSED, and failing closed must not fail the fetch.

    Two properties in one arm, and they pull in opposite directions. A browser
    that cannot answer which document it is showing has not told us the status is
    still ours, so the status goes. But the page has already been read, and
    raising here would convert a completed fetch into a tier failure -- which is
    the mistake this file made once before, when a cold-cache user-agent probe
    raised out from under an open browser and released its profile claim.
    """
    real = tiers.is_real_content
    seen = {}

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(tiers, "is_real_content", spy)
    browser(status=200, fail_on="Page.getFrameTree")
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert seen.get("status_is_real") is False, \
        "a browser that could not answer has not confirmed the status is still ours"
    assert result.ok is True, \
        "the page was read; an unanswerable query must not throw the fetch away"
    assert result.content, "the content must survive the withdrawn status"


def test_an_observed_status_is_reported_as_REAL_and_admits_an_embedded_widget(browser, monkeypatch):
    """THE POSITIVE CONTROL, and the suite had none until now.

    Every other assertion on `status_is_real` in this file is `is False`. That
    polarity is free: a tier that withdrew EVERY status would satisfy all of them
    while destroying the capability this branch exists to add. Measured --
    hard-coding `status_is_real=False` in `_tier_cdp_impl` passes all 1153 tests.

    The observable is chosen so the label has TEETH rather than being asserted for
    its own sake. `class="cf-turnstile"` is an EMBEDDABLE marker: a site may put
    one on its own contact form, so it is evidence of a wall only when the caller
    manufactured the status. Told the 200 is the origin's own, the content gate
    admits this page; told it is ours, the gate scans the embeddable tuple too and
    rejects it. So the same bytes come back ok on a real status and failed on a
    withdrawn one, and that is the fetch this branch is here to win.
    """
    real = tiers.is_real_content
    seen = {}

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(tiers, "is_real_content", spy)
    page = ("<html><head><title>Contact our team</title></head><body>"
            "<h1>Contact our team</h1><p>" + ("word " * 200) + "</p>"
            '<form><div class="cf-turnstile" data-sitekey="0x0"></div></form>'
            "</body></html>")
    # A clean load: nothing replaces anything, and the browser agrees the top
    # frame is still the document we navigated to.
    browser(status=200, html=page)
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert seen.get("status_is_real") is True, \
        "an observed status that nothing replaced must be reported as the origin's own"
    assert result.status == 200
    assert result.ok is True, \
        ("an embeddable widget on a page the origin served at 200 is a site's own "
         "form, not a wall -- withdrawing the status is what loses this fetch: %s"
         % result.error)


# ---- Teardown ----------------------------------------------------------------

def test_a_healthy_run_closes_its_own_target(browser):
    """The control arm: the happy path works and cleans up after itself."""
    fake = browser()
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.ok is True, result.error
    assert result.content
    assert fake.closed_targets == ["TGT1"], "our target, exactly once, by id"
    assert fake.closed is True, "the socket must be closed"


def test_the_browsers_last_page_is_never_closed(browser):
    """REGRESSION, review round 1's must-fix.

    Chrome exits when its last page closes. If the owner closes his own tabs
    while a fetch is running, ours becomes the last page -- and closing it would
    end his session. This project has already lost his browser once, from the
    other direction, when a cleanup closed fifteen tabs.

    So when ours is the only page left we navigate it to about:blank and leave
    it standing. One stranded empty tab beats one dead browser.
    """
    fake = browser(page_targets=1)
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.ok is True, result.error
    assert fake.closed_targets == [], \
        "closing the last page would have taken the owner's browser down"
    assert fake.navigated[-1] == "about:blank", \
        "the last page must be parked, not closed"


def test_a_target_is_closed_when_the_browser_has_other_pages(browser):
    """The paired control for the test above.

    Its whole point is that the last-page guard is a CONDITION and not a blanket
    refusal to clean up -- without this arm, a teardown that never closed
    anything would pass the test above and leak a tab on every fetch.
    """
    fake = browser(page_targets=4)
    tiers.tier_cdp(URL, total_timeout=5.0)

    assert fake.closed_targets == ["TGT1"]
    assert "about:blank" not in fake.navigated[1:], \
        "a browser with other pages must have our target CLOSED, not parked"


def test_the_target_is_closed_before_the_socket(browser):
    """Ordering: closing the socket first strands the teardown and leaks a tab."""
    fake = browser()
    order = []
    original_send, original_close = fake.send, fake.close

    def send(raw):
        if "closeTarget" in raw:
            order.append("close-target")
        return original_send(raw)

    def close():
        order.append("close-socket")
        return original_close()

    fake.send, fake.close = send, close
    tiers.tier_cdp(URL, total_timeout=5.0)

    assert order == ["close-target", "close-socket"], order


def test_teardown_closes_exactly_once(browser):
    """Teardown runs through ONE path, so it cannot double-close."""
    fake = browser(fail_on="Runtime.evaluate")
    tiers.tier_cdp(URL, total_timeout=5.0)

    assert fake.closed_targets == ["TGT1"], "exactly one close, not two"


# ---- The load wait must not spend the reserve it set aside --------------------

def test_a_page_that_never_finishes_loading_still_returns_its_dom(browser):
    """`M-CDP-NOLOAD-BUDGET`. The pump loop's comment promises this and the code
    used to do the opposite.

    A page that never fires load is not a failure -- plenty of real pages hold a
    connection open forever -- so the wait is supposed to give up and fall
    through to whatever the DOM already holds. It set 0.5s aside for the three
    round trips that come after it.

    THE GUARD WAS ON THE LOOP'S ENTRY AND THE SPEND WAS INSIDE THE RECV. `_left()
    <= 0.5` decides whether to enter, and the `_recv` it then enters was handed
    the WHOLE remaining budget, so one silent socket consumed the reserve and
    everything after it in a single call. The break came with the deadline
    already crossed; `evaluate` raised out of `call()`'s deadline guard, and the
    DOM the loop was holding was thrown away with it.

    A bigger reserve cannot fix that shape and the code comment says so -- what
    the recv is ALLOWED to spend has to be bounded, not just when it may start.

    The fake stalls for its full timeout plus a margin, which is what makes this
    fail against the unfixed code rather than describe it: an instant raise would
    leave the budget intact and the tier would finish either way.
    """
    fake = browser(emit_load=False, stall=True)

    result = tiers.tier_cdp(URL, total_timeout=2.0)

    assert result.ok is True, \
        "a page that never fires load must still return the DOM it already has"
    assert "A real page" in result.html
    assert fake.navigated[0] == URL


def test_a_page_streaming_events_forever_still_returns_its_dom(browser):
    """The reserve's OTHER half: the wait may not START inside the reserve.

    Bounding what one read may spend is not sufficient on its own. A page
    emitting a continuous `Network.*` stream (ads, video, long-polling) answers
    every read immediately, so no read ever times out and the size guard is the
    only thing that ends the wait. Take it away and the loop spins until the
    deadline, then `evaluate` raises out of `call()` and the DOM goes with it --
    the same lost fetch, reached by the other route.

    THIS IS THE ARM THE OTHER TESTS CANNOT REACH, and its absence showed up as a
    surviving mutant. The fake in the test above runs dry, so removing the size
    guard costs it nothing.
    `test_an_endless_event_stream_cannot_outrun_the_deadline` does stream
    forever, but it also suppresses the `Page.navigate` reply, so the tier dies
    in `call()` and never reaches this loop at all.

    The stream starts only once the queued protocol frames are drained, so the
    navigation still completes normally and it is the WAIT that meets the
    stream.
    """
    fake = browser(emit_load=False)
    queued = fake.recv

    def recv(timeout=None):
        if fake._out:
            return queued(timeout)
        return json.dumps({"method": "Network.dataReceived",
                           "params": {"requestId": "x"}})

    fake.recv = recv
    result = tiers.tier_cdp(URL, total_timeout=2.0)

    assert result.ok is True, \
        "the load wait must end on the reserve, not on the page running out of events"
    assert "A real page" in result.html


def test_a_load_arriving_inside_the_reserve_is_not_waited_for(browser):
    """A load arriving inside the reserve is not waited for."""
    pre, post = HTML, HTML.replace("A real page", "The completed page")
    fake = browser(emit_load=False)
    t0 = time.monotonic()
    load_at = t0 + 1.7          # inside the last 0.5s of a 2.0s budget
    queued = fake.recv
    fired = []

    def recv(timeout=None):
        if fake._out:
            return queued(timeout)
        now = time.monotonic()
        # A real socket returns the frame if it arrives inside the window the
        # caller asked for, and times out otherwise. That is the whole
        # difference the bound makes, so the fake has to honour it.
        if not fired and (timeout is None or now + timeout >= load_at):
            time.sleep(max(0.0, load_at - now))
            fired.append(True)
            fake.html = post
            return json.dumps({"method": "Page.frameStoppedLoading",
                               "params": {"frameId": FRAME}})
        time.sleep(max(0.0, timeout or 0))
        raise TimeoutError("nothing queued")

    fake.html = pre
    fake.recv = recv
    result = tiers.tier_cdp(URL, total_timeout=2.0)

    assert result.ok is True, "the fetch must still succeed -- that is the point of the reserve"
    assert "A real page" in result.html, \
        "the reserve is not waited through: the PRE-load snapshot is what comes back"
    assert "The completed page" not in result.html
    assert fired == [], \
        "the load event must not have been consumed -- if it was, the read was not bounded"


def test_a_navigation_between_the_reads_cannot_mix_two_documents(browser):
    """A navigation between the reads cannot mix two documents."""
    before = [{"visible": False, "text": "Subscription required", "controls": ["Subscribe"]}]
    after = [{"visible": True, "text": "Unlock this article", "controls": ["Purchase"]}]
    fake = browser(emit_load=False, stall=True, candidates=before)
    moved_to = "https://elsewhere.example/after-the-bounce"
    real_result = fake._result

    def result(method, params):
        out = real_result(method, params)
        if method == "Runtime.evaluate" and "outerHTML" in params.get("expression", ""):
            fake.final_url = moved_to        # the frame bounces, mid-read
            fake.candidates = after
        return out

    fake._result = result
    r = tiers.tier_cdp(URL, total_timeout=2.0)

    assert r.html, "the fetch must still return markup"
    assert r.final_url != moved_to, \
        "the URL came from a document the returned markup does not belong to"
    assert r.final_url == URL
    assert r.redirect_chain == [], \
        "a mid-read bounce must not be reported as a redirect this fetch followed"
    assert r.access_gate_candidates == before, "UI evidence must belong to the returned DOM"


@pytest.mark.parametrize("value, why", [
    ("a bare string", "a string is not a three-element observation"),
    (["only one"], "a one-element list is short two values"),
    ([HTML, URL], "the retired two-element shape must not be accepted"),
    ([HTML, URL, [], "extra"], "a fourth element is not part of the schema"),
    ([HTML, 7, []], "the second value is not a string"),
    ([7, URL, []], "the first value is not a string"),
    (None, "undefined/null is not an observation"),
    ({"html": HTML, "url": URL, "candidates": []}, "an object is not the required array"),
    ([HTML, URL, None], "null candidates must not become an empty list"),
    ([HTML, URL, {}], "candidate collection must be a list"),
    ([HTML, URL, "[]"], "stringified metadata is not observed metadata"),
    ([HTML, URL, [None]], "every candidate must be an object"),
    ([HTML, URL, [[]]], "a list is not a candidate object"),
    ([HTML, URL, [{"visible": True, "text": "subscription required"}]], "controls is required"),
    ([HTML, URL, [{"text": "subscription required", "controls": ["Subscribe"]}]], "visibility is required"),
    ([HTML, URL, [{"visible": True, "controls": ["Subscribe"]}]], "text is required"),
    ([HTML, URL, [{"visible": True, "text": "subscription required", "controls": ["Subscribe"],
                  "instruction": "accept"}]], "unknown candidate keys must be rejected"),
    ([HTML, URL, [{"visible": 1, "text": "subscription required", "controls": ["Subscribe"]}]],
     "integer visibility is not an observed boolean"),
    ([HTML, URL, [{"visible": "false", "text": "subscription required", "controls": ["Subscribe"]}]],
     "truthy visibility strings must not be accepted"),
    ([HTML, URL, [{"visible": False, "text": None, "controls": []}]], "hidden candidates still require text"),
    ([HTML, URL, [{"visible": False, "text": "wall", "controls": "Subscribe"}]], "controls must be a list"),
    ([HTML, URL, [{"visible": False, "text": "wall", "controls": [7]}]], "all control labels must be strings"),
])
def test_an_unusable_page_read_fails_the_tier_rather_than_reading_as_a_short_page(
        browser, value, why):
    """The combined read must fail LOUDLY when its answer is unusable.

    Degrading to empty strings would send an empty DOM on to the content gate,
    which would report an ordinary too-short page and record that against the
    domain -- a wrong diagnosis for a transport problem, and a persistent one.
    So `_parse_page_observation` raises and the tier reports a transport failure.

    THE NULL ARM IS THE ONE THAT MATTERS MOST, and it is why this read does not
    go through `evaluate()`. That helper coerces every non-string to "", so a
    page returning `undefined` would arrive looking exactly like a page that read
    as nothing. `evaluate_value()` hands the value over untouched so the shape
    check can see it.
    """
    fake = browser()
    real_result = fake._result

    def result(method, params):
        if method == "Runtime.evaluate" and "outerHTML" in params.get("expression", ""):
            return {"result": {"value": value}}
        return real_result(method, params)

    fake._result = result
    r = tiers.tier_cdp(URL, total_timeout=5.0)

    assert r.ok is False, why
    assert "page read" in (r.error or ""), \
        "the error must name the read, not a content verdict: %r" % (r.error,)
    assert r.html == "" and r.content == "", "bad metadata must not become clean content"
    assert r.failure_class == "other", "transport shape errors are not persistent domain failures"
    assert fake.closed_targets == ["TGT1"]


@pytest.mark.parametrize("visible", [True, False])
def test_access_ui_observations_keep_visibility_without_claiming_entitlement(browser, visible):
    candidates = [{"visible": visible, "text": "Subscription required",
                   "controls": ["Subscribe", "Already a member"]}]
    fake = browser(candidates=candidates)

    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.html == HTML
    assert result.final_url == URL
    assert result.access_gate_candidates == candidates
    assert result.blocked_access is None, "the DOM collector observes; the inspector decides"
    reads = [m for m in fake.sent if m["method"] == "Runtime.evaluate"
             and "outerHTML" in m["params"]["expression"]]
    assert len(reads) == 1, "collecting UI metadata must not add another DOM task"
    assert fake.navigated == [URL], "observation must never re-navigate"


def test_observation_candidates_survive_withdrawal_of_an_unpaired_status(browser):
    candidates = [{"visible": True, "text": "Subscribers only", "controls": ["Subscribe"]}]
    browser(status=200, final_loader="REPLACEMENT-DOCUMENT", candidates=candidates)

    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.status_is_real is False
    assert result.access_gate_candidates == candidates
    assert result.html == HTML



# ---- Containment -------------------------------------------------------------

def test_an_endless_event_stream_cannot_outrun_the_deadline(browser):
    """A chatty page must not be able to hold the read loop open forever.

    `_left()` floors at 0.1s so every blocking call gets a usable timeout --
    which means that once the budget is spent it keeps handing out 0.1s. A page
    emitting a continuous `Network.*` stream (ads, video, long-polling: not
    exotic) satisfies every recv inside that floor, so a loop that trusted only
    the recv timeout would never exit. The deadline is therefore checked
    explicitly on each turn.

    This fake never answers the command and never runs dry, which is exactly the
    shape that used to spin: the tier must give up on its own budget.
    """
    fake = browser()

    def recv(timeout=None):
        return json.dumps({"method": "Network.dataReceived",
                           "params": {"requestId": "x"}})

    def send(raw):
        return None          # the reply never arrives

    fake.recv, fake.send = recv, send
    result = tiers.tier_cdp(URL, total_timeout=1.0)

    assert result.ok is False
    assert result.elapsed_ms < 20000, \
        "the tier must end on its own deadline, not on the caller's patience"


def test_an_exhausted_budget_does_not_disarm_the_last_page_guard(browser):
    """REGRESSION where review findings #3 and #4 INTERACT, and the interaction
    is worse than either alone.

    `call()` refuses once the fetch deadline has passed -- the guard that stops a
    chatty page spinning the read loop. But `_is_last_page()` calls `call()`, and
    it catches `_CDPError` and returns False, meaning "not the last page". So on
    any fetch that used its whole budget the last-page guard SILENTLY DISARMED
    and teardown closed the target anyway. When ours really was the browser's
    final page, that is Chrome exiting and the owner's browser going with it --
    the exact hazard finding #3 is about, re-entering through the slow path.

    It is invisible to a fast test: the fake must genuinely consume the budget,
    and asserting merely that the target IS closed proves nothing either, because
    `call()` sends before it checks the deadline, so the command goes out either
    way. The property that actually separates the two is that the LAST page is
    NOT closed.

    Teardown now runs on its own budget, so the guard can still ask.
    """
    fake = browser(emit_load=False, stall=True, page_targets=1)
    tiers.tier_cdp(URL, total_timeout=0.4)

    assert fake.closed_targets == [],         "an exhausted budget must not disarm the last-page guard"
    assert fake.navigated[-1] == "about:blank",         "the last page must still be parked rather than closed"


def test_no_forbidden_command_is_sent_even_on_the_failure_paths(browser):
    """The never-send guarantee is a property of the CALL SITES, not the class.

    `call()` takes any method string and puts it on the wire, so nothing
    structurally prevents a forbidden verb -- an earlier docstring claimed
    otherwise and review round 1 was right to call that out. What holds the
    property is that every call site is in this file and none sends one. This
    asserts it over the methods actually sent, on the failure paths too, since
    those are where a teardown shortcut would be tempting.
    """
    for kw in ({}, {"fail_on": "Page.navigate"}, {"page_targets": 1},
               {"raise_on": "Runtime.evaluate"}):
        fake = browser(**kw)
        tiers.tier_cdp(URL, total_timeout=5.0)
        sent = set(fake.methods())
        for method in FORBIDDEN:
            assert method not in sent, "%s sent with %r" % (method, kw)


def test_a_transport_failure_fails_the_tier_and_does_not_escape(browser):
    """The boundary property, against the new transport.

    When this tier drove `playwright-cli`, an OSError from a spawn left the
    tier, left `cascade_fetch` (which sits in no try) and exited `main` as a
    traceback. A websocket has more ways to fail than a subprocess did.
    """
    browser(raise_on="Target.createTarget")
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.ok is False, "a transport failure must fail the tier"
    assert result.tier == "cdp"


def test_a_reported_protocol_error_fails_the_tier(browser):
    """A command the browser answers with an `error` is a failure, not a result."""
    browser(fail_on="Target.createTarget")
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.ok is False
    assert "refused" in (result.error or "")


def test_a_cleanup_failure_does_not_mask_the_bodys_error(browser):
    """An exception in a `finally` REPLACES the one in flight."""
    fake = browser(fail_on="Runtime.evaluate")
    original_send = fake.send

    def send(raw):
        if "closeTarget" in raw or "getTargets" in raw:
            raise OSError("cleanup denied")
        return original_send(raw)

    fake.send = send
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.ok is False
    assert "Runtime.evaluate refused" in (result.error or ""), \
        "the body's error must survive a failing cleanup"
    assert "cleanup denied" not in (result.error or "")


def test_a_cleanup_failure_after_a_successful_body_still_returns_ok(browser):
    """A completed fetch is not undone by a teardown that failed."""
    fake = browser()
    original_send = fake.send

    def send(raw):
        if "closeTarget" in raw or "getTargets" in raw:
            raise OSError("cleanup denied")
        return original_send(raw)

    fake.send = send
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.ok is True, result.error
    assert result.content


def test_a_socket_close_that_raises_still_does_not_fail_a_completed_fetch(browser):
    """The OTHER half of teardown, and it was untested until a mutation said so.

    `close()` does two things -- close the target, then close the socket -- and
    each is separately guarded. The target half was covered; the socket half was
    not, so removing its guard changed no test result. A mutation that SURVIVES
    is the only way that gap announces itself.
    """
    fake = browser()

    def close():
        raise OSError("the socket refused to close")

    fake.close = close
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.ok is True, result.error
    assert result.content, "a completed fetch must survive a failing teardown"


def test_a_failure_before_the_body_still_tears_down(browser):
    """Every post-connect exit runs teardown, not just the reported ones."""
    fake = browser(fail_on="Page.navigate")
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.ok is False
    assert fake.closed is True


# ---- Degradation -------------------------------------------------------------

def test_a_missing_websockets_package_costs_the_tier_and_not_the_fetch(monkeypatch):
    """`websockets` is a tier-2 fleet dependency, and tier 2 is a WARNING.

    So a correctly-provisioned box can be without it, exactly as with `pypdf`.
    """
    monkeypatch.setattr(tiers, "_ws_connect", None)
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.ok is False
    assert "websockets" in (result.error or "")
    assert result.tier == "cdp"


def test_an_absent_devtools_endpoint_is_a_tier_failure(monkeypatch):
    """Nothing listening is the ordinary case, not an error path.

    The browser is the owner's to start; this tier never launches one.
    """
    def refuse(port, timeout):
        raise tiers._CDPError("no DevTools endpoint on :%d" % port)

    monkeypatch.setattr(tiers, "_cdp_browser_ws_url", refuse)
    result = tiers.tier_cdp(URL, total_timeout=5.0)

    assert result.ok is False
    assert "cdp unavailable" in (result.error or "")


def test_an_unsafe_url_never_reaches_the_browser(monkeypatch):
    """The SSRF guard runs before anything is connected to."""
    reached = []
    monkeypatch.setattr(tiers, "_cdp_browser_ws_url",
                        lambda port, timeout: reached.append(port))
    result = tiers.tier_cdp("http://127.0.0.1:80/", total_timeout=5.0)

    assert result.ok is False
    assert "unsafe URL" in (result.error or "")
    assert reached == [], "the guard must run before the browser is contacted"
