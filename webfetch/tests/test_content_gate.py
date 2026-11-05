"""Content-quality gate over synthetic walls and readable documents.

Every wall is forced to status 200, so its named content arm must reject it.
Readable controls guard false positives, short pages and embedded widgets.
No captured site HTML or private browsing evidence is distributed.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import extract
import state

from synthetic_pages import PAGES

# Naming the arm prevents a regression from passing on an unrelated rejection.
WALLS = {
    "cloudflare_check": "challenge markup",
    "cloudflare_platform": "challenge markup",
    "cloudflare_form": "challenge markup",
    "cloudflare_inline": "challenge markup",
    "datadome": "challenge markup",             # DataDome
    "title_denied": "challenge page title",
    "title_robot": "challenge page title",
    "perimeterx": "challenge page title",
    # A behavioral challenge is recognized by its container, not script size.
    "akamai": "challenge markup",
}

REAL_PAGES = [
    "article",
    "short_document",
    "paragraphs",
    "reference",
    "plain_article",
]


def _extract(name):
    html = PAGES[name]
    return extract.html_to_markdown(html, "https://example.com/"), html


@pytest.mark.parametrize("name,arm", sorted(WALLS.items()))
def test_synthetic_walls_rejected_on_content_alone(name, arm):
    """Status forced to 200 so the content predicate has to do the work."""
    md, html = _extract(name)
    ok, reason = extract.is_real_content(md, html, 200)
    assert not ok, f"{name} was admitted"
    assert arm in reason, f"{name} rejected by the wrong arm: {reason}"


@pytest.mark.parametrize("name", REAL_PAGES)
def test_synthetic_readable_pages_admitted(name):
    md, html = _extract(name)
    ok, reason = extract.is_real_content(md, html, 200)
    assert ok, f"{name} rejected: {reason}"


def test_example_com_body_admitted():
    """113 chars, ratio 0.20, no scripts. The old 200-char floor rejected it."""
    md, html = _extract("short_document")
    assert len((md or "").strip()) < extract.SHELL_TEXT_MAX
    assert extract.is_real_content(md, html, 200) == (True, "")


# ---- M1: the title arm must not be an unbounded substring match --------------

_PROSE = ("The archive reader truncated members whose names exceeded one hundred bytes. "
          "This note explains the cause, the fix and the migration path. " * 12)

# Real titles that contain a challenge phrase. Every one is rejected by an
# unbounded `phrase in title` match, which is what the reference implementation
# did; "Access Denied - Stack Overflow" survives boundary-anchoring alone, so the
# DEMAND_TEXT_MAX conjunction is required as well.
REAL_TITLES = [
    "Troubleshoot access denied error messages - AWS IAM",
    "Security Checklist | Node.js",
    "Kubernetes Security Checklist",
    "Access Denied - Stack Overflow",
    "Are You a Robot? Testing CAPTCHA at scale",
    "Docker security check list",
    "Pardon our interruption while we fix a typo",
    "Bot verification systems compared",
    "You have been blocked: what Cloudflare error 1020 means",
    "Just a Moment - Wikipedia",
]


@pytest.mark.parametrize("title", REAL_TITLES)
def test_real_titles_carrying_prose_are_admitted(title):
    html = f"<html><head><title>{title}</title></head><body><p>{_PROSE}</p></body></html>"
    md = extract.html_to_markdown(html, "https://example.com/")
    ok, reason = extract.is_real_content(md, html, 200)
    assert ok, f"{title!r} rejected: {reason}"


@pytest.mark.parametrize("title", ["Just a moment...", "Just a moment…",
                                   "Attention Required! | Cloudflare"])
def test_challenge_titles_on_short_bodies_still_rejected(title):
    """The anchoring must not go so far as to miss the walls it exists for."""
    html = f"<html><head><title>{title}</title></head><body><p>please wait</p></body></html>"
    md = extract.html_to_markdown(html, "https://example.com/")
    ok, reason = extract.is_real_content(md, html, 200)
    assert not ok and "challenge page title" in reason


def test_short_page_with_a_challenge_phrase_in_its_title_is_admitted():
    """Isolates the ANCHORING half of the M1 fix. The body is under
    DEMAND_TEXT_MAX, so the length conjunction cannot rescue this one -- only the
    boundary rule does. An unbounded `phrase in title` match rejects it."""
    body = ("The accepted answer sets the bucket policy explicitly; the implicit "
            "grant does not survive a cross-account copy, which is why the object "
            "reads back as forbidden for the second principal.")
    html = ('<html><head><title>Access Denied - Stack Overflow</title></head>'
            f'<body><p>{body}</p></body></html>')
    md = extract.html_to_markdown(html, "https://example.com/")
    assert len((md or "").strip()) <= extract.DEMAND_TEXT_MAX, "precondition"
    assert extract.is_real_content(md, html, 200) == (True, "")


def test_challenge_title_with_long_body_is_admitted():
    """A page whose title IS the phrase but which served real content is a page.
    Both halves of the M1 fix are required; this is the half anchoring cannot do."""
    html = f"<html><head><title>Access Denied</title></head><body><p>{_PROSE}</p></body></html>"
    md = extract.html_to_markdown(html, "https://example.com/")
    assert extract.is_real_content(md, html, 200) == (True, "")


# ---- F4: the demand arm is gated on LENGTH, and position is gone -------------
#
# Two premises died here, each to a finding:
#   "an article is longer"  -> RV-4: a 323-char support note quoting the phrase
#                              was rejected as a wall.
#   "a wall LEADS with it"  -> F4: 200 chars of filler ahead of the phrase let a
#                              real wall through, and any position rule has an
#                              evasion of that same shape.
#
# So the arm is consulted only when the WHOLE extracted text is at or under
# SHELL_TEXT_MAX -- one threshold, already in the file, used by the JS-shell arm.
# MEASURED BOUNDARY, 2026-08-30: rejection holds to exactly 200 extracted chars
# and stops at 201, whatever the offset. The captured walls run 41-43.
#
# NOT A STRICT IMPROVEMENT IN COVERAGE, and worth saying: text of 201-600 chars
# with an early demand was caught by the old lead bound and is not caught now.
# That region contains no captured wall -- over the 24 captures this arm fires
# on NONE of them by itself; every rejection comes from markup, title, zero
# chars or script share. The trade is coverage this arm never demonstrably had,
# for the removal of a rule shape that produced a finding in both directions.

SUPPORT_NOTE_QUOTING_A_DEMAND = (
    '<html><head><title>Why the verification dialog appears</title></head><body>'
    '<article><p>Some visitors reach an interstitial before the checkout page '
    'loads. It appears when our edge cannot match the session cookie to a recent '
    'request, and it clears itself after a single reload in almost every case. '
    'The dialog asks you to verify you are human; answer it once and the session '
    'is restored for the rest of the day.</p></article></body></html>'
)


def test_short_support_note_quoting_a_demand_phrase_is_admitted():
    """RV-4's note, still admitted under the new rule -- now because its 323
    extracted chars exceed SHELL_TEXT_MAX, not because of where the phrase sits."""
    md = extract.html_to_markdown(SUPPORT_NOTE_QUOTING_A_DEMAND, "https://example.com/")
    text = (md or "").strip()
    assert len(text) > extract.SHELL_TEXT_MAX, (
        f"precondition: {len(text)} chars must exceed SHELL_TEXT_MAX "
        f"({extract.SHELL_TEXT_MAX}), or this test proves nothing")
    assert "verify you are human" in text.lower(), "precondition: it quotes a demand"
    ok, reason = extract.is_real_content(md, SUPPORT_NOTE_QUOTING_A_DEMAND, 200)
    assert ok, reason


LEADING_DEMAND_WALL = (
    '<html><head><title>Checkout</title></head><body><p>'
    'Complete the security check to continue to your basket. '
    'This usually takes a few seconds.</p></body></html>'
)


def test_a_wall_whose_text_is_its_demand_is_rejected():
    """The direction the rule exists for, isolated: benign title, benign markup,
    so the demand arm is the only one that can catch this."""
    md = extract.html_to_markdown(LEADING_DEMAND_WALL, "https://example.com/")
    text = (md or "").strip()
    assert len(text) <= extract.SHELL_TEXT_MAX, "precondition"
    ok, reason = extract.is_real_content(md, LEADING_DEMAND_WALL, 200)
    assert not ok
    assert "client-demand phrase" in reason, f"caught by the wrong arm: {reason}"


# ---- the boundary, and the residual, both pinned ----------------------------

def _padded_wall(filler_chars: int) -> str:
    """A wall with benign title and markup, `filler_chars` of filler, then the
    demand. Only the demand arm can catch it."""
    filler = ("We are checking your connection. " * 40)[:filler_chars]
    return ("<html><head><title>Checkout</title></head><body><p>"
            + filler + "Complete the security check to continue.</p></body></html>")


@pytest.mark.parametrize("filler,extracted_len,expect_admitted", [
    pytest.param(0, 40, False, id="no-filler-the-captured-wall-shape"),
    pytest.param(140, 180, False, id="demand-at-140-still-rejected"),
    pytest.param(158, 198, False, id="just-inside-the-bound"),
    pytest.param(160, 200, False, id="exactly-at-SHELL_TEXT_MAX"),
    pytest.param(161, 201, True, id="one-char-over-ADMITTED-the-residual"),
    pytest.param(199, 239, True, id="demand-at-199-admitted"),
    pytest.param(200, 240, True, id="demand-at-200-admitted"),
    pytest.param(400, 440, True, id="demand-far-in-admitted"),
])
def test_the_demand_arm_boundary_is_total_length_not_offset(
        filler, extracted_len, expect_admitted):
    """The spec asked for offsets 199, 200 and beyond. The honest answer is that
    the new rule does not key on offset AT ALL -- the boundary is the total
    extracted length, at exactly SHELL_TEXT_MAX. A demand at offset 199 implies
    239 chars of text, so it is admitted. That is the residual, pinned rather
    than described, so it cannot drift without a test saying so."""
    html = _padded_wall(filler)
    md = extract.html_to_markdown(html, "https://example.com/")
    text = (md or "").strip()
    assert len(text) == extracted_len, (
        f"fixture drift: expected {extracted_len} extracted chars, got {len(text)}")
    ok, reason = extract.is_real_content(md, html, 200)
    assert ok is expect_admitted, reason or "admitted"


def test_the_padded_wall_residual_is_caught_by_markup_when_it_is_real():
    """Why the residual is tolerable rather than merely acknowledged. A real
    padded wall carries challenge markup or a challenge title -- which is what
    catches every wall in the capture set. Same padded body, plus the markup a
    real Cloudflare interstitial serves.

    The marker used to be `class="cf-turnstile"`, which stopped being evidence on
    a 2xx when that class moved to _EMBEDDABLE_CHALLENGE_MARKUP (a site mounts
    one on its own contact form). `cf-browser-verification` carries the same
    point and is still interstitial-only."""
    padded = _padded_wall(400)
    assert extract.is_real_content(
        extract.html_to_markdown(padded, "https://example.com/"), padded, 200)[0], (
        "precondition: the demand arm alone admits this")
    real = padded.replace("<body>", '<body><div class="cf-browser-verification"></div>')
    ok, reason = extract.is_real_content(
        extract.html_to_markdown(real, "https://example.com/"), real, 200)
    assert not ok and "challenge markup" in reason, reason


# ---- M4: the external-bundle shell -------------------------------------------

# The Create React App default index.html. Its script share is 0.166 -- the 0.90
# arm cannot see it -- and its <noscript> keeps it out of the zero-char arm.
CRA_SHELL = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8"/>'
    '<meta name="viewport" content="width=device-width,initial-scale=1"/>'
    '<title>React App</title>'
    '<script defer="defer" src="/static/js/main.8f7c2b1a.js"></script>'
    '<link href="/static/css/main.073c9b0a.css" rel="stylesheet"></head>'
    '<body><noscript>You need to enable JavaScript to run this app.</noscript>'
    '<div id="root"></div></body></html>'
)


def test_external_bundle_shell_rejected():
    md = extract.html_to_markdown(CRA_SHELL, "https://example.com/")
    assert extract._script_byte_share(CRA_SHELL) < extract.SHELL_SCRIPT_SHARE, (
        "precondition: the script-share arm must NOT be what catches this")
    ok, reason = extract.is_real_content(md, CRA_SHELL, 200)
    assert not ok and "demands JS" in reason


def test_short_real_page_with_a_noscript_hint_is_admitted():
    """The false-positive direction of the arm above. trafilatura falls back to
    <noscript> only when there is nothing else, so a real page keeps its content."""
    html = ('<html><head><title>Release notes 2.4.1</title></head><body>'
            '<noscript>Please enable JavaScript to use this site.</noscript>'
            '<article><p>Version 2.4.1 fixes a regression in the archive reader and '
            'restores the documented exit code on a checksum mismatch.</p></article>'
            '</body></html>')
    md = extract.html_to_markdown(html, "https://example.com/")
    assert 0 < len((md or "").strip()) < extract.SHELL_TEXT_MAX
    assert extract.is_real_content(md, html, 200) == (True, "")


def test_inline_blob_shell_rejected():
    """The 0.90 script-share arm, which was dead code until it was conjoined with
    thin text (0 firings in 3,928 failed rows)."""
    shell = ('<html><head><title>App</title></head><body><div id=root>Loading</div>'
             '<script>' + ('var x=1;' * 40000) + '</script></body></html>')
    md = extract.html_to_markdown(shell, "https://example.com/")
    ok, reason = extract.is_real_content(md, shell, 200)
    assert not ok and "script byte share" in reason


# ---- the deleted gates must stay deleted -------------------------------------

def test_low_ratio_short_real_page_admitted():
    """287 real chars inside 85 KB. The removed ratio gate rejected exactly this
    shape -- and its live victims extracted 209-975 chars from github, wikipedia
    and docs.claude.com."""
    prose = ("The archive reader truncated members whose names exceeded one hundred "
             "bytes and the checksum path returned the wrong exit code. ") * 2
    html = ('<html><head><title>Engineering blog</title><style>'
            + 'div.a{color:#000}' * 5000
            + '</style></head><body><article><p>' + prose + '</p></article></body></html>')
    md = extract.html_to_markdown(html, "https://example.com/")
    text = (md or "").strip()
    assert len(text) / len(html) < 0.01 and len(text) < 1000, "precondition"
    assert extract.is_real_content(md, html, 200) == (True, "")


def test_link_only_index_page_admitted():
    """The prose-paragraph gate rejected 43 pages, dominated by github index
    pages. An index page is a legitimate answer to a fetch."""
    links = "".join(f'<li><a href="/p/{i}">package-{i}</a></li>' for i in range(60))
    html = f"<html><head><title>Index</title></head><body><ul>{links}</ul></body></html>"
    md = extract.html_to_markdown(html, "https://example.com/")
    assert extract.is_real_content(md, html, 200) == (True, "")


# ---- the embeddable markers are not evidence on a page the origin served ----
#
# MEASURED 2026-08-30: blog.crawlex.net/blog/cloudflare-turnstile-internals
# returns HTTP 200 with 31,947 characters of extracted article text, and was
# rejected here for `challenge markup present: 'class="cf-turnstile"'`. That
# class is Cloudflare's documented mount point for a widget a SITE embeds on its
# own form. Every page carrying one -- a contact form on an ordinary article --
# was rejected and escalated toward a browser window (C-3/C-4).

_TURNSTILE_CONTACT_FORM = (
    '<form action="/contact" method="post">'
    '<label>Message</label><textarea name="msg"></textarea>'
    '<div class="cf-turnstile" data-sitekey="0x4AAAAAAADWbCSNeh3IIMw3"></div>'
    '<button type="submit">Send</button></form>'
)


def test_a_200_article_carrying_an_embedded_turnstile_widget_is_admitted():
    """The crawlex case, reduced to its shape: real article text plus the widget
    the site mounts on its own contact form."""
    html = ('<html><head><title>Cloudflare Turnstile internals</title></head><body>'
            f'<article><p>{_PROSE}</p></article>{_TURNSTILE_CONTACT_FORM}'
            '</body></html>')
    md = extract.html_to_markdown(html, "https://example.com/")
    assert len((md or "").strip()) > extract.SHELL_TEXT_MAX, "precondition: real text"
    ok, reason = extract.is_real_content(md, html, 200)
    assert ok, reason


@pytest.mark.parametrize("marker", [
    '<div class="cf-browser-verification"></div>',
    "<script>a.src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1';</script>",
    '<script src="https://geo.captcha-delivery.com/captcha/?initialCid=x"></script>',
])
def test_the_interstitial_only_markers_still_reject_on_a_200(marker):
    """The other direction of the same split -- and the second assertion is what
    makes it that rather than a restatement. Rejection alone held before the
    split too, so on its own it proves nothing about the split; the SAME document
    with the embeddable mount in place of the marker must be admitted. These
    three appear only on a document served INSTEAD of the page, so a 2xx does not
    excuse them: five of the nine captured walls are caught by this arm and were
    served at 403, which is why the status is forced to 200 here."""
    def doc(m):
        return ('<html><head><title>Loading</title></head><body>'
                f'<article><p>{_PROSE}</p></article>{m}</body></html>')

    html = doc(marker)
    md = extract.html_to_markdown(html, "https://example.com/")
    ok, reason = extract.is_real_content(md, html, 200)
    assert not ok and "challenge markup" in reason, reason or "admitted"
    embeddable = doc(_TURNSTILE_CONTACT_FORM)
    md2 = extract.html_to_markdown(embeddable, "https://example.com/")
    ok2, reason2 = extract.is_real_content(md2, embeddable, 200)
    assert ok2, f"the tuple split is gone -- both markers reject: {reason2}"


def test_the_openreview_interstitial_is_rejected_by_the_title_arm():
    """The title boundary requires a widget mount and bounded document text.

    Text above SHELL_TEXT_MAX prevents the demand arm from answering instead.
    A widget alone on status 200 is not sufficient evidence of a wall.
    """
    md, html = _extract("turnstile_title")
    assert 'class="cf-turnstile"' in html, "precondition: the mount is present"
    assert not any(m in html for m in extract._CHALLENGE_MARKUP), (
        "precondition: no interstitial-only marker, so the markup arm cannot be "
        "what catches this")
    assert len((md or "").strip()) > extract.SHELL_TEXT_MAX, (
        "precondition: over SHELL_TEXT_MAX, so the demand arm is never consulted")
    ok, reason = extract.is_real_content(md, html, 200)
    assert not ok, "admitted -- the gated whitespace boundary is gone"
    assert "challenge page title" in reason, f"caught by the wrong arm: {reason}"


def test_a_long_article_with_a_turnstile_mount_is_still_admitted():
    """The companion, and the reason the whitespace boundary is gated on the
    mount instead of being added to _TITLE_BOUNDARY outright. The crawlex shape:
    the same mount, an ordinary title, and prose far over DEMAND_TEXT_MAX -- so
    the title arm is never consulted and the widened boundary cannot reach it.
    The existing cap does that work and nothing was added for it.

    What this one detects is the split being reverted -- put `class="cf-turnstile"`
    back in _CHALLENGE_MARKUP and it dies. The OTHER wrong repair, widening
    _TITLE_BOUNDARY globally, is caught above by
    `test_short_page_with_a_challenge_phrase_in_its_title_is_admitted`, which
    "Access Denied - Stack Overflow" fails under it."""
    html = ('<html><head><title>Cloudflare Turnstile internals: the challenge '
            'token, siteverify, and what it measures</title></head><body>'
            f'<article><p>{_PROSE}</p></article>{_TURNSTILE_CONTACT_FORM}'
            '</body></html>')
    md = extract.html_to_markdown(html, "https://example.com/")
    assert len((md or "").strip()) > extract.DEMAND_TEXT_MAX, (
        "precondition: over the cap, so the title arm is never consulted")
    ok, reason = extract.is_real_content(md, html, 200)
    assert ok, reason


def test_a_fabricated_status_buys_no_marker_exemption():
    """`status_is_real=False`, and why the parameter exists. Two tier call sites
    MANUFACTURE the 200 they pass here -- tier_cdp hard-codes it and
    tier_playwright falls back to it when navigation capture is unavailable --
    and under the split a 200 silently asserts "the origin served this AS the
    page", which is what grants the embeddable-marker exemption. A status the
    caller invented is evidence of nothing, so it grants nothing and both tuples
    are scanned again."""
    html = ('<html><head><title>Notes on edge verification</title></head><body>'
            f'<article><p>{_PROSE}</p></article>{_TURNSTILE_CONTACT_FORM}'
            '</body></html>')
    md = extract.html_to_markdown(html, "https://example.com/")
    assert extract.is_real_content(md, html, 200) == (True, ""), (
        "precondition: a status read off the response admits this")
    ok, reason = extract.is_real_content(md, html, 200, status_is_real=False)
    assert not ok and "cf-turnstile" in reason, reason or "admitted"


# ---- the near-empty document must error rather than come back empty ---------
#
# MEASURED PASSES that should have been failures, 2026-08-30:
# docs.openclaw.ai/architecture at 11 markdown chars, old.reddit.com at 20,
# quotes.toscrape.com/js/ at 29. All three load their content with JavaScript,
# and returning them as a success hands the agent nothing while telling it
# nothing is wrong -- the failure mode this project calls the worst available.
#
# MIN_DOCUMENT_TEXT is NOT the 200-char floor phase 1 removed. That floor was
# removed for cause (209-975 real chars rejected from github, wikipedia and
# docs.claude.com) and 50 sits 4x below the smallest of those, so it cannot
# reproduce a single one of the rejections that killed it.


@pytest.mark.parametrize("text,origin", [
    pytest.param("Architecture", "docs.openclaw.ai/architecture (11 measured)", id="11-chars"),
    pytest.param("r/programming search", "old.reddit.com (20 measured)", id="20-chars"),
    pytest.param("Quotes to Scrape Login", "quotes.toscrape.com/js/ (29 measured)", id="29-ish"),
])
def test_near_empty_extractions_are_rejected(text, origin):
    html = f"<html><head><title>Docs</title></head><body><p>{text}</p></body></html>"
    ok, reason = extract.is_real_content(text, html, 200)
    assert not ok, f"{origin} admitted"
    assert "near-empty document" in reason, f"caught by the wrong arm: {reason}"
    assert str(len(text)) in reason and str(extract.MIN_DOCUMENT_TEXT) in reason, (
        f"the reason must name the count and the threshold: {reason}")


def test_the_floor_sits_below_example_coms_113_chars():
    """The canonical page in both directions. 113 chars of real content, no
    scripts -- it is what the removed 200-char floor rejected, and it must stay
    admitted with the new one in place."""
    md, html = _extract("short_document")
    assert len((md or "").strip()) == 113, "fixture drift"
    assert extract.MIN_DOCUMENT_TEXT < 113, (
        f"MIN_DOCUMENT_TEXT ({extract.MIN_DOCUMENT_TEXT}) has grown past the "
        "smallest real page we hold")
    assert extract.is_real_content(md, html, 200) == (True, "")


@pytest.mark.parametrize("length,expect_admitted", [
    pytest.param(29, False, id="largest-measured-false-positive"),
    pytest.param(49, False, id="one-under-the-floor"),
    pytest.param(50, True, id="exactly-MIN_DOCUMENT_TEXT"),
])
def test_the_floor_is_a_strict_less_than(length, expect_admitted):
    """Pinned so the bound cannot drift by a character without a test saying so.
    The three params do not carry equal weight, and saying so is cheaper than
    letting a reader assume they do: 29 and 49 detect the floor itself and die
    when MIN_DOCUMENT_TEXT is removed, while 50 passes with or without a floor at
    all -- it is the BOUNDARY guard, and it dies only when the comparison drifts
    from `<` to `<=`."""
    text = ("release notes for the archive reader and the checksum path. " * 2)[:length]
    html = f"<html><head><title>Notes</title></head><body><p>{text}</p></body></html>"
    ok, _ = extract.is_real_content(text, html, 200)
    assert ok is expect_admitted


@pytest.mark.parametrize("builder,extracted_len", [
    pytest.param(lambda: CRA_SHELL, 46, id="external-bundle-shell-demands-JS"),
    pytest.param(lambda: ('<html><head><title>App</title></head><body>'
                          '<div id=root>Loading</div><script>'
                          + ('var x=1;' * 40000) + '</script></body></html>'),
                 7, id="inline-blob-shell-script-share"),
])
def test_a_short_shell_keeps_the_shell_reason_not_the_floor(builder, extracted_len):
    """ORDERING, asserted rather than assumed. Both shells extract UNDER
    MIN_DOCUMENT_TEXT, so a floor placed ahead of the two shell arms would answer
    first and replace a diagnosis ("this document never rendered") with a
    measurement ("it is short"). The floor is last for exactly this reason."""
    html = builder()
    md = extract.html_to_markdown(html, "https://example.com/")
    assert len((md or "").strip()) == extracted_len < extract.MIN_DOCUMENT_TEXT, (
        "precondition: the floor must be able to reach this document")
    ok, reason = extract.is_real_content(md, html, 200)
    assert not ok
    assert "unrendered script shell" in reason, f"the floor answered first: {reason}"


def test_zero_chars_reported_as_zero_not_as_short():
    ok, reason = extract.is_real_content("", "<html><body><img src=x></body></html>", 200)
    assert not ok and "0 chars" in reason and "too short" not in reason


def test_non_2xx_still_rejected():
    ok, reason = extract.is_real_content("x" * 900, "<html>x</html>", 403)
    assert not ok and "non-2xx" in reason


# ---- the cap must be measured on the SAME quantity in both judgements --------
# Cross-family review, 2026-08-30. `is_real_content` hands _challenge_reason the
# EXTRACTED text; `classify_failure` hands it script-stripped HTML. The title
# boundary is gated on that length, so a short wall wrapped in a lot of markup
# was rejected by the gate and classified transient `other` by the classifier --
# the domain never learned it was walled and re-walked the cascade every fetch.

def _short_wall_with_bulky_markup():
    """279 extracted chars, 1642 script-stripped chars -- the measured shape.

    The padding is markup carrying NO text: it survives _strip_script_and_style
    (which removes only script/style) while contributing nothing to extraction.
    That is what drives the two lengths apart, and it is why an earlier fixture
    of mine failed to reproduce -- trafilatura extracted the padding too.
    """
    prose = "Verification required to continue. " * 8
    pad = "".join('<div data-k="%s"></div>' % ("v" * 40) for _ in range(20))
    return ("<html><head><title>Verifying your browser | Example</title></head>"
            '<body><div class="cf-turnstile" data-sitekey="k"></div>'
            "<p>" + prose + "</p>" + pad + "</body></html>")


def test_gate_and_classifier_agree_on_a_short_wall_wrapped_in_markup():
    html = _short_wall_with_bulky_markup()
    md = extract.html_to_markdown(html, "https://example.com") or ""
    stripped = extract._strip_script_and_style(html)
    # preconditions: the two lengths must straddle the cap, or nothing is proved
    assert len(md.strip()) <= extract.DEMAND_TEXT_MAX
    assert len(stripped.strip()) > extract.DEMAND_TEXT_MAX

    ok, why = extract.is_real_content(md, html, 200)
    assert not ok and "title" in why
    assert extract.classify_failure(200, html, extracted=md) == "cloudflare_challenge", (
        "the gate calls this a challenge; the classifier must agree. Disagreeing "
        "records a real wall as transient, so the blocklist never learns it")


def test_the_long_article_guard_survives_the_cap_change():
    """The round-3 guard must NOT regress: a long real article stays transient.

    Same title shape and the same embedded Turnstile mount as the wall above --
    only the amount of real prose differs. If this flips to cloudflare_challenge
    the cap has stopped protecting articles and a PERSISTENT class now walks
    ordinary domains toward the window-opening tiers.
    """
    art = "Access denied is an HTTP status that means the origin refused you. " * 30
    html = ("<html><head><title>Access denied and what it means for your API "
            'integration</title></head><body><div class="cf-turnstile"></div>'
            "<p>" + art + "</p></body></html>")
    md = extract.html_to_markdown(html, "https://example.com") or ""
    assert len(md.strip()) > extract.DEMAND_TEXT_MAX      # precondition
    assert extract.classify_failure(200, html, extracted=md) == "other"


def test_omitting_extracted_falls_back_and_does_not_crash():
    """`extracted` is optional: the exception call sites pass body='' and no text."""
    assert extract.classify_failure(None, "", "TimeoutError") == "timeout"
    assert extract.classify_failure(500, "<html></html>") == "origin_5xx_transient"


# ---- The Akamai challenge, and the candidate that MEASURED a false positive ---
#
# `/.well-known/sbsd/` is the script path the behavioural challenge loads, and it
# is the obvious marker to reach for. MEASURED over 33 bodies on 2026-09-05 it
# fires on the challenge AND on two ordinary hilton "Page Not Found" pages, so
# Akamai injects that script into pages it is merely fronting -- the same shape
# as Cloudflare's `/cdn-cgi/challenge-platform/` injection documented beside
# _CHALLENGE_MARKUP. This control is what stops it being added later.

_SBSD_SCRIPT = ('<script type="text/javascript" '
                'src="/.well-known/sbsd/00c722?v=1f330d3b&amp;t=786212751"></script>')


def test_a_page_merely_fronted_by_akamai_is_still_admitted():
    """The SBSD script rides on ORDINARY pages. It must never be evidence."""
    html = (f"<html><head><title>Hotels in Honolulu</title></head><body>"
            f"{_SBSD_SCRIPT}<p>{_PROSE}</p></body></html>")
    md = extract.html_to_markdown(html, "https://example.com/")
    ok, reason = extract.is_real_content(md, html, 200)
    assert ok, f"an ordinary Akamai-fronted page was rejected: {reason}"


def test_akamai_challenge_container_is_evidence_on_a_served_200():
    """The challenge arrives AS the page, at 200, so it must be caught even when
    `page_served` withholds the embeddable-marker tuple."""
    html = (FIXTURES / "akamai").read_text(
        encoding="utf-8", errors="replace")
    md = extract.html_to_markdown(html, "https://www.hilton.com/en/")
    ok, reason = extract.is_real_content(md, html, 200, status_is_real=True)
    assert not ok, "the Akamai behavioural challenge was admitted as content"
    assert "challenge markup" in reason, f"caught by the wrong arm: {reason}"
    # AND NOT BY THE LENGTH FLOOR. Before this change the only thing standing
    # between 2.5 KB of challenge HTML and the caller was `near-empty document`,
    # which a challenge padded past 50 chars walks straight through. Naming the
    # arm is what makes this test about the marker rather than about the size.
    assert "near-empty" not in reason, (
        "still resting on the text floor, not the marker: %s" % reason)


def test_a_data_id_attribute_is_not_challenge_markup():
    """ROUND-1 REVIEW, must-fix high, and the expensive direction.

    `data-id="sec-if-cpt-container"` CONTAINS `id="sec-if-cpt-container"`, and
    `_challenge_reason` matches by plain substring over the raw document -- so
    before the leading space went into the marker this ordinary page was rejected
    as a challenge, and a rejected real page walks its domain toward the browser
    tiers. Reproduced failing before the fix.
    """
    html = (f'<html><head><title>Hotels in Honolulu</title></head><body>'
            f'<div data-id="sec-if-cpt-container">{_PROSE}</div></body></html>')
    md = extract.html_to_markdown(html, "https://example.com/")
    ok, reason = extract.is_real_content(md, html, 200)
    assert ok, f"a data-id attribute was read as challenge markup: {reason}"


def test_the_real_challenge_still_matches_with_the_leading_space():
    """The narrowing must not cost the true positive.

    Asserting the whitespace is present in the fixture FIRST is the point: if a
    future capture had no space before the id, the marker would silently stop
    matching real challenges and every other assertion here would still pass.
    """
    html = (FIXTURES / "akamai").read_text(
        encoding="utf-8", errors="replace")
    assert ' id="sec-if-cpt-container"' in html, (
        "the fixture has no whitespace before the id -- the leading-space "
        "narrowing would silently stop matching real challenges")
    md = extract.html_to_markdown(html, "https://www.hilton.com/en/")
    ok, reason = extract.is_real_content(md, html, 200, status_is_real=True)
    assert not ok and "challenge markup" in reason, reason


def test_a_challenge_served_with_a_newline_before_the_id_is_caught():
    """ROUND-2 REVIEW, finding 1, false-negative direction.

    `<div\nid="sec-if-cpt-container">` is valid HTML for the same attribute, and
    the marker used to require a literal space -- so this challenge matched
    nothing and would have been returned AS the page. Tab arm included because
    the two fail and pass together and a single arm would not show which.
    """
    for ws, label in ((chr(10), "newline"), (chr(9), "tab")):
        html = ("<html><head></head><body><div" + ws
                + 'id="sec-if-cpt-container" role="main"></div></body></html>')
        md = extract.html_to_markdown(html, "https://example.com/")
        ok, reason = extract.is_real_content(md, html, 200, status_is_real=True)
        assert not ok, f"a challenge separated by a {label} was admitted"
        assert "challenge markup" in reason, f"{label}: wrong arm: {reason}"


def test_a_page_quoting_the_challenge_ids_is_still_rejected_known_residual():
    """ROUND-2 REVIEW, finding 2, must-fix, DOCUMENTED RATHER THAN FIXED.

    This test asserts the CURRENT behaviour, and it is not an endorsement of it.
    A substring scan of the raw document cannot tell markup the origin served
    from markup a page quotes, so an article about Akamai bot detection is
    rejected and its domain promoted toward the browser tiers. Closing it needs
    attribute-aware parsing of this arm for all seven markers.

    IT IS PINNED HERE SO THE DAY SOMEBODY FIXES IT, THIS TEST FAILS AND SAYS SO
    -- a latent false positive that no test mentions is one nobody can find.

    STILL TRUE AFTER 2026-09-06, AND DELIBERATELY. Suppressing this rejection was
    built and MEASURED TO ADMIT A PADDED ONE-ID WALL AS THE PAGE, which outranks
    this false positive, so the verdict was left exactly as it is. What changed is
    the failure CLASS -- see
    `test_a_quoted_container_id_no_longer_promotes_the_domain` -- so the article
    is still rejected on this fetch but no longer walks its domain off tier 0 on
    every later one.
    """
    encoded = '<pre>&lt;div id="sec-if-cpt-container"&gt;</pre>'
    html = (f'<html><head><title>How Akamai challenges work</title></head>'
            f'<body>{encoded}<p>{_PROSE}</p></body></html>')
    md = extract.html_to_markdown(html, "https://example.com/")
    ok, reason = extract.is_real_content(md, html, 200)
    assert not ok and "challenge markup" in reason, (
        "the known residual has CHANGED -- a quoting page is now admitted. That "
        "is an improvement; delete this test and say so in the commit.")


# ---- The uncorroborated-container-id class demotion (2026-09-06) -------------
#
# `_challenge_reason` is UNCHANGED at its default, so every verdict this file
# returns is what `main` returned. Only `classify_failure` moves, and only for a
# document whose sole challenge evidence is ONE Akamai container id.

_CID1 = "sec-if-cpt-container"
_CID2 = "sec-bc-tile-parent"
_LONG_PROSE = "<p>" + ("The building has four floors and a lift. " * 40) + "</p>"


def _one_id_document(head="<title>How Akamai challenges work</title>",
                     quoted=True, extra="", body=None):
    marker = ('<pre>&lt;div id="' + _CID1 + '"&gt;</pre>' if quoted
              else '<div id="' + _CID1 + '"></div>')
    return ("<html><head>" + head + "</head><body>" + marker + extra
            + (_LONG_PROSE if body is None else body) + "</body></html>")


def test_a_quoted_container_id_no_longer_promotes_the_domain():
    """THE REPAIR, and BOTH halves are the point.

    An article about Akamai bot detection matches the markup arm, because that
    arm is a substring scan of the raw document and cannot tell markup the ORIGIN
    SERVED from markup a page DISPLAYS.

    The VERDICT is deliberately unchanged -- the article is still rejected, and
    the first assertion pins that, because suppressing the rejection was built and
    measured to admit a padded one-id wall AS THE PAGE.

    The CLASS is what moves. `cloudflare_challenge` is in
    PERSISTENT_FAILURE_CLASSES, so on `main` this article walked its whole DOMAIN
    off tier 0 -- the only tier that reads a PDF or returns a JSON body verbatim
    -- and toward the tiers that put a window on the user's screen, on every later
    fetch. That is the harm the backlog entry itself calls expensive.
    """
    html = _one_id_document()
    assert len(extract._akamai_ids_in(html)) == 1, (
        "this document does not carry exactly one container id in markup form, "
        "so it cannot exercise the exemption at all")
    md = extract.html_to_markdown(html, "https://example.com/")
    ok, reason = extract.is_real_content(md, html, 200)
    assert not ok and "challenge markup" in reason, (
        "THE VERDICT MOVED. It must not: leaving the rejection in place is what "
        "keeps a padded one-id wall from being returned as the page.")
    cls = extract.classify_failure(200, html, None, extracted=md)
    assert cls not in state.PERSISTENT_FAILURE_CLASSES, (
        f"a quoted container id still yields {cls!r}, which promotes the domain")


def test_a_padded_one_id_wall_is_still_rejected():
    """THE CRITICAL FROM THE ABANDONED APPROACH, pinned so it cannot return.

    The previous attempt suppressed the marker when a second container id was
    absent. Measured, that returned a 2xx Akamai wall carrying one id and fifty
    characters of text to the caller AS THE PAGE -- a silent wrong answer, which
    this project ranks above every false positive here.

    This change moves a CLASS and never a verdict, so that document is still
    rejected. If this test ever fails, a verdict-suppressing rule has been
    reintroduced and it is the expensive kind.
    """
    html = _one_id_document(head="", quoted=False)
    md = extract.html_to_markdown(html, "https://example.com/")
    ok, reason = extract.is_real_content(md, html, 200, status_is_real=True)
    assert not ok, (
        "a padded one-id Akamai wall was returned as real content -- this is the "
        "regression that caused the two-id approach to be abandoned")
    assert "challenge markup" in reason, f"caught by the wrong arm: {reason}"


def test_a_realistic_textless_one_id_wall_keeps_the_persistent_class():
    """ROUND-2 REVIEW, must-fix high. THE REGRESSION MY OWN TEST COULD NOT SEE.

    The first version of this test built a 76-byte document. That lands on
    `empty_content` because of its SIZE -- the arm below the exemption keys on
    `len(body.strip()) < 200`, RAW BYTES -- so it passed against code carrying no
    exemption at all, and it could not see that a REALISTIC wall was being
    demoted. Measured on the real shape: 2,497 raw bytes, 32 extracted characters,
    `main` says `cloudflare_challenge` and the unfixed tip said `other`, which is
    transient.

    The fixture is the CAPTURED wall with its second container id removed, so the
    document is a genuine Akamai body at genuine size rather than a hand-built
    stub. Its size is asserted, because the whole defect was a test whose fixture
    was too small to reach the arm it was aimed at.
    """
    wall = (FIXTURES / "akamai").read_text(
        encoding="utf-8", errors="replace")
    html = wall.replace(_CID2, "sec-bc-tile-absent")
    assert len(html.strip()) > 200, (
        "the fixture is under the raw-byte floor, so it would reach "
        "`empty_content` for its size and this test would prove nothing")
    assert extract._akamai_ids_in(html) == (_CID1,), (
        "this document does not carry exactly one container id")
    md = extract.html_to_markdown(html, "https://example.com/")
    assert len((md or "").strip()) < 50, (
        "the wall now extracts readable text, so it is no longer the case this "
        "test was written for")
    cls = extract.classify_failure(200, html, None, extracted=md)
    assert cls == "cloudflare_challenge", (
        f"a realistic textless one-id wall was demoted to {cls!r}; a wall with "
        f"nothing to read is not a page and must keep the persistent class")


def test_a_short_article_quoting_one_id_is_still_demoted():
    """ROUND-2 REVIEW, the OTHER half of the reversed boundary.

    A real article shorter than the 200 RAW BYTE floor used to reach
    `empty_content` and stay persistent, so the repair simply did not fire for
    short pages. The boundary now sits on READABLE TEXT -- the same
    `MIN_DOCUMENT_TEXT` measure `is_real_content` judges on -- so raw size no
    longer decides it.
    """
    html = ('<html><body><div id="' + _CID1 + '"></div><p>'
            + ("Akamai bot detection explained in brief for readers here. " * 2)
            + '</p></body></html>')
    md = extract.html_to_markdown(html, "https://example.com/")
    assert len((md or "").strip()) >= 50, "fixture has too little text to qualify"
    cls = extract.classify_failure(200, html, None, extracted=md)
    assert cls not in state.PERSISTENT_FAILURE_CLASSES, (
        f"a short article quoting one id is still {cls!r}, which promotes")


def test_the_demotion_needs_evidence_and_does_not_assume_it():
    """FAIL-SAFE. No `extracted` means no readability evidence, so no demotion.

    `classify_failure`'s `extracted` argument is optional. Every call site that
    passes a real body passes it, but the parameter allows None and a caller that
    omitted it would otherwise get a demotion decided on nothing. Absence of
    evidence is not evidence, so the persistent class is kept.

    This also keeps the measure single: `extracted` is what `is_real_content`
    judges on, and reaching for the script-stripped body instead would give the
    two consumers different answers about one document -- the haystack split that
    killed an earlier approach outright.
    """
    html = ('<html><body><div id="' + _CID1 + '"></div><p>'
            + ("Akamai bot detection explained in brief for readers here. " * 2)
            + '</p></body></html>')
    assert extract.classify_failure(200, html, None) == "cloudflare_challenge", (
        "the demotion fired with no extracted text supplied, so it was decided "
        "on no evidence of readability at all")
    # ROUND-2 REVIEW, the PRODUCTION path that does this. `tiers.py:902` hands a
    # real 2048-byte body off an HTTPError with no `extracted`, and a status like
    # 400 falls through to the body-only arm rather than returning earlier. Pinned
    # at that status so the claim in extract.py names a path a test also covers.
    assert extract.classify_failure(400, html, None) == "cloudflare_challenge", (
        "a fall-through 4xx with no extracted text was demoted; the HTTPError "
        "path at tiers.py:902 supplies no extraction and must keep main's class")


@pytest.mark.parametrize("label,extra,head", [
    ("cloudflare url", '<script src="/cdn-cgi/challenge-platform/h/b/x.js">'
                       '</script>', "<title>x</title>"),
    ("datadome", '<script src="https://captcha-delivery.com/x.js"></script>',
     "<title>x</title>"),
    ("cf browser verification", '<div class="cf-browser-verification"></div>',
     "<title>x</title>"),
    ("challenge title", "", "<title>Just a moment...</title>"),
])
    """BOUNDARY. The exemption says nothing about non-Akamai evidence."""
    is a defect this project has already shipped once (CONSTRAINTS, save#26).
    """
    html = _one_id_document(head=head, quoted=False, extra=extra)
    md = extract.html_to_markdown(html, "https://example.com/")
    cls = extract.classify_failure(200, html, None, extracted=md)
    assert cls == "cloudflare_challenge", (
        f"{label}: the exemption fired while other challenge evidence was "
        f"present, giving {cls!r}")


