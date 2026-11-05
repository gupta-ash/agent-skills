"""Cache bypass and ASK delivery across promoted domain starts."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import extract
import fetch
import safety
import state
import tiers


# THE HOSTNAME MUST RESOLVE. `validate_url_safety` runs for real here, as it
# does in every file of this suite, and it raises `unsafe_url: DNS resolution
# failed` before the cascade starts on anything under a reserved TLD. A first
# draft of this file used `blocked.example`: no tier ran, `next_step` came from
# the prefix table, and the fail-first arm went red for a reason that had
# nothing to do with the defect. `example.com` is what the rest of the suite
# uses for the same reason.
URL = "https://example.com/paper/2606.14525"
DOMAIN = "example.com"



# ---- harness ----------------------------------------------------------------

class _Tiers:
    """Every tier stubbed: record the name, then fail."""

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
    monkeypatch.setattr(
        extract, "escalation_verdict",
        lambda **kw: by_tier.get(kw["tier"], default))


def _row(conn, tier_idx: int):
    """Seed one unlocked learned domain hint."""
    state.Blocklist(conn)._upsert(DOMAIN, tier_idx, [], 0, 0.0, "seeded by test")
    conn.commit()
    return state.Blocklist(conn).get(DOMAIN)


def _run(conn, monkeypatch, *, headed_ok=True, cdp_up=False, url=URL, **kw):
    monkeypatch.setattr(tiers, "headed_tier_allowed", lambda: headed_ok)
    monkeypatch.setattr(safety, "cdp_port_reachable", lambda *a, **k: cdp_up)
    kw.setdefault("no_cache", True)
    kw.setdefault("search_path", "/stub/search.py")
    return fetch.cascade_fetch(url, conn=conn, **kw)




def test_a_cached_body_makes_start_tier_http_ALONE_skip_the_rung(
        conn, monkeypatch, ran):
    """THE PREMISE BEHIND `--no-cache`, measured instead of reasoned.

    The cache is consulted at Stage 3, before the start tier is read at Stage 4,
    and `compute_cache_key` is keyed on (url, raw_required, max_tier,
    allow_synthesis) with no `start_tier` in it. `allow_synthesis` is always
    false while Copilot is disabled, so fetches collide when they share this
    URL's `--max-tier` and `--raw-required`, not every other fetch. One of
    those landing between the failure and the advised retry comes back without http
    running and without anything being recorded -- which is what would make
    "re-run with --start-tier http" false in both of its promises.

    This asserts the mechanism directly: with an entry in the cache, a run
    asking for `--start-tier http` attempts NO tier at all.
    """
    state.Cache(conn).put(
        state.compute_cache_key(URL, False, "cdp", False),
        "cached body", "http", False)
    conn.commit()
    _verdicts(monkeypatch, {})
    env = _run(conn, monkeypatch, start_tier="http", no_cache=False)

    assert ran.attempted == [], ran.attempted
    assert env.get("from_cache") is True, env
    assert env.get("content") == "cached body", env


def test_the_no_cache_flag_really_BYPASSES_a_POPULATED_cache(
        conn, monkeypatch, ran):
    """THE OTHER HALF OF THE SAME MECHANISM, and the one the shipped advice rests on.

    The sibling above proves a populated cache SKIPS the rung when the cache is
    allowed. This proves `--no-cache` genuinely bypasses the READ, which is what
    makes the exhaustion note's `--no-cache --start-tier http` do anything at
    all. Without it the note instructs an operator to run a flag whose read half
    nothing asserts.

    MEASURED 2026-09-13, on a copy outside every git tree with an unmutated
    control at 1237: deleting `not no_cache and` from the Stage-3 read guard
    left the ENTIRE suite green. That establishes exactly one thing -- no
    former test DISTINGUISHED reading the cache from bypassing it. WHY each
    one failed to is a wider claim the mutation cannot support, so it is not
    made here: a mismatched key, a sensitive URL or a weak assertion would
    each produce the same survival. The guard is correct at `8a2c887`; what
    was missing was any arm that could see it go.

    THE PREMISE IS ASSERTED, NOT ASSUMED. If this key ever stopped matching the
    one the run computes, the cache would simply never be hit and the second
    half would pass having exercised nothing -- green before the guard existed.
    So the same key is warmed with `no_cache=False` first and must be served
    from cache. That warm run is also why `ran.attempted` is still empty when
    the real arm begins.
    """
    key = state.compute_cache_key(URL, False, "cdp", False)
    state.Cache(conn).put(key, "cached body", "http", False)
    conn.commit()
    _verdicts(monkeypatch, {})

    # PREMISE: this key IS the one the run reads. Without this the arm below is
    # green on a cache that was never consulted.
    warm = _run(conn, monkeypatch, start_tier="http", no_cache=False)
    assert warm.get("from_cache") is True, warm
    assert warm.get("content") == "cached body", warm
    assert ran.attempted == [], ran.attempted

    # THE ARM: same populated cache, `--no-cache`, and the rungs must RUN.
    env = _run(conn, monkeypatch, start_tier="http", no_cache=True)
    assert ran.attempted == ["http", "headless", "headed"], ran.attempted
    assert env.get("from_cache") is False, env
    assert env.get("content") != "cached body", env




def test_the_note_rides_an_ASK_without_duplicating_it_in_warnings(
        conn, monkeypatch, ran):
    """THE INTERACTION THAT COULD HAVE BROKEN SOMETHING ELSE.

    `next_step` starting with ASK THE USER is mirrored into `warnings`, and the
    in-loop captcha ask can already have put that exact sentence there. Append
    the note BEFORE the mirror and the suffixed string is "not in warnings", so
    the ask lands a second time in a near-identical copy. The note is therefore
    appended AFTER the mirror: `warnings` carries the ask exactly once and
    unchanged, `next_step` carries the ask plus the note.
    """
    _row(conn, state.TIERS.index("headless"))
    _verdicts(monkeypatch, {"headless": "CAPTCHA_CHALLENGE"})
    env = _run(conn, monkeypatch, cdp_up=False)

    step = env.get("next_step") or ""
    asks = [w for w in env["warnings"] if w.startswith("ASK THE USER")]
    assert step.startswith("ASK THE USER"), step
    assert len(asks) == 1, env["warnings"]
    assert step.startswith(asks[0]), (step, asks[0])


# ---- 2. the controls that must NOT move -------------------------------------

def test_the_MARKDOWN_output_prints_the_ask_once_not_twice(
        conn, monkeypatch, ran):
    """THE REGRESSION THE NOTE ITSELF CAUSED, one layer up from `warnings`.

    `_emit_envelope`'s markdown branch skipped a warning it had already shown
    by testing `warning != step`. That equality was correct while `next_step`
    and its `warnings` copy were one literal. The note makes `next_step` the ask
    PLUS a suffix, so the test became true on exactly the path it guards and the
    whole ask printed a second time -- measured at 2 occurrences before the fix.
    The dedupe is a PREFIX test now.

    FOUND BY READING THE CONSUMER, not by a review: three cross-family rounds
    did not carry `_emit_envelope` in their packet, because the span shipped was
    `cascade_fetch` and the emitter lives below it.
    """
    import io
    import contextlib

    _row(conn, state.TIERS.index("headless"))
    _verdicts(monkeypatch, {"headless": "CAPTCHA_CHALLENGE"})
    env = _run(conn, monkeypatch, cdp_up=False)
    assert (env.get("next_step") or "").startswith("ASK THE USER"), env.get("next_step")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fetch._emit_envelope(env, "markdown")
    out = buf.getvalue()

    assert out.count("ASK THE USER") == 1, out


