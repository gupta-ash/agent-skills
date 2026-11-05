"""extract.py - HTML extraction + content quality + failure classification.

Per project decision D8 (trafilatura is a hard prereq).
"""

from __future__ import annotations

import json
import html
import re
import sys
from collections.abc import Iterator
from html.parser import HTMLParser
from urllib.parse import SplitResult, parse_qs, urljoin, urlparse, urlsplit

try:
    import trafilatura
except ImportError:
    print(
        "webfetch: trafilatura is required but not installed. "
        "Install with: pip install --user trafilatura>=1.6",
        file=sys.stderr,
    )
    sys.exit(2)


# ---- Content extraction ------------------------------------------------------

def html_to_markdown(html: str, url: str | None = None) -> str:
    """Extract substantive markdown from raw HTML via trafilatura. Empty string on failure."""
    if not html:
        return ""
    try:
        out = trafilatura.extract(
            html, url=url,
            include_links=False,
            include_tables=True,
            no_fallback=False,
            output_format="markdown",
        )
    except Exception:  # trafilatura sometimes raises on weird input
        return ""
    return out or ""


# ---- Challenge / wall detection ----------------------------------------------
#
# Positive evidence that the origin served a challenge or a denial instead of the
# page. This replaces the length floor that used to stand here. Length never did
# that job: the openreview Turnstile page extracts 275 chars and the medium.com
# block page 461 chars of clean prose, and both cleared every length gate the
# file had. Measured over 24 captured responses with the status check disarmed
# (every fixture forced to 200): 11 walls and shells rejected on positive
# evidence, 13 real pages admitted, zero mismatches. All eleven still are as of
# 2026-08-30, but no longer all by the same arm: `class="cf-turnstile"` moved to
# _EMBEDDABLE_CHALLENGE_MARKUP below and stopped being evidence on a 2xx, which
# dropped the OpenReview capture -- an interstitial served at 200 that the markup
# arm alone was holding. The title arm now catches it; see
# _TITLE_BOUNDARY_WITH_MOUNT for the exemption that lets it, and why that
# exemption is gated rather than global.

# Markup rather than prose, so these are locale-independent. TWO tuples, because
# not every marker means the same thing: one set only ever appears on a document
# served INSTEAD of the page, the other can appear on a page the origin served
# normally. Which set applies is the caller's `page_served`.

# Only ever seen on an interstitial served in place of the page.
# Akamai Bot Manager's behavioural-challenge container, as 12 generated literals:
# two ids, two quote styles, three whitespace characters. GENERATED because a
# hand-typed list of twelve near-identical strings is where a transposition hides,
# and nothing would fail if one were wrong in a way that merely stopped matching.
#
# THE WHITESPACE PREFIX IS THE FALSE-POSITIVE GUARD. `_challenge_reason` tests
# `if m in h` over the whole raw document, so a bare `id="X"` also matches
# `data-id="X"` and `aria-id="X"` -- measured, and it rejected ordinary pages
# (round-1 review). Every `*-id=` attribute has a non-whitespace character before
# `id`, so requiring whitespace deletes that collision class.
#
# ALL THREE WHITESPACE CHARACTERS, because the comment that used to sit here said
# "real markup always has whitespace before an attribute" while the tuple required
# U+0020 specifically -- so a challenge served as `<div\nid="...">` matched nothing
# and would have come back as the page (round-2 review). Tab and newline are HTML
# whitespace; the code now covers what the sentence claims.
#
# THE MEASUREMENT, stated as what it is: across 34 real bodies and 10,536 `id=`
# occurrences, 0 were preceded by a tab or a newline and 31.9% by a space, while
# the collision characters this excludes (`-` for data-id/aria-id, and others)
# account for the rest. So the prefix had no MEASURED cost on that corpus -- which
# is not the same claim as "no cost", and an earlier version of this comment made
# the stronger one.
#
# KNOWN AND NOT SOLVED, the residual round 2 called must-fix: a document that
# QUOTES these ids -- an article about Akamai bot detection, an encoded code
# sample, an HTML comment -- still matches and is still rejected, because the test
# is a substring scan of the raw document rather than an inspection of parsed
# start-tag attributes. Two controls below pin that behaviour so it is explicit
# rather than latent. Closing it needs attribute-aware parsing of this arm for all
# seven markers, which is a wider change than this one; it is escalated, not
# silently absorbed.
#
# KNOWN LIMIT, other direction: `id = "x"` with spaces around the equals, `ID="x"`
# and an unquoted id match none of these. No observed Akamai response uses those
# forms, so they are deliberately not guarded.
_AKAMAI_CHALLENGE_IDS = ("sec-if-cpt-container", "sec-bc-tile-parent")

# ONE GENERATOR, TWO VIEWS, and the flat tuple is DERIVED from the grouped one so
# the two can never drift. `classify_failure` needs to count DISTINCT ids, which
# the flat tuple cannot express: it has no way to tell one id's six literals from
# the other's.
_AKAMAI_MARKUP_BY_ID = {
    name: tuple(ws + "id=" + q + name + q
                for q in ('"', "'")
                for ws in (" ", "\t", "\n"))
    for name in _AKAMAI_CHALLENGE_IDS
}
_AKAMAI_CHALLENGE_MARKUP = tuple(
    m for name in _AKAMAI_CHALLENGE_IDS for m in _AKAMAI_MARKUP_BY_ID[name]
)


_CHALLENGE_MARKUP = (
    "cf-browser-verification",
    # The `/h/` is load-bearing: Cloudflare Bot Fight Mode injects
    # `/cdn-cgi/challenge-platform/scripts/jsd/main.js` into ORDINARY pages, so
    # the bare prefix would fire on a page that was served normally. MEASURED
    # 2026-08-30, so this is an observed false positive and not merely a
    # documented risk: blog.crawlex.net/blog/cloudflare-turnstile-internals
    # serves that jsd/main.js on a plain HTTP 200 carrying 31,947 characters of
    # article text, and the bare prefix rejects it. No committed fixture carries
    # the injection, so the restriction is pinned by that measurement.
    "/cdn-cgi/challenge-platform/h/",
    "captcha-delivery.com",          # DataDome
    # Akamai Bot Manager's BEHAVIOURAL challenge -- the interactive one, served
    # AS the page at 200. Added 2026-09-05 after it came back from hilton.com/en/
    # through a real browser and this gate called it `other`: the ONLY thing
    # standing between 2.5 KB of challenge HTML and the caller was the 50-char
    # text floor (`near-empty document: 32 chars`), which a challenge padded past
    # 50 chars walks straight through. That is a silent wrong answer, not a
    # failed fetch, which is why it outranked the other leads.
    #
    # LEADING SPACE, AND IT IS LOAD-BEARING. Round-1 cross-family review found
    # the bare attribute form matched `data-id="sec-if-cpt-container"`, because
    # `_challenge_reason` tests `if m in h` over the whole raw document and
    # `data-id="X"` CONTAINS `id="X"`. Reproduced before fixing. Real markup
    # always has whitespace before an attribute, while `data-id=`, `aria-id=` and
    # every other `*-id=` has a non-space character there, so the space deletes
    # that entire collision class at no cost.
    #
    # WHAT THE SPACE DOES NOT BUY, stated because an earlier version of this
    # comment claimed it did and was WRONG: a document that QUOTES this markup --
    # an article about Akamai bot detection, a code sample, an HTML comment --
    # still matches and is still rejected. That is the exposure of EVERY member of
    # this tuple, it is the same false positive already documented above for
    # `/cdn-cgi/challenge-platform/`, and closing it needs attribute-aware parsing
    # of the arm for all members rather than a narrower literal here. Known and
    # accepted, not solved.
    #
    # KNOWN LIMIT, the other direction: `id = "x"` with spaces around the equals,
    # `ID="x"`, and an unquoted id match none of these. No observed Akamai
    # response uses any of those forms, so they are not guarded against.
    #
    # MEASURED over 33 bodies (14 committed fixtures + 19 captures, 2026-09-05):
    # both ids fire on the challenge and on nothing else.
    # REJECTED CANDIDATE, and the control test beside it is what keeps it out:
    # `/.well-known/sbsd/` is the script the challenge loads, and it ALSO rides
    # on ordinary hilton "Page Not Found" bodies -- Akamai injects it into pages
    # it is merely fronting, exactly as Cloudflare does with jsd/main.js.
) + _AKAMAI_CHALLENGE_MARKUP

# A SITE embeds these itself. `class="cf-turnstile"` is Cloudflare's documented
# mount point for a widget on the site's own form -- a contact form on an
# ordinary page -- so on a 2xx it is evidence of a form, not of a wall. Measured
# 2026-08-30: blog.crawlex.net/blog/cloudflare-turnstile-internals returns HTTP
# 200 with 31,947 characters of extracted article text and was rejected here for
# carrying one, then escalated toward a browser window (C-3/C-4). On a non-2xx
# the origin did not serve the page, so the marker is evidence again and both
# tuples are scanned.
_EMBEDDABLE_CHALLENGE_MARKUP = (
    'class="cf-turnstile"', "class='cf-turnstile'",
)

# Matched against the <title> element only, so an article ABOUT bot walls is not
# rejected for its own subject matter.
_CHALLENGE_TITLES = (
    "just a moment",
    "attention required! | cloudflare",
    "verifying your browser",
    "verify you are human",
    "access to this page has been denied",
    "access denied",
    "pardon our interruption",
    "checking your browser",
    "security check",
    "are you a robot",
    "bot verification",
    "you have been blocked",
)

# A title matches on equality, or on one of these IMMEDIATELY after the phrase --
# no whitespace is skipped. That is what keeps "Access Denied - Stack Overflow"
# out, since the next character there is a space. Equality alone would be wrong
# in the other direction: every captured Cloudflare wall is "Just a moment...".
_TITLE_BOUNDARY = ("...", "…", "|", "-", ".")

# ONE EXEMPTION, gated on THREE conditions together: a _CHALLENGE_TITLES phrase
# starting the title, an _EMBEDDABLE_CHALLENGE_MARKUP mount in the document, AND
# the text within DEMAND_TEXT_MAX. MEASURED 2026-08-30: the OpenReview capture is
# a real Cloudflare interstitial served at HTTP 200 titled "Verifying your
# browser | OpenReview" -- the phrase is in _CHALLENGE_TITLES and starts the
# title, but the next character is a SPACE, so the rule above lets it through.
# With `class="cf-turnstile"` no longer evidence on a 2xx, nothing else caught it
# and 275 chars of "Verifying your browser" came back as the page -- empty rather
# than erroring, since the cascade stops on ok=True.
#
# Widening _TITLE_BOUNDARY globally is the wrong repair: it newly rejects
# "Access Denied - Stack Overflow", which is the case the boundary rule exists
# for. Hence the mount condition -- Stack Overflow has no mount at all.
#
# THE LENGTH CONDITION IS THERE FOR classify_failure, AND ONLY IT. The first
# version of this exemption gated on the mount alone, on the argument that a long
# real article exceeds DEMAND_TEXT_MAX so the title arm is never consulted. That
# argument holds in is_real_content and is FALSE in classify_failure, which
# passes whole_body_demands=True and thereby lifts the cap OFF this arm.
# MEASURED 2026-08-30 on a 1,430-char real article carrying a contact-form
# Turnstile mount and titled "Access denied and what it means for your API
# integration": gated on the mount alone, classify_failure(200, ...) returned
# `cloudflare_challenge` -- a member of state.PERSISTENT_FAILURE_CLASSES, which
# walks the whole domain toward the headed tier and a window on the user's screen
# (C-3/C-4). Conjoined with `within_cap` it returns `other` (transient) again,
# and neither the OpenReview capture (275 extracted chars, inside the cap) nor
# the crawlex shape (31,947 chars, outside it and admitted) moves.
#
# RESIDUAL, STATED PLAINLY AND ACCEPTED -- the rule reaches wider than the three
# cases above. ANY short page whose title merely STARTS WITH a _CHALLENGE_TITLES
# phrase followed by whitespace is rejected once the site embeds a Turnstile
# widget anywhere on it, whatever the widget is for. MEASURED 2026-08-30: a
# Stack Overflow answer page of 186 extracted chars titled "Access Denied - Stack
# Overflow" is admitted as it stands, and rejected with the site's own contact
# form added and nothing else changed. The length condition does not cover this
# one -- that page is already inside the cap. It is a deliberate trade: a page
# carrying BOTH signals while staying that short is far likelier a wall than an
# article, and the alternative was leaving the OpenReview interstitial to come
# back empty. If a real page is ever measured losing to it, the mount condition
# is the half to tighten, not the boundary.
#
# A single space is the only whitespace that can reach here -- the title is
# `\s+`-collapsed and stripped before the comparison.
_TITLE_BOUNDARY_WITH_MOUNT = _TITLE_BOUNDARY + (" ",)

# A WALL IS ALL DEMAND. That is the third premise this arm has had, and the
# first two were each falsified by a finding:
#   "an article is longer"      -> a 323-char support note quoting the phrase
#                                  was rejected (RV-4).
#   "a wall LEADS with it"      -> 200 chars of filler ahead of the phrase
#                                  walked straight through (F4).
# Any position rule has an evasion of that second shape, so position is gone.
# The arm is now consulted only when the WHOLE extracted text is at or under
# SHELL_TEXT_MAX -- the bound the JS-shell arm already uses, so one threshold
# rather than two. The captured walls run 41-43 extracted chars; the support
# note is 323 and is admitted.
#
# RESIDUAL, STATED PLAINLY: a padded wall whose extracted text exceeds
# SHELL_TEXT_MAX is NOT caught by this arm. Lengthening or moving the bound
# does not fix that -- it only moves where the padding has to sit. What
# actually catches such a page is the markup or title arm, which is what
# catches every wall we have measured: over the 24 captures this arm fires on
# NONE of them by itself. Do not add a "contextual evidence" discriminator
# for it; there is no observed instance and it has not been approved.
_CLIENT_DEMANDS = (
    "enable javascript and cookies to continue",
    "please enable js",
    "enable javascript to continue",
    "verify you are human",
    "complete the security check",
    "complete the check below to continue",
    "unusual traffic from your computer",
    "you have been blocked",
    "this website is using a security service to protect itself",
    "checking your browser before access",
)

# Broad enough to catch a bundler-built shell whose only text is its <noscript>.
# `_script_byte_share` cannot see those: it counts bytes INSIDE <script> tags, and
# a shell referencing external bundles measures 0.166 -- far under the 0.90 that
# would reject it. Shared with classify_failure so the two cannot drift apart.
_JS_SHELL_MARKERS = ("enable javascript", "javascript is disabled")

# AN UNRENDERED-PAGE NOTICE, IN THE BAND WHERE THE SHELL ARMS DO NOT RUN.
#
# THE OPERATING BAND IS 200..600 EXTRACTED CHARACTERS, INCLUSIVE AT BOTH ENDS,
# and the lower end is 200 rather than 201 because the shell block is guarded
# by `len(text) < SHELL_TEXT_MAX` -- STRICT `<`, so at EXACTLY 200 that block
# is skipped and this arm is the only one left. Round 3 caught this comment
# understating the lower end by one character; the CODE was right and the
# sentence describing it was wrong. Pinned at both 199 and 200 by
# `test_the_band_includes_exactly_SHELL_TEXT_MAX`.
# Below 200 the shell arms answer first, by placement. Above 600 this arm
# deliberately does NOT fire, so a notice padded past the cap is MISSED --
# pinned by `test_a_notice_padded_past_the_bound_is_NOT_caught`.
#
# OTHER ARMS DO COVER PART OF THIS BAND, and an earlier version of this
# heading denied that. `_challenge_reason` runs BEFORE the shell block and
# rejects a document of ANY size through its markup arm, and up to
# DEMAND_TEXT_MAX through its title arm. What is true is narrower: the SHELL
# arms do not run here, which is why an unrendered notice got through.
#
# The notice signature must be complete: an article can mention a browser
# requirement without itself being a notice. A single phrase is insufficient;
# only the full distinct-phrase signature can reject otherwise valid content.
#
# THIS IS NOT THE REFUTED CORROBORATION SHAPE. Requiring a second Akamai
# container id was refuted because it SUPPRESSED a live detector and lost true
# positives. This arm can only ADD a rejection, and the reason is PLACEMENT
# ALONE: it runs after every other arm in `is_real_content`, so each of them
# has already returned its own verdict and its own reason before control can
# reach here. Structural, not a measurement that goes stale.
#
# NOT because 'nothing else fires above SHELL_TEXT_MAX' -- that sentence stood
# here until review round 3 and it is FALSE. `_challenge_reason` runs before
# the length-bounded block and rejects documents of any size through the
# markup arm, and through the title arm up to DEMAND_TEXT_MAX. Placement is
# the whole argument; the absence of earlier detections was never part of it.
#
# IT SCANS THE EXTRACTED READER TEXT, NEVER THE RAW DOCUMENT. Scanning raw
# markup is what put three earlier attempts into the served-versus-quoted trap;
# a page QUOTING interstitial markup does not put it into its extraction.
#
# RESIDUAL, STATED NOT FIXED: a page that quotes the WHOLE notice verbatim --
# an article about this very interstitial -- still matches. That is the quoted-
# content case `architecture.md` records as unanswerable without knowing what a
# browser would render. One of six constructed controls, none in the field.
#
# HONEST SCOPE: all four observations are the SAME body, so this closes an
# observed template, not a class. A publisher wording it differently still
# gets through until we see one and add it here.
#
# WHAT REJECTING COSTS, MEASURED RATHER THAN ASSERTED. For the OBSERVED
# notice `classify_failure` returns `other`, which is TRANSIENT, so no domain
# is promoted -- measured twice, with realistic HTML and with the raw text as
# the body, and the notice matches no `_CLIENT_DEMANDS` literal.
# THAT IS A FACT ABOUT THIS DOCUMENT, NOT A GUARANTEE ABOUT THE ARM, and an
# earlier version of this comment made the categorical claim.
#
# THE RESIDUAL, AND IT IS A FALSE PROMOTION RATHER THAN A CORRECT ONE. A
# 200..600 character ARTICLE that quotes all three notice phrases AND a
# `_CLIENT_DEMANDS` phrase is rejected by this arm and then classified
# `cloudflare_challenge`, which is PERSISTENT -- so a real page can walk its
# whole DOMAIN off tier 0. `classify_failure` reaches `_CLIENT_DEMANDS`
# UNCAPPED via `whole_body_demands=True`, which is the path that makes it
# reachable.
# An earlier version of this comment asserted that such a page must be a
# genuine challenge. IT NEED NOT BE. Nothing distinguishes a served
# challenge from an article
# quoting one, which is the same quoted-content limit recorded three times in
# `architecture.md`. Pinned by
# `test_a_quoted_article_with_a_demand_phrase_is_a_known_false_promotion`.
_UNRENDERED_NOTICE = (
    "javascript is disabled in your browser",
    "please enable javascript to proceed",
    "required part of this site",
)
# ALL OF THEM, NOT A SUBSET -- review round 3, and the evidence says so.
# Every one of the four observations carries all three phrases, so accepting
# any TWO invents a signature nobody has captured, and each invented
# combination is a terminal false rejection for a 200..600 character page that
# happens to quote two. Requiring all three keeps every measured positive and
# deletes the invented surface.
#
# DERIVED FROM THE TUPLE ON PURPOSE, and the footgun is worth naming: adding a
# phrase here RAISES the bar for the signature already proven, so a notice we
# currently catch would stop being caught. A differently-worded capture is a
# NEW SIGNATURE and needs its own tuple; it is not a fourth entry in this one.
_UNRENDERED_NOTICE_MIN = len(_UNRENDERED_NOTICE)


