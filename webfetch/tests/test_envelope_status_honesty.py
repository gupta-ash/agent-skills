"""Envelope status honesty behavioral regression tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch
import tiers

URL = "https://example.com/a"


def _env(**kw):
    base = dict(url=URL, original_url=None, tier="http", extractor="trafilatura",
                synthesized=False, content="hi", bytes_=2, elapsed_ms=1,
                pages_read=None, from_cache=False, blocklist_action=None,
                tier_attempts=[], warnings=[], redirect_chain=[])
    base.update(kw)
    return fetch._make_envelope(**base)


# ---- the two keys travel together --------------------------------------------

def test_the_marker_never_appears_without_a_status_beside_it():
    """A marker with no status would be a third state nobody asked for.

    Absence of `status` already means "the selected result carried none", which
    predates this change and is correct. It is NOT the cache path alone -- an
    earlier version of this docstring said so and review round 3 was right that
    the guard reads `status` and nothing else, so a transport failure, an unsafe
    URL and any other exit with no response produce the same shape. So the
    marker is emitted INSIDE the same guard rather than unconditionally.

    This is the arm the pre-existing absence assertion could not make: it uses
    `.get()`, which reads None for an absent key and for a present null alike.
    """
    env = _env(status=None, status_is_real=False)
    assert "status" not in env
    assert "status_is_real" not in env, (
        "a marker with nothing to qualify is worse than no marker")


def test_a_status_carries_its_marker():
    env = _env(status=200)
    assert env["status"] == 200
    assert env["status_is_real"] is True


def test_the_marker_says_false_when_the_tier_invented_the_number():
    env = _env(status=200, status_is_real=False)
    assert env["status"] == 200, "the value still ships -- callers keep working"
    assert env["status_is_real"] is False


# ---- per producer, which is where a wrong constant would hide -----------------



def test_disabled_copilot_reports_no_status_or_synthesis():
    """An unexecuted transport must not invent an origin status."""
    result = tiers.tier_copilot(URL, search_path="/dummy/search.py")
    assert result.ok is False and result.error_code == "disabled"
    assert result.status is None and result.content == ""
    assert result.failure_class == "other"



_PAGE = ("<html><head><title>A real page</title></head><body><article><p>"
         + ("This is ordinary article prose that a reader can see. " * 12)
         + "</p></article></body></html>")


def _fake_session(status):
    class _S:
        def fetch_html(self, url, total_timeout):
            return url, _PAGE, status, {}, []
    return _S()


def test_an_unread_status_reaches_the_tier_result_marked_unreal(monkeypatch):
    """The browser seam exercises the real tier without launching Chrome.

    `fetch_html` returning None for the status is exactly what the capture route
    does when it is unavailable, so this is the shape the tier really produces
    rather than one invented for the test.
    """
    monkeypatch.setattr(tiers, "get_session", lambda mode: _fake_session(None))
    r = tiers.tier_playwright("https://example.com/app", mode="headless")
    assert r.status == 200, "the placeholder still ships"
    assert r.status_is_real is False, "but it must not claim to be the origin's"


def test_a_read_status_reaches_the_tier_result_marked_real(monkeypatch):
    """THE CONTROL. Without it the test above passes against a constant False,
    which would be a marker that says nothing.
    """
    monkeypatch.setattr(tiers, "get_session", lambda mode: _fake_session(203))
    r = tiers.tier_playwright("https://example.com/app", mode="headless")
    assert r.status == 203
    assert r.status_is_real is True


