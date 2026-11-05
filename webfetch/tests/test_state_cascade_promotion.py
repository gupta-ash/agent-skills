"""state.py: a promotion streak counts CASCADES, and a 404/410 never promotes.

Two defects in `Blocklist.record_failure`, separable, both R1 (the fetch must
succeed).

(a) THE PROMOTION REGRESSION. `record_failure` fires once per TIER ATTEMPT and
    the streak counted ENTRIES, so ONE fetch of ONE URL supplied a whole
    PROMOTE_THRESHOLD streak by itself: http, headless and headed each appended
    `origin_4xx_block` inside a single cascade, and the domain was promoted off
    tier 0 by fetch 1. Tier 0 is the only tier that can read a PDF (pypdf) or
    return a verbatim non-HTML body, so one 404 on a docs host cost every later
    PDF and every later API response on that host. One fetch of one URL is ONE
    piece of evidence about a domain, however many rungs it climbed.

(b) A 404 OR 410 MUST NOT PROMOTE AT ALL. No browser turns a missing page into
    a present one, so promoting on it spends the higher tiers -- and, at the top
    of the cascade, asks the owner to start a CDP browser -- over a page that is
    simply not there. A 401/403 is different: that can be a wall a browser
    clears, so those still promote, on a genuine multi-fetch streak.

Hermetic: an in-memory SQLite blocklist and a loopback origin. No browser, no
external network.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import http.server
import sqlite3
import threading

import pytest

import extract
import state
import tiers


# ---- loopback origin (same shape as test_http_status_reporting) --------------

@pytest.fixture()
def origin():
    """Serve a fixed status on 127.0.0.1. Yields a `url_for(status)` callable."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status = int(self.path.strip("/") or "200")
            body = f"<html><title>t</title><body>status {status}</body></html>".encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    yield lambda status: f"http://127.0.0.1:{port}/{status}"
    srv.shutdown()
    srv.server_close()


def _fresh_blocklist():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(state._DDL)
    return state.Blocklist(conn)


# The classes ONE cascade records against a URL the origin walls with a 4xx,
# measured against this tree: http and the browser tiers see the status and
# classify `origin_4xx_block`; copilot fails its output contract and classifies
# the transient `other`.
_RUNGS = ((0, "origin_4xx_block"), (1, "origin_4xx_block"), (2, "origin_4xx_block"),
          (3, "other"), (4, "origin_4xx_block"))


def _real_reasons(origin, status):
    """The reason strings the REAL producers emit for `status`, not literals.

    `fetch.py` hands `record_failure` `result.error[:200]`, and those errors are
    written in exactly two places: `tier_http`'s HTTPError branch (tier 0) and
    `is_real_content`'s non-2xx branch (every browser tier). Both are taken from
    the live code here so that a reworded message fails this file rather than
    silently restoring 404 promotion.
    """
    tier0 = tiers.tier_http(origin(status), allow_private=True).error
    browser = extract.is_real_content("some extracted text", "<html></html>", status)[1]
    return tier0, browser


def _run_cascade(bl, domain, tier0_reason, browser_reason):
    """Drive one whole fetch of one URL through the rungs, as `fetch.py` does.

    Starts at the domain's CURRENT tier and climbs, and the start rung is read
    ONCE at the top -- `fetch.py` computes `start_idx` before the loop, so a
    promotion made mid-cascade does not move the cascade already running.
    """
    row = bl.get(domain)
    start = row["tier_idx"] if row else 0
    outcomes = []
    for tier_idx, cls in _RUNGS:
        if tier_idx < start:
            continue
        if cls == "other":
            reason = "copilot output not JSON"
        else:
            reason = tier0_reason if tier_idx == 0 else browser_reason
        outcomes.append(bl.record_failure(domain, tier_idx, cls, reason=reason))
    return outcomes


def _tier_of(bl, domain):
    row = bl.get(domain)
    return state.TIERS[row["tier_idx"]]


# ---- (a) a streak counts cascades -------------------------------------------

def test_one_cascade_is_one_piece_of_evidence(origin):
    """The regression itself. One 4xx-walled fetch must move nothing.

    Before the fix the http, headless and headed rungs of this single cascade
    each appended `origin_4xx_block`, the entry streak hit PROMOTE_THRESHOLD
    inside the one fetch, and the domain left tier 0 -- taking pypdf and every
    verbatim non-HTML body with it.
    """
    bl = _fresh_blocklist()
    t0, br = _real_reasons(origin, 403)
    outcomes = _run_cascade(bl, "walled.example", t0, br)
    assert "promoted" not in outcomes, f"one cascade promoted the domain: {outcomes}"
    assert _tier_of(bl, "walled.example") == "http"


