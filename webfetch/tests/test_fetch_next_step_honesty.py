"""fetch.py's exhaustion block: the instruction must be TRUE, not merely present.

R5 is not satisfied by a sentence. An instruction that is FALSE is worse than
none, and these are the five ways this block said something untrue:

  1. A TERMINAL VERDICT ERASED A DIAGNOSIS THE RUN HAD ALREADY EARNED. The
     chain tested HARD_BLOCK and RATE_LIMITED first and unconditionally, so a
     captcha diagnosed at headless followed by the 503 an origin serves a
     client that failed its challenge reported a rate limit and nothing else --
     no ask, and the captcha gone from the envelope. R4 acts on that diagnosis.
  2. THE GENERIC ARM OFFERED A RETRY THE GATE WOULD REFUSE. `_gate_allows`
     admits cdp only on a login wall or a diagnosed captcha, so on a
     CONTINUE-only run "start the CDP browser and retry" prescribes a
     byte-identical run -- and names a browser that may already be running.
  3. IT OFFERED A BROWSER FOR A DOCUMENT NO BROWSER READS. On the
     `skip_browser_tiers` path the cascade drops the browser rungs precisely
     because a PDF with no text layer defeats all of them.
  4. IT CLAIMED THE HEADED BROWSER HAD TRIED WHEN THE RUNG NEVER EXECUTED.
     `headed_attempted` is SEEDED TRUE when headed cannot run at all, which is
     right for the gate (R1) and the opposite of the truth for the wording.
  5. `cdp_browser_up` WENT STALE UNDER --require-cdp. The unreachable branch
     broke out without recording the dead port, so the envelope whose error is
     "CDP unreachable at :9222" asserted the CDP browser was running.

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
    """PER-TIER verdicts: the rungs of one run disagree, which is the point."""
    monkeypatch.setattr(
        extract, "escalation_verdict",
        lambda **kw: by_tier.get(kw["tier"], default))


def _run(conn, monkeypatch, *, headed_ok=True, cdp_up=False, url=URL,
         probe=None, **kw):
    monkeypatch.setattr(tiers, "headed_tier_allowed", lambda: headed_ok)
    monkeypatch.setattr(safety, "cdp_port_reachable",
                        probe or (lambda *a, **k: cdp_up))
    kw.setdefault("no_cache", True)
    kw.setdefault("search_path", "/stub/search.py")
    return fetch.cascade_fetch(url, conn=conn, **kw)


# ---- 1. a terminal verdict may not erase the diagnosis before it -------------

def test_a_rate_limit_after_a_captcha_reports_BOTH_and_still_asks(
        conn, monkeypatch, ran):
    """THE REPRODUCED DEFECT, default invocation, browser down.

    http returns a Cloudflare 403; headless is served the challenge document
    and diagnoses CAPTCHA_CHALLENGE; headed is then served the 503 +
    Retry-After that an origin gives a client which failed its challenge, which
    verdicts RATE_LIMITED. Testing the terminal verdict first and
    unconditionally, the envelope reported a rate limit, emitted no ask, and the
    captcha vanished -- and the captcha is what rule 4 acts on.

    A rate limit is recorded and carried to exhaustion; Copilot is now
    disabled, but the report still owes both diagnoses and the CDP ask.
    """
    _verdicts(monkeypatch, {"headless": "CAPTCHA_CHALLENGE",
                            "headed": "RATE_LIMITED"})
    ran.results["headed"] = tiers.TierResult(
        ok=False, tier="headed", status=503, error="HTTP 503",
        failure_class="origin_5xx_transient", headers={"Retry-After": "60"})
    env = _run(conn, monkeypatch, cdp_up=False)

    assert ran.attempted == ["http", "headless", "headed"], ran.attempted
    step = env.get("next_step") or ""
    assert env.get("retry_after") == "60", env
    assert step.startswith("ASK THE USER"), step
    assert step in env["warnings"], env["warnings"]


def test_a_hard_block_after_a_login_wall_reports_both_without_asking(
        conn, monkeypatch, ran):
    """The other terminal verdict, and it does NOT become an ask.

    A rate limit is transient, so a retry is a real route and a browser started
    before it is genuinely used. A hard block is not: the cascade breaks at the
    rung that found it and a retry breaks there again, so no browser the user
    starts is ever reached. Both facts have to survive into the envelope --
    the block that stopped the run, and the login wall diagnosed before it.
    """
    _verdicts(monkeypatch, {"headless": "NEEDS_LOGIN", "cdp": "HARD_BLOCK"})
    ran.results["headless"] = tiers.TierResult(
        ok=False, tier="headless", error="login wall",
        failure_class="auth_required", final_url=LOGIN_URL)
    env = _run(conn, monkeypatch, cdp_up=True)

    assert ran.attempted == ["http", "headless", "cdp"], ran.attempted
    step = env.get("next_step") or ""
    assert env.get("failure_class") == "hard_block", env
    assert env.get("login_url") == LOGIN_URL, env
    assert not step.startswith("ASK THE USER"), step


# ---- 2. the generic arm may not offer a retry the gate would refuse ----------

@pytest.mark.parametrize("cdp_up", [False, True])
def test_an_ordinary_exhaustion_does_not_offer_a_cdp_retry(
        conn, monkeypatch, ran, cdp_up):
    """CONTINUE everywhere: `_gate_allows` refuses cdp on this run and on the
    identical run a retry would produce. Offering it prescribes a byte-identical
    fetch -- and with the port up it names a browser already running."""
    _verdicts(monkeypatch, {})
    env = _run(conn, monkeypatch, cdp_up=cdp_up)

    step = env.get("next_step") or ""
    assert not step.startswith("ASK THE USER"), step




# ---- 3. a document no browser reads is not answered with a browser ----------

@pytest.mark.parametrize("cdp_up", [False, True])
def test_a_document_level_failure_is_not_answered_with_a_browser(
        conn, monkeypatch, ran, cdp_up):
    """`skip_browser_tiers` removes headless and headed BECAUSE no browser
    reads this document. fetch.py already suppresses HEADED_SKIP_WARNING here
    for exactly that reason; next_step must carry the same reasoning.

    Note what the two arms said before: port down, an ASK naming a captcha the
    CDP browser cannot help with; port up, "retry with --max-tier cdp" on a run
    whose ceiling is ALREADY cdp and whose gate refuses the rung anyway,
    because the headed rung it needs was the one skipped.
    """
    ran.results["http"] = tiers.TierResult(
        ok=False, tier="http", error="pdf has no text layer",
        failure_class="empty_content", skip_browser_tiers=True)
    _verdicts(monkeypatch, {"http": "CAPTCHA_CHALLENGE"})
    env = _run(conn, monkeypatch, cdp_up=cdp_up)

    assert ran.attempted == ["http"], ran.attempted
    step = env.get("next_step") or ""
    assert not step.startswith("ASK THE USER"), step
    assert not [w for w in env["warnings"] if w.startswith("ASK THE USER")], \
        env["warnings"]




# ---- 5. availability is probed once, only after eligibility -----------------

def test_require_cdp_probes_once_then_preserves_the_actual_cdp_failure(
        conn, monkeypatch, ran):
    """A single eligibility probe is not a second browser/navigation retry."""
    calls = {"n": 0}

    def _probe(*a, **k):
        calls["n"] += 1
        return calls["n"] == 1

    _verdicts(monkeypatch, {"headless": "CAPTCHA_CHALLENGE"})
    env = _run(conn, monkeypatch, probe=_probe, require_cdp=True)

    step = env.get("next_step") or ""
    assert step.startswith("ASK THE USER"), step
    assert calls["n"] == 1
    assert ran.attempted == ["http", "headless", "headed", "cdp"]
    assert [o["tier"] for o in env["inspection_history"]] == ran.attempted


