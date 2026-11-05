"""Slice D -- a rate limit is not a reason to PROMOTE A DOMAIN (spec S3.6).

The title said "not a reason to climb" until 2026-09-04 and that was two
claims wearing one sentence. The BLOCKLIST one is slice D's and is unchanged:
three rate limits must not walk a domain's permanent start tier up toward the
browser rungs. The CASCADE one -- that a rate limit ends this run where it is
found -- was measured false and is gone; the last section of this file is what
replaced it.

Four facts, and they are separable:

  * `classify_failure(429)` returns `rate_limited`, a class in NEITHER
    promotion set, so three rate limits leave the domain where it is instead of
    walking it a rung toward the browser tiers. `record_failure` tests
    membership of PERSISTENT_FAILURE_CLASSES and nothing else, so a class in
    neither set is transient at no cost -- TRANSIENT_FAILURE_CLASSES is
    declaration-only and is read nowhere in the skill.
  * The origin's own `Retry-After`, WHEN IT SENT ONE, reaches the caller in the
    envelope. When it sent none there is no field: a default would be a number
    the origin never named.
  * The envelope names the diagnosis for ALL THREE gate stops, not only the
    `needs_login` one slice C wired.
  * A rate limit does NOT end the cascade. NOT because it is always a fact
    about the client that met it -- that universal was withdrawn in review
    round 1, and an origin keying its quota by source IP puts every tier in
    one bucket. It climbs because the costs are asymmetric: stopping loses the
    fetch permanently when the limit IS client-specific, climbing costs further
    rungs in one cascade when it is not. Last section.

The promotion assertions run against the REAL `state.Blocklist`, not a stub --
the whole claim is about what the blocklist does with the class, and a stubbed
blocklist would assert only that a string changed.

NO BROWSER AND NO NETWORK (CONSTRAINTS C-3/C-4): every tier function is
replaced before the cascade can reach it, and the blocklist is in-memory
SQLite.
"""

import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import extract
import fetch
import safety
import state
import tiers


# ---- the class, and what the blocklist does with it -------------------------

def _fresh_blocklist():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(state._DDL)
    return state.Blocklist(conn)


HEADLESS = state.TIERS.index("headless")


def test_429_is_classified_rate_limited():
    """The class itself. Was `origin_4xx_block`, which is PERSISTENT."""
    assert extract.classify_failure(429, "") == "rate_limited"


def test_the_429_class_counts_toward_no_promotion_and_is_not_merely_transient():
    """Derived from the function, never from the literal.

    Asserting `"rate_limited" not in PERSISTENT_FAILURE_CLASSES` on the bare
    string would pass against the unfixed code too -- a name absent from a set
    proves nothing. What matters is the class the code RETURNS for a 429.
    """
    cls = extract.classify_failure(429, "")
    assert cls not in state.PERSISTENT_FAILURE_CLASSES, (
        f"{cls!r} counts toward promotion, so a rate limit walks the domain "
        f"toward a tier the user can see")
    assert cls not in state.TRANSIENT_FAILURE_CLASSES, (
        f"spec S3.6: a NEW class, in neither set -- {cls!r} is an existing one")


def test_three_consecutive_429s_do_not_promote_a_domain():
    """The whole point of the slice, against the real `record_failure`.

    Recorded at `headless`, so the promotion this prevents is the one that puts
    a window on the user's screen: headless -> headed.
    """
    bl = _fresh_blocklist()
    cls = extract.classify_failure(429, "")
    actions = [bl.record_failure("example.com", HEADLESS, cls) for _ in range(3)]

    assert actions == [None, None, None], (
        f"three rate limits promoted the domain: {actions}")
    landed = state.TIERS[bl.get("example.com")["tier_idx"]]
    assert landed == "headless", (
        f"the domain now starts at {landed!r}; no window clears a rate limit")


def test_the_control_three_consecutive_403s_still_DO_promote():
    """Proof the assertion above can fail.

    Same blocklist, same tier, same call count -- only the class differs. If
    this ever goes green-by-inertia the test above is measuring nothing. 403
    keeps `origin_4xx_block` deliberately: spec S3.6 is about 429 alone.
    """
    bl = _fresh_blocklist()
    cls = extract.classify_failure(403, "<html><body>Forbidden</body></html>")
    assert cls == "origin_4xx_block"
    actions = [bl.record_failure("example.com", HEADLESS, cls) for _ in range(3)]

    assert actions == [None, None, "promoted"]
    assert state.TIERS[bl.get("example.com")["tier_idx"]] == "headed"


