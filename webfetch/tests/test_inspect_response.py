"""Shared inspection contract through the real API, CLI and cascade.

Only transports and effect boundaries are replaced. Diagnosis stays in extract;
CLI cases load a fresh entry point and extraction module, so collection order
cannot hide an eager operational import. The assembled-suite launcher must set
its disposable home/state environment before collection (docs/implementation).
"""

import builtins
import copy
import glob
import io
import json
import os
import runpy
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import extract
import fetch
import safety
import state
import tiers


URL = "https://example.com/article"
LOGIN_URL = "https://example.com/users/sign_in"
HTML = "text/html; charset=utf-8"
SHORT_APP = "<html><title>Counter</title><body><button>Count: 1</button></body></html>"
LOGIN = ("<html><head><title>Sign in</title></head><body>"
         "<form id=login><input type=password name=p><button>Sign in</button>"
         "</form></body></html>")
CAPTCHA = ("<html><title>Verify you are human</title><body>"
           '<script src="https://ct.captcha-delivery.com/c.js"></script>'
           "</body></html>")
WALL = ("<html><title>Research report</title><body><article>Preview.</article>"
        '<div role="dialog" aria-modal="true"><p>Subscribe to continue reading</p>'
        "<button>Subscribe</button></div></body></html>")
ARTICLE = ("<html><title>Research report</title><body><article>"
           + "The published research includes the complete public findings. " * 20
           + "</article></body></html>")
PUBLIC_TEXT = "Complete public findings, fixture nonce: accessible-article."
CANDIDATE = {"visible": True, "text": "Subscribe to continue reading",
             "controls": ["Subscribe"]}
BLOCKED = {"requirement": "subscription_required", "provenance": "visible_blocking_ui"}
OUTPUT_KEYS = {"verdict", "failure_class", "access_requirement", "next_action", "next_step",
               "login_url", "retry_after", "error", "error_code", "observation"}
OBSERVATION_KEYS = {"tier", "executed", "skip_reason", "verdict", "login_url",
                    "retry_after", "access_requirement"}
VERDICTS = {"CONTINUE", "CAPTCHA_CHALLENGE", "NEEDS_LOGIN", "HARD_BLOCK", "RATE_LIMITED"}
ACTIONS = {"accept", "headless", "headed", "probe_cdp", "cdp", "ask_user", "check_access", "stop"}


def _response(**changes):
    value = {"tier": "headless", "executed": True, "ok": False,
             "requested_url": URL, "final_url": URL, "body": "",
             "status": 200, "status_is_real": True, "headers": {"Content-Type": HTML}}
    value.update(changes)
    return value


def _history(tier, *, executed=True, skip_reason=None, verdict="CONTINUE",
             login_url=None, retry_after=None, access_requirement=None):
    return {"tier": tier, "executed": executed, "skip_reason": skip_reason,
            "verdict": verdict, "login_url": login_url, "retry_after": retry_after,
            "access_requirement": access_requirement}


def _inspect(response=None, **options):
    options.setdefault("headed_allowed", True)
    options.setdefault("cdp_available", None)
    return fetch.inspect_response(_response() if response is None else response, **options)


def _assert_contract(result):
    assert set(result) == OUTPUT_KEYS
    assert result["verdict"] is None or result["verdict"] in VERDICTS
    assert result["next_action"] in ACTIONS
    assert isinstance(result["next_step"], str)
    for key in ("failure_class", "access_requirement", "login_url", "retry_after", "error", "error_code"):
        assert result[key] is None or isinstance(result[key], str), key
    observation = result["observation"]
    assert observation is None or set(observation) == OBSERVATION_KEYS
    if observation is not None:
        assert observation["verdict"] is None or observation["verdict"] in VERDICTS
        assert type(observation["executed"]) is bool
        if not observation["executed"]:
            assert all(observation[key] is None for key in (
                "verdict", "login_url", "retry_after", "access_requirement"))
    return result


def _login_response(**changes):
    return _response(ok=True, body=LOGIN, **changes)


def _captcha_response(**changes):
    value = _response(body=CAPTCHA, status=403,
                      headers={"Content-Type": HTML, "X-DataDome": "protected"})
    value.update(changes)
    return value


# Strict API input/output schema, including the JSON-observation round trip.

@pytest.mark.parametrize("tier", ["http", "headless", "headed", "cdp"])
def test_minimal_response_defaults_accept_a_short_usable_app(tier):
    response = {"tier": tier, "executed": True, "ok": True,
                "requested_url": URL, "final_url": URL, "body": SHORT_APP}
    result = _assert_contract(_inspect(response))
    assert result["verdict"] == "CONTINUE"
    assert result["next_action"] == "accept"
    assert result["observation"] == _history(tier)
    assert all(result[key] is None for key in (
        "failure_class", "access_requirement", "login_url", "retry_after", "error", "error_code"))


@pytest.mark.parametrize("response", [None, [], (), "response", 1, True])
def test_api_rejects_non_object_response(response):
    with pytest.raises(ValueError):
        fetch.inspect_response(response, headed_allowed=True, cdp_available=None)


@pytest.mark.parametrize("key", ["tier", "executed", "ok", "requested_url", "final_url", "body"])
def test_api_requires_each_response_field(key):
    response = _response()
    del response[key]
    with pytest.raises(ValueError):
        _inspect(response)


@pytest.mark.parametrize("key,value", [
    ("extra", "private-input-sentinel"),
    ("tier", "copilot"), ("tier", "browser"), ("tier", 1), ("tier", None),
    ("executed", 1), ("executed", "true"), ("executed", None),
    ("ok", 0), ("ok", "false"), ("ok", None),
    ("body", None), ("body", []), ("body", b"html"), ("body", 3),
    ("status", True), ("status", 200.0), ("status", "200"),
    ("status", 99), ("status", 600),
    ("status_is_real", 1), ("status_is_real", None),
    ("headers", []), ("headers", None), ("headers", {"x": 1}), ("headers", {1: "x"}),
    ("set_cookie", "a=b"), ("set_cookie", None), ("set_cookie", [1]),
    ("hops", None), ("hops", {}), ("hops", ["302"]),
    ("skip_reason", "timeout"), ("skip_reason", 1),
])
def test_api_rejects_unknown_response_keys_and_wrong_types(key, value):
    with pytest.raises(ValueError):
        _inspect(_response(**{key: value}))


@pytest.mark.parametrize("key", ["requested_url", "final_url"])
@pytest.mark.parametrize("url", [None, 42, "", "/relative", "file:///private", "ftp://example.com/a",
                                 "javascript:alert(1)", "https://", "http:///missing-host"])