def _unrendered_notice_reason(text: str) -> str:
    """Return a reason when the extraction IS a needs-JavaScript notice.

    TWO CONDITIONS, AND NEITHER IS SUFFICIENT ALONE.
      * EVERY phrase in `_UNRENDERED_NOTICE` present -- the full captured
        signature, not a subset -- so a real page naming the condition once,
        or twice, in passing is untouched; and
      * the whole extraction at or under `DEMAND_TEXT_MAX`.

    THE CAP LIMITS EXPOSURE. IT DOES NOT ESTABLISH IDENTITY, and an earlier
    version of this docstring claimed it did -- that a document at or under
    600 characters carrying the phrases must BE the notice. It need not: a
    short support note or article can quote the whole signature and is then
    REJECTED AT EVERY TIER, because `is_real_content` is shared. That residual
    is not closed by anything here; the cap only stops it reaching a long
    document.

    WHAT SCANNING THE EXTRACTION BUYS, AND WHAT IT DOES NOT. It defeats a
    match caused by hidden raw markup: a page that QUOTES interstitial markup
    does not put that markup into its extracted text. It does NOT separate a
    served notice from visible prose quoting one, and an earlier version of
    this docstring called the arm "safe to run unbounded" on exactly that
    false basis. It is not safe unbounded, which is why the length condition
    above exists.

    THE BOUND IS WHAT KEEPS A QUOTATION CHEAP. `is_real_content` is shared by
    every tier, so a false rejection here is TERMINAL for the whole cascade
    rather than a climb to the next rung -- re-fetching through a browser hits
    the same predicate. The measured counterexample is a code host RENDERING
    this skill's own `tests/test_unrendered_notice.py`, whose visible source
    carries all three phrases; at many kilobytes it is far past the bound and
    cannot reach this arm.

    RESIDUAL, STATED NOT FIXED: a notice PADDED past DEMAND_TEXT_MAX is not
    caught, and a short page quoting the WHOLE signature still is. Both are the
    quoted-content case `architecture.md` records as unanswerable without
    knowing what a browser would render.
    """
    low = (text or "").lower()
    if len(low.strip()) > DEMAND_TEXT_MAX:
        return ""
    hits = [p for p in _UNRENDERED_NOTICE if p in low]
    if len(hits) < _UNRENDERED_NOTICE_MIN:
        return ""
    return ("unrendered-page notice returned as the document: "
            "%d phrases (%s)" % (len(hits), ", ".join(repr(h) for h in hits)))


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

DEMAND_TEXT_MAX = 600
SHELL_TEXT_MAX = 200
SHELL_SCRIPT_SHARE = 0.90

# NOT the 200-char floor coming back. That one was removed for cause: measured
# over the failure log it rejected real content of 209-975 chars from github,
# wikipedia and docs.claude.com. What removing it ALSO removed was any answer to
# a document that arrives empty -- measured 2026-08-30, docs.openclaw.ai/
# architecture passed this gate on 11 markdown chars, old.reddit.com on 20 and
# quotes.toscrape.com/js/ on 29, all three JavaScript-rendered, all three handed
# back to the agent as a success. That is "empty rather than erroring", which
# this project holds to be the worst outcome available: the agent gets nothing
# AND is told nothing is wrong.
#
# 50 is set by the measured margin on both sides, not by taste. The smallest
# REAL content in the phase-1 analysis was 209 chars, so the floor sits 4x below
# the smallest true positive and cannot reproduce the rejections that killed the
# 200; the largest false positive observed here was 29. example.com legitimately
# extracts 113 chars and must keep passing -- it is the canonical case both ways.
MIN_DOCUMENT_TEXT = 50


def _akamai_ids_in(h: str) -> tuple:
    """Which Akamai container ids appear in `h` IN MARKUP FORM.

    THE WHITESPACE GUARD IS INHERITED RATHER THAN RE-IMPLEMENTED: this consults
    the same generated literals the markup arm matches on, so `data-id="X"` no
    more counts here than it matches there. A hand-written needle would be a
    second copy of that rule, and one rule written out twice drifts.

    Used ONLY by `classify_failure`, to tell a lone container id from the pair a
    real behavioural challenge carries. It decides a failure CLASS and never a
    verdict -- nothing here can admit a document.
    """
    return tuple(name for name in _AKAMAI_CHALLENGE_IDS
                 if any(m in h for m in _AKAMAI_MARKUP_BY_ID[name]))


def _challenge_reason(html: str, text: str, *,
                      whole_body_demands: bool = False,
                      page_served: bool = False,
                      cap_text: str | None = None,
                      skip_akamai_ids: bool = False) -> str:
    """Return a reason string if the document is positively a wall, else ''.

    `text` is the EXTRACTED text, already stripped. The title arm and the
    client-demand arm are conjoined with DEMAND_TEXT_MAX; without that cap the
    title arm rejects ordinary pages ("Security Checklist | Node.js", "Access
    Denied - Stack Overflow") on every tier at once.

    `whole_body_demands` lifts the LENGTH gate off the title and demand arms.
    It exists for classify_failure and nothing else. The gate protects against
    an ARTICLE that merely mentions a challenge phrase being called a wall -- a
    real risk in is_real_content, which judges a SUCCESSFUL fetch, and no risk
    in classify_failure, which runs only on a fetch that has already failed.
    Passing the caller's body as `text` and leaving the gate on is what
    produced R2-1: a prose-only wall whose demand sat past the doctype and
    <head> was classified `other` (transient) where the uncapped scan says
    cloudflare_challenge (persistent).

    IT DOES NOT MEAN "SCAN THE RAW BYTES". Round 2 pointed the uncapped scan at
    `html`, so <script> and <style> contents counted as evidence and a page
    whose own application JavaScript contains a demand phrase became a
    persistent cloudflare_challenge -- promoting the domain toward the headed
    tier, which is browser windows on the user's machine (C-3/C-4). The
    haystack is now always `text`, and classify_failure passes the
    script/style-stripped body as `text`.

    `page_served` NO LONGER MEANS "the origin returned a 2xx", and the name is
    older than the meaning. What it does, operationally and exactly: it suppresses
    `_EMBEDDABLE_CHALLENGE_MARKUP` and nothing else. `_CHALLENGE_MARKUP` still
    counts, and so do the challenge-title and client-demand arms below -- a wall
    that announces itself in its title or its text is unaffected by it.

    TRUE FOR TWO DIFFERENT REASONS, and a reader has to know both. Either the
    origin really did send a 2xx, so a marker a site may embed on its own form
    proves nothing about a page the origin agreed to serve; or the caller could
    not read a status at all and says so with `classify_failure`'s
    `status_unobserved`. The second may CONCEAL a non-2xx -- see below -- so this
    is not a claim about what the origin did.

    Default False scans both tuples, which is what an OBSERVED non-2xx wants.

    A status we NEVER READ is not a non-2xx and must not be treated as one.
    What that establishes is narrow and the narrowness is the point: NO REFUSAL
    WAS OBSERVED. It is NOT that none happened -- a withdrawn status can hide a
    real 404, and this file's own callers say so. So the embeddable marker, which
    a site may mount on its own form, is not evidence of a refusal nobody saw.
    `classify_failure`'s `status_unobserved` passes True here for exactly that
    case. An absent status reaching this function any OTHER way still scans both
    tuples.
    """
    h = html or ""
    markers = (_CHALLENGE_MARKUP if page_served
               else _CHALLENGE_MARKUP + _EMBEDDABLE_CHALLENGE_MARKUP)
    for m in markers:
        if skip_akamai_ids and m in _AKAMAI_CHALLENGE_MARKUP:
            # `skip_akamai_ids` answers ONE question, asked by classify_failure:
            # would this document still be a challenge if the Akamai container
            # ids were not here? It defaults False, so every other caller -- and
            # every verdict this file returns -- is unaffected.
            continue
        if m in h:
            return f"challenge markup present: {m!r}"
    # `cap_text` exists because the two callers hand this function DIFFERENT
    # haystacks: is_real_content passes EXTRACTED text, classify_failure passes
    # script-stripped HTML. The boundary decision below is a judgement about how
    # much a reader sees, so measuring it on stripped markup made the two
    # disagree about the same document -- measured 2026-08-30, a 200 Turnstile
    # wall with 279 extracted chars and 1642 stripped chars was rejected by the
    # gate and classified transient `other` by the classifier. When a caller
    # knows the extracted text it passes it here, and both judgements then read
    # the same number.
    within_cap = len(cap_text if cap_text is not None else text) <= DEMAND_TEXT_MAX
    # The title arm must survive a long body under whole_body_demands. An early
    # `return ""` on the cap gated it away, and classify_failure only escaped
    # that because it used to pass body[:600] -- so len(text) > cap could never
    # be true there. Handing it the full body reopens that door.
    if within_cap or whole_body_demands:
        tm = _TITLE_RE.search(h)
        if tm:
            title = re.sub(r"\s+", " ", tm.group(1)).strip().lower()
            # `within_cap` is false ONLY when whole_body_demands lifted the
            # gate, i.e. in classify_failure. The relaxed boundary was measured
            # on short documents and must not follow the arm out past the cap;
            # see _TITLE_BOUNDARY_WITH_MOUNT for the article it misclassified
            # persistent when this read the mount alone.
            boundary = (_TITLE_BOUNDARY_WITH_MOUNT
                        if within_cap and any(m in h
                                              for m in _EMBEDDABLE_CHALLENGE_MARKUP)
                        else _TITLE_BOUNDARY)
            for t in _CHALLENGE_TITLES:
                if title == t or (title.startswith(t)
                                  and title[len(t):].startswith(boundary)):
                    return f"challenge page title: {title[:60]!r}"
    if whole_body_demands:
        # Uncapped by LENGTH only. The haystack is `text`, which the caller has
        # already stripped of <script> and <style>; pointing this at `h` is
        # what F5 filed.
        haystack, where = text.lower(), "body"
    elif len(text) <= SHELL_TEXT_MAX:
        haystack, where = text.lower(), "short body"
    else:
        return ""
    for d in _CLIENT_DEMANDS:
        if d in haystack:
            return f"client-demand phrase in {where}: {d!r}"
    return ""


# ---- Script ratio ------------------------------------------------------------

# Scripts we DON'T count toward the "JS-only SPA" ratio - they carry structured data.
_DATA_SCRIPT_EXCLUSIONS = (
    'type="application/ld+json"',
    "type='application/ld+json'",
    'id="__NEXT_DATA__"',
    "id='__NEXT_DATA__'",
    'type="application/json"',
    "type='application/json'",
)

# `\s*` before the closing `>`: `</script >` is legal HTML, and rejecting it
# left the block unbalanced -- after which the open-ended pass below swallowed
# every visible byte past the close.
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.IGNORECASE | re.DOTALL)
# Comments first: a phrase inside <!-- --> is not shown to anyone, and removing
# them also keeps the open-ended pass below from firing on a `<script` that is
# only mentioned inside one.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# After the balanced pass, anything still OPENING a raw-text element runs to the
# end of the document -- which is also what a browser does with an unclosed
# <script>: everything after it is script text, not content.
_OPEN_RAWTEXT_RE = re.compile(r"<(?:script|style)\b", re.IGNORECASE)


def _strip_script_and_style(html: str) -> str:
    """Drop <script> and <style> blocks. What is left is roughly what the origin
    SHOWED, which is the only thing a client-demand phrase can be evidence of.

    SCOPED TO THE DEMAND AND JS-SHELL SCANS, and it must stay that way. The
    markup arm CANNOT run on the output of this: measured on the captured
    walls, `/cdn-cgi/challenge-platform/h/` appears INSIDE the script body
    (`a.src = '/cdn-cgi/challenge-platform/h/...'`), not in an attribute of an
    unstripped tag. Stripping would delete the evidence that catches 4 of the
    11 rejected captures.

    TWO PASSES, because the balanced one alone is evadable. A truncated
    response or malformed markup leaves an UNCLOSED <script> whose contents
    then stayed in the haystack -- and a demand phrase in there yields a
    PERSISTENT challenge class, which promotes the domain toward the headed
    tier (C-3/C-4). So a second pass removes any surviving opening tag to the
    end of the document. That is not an approximation of browser behaviour, it
    IS browser behaviour: an unclosed <script> swallows the rest of the
    document as script text.

    THE OPEN-ENDED PASS IS TAG-SCOPED, not a bare substring match. `<script`
    appears inside comments and inside attribute values, and truncating there
    deleted the VISIBLE text that followed -- so a real demand phrase went
    unseen and the failure classified transient, the exact inversion of what
    this stripping exists to prevent. `_at_tag_start` asks whether the nearest
    preceding angle bracket is `>` rather than `<`: inside a tag it is `<`, in
    ordinary content it is `>`. Three lines, no tokenizer.

    ACCEPTED RESIDUAL, asserted in the tests rather than left latent: literal
    `<script>...</script>` text displayed inside a <textarea> or <pre> is still
    erased, because it IS at a tag start. That is the OPPOSITE error -- it
    removes evidence rather than inventing it, so its failure mode is a missed
    wall rather than a false promotion, and no captured page exhibits it.
    """
    out = _COMMENT_RE.sub(" ", html or "")
    out = _STYLE_RE.sub(" ", _SCRIPT_RE.sub(" ", out))
    return _truncate_at_unclosed_rawtext(out)


def _at_tag_start(text: str, i: int) -> bool:
    """True when `i` (the index of a `<`) begins a tag rather than sitting
    inside one. Scanning back, the nearest angle bracket must be `>`."""
    return text.rfind(">", 0, i) >= text.rfind("<", 0, i)


def _truncate_at_unclosed_rawtext(text: str) -> str:
    """Cut at the first UNCLOSED <script>/<style> that really opens a tag.

    Balanced blocks are already gone, so anything left is unclosed. Occurrences
    inside an attribute value are skipped rather than truncated on -- that is
    the false positive the round-4 review found.
    """
    for m in _OPEN_RAWTEXT_RE.finditer(text):
        if _at_tag_start(text, m.start()):
            return text[:m.start()]
    return text


def _script_byte_share(html: str) -> float:
    """Return fraction of html bytes inside <script> tags, excluding data scripts."""
    if not html:
        return 0.0
    total = len(html)
    script_bytes = 0
    for m in _SCRIPT_RE.finditer(html):
        chunk = m.group(0)
        # Skip data-bearing scripts; they're content, not boilerplate JS.
        if any(excl in chunk for excl in _DATA_SCRIPT_EXCLUSIONS):
            continue
        script_bytes += len(chunk)
    return script_bytes / max(1, total)


# ---- Real-content heuristic --------------------------------------------------

def is_real_content(extracted: str, html: str, status: int,
                    response_headers: dict[str, str] | None = None,
                    *, status_is_real: bool = True) -> tuple[bool, str]:
    """Return (is_real, reason). is_real=False means cascade should escalate.

    The question this answers is "did the origin serve the page, and did it
    render" -- neither of which is a length question. The 200-char floor, the
    extracted/html ratio gate and the prose-paragraph gate are gone: measured
    over the failure log they rejected 209-975 real chars from github, wikipedia
    and docs.claude.com while catching no interstitial that positive evidence
    does not also catch. The ratio gate was worse than a weak proxy -- it was
    anti-correlated with its target, because interstitials are small documents
    with HIGH ratios.

    `response_headers` is unused, and kept only so callers need no edit. The
    Cloudflare header signals it used to feed measured worthless in both
    directions.

    `status_is_real=False` says the caller MANUFACTURED this status rather than
    reading it off the response, so it is evidence of nothing and both marker
    tuples are scanned -- the embeddable-marker exemption is granted only on a
    2xx the ORIGIN actually sent.
    """
    if status < 200 or status >= 300:
        return False, f"non-2xx status: {status}"
    text = (extracted or "").strip()
    # A 2xx by construction -- the non-2xx return above is the only other exit --
    # so the embeddable markers are not evidence here, PROVIDED the status was
    # read off a response. A fabricated one claims nothing and buys no exemption.
    reason = _challenge_reason(html or "", text, page_served=status_is_real)
    if reason:
        return False, reason
    if not text:
        return False, "extractor produced no text (0 chars)"
    if len(text) < SHELL_TEXT_MAX:
        share = _script_byte_share(html or "")
        if share >= SHELL_SCRIPT_SHARE:
            return False, (f"unrendered script shell: {len(text)} chars with "
                           f"{share:.2f} script byte share")
        tl = text.lower()
        for m in _JS_SHELL_MARKERS:
            if m in tl:
                return False, (f"unrendered script shell: {len(text)} chars whose only "
                               f"content demands JS ({m!r})")
        # Last, so the two arms above keep their more specific reasons wherever
        # they apply. This one answers the document that says nothing at all and
        # names no reason of its own.
        if len(text) < MIN_DOCUMENT_TEXT:
            return False, (f"near-empty document: {len(text)} chars, under the "
                           f"{MIN_DOCUMENT_TEXT}-char floor")
    # LAST, AND THE POSITION IS THE POINT -- review round 2, finding 4.
    #
    # This arm sat ABOVE the block for one commit, and there it STOLE the
    # reason from the shell arm on a short two-phrase input: measured on 76
    # characters, `main` reported "unrendered script shell: 76 chars whose only
    # content demands JS" and the branch reported the notice reason instead.
    # Same verdict, different reason, so the claim that this arm "can only ADD
    # detections" was false where it mattered. The 24-capture differential
    # missed it because no capture has that shape.
    #
    # Down here every arm above has already returned, so no existing verdict
    # and no existing reason can change. The claim is now true BY CONSTRUCTION
    # rather than by a measurement that a new fixture could falsify.
    #
    # WHAT IT COSTS: a document under SHELL_TEXT_MAX that carries two notice
    # phrases is now answered by the shell arm above, which is the better
    # reason for it anyway. This arm exists for the band those checks cannot
    # see -- SHELL_TEXT_MAX..DEMAND_TEXT_MAX -- which is where all four
    # observed instances sit, at 286 characters.
    notice = _unrendered_notice_reason(text)
    if notice:
        return False, notice
    return True, ""


# ---- Failure classification --------------------------------------------------