def test_a_403_wall_promotes_one_rung_per_FETCH_and_only_past_the_threshold(origin):
    """The control from the other side: a genuine multi-fetch streak still
    promotes, and it promotes ONCE per fetch rather than once per rung.

    PROMOTE_THRESHOLD is 3, so fetches 1 and 2 are evidence and fetch 3 is the
    one that moves the domain. Every fetch after that moves it one further rung
    while the wall persists, which is the ladder that gets a walled domain to a
    tier that can serve it (R1).
    """
    bl = _fresh_blocklist()
    t0, br = _real_reasons(origin, 403)
    seen = []
    for _ in range(5):
        _run_cascade(bl, "walled.example", t0, br)
        seen.append(_tier_of(bl, "walled.example"))
    assert seen == ["http", "http", "headless", "headed", "copilot"], seen


def test_a_cascade_promotes_at_most_one_rung(origin):
    """Stated on its own because it is the half a `streak` fix can miss. Once
    the threshold is met the streak stays met, so without a per-cascade guard
    EVERY remaining rung of the promoting cascade would promote again and the
    single fetch would walk the domain to the top of the ladder anyway."""
    bl = _fresh_blocklist()
    t0, br = _real_reasons(origin, 403)
    _run_cascade(bl, "walled.example", t0, br)
    _run_cascade(bl, "walled.example", t0, br)
    outcomes = _run_cascade(bl, "walled.example", t0, br)
    assert outcomes.count("promoted") == 1, outcomes
    assert _tier_of(bl, "walled.example") == "headless"


def test_entries_written_by_the_previous_schema_group_as_one_cascade():
    """Rows already on disk carry `ts` and `tier_idx` and nothing else new, so
    the grouping must be derivable from those two alone. Three legacy entries
    from ONE old cascade plus one fresh fetch is two pieces of evidence, not
    four."""
    bl = _fresh_blocklist()
    import time as _time
    now = _time.time()
    legacy = [{"ts": now - 30 + i, "class": "cloudflare_challenge", "tier_idx": i}
              for i in range(3)]
    bl._upsert("legacy.example", 0, legacy, 0, now, "seed")
    outcome = bl.record_failure("legacy.example", 0, "cloudflare_challenge")
    assert outcome is None, "one old cascade plus one fetch is 2 cascades, not 4"
    assert _tier_of(bl, "legacy.example") == "http"


# ---- (b) 404 and 410 never promote ------------------------------------------

@pytest.mark.parametrize("status", [404, 410])
def test_a_missing_page_never_promotes_the_domain(origin, status):
    """No browser turns a missing page into a present one. Five whole cascades
    of it must leave the domain exactly where it started -- otherwise a dead URL
    spends the browser tiers and ends up asking the owner to start Chrome."""
    bl = _fresh_blocklist()
    t0, br = _real_reasons(origin, status)
    for _ in range(5):
        outcomes = _run_cascade(bl, "gone.example", t0, br)
        assert "promoted" not in outcomes, f"a {status} promoted: {outcomes}"
    assert _tier_of(bl, "gone.example") == "http"


def test_the_reason_strings_this_module_parses_are_the_ones_the_tiers_produce(origin):
    """A 404 and a 403 arrive under the SAME failure class -- `classify_failure`
    files 401/402/403/451 and 404/410 alike as `origin_4xx_block` -- so the only
    signal state.py has for telling them apart is the reason text `fetch.py`
    passes. Pin both producers, so a reworded error fails here loudly instead of
    quietly restoring 404 promotion."""
    tier0, browser = _real_reasons(origin, 404)
    assert tier0.startswith("HTTP 404 "), tier0
    assert browser == "non-2xx status: 404", browser
    assert state._reason_status(tier0) == 404
    assert state._reason_status(browser) == 404
    assert state._reason_status("HTTP 403 Forbidden") == 403
    assert state._reason_status(None) is None
    assert state._reason_status("unsafe redirect to http://x/404") is None, (
        "the match must be anchored: a 404 elsewhere in a message is not a status")


def test_a_404_does_not_ride_a_403s_streak(origin):
    """The two share a class name, so the missing page must not be counted as
    evidence for the wall. Two 403 fetches, then a 404 fetch: the 404 is not the
    third piece of evidence for a wall a browser could clear."""
    bl = _fresh_blocklist()
    walled = _real_reasons(origin, 403)
    gone = _real_reasons(origin, 404)
    _run_cascade(bl, "mixed.example", *walled)
    _run_cascade(bl, "mixed.example", *walled)
    outcomes = _run_cascade(bl, "mixed.example", *gone)
    assert "promoted" not in outcomes, outcomes
    assert _tier_of(bl, "mixed.example") == "http"