def test_api_requires_absolute_http_urls_without_resolving_them(key, url):
    with pytest.raises(ValueError):
        _inspect(_response(**{key: url}))


@pytest.mark.parametrize("hop", [
    [], [302, URL], [302, URL, LOGIN_URL, "extra"], [True, URL, LOGIN_URL],
    [302.0, URL, LOGIN_URL], [99, URL, LOGIN_URL], [600, URL, LOGIN_URL],
    [302, "/relative", LOGIN_URL], [302, "file:///private", LOGIN_URL],
    [302, URL, None], [302, URL, 2], [302, URL, ""], [302, URL, "  "],
])
def test_api_validates_every_redirect_hop(hop):
    with pytest.raises(ValueError):
        _inspect(_response(hops=[hop]))


@pytest.mark.parametrize("container", [list, tuple])
@pytest.mark.parametrize("target", [LOGIN_URL, "/users/sign_in"])
def test_valid_redirect_triples_reach_the_real_login_classifier(container, target):
    result = _inspect(_response(hops=[container((302, URL, target))]))
    assert result["verdict"] == "NEEDS_LOGIN"
    assert result["next_action"] == "probe_cdp"


@pytest.mark.parametrize("options", [
    {"headed_allowed": 1}, {"headed_allowed": None}, {"headed_allowed": "false"},
    {"cdp_available": 0}, {"cdp_available": "unknown"},
    {"max_tier": "copilot"}, {"max_tier": "browser"}, {"max_tier": None}, {"max_tier": 3},
    {"entitlement": True}, {"entitlement": None}, {"entitlement": "paid"},
    {"history": None}, {"history": {}}, {"history": "history"}, {"history": [None]},
])
def test_api_rejects_invalid_policy_options(options):
    with pytest.raises(ValueError):
        _inspect(**options)


@pytest.mark.parametrize("key", sorted(OBSERVATION_KEYS))
def test_history_requires_all_seven_observation_keys(key):
    prior = _history("http")
    del prior[key]
    with pytest.raises(ValueError):
        _inspect(history=(prior,))


@pytest.mark.parametrize("key,value", [
    ("body", "private-body"), ("set_cookie", ["private=cookie"]), ("extra", 0),
    ("tier", "copilot"), ("tier", 1), ("executed", 1), ("skip_reason", "failed"),
    ("verdict", "accept"), ("verdict", 1), ("login_url", "/users/sign_in"),
    ("login_url", "file:///private"), ("login_url", 1),
    ("retry_after", 60), ("access_requirement", "paid"), ("access_requirement", 1),
])
def test_history_rejects_extra_keys_and_invalid_values(key, value):
    prior = _history("headless")
    prior[key] = value
    with pytest.raises(ValueError):
        _inspect(history=(prior,))


@pytest.mark.parametrize("key,value", [("verdict", "CAPTCHA_CHALLENGE"),
                                        ("login_url", LOGIN_URL), ("retry_after", "60"),
                                        ("access_requirement", "subscription_required")])
def test_unexecuted_history_cannot_manufacture_diagnosis(key, value):
    prior = _history("headed", executed=False, skip_reason="browser_unavailable", verdict=None)
    prior[key] = value
    with pytest.raises(ValueError):
        _inspect(history=(prior,))


@pytest.mark.parametrize("reason", ["headed_disabled", "browser_unavailable", "ceiling", "policy_disabled"])
def test_skip_reason_is_incompatible_with_an_executed_response_or_history(reason):
    with pytest.raises(ValueError):
        _inspect(_response(skip_reason=reason))
    with pytest.raises(ValueError):
        _inspect(history=(_history("headed", skip_reason=reason),))


@pytest.mark.parametrize("blocked", [
    "subscription_required", [], {}, {"requirement": "payment_required", "provenance": "visible_blocking_ui"},
    {"requirement": "subscription_required"}, {"provenance": "explicit_user_report"},
    {"requirement": "subscription_required", "provenance": "metadata"},
    {"requirement": "subscription_required", "provenance": "visible_blocking_ui", "extra": True},
])
def test_blocked_access_is_null_or_the_exact_observation_schema(blocked):
    with pytest.raises(ValueError):
        _inspect(_response(blocked_access=blocked))


@pytest.mark.parametrize("candidates", [
    None, {}, "subscribe", [None], [{}],
    [{"visible": True, "text": "subscribe"}],
    [{"visible": 1, "text": "subscribe", "controls": []}],
    [{"visible": True, "text": None, "controls": []}],
    [{"visible": True, "text": "subscribe", "controls": "Subscribe"}],
    [{"visible": True, "text": "subscribe", "controls": [1]}],
    [{"visible": True, "text": "subscribe", "controls": [], "instruction": "bypass"}],
])
def test_access_candidate_schema_is_strict(candidates):
    with pytest.raises(ValueError):
        _inspect(_response(access_gate_candidates=candidates))


# Actual evidence, not article length or a transport's optimistic ok flag.

@pytest.mark.parametrize("status", [401, 402, 405, 429, 451])
def test_manufactured_status_does_not_authorize_status_diagnosis(status):
    response = _response(status=status, status_is_real=False,
                         headers={"Content-Type": HTML, "WWW-Authenticate": 'Basic realm="site"',
                                  "X-Amzn-Waf-Action": "captcha"})
    result = _inspect(response)
    assert result["verdict"] == "CONTINUE"
    assert result["access_requirement"] is None
    assert result["next_action"] == "headed"
    assert response["status"] == status  # Normalization must not mutate caller evidence.


@pytest.mark.parametrize("status", [402, 429, 451])
def test_omitted_status_provenance_defaults_to_manufactured(status):
    response = _response(status=status)
    del response["status_is_real"]
    result = _inspect(response)
    assert result["verdict"] == "CONTINUE"
    assert result["access_requirement"] is None
    assert result["next_action"] == "headed"


def test_real_browser_login_is_diagnosed_before_readable_success():
    result = _assert_contract(_inspect(_login_response()))
    assert result["verdict"] == "NEEDS_LOGIN"
    assert result["failure_class"] == "needs_login"
    assert result["next_action"] == "probe_cdp"
    assert result["login_url"] == URL


def test_readable_browser_login_without_metadata_refuses_success_without_cdp():
    result = _inspect(_login_response(headers={}))
    assert result["verdict"] == "CONTINUE"
    assert result["next_action"] == "stop"
    assert result["error_code"] == "access_unverified"
    assert result["access_requirement"] is None


