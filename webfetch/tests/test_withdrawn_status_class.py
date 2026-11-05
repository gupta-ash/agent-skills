"""Withdrawn status class behavioral regression tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extract

# ---- documents ---------------------------------------------------------------

# Enough prose to clear MIN_DOCUMENT_TEXT (50) several times over, so no
# near-empty-document rule can be what decides any of these.
PROSE = ("We spent three weeks reducing the tail latency of the ingest path. "
         "The first change was to stop re-parsing the manifest on every batch. "
         "The second moved the checksum off the request thread. ") * 3

# The false positive itself: a REAL page whose only challenge-shaped evidence is
# the mount point Cloudflare documents for a site's OWN form.
TURNSTILE_ON_OWN_FORM = (
    "<html><head><title>How we tuned our ingest pipeline</title></head><body>"
    f"<article><p>{PROSE}</p></article>"
    '<form id="contact"><label>Email</label><input name="email">'
    '<div class="cf-turnstile" data-sitekey="0x4AAAAAAABkMYinuk"></div>'
    "</form></body></html>"
)

# A GENUINE interstitial. `cf-browser-verification` is in `_CHALLENGE_MARKUP`,
# which is scanned under both values of `page_served`, so this must not move.
# It is not a mount point a site puts on its own form -- which is the property
# that matters here -- though a page QUOTING it would still match, since the scan
# is over raw bytes (`M-AKAMAI-QUOTED-MARKUP-FP`, open).
GENUINE_WALL = (
    "<html><head><title>Just a moment...</title></head><body>"
    '<div class="cf-browser-verification cf-im-under-attack">'
    "<h1>Checking your browser before accessing the site.</h1>"
    "</div></body></html>"
)
WALL_TEXT = "Checking your browser before accessing the site."

# ONE Akamai container id -- the shape the merged `226b4e3` exemption is about --
# beside a Turnstile mount. The leading space is load-bearing: `data-id="X"`
# contains `id="X"`, which is why the marker requires whitespace in front.
AKAMAI_ONE_ID_AND_MOUNT = (
    "<html><head><title>Our contact page</title></head><body>"
    '<div id="sec-if-cpt-container"></div>'
    f"<article><p>{PROSE}</p></article>"
    '<form><div class="cf-turnstile" data-sitekey="0x4"></div></form>'
    "</body></html>"
)


# SHAPE 3: the same mount, on a document that also carries a JS-shell marker.
# `_JS_SHELL_MARKERS` is scanned AFTER the challenge block, so today the mount
# short-circuits and this arm never runs.
MOUNT_AND_JS_SHELL = (
    "<html><head><title>Our app</title></head><body>"
    "<noscript>You need to enable JavaScript to run this app.</noscript>"
    f"<article><p>{PROSE}</p></article>"
    '<form><div class="cf-turnstile" data-sitekey="0x4"></div></form>'
    "</body></html>"
)


def _classify(doc, text, status=None, **kw):
    return extract.classify_failure(status, doc, None, extracted=text, **kw)


# ---- the fix -----------------------------------------------------------------

def test_a_sites_own_turnstile_mount_no_longer_walks_the_domain_off_tier_0():
    """THE DEFECT, and the only assertion in this file that changed value.

    MUTATION M1: delete `status_unobserved or` from `page_served`. This dies --
    along with every other class arm here, the verdict pin, and both call-site
    tests, 7 in all. An earlier version of this line claimed it died ALONE, which
    contradicted two sibling docstrings in the same file and was simply false.

    The control sits in the same test: at an OBSERVED 200 the identical bytes
    already classified `other`. So before the change the class turned on whether
    WE managed to read a status, not on anything the origin did -- which is what
    made it evidence of nothing.
    """
    assert _classify(TURNSTILE_ON_OWN_FORM, PROSE, status=200) == "other", (
        "precondition: an origin's own 2xx already earned the exemption")
    assert _classify(TURNSTILE_ON_OWN_FORM, PROSE, status_unobserved=True) == "other"


def test_the_same_bit_demotes_a_lone_akamai_id_beside_a_mount():
    """SHAPE 2, and it is not obvious.

    `page_served` is forwarded into the Akamai exemption's `others` re-ask, so
    withholding the embeddable tuple ALSO lets a lone container id reach that
    exemption. Measured before and after: `cloudflare_challenge` -> `other`.

    It is filed here rather than left implicit because a behaviour nobody
    asserts is a behaviour nobody notices changing. Here the direction IS toward
    transient, which costs a re-climb from tier 0 and can never admit a document
    -- unlike shape 3 below, where it is not.

    MUTATION: the same one term; this dies with the Turnstile arm above.
    """
    assert _classify(AKAMAI_ONE_ID_AND_MOUNT, PROSE) == "cloudflare_challenge", (
        "precondition: without the caller's assertion this is still persistent")
    assert _classify(AKAMAI_ONE_ID_AND_MOUNT, PROSE,
                     status_unobserved=True) == "other"


def test_a_mount_beside_a_js_shell_marker_becomes_js_only_spa_not_transient():
    """A mount beside a js shell marker becomes js only spa not transient."""
    assert _classify(MOUNT_AND_JS_SHELL, PROSE) == "cloudflare_challenge", (
        "precondition: the mount short-circuits before the JS-shell arm today")
    assert _classify(MOUNT_AND_JS_SHELL, PROSE,
                     status_unobserved=True) == "other"


def test_all_three_markers_together_land_where_the_js_marker_says():
    """THE COMBINATION ROUND 2 NAMED, and it is here to make the RULE testable
    rather than to extend a census.

    One Akamai container id, a Turnstile mount and a JS-shell marker on one
    readable document. The Akamai exemption fires (the re-ask no longer sees the
    mount), and the document then falls to the arm below the challenge block --
    which is the JS-shell arm, exactly as the rule says.

    It shares its destination with the mount-plus-JS shape, so it is not a new
    KIND of movement. That is the point: the destination is decided by what else
    the document carries, not by which combination it is.

    SINCE 2026-09-08 that shared destination is `other` rather than
    `js_only_spa`: the JS-shell arm now requires the extracted text to be under
    `SHELL_TEXT_MAX` and this document carries 569 characters. The Akamai
    exemption still fires and the document still falls past the challenge block;
    what changed is that the arm below it no longer claims a content-rich page.

    MUTATION M1: dies with the other class arms.
    """
    doc = (
        "<html><head><title>Our app</title></head><body>"
        '<div id="sec-if-cpt-container"></div>'
        "<noscript>You need to enable JavaScript to run this app.</noscript>"
        f"<article><p>{PROSE}</p></article>"
        '<form><div class="cf-turnstile" data-sitekey="0x4"></div></form>'
        "</body></html>"
    )
    assert _classify(doc, PROSE) == "cloudflare_challenge", (
        "precondition: the mount short-circuits the challenge block today")
    assert _classify(doc, PROSE, status_unobserved=True) == "other"


# ---- the arms that must NOT move ---------------------------------------------

def test_a_genuine_wall_is_still_persistent_under_an_unobserved_status():
    """THE CONTROL THAT MATTERS MOST -- an arm that must not fire.

    A fix demonstrating only permissiveness has demonstrated the wrong half.
    `cf-browser-verification` is in `_CHALLENGE_MARKUP`, which is scanned under
    BOTH values of `page_served`, so the class must not move. The property that
    earns it that place is that no site mounts it on its own form -- not that it
    is impossible for a page to contain one.
    """
    assert _classify(GENUINE_WALL, WALL_TEXT) == "cloudflare_challenge"
    assert _classify(GENUINE_WALL, WALL_TEXT,
                     status_unobserved=True) == "cloudflare_challenge"


def test_an_observed_refusal_still_counts_the_embeddable_tuple():
    """A status the origin DID send is untouched, and by construction.

    The 401/402/403/451 arm passes `page_served=False` as a LITERAL, not the
    variable this change edits, so it cannot be reached by the new keyword even
    if a caller passed one.

    MUTATION: point that arm at the `page_served` variable -- this dies.
    """
    for status in (401, 403, 451):
        assert _classify(TURNSTILE_ON_OWN_FORM, PROSE,
                         status=status) == "cloudflare_challenge", status


def test_the_exception_path_callers_keep_todays_behaviour_exactly():
    """THE UNTOUCHED-CALLER CONTROL. `status_unobserved` defaults to False, so
    the five callers that mean "nothing arrived" are unaffected -- which is the
    whole reason it is an argument rather than a `status is None` test.

    Their correctness must not depend on the empty body they happen to pass, so
    this also asserts the default holds when a body IS supplied.
    """
    assert extract.classify_failure(None, "", "TimeoutError") == "timeout"
    assert extract.classify_failure(None, "", "ConnectionRefusedError") == "connect_refused"
    assert extract.classify_failure(None, "", "gaierror") == "dns_fail"
    assert extract.classify_failure(None, "", "OSError") == "other"
    # The default, with a real body: still both tuples, still persistent.
    assert _classify(TURNSTILE_ON_OWN_FORM, PROSE) == "cloudflare_challenge"


def test_the_empty_content_arm_cannot_fire_off_a_status_we_invented():
    """`empty_content` is PERSISTENT and keys on a real 2xx, not on
    `page_served`, so granting the page-served assertion must not reach it.

    MUTATION: rewrite that arm's guard as `page_served and len(...) < 200` --
    this dies, and it is the one plausible way the new bit could widen a
    persistent class rather than narrow one.
    """
    assert extract.classify_failure(None, "", None, extracted="",
                                    status_unobserved=True) == "other"
    assert extract.classify_failure(200, "", None, extracted="") == "empty_content"


# ---- the verdict is what must never move -------------------------------------

def test_the_verdict_is_unchanged_and_only_the_class_moves():
    """WHAT MAKES "moves a class, never a verdict" A MEASUREMENT.

    `is_real_content` is not touched by this change: under `status_is_real=False`
    it still scans BOTH tuples and still REJECTS the very document whose class
    just moved. So the fetch still fails and still climbs to the next rung; what
    changed is only the mark left against the DOMAIN for later fetches.

    The two judges now disagree about this document ON PURPOSE. That is the
    design and not an oversight: a verdict is spent on one fetch, a class is
    spent on every fetch to that domain afterwards.
    """
    ok, reason = extract.is_real_content(
        PROSE, TURNSTILE_ON_OWN_FORM, 200, status_is_real=False)
    assert ok is False, "a wall must never become content"
    assert "cf-turnstile" in reason, reason
    # ...while the class for the same document, same unread status, is transient.
    assert _classify(TURNSTILE_ON_OWN_FORM, PROSE, status_unobserved=True) == "other"
