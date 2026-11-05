"""An authentication refusal served at HTTP 200 must not be reported as content.

M-AUTH-200-FALSE-SUCCESS. `slack.com/api/conversations.list` answers an
unauthenticated request with HTTP **200** and 33 bytes of
`{"ok":false,"error":"not_authed"}`. Tier 0 returns a JSON body verbatim, and the
cascade computes an escalation verdict only on a FAILED tier, so the refusal came
back to the caller inside a success envelope: `ok=True`, `extractor=verbatim`,
the refusal as the page content. Nothing downstream can tell that is wrong, which
is what makes a false success worse than a failure.

WHY THE CONTROLS OUTNUMBER THE POSITIVES HERE. This gate can only ever turn a
working fetch into a failed one, fleet-wide, on every JSON fetch. A corpus of 139
real API bodies (2026-09-04) measured what the two obvious predicates would cost:
reusing `_json_unauth` costs 5 false positives on ordinary content, and widening
it with the credential vocabulary costs 24 more -- including every conforming
OAuth token response, since RFC 6749 S5.1 REQUIRES `access_token` and
`token_type: Bearer`. Both classes are pinned below so a later widening breaks a
test instead of the fleet.

Hermetic: a loopback http.server serving fixed bytes and headers. No browser, no
external network, no shared state.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import http.server
import json
import threading

import pytest

import extract
import state
import tiers


# ---- harness -----------------------------------------------------------------

@pytest.fixture()
def serve():
    """Yields serve(body: bytes, content_type: str) -> url, answering 200."""
    box = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body, ct = box["body"], box["ct"]
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def configure(body, content_type="application/json"):
        box.update(body=body, ct=content_type)
        return f"http://127.0.0.1:{port}/"

    yield configure
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def blocklist_db(tmp_path, monkeypatch):
    """A throwaway state.db. Never the real one: the promotion tests below WRITE."""
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    conn = state.open_db()
    yield conn
    conn.close()


def fetch(serve, body, content_type="application/json"):
    """A REAL tier-0 fetch over a real socket.

    Deliberately not a direct call to `_process_http_response`: this project has
    twice shipped a predicate that was green against inputs no transport can
    produce. Going through the socket means the status, the headers and the
    decode are the transport's, not the test's.
    """
    return tiers.tier_http(serve(body, content_type), allow_private=True)


# ---- the defect ---------------------------------------------------------------

SLACK = b'{"ok":false,"error":"not_authed"}'


def test_the_canonical_33_byte_slack_refusal_is_not_a_success(serve):
    assert len(SLACK) == 33, "the body the spec quotes, byte for byte"
    r = fetch(serve, SLACK)
    assert r.ok is False
    assert r.status == 200, "the origin really did say 200; we refuse the CONTENT"


def test_the_refusal_is_not_handed_back_as_content(serve):
    """The whole point: an agent must not be able to read the refusal as a page."""
    r = fetch(serve, SLACK)
    assert r.content == ""


def test_the_error_names_the_status_so_the_diagnosis_is_not_misleading(serve):
    r = fetch(serve, SLACK)
    assert "authentication refusal" in r.error
    assert "200" in r.error, "a reader must not conclude the origin sent an error status"


def test_graphql_reports_auth_at_200_by_construction_and_is_caught(serve):
    """GraphQL ALWAYS answers 200, so it is the ecosystem most characteristic of
    this defect. The code lives in `extensions.code`, one level down."""
    body = json.dumps({"data": None, "errors": [
        {"message": "Unauthorized", "extensions": {"code": "UNAUTHENTICATED"}}]}).encode()
    assert fetch(serve, body).ok is False


def test_a_nested_error_object_is_read(serve):
    """Azure, MediaWiki and JSON-RPC all put the code inside `error`."""
    body = b'{"error":{"code":"mustbeloggedin","info":"You must be logged in."}}'
    assert fetch(serve, body).ok is False


def test_an_array_root_is_read(serve):
    """Salesforce answers with a bare array and names the SESSION -- the one
    credential a browser actually holds."""
    body = b'[{"message":"Session expired or invalid","errorCode":"INVALID_SESSION_ID"}]'
    assert fetch(serve, body).ok is False


# ---- the controls: what must STILL succeed ------------------------------------

def test_an_ordinary_json_body_still_succeeds(serve):
    body = b'{"ok":true,"channels":[{"id":"C1","name":"general"}]}'
    r = fetch(serve, body)
    assert r.ok is True
    assert r.content == body.decode()


def test_an_article_about_a_401_is_content_not_a_refusal(serve):
    """MEASURED false positive of `_json_unauth`, and the reason no prose regex
    runs in this gate: a dev.to article whose TITLE names the error. It is the
    exact page an agent fetches while debugging an auth failure."""
    body = json.dumps({
        "type_of": "article",
        "title": "Handling 401 Unauthorized in a Rails API",
        "body_markdown": "When your API returns 401 Unauthorized, the first thing "
                         "to check is the Authorization header ...",
    }).encode()
    assert fetch(serve, body).ok is True


def test_an_oembed_response_is_content(serve):
    """oEmbed MANDATES a top-level `title`, so a prose arm here would be a
    whole-PROTOCOL false positive rather than one vendor's."""
    body = json.dumps({"version": "1.0", "type": "video",
                       "title": "Debugging 401 Unauthorized the hard way",
                       "html": "<iframe src='...'></iframe>"}).encode()
    assert fetch(serve, body).ok is True


