"""PDF and non-HTML textual bodies at tier 0.

PDF bytes must be parsed as documents, not returned as object dictionaries from
an HTML extractor. JSON, JavaScript and plain text need their own textual path
instead of being misclassified as empty HTML.

Hermetic: a loopback http.server serving fixed bytes and headers. No browser, no
external network, no shared state.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import builtins
import http.server
import threading

import pytest

import tiers


# ---- minimal PDFs, built here so the suite carries no binary fixtures --------

def build_pdf(with_text=True, prologue=b""):
    content = (b"BT /F1 18 Tf 20 100 Td (Hello phase one PDF text layer) Tj ET\n"
               if with_text else b"")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n" + prologue)
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1) + b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref_at))
    return bytes(out)


PDF_TEXT = build_pdf(True)
PDF_NO_TEXT = build_pdf(False)
# A genuine PDF that also carries `<html` early -- an XFA form embeds XHTML,
# and a print-to-PDF of a web page can carry the source in its metadata. This
# is the fixture that makes the RV-2 fall-through condition load-bearing: the
# HTML evidence is present, so ONLY the pdf_unreadable restriction keeps this
# document out of the HTML path.
PDF_NO_TEXT_WITH_HTML_MARKER = build_pdf(
    False, prologue=b"% <html> embedded XFA-style marker\n")
# A text banner ahead of the signature -- a proxy or CGI preamble. Round 2
# accepted this via a 1 KiB window; F2 replaced that with a signature-position
# rule, so it no longer enters the PDF branch. Kept as the fixture that PINS
# that reduction rather than letting it pass unnoticed.
PDF_BANNER_PREFIX = b"\xef\xbb\xbf" + b"# leading junk from a proxy\n" * 12 + PDF_TEXT
# Inside the widened skip: a BOM and ASCII whitespace only.
PDF_BOM_AND_WHITESPACE = b"\xef\xbb\xbf" + b"\r\n\t  \n" + PDF_TEXT

HTML_BODY = (b"<html><head><title>Sign in</title></head><body><p>"
             + b"You must sign in to view this document. " * 8
             + b"</p></body></html>")

# RV-2: an HTML page that DOCUMENTS the PDF header. `%PDF-` lands inside the
# first KiB, so the byte sniff -- correctly -- treats it as a PDF candidate.
# What must not happen is the failed parse being terminal.
HTML_ABOUT_PDF = (
    b"<!doctype html><html><head><title>PDF file structure</title></head><body>"
    b"<h1>The header</h1><pre>%PDF-1.7\n1 0 obj</pre>"
    b"<p>" + b"Every PDF file begins with a header line naming the version of "
    b"the specification the file claims to conform to. " * 6 + b"</p>"
    b"</body></html>")

JSON_BODY = b'{"login":"octocat","id":1,"public_repos":8,"followers":20}'
JS_BODY = b"export function add(a, b) {\n  return a + b;\n}\n"
TEXT_BODY = b"CHIP-8 opcode table\n\n00E0  CLS\n00EE  RET\n1NNN  JP addr\n"


# ---- loopback origin ---------------------------------------------------------

@pytest.fixture()
def serve():
    """Yields serve(body, content_type, header_name='Content-Type') -> url."""
    state = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body, ct, hdr_name = state["body"], state["ct"], state["hdr"]
            self.send_response(200)
            if ct is not None:
                self.send_header(hdr_name, ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def configure(body, content_type, header_name="Content-Type"):
        state.update(body=body, ct=content_type, hdr=header_name)
        return f"http://127.0.0.1:{port}/"

    yield configure
    srv.shutdown()
    srv.server_close()


def fetch(serve, body, content_type, header_name="Content-Type"):
    return tiers.tier_http(serve(body, content_type, header_name), allow_private=True)


# ---- W4: PDF -----------------------------------------------------------------

def test_pdf_with_a_text_layer_is_extracted(serve):
    r = fetch(serve, PDF_TEXT, "application/pdf")
    assert r.ok, r.error
    assert r.extractor == "pypdf"
    assert "Hello phase one PDF text layer" in r.content
    assert "%PDF" not in r.content, "PDF object dictionary leaked into content"


def test_bom_and_whitespace_prefixed_pdf_still_reads(serve):
    """The widened skip. A BOM and leading ASCII whitespace are skipped, so the
    signature is still \"first\" and the document reads normally."""
    assert PDF_BOM_AND_WHITESPACE[:5] != b"%PDF-", "precondition: not at offset 0"
    assert tiers._pdf_signature_at_start(PDF_BOM_AND_WHITESPACE)
    r = fetch(serve, PDF_BOM_AND_WHITESPACE, "application/pdf")
    assert r.ok and r.extractor == "pypdf", r.error
    assert "Hello phase one PDF text layer" in r.content


def test_banner_prefixed_pdf_no_longer_enters_the_pdf_branch(serve):
    """A DELIBERATE CAPABILITY REDUCTION, pinned so it cannot drift back.

    Round 2 entered the PDF branch on `%PDF-` anywhere in the first KiB, which
    is what let an HTML page documenting the format in. F2 replaced that with
    a signature-position rule, and a real PDF behind a proxy banner is the
    cost: it now takes the HTML path and escalates rather than being read here.

    The PDF Reference implementation notes do record that Acrobat accepts a
    header anywhere in the first 1024 bytes, so such files exist. This project
    has never captured one -- this fixture is synthetic -- so the trade is a
    documented-but-unobserved input against a false positive we reproduced.
    If a real one turns up, widen the SKIP in _pdf_signature_at_start.
    """
    assert b"%PDF-" in PDF_BANNER_PREFIX[:1024], "precondition: old rule accepted it"
    assert not tiers._pdf_signature_at_start(PDF_BANNER_PREFIX)
    r = fetch(serve, PDF_BANNER_PREFIX, "application/pdf")
    assert r.extractor != "pypdf", "it must not reach pypdf at all"
    assert not r.ok, "an honest failure that escalates, not a silent success"


