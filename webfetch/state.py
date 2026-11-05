"""state.py - SQLite-backed state for webfetch.

Per project decisions D6 (SQLite, NOT JSON) + D10 (threshold-gated blocklist).
Single file at ~/.local/share/webfetch/state.db, WAL mode for multi-harness concurrency.

Tables: blocklist, cache, fetch_log, rewrites, config.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

# ---- Constants ---------------------------------------------------------------

TIERS = ("http", "headless", "headed", "copilot", "cdp")  # tier_idx 0..4
SCHEMA_VERSION = 3
CACHE_POLICY_VERSION = "access-gate-v1"

# Keep the persisted Copilot slot reserved; it is not a runnable transport.
COPILOT_DISABLED_REASON = (
    "Copilot disabled: reliable search execution is not qualified (search_not_executed); "
    "diagnostics deferred.")

# Failure classes that count toward auto-promotion (D10).
PERSISTENT_FAILURE_CLASSES = frozenset({
    "cloudflare_challenge",
    "origin_4xx_block",
    "js_only_spa",
    "empty_content",
    "copilot_refused",
    "copilot_off_contract",
})

# Failure classes treated as transient — never count toward promotion.
TRANSIENT_FAILURE_CLASSES = frozenset({
    "connect_refused",
    "dns_fail",
    "origin_5xx_transient",
    "timeout",
    "other",
})

# Statuses that must NEVER promote a domain, whatever class they were filed
# under. `classify_failure` files 401/402/403/451 AND 404/410 under the one name
# `origin_4xx_block`, and those are not the same fact: a 401 or a 403 can be a
# wall a browser clears, while NO BROWSER TURNS A MISSING PAGE INTO A PRESENT
# ONE. Promoting on a 404 spends the higher tiers -- and, at the top of the
# cascade, asks the owner to start a CDP browser -- over a page that simply is
# not there. See `_reason_status` for how the status is recovered here and what
# the durable fix would be.
NON_PROMOTING_STATUSES = frozenset({404, 410})

PROMOTE_THRESHOLD = 3            # 3 same-class CASCADES within window
PROMOTE_WINDOW_S = 3600          # 1 hour
DEMOTE_THRESHOLD = 5             # 5 consecutive successes to demote

# The longest gap that can separate two rungs OF ONE CASCADE. `fetch.py` runs
# the tiers one after another inside a single call, so the gap between two
# `record_failure` calls of one cascade is the duration of the tier in between
# -- at most the copilot rung's `max(total_timeout, 60.0)` plus process spawn.
# `--total-timeout` is a caller-set flag, so that duration is NOT bounded by any
# default this file can read.
#
# THE TWO ERRORS ARE NOT SYMMETRIC, and that is what sets the number rather than
# a measurement of a typical rung.
#   TOO SHORT splits one fetch into several cascades, which is exactly the
#   defect this constant exists to prevent -- a raised --total-timeout would
#   quietly restore promotion-per-tier-attempt.
#   TOO LONG merges two fetches into one cascade, costing one fetch of delay
#   before a promotion. And it can only happen at all when the second fetch
#   STARTS above the rung the first one stopped on, which needs the first fetch
#   to have both stopped at its start rung and promoted there -- rare, and
#   self-limiting.
# So this is set long, at 15 minutes.
CASCADE_GAP_S = 900

# Raised from 10 on 2026-09-04, and the fix above is why. The streak counts
# CASCADES now, and one cascade appends up to five entries (one per tier), so a
# 10-entry tail could not hold the PROMOTE_THRESHOLD = 3 cascades a promotion
# needs -- the oldest evidence aged out of the list before it could be counted
# and a walled domain would never promote at all. 30 holds six full cascades.
#
# SINCE 2026-09-05 THIS LIST ALSO HOLDS SUCCESSES and the column name no longer
# says so. Renaming `recent_failures` means a schema migration on four boxes for
# a cosmetic gain, so the name stays and this comment carries the truth. One
# cascade now appends at most one entry per rung plus one success, so 30 still
# holds five full cascades against a PROMOTE_THRESHOLD of 3 -- and a long clean
# run flushes stale failures out of the tail, which is the ageing by EVIDENCE
# this list never had. The one-hour window ages by time and always did.
RECENT_FAILURES_CAP = 30
FETCH_LOG_KEEP = 50000
DEFAULT_CACHE_TTL_S = 15 * 60    # 15 minutes


# ---- Cascade grouping and the status behind a failure class ------------------
#
# ONE FETCH OF ONE URL IS ONE PIECE OF EVIDENCE ABOUT A DOMAIN, however many
# rungs it climbed. `record_failure` fires once per TIER ATTEMPT -- `fetch.py`
# calls it from inside the cascade loop -- so a streak counted in ENTRIES was
# supplied entirely by a single fetch: http, headless and headed each appended
# `origin_4xx_block` and PROMOTE_THRESHOLD was met before the fetch had even
# finished. One 404 then promoted the whole domain off tier 0, which is the only
# tier that can read a PDF (pypdf) or return a verbatim non-HTML body, so every
# later PDF and every later API response on that host paid for it (R1).
#
# The grouping is DERIVED from (ts, tier_idx) rather than stored: both fields
# have been on every entry since the table existed, so rows written by earlier
# versions of this file group correctly too, with no migration.

_REASON_STATUS_RE = re.compile(r"^(?:HTTP|non-2xx status:)\s+(\d{3})\b")


def _reason_status(reason: str | None) -> int | None:
    """Read the HTTP status back off a failure `reason`, or None if it says none.

    THE ONLY SIGNAL AVAILABLE HERE, stated as a limitation rather than dressed
    up. `record_failure` is handed a failure CLASS, and `classify_failure` files
    404, 410, 401, 402, 403 and 451 under the single name `origin_4xx_block` --
    so the class alone cannot tell a missing page from a wall. The status
    survives only inside the reason text, which `fetch.py` passes as
    `result.error[:200]`.

    Two producers, both writing the status at the START of the string:
        `tier_http`         -> "HTTP 404 Not Found"
        `is_real_content`   -> "non-2xx status: 404"   (every browser tier)
    Both are pinned against the live code by
    test_the_reason_strings_this_module_parses_are_the_ones_the_tiers_produce,
    so a reworded message fails a test rather than quietly restoring 404
    promotion. ANCHORED at the start deliberately: a 404 appearing incidentally
    further along an error message -- inside a URL, say -- is not a status.

    An unrecognised reason returns None and is treated as PROMOTING, i.e. the
    behaviour this module had before. Only a positively identified 404 or 410
    loses its vote.

    THE DURABLE FIX IS NOT IN THIS FILE: either `classify_failure` gives 404/410
    a class of their own (extract.py), or `fetch.py` passes the status it
    already holds. Both are owned elsewhere; this reads the evidence that
    actually reaches state.py today.
    """
    if not reason:
        return None
    m = _REASON_STATUS_RE.match(reason.strip())
    return int(m.group(1)) if m else None


def _same_cascade(prev: dict, cur: dict) -> bool:
    """True when `cur` is the next RUNG of the cascade `prev` belongs to.

    One fetch climbs the tiers in order, so within a cascade `tier_idx` strictly
    increases and consecutive entries are one tier attempt apart. A new fetch
    restarts at the domain's own start tier, which is never ABOVE the rung the
    previous cascade stopped on -- except immediately after a promotion, and the
    time bound separates that case.

    SINCE 2026-09-05 THIS IS THE FALLBACK, NOT THE PRIMARY RULE. Entries written
    by the current code carry a cascade id and are grouped by it; this runs only
    for rows that were already on disk when that change deployed. It cannot see
    two CONCURRENT fetches of one domain: their entries interleave, every rise
    crosses from one fetch into the other, and six entries from two fetches
    grouped as four cascades -- twice the votes toward promotion. Measured, with
    a sequential control that grouped correctly.
    """
    try:
        gap = float(cur.get("ts", 0.0)) - float(prev.get("ts", 0.0))
        rose = int(cur.get("tier_idx", -1)) > int(prev.get("tier_idx", -1))
    except (TypeError, ValueError):
        return False
    return rose and 0.0 <= gap <= CASCADE_GAP_S


def _success_entry(ts: float, tier_idx: int, cascade_id: str | None) -> dict:
    """The mark a SUCCESSFUL fetch leaves in `recent_failures`.

    `class` is None deliberately: `_counts_toward` compares it against a failure
    class name, and None can never equal one however that set grows. A cascade
    that failed low and succeeded high still votes, because this entry joins the
    group its failures are already in -- that fetch DID need the higher rung.
    What breaks a streak is a cascade whose only entry is one of these, i.e. a
    fetch that succeeded outright.
    """
    entry = {"ts": ts, "class": None, "tier_idx": tier_idx, "status": None, "ok": 1}
    if cascade_id is not None:
        entry["cid"] = cascade_id
    return entry


def _cascades(recent: list) -> list:
    """Split recent_failures into cascades, oldest first, each a list of entries.

    A cascade is IDENTIFIED wherever it can be: `fetch.py` mints one `cid` per
    `cascade_fetch()` call and every entry that fetch writes carries it, so the
    grouping is exact and survives concurrency. `architecture.md` records that
    CC, Codex and omp invoke this skill at the same time by design.

    Entries with NO `cid` are rows written before 2026-09-05 and fall back to
    `_same_cascade`. No migration is needed: a history of nothing BUT such rows
    groups exactly as it did before this change, and that is every box's
    state.db at the moment this deploys.

    WHERE IT IS NOT EXACT, STATED RATHER THAN CLAIMED AWAY. Once a no-id entry
    and an identified one INTERLEAVE, the no-id entries cannot be attributed to
    a fetch at all. These four

        (tier 0, no id) (tier 0, id=c) (tier 1, no id) (tier 1, id=c)

    are ONE legacy fetch straddling an identified one, or TWO legacy fetches
    either side of it, and the stored fields are byte-identical in both cases.
    No rule reading ts, tier_idx and class can separate them. Measured, after
    two review rounds asked for opposite answers on the same four entries.

    THE AMBIGUITY IS RESOLVED TOWARD MERGING, deliberately -- and NOT because
    under-counting is cheap. It is not.

    Merging can MISS a promotion outright. The streak walk breaks at the first
    cascade that does not vote, so if a success or a different failure class
    arrives before another same-class cascade does, the under-counted evidence
    is gone and the domain needs a fresh run of PROMOTE_THRESHOLD consecutive
    same-class fetches. There is no bound in fetches. An earlier version of this
    paragraph said "delays a promotion by one fetch"; that was false, and a
    review round was spent establishing it.

    IT IS STILL THE RIGHT WAY ROUND, because the two costs differ in KIND rather
    than in size, and that is readable in the code rather than argued.
    `fetch.py` computes `run_idxs = [i for i in allowed_idxs if i >= start_idx]`
    -- the blocklist sets only the START rung. So an unpromoted walled domain
    still climbs every rung inside each fetch and still reaches the tier that
    can serve it: what it pays is LATENCY, on every fetch, until three
    consecutive same-class cascades line up. A domain promoted EARLY loses tier
    0 -- the only rung that reads a PDF or returns a JSON body verbatim -- with
    no way back down inside a run, so later PDFs and API responses on that host
    fail at a capability the promotion took away. Repeated latency that
    self-corrects beats a lost capability that does not.

    Pinned by test_interleaved_legacy_entries_are_merged_known_residual, which
    asserts the current behaviour and disclaims endorsing it.

    THE BOUND IS ON TIME, NOT ON NUMBER. This needs an old-code process still
    writing. Every fetch process already running when a deploy lands keeps the
    module it loaded for the rest of its life, and CC, Codex and omp invoke this
    skill concurrently -- so there can be as many stale writers as there were
    fetches in flight at that moment, NOT one. (An earlier version of this
    paragraph said one, which contradicted the concurrency that is the whole
    reason this identifier exists.) What IS bounded: no NEW stale writer can
    appear after the deploy, and the entries age out of PROMOTE_WINDOW_S.

    GROUPS ARE ORDERED BY THEIR LAST ENTRY'S POSITION IN `recent`, which is
    load-bearing rather than tidiness. `record_failure` reads `groups[-1][:-1]`
    to ask whether the fetch it is serving has already promoted, and that is
    only meaningful while `groups[-1][-1]` is the entry it just appended. Under
    interleaving the newest entry can belong to the cascade that STARTED
    second, so creation order will not do. Pinned by
    test_the_last_group_ends_with_the_entry_just_appended.
    """
    ordered: list = []      # one [last_position, [entries]] per cascade
    by_cid: dict = {}
    legacy = None           # the open adjacency-grouped run, if there is one
    for pos, entry in enumerate(recent):
        cid = entry.get("cid")
        if cid is None:
            if legacy is not None and _same_cascade(legacy[1][-1], entry):
                legacy[1].append(entry)
                legacy[0] = pos
            else:
                legacy = [pos, [entry]]
                ordered.append(legacy)
        else:
            group = by_cid.get(cid)
            if group is None:
                group = [pos, []]
                by_cid[cid] = group
                ordered.append(group)
            group[1].append(entry)
            group[0] = pos
            # THE OPEN ADJACENCY RUN IS DELIBERATELY LEFT OPEN HERE, and the
            # `legacy = None` that used to sit on this line is gone. It guarded
            # against "stitching a legacy entry onto a modern cascade" -- which
            # this branch cannot do, since it never touches `legacy` -- while
            # actually SPLITTING one no-id cascade in two whenever an identified
            # entry landed between its rungs. Two fetches then cast three votes
            # and the domain promoted a whole fetch early, which spends tier 0:
            # the only rung that reads a PDF or returns a JSON body verbatim.
            #
            # Reachable across a DEPLOY and only then. A no-id entry is written
            # by a process that loaded the previous version of this module, and
            # every harness process on a box shares one state.db, so an
            # old-code fetch still in flight interleaves with a new-code one for
            # the lifetime of that fetch. Found by cross-family review, round 1;
            # reproduced at 3 cascades for 2 fetches before it was believed.
    ordered.sort(key=lambda g: g[0])
    return [g[1] for g in ordered]


def _counts_toward(entry: dict, failure_class: str, cutoff: float) -> bool:
    """Does this entry make its cascade a vote for `failure_class`?"""
    if entry.get("class") != failure_class:
        return False
    try:
        if float(entry.get("ts", 0.0)) < cutoff:
            return False
    except (TypeError, ValueError):
        return False
    # Absent on rows written before 2026-09-04 and on any reason this module
    # could not read a status from -- both mean "not known to be a 404", which
    # counts, exactly as it did before.
    return entry.get("status") not in NON_PROMOTING_STATUSES


def state_dir() -> Path:
    """Resolve the state directory ~/.local/share/webfetch (creating if missing)."""
    d = Path.home() / ".local" / "share" / "webfetch"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return state_dir() / "state.db"


# ---- Connection management ---------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS blocklist (
    domain TEXT PRIMARY KEY,
    tier_idx INTEGER NOT NULL,
    tier TEXT NOT NULL,
    reason TEXT,
    locked INTEGER NOT NULL DEFAULT 0,
    recent_failures TEXT NOT NULL DEFAULT '[]',
    recent_successes INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
    cache_key TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    tier TEXT NOT NULL,
    synthesized INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    url TEXT NOT NULL,
    tier TEXT NOT NULL,
    ok INTEGER NOT NULL,
    bytes INTEGER,
    elapsed_ms INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS rewrites (
    host TEXT NOT NULL,
    path_prefix TEXT NOT NULL,
    target_host TEXT NOT NULL,
    PRIMARY KEY(host, path_prefix)
);
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def open_db() -> sqlite3.Connection:
    """Open the SQLite DB with WAL mode + busy_timeout. Create schema if missing.

    check_same_thread=False so multiple harness threads can share one connection;
    WAL + busy_timeout + transactions provide the actual concurrency safety per D6.
    """
    conn = sqlite3.connect(str(db_path()), timeout=5.0, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_DDL)
    return conn


# ---- Bootstrap ---------------------------------------------------------------

def bootstrap_if_needed(conn: sqlite3.Connection, default_blocklist_path: Path) -> bool:
    """Idempotently seed blocklist + rewrites from default_blocklist.json on first run.

    Returns True if bootstrap ran, False if already done.
    """
    cur = conn.execute("SELECT value FROM config WHERE key='bootstrap_done'")
    row = cur.fetchone()
    if row and row["value"] == "1":
        return False
    if not default_blocklist_path.exists():
        # No seed file -> just mark done with empty state.
        conn.execute("BEGIN")
        conn.execute("INSERT OR REPLACE INTO config(key, value) VALUES (?, ?)",
                     ("bootstrap_done", "1"))
        conn.execute("INSERT OR REPLACE INTO config(key, value) VALUES (?, ?)",
                     ("schema_version", str(SCHEMA_VERSION)))
        conn.execute("COMMIT")
        return True
    seed = json.loads(default_blocklist_path.read_text(encoding="utf-8"))
    now = time.time()
    conn.execute("BEGIN")
    try:
        # Re-check inside the transaction to handle racing first-runs.
        row = conn.execute("SELECT value FROM config WHERE key='bootstrap_done'").fetchone()
        if row and row["value"] == "1":
            conn.execute("COMMIT")
            return False
        for rw in seed.get("rewrites", []):
            conn.execute(
                "INSERT OR IGNORE INTO rewrites(host, path_prefix, target_host) VALUES (?, ?, ?)",
                (rw["host"], rw["path_prefix"], rw["target_host"]),
            )
        for d in seed.get("domains", []):
            conn.execute(
                "INSERT OR IGNORE INTO blocklist"
                "(domain, tier_idx, tier, reason, locked, recent_failures, recent_successes, updated_at) "
                "VALUES (?, ?, ?, ?, 0, '[]', 0, ?)",
                (d["domain"], int(d["tier_idx"]), d["tier"], d.get("reason"), now),
            )
        conn.execute("INSERT OR REPLACE INTO config(key, value) VALUES (?, ?)",
                     ("bootstrap_done", "1"))
        conn.execute("INSERT OR REPLACE INTO config(key, value) VALUES (?, ?)",
                     ("schema_version", str(SCHEMA_VERSION)))
        conn.execute("COMMIT")
        return True
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---- Blocklist ---------------------------------------------------------------

class Blocklist:
    """Per-domain tier hints with threshold-gated promotion (D10)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, domain: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM blocklist WHERE domain=?", (domain,)).fetchone()
        return dict(row) if row else None

    def record_failure(self, domain: str, tier_idx: int, failure_class: str,
                       reason: str | None = None,
                       cascade_id: str | None = None) -> str | None:
        """Append failure; promote tier_idx if threshold met. Returns 'promoted'|'locked'|None.

        H5 fix (discriminator iter 2): wrap read-modify-write in BEGIN IMMEDIATE so
        concurrent CC + Codex invocations can't race-overwrite each other's append.
        M1 fix: count CONSECUTIVE same-class failures from the tail of recent_failures,
        not all-in-window.
        2026-09-04: and count them in CASCADES, not tier attempts -- this fires once
        per rung, so one fetch used to supply a whole streak by itself. A 404 or 410
        no longer promotes at all.
        2026-09-05: `cascade_id` says which fetch this rung belongs to instead of
        leaving `_cascades` to infer it. Optional, and omitted from the stored entry
        when absent, so a caller that does not pass one writes exactly the row this
        module wrote before.
        """
        now = time.time()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM blocklist WHERE domain=?", (domain,)).fetchone()
            if row is None:
                recent = []
                cur_tier_idx = tier_idx
                locked = 0
            else:
                recent = json.loads(row["recent_failures"])
                cur_tier_idx = row["tier_idx"]
                locked = row["locked"]
            # `status` is what tells a 404 from a 403 later: both arrive under
            # the class name `origin_4xx_block`, and this is the last point at
            # which the reason text is in hand. None when unreadable.
            entry = {"ts": now, "class": failure_class, "tier_idx": tier_idx,
                     "status": _reason_status(reason)}
            if cascade_id is not None:
                # OMITTED rather than stored as None when absent, so a row this
                # writes without one is indistinguishable from a pre-2026-09-05
                # row and `_cascades` needs one test for both.
                entry["cid"] = cascade_id
            recent.append(entry)
            recent = recent[-RECENT_FAILURES_CAP:]
            if locked:
                self._upsert(domain, cur_tier_idx, recent, 0, now, reason)
                self.conn.execute("COMMIT")
                return "locked"
            if failure_class in PERSISTENT_FAILURE_CLASSES:
                # M1 fix: walk from the tail and stop at the first break, so a
                # different class ends the streak.
                # 2026-09-04 fix: THE UNIT IS THE CASCADE, NOT THE TIER ATTEMPT.
                # Group the tail into fetches (`_cascades`) and count how many
                # CONSECUTIVE fetches, within the 1-hour window, produced this
                # class at any rung. A fetch that produced it nowhere -- or
                # produced it only as a 404/410, which no browser can fix --
                # breaks the streak, exactly as a different class always did.
                cutoff = now - PROMOTE_WINDOW_S
                groups = _cascades(recent)
                streak = 0
                for group in reversed(groups):
                    if not any(_counts_toward(e, failure_class, cutoff) for e in group):
                        break
                    streak += 1
                # AT MOST ONE RUNG PER FETCH. Once the threshold is met it stays
                # met for the rest of the cascade, so without this the remaining
                # rungs of the promoting fetch would each promote again and the
                # single fetch would walk the domain to the top of the ladder --
                # the very defect the cascade count fixes, one layer down. The
                # current entry is `groups[-1][-1]`; only its predecessors in the
                # same cascade are consulted.
                already = any(e.get("promoted") for e in groups[-1][:-1])
                if (streak >= PROMOTE_THRESHOLD and not already
                        and cur_tier_idx < len(TIERS) - 1):
                    recent[-1]["promoted"] = 1
                    cur_tier_idx += 1
                    self._upsert(domain, cur_tier_idx, recent, 0, now, reason)
                    self.conn.execute("COMMIT")
                    return "promoted"
            # No promotion; reset recent_successes on any failure.
            self._upsert(domain, cur_tier_idx, recent, 0, now, reason)
            self.conn.execute("COMMIT")
            return None
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def record_success(self, domain: str, tier_idx: int,
                       cascade_id: str | None = None) -> str | None:
        """Bump recent_successes; demote tier_idx if threshold met. Returns 'demoted'|'locked'|None.

        H5 fix: atomic via BEGIN IMMEDIATE for multi-process safety.

        2026-09-05: A SUCCESSFUL FETCH NOW LEAVES A MARK. This used to write
        `recent_failures` back untouched, so the streak in `record_failure` --
        which counts CONSECUTIVE cascades producing a class -- walked straight
        past a run of clean fetches as though they had not happened. Measured
        before the fix: two failing fetches, then FIVE successful ones, then one
        more failing fetch still promoted the domain; and a demotion earned over
        five successes was undone by the very next failure, on evidence those
        successes should have retired. Both close here, in the one place that
        knows a fetch went well.

        `recent_successes` and the demotion rule are UNCHANGED. The locked branch
        is unchanged too: a locked domain never promotes or demotes, so a mark
        there would be written and never read.
        """
        now = time.time()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM blocklist WHERE domain=?", (domain,)).fetchone()
            mark = _success_entry(now, tier_idx, cascade_id)
            if row is None:
                # New entry. Successes start at 1.
                self._upsert(domain, tier_idx, [mark], 1, now, "auto-learned (success)")
                self.conn.execute("COMMIT")
                return None
            recent = json.loads(row["recent_failures"])
            cur_tier_idx = row["tier_idx"]
            if row["locked"]:
                self.conn.execute(
                    "UPDATE blocklist SET recent_successes = recent_successes + 1, updated_at=? WHERE domain=?",
                    (now, domain))
                self.conn.execute("COMMIT")
                return "locked"
            recent = (recent + [mark])[-RECENT_FAILURES_CAP:]
            successes = row["recent_successes"] + 1
            if successes >= DEMOTE_THRESHOLD and cur_tier_idx > 0:
                cur_tier_idx -= 1
                self._upsert(domain, cur_tier_idx, recent, 0, now, "auto-demoted")
                self.conn.execute("COMMIT")
                return "demoted"
            # `_upsert` rather than the narrow UPDATE this used to run, because
            # the mark has to be persisted too. `reason=None` keeps the stored
            # reason (COALESCE) and the tier is written back as it was read.
            self._upsert(domain, cur_tier_idx, recent, successes, now, None)
            self.conn.execute("COMMIT")
            return None
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def _upsert(self, domain, tier_idx, recent_failures, recent_successes, updated_at, reason):
        self.conn.execute(
            "INSERT INTO blocklist(domain, tier_idx, tier, reason, locked, recent_failures, recent_successes, updated_at) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, ?) "
            "ON CONFLICT(domain) DO UPDATE SET tier_idx=excluded.tier_idx, tier=excluded.tier, "
            "  reason=COALESCE(excluded.reason, blocklist.reason), recent_failures=excluded.recent_failures, "
            "  recent_successes=excluded.recent_successes, updated_at=excluded.updated_at",
            (domain, tier_idx, TIERS[tier_idx], reason, json.dumps(recent_failures),
             recent_successes, updated_at))

    def clear(self) -> None:
        self.conn.execute("DELETE FROM blocklist")