def test_the_exemption_does_not_fire_when_both_container_ids_are_present():
    """The real wall must be untouched, asserted on the committed capture."""
    html = (FIXTURES / "akamai").read_text(
        encoding="utf-8", errors="replace")
    assert len(extract._akamai_ids_in(html)) == 2, (
        "the captured wall no longer carries both container ids -- the premise "
        "of the whole exemption has changed and it must be re-measured")
    md = extract.html_to_markdown(html, "https://www.hilton.com/en/")
    ok, reason = extract.is_real_content(md, html, 200, status_is_real=True)
    assert not ok and "challenge markup" in reason, reason
    assert extract.classify_failure(200, html, None,
                                    extracted=md) == "cloudflare_challenge"


def test_a_data_id_spelling_does_not_count_toward_the_id_count():
    """BOUNDARY, and the reason `_akamai_ids_in` reuses the shipped literals.

    `data-id="X"` CONTAINS `id="X"`, which is why the markup arm requires leading
    whitespace. The id count consults the SAME generated literals, so it inherits
    that guard rather than re-implementing it -- a hand-written needle here would
    make a page carrying one real id and one `data-id` look corroborated and keep
    the persistent class it should lose.
    """
    html = ('<html><head><title>Hotels</title></head><body>'
            '<div id="' + _CID1 + '"></div>'
            '<div data-id="' + _CID2 + '"></div>' + _LONG_PROSE
            + '</body></html>')
    assert extract._akamai_ids_in(html) == (_CID1,), (
        "the data-id spelling was counted as a distinct container id")
    md = extract.html_to_markdown(html, "https://example.com/")
    cls = extract.classify_failure(200, html, None, extracted=md)
    assert cls not in state.PERSISTENT_FAILURE_CLASSES, (
        f"a data-id spelling corroborated a real id, leaving {cls!r}")