def test_pdf_mislabelled_as_html_still_detected(serve):
    """Content-Type may not reject a candidate; the bytes decide."""
    r = fetch(serve, PDF_TEXT, "text/html; charset=utf-8")
    assert r.ok and r.extractor == "pypdf"


def test_html_mislabelled_as_pdf_does_not_go_to_pypdf(serve):
    """The other direction: an application/pdf login interstitial is still HTML."""
    r = fetch(serve, HTML_BODY, "application/pdf")
    assert r.extractor == "trafilatura", r.extractor
    assert "sign in" in r.content.lower()


def test_pdf_without_a_text_layer_gets_a_format_specific_error(serve):
    """Never the generic short-text message -- that floor is what is being replaced."""
    r = fetch(serve, PDF_NO_TEXT, "application/pdf")
    assert not r.ok
    assert "pdf_no_text_layer" in r.error


def test_pdf_failure_cannot_promote_the_domain(serve):
    """A PDF we could not read says nothing about needing a browser: 0 successes
    in 140 browser attempts on PDF URLs."""
    import state
    r = fetch(serve, PDF_NO_TEXT, "application/pdf")
    assert r.failure_class not in state.PERSISTENT_FAILURE_CLASSES



@pytest.fixture()
def no_pypdf(monkeypatch):
    """Make `import pypdf` fail, as it does on a box the bootstrap has not reached."""
    real_import = builtins.__import__

    def blocked(name, *a, **kw):
        if name == "pypdf":
            raise ImportError("No module named 'pypdf'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", blocked)


def test_missing_pypdf_does_not_terminate_the_cascade(serve, no_pypdf):
    """M7: a missing dependency says nothing about the document, and copilot holds
    the only nonzero PDF success rate on record (14 of 20)."""
    r = fetch(serve, PDF_TEXT, "application/pdf")
    assert not r.ok
    assert "pdf_extractor_missing" in r.error
    assert not getattr(r, "terminal", False), "a missing dependency stopped the cascade"




def test_missing_pypdf_cannot_promote_the_domain(serve, no_pypdf):
    """The consequence of getting this wrong is browser windows on the user's
    screen: with pypdf absent fleet-wide, every PDF fetch fails at tier 0, and a
    promotable class there would walk domains up toward `headed`."""
    import state
    r = fetch(serve, PDF_TEXT, "application/pdf")
    assert r.failure_class not in state.PERSISTENT_FAILURE_CLASSES


def test_missing_pypdf_does_not_claim_pypdf_as_the_extractor(serve, no_pypdf):
    """R2-3: the failure envelope named an extractor that is not installed."""
    r = fetch(serve, PDF_TEXT, "application/pdf")
    assert "pdf_extractor_missing" in r.error
    assert r.extractor == "", r.extractor


@pytest.mark.parametrize("body,needle", [
    (PDF_NO_TEXT, "pdf_no_text_layer"),
    (PDF_TEXT[:120], "pdf_unreadable"),
])
def test_pdf_failures_where_pypdf_ran_still_name_it(serve, body, needle):
    """The other direction. pypdf DID run and DID fail on these, so naming it
    is truthful -- blanking the field everywhere would trade one wrong
    envelope for three."""
    r = fetch(serve, body, "application/pdf")
    assert not r.ok and needle in r.error
    assert r.extractor == "pypdf", r.extractor


def test_pdf_extract_timeout_still_names_pypdf(serve, monkeypatch):
    monkeypatch.setattr(
        tiers, "_extract_pdf_text",
        lambda b, **kw: ("", "pdf_extract_timeout: total_timeout exceeded "
                             "after 1 of 9 page(s)"))
    r = fetch(serve, PDF_TEXT, "application/pdf")
    assert r.extractor == "pypdf", r.extractor


def test_missing_pypdf_leaves_non_pdf_urls_untouched(serve, no_pypdf):
    """Nothing about an HTML page depends on pypdf. This is what makes the
    dependency non-fatal rather than merely non-exiting."""
    r = fetch(serve, HTML_BODY, "text/html; charset=utf-8")
    assert r.ok and r.extractor == "trafilatura"
    r2 = fetch(serve, JSON_BODY, "application/json")
    assert r2.ok and r2.extractor == "verbatim"


def test_missing_pypdf_cascade_preserves_document_error_without_synthesis(serve, no_pypdf,
                                                                         tmp_path, monkeypatch):
    """The real PDF response fails honestly without browser or model rescue."""
    import fetch as fetch_mod
    import state

    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    conn = state.open_db()
    try:
        def playwright(url, *, mode, **kw):
            raise AssertionError(f"a browser rung ran on a PDF: {mode}")

        def copilot(url, **kw):
            raise AssertionError("disabled Copilot ran on a PDF")

        monkeypatch.setattr(tiers, "tier_playwright", playwright)
        monkeypatch.setattr(tiers, "tier_copilot", copilot)
        url = serve(PDF_TEXT, "application/pdf")
        env = fetch_mod.cascade_fetch(url, conn=conn, allow_private=True,
                                      no_cache=True, search_path="/stub/search.py")
        assert env["content"] == "" and env["synthesized"] is False
        assert env["error"].startswith("cascade_exhausted:")
        attempts = {a["tier"]: a for a in env["tier_attempts"]}
        assert "pdf_extractor_missing" in attempts["http"]["error"]
    finally:
        conn.close()


# ---- F3: HTML mentioning %PDF- never enters the branch, on ANY path ---------
#
# Round 2 rescued such a page on the way OUT, keyed on pdf_unreadable plus a
# literal <html marker. That left two real cases stranded: with pypdf absent the
# error is pdf_extractor_missing, not pdf_unreadable; and an HTML FRAGMENT
# carries neither <html> nor a doctype. Deciding at the door closes both,
# because neither document ever reaches pypdf.

