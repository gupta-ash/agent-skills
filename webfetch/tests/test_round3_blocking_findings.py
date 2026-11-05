"""Retry-After must preserve captcha and origin rate-limit diagnoses."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extract


# A wall of the SECOND shape the captcha block accepts: an interactive captcha
# client mounted on a refused document that is shell-shaped. Deliberately
# carries NO interstitial markup marker, NO _CHALLENGE_TITLES title and NO
# client-demand phrase -- so `_document_is_positively_a_challenge` is False and
# ONLY the widget arm can name it. That is the whole point: the Retry-After
# suppressor used to consult the positively-a-challenge test alone.
SHELL_WALL = (
    "<html><head><title>x</title></head><body>"
    "<div id=\"c\"></div>"
    "<script src=\"https://www.google.com/recaptcha/api.js\"></script>"
    "</body></html>")


def _verdict(body, headers, status=503):
    return extract.escalation_verdict(
        tier="headless", status=status, headers=headers, set_cookie=[],
        hops=[], body=body, content_type="text/html",
        requested_url="https://example.com/p",
        final_url="https://example.com/p")


# ---- 1. an advisory header must not erase a captcha -------------------------

def test_the_fixture_is_a_captcha_before_any_header_is_added():
    """CONTROL, and it runs FIRST on purpose.

    Without it the test below passes just as well against a fixture nothing
    ever diagnosed, which is how this project has twice shipped a green test
    that exercised nothing.
    """
    assert _verdict(SHELL_WALL, {"Server": "cloudflare"}) == "CAPTCHA_CHALLENGE"


def test_a_retry_after_header_does_not_turn_a_widget_wall_into_a_rate_limit():
    """BLOCKING, round 3. Reading this document as a rate limit loses the
    captcha: `failure_class` becomes `rate_limited`, no captcha is ever
    diagnosed, and the CDP ask -- which rule 4 hangs off that diagnosis --
    is never emitted. A 503 carrying Retry-After is the ordinary reply a
    Cloudflare edge gives a client that failed a challenge, so this is the
    common path.

    THE ORIGINAL REASON WAS STRONGER AND IS NOW GONE: "RATE_LIMITED is TERMINAL
    -- the cascade breaks at that rung -- so it ends the run below the headed
    rung". True when this was written; false since 2026-09-04, when the break
    was removed from `fetch.py` as measurably wrong. The verdict here is
    unchanged and still right for the reason above.
    """
    assert _verdict(
        SHELL_WALL,
        {"Server": "cloudflare", "Retry-After": "30"}) == "CAPTCHA_CHALLENGE"


def test_a_plain_rate_limit_is_still_terminal():
    """The suppressor must not swallow the case the verdict exists for. No
    widget, no challenge shape -- an ordinary throttled response.
    """
    body = "<html><body><h1>Too many requests</h1>" + ("padding. " * 60) + "</body></html>"
    assert _verdict(body, {"Retry-After": "30"}) == "RATE_LIMITED"


def test_a_429_stays_terminal_whatever_the_body_carries():
    """The status arm is deliberately UNGATED: 429 is the origin declaring the
    reason in a status line, and no body can launder a status line.
    """
    assert _verdict(SHELL_WALL, {"Retry-After": "30"}, status=429) == "RATE_LIMITED"


