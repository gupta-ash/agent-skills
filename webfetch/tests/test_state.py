"""Unit tests for state.py SQLite layer - run with: python -m pytest tests/test_state.py

Uses tmp DB via monkeypatching state.state_dir, so no real state is touched.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hashlib
import json
import threading
import pytest
import state


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    conn = state.open_db()
    yield conn
    conn.close()


def test_bootstrap_idempotent(tmp_db, tmp_path):
    blocklist_json = tmp_path / "default_blocklist.json"
    blocklist_json.write_text(json.dumps({
        "schema_version": 3,
        "rewrites": [{"host": "a.com", "path_prefix": "/x/", "target_host": "b.com"}],
        "domains": [{"domain": "z.com", "tier_idx": 1, "tier": "headless"}],
    }))
    assert state.bootstrap_if_needed(tmp_db, blocklist_json) is True
    assert state.bootstrap_if_needed(tmp_db, blocklist_json) is False
    rewrites = tmp_db.execute("SELECT * FROM rewrites").fetchall()
    assert len(rewrites) == 1


def test_blocklist_promote_threshold(tmp_db):
    bl = state.Blocklist(tmp_db)
    assert bl.record_failure("x.com", 0, "cloudflare_challenge") is None
    assert bl.record_failure("x.com", 0, "cloudflare_challenge") is None
    assert bl.record_failure("x.com", 0, "cloudflare_challenge") == "promoted"
    entry = bl.get("x.com")
    assert entry["tier_idx"] == 1


def test_blocklist_no_promote_on_transient(tmp_db):
    bl = state.Blocklist(tmp_db)
    for _ in range(5):
        assert bl.record_failure("flaky.com", 0, "connect_refused") is None
    assert bl.get("flaky.com")["tier_idx"] == 0


def test_blocklist_demote_after_successes(tmp_db):
    bl = state.Blocklist(tmp_db)
    # Seed at tier 1
    bl._upsert("y.com", 1, [], 0, 0.0, "test")
    for _ in range(4):
        assert bl.record_success("y.com", 1) is None
    assert bl.record_success("y.com", 1) == "demoted"
    assert bl.get("y.com")["tier_idx"] == 0


def test_blocklist_locked_no_change(tmp_db):
    bl = state.Blocklist(tmp_db)
    bl._upsert("locked.com", 2, [], 0, 0.0, "manual")
    tmp_db.execute("UPDATE blocklist SET locked=1 WHERE domain='locked.com'")
    for _ in range(5):
        assert bl.record_failure("locked.com", 2, "cloudflare_challenge") == "locked"
    assert bl.get("locked.com")["tier_idx"] == 2


def test_cache_ttl(tmp_db):
    cache = state.Cache(tmp_db)
    cache.put("k1", "content", "http", ttl_s=900)
    assert cache.get("k1")["content"] == "content"
    cache.put("k2", "stale", "http", ttl_s=-1)  # already expired
    assert cache.get("k2") is None


@pytest.mark.parametrize("url,raw_required,max_tier,allow_synthesis", [
    ("https://x.com/other", False, "cdp", False),
    ("https://x.com/", True, "cdp", False),
    ("https://x.com/", False, "http", False),
    ("https://x.com/", False, "cdp", True),
])
def test_cache_key_distinguishes_flags(url, raw_required, max_tier, allow_synthesis):
    baseline = state.compute_cache_key("https://x.com/", False, "cdp", False)
    changed = state.compute_cache_key(url, raw_required, max_tier, allow_synthesis)
    assert baseline != changed


def test_access_policy_cache_key_is_deterministic_and_differs_from_legacy():
    url = "https://publisher.example/article"
    legacy_payload = f"{url}\x000\x00cdp\x000"
    legacy_key = hashlib.sha256(legacy_payload.encode("utf-8")).hexdigest()
    key = state.compute_cache_key(url, False, "cdp", False)
    assert key == state.compute_cache_key(url, False, "cdp", False)
    assert len(key) == 64
    assert key != legacy_key
    expected_payload = "access-gate-v1\x00" + legacy_payload
    assert key == hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()


def test_access_policy_cache_cutover_misses_legacy_teasers_without_clearing_rows(tmp_db):
    url = "https://publisher.example/article"
    legacy_payload = f"{url}\x000\x00cdp\x000"
    legacy_key = hashlib.sha256(legacy_payload.encode("utf-8")).hexdigest()
    cache = state.Cache(tmp_db)
    cache.put(legacy_key, "Subscription required: old cached teaser", "http")
    current_key = state.compute_cache_key(url, False, "cdp", False)
    assert cache.get(current_key) is None
    cache.put(current_key, "Browser-confirmed readable article", "headless")
    assert cache.get(current_key)["content"] == "Browser-confirmed readable article"
    assert cache.get(legacy_key)["content"] == "Subscription required: old cached teaser"


def test_persisted_tier_slots_remain_stable_with_copilot_disabled():
    assert state.TIERS == ("http", "headless", "headed", "copilot", "cdp")
    assert state.TIERS[3] == "copilot" and state.TIERS[4] == "cdp"


def test_disabled_tier_failures_never_promote_the_reserved_slot(tmp_db):
    import tiers

    bl = state.Blocklist(tmp_db)
    for i in range(state.PROMOTE_THRESHOLD + 2):
        result = tiers.tier_copilot("not a URL", search_path="/dummy/search.py")
        assert result.error_code == "disabled" and result.failure_class == "other"
        assert bl.record_failure("disabled.example", 3, result.failure_class,
                                 reason=result.error, cascade_id=f"disabled-{i}") is None
    assert bl.get("disabled.example")["tier_idx"] == 3


def test_apply_rewrites_path_scoped(tmp_db):
    tmp_db.execute(
        "INSERT INTO rewrites VALUES (?, ?, ?)", ("reddit.com", "/r/", "old.reddit.com"))
    out, orig = state.apply_rewrites(tmp_db, "https://reddit.com/r/x/")
    assert out == "https://old.reddit.com/r/x/" and orig == "https://reddit.com/r/x/"
    out, orig = state.apply_rewrites(tmp_db, "https://reddit.com/api/v1/")
    assert orig is None


def test_concurrent_inserts(tmp_path, monkeypatch):
    """Stress test: 8 threads each opening their own connection (mirrors multi-process model).

    The production concurrency model is one connection per harness process; WAL +
    busy_timeout handle cross-process safety per D6. This test mirrors that by
    opening a fresh connection per thread (not sharing one connection).
    """
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    # Initialize schema once.
    init_conn = state.open_db()
    init_conn.close()

    def worker(i):
        conn = state.open_db()
        try:
            fl = state.FetchLog(conn)
            bl = state.Blocklist(conn)
            for j in range(20):
                fl.append(f"https://example.com/{i}/{j}", "http", True, bytes_=100, elapsed_ms=50)
                bl.record_success(f"d{i}.com", 0)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check_conn = state.open_db()
    count = check_conn.execute("SELECT COUNT(*) AS c FROM fetch_log").fetchone()["c"]
    check_conn.close()
    assert count == 8 * 20  # no lost writes across processes/threads