@pytest.mark.parametrize("response", [
    _response(body=LOGIN, headers={}),
    _response(body=CAPTCHA, status=403, headers={}),
    _response(body=CAPTCHA, status=403, headers={"Content-Type": HTML}),
])
def test_missing_or_unproven_vendor_metadata_cannot_unlock_cdp(response):
    result = _inspect(response)
    assert result["verdict"] == "CONTINUE"
    assert result["next_action"] == "headed"


def test_captcha_uses_real_browser_vendor_and_document_evidence():
    result = _assert_contract(_inspect(_captcha_response()))
    assert result["verdict"] == "CAPTCHA_CHALLENGE"
    assert result["next_action"] == "headed"
    assert result["observation"]["verdict"] == "CAPTCHA_CHALLENGE"


def test_raw_duplicate_cookie_lines_preserve_browser_vendor_evidence():
    result = _inspect(_response(status=403,
        body='<html><script src="/px/captcha.js?a=c&u=1"></script></html>',
        set_cookie=["unrelated=a; Path=/", "_pxvid=fixture; Path=/; HttpOnly"]))
    assert result["verdict"] == "CAPTCHA_CHALLENGE"
    assert result["next_action"] == "headed"


@pytest.mark.parametrize("availability,action", [(None, "probe_cdp"), (False, "ask_user"), (True, "cdp")])
def test_login_routes_directly_to_eligible_cdp_without_fresh_headed(availability, action):
    result = _inspect(_login_response(), cdp_available=availability)
    assert result["next_action"] == action
    if action == "ask_user":
        assert result["next_step"].startswith("ASK THE USER")
        assert "9222" in result["next_step"]


def test_cdp_still_showing_login_asks_for_sign_in_without_reentry():
    result = _inspect(_login_response(tier="cdp", final_url=LOGIN_URL), cdp_available=True,
                      history=(_history("headless", verdict="NEEDS_LOGIN", login_url=LOGIN_URL),))
    assert result["next_action"] == "ask_user"
    assert "sign in" in result["next_step"].lower()
    assert result["login_url"] == LOGIN_URL


def test_earlier_login_evidence_survives_cdp_generic_failure_without_reentry():
    result = _inspect(_response(tier="cdp", status=500), cdp_available=True,
        history=(_history("headless", verdict="NEEDS_LOGIN", login_url=LOGIN_URL),))
    assert result["observation"]["verdict"] == "CONTINUE"
    assert result["verdict"] == "NEEDS_LOGIN"
    assert result["login_url"] == LOGIN_URL
    assert result["next_action"] == "ask_user"
    assert "sign in" in result["next_step"].lower()


def test_cdp_still_showing_captcha_asks_human_without_another_navigation():
    result = _inspect(_captcha_response(tier="cdp"), cdp_available=True,
                      history=(_history("headless", verdict="CAPTCHA_CHALLENGE"),
                               _history("headed", verdict="CAPTCHA_CHALLENGE")))
    assert result["next_action"] == "ask_user"
    assert result["failure_class"] == "captcha_challenge"


def test_captcha_is_sticky_when_headed_loses_markers_but_still_fails():
    result = _inspect(_response(tier="headed", status=500),
                      history=(_history("headless", verdict="CAPTCHA_CHALLENGE"),))
    assert result["verdict"] == "CAPTCHA_CHALLENGE"
    assert result["observation"]["verdict"] == "CONTINUE"
    assert result["next_action"] == "probe_cdp"
    assert result["failure_class"] == "captcha_challenge"


@pytest.mark.parametrize("reason", ["headed_disabled", "browser_unavailable"])
def test_explicit_within_ceiling_headed_skip_satisfies_captcha_opportunity(reason):
    response = _response(tier="headed", executed=False, skip_reason=reason)
    result = _assert_contract(_inspect(response, headed_allowed=False,
        history=(_history("headless", verdict="CAPTCHA_CHALLENGE"),)))
    assert result["verdict"] == "CAPTCHA_CHALLENGE"
    assert result["observation"] == _history("headed", executed=False, skip_reason=reason, verdict=None)
    assert result["next_action"] == "probe_cdp"
    absent = _inspect(response, headed_allowed=False, cdp_available=False,
                      history=(_history("headless", verdict="CAPTCHA_CHALLENGE"),))
    assert absent["next_action"] == "ask_user"
    assert "headed browser could not clear" not in absent["next_step"].lower()


@pytest.mark.parametrize("reason", ["ceiling", "policy_disabled", None])
def test_arbitrary_unexecuted_headed_observation_is_not_a_captcha_opportunity(reason):
    result = _inspect(_response(tier="headed", executed=False, skip_reason=reason),
                      history=(_history("headless", verdict="CAPTCHA_CHALLENGE"),))
    assert result["next_action"] not in {"probe_cdp", "cdp", "ask_user"}
    assert result["observation"]["verdict"] is None


def test_headed_boolean_opt_out_alone_does_not_forge_a_skip_observation():
    result = _inspect(_captcha_response(), headed_allowed=False)
    assert result["next_action"] == "stop"


@pytest.mark.parametrize("ceiling", ["http", "headless", "headed"])
def test_cdp_ceiling_cannot_be_bypassed_by_login_or_headed_skip(ceiling):
    history = (_history("headless", verdict="CAPTCHA_CHALLENGE"),
               _history("headed", executed=False, verdict=None, skip_reason="headed_disabled"))
    result = _inspect(_login_response(), history=history, max_tier=ceiling, cdp_available=True)
    assert result["next_action"] == "stop"


def test_an_executed_stage_in_history_is_not_requested_again():
    result = _inspect(_response(tier="http", status=503), history=(_history("headless"),))
    assert result["next_action"] == "headed"
    exhausted = _inspect(_response(tier="http", status=503),
                         history=(_history("headless"), _history("headed"), _history("cdp")))
    assert exhausted["next_action"] == "stop"


@pytest.mark.parametrize("status", [None, 404, 500, 503])
def test_ordinary_exhaustion_never_probes_or_requests_cdp(status):
    result = _inspect(_response(tier="headed", status=status), cdp_available=True,
                      history=(_history("http"), _history("headless")))
    assert result["next_action"] == "stop"
    assert not result["next_step"].startswith("ASK THE USER")