# ---- (c) a cascade is IDENTIFIED, and a success is EVIDENCE ------------------
#
# Two findings that share one root: `state.py` was never told where one fetch
# ended and the next began, nor that a fetch had SUCCEEDED. It inferred the
# first from a rising `tier_idx` plus a clock (`_same_cascade`), and it was not
# told the second at all -- `record_success` wrote `recent_failures` back
# untouched, so a clean fetch left no mark in the history the streak is counted
# from.
#
# MEASURED against the unfixed code before any of this was written, each arm
# with a control that came out DIFFERENT:
#   two CONCURRENT 3-rung fetches grouped as 4 cascades   (sequential control: 2)
#   five clean fetches between failing ones did not stop the promotion
#     (control: the same shape aged past the window did not promote)
#   a demotion was undone by the very next failing fetch


def _entry(ts, tier_idx, cls="cloudflare_challenge", cid=None, status=None):
    """One `recent_failures` row. `cid` omitted entirely when None, so these
    fixtures can express a pre-change row as well as a current one."""
    e = {"ts": ts, "class": cls, "tier_idx": tier_idx, "status": status}
    if cid is not None:
        e["cid"] = cid
    return e


def test_two_concurrent_fetches_are_two_cascades_not_four():
    """Two concurrent fetches are two cascades not four."""
    a, b = "cascade-a", "cascade-b"
    interleaved = [
        _entry(0.0, 0, cid=a), _entry(0.5, 0, cid=b),
        _entry(1.0, 1, cid=a), _entry(1.5, 1, cid=b),
        _entry(2.0, 2, cid=a), _entry(2.5, 2, cid=b),
    ]
    groups = state._cascades(interleaved)
    assert len(groups) == 2, (
        "two concurrent fetches produced %d cascades: %s"
        % (len(groups), [[e["tier_idx"] for e in g] for g in groups]))
    assert [e.get("cid") for e in groups[0]] == [a, a, a], groups[0]
    assert [e.get("cid") for e in groups[1]] == [b, b, b], groups[1]

    # CONTROL, and it passes on both sides of this change: run the same two
    # fetches one after the other and the OLD adjacency rule gets them right
    # too. So the arm above is measuring interleaving, not merely the presence
    # of an identifier.
    sequential = [
        _entry(0.0, 0, cid=a), _entry(1.0, 1, cid=a), _entry(2.0, 2, cid=a),
        _entry(10.0, 0, cid=b), _entry(11.0, 1, cid=b), _entry(12.0, 2, cid=b),
    ]
    assert len(state._cascades(sequential)) == 2


def test_the_last_group_ends_with_the_entry_just_appended():
    """An invariant `record_failure` DEPENDS on, stated where it can fail.

    Its per-cascade guard reads `groups[-1][:-1]` to ask whether THIS fetch has
    already promoted. That is only meaningful while `groups[-1][-1]` is the
    entry just appended -- and grouping by identifier, unlike grouping by
    adjacency, could reorder the groups. Interleaving is the case that would
    break it: the newest entry belongs to the cascade that STARTED second.
    """
    a, b = "cascade-a", "cascade-b"
    interleaved = [
        _entry(0.0, 0, cid=a), _entry(0.5, 0, cid=b),
        _entry(1.0, 1, cid=a), _entry(1.5, 1, cid=b),
    ]
    groups = state._cascades(interleaved)
    assert groups[-1][-1] is interleaved[-1], (
        "the group holding the newest entry must sort last, or the "
        "already-promoted guard reads the wrong cascade")


def test_a_run_of_successes_breaks_a_promotion_streak(origin):
    """Five fetches in which the domain served us perfectly must count.

    Before this change they counted for nothing: `record_success` never wrote
    the failure history, so the streak walked straight past them and the third
    failing fetch promoted the domain as though the successes had not happened.
    """
    bl = _fresh_blocklist()
    t0, br = _real_reasons(origin, 403)

    _run_cascade(bl, "recovered.example", t0, br)
    _run_cascade(bl, "recovered.example", t0, br)
    for _ in range(state.DEMOTE_THRESHOLD):
        bl.record_success("recovered.example", 0)
    outcomes = _run_cascade(bl, "recovered.example", t0, br)
    assert "promoted" not in outcomes, (
        "%d clean fetches did not break the streak: %s"
        % (state.DEMOTE_THRESHOLD, outcomes))
    assert _tier_of(bl, "recovered.example") == "http"

    # CONTROL THAT MUST FIRE. The identical three fetches with no successes
    # between them DO promote -- otherwise the arm above would pass just as
    # well against a build that had stopped promoting altogether.
    bl2 = _fresh_blocklist()
    _run_cascade(bl2, "still-walled.example", t0, br)
    _run_cascade(bl2, "still-walled.example", t0, br)
    control = _run_cascade(bl2, "still-walled.example", t0, br)
    assert "promoted" in control, control
    assert _tier_of(bl2, "still-walled.example") == "headless"


