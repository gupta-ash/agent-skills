"""fetch.py's half of the R1-R5 change set: the gate, the ask, and next_step.

WHAT THIS FILE IS FOR. `test_escalation_cascade.py` pins the ROUTING under the
old policy; this file pins what the owner's five rules changed in `fetch.py`:

    R1  the fetch must succeed -- so a verdict removes a rung only where that
        rung provably cannot help, and every other verdict reaches headed.
    R3  headed and cdp are both IN the cascade and both ENABLED.
    R4  the agent CHECKS the CDP port and ASKS the user to start the browser.
    R5  never fail silently -- every error envelope carries a `next_step`.

`tiers.headed_tier_allowed` is monkeypatched rather than driven through its
environment variable. The variable itself belongs to `tiers.py` and is that
module's test to write; what is being pinned HERE is that fetch.py routes
correctly GIVEN a headed permission, which is true either side of that change.

NO BROWSER IS EVER LAUNCHED HERE. Every tier function is replaced before the
cascade can reach it, and there is no network in this file. (Agents testing on
this machine must never open a headed window; the shipped cascade is a
different question and is exactly what these tests are about.)
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


def _verdict(monkeypatch, value):
    """Every failed tier produces `value`. Isolates routing from the predicate."""
    monkeypatch.setattr(extract, "escalation_verdict", lambda **kw: value)


def _run(conn, monkeypatch, *, headed_ok=True, cdp_up=False, url=URL, **kw):
    monkeypatch.setattr(tiers, "headed_tier_allowed", lambda: headed_ok)
    monkeypatch.setattr(safety, "cdp_port_reachable", lambda *a, **k: cdp_up)
    kw.setdefault("no_cache", True)
    kw.setdefault("search_path", "/stub/search.py")
    return fetch.cascade_fetch(url, conn=conn, **kw)


# ---- R1 + R3: the rungs a verdict may no longer withhold ---------------------

def test_a_captcha_verdict_reaches_headed_then_cdp_without_copilot(conn, monkeypatch, ran):
    """The disabled synthesis rung is a gap; headed/CDP safety stays active."""
    _verdict(monkeypatch, "CAPTCHA_CHALLENGE")
    env = _run(conn, monkeypatch, cdp_up=True)

    assert ran.attempted == ["http", "headless", "headed", "cdp"]
    assert env.get("error", "").startswith("cascade_exhausted:")


def test_a_continue_verdict_still_reaches_headed(conn, monkeypatch, ran):
    """R1 must not depend on the captcha predicate naming the captcha.

    CONTINUE is what the verdict returns for every challenge it cannot
    positively identify, and it used to be the one verdict that guaranteed the
    headed rung never ran. It is the commonest verdict there is, and headed is
    the tier measured to win 26 of 320 URLs on nothing lower.
    """
    _verdict(monkeypatch, "CONTINUE")
    _run(conn, monkeypatch)

    assert "headed" in ran.attempted, ran.attempted
    assert fetch._gate_allows("headed", "CONTINUE") is True


def test_cdp_is_refused_until_a_captcha_was_seen_AND_headed_has_run(
        conn, monkeypatch):
    """The cdp rung takes BOTH halves of the owner's rule 4, not one.

    "If #3 fails, OR for a login requirement, the agent may request the user
    for CDP" -- and #3 is the headed attempt AT A CAPTCHA. So the non-login
    route needs a captcha to have been diagnosed somewhere in the run AND the
    headed rung to have had its turn.

    CORRECTED 2026-09-04, and this test was written asserting the defect. It
    read `headed_attempted=True` alone as sufficient. headed enters on every
    non-login verdict, so that made cdp reachable on EVERY exhausted cascade:
    a 404, a dead host and a timeout each drove the browser holding the user's
    logins, with no captcha anywhere in the run. No browser turns a 404 into a
    page, so it bought R1 nothing and spent the user's own session to do it.

    `captcha_seen` IS STICKY and that is the point the original made correctly:
    read off the LAST verdict, a headed attempt that cleared the challenge
    markers without getting the page would read as "nothing to escalate" and
    the tier succeeding at diagnosis would cost the fetch its last rung.
    """
    assert fetch._gate_allows("cdp", "CAPTCHA_CHALLENGE",
                              headed_attempted=False, captcha_seen=True) is False
    assert fetch._gate_allows("cdp", "CAPTCHA_CHALLENGE",
                              headed_attempted=True, captcha_seen=True) is True
    # THE HALF THE DEFECT LOST: headed ran, nothing was ever diagnosed as a
    # captcha, and that is an ordinary exhausted fetch. It may not reach cdp.
    assert fetch._gate_allows("cdp", "CONTINUE",
                              headed_attempted=True, captcha_seen=False) is False
    # A login verdict admits cdp with no headed attempt at all: a throwaway
    # profile cannot pass a sign-in wall, so there is nothing to wait for.
    assert fetch._gate_allows("cdp", "NEEDS_LOGIN") is True
    # Login still shuts headed; no verdict may enable disabled Copilot.
    assert fetch._gate_allows("headed", "NEEDS_LOGIN") is False
    assert fetch._gate_allows("copilot", "NEEDS_LOGIN") is False
    assert fetch._gate_allows("copilot", "CAPTCHA_CHALLENGE") is False


def test_an_opted_out_box_still_escalates_to_the_cdp_ask(conn, monkeypatch, ran):
    """Headed cannot run here, so `headed_attempted` is SEEDED true.

    Seeded false instead, a captcha on an opted-out machine would find cdp
    refused as well and the run would dead-end with nothing to tell the user.
    """
    _verdict(monkeypatch, "CAPTCHA_CHALLENGE")
    env = _run(conn, monkeypatch, headed_ok=False, cdp_up=True)

    assert "headed" not in ran.attempted
    assert ran.attempted[-1] == "cdp", ran.attempted
    assert fetch.HEADED_SKIP_WARNING in env["warnings"], env["warnings"]


# ---- R4: check the port, then ASK -------------------------------------------

def test_a_login_wall_with_no_cdp_browser_asks_the_user_by_default(
        conn, monkeypatch, ran):
    """NO FLAGS. The default invocation is the one that has to ask.

    The CDP sentence used to be reachable only under --require-cdp, which made
    the flag the thing that unlocked the instruction rather than the thing that
    made a dead port fatal.
    """
    _verdict(monkeypatch, "NEEDS_LOGIN")
    env = _run(conn, monkeypatch, cdp_up=False)

    step = env.get("next_step") or ""
    assert step.startswith("ASK THE USER"), step
    # The same sentence in `warnings`, for a caller that reads only those.
    assert step in env["warnings"], env["warnings"]
    assert env.get("failure_class") == "needs_login"


def test_a_login_wall_with_the_cdp_browser_up_does_not_emit_a_start_ask(
        conn, monkeypatch, ran):
    """An already-running browser does not need the startup ASK protocol."""
    _verdict(monkeypatch, "NEEDS_LOGIN")
    env = _run(conn, monkeypatch, cdp_up=True)

    step = env.get("next_step") or ""
    assert not step.startswith("ASK THE USER"), step


def test_an_unsolved_captcha_with_no_cdp_browser_also_asks(
        conn, monkeypatch, ran):
    """A captcha exhaustion sets no gate_stop, so it used to carry no
    failure_class at all and no instruction of any kind -- a caller had no way
    to tell it from a 404."""
    _verdict(monkeypatch, "CAPTCHA_CHALLENGE")
    env = _run(conn, monkeypatch, cdp_up=False)

    assert env.get("failure_class") == "captcha_challenge", env
    step = env.get("next_step") or ""
    assert step.startswith("ASK THE USER"), step
    assert step in env["warnings"]


@pytest.mark.parametrize("verdict", ["HARD_BLOCK", "RATE_LIMITED"])
def test_terminal_verdicts_without_login_or_captcha_do_not_page_the_user(
        conn, monkeypatch, ran, verdict):
    """Only a diagnosed interactive boundary calls for the user's browser."""
    _verdict(monkeypatch, verdict)
    env = _run(conn, monkeypatch, cdp_up=False)

    step = env.get("next_step") or ""
    assert not step.startswith("ASK THE USER"), step