def test_earlier_login_url_survives_a_later_hard_block_without_an_ask():
    result = _inspect(_response(tier="cdp", status=451), cdp_available=True,
                      history=(_history("headless", verdict="NEEDS_LOGIN", login_url=LOGIN_URL),))
    assert result["verdict"] == "HARD_BLOCK"
    assert result["failure_class"] == "hard_block"
    assert result["login_url"] == LOGIN_URL
    assert result["next_action"] == "stop"
    assert not result["next_step"].startswith("ASK THE USER")


@pytest.mark.parametrize("tier,action", [("http", "headless"), ("headless", "headed"), ("headed", "stop")])
def test_real_rate_limit_keeps_finite_owned_escalation_only(tier, action):
    result = _inspect(_response(tier=tier, status=429, headers={"Retry-After": "60"}))
    assert result["verdict"] == "RATE_LIMITED"
    assert result["next_action"] == action
    assert result["retry_after"] == "60"


@pytest.mark.parametrize("first,later,expected", [("120", "30", "120"), (None, "30", "30"),
                                                ("120", None, "120"), (None, None, None)])
def test_first_rate_limit_wait_survives_unless_later_actual_evidence_fills_it(first, later, expected):
    headers = {"Content-Type": HTML}
    if later is not None:
        headers["rEtRy-AfTeR"] = later
    result = _inspect(_response(tier="headed", status=429, headers=headers),
                      history=(_history("http", verdict="RATE_LIMITED", retry_after=first),))
    assert result["retry_after"] == expected
    assert result["observation"]["retry_after"] == later
    assert result["next_action"] == "stop"


def test_rate_limit_does_not_erase_prior_captcha_permission_or_wait():
    result = _inspect(_response(tier="headed", status=429, headers={"Retry-After": "90"}),
                      history=(_history("headless", verdict="CAPTCHA_CHALLENGE"),))
    assert result["verdict"] == "RATE_LIMITED"
    assert result["retry_after"] == "90"
    assert result["next_action"] == "probe_cdp"


@pytest.mark.parametrize("available,action", [(False, "ask_user"), (True, "cdp")])
def test_inspection_caller_reclassifies_identical_observation_and_appends_it_once(available, action):
    response = _login_response()
    history = (_history("http"),)
    before = copy.deepcopy((response, history))
    probe = _inspect(response, history=history)
    classified = _inspect(response, history=history, cdp_available=available)
    assert probe["next_action"] == "probe_cdp"
    assert classified["next_action"] == action
    assert probe["observation"] == classified["observation"]
    assert (response, history) == before
    roundtripped = json.loads(json.dumps([*history, classified["observation"]]))
    following = _inspect(_response(tier="cdp", ok=True, body=SHORT_APP),
                         history=roundtripped, cdp_available=True)
    assert following["next_action"] == "accept"
    assert [item["tier"] for item in roundtripped] == ["http", "headless"]
    assert all(set(item) == OBSERVATION_KEYS for item in roundtripped)


@pytest.mark.parametrize("control", ["Subscribe", "Purchase access", "Become a member"])
def test_visible_gate_accepts_subscription_purchase_and_member_controls(control):
    result = _inspect(_response(ok=True, body=WALL, access_gate_candidates=[
        {"visible": True, "text": "Subscription required", "controls": [control]}]))
    assert result["access_requirement"] == "subscription_required"
    assert result["next_action"] == "check_access"


# Access evidence: positive visible blocking UI and the equally important controls.

@pytest.mark.parametrize("phrase", ["subscribe to continue reading", "subscribe to read", "subscription required",
                                  "subscribers only", "unlock this article", "  SUBSCRIBE\n TO   READ  "])
def test_visible_candidate_requires_explicit_restriction_and_subscription_control(phrase):
    result = _inspect(_response(ok=True, body=ARTICLE, access_gate_candidates=[
        {"visible": True, "text": phrase, "controls": ["Subscribe"]}]))
    assert result["access_requirement"] == "subscription_required"
    assert result["next_action"] == "check_access"
    assert result["observation"]["access_requirement"] == "subscription_required"


@pytest.mark.parametrize("candidate", [
    {"visible": False, "text": "Subscribe to continue reading", "controls": ["Subscribe"]},
    {"visible": True, "text": "Subscribe to our newsletter", "controls": ["Subscribe"]},
    {"visible": True, "text": "Subscribe to continue reading", "controls": ["Close"]},
    {"visible": True, "text": "Subscription plans are discussed in this article", "controls": ["Subscribe"]},
    {"visible": True, "text": "Subscribe to continue reading", "controls": []},
])
def test_generic_invisible_or_nonblocking_candidate_does_not_invent_access_requirement(candidate):
    result = _inspect(_response(ok=True, body=ARTICLE, access_gate_candidates=[candidate]))
    assert result["next_action"] == "accept"
    assert result["access_requirement"] is None


def test_article_body_and_paid_metadata_are_not_visible_gate_evidence():
    body = ("<html><head><title>Subscription economics</title>"
            '<meta name="isAccessibleForFree" content="false"></head><body><article>'
            "The phrase subscribers only appears in this discussion of subscription required notices."
            " Subscribe to our newsletter.</article></body></html>")
    result = _inspect(_response(ok=True, body=body))
    assert result["next_action"] == "accept"
    assert result["access_requirement"] is None


@pytest.mark.parametrize("provenance", ["explicit_user_report", "visible_blocking_ui"])
@pytest.mark.parametrize("entitlement,action", [("unknown", "check_access"), ("denied", "stop"), ("confirmed", "stop")])
def test_validated_blocked_access_forbids_success_for_every_entitlement(provenance, entitlement, action):
    result = _inspect(_response(ok=True, body=ARTICLE, blocked_access={
        "requirement": "subscription_required", "provenance": provenance}), entitlement=entitlement)
    assert result["next_action"] == action
    assert result["access_requirement"] == "subscription_required"
    assert result["next_step"]


@pytest.mark.parametrize("entitlement,action", [("unknown", "check_access"), ("denied", "stop"), ("confirmed", "stop")])
def test_real_402_requires_legitimate_access_without_assuming_subscription(entitlement, action):
    result = _inspect(_response(tier="http", status=402), entitlement=entitlement)
    assert result["access_requirement"] == "payment_required"
    assert result["next_action"] == action


@pytest.mark.parametrize("entitlement", ["unknown", "denied", "confirmed"])
def test_hard_legal_block_outranks_access_and_login(entitlement):
    result = _inspect(_response(status=451, body=LOGIN, blocked_access=BLOCKED), entitlement=entitlement,
                      history=(_history("headless", verdict="NEEDS_LOGIN", login_url=LOGIN_URL),))
    assert result["verdict"] == "HARD_BLOCK"
    assert result["failure_class"] == "hard_block"
    assert result["next_action"] == "stop"


