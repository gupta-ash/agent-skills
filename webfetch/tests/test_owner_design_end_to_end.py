"""The owner's design, driven END TO END through `cascade_fetch` in the REAL
environment. No predicate is stubbed out of the picture except the tier
functions themselves, and no environment variable is set.

WHY THIS FILE EXISTS ALONGSIDE `test_fetch_gate_and_next_step.py`. That file
monkeypatches `tiers.headed_tier_allowed` to a lambda, which is the right
isolation for pinning ROUTING given a permission -- and it means not one test
in it can tell a working default from a rung nobody can reach. The owner's
ruling is ABOUT the default:

    "headed or CDP should not be disabled. it should be included. Agent should
     check if CDP browser is running, if not it should ask the user to start it.
     Never never never fail silently. #1 priority is that the agent succeeds.
     #2 is that the user is not disrupted."

So every run below clears BOTH variables and asserts they are clear, then reads
the real `headed_tier_allowed`. A gate reintroduced anywhere between the
environment and the cascade turns this file red and leaves the other one green.

NO BROWSER IS EVER LAUNCHED HERE. `tier_playwright`, `tier_copilot` and
`tier_cdp` are replaced before the cascade can reach them, and
`safety.cdp_port_reachable` is a stub, so the DevTools probe opens no socket.
Testing on a machine somebody is sitting at must never put a window up; that
constraint is about test runs, not about the shipped cascade, which is what
these tests are describing.
"""

import os
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


def _verdict(monkeypatch, value, *, by_tier=None):
    """Script the predicate. Routing is what is under test, not the needles."""
    table = by_tier or {}
    monkeypatch.setattr(
        extract, "escalation_verdict",
        lambda **kw: table.get(kw.get("tier"), value))


def _default_env(monkeypatch):
    """A REAL default environment, asserted rather than assumed.

    Both names are cleared: the opt-out that exists, and the opt-in that was
    retired. The second is cleared so a machine that still exports the old
    variable -- and several on this fleet do -- cannot make these runs pass or
    fail for a reason nobody wrote down.
    """
    monkeypatch.delenv(tiers.HEADED_TIER_DISABLE_ENV, raising=False)
    monkeypatch.delenv("WEBFETCH_ALLOW_HEADED", raising=False)
    assert tiers.HEADED_TIER_DISABLE_ENV not in os.environ
    assert "WEBFETCH_ALLOW_HEADED" not in os.environ


def _run(conn, monkeypatch, *, cdp_up=False, url=URL, **kw):
    _default_env(monkeypatch)
    monkeypatch.setattr(safety, "cdp_port_reachable", lambda *a, **k: cdp_up)
    kw.setdefault("no_cache", True)
    kw.setdefault("search_path", "/stub/search.py")
    return fetch.cascade_fetch(url, conn=conn, **kw)


# ---- 1. a captcha reaches headed with NOTHING set ---------------------------

def test_a_captcha_reaches_headed_with_no_environment_variable_set(
        conn, monkeypatch, ran):
    """The owner's rule 3, through the real predicate for the permission.

    `tiers.headed_tier_allowed` is NOT stubbed here. If a gate returns to the
    environment, to `_allowed_for`, or to `_ensure_open`, this is the test that
    sees it.
    """
    _verdict(monkeypatch, "CAPTCHA_CHALLENGE")
    _run(conn, monkeypatch)

    assert tiers.headed_tier_allowed() is True, \
        "the default environment must permit the rung"
    assert "headed" in ran.attempted, ran.attempted
    assert ran.attempted[:3] == ["http", "headless", "headed"], ran.attempted


# ---- 2. headed failed -> evidence-gated cdp, without synthesis ---------------

def test_a_captcha_headed_could_not_clear_reaches_cdp_without_copilot(
        conn, monkeypatch, ran):
    """Headed's failed challenge attempt opens CDP, not the disabled model rung."""
    _verdict(monkeypatch, "CAPTCHA_CHALLENGE")
    _run(conn, monkeypatch, cdp_up=True, max_tier="cdp")

    assert ran.attempted == ["http", "headless", "headed", "cdp"], \
        ran.attempted


def test_the_cdp_rung_still_needs_a_captcha_and_not_merely_a_failure(
        conn, monkeypatch, ran):
    """THE CONTROL for the test above, and the reason it is not vacuous.

    Same flags, same live port, one difference: nothing diagnosed a captcha.
    That is an ordinary exhausted fetch -- a 404, a dead host, a timeout -- and
    no browser turns one into a page, so it may not end up driving the browser
    holding the user's logins. Without this control the sequence above passes
    just as well against a gate that admits cdp on every exhausted cascade,
    which is what it did when first written.
    """
    _verdict(monkeypatch, "CONTINUE")
    _run(conn, monkeypatch, cdp_up=True, max_tier="cdp")

    assert "cdp" not in ran.attempted, ran.attempted
    assert ran.attempted == ["http", "headless", "headed"], \
        ran.attempted


# ---- 3. a login wall with a dead port ASKS ----------------------------------

def test_a_login_wall_with_a_dead_port_asks_the_user_in_the_warnings(
        conn, monkeypatch, ran):
    """The ASK reaches both supported consumer fields on an ordinary call."""
    _verdict(monkeypatch, "NEEDS_LOGIN")
    env = _run(conn, monkeypatch, cdp_up=False)

    asks = [w for w in env["warnings"] if w.startswith("ASK THE USER")]
    assert len(asks) == 1, env["warnings"]
    ask = asks[0]
    assert "--remote-debugging-port=9222" in ask, ask
    assert env.get("failure_class") == "needs_login", env
    assert env.get("next_step") == ask, env