def classify_failure(status: int | None, body: str | None,
                     exception_type: str | None = None, *,
                     extracted: str | None = None,
                     status_unobserved: bool = False) -> str:
    """Map a fetch outcome to a failure_class for blocklist threshold logic (D10).

    `body` MUST BE THE WHOLE DOCUMENT. Callers used to pass html[:4096] and
    that starved every arm: a 200 shell with more than 4 KiB of CSS ahead of
    its <noscript>You need to enable JavaScript</noscript> is rejected by
    is_real_content, which sees the extracted marker, while this returned
    `other` instead of js_only_spa -- so the domain never reached the
    persistent promotion the shared marker tuple was supposed to guarantee.
    Challenge markup, the <title> match and the client-demand scan were all
    confined to the same 4 KiB.

    Cost of honouring this, measured 2026-08-30 on the captured corpus: 28 ms
    on the largest real page (edX, 1.5 MB) and 83 ms on a synthetic 10 MB body
    where no arm matches, against 0.03 ms truncated. It runs only on a fetch
    that has ALREADY failed, so it is measured against seconds already spent.
    Do not reintroduce a cap to save it.
    """
    et = (exception_type or "").lower()
    body_l = (body or "").lower()
    # F5. Scripts and styles are not what the origin showed the user, and this
    # function's output is the PROMOTION SIGNAL, not a label: `other` is
    # transient and `cloudflare_challenge` is persistent, so a phrase inside a
    # site's own JavaScript could walk a domain toward the headed tier. The
    # markup arm inside _challenge_reason still sees the RAW body, deliberately
    # -- see _strip_script_and_style for why stripping would blind it.
    visible = _strip_script_and_style(body or "")
    # THE JS-SHELL HAYSTACK IS THE READER'S TEXT AS CLOSELY AS THIS FILE CAN
    # GET IT -- NOT the markup around it, and NOT a guarantee.
    #
    # TWO MEASURED LIMITS, because `_visible_text` is a `<[^>]*>` substitution
    # and not an HTML text extractor, and an earlier version of this comment
    # claimed the reader's text outright:
    #   * a `>` INSIDE A QUOTED ATTRIBUTE VALUE ends the match early, so the
    #     rest of that attribute leaks into the haystack. Measured:
    #     `<p data-x="a > enable javascript">Short note.</p>` yields
    #     `N enable javascript">Short note.` and classifies `js_only_spa`,
    #     where the same document without the `>` yields `N Short note.`
    #     and classifies `other`.
    #   * an ENTITY-ENCODED marker is not decoded, so `enable&#32;javascript`
    #     is missed. Measured, returned `other`.
    # BOTH ARE FIXED, and this paragraph is now the MOTIVE for the fix rather
    # than a live disclosure -- it read as a live disclosure for one commit
    # after the repair landed, which review round 3 correctly called a comment
    # describing behaviour the code no longer has.
    #
    # The arm below uses `_reader_text_for_markers`, which is CALLER-LOCAL:
    # quote-aware tag stripping, the tag-open rule, and `html.unescape`.
    # `_visible_text` keeps its behaviour and keeps its other caller,
    # `_document_refuses_with_a_challenge`, which asks a LENGTH question where
    # an over-long answer is the safe direction. Round 1 of review declined
    # this repair because that helper is shared; round 2 answered with
    # CALLER-LOCAL, which is why it was then built.
    #
    # Until 2026-09-08 the arm below matched against `visible.lower()` --
    # script/style/comment-stripped markup with every tag name and attribute
    # value still in it. MEASURED: a marker appearing only inside
    # `<p title="enable javascript">`, which no reader can ever see, returned
    # `js_only_spa` and walked the domain off tier 0.
    #
    # THE HELPER BELOW IS `_reader_text_for_markers`, WHICH IS NEW AND HAS ITS
    # OWN DIVERGENCE SURFACE. An earlier version of this comment named
    # `_visible_text` and claimed 'no new needle and no new divergence surface',
    # which review round 4 correctly called a behavioural-comment mismatch: that
    # helper was replaced here in round 2 precisely BECAUSE it diverges.
    # What the new one owns, and what it therefore has to get right: the
    # tag-open rule, quote state inside a tag, and entity decoding. Three
    # divergences have already been found and fixed in it and one is filed open
    # (`M-MARKER-HAYSTACK-RAWTEXT-DECODE`). It is NOT a parser and does not
    # claim to be.
    # `visible` itself stays RAW-ish for the `_challenge_reason` calls below --
    # they want the markup, deliberately.
    visible_text_l = _reader_text_for_markers(visible).lower()
    # Network-layer transients first
    if "timeout" in et:
        return "timeout"
    if "connectionrefused" in et or "connection refused" in et:
        return "connect_refused"
    if "gaierror" in et or "name or service not known" in body_l or "name resolution" in body_l:
        return "dns_fail"
    # HTTP layer
    if status is not None:
        if 500 <= status < 600:
            return "origin_5xx_transient"
        if status in (401, 402, 403, 451):
            # Distinguish a challenge from an origin-side block
            # page_served=False: the origin refused, so a document carrying a
            # Turnstile mount is an interstitial, not someone's contact form.
            # Both tuples are evidence here -- the unchanged behaviour.
            if _challenge_reason(body or "", visible, cap_text=extracted, whole_body_demands=True,
                                 page_served=False):
                return "cloudflare_challenge"
            return "origin_4xx_block"
        if status in (404, 410):
            return "origin_4xx_block"
        # A CLASS IN NEITHER PROMOTION SET, deliberately. `record_failure`
        # tests membership of PERSISTENT_FAILURE_CLASSES and nothing else, so a
        # new name is transient at no cost; TRANSIENT_FAILURE_CLASSES is
        # declaration-only and read nowhere (verified 2026-09-03: one grep hit
        # across the skill, its own definition at state.py:35). Adding it there
        # would be cosmetic, and spec S4.21 forbids rewriting the blocklist.
        #
        # As `origin_4xx_block` this walked a domain a rung toward the tiers the
        # user can SEE on three consecutive rate limits, and no window clears a
        # rate limit. `escalation_verdict` ends the cascade on the matching
        # RATE_LIMITED verdict (spec S3.6); this is the blocklist half of the
        # same fact, and the two are independent -- the verdict is computed only
        # at tier >= 1, this runs wherever a status is known.
        if status == 429:
            return "rate_limited"
    # Body-only signals. A DataDome or Turnstile wall is logged under the
    # Cloudflare-named class; renaming it would change blocklist promotion keys.
    # Reachable WITH a 2xx -- a 200 that failed the content gate lands here -- so
    # this is the same false positive in its second home, and the more expensive
    # one: the class it returns is PERSISTENT, which walks the whole domain
    # toward the headed tier and a window on the user's screen (C-3/C-4). A
    # contact form must not do that.
    #
    # AN ABSENT STATUS IS TWO DIFFERENT FACTS AND `status=None` CANNOT TELL THEM
    # APART, which is why the caller says which one it means.
    #   `status_unobserved=True`  -- a response ARRIVED and we could not read its
    #     status.  The two browser tiers say this when their capture route was
    #     unavailable.  A document is in hand and NO REFUSAL WAS OBSERVED, which
    #     is not the same as none happening: a status we failed to read can have
    #     been a real 404.  So this is a deliberate false-negative trade -- on a
    #     document the origin really did refuse, a mount that IS a wall now goes
    #     unrecorded as one.  It is taken because the evidence is a marker a SITE
    #     may put on its own contact form, and the cost of believing it is the
    #     whole DOMAIN's tier 0, and tier 0 is the only tier that reads a PDF or
    #     returns a JSON body verbatim -- so a wrong promotion changes what a
    #     LATER fetch RETURNS, not merely how long it takes.
    #     WHAT THIS ACTUALLY WITHHOLDS is one of the two MARKUP tuples and
    #     nothing else: `_CHALLENGE_MARKUP` still counts, and so do
    #     `_challenge_reason`'s challenge-title and client-demand arms. A wall
    #     that announces itself in its title or its text is unaffected by this.
    #   the default, False -- no response arrived at all (the five exception-path
    #     callers in tiers.py).  Both tuples still count there, unchanged, and it
    #     is inert in practice because those callers pass an empty body.
    # why-not derive this from `status is None` and add no argument: the five
    # callers above would silently change meaning too, and their correctness would
    # then rest on that empty body -- an accident at call sites this function does
    # not control.
    # Set only where the Akamai exemption below actually fires. It stops the
    # raw-byte `empty_content` arm relabelling a document this function has just
    # established HAS readable text; it can never widen anything.
    akamai_exempt = False
    page_served = status_unobserved or (status is not None and 200 <= status < 300)
    if _challenge_reason(body or "", visible, cap_text=extracted,
                         whole_body_demands=True, page_served=page_served):
        # ONE EXEMPTION, AND IT MOVES A CLASS RATHER THAN A VERDICT. A LONE
        # Akamai container id, with no second id and no other challenge evidence
        # in the document, no longer makes this class PERSISTENT.
        #
        # WHY. The markup arm is a substring scan of the raw document, so it
        # cannot tell markup the ORIGIN SERVED from markup a page DISPLAYS: an
        # article about Akamai bot detection matches it. `is_real_content` still
        # rejects that article -- that behaviour is UNCHANGED and deliberately so,
        # because the alternative was measured to admit a padded one-id wall as
        # the page, and a wall returned as content outranks every false positive
        # here. What this removes is the SECOND, larger harm the comment above
        # describes: a persistent class walks the whole DOMAIN off tier 0, the
        # only tier that reads a PDF or returns a JSON body verbatim, and toward
        # the tiers that put a window on the user's screen.
        #
        # STATED AGAINST `state.py` RATHER THAN FROM MEMORY, because round 2 of
        # review caught the earlier wording overclaiming here. `record_failure`
        # counts CONSECUTIVE SAME-CLASS failures from the tail of
        # `recent_failures`, in CASCADES rather than tier attempts, and promotes
        # at `PROMOTE_THRESHOLD`. So returning a different class does not merely
        # "fail to promote" -- it BREAKS AN EXISTING STREAK, which is the larger
        # effect and the intended one for a domain whose article we keep
        # misreading. Nor is promotion permanent: `record_success` demotes a
        # domain after five successes.
        #
        # WHY A LONE ID IS THE LINE. Both real Akamai challenges in this
        # project's corpus carry BOTH container ids; a page that merely quotes
        # one carries one. That is evidence about the class, not proof about the
        # verdict, which is exactly the weight this exemption gives it.
        #
        # WHAT IT CANNOT DO, and all three conjuncts below are load-bearing.
        #
        # It cannot fire beside any OTHER challenge evidence -- a Cloudflare or
        # DataDome marker, a challenge title, a client demand -- because
        # `skip_akamai_ids=True` re-asks the whole question without the container
        # ids, and any surviving reason keeps the persistent class.
        #
        # It cannot fire on a document with NOTHING TO READ. The exemption's
        # premise is that this looks like a PAGE, and a wall does not: the
        # captured one is 2,499 bytes that extract 32 characters. Round 2 of
        # review caught this and it was REAL IN BOTH DIRECTIONS -- the arms below
        # key `empty_content` on RAW BYTES, so a realistic textless wall was
        # demoted to a transient class while a SHORT article stayed persistent,
        # the boundary exactly reversed.
        #
        # THIS IS NOT THE REFUTED LENGTH SIGNAL, and the difference is the whole
        # reason it is safe here. That one decided a VERDICT: it suppressed a
        # marker, so text hidden with `display:none` inflated the measure and a
        # WALL WAS ADMITTED as the page. This decides only a CLASS, and inflation
        # pushes toward the TRANSIENT side -- an unpromoted domain re-climbs from
        # tier 0 and pays latency. Nothing here can admit a document.
        #
        # `extracted` is the same measure `is_real_content` judges on, so the two
        # cannot disagree about one document. When the caller did not supply it we
        # have no evidence of readability and DO NOT DEMOTE: absence of evidence
        # is not evidence.
        #
        # SO THE EXEMPTION REACHES ONLY THE PATHS THAT EXTRACT, and round 2 of
        # review caught the earlier wording claiming otherwise. `tiers.py:902`
        # passes a REAL body -- `e.read(2048)` off an HTTPError -- and no
        # `extracted`, so a fall-through status like 400 carrying one container id
        # keeps `cloudflare_challenge` there. That is `main`'s classification, so
        # the path is unharmed rather than repaired, and the fail-safe is doing
        # exactly what it says.
        #
        # why-not extract on that path so the exemption reaches it: it would run
        # an extractor over a truncated error body to change a label, which is a
        # behaviour change on every 4xx this branch was not approved to touch.
        # Filed rather than built.
        others = _challenge_reason(body or "", visible, cap_text=extracted,
                                   whole_body_demands=True,
                                   page_served=page_served,
                                   skip_akamai_ids=True)
        if (others
                or len(_akamai_ids_in(body or "")) != 1
                or extracted is None
                or len(extracted.strip()) < MIN_DOCUMENT_TEXT):
            return "cloudflare_challenge"
        akamai_exempt = True
    # TWO CONJUNCTS, AND ONLY THE MARKER USED TO BE HERE.
    #
    # The bound is the second half. `is_real_content` has always treated these
    # same two markers as shell evidence only below SHELL_TEXT_MAX; this arm
    # applied no bound at all, so the two disagreed about one document by
    # accident rather than by design. MEASURED on unmodified `main`: 569
    # extracted characters of article prose beside a plain `<noscript>` warning
    # returned `js_only_spa`, which is PERSISTENT -- three such cascades walk
    # the whole DOMAIN off tier 0, the only tier that reads a PDF or returns a
    # JSON body verbatim. That is content loss, not latency.
    #
    # THE QUANTITY AND THE OPERATOR ARE `is_real_content`'s, deliberately:
    # `len(extracted.strip()) < SHELL_TEXT_MAX`, strict `<`. The file's two
    # other SHELL_TEXT_MAX sites use `<=` and bound DIFFERENT quantities -- the
    # `_challenge_reason` text param, unreachable from here because all three
    # calls above pass `whole_body_demands=True`, and the visible text of the
    # raw body in `_document_refuses_with_a_challenge`. Matching the sibling
    # that judges these same markers is the point; matching an unrelated site's
    # operator is not.
    #
    # `extracted is None` KEEPS THE ARM FIRING -- absence of evidence is not
    # evidence of readability, the same rule the Akamai exemption states four
    # lines above. why-not fall back to measuring `visible` when the caller
    # supplied nothing: MEASURED, the stripped markup of a genuine 279-byte
    # shell runs 223 characters against a 200 bound, so the fallback loses a
    # true positive to buy a convenient answer. Exactly one caller is governed
    # by this branch -- `tiers.py:902`, a truncated 2 KiB error body off an
    # HTTPError with no extraction available on that path.
    #
    # ACCEPTED RESIDUAL, DISCLOSED: a GENUINE unrendered shell padded past the
    # bound with navigation furniture stops being detected -- measured at 358
    # extracted chars, where it now returns `other` instead of `js_only_spa`.
    # Review round 1 rated this high and recommended admitting structural shell
    # evidence regardless of length. NOT BUILDABLE from this file: the only
    # structural signal is `_script_byte_share`, and MEASURED it reads 0.168 on
    # that padded shell against 0.000 on a real article -- both far under the
    # 0.90 threshold, so nothing separates them without fitting a new constant
    # to two constructed documents (n=2, the move refuted for the Akamai
    # corroborator). An app-shell detector would be new machinery for a signal
    # no consumer reads.
    # WHAT IS ACCEPTED HERE IS LATENCY, NOT CONTENT -- and the scope of that
    # sentence is load-bearing, because review round 3 read an earlier version
    # of it as a claim about padded shells GENERALLY and was right to.
    # ON THE PATH THIS ARM CONTROLS (a non-2xx that falls through to the
    # classifier) the shell still fails at tier 0 and still climbs every rung
    # inside the same fetch, because the cascade consults no failure class; it
    # merely stops promoting its domain as fast. That is latency.
    # THERE IS A SEPARATE, WORSE PATH AND IT IS NOT THIS ARM'S: at status 200
    # `is_real_content` returns (True, "") for that same padded shell, because
    # its own shell checks sit under `len(text) < SHELL_TEXT_MAX`, so tier 0
    # reports SUCCESS with navigation chrome as the content and the classifier
    # is never called at all. MEASURED on unmodified `main` @ 7ebcf9b: ok=True,
    # reason=''. PRE-EXISTING, untouched by this branch, and filed as
    # `M-PADDED-SHELL-ACCEPTED-AS-CONTENT`. Do not read the latency sentence
    # above as covering it.
    # What the bound REMOVES is content loss -- a content-rich page wrongly
    # promoted loses tier 0, the only tier that reads a PDF or returns JSON
    # verbatim. Priority one is that the fetch succeeds, so the trade runs the
    # right way. Pinned in `test_jsshell_arm_bound.py`.
    #
    # NOT GUARDED ON `akamai_exempt`, unlike the `empty_content` arm below, and
    # that asymmetry is deliberate. The exemption says the lone Akamai id is not
    # evidence of a CHALLENGE; it says nothing about whether the page rendered.
    # With the bound in place the only exempt document that can still reach here
    # carries 50..199 extracted chars, where `js_only_spa` is the better answer
    # anyway. Suppressing a detector on unrelated evidence is the shape refuted
    # three times in `architecture.md`.
    if (any(m in visible_text_l for m in _JS_SHELL_MARKERS)
            and (extracted is None
                 or len(extracted.strip()) < SHELL_TEXT_MAX)):
        return "js_only_spa"
    if (not akamai_exempt
            and status is not None and 200 <= status < 300
            and len((body or "").strip()) < 200):
        return "empty_content"
    return "other"



# Vendor PRESENCE is never sufficient for anything. Measured 2026-08-30:
# `cf-ray`, `server: cloudflare`, `__cf_bm`, `x-cdn: Imperva` and `akamai-grn`
# all appear on ordinary 200s, and `cf-ray`/`server: cloudflare` on four genuine
# auth responses. Presence is only ever a PRECONDITION of an affirmative marker.
_PX_COOKIES = frozenset({"_px", "_px2", "_px3", "_pxvid", "_pxhd", "_pxde", "pxcts"})
_AKAMAI_COOKIES = frozenset({"_abck", "bm_sz", "ak_bmsc", "bm_sv"})

_URL_TOKEN_RE = re.compile(r"""[^\s"'`<>]+""")

# THE ESCAPED AMPERSAND, AND DELIBERATELY NOTHING ELSE (R1). Tier 1 hands this
# module `document.documentElement.outerHTML`, and the HTML serialiser writes a
# `&` in an ATTRIBUTE VALUE as `&amp;`. So `?u=1&a=b` arrives as `?u=1&amp;a=b`,
# `parse_qs` splits on `&` alone, and every parameter after the first becomes a
# key named `amp;a`. MEASURED on the committed tree: with `a` anywhere but first,
# `a=b` was INVISIBLE, so the terminal arm's HARD_BLOCK silently became CONTINUE
# and a response that used to STOP the cascade climbed it toward a window
# instead (C-3/C-4). The window arm lost `a=c` the same way, one fetch.
#
# `html.unescape` IS THE WRONG TOOL AND WOULD REOPEN THE SAME DEFECT. It
# implements the in-TEXT algorithm, which expands the legacy HTML4 named
# references even without their semicolon; the ATTRIBUTE-VALUE rule a browser
# uses does not, precisely so that query strings survive. MEASURED 2026-09-03:
# `html.unescape("?a=c&copy=1")` -> `?a=c(c)=1`, `"?a=b&times=2"` -> `?a=b(x)=2`,
# `"?a=b&reg=1"` -> `?a=b(R)=1`. Each swallows the separator in front of a real
# parameter, which is R1 again wearing a new coat.
#
# The ampersand is also the ONLY escape that can change any answer here: the
# three comparisons are a HOST, a PATH tail and the `a` PARAMETER, and no other
# escape moves where a query splits. The SEMICOLON IS REQUIRED for the same
# reason -- `&amp` followed by a letter is an ambiguous ampersand, which a
# browser leaves alone inside an attribute value.
_ESCAPED_AMP_RE = re.compile(r"&(?:amp|AMP|#0*38|#[Xx]0*26);")