HTML_FRAGMENT_ABOUT_PDF = (
    b"<article><h2>File signatures</h2>"
    b"<pre>%PDF-1.7\n1 0 obj</pre>"
    b"<p>" + b"The signature identifies the format and the version it claims. " * 6
    + b"</p></article>")


def test_html_fragment_mentioning_pdf_never_enters_the_branch(serve):
    """F3 second case: no <html>, no doctype, so round 2's marker test could not
    rescue it and it stayed pdf_unreadable."""
    assert b"%PDF-" in HTML_FRAGMENT_ABOUT_PDF[:1024], "precondition"
    assert b"<html" not in HTML_FRAGMENT_ABOUT_PDF.lower()
    assert b"<!doctype" not in HTML_FRAGMENT_ABOUT_PDF.lower()
    r = fetch(serve, HTML_FRAGMENT_ABOUT_PDF, "text/html; charset=utf-8")
    assert r.ok, r.error
    assert r.extractor == "trafilatura", r.extractor
    assert "identifies the format" in r.content


def test_html_about_pdf_with_pypdf_absent_still_extracts(serve, no_pypdf):
    """F3 first case. With pypdf missing the error was pdf_extractor_missing, which
    round 2's rescue did not accept, so valid HTML became cascade_exhausted under
    --max-tier http. Now it never reaches pypdf, so its absence is irrelevant."""
    r = fetch(serve, HTML_ABOUT_PDF, "text/html; charset=utf-8")
    assert r.ok, r.error
    assert r.extractor == "trafilatura"
    assert "begins with a header line" in r.content


def test_html_about_pdf_with_pypdf_absent_does_not_exhaust_at_max_tier_http(
        serve, no_pypdf, tmp_path, monkeypatch):
    """The consequence F3 actually named, end to end."""
    import fetch as fetch_mod
    import state

    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    conn = state.open_db()
    try:
        url = serve(HTML_ABOUT_PDF, "text/html; charset=utf-8")
        env = fetch_mod.cascade_fetch(url, conn=conn, allow_private=True,
                                      no_cache=True, max_tier="http",
                                      search_path="/stub/search.py")
        assert not (env.get("error") or "").startswith("cascade_exhausted"), env
        assert env["tier"] == "http" and env["extractor"] == "trafilatura"
    finally:
        conn.close()


def test_encrypted_pdf_with_html_metadata_does_not_reach_the_html_path(
        serve, monkeypatch):
    """F2's case. An encrypted REAL PDF raises inside pypdf, and round 2 read that
    as proof the body was HTML whenever an early <html marker was present -- so a
    real PDF could come back as its own embedded markup."""
    import types

    class _Encrypted:
        def __init__(self, _stream):
            self.is_encrypted = True

        @property
        def pages(self):
            raise AssertionError("must not enumerate pages on an encrypted PDF")

    monkeypatch.setitem(sys.modules, "pypdf",
                        types.SimpleNamespace(PdfReader=_Encrypted))
    body = build_pdf(True, prologue=b"% <html> xmp metadata marker\n")
    assert b"<html" in body[:4096], "precondition: the round-2 rescue would have fired"

    r = fetch(serve, body, "application/pdf")
    assert not r.ok
    assert "pdf_encrypted" in r.error, r.error
    assert r.extractor == "pypdf", "it must keep its PDF identity, not become HTML"


# ---- RV-2: a failed PDF parse must not be terminal for HTML -----------------

def test_html_documenting_the_pdf_header_is_still_extracted_as_html(serve):
    """`<pre>%PDF-1.7</pre>` in the first KiB of a text/html page. The byte
    sniff enters the PDF branch, pypdf rejects the bytes, and the branch used
    to RETURN pdf_unreadable -- losing a perfectly good HTML page."""
    assert b"%PDF-" in HTML_ABOUT_PDF[:1024], "precondition: sniff must fire"
    r = fetch(serve, HTML_ABOUT_PDF, "text/html; charset=utf-8")
    assert r.ok, r.error
    assert r.extractor == "trafilatura", r.extractor
    assert "begins with a header line" in r.content


def test_html_documenting_the_pdf_header_does_not_exhaust_at_max_tier_http(
        serve, tmp_path, monkeypatch):
    """Where the bound leaked. With no tier above http to rescue it, the false
    positive turned valid HTML into cascade_exhausted."""
    import fetch as fetch_mod
    import state

    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    conn = state.open_db()
    try:
        url = serve(HTML_ABOUT_PDF, "text/html; charset=utf-8")
        env = fetch_mod.cascade_fetch(url, conn=conn, allow_private=True,
                                      no_cache=True, max_tier="http",
                                      search_path="/stub/search.py")
        assert not (env.get("error") or "").startswith("cascade_exhausted"), env
        assert env["tier"] == "http" and env["extractor"] == "trafilatura"
        assert "begins with a header line" in env["content"]
    finally:
        conn.close()


def test_a_real_unreadable_pdf_does_not_fall_through(serve):
    """The fall-through is conditioned on HTML evidence, not on the error alone.
    A truncated PDF carries none, so it keeps its format-specific error."""
    truncated = PDF_TEXT[:120]
    r = fetch(serve, truncated, "application/pdf")
    assert not r.ok
    assert "pdf_unreadable" in r.error, r.error
    assert r.extractor == "pypdf"


def test_no_text_layer_pdf_does_not_fall_through_to_the_html_path(serve):
    """pdf_no_text_layer means pypdf ACCEPTED the bytes. Falling through would
    hand the object dictionary to trafilatura -- the W4 defect, restored.

    The fixture carries `<html` in its first 4 KiB ON PURPOSE. Without that the
    test cannot fail: the fall-through is conditioned on error prefix AND HTML
    evidence, so a fixture with no evidence stays out of the HTML path however
    far the error condition is widened, and the test would prove nothing."""
    assert b"<html" in PDF_NO_TEXT_WITH_HTML_MARKER[:4096], "precondition"
    r = fetch(serve, PDF_NO_TEXT_WITH_HTML_MARKER, "application/pdf")
    assert not r.ok
    assert "pdf_no_text_layer" in r.error, r.error
    assert r.extractor == "pypdf", r.extractor
    assert "%PDF" not in r.content


