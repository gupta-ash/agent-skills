"""The escalation gate, as the CASCADE sees it (plan S2, S3.2, S3.3; spec S3.1, S3.4).

WHAT THIS FILE IS FOR. `test_escalation_verdict.py` pins the PREDICATE -- which
needle produces which verdict. This file pins the ROUTING: given a verdict,
which tiers run, in what order, and which never run at all. So
`extract.escalation_verdict` is STUBBED in almost every test here. That
isolation is deliberate and load-bearing: a red test in this file must mean the
cascade is wired wrong, never that a needle moved.

THE RULES BEING PINNED. Rule A was REWRITTEN 2026-09-04 on the owner's ruling
that the headed and cdp rungs are in the cascade and enabled, and that the
fetch succeeding outranks where a window lands:

    Rule A -- the gate filters tier ENTRY, not tier exit.
        enter headed   iff last_verdict != NEEDS_LOGIN
        enter copilot  iff last_verdict != NEEDS_LOGIN
        enter cdp      iff last_verdict == NEEDS_LOGIN, or a captcha was
                       diagnosed in this run AND headed has had its turn
    Rule B -- the verdict is only computed on a tier->=1 result.

WHAT THE OLD RULE A SAID, and why every "never reaches headed" test in this
file changed rather than went red honestly: headed was entered ONLY on
CAPTCHA_CHALLENGE, cdp ONLY on NEEDS_LOGIN, and copilot on neither, because a
verdict was treated as a LICENCE to put a window on the owner's screen. The
owner rejected that framing in as many words -- "headed or CDP should not be
disabled. it should be included ... #1 priority is that the agent succeeds. #2
is that the user is not disrupted" -- so a verdict no longer decides WHETHER a
tier may run, only what the run DIAGNOSES and whether it ends by asking the
owner to start his CDP browser.

and two consequences that are easy to get wrong:

  * A START tier is not "entered" -- it is where the loop begins -- so Rule A
    never sees it. It arrives with no verdict computed and nothing beneath it
    tried, which is why a CLAMP may not land on headed or cdp (plan S3.3). That
    is a rule about where a run may BEGIN; it says nothing about where the
    cascade may climb to, and the cascade climbs to both.
  * `HARD_BLOCK` ends the cascade where it is found, and a `NEEDS_LOGIN` with
    cdp outside the ceiling ends it too -- with a diagnosis, NOT by falling
    through to copilot (plan S3.2, a deliberate, recorded cost). `RATE_LIMITED`
    did until 2026-09-04 and no longer does. NOT because a rate limit is always
    a fact about the client that met it -- that universal was WRONG and was
    removed from `fetch.py`; an origin keying its quota by source IP puts every
    tier in one bucket. It climbs because the costs are asymmetric: stopping
    loses the fetch permanently when the limit IS client-specific, climbing
    costs further rungs in one cascade when it is not, and the owner ruled
    which way that goes. It is recorded where it is met and promoted at
    exhaustion.

NO BROWSER IS EVER LAUNCHED HERE. Every tier function is replaced before the
cascade can reach it, and there is no network in this file. That is a rule
about TEST runs on a machine somebody is sitting at, and it is unchanged; it
is not the retired claim that the shipped cascade may not open a window.
"""

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import extract
import fetch
import safety
import state
import tiers


# ---- harness ----------------------------------------------------------------

class _Tiers:
    """Every tier stubbed: record the name, then fail (or return an override)."""

    def __init__(self):
        self.attempted: list[str] = []
        self.results: dict[str, tiers.TierResult] = {}

    def run(self, name: str) -> tiers.TierResult:
        self.attempted.append(name)
        override = self.results.get(name)
        if override is not None:
            return override
        return tiers.TierResult(ok=False, tier=name, error="stub",
                                failure_class="empty_content")


class _Verdict:
    """A scripted `escalation_verdict`, keyed by the tier that just failed.

    Keying by tier is how a test writes an EVIDENCE SEQUENCE ("tier 0 saw
    nothing, tier 1 saw a captcha") without reimplementing any predicate. The
    default is `CONTINUE`, which is what the real function returns for tier 0
    under Rule B and for every ambiguous case above it.
    """

    def __init__(self):
        self.default = "CONTINUE"
        self.by_tier: dict[str, str] = {}
        self.calls: list[dict] = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return self.by_tier.get(kw.get("tier"), self.default)


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    c = state.open_db()
    yield c
    c.close()