# ---- the cascade harness ----------------------------------------------------
#
# Same shape as test_escalation_cascade.py's, and stubbed for the same reason:
# a red test here must mean the ENVELOPE is wired wrong, never that a needle in
# `escalation_verdict` moved.

class _Tiers:
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
    def __init__(self):
        self.by_tier: dict[str, str] = {}

    def __call__(self, **kw):
        return self.by_tier.get(kw.get("tier"), "CONTINUE")


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


def _run(conn, monkeypatch, **kw):
    monkeypatch.delenv(tiers.HEADED_TIER_DISABLE_ENV, raising=False)
    monkeypatch.setattr(safety, "cdp_port_reachable", lambda *a, **k: False)
    kw.setdefault("no_cache", True)
    kw.setdefault("search_path", "/stub/search.py")
    return fetch.cascade_fetch(URL, conn=conn, **kw)


def _stop_at_headless(ran, verdict, name, **result_kw):
    verdict.by_tier = {"headless": name}
    result_kw.setdefault("status", 429)
    # NOT the verdict: the tier's own class, so an envelope that echoed the
    # last tier's classification instead of the terminal verdict is visible.
    result_kw.setdefault("failure_class", "empty_content")
    ran.results["headless"] = tiers.TierResult(
        ok=False, tier="headless", error="stub", **result_kw)


# ---- Retry-After in the envelope --------------------------------------------

@pytest.mark.parametrize("header_name", ["Retry-After", "retry-after", "RETRY-AFTER"])
def test_a_retry_after_reaches_the_envelope_in_any_casing(
        conn, monkeypatch, ran, verdict, header_name):
    """`TierResult.headers` carries the ORIGIN's casing and does not normalise.

    A plain `headers.get("retry-after")` finds nothing on most origins, which is
    the same trap `_content_type` was written for.
    """
    _stop_at_headless(ran, verdict, "RATE_LIMITED", headers={header_name: "120"})
    env = _run(conn, monkeypatch)

    assert env.get("retry_after") == "120", (
        f"the origin sent {header_name}: 120 and the caller cannot see it")


def test_no_retry_after_header_means_no_field_at_all(
        conn, monkeypatch, ran, verdict):
    """Absent header, absent field. A default would be a number we invented.

    Passes against the unfixed code, where the field never exists at all --
    declared, not evidence. It guards the fix in the other direction: the day
    someone gives `retry_after` a fallback, this is what goes red.
    """
    _stop_at_headless(ran, verdict, "RATE_LIMITED",
                      headers={"Content-Type": "text/html"})
    env = _run(conn, monkeypatch)

    assert "retry_after" not in env, f"invented a wait: {env.get('retry_after')!r}"


def test_a_hard_block_does_not_surface_a_retry_after(
        conn, monkeypatch, ran, verdict):
    """The boundary, pinned deliberately (plan S8.9).

    HARD_BLOCK is decided BEFORE RATE_LIMITED precisely because a 451 is a legal
    block that no amount of waiting clears, so a `Retry-After` riding along on
    one is not a wait worth reporting. Same declaration as the test above: it
    passes on both revisions and exists to stop the field widening later.
    """
    _stop_at_headless(ran, verdict, "HARD_BLOCK", status=451,
                      headers={"Retry-After": "3600"})
    env = _run(conn, monkeypatch)

    assert "retry_after" not in env


# ---- the terminal verdict, machine-readable ---------------------------------

@pytest.mark.parametrize("name,expected,attempted", [
    ("NEEDS_LOGIN", "needs_login", ["http", "headless"]),
    ("RATE_LIMITED", "rate_limited", ["http", "headless", "headed"]),
    ("HARD_BLOCK", "hard_block", ["http", "headless"]),
])
def test_the_envelope_names_the_terminal_verdict_for_every_gate_stop(
        conn, monkeypatch, ran, verdict, name, expected, attempted):
    """Slice C set `failure_class` on the NEEDS_LOGIN stop only; the other two
    reported their reason in the error string and nowhere a caller can branch
    on. `needs_login` is included as the control -- it passes on both revisions,
    and its presence is what says the other two were completing one mechanism
    rather than adding a second.

    The stubbed result carries `failure_class="empty_content"`, so an envelope
    that echoed the last tier's own class instead of the verdict fails here.

    THE RUNG LIST IS NOW PER-VERDICT, 2026-09-04. It was one shared
    `["http", "headless"]` under the assertion message "the stop must end the
    cascade", which is no longer true of RATE_LIMITED: it is recorded and
    climbed past rather than breaking the loop, because stopping loses the
    fetch permanently whenever the limit turns out to be client-specific.
    (NOT because it always is -- that universal was withdrawn in round 1.) Parametrized rather than dropped, because the claim
    the row still makes is the one this test is FOR -- the class reaches the
    envelope -- and it now also pins that climbing past the limit does not cost
    the caller the machine-readable diagnosis.
    """
    _stop_at_headless(ran, verdict, name)
    env = _run(conn, monkeypatch)

    assert ran.attempted == attempted, ran.attempted
    assert env.get("failure_class") == expected