# ---- R5: never fail silently ------------------------------------------------

@pytest.mark.parametrize("kwargs,url,prefix", [
    ({"allow_private": False}, "http://127.0.0.1/x", "unsafe_url:"),
    ({"start_tier": "not-a-tier"}, URL, "invalid --start-tier"),
    ({"max_tier": "not-a-tier"}, URL, "invalid --max-tier"),
])
def test_every_error_envelope_carries_a_next_step(
        conn, monkeypatch, ran, kwargs, url, prefix):
    """The early exits, end to end. Each returned an `error` and nothing a
    caller could act on."""
    env = _run(conn, monkeypatch, url=url, **kwargs)

    assert env["error"].startswith(prefix), env["error"]
    assert env.get("next_step"), env


@pytest.mark.parametrize("error", [
    "unsafe_url: private address",
    "invalid --start-tier: 'x'",
    "invalid --max-tier: 'x'",
    "no_runnable_tiers: contract excludes every tier",
    "some_exit_invented_next_year: it broke",
])
def test_the_next_step_guarantee_is_structural_not_per_call_site(error):
    """DERIVED IN `_make_envelope`, so an exit added later inherits it.

    The last two rows are the point of the test. `no_runnable_tiers` is
    UNREACHABLE today -- tier 0 is excluded by no flag and no ceiling, so
    `_allowed_for` never returns an empty list -- and the fifth row is an exit
    that does not exist. Neither can be driven through `cascade_fetch`, which
    is exactly why the derivation must not live at the call sites.
    """
    env = fetch._make_envelope(
        url=URL, original_url=None, tier="-", extractor="-", synthesized=False,
        content="", bytes_=0, elapsed_ms=0, pages_read=None, from_cache=False,
        blocklist_action=None, tier_attempts=[], warnings=[], redirect_chain=[],
        error=error)

    assert env.get("next_step"), env