@pytest.fixture()
def ran(monkeypatch):
    t = _Tiers()
    monkeypatch.setattr(tiers, "tier_http", lambda url, **kw: t.run("http"))
    monkeypatch.setattr(tiers, "tier_playwright",
                        lambda url, *, mode, **kw: t.run(mode))
    monkeypatch.setattr(tiers, "tier_copilot", lambda url, **kw: t.run("copilot"))
    monkeypatch.setattr(tiers, "tier_cdp", lambda url, **kw: t.run("cdp"))
    return t


@pytest.fixture()
def verdict(monkeypatch):
    v = _Verdict()
    monkeypatch.setattr(extract, "escalation_verdict", v)
    return v


URL = "https://example.com/"


def _run(conn, monkeypatch, *, headed_off=False, cdp_up=False, **kw):
    """A cascade run in the DEFAULT environment, which is headed ENABLED.

    REWRITTEN 2026-09-04. The old signature took `allow_headed` and set
    WEBFETCH_ALLOW_HEADED, because tier 2 was opt-IN and every test that
    wanted the rung in the list had to say so. The owner ruled the rung is in
    the cascade and enabled, so the interesting environment is now the one with
    NOTHING set -- and a test that had to opt in could no longer tell a working
    default from a broken one. `headed_off` drives the opt-OUT variable, for
    the one case that still needs it.
    """
    if headed_off:
        monkeypatch.setenv(tiers.HEADED_TIER_DISABLE_ENV, "1")
    else:
        monkeypatch.delenv(tiers.HEADED_TIER_DISABLE_ENV, raising=False)
    monkeypatch.setattr(safety, "cdp_port_reachable", lambda *a, **k: cdp_up)
    kw.setdefault("no_cache", True)
    kw.setdefault("search_path", "/stub/search.py")
    return fetch.cascade_fetch(URL, conn=conn, **kw)


def _promote(conn, tier_idx, tier_name, *, locked=0):
    """Put the domain in the blocklist at `tier_idx`.

    `locked=0` is what three failures leave behind -- an automatic promotion.
    `locked=1` is the README's documented operator UPDATE, which the promotion
    machine cannot produce: `record_failure` and `record_success` both refuse a
    locked row. Only a human writes that, and the clamp treats it as the
    instruction it is (plan S3.3).
    """
    conn.execute(
        "INSERT OR REPLACE INTO blocklist"
        " (domain, tier_idx, tier, reason, locked, recent_failures,"
        "  recent_successes, updated_at) VALUES (?,?,?,?,?,'[]',0,0.0)",
        ("example.com", tier_idx, tier_name, "test", locked))
    conn.commit()


# ---- Rule A: which verdict opens which rung ---------------------------------

def test_a_captcha_reaches_headed_without_synthesis_on_a_DEFAULT_run(
        conn, monkeypatch, ran, verdict):
    """Headed is enabled by default even though Copilot is now disabled."""
    verdict.by_tier = {"headless": "CAPTCHA_CHALLENGE", "headed": "CAPTCHA_CHALLENGE"}
    _run(conn, monkeypatch)
    assert ran.attempted == ["http", "headless", "headed"]


def test_a_continue_verdict_also_reaches_headed(conn, monkeypatch, ran, verdict):
    """A continue verdict also reaches headed."""
    _run(conn, monkeypatch)
    assert ran.attempted == ["http", "headless", "headed"]


def test_needs_login_routes_to_cdp_when_cdp_is_inside_the_ceiling(
        conn, monkeypatch, ran, verdict):
    """Both windowed rungs are available; the verdict picks the right one.

    headed is permitted and cdp is reachable, so the loop walks over headed AND
    over copilot to reach cdp. That is Rule A routing, not tier ordering.
    """
    verdict.by_tier = {"headless": "NEEDS_LOGIN", "cdp": "NEEDS_LOGIN"}
    _run(conn, monkeypatch, cdp_up=True, max_tier="cdp")
    assert ran.attempted == ["http", "headless", "cdp"]


def test_needs_login_terminates_with_a_diagnosis_when_cdp_is_out_of_reach(
        conn, monkeypatch, ran, verdict):
    """Plan S3.2: it does NOT fall through to copilot. This is the recorded cost.

    A login-walled page stops with the diagnosis when CDP is unavailable,
    rather than returning a synthesized account of public sources.
    """
    verdict.by_tier = {"headless": "NEEDS_LOGIN"}
    ran.results["headless"] = tiers.TierResult(
        ok=False, tier="headless", status=403, error="stub",
        failure_class="empty_content",
        final_url="https://example.com/users/sign_in")
    env = _run(conn, monkeypatch)

    assert ran.attempted == ["http", "headless"], "copilot must not answer a login wall"
    assert env["failure_class"] == "needs_login"
    assert env["login_url"] == "https://example.com/users/sign_in", (
        "the login URL must come off the result, not be echoed from the request")


