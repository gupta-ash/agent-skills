"""safety.py - URL/IP/scheme guards (SSRF defense) for webfetch.

Per project decision D7 (web_fetch decisions.md):
- Scheme allowlist: only http, https
- Custom OpenerDirector with NO FileHandler/FTPHandler
- IP-class rejection (private/loopback/link-local/reserved/multicast)
- Re-validation per redirect (TOCTOU + DNS-rebinding defense)
- Escape hatch: allow_private=True (CLI --allow-private or WEBFETCH_ALLOW_PRIVATE=1)
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.request
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

ALLOWED_SCHEMES = frozenset({"http", "https"})

SENSITIVE_PARAMS = frozenset({
    "token", "api_key", "apikey", "password", "secret",
    "session", "key", "auth", "authorization", "access_token",
    "refresh_token", "bearer", "client_secret",
})


class UnsafeURL(Exception):
    """Raised when a URL fails safety validation (unsafe scheme, private IP, etc.)."""


def _allow_private_from_env() -> bool:
    return os.environ.get("WEBFETCH_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes")


def _resolve_all_ips(hostname: str) -> list[str]:
    """Resolve hostname to all A/AAAA records via getaddrinfo. Raise UnsafeURL on DNS failure."""
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, socket.herror) as e:
        raise UnsafeURL(f"DNS resolution failed for {hostname!r}: {e}") from e
    return [info[4][0] for info in infos]


def _is_blocked_ip(ip_str: str) -> tuple[bool, str]:
    """Return (is_blocked, reason). Blocks private/loopback/link-local/reserved/multicast/unspecified."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True, f"not a valid IP: {ip_str!r}"
    if ip.is_loopback:
        return True, "loopback"
    if ip.is_private:
        return True, "private"
    if ip.is_link_local:
        return True, "link-local"
    if ip.is_reserved:
        return True, "reserved"
    if ip.is_multicast:
        return True, "multicast"
    if ip.is_unspecified:
        return True, "unspecified"
    return False, ""


def validate_url_safety(url: str, *, allow_private: bool = False) -> None:
    """Validate a URL against safety policy. Raise UnsafeURL on hazard.

    Checks:
    1. Scheme is http or https
    2. Hostname present and DNS-resolvable
    3. None of the resolved IPs are private/loopback/link-local/reserved/multicast
       (skipped when allow_private=True or WEBFETCH_ALLOW_PRIVATE=1)
    """
    if not url:
        raise UnsafeURL("empty URL")
    try:
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        host = parsed.hostname
    except ValueError as e:
        # FAIL CLOSED. A URL the stdlib parser refuses cannot be validated, so
        # it cannot be cleared. `http://[::1` raises ValueError("Invalid IPv6
        # URL") out of urlsplit, and EVERY caller of this function guards only
        # UnsafeURL -- so that ValueError escaped tier_http and fetch.py's own
        # stage-1 guard alike and ended the whole cascade with a traceback.
        # Measured 2026-08-30 and reproduced at main dd881d6, so it predates the
        # tier-0 work; one guard here covers all six call sites, which is why
        # this is fixed at the source rather than beside each `except UnsafeURL`.
        raise UnsafeURL(f"unparseable URL {url!r}: {e}") from e
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL(f"scheme {scheme!r} not allowed; only http/https")
    if not host:
        raise UnsafeURL(f"no hostname in URL {url!r}")
    if allow_private or _allow_private_from_env():
        return
    # Resolve and check each IP. If hostname is itself an IP literal, getaddrinfo returns it back.
    for ip_str in _resolve_all_ips(host):
        blocked, reason = _is_blocked_ip(ip_str)
        if blocked:
            raise UnsafeURL(
                f"hostname {host!r} resolves to {ip_str} ({reason}); "
                f"pass --allow-private to override"
            )


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTPRedirectHandler that re-validates the redirect target (TOCTOU/DNS-rebinding defense).

    Reads `_webfetch_allow_private` attribute from the request to know whether to skip IP checks.

    Also RECORDS each followed hop onto `_webfetch_hops`, when the caller attached
    one. urllib follows redirects inside `open()`, so this handler is the only
    thing in the process that sees the chain -- the caller gets back the final
    URL and nothing else. Recording only: it decides nothing, and the
    re-validation below is unchanged by its presence.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allow_private = getattr(req, "_webfetch_allow_private", False)
        validate_url_safety(newurl, allow_private=allow_private)
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            # Carry the flag forward across redirects.
            new_req._webfetch_allow_private = allow_private
            hops = getattr(req, "_webfetch_hops", None)
            if hops is not None:
                # `newurl` is ALREADY ABSOLUTE: http_error_302 urljoins the
                # Location header against the request URL before calling this.
                # A hop is recorded only where one is actually FOLLOWED -- a
                # None from super() means urllib refused this redirect, and a
                # refused redirect is not part of any chain.
                hops.append((code, req.get_full_url(), newurl))
                new_req._webfetch_hops = hops
        return new_req


def safe_opener() -> urllib.request.OpenerDirector:
    """Return an OpenerDirector with only HTTP/HTTPS + safe-redirect handlers.

    Defense-in-depth: even if validate_url_safety is bypassed, this opener
    won't process file://, ftp://, etc. (no FileHandler / FTPHandler installed).
    """
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.HTTPHandler())
    opener.add_handler(urllib.request.HTTPSHandler())
    opener.add_handler(_SafeRedirectHandler())
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    # Registers handle_error['http']['default']. Without it OpenerDirector.error()
    # finds no handler for a non-2xx, returns None, and open() hands back a literal
    # None instead of raising HTTPError -- so every 404/403/429/5xx arrives at the
    # caller with no status at all. build_opener() installs this by default; a bare
    # OpenerDirector does not.
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    return opener


def cdp_port_reachable(port: int = 9222, timeout: float = 0.5) -> bool:
    """Probe whether a CDP target is reachable at 127.0.0.1:<port>."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except (OSError, socket.error):
        return False


def redact_sensitive_params(url: str) -> str:
    """Strip sensitive query params from URL for logging / cache-key derivation."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    safe_pairs = [(k, v) for k, v in pairs if k.lower() not in SENSITIVE_PARAMS]
    new_query = urlencode(safe_pairs)
    return urlunparse(parsed._replace(query=new_query))


def has_sensitive_params(url: str) -> bool:
    """Return True if the URL contains any sensitive query params."""
    parsed = urlparse(url)
    if not parsed.query:
        return False
    for k, _ in parse_qsl(parsed.query, keep_blank_values=True):
        if k.lower() in SENSITIVE_PARAMS:
            return True
    return False