def test_no_text_layer_pdf_without_html_evidence_also_keeps_its_error(serve):
    """The plain case, kept as the regression guard it already was."""
    r = fetch(serve, PDF_NO_TEXT, "application/pdf")
    assert not r.ok and "pdf_no_text_layer" in r.error
    assert "%PDF" not in r.content


def test_pdf_respects_the_total_timeout(serve, monkeypatch):
    """M9: the documented 30 s budget must still hold on a large text-dense PDF."""
    called = []
    monkeypatch.setattr(tiers, "_extract_pdf_text",
                        lambda b, **kw: called.append((b, kw)) or ("x", ""))
    url = serve(PDF_TEXT, "application/pdf")
    r = tiers.tier_http(url, allow_private=True, total_timeout=-1.0)
    assert not r.ok and "total_timeout" in r.error
    assert called == [], "pypdf was invoked after the deadline had passed"


# ---- RV-1: the deadline has to survive INTO extraction ----------------------
#
# The check above only proves extraction is skipped when time is ALREADY gone.
# A download that finishes one millisecond inside the deadline passes it, and
# extraction then ran unbounded -- so --total-timeout bounded the download and
# nothing else. These exercise extraction that STARTS in time and finishes late.

class _TripwirePage:
    """A page that records whether it was read, and can burn the clock."""

    def __init__(self, n, clock, burn=0.0):
        self.n, self._clock, self._burn = n, clock, burn
        self.touched = False

    def extract_text(self):
        self.touched = True
        self._clock[0] += self._burn
        return f"page {self.n} text"


def _fake_pypdf(monkeypatch, pages):
    """Install a pypdf whose PdfReader yields `pages`. No real PDF parsing."""
    import types

    class Reader:
        def __init__(self, _stream):
            self.pages = pages

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=Reader))


def test_pdf_extraction_stops_at_the_deadline_mid_document(monkeypatch):
    """Page 1 burns the whole budget; pages 2 and 3 must never be read."""
    import types

    clock = [1000.0]
    monkeypatch.setattr(tiers, "time",
                        types.SimpleNamespace(monotonic=lambda: clock[0]))
    pages = [_TripwirePage(1, clock, burn=90.0),
             _TripwirePage(2, clock), _TripwirePage(3, clock)]
    _fake_pypdf(monkeypatch, pages)

    deadline = clock[0] + 30.0        # genuinely in the future when we start
    text, err = tiers._extract_pdf_text(b"%PDF-1.4 stub", deadline=deadline)

    assert text == ""
    assert err.startswith("pdf_extract_timeout:"), err
    assert "1 of 3 page(s)" in err, err
    assert pages[0].touched, "precondition: extraction must genuinely begin"
    assert not pages[1].touched and not pages[2].touched, (
        "extraction ran on past the deadline")


def test_pdf_extraction_inside_the_deadline_reads_every_page(monkeypatch):
    """The false-positive direction: a document that fits must not be truncated."""
    import types

    clock = [1000.0]
    monkeypatch.setattr(tiers, "time",
                        types.SimpleNamespace(monotonic=lambda: clock[0]))
    pages = [_TripwirePage(n, clock, burn=1.0) for n in (1, 2, 3)]
    _fake_pypdf(monkeypatch, pages)

    text, err = tiers._extract_pdf_text(b"%PDF-1.4 stub", deadline=clock[0] + 30.0)

    assert err == "" and all(p.touched for p in pages)
    assert "page 3 text" in text


# ---- F1: per-page alone left two holes, and one of them returned SUCCESS ----
#
# The pre-page check never re-runs once the LAST page starts. So a one-page PDF
# got no protection after entry, and the final page of any PDF was equally
# unguarded: the check passed, extraction ran long, and _extract_pdf_text
# returned success on a result that arrived after the budget expired. The
# round-2 three-page tripwire only fires when the loop reaches ANOTHER page,
# which is exactly why it did not catch this.

