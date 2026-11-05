"""fetch.py's exhaustion block: the ask must fire off the STICKY state.

WHAT THIS FILE PINS, and why it is not covered by the files beside it.
`test_fetch_gate_and_next_step.py` drives one verdict for the whole run, so the
gate and the exhaustion block agree by construction. The defects here only
exist when they DISAGREE -- when a lower rung diagnoses a captcha or a login
wall and a higher rung's verdict comes out CONTINUE, which is the ordinary
shape of a real run:

  * headed CLEARS the challenge markers without getting the page  -> CONTINUE
  * the next allowed rung also returns CONTINUE

Read off `last_verdict`, either can erase a diagnosis. The sticky flags retain
the ask, failure_class and login_url; disabled Copilot never enters the run.

Also pinned here: the instruction must match the situation. A CDP browser that
is up and has already failed must not be "start the CDP browser", and ordinary
exhaustion must neither probe the dedicated browser nor page a person.

NO BROWSER IS EVER LAUNCHED HERE. Every tier function is stubbed before the
cascade can reach it and there is no network in this file.
"""

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


def _verdicts(monkeypatch, by_tier: dict, default: str = "CONTINUE"):
    """PER-TIER verdicts. The whole point of this file: the rungs disagree."""
    monkeypatch.setattr(
        extract, "escalation_verdict",
        lambda **kw: by_tier.get(kw["tier"], default))


def _run(conn, monkeypatch, *, headed_ok=True, cdp_up=False, url=URL, **kw):
    monkeypatch.setattr(tiers, "headed_tier_allowed", lambda: headed_ok)
    monkeypatch.setattr(safety, "cdp_port_reachable", lambda *a, **k: cdp_up)
    kw.setdefault("no_cache", True)
    kw.setdefault("search_path", "/stub/search.py")
    return fetch.cascade_fetch(url, conn=conn, **kw)


# ---- 1. the headline case: a captcha the higher rungs stopped naming --------

def test_a_captcha_cleared_by_headed_without_the_page_still_asks(
        conn, monkeypatch, ran):
    """CRITICAL. This is the sequence `captcha_seen` was made sticky for.

    headless diagnoses the captcha; headed clears the challenge markers and
    still does not get the page, so its verdict is CONTINUE; the CDP browser is
    not running, so cdp is not in the run at all and nothing sets `gate_stop`.
    The exhaustion block keyed off `last_verdict` therefore saw CONTINUE and
    ended the run with no ask, no failure_class and no login_url -- on the one
    shape the whole escalation gate exists to serve.
    """
    _verdicts(monkeypatch, {"headless": "CAPTCHA_CHALLENGE"})
    env = _run(conn, monkeypatch, cdp_up=False)

    assert ran.attempted == ["http", "headless", "headed"], \
        ran.attempted
    step = env.get("next_step") or ""
    assert step.startswith("ASK THE USER"), step
    assert "--remote-debugging-port=9222" in step, step
    assert step in env["warnings"], env["warnings"]
    assert env.get("failure_class") == "captcha_challenge", env


# ---- 2. the login side had the same flaw ------------------------------------

def test_a_login_wall_survives_the_cdp_rung_running_after_it(
        conn, monkeypatch, ran):
    """The login half of the same defect, and it is reached by a DIFFERENT
    route: the NEEDS_LOGIN break only fires when cdp cannot run, so with the
    browser up the cascade climbs THROUGH the diagnosis into cdp -- and cdp's
    own failure verdict became the whole envelope.
    """
    _verdicts(monkeypatch, {"headless": "NEEDS_LOGIN"})
    ran.results["headless"] = tiers.TierResult(
        ok=False, tier="headless", error="login wall",
        failure_class="auth_required", final_url=LOGIN_URL)
    env = _run(conn, monkeypatch, cdp_up=True)

    # headed and copilot are refused on a login verdict; cdp is admitted.
    assert ran.attempted == ["http", "headless", "cdp"], ran.attempted
    assert env.get("failure_class") == "needs_login", env
    assert env.get("login_url") == LOGIN_URL, env


# ---- 3. the CDP browser is up and has already failed ------------------------

def test_a_failed_cdp_captcha_attempt_preserves_the_ask_and_diagnosis(
        conn, monkeypatch, ran):
    """Running the last browser rung cannot discard the consumer's ASK."""
    _verdicts(monkeypatch, {}, default="CAPTCHA_CHALLENGE")
    env = _run(conn, monkeypatch, cdp_up=True)

    assert ran.attempted[-1] == "cdp", ran.attempted
    step = env.get("next_step") or ""
    assert step.startswith("ASK THE USER"), step
    assert env.get("failure_class") == "captcha_challenge", env


# ---- 7. controls: nothing diagnosed must still not page a person ------------

def test_an_ordinary_exhaustion_still_does_not_ask(conn, monkeypatch, ran):
    """A 404, a dead host and a timeout all land here. No browser turns one
    into a page, so none of them may pull a person away from their work."""
    _verdicts(monkeypatch, {})
    def _refuse_probe(*a, **k):
        pytest.fail("ordinary exhaustion does not authorize a CDP probe")

    monkeypatch.setattr(tiers, "headed_tier_allowed", lambda: True)
    monkeypatch.setattr(safety, "cdp_port_reachable", _refuse_probe)
    env = fetch.cascade_fetch(URL, conn=conn, no_cache=True)

    step = env.get("next_step") or ""
    assert not step.startswith("ASK THE USER"), step
    assert env.get("failure_class") is None, env
    assert not [w for w in env["warnings"] if w.startswith("ASK THE USER")]
    assert "cdp" not in ran.attempted
