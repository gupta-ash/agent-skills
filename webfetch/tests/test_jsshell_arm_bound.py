"""Jsshell arm bound behavioral regression tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extract

# A 400 is the cheapest honest way into the arm with real extracted text: it is
# not in {401,402,403,451}, not {404,410}, not 429, not 5xx, so it falls through
# every earlier arm, and `is_real_content` has already rejected it on the status.
FALLTHROUGH_STATUS = 400

PROSE = ("We spent three weeks reducing the tail latency of the ingest path. "
         "The first change was to stop re-parsing the manifest on every batch. "
         "The second moved the checksum off the request thread. ") * 3

# The headline false positive: a real article that merely WARNS about JavaScript.
# Nothing challenge-shaped anywhere in it.
ARTICLE_WITH_NOSCRIPT = (
    "<html><head><title>How we tuned our ingest pipeline</title></head><body>"
    "<noscript>You need to enable JavaScript to run this app.</noscript>"
    f"<article><p>{PROSE}</p></article>"
    "</body></html>"
)

# A genuine unrendered shell: an empty mount point, a deferred bundle, and only
# the chrome text a shell shows before its JavaScript runs. Deliberately ABOVE
# the 50-char near-empty floor -- below it, `is_real_content` rejects on
# emptiness and never reaches the marker arm, which would make the
# two-predicates test below agree for the wrong reason.
GENUINE_SHELL = (
    '<html><head><title>Dashboard</title>'
    '<script defer src="/static/js/main.8f7c2b1a.js"></script></head><body>'
    '<noscript>You need to enable JavaScript to run this app.</noscript>'
    '<div id="root"></div>'
    '<p>Loading the console, this usually takes a moment on first visit.</p>'
    '</body></html>'
)
SHELL_TEXT = ("Dashboard You need to enable JavaScript to run this app. "
              "Loading the console.")

# The marker exists ONLY as an attribute value. A reader is shown the article and
# never these bytes. Extracted text is kept SHORT on purpose so the bound cannot
# be what saves it -- this arm isolates the haystack conjunct.
MARKER_ONLY_IN_ATTRIBUTE = (
    "<html><head><title>Notes</title></head><body>"
    '<p title="enable javascript">Short note.</p>'
    "</body></html>"
)
SHORT_TEXT = "Short note."


def _classify(doc, text, status=FALLTHROUGH_STATUS, **kw):
    """Mirror the tier call shape: `tiers.py:805` passes `extracted=md`."""
    return extract.classify_failure(status, doc, None, extracted=text, **kw)


# ---- direction 1: the false positive is fixed --------------------------------

def test_a_content_rich_article_with_a_noscript_warning_is_not_a_shell():
    """THE DEFECT ITSELF. 569 extracted characters of article prose beside a
    routine `<noscript>` warning, and no challenge markup of any kind.

    Before: `js_only_spa`, PERSISTENT, three cascades walk the DOMAIN off tier 0.
    After: `other`, TRANSIENT, the domain keeps tier 0.

    MUTATION B kills this. MUTATION H does not -- the marker really is in the
    reader's text here (a `<noscript>` body IS shown when scripting is off), so
    only the bound can save this document. That is the point of pairing it with
    the attribute test below.
    """
    assert len(PROSE) > extract.SHELL_TEXT_MAX, "precondition: prose exceeds the bound"
    assert _classify(ARTICLE_WITH_NOSCRIPT, PROSE) == "other"


def test_the_bound_is_the_extracted_text_not_the_raw_body():
    """WHICH QUANTITY, stated as a test rather than as a comment.

    The same document classified twice, differing only in what the caller says it
    extracted. The body is byte-identical in both calls, so anything reading the
    body cannot produce this split.
    """
    assert _classify(ARTICLE_WITH_NOSCRIPT, PROSE) == "other"
    assert _classify(ARTICLE_WITH_NOSCRIPT, "tiny") == "js_only_spa"


# ---- direction 2: the true positive is kept ----------------------------------

def test_a_genuine_unrendered_shell_is_still_a_shell():
    """A genuine unrendered shell is still a shell."""
    assert len(SHELL_TEXT) < extract.SHELL_TEXT_MAX
    assert _classify(GENUINE_SHELL, SHELL_TEXT) == "js_only_spa"


# ---- the haystack conjunct, isolated -----------------------------------------

def test_a_marker_only_in_a_tag_attribute_is_not_a_shell():
    """THE HALF THE BACKLOG ENTRY NEVER NAMED, and it needs the short-text
    fixture to mean anything.

    The marker is in a `title=` attribute and nowhere else. Extracted text is 11
    characters, far UNDER the bound -- so the bound cannot rescue this document
    and only the haystack change can. Before, this was `js_only_spa` and the page
    was walked toward the browser tiers on evidence no reader was ever shown.

    MUTATION H kills this. MUTATION B does not.
    """
    assert len(SHORT_TEXT) < extract.SHELL_TEXT_MAX, (
        "precondition: the bound is NOT what makes this pass")
    assert _classify(MARKER_ONLY_IN_ATTRIBUTE, SHORT_TEXT) != "js_only_spa"


# ---- the None policy ---------------------------------------------------------

def test_no_extracted_text_keeps_the_arm_firing():
    """FAIL-CLOSED, and the precedent is four lines above in the same function:
    absence of evidence is not evidence of readability, so we do not demote.

    Exactly one production caller is governed by this branch -- `tiers.py:902`,
    which hands over a truncated 2 KiB error body off an `HTTPError` with no
    extraction available on that path.

    why-not fall back to measuring the stripped markup when `extracted` is None:
    MEASURED, a genuine 279-byte shell's stripped markup runs 223 characters
    against a 200 bound, so the fallback loses a true positive to buy a
    convenient answer. It would turn the test above red.
    """
    assert extract.classify_failure(
        FALLTHROUGH_STATUS, GENUINE_SHELL, None) == "js_only_spa"
    # And the content-rich page too: no evidence means no demotion, even here.
    assert extract.classify_failure(
        FALLTHROUGH_STATUS, ARTICLE_WITH_NOSCRIPT, None) == "js_only_spa"


# ---- what must not have moved ------------------------------------------------

def test_a_marker_only_inside_a_script_is_still_not_a_shell():
    """PRE-EXISTING behaviour, pinned again here because the haystack changed
    underneath it. `_strip_script_and_style` already removed script bodies; this
    asserts the new tag-stripping step did not somehow put them back.
    """
    doc = ("<html><head><title>App</title></head><body>"
           '<script>var msg = "enable javascript";</script>'
           f"<article><p>{PROSE}</p></article></body></html>")
    assert _classify(doc, PROSE) != "js_only_spa"


def test_the_two_predicates_now_agree_on_one_document():
    """THE REASON THE BOUND IS THE SIBLING'S BOUND rather than a number of my
    own: `is_real_content` and `classify_failure` judge the same two markers, and
    they used to disagree about the same document by accident rather than design.

    Asserted in BOTH directions, because the agreement is only meaningful if the
    predicates can also agree the other way. A vacuous version of this test --
    checking a substring of a reason string that is empty on success -- would
    pass without either predicate being consulted.
    """
    # Content-rich: is_real_content accepts it outright, and the classifier no
    # longer calls it a shell either.
    ok_rich, reason_rich = extract.is_real_content(PROSE, ARTICLE_WITH_NOSCRIPT, 200)
    assert ok_rich is True, reason_rich
    assert _classify(ARTICLE_WITH_NOSCRIPT, PROSE) != "js_only_spa"

    # Genuine shell: is_real_content rejects it AS a shell, and the classifier
    # names the same thing. This is the arm that makes the pair non-vacuous.
    ok_shell, reason_shell = extract.is_real_content(SHELL_TEXT, GENUINE_SHELL, 200)
    assert ok_shell is False
    assert "unrendered script shell" in reason_shell, reason_shell
    assert _classify(GENUINE_SHELL, SHELL_TEXT) == "js_only_spa"


# ---- the haystack, and two residuals that are now CLOSED --------------------
#
# Round 1 of review found both of these and I pinned them as residuals, on the
# ground that repairing them meant repairing the SHARED `_visible_text`. Round 2
# answered that objection with one word -- CALLER-LOCAL -- so the reason to
# decline was gone and `_reader_text_for_markers` was built. The pins are
# REPLACED rather than edited, which is what their own docstrings demanded.
#
# One residual REMAINS below, and its pin is untouched.

def test_a_quoted_angle_bracket_in_an_attribute_does_not_leak():
    """CLOSED, round 2. `<[^>]*>` ends a tag at the FIRST `>`, including one
    inside a quoted attribute value, so the rest of that attribute survived as if
    it were text. Measured before the fix: this document classified
    `js_only_spa` off a marker no reader is ever shown.

    `_strip_tags_respecting_quotes` tracks quote state, so a `>` inside a value
    is not a delimiter. That is a TOKENISER question with one rule -- not the
    tree-constructor question `architecture.md` records as refuted here.

    THE CONTROL is the second assertion: the same document without the `>` must
    also be `other`. Without it, a helper that simply dropped all attributes
    would pass the first assertion while proving nothing about quote handling.
    """
    leak = ('<html><head><title>N</title></head><body>'
            '<p data-x="a > enable javascript">Short note.</p></body></html>')
    clean = ('<html><head><title>N</title></head><body>'
             '<p data-x="a enable javascript">Short note.</p></body></html>')
    assert _classify(leak, "Short note.") == "other"
    assert _classify(clean, "Short note.") == "other"
    assert "enable javascript" not in extract._reader_text_for_markers(
        extract._strip_script_and_style(leak)), "the attribute must not reach the haystack"


def test_an_entity_encoded_marker_is_seen():
    """CLOSED, round 2, and it is the OPPOSITE direction to the test above --
    this one used to MISS a real shell rather than invent one."""
    doc = ('<html><head><title>N</title></head><body>'
           '<p>enable&#32;javascript</p></body></html>')
    assert _classify(doc, "enable javascript") == "js_only_spa"


def test_the_shared_helper_was_not_repaired_for_this_callers_benefit():
    """`_visible_text` keeps its behaviour and its other caller. Round 1 declined
    the repair for this reason and round 2's CALLER-LOCAL remedy is what made it
    buildable -- so the boundary is asserted rather than trusted.
    """
    stripped = extract._strip_script_and_style(
        '<p data-x="a > enable javascript">Short note.</p>')
    assert "enable javascript" in extract._visible_text(stripped), (
        "the shared helper must be UNCHANGED -- it still leaks, by design")
    assert "enable javascript" not in extract._reader_text_for_markers(stripped), (
        "and the caller-local one must not")


def test_RESIDUAL_a_padded_genuine_shell_is_no_longer_detected():
    """THE ACCEPTED COST OF THE BOUND, and review round 1's must-fix high.

    A genuine unrendered shell padded past the bound with navigation furniture
    stops being classified `js_only_spa`.

    The recommended remedy -- admit structural shell evidence regardless of
    length -- is NOT BUILDABLE here, and that is measured rather than argued:
    the file's only structural signal is `_script_byte_share`, which reads 0.168
    on this document against 0.000 on a real article, both far under the 0.90
    threshold. Separating them needs a constant fitted to two constructed
    documents, or a new app-shell detector nothing consumes.

    WHAT IS ACCEPTED IS LATENCY, NOT CONTENT. This document still fails at tier 0
    and still climbs every rung inside the same fetch -- the cascade consults no
    failure class -- it merely stops promoting its domain as quickly. The bound
    removes the opposite and worse error: a content-rich page wrongly promoted
    loses tier 0, the only tier that reads a PDF or returns JSON verbatim.
    """
    nav = ("Home Products Pricing Docs Support Careers Blog Status Login "
           "Sign up Contact ")
    pad = (nav * 5)[:358]
    shell = ('<html><head><title>Dashboard</title>'
             '<script defer src="/static/js/main.8f7c2b1a.js"></script>'
             '<script src="/static/js/vendor.a91.js"></script></head><body>'
             '<noscript>You need to enable JavaScript to run this app.</noscript>'
             '<div id="root"></div><nav>' + pad + '</nav></body></html>')
    assert len(pad) > extract.SHELL_TEXT_MAX, "precondition: padded past the bound"
    assert extract._script_byte_share(shell) < extract.SHELL_SCRIPT_SHARE, (
        "precondition: the structural signal does NOT catch it either")
    assert _classify(shell, pad) == "other"


def test_a_literal_less_than_in_visible_text_does_not_eat_the_marker():
    """A REGRESSION THIS BRANCH INTRODUCED, found by review round 3 and measured
    against `main` before it was called mine.

    My first scanner treated every `<` as a tag opener, so `1 < 2. You need to
    enable JavaScript` was consumed from the `<` to the next `>` and the haystack
    became `N 1`. MEASURED: `main` classified that document `js_only_spa`, the
    tip classified it `other`. That is a marker a reader plainly sees, lost.

    HTML's tag-open state moves to a tag only for an ASCII letter, `/`, `!` or
    `?`, and emits `<` as text otherwise. One predicate, no element list.

    The entity-encoded form is the CONTROL: it never went through the tag path at
    all, so it must be unaffected in both directions.
    """
    raw = ('<html><head><title>N</title></head><body>'
           '<p>1 < 2. You need to enable JavaScript</p></body></html>')
    entity = ('<html><head><title>N</title></head><body>'
              '<p>1 &lt; 2. You need to enable JavaScript</p></body></html>')
    assert _classify(raw, "1 < 2. enable") == "js_only_spa"
    assert _classify(entity, "1 < 2. enable") == "js_only_spa"
    assert "enable javascript" in extract._reader_text_for_markers(
        extract._strip_script_and_style(raw)).lower()


def test_a_trailing_less_than_is_text_not_an_unclosed_tag():
    """The boundary of the same rule: a `<` as the final character has no next
    character to classify it, and must not swallow the document behind it."""
    doc = ('<html><head><title>N</title></head><body>'
           '<p>You need to enable JavaScript</p></body>2 <')
    assert _classify(doc, "short") == "js_only_spa"


def test_a_real_tag_is_still_removed():
    """THE CONTROL FOR BOTH RULES ABOVE. If the scanner became too permissive and
    stopped removing tags at all, the two tests above would pass while the
    attribute-leak test regressed. This asserts the ordinary case directly.
    """
    assert extract._reader_text_for_markers(
        '<div class="x"><span>hello</span></div>') == "hello"


def test_a_quote_inside_an_UNQUOTED_attribute_does_not_run_to_the_end():
    """Review round 4. HTML enters an attribute value's quoted state only
    from the before-attribute-value state -- after `=`. My scanner entered it
    on ANY quote inside a tag, so `<p data-x=a">` opened a quoted state that
    never closed and ate the rest of the document, losing a marker a reader
    plainly sees.

    THE CONTROL is the second assertion: a quote that DOES follow `=` must
    still shield a `>` inside the value. Without it, never entering quote mode
    at all would pass the first assertion while silently undoing round 2.
    """
    unquoted = ('<html><body><p data-x=a">You need to enable JavaScript'
                '</p></body></html>')
    quoted = ('<html><body><p data-x="a > enable javascript">Short note.'
              '</p></body></html>')
    assert _classify(unquoted, "short") == "js_only_spa"
    assert _classify(quoted, "Short note.") == "other"


def test_a_non_ascii_letter_does_not_open_a_tag():
    """Review round 4. The rule is ASCII letters and `str.isalpha()` accepts
    every Unicode letter, so a tag-looking sequence opened by an accented letter
    was consumed as an element and its marker lost. A browser shows it as text.

    THE CONTROL is the second assertion: an ASCII letter must STILL open a tag,
    or the attribute-leak fix from round 2 is undone.
    """
    non_ascii = ('<html><body><' + chr(233) + ' You need to enable JavaScript>'
                 '</body></html>')
    ascii_tag = ('<html><body><p title="enable javascript">Short note.'
                 '</p></body></html>')
    assert _classify(non_ascii, "short") == "js_only_spa"
    assert _classify(ascii_tag, "Short note.") == "other"