@pytest.mark.parametrize("entitlement,action", [("unknown", "check_access"), ("denied", "stop"),
                                               ("confirmed", "probe_cdp")])
def test_access_and_separate_browser_login_use_entitlement_before_cdp(entitlement, action):
    result = _inspect(_login_response(blocked_access=BLOCKED), entitlement=entitlement)
    assert result["verdict"] == "NEEDS_LOGIN"
    assert result["access_requirement"] == "subscription_required"
    assert result["next_action"] == action


def test_confirmed_access_does_not_skip_captcha_headed_opportunity():
    result = _inspect(_captcha_response(blocked_access=BLOCKED), entitlement="confirmed")
    assert result["next_action"] == "headed"
    after_headed = _inspect(_captcha_response(tier="headed", blocked_access=BLOCKED),
        entitlement="confirmed", history=(_history("headless", verdict="CAPTCHA_CHALLENGE",
                                                   access_requirement="subscription_required"),))
    assert after_headed["next_action"] == "probe_cdp"


def test_fresh_wall_free_observation_can_accept_after_human_access_confirmation():
    result = _inspect(_response(ok=True, body=SHORT_APP), entitlement="confirmed",
                      history=(_history("headless", access_requirement="subscription_required"),))
    assert result["next_action"] == "accept"
    assert result["access_requirement"] is None


@pytest.mark.parametrize("body", [WALL, LOGIN])
def test_http_structural_hint_requests_visibility_confirmation_before_success(body):
    result = _inspect(_response(tier="http", ok=True, body=body))
    assert result["verdict"] == "CONTINUE"
    assert result["access_requirement"] is None
    assert result["next_action"] == "headless"
    ceiling = _inspect(_response(tier="http", ok=True, body=body), max_tier="http")
    assert ceiling["next_action"] == "stop"
    assert ceiling["error_code"] == "access_unverified"


def test_browser_hidden_subscription_structure_is_not_an_http_hint_replay():
    candidate = {**CANDIDATE, "visible": False}
    result = _inspect(_response(ok=True, body=WALL, access_gate_candidates=[candidate]))
    assert result["next_action"] == "accept"
    assert result["access_requirement"] is None


def test_unexecuted_current_observation_without_history_has_null_verdict():
    result = _assert_contract(_inspect(_response(executed=False)))
    assert result["verdict"] is None
    assert result["observation"] == _history("headless", executed=False, verdict=None)
    assert result["next_action"] != "accept"


def test_executed_history_with_unknown_verdict_does_not_invent_captcha():
    result = _inspect(_response(tier="headed", status=500),
        history=(_history("headless", verdict=None),))
    assert result["next_action"] == "stop"
    assert result["observation"]["verdict"] == "CONTINUE"


# Pure API and fresh CLI effect containment.

def _install_effect_tripwires(monkeypatch, effects):
    def forbid(name):
        def called(*args, **kwargs):
            effects.append(name)
            raise AssertionError("inspection reached an operational boundary")
        return called

    for module, names in (
        (state, ("open_db", "bootstrap_if_needed", "state_dir", "apply_rewrites",
                 "compute_cache_key", "Cache", "Blocklist", "FetchLog", "Config")),
        (safety, ("validate_url_safety", "cdp_port_reachable")),
        (tiers, ("startup_reap", "reap_zombies", "tier_http", "tier_playwright", "tier_cdp", "tier_copilot")),
        (fetch, ("cascade_fetch",)),
        (socket, ("getaddrinfo", "create_connection")),
        (sqlite3, ("connect",)),
        (subprocess, ("run", "Popen")),
        (glob, ("glob",)),
    ):
        for name in names:
            monkeypatch.setattr(module, name, forbid(module.__name__ + "." + name))
    monkeypatch.setattr(socket.socket, "connect", forbid("socket.connect"))


def test_api_has_no_operational_effects_output_or_input_mutation(monkeypatch, capsys):
    effects = []
    response = _login_response(set_cookie=["private=session-sentinel"])
    history = (_history("http"),)
    before = copy.deepcopy((response, history))
    _install_effect_tripwires(monkeypatch, effects)
    result = _assert_contract(_inspect(response, history=history))
    assert result["next_action"] == "probe_cdp"
    assert effects == []
    assert (response, history) == before
    streams = capsys.readouterr()
    assert streams.out == streams.err == ""
    assert "session-sentinel" not in json.dumps(result)


@pytest.fixture
def inspect_cli(monkeypatch, tmp_path):
    """Fresh actual CLI, no process spawn; record even a swallowed effect call."""
    def invoke(payload, *, options=(), dependency=None):
        raw = payload if isinstance(payload, (str, bytes)) else json.dumps(payload, ensure_ascii=False)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        effects = []
        output, error = io.StringIO(), io.StringIO()
        original_import = builtins.__import__
        original_open, original_io_open, original_os_open = builtins.open, io.open, os.open
        before_files = set(tmp_path.rglob("*"))

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            root = name.split(".", 1)[0]
            if root in {"state", "tiers", "safety", "providers"}:
                effects.append("import:" + name)
                raise AssertionError("inspection imported operational code")
            if root == "trafilatura" and dependency == "missing":
                raise ModuleNotFoundError("private-dependency-sentinel", name="trafilatura")
            if root == "trafilatura" and dependency == "system_exit":
                print("private-import-stdout-sentinel")
                print("private-import-stderr-sentinel", file=sys.stderr)
                raise SystemExit(2)
            return original_import(name, globals, locals, fromlist, level)

        def guard_open(original):
            def opened(file, mode="r", *args, **kwargs):
                if any(flag in mode for flag in "wax+"):
                    effects.append("file-write")
                    raise AssertionError("inspection wrote a file")
                return original(file, mode, *args, **kwargs)
            return opened

        def guard_os_open(path, flags, *args, **kwargs):
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                effects.append("os-file-write")
                raise AssertionError("inspection wrote a file")
            return original_os_open(path, flags, *args, **kwargs)

        with monkeypatch.context() as patch:
            _install_effect_tripwires(patch, effects)
            patch.setattr(sys, "dont_write_bytecode", True)
            patch.setattr(sys, "argv", [str(Path(fetch.__file__)), "--inspect-response", *options])
            patch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
            patch.setattr(sys, "stdout", output)
            patch.setattr(sys, "stderr", error)
            patch.delitem(sys.modules, "extract", raising=False)
            patch.setattr(builtins, "__import__", guarded_import)
            patch.setattr(builtins, "open", guard_open(original_open))
            patch.setattr(io, "open", guard_open(original_io_open))
            patch.setattr(os, "open", guard_os_open)
            patch.chdir(tmp_path)
            try:
                runpy.run_path(str(Path(fetch.__file__)), run_name="__main__")
            except SystemExit as exc:
                code = exc.code
            else:
                code = 0
        assert effects == [], effects
        assert set(tmp_path.rglob("*")) == before_files
        stdout, stderr = output.getvalue(), error.getvalue()
        assert stderr == ""
        decoded, end = json.JSONDecoder().raw_decode(stdout.lstrip())
        assert stdout.lstrip()[end:].strip() == "", "CLI emitted more than one JSON value"
        _assert_contract(decoded)
        assert "private-" not in stdout
        return code, decoded
    return invoke


