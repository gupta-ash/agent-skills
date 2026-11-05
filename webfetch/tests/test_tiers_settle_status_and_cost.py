"""`tiers.py`: what the settle loop hands the gate, and what it costs.

TWO PROPERTIES OF `PlaywrightSession.fetch_html`, both about the loop that waits
out a self-clearing JS challenge.

1. THE STATUS IT HANDS UP. The loop decides "settled" with `_challenge_reason`
   under a `page_served` computed from the captured status, and `tier_playwright`
   then re-judges the same document with `is_real_content` under
   `status_is_real`. If the loop drops a status the ORIGIN sent, those two
   answers stop matching: `status_is_real=False` withdraws the embeddable-marker
   exemption, so the gate scans `_EMBEDDABLE_CHALLENGE_MARKUP` over a document
   the loop cleared with that scan switched off -- and rejects the page the wait
   just won. A stale NON-2xx is the opposite case and must still be dropped.

2. WHAT IT COSTS AN ORDINARY PAGE. `_challenge_reason` judges EXTRACTED text, so
   the precondition has to extract; `tier_playwright` then needs the same
   markdown for the gate and for the body. Trafilatura is the most expensive
   step on tiers 1 and 2 and the fetch deadline is R1's, so one document must
   cost one extraction.

NO TEST HERE MAY REACH A BROWSER. `_run_pw` and `_navigate_capture` are both
replaced; nothing spawns a subprocess and nothing opens a window.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import extract
import state
import tiers


URL = "https://example.com/article"

HEADLESS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) HeadlessChrome/151.0.0.0 Safari/537.36")

# A challenge the STRICT tuple catches, so it is a wall on a 2xx as well as on a
# 403 -- which is the case this file is about.
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


def article_html(n_chars: int = 5000, mount: bool = False) -> str:
    """A real article. `mount` adds the contact-form Turnstile widget a site
    embeds on its own page -- an _EMBEDDABLE_CHALLENGE_MARKUP marker, which is
    evidence only when the origin did NOT serve the page."""
    body = (_PARA * (n_chars // len(_PARA) + 1))[:n_chars]
    widget = ('<form><div class="cf-turnstile" data-sitekey="0x0"></div>'
              "</form>") if mount else ""
    return ("<html><head><title>Wide-body economics</title></head><body>"
            "<article><h1>Wide-body economics</h1><p>" + body
            + "</p></article>" + widget + "</body></html>")


class EvalScript:
    """Serve atomic page observations plus `_ensure_open`'s user-agent probe.
    List entries supply [html, url, candidates]; strings supply plain HTML."""

    def __init__(self, docs):
        self.docs = list(docs)
        self.outer_html_calls = 0

    def __call__(self, args, timeout=None):
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


@pytest.fixture(autouse=True)
def _clean_session_cache():
    """`get_session` caches per mode for the life of the PROCESS."""
    tiers._SESSIONS.clear()
    yield
    tiers._SESSIONS.clear()


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch):
    monkeypatch.setattr(tiers, "CHALLENGE_POLL_S", 0.01, raising=False)
    monkeypatch.setattr(tiers, "CHALLENGE_SETTLE_BUDGET", 5.0, raising=False)


@pytest.fixture
def stub_browser(tmp_path, monkeypatch):
    """A stubbed browser: no subprocess, no window, state DB under tmp_path."""
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(tiers, "_list_sessions", lambda open_only=False: [])
    monkeypatch.setattr(tiers, "_claim_profile_dir",
                        lambda: (str(tmp_path / "profile"), None))
    monkeypatch.delenv("WEBFETCH_DISABLE_HEADED", raising=False)

    def install(docs, status=200):
        rec = EvalScript(docs)
        monkeypatch.setattr(tiers, "_run_pw", rec)
        monkeypatch.setattr(
            tiers, "_navigate_capture",
            lambda session_name, url, timeout: {
                "ok": True, "status": status,
                "headers": {"server": "cloudflare"}, "url": url})
        return rec

    return install


@pytest.fixture
def count_extractions(monkeypatch):
    """Counts every real `html_to_markdown` call the fetch path makes."""
    calls = []
    real = extract.html_to_markdown

    def spy(html, url=None):
        calls.append(url)
        return real(html, url)

    monkeypatch.setattr(tiers, "html_to_markdown", spy)
    return calls


# ---- 1. the status the settle loop hands the gate ----------------------------

def test_a_cleared_challenge_keeps_the_status_the_origin_sent(stub_browser):
    """R1. A Turnstile interstitial served at 200 clears into a real article
    that carries a contact-form Turnstile mount of its own.

    The loop cleared it under `page_served=True`, which does not treat that
    mount as evidence. Dropping the 200 flips the gate to
    `status_is_real=False`, which scans the embeddable markers -- and the
    article is then rejected for the widget on its own contact form, i.e. the
    loop throws away the document it exists to rescue.
    """
    stub_browser([INTERSTITIAL, article_html(5000, mount=True)], status=200)

    res = tiers.tier_playwright(URL, mode="headless", total_timeout=30.0)

    assert res.ok is True, (
        "the settled article must survive the gate that the loop already "
        "cleared it against; rejected with: %r" % res.error)
    assert res.status == 200
    assert "Wide-body" in (res.content or "")


def test_the_settled_documents_status_is_the_captured_2xx(stub_browser):
    """The same property one layer down, so the tuple `fetch_html` returns is
    pinned and not only the tier's verdict."""
    stub_browser([INTERSTITIAL, article_html(5000)], status=200)

    session = tiers.get_session("headless")
    _final_url, html, status, _headers, _candidates = session.fetch_html(
        URL, total_timeout=30.0)

    assert "Wide-body" in html
    assert status == 200, (
        "the origin served this navigation with a 2xx and never sent a second "
        "status; got %r" % (status,))


