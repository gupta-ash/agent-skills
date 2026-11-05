"""ARM 3 of `extract.escalation_verdict` -- an interactive captcha widget on a
document the origin refused (extract.py, 2026-09-04).

WHY THIS ARM NEEDS ITS OWN FILE. Restored on 2026-09-04 as "non-2xx AND a
captcha widget is mounted", the arm was excused by a cost paragraph that said a
false positive "opens nothing the CONTINUE path would not also open". That was
false. `fetch._gate_allows` admits cdp on `NEEDS_LOGIN or (captcha_seen and
headed_attempted)`, and `captcha_seen` is set by this verdict alone -- so on a
non-login run CAPTCHA_CHALLENGE is the ONE thing that unlocks the cdp rung, and
the rung's no-target path asks the owner to go start a browser.

THE SHAPE THAT MADE IT A DEFECT IS ORDINARY. A site whose global template loads
`recaptcha/api.js` for a newsletter or contact form serves that template on its
404 too. Tier 0 returns the 404 with an empty body (CONTINUE); tier 1 returns
the same 404 with the full DOM, the arm fires, and the owner is paged about a
captcha for a page that does not exist.

The arm now takes a third conjunct -- `_document_refuses_with_a_challenge` --
and these tests pin both directions of it: the ordinary 404 is refused, and a
document that really is a wall still fires.

No network and no browser: the module under test is a pure function.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extract

HTML = "text/html; charset=utf-8"
NEUTRAL = {"Content-Type": HTML}

# The client each family actually serves, mounted the way a site's own template
# mounts it -- in <head>, for a form somewhere else on the page.
RECAPTCHA = '<script src="https://www.google.com/recaptcha/api.js" async></script>'
TURNSTILE = '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
# Cloudflare's documented MOUNT POINT. A site puts this on its own form; it is
# `_EMBEDDABLE_CHALLENGE_MARKUP`, and the same template that carries the client
# above carries this.
TURNSTILE_MOUNT = '<div class="cf-turnstile" data-sitekey="0x4AAAAAAADWbC"></div>'

# An ordinary site's chrome and its 404 message. Nothing here is a challenge:
# no `_CHALLENGE_TITLES` phrase, no `_CLIENT_DEMANDS` phrase, no interstitial
# markup -- and well over SHELL_TEXT_MAX of text the origin actually rendered.
CHROME = ("<nav><a href='/'>Home</a> <a href='/about'>About us</a> "
          "<a href='/blog'>Blog</a> <a href='/contact'>Contact</a></nav>")
NOT_FOUND_BODY = (
    "<h1>Nothing found here</h1>"
    "<p>Sorry, the page you were looking for is not on this site. It may have "
    "been moved, renamed or deleted some time ago. Try the search box above, or "
    "head back to the front page and start from the top -- most of what used to "
    "live at this address is still here somewhere.</p>"
    "<footer><p>Subscribe to the monthly newsletter for new posts.</p></footer>")


def _doc(title, head="", body=""):
    return (f"<html><head><title>{title}</title>{head}</head>"
            f"<body>{body}</body></html>")


# The failing shape, exactly: an ordinary 404 from a template that mounts a
# widget for a form.
TEMPLATE_404 = _doc("Page not found - Example Blog", head=RECAPTCHA,
                    body=CHROME + NOT_FOUND_BODY)


def _verdict(**kw):
    args = dict(tier="headless", status=404, headers=dict(NEUTRAL), set_cookie=[],
                hops=[], body="", content_type=HTML,
                requested_url="https://example.com/gone", final_url="")
    args.update(kw)
    return extract.escalation_verdict(**args)


def test_an_ordinary_404_from_a_widget_bearing_template_is_not_a_captcha():
    """THE DEFECT. A missing page is not a challenge, whatever its <head> loads.

    The independent check is the file's own established wall test: the same
    document classifies `origin_4xx_block`, not `cloudflare_challenge`. Nothing
    about it says the origin served a challenge INSTEAD of the page.
    """
    # MUTATION: drop the `_document_refuses_with_a_challenge` conjunct from the
    # widget arm -- this assertion is the one that dies.
    assert extract.classify_failure(404, TEMPLATE_404) == "origin_4xx_block", (
        "precondition: this document is not a wall by the file's own test")
    assert _verdict(status=404, body=TEMPLATE_404) == "CONTINUE"
    # The same template on the other statuses the arm is live for.
    assert _verdict(status=403, body=TEMPLATE_404) == "CONTINUE"
    assert _verdict(status=410, body=TEMPLATE_404) == "CONTINUE"


def test_the_widgets_own_mount_marker_may_not_corroborate_the_widget():
    """CIRCULARITY, and it is why the corroboration runs `page_served=True`.

    `class="cf-turnstile"` is the MOUNT POINT for the very client the widget
    predicate just found, so the template that puts one on its 404 puts the
    other there too. Accepting it as evidence of a refusal would let the arm
    corroborate itself and leave the Turnstile half of the defect untouched.
    """
    # MUTATION: pass `page_served=False` (or leave it defaulted) in
    # `_document_refuses_with_a_challenge`, which restores
    # `_EMBEDDABLE_CHALLENGE_MARKUP` as evidence.
    body = _doc("Page not found - Example Blog", head=TURNSTILE,
                body=CHROME + NOT_FOUND_BODY + TURNSTILE_MOUNT)
    assert _verdict(status=404, body=body) == "CONTINUE"


def test_a_document_that_is_nothing_but_the_widget_is_still_a_captcha():
    """R1's half. A challenge that renders itself from script has no title arm
    and no demand phrase to find -- what it has is nothing else at all.

    The captured walls run 41-43 extracted characters; SHELL_TEXT_MAX is the
    bound the JS-shell arm already uses, so this is one threshold rather than a
    new one.
    """
    # MUTATION: delete the whole arm, or drop the visible-text disjunct from
    # `_document_refuses_with_a_challenge`.
    bare = ('<html><body><iframe src="https://www.google.com/recaptcha/api2/'
            'anchor?k=x"></iframe></body></html>')
    assert _verdict(status=403, body=bare) == "CAPTCHA_CHALLENGE"


def test_the_same_template_is_a_captcha_when_the_document_really_is_a_wall():
    """THE CONTROL (plan S8.8), three ways -- without it the refusals above
    could pass for want of any working arm.

    Same chrome, same widget, same status. What changes is the one fact the arm
    now asks for: a `_CHALLENGE_TITLES` title, a `_CLIENT_DEMANDS` phrase in the
    text, or interstitial-only markup.
    """
    # MUTATION: narrow `_document_refuses_with_a_challenge` to the visible-text
    # bound alone -- every assertion here is over SHELL_TEXT_MAX.
    titled = _doc("Just a moment...", head=RECAPTCHA, body=CHROME + NOT_FOUND_BODY)
    assert _verdict(status=403, body=titled) == "CAPTCHA_CHALLENGE"

    demanded = _doc("Example Blog", head=RECAPTCHA,
                    body=CHROME + NOT_FOUND_BODY
                    + "<p>Verify you are human to continue.</p>")
    assert _verdict(status=403, body=demanded) == "CAPTCHA_CHALLENGE"

    interstitial = _doc(
        "Example Blog", head=RECAPTCHA,
        body=CHROME + NOT_FOUND_BODY
        + '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1">'
          "</script>")
    assert _verdict(status=403, body=interstitial) == "CAPTCHA_CHALLENGE"


def test_the_corroboration_does_not_resurrect_the_arm_on_a_2xx():
    """The non-2xx conjunct still decides first. A page the origin SERVED,
    carrying a widget and a challenge-shaped title, is still not a refusal --
    that ordering is what `_challenge_reason`'s `page_served` rule says, and the
    new conjunct is conjoined with it rather than replacing it.
    """
    # MUTATION: reorder the arm so the corroboration alone can carry it.
    titled = _doc("Just a moment...", head=RECAPTCHA, body=CHROME + NOT_FOUND_BODY)
    assert _verdict(status=200, body=titled) == "CONTINUE"