def _request(response=None, **changes):
    value = {"response": _response() if response is None else response, "headed_allowed": True}
    value.update(changes)
    return value


@pytest.mark.parametrize("response,action", [
    (_response(ok=True, body=SHORT_APP), "accept"),
    (_response(tier="headed", status=404), "stop"),
    (_login_response(), "ask_user"),
    (_response(status=402), "check_access"),
])
def test_cli_valid_decisions_exit_zero_without_operational_imports(inspect_cli, response, action):
    code, result = inspect_cli(_request(response, cdp_available=False))
    assert code == 0
    assert result["next_action"] == action


def test_cli_valid_input_does_not_echo_body_or_raw_cookies(inspect_cli):
    response = _response(ok=True, body=SHORT_APP + "<!--private-body-sentinel-->",
                         set_cookie=["private-cookie-sentinel=value; HttpOnly"])
    code, result = inspect_cli(_request(response))
    assert code == 0
    assert result["next_action"] == "accept"


def test_cli_minimal_request_defaults_and_history_roundtrip(inspect_cli):
    code, initial = inspect_cli(_request(_captcha_response()))
    assert code == 0 and initial["next_action"] == "headed"
    skipped = _response(tier="headed", executed=False, skip_reason="headed_disabled")
    history = [initial["observation"]]
    code, probe = inspect_cli(_request(skipped, history=history, headed_allowed=False))
    assert code == 0 and probe["next_action"] == "probe_cdp"
    code, classified = inspect_cli(_request(skipped, history=history,
                                           headed_allowed=False, cdp_available=False))
    assert code == 0 and classified["next_action"] == "ask_user"
    assert probe["observation"] == classified["observation"]
    history.append(classified["observation"])
    assert [item["tier"] for item in history] == ["headless", "headed"]


@pytest.mark.parametrize("payload", [
    {}, [], None, "null", "[]", '{"private-input-sentinel":',
    {"response": _response()}, {"headed_allowed": True},
    _request(extra="private-input-sentinel"), _request(headed_allowed=1),
    _request(cdp_available="yes"), _request(max_tier="copilot"),
    _request(entitlement="paid"), _request(history={}),
    _request(history=[{"tier": "headless", "body": "private-body-sentinel"}]),
    _request(_response(extra="private-input-sentinel")),
    _request(_response(body=None)), _request(_response(headers={"x": False})),
    _request(_response(hops=[[302, URL]])),
    _request(_response(access_gate_candidates=[{"visible": True}])),
])
def test_cli_invalid_requests_are_one_private_structured_stop(inspect_cli, payload):
    code, result = inspect_cli(payload)
    assert code == 1
    assert result["next_action"] == "stop"
    assert result["error_code"] == "invalid_input"
    assert result["error"]


@pytest.mark.parametrize("payload", [b"\xff", b"\xef\xbb\xbf{", b"{\"response\": NaN}", b"{}\n{}",
                                     json.dumps(_request(_response(body="\ud800")))])
def test_cli_malformed_encoding_and_non_json_are_contained(inspect_cli, payload):
    code, result = inspect_cli(payload)
    assert code == 1
    assert result["error_code"] == "invalid_input"
    assert result["next_action"] == "stop"


@pytest.mark.parametrize("options", [
    ("--url", "https://example.com/private-option-sentinel"), ("--format", "json"),
    ("--format", "markdown"), ("--raw-required",), ("--start-tier", "http"),
    ("--max-tier", "cdp"), ("--no-rewrite",), ("--no-cache",), ("--allow-private",),
    ("--require-cdp",), ("--connect-timeout", "5"), ("--read-timeout", "15"),
    ("--total-timeout", "30"), ("--copilot-model", "private-model-sentinel"),
    ("--reap-zombies",), ("--clear-cache",), ("--clear-blocklist",),
    ("--unknown-private-option-sentinel",), ("--start-tier", "private-tier-sentinel"),
    ("--connect-timeout", "private-number-sentinel"), ("--url",),
    ("--help",), ("--inspect-response",), ("--inspec",),
])
def test_cli_rejects_explicit_conflicting_and_parser_error_options(inspect_cli, options):
    code, result = inspect_cli(_request(), options=options)
    assert code == 1
    assert result["error_code"] == "invalid_input"
    assert result["next_action"] == "stop"


@pytest.mark.parametrize("character,count", [("x", 4 * 1024 * 1024 + 1),
                                            ("é", 2 * 1024 * 1024 + 1)],
                         ids=["ascii-over-limit", "utf8-over-limit"])
def test_cli_body_limit_counts_utf8_bytes_not_characters(inspect_cli, character, count):
    code, result = inspect_cli(_request(_response(body=character * count)))
    assert code == 1
    assert result["error_code"] == "invalid_input"
    assert result["next_action"] == "stop"


def test_cli_exact_body_limit_is_valid_within_request_limit(inspect_cli):
    request = _request(_response(ok=True, body="x" * (4 * 1024 * 1024)))
    code, result = inspect_cli(json.dumps(request, ensure_ascii=False))
    assert code == 0
    assert result["next_action"] == "accept"


def test_cli_request_limit_includes_whitespace_and_accepts_exact_boundary(inspect_cli):
    request = json.dumps(_request(_response(ok=True, body=SHORT_APP)))
    exact = request + " " * (8 * 1024 * 1024 - len(request.encode("utf-8")))
    code, result = inspect_cli(exact)
    assert code == 0 and result["next_action"] == "accept"
    code, result = inspect_cli(exact + " ")
    assert code == 1
    assert result["error_code"] == "invalid_input"
    assert result["next_action"] == "stop"