# The two SHAPES of "cdp will not run", pinned apart. The test above reaches
# no CDP target under the default ceiling. The paired test below instead holds
# the target reachable and uses a raw-tier ceiling below CDP.

def _login_wall(ran):
    """A headless failure whose post-redirect URL IS the login page."""
    ran.results["headless"] = tiers.TierResult(
        ok=False, tier="headless", status=403, error="stub",
        failure_class="empty_content",
        final_url="https://example.com/users/sign_in")


def test_needs_login_terminates_when_the_ceiling_excludes_a_REACHABLE_cdp(
        conn, monkeypatch, ran, verdict):
    """Shape 1: Chrome is up on :9222, but --max-tier headed excludes cdp.

    A CONTROL, and it is the half that already worked: this shape is what the
    ceiling-membership test measured, and it must keep passing unchanged after
    the stop starts asking about effective availability instead.
    """
    verdict.by_tier = {"headless": "NEEDS_LOGIN"}
    _login_wall(ran)
    env = _run(conn, monkeypatch, cdp_up=True,
               max_tier="headed")

    assert ran.attempted == ["http", "headless"]
    assert env.get("failure_class") == "needs_login"
    assert env.get("login_url") == "https://example.com/users/sign_in"


def test_needs_login_terminates_when_cdp_is_INSIDE_the_ceiling_but_the_port_is_dead(
        conn, monkeypatch, ran, verdict):
    """Shape 2: cdp is in the tier list and nothing answers on :9222.

    `--require-cdp` is what puts it there -- it forces `cdp_ok` true so the
    loop's own guard can fail loudly on a dead port -- and it is the only
    deterministic way to hold "inside the ceiling" and "port down" at once.

    Against the unfixed code the ROUTING is already right: the cdp tier function
    never runs either way, because the guards at the top of the loop turn that
    iteration away before it. What is lost is the DIAGNOSIS -- `gate_stop` is
    never set, so the envelope carries no `failure_class` and no `login_url` and
    the caller is told only that the cascade ran out of tiers. Hence `.get`:
    pre-fix these are absent, and the failure must read as a value comparison.

    BOTH FACTS, IN ONE TEST, ON PURPOSE. The stop breaks before the loop's own
    --require-cdp guard, which is what normally tells an operator the port is
    dead. That operator asked for that explicitly, and a login diagnosis is a
    different true statement, not a replacement -- so the envelope owes them
    both. Asserted together so a later change cannot drop one and stay green.
    """
    verdict.by_tier = {"headless": "NEEDS_LOGIN"}
    _login_wall(ran)
    env = _run(conn, monkeypatch, cdp_up=False,
               max_tier="cdp", require_cdp=True)

    assert ran.attempted == ["http", "headless"]
    assert env.get("failure_class") == "needs_login", (
        "a login wall with no reachable Chrome is still a login wall")
    assert env.get("login_url") == "https://example.com/users/sign_in"
    # ONE warning, and it is the ASK. REWRITTEN 2026-09-04: this pinned the
    # exact literal `tier_cdp_skipped: launch chrome with
    # --remote-debugging-port=9222 to enable`, which was a line for a shell and
    # not for a person. R4 says the agent CHECKS and then ASKS the user to
    # start the browser, so the wording had to change; what this still pins is
    # that exactly ONE sentence is emitted, not two copies from two sites, and
    # that --require-cdp is still told the port is dead.
    assert len(env["warnings"]) == 1, env["warnings"]
    ask = env["warnings"][0]
    assert ask.startswith("ASK THE USER"), ask
    assert "--remote-debugging-port=9222" in ask, (
        "--require-cdp asked to be told when nothing answers on :9222")
    assert ask == env["next_step"], (
        "the ask a caller reads off `warnings` and the one it reads off "
        "`next_step` must be the same sentence, not two wordings")