def _candidate_urls(body: str, *, containing: str) -> Iterator[SplitResult]:
    """Every delimited token of `body` that CONTAINS `containing`, parsed.

    `containing` is a lowercase PREFILTER and never a decision: it is here so a
    megabyte of markup is not parsed token by token, and the caller still
    compares whole values on everything it gets back. IT HAS NO DEFAULT, for the
    reason `segment_bounded` below has none -- a caller whose prefilter is not a
    substring of every URL it can accept loses candidates silently.

    `urlsplit`, NOT `urlparse`: `urlparse` splits a `;` suffix off the last path
    segment into `params`, so `.../c.js;anything` would compare EQUAL to `/c.js`.
    MEASURED 2026-09-03.

    A token that will not parse is skipped. `urlsplit` raises ValueError on a
    malformed bracketed host -- `//[::1`, `//[bad]host/` -- which ordinary page
    text can produce, and an unhandled raise here would fail the whole fetch.
    """
    for m in _URL_TOKEN_RE.finditer(body or ""):
        token = m.group(0)
        if containing not in token.lower():
            continue
        try:
            yield urlsplit(_ESCAPED_AMP_RE.sub("&", token))
        except ValueError:
            continue


# The PATH TAIL each arm compares. Both are read off the parsed URL, so the
# leading `/` is a SEGMENT boundary rather than a character in a text scan:
# `/assets/js/recaptcha.js` ends with `captcha.js` and does NOT end with
# `/captcha.js`, which is the whole of the split below.
_PX_CAPTCHA_PATH = "captcha.js"
_PX_CAPTCHA_PATH_BOUNDED = "/captcha.js"


def _px_captcha_actions(body: str, *, segment_bounded: bool) -> frozenset[str]:
    """Every COMPLETE `a` value on a PerimeterX `captcha.js` URL in `body`.

    `segment_bounded` picks the tail: True requires `captcha.js` to BEGIN a path
    segment, False accepts any path ending in it. IT HAS NO DEFAULT,
    deliberately -- which one a call site wants IS its safety decision (see the
    comment above), so a new caller has to state it rather than inherit whichever
    was written first. That inheritance is the exact defect this parameter exists
    to prevent.

    The path compares case-INsensitively, as the two `re.I` needles it replaces
    did. Lowercasing it would narrow the terminal arm, where over-refusal is the
    expensive direction.

    The parse reads `a` WHEREVER it sits in the query; the substring form it
    replaces required it immediately after the `?`. Every captured PerimeterX URL
    puts it first, so no measured case moves -- but one that did not would have
    been missed, and a missed captcha returns nothing to the agent.
    """
    tail = _PX_CAPTCHA_PATH_BOUNDED if segment_bounded else _PX_CAPTCHA_PATH
    actions: set[str] = set()
    for u in _candidate_urls(body, containing=_PX_CAPTCHA_PATH):
        if not u.path.lower().endswith(tail):
            continue
        for key, values in parse_qs(u.query, keep_blank_values=True).items():
            if key.strip().lower() == "a":
                actions.update(v.strip().lower() for v in values)
    return frozenset(actions)

# NO WINDOW-ELIGIBLE-VENDOR SET AND NO WIDGET-URL TUPLE LIVE HERE ANY MORE. Both
# existed to serve one generic CAPTCHA arm -- "a window-eligible vendor is present
# AND some interactive widget URL is in the body" -- and that arm was deleted
# 2026-09-03 (see the CAPTCHA block in `escalation_verdict` for the reasoning and
# for what now excludes Cloudflare, Akamai and Imperva). Neither constant had a
# second reader, so both went with it rather than staying as dead code that reads
# as live safety machinery.

# The whole body must BE the error line -- `re.match` plus `$` against a stripped
# body. A substring scan here would fire on any page quoting an error code.
# 1010 is deliberately absent: it is a Browser Integrity Check block based on the
# client web browser, which is exactly the class a correctly fingerprinted tier 1
# should pass, so mapping it terminal discards the one case where escalating to a
# browser is right. 1015 is rate limiting, not a block.
#
# STATED RESIDUAL: THIS ARM HAS NO PRODUCIBLE INPUT ON THE PATH IT WAS WRITTEN
# FOR, the same way `_login_hop` has none -- and it is kept for the same reason,
# so that the day the input exists the arm is already right. `cascade_fetch`
# passes `result.html` to `escalation_verdict` (fetch.py:660). At tier 0 that is
# `''` on EVERY non-2xx path -- both transports build their non-2xx
# `TierResult` with no `html=`, in `_tier_http_urllib` (tiers.py:809) and
# `_tier_http_impersonate` (tiers.py:1118) -- and `""` explicitly on the 2xx
# verbatim-text branch of `_process_http_response` (tiers.py:699), and a
# Cloudflare `error code: NNNN` body is served on a non-2xx. At tier >= 1 it is
# `document.documentElement.outerHTML`, a serialised DOM, which `^...$` against
# a stripped body cannot match at all. The one body this predicate ever sees at
# tier 0 is a 2xx HTML response that failed the content gate, the other
# `_process_http_response` return (tiers.py:713), which is not the shape a block
# arrives in. MEASURED 2026-09-03 by reading every `TierResult` construction on
# the failure paths; line numbers RE-VERIFIED 2026-09-03 after the fetch.py
# edits moved the call site, which is why the SYMBOL leads and the line follows.
_CF_HARD_BLOCK_RE = re.compile(r"^error code: (1006|1007|1008|1009|1012|1020|1106)$")

_IDP_HOSTS = frozenset({
    "accounts.google.com", "login.microsoftonline.com", "login.microsoft.com",
    "login.windows.net", "login.live.com", "sts.windows.net",
    "signin.aws.amazon.com", "id.atlassian.com", "auth.atlassian.com",
})
# Spec writes these as `*.okta.com` etc. -- a SUBDOMAIN wildcard. The bare
# registrable domain is the vendor marketing site and is not a login host.
#
# AND NEITHER IS `www.` (round-4 finding 4, 2026-09-03). The sentence above
# states an intent the suffix test does not implement: `www.okta.com` IS that
# marketing site and it ends with `.okta.com`. So does every one of the vendor
# blog, docs, help-centre and community subdomains, which are ordinary public
# pages an agent fetches constantly while researching authentication -- the one
# topic that brings an agent to these hosts at all. MEASURED:
# `_is_login_url` returned True for `www.okta.com/blog/x`,
# `developer.okta.com/docs/g`, `support.okta.com/help/s/a` and
# `www.auth0.com/blog/y`. The reach is worse than it looks because the final-URL
# login arm sits ABOVE the captcha block, so any failing fetch of such a host
# that involved ANY redirect -- a trailing-slash 301, a locale hop -- yielded
# NEEDS_LOGIN and drove the owner logged-in Chrome (C-3/C-4). A live capture has
# the shape: `www.okta.com/blog/2019/06/what-is-zero-trust-security` 301s to the
# trailing-slash form and answers 404.
#
# SO A VENDOR HOST IS NOW A PRECONDITION AND NEVER SUFFICIENT, the same rule
# `_vendors_present` states one screen up: presence pairs with an affirmative
# marker or it decides nothing. The marker here is the path, because a real IdP
# login endpoint LEADS with one -- `/oauth2/v1/authorize`, `/authorize`,
# `/login/login.htm`, `/u/login/identifier` -- while a blog post carries `/blog/`
# and a docs page `/docs/`. `_IDP_HOSTS` above is
# deliberately NOT changed: those are whole hostnames that serve nothing but
# authentication, so there the host IS the endpoint name.
_IDP_HOST_SUFFIXES = (".okta.com", ".oktapreview.com", ".okta-emea.com",
                      ".auth0.com", ".onelogin.com", ".pingone.com",
                      ".duosecurity.com")
# AT THE PATH ROOT -- the LEADING segments -- compared with the same expression
# `_LOGIN_PATH_ROOTS` uses two screens down, and written in the same tuple form
# so this file has ONE mechanism for a root-anchored path marker and not two.
#
# ANYWHERE-IN-THE-PATH WAS ROUND-1 FINDING 5 RECREATED (round-4 finding 1,
# 2026-09-03). Requiring an endpoint segment closed the bare-host-suffix defect
# and then accepted that segment at ANY depth, which is what round 1 had already
# ruled insufficient -- "a bare one-word segment is no longer sufficient".
# MEASURED: `developer.okta.com/docs/login/old` and
# `developer.okta.com/docs/oauth2/guide` both returned True. Vendor documentation
# is full of paths carrying `login` and `oauth2` as segments, and these are the
# hosts an agent hits while researching authentication -- the one topic that
# brings it to them at all -- so the recreated defect reached the same pages the
# original did, and NEEDS_LOGIN drives the owner's logged-in Chrome (C-3/C-4).
#
# A real IdP login endpoint puts the marker FIRST (`/oauth2/v1/authorize`,
# `/login/login.htm`); documentation files it under `/docs/` or `/help/`. That is
# the same separation the Shibboleth root prefix makes, for the same reason.
#
# Whole segments either way: a substring test would put
# `/docs/concepts/oauth-openid/` and `/docs/guides/sign-in-to-web/` -- vendor
# documentation -- back in from the other side.
#
# The list is short on purpose. `sso`, `saml`, `oidc` and `idp` are NOT here:
# every one of them is also a documentation-section name on `developer.okta.com`
# (`/docs/reference/api/oidc/` is a docs page, not an endpoint), which is the
# defect this repair exists to close rather than relocate.
#
# WHAT THAT COSTS, STATED -- AND MEASURED BEFORE BEING CALLED A COST. An
# endpoint filed behind a leading tenant or environment segment is out of this
# arm's reach, because the marker is not first: PingOne serves
# `auth.pingone.com/<envID>/as/authorize`. That is NOT a lost fetch, and an
# earlier draft of this comment wrongly said it was. The QUERY arm below carries
# it on any host at all -- an authorize request must send `response_type` and
# `client_id` (RFC 6749 S4.1.1) and, as an OIDC one, `redirect_uri` (OIDC Core
# S3.1.2.1), so a URL an IdP actually serves matches the triple. Only a stripped
# `?client_id=x` misses, and no IdP serves that.
#
# A SAML POST-binding endpoint with no query is the real residual --
# `acme.onelogin.com/trust/saml2/http-post/sso/123` reads CONTINUE. The REDIRECT
# binding, which is the observable one, carries `SAMLRequest` and is caught by
# the query arm. One fetch, the cheap direction (C-3/C-4) -- and it was equally
# out of reach under the ANYWHERE test this replaces, since `trust`, `http-post`
# and `as` were never in the list either.
#
# `("u", "login")` IS WHY THIS IS A TUPLE LIST AND NOT A WORD SET. Auth0's
# Universal Login serves `/u/login/identifier`, so the marker is TWO segments at
# the root -- `_LOGIN_PATH_TAILS` carries the same pair, but a tail matches only
# a path that ENDS there and this one does not. Written as one more root tuple it
# needs no rule of its own.
_IDP_ENDPOINT_ROOTS = (("authorize",), ("login",), ("signin",), ("sign-in",),
                       ("sign_in",), ("oauth2",), ("u", "login"))
# SPECIFIC PATHS ONLY. A bare one-word segment list (`login`, `sso`, `auth`,
# `idp`, `cas`, ...) lived here until 2026-09-03 and was deleted: any WHOLE
# segment equal to one of those made a URL a login URL, so `/docs/auth/reference`
# and `/support/login/troubleshooting` -- ordinary public documentation -- routed
# to the user's logged-in Chrome. Whole-segment matching fixed the substring
# defect (`/blog/authoritative-guide`) and left the real one untouched: the
# segment is a WORD, and words appear in public paths.
#
# WRITTEN AS SEGMENT TUPLES, and compared as tuples, because a string suffix has
# no LEFT boundary: `"docs/endusers/sign_in".endswith("users/sign_in")` is TRUE,
# and so is `"api/subaccount/login".endswith("account/login")`. Both are ordinary
# routes that came back NEEDS_LOGIN and, after a redirect, drove the user's
# logged-in Chrome (round-2 finding 1, 2026-09-03). The tuple form is the
# representation the comparison uses; there is no second string form to drift
# from it.
_LOGIN_PATH_TAILS = (
    ("session", "new"), ("users", "sign_in"), ("account", "login"),
    ("u", "login"), ("connect", "authorize"), ("openid-connect", "auth"),
    ("adfs", "ls"), ("cas", "login"),
)
# MATCHED AT THE PATH ROOT -- the LEADING segments, not a tail and not
# containment. ONE entry, and a root prefix is ruled for it ALONE (2026-09-03).
#
# Shibboleth's deployed endpoint always carries a binding suffix
# (`/idp/profile/SAML2/Redirect/SSO`, `/POST/SSO`), so an `endswith` test can
# never reach it; until the bare `idp` segment was deleted, that segment was what
# caught it. Three fixed tokens that nothing but an identity provider serves is a
# different thing from a one-word segment, which is why this one gets a rule of
# its own while `auth` may not.
#
# IT WAS CONTAINMENT-ANYWHERE UNTIL ROUND-2 FINDING 1 (2026-09-03), and that
# admitted `/docs/idp/profile/saml2/api-guide` -- the vendor's own guide TO the
# endpoint, read as the endpoint. The IdP serves it at the path root; a docs site
# files it under one. Anchoring at the root is what separates them.
#
# THE TAILS DO NOT GET A PREFIX RULE AND THIS DOES NOT GET A TAIL RULE. Asking
# for either re-opens the class just closed.
_LOGIN_PATH_ROOTS = (("idp", "profile", "saml2"),)

_WWW_AUTH_COOKIEABLE_SCHEMES = frozenset({"basic", "digest", "negotiate", "ntlm",
                                          "mutual", "hoba"})

# DataDome's captcha client, PARSED AND COMPARED AS TWO WHOLE VALUES (round-4
# finding 0, 2026-09-03). The substring form matched `/c.json`, `/c.jsx` and
# `/c.js.map`; the right boundary that replaced it stopped those and admitted
# `/c.js-backup`, because the lookahead excluded `\w` and `.` and `-` is neither.
# It also never had a LEFT boundary, so `myct.captcha-delivery.com/c.js` -- any
# label ENDING in `ct` -- matched as well. MEASURED: both returned
# CAPTCHA_CHALLENGE, the one verdict that opens the headed window (C-3/C-4).
#
# THE COMMENT THAT STOOD HERE WAS WRONG AND IS CORRECTED, not merely its regex.
# It argued that because `c.js` is a complete path whose only sibling is `i.js`,
# "there is nothing to parse, and a boundary is the whole repair". A boundary
# bounds ONE side of ONE component. What this arm decides is whether a URL names
# a specific HOST at a specific PATH -- two whole values -- so it is the repair
# `_px_captcha_actions` already got: extract the candidates, parse them, compare
# the values. The argument for a boundary WAS the defect.
#
# THE CANDIDATES COME FROM `_candidate_urls` -- the same extractor the
# PerimeterX arm reads, two screens up -- so this arm inherits the delimited
# token, the unescaped ampersand and `urlsplit` without restating any of them.
#
# The HOST compares case-insensitively (`hostname` lowercases, and a host is
# case-insensitive anyway); the PATH compares case-sensitively, as the substring
# test did. `urlsplit` leaves the query and the fragment out of `path`, which is
# what keeps `/c.js?t=fe` matching while `/c.js.map` does not.
_DD_CAPTCHA_HOST = "ct.captcha-delivery.com"
_DD_CAPTCHA_PATH = "/c.js"


def _dd_captcha_client(body: str) -> bool:
    """True iff `body` references DataDome's CAPTCHA client AT ITS OWN HOST.

    `i.js` -- the device-check client, same host -- and every longer path
    (`/c.json`, `/c.jsx`, `/c.js.map`, `/c.js-backup`) are a DIFFERENT path and
    compare unequal; a host that merely ends with the vendor's
    (`myct.captcha-delivery.com`) is a DIFFERENT host; and the same URL sitting
    in another URL's query is a query VALUE rather than a candidate. None of the
    three needs a boundary rule, because none is compared as a substring.
    """
    return any(u.hostname == _DD_CAPTCHA_HOST and u.path == _DD_CAPTCHA_PATH
               for u in _candidate_urls(body, containing="captcha-delivery.com"))


# THE THREE CAPTCHA FAMILIES A HUMAN IS ACTUALLY ASKED TO SOLVE, as (HOST, PATH
# PREFIX) pairs compared AS WHOLE VALUES -- never as a substring of the body and
# never as a substring of a URL. `notchallenges.cloudflare.com` is a DIFFERENT
# host and compares unequal; `www.google.com/maps/api.js` is a DIFFERENT path
# and does not start with `/recaptcha/`. Neither needs a boundary rule, for the
# reason `_dd_captcha_client` states one screen up: neither is a substring test.
#
# A PATH PREFIX RATHER THAN A WHOLE PATH, deliberately. Every vendor versions
# its own client -- `/turnstile/v0/api.js`, `/recaptcha/api.js`,
# `/recaptcha/api2/anchor`, `/recaptcha/enterprise.js` -- so pinning the version
# is a needle that goes stale silently the day the vendor ships the next one,
# and a missed captcha returns nothing to the agent. The two hosts that serve
# NOTHING but the widget take `/` for the same reason. The APEX `hcaptcha.com`
# does NOT: it serves the vendor's marketing site, so only its documented client
# path counts there.
_CAPTCHA_WIDGET_URLS = (
    ("challenges.cloudflare.com", "/turnstile/"),
    ("www.google.com", "/recaptcha/"),
    ("www.recaptcha.net", "/recaptcha/"),
    ("recaptcha.net", "/recaptcha/"),
    ("js.hcaptcha.com", "/"),
    ("hcaptcha.com", "/1/api.js"),
    ("newassets.hcaptcha.com", "/"),
)


def _interactive_captcha_widget(body: str) -> bool:
    """True iff `body` mounts a Turnstile, reCAPTCHA or hCaptcha widget.

    Built exactly like `_dd_captcha_client`, so it inherits the delimited token,
    the unescaped ampersand and `urlsplit` without restating any of them.

    THE HOST IS ITS OWN PREFILTER. `_candidate_urls` warns that a caller whose
    `containing` is not a substring of every URL it can accept loses candidates
    SILENTLY; passing the pair's own host makes that property structural rather
    than remembered, and a pair added later cannot get it wrong. The cost is one
    scan per pair, paid only on the failure path -- the sole caller conjoins a
    non-2xx status AHEAD of this call, so an ordinary 200 never reaches it.

    A WIDGET IS NOT A WALL and this predicate does not claim it is; it reports
    presence. The caller supplies the half that makes presence mean something.
    """
    return any(u.hostname == host and u.path.startswith(prefix)
               for host, prefix in _CAPTCHA_WIDGET_URLS
               for u in _candidate_urls(body, containing=host))

# THE MEASURE FOR "the widget is substantially the whole document": tags out,
# whitespace collapsed. Scripts, styles and comments are already gone by the
# time this runs, so what is left is what the origin SHOWED. Not a parser and
# not trying to be -- it answers one question, whether the reader was shown
# anything besides the widget.
_TAG_RE = re.compile(r"<[^>]*>")


def _visible_text(stripped_html: str) -> str:
    """Collapse `_strip_script_and_style` output with a REGEX approximation of
    the reader's text.

    NOT the reader's text, and an earlier version of this docstring said it was.
    `_TAG_RE` is `<[^>]*>`, so this ends a tag at the first `>` even inside a
    quoted attribute value, and it does not decode character references. Both
    were measured to decide a classification, which is why the JS-shell arm uses
    `_reader_text_for_markers` instead.

    Kept as-is because `_document_refuses_with_a_challenge` asks a different
    question of it -- whether the reader was shown anything BESIDES a widget,
    a length question where an over-long answer is the safe direction.
    """
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", stripped_html or "")).strip()