@pytest.mark.parametrize("dependency", ["missing", "system_exit"])
def test_cli_missing_classifier_and_noisy_dependency_exit_are_contained(inspect_cli, dependency):
    code, result = inspect_cli(_request(), dependency=dependency)
    assert code == 1
    assert result["error_code"] == "inspection_unavailable"
    assert result["next_action"] == "stop"
    assert result["verdict"] is None


def test_api_missing_classifier_raises_runtime_unavailability_not_continue(monkeypatch, capsys):
    original_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name.split(".", 1)[0] == "trafilatura":
            print("private-import-stdout-sentinel")
            print("private-import-stderr-sentinel", file=sys.stderr)
            raise SystemExit(2)
        return original_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "extract", raising=False)
    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(fetch.InspectionUnavailable) as caught:
        _inspect()
    assert isinstance(caught.value, RuntimeError)
    assert "private-" not in str(caught.value)
    streams = capsys.readouterr()
    assert streams.out == streams.err == ""


# Ordinary cascade uses the same inspector before success/cache. Database and
# classifier are real; each substituted tier returns actual transport fields.

@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.executescript(state._DDL)
    yield connection
    connection.close()


class _Transports:
    def __init__(self):
        self.attempted = []
        self.results = {}
        self.probes = []
        self.inspections = []
        self.cdp_available = False

    def run(self, name):
        self.attempted.append(name)
        return self.results.get(name, _tier(name))


def _tier(name, *, ok=False, body="", status=503, content="", **changes):
    values = dict(ok=ok, tier=name, html=body, content=content, status=status,
                  status_is_real=True, headers={"Content-Type": HTML}, final_url=URL,
                  bytes_=len(content.encode("utf-8")), error="" if ok else "fixture transport failure",
                  failure_class="" if ok else "empty_content")
    values.update(changes)
    return tiers.TierResult(**values)


@pytest.fixture
def transports(monkeypatch):
    transport = _Transports()
    actual_inspect = fetch.inspect_response

    def inspect(response, **options):
        result = actual_inspect(response, **options)
        transport.inspections.append((copy.deepcopy(response), copy.deepcopy(options), copy.deepcopy(result)))
        return result

    def probe(*args, **kwargs):
        transport.probes.append((args, kwargs))
        return transport.cdp_available

    def forbidden(*args, **kwargs):
        raise AssertionError("cascade reached disabled provider or real DNS")

    monkeypatch.setattr(fetch, "inspect_response", inspect)
    monkeypatch.setattr(tiers, "tier_http", lambda url, **kw: transport.run("http"))
    monkeypatch.setattr(tiers, "tier_playwright", lambda url, *, mode, **kw: transport.run(mode))
    monkeypatch.setattr(tiers, "tier_cdp", lambda url, **kw: transport.run("cdp"))
    monkeypatch.setattr(tiers, "tier_copilot", forbidden)
    monkeypatch.setattr(tiers, "headed_tier_allowed", lambda: True)
    monkeypatch.setattr(safety, "validate_url_safety", lambda *a, **kw: None)
    monkeypatch.setattr(safety, "cdp_port_reachable", probe)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    return transport


def _cascade(conn, **options):
    return fetch.cascade_fetch(URL, conn=conn, **options)


def _assert_no_cache(conn):
    assert conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0


def test_cascade_http_visible_200_wall_is_confirmed_then_checks_access_once(conn, transports):
    transports.results["http"] = _tier("http", ok=True, body=WALL, status=200, content="Readable preview")
    transports.results["headless"] = _tier("headless", ok=True, body=WALL, status=200,
        content="Readable preview", access_gate_candidates=[copy.deepcopy(CANDIDATE)])
    envelope = _cascade(conn)
    assert transports.attempted == ["http", "headless"]
    assert transports.probes == []
    assert envelope["next_action"] == "check_access"
    assert envelope["access_requirement"] == "subscription_required"
    assert envelope["error"] and envelope["content"] == ""
    _assert_no_cache(conn)
    assert [entry[2]["next_action"] for entry in transports.inspections] == ["headless", "check_access"]


def test_cascade_http_hint_browser_negative_can_accept_and_cache_actual_browser_content(conn, transports):
    transports.results["http"] = _tier("http", ok=True, body=WALL, status=200, content="Unconfirmed preview")
    transports.results["headless"] = _tier("headless", ok=True, body=ARTICLE, status=200,
                                           content=PUBLIC_TEXT, access_gate_candidates=[])
    envelope = _cascade(conn)
    assert transports.attempted == ["http", "headless"]
    assert envelope["content"] == PUBLIC_TEXT and not envelope.get("error")
    assert envelope["tier"] == "headless"
    rows = conn.execute("SELECT content, tier FROM cache").fetchall()
    assert [(row["content"], row["tier"]) for row in rows] == [(PUBLIC_TEXT, "headless")]
    assert transports.probes == []


def test_cascade_generic_subscription_article_accepts_http_without_browser_or_probe(conn, transports):
    body = ARTICLE.replace("</article>", "Subscribe to our newsletter; subscribers only is a quoted label.</article>")
    transports.results["http"] = _tier("http", ok=True, body=body, status=200, content=PUBLIC_TEXT)
    envelope = _cascade(conn)
    assert transports.attempted == ["http"]
    assert envelope["content"] == PUBLIC_TEXT and not envelope.get("error")
    assert transports.probes == []
    assert conn.execute("SELECT content FROM cache").fetchone()[0] == PUBLIC_TEXT


def test_cascade_paid_but_readable_hidden_form_accepts_after_actual_visibility_check(conn, transports):
    body = ARTICLE.replace("</body>", '<form hidden><p>Subscribers only</p>'
                           '<button>Subscribe</button></form></body>')
    transports.results["http"] = _tier("http", ok=True, status=200, body=body, content="HTTP text")
    transports.results["headless"] = _tier("headless", ok=True, status=200, body=body,
        content=PUBLIC_TEXT, access_gate_candidates=[
            {"visible": False, "text": "Subscribers only", "controls": ["Subscribe"]}])
    envelope = _cascade(conn)
    assert transports.attempted == ["http", "headless"]
    assert envelope["content"] == PUBLIC_TEXT and not envelope.get("error")
    assert transports.probes == []
    assert conn.execute("SELECT content FROM cache").fetchone()[0] == PUBLIC_TEXT