@pytest.mark.parametrize("terminal,expected", [
    ("HARD_BLOCK", ["http", "headless"]),
    ("RATE_LIMITED", ["http", "headless", "headed"]),
])
def test_a_hard_block_ends_the_cascade_but_a_rate_limit_is_climbed_past(
        conn, monkeypatch, ran, verdict, terminal, expected):
    """REWRITTEN 2026-09-04. It read `terminal` over both verdicts with one
    expectation -- `["http", "headless"]` -- and its docstring was "nothing
    above helps, so nothing above runs". For RATE_LIMITED that is MEASURED
    FALSE: marriott.com answers urllib with 403 + `Retry-After: 28800` out of
    Akamai Bot Manager, and hyatt.com answers the SAME URL with 403 to urllib
    and 429 to headless Chrome, so the diagnosis CAN be a fact about the client
    rather than about the URL -- not always; the universal was withdrawn -- and
    the rungs above run different clients. It is now recorded where it is met
    and the cascade climbs; `test_rate_limited.py` pins what the envelope
    carries out the far end.

    KEPT AS ONE PARAMETRIZED TEST rather than split, because the CONTRAST is
    the claim: same fixture, same rung, same reachable cdp port, and the two
    verdicts route differently. HARD_BLOCK is untouched and is the control.

    NOTE THE cdp PORT IS UP AND cdp STILL DOES NOT RUN on the rate-limited row.
    Climbing past a rate limit is not a licence to drive the browser holding
    the user's logins: `_gate_allows` admits cdp on a login wall, or on a
    captcha once headed has had its turn, and a rate limit is neither.
    """
    verdict.by_tier = {"headless": terminal}
    env = _run(conn, monkeypatch, cdp_up=True, max_tier="cdp")
    assert ran.attempted == expected
    assert terminal.lower() in env["error"]



@pytest.mark.parametrize("shape,result_kw", [
    ("404", dict(status=404, error="HTTP 404 Not Found",
                 failure_class="origin_4xx_block")),
    ("timeout", dict(status=None, error="timed out after 30.0s",
                     failure_class="timeout")),
    ("5xx", dict(status=503, error="HTTP 503 Service Unavailable",
                 failure_class="origin_5xx_transient")),
    ("content gate", dict(status=200, error="content gate: 0 chars of prose",
                          failure_class="empty_content")),
])
def test_an_ordinary_failure_never_reaches_cdp(
        conn, monkeypatch, ran, verdict, shape, result_kw):
    """An ordinary failure never reaches cdp."""
    ran.results["headless"] = tiers.TierResult(
        ok=False, tier="headless", **result_kw)
    env = _run(conn, monkeypatch, cdp_up=True)
    assert "cdp" not in ran.attempted, shape
    assert ran.attempted == ["http", "headless", "headed"], shape
    assert env["error"].startswith("cascade_exhausted:"), shape


# ---- wiring: what the predicate is actually fed -----------------------------

def test_the_verdict_is_fed_from_the_failed_result(conn, monkeypatch, ran, verdict):
    """Every argument comes off the `TierResult`, and the tier name is the real one.

    `content_type` is derived case-insensitively: `TierResult.headers` carries
    the ORIGIN's own casing by construction and does not normalise, so a
    `Content-Type` lookup that assumes lowercase finds nothing and every
    content-type-gated arm of the predicate silently dies.

    Passing the REAL tier name is what makes Rule B work at all -- the predicate
    enforces it internally off that argument, and a hardcoded or shifted name
    would defeat it without any test above noticing.
    """
    ran.results["headless"] = tiers.TierResult(
        ok=False, tier="headless", status=403, error="stub",
        failure_class="empty_content",
        html="<html>wall</html>",
        headers={"Content-Type": "text/html; charset=utf-8",
                 "X-DataDome": "protected"},
        set_cookie=["datadome=abc; Path=/"],
        hops=[(302, "https://example.com/", "https://example.com/login")],
        final_url="https://example.com/login")
    _run(conn, monkeypatch)

    assert [c["tier"] for c in verdict.calls] == ["http", "headless", "headed"]
    call = verdict.calls[1]
    assert call["status"] == 403
    assert call["body"] == "<html>wall</html>"
    assert call["headers"] == {"Content-Type": "text/html; charset=utf-8",
                               "X-DataDome": "protected"}
    assert call["content_type"] == "text/html; charset=utf-8"
    assert call["set_cookie"] == ["datadome=abc; Path=/"]
    assert call["hops"] == [(302, "https://example.com/", "https://example.com/login")]
    assert call["requested_url"] == URL
    assert call["final_url"] == "https://example.com/login"


# ---- Rule B: a tier-0 diagnosis is not a diagnosis --------------------------
#
# These two are the only tests in this file that run the REAL predicate, because
# Rule B lives INSIDE it and the cascade's whole job is to hand it the true tier
# name. They come in a pair on purpose: the control proves this exact body IS a
# captcha to the real predicate and DOES reach headed from tier 1, so when the
# subject test says headed was not reached, the tier name is the only thing that
# can have changed the answer. If the control ever goes red, the needle moved --
# read `test_escalation_verdict.py`, not this file.