def test_a_successful_oauth_token_response_is_content(serve):
    """THE CONTROL THAT KILLED THE FIRST DESIGN. RFC 6749 S5.1 requires
    `access_token` and `token_type`, so a credential-vocabulary predicate refuses
    every conforming OAuth success -- the most valuable document those endpoints
    produce. If this test ever fails, the gate has been widened into that."""
    body = json.dumps({"access_token": "synthetic-access", "expires_in": 3599,
                       "token_type": "Bearer", "refresh_token": "synthetic-refresh"}).encode()
    assert fetch(serve, body).ok is True


def test_an_openapi_document_is_content(serve):
    """A spec for an authenticated API necessarily declares its own auth, and a
    machine-readable spec is exactly what an agent fetches."""
    body = json.dumps({"openapi": "3.1.0", "info": {"title": "Acme"},
                       "components": {"securitySchemes": {"bearerAuth": {
                           "type": "http", "scheme": "bearer",
                           "bearerFormat": "JWT"}}}}).encode()
    assert fetch(serve, body).ok is True


def test_this_is_not_a_general_error_detector(serve):
    """A rate limit is a real error and NOT an authentication refusal. Refusing
    it would widen the change past what was asked for."""
    assert fetch(serve, b'{"ok":false,"error":"ratelimited"}').ok is True


def test_a_partial_resolve_keeps_its_data(serve):
    """GraphQL answers 200 with BOTH `data` and `errors` when one field was
    refused and the rest succeeded. That is a partial result, and discarding it
    loses content the caller asked for and the server already sent.

    THE CODE HERE MUST BE ONE THE GATE ACTUALLY RECOGNISES. The first version of
    this test used FORBIDDEN, which is not in `_AUTH_REFUSAL_CODES` at all, so
    it passed without exercising the partial-result rule for one instant -- a
    test green for a reason other than the one it names. Cross-family review
    caught it, 2026-09-04."""
    assert "unauthenticated" in extract._AUTH_REFUSAL_CODES, (
        "the whole point of this test is that the code IS recognised")
    body = json.dumps({
        "data": {"publicStats": {"repositoryCount": 42}},
        "errors": [{"message": "Must be authenticated",
                    "extensions": {"code": "UNAUTHENTICATED"}}]}).encode()
    r = fetch(serve, body)
    assert r.ok is True
    assert "repositoryCount" in r.content


def test_a_null_only_data_block_is_still_a_total_refusal(serve):
    """The other side of the same rule: GraphQL emits the key precisely because
    the field failed, so `{"viewer": null}` is not resolved data. Without this,
    the partial-result rule would wave through every GraphQL refusal."""
    body = json.dumps({
        "data": {"viewer": None},
        "errors": [{"message": "x", "extensions": {"code": "UNAUTHENTICATED"}}]}).encode()
    assert fetch(serve, body).ok is False


def test_a_null_data_block_is_still_a_total_refusal(serve):
    body = json.dumps({
        "data": None,
        "errors": [{"message": "x", "extensions": {"code": "UNAUTHENTICATED"}}]}).encode()
    assert fetch(serve, body).ok is False


def test_malformed_json_passes_through(serve):
    """The parse guard: a body that is not JSON at all is not a refusal."""
    assert fetch(serve, b'{"ok":false,"error":', "application/json").ok is True


def test_an_empty_body_still_reports_as_empty_not_as_a_refusal(serve):
    """Regression on the branch this change edits: the pre-existing empty-body
    arm must keep its own diagnosis rather than inheriting the new one."""
    r = fetch(serve, b"   ")
    assert r.ok is False
    assert "empty" in r.error
    assert "authentication" not in r.error


# ---- the safety properties ----------------------------------------------------