def _ascii_letter(ch: str) -> bool:
    """True for A-Z and a-z ONLY.

    `str.isalpha()` accepts every Unicode letter, and HTML's tag-open state does
    not: `<é` is TEXT to a browser, not the start of an element. Measured before
    this existed -- `<e' + chr(233) + ' You need to enable JavaScript>` was consumed as a tag
    and its marker lost. Review round 4.
    """
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def _strip_tags_respecting_quotes(stripped_html: str) -> str:
    """Remove tags, honouring quoted attribute values.

    `_TAG_RE` is `<[^>]*>`, so it ends a tag at the FIRST `>` -- including one
    inside a quoted attribute value. The remainder of that attribute then
    survives as if it were text, which is how
    `<p data-x="a > enable javascript">` put a marker no reader can see into a
    haystack searched for exactly that marker.

    THIS IS A TOKENISER QUESTION, NOT A TREE-CONSTRUCTOR ONE, and the difference
    is why this is buildable where the parsing attempts recorded in
    `architecture.md` were not. Those asked "would a browser BUILD this element?"
    and died on ten divergences across five review rounds. This asks only "where
    does this tag end?", which has one rule: a quote character opens a value and
    the matching quote closes it, and `>` inside that value is not a delimiter.
    No element list, no insertion modes, nothing to keep in step with a spec.

    Scripts, styles and comments are already gone by the time this runs -- see
    `_strip_script_and_style` -- so an unquoted `>` inside JavaScript cannot
    reach here.
    """
    out = []
    i, n = 0, len(stripped_html)
    while i < n:
        ch = stripped_html[i]
        # A `<` OPENS A TAG ONLY WHEN THE NEXT CHARACTER SAYS SO. HTML's
        # tag-open state moves to a tag for an ASCII letter, `/`, `!` or `?`
        # and EMITS `<` AS TEXT for anything else -- so `1 < 2. You need to
        # enable JavaScript` is visible text, not a malformed element.
        # Measured before this clause existed: that document classified
        # `other` here and `js_only_spa` on `main`, because everything from
        # the `<` to the next `>` was eaten. That was a REGRESSION this branch
        # introduced, found by review round 3.
        # ONE PREDICATE, no element list: the same reason the quote rule below
        # is buildable where a tree constructor is not.
        if ch != "<" or (i + 1 < n and not (_ascii_letter(stripped_html[i + 1])
                                            or stripped_html[i + 1] in "/!?")):
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:          # a trailing `<` is text, not an unclosed tag
            out.append(ch)
            i += 1
            continue
        # Inside a tag: skip to the closing '>', tracking quote state.
        i += 1
        quote = ""
        prev = ""      # last non-space character seen inside this tag
        while i < n:
            c = stripped_html[i]
            if quote:
                if c == quote:
                    quote = ""
            elif c in ('"', "'") and prev == "=":
                # QUOTE MODE IS ENTERED ONLY AFTER `=`, which is HTML's
                # before-attribute-value state. A quote INSIDE an unquoted
                # value is an ordinary character -- `<p data-x=a">` -- and
                # treating it as an opener put this scanner into a quoted
                # state that never closed, eating the rest of the document
                # and losing the marker. Review round 4.
                quote = c
            elif c == ">":
                i += 1
                break
            if not c.isspace():
                prev = c
            i += 1
        out.append(" ")
    return "".join(out)


def _reader_text_for_markers(stripped_html: str) -> str:
    """Reader text for markers."""
    return re.sub(r"\s+", " ",
                  html.unescape(_strip_tags_respecting_quotes(stripped_html or ""))).strip()



def _document_is_positively_a_challenge(body: str) -> bool:
    """True iff the DOCUMENT ITSELF says the origin served a challenge.

    One line of logic and no new needles: `_challenge_reason` is this file's
    established test -- the same call `classify_failure` makes on a 4xx and the
    same call tier 1's settle loop re-reads with -- so the three cannot drift
    apart. Evidence is an interstitial-only markup marker, a `_CHALLENGE_TITLES`
    <title>, or a `_CLIENT_DEMANDS` phrase in the visible text.

    `page_served=True` IS NOT A CLAIM THAT THE ORIGIN SERVED THE PAGE. It is
    what restricts the markup arm to `_CHALLENGE_MARKUP`, and the reason is the
    same one `_document_refuses_with_a_challenge` states for its own call:
    `class="cf-turnstile"` is a mount a SITE embeds on its own form, so admitting
    it would let an ordinary error page whose global template carries a
    contact-form widget read as a challenge. What is left is the set of markers
    only ever seen on a document served IN PLACE OF the page.

    IT IS NOT `_document_refuses_with_a_challenge`, AND THE DIFFERENCE IS LOAD-
    BEARING. That predicate additionally ORs in "the widget is substantially the
    whole document" -- visible text at or under SHELL_TEXT_MAX -- which is
    deliberately loose because it is one half of a conjunction with
    `_interactive_captcha_widget`. Standing ALONE that arm answers True for every
    short body, an origin's bare `Too many requests` among them, so a caller with
    no widget conjunct must not use it. This is that caller's predicate.
    """
    return bool(_challenge_reason(body or "",
                                  _strip_script_and_style(body or ""),
                                  whole_body_demands=True, page_served=True))


def _document_refuses_with_a_challenge(body: str) -> bool:
    """True iff the document is one the origin served INSTEAD of the page.

    THE HALF THE WIDGET ARM WAS MISSING. `_interactive_captcha_widget` reports
    that a captcha widget is MOUNTED; a non-2xx reports that the origin refused
    something. Neither says the refusal WAS a challenge, and the conjunction of
    the two does not either: an ordinary site whose global template loads
    `recaptcha/api.js` for a newsletter or contact form serves that same
    template on its 404, so "refused" and "mounts a widget" are both true of a
    page that is simply missing. This predicate asks for the third fact.

    TWO FORMS OF EVIDENCE, either sufficient:

    * The document is POSITIVELY A WALL by the file's own established test --
      `_document_is_positively_a_challenge`, which wraps the same
      `_challenge_reason` call `classify_failure` makes on a 4xx: an
      interstitial-only markup marker, a `_CHALLENGE_TITLES` <title>, or a
      client-demand phrase in the visible text. That wrapper carries the
      `page_served=True` reasoning restated below, and has one other caller --
      the RATE_LIMITED block in `escalation_verdict`, which has NO widget
      conjunct and therefore must not use the second arm here.
    * The widget IS substantially the whole document -- at or under
      SHELL_TEXT_MAX of visible text, the bound the JS-shell arm already uses.
      A challenge page that renders its own copy from script has no title arm
      and no demand phrase to find, and the captured walls run 41-43 extracted
      characters. A page whose chrome, navigation and 404 message the origin
      DID render is not this shape.

    `page_served=True` IS DELIBERATE AND IT IS NOT A CLAIM THAT THE ORIGIN
    SERVED THE PAGE -- it is what restricts the markup arm to
    `_CHALLENGE_MARKUP`, the markers only ever seen on a document served in
    place of the page. `_EMBEDDABLE_CHALLENGE_MARKUP` must NOT corroborate here,
    and the reason is circularity rather than caution: `class="cf-turnstile"` is
    the MOUNT POINT for the very widget `_interactive_captcha_widget` just
    found, so the template that puts the client on its 404 puts the mount there
    too. Accepting it would let the arm corroborate itself and would leave the
    Turnstile half of this defect exactly where it was.

    RESIDUAL, STATED PLAINLY: a minimal theme whose 404 shows under
    SHELL_TEXT_MAX of text and whose template mounts a widget still reads as a
    challenge. That is the same trade the JS-shell bound already makes
    everywhere else in this file, and the alternative -- a lower bound -- starts
    losing real walls, which is the R1 direction and the expensive one.
    """
    if _document_is_positively_a_challenge(body):
        return True
    return len(_visible_text(_strip_script_and_style(body or ""))) <= SHELL_TEXT_MAX


def _widget_arm_fires(status: int | None, body: str) -> bool:
    """The captcha block's widget arm, named once so two callers cannot drift.

    WHY THIS EXISTS AS A FUNCTION. It has two callers that must agree, and when
    the condition was written out at both of them they did NOT agree: the
    RATE_LIMITED block suppressed itself on `_document_is_positively_a_challenge`
    alone, while the captcha arm ALSO accepts a widget on a refused,
    shell-shaped document. A wall of that second shape carrying a `Retry-After`
    header therefore returned RATE_LIMITED -- which is TERMINAL -- so the
    cascade broke below the headed rung, the captcha was never diagnosed, and
    the owner was never asked for the CDP browser. Found by review round 3,
    driven end to end through the real cascade.

    The three conjuncts and what each is for: a non-2xx says the origin refused
    something; `_interactive_captcha_widget` says a captcha client is mounted;
    `_document_refuses_with_a_challenge` says the refusal WAS a challenge rather
    than an ordinary 404 wearing the site's own template. The first two are true
    together of a missing page on any site whose global template carries a
    contact-form widget, which is why the third is not optional.
    """
    return (status is not None and not (200 <= status < 300)
            and _interactive_captcha_widget(body)
            and _document_refuses_with_a_challenge(body))


# FIELDS, never a substring scan of the body. The word unauthorized appears in
# the navigation of plenty of ordinary documentation pages.
#
# ONE TEST DECIDES MEMBERSHIP: does the error name a credential THE BROWSER
# HOLDS? A session, yes -- that is exactly what the CDP tier carries. An API
# token or an OAuth CLIENT credential, no, and routing one of those to the user's
# logged-in Chrome cannot work. So `invalid_token`, `token_expired`,
# `invalid_grant`, `invalid_client` and `unauthorized_client` were REMOVED
# 2026-09-03: the first two name an access token, and the last three are RFC 6749
# S5.2 token-endpoint errors about the client's own registered credentials, which
# no browser session supplies. Same rule that took the API-key phrases out of
# `_JSON_UNAUTH_MSG_RE` below -- one mechanism, two arms, and the second was
# found by looking for it rather than by another review round.
_JSON_UNAUTH_ERRORS = frozenset({
    "not_authed", "unauthorized", "authentication_required",
    "login_required", "not_logged_in",
})
# Same test, and all three name a session.
_JSON_UNAUTH_CODES = frozenset({"unauthorized", "not_authenticated",
                                "authentication_failed"})
# A BODY THAT NAMES AN API KEY OR AN ACCESS TOKEN IS NOT A LOGIN. `invalid api
# key`, `invalid access token`, `you did not provide an api key` and `missing
# bearer` were here until 2026-09-03 and were deleted: they made
# `{"message":"Invalid API key provided"}` a NEEDS_LOGIN, which routes the fetch
# to the user's logged-in Chrome, and a browser session cookie cannot supply an
# API key. The verdict for those is CONTINUE.
#
# This is NOT the Bearer/DPoP split the spec dropped. That was about the
# `WWW-Authenticate` SCHEME being unreliable -- api.stripe.com sends `Basic`
# while its body says to use Bearer -- and says nothing about a body that names
# a key outright, which is the origin telling you which credential it wants.
#
# `invalid authorization` stays: it names neither a key nor a token, and it is
# the phrasing a cookie-session API uses as often as a token one.
#
# WORD-BOUNDED ON EVERY ALTERNATIVE (round-3 boundary sweep, 2026-09-03), which
# is how `_CREDENTIAL_MSG_RE` below was already written. Unbounded, `unauthorized`
# matched inside `unauthorized_client` and `invalid authorization` inside
# `invalid authorization_code` -- RFC 6749 S5.2 errors about the CLIENT's own
# registered credentials, which no browser session supplies. The veto does not
# reach those: `_CREDENTIAL_ERRORS` is an EXACT match on the whole field and both
# sit mid-sentence. `\b` also excludes `UnauthorizedAccessException`, since a
# following letter is a word character.
_JSON_UNAUTH_MSG_RE = re.compile(
    r"\brequires? authentication\b"
    r"|\bnot authenticated\b"
    r"|\bauthentication (?:credentials were not provided|required|failed)\b"
    r"|\bunauthorized\b"
    r"|\binvalid authorization\b",
    re.I)

# THE SAME TEST, RUN FIRST AND OVER THE WHOLE DOCUMENT. Removing the credential
# phrases from the two constants above stopped a body that ONLY named a key; it
# did not stop one that names a key in one field and says `unauthorized` in
# another. `{"error":"unauthorized","message":"Invalid API key provided"}`
# returned on `error` before the message was ever read, and `Unauthorized:
# invalid API key` matches the broad alternative on its first word (round-2
# finding 2, 2026-09-03). So the question -- does the error name a credential THE
# BROWSER HOLDS? -- is now asked of every field before any generic arm answers.
#
# ROUND 3 MOVED IT AGAIN, OUT OF `_json_unauth` ENTIRELY. Round 2 put the veto
# inside the one arm that had been reported, and short-circuit evaluation then
# routed straight past it: a 401 carrying `WWW-Authenticate: Basic` AND
# `{"message":"Invalid API key provided"}` satisfied `_www_auth_cookieable`
# first, so `_json_unauth` -- and the veto inside it -- never ran. The veto is now
# `_names_non_browser_credential` and stands as its OWN guard above the whole
# remaining-login block, so an arm added below it is dominated by construction
# rather than by the author remembering. Same shape as the empty-headers guard.
#
# THESE ARE VETOES, NOT VERDICTS: matching one yields CONTINUE, never a verdict
# of its own. A false veto costs one fetch; a false NEEDS_LOGIN drives the user's
# logged-in Chrome at a credential no browser session can supply (C-3/C-4).
#
# The five error codes are the ones deleted from `_JSON_UNAUTH_ERRORS` the same
# day: the first two name an access token, the last three are RFC 6749 S5.2
# token-endpoint errors about the client's own registered credentials.
_CREDENTIAL_ERRORS = frozenset({
    "invalid_token", "token_expired", "invalid_grant", "invalid_client",
    "unauthorized_client",
})
# THE SAME FIVE AS A TEXT NEEDLE, derived from the set above so the two cannot
# drift. Word-bounded: `_` is a word character to `re`, so the alternative is
# the WHOLE token or nothing -- it cannot match inside `x_invalid_token` or
# `invalid_token_2`. Needed because the veto now reads the body as TEXT and not
# only as parsed JSON; see `_names_non_browser_credential`.
_CREDENTIAL_ERRORS_RE = re.compile(
    r"\b(?:%s)\b" % "|".join(sorted(re.escape(e) for e in _CREDENTIAL_ERRORS)),
    re.I)
# `token` ALONE IS NOT HERE, deliberately: a CSRF token is named on the login
# pages this predicate exists to recognise, so the bare word would veto the case
# it is meant to admit. Each alternative names the credential's KIND.
_CREDENTIAL_MSG_RE = re.compile(
    r"\bapi[ _-]?(?:key|token)\b"
    r"|\baccess[ _-]?token\b"
    r"|\bbearer\b"
    r"|\bclient[ _-]?(?:id|secret)\b"
    r"|\bclient credentials?\b",
    re.I)

# ---- THE <title>, <form> AND <input> FACTS COME FROM A PARSER ---------------
#
# ROUND-4 FINDINGS 2 AND 3, RE-DONE 2026-09-03 UNDER THE CLASS RULING. Both were
# first repaired by tightening a regex over raw markup -- word boundaries on the
# value, a `\s` before the attribute name, separate quoted and unquoted value
# branches. Every patch was correct and every one closed only the instance it was
# aimed at, because the MECHANISM is what fails: a regex cannot tell an attribute
# from the text around it, so each miss needs a new boundary rule and the next
# shape needs the next one.
#
# MEASURED WITH THOSE PATCHES IN PLACE, which is what settled it:
# `<input data-type="password">` and `<input x-type=password>` still satisfied
# the password test and `<input data-autocomplete="username">` the autocomplete
# one -- `\btype` matches inside `data-type` because `-` is not a word character.
# That is finding 3's attribute-NAME half, alive in the two needles beside the
# one that was repaired, and each returned NEEDS_LOGIN, which drives the owner's
# logged-in Chrome (C-3/C-4).
#
# `html.parser.HTMLParser` DOES THE TOKENISING NOW -- stdlib, no dependency. An
# attribute is a KEY, so `data-type` is simply not `type` and no boundary rule
# exists to get wrong; an unquoted value cannot bleed into the next attribute,
# because the parser ends it where HTML ends it; and the <title> arrives as TEXT
# instead of as a slice of the markup that follows it.
#
# THE VALUE STILL GETS A WORD-BOUNDED NEEDLE, and that is not the patched
# mechanism coming back. Asked of a WHOLE value it is a text question, and the
# right one: `/users/sign_in` and `login-form` NAME a login, `authenticity-token`
# does not. That last one is Rails' CSRF field and rides on essentially every
# form Rails renders, so `\bauth\b` rather than `auth` is what keeps a large
# share of the public web out of the owner's browser.
#
# A CONJUNCT AND NOT A SAFETY NET. The previously-filed reading that "the title
# arm cannot act alone because `_login_form` also requires a password input" is
# FALSE and was measured to be: the form-attribute test is a DISJUNCT beside the
# two password tests, so a page with a sign-in title and no password box anywhere
# reaches NEEDS_LOGIN on a form id alone.
_LOGIN_FORM_ATTRS = ("action", "id", "name")
_LOGIN_MARKER_RE = re.compile(r"\b(?:log[-_]?in|sign[-_]?in|session|auth)\b", re.I)
# A SPACE-SEPARATED TOKEN LIST per the HTML spec, so it is SPLIT and not
# compared whole: `autocomplete="username webauthn"` is what a passkey-enabled
# login form carries, and equality against the attribute would lose it.
_PASSWORD_AUTOCOMPLETE_TOKENS = frozenset({"current-password", "username"})


# SVG and MathML are FOREIGN CONTENT: inside them `<title>` is an accessible
# name for a graphic, not the document's title. These are the only two roots
# HTML defines, so the set is closed rather than a list that could grow.
_FOREIGN_CONTENT_ROOTS = frozenset({"svg", "math"})

_ACCESS_RESTRICTION_PHRASES = (
    "subscribe to continue reading", "subscribe to read", "subscription required",
    "subscribers only", "unlock this article",
)
_ACCESS_RESTRICTION_RE = re.compile(
    r"\b(?:%s)\b" % "|".join(re.escape(p) for p in _ACCESS_RESTRICTION_PHRASES))
_ACCESS_CONTROL_RE = re.compile(
    r"\b(?:subscribe|subscribers?|subscriptions?|purchase|buy|members?|memberships?)\b")
_ACCESS_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})
_ACCESS_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "dd", "dialog", "div",
    "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1",
    "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol",
    "p", "pre", "section", "table", "td", "th", "tr", "ul",
})
_ACCESS_NONRENDERED_TAGS = frozenset({"script", "style", "template", "noscript"})


def _access_gate_match(text: str, controls: list[str]) -> bool:
    """The shared restriction-and-control test, without a visibility assertion."""
    if not isinstance(text, str) or not isinstance(controls, list):
        return False
    if not all(isinstance(control, str) for control in controls):
        return False
    normalized = " ".join(text.lower().split())
    return bool(_ACCESS_RESTRICTION_RE.search(normalized) and any(
        _ACCESS_CONTROL_RE.search(" ".join(control.lower().split()))
        for control in controls))