# The `_pxvid` cookie is REQUIRED, not decoration: round-2 finding 3 (2026-09-03)
# made vendor PRESENCE header/`server`/cookie-only, so `window._pxAppId` in the
# body no longer puts PerimeterX in the set and this response would be CONTINUE
# at every tier -- which would leave the control below green while proving
# nothing. It is a synthetic value; `_pxvid` is a measured PerimeterX cookie
# name.
_PX_CAPTCHA = dict(
    status=403, error="HTTP 403", failure_class="origin_4xx_block",
    headers={"Content-Type": "text/html"},
    set_cookie=["_pxvid=8f2a3c4d5e6f; Path=/; HttpOnly"],
    html=("<html><head><title>Access to this page has been denied</title></head>"
          "<body><script src=\"/px/captcha.js?a=c&u=1\"></script></body></html>"))


def test_rule_b_control_the_same_captcha_at_tier_1_does_reach_cdp(
        conn, monkeypatch, ran):
    """REWRITTEN 2026-09-04, and the DISCRIMINATOR MOVED because it had to.

    This pair used to turn on whether headed ran. It cannot any more: headed
    is entered on every non-login verdict, so it runs in BOTH halves and the
    pair would have proved nothing while staying green in one half. The rung
    a verdict still gates is cdp -- admitted only on a login wall, or on a
    captcha the headed rung could not clear -- so that is what the pair now
    reads, and Rule B is exactly as observable through it as it was before.
    """
    ran.results["headless"] = tiers.TierResult(ok=False, tier="headless",
                                               **_PX_CAPTCHA)
    _run(conn, monkeypatch, cdp_up=True, max_tier="cdp")
    assert ran.attempted == ["http", "headless", "headed", "cdp"]


def test_rule_b_a_tier_0_captcha_cannot_reach_cdp(conn, monkeypatch, ran):
    """Tier 1 has not been tried yet, and tier 1 is the remedy for a JS challenge.

    Same body, same headers, same status as the control -- one tier lower. Rule
    B makes it a CONTINUE, so no captcha is ever diagnosed in this run and the
    cascade must not end up in the user's own browser or asking him for it.
    """
    ran.results["http"] = tiers.TierResult(ok=False, tier="http", **_PX_CAPTCHA)
    _run(conn, monkeypatch, cdp_up=True, max_tier="cdp")
    assert ran.attempted == ["http", "headless", "headed"]


# ---- plan S3.3: a promotion may not start a fetch above headless ------------

def test_a_promotion_to_headed_starts_at_headless_instead(
        conn, monkeypatch, ran, verdict):
    """A promoted start tier bypasses Rule A entirely -- the loop BEGINS there.

    This is a live hole, not future-proofing: a `401` with `WWW-Authenticate`
    classifies as `origin_4xx_block`, which is persistent, so three of them
    promote the domain a rung. On any box with WEBFETCH_ALLOW_HEADED=1 the
    next fetch then opened a window with no captcha diagnosis and no verdict
    computed at all, because nothing below it ran.
    """
    _promote(conn, 2, "headed")
    _run(conn, monkeypatch)
    # REWRITTEN 2026-09-04. The old form asserted `"headed" not in attempted`,
    # which is now false and was always a stronger claim than the cap makes:
    # the cap governs where a run BEGINS, not where it may climb to. Reaching
    # headed as the third rung, with http and headless tried and a verdict
    # computed, is the cascade working. Beginning there is the defect.
    assert ran.attempted == ["headless", "headed"]
    assert ran.attempted[0] == "headless", (
        "a promotion may not make a windowed rung the START tier")


def test_a_promotion_to_cdp_starts_at_headless_instead(
        conn, monkeypatch, ran, verdict):
    """Same hole at the other windowed rung, reachable once the ceiling rises."""
    _promote(conn, 4, "cdp")
    _run(conn, monkeypatch, cdp_up=True, max_tier="cdp")
    assert ran.attempted == ["headless", "headed"]
    assert ran.attempted[0] == "headless", (
        "a promotion may not make a windowed rung the START tier")
    assert "cdp" not in ran.attempted, (
        "and no verdict in this run admits it from below either")


def test_an_operator_start_tier_headed_survives_the_clamp(
        conn, monkeypatch, ran, verdict):
    """`--start-tier headed` is a direct instruction; a promotion is not.

    The clamp is placed on the blocklist arm alone, so this run still BEGINS at
    headed -- and, because a start tier is not "entered", it begins there
    without a verdict. Rule A then applies from the next rung on.

    """
    verdict.by_tier = {"headed": "CAPTCHA_CHALLENGE"}
    _run(conn, monkeypatch, start_tier="headed")
    assert ran.attempted == ["headed"]
    assert ran.attempted[0] == "headed", (
        "an explicit --start-tier is an instruction, not a promotion")