def test_the_refusal_cannot_promote_the_domain_toward_a_browser(serve, blocklist_db):
    """C-3/C-4. A refused fetch records a failure, and a PERSISTENT class walks
    the domain up the tiers on a streak, so a repeatedly-unauthenticated API
    would eventually open a window.

    DRIVEN THROUGH `record_failure` RATHER THAN ASSERTED ON A PROXY. The first
    version of this test checked membership in `TRANSIENT_FAILURE_CLASSES` --
    but promotion consults `PERSISTENT_FAILURE_CLASSES`, and the two are
    INDEPENDENT frozensets, not complements. Adding this class to the persistent
    set would have promoted the domain while leaving that assertion green.
    Cross-family review caught it, 2026-09-04."""
    r = fetch(serve, SLACK)
    assert r.failure_class not in state.PERSISTENT_FAILURE_CLASSES

    # And the behaviour itself, driven past the threshold that would promote.
    bl = state.Blocklist(blocklist_db)
    for _ in range(state.PROMOTE_THRESHOLD + 2):
        assert bl.record_failure("slack.com", 0, r.failure_class) is None
    assert bl.get("slack.com")["tier_idx"] == 0, "must still start at http"


def test_the_promotion_check_can_actually_fail(blocklist_db):
    """The control for the test above. A genuinely persistent class DOES promote
    on the same path, so the assertion there is not vacuous."""
    bl = state.Blocklist(blocklist_db)
    outcome = None
    for _ in range(state.PROMOTE_THRESHOLD):
        outcome = bl.record_failure("walled.example", 0, "cloudflare_challenge")
    assert outcome == "promoted"
    assert bl.get("walled.example")["tier_idx"] == 1


def test_this_rung_does_not_itself_ask_the_user_for_the_cdp_browser(serve):
    """The verdict computed on the refused result decides what the cascade does
    next -- and a CONTINUE keeps the CDP ASK shut, not the browser tiers.

    REWRITTEN 2026-09-04, and the previous assertion was the rejected framing.
    It read `_gate_allows("headed", verdict) is False` and called the tier
    "windowed", on the rule that a verdict is a LICENCE to put a window on the
    owner's screen. It is not: the owner ruled that the headed rung is an
    ordinary rung of the cascade, in it and enabled, and that the fetch
    succeeding outranks where a window lands. So a CONTINUE admits headed, and
    asserting otherwise now pins the wrong design.

    WHAT THIS RUNG IS ACTUALLY ABOUT SURVIVES, and it is the half worth
    keeping: a 200 carrying an auth refusal must NOT be diagnosed NEEDS_LOGIN
    off the JSON alone. A NEEDS_LOGIN is the one verdict that stops the cascade
    dead and asks the owner to start his CDP browser, and paging a person for
    an API error nobody can sign into is exactly the disruption ranked second.
    `cdp` staying shut is therefore still asserted -- it is shut here because
    the verdict is not NEEDS_LOGIN and headed has not run yet, which is the
    condition that survives from the old gate."""
    import fetch as fetch_mod
    r = fetch(serve, SLACK)
    verdict = extract.escalation_verdict(
        tier="http", status=r.status, headers={"Content-Type": "application/json"},
        set_cookie=[], hops=[], body=SLACK.decode(),
        content_type="application/json",
        requested_url="https://slack.com/api/conversations.list",
        final_url="https://slack.com/api/conversations.list")
    assert verdict == "CONTINUE"
    assert fetch_mod._gate_allows("headed", verdict) is True, (
        "headed is an ordinary rung and a CONTINUE enters it")
    assert fetch_mod._gate_allows("cdp", verdict, headed_attempted=False) is False, (
        "an API auth refusal must not page the owner for his logged-in browser")


# ---- stated residuals, pinned so they are decisions and not surprises ---------

def test_a_plain_text_refusal_is_a_known_miss(serve):
    """`text/plain` "Unauthorized" takes the same verbatim path and no JSON
    predicate can reach it. Pinned as a KNOWN MISS: if someone later makes this
    pass, that is a deliberate widening and this test should be rewritten, not
    deleted quietly."""
    assert fetch(serve, b"Unauthorized", "text/plain").ok is True


def test_a_prose_only_refusal_is_a_known_miss(serve):
    """No prose arm, by measured choice. `{"message": "Could not authenticate
    you."}` is a real refusal that this gate does not catch, and the four
    false positives a prose arm brings are why."""
    assert fetch(serve, b'{"message":"Could not authenticate you."}').ok is True