def visible_access_gate(candidates: list[dict]) -> bool:
    """Recognize an observed blocking UI, never arbitrary page text or metadata.

    Callers validate the exact candidate schema. Only a literal observed True
    permits the common subscription-control and restriction test to apply.
    """
    return isinstance(candidates, list) and any(
        isinstance(candidate, dict) and candidate.get("visible") is True
        and _access_gate_match(candidate.get("text"), candidate.get("controls"))
        for candidate in candidates)


class _MarkupFacts(HTMLParser):
    """Document title/form/input facts and structural access-gate candidates.

    `title` stays None until a `</title>` is seen, which is exactly the
    precondition `<title[^>]*>(.*?)</title>` had: an unclosed title element
    yielded no match there and yields None here, so this arm does not newly fire
    on one. The text is whitespace-collapsed and stripped, as that regex's
    caller did.

    Attributes: the FIRST occurrence of a duplicate wins, as a browser does.
    Names are lowercased by the parser; a valueless attribute reads as "".

    A `<title>` INSIDE `<svg>` OR `<math>` IS NOT THE DOCUMENT TITLE and is
    skipped. In foreign content `<title>` is an accessible NAME, and
    `<svg><title>Sign in</title>...</svg>` is the standard pattern for a
    sign-in icon button -- so without this, an ordinary article carrying that
    icon and a header login form returned NEEDS_LOGIN and drove the owner's
    logged-in Chrome (C-3/C-4). Measured 2026-09-04. The regex this replaced
    took the first `<title>` anywhere too, so the behaviour is not new; what IS
    new is that it can be closed at all, because only a parser knows it is
    inside foreign content.

    why-not track the full HTML insertion mode, or require `<head>`: neither is
    needed to answer this question, and a real document puts its title before
    any `<svg>` in source order. An UNCLOSED `<svg>` therefore suppresses every
    later title, which loses a fetch -- the cheap direction, and the one to fail
    in.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.forms: list[dict[str, str]] = []
        self.inputs: list[dict[str, str]] = []
        self.access_gate_candidates: list[dict] = []
        self._access_candidates: list[dict] = []
        self._access_controls: list[list[str]] = []
        self._access_stack: list[tuple[str, int, int, bool]] = []
        self._access_suppressed = False
        self._in_title = False
        self._title_parts: list[str] = []
        self._foreign = 0

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, value in attrs:
            out.setdefault(name.lower(), value if value is not None else "")
        return out

    def _access_starttag(self, tag: str, attrs: dict[str, str]) -> None:
        before = (tag, len(self._access_candidates), len(self._access_controls),
                  self._access_suppressed)
        if tag not in _ACCESS_VOID_TAGS:
            self._access_stack.append(before)
        self._access_suppressed = (self._access_suppressed or bool(self._foreign)
                                   or tag in _FOREIGN_CONTENT_ROOTS
                                   or tag in _ACCESS_NONRENDERED_TAGS)
        if self._access_suppressed:
            return
        if tag in _ACCESS_BLOCK_TAGS:
            self._access_data(" ")
        if tag not in _ACCESS_VOID_TAGS and (
                tag in {"dialog", "form"}
                or attrs.get("role", "").strip().lower() in {"dialog", "alertdialog"}
                or attrs.get("aria-modal", "").strip().lower() == "true"):
            candidate = {"text": [], "controls": []}
            self.access_gate_candidates.append(candidate)
            self._access_candidates.append(candidate)
        if not self._access_candidates:
            return
        is_input = tag == "input" and attrs.get("type", "text").lower() in {
            "button", "submit", "image"}
        if not (tag in {"button", "a"} or is_input
                or attrs.get("role", "").strip().lower() == "button"):
            return
        label = attrs.get("aria-label", "")
        if is_input:
            label = label or attrs.get("value", "") or attrs.get("alt", "")
        parts = [label] if label else []
        for candidate in self._access_candidates:
            candidate["controls"].append(parts)
        if tag not in _ACCESS_VOID_TAGS and not label:
            self._access_controls.append(parts)

    def _access_endtag(self, tag: str) -> None:
        if not self._access_suppressed and tag in _ACCESS_BLOCK_TAGS:
            self._access_data(" ")
        for index in range(len(self._access_stack) - 1, -1, -1):
            name, candidate_count, control_count, suppressed = self._access_stack[index]
            if name == tag:
                del self._access_stack[index:]
                del self._access_candidates[candidate_count:]
                del self._access_controls[control_count:]
                self._access_suppressed = suppressed
                break

    def _access_data(self, data: str) -> None:
        if self._access_suppressed or self._in_title:
            return
        for candidate in self._access_candidates:
            candidate["text"].append(data)
        for parts in self._access_controls:
            parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._access_starttag(tag, self._attrs(attrs))
        if tag in _FOREIGN_CONTENT_ROOTS:
            self._foreign += 1
        elif tag == "title":
            if self._foreign:
                return
            self._in_title = True
            self._title_parts = []
        elif tag == "form":
            self.forms.append(self._attrs(attrs))
        elif tag == "input":
            self.inputs.append(self._attrs(attrs))

    def handle_endtag(self, tag: str) -> None:
        self._access_endtag(tag)
        if tag in _FOREIGN_CONTENT_ROOTS:
            # Clamped at zero: a stray `</svg>` with no opener must not make
            # `_foreign` negative, which would read as truthy and skip every
            # title after it.
            self._foreign = max(0, self._foreign - 1)
        elif tag == "title" and self._in_title:
            self._in_title = False
            if self.title is None:
                self.title = " ".join("".join(self._title_parts).split())

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        self._access_data(data)


def _markup_facts(body: str) -> _MarkupFacts:
    """Parse `body`, or return EMPTY facts -- never raise into the cascade.

    THE GUARD IS NECESSARY, NOT DEFENSIVE. `HTMLParser` DOES raise on input a
    public web server can serve, and an earlier version of this docstring said
    the opposite -- it claimed ZERO exceptions over 51 documents, which was true
    of those 51 and false of the parser. The corpus fed it `<![CDATA[...]]>`,
    which does not raise, and never `<![foo]>`, which does. A second agent
    working the same question independently found the raising shape; the claim
    was under-tested, the guard was already right. MEASURED 2026-09-04:

        <![foo]>        AssertionError: unknown status keyword 'foo' in marked section
        <![then[ x ]]>  AssertionError: unknown status keyword 'then' in marked section
        <![>            AssertionError: expected name token at '<![>'

    `-O` DOES NOT STRIP THESE. They are explicit `raise AssertionError`
    statements in `_markupbase`'s declaration parsing, not `assert` statements,
    so a python running with assertions disabled raises them exactly the same.

    Everything else measured stays clean, `feed` and `close` together: a stray
    `<`, `</`, an unterminated quote, a truncated attribute, a bogus comment, a
    `<form>` inside a `<script>` string, nested forms, a 100 KB attribute name, a
    NUL, a BOM, duplicate attributes, `<form/ id=login>`, a 200 KB title, 1 MB
    bodies, non-ASCII, raw binary, empty input, and all 14 captured fixtures.

    On ANY exception the partial facts are DISCARDED and a fresh empty
    `_MarkupFacts` is returned, so `title is None` and `_login_form` returns
    False on its first guard. A malformed page therefore costs one fetch and can
    never open a rung -- the cheap direction (C-3/C-4). Keeping the partial facts
    would be the expensive one: half a parse is evidence of nothing, and the half
    that survived could carry the title.
    """
    facts = _MarkupFacts()
    try:
        facts.feed(body or "")
        facts.close()
        for candidate in facts.access_gate_candidates:
            candidate["text"] = " ".join("".join(candidate["text"]).split())
            candidate["controls"] = [" ".join("".join(parts).split())
                                     for parts in candidate["controls"]]
    except Exception:
        return _MarkupFacts()
    return facts


def _is_password_input(attrs: dict[str, str]) -> bool:
    """A password box, read off a PARSED <input> tag."""
    if attrs.get("type", "").strip().lower() == "password":
        return True
    return bool(_PASSWORD_AUTOCOMPLETE_TOKENS
                & set(attrs.get("autocomplete", "").lower().split()))


def _is_login_form_tag(attrs: dict[str, str]) -> bool:
    """A <form> whose `action`, `id` or `name` NAMES a login (spec S3.4)."""
    return any(_LOGIN_MARKER_RE.search(attrs.get(name, ""))
               for name in _LOGIN_FORM_ATTRS)


# The CONJUNCT that makes LOGIN_FORM survivable. A password input alone sits on
# every page carrying a header sign-in dropdown; the <title> is what says the
# origin served a login page INSTEAD of the page. Dropping this is the mutation
# that routes ordinary pages to the user's logged-in Chrome.
#
# THE THIRD ALTERNATIVE, `\bsign in to\b`, WAS DROPPED 2026-09-03 -- it was the
# only UNANCHORED needle here, and it fired mid-title on help and documentation
# pages ABOUT signing in ("How to sign in to your account | Acme Help"). That is
# the same distinction this file already draws twice: `_CHALLENGE_TITLES` exists
# so "an article ABOUT bot walls is not rejected for its own subject matter",
# and `_TITLE_BOUNDARY` keeps "Access Denied - Stack Overflow" out by requiring
# the phrase to START the title. The two surviving alternatives are `^`-anchored
# for exactly that reason and the dropped one was not.
#
# IT WAS ALSO REDUNDANT FOR EVERY ANCHORED TITLE: `^(?:sign|log)\s*[-_]?in\b`
# already matches "Sign in to GitHub", "Sign in to your account" and every other
# title that BEGINS with the phrase, which is how real login pages are titled.
# What it cost is the mid-sentence form -- "Please sign in to continue" -- which
# now reads CONTINUE. One lost fetch against a window on a help article, and the
# lost fetch is the cheap direction (C-3/C-4).
#
# THIS FIXED CASE WAS FILED-NOT-FIXED ONCE ALREADY, on the ground that
# `_login_form` "also requires a password input". It does not -- see
# `_LOGIN_FORM_ATTR_RE` above, which is a DISJUNCT beside the password test.
_LOGIN_TITLE_RE = re.compile(r"^(?:sign|log)\s*[-_]?in\b|^login\b", re.I)
# AND THE ANCHOR ALONE WAS NOT THE REPAIR (round-4 finding 2, 2026-09-03).
# Dropping the unanchored alternative did nothing for "Sign in to your account -
# Help", which BEGINS with the phrase and so matches the first anchored
# alternative. A help article carrying the site-wide header login form -- which
# is every Discourse forum, among others -- then satisfied `_login_form` and
# returned NEEDS_LOGIN at a documentation page, driving the owner's logged-in
# Chrome (C-3/C-4). MEASURED True, after the packet reporting it narrowed.
#
# THIS IS `_CHALLENGE_TITLES`' RULE IN THE OTHER PREDICATE: an article ABOUT
# signing in is not a sign-in page, the same way an article about bot walls is
# not a wall. One tuple of literal words, one word-boundary rule, no third
# mechanism.
#
# SCANNED OVER THE WHOLE TITLE, WHICH IS WHY THERE IS NO SEPARATOR LIST. A
# positional rule would have to enumerate " - ", " | ", an en dash, an em dash, a
# middle dot, a bullet, " :: " and whatever the next site picks. Position decides
# nothing here: `_LOGIN_TITLE_RE` has already pinned the login phrase to the
# START, so every word this can see either IS that phrase or follows it. Every
# separator is covered, including the ones nobody has listed.
#
# WHAT IT COSTS, STATED. A genuine login page whose title names one of these --
# "Sign in - Acme Support", which is how a Zendesk help centre titles its own
# sign-in -- now reads CONTINUE. One lost fetch against a window on a help
# article, and the lost fetch is the cheap direction (C-3/C-4).
#
# WHAT IT STILL ADMITS, ALSO STATED: a help title using none of these words
# ("Sign in to your account - Acme Community", "- Learn", "- Guides", "-
# Helpdesk", which `\bhelp\b` does not reach), and every non-English one
# ("Aide", "Ayuda", "Hilfe"). The list is the words round 4 named; widening it is
# a decision for whoever measures one of those.
_HELP_TITLE_RE = re.compile(
    r"\b(?:help|docs|documentation|support|faq|troubleshoot(?:ing)?)\b", re.I)


def _is_login_title(title: str) -> bool:
    """The <title> says the ORIGIN SERVED A LOGIN PAGE, not an article about one.

    BOTH HALVES, ALWAYS, and that is why this is a function and not two guards in
    `_login_form`. Neither constant is a predicate on its own: `_LOGIN_TITLE_RE`
    alone matches "Sign in to your account - Help", which is the whole of round-4
    finding 2. A second reader inheriting the positive needle without the
    exclusion is the same inheritance defect `_px_captcha_actions`'s
    no-default parameter exists to prevent, so there is one name for the
    question and no way to ask half of it.
    """
    return bool(_LOGIN_TITLE_RE.search(title)
                and not _HELP_TITLE_RE.search(title))


def _cookie_names(set_cookie: list[str] | None) -> set[str]:
    """Lowercased cookie NAMES from raw Set-Cookie lines.

    Names, not a substring scan of the joined lines: `datadome=` as a substring
    also matches `x-datadome=` and any cookie whose VALUE happens to contain it.
    """
    names = set()
    for line in set_cookie or ():
        name = (line or "").split("=", 1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def _vendors_present(h: dict[str, str], cookies: set[str]) -> frozenset[str]:
    """Which bot-wall vendors are PRESENT (spec S3.2). Presence, never a wall.

    HEADERS, `server`, AND COOKIES ONLY -- NEVER THE BODY. Every arm that
    consumes presence pairs it with a body discriminator, and a body needle here
    made the two halves of that conjunction the same fact: `captcha-delivery.com`
    put DataDome in the set, and the captcha arm then asked for
    `ct.captcha-delivery.com/c.js`, which CONTAINS it (round-2 finding 3,
    2026-09-03). PerimeterX's `px-captcha` / `window._pxAppId` went with it --
    not the identical string, but the same defect, since a page that merely
    QUOTES a vendor URL is not a page that vendor served. What is left is what
    the ORIGIN sent, which is the only thing that can establish who answered.

    THE COST, TAKEN DELIBERATELY: a real DataDome or PerimeterX response whose
    headers were not captured now reads as CONTINUE. That is one fetch, against a
    window opened on a blog post about bot detection (C-3/C-4).
    """
    server = h.get("server", "").strip().lower()
    v = set()
    if server == "cloudflare" or "cf-ray" in h or "__cf_bm" in cookies:
        v.add("cloudflare")
    if "x-datadome" in h or server == "datadome" or "datadome" in cookies:
        v.add("datadome")
    if cookies & _PX_COOKIES:
        v.add("perimeterx")
    if "x-amzn-waf-action" in h or "aws-waf-token" in cookies:
        v.add("aws")
    if "akamai-grn" in h or (cookies & _AKAMAI_COOKIES):
        v.add("akamai")
    if ("x-iinfo" in h or h.get("x-cdn", "").strip().lower() == "imperva"
            or any(n.startswith("incap_ses_") for n in cookies)):
        v.add("imperva")
    return frozenset(v)


def _is_login_url(url: str) -> bool:
    """LOGIN_URL (spec S3.4), against an ALREADY-ABSOLUTE url."""
    p = urlparse(url or "")
    host = (p.hostname or "").lower()
    segments = tuple(s for s in (p.path or "").lower().split("/") if s)
    # A DEDICATED auth host: the whole hostname is the endpoint name and the
    # path adds nothing. `accounts.google.com` serves nothing else.
    if host in _IDP_HOSTS:
        return True
    # A VENDOR host is a PRECONDITION and never sufficient -- see the constants.
    if (any(host.endswith(s) for s in _IDP_HOST_SUFFIXES)
            and any(segments[:len(r)] == r for r in _IDP_ENDPOINT_ROOTS)):
        return True
    # TUPLE comparison, never a string suffix: `endusers` must not satisfy
    # `users`. A slice longer than the path is a shorter tuple and simply does
    # not compare equal, so no length guard is needed.
    if any(segments[-len(t):] == t for t in _LOGIN_PATH_TAILS):
        return True
    if any(segments[:len(r)] == r for r in _LOGIN_PATH_ROOTS):
        return True
    names = {k.lower() for k in parse_qs(p.query, keep_blank_values=True)}
    if {"client_id", "redirect_uri", "response_type"} <= names:   # RFC 6749 S4.1.1
        return True
    return "samlrequest" in names


def _login_hop(hops: list[tuple[int, str, str]] | None) -> bool:
    """LOGIN_HOP (spec S3.4): a redirect whose TARGET is a login URL.

    UNREACHABLE AT ANY TIER WHERE A VERDICT IS COMPUTED, as of 2026-09-03, and
    kept deliberately. `hops` is populated ONLY by the two tier-0 transports
    (`tiers.py` urllib arm and impersonate arm); `tier_playwright` builds its
    TierResult with `hops` left empty, because the capture route reports the
    FINAL response only and the chain that reached it is not observable from
    there. Rule B computes no verdict below tier 1. So the one tier that fills
    `hops` never reaches this function, and the tiers that reach it never fill
    `hops`.

    It goes live the day tier-1 hops are captured, which is a transport change
    and not this predicate's. Until then the observable login-redirect evidence
    at tier >= 1 is `final_url` -- which is why the final-URL arm was given this
    arm's precedence above the captcha block rather than left below it.

    This is the gitlab.com case -- `/-/profile` -> 302 -> `/users/sign_in` -> 403
    with a Cloudflare challenge title. A reader that looks only at the LAST
    response calls that a bot wall forever; a wall served on a login URL you were
    redirected TO is still an auth requirement. It is why the hop list exists.

    The spec second login arm, `LOGIN_HOP and BOUNCE(requested_url,
    hop.location)`, is NOT implemented and cannot change this function answer:
    `A or (A and B)` is `A`. It exists in the spec to record WHICH arm fired, and
    this signature returns a verdict with no channel for that. Build it when
    there is a channel.
    """
    for hop_status, from_url, location in hops or ():
        if hop_status in (301, 302, 303, 307, 308) and location:
            if _is_login_url(urljoin(from_url or "", location)):
                return True
    return False


def _www_auth_cookieable(status: int | None, h: dict[str, str]) -> bool:
    """WWW_AUTH_COOKIEABLE (spec S3.4).

    Absence of `WWW-Authenticate` on a 401 proves NOTHING despite RFC 9110
    S15.5.2 stating a MUST -- api.github.com, api.notion.com and api.twitter.com
    all omit it -- so this arm is affirmative-only and never a veto.
    """
    if status != 401:
        return False
    raw = h.get("www-authenticate", "")
    scheme = raw.strip().split(",", 1)[0].split(" ", 1)[0].lower()
    return scheme in _WWW_AUTH_COOKIEABLE_SCHEMES


def _names_non_browser_credential(body: str) -> bool:
    """THE CREDENTIAL VETO: does this body name a credential the browser does NOT
    hold -- an API key, an access token, an OAuth client credential?

    A veto, never a verdict: True yields CONTINUE and nothing else. So its two
    failure directions are NOT symmetric. Too permissive costs ONE FETCH. Failing
    to fire drives the user logged-in Chrome (C-3/C-4) at a credential no browser
    session can supply -- which does not work either, and puts the expensive
    thing on the screen to find that out. Every choice below takes the first
    cost.

    IT READ JSON ONLY UNTIL ROUND 4, AND THAT MADE IT INERT IN PRODUCTION. No
    tier hands this function a raw JSON response body. `cascade_fetch` passes
    `result.html` to `escalation_verdict` (fetch.py:660), and that is:
      - empty at tier 0 on every non-2xx path -- both transports build their
        non-2xx `TierResult` with no `html=`, `_tier_http_urllib`
        (tiers.py:809) and `_tier_http_impersonate` (tiers.py:1118);
      - set to the empty string explicitly on the tier-0 2xx verbatim-text
        branch of `_process_http_response`, which is the one that handles
        `application/json` (tiers.py:699);
      - `document.documentElement.outerHTML` at tiers 1, 2 and 4 --
        `fetch_html` (tiers.py:3174) and `_tier_cdp_impl` (tiers.py:3893).
    SYMBOLS LEAD AND LINE NUMBERS FOLLOW, because four of these citations went
    stale inside one session when the file above them was edited.
    Chrome renders a JSON response as a `<pre>` inside a generated document, so
    at every tier that can reach this veto `json.loads` fails and the JSON scan
    answered False. MEASURED 2026-09-03 on the api.stripe.com shape the veto was
    WRITTEN for -- 401, `WWW-Authenticate: Basic`, a body naming an API key: with
    the raw body CONTINUE, with the DOM body NEEDS_LOGIN, and
    `fetch._gate_allows("cdp", "NEEDS_LOGIN")` is True. The round-3 repair was
    green in the suite and absent from the product.

    SO THE TEXT SCAN IS THE PRODUCTION PATH and runs first, unconditionally,
    whatever the body parses as. It also closes a second hole for free: the JSON
    scan read TOP-LEVEL string values only, so the industry-standard nested
    envelope `{"error":{"message":"Invalid API key provided", ...}}` -- the shape
    api.stripe.com actually returns, and the origin this file own comments cite
    -- escaped it entirely. Text has no nesting depth.

    WHAT THE TEXT SCAN COSTS, STATED BECAUSE IT IS THE TRADE BEING CHOSEN
    DELIBERATELY: a page that merely MENTIONS an API key, an access token or a
    bearer token now suppresses a genuine NEEDS_LOGIN. An API own documentation
    behind a login wall is the obvious case. That is a lost fetch, and a lost
    fetch is the cheap direction -- a window, or the owner logged-in Chrome, is
    the expensive one.

    THE JSON SCAN IS KEPT BESIDE IT, not replaced. It is now nearly subsumed --
    anything it finds in a plain body is in the text too -- but not entirely: a
    field written with JSON escapes is credential evidence the text scan cannot
    see. A veto that fires on more is the safe failure, so the cheap extra parse
    stays.
    """
    text = body or ""
    if _CREDENTIAL_MSG_RE.search(text) or _CREDENTIAL_ERRORS_RE.search(text):
        return True
    try:
        doc = json.loads(text)
    except (ValueError, RecursionError):
        return False
    if not isinstance(doc, dict):
        return False
    for val in doc.values():
        if isinstance(val, str) and (val.strip().lower() in _CREDENTIAL_ERRORS
                                     or _CREDENTIAL_MSG_RE.search(val)):
            return True
    return False


def _json_unauth(body: str) -> bool:
    """JSON_UNAUTH (spec S3.4). Runs on 200 as well as 4xx.

    slack.com/api/conversations.list returns HTTP **200** with
    `{"ok":false,"error":"not_authed"}`, so gating this on a 4xx loses the case
    it was written for.

    NO CREDENTIAL VETO IN HERE. It used to live at the top of this function and
    was reachable only by callers that got this far; `escalation_verdict` now
    applies `_names_non_browser_credential` above the whole login block instead.
    Putting a second copy back here would restore the belief that the veto is
    this arm's business, which is the belief round-3 finding 1 falsified.

    STATED RESIDUAL: THIS ARM HAS NO PRODUCIBLE INPUT AT ANY TIER, exactly the
    way `_login_hop` has none, and it is kept for the same reason -- so that the
    day a transport supplies the input the arm is already right. Rule B computes
    no verdict below tier 1, and `cascade_fetch` hands tiers 1/2/4
    `document.documentElement.outerHTML` (fetch.py:660): Chrome wraps a JSON
    response in a
    `<pre>` inside a generated document, so `json.loads` fails and every arm
    below returns False. Tier 0 does produce a raw JSON body, but its verdict is
    CONTINUE by Rule B whatever this returns. MEASURED 2026-09-03.

    IT IS DELIBERATELY NOT WIDENED TO THE DOM. This is a VERDICT arm, not a
    veto: making it fire on a serialised DOM produces NEEDS_LOGIN, which drives
    the user logged-in Chrome (C-3/C-4). The veto beside it WAS widened, and
    only because a veto can never do anything but prevent that. Same defect,
    opposite directions, and only one of the two is safe to close.
    """
    try:
        doc = json.loads(body or "")
    except (ValueError, RecursionError):
        return False
    if not isinstance(doc, dict):
        return False
    err = doc.get("error")
    if isinstance(err, str) and err.strip().lower() in _JSON_UNAUTH_ERRORS:
        return True
    code = doc.get("code")
    if isinstance(code, str) and code.strip().lower() in _JSON_UNAUTH_CODES:
        return True
    for key in ("message", "detail", "title"):
        val = doc.get(key)
        if isinstance(val, str) and _JSON_UNAUTH_MSG_RE.search(val):
            return True
    return False


_AUTH_REFUSAL_CODES = frozenset(
    _JSON_UNAUTH_ERRORS | _JSON_UNAUTH_CODES | {
        # Measured 200-served refusals the two sets above miss. Each is a CODE a
        # server emits, never prose a document could carry incidentally.
        "invalid_auth", "token_revoked", "account_inactive",
        "rest_not_logged_in", "rest_cookie_invalid_nonce",
        "mustbeloggedin", "readapidenied",
        "invalid_session_id", "unauthenticated", "authenticationfailed",
    })

# The keys a server puts a machine-readable auth code in.
_AUTH_REFUSAL_CODE_KEYS = ("error", "code", "errorCode", "status")


def _auth_refusal_nodes(doc: object) -> list:
    """The standard error containers, ONE level deep, and nothing else.

    Not a search of the document: a walk of the three shapes servers actually
    use to carry an error, so the vocabulary is read where the error IS.
    `{"error": {...}}` (Azure, MediaWiki, JSON-RPC), `{"errors": [...]}`
    (GraphQL, JSON:API) and a bare array root (Salesforce) say the same thing as
    a flat body in a different house style -- and those ecosystems answer at 200
    by construction, which is the only status that reaches this gate at all.

    DEPTH IS BOUNDED AT ONE ON PURPOSE. A recursive walk reaches the `_source`
    of a log-search hit and the `fields` of an issue-tracker record, which are
    payload QUOTING an error rather than an error.
    """
    nodes = []
    if isinstance(doc, dict):
        nodes.append(doc)
        inner = doc.get("error")
        if isinstance(inner, dict):
            nodes.append(inner)
        seq = doc.get("errors")
        if isinstance(seq, list):
            nodes.extend(m for m in seq if isinstance(m, dict))
        # why-not also walk `errorMessages`: it was here and was DEAD. Jira, the
        # API it was added for, emits a list of STRINGS, and this walk only
        # yields dicts -- so it contributed a node for no real input. Measured
        # 2026-09-04. Jira's refusal stays a known miss, honestly, rather than
        # being covered by a branch that never fires.
    elif isinstance(doc, list):
        nodes.extend(m for m in doc if isinstance(m, dict))
    return nodes


def _graphql_error_paths(doc: dict) -> tuple:
    """`(implicated, complete)` for the `errors[]` entries of a GraphQL response.

    `implicated` is the set of top-level field names an error names WHOLLY.
    `complete` is False when any entry gives no usable path, and the caller must
    then ignore `implicated` entirely.

    TWO RULES, each one a measured false result rather than a refinement:

    A PATHLESS ERROR IS REQUEST-LEVEL AND IMPLICATES EVERYTHING. GraphQL emits
    `path` only for an error raised while resolving a field; an authentication
    failure raised before execution has none. Mixing the two -- one error with a
    path, one without -- made every field the pathless error covered look
    unimplicated, so a total refusal read as a partial result and came back as a
    FALSE SUCCESS. That is the expensive direction, and it was round 3's
    critical.

    A PATH DEEPER THAN ONE ELEMENT NAMES A LEAF, NOT THE FIELD. `["user",
    "email"]` means `user` resolved and one property under it did not, so `user`
    is NOT implicated -- its siblings are real content. Treating `path[0]` as
    the whole implication discarded them.
    """
    implicated = set()
    complete = True
    for err in doc.get("errors") or []:
        if not isinstance(err, dict):
            complete = False
            continue
        path = err.get("path")
        if not (isinstance(path, list) and path and isinstance(path[0], str)):
            complete = False
            continue
        if len(path) == 1:
            implicated.add(path[0])
    return implicated, complete


def _has_resolved_data(doc: object) -> bool:
    """Did the server successfully resolve SOME of what was asked for?

    GraphQL answers 200 with BOTH `data` and `errors` when one field was refused
    and the rest succeeded -- a partial result, not a refusal. Reading only the
    error there discards content the caller asked for and already received,
    which is the EXPENSIVE direction for this gate.

    SCOPED TO THE GraphQL ENVELOPE: an `errors` LIST must be present. `data` is
    NOT a GraphQL-exclusive key, and assuming it was disabled the gate outright
    for WordPress REST -- the most widely deployed REST API there is -- which
    answers `{"code": "rest_not_logged_in", "message": ..., "data": {"status":
    401}}`. That `data` is error METADATA, and treating it as resolved content
    waved the whole refusal through. Regression introduced by the round-1 fix
    and caught by cross-family review round 2, 2026-09-04.

    WHICH FIELDS COUNT AS RESOLVED. A field the errors do not implicate is
    resolved even when its value is legitimately null, so `path` is read when
    every error provides one. When ANY error is pathless the failure is
    request-level and the path logic is abandoned, not patched: the fallback is
    at least one NON-NULL value, which keeps the common total-auth-failure shape
    `{"data": {"viewer": null}, "errors": [...]}` a refusal.
    """
    if not isinstance(doc, dict) or not isinstance(doc.get("errors"), list):
        return False
    if "data" not in doc:
        return False
    data = doc["data"]
    if isinstance(data, dict):
        implicated, complete = _graphql_error_paths(doc)
        if complete and implicated:
            return any(key not in implicated for key in data)
        return any(v is not None for v in data.values())
    if isinstance(data, list):
        return any(v is not None for v in data)
    return data is not None


def _json_auth_refusal(body: str) -> bool:
    """Is this 2xx JSON body an authentication refusal rather than content?

    Used ONLY by tier 0's verbatim branch, to refuse a success. Read the block
    comment above before widening anything: every arm this predicate does NOT
    have was measured out, not forgotten.

    ACCEPTED FALSE POSITIVES, irreducible for a predicate that sees only the
    body: a documentation endpoint whose payload IS a refusal shape --
    `/reference/errors/unauthorized`, and the same catalogue written as
    `{"errors": [{"code": "unauthorized", "meaning": ...}]}` -- and a flat i18n
    bundle carrying `{"error": "unauthorized", "message": "Sign in to
    continue"}`. Separating those needs a payload classifier, which is more
    machinery than this gate is worth.

    KNOWN MISSES, stated rather than chased: a refusal whose only evidence is
    prose (`{"message": "Could not authenticate you."}`), and any non-JSON body
    -- a `text/plain` "Unauthorized" and a SOAP fault at 200 take this same
    verbatim path and no JSON predicate reaches either.
    """
    try:
        doc = json.loads(body or "")
    except (ValueError, RecursionError):
        return False
    if _has_resolved_data(doc):
        # A partial result. The caller keeps what the server did return.
        return False
    for node in _auth_refusal_nodes(doc):
        for key in _AUTH_REFUSAL_CODE_KEYS:
            val = node.get(key)
            if isinstance(val, str) and val.strip().lower() in _AUTH_REFUSAL_CODES:
                return True
        ext = node.get("extensions")
        if isinstance(ext, dict):
            # GraphQL's own home for the machine-readable code. Without this arm
            # UNAUTHENTICATED is invisible, and GraphQL answers 200 ALWAYS -- so
            # the ecosystem most characteristic of this defect would be missed.
            code = ext.get("code")
            if isinstance(code, str) and code.strip().lower() in _AUTH_REFUSAL_CODES:
                return True
    return False


def _login_form(body: str, *, facts: _MarkupFacts | None = None) -> bool:
    """LOGIN_FORM (spec S3.4): form evidence AND a sign-in <title>, never either
    alone. BOTH HALVES ARE READ OFF A PARSE -- see `_MarkupFacts`. A caller
    already inspecting that same body may supply its parsed facts."""
    if facts is None:
        facts = _markup_facts(body)
    if facts.title is None:
        return False
    if not _is_login_title(facts.title):
        return False
    return (any(_is_password_input(a) for a in facts.inputs)
            or any(_is_login_form_tag(a) for a in facts.forms))


def access_gate_hint(body: str, content_type: str) -> bool:
    """Return an HTTP-only structural hint requiring browser confirmation.

    Source markup cannot establish computed visibility or legitimate access.
    Only the existing login-form structure or a modal/dialog/form containing
    the shared restriction-and-control combination warrants confirmation.
    """
    if "html" not in (content_type or "").lower():
        return False
    facts = _markup_facts(body)
    return (_login_form(body, facts=facts)
            or any(_access_gate_match(candidate["text"], candidate["controls"])
                   for candidate in facts.access_gate_candidates))


def escalation_verdict(*, tier: str, status: int | None,
                       headers: dict[str, str],
                       set_cookie: list[str],
                       hops: list[tuple[int, str, str]],
                       body: str, content_type: str,
                       requested_url: str, final_url: str) -> str:
    """Diagnose WHAT STOPPED the fetch (spec S3). Pure; no I/O.

    Returns one of `CONTINUE`, `CAPTCHA_CHALLENGE`, `NEEDS_LOGIN`, `HARD_BLOCK`,
    `RATE_LIMITED`. `CONTINUE` absorbs every ambiguous case and means run the
    cascade exactly as it does today.

    `tier` is a `state.TIERS` name. `headers` keys are lowercased here, so the
    caller may pass them in whatever case the transport produced. `set_cookie` is
    the RAW multi-valued list -- a flattened dict loses every cookie but the last,
    which can only make a vendor set FALSE, i.e. bias toward CONTINUE. `hops` is
    `(status, from_url, location)` per redirect, with `location` unresolved.

    PRECEDENCE, which is the spec's guard clauses in S3.3 and S3.4 with TWO
    ruled overrides: HARD_BLOCK, then RATE_LIMITED, then the two REDIRECT-INTO-
    LOGIN arms, then CAPTCHA_CHALLENGE, then the remaining NEEDS_LOGIN arms.
    Both terminal verdicts are decided ABOVE Rule B, so they are answered at
    every tier including tier 0. `LOGIN_HOP` and the final-URL arm sit above the
    captcha block by ruling (2026-09-03) -- see the comment there; every other
    login arm sits below it.

    THE SECOND OVERRIDE IS INSIDE THE RATE_LIMITED BLOCK RATHER THAN A MOVE OF
    IT (2026-09-04). A `Retry-After` HEADER alone no longer outranks a document
    that is POSITIVELY A CHALLENGE: the header is advisory and legal on any
    status, a 503 carrying one is the ordinary answer a Cloudflare edge gives a
    client that failed a challenge, and RATE_LIMITED WAS TERMINAL AT THE TIME --
    so the header was ending the cascade on documents that were plainly
    challenges. (It stopped being terminal on 2026-09-04; this repair predates
    that and stands on its own -- a challenge should not be CALLED a rate limit
    whether or not the label ends the run.) A `429`
    status and Cloudflare's own `error code: 1015` are UNCHANGED and still read
    RATE_LIMITED whatever the body says -- which since 2026-09-04 no longer means
    the cascade stops there. Read the block for the evidence-strength ordering
    that decides which verdict is which.
    """
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    body = body or ""
    vendors = _vendors_present(h, _cookie_names(set_cookie))
    # Parsed only when PerimeterX actually answered. The vendor gate lives here
    # rather than being repeated as a conjunct on each of the two arms below:
    # an empty set is what "PerimeterX did not serve this" means, so no arm can
    # read the actions without the presence test having passed.
    #
    # TWO SETS, NOT ONE, AND THAT IS THE POINT -- the terminal arm and the
    # window arm read DIFFERENT needles because their cheap mistakes point in
    # opposite directions. See `_px_captcha_actions` for the rule and the cost
    # of each; each call site below states its own direction.
    px_served = "perimeterx" in vendors
    px_block_actions = (_px_captcha_actions(body, segment_bounded=False)
                        if px_served else frozenset())
    px_window_actions = (_px_captcha_actions(body, segment_bounded=True)
                         if px_served else frozenset())

    # HARD_BLOCK before RATE_LIMITED: a 451 is a legal block that no amount of
    # waiting clears, so it outranks a `Retry-After` that happens to ride along.
    # The spec orders neither against the other; it orders both before the rest.
    #
    # `px_block_actions` IS THE UNBOUNDED SET. Over-permission here ends a
    # cascade early -- one lost fetch. Over-refusal lets the fetch climb toward
    # the headed window, which is the expensive direction (C-3/C-4). So a
    # PerimeterX-protected origin serving `recaptcha.js?a=b` reads HARD_BLOCK
    # here, deliberately, and the window arm below does NOT read this set.
    if (status == 451
            or "b" in px_block_actions
            or ("cloudflare" in vendors
                and _CF_HARD_BLOCK_RE.match(body.strip()) is not None)):
        return "HARD_BLOCK"

    # RATE_LIMITED. THIS VERDICT NO LONGER ENDS THE CASCADE -- changed
    # 2026-09-04, in `fetch.py`, not here. Every "is terminal" further down this
    # function describes what it did BEFORE that change; the arms themselves are
    # unchanged and still correct.
    #
    # What changed and why: a rate limit CAN be a fact about the client rather
    # than about the URL -- not always, and the universal that once stood here
    # was withdrawn (review round 1). An origin keying its quota by source IP
    # puts every tier in one bucket. The cascade climbs anyway because the
    # costs are asymmetric, not because the limit is always client-specific.
    #
    # Measured the same day -- marriott.com answers plain HTTP with an
    # empty 403 carrying `Retry-After: 28800` and Akamai Bot Manager cookies,
    # and hyatt.com answers headless Chrome with 429, while the SAME URL gives
    # plain HTTP a different status. Treating that as terminal meant marriott
    # never tried headless and hyatt never reached headed, which is the opposite
    # of the owner's rule that a fetch the lower tiers lost may escalate.
    # `fetch.py` now carries the diagnosis forward and still reports it, with
    # its `Retry-After`, if nothing above succeeds.
    #
    # THE EVIDENCE BAR HERE IS UNCHANGED AND STILL MATTERS: the verdict must
    # rest on the origin SAYING it rate-limited us, not on a header that merely
    # rode along. A wrong RATE_LIMITED no longer ends a run, but it is still the
    # string the caller is told.
    #
    # A CHALLENGE DOCUMENT IS NOT A RATE LIMIT -- REPAIRED 2026-09-04, AND IT WAS
    # THE COMMON PATH. Before this, the mere PRESENCE of `Retry-After` at any
    # status outranked the whole captcha and login block below. Measured on this
    # predicate: a Cloudflare interstitial carrying `cf-browser-verification` and
    # `/cdn-cgi/challenge-platform/h/`, served as 503 with `Retry-After: 30`,
    # returned RATE_LIMITED, while the BYTE-IDENTICAL body without the header
    # returned CAPTCHA_CHALLENGE. Since RATE_LIMITED breaks the cascade at this
    # rung, that cost the headed tier, cost the cdp rung, and produced NO ASK at
    # all -- R1 and R5 in one line. A 503 with `Retry-After` is the ordinary
    # answer a Cloudflare edge gives a client that failed a challenge, so this
    # was the ordinary shape rather than a corner.
    #
    # THE PRECEDENCE IS BY EVIDENCE STRENGTH, and the three arms below are not
    # equal:
    #   * `status == 429` is the ORIGIN's own machine-readable "too many
    #     requests". A body cannot launder a status line.
    #   * `error code: 1015` beside Cloudflare PRESENCE is the EDGE naming its
    #     own rate limit, and vendor presence is header/`server`/cookie-only
    #     (`_vendors_present`), so a page that merely quotes the code cannot
    #     reach this arm.
    #     IT IS DELIBERATELY NOT GATED ON THE DOCUMENT, and that is measured
    #     rather than cautious: Cloudflare's real 1015 page says "Sorry, you have
    #     been blocked" and "This website is using a security service to protect
    #     itself", BOTH of which are `_CLIENT_DEMANDS` phrases, so the genuine
    #     rate-limit page reads as positively-a-challenge. Gating this arm would
    #     therefore have un-terminated the single case the verdict exists for.
    #   * `Retry-After` is ADVISORY, legal on any status, and says WHEN to come
    #     back rather than WHAT stopped the fetch. It decides RATE_LIMITED only
    #     when the document is not positively a challenge.
    #
    # THE RESIDUAL, STATED PLAINLY: a challenge document served WITH status 429
    # still reads RATE_LIMITED. Deliberate -- 429 is the origin declaring the
    # reason -- and MUCH CHEAPER since 2026-09-04, because the cascade now climbs
    # past this verdict: the cost is a mislabelled diagnosis, no longer a lost
    # fetch. It matches what Cloudflare actually returns: its rate-limit
    # BLOCK action answers 429 with the 1015 page, while its challenge actions
    # serve the interstitial on 403. A measurement of a 429 interstitial would
    # be grounds to revisit this arm; no such capture exists today.
    #
    # `classify_failure(429, ...)` USED TO return `origin_4xx_block`, which is
    # PERSISTENT and walked the domain toward the headed tier; the same change
    # gave 429 its own `rate_limited` class, so the blocklist no longer promotes
    # on one. The two halves are independent -- this block sits above Rule B and
    # is answered at every tier, `classify_failure` runs wherever a status is
    # known.
    # THE SUPPRESSOR MUST COVER EVERY WAY THE CAPTCHA BLOCK BELOW CAN FIRE, not
    # just the first one. It asked `_document_is_positively_a_challenge` alone
    # until review round 3, while the captcha block's widget arm accepts a
    # SECOND evidence shape -- so a shell-shaped wall carrying a widget and a
    # `Retry-After` read RATE_LIMITED, which WAS terminal at the time, and the
    # run ended below the headed rung with no diagnosis and no ask. Both shapes are now
    # named by one predicate each and both are consulted here.
    #
    # The other two arms stay UNGATED and that is deliberate. `status == 429` is
    # the origin declaring the reason in a status line no body can launder.
    # Cloudflare's real 1015 page carries `_CLIENT_DEMANDS` phrases ("you have
    # been blocked"), so it reads as positively-a-challenge -- gating that arm
    # would un-terminate the exact case this verdict exists for. Measured, and
    # pinned by a test.
    if (status == 429
            or ("cloudflare" in vendors and "error code: 1015" in body)
            or ("retry-after" in h
                and not _document_is_positively_a_challenge(body)
                and not _widget_arm_fires(status, body))):
        return "RATE_LIMITED"

    # RULE B, NARROWED 2026-09-03 TO THE WINDOW VERDICTS -- the two blocks above
    # are decided at EVERY tier. This is the same correction the empty-headers
    # guard took a day earlier, made twice because the first fix was not
    # generalised beyond the guard it was about.
    #
    # Rule B exists so that a CHALLENGE seen at tier 0 cannot open a window
    # before tier 1 -- the tier that clears a non-interactive challenge by
    # itself -- has run. Neither verdict above opens anything: both END the
    # cascade. Held behind Rule B they cost a correct diagnosis, because a tier-0
    # 429 climbed the whole ladder and then, under `--max-tier http`, returned
    # generic exhaustion with no machine-readable class and no `Retry-After` for
    # the caller to act on. A 429 or a 451 is DIAGNOSED on STATUS alone and needs
    # no browser result to be believed. (Only the 451 still ENDS the cascade;
    # `fetch.py` climbs past a rate limit since 2026-09-04 and reports it at
    # exhaustion. What is decided at tier 0 here is the verdict, not the stop.)
    #
    # THE BOUNDARY, stated because moving a block moves everything it carried
    # (plan S8.9): the VENDOR arms beside those two statuses are now CONSULTED at
    # tier 0 as well.
    #
    # CONSULTED, NOT DECIDING -- the earlier wording here said "now decide at
    # tier 0 as well" and that sentence was FALSE. Corrected 2026-09-03 by
    # reading every `TierResult` construction on the tier-0 failure paths: the
    # body `cascade_fetch` hands this function (fetch.py:660) is `''` on every
    # tier-0 non-2xx -- `_tier_http_urllib` (tiers.py:809) and
    # `_tier_http_impersonate` (tiers.py:1118) -- and `""` on
    # `_process_http_response`'s 2xx verbatim-text branch (tiers.py:699), so
    # neither BODY-derived vendor arm -- the PerimeterX action set or the
    # Cloudflare error-code line -- can fire at tier 0 on the shapes those
    # blocks actually arrive in. What genuinely moved to tier 0 is the STATUS
    # half: a 429 or a 451 now ends the cascade there instead of climbing it,
    # which is the whole of the diagnosis this narrowing bought.
    #
    # Taken deliberately: both verdicts are terminal and neither opens a window,
    # and the alternative -- splitting each block by evidence type -- inverts
    # HARD_BLOCK over RATE_LIMITED on a response carrying both.
    #
    # Only observed browser tiers can diagnose a session/challenge. HTTP,
    # disabled Copilot and unknown names supply no such browser evidence.
    if tier not in ("headless", "headed", "cdp"):
        return "CONTINUE"

    # NO WINDOW VERDICT WITHOUT HEADERS. Spec S3.8 -- tier 1's headers are
    # CONDITIONAL, since `_navigate_capture` returns None and the tier falls back
    # to a plain `goto`, which supplies none at all.
    #
    # NARROWED BY RULING 2026-09-03 from "no verdict" to "no WINDOW verdict". The
    # rule's own stated purpose is "never a window on missing evidence", and the
    # two verdicts above open nothing -- they END the cascade. Their primary arms
    # are status-only (`== 429`, `== 451`) and were never header-derived, so
    # suppressing them bought no safety and cost a correct diagnosis: a headerless
    # 429 went on to copilot, a call no tier above can make succeed.
    #
    # THE PLACEMENT IS THE ENFORCEMENT. Every path below returns
    # CAPTCHA_CHALLENGE, NEEDS_LOGIN or CONTINUE and nothing else, so this one
    # line covers both window verdicts and covers any arm added below it. It is
    # not a conjunct anyone has to remember to repeat. Moving it back above the
    # two blocks is what the ruling reversed.
    if not h:
        return "CONTINUE"

    # Spec S3.4's status exclusion, hoisted because TWO blocks below need it now:
    # a proxy-auth 407 or a network-auth 511 is not a site login and the user's
    # logged-in Chrome does not hold it. 451 is unreachable (HARD_BLOCK above)
    # and kept because the spec writes it.
    login_eligible = status not in (407, 511, 451)

    # THE REDIRECT-INTO-LOGIN ARMS OUTRANK THE CAPTCHA ARMS, and they are the
    # only login arms that do. RULED 2026-09-03, overriding S3.4's mechanical
    # `not CAPTCHA_CHALLENGE` guard in favour of S3.4's own prose ("LOGIN_HOP
    # outranks every bot-wall signal", "highest precision").
    #
    # Solving a captcha on a page you were REDIRECTED to does not get you the
    # page -- it gets you to a login form. And the two errors are asymmetric:
    # calling a login wall a captcha opens a window that ALSO does not work,
    # while calling a captcha a login costs one fetch and opens nothing.
    #
    # THE FINAL-URL ARM WAS PROMOTED TO JOIN `_login_hop` HERE on 2026-09-03,
    # because `_login_hop` is unreachable at every tier that computes a verdict
    # (see its docstring) -- so the ruling above had no producible input and the
    # only tests exercising it fed the predicate a shape no transport builds.
    # `final_url` IS produced at tier >= 1, and it is the same evidence one
    # observation later: the cascade asked for A and was answered from a login
    # URL B. Safe to promote only because `_is_login_url` was narrowed the same
    # day to identity-provider hosts, the OAuth query tuple, `SAMLRequest` and
    # specific path tails; against the bare one-word segment list it removed,
    # this promotion would have put `/docs/auth/reference` above the captcha
    # arms.
    #
    # THE OTHER LOGIN ARMS STAY BELOW THE CAPTCHA BLOCK, deliberately. A login
    # form rendered ON a captcha interstitial is a captcha; only a redirect
    # carries the "you were sent here" evidence.
    if login_eligible and (_login_hop(hops)
                           or (final_url and final_url != requested_url
                               and _is_login_url(final_url))):
        return "NEEDS_LOGIN"

    # CAPTCHA_CHALLENGE (spec S3.3). SIX ARMS: three name the vendor that SERVED
    # the response (AWS, DataDome, PerimeterX), two name Cloudflare's own EDGE
    # evidence, and the sixth names an interactive widget on a document the
    # origin REFUSED to serve.
    #
    # WHAT THIS VERDICT COSTS -- CORRECTED 2026-09-04, AND THE PARAGRAPH THAT
    # STOOD HERE WAS FALSE. It claimed that "headed is entered on every
    # non-login verdict and cdp once headed has run, so CAPTCHA_CHALLENGE and
    # CONTINUE ADMIT THE SAME TIERS", and concluded that a false positive
    # "opens nothing the CONTINUE path would not also open; it costs one
    # unnecessary ask". The first half of that is right and the second does not
    # follow. Read `fetch._gate_allows`: cdp is entered on `verdict ==
    # "NEEDS_LOGIN" or (captcha_seen and headed_attempted)`, and `captcha_seen`
    # is set by THIS VERDICT and by nothing else. So on a non-login run
    # CAPTCHA_CHALLENGE is the ONLY thing that unlocks the cdp rung -- CONTINUE
    # does not admit it -- and the rung's no-target path PAGES THE OWNER:
    # `_cdp_ask("a captcha the headed browser could not clear")`. A false
    # positive is an ask to go start a browser for a page that is simply
    # missing. That is R2, not a spare adjective.
    #
    # THE FALSE NEGATIVE COSTS SOMETHING TOO, and it points the other way (R1):
    # a wall this block fails to NAME loses the cdp rung and the ask with it. It
    # does not lose the fetch -- headed is entered on every non-login verdict,
    # CONTINUE included -- so the two errors are not symmetric. Neither is free.
    # Each arm below states which way it is aimed.
    #
    # AND THAT IS THE WHOLE OF WHY CLOUDFLARE HAS ARMS AGAIN. Tie-break 1
    # excluded it because `cf-mitigated: challenge` is uniform by contract
    # (challenge is its only valid value, set for every Challenge Page type) and
    # a Managed Challenge renders the Turnstile widget too, so neither marker
    # separates the challenge tier 1 clears by itself from one that needs a
    # human; CF challenges are also intermittent (tie-break 7), so a tier-1
    # failure is not evidence a person is required. EVERY ONE OF THOSE FACTS IS
    # STILL TRUE. What changed is what follows from them: being wrong no longer
    # decides whether a WINDOW opens -- headed runs on CONTINUE too -- it
    # decides the cdp rung and the ask. Both arms survive that price because
    # neither can be laundered: one is an EDGE RESPONSE HEADER and the other is
    # the interstitial's own orchestration path, so when they fire it is
    # Cloudflare itself saying it served a challenge. Akamai and
    # Imperva still have NO arm -- tie-break 5, both marker sets are
    # RECONSTRUCTIONS rather than measurements, and a reconstruction is not
    # evidence at any price.
    #
    # THE SIXTH ARM IS THE DELETED GENERIC ARM, NARROWED TWICE -- AND THE
    # SECOND NARROWING IS THE 2026-09-04 REPAIR. The arm deleted 2026-09-03 was
    # "a window-eligible vendor is present AND non-2xx AND some interactive
    # widget URL is in the body", and its measured false positive was a 403
    # denial template carrying a reCAPTCHA feedback form. THE NON-2XX CONJUNCT
    # DOES NOT EXCLUDE THAT CASE -- the deleted arm already had it -- so when
    # the arm was restored that false positive came back, WIDER, the vendor
    # conjunct dropped, excused by the cost paragraph now corrected above.
    #
    # AT THE REAL PRICE IT IS NOT EXCUSABLE, because the shape is ordinary
    # rather than exotic. Any site whose GLOBAL template loads
    # `recaptcha/api.js` for a newsletter or a contact form serves that template
    # on its 404 as well -- plain WordPress and marketing sites, by the
    # thousand. Tier 0 returns that 404 with an empty body and reads CONTINUE;
    # tier 1 returns the same 404 with the full DOM, the arm fires, the verdict
    # sticks in `captcha_seen`, and the owner is asked to start a CDP browser
    # for a page that does not exist.
    #
    # SO THE ARM NOW ASKS FOR THE FACT IT WAS ASSUMING: that the origin served
    # a CHALLENGE INSTEAD OF THE PAGE, not merely that it refused a page which
    # happens to mount a widget. `_document_refuses_with_a_challenge` is that
    # conjunct -- read its docstring for the two forms of evidence it takes, and
    # for why the widget's own MOUNT marker is not allowed to be one of them.
    #
    # WHAT THE RESTORATION BOUGHT IS KEPT: the 2xx side, where a contact form on
    # a page the origin SERVED is still not a captcha (the `page_served` rule
    # `_challenge_reason` already applies), and the whole-value host/path
    # compare, which stops `notchallenges.cloudflare.com` and
    # `www.google.com/maps/api.js` from reaching the arm at all.
    #
    # THE ARM IS AIMED AT THE FALSE POSITIVE, deliberately, and pays for it on
    # the other side: a wall with no challenge title, no demand phrase, no
    # interstitial markup and a fully rendered page around the widget now reads
    # CONTINUE and loses the cdp ask. Headed still runs on CONTINUE, so the
    # fetch itself keeps every tier it had.
    if ((status == 405 and h.get("x-amzn-waf-action", "").strip().lower() == "captcha")
            # DataDome's ONLY measured discriminator, from the vendor's own
            # client: c.js builds the iframe at /captcha/, i.js at /interstitial/.
            # Same status, same headers, same title, bodies within 3 bytes.
            or ("datadome" in vendors and _dd_captcha_client(body))
            # PerimeterX: `a=c`/`a=j` are the interactive actions, `a=b` is the
            # block handled above. The param is the whole discriminator -- the
            # 403s are otherwise byte-identical -- so it is compared as a whole
            # VALUE. See `_px_captcha_actions` for the prefix collision that
            # made `a=callback` a window.
            #
            # `px_window_actions` IS THE SEGMENT-BOUNDED SET, and this arm is
            # the ONLY reader of it. Over-permission here does not open a
            # window -- headed runs on CONTINUE too -- but it does unlock the
            # cdp rung and page the owner (see the corrected cost paragraph in
            # the block header), so the bounded needle is not merely tidiness.
            # It stays, and nothing was measured that would justify widening
            # it. The terminal arm above keeps the UNBOUNDED
            # set for a cost model of its own, stated at its own site.
            or bool(px_window_actions & {"c", "j"})
            # CLOUDFLARE, ARM 1 -- an EDGE RESPONSE HEADER, and that is what
            # separates it from every widget marker in this file. `cf-mitigated`
            # is set by Cloudflare IN FRONT OF the origin, so a site cannot put
            # it on its own contact form; there is nothing here to launder.
            # Cloudflare documents `challenge` as its only value, so the
            # comparison is equality on the whole value.
            or ("cloudflare" in vendors
                and h.get("cf-mitigated", "").strip().lower() == "challenge")
            # CLOUDFLARE, ARM 2 -- the interstitial's own orchestration path.
            # The SAME literal `_CHALLENGE_MARKUP` carries, with the same `/h/`
            # restriction and for the same measurement: Bot Fight Mode injects
            # `/cdn-cgi/challenge-platform/scripts/jsd/main.js` into ORDINARY
            # pages, and blog.crawlex.net serves that on a plain 200 carrying
            # 31,947 characters of article text (2026-08-30). The `/h/` form is
            # absent from every ordinary 200 measured.
            or ("cloudflare" in vendors
                and "/cdn-cgi/challenge-platform/h/" in body)
            # ARM 3 -- an interactive widget, on a document the origin refused
            # AND that is shaped like a challenge rather than like a page.
            # The non-2xx conjunct is `_challenge_reason`'s `page_served` rule:
            # on a 2xx the origin served the page and a widget on it is a form.
            # On its own it is NOT a defence against laundering -- a site's own
            # 404 carries the site's own template -- which is what the third
            # conjunct is for; read the block header above and that predicate's
            # docstring.
            or _widget_arm_fires(status, body)):
        return "CAPTCHA_CHALLENGE"

    # The remaining NEEDS_LOGIN arms (spec S3.4), all behind the captcha block.
    if not login_eligible:
        return "CONTINUE"

    # THE CREDENTIAL VETO, AND ITS PLACEMENT IS THE ENFORCEMENT. A body naming an
    # API key, an access token or an OAuth client credential is not a login, and
    # NEEDS_LOGIN drives the user's logged-in Chrome (C-3/C-4) at a credential no
    # browser session supplies.
    #
    # It lived inside `_json_unauth` until round-3 finding 1, where a 401 with
    # `WWW-Authenticate: Basic` and `{"message":"Invalid API key provided"}`
    # short-circuited on the first arm and never reached it. As one line HERE it
    # covers every arm below and every arm a later change adds below -- it is not
    # a conjunct anyone has to remember to repeat, which is the same reason the
    # empty-headers guard sits above these blocks rather than inside three of
    # them.
    #
    # IT DOES NOT COVER THE TWO REDIRECT ARMS ABOVE, deliberately. Vetoing one of
    # those does not end at CONTINUE -- it falls through to the captcha block,
    # which opens a HEADED window. That trades one window for another instead of
    # closing anything, and a body naming an API key is not what an origin serves
    # from a login URL it redirected you to.
    if _names_non_browser_credential(body):
        return "CONTINUE"

    if (_www_auth_cookieable(status, h)
            or _json_unauth(body)
            or ("html" in (content_type or "").lower() and _login_form(body))):
        return "NEEDS_LOGIN"

    return "CONTINUE"