# ---- plan S3.3: a LOCKED entry is an instruction, not a promotion -----------
#
# The clamp above exists to stop the promotion MACHINE starting a fetch at a
# windowed rung. `locked=1` is not something that machine can produce --
# `record_failure` and `record_success` both refuse a locked row -- so the only
# way a row carries it is a human running the UPDATE that README documents as
# forcing a tier PERMANENTLY. Clamping it would make that command mean
# something other than what it says.

def test_a_LOCKED_entry_starts_at_headed_unclamped(conn, monkeypatch, ran, verdict):
    """The operator's hand-written headed start survives, exactly as --start-tier does.

    And note the recorded consequence rather than hiding it: the first verdict
    computed in this run is headed's OWN. A locked entry may therefore open a
    window with no captcha diagnosis behind it -- which is the same latitude
    --start-tier already has, and is the operator's choice to make.
    """
    _promote(conn, 2, "headed", locked=1)
    _run(conn, monkeypatch)

    assert ran.attempted == ["headed"]
    assert [c["tier"] for c in verdict.calls] == ["headed"], (
        "nothing below headed ran, so no verdict preceded the window")


def test_a_LOCKED_entry_starts_at_cdp_unclamped(conn, monkeypatch, ran, verdict):
    """Same at the other windowed rung, reachable once the ceiling rises."""
    _promote(conn, 4, "cdp", locked=1)
    _run(conn, monkeypatch, cdp_up=True, max_tier="cdp")

    assert ran.attempted == ["cdp"]


def test_failed_explicit_cdp_does_not_descend_to_an_unexecuted_owned_stage(
        conn, monkeypatch, ran, verdict):
    env = _run(conn, monkeypatch, cdp_up=True, start_tier="cdp")

    assert ran.attempted == ["cdp"]
    assert env["next_action"] == "stop"


@pytest.mark.parametrize("selection", ["explicit_start", "locked_blocklist"])
def test_unavailable_selected_cdp_is_a_truthful_ask_accepted_by_inspector(
        conn, monkeypatch, ran, verdict, selection):
    """An operator-selected but absent browser is not a failed page response."""
    if selection == "locked_blocklist":
        _promote(conn, 4, "cdp", locked=1)
    before = state.Blocklist(conn).get("example.com")
    probes = []

    def unavailable(port=9222, timeout=0.5):
        probes.append((port, timeout))
        return False

    monkeypatch.setattr(safety, "validate_url_safety", lambda *a, **kw: None)
    monkeypatch.setattr(safety, "cdp_port_reachable", unavailable)
    monkeypatch.setattr(tiers, "headed_tier_allowed", lambda: True)
    env = fetch.cascade_fetch(
        URL, conn=conn, no_cache=True, search_path="/stub/search.py",
        start_tier="cdp" if selection == "explicit_start" else None,
        max_tier="cdp")

    assert len(probes) == 1
    port, timeout = probes[0]
    assert port == 9222 and 0 < timeout <= 0.5
    assert ran.attempted == []
    assert verdict.calls == [], "no page existed to diagnose"
    assert state.Blocklist(conn).get("example.com") == before
    assert env.get("blocklist_action") is None
    assert conn.execute("SELECT COUNT(*) FROM fetch_log").fetchone()[0] == 0
    assert env["next_action"] == "ask_user"
    assert env.get("error_code") is None
    assert env["error"] and "cdp" in env["error"].lower()
    assert env["content"] == "" and env["synthesized"] is False
    assert "status" not in env and "status_is_real" not in env
    assert all(env.get(key) is None for key in (
        "failure_class", "access_requirement", "login_url", "retry_after"))
    assert env["next_step"].startswith("ASK THE USER")
    assert "--remote-debugging-port=9222" in env["next_step"]
    assert env["warnings"] == [env["next_step"]]
    assert len(env["tier_attempts"]) == 1
    assert env["tier_attempts"][0]["tier"] == "cdp"
    assert "skipped" in env["tier_attempts"][0]
    assert not env["tier_attempts"][0].get("ok")

    history = json.loads(json.dumps(env["inspection_history"]))
    assert len(history) == 1
    observation = history[0]
    assert set(observation) == {
        "tier", "executed", "skip_reason", "verdict", "login_url",
        "retry_after", "access_requirement"}
    assert observation["tier"] == "cdp"
    assert observation["executed"] is False
    assert observation["skip_reason"] == "browser_unavailable"
    assert all(observation[key] is None for key in (
        "verdict", "login_url", "retry_after", "access_requirement"))

    # Feed the emitted JSON history through the real strict consumer, without
    # pretending that either the absent browser or a page fetch executed.
    inspected = fetch.inspect_response(
        {"tier": observation["tier"], "executed": observation["executed"],
         "skip_reason": observation["skip_reason"], "ok": False,
         "requested_url": URL, "final_url": URL, "body": ""},
        history=history, headed_allowed=True, cdp_available=False)
    assert inspected["error_code"] is None
    assert inspected["observation"] == observation
    assert all(inspected[key] is None for key in (
        "verdict", "failure_class", "access_requirement", "login_url", "retry_after"))
    assert history == env["inspection_history"]
    assert len(probes) == 1 and ran.attempted == [] and verdict.calls == []