def test_wordpress_error_metadata_is_not_mistaken_for_resolved_data(serve):
    """REGRESSION, and it was a CRITICAL. `data` is not a GraphQL-exclusive key:
    WordPress REST -- the most widely deployed REST API there is -- puts error
    METADATA there. The first version of the partial-result exemption read that
    as resolved content and waved the whole refusal through, silently disabling
    the gate for every WordPress site. Caught by cross-family review round 2."""
    body = (b'{"code":"rest_not_logged_in",'
            b'"message":"You are not currently logged in.",'
            b'"data":{"status":401}}')
    assert "rest_not_logged_in" in extract._AUTH_REFUSAL_CODES
    assert fetch(serve, body).ok is False


def test_the_partial_exemption_needs_a_graphql_envelope(serve):
    """The rule behind that regression, stated directly: no `errors` LIST means
    no partial-result exemption, whatever `data` holds."""
    assert extract._has_resolved_data({"data": {"status": 401}}) is False
    assert extract._has_resolved_data(
        {"data": {"a": 1}, "errors": [{"message": "x"}]}) is True


def test_an_unimplicated_field_is_resolved_even_when_null(serve):
    """A field the errors do not name is resolved even if its value is
    legitimately null. Without reading `errors[].path` this partial result was
    discarded as a total refusal -- the expensive direction."""
    body = json.dumps({
        "data": {"failed": None, "public": None},
        "errors": [{"message": "Must be authenticated", "path": ["failed"],
                    "extensions": {"code": "UNAUTHENTICATED"}}]}).encode()
    assert fetch(serve, body).ok is True


def test_a_path_naming_every_field_is_still_a_total_refusal(serve):
    """The control for the test above: when the errors implicate every field
    returned, nothing was resolved and it is a refusal after all."""
    body = json.dumps({
        "data": {"viewer": None},
        "errors": [{"message": "Must be authenticated", "path": ["viewer"],
                    "extensions": {"code": "UNAUTHENTICATED"}}]}).encode()
    assert fetch(serve, body).ok is False


def test_a_pathless_error_beside_a_path_is_a_total_refusal(serve):
    """ROUND-3 CRITICAL. GraphQL emits `path` only for an error raised while
    resolving a field; an authentication failure raised BEFORE execution has
    none. Mixing the two made every field the pathless error covered look
    unimplicated, so a total refusal read as a partial result and came back as a
    FALSE SUCCESS -- the expensive direction.

    One error names `a`, the other names nothing and therefore covers the whole
    response. Nothing is reliably resolved, so this is a refusal."""
    body = json.dumps({
        "data": {"a": None, "b": None, "c": None},
        "errors": [
            {"path": ["a"], "extensions": {"code": "UNAUTHENTICATED"}},
            {"message": "Unauthenticated", "extensions": {"code": "UNAUTHENTICATED"}},
        ]}).encode()
    assert fetch(serve, body).ok is False


def test_all_errors_carrying_a_path_still_permits_a_partial(serve):
    """The true pair for the test above: IDENTICAL all-null data, differing only
    in whether the second error carries a path. `c` is named by neither error, so
    it is a legitimately-null resolved field and this is a partial result.

    THE DATA IS ALL-NULL ON PURPOSE. This control previously carried
    `"c": "kept"`, and a non-null value satisfies the FALLBACK -- so it passed
    with the path logic disabled and isolated nothing. Measured identical both
    ways; cross-family review round 4 caught it. With every value null the
    assertion can only hold if `complete` is true AND `implicated` is trusted."""
    body = json.dumps({
        "data": {"a": None, "b": None, "c": None},
        "errors": [
            {"path": ["a"], "extensions": {"code": "UNAUTHENTICATED"}},
            {"path": ["b"], "extensions": {"code": "UNAUTHENTICATED"}},
        ]}).encode()
    assert fetch(serve, body).ok is True


def test_a_nested_path_names_a_leaf_not_the_whole_field(serve):
    """ROUND-3 HIGH. `["user","email"]` means `user` RESOLVED and one property
    under it did not. Reading only `path[0]` implicated the whole field and threw
    away `user.name`, which is real content the server sent."""
    body = json.dumps({
        "data": {"user": {"name": "Ada", "email": None}},
        "errors": [{"path": ["user", "email"],
                    "extensions": {"code": "UNAUTHENTICATED"}}]}).encode()
    r = fetch(serve, body)
    assert r.ok is True
    assert "Ada" in r.content


def test_errormessages_is_not_walked_because_it_cannot_be(serve):
    """Jira answers `{"errorMessages":["Login Required"],"errors":{}}` -- a list
    of STRINGS. The node walk yields dicts only, so a branch for it fired on no
    real input. It was removed rather than left looking like coverage; this
    pins the resulting KNOWN MISS so its return would be a deliberate act."""
    body = b'{"errorMessages":["Login Required"],"errors":{}}'
    assert fetch(serve, body).ok is True