def test_skip_akamai_ids_defaults_to_the_shipped_behaviour():
    """CONTRACT PIN. The new keyword must be invisible to every other caller.

    `_challenge_reason` has five call sites and only one passes the keyword. If
    its default ever flipped, `is_real_content` would stop rejecting a real
    Akamai wall and the safety argument for this whole change would be void --
    so the default is asserted directly rather than trusted.
    """
    html = '<html><head></head><body><div id="' + _CID1 + '"></div></body></html>'
    assert extract._challenge_reason(html, "", page_served=True), (
        "the default no longer reports a lone container id as challenge markup")
    assert not extract._challenge_reason(html, "", page_served=True,
                                         skip_akamai_ids=True), (
        "skip_akamai_ids=True still reported the container id")


def test_the_grouped_derivation_produces_the_same_twelve_literals():
    """REFACTOR PIN. The flat tuple is now DERIVED from the grouped mapping.

    Asserting the exact SET rather than the count is the point -- a count
    survives a wrong quote character or a lost tab, and nothing else in this
    suite would notice a literal that changed rather than disappeared.
    """
    expected = {ws + "id=" + q + name + q
                for name in (_CID1, _CID2)
                for q in ('"', "'")
                for ws in (" ", chr(9), chr(10))}
    assert set(extract._AKAMAI_CHALLENGE_MARKUP) == expected
    assert len(extract._AKAMAI_CHALLENGE_MARKUP) == 12
    for name in extract._AKAMAI_CHALLENGE_IDS:
        group = extract._AKAMAI_MARKUP_BY_ID[name]
        assert len(group) == 6, f"{name} has {len(group)} literals, expected 6"
        assert set(group) <= set(extract._AKAMAI_CHALLENGE_MARKUP), (
            f"{name} has literals the flat tuple does not carry -- drift")