def test_a_demotion_is_not_undone_by_one_later_failing_fetch(origin):
    """A domain demoted on five clean fetches must not be put straight back by
    a single failure, on evidence those successes should have retired."""
    bl = _fresh_blocklist()
    t0, br = _real_reasons(origin, 403)
    d = "seesaw.example"

    for _ in range(state.PROMOTE_THRESHOLD):
        _run_cascade(bl, d, t0, br)
    assert _tier_of(bl, d) == "headless", "setup: the domain should have promoted"

    for _ in range(state.DEMOTE_THRESHOLD):
        bl.record_success(d, bl.get(d)["tier_idx"])
    assert _tier_of(bl, d) == "http", "setup: five successes should have demoted"

    outcomes = _run_cascade(bl, d, t0, br)
    assert "promoted" not in outcomes, (
        "one failing fetch undid a demotion earned over %d successes: %s"
        % (state.DEMOTE_THRESHOLD, outcomes))
    assert _tier_of(bl, d) == "http"


def test_a_fetch_that_failed_low_and_succeeded_high_still_counts_as_evidence(origin):
    """THE OVER-CORRECTION CONTROL, and the reason a success is not simply
    treated as a reset.

    http failed, headless failed, headed got the page: that fetch SUCCEEDED,
    and starting higher next time is exactly the lesson the blocklist exists to
    learn. Its success entry belongs to the same cascade as its failures, so
    that cascade still votes. A fix that let any success break a streak would
    strand these domains at tier 0 for good.

    This one cannot be run against the unfixed code -- it passes `cascade_id`,
    which did not exist there -- so it is a control on the fixed behaviour
    rather than a fail-first regression, and says so rather than implying
    otherwise.
    """
    bl = _fresh_blocklist()
    t0, br = _real_reasons(origin, 403)
    d = "clears-at-headed.example"
    for i in range(state.PROMOTE_THRESHOLD):
        cid = "fetch-%d" % i
        bl.record_failure(d, 0, "origin_4xx_block", reason=t0, cascade_id=cid)
        bl.record_failure(d, 1, "origin_4xx_block", reason=br, cascade_id=cid)
        bl.record_success(d, 2, cascade_id=cid)
    assert _tier_of(bl, d) == "headless", (
        "a fetch that succeeded at a higher rung must still be evidence for "
        "starting higher")


def test_a_history_holding_both_legacy_and_identified_entries_groups_correctly():
    """Every box's live state.db carries rows written before this change, and
    they arrive with no identifier. They must keep grouping by the old rule
    while new rows group by theirs -- three legacy entries from one old cascade
    plus one identified 3-rung fetch is TWO cascades, not four and not one."""
    import time as _time
    now = _time.time()
    mixed = [
        _entry(now - 30, 0), _entry(now - 29, 1), _entry(now - 28, 2),
        _entry(now, 0, cid="new"), _entry(now + 1, 1, cid="new"),
        _entry(now + 2, 2, cid="new"),
    ]
    groups = state._cascades(mixed)
    assert len(groups) == 2, [[e["tier_idx"] for e in g] for g in groups]
    assert all(e.get("cid") is None for e in groups[0]), groups[0]
    assert all(e.get("cid") == "new" for e in groups[1]), groups[1]


def test_a_legacy_cascade_interleaved_with_an_identified_one_is_not_split():
    """Round 1's must-fix. Two fetches must cast TWO votes, not three.

    A no-id cascade and an identified one running at the same time interleave
    their entries. Grouping must keep the no-id rungs together across the
    identified entries sitting between them -- otherwise one legacy fetch
    becomes two votes toward PROMOTE_THRESHOLD and a domain promotes a whole
    fetch early, which spends tier 0: the only rung that reads a PDF or returns
    a JSON body verbatim.

    Reachable across a deploy, and only then: a no-id entry is written by a
    process that loaded the previous `state.py`, and every harness process on a
    box shares one state.db.
    """
    import time as _time
    now = _time.time()
    interleaved = [
        _entry(now, 0),                     # old-code fetch, rung 0
        _entry(now + 0.1, 0, cid="new"),    # new-code fetch, rung 0
        _entry(now + 1.0, 1),               # old-code fetch, rung 1
        _entry(now + 1.1, 1, cid="new"),    # new-code fetch, rung 1
    ]
    groups = state._cascades(interleaved)
    assert len(groups) == 2, (
        "two interleaved fetches produced %d cascades: %s"
        % (len(groups), [[(e["tier_idx"], e.get("cid")) for e in g] for g in groups]))
    legacy = [g for g in groups if all(e.get("cid") is None for e in g)]
    assert len(legacy) == 1 and len(legacy[0]) == 2, (
        "the no-id cascade was split: %s" % legacy)


