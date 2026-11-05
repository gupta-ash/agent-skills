"""Unit tests for safety.py - run with: python -m pytest tests/test_safety.py"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import safety


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/",
    "gopher://example.com/",
    "javascript:alert(1)",
    "data:text/plain,hello",
])
def test_blocked_schemes(url):
    with pytest.raises(safety.UnsafeURL):
        safety.validate_url_safety(url)


@pytest.mark.parametrize("url", [
    "http://localhost/",
    "http://127.0.0.1:9222/",
    "http://192.168.1.1/",
    "http://10.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
])
def test_blocked_private_ips(url):
    with pytest.raises(safety.UnsafeURL):
        safety.validate_url_safety(url)


def test_allow_private_bypass():
    safety.validate_url_safety("http://127.0.0.1:8080/", allow_private=True)
    # No exception expected.


def test_allow_private_via_env(monkeypatch):
    monkeypatch.setenv("WEBFETCH_ALLOW_PRIVATE", "1")
    safety.validate_url_safety("http://127.0.0.1:8080/")


def test_public_url_passes():
    safety.validate_url_safety("https://example.com/")


def test_safe_opener_handlers():
    op = safety.safe_opener()
    handler_names = {type(h).__name__ for h in op.handlers}
    assert "HTTPHandler" in handler_names
    assert "HTTPSHandler" in handler_names
    assert "FileHandler" not in handler_names
    assert "FTPHandler" not in handler_names


def test_redact_sensitive_params():
    out = safety.redact_sensitive_params(
        "https://api.com/x?token=abc&q=hello&api_key=zzz&Authorization=Bearer+xyz")
    assert "token" not in out
    assert "api_key" not in out
    assert "Authorization" not in out
    assert "q=hello" in out


def test_has_sensitive_params():
    assert safety.has_sensitive_params("https://api.com/?token=abc")
    assert safety.has_sensitive_params("https://api.com/?Api_Key=abc")  # case-insensitive
    assert not safety.has_sensitive_params("https://api.com/?q=hello")


# ---- a URL the stdlib parser refuses must FAIL CLOSED, not escape -------------
# Cross-family review, 2026-08-30. `http://[::1` raises ValueError("Invalid IPv6
# URL") out of urlsplit. Every caller of validate_url_safety guards only
# UnsafeURL, so before the fix that ValueError escaped tier_http AND fetch.py's
# own stage-1 guard and ended the entire cascade with a traceback. Reproduced at
# main dd881d6 too, so it predates the tier-0 work.

@pytest.mark.parametrize("url", [
    "http://[::1",                 # the measured case: unmatched bracket
    "https://[::1",
    "http://[not:an:ip",
])
def test_an_unparseable_url_raises_unsafeurl_not_valueerror(url):
    """The parser's own refusal must arrive as UnsafeURL, which callers handle.

    The assertion is the EXCEPTION TYPE, not the message: every call site in this
    skill is written `except UnsafeURL`, so a ValueError reaching them is not a
    worse error message, it is an unhandled crash three frames up.
    """
    with pytest.raises(safety.UnsafeURL) as ei:
        safety.validate_url_safety(url)
    assert "unparseable" in str(ei.value).lower()


def test_a_wellformed_ipv6_url_still_reaches_the_ip_checks():
    """The guard must not swallow well-formed IPv6 -- it would mask the SSRF check.

    `http://[::1]/` is parseable and resolves to loopback, so it must be refused
    for THAT reason. If this test reports 'unparseable' the guard is too wide and
    is now hiding the check it sits in front of.
    """
    with pytest.raises(safety.UnsafeURL) as ei:
        safety.validate_url_safety("http://[::1]/")
    msg = str(ei.value).lower()
    assert "unparseable" not in msg
    assert "loopback" in msg or "resolves to" in msg
