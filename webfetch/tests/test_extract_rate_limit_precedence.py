"""`extract.escalation_verdict`: a CHALLENGE DOCUMENT IS NOT A RATE LIMIT.

THE DEFECT THIS FILE PINS (2026-09-04). `escalation_verdict` returned
RATE_LIMITED on the mere PRESENCE of a `Retry-After` header, above the whole
captcha and login block -- so a Cloudflare interstitial served as `503
Retry-After: 30` was reported as a throttle, the captcha was never diagnosed,
and no ask was produced. Measured against the unfixed predicate: the
byte-identical body WITHOUT the header returned CAPTCHA_CHALLENGE. R1 (the
fetch must succeed) and R5 (never fail silently).

THE COST WAS LARGER WHEN THIS WAS WRITTEN, and the original wording is kept
here because it explains the severity: RATE_LIMITED was TERMINAL, so the
mis-verdict also broke the cascade at that rung and lost the headed tier and
the cdp rung outright. `fetch.py` stopped breaking on RATE_LIMITED later the
same day, so a mis-verdict no longer breaks the cascade at that rung.
IT STILL COSTS MORE THAN THE LABEL (review round 2, H3): `_gate_allows` admits
the cdp rung on `NEEDS_LOGIN`, or on a captcha seen earlier in this run once
headed has had its turn. A captcha called RATE_LIMITED never sets that sticky
flag, so the cdp rung and the ASK that goes with it are lost even though the
ladder was climbed. Neither the
predicate nor any assertion in this file changed with it.

WHAT IS DELIBERATELY UNCHANGED, and half the tests here are those controls: a
`429` status and Cloudflare's own `error code: 1015` still decide RATE_LIMITED
whatever the document says. The 1015 control is not decoration -- Cloudflare's
real 1015 page carries two `_CLIENT_DEMANDS` phrases, so it READS as positively
a challenge, and gating that arm on the document would have un-terminated the
single case the verdict exists for.

Each behavioural test names the change to `extract.py` that would make it pass
wrongly. No network, no browser: the module under test is a pure function.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import extract

HTML = "text/html; charset=utf-8"
# A real Cloudflare edge response. PRESENT, not a wall.
CF = {"Server": "cloudflare", "CF-RAY": "8d1f0a2b3c4d5e6f-SJC", "Content-Type": HTML}

# A Cloudflare managed-challenge interstitial. Two independent markers, both
# from `_CHALLENGE_MARKUP`: the wrapper class, and the orchestration path in its
# `/h/` form (the form the file pins BECAUSE Bot Fight Mode injects the `jsd/`
# one into ordinary 200s).
CF_INTERSTITIAL = (
    "<html><head><title>Just a moment...</title></head><body>"
    "<div id='cf-wrapper'><div class='cf-browser-verification'></div></div>"
    "<script>window._cf_chl_opt={};"
    "var a=document.createElement('script');"
    "a.src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1?ray=8d1f';"
    "document.body.appendChild(a);</script></body></html>"
)

# Cloudflare's REAL rate-limit page, reconstructed from the strings it serves.
# "you have been blocked" and "this website is using a security service to
# protect itself" are BOTH `_CLIENT_DEMANDS` members, which is the whole point
# of the control below.
CF_1015 = (
    "<html><head><title>Access denied | example.com used Cloudflare to "
    "restrict access</title></head><body>"
    "<h1>Sorry, you have been blocked</h1>"
    "<p>This website is using a security service to protect itself from online "
    "attacks.</p><p>error code: 1015</p></body></html>"
)

# An ordinary origin rate limit: no vendor, no challenge markup, no demand
# phrase, and short -- which is exactly the shape
# `_document_refuses_with_a_challenge`'s second arm answers True for.
PLAIN_429_BODY = "<html><body><h1>Too many requests</h1></body></html>"


def _verdict(**kw):
    """`escalation_verdict` with defaults for the arguments a case is not about."""
    args = dict(tier="headless", status=200, headers=dict(CF), set_cookie=[],
                hops=[], body="", content_type=HTML,
                requested_url="https://example.com/page", final_url="")
    args.update(kw)
    return extract.escalation_verdict(**args)


def _cf(**extra):
    h = dict(CF)
    h.update(extra)
    return h


# ---- The defect --------------------------------------------------------------

@pytest.mark.parametrize("status", [503, 403, 429 - 1])
def test_a_challenge_served_with_retry_after_is_a_captcha_not_a_rate_limit(status):
    """THE DEFECT. A Cloudflare interstitial carrying `Retry-After` returned
    RATE_LIMITED, which was TERMINAL at the time, so the cascade never reached
    headed or cdp and no ask was produced. The break is gone from `fetch.py`
    since 2026-09-04; the mis-verdict still costs the captcha diagnosis and the
    ask that hangs off it, which is what this asserts.

    `429 - 1` is written out rather than 428 so that a reader sees the case is
    "any status BELOW the one arm that is allowed to ignore the body".
    """
    # MUTATION: drop the `not _document_is_positively_a_challenge(body)`
    # conjunct from the `retry-after` arm (i.e. restore the header-presence
    # test).
    assert _verdict(status=status, headers=_cf(**{"Retry-After": "30"}),
                    body=CF_INTERSTITIAL) == "CAPTCHA_CHALLENGE"


def test_the_header_was_the_whole_difference():
    """THE CONTROL THAT MAKES THE TEST ABOVE MEAN SOMETHING -- the byte-identical
    body without the header already read CAPTCHA_CHALLENGE on the unfixed code.

    Passes on both revisions, deliberately: without it, the assertion above
    could be satisfied by a captcha arm that fires on anything.
    """
    assert _verdict(status=503, headers=dict(CF),
                    body=CF_INTERSTITIAL) == "CAPTCHA_CHALLENGE"


def test_a_lowercased_or_odd_cased_retry_after_is_still_gated():
    """`escalation_verdict` lowercases the header keys itself, so the repair must
    not be reachable only through one casing."""
    # MUTATION: compare the raw key instead of the lowercased dict.
    assert _verdict(status=503, headers=_cf(**{"RETRY-AFTER": "30"}),
                    body=CF_INTERSTITIAL) == "CAPTCHA_CHALLENGE"


def test_a_challenge_title_page_with_retry_after_is_not_a_rate_limit():
    """The SECOND arm of the challenge test -- a `_CHALLENGE_TITLES` <title> and
    no interstitial-only markup at all.

    The verdict here is the widget arm (`_interactive_captcha_widget` conjoined
    with `_document_refuses_with_a_challenge`), which is what the repair hands
    the document to once `Retry-After` stops short-circuiting above it.
    """
    body = ("<html><head><title>Verify you are human...</title></head><body>"
            '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js">'
            "</script></body></html>")
    # MUTATION: gate on `_document_refuses_with_a_challenge` only where the
    # markup arm can see it -- e.g. `page_served=False` scanning the embeddable
    # tuple -- rather than on the file's own challenge test.
    assert _verdict(status=503, headers=_cf(**{"Retry-After": "30"}),
                    body=body) == "CAPTCHA_CHALLENGE"


# ---- What must NOT have moved ------------------------------------------------

def test_a_429_with_retry_after_and_no_challenge_is_still_rate_limited():
    """The verdict the block exists for. Passes on both revisions."""
    assert _verdict(status=429, headers=_cf(**{"Retry-After": "120"}),
                    body=PLAIN_429_BODY) == "RATE_LIMITED"


def test_a_503_retry_after_with_no_challenge_is_still_rate_limited():
    """`Retry-After` on any status is still the verdict when the document is not
    a challenge. Passes on both revisions -- it is what stops the repair being
    "delete the `retry-after` arm"."""
    assert _verdict(status=503, headers=_cf(**{"Retry-After": "30"}),
                    body="<html><body>Service unavailable. Back shortly."
                         "</body></html>") == "RATE_LIMITED"


def test_a_short_non_challenge_body_does_not_defeat_the_retry_after_arm():
    """THE PREDICATE CHOICE, PINNED. `_document_refuses_with_a_challenge` answers
    True for ANY body under SHELL_TEXT_MAX of visible text -- it is one half of a
    conjunction with a widget test and is loose on purpose. Using it here would
    make every terse `Too many requests` page stop being a rate limit.
    """
    # MUTATION: gate the `retry-after` arm on
    # `_document_refuses_with_a_challenge` instead of
    # `_document_is_positively_a_challenge`.
    assert len(extract._visible_text(PLAIN_429_BODY)) <= extract.SHELL_TEXT_MAX
    assert extract._document_refuses_with_a_challenge(PLAIN_429_BODY) is True
    assert _verdict(status=503, headers=_cf(**{"Retry-After": "30"}),
                    body=PLAIN_429_BODY) == "RATE_LIMITED"


def test_cloudflare_1015_stays_terminal_even_though_it_reads_as_a_challenge():
    """MEASURED, NOT ASSUMED. Cloudflare's own rate-limit page carries two
    `_CLIENT_DEMANDS` phrases, so the file's challenge test answers True on it.
    Gating the 1015 arm on the document would have un-terminated the exact case
    the verdict exists for.
    """
    # MUTATION: add the `not _document_is_positively_a_challenge(body)` conjunct
    # to the `error code: 1015` arm as well.
    assert extract._document_is_positively_a_challenge(CF_1015) is True
    assert _verdict(status=503, headers=_cf(**{"Retry-After": "30"}),
                    body=CF_1015) == "RATE_LIMITED"


def test_a_451_still_outranks_the_whole_block():
    """HARD_BLOCK is decided ABOVE RATE_LIMITED and the repair did not move it.
    Passes on both revisions."""
    assert _verdict(status=451, headers=_cf(**{"Retry-After": "3600"}),
                    body=CF_INTERSTITIAL) == "HARD_BLOCK"


def test_tier_0_keeps_its_terminal_rate_limit():
    """Rule B narrowing, unmoved: a tier-0 429 is terminal on STATUS alone and
    needs no browser body. Passes on both revisions."""
    assert _verdict(tier="http", status=429,
                    headers=_cf(**{"Retry-After": "120"}),
                    body="") == "RATE_LIMITED"


# ---- The predicate itself ----------------------------------------------------

def test_the_predicate_reads_the_visible_text_not_the_script_source():
    """`_challenge_reason`'s haystack rule, inherited rather than restated: a
    page whose own application JavaScript contains a demand phrase is not a
    challenge."""
    body = ("<html><head><title>Dashboard</title></head><body>"
            "<p>" + "x" * 800 + "</p>"
            "<script>var msg = 'verify you are human';</script></body></html>")
    # MUTATION: pass `body` as the `text` argument instead of
    # `_strip_script_and_style(body)`.
    assert extract._document_is_positively_a_challenge(body) is False


def test_the_predicate_does_not_read_an_embeddable_mount_as_a_wall():
    """`class="cf-turnstile"` is the mount a SITE puts on its own form. Admitting
    it here would make an ordinary error page carrying a contact-form widget stop
    being a rate limit."""
    body = ("<html><head><title>Server error</title></head><body>"
            "<p>" + "y" * 800 + "</p>"
            "<div class=\"cf-turnstile\" data-sitekey=\"x\"></div>"
            "</body></html>")
    # MUTATION: call `_challenge_reason` with `page_served=False`.
    assert extract._document_is_positively_a_challenge(body) is False


def test_the_predicate_is_the_first_arm_of_the_widget_predicate():
    """One definition, two callers. A document the standalone predicate calls a
    challenge is one `_document_refuses_with_a_challenge` calls a challenge too;
    the second differs only by the shell arm it ORs in."""
    assert extract._document_is_positively_a_challenge(CF_INTERSTITIAL) is True
    assert extract._document_refuses_with_a_challenge(CF_INTERSTITIAL) is True