def test_an_interleaved_deploy_window_does_not_promote_a_domain_early():
    """The consequence, and the arm that DISCRIMINATES.

    Seeding the four interleaved entries above is TWO fetches. Adding a third
    fetch would promote under the broken and the fixed grouping alike and prove
    nothing, so this adds another rung to a fetch that is already in the list --
    still two fetches, so any promotion here is premature by construction.

    Split grouping reads three cascades and promotes. Correct grouping reads
    two, which is below PROMOTE_THRESHOLD.
    """
    import time as _time
    bl = _fresh_blocklist()
    now = _time.time()
    interleaved = [
        _entry(now, 0), _entry(now + 0.1, 0, cid="new"),
        _entry(now + 1.0, 1), _entry(now + 1.1, 1, cid="new"),
    ]
    bl._upsert("deploying.example", 0, interleaved, 0, now, "seed")
    outcome = bl.record_failure("deploying.example", 2, "cloudflare_challenge",
                                cascade_id="new")
    assert outcome != "promoted", (
        "two fetches promoted the domain: the interleaved no-id cascade was "
        "counted twice")
    assert _tier_of(bl, "deploying.example") == "http"

    # CONTROL THAT MUST FIRE: a genuine THIRD fetch, on the same seeded history,
    # does promote. Without this the arm above would pass just as well against a
    # build that had stopped promoting altogether.
    bl2 = _fresh_blocklist()
    bl2._upsert("control.example", 0, interleaved, 0, now, "seed")
    bl2.record_failure("control.example", 2, "cloudflare_challenge", cascade_id="new")
    third = bl2.record_failure("control.example", 0, "cloudflare_challenge",
                               cascade_id="third-fetch")
    assert third == "promoted", (
        "a genuine third fetch did not promote -- the arm above proves nothing")


def test_interleaved_legacy_entries_are_merged_known_residual():
    """A KNOWN RESIDUAL, pinned so it is visible rather than latent. This
    asserts what the code CURRENTLY does and is NOT an endorsement of it.

    Once a no-id entry interleaves with an identified one, the no-id entries
    cannot be attributed to a fetch. The four entries below are ONE legacy
    fetch straddling an identified one, or TWO legacy fetches either side of
    it, and nothing in the stored fields tells them apart -- review round 1
    asked for one reading and round 2 for the other, on input that compares
    equal.

    The tie goes to MERGING, and not because under-counting is cheap: it can
    miss a promotion outright, since the streak walk breaks at the first
    non-voting cascade. It is the right way round because the costs differ in
    KIND. The blocklist sets only the START rung, so an unpromoted domain still
    climbs every rung inside each fetch and pays latency; a domain promoted
    early loses tier 0 -- the only rung that reads a PDF or returns a JSON body
    verbatim -- with no way back down inside a run. If you are here because this
    test failed, read the trade in `_cascades`' docstring before changing the
    behaviour, and if you change it deliberately, delete this test rather than
    inverting it.

    The window is bounded in TIME, not in number: every fetch process already
    running when the deploy lands keeps the old module for the rest of its life,
    and CC, Codex and omp invoke this skill concurrently, so there can be as
    many stale writers as there were fetches in flight. No NEW one appears after
    the deploy, and the entries age out of PROMOTE_WINDOW_S.
    """
    import time as _time
    now = _time.time()
    ambiguous = [
        _entry(now, 0),                     # a no-id fetch, rung 0
        _entry(now + 0.1, 0, cid="c"),      # an identified fetch, rung 0
        _entry(now + 1.0, 1),               # no-id: same fetch as the first, or another?
        _entry(now + 1.1, 1, cid="c"),      # the identified fetch, rung 1
    ]
    groups = state._cascades(ambiguous)
    assert len(groups) == 2, (
        "the residual changed: %s"
        % [[(e["tier_idx"], e.get("cid")) for e in g] for g in groups])
    legacy = [g for g in groups if all(e.get("cid") is None for e in g)]
    assert len(legacy) == 1 and len(legacy[0]) == 2, (
        "the no-id entries are merged into one cascade by design: %s" % legacy)