# ---- 4. the ask survives --format markdown ----------------------------------

def test_the_ask_survives_format_markdown(conn, monkeypatch, ran, capsys):
    """END TO END, and that is the point: the envelope is the one the cascade
    actually produced, not a dict written by the test.

    A hand-built envelope cannot catch a cascade that stops populating
    `next_step`, or that puts the ask somewhere the emitter does not read. This
    runs the real thing and then hands its output to the real emitter.
    """
    _verdict(monkeypatch, "NEEDS_LOGIN")
    env = _run(conn, monkeypatch, cdp_up=False)

    fetch._emit_envelope(env, "markdown")
    out = capsys.readouterr().out

    assert "--remote-debugging-port=9222" in out, repr(out)
    assert env["error"] in out, repr(out)
    # ONE copy. The ask is in `next_step` AND in `warnings`; the emitter must
    # not print it twice, or an agent relaying it verbatim says it twice.
    assert out.count("--remote-debugging-port=9222") == 1, repr(out)


def test_a_captcha_exhaustion_also_survives_format_markdown(
        conn, monkeypatch, ran, capsys):
    """The other route to the ask, and it is the one that carries no gate_stop.

    A captcha exhaustion does not BREAK the cascade -- it runs out of rungs --
    so it took a different path to the envelope and used to arrive carrying
    neither a failure class nor an instruction.
    """
    _verdict(monkeypatch, "CAPTCHA_CHALLENGE")
    env = _run(conn, monkeypatch, cdp_up=False)

    fetch._emit_envelope(env, "markdown")
    out = capsys.readouterr().out

    assert env.get("failure_class") == "captcha_challenge", env
    assert env["next_step"] in out, repr(out)


# ---- 5. R5: no exit returns a failure with nothing to do about it ------------

# Every shape that ends in an `error`, driven through the REAL cascade. The
# grid is the point: R5 is a claim about EVERY exit, so a spot check of the two
# that were fixed is not a test of it.
_FAILING_RUNS = [
    ("a login wall, no browser", dict(verdict="NEEDS_LOGIN", cdp_up=False)),
    ("a login wall, browser up", dict(verdict="NEEDS_LOGIN", cdp_up=True)),
    ("a captcha, no browser", dict(verdict="CAPTCHA_CHALLENGE", cdp_up=False)),
    ("a captcha, browser up", dict(verdict="CAPTCHA_CHALLENGE", cdp_up=True)),
    ("a hard block", dict(verdict="HARD_BLOCK", cdp_up=False)),
    ("a rate limit", dict(verdict="RATE_LIMITED", cdp_up=False)),
    ("an ordinary exhaustion", dict(verdict="CONTINUE", cdp_up=False)),
    ("an ordinary exhaustion, browser up",
     dict(verdict="CONTINUE", cdp_up=True)),
    ("a ceiling at http", dict(verdict="CONTINUE", cdp_up=False,
                               run_kw=dict(max_tier="http"))),
    ("raw required", dict(verdict="CONTINUE", cdp_up=False,
                          run_kw=dict(raw_required=True))),
    ("no copilot binary", dict(verdict="CONTINUE", cdp_up=False,
                               run_kw=dict(search_path=None))),
    ("require-cdp on a dead port",
     dict(verdict="NEEDS_LOGIN", cdp_up=False,
          run_kw=dict(require_cdp=True, max_tier="cdp"))),
    ("an unsafe URL", dict(verdict="CONTINUE", cdp_up=False,
                           run_kw=dict(url="http://127.0.0.1/x",
                                       allow_private=False))),
    ("a bad --start-tier", dict(verdict="CONTINUE", cdp_up=False,
                                run_kw=dict(start_tier="not-a-tier"))),
    ("a bad --max-tier", dict(verdict="CONTINUE", cdp_up=False,
                              run_kw=dict(max_tier="not-a-tier"))),
]


@pytest.mark.parametrize(
    "label,spec", _FAILING_RUNS, ids=[r[0] for r in _FAILING_RUNS])
def test_no_failing_exit_is_silent(conn, monkeypatch, ran, label, spec):
    """Every failing exit provides a separate nonempty next-step field."""
    _verdict(monkeypatch, spec["verdict"])
    env = _run(conn, monkeypatch, cdp_up=spec["cdp_up"],
               **spec.get("run_kw", {}))

    assert env.get("error"), f"{label}: expected a failing run, got {env}"
    step = env.get("next_step") or ""
    assert step, f"{label}: no next step: {env}"
    assert step != env["error"], (
        f"{label}: restating the error is not a next step")


@pytest.mark.parametrize(
    "label,spec", _FAILING_RUNS, ids=[r[0] for r in _FAILING_RUNS])
def test_no_failing_exit_is_silent_in_markdown_either(
        conn, monkeypatch, ran, capsys, label, spec):
    """The same sweep through the HUMAN-READABLE format.

    `--format markdown` wrote `content` and nothing else, and `content` is
    empty on every error envelope -- so every one of these exits printed a
    single newline and the whole diagnosis went in the bin. Sweeping it is what
    makes 'never fail silently' true of the format a person actually reads.
    """
    _verdict(monkeypatch, spec["verdict"])
    env = _run(conn, monkeypatch, cdp_up=spec["cdp_up"],
               **spec.get("run_kw", {}))
    fetch._emit_envelope(env, "markdown")
    out = capsys.readouterr().out

    assert env["error"] in out, f"{label}: {out!r}"
    assert env["next_step"] in out, f"{label}: {out!r}"