def test_two_container_ids_keep_the_persistent_class_even_with_real_text():
    """ISOLATES THE ID-COUNT GUARD, which a re-run of the mutations found had
    become invisible.

    `test_the_exemption_does_not_fire_when_both_container_ids_are_present` uses
    the captured wall, and that wall extracts 32 characters. Once round 2 added
    the readable-text conjunct, THAT conjunct alone kept the wall persistent --
    so deleting the distinct-id guard entirely changed nothing the test could
    see, and the control had quietly become a tautology.

    Narrowing a predicate is exactly what disarms a control, which is why the
    mutations get re-run after one rather than just the suite.

    This document carries BOTH ids AND enough prose to clear MIN_DOCUMENT_TEXT,
    so the readable-text conjunct is satisfied and ONLY the id count can keep the
    persistent class. It is also the stated residual: a page quoting both ids is
    still treated as a challenge.
    """
    html = ('<html><head><title>How Akamai challenges work</title></head><body>'
            '<pre>&lt;div id="' + _CID1 + '"&gt;&lt;div id="' + _CID2 + '"&gt;</pre>'
            '<p>' + ("The building has four floors and a lift. " * 40) + '</p>'
            '</body></html>')
    md = extract.html_to_markdown(html, "https://example.com/")
    assert len(extract._akamai_ids_in(html)) == 2, "needs both ids to test the count"
    assert len((md or "").strip()) >= 50, (
        "this document must clear MIN_DOCUMENT_TEXT, or the readable-text "
        "conjunct decides it and the id count is invisible again")
    cls = extract.classify_failure(200, html, None, extracted=md)
    assert cls == "cloudflare_challenge", (
        f"two container ids with real text were demoted to {cls!r}; the "
        f"distinct-id guard is not doing its job")
