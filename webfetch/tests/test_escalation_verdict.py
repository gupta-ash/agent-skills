"""Pure escalation policy over synthetic response evidence.

Headers, cookies, status, redirects and authored HTML are supplied independently.
Vendor presence alone must not diagnose a captcha. Ordinary pages and embedded
widgets guard over-escalation. Browser JSON cases use a generated pre document
where transport would expose one; explicitly named raw-JSON/unproducible-hop
cases cover predicate behavior, not transport coverage. No captures are shipped."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import extract

from synthetic_pages import PAGES

HTML = "text/html; charset=utf-8"
JSON_CT = "application/json"

# Non-empty and carrying no vendor signal. Non-empty matters: spec S3.8 makes an
# EMPTY header dict `CONTINUE` outright, so a case that means to exercise a body
# arm has to supply something.
NEUTRAL = {"Content-Type": HTML}
# A real Cloudflare edge response. PRESENT, not a wall.
CF = {"Server": "cloudflare", "CF-RAY": "8d1f0a2b3c4d5e6f-SJC", "Content-Type": HTML}

TURNSTILE_WIDGET = '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
# AN ARM READS THESE AGAIN SINCE 2026-09-04 -- `_CAPTCHA_WIDGET_URLS`, conjoined
# with a non-2xx status. The note that stood here said no arm did, which was
# true for exactly one day.
#
# TURNSTILE_WIDGET and RECAPTCHA_WIDGET MATCH that table. HCAPTCHA_WIDGET does
# NOT, and the mismatch is deliberate rather than an oversight: `hcaptcha.com`
# is the vendor's MARKETING apex, so the table admits only its documented
# `/1/api.js` client there, and the real widget iframe is served from
# `newassets.hcaptcha.com`. Left as written so that one case in this file is a
# vendor-adjacent URL the table refuses; `test_extract_captcha_arms.py` carries
# the matching hCaptcha needle.
RECAPTCHA_WIDGET = '<iframe src="https://www.google.com/recaptcha/api2/anchor?k=x"></iframe>'
HCAPTCHA_WIDGET = '<iframe src="https://hcaptcha.com/captcha/v1"></iframe>'
# The DataDome captcha discriminator -- an arm that DOES still fire. Every
# "a widget is not evidence" control needs one of these beside it, or the
# control could pass because no captcha arm works at all (plan S8.8).
DATADOME_CAPTCHA = '<script src="https://ct.captcha-delivery.com/c.js"></script>'

# SYNTHETIC RESPONSE EVIDENCE -- see the module docstring. `x-datadome` is the
# header spec S3.5 records on g2.com's 403; `_pxvid` is one of the measured
# PerimeterX cookie names. Since round-2 finding 3 the BODY cannot supply either
# vendor's presence, so a case that means to exercise a vendor arm has to say
# who answered.
DD_HEADERS = {"X-DataDome": "protected", "Content-Type": HTML}
PX_COOKIES = ["_pxvid=8f2a3c4d5e6f; Path=/; HttpOnly"]
# A 401 carrying a scheme `_www_auth_cookieable` accepts. THIS IS THE
# api.stripe.com SHAPE the spec cites -- `WWW-Authenticate: Basic realm="Stripe"`
# beside a JSON body that names an API key -- and, once the body is the DOM tier
# 1 really builds, it is the ONLY login arm still able to fire on a JSON
# response. Every credential-veto control below is paired with it for that
# reason: without an arm that fires, a control asserting CONTINUE proves
# nothing.
BASIC_401 = {"WWW-Authenticate": 'Basic realm="api"', "Content-Type": JSON_CT}


def _chrome_json_dom(raw):
    """THE BODY TIER 1 ACTUALLY BUILDS for a JSON response.

    `fetch.py:560` passes `result.html`. Tiers 1, 2 and 4 set it from
    `document.documentElement.outerHTML` (`tiers.py:3174`, `:3893`), and Chrome
    renders a JSON or plain-text response as a `<pre>` inside a generated
    document -- so what the predicate receives is never the raw body. Tier 0 is
    the only tier that has a raw body and it sets `html` to the empty string on
    the verbatim path (`tiers.py:700`) and leaves it unset on every non-2xx path
    (`:809`, `:1114`); Rule B computes no verdict there anyway.

    ROUND 4 EXISTS BECAUSE OF THIS FUNCTION'S ABSENCE. Every credential-veto
    control in this file fed the predicate `tier="headless"` with a bare JSON
    `body=`, a pairing no transport produces, and they were green because their
    input was impossible rather than because the veto worked. The veto was
    JSON-only and therefore INERT in production; the shape it was written for --
    401 + `WWW-Authenticate: Basic` + a body naming an API key -- returned
    NEEDS_LOGIN, which drives the owner's logged-in Chrome (C-3/C-4).
    """
    return ('<html><head></head><body><pre style="word-wrap: break-word; '
            'white-space: pre-wrap;">' + raw + '</pre></body></html>')


def _verdict(**kw):
    """`escalation_verdict` with defaults for the arguments a case is not about."""
    args = dict(tier="headless", status=200, headers=dict(NEUTRAL), set_cookie=[],
                hops=[], body="", content_type=HTML,
                requested_url="https://example.com/page", final_url="")
    args.update(kw)
    return extract.escalation_verdict(**args)


def _fixture(name):
    return PAGES[name]



FIXTURE_CASES = [
    ("perimeterx", 403, NEUTRAL, PX_COOKIES, "CAPTCHA_CHALLENGE",
     "PerimeterX interactive: captcha.js?a=c, beside a synthetic _pxvid cookie"),
    ("datadome", 403, DD_HEADERS, [], "CAPTCHA_CHALLENGE",
     "DataDome captcha: ct.captcha-delivery.com/c.js, beside a synthetic x-datadome"),
    ("cloudflare_check", 403, CF, [], "CAPTCHA_CHALLENGE",
     "synthetic Cloudflare interstitial with independently supplied vendor headers"),
    ("cloudflare_platform", 403, CF, [], "CAPTCHA_CHALLENGE", "Cloudflare interstitial"),
    ("cloudflare_form", 403, CF, [], "CAPTCHA_CHALLENGE", "Cloudflare interstitial"),
    ("cloudflare_inline", 403, CF, [], "CAPTCHA_CHALLENGE", "Cloudflare interstitial"),
    ("turnstile_title", 200, CF, [], "CONTINUE",
     "sharpest control: Turnstile WIDGET url and class=cf-turnstile, on a 200"),
    ("title_denied", 403, NEUTRAL, [], "CONTINUE",
     "tie-break 4: bare 403, no vendor marker"),
    ("title_robot", 403, NEUTRAL, [], "CONTINUE",
     "tie-break 4: bare 403, no vendor marker"),
    ("article", 200, NEUTRAL, [], "CONTINUE", "ordinary page"),
    ("short_document", 200, NEUTRAL, [], "CONTINUE", "ordinary page"),
    ("paragraphs", 200, NEUTRAL, [], "CONTINUE", "ordinary page"),
    ("reference", 200, NEUTRAL, [], "CONTINUE", "ordinary page"),
    ("plain_article", 200, NEUTRAL, [], "CONTINUE", "ordinary page"),
]


@pytest.mark.parametrize("name,status,headers,cookies,expected,why",
                         FIXTURE_CASES, ids=[c[0] for c in FIXTURE_CASES])
def test_synthetic_corpus(name, status, headers, cookies, expected, why):
    got = _verdict(tier="headless", status=status, headers=dict(headers),
                   set_cookie=list(cookies), body=_fixture(name),
                   content_type=HTML, final_url="https://example.com/page")
    assert got == expected, f"{name}: {why}"


@pytest.mark.parametrize("name,status,headers,cookies,expected,why",
                         FIXTURE_CASES, ids=[c[0] for c in FIXTURE_CASES])
def test_rule_b_every_synthetic_page_is_continue_at_tier_0(name, status, headers,
                                                    cookies, expected, why):
    """Rule B: below tier 1 no WINDOW verdict is reached, challenge or not.

    A challenge seen at tier 0 means the browser tier has not run, and the
    browser tier is what satisfies a non-interactive JS challenge. Narrowed to
    the window verdicts 2026-09-03 -- see
    `test_rule_b_does_not_suppress_the_terminal_verdicts_either`. No capture in
    the corpus carries a terminal status or a terminal body, so every row here is
    still CONTINUE.
    """
    # MUTATION: delete the browser-tier membership guard.
    got = _verdict(tier="http", status=status, headers=dict(headers),
                   set_cookie=list(cookies), body=_fixture(name),
                   content_type=HTML)
    assert got == "CONTINUE"


def test_unknown_tier_name_is_continue():
    assert _verdict(tier="somethingelse", status=403, headers=dict(NEUTRAL),
                    set_cookie=list(PX_COOKIES),
                    body=_fixture("perimeterx")) == "CONTINUE"


# ---- Missing evidence --------------------------------------------------------

def test_empty_headers_never_yields_a_window():
    """Spec S3.8: tier 1 falls back to a plain `goto` and supplies no headers."""
    assert _verdict(tier="headless", status=403, headers={},
                    set_cookie=list(PX_COOKIES),
                    body=_fixture("perimeterx")) == "CONTINUE"
    assert _verdict(tier="headless", status=401, headers={},
                    body="", content_type=HTML) == "CONTINUE"


def test_empty_headers_do_not_suppress_the_terminal_verdicts():
    """The OTHER HALF of the same rule, RULED 2026-09-03.

    The guard's purpose is "never a window on missing evidence", and neither
    verdict here opens anything -- both END the cascade. Both arms are status-only
    and were never header-derived, so suppressing them bought no safety and cost a
    correct diagnosis: a headerless 429 went on to copilot, a call no tier above
    can make succeed. `tier="headless"` with no headers is the real tier-1
    `goto`-fallback shape.
    """
    # MUTATION: move the guard back above the HARD_BLOCK and RATE_LIMITED blocks
    # (M10 widens it to all five verdicts again).
    assert _verdict(tier="headless", status=429, headers={}, body="") == "RATE_LIMITED"
    assert _verdict(tier="headless", status=451, headers={}, body="") == "HARD_BLOCK"


def test_rule_b_does_not_suppress_the_terminal_verdicts_either():
    """FINDING 7, RULED 2026-09-03 -- the SAME correction as the test above, made
    twice because the first fix was not generalised beyond its own guard.

    Rule B returned `CONTINUE` before the terminal blocks were reached, so a
    tier-0 429 climbed the whole ladder and, under `--max-tier http`, came back
    as generic exhaustion with no machine-readable class and no `Retry-After` for
    the caller to act on. A 429 or a 451 is terminal on STATUS alone and needs no
    browser result to be believed; neither verdict opens anything.
    """
    # MUTATION: put both terminal blocks back below the Rule B tier guard (M17).
    assert _verdict(tier="http", status=429,
                    headers={"Retry-After": "120", "Content-Type": HTML},
                    body="") == "RATE_LIMITED"
    assert _verdict(tier="http", status=451, headers=dict(NEUTRAL),
                    body="") == "HARD_BLOCK"
    # THE CONTROL, and it is the whole of Rule B: the WINDOW verdicts are still
    # suppressed at tier 0. Without this line the two assertions above could be
    # satisfied by deleting Rule B outright -- and without the synthetic cookie
    # the control itself would be satisfied by there being no captcha at all.
    assert _verdict(tier="http", status=403, headers=dict(NEUTRAL),
                    set_cookie=list(PX_COOKIES),
                    body=_fixture("perimeterx")) == "CONTINUE"


# ---- Vendor presence is never sufficient -------------------------------------

def test_cloudflare_presence_without_challenge_markup_is_not_a_window():
    """`cf-ray` and `server: cloudflare` were measured on ordinary 200s AND on
    four genuine auth responses. A veto on them would suppress real auth."""
    # MUTATION: add `("cloudflare" in vendors and not page_served)` to the
    # CAPTCHA arms.
    body = "<html><head><title>Forbidden</title></head><body>Denied.</body></html>"
    assert _verdict(tier="headless", status=403, headers=dict(CF), body=body) == "CONTINUE"


def test_a_widget_url_is_read_on_a_refusal_and_ignored_on_a_page_the_origin_SERVED():
    """REWRITTEN 2026-09-04. The half this test proved is now half wrong.

    Finding 3 deleted the generic co-occurrence arm on 2026-09-03 because
    `VENDOR_PRESENT and non-2xx and INTERACTIVE_IFRAME(body)` is satisfied by a
    403 denial template carrying a reCAPTCHA feedback form, and neither signal
    establishes the widget had anything to do with the failure. THAT REASONING
    IS UNCHANGED AND THE FALSE POSITIVE IS NOW ACCEPTED ANYWAY: the arm it
    argued against opened a window, and the arm restored on 2026-09-04 opens
    nothing the CONTINUE path does not. It costs one unnecessary ask.

    So the non-2xx assertions below are INVERTED, and what this test still
    proves is the two bounds that survive:

      1. On a 2xx the origin SERVED the page, so a widget on it is a form. This
         is `_challenge_reason`'s `page_served` rule and it is the reason the
         openreview 200 capture is still CONTINUE.
      2. THE VENDOR CONJUNCT IS GONE. The verdict is the same with a vendor
         header, with a vendor cookie and with neither -- which is what proves
         no vendor gate crept back in to make the arm look narrower than it is.
    """
    # MUTATION: restore the vendor conjunct to the widget arm (the third
    # assertion kills it); or drop the non-2xx conjunct (the fourth kills that).
    body = f"<html><head><title>Access denied</title></head><body>{RECAPTCHA_WIDGET}</body></html>"
    assert _verdict(tier="headless", status=403,
                    headers={"X-DataDome": "protected", "Content-Type": HTML},
                    body=body) == "CAPTCHA_CHALLENGE"
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=["_pxvid=8f2a; Path=/"], body=body) == "CAPTCHA_CHALLENGE"
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    body=body) == "CAPTCHA_CHALLENGE"
    assert _verdict(tier="headless", status=200,
                    headers={"X-DataDome": "protected", "Content-Type": HTML},
                    body=body) == "CONTINUE"
    # HCAPTCHA_WIDGET names the vendor's MARKETING apex at a path the table does
    # not admit, so it is refused at BOTH statuses -- the whole-value comparison
    # doing its job on a URL that genuinely mentions a captcha vendor.
    hbody = (f"<html><head><title>Access denied</title></head><body>"
             f"{HCAPTCHA_WIDGET}</body></html>")
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    body=hbody) == "CONTINUE"
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=hbody) == "CONTINUE"
    # THE CONTROL (plan S8.8). Without it every assertion above could pass for
    # want of any working captcha arm at all: same vendor, same status, its OWN
    # measured discriminator in place of the widget.
    assert _verdict(tier="headless", status=403,
                    headers={"X-DataDome": "protected", "Content-Type": HTML},
                    body=f"<html><body>{DATADOME_CAPTCHA}</body></html>"
                    ) == "CAPTCHA_CHALLENGE"


@pytest.mark.parametrize("headers,cookies", [
    ({"AKAMAI-GRN": "0.1a2b3c4d.1234567890.abcdef", "Content-Type": HTML}, []),
    ({"Content-Type": HTML}, ["_abck=ABC~-1~YAAQ; Path=/"]),
    ({"X-CDN": "Imperva", "Content-Type": HTML}, []),
    ({"X-Iinfo": "9-12345678-12345679 NNNY CT(1 1 0)", "Content-Type": HTML}, []),
    ({"Content-Type": HTML}, ["incap_ses_1234_5678=abc; Path=/"]),
])
def test_akamai_and_imperva_presence_is_never_itself_a_captcha(headers, cookies):
    """Tie-break 5, RE-AIMED 2026-09-04. Both marker sets are RECONSTRUCTIONS,
    not measurements, so they may only ever evidence CONTINUE, and neither
    vendor has an arm.

    THE OLD BODY CARRIED A WIDGET AND THAT NO LONGER TESTS THIS CLAIM. The widget
    arm restored on 2026-09-04 is VENDOR-BLIND, so a reCAPTCHA mount on a 403
    is a captcha whoever is in front of the origin -- which says nothing about
    Akamai or Imperva. The body below carries no widget and no vendor
    discriminator, so the only thing that could produce a verdict is presence
    itself, which is exactly the claim.
    """
    # MUTATION: give them an arm (M3) -- akamai/imperva presence on a non-2xx.
    body = "<html><head><title>Denied</title></head><body>Denied.</body></html>"
    assert _verdict(tier="headless", status=403, headers=dict(headers),
                    set_cookie=list(cookies), body=body) == "CONTINUE"
    # THE CONTROL (plan S8.8): same vendor evidence, same status, plus a needle
    # an arm DOES read. Without it the assertion above could pass for want of any
    # working captcha arm at all.
    assert _verdict(tier="headless", status=403, headers=dict(headers),
                    set_cookie=list(cookies),
                    body=f"<html><body>{RECAPTCHA_WIDGET}</body></html>"
                    ) == "CAPTCHA_CHALLENGE"


def test_cloudflare_has_captcha_arms_again_but_presence_is_still_not_one():
    """INVERTED 2026-09-04. Cloudflare was excluded by having no arm at all; it
    now has two -- `cf-mitigated: challenge` and the `/cdn-cgi/challenge-
    platform/h/` interstitial path -- and the vendor-blind widget arm reaches a
    Cloudflare 403 as well.

    Tie-break 1's facts did not change: neither marker separates the challenge
    tier 1 clears by itself from one that needs a human. What changed is the
    consequence. Being wrong used to open a window; it now writes an unnecessary
    sentence at the end of a run that had already exhausted every tier.

    WHAT STILL BOUNDS IT is the line below and
    `test_cloudflare_presence_without_challenge_markup_is_not_a_window` above:
    presence is a PRECONDITION, never a marker. `cf-ray` and `server:
    cloudflare` were measured on ordinary 200s and on four genuine auth
    responses, and a verdict keyed on them would mis-diagnose all of those.
    """
    # MUTATION: replace either Cloudflare arm with bare presence on a non-2xx
    # (M2) -- the second assertion kills it.
    body = f"<html><head><title>Just a moment...</title></head><body>{RECAPTCHA_WIDGET}</body></html>"
    assert _verdict(tier="headless", status=403, headers=dict(CF),
                    body=body) == "CAPTCHA_CHALLENGE"
    bare = "<html><head><title>Just a moment...</title></head><body>x</body></html>"
    assert _verdict(tier="headless", status=403, headers=dict(CF),
                    body=bare) == "CONTINUE"


def test_a_second_vendor_changes_nothing_about_a_turnstile_403():
    """RE-AIMED 2026-09-04, AND THE DEFECT IT WAS WRITTEN FOR IS GONE BY
    CONSTRUCTION.

    The original bug was a LAUNDERING one: `x-datadome` put a window-eligible
    vendor in the set while the widget URL matched was entirely Cloudflare's, so
    which second vendor happened to be on the response decided the verdict.
    Measured before the 2026-09-03 removal -- 403 + `server: cloudflare` + the
    widget was CONTINUE, and adding `x-datadome: protected` and nothing else
    made it CAPTCHA_CHALLENGE. Not a contrived shape either: spec S3.5 records
    g2.com answering 403 with both.

    THERE IS NO WINDOW-ELIGIBLE VENDOR SET NOW AND THE WIDGET ARM READS NO
    VENDOR AT ALL, so laundering has nothing to launder through. This test
    pins that: all four responses below carry the same Turnstile mount on the
    same 403 and differ only in who else is present, and all four must agree.
    A vendor conjunct reintroduced anywhere in the widget arm splits them.
    """
    # MUTATION: add any `vendors` conjunct to the widget arm -- every shape
    # below except the one naming that vendor flips.
    body = f"<html><head><title>Just a moment...</title></head><body>{TURNSTILE_WIDGET}</body></html>"
    for headers, cookies in (({**CF, "X-DataDome": "protected"}, []),
                             (dict(CF), ["_pxvid=8f2a; Path=/"]),
                             (dict(CF), ["aws-waf-token=abc; Path=/"]),
                             (dict(NEUTRAL), [])):
        assert _verdict(tier="headless", status=403, headers=dict(headers),
                        set_cookie=list(cookies),
                        body=body) == "CAPTCHA_CHALLENGE"


# `test_a_measured_vendor_with_a_widget_url_on_a_2xx_is_not_a_window` lived here
# and was DELETED 2026-09-03. It pinned the `not page_served` conjunct of the
# generic co-occurrence arm; finding 3 deleted the arm, so the conjunct it
# guarded no longer exists, its second assertion (the same evidence on a 403 IS a
# window) is false by design, and its first is asserted -- against a mutation
# that can still fail it -- inside
# `test_a_widget_url_beside_a_vendor_marker_is_not_a_window`.


# ---- Header arms -------------------------------------------------------------

def test_aws_waf_405_captcha_header():
    """The only human-required case a HEADER can identify: AWS splits
    captcha/405 from challenge/202."""
    # MUTATION: drop the `status == 405` conjunct, or compare the header to
    # "challenge".
    assert _verdict(tier="headless", status=405,
                    headers={"x-amzn-waf-action": "captcha", "Content-Type": HTML},
                    body="<html></html>") == "CAPTCHA_CHALLENGE"


def test_aws_waf_challenge_202_is_not_a_window():
    # 202 + challenge is the non-interactive half, which tier 1 clears itself.
    assert _verdict(tier="headless", status=202,
                    headers={"x-amzn-waf-action": "challenge", "Content-Type": HTML},
                    body="<html></html>") == "CONTINUE"


@pytest.mark.parametrize("value", ['Basic realm="corp"', "Digest realm=x, qop=auth",
                                   "Negotiate", "NTLM", "Negotiate, NTLM"])
def test_401_with_a_cookieable_www_authenticate_scheme(value):
    # MUTATION: match the whole header value instead of its first token.
    assert _verdict(tier="headless", status=401,
                    headers={"WWW-Authenticate": value, "Content-Type": HTML},
                    body="") == "NEEDS_LOGIN"


def test_401_bearer_is_not_needs_login():
    """The Bearer/DPoP -> `needs_api_token` split was DROPPED, and Bearer is not
    in the cookieable set -- api.stripe.com sends `Basic` while its body says to
    use Bearer, so the scheme is not reliably the scheme the API wants."""
    assert _verdict(tier="headless", status=401,
                    headers={"WWW-Authenticate": 'Bearer realm="api"', "Content-Type": HTML},
                    body="") == "CONTINUE"


def test_403_with_www_authenticate_is_not_needs_login_by_that_arm():
    """The arm is written `status == 401`. `WWW-Authenticate` is legal on any
    status and corroborates a 403, but corroboration is not the arm."""
    assert _verdict(tier="headless", status=403,
                    headers={"WWW-Authenticate": 'Basic realm="corp"', "Content-Type": HTML},
                    body="<html><head><title>Forbidden</title></head></html>") == "CONTINUE"


def test_429_is_rate_limited_not_a_promotion():
    # MUTATION: delete the RATE_LIMITED block. `classify_failure(429)` USED TO
    # return `origin_4xx_block`, which is PERSISTENT and promoted toward a
    # window; the same change gave 429 its own `rate_limited` class.
    assert _verdict(tier="headless", status=429,
                    headers={"Retry-After": "120", "Content-Type": HTML},
                    body="") == "RATE_LIMITED"


def test_retry_after_on_any_status_is_rate_limited():
    assert _verdict(tier="headless", status=503,
                    headers={"Retry-After": "30", "Content-Type": HTML},
                    body="") == "RATE_LIMITED"


def test_451_is_a_hard_block():
    assert _verdict(tier="headless", status=451, headers=dict(NEUTRAL),
                    body="<html><body>Unavailable for legal reasons</body></html>"
                    ) == "HARD_BLOCK"


@pytest.mark.parametrize("code,expected", [
    ("1006", "HARD_BLOCK"), ("1020", "HARD_BLOCK"), ("1106", "HARD_BLOCK"),
    # 1010 is a Browser Integrity Check block -- exactly what a correctly
    # fingerprinted tier 1 should pass, so it must NOT be terminal.
    ("1010", "CONTINUE"),
    ("1015", "RATE_LIMITED"),
])
def test_cloudflare_error_code_bodies(code, expected):
    assert _verdict(tier="headless", status=403, headers=dict(CF),
                    body=f"error code: {code}\n") == expected


def test_cloudflare_error_code_must_be_the_whole_body():
    """`re.match` plus `$`, not a substring scan: a page QUOTING an error code
    is not a block."""
    # MUTATION: swap `_CF_HARD_BLOCK_RE.match(body.strip())` for
    # `"error code: 1020" in body`.
    body = ("<html><head><title>Fixing CF errors</title></head><body>"
            "When you see error code: 1020 in your logs, check your WAF rules."
            "</body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(CF), body=body) == "CONTINUE"


def test_perimeterx_block_action_is_a_hard_block_not_a_captcha():
    """Tie-break 2: `a=b` is the block, `a=c`/`a=j` are the interactive actions.
    The 403s are otherwise byte-identical."""
    body = '<html><body><script src="/px/captcha.js?a=b&u=1"></script></body></html>'
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=list(PX_COOKIES), body=body) == "HARD_BLOCK"


@pytest.mark.parametrize("action,expected", [
    # The three REAL actions. They are here as controls: a narrowing that stops
    # the collisions below by breaking the arm outright is not green.
    ("c", "CAPTCHA_CHALLENGE"), ("j", "CAPTCHA_CHALLENGE"), ("b", "HARD_BLOCK"),
    # The prefix collisions. Every one of these is a longer word beginning with
    # a real action letter.
    ("callback", "CONTINUE"), ("json", "CONTINUE"), ("block", "CONTINUE"),
    ("challenge", "CONTINUE"), ("bypass", "CONTINUE"),
])
def test_perimeterx_action_is_the_whole_value_not_a_prefix(action, expected):
    """FINDING 2, ROUND 3, RULED 2026-09-03: `captcha.js?a=c` as a SUBSTRING is
    satisfied by `captcha.js?a=callback`, whose action is not `c`.

    `a` is a VALUE in a namespace that contains longer words, so the needle had
    no RIGHT boundary and a page whose action was `callback` opened the headed
    browser (C-3/C-4). `a=j` and `a=b` had the identical defect. This is round-2
    finding 1 in the other direction -- that one was a missing LEFT boundary in a
    path comparison, this one a missing RIGHT boundary in a query. Both are a
    substring test standing in for an equality test.

    The fix parses the query and compares the COMPLETE value, which is why
    lengthening the needle was not the remedy: `a=cx` would still collide.
    """
    # MUTATION: restore `("perimeterx" in vendors and "captcha.js?a=c" in body)`
    # and its two siblings as substring tests (M25).
    body = f'<html><body><script src="/px/captcha.js?a={action}&u=1"></script></body></html>'
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=list(PX_COOKIES), body=body) == expected, action


@pytest.mark.parametrize("sep", [
    # WHAT TIER 1 ACTUALLY BUILDS. `document.documentElement.outerHTML` writes a
    # `&` inside an attribute value as `&amp;`, and these two are the numeric
    # spellings of the same character.
    "&amp;", "&#38;", "&#x26;",
    # THE RAW SEPARATOR, kept as ONE case because it pins the PARSE itself --
    # not because any tier that computes a verdict produces it.
    "&",
])
def test_perimeterx_action_is_read_wherever_it_sits_in_the_query(sep):
    """A DELIBERATE WIDENING, pinned so a later reader does not read it as drift
    -- AND, SINCE R1, THE PLACE THAT WIDENING WAS SILENTLY UNDONE.

    The substring form required `a=` IMMEDIATELY after the `?`; parsing the query
    reads the parameter wherever it sits. But `parse_qs` splits on `&` alone, and
    the body this predicate receives at tier 1 is a SERIALISED DOM in which an
    attribute value carries `&amp;` -- so every parameter after the first became
    a key named `amp;a` and the widening reached nothing. This test used to pin
    it with a raw `&`, a shape no verdict-computing tier produces: green on
    impossible input, which is the same defect that hid the critical earlier
    today.
    """
    # MUTATION: drop `_ESCAPED_AMP_RE` from `_candidate_urls`.
    body = f'<html><body><script src="/px/captcha.js?u=1{sep}a=c"></script></body></html>'
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=list(PX_COOKIES), body=body) == "CAPTCHA_CHALLENGE", sep


@pytest.mark.parametrize("query,expected", [
    # THE EXPENSIVE DIRECTION, WHICH IS WHY R1 IS NOT COSMETIC. HARD_BLOCK ENDS
    # the cascade; losing it lets the response CLIMB toward a window.
    ("u=1&amp;a=b", "HARD_BLOCK"),
    ("u=1&#38;a=b", "HARD_BLOCK"),
    ("u=1&#x26;a=b", "HARD_BLOCK"),
    ("u=1&AMP;a=b", "HARD_BLOCK"),
    # The cheap direction of the same defect: a real captcha went unnoticed.
    ("u=1&amp;a=c", "CAPTCHA_CHALLENGE"),
    # THE CONTROLS -- the raw separator, unchanged.
    ("u=1&a=b", "HARD_BLOCK"),
    ("a=b", "HARD_BLOCK"),
    # `html.unescape` WOULD BREAK THESE, WHICH IS WHY IT IS NOT USED. It expands
    # the legacy named references without their semicolon, so `&copy=`,
    # `&times=` and `&reg=` each swallow the separator in front of a real
    # parameter -- R1 again, in a new coat. Green before this repair and after
    # it; red the day someone reaches for the general-purpose unescaper.
    ("a=b&copy=1", "HARD_BLOCK"),
    ("a=b&times=2", "HARD_BLOCK"),
    ("a=b&reg=1", "HARD_BLOCK"),
    ("a=c&copy=1", "CAPTCHA_CHALLENGE"),
    # An ambiguous ampersand is NOT an escape: `&ampersand=1` is a parameter.
    ("a=b&ampersand=1", "HARD_BLOCK"),
])
def test_an_escaped_ampersand_does_not_hide_the_perimeterx_action(query, expected):
    """R1, RULED 2026-09-03, AND IT POINTS THE EXPENSIVE WAY.

    Filed as a parse nicety, measured as the opposite: the arm that loses its
    parameter here is the TERMINAL one. `a=b` past the first position was
    invisible, so a PerimeterX block page read CONTINUE and the cascade climbed
    toward the headed window (C-3/C-4) instead of stopping at HARD_BLOCK.

    The repair restores the ampersand and NOTHING ELSE -- see `_ESCAPED_AMP_RE`
    for why the general-purpose unescaper is the wrong tool, and for the three
    rows above that would go red under it.
    """
    # MUTATION: drop `_ESCAPED_AMP_RE`, or replace it with `html.unescape`.
    body = f'<html><body><script src="/captcha.js?{query}"></script></body></html>'
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=list(PX_COOKIES), body=body) == expected, query


@pytest.mark.parametrize("fragment,expected", [
    # R2: THE CAPTCHA URL IS INSIDE ANOTHER URL'S QUERY, so it is a query VALUE
    # of `/app.js` and not a URL this response references at all.
    ('<script src="/app.js?next=/captcha.js?a=c"></script>', "CONTINUE"),
    ('<script src="/app.js?redirect=https://evil.test/captcha.js?a=c"></script>',
     "CONTINUE"),
    # The terminal arm goes the same way, and that is a verdict CHANGED rather
    # than lost: this page is not a PerimeterX block, so the HARD_BLOCK it used
    # to return was wrong on it.
    ('<script src="/app.js?next=/captcha.js?a=b"></script>', "CONTINUE"),
    ("<script>var pxCaptchaSrc = '/HYx10rg3/captcha/captcha.js?a=c&u=1';</script>",
     "CAPTCHA_CHALLENGE"),
    ("<script>script.src = 'https://captcha.px-cloud.net/PXHYx10rg3/captcha.js?a=j';</script>",
     "CAPTCHA_CHALLENGE"),
    # THE CONTROLS -- the same URLs as the candidate itself.
    ('<script src="/px/captcha.js?a=c"></script>', "CAPTCHA_CHALLENGE"),
    ('<script src="captcha.js?a=b"></script>', "HARD_BLOCK"),
])
def test_a_captcha_url_inside_another_urls_query_is_not_a_candidate(fragment, expected):
    """R2, RULED 2026-09-03: the needle scanned RAW BODY TEXT, so a captcha URL
    sitting in another URL's query matched -- and `?next=/captcha.js` puts a `/`
    right where the segment boundary looks for one, so the left boundary does not
    help. MEASURED: both redirect shapes returned CAPTCHA_CHALLENGE, which opens
    the headed window (C-3/C-4).

    A candidate is a DELIMITED TOKEN now, parsed once. `/app.js?next=...` is one
    token whose path is `/app.js` and whose query has a parameter named `next`,
    so neither the path tail nor the `a` lookup can reach inside it.
    """
    # MUTATION: scan the raw body for the tail instead of the parsed path.
    body = f"<html><body>{fragment}</body></html>"
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=list(PX_COOKIES), body=body) == expected, fragment


def test_perimeterx_with_no_action_param_is_continue():
    """Tie-break 2: param absent (another enforcer, or the JSON block response)
    -> CONTINUE, not a window."""
    body = '<html><body><script src="/px/captcha.js"></script></body></html>'
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=list(PX_COOKIES), body=body) == "CONTINUE"


@pytest.mark.parametrize("src_attr,expected", [
    # ---- THE WINDOW ARM reads the SEGMENT-BOUNDED needle -------------------
    # THE COLLISIONS. Every one ENDS with the literal `captcha.js` and carries
    # an INTERACTIVE action, so under one shared unbounded needle every one of
    # them opened the window.
    ("/assets/js/recaptcha.js?a=c", "CONTINUE"),
    ("/js/hcaptcha.js?a=c", "CONTINUE"),
    ("/static/mycaptcha.js?a=c", "CONTINUE"),
    ("/vendor/px-captcha.js?a=c", "CONTINUE"),
    # WHAT THE BOUNDED NEEDLE COSTS: a bare relative src with no path at all no
    # longer opens a window. Pinned so it stays a decision rather than becoming
    # a surprise.
    ("captcha.js?a=c", "CONTINUE"),
    ("/px/captcha.js?a=c&u=1", "CAPTCHA_CHALLENGE"),
    ("https://x.px-cloud.net/PXHYx10rg3/captcha.js?a=j", "CAPTCHA_CHALLENGE"),
    ("/HYx10rg3/captcha/captcha.js?a=c", "CAPTCHA_CHALLENGE"),
    # ---- THE TERMINAL ARM reads the UNBOUNDED needle -----------------------
    # The SAME shapes with `a=b` go the OTHER way, and that is the whole of the
    # two-predicate split: HARD_BLOCK ENDS the cascade, so bounding it would let
    # these climb toward the window instead of stopping.
    ("/HYx10rg3/captcha/captcha.js?a=b", "HARD_BLOCK"),
    ("captcha.js?a=b", "HARD_BLOCK"),
    ("/vendor/px-captcha.js?a=b", "HARD_BLOCK"),
    ("/assets/js/recaptcha.js?a=b", "HARD_BLOCK"),
    ("recaptcha.js?a=b", "HARD_BLOCK"),
])
def test_the_left_boundary_binds_the_WINDOW_arm_and_not_the_TERMINAL_one(
        src_attr, expected):
    """The left boundary binds the window arm and not the terminal one."""
    # MUTATION A: collapse the two tails onto the bounded one -- `tail =
    # _PX_CAPTCHA_PATH_BOUNDED` unconditionally. Kills the `a=b` rows with no
    # leading `/`.
    # MUTATION B: drop the leading `/` from `_PX_CAPTCHA_PATH_BOUNDED`. Kills
    # the interactive collision rows.
    body = f'<html><body><script src="{src_attr}"></script></body></html>'
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=list(PX_COOKIES), body=body) == expected, src_attr


def test_datadome_device_check_is_not_a_captcha():
    """Tie-break 3: `i.js` builds the /interstitial/ iframe, `c.js` the
    /captcha/ one. `x-datadome` with neither is CONTINUE."""
    body = '<html><body><script src="https://ct.captcha-delivery.com/i.js"></script></body></html>'
    assert _verdict(tier="headless", status=403,
                    headers={"X-DataDome": "protected", "Content-Type": HTML},
                    body=body) == "CONTINUE"


@pytest.mark.parametrize("path,expected", [
    ("/c.js", "CAPTCHA_CHALLENGE"),
    ("/c.js?t=fe", "CAPTCHA_CHALLENGE"),
    # The prefix collisions, and the sibling path that was always CONTINUE.
    ("/c.json", "CONTINUE"),
    ("/c.jsx", "CONTINUE"),
    ("/c.js.map", "CONTINUE"),
    ("/i.js", "CONTINUE"),
])
def test_datadome_client_path_has_a_right_boundary(path, expected):
    """THE BOUNDARY SWEEP, ROUND 3 -- round-3 finding 2 one arm over.

    `"ct.captcha-delivery.com/c.js" in body` is satisfied by `/c.json` and
    `/c.jsx`, so a DataDome-served response referencing a JSON endpoint opened the
    headed browser. `c.js` is a COMPLETE path; the only continuations a real
    response carries are a query string or the closing quote of the `src`
    attribute.

    The two real forms are asserted beside the three collisions, so a boundary
    that stops them by breaking the arm is not green here.
    """
    # MUTATION: restore `"ct.captcha-delivery.com/c.js" in body` (M28).
    body = f'<html><body><script src="https://ct.captcha-delivery.com{path}"></script></body></html>'
    assert _verdict(tier="headless", status=403,
                    headers={"X-DataDome": "protected", "Content-Type": HTML},
                    body=body) == expected, path


@pytest.mark.parametrize("url,expected", [
    ("https://ct.captcha-delivery.com/c.js", "CAPTCHA_CHALLENGE"),
    ("//ct.captcha-delivery.com/c.js", "CAPTCHA_CHALLENGE"),
    ("https://ct.captcha-delivery.com/c.js?t=fe", "CAPTCHA_CHALLENGE"),
    # THE RIGHT-BOUNDARY HOLE. `(?![\w.])` excluded word characters and `.`;
    # `-` is neither, and neither is `~` or `,`.
    ("https://ct.captcha-delivery.com/c.js-backup", "CONTINUE"),
    ("https://ct.captcha-delivery.com/c.js~", "CONTINUE"),
    # NO LEFT BOUNDARY AT ALL: any label ENDING in `ct` matched the host.
    ("https://myct.captcha-delivery.com/c.js", "CONTINUE"),
    ("https://ct.captcha-delivery.com.evil.example/c.js", "CONTINUE"),
    # THE HOST IS THE HOST, not a string somewhere in the URL.
    ("https://evil.example/r?u=https://ct.captcha-delivery.com/c.js", "CONTINUE"),
    # A `;` SUFFIX IS PART OF THE PATH. `urlparse` would split it off as
    # `params` and compare the remainder EQUAL to `/c.js`; `urlsplit` does not.
    ("https://ct.captcha-delivery.com/c.js;evil", "CONTINUE"),
    # The sibling client, and the paths round 3 caught -- pinned again so a
    # repair that reaches them by a different route is still green.
    ("https://ct.captcha-delivery.com/i.js", "CONTINUE"),
    ("https://ct.captcha-delivery.com/c.json", "CONTINUE"),
    ("https://ct.captcha-delivery.com/c.js.map", "CONTINUE"),
])
def test_the_datadome_client_is_compared_as_a_whole_host_and_path(url, expected):
    r"""FINDING 0, ROUND 4, RULED 2026-09-03: the round-3 right boundary was not
    the repair, and the comment arguing it was is corrected with it.

    `ct\.captcha-delivery\.com/c\.js(?![\w.])` excludes `\w` and `.` after the
    path, so `/c.js-backup` passed -- `-` is neither. And it anchored nothing on
    the LEFT, so `myct.captcha-delivery.com/c.js` passed too. Either one returned
    CAPTCHA_CHALLENGE, the only verdict that opens the headed window (C-3/C-4).

    A boundary bounds one side of one component. This arm asks whether a URL
    names a specific HOST at a specific PATH -- two whole values -- so the repair
    is `_px_captcha_actions`': extract the candidates, parse them, compare.
    """
    # MUTATION: restore `"ct.captcha-delivery.com/c.js" in body` (M28), or
    # compare the host with `endswith` instead of equality.
    body = f'<html><body><script src="{url}"></script></body></html>'
    assert _verdict(tier="headless", status=403,
                    headers={"X-DataDome": "protected", "Content-Type": HTML},
                    body=body) == expected, url


def test_a_body_quoting_a_vendor_url_is_not_that_vendor():
    """FINDING 3, ROUND 2, RULED 2026-09-03: vendor PRESENCE must be independent
    of the arm's own discriminator.

    `_vendors_present` added DataDome when the body contained
    `captcha-delivery.com`, and the captcha arm then asked for
    `ct.captcha-delivery.com/c.js` -- which CONTAINS that substring. One body
    string supplied both halves of a conjunction written to require two. That is
    the laundering shape in a new coat: a blog post about bot detection, or a
    foreign vendor's error template, quotes a vendor URL without that vendor
    having served anything. Presence now comes only from a response header, a
    `server` value or a cookie -- something the ORIGIN sent.
    """
    # MUTATION: restore the body needles to `_vendors_present` -- DataDome's
    # `captcha-delivery.com`, PerimeterX's `px-captcha` / `window._pxAppId`
    # (M24).
    dd = ("<html><head><title>How DataDome works</title></head><body>"
          + DATADOME_CAPTCHA + "</body></html>")
    px = ('<html><head><title>How PerimeterX works</title></head><body>'
          '<script src="/px/captcha.js?a=c&u=1"></script>window._pxAppId'
          '</body></html>')
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    body=dd) == "CONTINUE"
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    body=px) == "CONTINUE"
    # THE CONTROL: the SAME bodies, plus the evidence a real response of that
    # shape carries. Without it both assertions above could pass for want of any
    # working captcha arm.
    assert _verdict(tier="headless", status=403,
                    headers={"X-DataDome": "protected", "Content-Type": HTML},
                    body=dd) == "CAPTCHA_CHALLENGE"
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=["_pxvid=8f2a; Path=/"],
                    body=px) == "CAPTCHA_CHALLENGE"


def test_vendor_cookies_supply_presence():
    """The cookie arms of the presence sets, read from the RAW multi-valued list."""
    body = '<html><body><script src="/px/captcha.js?a=c&u=1"></script></body></html>'
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=["_pxvid=8f2a; Path=/; HttpOnly",
                                "_pxhd=abc; Path=/"],
                    body=body) == "CAPTCHA_CHALLENGE"


def test_cookie_names_are_names_not_substrings():
    """A cookie whose VALUE mentions a vendor name is not that vendor.

    SHARPER SINCE ROUND-2 FINDING 3. The body needle used to make DataDome
    present on its own, so the reader could only be asserted directly; now the
    cookie is the ONLY thing on this response that can name a vendor, and the
    name-versus-substring reader decides the VERDICT.
    """
    # MUTATION: test `"datadome" in "".join(set_cookie)` instead of the name set.
    body = '<html><body><script src="https://ct.captcha-delivery.com/c.js"></script></body></html>'
    assert extract._cookie_names(["sessionid=datadome-is-fine; Path=/"]) == {"sessionid"}
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=["sessionid=datadome-is-fine; Path=/"],
                    body=body) == "CONTINUE"
    # THE CONTROL: the same body, and a cookie actually NAMED datadome.
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=["datadome=1a2b3c4d; Path=/; SameSite=Lax"],
                    body=body) == "CAPTCHA_CHALLENGE"


# ---- NEEDS_LOGIN -------------------------------------------------------------


def test_unproducible_hops_login_hop_into_an_idp_host():
    """LOGIN_HOP: the redirect TARGET is what is judged, not the last response."""
    # MUTATION: judge `final_url` only and drop `_login_hop` -- the gitlab case
    # (302 -> /users/sign_in -> 403 Cloudflare wall) then reads as a bot wall.
    hops = [(302, "https://app.example.com/dash",
             "https://accounts.google.com/o/oauth2/v2/auth?client_id=x")]
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    hops=hops, body="<html></html>") == "NEEDS_LOGIN"


def test_unproducible_hops_relative_location_resolves_against_its_own_hop_url():
    hops = [(302, "https://gitlab.example.com/-/profile", "/users/sign_in")]
    assert _verdict(tier="headless", status=403, headers=dict(CF),
                    hops=hops,
                    body=_fixture("cloudflare_check")) == "NEEDS_LOGIN"


def test_unproducible_hops_a_non_redirect_status_is_not_a_login_hop():
    hops = [(200, "https://app.example.com/dash", "https://accounts.google.com/signin")]
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    hops=hops, body="<html></html>") == "CONTINUE"


def test_final_url_login_redirect():
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url="https://app.example.com/users/sign_in",
                    body="<html></html>") == "NEEDS_LOGIN"


def test_final_url_equal_to_requested_url_is_not_a_login_redirect():
    """The arm is `final_url != requested_url`: a login PAGE fetched directly is
    not evidence that the CASCADE was bounced to one."""
    url = "https://app.example.com/users/sign_in"
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url=url, final_url=url, body="<html></html>") == "CONTINUE"


def test_oauth_query_triple():
    """RFC 6749 S4.1.1 -- all three of client_id, redirect_uri, response_type."""
    # MUTATION: make the triple an `any` instead of a subset test.
    final = ("https://sso-provider.example.com/authz?client_id=abc"
             "&redirect_uri=https%3A%2F%2Fapp.example.com%2Fcb&response_type=code")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url=final, body="<html></html>") == "NEEDS_LOGIN"


def test_partial_oauth_query_is_not_a_login_url():
    final = "https://cdn.example.com/asset?client_id=abc&v=2"
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url=final, body="<html></html>") == "CONTINUE"


def test_saml_request_query():
    final = "https://idp.example.net/redirect?SAMLRequest=fZJb"
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url=final, body="<html></html>") == "NEEDS_LOGIN"


@pytest.mark.parametrize("path", ["/session/new", "/users/sign_in", "/account/login",
                                  "/u/login", "/connect/authorize",
                                  "/openid-connect/auth", "/adfs/ls",
                                  "/cas/login"])
def test_login_path_tails(path):
    """The SPECIFIC tails, which are what LOGIN_URL keeps after finding 5.

    `/users/sign_in` is the gitlab case and it survives the narrowing: it is a
    tail, not a bare segment.
    """
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url="https://app.example.com" + path,
                    body="<html></html>") == "NEEDS_LOGIN"


def test_shibboleth_sso_is_matched_as_a_root_endpoint_prefix():
    """`idp/profile/saml2` is matched against the LEADING segments of the path,
    and it is the only entry with a rule of its own.

    Shibboleth's deployed endpoint always carries a binding suffix, so an
    `endswith` test never reaches it -- the bare `idp` segment was what caught it
    until round-1 finding 5 deleted the segment list. Three fixed tokens that
    nothing but an identity provider serves is a different thing from a one-word
    segment, which is why this one gets a rule at all.

    IT WAS CONTAINMENT-ANYWHERE UNTIL ROUND-2 FINDING 1 (2026-09-03). Segment
    boundaries kept `/blog/idp/profile/saml2-explained` out and still let
    `/docs/idp/profile/saml2/api-guide` in -- see
    `test_shibboleth_matches_only_at_the_path_ROOT`. The other tails get neither
    rule: `/docs/users/sign_in/api-guide` is a documentation page.
    """
    # MUTATION: revert the root prefix to an `endswith` test (M19).
    base = "https://idp.example.edu"
    for path in ("/idp/profile/SAML2/Redirect/SSO",      # the deployed shape
                 "/idp/profile/SAML2/POST/SSO",
                 "/idp/profile/SAML2",                   # the bare endpoint
                 "/idp/profile/SAML2/Unsolicited/SSO?providerId=x"):
        assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                        requested_url="https://app.example.com/dash",
                        final_url=base + path,
                        body="<html></html>") == "NEEDS_LOGIN", path
    # Still true with the query the real SP-initiated redirect carries. That arm
    # was all that remained while the tail could not reach the path.
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url=base + "/idp/profile/SAML2/Redirect/SSO?SAMLRequest=fZJb",
                    body="<html></html>") == "NEEDS_LOGIN"
    # THE BOUNDARY CONTROL: the prefix is compared as SEGMENTS, so an article
    # whose slug merely begins with the last token is not an SSO endpoint.
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url="https://blog.example.com/idp/profile/saml2-explained",
                    body="<html></html>") == "CONTINUE"


@pytest.mark.parametrize("path", ["/docs/endusers/sign_in", "/api/subaccount/login",
                                  "/xu/login", "/reconnect/authorize", "/myadfs/ls"])
def test_a_login_path_tail_needs_a_LEFT_segment_boundary(path):
    """FINDING 1, ROUND 2, RULED 2026-09-03: the tails are compared as SEGMENT
    TUPLES, never as a string suffix.

    `"docs/endusers/sign_in".endswith("users/sign_in")` is TRUE -- the string
    genuinely ends with it -- so any route whose segment merely ENDS with a tail
    segment came back NEEDS_LOGIN and, after a redirect, drove the user's
    logged-in Chrome. A tuple comparison supplies the left boundary the string
    test never had: `endusers` cannot satisfy `users`, and `subaccount` cannot
    satisfy `account`.
    """
    # MUTATION: restore `"/".join(segments).endswith(t)` (M20).
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url="https://app.example.com" + path,
                    body="<html></html>") == "CONTINUE"


@pytest.mark.parametrize("path", ["/docs/idp/profile/saml2/api-guide",
                                  "/blog/idp/profile/saml2/how-it-works",
                                  "/wiki/idp/profile/saml2"])
def test_shibboleth_matches_only_at_the_path_ROOT(path):
    """FINDING 1, ROUND 2, RULED 2026-09-03: Shibboleth is a ROOT endpoint
    prefix, not arbitrary containment.

    Segment-boundary containment kept `/blog/idp/profile/saml2-explained` out
    but let `/docs/idp/profile/saml2/api-guide` through -- documentation ABOUT
    an SSO endpoint, filed under a docs root, read as the endpoint itself. The
    deployed endpoint is served at the path root; a vendor's own guide to it is
    not.
    """
    # MUTATION: restore the padded `"/" + c + "/" in padded` containment (M21).
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url="https://idp.example.edu" + path,
                    body="<html></html>") == "CONTINUE"


@pytest.mark.parametrize("path", ["/docs/auth/reference", "/blog/oauth/what-it-is",
                                  "/support/login/troubleshooting", "/idp/overview",
                                  "/cas/about", "/help/sso/setup"])
def test_a_bare_login_path_segment_is_not_a_login_url(path):
    """FINDING 5, RULED 2026-09-03: the one-word segment list is DELETED.

    Any WHOLE segment equal to `auth`, `oauth`, `idp`, `cas`, `login`, `sso` ...
    made a URL a login URL, so an ordinary documentation page reached by a
    redirect returned NEEDS_LOGIN and drove the user's logged-in Chrome. Whole-
    segment matching had already fixed the substring defect below; it left this
    one untouched, because the segments are WORDS and words appear in public
    paths.
    """
    # MUTATION: restore `_LOGIN_PATH_SEGMENTS` and its `any(s in ...)` test (M14).
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url="https://app.example.com" + path,
                    body="<html></html>") == "CONTINUE"


@pytest.mark.parametrize("path", ["/blog/authoritative-guide", "/docs/authors",
                                  "/casserole-recipes", "/idport/status",
                                  "/single-sign-on-explained"])
def test_a_login_word_inside_a_path_segment_is_not_a_login_url(path):
    """The substring class, still asserted after the segment list went: these
    paths contain `auth`, `cas`, `idp` and `sign-in` as SUBSTRINGS, and the tails
    that remain are matched on a `/`-joined path, so a tail written without its
    leading boundary could still catch one."""
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url="https://app.example.com" + path,
                    body="<html></html>") == "CONTINUE"


def test_bare_okta_domain_is_not_an_idp_host():
    """The spec writes `*.okta.com` -- a subdomain wildcard. okta.com itself is
    the vendor marketing site.

    The true-positive half moved at round 4: `acme.okta.com/app/start` used to
    stand for "a subdomain IS a login host", which is exactly the belief finding
    4 falsified. The endpoint below carries the path a real Okta login endpoint
    carries.
    """
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url="https://okta.com/pricing",
                    body="<html></html>") == "CONTINUE"
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url="https://acme.okta.com/login/login.htm",
                    body="<html></html>") == "NEEDS_LOGIN"


@pytest.mark.parametrize("url", [
    "https://www.okta.com/blog/2019/06/what-is-zero-trust-security/",
    "https://developer.okta.com/docs/guides/implement-grant-type/authcode/main/",
    # The docs section named after a protocol. `oidc`, `sso`, `saml` and `idp`
    # are deliberately NOT endpoint segments for this reason.
    "https://developer.okta.com/docs/reference/api/oidc/",
    "https://support.okta.com/help/s/article/how-to-configure-sso",
    "https://www.auth0.com/blog/a-first-look-at-oauth/",
    "https://community.auth0.com/t/how-do-i-log-in-programmatically/1234",
    "https://www.okta.com/products/single-sign-on/",
])
def test_an_idp_vendor_subdomain_is_not_a_login_url_without_an_endpoint_path(url):
    """FINDING 4, ROUND 4, RULED 2026-09-03: `host.endswith('.okta.com')` is a
    suffix test standing in for "this host is an identity provider's login
    endpoint".

    The comment above `_IDP_HOST_SUFFIXES` stated the intent the test failed to
    implement -- "the bare registrable domain is the vendor marketing site and is
    not a login host" -- but `www.okta.com` IS that marketing site and ends with
    `.okta.com`, and so do the blog, docs, help-centre and community subdomains.
    Those are ordinary public pages an agent fetches while researching
    authentication, which is the one topic that brings an agent to these hosts.
    Each of these returned NEEDS_LOGIN, which drives the owner's logged-in Chrome
    (C-3/C-4).
    """
    # MUTATION: restore `any(host.endswith(s) for s in _IDP_HOST_SUFFIXES)` as a
    # sufficient condition in `_is_login_url`.
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url=url, body="<html></html>") == "CONTINUE", url


@pytest.mark.parametrize("url", [
    # THE TWO IN THE FINDING, both measured True before this repair.
    "https://developer.okta.com/docs/login/old",
    "https://developer.okta.com/docs/oauth2/guide",
    # The real shapes those stand for -- a vendor documents its own endpoints,
    # so the endpoint word is in the docs path by construction.
    "https://www.auth0.com/docs/authenticate/login/auth0-universal-login",
    "https://developer.okta.com/docs/guides/oauth2/main/",
    "https://community.auth0.com/c/authorize/23",
    # Already CONTINUE before the repair, and pinned so it stays that way: the
    # marker has to be a WHOLE segment as well as the FIRST one.
    "https://support.okta.com/help/s/article/login-errors",
])
def test_the_idp_endpoint_marker_must_be_at_the_path_root(url):
    """FINDING 1, ROUND 4, RULED 2026-09-03 -- ROUND-1 FINDING 5 RECREATED.

    The pre-round-4 sweep closed the bare-host-suffix defect by requiring an
    endpoint segment, and accepted that segment ANYWHERE in the path. Round 1
    had already ruled that insufficient -- "a bare one-word segment is no longer
    sufficient" -- and it is insufficient for the same reason here: vendor
    documentation is full of paths carrying `login` and `oauth2` as segments,
    and these are the hosts an agent fetches while researching authentication.

    A real IdP endpoint LEADS with the marker; documentation files it under
    `/docs/` or `/help/`. That is the separation `_LOGIN_PATH_ROOTS` already
    makes for Shibboleth, and this arm now makes it with the same comparison.
    """
    # MUTATION: accept the marker anywhere -- `any(s in _IDP_ENDPOINT_ROOTS ...
    # for s in segments)` -- or match it as a path TAIL instead of a root.
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url=url, body="<html></html>") == "CONTINUE", url


def test_a_pingone_authorize_url_is_carried_by_its_QUERY_not_its_path():
    """WHICH SIGNAL RECOGNISES A REAL AUTHORIZE URL, pinned because an earlier
    draft of this test called the answer a LOSS and it is not one.

    PingOne files its authorize endpoint behind the environment id --
    `auth.pingone.com/<envID>/as/authorize` -- so the root rule cannot reach it.
    It does not have to. An authorize request must carry `response_type` and
    `client_id` (RFC 6749 S4.1.1, both REQUIRED) and, being OIDC, `redirect_uri`
    (OIDC Core S3.1.2.1), so the query arm recognises every URL an IdP actually
    serves, on any host and whatever the path shape.

    THE STRIPPED FORM IS A SYNTHETIC PROBE, NOT A MEASURED LOSS. `?client_id=x`
    alone is not a URL any IdP serves; it is here to show WHICH arm is doing the
    work -- remove the other two parameters and the recognition goes with them,
    which is what proves it is the query and not the path.
    """
    real = ("https://acme.pingone.com/abc-env/as/authorize?response_type=code"
            "&client_id=x&redirect_uri=https%3A%2F%2Fapp.example.com%2Fcb")
    probe = "https://acme.pingone.com/abc-env/as/authorize?client_id=x"
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url=real, body="<html></html>") == "NEEDS_LOGIN"
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url=probe, body="<html></html>") == "CONTINUE"


@pytest.mark.parametrize("url", [
    "https://dev-12345.okta.com/oauth2/v1/authorize?client_id=x&state=y",
    "https://dev-12345.okta.com/oauth2/default/v1/authorize",
    "https://acme.okta.com/login/login.htm",
    "https://tenant.auth0.com/authorize?client_id=x",
    # TWO segments at the ROOT, which is why the roots are tuples: Auth0's
    # Universal Login serves `/u/login/identifier`, and `_LOGIN_PATH_TAILS`
    # carries the same pair but matches only a path that ENDS there.
    "https://tenant.auth0.com/u/login/identifier?state=x",
    "https://acme.onelogin.com/login",
    # THE PATH RULE DOES NOT REACH THIS ONE and does not have to -- the OAuth
    # query triple does. See the test below for which signal carries it.
    ("https://acme.pingone.com/abc-env/as/authorize?response_type=code"
     "&client_id=x&redirect_uri=https%3A%2F%2Fapp.example.com%2Fcb"),
])
def test_a_genuine_idp_endpoint_on_a_vendor_host_is_still_a_login_url(url):
    """THE OTHER DIRECTION OF FINDING 4 (plan S8.8). A narrowing that stops the
    vendor blog by breaking the arm outright is not green.

    Every real IdP login endpoint carries a path signal -- `/oauth2/`,
    `/authorize`, `/login`, `/signin` -- which is what makes the host a
    PRECONDITION rather than the whole test.
    """
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url=url, body="<html></html>") == "NEEDS_LOGIN", url


def test_json_unauth_on_a_200():
    """slack.com/api/conversations.list answers HTTP 200 with 33 bytes of JSON."""
    # MUTATION: gate `_json_unauth` on a 4xx status.
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    content_type=JSON_CT,
                    body='{"ok":false,"error":"not_authed"}') == "NEEDS_LOGIN"


@pytest.mark.parametrize("body", [
    '{"code":"not_authenticated","detail":"x"}',
    '{"detail":"Authentication credentials were not provided."}',
    '{"title":"Unauthorized"}',
    '{"error":"not_logged_in"}',
    '{"error":"login_required"}',
])
def test_json_unauth_fields(body):
    """The retained half of both field arms: every one of these names a SESSION,
    which is the credential the CDP tier actually holds."""
    assert _verdict(tier="headless", status=401, headers=dict(NEUTRAL),
                    content_type=JSON_CT, body=body) == "NEEDS_LOGIN"


@pytest.mark.parametrize("body", [
    '{"error":"invalid_token","error_description":"expired"}',
    '{"error":"token_expired"}',
    '{"error":"invalid_grant"}',
    '{"error":"invalid_client"}',
    '{"error":"unauthorized_client"}',
])
def test_a_token_or_client_credential_error_is_not_needs_login(body):
    """RULED 2026-09-03, the SIBLING arm of finding 4 and the same mechanism.

    An error naming a credential the browser does not hold is not a login. The
    first two name an access token; the last three are RFC 6749 S5.2
    token-endpoint errors about the CLIENT's own registered credentials, which no
    browser session supplies. Fixing the message regex alone would have left a
    known instance of the class open -- one arm over.

    TWO MECHANISMS PRODUCE THIS ANSWER NOW, and the second is the operative one.
    Round 1 REMOVED these five from `_JSON_UNAUTH_ERRORS`; round-2 finding 2 then
    added a credential scan that runs before every arm, and it names the same
    five. The removal is redundant, not wrong, and the scan is what a mutation
    can still reach -- see the note below.
    """
    # MUTATION: delete the `_names_non_browser_credential` guard from
    # `escalation_verdict` AND restore the five errors to `_JSON_UNAUTH_ERRORS`
    # -- M22 plus the old M18. (M22 moved with the veto in round 3; it used to
    # read "drop the credential scan from `_json_unauth`".) Restoring the
    # constant ALONE (M18) no longer changes anything: round-2 finding 2 put the
    # credential scan in front of it, and `_CREDENTIAL_ERRORS` is exactly these
    # five names, so the scan answers first. Measured 2026-09-03 over 334
    # generated bodies, zero disagreements -- M18 is retired as an equivalent
    # mutant, not as lost coverage.
    #
    # REPOINTED AT ROUND 4 ONTO THE PRODUCIBLE BODY. This case used to pass
    # `tier="headless"` with a bare JSON `body=`, which no transport builds, and
    # it was green because its input was impossible. See `_chrome_json_dom`.
    assert _verdict(tier="headless", status=401, headers=dict(BASIC_401),
                    content_type=JSON_CT,
                    body=_chrome_json_dom(body)) == "CONTINUE"
    # THE CONTROL: the arm that still fires when the body is a DOM is
    # `_www_auth_cookieable`, not `_json_unauth` -- so the assertion above is not
    # passing for want of a working arm.
    assert _verdict(tier="headless", status=401, headers=dict(BASIC_401),
                    content_type=JSON_CT,
                    body=_chrome_json_dom('{"detail":"Please sign in"}')) == "NEEDS_LOGIN"


@pytest.mark.parametrize("body", [
    '{"message":"Invalid API key provided"}',
    '{"detail":"You did not provide an API key."}',
    '{"message":"Invalid access token"}',
    '{"message":"Missing bearer token"}',
])
def test_an_api_key_or_access_token_message_is_not_needs_login(body):
    """FINDING 4, RULED 2026-09-03: a body that NAMES a key or a token is not a
    login.

    `NEEDS_LOGIN` routes the fetch to the user's logged-in Chrome, and a browser
    session cookie cannot supply an API key. This is not the Bearer/DPoP split
    the spec dropped -- that was about the `WWW-Authenticate` SCHEME being
    unreliable (api.stripe.com sends `Basic` while its body says to use Bearer)
    and says nothing about a body naming the credential outright.

    TWO MECHANISMS PRODUCE THIS ANSWER NOW, and the second is the operative one.
    Round 1 REMOVED these phrases from `_JSON_UNAUTH_MSG_RE`; round-2 finding 2
    then added a credential scan that runs before every arm and covers all of
    them. The removal is redundant, not wrong -- see the note below.
    """
    # MUTATION: delete the `_names_non_browser_credential` guard from
    # `escalation_verdict` AND restore
    # `invalid (?:api key|access token)`, `missing bearer` and `you did not
    # provide an api key` to `_JSON_UNAUTH_MSG_RE` -- M22 plus the old M15.
    # Restoring the regex ALONE (M15) no longer changes anything: every phrase it
    # adds is already covered by `_CREDENTIAL_MSG_RE`, which runs first. Measured
    # 2026-09-03 over 334 generated bodies, zero disagreements -- M15 is retired
    # as an equivalent mutant, not as lost coverage.
    #
    # REPOINTED AT ROUND 4 ONTO THE PRODUCIBLE BODY, and this is THE
    # api.stripe.com CASE: 401, `WWW-Authenticate: Basic`, a body naming an API
    # key, arriving at the predicate as a serialised DOM. Measured 2026-09-03,
    # it returned NEEDS_LOGIN -- the round-3 veto never ran, because it parsed
    # JSON and the body is not JSON at any tier that reaches it.
    assert _verdict(tier="headless", status=401, headers=dict(BASIC_401),
                    content_type=JSON_CT,
                    body=_chrome_json_dom(body)) == "CONTINUE"
    # THE CONTROL: `_www_auth_cookieable` is the arm that still fires on a DOM
    # body, so the assertions above are not passing for want of a working arm.
    assert _verdict(tier="headless", status=401, headers=dict(BASIC_401),
                    content_type=JSON_CT,
                    body=_chrome_json_dom('{"detail":"Please sign in"}')) == "NEEDS_LOGIN"


@pytest.mark.parametrize("body", [
    '{"error":"unauthorized","message":"Invalid API key provided"}',
    '{"error":"unauthorized","error_description":"The access token has expired"}',
    '{"code":"unauthorized","detail":"You did not provide an API key."}',
    '{"error":"invalid_client","message":"Unauthorized"}',
    '{"title":"Unauthorized","message":"Missing bearer token"}',
])
def test_credential_evidence_in_any_field_beats_a_generic_unauthorized_field(body):
    """FINDING 2, ROUND 2, RULED 2026-09-03: explicit credential evidence WINS
    over generic unauthorized wording, wherever in the document each sits.

    DELIBERATELY LEFT ON RAW JSON AT ROUND 4, and one of only two credential
    controls that are. The pairing is SYNTHETIC -- `tier="headless"` with a bare
    JSON body is a shape no transport builds (see `_chrome_json_dom`) -- and it
    is kept because it is the only credential case that still exercises the
    veto's `json.loads` path and the `_json_unauth` field ordering this finding
    was about. Its producible siblings moved to the DOM body; this one pins the
    parser they no longer reach.

    `_json_unauth` returned on the FIRST field that matched, so
    `{"error":"unauthorized","message":"Invalid API key provided"}` never reached
    the message that names the credential -- and NEEDS_LOGIN routes the fetch to
    the user's logged-in Chrome, which cannot supply an API key. The principle
    was already in the file and was applied one field at a time; it is now
    applied to the whole document.
    """
    # MUTATION: delete the `_names_non_browser_credential` guard from
    # `escalation_verdict`, restoring first-field-wins (M22).
    assert _verdict(tier="headless", status=401, headers=dict(NEUTRAL),
                    content_type=JSON_CT, body=body) == "CONTINUE"


@pytest.mark.parametrize("body", [
    '{"message":"Unauthorized: invalid API key"}',
    '{"detail":"Unauthorized - your access token is expired"}',
    '{"error":"unauthorized","message":"Unauthorized: the client credentials are invalid"}',
])
def test_credential_evidence_in_the_same_message_beats_the_generic_wording(body):
    """FINDING 2, ROUND 2: the collision inside ONE string, not across two.

    `Unauthorized: invalid API key` satisfies the broad `unauthorized`
    alternative of `_JSON_UNAUTH_MSG_RE` on its first word. The credential named
    in the second half is the fact that decides it.
    """
    # MUTATION: move the `_names_non_browser_credential` guard BELOW the login
    # block, so the veto is consulted only when no arm matched (M23).
    #
    # REPOINTED AT ROUND 4 ONTO THE PRODUCIBLE BODY. On a DOM body the arm the
    # veto has to beat is `_www_auth_cookieable` rather than `_json_unauth`,
    # which is the whole reason PLACEMENT -- not membership of one arm -- is the
    # fix.
    assert _verdict(tier="headless", status=401, headers=dict(BASIC_401),
                    content_type=JSON_CT,
                    body=_chrome_json_dom(body)) == "CONTINUE"
    # THE CONTROL: the arm the veto is beating, with a body naming a credential
    # the browser DOES hold.
    assert _verdict(tier="headless", status=401, headers=dict(BASIC_401),
                    content_type=JSON_CT,
                    body=_chrome_json_dom('{"detail":"Please sign in"}')) == "NEEDS_LOGIN"


def test_a_generic_unauthorized_naming_no_credential_is_still_needs_login():
    """THE CONTROL for the two tests above (plan S8.8): without it both could
    pass for want of any working JSON arm at all."""
    for body in ('{"error":"unauthorized","message":"Your session has expired"}',
                 '{"code":"unauthorized","detail":"Please sign in to continue"}'):
        assert _verdict(tier="headless", status=401, headers=dict(NEUTRAL),
                        content_type=JSON_CT, body=body) == "NEEDS_LOGIN", body


def test_json_unauth_matches_fields_not_a_substring_of_the_body():
    """An HTML page that MENTIONS an auth error is not an auth error."""
    # MUTATION: replace `json.loads` + field lookup with a substring scan.
    body = ("<html><head><title>Slack API errors</title></head><body>"
            "<p>An <code>not_authed</code> error means your token is unauthorized."
            "</p></body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == "CONTINUE"


@pytest.mark.parametrize("body", [
    '{"message":"Invalid API key provided"}',
    '{"error":"invalid_token"}',
    '{"error":"unauthorized","message":"Missing bearer token"}',
])
def test_www_authenticate_does_not_bypass_the_credential_veto(body):
    """FINDING 1, ROUND 3, RULED 2026-09-03: the veto dominates EVERY remaining
    login arm, not only the one it was reported in.

    A 401 carrying `WWW-Authenticate: Basic` AND a body naming an API key
    returned NEEDS_LOGIN: `_www_auth_cookieable` is true, short-circuit
    evaluation never reached `_json_unauth`, and the veto that lived INSIDE
    `_json_unauth` never ran. NEEDS_LOGIN drives the user's logged-in Chrome, and
    no browser session supplies an API key.

    THIS IS ROUND-2 FINDING 2 IN A SECOND ARM. There the ruling was "credential
    evidence wins over a generic field" and the veto was placed inside the one
    arm that had been reported. The fix is now the PLACEMENT: the veto is its own
    guard above the whole block, so an arm added below it is covered by
    construction -- the same reasoning that put the empty-headers guard below the
    terminal blocks rather than adding a conjunct to three arms.

    ROUND 4 FOUND THAT REPAIR INERT. The veto read JSON only, and no tier hands
    the predicate JSON: at tier 0 the body is empty and Rule B answers CONTINUE
    anyway, and at tiers 1/2/4 it is `document.documentElement.outerHTML`. So
    this exact case -- measured 2026-09-03 -- still returned NEEDS_LOGIN in
    production while every test here stayed green, because every test fed it a
    raw body no transport builds. The bodies below are now wrapped the way
    Chrome wraps a JSON response.
    """
    # MUTATION: put the credential scan back inside `_json_unauth` (M26).
    assert _verdict(tier="headless", status=401, headers=dict(BASIC_401),
                    content_type=JSON_CT,
                    body=_chrome_json_dom(body)) == "CONTINUE"
    # THE CONTROL (plan S8.8): the SAME header, and a body naming a credential
    # the browser DOES hold. Without it the assertion above could pass for want
    # of a working `WWW-Authenticate` arm.
    assert _verdict(tier="headless", status=401, headers=dict(BASIC_401),
                    content_type=JSON_CT,
                    body=_chrome_json_dom('{"detail":"Please sign in"}')) == "NEEDS_LOGIN"


@pytest.mark.parametrize("raw", [
    # The envelope api.stripe.com actually returns, and the shape this file's own
    # comments cite as the origin of the rule.
    '{"error":{"message":"Invalid API key provided","type":"invalid_request_error"}}',
    '{"errors":[{"detail":"Your access token has expired"}]}',
    '{"error":{"code":"invalid_client","message":"Unauthorized"}}',
])
def test_the_credential_veto_reads_a_nested_error_envelope(raw):
    """FINDING 1, ROUND 4, THE SECOND HALF: the JSON scan read TOP-LEVEL string
    values only, so an envelope one level deep escaped it entirely -- in the raw
    form as well as the DOM form.

    A text scan has no nesting depth, which is why one repair closes both.
    """
    # MUTATION: delete the text scan from `_names_non_browser_credential`,
    # leaving the top-level JSON loop.
    for body in (raw, _chrome_json_dom(raw)):
        assert _verdict(tier="headless", status=401, headers=dict(BASIC_401),
                        content_type=JSON_CT, body=body) == "CONTINUE", body
    # THE CONTROL: the same nesting, naming a credential the browser DOES hold.
    assert _verdict(tier="headless", status=401, headers=dict(BASIC_401),
                    content_type=JSON_CT,
                    body=_chrome_json_dom('{"error":{"message":"Please sign in"}}')
                    ) == "NEEDS_LOGIN"


def test_a_login_page_naming_an_api_key_is_vetoed_and_that_is_the_cost():
    """WHAT THE TEXT SCAN COSTS, PINNED SO IT IS NOT LATER DISCOVERED AS A BUG.

    The veto now reads the whole document, so it reaches `_login_form` -- an arm
    it could never collide with while it parsed JSON. A genuine login page that
    also mentions an API key is now CONTINUE. That is a LOST FETCH, which is the
    cheap direction; the expensive one is the owner's logged-in Chrome opening on
    a credential no browser session supplies (C-3/C-4). The trade is chosen
    deliberately and this test is the record of choosing it.
    """
    page = ("<html><head><title>Sign in</title></head><body>"
            "<form id=login><input type=password name=p></form>"
            "%s</body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=page % "<p>Automating? Use an API key instead.</p>"
                    ) == "CONTINUE"
    # THE CONTROL: the same page without the sentence.
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=page % "<p>Forgot your password?</p>") == "NEEDS_LOGIN"


@pytest.mark.parametrize("body", [
    '{"message":"unauthorized_client: unknown client"}',
    '{"detail":"invalid authorization_code"}',
    '{"message":"UnauthorizedAccessException at line 4"}',
])
def test_the_generic_message_arm_matches_words_not_substrings_of_words(body):
    """THE BOUNDARY SWEEP, ROUND 3: `_JSON_UNAUTH_MSG_RE` had no word boundaries
    while `_CREDENTIAL_MSG_RE` -- the veto beside it -- had them on every
    alternative.

    So `unauthorized` matched inside `unauthorized_client`, an RFC 6749 S5.2
    token-endpoint error about the CLIENT's own registered credentials, and
    `invalid authorization` matched inside `invalid authorization_code`. Neither
    names a credential the browser holds; both returned NEEDS_LOGIN and drove the
    user's logged-in Chrome. The veto does not catch them -- `_CREDENTIAL_ERRORS`
    is an EXACT match on the whole field and these sit mid-sentence.
    """
    # MUTATION: drop the `\b` boundaries from `_JSON_UNAUTH_MSG_RE` (M27).
    assert _verdict(tier="headless", status=401, headers=dict(NEUTRAL),
                    content_type=JSON_CT, body=body) == "CONTINUE"
    # THE CONTROL: the bare word, which is what the alternative is FOR.
    assert _verdict(tier="headless", status=401, headers=dict(NEUTRAL),
                    content_type=JSON_CT,
                    body='{"message":"Unauthorized"}') == "NEEDS_LOGIN"


def test_json_that_is_not_an_object_is_not_unauth():
    assert _verdict(tier="headless", status=401, headers=dict(NEUTRAL),
                    content_type=JSON_CT, body='["unauthorized"]') == "CONTINUE"


def test_login_form_needs_a_sign_in_title_as_well_as_a_password_box():
    """THE control for `LOGIN_FORM`. A password input sits on every page with a
    header sign-in dropdown; without the title conjunct those all route to the
    user's logged-in Chrome."""
    # MUTATION: drop the `_LOGIN_TITLE_RE` conjunct from `_login_form`.
    body = ("<html><head><title>Quarterly report | Acme</title></head><body>"
            "<h1>Results</h1><form action=/subscribe>"
            "<input type=email name=e><input type=\"password\" name=p></form>"
            "</body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == "CONTINUE"


@pytest.mark.parametrize("attr,expected", [
    # THE COLLISIONS. `auth` inside `author`/`authors`/`authenticity`/
    # `authority`, `session` inside `sessions`. None of these bodies carries a
    # password input anywhere, so the attribute test is the ONLY form evidence --
    # which is the point: it is a DISJUNCT beside the password test, not a
    # conjunct behind it.
    ('id="author-search"', "CONTINUE"),
    ('id="authors"', "CONTINUE"),
    ('id="authenticity-token"', "CONTINUE"),
    ('id="authority-lookup"', "CONTINUE"),
    ('id="sessions"', "CONTINUE"),
    # THE CONTROLS -- the words the arm is FOR.
    ('id="login-form"', "NEEDS_LOGIN"),
    ('id="signin"', "NEEDS_LOGIN"),
    ('action="/users/sign_in"', "NEEDS_LOGIN"),
])
def test_a_login_form_attribute_value_matches_words_not_substrings(attr, expected):
    """FINDING 3, ROUND 4, RULED 2026-09-03: `_LOGIN_FORM_ATTR_RE` had no word
    boundaries while `_JSON_UNAUTH_MSG_RE`, repaired one round earlier, has them
    on every alternative.

    `authenticity-token` is Rails' CSRF field and rides on essentially every form
    Rails renders, so a documentation page with a search box and a sign-in title
    returned NEEDS_LOGIN and drove the owner's logged-in Chrome (C-3/C-4).

    THIS ALSO FALSIFIES A PREVIOUSLY-FILED RULING. The title over-match was filed
    not-fixed on the ground that "`_login_form` also requires a password input,
    so the title arm cannot act alone". It does not: the password input is one
    DISJUNCT of three, and these bodies have none.
    """
    # MUTATION: drop the `\b` boundaries from the value half of
    # `_LOGIN_FORM_ATTR_RE`.
    body = ('<html><head><title>Sign in to your account</title></head><body>'
            f'<form {attr}><input type="text" name="q"></form></body></html>')
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == expected, attr


@pytest.mark.parametrize("attr,expected", [
    # `\bid` matched inside `data-id` -- `-` is a non-word character, so the
    # left boundary was satisfied and there was no right one.
    ('data-id="login"', "CONTINUE"),
    ('data-name="login"', "CONTINUE"),
    ('data-action="/login"', "CONTINUE"),
    ('x-id="signin"', "CONTINUE"),
    # A CONTROL, not a repair: `formaction` never matched, because `m`->`a` is
    # not a word boundary. It is here so the new left boundary is asserted to
    # exclude it from the other side too.
    ('formaction="/login"', "CONTINUE"),
    # THE CONTROLS -- the three names spec S3.4 writes.
    ('id="login"', "NEEDS_LOGIN"),
    ('name="login"', "NEEDS_LOGIN"),
    ('action="/login"', "NEEDS_LOGIN"),
])
def test_a_login_form_attribute_name_is_action_id_or_name_only(attr, expected):
    """FINDING 3, ROUND 4, the other half: the attribute-NAME alternation had a
    left boundary and no right one, so `data-id`, `data-name`, `data-action` and
    `x-id` all reached the value scan.

    In HTML an attribute name is preceded by whitespace, so requiring that one
    character is the whole repair -- and spec S3.4 writes the set as
    `action`/`id`/`name`.
    """
    # MUTATION: restore `\b(?:action|id|name)` in place of the leading `\s`.
    body = ('<html><head><title>Sign in to your account</title></head><body>'
            f'<form {attr}><input type="text" name="q"></form></body></html>')
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == expected, attr


@pytest.mark.parametrize("form,expected", [
    # THE DEFECT INPUT: no login marker in any `action`, `id` or `name`, no
    # password field, and the value scan crossed into `data-mode=auth`.
    ("<form action=/search data-mode=auth>", "CONTINUE"),
    ("<form id=q data-role=login>", "CONTINUE"),
    ("<form name=search data-x=sign-in>", "CONTINUE"),
    ("<form action=/search data-session=1>", "CONTINUE"),
    # THE CONTROLS. Both quotings still fire, and an unquoted value is still
    # READ -- the branch ends at whitespace, it does not stop existing.
    ("<form action=/users/login>", "NEEDS_LOGIN"),
    ('<form action="/users/login">', "NEEDS_LOGIN"),
    ("<form id=login-form>", "NEEDS_LOGIN"),
    ('<form id="login-form">', "NEEDS_LOGIN"),
    ("<form name=signin>", "NEEDS_LOGIN"),
    # THE TAG IS THE SCAN REGION: `[^>]*` stops at the first `>`, so a login
    # word in the DOCUMENT is out of reach. Green before this repair too, and
    # pinned so a branch that drops the `>` exclusion is not.
    ("<form action=/search><p>login here</p>", "CONTINUE"),
])
def test_an_unquoted_form_attribute_value_ends_at_whitespace(form, expected):
    r"""FINDING 3, ROUND 4, RULED 2026-09-03 -- the third boundary the value half
    needed, after the two the pre-round-4 sweep gave it.

    `[\"']?[^\"'>]*` made the quote optional and then read a class that crosses
    WHITESPACE, so after `action=` the scan swallowed ` data-mode=` and the login
    alternation matched inside a DIFFERENT attribute. MEASURED: `_login_form`
    True and the verdict NEEDS_LOGIN, which drives the owner's logged-in Chrome
    (C-3/C-4), on a body with no login marker in any attribute spec S3.4 names.

    Quoted and unquoted are separate branches now: a quoted value ends at its
    own quote, an unquoted one at whitespace, and both still exclude `>`.
    """
    # MUTATION: collapse the branches back to `[\"']?[^\"'>]*`.
    body = ('<html><head><title>Sign in to your account</title></head><body>'
            f'{form}<input type="text" name="q"></form></body></html>')
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == expected, form


@pytest.mark.parametrize("frag,expected", [
    # `<script>` and `<style>` hold CDATA: their content is TEXT, not markup.
    # A JS string that happens to contain a login form is not a login form, and
    # `document.write` templates like these are ordinary on real pages.
    ('<script>var s="<form id=login><input type=password>";</script>',
     "CONTINUE"),
    ('<script>document.write("<input type=password name=p>")</script>',
     "CONTINUE"),
    ("<style>/* <form id=login-form> */</style>", "CONTINUE"),
    # THE CONTROLS: a real form beside a script, and a real form alone. Without
    # them this passes against a parser that has stopped seeing forms at all.
    ('<script>var s="x";</script>'
     "<form id=login-form><input type=password name=p></form>", "NEEDS_LOGIN"),
    ("<form id=login-form><input type=password name=p></form>", "NEEDS_LOGIN"),
])
def test_script_or_style_content_is_not_form_evidence(frag, expected):
    """THIS IS THE BENEFIT PARSING WAS BOUGHT FOR, AND NOTHING HELD IT. I cited
    it in a commit message as what a boundary fix could never reach -- and then
    left it unpinned, which is the third invariant in this file to be correct
    in the code and held by nothing.

    Measured 2026-09-04: setting `CDATA_CONTENT_ELEMENTS = ()` on `_MarkupFacts`
    makes a `<form id=login>` inside a JS string read NEEDS_LOGIN and drive the
    owner's logged-in Chrome, with ALL 882 TESTS GREEN.

    A regex cannot ask this question at all: `<script>` content is only text
    because the tokeniser says so. The regex this replaced matched the form in
    the string.
    """
    # MUTATION: `CDATA_CONTENT_ELEMENTS = ()` on `_MarkupFacts`.
    body = ("<html><head><title>Sign in to Acme</title></head><body>"
            f"{frag}</body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == expected, frag


@pytest.mark.parametrize("head,expected", [
    # A `<title>` inside SVG or MathML is an ACCESSIBLE NAME for a graphic, not
    # the document's title. `<svg><title>Sign in</title>...</svg>` is the
    # standard markup for a sign-in ICON BUTTON, so an ordinary article carrying
    # one -- and a header login form, which a content site routinely has -- read
    # NEEDS_LOGIN and drove the owner's logged-in Chrome.
    ("<svg><title>Sign in</title><path d='M0 0'/></svg>", "CONTINUE"),
    ("<math><title>Sign in</title></math>", "CONTINUE"),
    ("<svg><svg><title>Sign in</title></svg></svg>", "CONTINUE"),
    # The icon's name must not win a first-match race against the real title
    # either, whichever order they appear in.
    ("<svg><title>Sign in</title></svg><title>How DNS works - Acme</title>",
     "CONTINUE"),
    # THE CONTROLS. Without these the test passes against a parser that has
    # stopped reading titles altogether, which is the failure mode this whole
    # file keeps producing.
    ("<title>Sign in to your account</title>", "NEEDS_LOGIN"),
    ("<title>Sign in to Acme</title><svg><title>Home</title></svg>",
     "NEEDS_LOGIN"),
    # A stray `</svg>` with no opener must not drive the counter negative --
    # negative reads as truthy and would suppress every title after it.
    ("</svg><title>Sign in to Acme</title>", "NEEDS_LOGIN"),
])
def test_a_title_in_foreign_content_is_not_the_document_title(head, expected):
    """FOUND 2026-09-04 by the independent differential, and BRANCH-CREATED:
    `main` carries no login arm at all, so this verdict cannot pre-exist there.

    The regex this replaced took the first `<title>` anywhere too, so the
    BEHAVIOUR is not new -- what is new is that it can be closed, because only a
    parser knows a tag is inside foreign content. That is the whole of the
    owner's fix-the-class ruling in one case: not a boundary tightened, a
    question a substring scan could not ask.

    The counter is clamped at zero, and the unclosed-`<svg>` direction is
    deliberate: it suppresses every later title, which loses a fetch. Cheap.
    """
    # MUTATION: drop `_foreign` -- the counter, both guards, or the constant.
    body = (f"<html><head>{head}</head><body>"
            "<form id=login-form><input type=password name=p></form></body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == expected, head


@pytest.mark.parametrize("mark,expected", [
    # `HTMLParser` RAISES on each of these -- explicit `raise AssertionError`
    # statements in `_markupbase`'s declaration parsing, not `assert`, so `-O`
    # does not strip them:
    #   <![foo]>        unknown status keyword 'foo' in marked section
    #   <![then[ x ]]>  unknown status keyword 'then' in marked section
    #   <![>            expected name token at '<![>'
    ("<![foo]>", "CONTINUE"),
    ("<![then[ x ]]>", "CONTINUE"),
    ("<![>", "CONTINUE"),
    # THE CONTROLS, and they are what make the rows above mean anything: the
    # SAME document with a marked section the parser ACCEPTS, and with none at
    # all. Both carry a real login form and read NEEDS_LOGIN, so CONTINUE above
    # is the guard firing -- not the body having been evidence of nothing all
    # along, which is how this assertion would pass if `_login_form` were
    # broken outright.
    ("<![CDATA[x]]>", "NEEDS_LOGIN"),
    ("", "NEEDS_LOGIN"),
])
def test_a_parser_refusal_does_not_escape_into_the_cascade(mark, expected):
    """`escalation_verdict` is called from `fetch.py`'s cascade loop OUTSIDE any
    try/except, so an exception out of the parser does not fail a tier -- it
    fails the whole fetch. `_markup_facts` therefore catches and returns FRESH
    EMPTY facts, so `title is None` and `_login_form` returns False on its first
    guard: a malformed page costs one fetch and can never open a rung (C-3/C-4).

    THE GUARD WAS ENTIRELY UNPINNED UNTIL THIS TEST, and its docstring asserted
    the opposite. Measured 2026-09-04 on a full copy of the skill:

      - no test in ANY of the 18 test files fed a body the parser refuses --
        zero occurrences of `<![foo]>`, `<![>`, `marked section` or `unknown
        status keyword` anywhere;
      - deleting the `try`/`except` from `_markup_facts` outright left all 870
        tests GREEN.

    So a reader who believed the old docstring's "ZERO exceptions over 51
    documents" could have removed the guard as dead defensive code, and nothing
    would have failed until a bot wall served a marked section. That claim was
    true of those 51 documents and false of the parser: the corpus fed it
    `<![CDATA[...]]>`, which does not raise, and never `<![foo]>`, which does.
    """
    # MUTATION: delete the `try`/`except` in `_markup_facts`.
    body = ("<html><head><title>Sign in to your account</title></head><body>"
            f"{mark}<form id=login-form><input type=password name=p></form>"
            "</body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == expected, mark


@pytest.mark.parametrize("tag,expected", [
    # FIRST OCCURRENCE WINS, which is what a browser does: HTML tree
    # construction DROPS a duplicate attribute rather than overwriting with it.
    # So the `type` here is `text` and this is not a password box, however the
    # second pair reads.
    ("<input type=text type=password name=q>", "CONTINUE"),
    ("<form id=search id=login><input name=q></form>", "CONTINUE"),
    ("<form action=/search action=/login><input name=q></form>", "CONTINUE"),
    ("<form name=search name=signin><input name=q></form>", "CONTINUE"),
    # THE CONTROLS -- the same pairs reversed. The login value is first, so it
    # is the one that survives and the arm still fires. Without these the test
    # would pass against a parser that dropped duplicate attributes entirely.
    ("<input type=password type=text name=p>", "NEEDS_LOGIN"),
    ("<form id=login id=search><input name=q></form>", "NEEDS_LOGIN"),
    ("<form action=/users/login action=/search><input name=q></form>",
     "NEEDS_LOGIN"),
])
def test_a_duplicate_attribute_keeps_the_FIRST_occurrence(tag, expected):
    """`HTMLParser` hands back a LIST of pairs with BOTH duplicates present, so
    which one wins is a decision the collapse makes -- it is not cosmetic.

    `_MarkupFacts._attrs` uses `dict.setdefault`, so the first wins, which is
    what a browser does. Iterate every pair instead and ANY occurrence wins:
    `<input type=text type=password>` renders as an ordinary search box and
    reads NEEDS_LOGIN, driving the owner's logged-in Chrome (C-3/C-4).

    FOUND BY DIFFERENTIAL, NOT BY THIS SUITE, 2026-09-04. A second agent built
    the same parser independently, chose any-occurrence-wins, defended it in a
    comment as preserving what the old regex did -- and then reported the defect
    in its OWN implementation when the two were compared over 8,772 bodies.

    IT WAS UNPINNED UNTIL THIS TEST. Measured on a full copy of the skill, not
    argued: changing `setdefault` to `out[name] =` flips both CONTINUE rows to
    NEEDS_LOGIN and leaves all 863 tests green. The correct behaviour was one
    edit away from a window-opening regression that nothing would have caught.
    """
    # MUTATION: `out.setdefault(k, v)` -> `out[k] = v` in `_MarkupFacts._attrs`.
    body = ("<html><head><title>Sign in to your account</title></head><body>"
            f"{tag}</body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == expected, tag


@pytest.mark.parametrize("tag,expected", [
    # THE SIBLING NEEDLES FINDING 3 NEVER REACHED. `\btype` matches inside
    # `data-type` because `-` is not a word character -- the same
    # attribute-NAME defect, alive in the two <input> needles beside the <form>
    # one that was patched. Each returned NEEDS_LOGIN.
    ('<input data-type="password" name=q>', "CONTINUE"),
    ("<input x-type=password name=q>", "CONTINUE"),
    ('<input data-autocomplete="username" name=q>', "CONTINUE"),
    ('<input data-autocomplete="current-password" name=q>', "CONTINUE"),
    # THE CONTROLS -- a real password box, both quotings, both attributes.
    ('<input type="password" name=p>', "NEEDS_LOGIN"),
    ("<input type=password name=p>", "NEEDS_LOGIN"),
    ('<input autocomplete="current-password" name=p>', "NEEDS_LOGIN"),
    ('<input autocomplete="username" name=u>', "NEEDS_LOGIN"),
    # A SPACE-SEPARATED TOKEN LIST, which is what a passkey-enabled login form
    # carries. Comparing the whole attribute for equality would lose it.
    ('<input autocomplete="username webauthn" name=u>', "NEEDS_LOGIN"),
])
def test_a_password_input_is_read_off_the_parsed_tag(tag, expected):
    """THE CLASS RULING APPLIED TO THE TWO NEEDLES BESIDE FINDING 3.

    Finding 3 was repaired by bounding a regex, and the repair closed the
    instance it was aimed at. These three shapes were measured against that
    repaired file and still reached NEEDS_LOGIN, which drives the owner's
    logged-in Chrome (C-3/C-4) -- because a regex cannot tell an attribute from
    the text around it, so each miss needs one more boundary rule.

    An attribute is a KEY now. `data-type` is not `type`, and there is no
    boundary rule to get wrong.
    """
    # MUTATION: match `type` / `autocomplete` as a substring of the attribute
    # NAME rather than as a key.
    body = ('<html><head><title>Sign in to your account</title></head><body>'
            f'<form id=q>{tag}</form></body></html>')
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == expected, tag


@pytest.mark.parametrize("form,expected", [
    # FINDING 3'S OWN INPUT, now unreachable by construction rather than by a
    # branch that ends the value at whitespace.
    ("<form action=/search data-mode=auth>", "CONTINUE"),
    ("<form id=q data-role=login>", "CONTINUE"),
    ('<form data-id="login">', "CONTINUE"),
    ('<form formaction="/login">', "CONTINUE"),
    ('<form id="authenticity-token">', "CONTINUE"),
    # THE CONTROLS.
    ('<form action="/users/login">', "NEEDS_LOGIN"),
    ("<form action=/users/login>", "NEEDS_LOGIN"),
    ("<form id=login-form>", "NEEDS_LOGIN"),
    ("<form name=signin>", "NEEDS_LOGIN"),
    # TWO SHAPES THE REGEX COULD NOT REACH AND THE PARSER CAN, both of them the
    # parser being RIGHT: a `>` inside a quoted value does not end the tag, and
    # an entity in an attribute value is that character. Every regex branch
    # excluded `>`, so the first was CONTINUE before this change.
    ('<form title="a>b" name=login>', "NEEDS_LOGIN"),
    ('<form id="&#108;ogin-form">', "NEEDS_LOGIN"),
])
def test_a_form_attribute_is_a_parsed_key_not_a_regex_match(form, expected):
    """FINDING 3 UNDER THE CLASS RULING: the value cannot bleed into the next
    attribute because the parser ends it where HTML ends it.

    The word-bounded needle on the VALUE stays, and is not the patched mechanism
    returning: asked of a whole attribute value it is the right question, and
    `authenticity-token` -- Rails' CSRF field, on essentially every form Rails
    renders -- is what it keeps out.
    """
    # MUTATION: scan the raw markup for the attribute instead of reading the
    # parsed key.
    body = ('<html><head><title>Sign in to your account</title></head><body>'
            f'{form}<input type="text" name="q"></form></body></html>')
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == expected, form


@pytest.mark.parametrize("head,expected", [
    ("<title>Sign <b>in</b> to GitHub</title>", "NEEDS_LOGIN"),
    ("<title>Sign in to your account - &#72;elp</title>", "CONTINUE"),
    # UNCLOSED, AND STILL NOTHING: `title` stays None until `</title>`, which is
    # the precondition the regex had. Pinned so the parser does not newly fire
    # on a shape that used to yield no match at all.
    ("<title>Sign in to your account", "CONTINUE"),
    # No title element at all.
    ("<meta charset=utf-8>", "CONTINUE"),
    # The ordinary shapes, unmoved.
    ("<title>Sign in to GitHub</title>", "NEEDS_LOGIN"),
    ("<title>Sign in to your account - Help</title>", "CONTINUE"),
])
def test_the_login_title_is_the_parsed_title_elements_text(head, expected):
    """THE CLASS RULING ON THE <title> HALF (finding 2).

    `<title[^>]*>(.*?)</title>` captured MARKUP. `Sign <b>in</b> to GitHub` is a
    login page whose title the anchored needle could not match, and
    `- &#72;elp` is a help page whose exclusion word was not spelled as a word.
    The parser hands over the text a browser would show, so both read correctly
    and neither needs a rule of its own.
    """
    # MUTATION: read the title with `_TITLE_RE` again.
    body = (f"<html><head>{head}</head><body>"
            "<form id=login-form><input type=password name=p></form>"
            "</body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == expected, head


@pytest.mark.parametrize("title,expected", [
    # ABOUT signing in, not a sign-in page. The dropped alternative
    # `\bsign in to\b` was the only UNANCHORED needle in `_LOGIN_TITLE_RE`.
    ("How to sign in to your account | Acme Help", "CONTINUE"),
    ("Can't sign in to Slack? - Slack Help Center", "CONTINUE"),
    ("Troubleshooting: unable to sign in to the console", "CONTINUE"),
    # THE CONTROLS -- every title a real login page carries BEGINS with the
    # phrase, and the two surviving alternatives are anchored there.
    ("Sign in", "NEEDS_LOGIN"),
    ("Sign in to GitHub - GitHub", "NEEDS_LOGIN"),
    ("Sign in to your account", "NEEDS_LOGIN"),
    ("Log in to Acme", "NEEDS_LOGIN"),
    ("Login", "NEEDS_LOGIN"),
])
def test_the_login_title_arm_is_anchored_at_the_title_start(title, expected):
    """ROUND 4, THE SECOND HALF OF FINDING 3, decided rather than filed.

    `_LOGIN_TITLE_RE` carried one unanchored alternative beside two `^`-anchored
    ones, and it fired mid-title on help and documentation pages ABOUT signing
    in. That is the distinction this file already draws twice -- `_CHALLENGE_TITLES`
    exists so an article ABOUT bot walls is not rejected for its subject matter,
    and `_TITLE_BOUNDARY` keeps "Access Denied - Stack Overflow" out by requiring
    the phrase to START the title.

    Dropping it costs the mid-sentence form ("Please sign in to continue"), which
    now reads CONTINUE. One lost fetch against a window on a help article, and
    the lost fetch is the cheap direction.
    """
    # MUTATION: restore `|\bsign in to\b` to `_LOGIN_TITLE_RE`.
    body = (f"<html><head><title>{title}</title></head><body>"
            "<form id=login-form><input type=password name=p></form></body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == expected, title


@pytest.mark.parametrize("title,expected", [
    # THE DEFECT INPUT. The round-4 packet reported this narrowed; it was not.
    ("Sign in to your account - Help", "CONTINUE"),
    # THE SEPARATOR IS NOT THE MECHANISM -- the scan is over the whole title, so
    # every one of these is covered without a separator list.
    ("Sign in to your account | Acme Help Center", "CONTINUE"),
    ("Sign in to your account \u2014 Docs", "CONTINUE"),
    ("Sign in to your account \u2013 Support", "CONTINUE"),
    ("Sign in to your account \u00b7 FAQ", "CONTINUE"),
    ("Sign in to your account \u2022 Troubleshooting", "CONTINUE"),
    ("Log in :: Documentation", "CONTINUE"),
    ("Sign in problems - Troubleshoot", "CONTINUE"),
    # THE CONTROLS -- how a real login page is titled, unchanged.
    ("Sign in to GitHub", "NEEDS_LOGIN"),
    ("Sign in to your account", "NEEDS_LOGIN"),
    ("Sign in - Acme", "NEEDS_LOGIN"),
    ("Login", "NEEDS_LOGIN"),
])
def test_a_help_or_docs_title_is_not_a_login_title(title, expected):
    r"""FINDING 2, ROUND 4, RULED 2026-09-03: dropping the unanchored alternative
    did nothing for a title that BEGINS with the login phrase and then says what
    the page actually is.

    "Sign in to your account - Help" matched `^(?:sign|log)\s*[-_]?in\b`, so a
    help article carrying the site-wide header login form -- every Discourse
    forum, among others -- satisfied `_login_form` and returned NEEDS_LOGIN,
    driving the owner's logged-in Chrome at a documentation page (C-3/C-4).

    `_CHALLENGE_TITLES` is the precedent: an article ABOUT bot walls is not a
    wall, and an article about signing in is not a sign-in page.
    """
    # MUTATION: drop `_HELP_TITLE_RE` from `_is_login_title`, or scan only the
    # segment after a " - " separator.
    body = (f"<html><head><title>{title}</title></head><body>"
            "<form id=login-form><input type=password name=p></form></body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == expected, title


@pytest.mark.parametrize("title", ["Sign in", "Sign-in | Acme", "Log in to Acme",
                                   "Login", "Sign in to GitHub - GitHub"])
def test_login_form_fires_on_a_sign_in_title_with_form_evidence(title):
    body = (f"<html><head><title>{title}</title></head><body>"
            "<form id=login-form><input type=password name=p></form></body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    body=body) == "NEEDS_LOGIN"


def test_login_form_needs_html_content_type():
    body = ("<html><head><title>Sign in</title></head><body>"
            "<form id=login><input type=password name=p></form></body></html>")
    assert _verdict(tier="headless", status=200, headers=dict(NEUTRAL),
                    content_type="text/plain", body=body) == "CONTINUE"


@pytest.mark.parametrize("status", [407, 511])
def test_proxy_and_network_auth_are_not_a_site_login(status):
    """A 407 or a 511 is not something the user's logged-in Chrome holds."""
    body = ("<html><head><title>Sign in</title></head><body>"
            "<form id=login><input type=password name=p></form></body></html>")
    assert _verdict(tier="headless", status=status, headers=dict(NEUTRAL),
                    body=body) == "CONTINUE"


# ---- Precedence --------------------------------------------------------------

def test_rate_limited_outranks_a_captcha_body():
    """A window cannot clear a rate limit."""
    # MUTATION: move the CAPTCHA block above the RATE_LIMITED block. The
    # synthetic PerimeterX cookie is what makes that mutation killable.
    assert _verdict(tier="headless", status=429,
                    headers={"Retry-After": "60", "Content-Type": HTML},
                    set_cookie=list(PX_COOKIES),
                    body=_fixture("perimeterx")) == "RATE_LIMITED"


def test_hard_block_outranks_a_captcha_body():
    # MUTATION: move the CAPTCHA block above the HARD_BLOCK block.
    assert _verdict(tier="headless", status=451, headers=dict(NEUTRAL),
                    set_cookie=list(PX_COOKIES),
                    body=_fixture("perimeterx")) == "HARD_BLOCK"


def test_captcha_outranks_the_login_arms_below_it():
    """The NARROWED form of S3.4's `not CAPTCHA_CHALLENGE` guard.

    A PerimeterX interactive captcha still wins over `WWW_AUTH_COOKIEABLE`, and
    likewise over `JSON_UNAUTH` and `LOGIN_FORM`: a login form rendered ON a
    captcha interstitial is a captcha. Only the two REDIRECT arms carry the "you
    were sent here" evidence, and only they outrank the captcha block -- see the
    tests below.
    """
    assert _verdict(tier="headless", status=401,
                    headers={"WWW-Authenticate": 'Basic realm="x"', "Content-Type": HTML},
                    set_cookie=list(PX_COOKIES),
                    body=_fixture("perimeterx")) == "CAPTCHA_CHALLENGE"
    # A sign-in title and a password box rendered ON the interstitial itself.
    body = ('<html><head><title>Sign in</title></head><body>'
            '<script src="/px/captcha.js?a=c&u=1"></script>'
            '<form id=login><input type=password name=p></form></body></html>')
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=list(PX_COOKIES), body=body) == "CAPTCHA_CHALLENGE"


def test_a_final_url_login_redirect_outranks_a_measured_vendor_captcha():
    """FINDING 2, RULED 2026-09-03: the final-URL arm is PROMOTED to the
    precedence `LOGIN_HOP` has, above the captcha block.

    `_login_hop` cannot receive real evidence at any tier where a verdict is
    computed (see the section above), so the 2026-09-03 ruling that a login
    redirect outranks a captcha marker had no producible input. `final_url` IS
    produced at tier >= 1 -- `tier_playwright` sets it from the post-navigation
    URL -- and it is the same evidence one observation later: the cascade asked
    for A and was answered from a login URL B. Solving the captcha on B does not
    get you A; it gets you a login form.

    This is the input the promotion was made FOR, and it is the shape the real
    transport builds: a zillow PerimeterX interstitial served after a bounce to
    `/users/sign_in`.
    """
    # MUTATION: demote the final-URL arm back into the block below the captcha
    # check (M16), or demote both promoted arms (M9).
    # The synthetic PerimeterX cookie is load-bearing: without it there is no
    # captcha for the login arm to outrank and the assertion passes vacuously.
    assert _verdict(tier="headless", status=403, headers=dict(NEUTRAL),
                    set_cookie=list(PX_COOKIES),
                    requested_url="https://www.zillow.com/homes",
                    final_url="https://www.zillow.com/users/sign_in",
                    body=_fixture("perimeterx")) == "NEEDS_LOGIN"


def test_a_final_url_login_redirect_on_a_proxy_auth_status_is_not_a_site_login():
    """The 407/511/451 exclusion covers the PROMOTED arms as well as the arms
    below the captcha block -- the S8.9 shape: a guard does not move with the
    predicate it used to guard."""
    # MUTATION: drop `login_eligible` from the promoted condition.
    assert _verdict(tier="headless", status=407, headers=dict(NEUTRAL),
                    requested_url="https://app.example.com/dash",
                    final_url="https://app.example.com/users/sign_in",
                    body="<html></html>") == "CONTINUE"


@pytest.mark.parametrize("fixture_name,headers,cookies", [
    ("perimeterx", NEUTRAL, PX_COOKIES),
    ("datadome", DD_HEADERS, []),
])
def test_unproducible_hops_login_hop_outranks_a_measured_vendor_captcha(
        fixture_name, headers, cookies):
    """RULED 2026-09-03, overriding S3.4's mechanical guard with S3.4's prose.

    UNPRODUCIBLE INPUT -- see the section header. The producible assertion of the
    same ruling is
    `test_a_final_url_login_redirect_outranks_a_measured_vendor_captcha`.
    """
    # MUTATION: put the `_login_hop` arm back inside the block below the captcha
    # check (M9 restores the captcha-first ordering).
    hops = [(302, "https://app.example.com/dash",
             "https://accounts.google.com/o/oauth2/v2/auth?client_id=x")]
    assert _verdict(tier="headless", status=403, headers=dict(headers),
                    set_cookie=list(cookies), hops=hops,
                    body=_fixture(fixture_name)) == "NEEDS_LOGIN"


def test_unproducible_hops_a_login_hop_on_a_proxy_auth_status_is_not_a_site_login():
    """The 407/511/451 exclusion is hoisted so it covers the promoted
    `LOGIN_HOP` arm as well as the arms below the captcha block.

    UNPRODUCIBLE INPUT -- see the section header.
    """
    # MUTATION: drop `login_eligible` from the promoted condition.
    hops = [(302, "https://app.example.com/dash",
             "https://accounts.google.com/o/oauth2/v2/auth?client_id=x")]
    assert _verdict(tier="headless", status=407, headers=dict(NEUTRAL),
                    hops=hops, body="<html></html>") == "CONTINUE"


def test_header_keys_are_case_insensitive():
    assert _verdict(tier="headless", status=405,
                    headers={"X-Amzn-Waf-Action": "captcha", "Content-Type": HTML},
                    body="") == "CAPTCHA_CHALLENGE"


# ---- Shared visible-access evidence and HTTP-only structural hints -----------

@pytest.mark.parametrize("phrase", [
    "subscribe to continue reading", "subscribe to read", "subscription required",
    "subscribers only", "unlock this article",
])
def test_visible_access_gate_recognizes_each_normalized_restriction(phrase):
    text = "Publisher: " + " \n\t ".join(phrase.upper().split()) + "."
    assert extract.visible_access_gate([
        {"visible": True, "text": text, "controls": ["Subscribe now"]},
    ]) is True


@pytest.mark.parametrize("control", [
    "SUBSCRIBE", "Manage subscription", "Subscriber sign in", "Purchase access",
    "Buy now", "Become a member", "Membership options",
])
def test_visible_access_gate_recognizes_subscription_purchase_member_controls(control):
    assert extract.visible_access_gate([
        {"visible": True, "text": "Unlock this article", "controls": [control]},
    ]) is True


@pytest.mark.parametrize("visible", [False, None, 0, 1, "true"])
def test_visible_access_gate_requires_literal_observed_visibility(visible):
    assert extract.visible_access_gate([
        {"visible": visible, "text": "Subscription required", "controls": ["Subscribe"]},
    ]) is False


@pytest.mark.parametrize("text,controls", [
    ("Read our subscription news", ["Subscribe"]),
    ("Subscribe for the newsletter", ["Subscribe"]),
    ("Resubscribe to read", ["Subscribe"]),
    ("Subscribers onlyish", ["Subscribe"]),
    ("Subscription required", []),
    ("Subscription required", ["Continue", "Learn more", "Join the discussion"]),
    ("Subscription required", ["Unlock"]),
    ("Subscription required", ["Remember me", "Unsubscribe"]),
])
def test_visible_access_gate_requires_exact_phrase_and_subscription_control(text, controls):
    assert extract.visible_access_gate([
        {"visible": True, "text": text, "controls": controls},
    ]) is False


def test_visible_access_gate_does_not_combine_separate_candidates():
    assert extract.visible_access_gate([
        {"visible": True, "text": "Subscription required", "controls": ["Close"]},
        {"visible": True, "text": "Get our newsletter", "controls": ["Subscribe"]},
        {"visible": False, "text": "Subscription required", "controls": ["Subscribe"]},
    ]) is False


@pytest.mark.parametrize("candidate", [
    None, {}, {"visible": True, "text": "Subscription required", "controls": "Subscribe"},
    {"visible": True, "text": None, "controls": ["Subscribe"]},
    {"visible": True, "text": "Subscription required", "controls": [None]},
])
def test_visible_access_gate_does_not_treat_missing_observations_as_evidence(candidate):
    assert extract.visible_access_gate([candidate]) is False


def test_visible_access_gate_reads_later_candidates_without_mutating_observations():
    candidates = [
        {"visible": False, "text": "Subscription required", "controls": ["Subscribe"]},
        {"visible": True, "text": "Subscribers only", "controls": ["Member sign in"]},
    ]
    assert extract.visible_access_gate(candidates) is True
    assert candidates == [
        {"visible": False, "text": "Subscription required", "controls": ["Subscribe"]},
        {"visible": True, "text": "Subscribers only", "controls": ["Member sign in"]},
    ]


@pytest.mark.parametrize("body", [
    "<dialog><p> SUBSCRIPTION \n REQUIRED </p><button>Become a <b>member</b></button></dialog>",
    '<div role="dialog"><p>Subscribe to continue reading</p><a href="/subscribe">Subscribe</a></div>',
    '<section aria-modal="true"><h2>Subscribers only</h2><button>Purchase access</button></section>',
    '<form><p>Unlock this article</p><input type="submit" value="Buy now"></form>',
    '<form><p>Subscribe to read</p><button aria-label="Subscription options">Continue</button></form>',
    '<form><p>Subscribe to <span>read</span></p><button>Subscribe</button></form>',
])
def test_access_gate_hint_recognizes_structural_control_and_restriction(body):
    assert extract.access_gate_hint(body, HTML) is True


@pytest.mark.parametrize("body", [
    '<article><p>Subscribe to read is a common publisher phrase.</p><a href="/subscribe">Subscribe</a></article>',
    '<article><p>Subscription required</p></article><form><button>Subscribe</button></form>',
    '<dialog><p>Subscription required</p></dialog><a href="/subscribe">Subscribe</a>',
    '<form><p>Get our newsletter</p><button>Subscribe</button></form>',
    '<form><p>Subscription required</p><button>Continue</button></form>',
    '<meta name="subscription" content="required"><article>Readable paid article</article>',
    '<script type="application/ld+json">{"isAccessibleForFree":false,"text":"Subscription required"}</script>',
    '<dialog><script>"Subscription required"</script><button>Subscribe</button></dialog>',
    '<dialog><style>/* Subscription required */</style><button>Subscribe</button></dialog>',
    '<template><dialog><p>Subscription required</p><button>Subscribe</button></dialog></template>',
    '<dialog><p>Subscription required</p><script>"<button>Subscribe</button>"</script></dialog>',
    '<dialog><p>Subscription required</p><input type="hidden" value="Subscribe"></dialog>',
    '<dialog><p>Subscription required</p><!-- <button>Subscribe</button> --></dialog>',
    '<svg><form><p>Subscription required</p><button>Subscribe</button></form></svg>',
    '<math><form><p>Subscription required</p><button>Subscribe</button></form></math>',
    '<div data-role="dialog"><p>Subscription required</p><button>Subscribe</button></div>',
    '<div role="article" role="dialog"><p>Subscription required</p><button>Subscribe</button></div>',
])
def test_access_gate_hint_ignores_article_mentions_metadata_and_nonrendered_controls(body):
    assert extract.access_gate_hint(body, HTML) is False


def test_access_gate_hint_does_not_assert_computed_visibility():
    body = '<form hidden><p>Subscription required</p><button>Subscribe</button></form>'
    assert extract.access_gate_hint(body, HTML) is True
    assert extract.visible_access_gate([
        {"visible": False, "text": "Subscription required", "controls": ["Subscribe"]},
    ]) is False


@pytest.mark.parametrize("content_type", ["text/plain", "application/json", "application/pdf", ""])
def test_access_gate_hint_only_parses_html(content_type):
    body = '<form><p>Subscription required</p><button>Subscribe</button></form>'
    assert extract.access_gate_hint(body, content_type) is False


@pytest.mark.parametrize("head,form,expected", [
    ("<title>Sign in</title>", '<form id="login"><input type="password"></form>', True),
    ("<title>Sign <b>in</b> to Acme</title>", '<form id="login"></form>', True),
    ("<title>Public article</title>", '<form id="login"><input type="password"></form>', False),
    ("<title>Sign in - Help</title>", '<form id="login"></form>', False),
    ("<title>Sign in</title>", '<script>"<form id=login><input type=password>"</script>', False),
    ("<svg><title>Sign in</title></svg>", '<form id="login"></form>', False),
    ("<title>Sign in", '<form id="login"></form>', False),
    ("<title>Sign in</title>", '<form id="search" id="login"></form>', False),
])
def test_access_gate_hint_preserves_login_form_title_and_parser_rules(head, form, expected):
    body = f"<html><head>{head}</head><body>{form}</body></html>"
    assert extract.access_gate_hint(body, HTML) is expected


@pytest.mark.parametrize("prefix", [
    '<svg><title>Other</title></svg>',
    '<script>"<dialog>Subscription required<button>Subscribe</button></dialog>"</script>',
    '<template><div>Unused template</div></template>',
    '</svg>',
])
def test_access_gate_hint_retains_real_candidate_after_nonrendered_or_foreign_content(prefix):
    body = prefix + '<dialog><p>Subscribers only</p><button>Become a member</button></dialog>'
    assert extract.access_gate_hint(body, HTML) is True


@pytest.mark.parametrize("mark", ["<![foo]>", "<![then[ x ]]>", "<![>"])
def test_access_gate_hint_discards_partial_access_evidence_on_parser_refusal(mark):
    body = '<form><p>Subscription required</p><button>Subscribe</button></form>' + mark
    assert extract.access_gate_hint(body, HTML) is False


def test_disabled_copilot_is_not_browser_login_evidence():
    assert _verdict(tier="copilot", status=200, headers=dict(NEUTRAL),
                    body='<title>Sign in</title><form id="login"></form>') == "CONTINUE"


@pytest.mark.parametrize("body", [
    '<form><input role="button"><p>Subscription required</p><p>Subscribe for updates</p></form>',
    '<input aria-modal="true"><p>Subscription required</p><button>Subscribe</button>',
])
def test_access_gate_hint_void_elements_cannot_capture_later_page_text(body):
    assert extract.access_gate_hint(body, HTML) is False