@pytest.mark.parametrize("tier_idx,tier_name,run_kw", [
    (2, "headed", dict()),
    (4, "cdp", dict(cdp_up=True, max_tier="cdp")),
])
def test_an_UNLOCKED_promotion_to_a_windowed_rung_is_still_capped(
        conn, monkeypatch, ran, verdict, tier_idx, tier_name, run_kw):
    """The control the pair above must not break.

    Exempting locked entries is permissiveness, and a fix that demonstrates only
    permissiveness has demonstrated the wrong half. Under the SAME inputs as
    those two tests, with `locked` back at 0, the cap must still hold -- so this
    goes red if the exemption is ever widened from "locked" to "blocklist".
    """
    _promote(conn, tier_idx, tier_name)
    _run(conn, monkeypatch, **run_kw)

    # REWRITTEN 2026-09-04: the second assertion was `tier_name not in
    # ran.attempted`, which the headed arm now fails for the right reason --
    # headed is an ordinary rung and the cascade climbs to it. The cap's claim
    # is about the LANDING, so that is what is asserted, and it is the same
    # claim for both rungs.
    assert ran.attempted[0] == "headless", (
        "an unlocked promotion may not START the run on %s: %s"
        % (tier_name, ran.attempted))


# ---- the two ROUTES into a windowed rung that the gate never sees -----------
#
# Found 2026-09-03 by a sweep of the 10,560 flag/blocklist combinations, spying
# on `_gate_allows`: 1,536 runs ENTERED a windowed rung with the gate never
# consulted, and 768 of those are named by no latitude in the plan. Both
# mechanisms below are ROUTE faults, not predicate faults -- Rule A's three
# lines were right the whole time and the cascade simply arrived at headed or
# cdp without asking them. That is why neither this file nor
# `test_escalation_verdict.py` saw them, and why the tests here assert the TIER
# SEQUENCE rather than any verdict.
#
# Both are reachable with NO `--start-tier` at all, so they are written that
# way: the hint comes from a `locked` blocklist row, which is the one thing on
# the machine that can name a start tier without anybody typing a flag.


def test_a_clamp_may_not_promote_a_copilot_hint_onto_headed(
        conn, monkeypatch, ran, verdict):
    """`--max-tier headed` deletes copilot, and the clamp used to land ON headed.

    A locked row at COPILOT is an operator saying "always start this domain at
    tier 3". Add `--max-tier headed` and tier 3 is not in the allowed set, so
    `_clamp` runs: nothing allowed sits above the hint, and the old tail arm
    answered `max(allowed)` -- which is headed. A start tier is not "entered",
    so Rule A never sees it, and the window opened with no verdict computed and
    no tier beneath it run. Nobody named headed here: not the operator, not the
    flag.

    THE SECOND HALF IS THE CONTROL, and it is why the ban is aimed at the
    LANDING rather than at the rung. Move that same locked row to headed and
    the run must still begin there -- that is the documented latitude (plan
    S3.3), it leaves `_clamp` on the `hint in allowed` line, and a fix that
    took it away would have narrowed the wrong thing.
    """
    _promote(conn, 3, "copilot", locked=1)
    _run(conn, monkeypatch, max_tier="headed")
    # REWRITTEN 2026-09-04: `== ["headless"]` became `[0] == "headless"`.
    # headless no longer ENDS the run -- the cascade climbs from it to headed,
    # which is the rung working normally. The fault this pins is the LANDING:
    # nobody named headed here, not the operator and not the flag, so the run
    # may not BEGIN there with no verdict computed and nothing tried beneath.
    assert ran.attempted[0] == "headless", (
        "a hint that named copilot must not be clamped up onto headed as the "
        "START tier; headless is the highest rung a clamp may land on: %s"
        % (ran.attempted,))

    ran.attempted.clear()
    _promote(conn, 2, "headed", locked=1)
    _run(conn, monkeypatch, max_tier="headed")
    assert ran.attempted == ["headed"], (
        "a locked row AT headed is the operator's own instruction and is "
        "untouched by the ban")