MARRIOTT_HEADERS = {
    "Server": "AkamaiGHost",
    "Content-Type": "text/html",
    "Retry-After": "28800",
    "Akamai-GRN": "0.962d3e17.1788583923.dddab3c3",
}

# Every rung the cascade can run with nothing on the DevTools port -- which is
# what `_run` stubs. Asserted as a LIST, not as `"headed" in attempted`: the
# claim is that the code above the limit was ENTERED, and a membership test
# would stay green against a cascade that skipped one of them.
FULL_CLIMB = ["http", "headless", "headed"]


def _rate_limit_at(ran, verdict, tier, *, status=403, error="HTTP 403 Forbidden",
                   headers=None, also=None):
    """One rung verdicts RATE_LIMITED; every other rung fails ordinarily.

    `failure_class` is the tier's OWN class and deliberately not `rate_limited`
    -- the same trick `_stop_at_headless` uses: an envelope that echoed the last
    tier's classification instead of the promoted verdict is visible here.
    """
    verdict.by_tier = dict(also or {})
    verdict.by_tier[tier] = "RATE_LIMITED"
    ran.results[tier] = tiers.TierResult(
        ok=False, tier=tier, status=status, error=error,
        failure_class="empty_content",
        headers=dict(MARRIOTT_HEADERS) if headers is None else headers)


def test_a_tier0_rate_limit_does_not_end_the_cascade(
        conn, monkeypatch, ran, verdict):
    """marriott.com, exactly as measured: it died at http and never tried a
    browser at all. A urllib 403 is not evidence about headless Chrome."""
    _rate_limit_at(ran, verdict, "http")
    _run(conn, monkeypatch)

    assert ran.attempted == FULL_CLIMB, (
        f"a rate limit at tier 0 stopped the cascade: {ran.attempted}")


def test_a_tier1_rate_limit_still_reaches_headed(
        conn, monkeypatch, ran, verdict):
    """hyatt.com: 403 to urllib, 429 to headless. Dying at headless is dying
    one rung below the window that clears an interactive challenge, and two
    below the CDP ask."""
    _rate_limit_at(ran, verdict, "headless", status=429, error="HTTP 429",
                   headers={"Retry-After": "60"})
    _run(conn, monkeypatch)

    assert ran.attempted == FULL_CLIMB, (
        f"a rate limit at tier 1 stopped the cascade: {ran.attempted}")


def test_the_carried_rate_limit_is_still_the_reported_diagnosis(
        conn, monkeypatch, ran, verdict):
    """The half that must NOT change. A caller loses nothing by the cascade
    climbing: the class, the origin's own wait and the wait-then-retry
    instruction are what the break produced, byte for byte."""
    _rate_limit_at(ran, verdict, "http")
    env = _run(conn, monkeypatch)

    assert ran.attempted == FULL_CLIMB, ran.attempted
    assert env.get("failure_class") == "rate_limited", env
    assert env.get("retry_after") == "28800", env


def test_a_hard_block_is_still_terminal(conn, monkeypatch, ran, verdict):
    """CONTROL, and it passes on BOTH revisions by design.

    HARD_BLOCK keeps the break this change removes from RATE_LIMITED, and its
    own reasoning is untouched: a 451 is a legal block found again at the same
    rung on a retry. If this ever goes red, the change widened past its scope.
    """
    _stop_at_headless(ran, verdict, "HARD_BLOCK", status=451)
    env = _run(conn, monkeypatch)

    assert ran.attempted == ["http", "headless"], ran.attempted
    assert env.get("failure_class") == "hard_block", env


