"""Offline consumer smoke: public state, raw fetch and option-sensitive caching."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch
import safety
import state
import tiers
from synthetic_pages import PAGES


def test_public_state_is_created_only_beneath_the_supplied_home(tmp_path, monkeypatch):
    monkeypatch.setattr(state.Path, "home", lambda: tmp_path)
    directory = state.state_dir()
    assert directory == tmp_path / ".local" / "share" / "webfetch"
    assert directory.is_dir()
    conn = state.open_db()
    try:
        state.Config(conn).set("public_smoke", "present")
        assert state.Config(conn).get("public_smoke") == "present"
    finally:
        conn.close()
    assert (directory / "state.db").is_file()
    assert {p.name for p in directory.parent.iterdir()} == {"webfetch"}


def test_raw_cascade_cache_round_trip_preserves_status_honesty(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(safety, "validate_url_safety", lambda *a, **k: None)
    attempts = []
    content = "Public reference content. " * 30

    def http(url, **kwargs):
        attempts.append(url)
        return tiers.TierResult(
            ok=True, tier="http", content=content, html=PAGES["article"],
            status=200, status_is_real=True, bytes_=len(content),
            headers={"Content-Type": "text/html"})

    monkeypatch.setattr(tiers, "tier_http", http)
    conn = state.open_db()
    try:
        first = fetch.cascade_fetch("https://example.com/reference", conn=conn,
                                    raw_required=True, max_tier="http")
        cached = fetch.cascade_fetch("https://example.com/reference", conn=conn,
                                     raw_required=True, max_tier="http")
        assert first.get("error") is None
        assert first["content"] == content
        assert first["status"] == 200 and first["status_is_real"] is True
        assert first["synthesized"] is False and first["from_cache"] is False
        assert cached["content"] == content and cached["from_cache"] is True
        assert "status" not in cached and "status_is_real" not in cached
        assert attempts == ["https://example.com/reference"]
    finally:
        conn.close()
