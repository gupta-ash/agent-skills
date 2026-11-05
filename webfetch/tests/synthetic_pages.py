"""Authored HTML for content and escalation tests; no captured site responses."""


def _page(title, body):
    return f"<!doctype html><html><head><title>{title}</title></head><body>{body}</body></html>"


_ARTICLE = (
    "A public reference document explains how readers can compare independent "
    "observations, preserve useful context, and report uncertainty clearly. "
)
_SHORT = (
    "This domain is for use in documentation examples without needing "
    "permission. Avoid use in operational applications."
)

PAGES = {
    "cloudflare_check": _page("Reference", '<div id="cf-browser-verification">Checking your browser.</div><script src="/cdn-cgi/challenge-platform/h/check.js"></script>'),
    "cloudflare_platform": _page("Reference", '<script src="/cdn-cgi/challenge-platform/h/check.js"></script><p>Please wait for this check.</p>'),
    "cloudflare_form": _page("Reference", '<form id="cf-browser-verification"><p>Complete this browser check.</p></form><script src="/cdn-cgi/challenge-platform/h/check.js"></script>'),
    "cloudflare_inline": _page("Reference", '<div data-check="/cdn-cgi/challenge-platform/h/check">Browser check in progress.</div>'),
    "datadome": _page("Reference", '<script src="https://ct.captcha-delivery.com/c.js"></script><p>Complete the check to continue.</p>'),
    "title_denied": _page("Access denied", '<p>This document is not available.</p>'),
    "title_robot": _page("Are you a robot", '<p>Complete the check below.</p>'),
    "perimeterx": _page("Access to this page has been denied", '<script src="https://captcha.px-cdn.net/fixture/captcha.js?a=c"></script><p>Complete the check below.</p>'),
    "akamai": _page("Reference", '<div id="sec-if-cpt-container">Complete the browser check.</div>'),
    "turnstile_title": _page(
        "Verifying your browser | Reference",
        '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
        '<div class="cf-turnstile"></div><p>'
        + "Complete the check below to continue to the reference document. " * 5 + '</p>'),
    "article": _page("Reference guide", '<article><h1>Reference guide</h1><p>' + _ARTICLE * 8 + '</p></article>'),
    "short_document": _page("Example document", '<h1>Example document</h1><p>' + _SHORT + '</p>'),
    "paragraphs": _page("Public notes", '<h1>Public notes</h1><p>' + _ARTICLE * 3 + '</p><p>' + _ARTICLE * 3 + '</p>'),
    "reference": _page("Example reference", '<h1>Example reference</h1><p>' + _ARTICLE * 6 + '</p>'),
    "plain_article": _page("Plain article", '<h1>Plain article</h1><p>' + _ARTICLE * 6 + '</p>'),
}
