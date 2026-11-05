"""The captcha arms `escalation_verdict` gained on 2026-09-04 (extract.py).

THREE NEW ARMS AND A NEW COST MODEL. Under the cascade shipped this day headed
is entered on every non-login verdict and cdp once headed has run and failed, so
CAPTCHA_CHALLENGE and CONTINUE admit exactly the SAME tiers. What the verdict
now selects is the DIAGNOSIS -- the `failure_class` on the envelope and the
wording of the "start the CDP browser" ask. A false positive costs one
unnecessary sentence; it does not open a window, because the CONTINUE path
opens the same ones.

THE CONTROLS ARE STILL THE POINT, on a new footing. They no longer protect the
owner's screen. They protect the diagnosis: a predicate that answers
CAPTCHA_CHALLENGE to every 403 has stopped diagnosing anything, and the ask it
writes then tells the owner to go solve a captcha that is not there.

Each behavioural test names, in a comment, the single change to `extract.py`
that would make it pass wrongly.

No network and no browser: the module under test is a pure function.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import extract

HTML = "text/html; charset=utf-8"

# Non-empty and carrying no vendor signal. Non-empty matters: an EMPTY header
# dict is CONTINUE outright (spec S3.8), so a case meaning to exercise a body
# arm has to supply something.
NEUTRAL = {"Content-Type": HTML}
# A real Cloudflare edge response. PRESENT, not a wall.
CF = {"Server": "cloudflare", "CF-RAY": "8d1f0a2b3c4d5e6f-SJC", "Content-Type": HTML}

# ~30k characters of ordinary article prose. Length decides nothing in this
# predicate -- it is here so the 2xx controls are the shape they claim to be: a
# real page that happens to mount a widget, not a stub that could pass for an
# interstitial.
ARTICLE = ("<p>The quick brown fox jumps over the lazy dog, and the article "
           "continues at length about nothing in particular.</p>\n") * 250

# The client each family actually serves. THE PATHS ARE VERSIONED and the
# predicate compares a PREFIX for that reason; pinning `/turnstile/v0/api.js`
# is a needle that goes stale the day the vendor ships v1.
TURNSTILE = '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
RECAPTCHA = '<script src="https://www.google.com/recaptcha/api.js"></script>'
HCAPTCHA = '<script src="https://js.hcaptcha.com/1/api.js"></script>'

# Cloudflare's interstitial orchestration path. The `/h/` is load-bearing --
# Bot Fight Mode injects `/cdn-cgi/challenge-platform/scripts/jsd/main.js` into
# ORDINARY pages, and `_CHALLENGE_MARKUP` carries the same restriction for the
# same measured reason.
CF_INTERSTITIAL = ('<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/'
                   'chl_page/v1?ray=a33166ad6ff12c2d"></script>')
CF_BOT_FIGHT = ('<script src="/cdn-cgi/challenge-platform/scripts/jsd/'
                'main.js"></script>')


def _verdict(**kw):
    """`escalation_verdict` with defaults for the arguments a case is not about."""
    args = dict(tier="headless", status=200, headers=dict(NEUTRAL), set_cookie=[],
                hops=[], body="", content_type=HTML,
                requested_url="https://example.com/page", final_url="")
    args.update(kw)
    return extract.escalation_verdict(**args)


def _page(inner, *, prose=""):
    return ("<html><head><title>Just a moment...</title></head><body>"
            + inner + prose + "</body></html>")


# ---- Arm 1: the edge response header ----------------------------------------

def test_cf_mitigated_challenge_is_a_captcha_verdict():
    """`cf-mitigated: challenge` is an EDGE RESPONSE HEADER.

    A site cannot put it on its own document -- it is set by Cloudflare in front
    of the origin -- so unlike every widget marker it cannot be laundered by a
    contact form. Cloudflare documents `challenge` as its only value.
    """
    # MUTATION: delete the `cf-mitigated` disjunct from the captcha `if`.
    assert _verdict(tier="headless", status=403,
                    headers={**CF, "CF-Mitigated": "challenge"},
                    body=_page("")) == "CAPTCHA_CHALLENGE"


def test_cf_mitigated_needs_cloudflare_present_and_the_documented_value():
    """The two conjuncts, each removed once.

    The header is only Cloudflare's if Cloudflare answered, and `challenge` is
    the only value that names a challenge page. Neither control can pass for
    want of a working arm: the first assertion above fires on this exact shape.
    """
    # MUTATION: drop the `"cloudflare" in vendors` conjunct; or compare the
    # header with `in` instead of `==`.
    assert _verdict(tier="headless", status=403,
                    headers={"CF-Mitigated": "challenge", "Content-Type": HTML},
                    body=_page("")) == "CONTINUE"
    assert _verdict(tier="headless", status=403,
                    headers={**CF, "CF-Mitigated": "challenged-elsewhere"},
                    body=_page("")) == "CONTINUE"


# ---- Arm 2: the interstitial's own orchestration path -------------------------

def test_the_challenge_platform_interstitial_path_is_a_captcha_verdict():
    """`/cdn-cgi/challenge-platform/h/` -- measured absent from ordinary 200s.

    This is the same literal `_CHALLENGE_MARKUP` carries, and for the same
    reason: the bare `/cdn-cgi/challenge-platform/` prefix fires on any page
    Bot Fight Mode touched.
    """
    # MUTATION: delete the challenge-platform disjunct; or widen it to the bare
    # `/cdn-cgi/challenge-platform/` prefix, which the second assertion kills.
    assert _verdict(tier="headless", status=403, headers=dict(CF),
                    body=_page(CF_INTERSTITIAL)) == "CAPTCHA_CHALLENGE"
    assert _verdict(tier="headless", status=200, headers=dict(CF),
                    body=_page(CF_BOT_FIGHT, prose=ARTICLE)) == "CONTINUE"


# ---- Arm 3: an interactive widget on a document the origin REFUSED ------------

WIDGETS = [("turnstile", TURNSTILE), ("recaptcha", RECAPTCHA), ("hcaptcha", HCAPTCHA)]


@pytest.mark.parametrize("name,widget", WIDGETS, ids=[w[0] for w in WIDGETS])
def test_an_interactive_widget_is_a_captcha_only_on_a_non_2xx(name, widget):
    """The `page_served` rule `_challenge_reason` already applies, one layer up.

    On a non-2xx the origin did NOT serve the page, so the widget is evidence of
    what it refused with. On a 2xx it served the page and the widget is a form
    on it -- blog.crawlex.net returns 200 with 31,947 characters of article text
    and a Turnstile mount, measured 2026-08-30.
    """
    # MUTATION: delete the `not (200 <= status < 300)` conjunct, which the
    # second assertion kills; or delete the arm, which the first kills.
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    body=_page(widget)) == "CAPTCHA_CHALLENGE"
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=_page(widget, prose=ARTICLE)) == "CONTINUE"


@pytest.mark.parametrize("url", [
    # A host that merely ENDS with the vendor's. The prefilter admits the token
    # -- `containing` is a substring test by design -- and the whole-value
    # comparison after it is what refuses.
    "https://notchallenges.cloudflare.com/turnstile/v0/api.js",
    # The right host, a DIFFERENT path. `/maps/` does not start with
    # `/recaptcha/`, and Google serves a great deal that is not a captcha.
    "https://www.google.com/maps/api.js",
    # THE LEFT BOUNDARY, which is round-2 finding 1's defect in this arm's
    # shape: `/intl/en/policies/recaptcha/terms.html` CONTAINS `/recaptcha/`
    # and does not START with it. A page linking reCAPTCHA's terms without
    # mounting the widget is ordinary boilerplate. MEASURED 2026-09-04: with
    # `prefix in u.path` for `u.path.startswith(prefix)` the whole suite still
    # passed, so this line is the only thing pinning it.
    "https://www.google.com/intl/en/policies/recaptcha/terms.html",
    # The apex hCaptcha host is the vendor's MARKETING site; only its documented
    # client path counts there.
    "https://hcaptcha.com/about",
    # Relative: no host at all, so no pair can match.
    "/recaptcha/api.js",
])
def test_a_widget_url_is_compared_as_whole_values_not_substrings(url):
    """The boundary controls, on the status where the arm is live.

    Each of these fails ONE of the two comparisons. None needs a boundary rule,
    because none is compared as a substring -- the same repair
    `_dd_captcha_client` and `_px_captcha_actions` already took.
    """
    # MUTATION: compare the host with `endswith`, or the path with `in`.
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    body=_page(f'<script src="{url}"></script>')) == "CONTINUE"
    # THE CONTROL (plan S8.8): same status, same headers, a URL that DOES match.
    # Without it every assertion above could pass for want of any working arm.
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    body=_page(RECAPTCHA)) == "CAPTCHA_CHALLENGE"


def test_an_unknown_status_reaches_no_widget_verdict_and_does_not_raise():
    """`status is not None` is a GUARD, not decoration.

    `not (200 <= None < 300)` is a TypeError, and an unhandled raise here fails
    the whole fetch. An unknown status is also missing evidence, and missing
    evidence is CONTINUE -- the same rule the empty-headers guard applies two
    screens up.
    """
    # MUTATION: drop `status is not None` from the widget arm.
    assert _verdict(tier="headless", status=None, headers=dict(NEUTRAL),
                    body=_page(RECAPTCHA)) == "CONTINUE"


def test_rule_b_still_holds_the_new_arms_below_tier_1():
    """Every new arm is a WINDOW verdict and sits under Rule B, unchanged.

    A challenge seen at tier 0 means the browser tier has not been tried, and
    the browser tier is what clears a non-interactive challenge by itself. The
    diagnosis is not worth writing before then.
    """
    # MUTATION: hoist any of the three arms above the `TIERS.index(tier) < 1`
    # guard, the way the 429/451 blocks were hoisted on 2026-09-03.
    for body, headers in ((_page(CF_INTERSTITIAL), CF),
                          (_page(RECAPTCHA), NEUTRAL),
                          (_page(""), {**CF, "CF-Mitigated": "challenge"})):
        assert _verdict(tier="http", status=403, headers=dict(headers),
                        body=body) == "CONTINUE"