def test_a_successful_envelope_carries_no_next_step():
    """`next_step` is the answer to a failure. On a fetch that worked there is
    nothing to instruct, and an empty-but-present key would make every caller
    test its truthiness rather than its presence."""
    env = fetch._make_envelope(
        url=URL, original_url=None, tier="http", extractor="trafilatura",
        synthesized=False, content="hello", bytes_=5, elapsed_ms=1,
        pages_read=None, from_cache=False, blocklist_action=None,
        tier_attempts=[], warnings=[], redirect_chain=[], status=200)

    assert "next_step" not in env


def test_markdown_format_still_reports_the_diagnosis_and_the_next_step(capsys):
    """`--format markdown` used to print exactly "\\n" for every failed fetch:
    the branch wrote `content`, which is empty on every error envelope, and
    discarded the whole diagnosis -- including the ask meant for a person."""
    env = {
        "content": "",
        "error": "cascade_exhausted: needs_login at http: stub",
        "next_step": fetch._cdp_ask("a login wall"),
        "warnings": [fetch._cdp_ask("a login wall")],
    }
    fetch._emit_envelope(env, "markdown")
    out = capsys.readouterr().out

    assert env["error"] in out, repr(out)
    assert out.count(env["next_step"]) == 1, repr(out)


def test_markdown_format_leaves_a_successful_fetch_alone(capsys):
    """The control. Page text must not grow a diagnosis footer."""
    fetch._emit_envelope({"content": "the article\n", "warnings": []}, "markdown")
    assert capsys.readouterr().out == "the article\n"


# ---- Copilot FAILED / DISABLED: source-owned refusal before effects ----------

DISABLED_REASON = (
    "Copilot disabled: reliable search execution is not qualified (search_not_executed); "
    "diagnostics deferred.")


def _forbid_effect(*args, **kwargs):
    raise AssertionError("disabled Copilot request reached a side-effect boundary")