def test_a_cleared_challenge_still_drops_a_stale_non_2xx(stub_browser):
    """THE OTHER ARM, preserved. A 403 belongs to the interstitial, and
    `is_real_content` rejects on status before it looks at anything else.

    Not a regression test -- this behaviour is unchanged -- but the change
    above is a branch, and an unguarded branch is one edit from taking both
    arms.
    """
    stub_browser([INTERSTITIAL, article_html(5000)], status=403)

    session = tiers.get_session("headless")
    _final_url, _html, status, _headers, _candidates = session.fetch_html(
        URL, total_timeout=30.0)
    assert status is None, (
        "a status read off the wall must not be presented as the settled "
        "document's own; got %r" % (status,))

    tiers._SESSIONS.clear()
    stub_browser([INTERSTITIAL, article_html(5000)], status=403)
    res = tiers.tier_playwright(URL, mode="headless", total_timeout=30.0)
    assert res.ok is True, res.error
    assert res.status == 200, "the documented placeholder, not the 403"


def test_an_unsettled_challenge_keeps_reporting_the_captured_status(stub_browser):
    """Budget exhausted: the loop's own comment says the ORIGINAL status
    stands, and the tier fails on its own gate. The 2xx branch above must not
    reach a document that never cleared."""
    stub_browser([INTERSTITIAL], status=403)

    session = tiers.get_session("headless")
    _final_url, _html, status, _headers, _candidates = session.fetch_html(
        URL, total_timeout=30.0)
    assert status == 403, "the wall's own status is the honest one here"


# ---- 2. what one document costs ----------------------------------------------

def test_an_ordinary_page_is_extracted_exactly_once(stub_browser,
                                                    count_extractions):
    """R1 cost. The settle precondition and the gate judge the SAME document;
    extracting it twice spends the fetch deadline on nothing."""
    stub_browser([article_html(5000)], status=200)

    res = tiers.tier_playwright(URL, mode="headless", total_timeout=30.0)

    assert res.ok is True, res.error
    assert len(count_extractions) == 1, (
        "one document must cost one trafilatura run; extracted %d times (urls "
        "%r)" % (len(count_extractions), count_extractions))


def test_a_settled_page_is_extracted_once_per_document_read(stub_browser,
                                                            count_extractions):
    """Two reads -- the interstitial and the article -- so two extractions, and
    the tier must not add a third for the document it was just handed."""
    stub_browser([INTERSTITIAL, article_html(5000)], status=200)

    res = tiers.tier_playwright(URL, mode="headless", total_timeout=30.0)

    assert res.ok is True, res.error
    assert len(count_extractions) == 2, (
        "expected one extraction per document read; got %d (urls %r)"
        % (len(count_extractions), count_extractions))