@pytest.mark.parametrize("body", [WALL, LOGIN])
def test_cascade_http_hint_under_http_ceiling_stops_without_wall_content_or_cache(conn, transports, body):
    transports.results["http"] = _tier("http", ok=True, body=body, status=200, content="Wall text")
    envelope = _cascade(conn, max_tier="http")
    assert transports.attempted == ["http"]
    assert envelope["error_code"] == "access_unverified"
    assert envelope["next_action"] == "stop"
    assert envelope["content"] == ""
    assert transports.probes == []
    _assert_no_cache(conn)


def test_cascade_readable_login_never_returns_or_caches_as_success(conn, transports):
    transports.results["http"] = _tier("http", ok=True, body=LOGIN, status=200, content="Sign in")
    transports.results["headless"] = _tier("headless", ok=True, body=LOGIN, status=200, content="Sign in")
    envelope = _cascade(conn)
    assert transports.attempted == ["http", "headless"]
    assert len(transports.probes) == 1
    assert envelope["failure_class"] == "needs_login"
    assert envelope["next_action"] == "ask_user"
    assert envelope["content"] == "" and envelope["error"]
    _assert_no_cache(conn)


@pytest.mark.parametrize("real", [True, False])
def test_cascade_real_402_checks_access_but_manufactured_402_does_not(conn, transports, real):
    transports.results["http"] = _tier("http", status=402, status_is_real=real)
    transports.results["headless"] = _tier("headless", ok=True, body=ARTICLE, status=200, content=PUBLIC_TEXT)
    envelope = _cascade(conn)
    if real:
        assert transports.attempted == ["http"]
        assert envelope["access_requirement"] == "payment_required"
        assert envelope["next_action"] == "check_access"
        assert envelope["content"] == ""
        _assert_no_cache(conn)
    else:
        assert transports.attempted == ["http", "headless"]
        assert envelope["content"] == PUBLIC_TEXT and not envelope.get("error")
        assert not envelope.get("access_requirement")
    assert transports.probes == []


def test_cascade_hard_block_precedes_explicit_access_even_if_transport_says_ok(conn, transports):
    transports.results["http"] = _tier("http", ok=True, status=451, body=ARTICLE,
                                        content=PUBLIC_TEXT, blocked_access=copy.deepcopy(BLOCKED))
    envelope = _cascade(conn)
    assert transports.attempted == ["http"]
    assert envelope["failure_class"] == "hard_block"
    assert envelope["next_action"] == "stop"
    assert envelope["content"] == "" and envelope["error"]
    assert transports.probes == []
    _assert_no_cache(conn)


@pytest.mark.parametrize("available", [False, True])
def test_cascade_probe_reclassifies_same_response_and_roundtrips_history_once(conn, transports, available):
    transports.cdp_available = available
    transports.results["headless"] = _tier("headless", ok=True, status=200, body=LOGIN, content="Sign in")
    transports.results["cdp"] = _tier("cdp", ok=True, status=200, body=SHORT_APP, content="Count: 1")
    envelope = _cascade(conn)
    assert len(transports.probes) == 1
    calls = transports.inspections
    pending = next(index for index, call in enumerate(calls) if call[2]["next_action"] == "probe_cdp")
    before, after = calls[pending], calls[pending + 1]
    assert before[0] == after[0]
    assert before[1]["history"] == after[1]["history"]
    assert before[1]["cdp_available"] is None
    assert after[1]["cdp_available"] is available
    assert before[2]["observation"] == after[2]["observation"]
    if available:
        assert transports.attempted == ["http", "headless", "cdp"]
        assert envelope["content"] == "Count: 1" and not envelope.get("error")
        cdp_call = next(call for call in calls if call[0]["tier"] == "cdp")
        history = cdp_call[1]["history"]
    else:
        assert transports.attempted == ["http", "headless"]
        assert envelope["next_action"] == "ask_user"
        history = envelope["inspection_history"]
    assert [item["tier"] for item in history] == ["http", "headless"]
    assert all(set(item) == OBSERVATION_KEYS for item in history)


@pytest.mark.parametrize("available", [False, True])
def test_cascade_ordinary_exhaustion_never_probes_even_when_cdp_might_exist(conn, transports, available):
    transports.cdp_available = available
    envelope = _cascade(conn)
    assert transports.attempted == ["http", "headless", "headed"]
    assert transports.probes == []
    assert envelope["next_action"] == "stop"
    assert envelope["error"] and envelope["content"] == ""
    _assert_no_cache(conn)


def test_cascade_headed_opt_out_records_actual_skip_before_captcha_probe(conn, transports, monkeypatch):
    monkeypatch.setattr(tiers, "headed_tier_allowed", lambda: False)
    transports.results["headless"] = _tier("headless", body=CAPTCHA, status=403,
        headers={"Content-Type": HTML, "X-DataDome": "protected"})
    envelope = _cascade(conn)
    assert transports.attempted == ["http", "headless"]
    assert len(transports.probes) == 1
    assert envelope["next_action"] == "ask_user"
    skipped = [item for item in envelope["inspection_history"] if item["tier"] == "headed"]
    assert skipped == [_history("headed", executed=False, verdict=None, skip_reason="headed_disabled")]
    assert "headed browser could not clear" not in envelope["next_step"].lower()


def test_cascade_earliest_retry_after_survives_later_exhaustion_without_cdp(conn, transports):
    transports.results["http"] = _tier("http", status=429, headers={"Retry-After": "120"})
    transports.results["headless"] = _tier("headless", status=429, headers={"Retry-After": "30"})
    envelope = _cascade(conn)
    assert transports.attempted == ["http", "headless", "headed"]
    assert envelope["failure_class"] == "rate_limited"
    assert envelope["retry_after"] == "120"
    assert envelope["next_action"] == "stop"
    assert transports.probes == []
    _assert_no_cache(conn)


def test_cascade_sticky_captcha_with_real_headed_rate_limit_probes_once(conn, transports):
    transports.results["headless"] = _tier("headless", status=403, body=CAPTCHA,
        headers={"Content-Type": HTML, "X-DataDome": "protected"})
    transports.results["headed"] = _tier("headed", status=429, headers={"Retry-After": "90"})
    envelope = _cascade(conn)
    assert transports.attempted == ["http", "headless", "headed"]
    assert len(transports.probes) == 1
    assert envelope["next_action"] == "ask_user"
    assert envelope["retry_after"] == "90"
    assert [item["verdict"] for item in envelope["inspection_history"]] == [
        "CONTINUE", "CAPTCHA_CHALLENGE", "RATE_LIMITED"]
    assert envelope["content"] == ""
    _assert_no_cache(conn)