def _clock_at(monkeypatch, start=1000.0):
    """Freeze tiers' clock at `start`; return the mutable holder."""
    import types
    clock = [start]
    monkeypatch.setattr(tiers, "time",
                        types.SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


def test_one_page_pdf_that_overruns_returns_timeout_not_success(monkeypatch):
    """The hole with no loop iteration left to catch it."""
    clock = _clock_at(monkeypatch)
    pages = [_TripwirePage(1, clock, burn=90.0)]
    _fake_pypdf(monkeypatch, pages)

    text, err = tiers._extract_pdf_text(b"%PDF-1.4 stub", deadline=clock[0] + 30.0)

    assert pages[0].touched, "precondition: the single page must actually be read"
    assert text == "", "returned content produced after the budget expired"
    assert err.startswith("pdf_extract_timeout:"), err
    assert "after the budget" in err, err


def test_final_page_overrun_returns_timeout_not_success(monkeypatch):
    """Same hole, reached by the last page of a multi-page document."""
    clock = _clock_at(monkeypatch)
    pages = [_TripwirePage(1, clock, burn=1.0),
             _TripwirePage(2, clock, burn=1.0),
             _TripwirePage(3, clock, burn=90.0)]     # the final page overruns
    _fake_pypdf(monkeypatch, pages)

    text, err = tiers._extract_pdf_text(b"%PDF-1.4 stub", deadline=clock[0] + 30.0)

    assert all(p.touched for p in pages), "precondition: every page read"
    assert text == ""
    assert err.startswith("pdf_extract_timeout:"), err


def test_extraction_inside_the_budget_still_succeeds(monkeypatch):
    """The false-positive direction: the post-loop check must not fire early."""
    clock = _clock_at(monkeypatch)
    pages = [_TripwirePage(n, clock, burn=1.0) for n in (1, 2, 3)]
    _fake_pypdf(monkeypatch, pages)

    text, err = tiers._extract_pdf_text(b"%PDF-1.4 stub", deadline=clock[0] + 30.0)

    assert err == "" and "page 3 text" in text


def test_a_blocking_page_returns_control_at_the_deadline(monkeypatch):
    """The third enforcement point: a single pypdf call that never comes back.

    MEASURED 2026-08-30: one page of dense text operations extracts in 6.7 s at
    3 MB and 106 s at the 10 MB MAX_HTTP_BYTES ceiling -- roughly O(n^2.3), so a
    single page crosses a 30 s budget around 5.8 MB, inside what this tier will
    download. Checks alone cannot close that; they wait for pypdf either way.

    Real clock and a real thread here, deliberately. A fake clock cannot show
    that control comes back, which is the whole property under test.
    """
    import threading
    import time as real_time

    release = threading.Event()

    class _BlockingPage:
        def extract_text(self):
            release.wait(30)          # never set before the assertion
            return "late"

    _fake_pypdf(monkeypatch, [_BlockingPage()])
    try:
        budget = 0.2
        # MEASURED, n=25 against a 0.2 s budget: overhead max 19 ms, median
        # 18 ms. The allowance is 0.15 s -- roughly 8x the observed worst case,
        # so it is stable rather than flaky, and it caps the tolerance at 1.75x
        # the budget instead of the 3.5x that 0.5 s allowed (and the 25x that
        # the original `elapsed < 5.0` allowed). Re-measure before tightening
        # further; the failure mode of too tight is a flaky suite, which is
        # worse than a slightly loose bound.
        allowance = 0.15
        t0 = real_time.monotonic()
        text, err = tiers._extract_pdf_text(b"%PDF-1.4 stub",
                                            deadline=real_time.monotonic() + budget)
        elapsed = real_time.monotonic() - t0

        assert text == ""
        assert err.startswith("pdf_extract_timeout:"), err
        assert "cannot be cancelled" in err, "the residual must be stated in the error"
        assert elapsed < budget + allowance, (
            f"asked for {budget}s, control came back after {elapsed:.3f}s")
    finally:
        release.set()                 # let the orphaned worker exit


def test_encrypted_pdf_gets_its_own_error_not_pdf_unreadable(monkeypatch):
    """F2: encryption is a different fact from 'malformed', and folding it into
    pdf_unreadable is what let an arbitrary pypdf exception stand as proof that
    the body was HTML."""
    import types

    class _Encrypted:
        def __init__(self, _stream):
            self.is_encrypted = True

        @property
        def pages(self):
            raise AssertionError("pages must not be enumerated on an encrypted PDF")

    monkeypatch.setitem(sys.modules, "pypdf",
                        types.SimpleNamespace(PdfReader=_Encrypted))
    text, err = tiers._extract_pdf_text(b"%PDF-1.4 stub")

    assert text == ""
    assert err.startswith("pdf_encrypted:"), err
    assert "pdf_unreadable" not in err


def test_tier_http_arms_the_page_deadline(serve, monkeypatch):
    """A per-page check no caller arms is dead code. tier_http must pass one."""
    seen = {}

    def stub(raw, *, deadline=None):
        seen["deadline"] = deadline
        return "text", ""

    monkeypatch.setattr(tiers, "_extract_pdf_text", stub)
    r = fetch(serve, PDF_TEXT, "application/pdf")
    assert r.ok
    assert seen["deadline"] is not None, "tier_http did not pass its deadline"


def test_pdf_extract_timeout_cannot_promote_the_domain(serve, monkeypatch):
    """Running out of time says nothing about the domain -- same as the other
    PDF errors, and the reason browser rungs must not be walked up to."""
    import state
    monkeypatch.setattr(
        tiers, "_extract_pdf_text",
        lambda b, **kw: ("", "pdf_extract_timeout: total_timeout exceeded "
                             "after 1 of 9 page(s)"))
    r = fetch(serve, PDF_TEXT, "application/pdf")
    assert not r.ok and "pdf_extract_timeout" in r.error
    assert r.failure_class not in state.PERSISTENT_FAILURE_CLASSES


# ---- Unreadable PDFs skip browser rungs without synthesis rescue ------------
# Historical measurement: browsers 0/140, Copilot 14/20. Copilot is now disabled;
# this does not enable a browser rung that cannot read the document's text.

@pytest.fixture()
def cascade(tmp_path, monkeypatch):
    """cascade_fetch on a temp DB, with headed PERMITTED and cdp unreachable.

    Permitting headed is what makes the skip observable at all: with the machine
    gate off, headed is already absent from the tier list and an assertion that
    it was not attempted would pass for the wrong reason.
    """
    import fetch as fetch_mod
    import safety
    import state

    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    monkeypatch.delenv(tiers.HEADED_TIER_DISABLE_ENV, raising=False)
    monkeypatch.setattr(safety, "cdp_port_reachable", lambda: False)
    conn = state.open_db()

    def run(url, **kw):
        kw.setdefault("allow_private", True)
        kw.setdefault("no_cache", True)
        kw.setdefault("search_path", "/stub/search.py")
        return fetch_mod.cascade_fetch(url, conn=conn, **kw)

    yield run
    conn.close()


@pytest.fixture()
def browser_tripwire(monkeypatch):
    """Records any browser-tier call. No browser is ever launched."""
    seen = []

    def playwright(url, *, mode, **kw):
        seen.append(mode)
        return tiers.TierResult(ok=True, tier=mode, content="from the browser",
                                status=200, bytes_=16)

    monkeypatch.setattr(tiers, "tier_playwright", playwright)
    return seen


def _copilot(monkeypatch):
    def stub(url, **kw):
        raise AssertionError("disabled Copilot ran on a PDF")

    monkeypatch.setattr(tiers, "tier_copilot", stub)


def test_no_text_layer_pdf_skips_browser_rungs_and_disabled_copilot(
        serve, cascade, browser_tripwire, monkeypatch):
    _copilot(monkeypatch)
    env = cascade(serve(PDF_NO_TEXT, "application/pdf"))

    assert env["content"] == "" and env["synthesized"] is False
    assert browser_tripwire == [], f"a browser rung was attempted: {browser_tripwire}"
    attempts = {a["tier"]: a for a in env["tier_attempts"]}
    assert "pdf_no_text_layer" in attempts["http"]["error"]
    for rung in ("headless", "headed"):
        assert "skipped" in attempts[rung], attempts[rung]


def test_no_text_layer_pdf_keeps_its_error_when_everything_fails(
        serve, cascade, browser_tripwire, monkeypatch):
    """The format-specific error survives exhaustion without model fallback."""
    _copilot(monkeypatch)
    env = cascade(serve(PDF_NO_TEXT, "application/pdf"))

    assert "pdf_no_text_layer" in (env.get("error") or ""), env.get("error")
    assert browser_tripwire == []


def test_no_text_layer_pdf_does_not_fire_the_headed_skip_warning(
        serve, cascade, browser_tripwire, monkeypatch):
    """HEADED_SKIP_WARNING tells the reader that setting WEBFETCH_ALLOW_HEADED=1
    would have got them the page. On this path that is false -- and it would only
    ever mislead on machines where headed is disabled, i.e. the user's own."""
    import fetch as fetch_mod

    _copilot(monkeypatch)
    monkeypatch.setattr(tiers, "headed_tier_allowed", lambda: False)
    env = cascade(serve(PDF_NO_TEXT, "application/pdf"))

    assert fetch_mod.HEADED_SKIP_WARNING not in env["warnings"], env["warnings"]


@pytest.mark.parametrize("failed_tier,headed_off,max_tier,expected_skipped", [
    pytest.param("headless", True, "cdp", [], id="headless-headed-opt-out"),
    pytest.param("headless", False, "cdp", ["headed"], id="headless-headed-permitted"),
    pytest.param("headless", False, "headless", [], id="headless-ceiling"),
    pytest.param("http", True, "cdp", ["headless"], id="http-headed-opt-out"),
    pytest.param("http", False, "headed", ["headless", "headed"], id="http-both-permitted"),
    pytest.param("http", False, "http", [], id="http-ceiling"),
    pytest.param("http", False, "headless", ["headless"], id="http-headless-only"),
])
def test_document_stop_reports_only_remaining_permitted_browser_skips(
        cascade, browser_tripwire, tmp_path, monkeypatch,
        failed_tier, headed_off, max_tier, expected_skipped):
    """Executed, opted-out and above-ceiling rungs cannot be document skips."""
    import safety
    import state

    _copilot(monkeypatch)
    if headed_off:
        monkeypatch.setenv(tiers.HEADED_TIER_DISABLE_ENV, "1")
    monkeypatch.setattr(safety, "validate_url_safety", lambda *a, **kw: None)
    http_calls = []
    error = "pdf_no_text_layer: document fixture has no extractable text"

    def document_failure(tier):
        return tiers.TierResult(
            ok=False, tier=tier, status=200, error=error,
            failure_class="other", skip_browser_tiers=True,
            headers={"Content-Type": "application/pdf"})

    def http(url, **kw):
        http_calls.append(url)
        if failed_tier == "http":
            return document_failure("http")
        return tiers.TierResult(ok=False, tier="http", error="no readable HTML",
                                failure_class="empty_content")

    def browser(url, *, mode, **kw):
        browser_tripwire.append(mode)
        assert mode == failed_tier, "a document stop attempted a later browser"
        return document_failure(mode)

    def forbidden(*a, **kw):
        raise AssertionError("a document failure does not authorize CDP")

    monkeypatch.setattr(tiers, "tier_http", http)
    monkeypatch.setattr(tiers, "tier_playwright", browser)
    monkeypatch.setattr(tiers, "tier_cdp", forbidden)
    monkeypatch.setattr(safety, "cdp_port_reachable", forbidden)
    url = "https://example.com/document.pdf"
    env = cascade(url, max_tier=max_tier, no_cache=False)

    expected_executed = ["http"] + (["headless"] if failed_tier == "headless" else [])
    assert http_calls == [url]
    assert browser_tripwire == expected_executed[1:]
    executed = [a for a in env["tier_attempts"] if "skipped" not in a]
    skipped = [a["tier"] for a in env["tier_attempts"] if "skipped" in a]
    assert [a["tier"] for a in executed] == expected_executed
    assert skipped == expected_skipped
    assert set(expected_executed).isdisjoint(skipped)
    assert all(a["ok"] is False for a in executed)
    assert all(a["tier"] not in skipped for a in env["inspection_history"] if a["executed"])
    assert [a["tier"] for a in env["inspection_history"] if a["executed"]] == expected_executed
    assert "pdf_no_text_layer" in executed[-1]["error"]
    assert "pdf_no_text_layer" in env["error"]
    assert env["next_action"] == "stop" and env.get("error_code") is None
    assert env["content"] == "" and env["synthesized"] is False
    assert env["from_cache"] is False
    assert env["status"] == 200 and env["status_is_real"] is True
    guidance = env["next_step"].lower()
    assert "pdf" in guidance and "dom" in guidance
    assert "source" in guidance and "text" in guidance
    assert "extract" in guidance

    # Cache is enabled for this run: the empty result must never become a hit.
    conn = state.open_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fetch_log WHERE ok=1").fetchone()[0] == 0
    finally:
        conn.close()


def test_missing_pypdf_skips_browser_rungs_and_disabled_copilot(
        serve, cascade, browser_tripwire, no_pypdf, monkeypatch):
    """M7 RESTATED, NOT WEAKENED -- and this is the case most likely to occur.

    pypdf is a tier-2, warning-only dependency, so a box can finish bootstrap
    without it. On such a machine EVERY PDF used to escalate into headless and
    headed, forever, for a document no browser could have read.

    The reason is structural rather than statistical: the browser tiers reach
    content only through document.documentElement.outerHTML, which serialises
    the light DOM, and Chrome draws a PDF with a plugin inside an <embed> --
    so the text is never DOM text whatever the reason our own extraction
    failed.

    A missing pypdf remains a tier failure, not a process exit, but the
    disabled synthesis tier is never an active fallback."""
    _copilot(monkeypatch)
    env = cascade(serve(PDF_TEXT, "application/pdf"))

    assert browser_tripwire == [], f"a browser rung was attempted: {browser_tripwire}"
    assert env["content"] == "" and env["synthesized"] is False
    attempts = {a["tier"]: a for a in env["tier_attempts"]}
    assert "pdf_extractor_missing" in attempts["http"]["error"]
    for rung in ("headless", "headed"):
        assert "skipped" in attempts[rung], attempts[rung]


def test_unreadable_pdf_skips_the_browser_rungs(
        serve, cascade, browser_tripwire, monkeypatch):
    """The premise this test used to carry -- "pdf_unreadable means the bytes are
    not a PDF at all" -- is the one F2 refuted, and it was pinning a routing
    decision. The fixture is a TRUNCATED REAL PDF, so a browser has nothing to
    render: structurally a browser tier extracts the PDF viewer's DOM rather
    than the document, and measured, the browser rungs are 0 for 140 on PDF
    URLs. Escalating put a window on the user's screen for nothing."""
    _copilot(monkeypatch)
    env = cascade(serve(PDF_TEXT[:120], "application/pdf"))

    assert browser_tripwire == [], f"a browser rung was attempted: {browser_tripwire}"
    assert env["content"] == "" and env["synthesized"] is False
    attempts = {a["tier"]: a for a in env["tier_attempts"]}
    assert "pdf_unreadable" in attempts["http"]["error"]
    for rung in ("headless", "headed"):
        assert "skipped" in attempts[rung], attempts[rung]


def test_encrypted_pdf_skips_the_browser_rungs(
        serve, cascade, browser_tripwire, monkeypatch):
    """Same fact about the document, different error. A browser cannot decrypt
    it either."""
    import types

    class _Encrypted:
        def __init__(self, _stream):
            self.is_encrypted = True

    monkeypatch.setitem(sys.modules, "pypdf",
                        types.SimpleNamespace(PdfReader=_Encrypted))
    _copilot(monkeypatch)
    env = cascade(serve(PDF_TEXT, "application/pdf"))

    assert browser_tripwire == [], browser_tripwire
    assert env["content"] == "" and env["synthesized"] is False


# ---- finding 1: a textual document may LEAD with the signature ---------------
#
# `%PDF-` at offset 0 satisfies the signature rule, and with the branch terminal
# a text/plain or text/html document that opens with those bytes had no way back
# to the text paths. The existing fragment tests all put markup BEFORE the
# signature, so none of them could reach this.

TEXT_LEADING_SIGNATURE = (
    b"%PDF-1.7 is the header line every PDF file begins with.\n\n"
    + b"This note explains the signature and why format sniffers look for it. " * 5
    + b"\nThe five bytes are followed by a version number.\n")

HTML_LEADING_SIGNATURE = (
    b"%PDF-1.7<html><head><title>File signatures</title></head><body><p>"
    + b"Every PDF begins with a header naming the version it claims. " * 5
    + b"</p></body></html>")


# ORDERING NOTE FOR WHOEVER BREAKS THIS. The Content-Type tiebreaker and the
# browser-skip flag both key on the PDF branch, and the tiebreaker MUST be
# evaluated first. Resolve the skip first and the branch returns before the
# fall-through can fire, so these two tests are what catch a reorder -- their
# messages say so rather than just reporting a wrong extractor.
_ORDERING_HINT = (
    "the Content-Type tiebreaker did not fire. If tiers.py was reordered so the "
    "browser-skip/TierResult return happens BEFORE "
    "`defer_to_declared_text_type` is evaluated, that is the cause: the PDF "
    "branch returns and the fall-through to the text paths is unreachable.")


def test_text_plain_leading_with_the_signature_keeps_the_pdf_error(serve):
    """A DELIBERATE NARROWING, and the trade is stated rather than implied.

    Round 3 rescued this document via the verbatim path. Round 4's review
    showed why that path cannot be trusted here: it applies NO content gate, so
    the same route returns a corrupted real PDF as document content whenever a
    server declares a textual type. A signature test cannot separate the two --
    the branch is entered only when %PDF- is at offset 0, so BOTH documents
    begin with it.

    So the tiebreaker now defers only to a GATED path (HTML). A text/plain
    document that genuinely opens with `%PDF-` fails here instead, honestly,
    and escalates to copilot -- which is the tier that reads PDFs anyway. The
    failure direction is a wasted escalation rather than binary returned as
    prose."""
    assert tiers._pdf_signature_at_start(TEXT_LEADING_SIGNATURE), "precondition"
    r = fetch(serve, TEXT_LEADING_SIGNATURE, "text/plain; charset=utf-8")
    assert not r.ok
    assert "pdf_unreadable" in r.error, r.error
    assert r.extractor == "pypdf"


# ---- round 5: a lying header must not manufacture success --------------------
#
# The fixture the round-4 review named and the round-4 test set lacked: a
# CORRUPTED REAL PDF carrying a textual Content-Type. Round 4 used
# application/pdf for its negative case, so it caught a reorder but not this.

CORRUPTED_REAL_PDF = PDF_TEXT[:400]


@pytest.mark.parametrize("ct", [
    "text/plain; charset=utf-8",
    "text/html; charset=utf-8",
    "application/json",
])
def test_a_corrupted_pdf_with_a_lying_textual_header_is_not_success(serve, ct):
    """Binary must never come back as document content. text/plain and json are
    refused at the tiebreaker (ungated paths); text/html is admitted to the
    HTML path and then rejected by the content gate. Both routes end in a
    failure, which is the only property that matters here."""
    assert tiers._pdf_signature_at_start(CORRUPTED_REAL_PDF), "precondition"
    r = fetch(serve, CORRUPTED_REAL_PDF, ct)
    assert not r.ok, (
        f"a lying {ct!r} header turned a damaged PDF into success; "
        f"content starts: {r.content[:60]!r}")
    assert "%PDF" not in r.content
    assert "endobj" not in r.content


def test_the_html_route_is_gated_not_merely_permitted(serve):
    """text/html IS still admitted to the fall-through -- the round-3 case is
    kept -- and what stops the corrupted PDF there is the content gate, not the
    tiebreaker. Asserted separately so the two mechanisms cannot be confused."""
    r = fetch(serve, CORRUPTED_REAL_PDF, "text/html; charset=utf-8")
    assert not r.ok
    assert r.extractor == "trafilatura", (
        f"got {r.extractor!r}: the tiebreaker should have deferred to the HTML "
        f"path and let the gate refuse it")
    assert "0 chars" in r.error, r.error


def test_text_html_leading_with_the_signature_is_extracted(serve):
    assert tiers._pdf_signature_at_start(HTML_LEADING_SIGNATURE), "precondition"
    r = fetch(serve, HTML_LEADING_SIGNATURE, "text/html; charset=utf-8")
    assert r.ok, f"{r.error} -- {_ORDERING_HINT}"
    assert r.extractor == "trafilatura", f"got {r.extractor!r}; {_ORDERING_HINT}"
    assert "naming the version it claims" in r.content


def test_the_tiebreaker_needs_a_TEXTUAL_declared_type(serve):
    """application/pdf is not textual, so a damaged PDF keeps its own error
    rather than being reinterpreted as text."""
    r = fetch(serve, PDF_TEXT[:120], "application/pdf")
    assert not r.ok
    assert "pdf_unreadable" in r.error, r.error
    assert r.extractor == "pypdf"


def test_the_tiebreaker_does_not_reopen_F2(serve, monkeypatch):
    """THE GUARD THAT MATTERS. F2 was an encrypted real PDF being sent into the
    HTML path. The tiebreaker fires on pdf_unreadable ONLY, and encryption now
    has its own error -- so even served as text/html, an encrypted PDF keeps its
    PDF identity instead of being reinterpreted."""
    import types

    class _Encrypted:
        def __init__(self, _stream):
            self.is_encrypted = True

    monkeypatch.setitem(sys.modules, "pypdf",
                        types.SimpleNamespace(PdfReader=_Encrypted))
    r = fetch(serve, PDF_TEXT, "text/html; charset=utf-8")
    assert not r.ok
    assert "pdf_encrypted" in r.error, r.error
    assert r.extractor == "pypdf", "it must not be reinterpreted as HTML"


# ---- W5: non-HTML textual bodies ---------------------------------------------

@pytest.mark.parametrize("body,ct,needle", [
    (JSON_BODY, "application/json; charset=utf-8", '"login":"octocat"'),
    (JSON_BODY, "application/vnd.github+json", '"public_repos":8'),
    (JS_BODY, "application/javascript", "export function add"),
    (JS_BODY, "text/javascript; charset=utf-8", "return a + b"),
    (TEXT_BODY, "text/plain; charset=utf-8", "1NNN  JP addr"),
    (b"<rss><item>x</item></rss>", "application/xml", "<item>x</item>"),
])
def test_textual_non_html_body_returned_verbatim(serve, body, ct, needle):
    r = fetch(serve, body, ct)
    assert r.ok, r.error
    assert r.extractor == "verbatim"
    assert needle in r.content


def test_html_still_goes_through_trafilatura(serve):
    r = fetch(serve, HTML_BODY, "text/html; charset=utf-8")
    assert r.ok and r.extractor == "trafilatura"
    assert "<html>" not in r.content


def test_xhtml_is_not_treated_as_verbatim_text(serve):
    """application/xhtml+xml is HTML and must keep its extractor."""
    r = fetch(serve, HTML_BODY, "application/xhtml+xml")
    assert r.extractor == "trafilatura"


def test_verbatim_type_read_from_a_lowercase_header(serve):
    r = fetch(serve, JSON_BODY, "application/json", header_name="content-type")
    assert r.ok and r.extractor == "verbatim"


def test_charset_read_from_a_lowercase_header(serve):
    """The other half of the header-case bug. arxiv.org sends `content-type`
    lowercase in a response whose other headers are title-case, so the old
    case-sensitive lookup returned '' -- silently defaulting every such response
    to utf-8 and skipping the Content-Length pre-check."""
    r = fetch(serve, "café au lait".encode("latin-1"),
              "text/plain; charset=iso-8859-1", header_name="content-type")
    assert r.ok, r.error
    assert "café au lait" in r.content, repr(r.content)


def test_empty_json_body_is_an_honest_failure(serve):
    r = fetch(serve, b"   ", "application/json")
    assert not r.ok


# ---- M8: the cache must not mislabel a non-trafilatura success ---------------

@pytest.mark.parametrize("extractor,expect_cached", [
    ("trafilatura", True),
    ("pypdf", False),
    ("verbatim", False),
])
def test_only_replayable_extractors_are_cached(tmp_path, monkeypatch,
                                               extractor, expect_cached):
    """The cache row stores `synthesized`, not `extractor`, and the replay derives
    one from the other -- so a pypdf or verbatim body would come back labelled
    "trafilatura" for the whole 15-minute TTL."""
    import fetch
    import state

    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    conn = state.open_db()
    try:
        def ok_http(url, **kw):
            return tiers.TierResult(ok=True, tier="http", content="body text",
                                    status=200, bytes_=9, extractor=extractor)

        monkeypatch.setattr(tiers, "tier_http", ok_http)
        url = "https://example.com/doc"
        env = fetch.cascade_fetch(url, conn=conn, max_tier="http")
        assert env["extractor"] == extractor

        key = state.compute_cache_key(url, False, "http", False)
        assert (state.Cache(conn).get(key) is not None) is expect_cached
    finally:
        conn.close()
