"""The unrendered-page notice arm: a needs-JavaScript notice is not the page.

A JavaScript-required notice is not the requested article. Accepting it as
document content would both return the wrong body and incorrectly teach the
blocklist that the plain HTTP tier succeeds.

WHY THE OLD ARMS MISSED IT. All three shell checks in `is_real_content` sit under
`if len(text) < SHELL_TEXT_MAX:`. The notice extracts to 286 characters, so it
skipped every one of them. So did the client-demand scan, which `_challenge_reason`
reaches only via `elif len(text) <= SHELL_TEXT_MAX`.

The full signature distinguishes a notice from help articles, support FAQs and
accessibility statements that mention the same browser condition in passing.
Single phrases or incomplete combinations cannot authorize a rejection.
The tests retain both complete-notice and incidental-mention controls.

WHY THIS IS NOT THE REFUTED CORROBORATION SHAPE. Requiring a second Akamai
container id was refuted because it SUPPRESSED a live detector and lost true
positives. This arm can only ADD a rejection, and the reason is PLACEMENT
ALONE -- it runs after every other arm, so each has already returned. NOT because
"nothing fires above SHELL_TEXT_MAX", which stood here until round 3 and is
false: `_challenge_reason` rejects documents of any size via the markup arm.
`test_the_rule_only_adds_and_never_removes_a_rejection` pins the direction.

WHY THIS ARM RUNS LAST, below the length-bounded block. It sat ABOVE that block
for one commit and there it STOLE the reason from the shell arm on a short
two-phrase input -- measured on 76 characters, `main` said "unrendered script
shell: 76 chars whose only content demands JS" and the branch said the notice
reason instead. Same verdict, different reason, so the claim that this arm "can
only ADD detections" was false where it mattered, and the 24-capture differential
missed it because no capture has that shape. Down here every arm above has
already returned, so the claim is true by construction.
`test_a_short_two_phrase_input_keeps_the_shell_arms_reason` pins it.

WHY IT IS BOUNDED AT DEMAND_TEXT_MAX. `is_real_content` is shared by every tier,
so a false rejection is TERMINAL for the whole cascade -- re-fetching through a
browser hits the same predicate. The sharp counterexample is a code host
RENDERING this very file, whose visible source carries all three phrases. The
bound puts that far out of reach while the observed notice, at 286 characters,
stays in. `test_the_signature_inside_substantial_content_is_admitted` pins it.

WHAT REJECTING COSTS, MEASURED: `classify_failure` returns `other` for the
observed notice, which is TRANSIENT, so no domain is promoted. That is a fact
about this document and NOT a guarantee about the arm. THE RESIDUAL IS A FALSE
PROMOTION: a 200..600 character ARTICLE quoting the whole signature AND a
client-demand phrase is rejected here and then classified `cloudflare_challenge`,
which is persistent, so a real page can walk its domain off tier 0. Nothing here
tells a served challenge from an article quoting one.
`test_the_observed_notice_stays_transient` pins the measured half and
`test_a_quoted_article_with_a_demand_phrase_is_a_known_false_promotion` pins the
residual.

WHAT THIS DOES NOT CLOSE, stated so nobody reads the backlog entry as finished:
a shell padded with NAVIGATION FURNITURE rather than notice text still passes.
`test_the_nav_furniture_shell_is_still_admitted` pins that honestly, and
`M-PADDED-SHELL-ACCEPTED-AS-CONTENT` stays open because of it.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import extract  # noqa: E402


# The observed body, verbatim from this box's cache, including the curly
# apostrophe in "couldn't". A paraphrase would not be the fixture: the first
# phrase set written for this change assumed "could not" and missed all four.
OBSERVED_NOTICE = (
    "JavaScript is disabled in your browser.\n"
    "Please enable JavaScript to proceed.\n"
    "A required part of this site couldn’t load. This may be due to a browser "
    "extension, network issues, or browser settings. Please check your "
    "connection, disable any ad blockers, or try using a different browser."
)

# A real help page that names the condition ONCE. Over SHELL_TEXT_MAX, so it sits
# in the band this arm governs -- a control below the bound is rejected by the
# pre-existing marker arm and proves nothing about this one.
HELP_PAGE = (
    "If JavaScript is disabled in your browser, follow the steps below to turn "
    "it back on. Most browsers let you re-enable it from the settings page, and "
    "the change takes effect after a reload. In Chrome, open Settings, choose "
    "Privacy and security, then Site settings, then JavaScript, and select the "
    "option allowing sites to run it. In Firefox the equivalent switch lives in "
    "about:config under javascript.enabled."
)

ORDINARY_ARTICLE = (
    "The migration finished on Tuesday. We moved every record in the primary "
    "table to the new schema, verified the row counts on both sides, and kept "
    "the old table in place for a week in case a rollback was needed. Nothing "
    "in the application layer changed. The one surprise was the index rebuild, "
    "which took four hours rather than the twenty minutes we had budgeted."
)


def _wrap(text):
    return ("<html><head><title>Page</title></head><body><p>%s</p></body></html>"
            % text)


def test_the_observed_notice_is_rejected_as_the_document():
    """THE DEFECT ITSELF. Fails against unfixed code, which returns (True, '')."""
    ok, reason = extract.is_real_content(OBSERVED_NOTICE,
                                         _wrap(OBSERVED_NOTICE), 200)
    assert ok is False, (
        "a needs-JavaScript notice was returned AS the page -- this is the "
        "content-loss defect M-PADDED-SHELL-ACCEPTED-AS-CONTENT was filed for")
    assert "unrendered-page notice" in reason, reason


def test_the_notice_is_ABOVE_the_bound_that_hid_it():
    """PRECONDITION -- the fixture must sit inside the arm's operating band.

    That band is SHELL_TEXT_MAX..DEMAND_TEXT_MAX. If the fixture ever drops under
    SHELL_TEXT_MAX the test above starts passing for a different reason -- the
    pre-existing marker arm -- and stops testing this change at all.
    """
    assert len(OBSERVED_NOTICE.strip()) > extract.SHELL_TEXT_MAX, (
        "the fixture must sit above the bound, or it is not this defect")


def test_one_phrase_is_not_enough():
    """THE FALSE POSITIVE THAT KILLED THE OBVIOUS FIX -- keep this test.

    Matching any single notice phrase against the extraction rejects a genuine
    help page. Measured: 'javascript is disabled in your browser' alone fires on
    4 of 6 constructed real pages.
    """
    assert "javascript is disabled in your browser" in HELP_PAGE.lower(), (
        "precondition: the control must actually contain one phrase, or it is "
        "green for the wrong reason")
    assert extract._unrendered_notice_reason(HELP_PAGE) == "", (
        "a real help page naming the condition once must not be rejected")
    ok, _ = extract.is_real_content(HELP_PAGE, _wrap(HELP_PAGE), 200)
    assert ok is True


def test_an_ordinary_article_is_untouched():
    """The control that must NOT move. Carries no phrase at all."""
    assert extract._unrendered_notice_reason(ORDINARY_ARTICLE) == ""
    ok, _ = extract.is_real_content(ORDINARY_ARTICLE,
                                    _wrap(ORDINARY_ARTICLE), 200)
    assert ok is True


def test_the_predicate_counts_DISTINCT_phrases():
    """Two occurrences of ONE phrase are one phrase.

    Without this the rule would fire on a real page that repeats a single
    sentence, which is the same false positive `test_one_phrase_is_not_enough`
    guards, arriving by repetition instead of by length.
    """
    one = "javascript is disabled in your browser. " * 4
    assert extract._unrendered_notice_reason(one) == "", (
        "repetition of one phrase is still one phrase")
    two = ("javascript is disabled in your browser. "
           "please enable javascript to proceed.")
    assert extract._unrendered_notice_reason(two) == "", (
        "ROUND 3: TWO IS NO LONGER ENOUGH. All four observations carry all "
        "three phrases, so any two-phrase combination is a signature nobody "
        "captured -- and every invented combination is a terminal false "
        "rejection for a 200..600 char page that quotes two.")
    three = ("javascript is disabled in your browser. "
             "please enable javascript to proceed. "
             "a required part of this site could not load.")
    assert extract._unrendered_notice_reason(three) != "", (
        "the full captured signature must still fire")


def test_the_rule_only_adds_and_never_removes_a_rejection():
    """THE DIRECTION IS THE SAFETY ARGUMENT, so it is pinned rather than argued.

    The arm can only turn an ADMIT into a reject. It is reached after the
    challenge check and the empty check have each had their chance to return, so
    no document this file rejects today can be admitted by it.
    """
    wall = '<html><body><div class="cf-browser-verification"></div></body></html>'
    ok, reason = extract.is_real_content("short", wall, 200)
    assert ok is False
    assert "challenge markup" in reason, (
        "the challenge arm must still own its verdict -- the notice arm runs "
        "after it and must not take the reason away")


def test_the_nav_furniture_shell_is_still_admitted():
    """AN HONEST NEGATIVE. This change does NOT close the constructed case.

    A shell padded with navigation furniture carries no notice phrase, so this
    arm cannot see it, and `M-PADDED-SHELL-ACCEPTED-AS-CONTENT` stays open. The
    test exists so that the entry's status cannot be misread from the suite:
    if someone later closes this shape, this test fails and they update the
    backlog deliberately rather than by accident.
    """
    nav = ("Home Products Pricing Docs Support Careers Blog Status Login "
           "Sign up Contact ")
    pad = (nav * 5)[:358]
    shell = ('<html><head><title>Dashboard</title>'
             '<script defer src="/static/js/main.8f7c2b1a.js"></script></head>'
             '<body><noscript>You need to enable JavaScript to run this app.'
             '</noscript><div id="root"></div><nav>' + pad + '</nav></body></html>')
    md = (extract.html_to_markdown(shell, "https://example.com/") or "").strip()
    assert len(md) > extract.SHELL_TEXT_MAX, "precondition: padded past the bound"
    ok, _ = extract.is_real_content(md, shell, 200)
    assert ok is True, (
        "if this now rejects, the nav-furniture shape has been closed too -- "
        "good, but update M-PADDED-SHELL-ACCEPTED-AS-CONTENT rather than "
        "deleting this test")


def test_every_phrase_was_read_off_the_observed_notice():
    """A phrase nobody has seen is a guess, and a guess in a marker set is
    machinery. This pins the set to the one body it was derived from, so a
    speculative addition fails here and has to be justified deliberately."""
    low = OBSERVED_NOTICE.lower()
    for phrase in extract._UNRENDERED_NOTICE:
        assert phrase in low, (
            "%r is in the marker set but not in the observed notice; if it came "
            "from a NEW capture, add that capture to this test" % phrase)


# --------------------------------------------------------------------------
# Review round 2. Each of the three below pins a finding that was CONFIRMED by
# measurement rather than accepted on the reviewer's say-so.
# --------------------------------------------------------------------------

def test_a_short_two_phrase_input_keeps_the_shell_arms_reason():
    """ROUND 2, FINDING 4 -- and this one was a real defect, not a false alarm.

    While the arm sat above the length-bounded block it answered short inputs
    that belonged to the shell arm. Measured on this exact 76-character string:
    `main` reported "unrendered script shell: 76 chars whose only content demands
    JS" and the branch reported the notice reason instead. Same verdict, so no
    corpus differential over verdicts could see it.

    The arm now runs last, so this asserts the OLD reason survives. It is a
    reason test on purpose: asserting only `ok is False` passes under the defect.
    """
    short = ("javascript is disabled in your browser. "
             "please enable javascript to proceed.")
    assert len(short) < extract.SHELL_TEXT_MAX, (
        "precondition: the input must be short enough to reach the shell arms, "
        "or this test is green for the wrong reason")
    ok, reason = extract.is_real_content(short, _wrap(short), 200)
    assert ok is False
    assert "unrendered script shell" in reason, (
        "the shell arm must keep ownership of a short document -- the notice arm "
        "runs below it precisely so no existing reason can change. Got: %r"
        % reason)
    assert "unrendered-page notice" not in reason


def test_the_signature_inside_substantial_content_is_admitted():
    """ROUND 2, FINDING 2 -- phrase presence is not document identity.

    The reviewer's counterexample is a code host RENDERING this project's own
    test file: its visible source carries all three phrases. Because
    `is_real_content` is shared across every tier, a false rejection there is
    TERMINAL for the cascade rather than a cheap climb to the next rung, so this
    is the expensive direction and the bound exists for it.
    """
    quoted = (
        "In this post I want to walk through an interstitial that several "
        "academic publishers serve to plain HTTP clients. The page reads: "
        "JavaScript is disabled in your browser. Please enable JavaScript to "
        "proceed. A required part of this site could not load. "
    ) + (
        "What makes it interesting is that the notice is served with a 200 "
        "status, so a naive fetcher records a success and caches the wrong "
        "bytes. The remainder of this article walks through how we detect it "
        "and what the detection deliberately does not cover, including the "
        "case you are reading right now. "
    ) * 3
    assert len([p for p in extract._UNRENDERED_NOTICE
                if p in quoted.lower()]) >= extract._UNRENDERED_NOTICE_MIN, (
        "precondition: this control must really carry the whole signature, or "
        "it is green because it has nothing to match")
    assert len(quoted) > extract.DEMAND_TEXT_MAX, (
        "precondition: and it must be past the bound, which is what saves it")
    assert extract._unrendered_notice_reason(quoted) == ""
    ok, _ = extract.is_real_content(quoted, _wrap(quoted), 200)
    assert ok is True, "a real article quoting the notice must still be returned"


def test_the_observed_notice_stays_transient():
    """ROUND 2, FINDING 1 -- the measured half of the promotion question.

    Rejecting sends the document to `classify_failure`, which scans
    `_CLIENT_DEMANDS` UNCAPPED via `whole_body_demands=True`. For THIS notice
    that scan matches nothing and the class is `other`, which is transient, so no
    domain is promoted off tier 0.

    The claim is deliberately narrow. A page carrying two notice phrases AND a
    client-demand phrase classifies `cloudflare_challenge`, which is persistent.
    THAT IS A FALSE PROMOTION, NOT CORRECT BEHAVIOUR: nothing here separates a
    served challenge from an article quoting one, so a real page can walk its
    domain off tier 0. An earlier version of this docstring called it correct,
    and an earlier comment claimed categorically that no promotion could follow.
    Both were false. The residual is pinned by
    `test_a_quoted_article_with_a_demand_phrase_is_a_known_false_promotion`.
    """
    import state
    fc = extract.classify_failure(200, _wrap(OBSERVED_NOTICE), None,
                                  extracted=OBSERVED_NOTICE)
    assert fc == "other", fc
    assert fc not in state.PERSISTENT_FAILURE_CLASSES, (
        "the observed notice must not carry a promoting class")
    assert not [d for d in extract._CLIENT_DEMANDS
                if d in OBSERVED_NOTICE.lower()], (
        "if the notice ever matches a client-demand literal this test's premise "
        "is gone and the class will change")


def test_a_notice_padded_past_the_bound_is_NOT_caught():
    """AN HONEST NEGATIVE, and the cost of round 2's bound.

    Padding the notice past DEMAND_TEXT_MAX puts it out of the arm's reach. That
    is the price of not rejecting a real article that quotes it, and the trade is
    deliberate: a false rejection is terminal across every tier, while this miss
    leaves the pre-existing behaviour exactly as it was.
    """
    padded = OBSERVED_NOTICE + " " + ("Related articles and further reading. " * 30)
    assert len(padded) > extract.DEMAND_TEXT_MAX
    assert extract._unrendered_notice_reason(padded) == ""


def test_a_quoted_article_with_a_demand_phrase_is_a_known_false_promotion():
    """ROUND 3, FINDING 6 -- the residual is a FALSE promotion, not a correct one.

    A 200..600 character ARTICLE that quotes the whole notice signature AND a
    `_CLIENT_DEMANDS` phrase is rejected by this arm, and `classify_failure` then
    scans `_CLIENT_DEMANDS` UNCAPPED via `whole_body_demands=True` and returns
    `cloudflare_challenge`, which is PERSISTENT. So a real page can walk its whole
    domain off tier 0.

    An earlier comment asserted such a page must be a genuine challenge. It need
    not be, and this test exists so the residual is a measured fact in the suite
    rather than a sentence in a comment. It asserts the CURRENT behaviour; if a
    later change closes it, this test fails and the residual is retired
    deliberately.
    """
    import state
    article = (
        "JavaScript is disabled in your browser. Please enable JavaScript to "
        "proceed. A required part of this site could not load. Those three "
        "sentences are what several publishers serve instead of the paper, and "
        "a fetcher that stores them has stored the wrong bytes. You have been "
        "blocked is another such string."
    )
    assert extract.SHELL_TEXT_MAX < len(article) <= extract.DEMAND_TEXT_MAX, (
        "precondition: the article must sit inside the arm's operating band")
    assert extract._unrendered_notice_reason(article) != "", (
        "precondition: the arm rejects it -- that is the false positive")
    assert [d for d in extract._CLIENT_DEMANDS if d in article.lower()], (
        "precondition: it must also carry a client-demand phrase")
    fc = extract.classify_failure(200, _wrap(article), None, extracted=article)
    assert fc in state.PERSISTENT_FAILURE_CLASSES, (
        "THE RESIDUAL: a quoted article is classified persistent (%r), which "
        "promotes the domain. If this ever stops being true the residual has "
        "been closed -- update the comment above `_UNRENDERED_NOTICE`." % fc)


def test_the_band_includes_exactly_SHELL_TEXT_MAX():
    """ROUND 3, FINDING 1 -- the comment said 200..600 and the code does 200..600.

    The shell block is guarded by `len(text) < SHELL_TEXT_MAX`, a STRICT `<`, so
    at exactly 200 characters that block is skipped and this arm is the only one
    left. The CODE was right; the sentence describing it was wrong by one, and a
    band understated by one character understates the false-rejection surface the
    deploy decision rests on.
    """
    sig = ("javascript is disabled in your browser. "
           "please enable javascript to proceed. "
           "a required part of this site could not load. ")
    at_200 = (sig + "x" * 400)[:extract.SHELL_TEXT_MAX]
    assert len(at_200) == extract.SHELL_TEXT_MAX
    assert len([p for p in extract._UNRENDERED_NOTICE
                if p in at_200.lower()]) == len(extract._UNRENDERED_NOTICE), (
        "precondition: the truncation must keep the whole signature")
    assert extract._unrendered_notice_reason(at_200) != "", (
        "at exactly SHELL_TEXT_MAX the shell block is skipped, so this arm must "
        "cover it -- the band starts AT SHELL_TEXT_MAX, not one above it")
    just_below = (sig + "x" * 400)[:extract.SHELL_TEXT_MAX - 1]
    assert extract._unrendered_notice_reason(just_below) != "", (
        "the predicate itself has no lower bound; below 200 it is PLACEMENT that "
        "stops it running, because the shell arms return first")
    ok, reason = extract.is_real_content(just_below, _wrap(just_below), 200)
    assert ok is False and "unrendered script shell" in reason, (
        "and below the bound the shell arm must still own the reason: %r" % reason)