def test_a_later_hard_block_outranks_a_carried_rate_limit(
        conn, monkeypatch, ran, verdict):
    """PRECEDENCE: the later diagnosis wins, and it may not drag a wait with it.

    The rate limit was measured on the urllib client; the hard block was
    measured one rung up, by a client that got further. Reporting the older,
    weaker answer would tell the caller to wait for something no wait clears --
    and a `retry_after` beside a `hard_block` is exactly the pairing
    `test_a_hard_block_does_not_surface_a_retry_after` exists to forbid.
    """
    _rate_limit_at(ran, verdict, "http", also={"headless": "HARD_BLOCK"})
    ran.results["headless"] = tiers.TierResult(
        ok=False, tier="headless", status=451, error="HTTP 451",
        failure_class="empty_content")
    env = _run(conn, monkeypatch)

    assert ran.attempted == ["http", "headless"], ran.attempted
    assert env.get("failure_class") == "hard_block", env
    assert "retry_after" not in env, env.get("retry_after")
    assert env["error"] == (
        "cascade_exhausted: hard_block at headless: HTTP 451"), env["error"]


def test_the_first_rate_limit_is_reported_and_a_later_one_cannot_erase_the_wait(
        conn, monkeypatch, ran, verdict):
    """TWO rate limits, and only the first carried a `Retry-After`.

    Recording the LAST one would replace the origin's own eight-hour wait with
    nothing -- losing the actionable half of the diagnosis on a run that
    diagnosed it twice. The first rung is also the one the cascade used to
    break at, so the envelope reads exactly as it did before.
    """
    _rate_limit_at(ran, verdict, "http", also={"headed": "RATE_LIMITED"})
    ran.results["headed"] = tiers.TierResult(
        ok=False, tier="headed", status=429, error="HTTP 429",
        failure_class="empty_content", headers={"Content-Type": "text/html"})
    env = _run(conn, monkeypatch)

    assert ran.attempted == FULL_CLIMB, ran.attempted
    assert env.get("retry_after") == "28800", env
    assert env["error"].startswith(
        "cascade_exhausted: rate_limited at http:"), env["error"]


def test_the_gate_admits_headed_on_a_rate_limit_and_refuses_cdp_without_a_captcha():
    """Rate limits may reach headed but neither enable Copilot nor ungated CDP."""
    assert fetch._gate_allows("headed", "RATE_LIMITED") is True
    assert fetch._gate_allows("copilot", "RATE_LIMITED") is False
    assert fetch._gate_allows("cdp", "RATE_LIMITED",
                              headed_attempted=True, captcha_seen=False) is False
    assert fetch._gate_allows("cdp", "RATE_LIMITED",
                              headed_attempted=True, captcha_seen=True) is True


# --------------------------------------------------------------------------
# Review round 1 (gpt-5.6-sol, contract code-fleet, 2026-09-05). Two must-fix
# highs, both about what the PROMOTED record carries rather than about whether
# the cascade climbs. Both arms below are red on the revision that shipped the
# climb and green after the repair.
# --------------------------------------------------------------------------


def test_a_later_retry_after_is_kept_when_the_first_limit_carried_none(
        conn, monkeypatch, ran, verdict):
    """F1. First-wins discarded the only actionable figure in the run.

    An origin can rate limit at tier 0 with a bare 429 and, at the next rung,
    say how long to wait. Under a pure first-wins rule the header-less record
    is kept, `retry_after` is absent from the envelope, and the caller is told
    to wait "a few minutes" while the origin had named its own wait. The mirror
    rule loses the opposite case, so the rule is: keep the earliest record,
    unless a later one carries a wait the earliest did not.
    """
    _rate_limit_at(ran, verdict, "http", status=429, error="HTTP 429",
                   headers={})                       # no Retry-After at all
    _rate_limit_at(ran, verdict, "headless", status=429, error="HTTP 429",
                   headers={"Retry-After": "1800"},
                   also={"http": "RATE_LIMITED"})    # keep tier 0's verdict

    env = _run(conn, monkeypatch)

    assert ran.attempted == FULL_CLIMB, ran.attempted
    assert env.get("failure_class") == "rate_limited", env
    assert env.get("retry_after") == "1800", (
        f"the only Retry-After in the run was discarded: {env.get('retry_after')!r}")


def test_the_promoted_rate_limit_reports_its_own_status_not_a_later_rungs(
        conn, monkeypatch, ran, verdict):
    """F2. `failure_class` came from the promoted record, `status` from the
    rung that ran LAST -- two fields describing two different responses.

    A tier-0 429 followed by an upper-tier 500 reported `rate_limited` beside
    `status: 500`, which is a wrong report rather than a lost fetch: a caller
    keying off the status retries a server error that never happened.
    """
    _rate_limit_at(ran, verdict, "http", status=429, error="HTTP 429",
                   headers={"Retry-After": "600"})
    ran.results["headed"] = tiers.TierResult(
        ok=False, tier="headed", status=500,
        error="HTTP 500 Internal Server Error", failure_class="empty_content")

    env = _run(conn, monkeypatch)

    assert ran.attempted == FULL_CLIMB, ran.attempted
    assert env.get("failure_class") == "rate_limited", env
    assert env["status"] == 429, (
        f"the promoted rate limit was paired with another rung's status: "
        f"{env['status']}")
    assert env.get("retry_after") == "600", env