def _disabled_tripwires(monkeypatch):
    import glob
    import socket

    for module, names in (
        (safety, ("validate_url_safety", "cdp_port_reachable")),
        (state, ("open_db", "bootstrap_if_needed", "apply_rewrites",
                 "compute_cache_key", "Cache", "Blocklist", "FetchLog", "Config")),
        (tiers, ("validate_url_safety", "startup_reap", "reap_zombies",
                 "tier_http", "tier_playwright", "tier_cdp")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, _forbid_effect)
    monkeypatch.setattr(socket, "getaddrinfo", _forbid_effect)
    monkeypatch.setattr(tiers.subprocess, "run", _forbid_effect)
    monkeypatch.setattr(glob, "glob", _forbid_effect)


def _assert_disabled_envelope(env):
    assert env["error"] == DISABLED_REASON
    assert env["error_code"] == "disabled"
    assert env["failure_class"] == "other"
    assert env["content"] == "" and env["synthesized"] is False
    assert env["pages_read"] is None and env["from_cache"] is False
    assert env["blocklist_action"] is None
    assert env["tier_attempts"] == [{
        "tier": "copilot", "ok": False, "elapsed_ms": 0,
        "error": DISABLED_REASON, "error_code": "disabled", "failure_class": "other",
    }]
    assert not {"model", "transport", "status", "status_is_real", "upstream_status"} & env.keys()
    assert env["next_step"]


@pytest.mark.parametrize("options", [
    {"start_tier": "copilot"}, {"max_tier": "copilot"},
    {"copilot_model": "dummy-model"}, {"copilot_model": ""},
    {"start_tier": "copilot", "raw_required": True, "allow_private": True,
     "no_cache": True, "search_path": "/dummy/search.py"},
])
def test_explicit_copilot_refuses_before_url_dns_cache_or_discovery(monkeypatch, options):
    _disabled_tripwires(monkeypatch)
    monkeypatch.setattr(tiers, "tier_copilot", _forbid_effect)
    env = fetch.cascade_fetch("not even a URL", conn=object(), **options)
    _assert_disabled_envelope(env)


@pytest.mark.parametrize("option", [
    ["--start-tier", "copilot"], ["--max-tier", "copilot"],
    ["--copilot-model", "dummy-model"], ["--copilot-model", ""],
])
@pytest.mark.parametrize("fmt", ["json", "markdown"])
@pytest.mark.parametrize("admin", [[], ["--reap-zombies"], ["--clear-cache"], ["--clear-blocklist"]])
def test_main_copilot_refuses_before_db_bootstrap_reaping_or_discovery(
        monkeypatch, capsys, option, fmt, admin):
    import json

    _disabled_tripwires(monkeypatch)
    monkeypatch.setattr(fetch, "cascade_fetch", _forbid_effect)
    monkeypatch.setattr(sys, "argv", ["fetch.py", "--url", "file:///not-allowed",
                                     "--format", fmt, *option, *admin])
    assert fetch.main() == 1
    out = capsys.readouterr().out
    if fmt == "json":
        _assert_disabled_envelope(json.loads(out))
    else:
        assert DISABLED_REASON in out and "next step:" in out


@pytest.mark.parametrize("options", [{}, {"model": "dummy-model", "allow_private": True}])
def test_direct_copilot_tier_refuses_before_dns_or_subprocess(monkeypatch, options):
    _disabled_tripwires(monkeypatch)
    result = tiers.tier_copilot("file:///not-allowed", search_path="/dummy/search.py", **options)
    assert result.ok is False and result.tier == "copilot"
    assert result.error == DISABLED_REASON and result.error_code == "disabled"
    assert result.failure_class == "other"
    assert result.status is None and result.content == "" and result.bytes_ == 0
    assert result.pages_read is None and result.extractor == "-"


@pytest.mark.parametrize("raw_required", [False, True])
@pytest.mark.parametrize("cached_tier,synthesized", [
    ("copilot", False), ("http", True), ("copilot", True),
])
def test_effective_cache_lookup_rejects_each_synthesis_marker_then_runs_raw_tiers(
        conn, monkeypatch, ran, raw_required, cached_tier, synthesized):
    key = state.compute_cache_key(URL, raw_required, "cdp", False)
    state.Cache(conn).put(key, "disabled cached body", cached_tier, synthesized)
    original_get = state.Cache.get
    reads = []

    def get(cache, lookup_key):
        reads.append(lookup_key)
        row = original_get(cache, lookup_key)
        assert row["content"] == "disabled cached body"
        return row

    monkeypatch.setattr(state.Cache, "get", get)
    monkeypatch.setattr(safety, "validate_url_safety", lambda *a, **k: None)
    monkeypatch.setattr(tiers, "tier_copilot", _forbid_effect)
    monkeypatch.setattr(state.Config, "get", _forbid_effect)
    ran.results["headless"] = tiers.TierResult(
        ok=True, tier="headless", content="raw page", bytes_=8, status=200)
    env = _run(conn, monkeypatch, no_cache=False, raw_required=raw_required)
    assert reads == [key]
    assert ran.attempted == ["http", "headless"]
    assert env["content"] == "raw page" and env["tier"] == "headless"
    assert env["synthesized"] is False and env["from_cache"] is False


def test_ordinary_fetch_ignores_model_environment_and_provider_discovery(
        conn, monkeypatch, ran):
    import glob

    original_get = tiers.os.environ.get

    def get(name, *args):
        assert name != "WEBFETCH_COPILOT_MODEL", "ordinary fetch resolved a disabled model"
        return original_get(name, *args)

    monkeypatch.setattr(tiers.os.environ, "get", get)
    monkeypatch.setattr(glob, "glob", _forbid_effect)
    monkeypatch.setattr(state.Config, "get", _forbid_effect)
    monkeypatch.setattr(tiers, "tier_copilot", _forbid_effect)
    monkeypatch.setattr(safety, "validate_url_safety", lambda *a, **k: None)
    _verdict(monkeypatch, "CONTINUE")
    env = _run(conn, monkeypatch, cdp_up=True, search_path="/dummy/search.py")
    assert ran.attempted == ["http", "headless", "headed"]
    assert not any(a["tier"] == "copilot" for a in env["tier_attempts"])


def test_main_ordinary_fetch_passes_no_provider_path_or_model(conn, monkeypatch, capsys):
    import glob

    calls = []
    monkeypatch.setattr(state, "open_db", lambda: conn)
    monkeypatch.setattr(state, "bootstrap_if_needed", lambda *a: None)
    monkeypatch.setattr(tiers, "startup_reap", lambda: 0)
    monkeypatch.setattr(state.Config, "get", _forbid_effect)
    monkeypatch.setattr(glob, "glob", _forbid_effect)
    monkeypatch.setattr(fetch, "cascade_fetch", lambda url, **kw: (calls.append(kw) or {"content": "raw"}))
    monkeypatch.setattr(sys, "argv", ["fetch.py", "--url", URL])
    assert fetch.main() == 0
    assert len(calls) == 1 and calls[0].get("search_path") is None
    assert calls[0].get("copilot_model") is None


def test_disabled_attempt_and_error_are_nonpromoting(conn, monkeypatch):
    monkeypatch.setattr(safety, "validate_url_safety", lambda *a, **k: None)
    monkeypatch.setattr(safety, "cdp_port_reachable", lambda: False)
    # Exercise result-code plumbing independently of the removed Copilot rung.
    monkeypatch.setattr(tiers, "tier_http", lambda *a, **k: tiers.tier_copilot(
        URL, search_path="/dummy/search.py"))
    for _ in range(state.PROMOTE_THRESHOLD + 2):
        env = fetch.cascade_fetch(URL, conn=conn, no_cache=True, max_tier="http")
        assert env["error_code"] == "disabled" and env["failure_class"] == "other"
        assert env["tier_attempts"][0]["error_code"] == "disabled"
        assert env["tier_attempts"][0]["failure_class"] == "other"
        assert env["blocklist_action"] is None
        assert "status" not in env and "status_is_real" not in env
    assert state.Blocklist(conn).get("example.com")["tier_idx"] == 0


@pytest.mark.parametrize("tier", ["http", "headless", "headed", "cdp"])
@pytest.mark.parametrize("status_is_real", [False, True])
def test_raw_success_and_status_provenance_survive_copilot_disable(
        conn, monkeypatch, ran, tier, status_is_real):
    monkeypatch.setattr(safety, "validate_url_safety", lambda *a, **k: None)
    monkeypatch.setattr(tiers, "tier_copilot", _forbid_effect)
    for lower in ("http", "headless", "headed"):
        ran.results[lower] = tiers.TierResult(
            ok=False, tier=lower, status=403, status_is_real=True,
            html="<html><head><title>Just a moment...</title></head><body>Challenge</body></html>",
            headers={"Content-Type": "text/html", "Server": "cloudflare",
                     "cf-mitigated": "challenge"})
    ran.results[tier] = tiers.TierResult(
        ok=True, tier=tier, content="raw page", bytes_=8,
        html="<html><body><main>raw page</main></body></html>",
        headers={"Content-Type": "text/html"},
        status=203, status_is_real=status_is_real)
    env = _run(conn, monkeypatch, cdp_up=True)
    assert env["tier"] == tier and env["content"] == "raw page"
    assert env["status"] == 203 and env["status_is_real"] is status_is_real
    assert env["synthesized"] is False and env["from_cache"] is False
    assert "error" not in env and "error_code" not in env
    assert not any(a["tier"] == "copilot" for a in env["tier_attempts"])


def test_tier_error_code_is_optional_for_existing_producers():
    assert tiers.TierResult(ok=False, tier="http").error_code == ""
