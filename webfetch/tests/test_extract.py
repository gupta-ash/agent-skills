"""Unit tests for extract.py - run with: python -m pytest tests/test_extract.py"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extract


def test_html_to_markdown_basic():
    html = "<html><body><h1>T</h1><p>" + ("paragraph content. " * 30) + "</p></body></html>"
    md = extract.html_to_markdown(html, "https://example.com/")
    assert "paragraph content" in md


def test_is_real_content_zero_extract():
    """W2: the 200-char floor is gone, and "0 chars" is its honest replacement --
    449 of the 580 rows it rejected had extracted NOTHING, so "too short" was
    describing a length the extractor never produced."""
    ok, reason = extract.is_real_content("", "<html><body><img src=x></body></html>", 200)
    assert not ok and "0 chars" in reason


def test_is_real_content_short_extract_now_admitted():
    """The behaviour change, stated rather than implied: a short extraction that is
    real content is content. The body below is example.com's own, 113 chars.

    It used to read "hi", which the MIN_DOCUMENT_TEXT floor now rejects at 2
    chars. The docstring already named 113 as the case it meant; the fixture now
    is that case, so the test still guards the removal of the 200-char floor
    without also asserting that a two-character document is a page."""
    body = ("This domain is for use in documentation examples without needing "
            "permission. Avoid use in operations.\n\nLearn more")
    assert len(body) == 113, "precondition: example.com's measured extraction"
    ok, _ = extract.is_real_content(body, f"<html><body>{body}</body></html>", 200)
    assert ok


def test_is_real_content_4xx():
    md = "real content paragraph " * 20
    ok, reason = extract.is_real_content(md, "<html>" + md + "</html>", 403)
    assert not ok and "non-2xx" in reason


def test_is_real_content_cloudflare_body():
    """W2: detection is scoped to the <title> element rather than free prose, so
    the fixture carries the title every captured Cloudflare wall actually has."""
    body = ("<html><head><title>Just a moment...</title></head><body>"
            "Checking your browser before access...</body></html>")
    ok, reason = extract.is_real_content("Just a moment...", body, 200)
    assert not ok and "challenge page title" in reason


def test_classify_failure_cloudflare():
    body = "<html><head><title>Just a moment...</title></head><body>x</body></html>"
    assert extract.classify_failure(403, body) == "cloudflare_challenge"


def test_classify_failure_4xx_without_challenge_evidence():
    """The other side of the same change: a plain 403 is an origin block, not a
    challenge, and is no longer promoted to one by a bare prose match."""
    assert extract.classify_failure(403, "<html><body>Forbidden</body></html>") == "origin_4xx_block"


# ---- R2-1 + RV-5: classify_failure was scanning the wrong 600 bytes ---------
#
# `_challenge_reason(body, body[:600])` -- the second argument means EXTRACTED
# text, and it was being handed the first 600 bytes of RAW HTML: doctype, <head>,
# stylesheet links. The client-demand arm therefore never saw prose. RV-5 is the
# same fault seen from the tier call sites: is_real_content and classify_failure
# could disagree about one document despite sharing their evidence.
#
# The <head> below is an ordinary one -- charset, viewport, robots, preconnect,
# three stylesheets, two icons, two og tags -- and it alone is 693 bytes. Nothing
# is padded to make the point; that is simply where a real demand phrase sits.

_ORDINARY_HEAD = (
    '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '<meta name="robots" content="noindex, nofollow, noarchive">\n'
    '<link rel="preconnect" href="https://static.example-cdn.net" crossorigin>\n'
    '<link rel="stylesheet" href="/assets/base.4f2a91c7.css">\n'
    '<link rel="stylesheet" href="/assets/layout.9b31ee02.css">\n'
    '<link rel="stylesheet" href="/assets/print.1c07da53.css" media="print">\n'
    '<link rel="icon" href="/favicon.ico" sizes="any">\n'
    '<link rel="apple-touch-icon" href="/apple-touch-icon-180.png">\n'
    '<meta property="og:site_name" content="Example Storefront">\n'
    '<meta property="og:type" content="website">\n'
    '</head>\n<body>\n<main>\n'
)

# Status 200, no <title>, no challenge markup, prose only. The one arm that can
# catch it is the client-demand arm.
PROSE_ONLY_WALL = (
    _ORDINARY_HEAD
    + '<p>We could not confirm this request came from a browser. '
      'Please verify you are human to continue to the page you asked for.</p>\n'
    + '</main>\n</body>\n</html>\n'
)


def test_prose_only_wall_is_classified_persistent():
    """R2-1. Measured: the phrase begins at byte 758, so a 600-byte prefix scan
    of the raw HTML cannot reach it and the wall is called `other` -- transient,
    so the domain never promotes off a tier that will never serve it."""
    at = PROSE_ONLY_WALL.lower().find("verify you are human")
    assert at > extract.DEMAND_TEXT_MAX, (
        f"precondition: phrase at {at} must sit past the {extract.DEMAND_TEXT_MAX}"
        "-byte cap, or this test proves nothing")
    assert "<title" not in PROSE_ONLY_WALL.lower(), "precondition: no title arm"
    assert extract.classify_failure(200, PROSE_ONLY_WALL, None) == "cloudflare_challenge"


def test_the_two_predicates_agree_on_the_same_document():
    """RV-5, verified rather than assumed: one document, both predicates."""
    import state

    md = extract.html_to_markdown(PROSE_ONLY_WALL, "https://example.com/")
    ok, reason = extract.is_real_content(md, PROSE_ONLY_WALL, 200)
    assert not ok, "precondition: the content gate must reject this"
    assert "client-demand" in reason, reason

    cls = extract.classify_failure(200, PROSE_ONLY_WALL, None)
    assert cls in state.PERSISTENT_FAILURE_CLASSES, (
        f"the gate rejected it as a wall but the classifier called it {cls!r}")


def test_classify_failure_still_reads_the_title_on_a_long_body():
    """The restructure guard. classify_failure now receives the WHOLE body as
    `text`, so an early `return ""` on the cap would gate the title arm away on
    every body over 600 bytes -- which is most of them."""
    html = ('<html><head><title>Just a moment...</title></head><body><p>'
            + "filler. " * 400 + '</p></body></html>')
    assert len(html) > extract.DEMAND_TEXT_MAX, "precondition"
    assert extract.classify_failure(200, html, None) == "cloudflare_challenge"


def test_the_two_demand_paths_are_distinguishable_in_a_log():
    """Capped and uncapped scans reach the same verdict by different evidence."""
    capped = extract._challenge_reason(PROSE_ONLY_WALL, "please verify you are human")
    whole = extract._challenge_reason(PROSE_ONLY_WALL, PROSE_ONLY_WALL,
                                      whole_body_demands=True)
    assert "in short body" in capped, capped
    assert "in body" in whole and "in short body" not in whole, whole


def test_is_real_content_keeps_its_cap():
    """M1 depends on it. An ARTICLE that mentions a demand phrase is judged on a
    SUCCESSFUL fetch, so the whole-body scan must not leak onto this path."""
    article = ('<html><head><title>Bot walls explained</title></head><body><p>'
               'When a site cannot confirm the request came from a browser it '
               'serves an interstitial that asks you to verify you are human. '
               + "This note explains what that page is doing and why. " * 20
               + '</p></body></html>')
    md = extract.html_to_markdown(article, "https://example.com/")
    assert len((md or "").strip()) > extract.DEMAND_TEXT_MAX, "precondition"
    ok, reason = extract.is_real_content(md, article, 200)
    assert ok, reason


# ---- F5: scripts and styles are not evidence of a wall ----------------------
#
# classify_failure's output is the PROMOTION SIGNAL, not a label. `other` is
# transient; `cloudflare_challenge` is persistent. So when round 2 pointed the
# uncapped scan at the raw bytes, a demand phrase inside a site's own
# application JavaScript could walk a domain toward the headed tier -- browser
# windows on the user's machine, which is what C-3/C-4 forbids.

# The raw body has to clear 200 characters or classify_failure returns
# `empty_content` before it ever reaches the question under test -- which is how
# the first version of this fixture passed for the wrong reason. A real SPA
# shell carries a bundle this size and more.
SCRIPT_ONLY_DEMAND = (
    '<html><head><title>Dashboard</title></head><body><div id="root"></div>'
    '<script>'
    'function gate(session){ if (!session) { return show("verify you are human"); } }'
    'function boot(){ var el = document.getElementById("root");'
    ' el.setAttribute("data-ready", "1"); return gate(window.__SESSION__); }'
    'window.addEventListener("DOMContentLoaded", boot);'
    '</script></body></html>'
)

STYLE_ONLY_DEMAND = (
    '<html><head><title>Dashboard</title>'
    '<style>.banner::after{content:"complete the security check"}</style>'
    '</head><body><div id="root"></div></body></html>'
)

VISIBLE_DEMAND_WALL = (
    '<html><head><title>Dashboard</title></head><body>'
    '<p>Please verify you are human to continue.</p></body></html>'
)


def test_a_demand_inside_a_script_is_not_a_challenge():
    """F5's first example, and the one that is a genuine promotion defect: this
    moves the class from transient to persistent."""
    import state

    assert "verify you are human" in SCRIPT_ONLY_DEMAND, "precondition"
    cls = extract.classify_failure(200, SCRIPT_ONLY_DEMAND, None)
    assert cls != "cloudflare_challenge", cls
    assert cls not in state.PERSISTENT_FAILURE_CLASSES, (
        f"{cls!r} would promote the domain on its own JavaScript")


# The JS-shell arm has the same exposure as the demand arm: an SPA that renders
# its own fallback message from code carries "enable JavaScript" inside a
# <script>, where it is the site's source rather than something the origin
# showed. A GENUINE shell puts it in <noscript>, which the stripper leaves alone
# -- so stripping costs the real signal nothing. Both directions are asserted.
SHELL_MARKER_ONLY_IN_SCRIPT = (
    '<html><head><title>Dashboard</title></head><body><div id="root"></div>'
    '<script>'
    'function fallback(){ return "Please enable JavaScript to use this app"; }'
    'function boot(){ var el = document.getElementById("root");'
    ' el.setAttribute("data-ready", "1"); return fallback(); }'
    'window.addEventListener("DOMContentLoaded", boot);'
    '</script></body></html>'
)

REAL_SHELL_WITH_NOSCRIPT = (
    '<html><head><title>Dashboard</title>'
    '<script defer src="/static/js/main.8f7c2b1a.js"></script></head><body>'
    '<noscript>You need to enable JavaScript to run this app.</noscript>'
    '<div id="root"></div>'
    '<p>Loading the console, this usually takes a moment on first visit.</p>'
    '</body></html>'
)


def test_a_shell_marker_only_inside_a_script_is_not_a_js_shell():
    """The JS-shell half of F5. Same defect shape as the demand arm."""
    import state

    cls = extract.classify_failure(200, SHELL_MARKER_ONLY_IN_SCRIPT, None)
    assert cls != "js_only_spa", cls
    assert cls not in state.PERSISTENT_FAILURE_CLASSES, (
        f"{cls!r} would promote the domain on its own source code")


def test_a_real_shell_declaring_itself_in_noscript_is_still_a_js_shell():
    """The direction the strip must not break. <noscript> is not stripped, so a
    genuine shell keeps its class -- and promoting THAT toward a browser tier is
    correct, since a browser is what renders it."""
    assert extract.classify_failure(200, REAL_SHELL_WITH_NOSCRIPT, None) == "js_only_spa"


def test_a_demand_inside_a_style_block_is_not_a_challenge():
    cls = extract.classify_failure(200, STYLE_ONLY_DEMAND, None)
    assert cls != "cloudflare_challenge", cls


def test_a_demand_in_visible_text_is_still_a_challenge():
    """The direction the strip must not break."""
    assert extract.classify_failure(200, VISIBLE_DEMAND_WALL, None) == "cloudflare_challenge"


def test_a_403_whose_demand_is_only_in_a_script_stays_an_origin_block():
    """F5's second example. Measured against state.py: BOTH origin_4xx_block and
    cloudflare_challenge are in PERSISTENT_FAILURE_CLASSES, so a CONSISTENTLY
    mislabelled domain promotes identically -- this is not the browser-window
    defect the first example is. It still matters, because promotion counts
    CONSECUTIVE same-class failures: an INTERMITTENT mislabel breaks a valid
    streak and delays promotion. Worth fixing for honest logs and for streak
    continuity; not worth calling a C-3/C-4 defect."""
    import state

    body = ('<html><head><title>Access denied - support</title></head><body>'
            '<p>Your request was blocked by an origin rule.</p>'
            '<script>var help = "verify you are human";</script></body></html>')
    cls = extract.classify_failure(403, body, None)
    assert cls == "origin_4xx_block", cls
    assert cls in state.PERSISTENT_FAILURE_CLASSES, (
        "precondition: both candidate classes are persistent, which is the point")


def test_the_markup_arm_still_reads_the_raw_body():
    """THE SCOPING DECISION, PINNED. Measured on the captured walls, the marker
    `/cdn-cgi/challenge-platform/h/` sits INSIDE the script body
    (`a.src = '/cdn-cgi/...'`), not in an attribute of an unstripped tag. If the
    strip were applied before the markup arm it would delete the evidence that
    catches 4 of the 11 rejected captures."""
    body = ('<html><head><title>Loading</title></head><body>'
            "<script>var a=document.createElement('script');"
            "a.src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1';"
            '</script></body></html>')
    assert extract.classify_failure(403, body, None) == "cloudflare_challenge"
    reason = extract._challenge_reason(body, extract._strip_script_and_style(body),
                                       whole_body_demands=True)
    assert "challenge markup" in reason, reason


# ---- round 4: the balanced regex alone was evadable --------------------------
#
# A truncated response or malformed markup leaves an UNCLOSED <script>, whose
# contents stayed in the haystack. A demand phrase in there yields a PERSISTENT
# challenge class, which promotes the domain toward the headed tier -- the same
# C-3/C-4 exposure F5 closed for balanced blocks, reached by a different route.
#
# Bodies are padded past 200 raw characters so `empty_content` cannot answer
# first; that is how an earlier fixture in this file passed for the wrong reason.

UNCLOSED_SCRIPT_DEMAND = (
    '<html><head><title>Dashboard</title></head><body><div id="root"></div>'
    '<p>Loading the console, this usually takes a moment on a first visit.</p>'
    '<script>var notice = "verify you are human";'
    ' function boot(){ return document.getElementById("root"); }'
    ' window.addEventListener("DOMContentLoaded", boot);'
)

UNCLOSED_STYLE_DEMAND = (
    '<html><head><title>Dashboard</title></head><body><div id="root"></div>'
    '<p>Loading the console, this usually takes a moment on a first visit.</p>'
    '<style>.banner::after{content:"complete the security check"}'
    ' .banner{display:block;margin:0;padding:0;border:0;font-size:14px}'
)


def test_an_unclosed_script_carrying_a_demand_is_not_a_challenge():
    import state

    assert "</script>" not in UNCLOSED_SCRIPT_DEMAND, "precondition: it is unclosed"
    assert len(UNCLOSED_SCRIPT_DEMAND.strip()) > 200, "precondition: not empty_content"
    cls = extract.classify_failure(200, UNCLOSED_SCRIPT_DEMAND, None)
    assert cls != "cloudflare_challenge", cls
    assert cls not in state.PERSISTENT_FAILURE_CLASSES, (
        f"{cls!r} would promote the domain on an unclosed script")


def test_an_unclosed_style_carrying_a_demand_is_not_a_challenge():
    assert "</style>" not in UNCLOSED_STYLE_DEMAND, "precondition: it is unclosed"
    assert len(UNCLOSED_STYLE_DEMAND.strip()) > 200, "precondition"
    cls = extract.classify_failure(200, UNCLOSED_STYLE_DEMAND, None)
    assert cls != "cloudflare_challenge", cls


def test_the_unclosed_pass_runs_to_the_end_of_the_document():
    """Directly on the stripper, so the mechanism is pinned and not merely its
    consequence. This is also what a browser does with an unclosed <script>."""
    out = extract._strip_script_and_style(
        "<p>kept</p><script>var x = 1; // never closed")
    assert "kept" in out
    assert "var x = 1" not in out


def test_an_unclosed_script_swallows_the_tags_that_follow_it():
    """The EOD semantics specifically, which is what a browser does. A pass that
    stopped at the next `<` would leave everything after the first tag boundary
    in the haystack -- including a demand phrase, which is the whole exposure.

    My first version of this test put the phrase inside a BALANCED <style> after
    the unclosed script, so the balanced pass removed it and the test passed
    whether or not the open-ended pass reached the end. It could not fail on the
    fault it names."""
    out = extract._strip_script_and_style(
        "<p>kept</p><script>var a = 1;"
        "<b>verify you are human</b><p>swallowed</p>")
    assert "kept" in out
    assert "var a = 1" not in out
    assert "verify you are human" not in out, (
        "the open-ended pass stopped at a tag boundary instead of the end")
    assert "swallowed" not in out


# ---- round 5: the open-ended pass matched text that was not an opening tag ---
#
# `<script\b.*` fired inside HTML comments and inside attribute values, deleting
# every visible byte after them. A real demand phrase following such text was
# stripped, so the failure classified TRANSIENT -- the exact inversion of the
# defect the stripping exists to fix. Separately, the balanced pattern rejected
# `</script >` with a space, after which the fallback swallowed the visible
# content past the close.
#
# All three are asserted at classify_failure level, because "persistent vs
# transient" is the consequence that matters, not the string content.

_VISIBLE_DEMAND = ('<p>Access to this content requires a check. '
                   'Please complete the security check to continue, it usually '
                   'takes only a few seconds to finish.</p>')


def test_a_demand_after_a_script_mentioned_in_a_comment_still_classifies():
    html = ('<html><head><title>Checkout</title></head><body><p>hello</p>'
            '<!-- the inline <script> block was removed in this build -->'
            + _VISIBLE_DEMAND + '</body></html>')
    assert extract.classify_failure(200, html, None) == "cloudflare_challenge"


def test_a_demand_after_a_script_in_an_attribute_value_still_classifies():
    html = ('<html><head><title>Checkout</title></head><body><p>hello</p>'
            '<div data-template="<script>"></div>'
            + _VISIBLE_DEMAND + '</body></html>')
    assert extract.classify_failure(200, html, None) == "cloudflare_challenge"


def test_a_demand_after_a_spaced_closing_tag_still_classifies():
    """`</script >` and `</style >` are legal HTML."""
    html = ('<html><head><title>Checkout</title></head><body><p>hello</p>'
            '<script>var x = 1;</script >'
            + _VISIBLE_DEMAND + '</body></html>')
    assert extract.classify_failure(200, html, None) == "cloudflare_challenge"


def test_a_demand_after_a_spaced_style_close_still_classifies():
    html = ('<html><head><title>Checkout</title>'
            '<style>.a{color:#000}</style >'
            '</head><body><p>hello</p>' + _VISIBLE_DEMAND + '</body></html>')
    assert extract.classify_failure(200, html, None) == "cloudflare_challenge"


def test_a_phrase_that_is_ONLY_inside_a_comment_is_not_evidence():
    """The other direction for comment handling: an invisible phrase must not
    classify, because nobody was shown it."""
    import state

    html = ('<html><head><title>Dashboard</title></head><body>'
            '<p>Loading the console, this usually takes a moment on first visit.</p>'
            '<!-- TODO: restore the "verify you are human" interstitial here -->'
            '<div id="root"></div></body></html>')
    cls = extract.classify_failure(200, html, None)
    assert cls != "cloudflare_challenge", cls
    assert cls not in state.PERSISTENT_FAILURE_CLASSES, cls


def test_a_genuinely_unclosed_script_is_still_truncated():
    """The round-4 property must survive the round-5 narrowing."""
    import state

    html = ('<html><head><title>Dashboard</title></head><body>'
            '<p>Loading the console, this usually takes a moment on first visit.</p>'
            '<script>var notice = "verify you are human";'
            ' function boot(){ return document.getElementById("root"); }')
    cls = extract.classify_failure(200, html, None)
    assert cls != "cloudflare_challenge", cls
    assert cls not in state.PERSISTENT_FAILURE_CLASSES, cls


def test_at_tag_start_distinguishes_content_from_inside_a_tag():
    """The three-line predicate the whole narrowing rests on, tested directly."""
    content = '<p>hi</p><script>'
    assert extract._at_tag_start(content, content.index("<script"))

    in_attr = '<div data-x="<script">'
    assert not extract._at_tag_start(in_attr, in_attr.index("<script"))

    in_comment = '<!-- <script> -->'
    assert not extract._at_tag_start(in_comment, in_comment.index("<script"))

    at_start = '<script>'
    assert extract._at_tag_start(at_start, 0)


def test_literal_script_text_in_a_textarea_is_erased_ACCEPTED_RESIDUAL():
    """PINNED, NOT FIXED. The stripper is context-free, so literal markup shown
    inside a <textarea> is erased along with real scripts.

    This is the OPPOSITE error to the one above: it removes evidence rather than
    inventing it, so its failure mode is a missed wall rather than a false
    promotion toward the headed tier. No captured page exhibits it. Closing it
    needs an HTML-aware tokenizer, which is machinery for a case with no observed
    instance -- declined deliberately, and asserted here so the behaviour is
    visible rather than latent."""
    html = ('<html><body><p>Paste your snippet:</p>'
            '<textarea><script>alert(1)</script></textarea></body></html>')
    out = extract._strip_script_and_style(html)
    assert "Paste your snippet:" in out
    assert "alert(1)" not in out, (
        "if this ever passes, the residual is closed and this test should be "
        "rewritten as a guard rather than a disclosure")


def test_the_stripper_leaves_visible_prose_alone():
    html = ('<html><head><style>p{color:#000}</style></head><body>'
            '<p>Real prose survives.</p><script>var x=1;</script></body></html>')
    out = extract._strip_script_and_style(html)
    assert "Real prose survives." in out
    assert "var x=1" not in out and "color:#000" not in out


# ---- the embeddable Turnstile marker, in classify_failure's two call sites --
#
# Same defect as in the content gate, in its second home, and the more expensive
# one: `cloudflare_challenge` is a PERSISTENT class, so a contact form on a 200
# would walk the whole domain toward the headed tier and a window on the user's
# screen (C-3/C-4). On a non-2xx the origin did not serve the page, so the same
# marker IS evidence and nothing changes.

_TURNSTILE_MOUNT = ('<div class="cf-turnstile" '
                    'data-sitekey="0x4AAAAAAADWbCSNeh3IIMw3"></div>')

# No challenge title, no demand phrase, no JS-shell marker, and comfortably over
# 200 raw chars so `empty_content` cannot answer first. The Turnstile mount is
# the ONLY thing any arm can key on.
_ARTICLE_WITH_A_CONTACT_FORM = (
    '<html><head><title>Notes on edge verification</title></head><body>'
    '<article><p>The archive reader truncated members whose names exceeded one '
    'hundred bytes. This note explains the cause, the fix, and the migration '
    'path for anyone still reading the old format.</p></article>'
    '<form action="/contact" method="post"><textarea name="msg"></textarea>'
    + _TURNSTILE_MOUNT +
    '<button type="submit">Send</button></form></body></html>'
)


def test_a_403_carrying_a_turnstile_mount_is_still_a_challenge():
    """UNCHANGED-BEHAVIOUR GUARD, labelled rather than dressed up as evidence for
    the split: it passed before the split too, when the two tuples were one. What
    it does pin is the argument on the 4xx branch -- the origin refused, so the
    widget is an interstitial. Flip `page_served=False` to True there and the
    mount is exempted, the document classifies `origin_4xx_block`, and this
    dies."""
    assert extract.classify_failure(403, _ARTICLE_WITH_A_CONTACT_FORM, None) == (
        "cloudflare_challenge")


def test_a_200_carrying_a_turnstile_mount_is_not_a_challenge():
    """The fix. A 200 that failed the content gate reaches the body-only
    fallback, and a contact form there must not promote the domain."""
    import state

    cls = extract.classify_failure(200, _ARTICLE_WITH_A_CONTACT_FORM, None)
    assert cls != "cloudflare_challenge", cls
    assert cls not in state.PERSISTENT_FAILURE_CLASSES, cls


def test_an_unknown_status_still_scans_both_marker_tuples():
    """UNCHANGED-BEHAVIOUR GUARD, and it passes with the split reverted as well.
    `page_served` is a claim that the origin served the page; a transport
    exception carries no status, so there is no such claim and no exemption. What
    it pins is the `status is not None` half of the expression -- drop that half,
    so a missing status reads as a served page, and this dies."""
    assert extract.classify_failure(None, _ARTICLE_WITH_A_CONTACT_FORM, None) == (
        "cloudflare_challenge")


def test_the_tuple_is_what_differs_not_the_status():
    """The split stated as the contrast it actually is. The previous version of
    this test asserted only the interstitial-only half and then claimed to show
    "the difference is the tuple, not the status" -- which one half cannot show,
    and which it went on passing with the split reverted. Both documents are
    asserted here at the SAME 200: same article, same contact form, only the
    marker swapped."""
    interstitial = _ARTICLE_WITH_A_CONTACT_FORM.replace(
        _TURNSTILE_MOUNT, '<div class="cf-browser-verification"></div>')
    assert extract.classify_failure(200, interstitial, None) == "cloudflare_challenge"
    assert extract.classify_failure(403, interstitial, None) == "cloudflare_challenge"
    assert extract.classify_failure(200, _ARTICLE_WITH_A_CONTACT_FORM, None) != (
        "cloudflare_challenge")


# ---- the gated whitespace title boundary, in classify_failure ---------------
#
# THE SITE THAT HAD NO TEST. _TITLE_BOUNDARY_WITH_MOUNT lets whitespace end a
# challenge phrase when the document carries a Turnstile mount, and it was
# measured on SHORT documents -- the 275-char OpenReview interstitial. For one
# round it was gated on the mount ALONE, on the argument that a long real article
# exceeds DEMAND_TEXT_MAX so the title arm is never consulted. That argument
# holds in is_real_content and is FALSE here: classify_failure passes
# whole_body_demands=True, which lifts the cap off this very arm, so the relaxed
# boundary reached every length.
#
# MEASURED 2026-08-30 on _DENIED_ARTICLE below (1,430 raw chars): mount-only
# gating returned `cloudflare_challenge`, a member of
# state.PERSISTENT_FAILURE_CLASSES, for a real 200 page whose only challenge
# signals are a contact-form widget and a title that happens to open with
# "Access denied". Persistent walks the whole domain toward the headed tier and a
# window on the user's screen (C-3/C-4). is_real_content showed nothing, there
# and now: `within_cap` is the condition that arm was already gated on.

_DENIED_TITLE = "Access denied and what it means for your API integration"

_DENIED_ARTICLE = (
    f'<html><head><title>{_DENIED_TITLE}</title></head><body><article><p>'
    'The archive reader truncated members whose names exceeded one hundred '
    'bytes. This note explains the cause, the fix, and the migration path for '
    'anyone still reading the old format. The header field is a fixed-width slot '
    'and the writer silently discarded the overflow, so the reader had nothing '
    'to recover from. We replaced it with the POSIX extended header, which '
    'stores the long name in a preceding record and leaves the fixed slot as a '
    'truncated hint for old readers. Existing archives are unaffected; only '
    'newly written ones carry the extension. If you maintain a reader, accept '
    'both forms and prefer the extended record when present. The migration is '
    'backward compatible in the direction that matters.</p></article>'
    '<form action="/contact" method="post"><textarea name="msg"></textarea>'
    + _TURNSTILE_MOUNT +
    '<button type="submit">Send</button></form></body></html>'
)

# The same title and the same mount, short enough that the cap does not withhold
# the exemption. Deliberately carries no demand phrase, no JS-shell marker and no
# interstitial-only marker, and is over 200 raw chars so `empty_content` cannot
# answer first -- the title arm is the only thing that can classify it.
_DENIED_SHORT = (
    f'<html><head><title>{_DENIED_TITLE}</title></head><body>'
    '<p>The gateway returned 403 for the second principal. See the migration '
    'note for the header change.</p>'
    '<form action="/contact" method="post"><textarea name="msg"></textarea>'
    + _TURNSTILE_MOUNT +
    '<button type="submit">Send</button></form></body></html>'
)


def test_the_whitespace_title_boundary_is_withheld_past_the_cap():
    """THE REGRESSION GUARD. Drop `within_cap and` from the boundary expression
    in _challenge_reason and this dies: the article classifies
    `cloudflare_challenge` instead of `other`, and the domain starts walking
    toward the headed tier."""
    import state

    visible = extract._strip_script_and_style(_DENIED_ARTICLE)
    assert len(visible) > extract.DEMAND_TEXT_MAX, (
        "precondition: past the cap, which is where whole_body_demands would "
        "otherwise carry the relaxed boundary")
    assert extract._TITLE_BOUNDARY_WITH_MOUNT != extract._TITLE_BOUNDARY, (
        "precondition: the two boundaries differ, so the choice between them "
        "is observable")
    cls = extract.classify_failure(200, _DENIED_ARTICLE, None)
    assert cls == "other", cls
    assert cls not in state.PERSISTENT_FAILURE_CLASSES, cls


def test_the_whitespace_title_boundary_still_applies_within_the_cap():
    """The companion, and the reason the guard above is about the CAP rather than
    about the exemption. Deleting _TITLE_BOUNDARY_WITH_MOUNT outright would
    satisfy the guard too; it would kill this. Same title, same mount, only the
    length differs, so the boundary choice is the only variable between them."""
    visible = extract._strip_script_and_style(_DENIED_SHORT)
    assert len(visible) <= extract.DEMAND_TEXT_MAX, "precondition: within the cap"
    assert len(_DENIED_SHORT.strip()) >= 200, (
        "precondition: `empty_content` must not answer first")
    assert extract.classify_failure(200, _DENIED_SHORT, None) == (
        "cloudflare_challenge")


def test_classify_failure_5xx():
    assert extract.classify_failure(503, "") == "origin_5xx_transient"


def test_classify_failure_timeout():
    assert extract.classify_failure(None, "", "TimeoutError") == "timeout"


def test_classify_failure_dns():
    assert extract.classify_failure(None, "Name or service not known") == "dns_fail"


def test_classify_failure_js_spa():
    assert extract.classify_failure(200, "Please enable javascript") == "js_only_spa"