def test_the_extraction_is_not_reused_across_documents(stub_browser):
    """The memo is keyed on the document, not on the session. A settle poll
    that returns a DIFFERENT document must be judged on its own text, or the
    loop would clear on the interstitial's own extraction."""
    stub_browser([INTERSTITIAL, article_html(5000)], status=200)

    session = tiers.get_session("headless")
    _final_url, html, _status, _headers, _candidates = session.fetch_html(
        URL, total_timeout=30.0)
    md = tiers._extracted_markdown(session, html, URL)
    assert "Wide-body" in md, (
        "the memo handed back an extraction of some other document: %r"
        % md[:120])


@pytest.mark.parametrize("mode", ["headless", "headed"])
@pytest.mark.parametrize("visible", [True, False])
def test_one_document_observation_preserves_access_ui_and_extraction_cost(
        stub_browser, count_extractions, mode, visible):
    candidates = [{"visible": visible, "text": "Subscription required",
                   "controls": ["Subscribe", "Already a member"]}]
    rec = stub_browser([[article_html(), URL, candidates]])

    result = tiers.tier_playwright(URL, mode=mode, total_timeout=30.0)

    assert result.html == article_html()
    assert result.final_url == URL
    assert result.access_gate_candidates == candidates
    assert result.blocked_access is None, "collection is not an entitlement decision"
    assert rec.outer_html_calls == 1
    assert len(count_extractions) == 1, "UI collection must not add another extraction"


def test_settle_keeps_candidates_from_the_final_document_only(stub_browser):
    before = [{"visible": False, "text": "Subscribers only", "controls": ["Subscribe"]}]
    after = [{"visible": True, "text": "Unlock this article", "controls": ["Purchase"]}]
    rec = stub_browser([[INTERSTITIAL, URL, before], [article_html(), URL, after]])

    final_url, html, status, headers, candidates = tiers.get_session("headless").fetch_html(
        URL, total_timeout=30.0)

    assert html == article_html() and final_url == URL
    assert candidates == after
    assert status == 200 and headers == {"server": "cloudflare"}
    assert rec.outer_html_calls == 2


@pytest.mark.parametrize("during_settle", [False, True])
@pytest.mark.parametrize("candidates", [
    None,
    {},
    [{"visible": True, "text": "Subscription required"}],
    [{"visible": "false", "text": "Subscription required", "controls": ["Subscribe"]}],
    [{"visible": False, "text": "Subscription required", "controls": [None]}],
    [{"visible": True, "text": "Subscription required", "controls": ["Subscribe"], "accept": True}],
])
def test_malformed_ui_metadata_cannot_become_clean_content(stub_browser, candidates, during_settle):
    docs = [[article_html(), URL, candidates]]
    if during_settle:
        docs.insert(0, INTERSTITIAL)
    rec = stub_browser(docs)

    result = tiers.tier_playwright(URL, mode="headless", total_timeout=30.0)

    assert result.ok is False
    assert "page read" in result.error
    assert result.html == "" and result.content == ""
    assert rec.outer_html_calls == (2 if during_settle else 1)


def test_a_document_url_change_withdraws_status_headers_and_cookies(stub_browser):
    final_url = "https://example.com/replaced-document"
    candidates = [{"visible": True, "text": "Subscription required", "controls": ["Subscribe"]}]
    stub_browser([[article_html(), final_url, candidates]], status=200)

    result = tiers.tier_playwright(URL, mode="headless", total_timeout=30.0)

    assert result.final_url == final_url
    assert result.html == article_html()
    assert result.status_is_real is False
    assert result.headers == {} and result.set_cookie == []
    assert result.access_gate_candidates == candidates


def test_a_captured_redirect_keeps_its_status_and_extracts_at_its_observed_url_once(
        stub_browser, monkeypatch, count_extractions):
    final_url = "https://example.com/redirected-article"
    rec = stub_browser([[article_html(), final_url, []]])
    monkeypatch.setattr(tiers, "_navigate_capture", lambda session_name, url, timeout: {
        "ok": True, "status": 200, "url": final_url,
        "headers": {"set-cookie": "sid=actual; Path=/"}})

    result = tiers.tier_playwright(URL, mode="headless", total_timeout=30.0)

    assert result.ok is True, result.error
    assert result.status_is_real is True and result.status == 200
    assert result.set_cookie == ["sid=actual; Path=/"]
    assert result.final_url == final_url
    assert count_extractions == [final_url]
    assert rec.outer_html_calls == 1