def test_a_promoted_rate_limit_carries_its_own_redirect_chain(
        conn, monkeypatch, ran, verdict):
    """H2. `redirect_chain` came from whichever rung ran LAST while the tier,
    error and status came from the promoted record -- one envelope presenting
    two different responses as though they were one failure.

    A caller reading the chain believes the rate-limited response arrived at
    that final URL. It did not; a later rung went there.
    """
    # These are synthetic response URLs; this case tests response provenance,
    # not DNS reachability or the safety validator's independent stop policy.
    monkeypatch.setattr(safety, "validate_url_safety", lambda *a, **k: None)
    _rate_limit_at(ran, verdict, "http", status=429, error="HTTP 429",
                   headers={"Retry-After": "600"})     # no redirects
    ran.results["headed"] = tiers.TierResult(
        ok=False, tier="headed", status=500, error="HTTP 500",
        failure_class="empty_content",
        redirect_chain=["https://example.com/", "https://elsewhere.example/x"])

    env = _run(conn, monkeypatch)

    assert env.get("failure_class") == "rate_limited", env
    # `.get` with a default: the envelope OMITS an empty chain, so the fixed
    # state has no key at all and `env[...]` would KeyError on success.
    assert env.get("redirect_chain", []) == [], (
        f"the promoted diagnosis carried another rung's redirect chain: "
        f"{env.get('redirect_chain')}")


def test_a_promoted_rate_limit_with_no_status_does_not_borrow_one(
        conn, monkeypatch, ran, verdict):
    """H1. The repair keyed 'was anything promoted?' on `promoted_status is not
    None`, so a promoted record whose own status is None read as NO PROMOTION
    and the envelope substituted the last rung's status.

    Latent rather than live today -- every transport that supplies headers also
    supplies a status -- but it is a sentinel that cannot represent a legitimate
    value, and the next transport that reports a limit without one arms it
    silently. The fix is a separate flag, not a magic value.
    """
    _rate_limit_at(ran, verdict, "http", status=None, error="rate limited",
                   headers={"Retry-After": "600"})
    ran.results["headed"] = tiers.TierResult(
        ok=False, tier="headed", status=500, error="HTTP 500",
        failure_class="empty_content")

    env = _run(conn, monkeypatch)

    assert env.get("failure_class") == "rate_limited", env
    # Same reason: a null status is omitted, so the fixed state has no key.
    assert env.get("status") is None, (
        f"a promoted null status borrowed another rung's: {env.get('status')}")


def test_a_login_wall_is_not_reported_as_a_rate_limit_when_cdp_ran(
        conn, monkeypatch, ran, verdict):
    """Review round 3, the one regression THIS change introduced.

    A `NEEDS_LOGIN` deliberately does NOT set `gate_stop` when the cdp rung is
    going to run -- it falls through so cdp gets its turn (`and not
    cdp_will_run`). Promotion then saw an unset `gate_stop` and put the carried
    rate limit in, so a login-walled run that had ALREADY driven the CDP browser
    and lost reported `failure_class: rate_limited` and advised waiting out the
    limit "so the CDP browser can be used" -- on a run where it had just been.
    The actionable instruction, sign in by hand, was lost.

    `not login_seen` is the guard: the precedence rule was always "a later
    terminal diagnosis wins", and a login seen above the limit is exactly that.
    Every other test in this file pins `cdp_port_reachable` False, which is why
    the whole path was invisible to the suite.
    """
    _rate_limit_at(ran, verdict, "http", status=429, error="HTTP 429",
                   headers={"Retry-After": "600"})
    verdict.by_tier["headless"] = "NEEDS_LOGIN"
    monkeypatch.delenv(tiers.HEADED_TIER_DISABLE_ENV, raising=False)
    monkeypatch.setattr(safety, "cdp_port_reachable", lambda *a, **k: True)

    env = fetch.cascade_fetch(URL, conn=conn, no_cache=True,
                              search_path="/stub/search.py")

    assert "cdp" in ran.attempted, ran.attempted
    assert env.get("failure_class") != "rate_limited", (
        f"a login wall was reported as a rate limit: {env.get('failure_class')}")