# ---- Cache -------------------------------------------------------------------

def compute_cache_key(url: str, raw_required: bool, max_tier: str, allow_synthesis: bool) -> str:
    payload = (f"{CACHE_POLICY_VERSION}\x00{url}\x00{int(raw_required)}"
               f"\x00{max_tier}\x00{int(allow_synthesis)}")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Cache:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, cache_key: str) -> dict | None:
        now = time.time()
        row = self.conn.execute(
            "SELECT content, tier, synthesized, created_at, expires_at "
            "FROM cache WHERE cache_key=? AND expires_at > ?",
            (cache_key, now)).fetchone()
        return dict(row) if row else None

    def put(self, cache_key: str, content: str, tier: str, synthesized: bool = False,
            ttl_s: int = DEFAULT_CACHE_TTL_S) -> None:
        now = time.time()
        self.conn.execute(
            "INSERT OR REPLACE INTO cache(cache_key, content, tier, synthesized, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cache_key, content, tier, int(bool(synthesized)), now, now + ttl_s))

    def clear_expired(self) -> int:
        now = time.time()
        cur = self.conn.execute("DELETE FROM cache WHERE expires_at <= ?", (now,))
        return cur.rowcount

    def clear(self) -> None:
        self.conn.execute("DELETE FROM cache")


# ---- FetchLog ----------------------------------------------------------------

class FetchLog:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def append(self, url: str, tier: str, ok: bool, *, bytes_: int | None = None,
               elapsed_ms: int | None = None, error: str | None = None) -> None:
        # Caller is responsible for redacting sensitive params via safety.redact_sensitive_params.
        self.conn.execute(
            "INSERT INTO fetch_log(ts, url, tier, ok, bytes, elapsed_ms, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), url, tier, int(bool(ok)), bytes_, elapsed_ms, error))

    def rotate(self, keep: int = FETCH_LOG_KEEP) -> int:
        """Delete fetch_log rows beyond the most recent `keep`. Returns deleted count."""
        cutoff_row = self.conn.execute(
            "SELECT id FROM fetch_log ORDER BY id DESC LIMIT 1 OFFSET ?", (keep,)).fetchone()
        if cutoff_row is None:
            return 0
        cur = self.conn.execute("DELETE FROM fetch_log WHERE id <= ?", (cutoff_row["id"],))
        return cur.rowcount


# ---- Rewrites ----------------------------------------------------------------

def apply_rewrites(conn: sqlite3.Connection, url: str) -> tuple[str, str | None]:
    """Apply path-scoped URL rewrites. Returns (rewritten_url, original_or_None).

    First matching (host, path_prefix) wins. Path-scoped per D4: only when both host
    matches AND path startswith path_prefix.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return url, None
    rows = conn.execute(
        "SELECT path_prefix, target_host FROM rewrites WHERE host=? "
        "ORDER BY length(path_prefix) DESC", (host,)).fetchall()
    for row in rows:
        if parsed.path.startswith(row["path_prefix"]):
            new_netloc = row["target_host"]
            if parsed.port:
                new_netloc += f":{parsed.port}"
            new_url = urlunparse(parsed._replace(netloc=new_netloc))
            return new_url, url
    return url, None


# ---- Config ------------------------------------------------------------------

class Config:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO config(key, value) VALUES (?, ?)", (key, value))