def test_a_clamp_may_not_promote_a_copilot_hint_onto_cdp(
        conn, monkeypatch, ran, verdict):
    """The other landing, and the more expensive one: the owner's logged-in Chrome.

    `--raw-required` deletes copilot, so the same locked row at tier 3 reaches
    `_clamp` with cdp sitting above it in the allowed set. The old walk-up arm
    answered `min(above)` -- cdp -- and drove the browser holding his logins as
    a START tier, with no diagnosis of any kind behind it.

    Same control as the pair above, at this rung: a locked row AT cdp still
    starts there.
    """
    _promote(conn, 3, "copilot", locked=1)
    _run(conn, monkeypatch, cdp_up=True, max_tier="cdp", raw_required=True)
    # REWRITTEN 2026-09-04, same reason as the headed pair above: the claim is
    # about the landing, and the cascade climbing on from it is not the fault.
    assert ran.attempted[0] == "headless", (
        "the caller's floor gives way rather than STARTING on a rung nobody "
        "named: %s" % (ran.attempted,))
    assert "cdp" not in ran.attempted, (
        "and with no captcha diagnosed, nothing admits it from below either")

    ran.attempted.clear()
    _promote(conn, 4, "cdp", locked=1)
    _run(conn, monkeypatch, cdp_up=True, max_tier="cdp", raw_required=True)
    assert ran.attempted == ["cdp"], (
        "a locked row AT cdp is the operator's own instruction")


@pytest.mark.parametrize("explicit", [False, True])
def test_disabled_copilot_never_hands_cdp_an_ungated_run(
        conn, monkeypatch, ran, verdict, explicit):
    """Explicit selection refuses; a persisted locked slot starts on headless."""
    if not explicit:
        _promote(conn, 3, "copilot", locked=1)
    env = _run(conn, monkeypatch, cdp_up=True, search_path=None,
               **({"start_tier": "copilot"} if explicit else {}))
    if explicit:
        assert ran.attempted == []
        assert env["error_code"] == "disabled" and env["failure_class"] == "other"
    else:
        assert ran.attempted == ["headless", "headed"]
    assert "cdp" not in ran.attempted and "copilot" not in ran.attempted


@pytest.mark.parametrize("search_path", [None, "/dummy/search.py"])
@pytest.mark.parametrize("locked", [0, 1])
def test_reserved_copilot_hint_reaches_cdp_only_on_raw_login_evidence(
        conn, monkeypatch, ran, verdict, search_path, locked):
    """The reserved slot and provider availability confer no browser authority."""
    verdict.by_tier = {"headless": "NEEDS_LOGIN"}
    _promote(conn, 3, "copilot", locked=locked)
    _run(conn, monkeypatch, cdp_up=True, search_path=search_path)
    assert ran.attempted == ["headless", "cdp"]






def test_the_raised_ceiling_does_not_let_an_ordinary_failure_reach_cdp(
        conn, monkeypatch, ran, verdict):
    """THE SAFETY PROPERTY OF THIS CHANGE, and the reason it is safe at all.

    A browser is listening and every tier fails, but no tier ever diagnosed a
    login wall. cdp is now inside the ceiling, so nothing but the gate is
    keeping the cascade out of the owner's browser -- and the gate must hold.
    """
    env = _run(conn, monkeypatch, cdp_up=True)

    assert "cdp" not in ran.attempted, \
        "an ordinary failure must not reach the browser just because the " \
        "ceiling allows it"
    assert env["error"].startswith("cascade_exhausted:"), env["error"]


def test_a_login_verdict_now_actually_reaches_cdp(conn, monkeypatch, ran, verdict):
    """The remedy that was unreachable before the ceiling moved.

    Same verdict, same port state, same gate -- the only thing that changed is
    that cdp is inside the default ceiling. Before this, the run below stopped
    at the diagnosis.
    """
    verdict.by_tier["headless"] = "NEEDS_LOGIN"
    _run(conn, monkeypatch, cdp_up=True)

    assert "cdp" in ran.attempted, \
        "a login wall with a browser listening must now use it"


def test_a_login_verdict_with_no_browser_still_stops_at_the_diagnosis(
        conn, monkeypatch, ran, verdict):
    """Nothing listening: report it, and do NOT fall through to copilot.

    A synthesized summary of a page behind a login is the "synthesized mistaken
    for raw" hazard, so a diagnosis the caller can act on beats an answer
    written from public sources. A deliberate, recorded cost.
    """
    verdict.by_tier["headless"] = "NEEDS_LOGIN"
    env = _run(conn, monkeypatch, cdp_up=False)

    assert "cdp" not in ran.attempted
    assert "copilot" not in ran.attempted, \
        "a login wall must not be answered by synthesis"
    assert env["failure_class"] == "needs_login", env["error"]
