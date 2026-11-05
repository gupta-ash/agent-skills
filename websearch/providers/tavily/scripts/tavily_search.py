#!/usr/bin/env python3
"""Tavily Search skill for OpenClaw — web search with topic routing + URL extraction.

Pure stdlib Python (3.8+). No external dependencies.
POST to https://api.tavily.com/search or https://api.tavily.com/extract.
Always emits structured JSON to stdout. Diagnostics to stderr with --debug.

v1.0.0 — search with topic routing (general/news/finance) + extract endpoint.
"""

import argparse
import gzip
import ipaddress
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# Key file support — shared across all search skills
import sys as _sys
_shared_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "_shared")
if _shared_dir not in _sys.path:
    _sys.path.insert(0, _shared_dir)
from keyfile import load_keys, rotate_key, DEFAULT_KEY_DIR

_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB cap on response.read()

VERSION = "1.0.0"
_BASE_URL = "https://api.tavily.com"
MAX_QUERY_LENGTH = 400

# SSRF protection — block private/reserved IP ranges for extract URLs
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),         # "this network" (can alias localhost)
    ipaddress.ip_network("10.0.0.0/8"),         # RFC-1918 private
    ipaddress.ip_network("100.64.0.0/10"),      # carrier-grade NAT (RFC 6598)
    ipaddress.ip_network("127.0.0.0/8"),        # loopback IPv4
    ipaddress.ip_network("169.254.0.0/16"),     # link-local (incl. cloud metadata)
    ipaddress.ip_network("172.16.0.0/12"),      # RFC-1918 private
    ipaddress.ip_network("192.168.0.0/16"),     # RFC-1918 private
    ipaddress.ip_network("198.18.0.0/15"),      # benchmark testing (RFC 2544)
    ipaddress.ip_network("240.0.0.0/4"),        # reserved for future use
    ipaddress.ip_network("::1/128"),            # loopback IPv6
    ipaddress.ip_network("fc00::/7"),           # unique-local IPv6
    ipaddress.ip_network("fe80::/10"),          # link-local IPv6
]


def _normalize_ip(addr: Any) -> Any:
    """Normalize IPv4-mapped IPv6 addresses to IPv4 for consistent blocking.

    Prevents bypasses like ::ffff:127.0.0.1 evading IPv4 blocklist checks.
    """
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped
    return addr


def _is_ssrf_safe(url: str) -> bool:
    """Check if a URL resolves to a public (non-private) IP address.

    Returns True if safe, False if blocked.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        addr_infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC,
                                         socket.SOCK_STREAM)
    except (socket.gaierror, OSError, ValueError):
        return False

    for family, _, _, _, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = _normalize_ip(ipaddress.ip_address(ip_str))
        except ValueError:
            continue
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                return False

    return True


def _detect_error_code(status: int) -> str:
    """Map HTTP status code to a structured error code string."""
    if status == 401:
        return "auth_error"
    if status == 429:
        return "rate_limited"
    if status == 432:
        return "quota_exceeded"
    if status == 433:
        return "payg_cap_exceeded"
    if 400 <= status <= 499:
        return "client_error"
    if 500 <= status <= 599:
        return "server_error"
    return "unknown_error"


def _make_output(query: str, search_type: str, results: List[Dict[str, Any]],
                 error: Optional[str], http_status: Optional[int],
                 error_code: Optional[str], meta: Dict[str, Any]) -> Dict[str, Any]:
    """Build the standard output envelope."""
    meta.setdefault("version", VERSION)
    return {
        "provider": "tavily",
        "query": query,
        "type": search_type,
        "results": results,
        "error": error,
        "http_status": http_status,
        "error_code": error_code,
        "meta": meta,
    }


def _emit(output: Dict[str, Any]) -> None:
    """Print JSON output to stdout."""
    print(json.dumps(output, separators=(",", ":")))


def _http_post_json(url: str, body: Dict[str, Any], api_key: str,
                    timeout_s: int = 30) -> Tuple[int, Dict[str, Any]]:
    """POST JSON to a URL with Bearer auth.

    Returns (http_status, parsed_response_body).
    Raises urllib.error.HTTPError or urllib.error.URLError on failure.
    """
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("Accept-Encoding", "gzip")
    req.add_header("User-Agent", f"openclaw-tavily/{VERSION}")
    with urllib.request.urlopen(req, timeout=timeout_s) as response:
        status = response.getcode()
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError(f"response exceeds {_MAX_RESPONSE_BYTES} bytes")
        enc = response.headers.get("Content-Encoding", "")
        if "gzip" in enc.lower() or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return status, json.loads(raw.decode("utf-8", errors="replace"))


def _format_search_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize Tavily search results into standard format."""
    results = []
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return results
    for pos, r in enumerate(items, 1):
        if not isinstance(r, dict):
            continue
        entry = {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
            "score": r.get("score"),
            "position": pos,
        }
        if r.get("published_date"):
            entry["published_date"] = r["published_date"]
        if r.get("raw_content"):
            entry["raw_content"] = r["raw_content"]
        results.append(entry)
    return results


def _format_extract_results(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Normalize Tavily extract results. Returns (results, failed_results)."""
    results = []
    failed = []
    items = data.get("results") if isinstance(data, dict) else None
    if isinstance(items, list):
        for pos, r in enumerate(items, 1):
            if not isinstance(r, dict):
                continue
            results.append({
                "url": r.get("url", ""),
                "raw_content": r.get("raw_content", ""),
                "position": pos,
            })
    fail_items = data.get("failed_results") if isinstance(data, dict) else None
    if isinstance(fail_items, list):
        for r in fail_items:
            if isinstance(r, dict):
                failed.append({
                    "url": r.get("url", ""),
                    "error": r.get("error", "unknown"),
                })
    return results, failed


def _handle_search(args: argparse.Namespace, api_key: str, started_at: float) -> int:
    """Handle --type search requests."""
    def elapsed_ms():
        return int((time.time() - started_at) * 1000)

    query = (args.query or "").strip()
    if not query:
        _emit(_make_output(
            query="", search_type="search", results=[],
            error="validation_error: --query is required",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/search"}))
        return 2

    if len(query) > MAX_QUERY_LENGTH:
        _emit(_make_output(
            query=query[:MAX_QUERY_LENGTH], search_type="search", results=[],
            error=f"validation_error: query exceeds {MAX_QUERY_LENGTH} chars ({len(query)} given)",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/search"}))
        return 2

    # Build request body
    body = {
        "query": query,
        "topic": args.topic,
        "search_depth": args.search_depth,
        "max_results": max(0, min(args.max_results, 20)),
    }
    if args.time_range:
        body["time_range"] = args.time_range
    if args.start_date:
        body["start_date"] = args.start_date
    if args.end_date:
        body["end_date"] = args.end_date
    if args.include_domains:
        body["include_domains"] = [d.strip() for d in args.include_domains.split(",") if d.strip()]
    if args.exclude_domains:
        body["exclude_domains"] = [d.strip() for d in args.exclude_domains.split(",") if d.strip()]
    if args.country and args.topic == "general":
        body["country"] = args.country
    if args.include_images:
        body["include_images"] = True
    if args.include_raw_content:
        body["include_raw_content"] = True

    url = _BASE_URL + "/search"

    try:
        status, data = _http_post_json(url, body, api_key, args.timeout)
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        try:
            raw_body = e.read(_MAX_RESPONSE_BYTES) or b""
            data = json.loads(raw_body.decode("utf-8", errors="replace")) if raw_body else {}
        except Exception:
            data = {}
        error_code = _detect_error_code(status)
        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("message") or data.get("error") or "")
        # Rotate key on quota/auth exhaustion
        if error_code in ("quota_exceeded", "auth_error", "payg_cap_exceeded"):
            rotate_key(args.key_file)
        _emit(_make_output(
            query=query, search_type="search", results=[],
            error=f"{error_code}: {msg}" if msg else f"http_{status}",
            http_status=status, error_code=error_code,
            meta={"duration_ms": elapsed_ms(), "endpoint": "/search",
                  "topic": args.topic, "search_depth": args.search_depth}))
        return 1
    except Exception as e:
        _emit(_make_output(
            query=query, search_type="search", results=[],
            error=f"network_error: {str(e)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/search"}))
        return 1

    results = _format_search_results(data)

    out = _make_output(
        query=query, search_type="search", results=results,
        error=None, http_status=status, error_code=None,
        meta={"duration_ms": elapsed_ms(), "endpoint": "/search",
              "topic": args.topic, "search_depth": args.search_depth,
              "result_count": len(results)})
    if args.raw:
        out["raw_response"] = data
    if args.debug:
        out["meta"]["debug"] = {"body": {k: v for k, v in body.items() if k != "query"}}
    _emit(out)
    return 0


def _handle_extract(args: argparse.Namespace, api_key: str, started_at: float) -> int:
    """Handle --type extract requests."""
    def elapsed_ms():
        return int((time.time() - started_at) * 1000)

    if not args.urls:
        _emit(_make_output(
            query="", search_type="extract", results=[],
            error="validation_error: --urls is required for extract",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/extract"}))
        return 2

    url_list = [u.strip() for u in args.urls.split(",") if u.strip()]
    if not url_list:
        _emit(_make_output(
            query="", search_type="extract", results=[],
            error="validation_error: --urls must contain at least one URL",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/extract"}))
        return 2

    if len(url_list) > 20:
        _emit(_make_output(
            query="", search_type="extract", results=[],
            error="validation_error: max 20 URLs per extract request",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/extract"}))
        return 2

    # SSRF check each URL
    safe_urls = []
    ssrf_blocked = []
    for u in url_list:
        if _is_ssrf_safe(u):
            safe_urls.append(u)
        else:
            ssrf_blocked.append({"url": u, "error": "blocked_by_ssrf_guard"})

    if not safe_urls:
        out = _make_output(
            query="", search_type="extract", results=[],
            error="all URLs blocked by SSRF guard",
            http_status=None, error_code="config_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/extract",
                  "urls_requested": len(url_list)})
        out["failed_results"] = ssrf_blocked
        _emit(out)
        return 2

    body = {
        "urls": safe_urls,
        "extract_depth": args.extract_depth,
        "format": args.extract_format,
    }

    api_url = _BASE_URL + "/extract"

    try:
        status, data = _http_post_json(api_url, body, api_key, args.timeout)
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        try:
            raw_body = e.read(_MAX_RESPONSE_BYTES) or b""
            data = json.loads(raw_body.decode("utf-8", errors="replace")) if raw_body else {}
        except Exception:
            data = {}
        error_code = _detect_error_code(status)
        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("message") or data.get("error") or "")
        # Rotate key on quota/auth exhaustion
        if error_code in ("quota_exceeded", "auth_error", "payg_cap_exceeded"):
            rotate_key(args.key_file)
        out = _make_output(
            query="", search_type="extract", results=[],
            error=f"{error_code}: {msg}" if msg else f"http_{status}",
            http_status=status, error_code=error_code,
            meta={"duration_ms": elapsed_ms(), "endpoint": "/extract",
                  "urls_requested": len(url_list)})
        out["failed_results"] = ssrf_blocked
        _emit(out)
        return 1
    except Exception as e:
        out = _make_output(
            query="", search_type="extract", results=[],
            error=f"network_error: {str(e)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/extract",
                  "urls_requested": len(url_list)})
        out["failed_results"] = ssrf_blocked
        _emit(out)
        return 1

    results, api_failed = _format_extract_results(data)
    all_failed = ssrf_blocked + api_failed

    out = _make_output(
        query="", search_type="extract", results=results,
        error=None, http_status=status, error_code=None,
        meta={"duration_ms": elapsed_ms(), "endpoint": "/extract",
              "urls_requested": len(url_list), "urls_extracted": len(results),
              "urls_failed": len(all_failed)})
    out["failed_results"] = all_failed
    if args.raw:
        out["raw_response"] = data
    _emit(out)
    return 0


def main() -> int:
    """CLI entry point."""
    started_at = time.time()

    parser = argparse.ArgumentParser(
        description="Tavily Search (OpenClaw skill) — web search with topic routing + URL extraction"
    )
    parser.add_argument("--query", "-q", default=None, help="Search query (<=400 chars)")
    parser.add_argument("--type", "-t", dest="search_type",
                        choices=["search", "extract"], default="search",
                        help="Operation type (default: search)")
    parser.add_argument("--topic", choices=["general", "news", "finance"],
                        default="general",
                        help="Search topic routing (default: general)")
    parser.add_argument("--max-results", "-n", type=int, default=10,
                        help="Max results 0-20 (default: 10)")
    parser.add_argument("--search-depth", choices=["ultra-fast", "fast", "basic", "advanced"],
                        default="basic", help="Search depth (default: basic)")
    parser.add_argument("--time-range", default=None,
                        help="Time filter: day, week, month, year")
    parser.add_argument("--start-date", default=None, help="Results after YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="Results before YYYY-MM-DD")
    parser.add_argument("--include-domains", default=None,
                        help="Comma-separated domains to include (max 300)")
    parser.add_argument("--exclude-domains", default=None,
                        help="Comma-separated domains to exclude (max 150)")
    parser.add_argument("--country", default=None,
                        help="Boost results from country (general topic only)")
    parser.add_argument("--include-images", action="store_true",
                        help="Include image results")
    parser.add_argument("--include-raw-content", action="store_true",
                        help="Include full page content per result")
    # Extract flags
    parser.add_argument("--urls", default=None,
                        help="Comma-separated URLs for extract (max 20)")
    parser.add_argument("--extract-depth", choices=["basic", "advanced"],
                        default="basic", help="Extract depth (default: basic)")
    parser.add_argument("--extract-format", choices=["markdown", "text"],
                        default="markdown", help="Extract output format (default: markdown)")
    # Key file
    parser.add_argument("--key-file", default=os.path.join(DEFAULT_KEY_DIR, "tavily.json"),
                        help=f"Path to key file for key rotation (default: {DEFAULT_KEY_DIR}/tavily.json)")
    # Shared flags
    parser.add_argument("--timeout", type=int, default=30,
                        help="HTTP timeout seconds (default: 30)")
    parser.add_argument("--raw", action="store_true",
                        help="Include raw API response in output")
    parser.add_argument("--debug", action="store_true",
                        help="Include diagnostics in meta")
    args = parser.parse_args()

    def elapsed_ms():
        return int((time.time() - started_at) * 1000)

    # --- Get API key (key file > env var) ---
    _all_keys = load_keys(args.key_file, env_fallback="TAVILY_API_KEY")
    if not _all_keys:
        _emit(_make_output(
            query=args.query or "", search_type=args.search_type, results=[],
            error="config_error: TAVILY_API_KEY not set (checked key file and env)",
            http_status=None, error_code="config_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": None}))
        return 2
    api_key = _all_keys[0]

    # --- Route by type ---
    if args.search_type == "search":
        return _handle_search(args, api_key, started_at)
    elif args.search_type == "extract":
        return _handle_extract(args, api_key, started_at)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as _exc:
        _err = {
            "provider": "tavily",
            "query": "",
            "type": "search",
            "results": [],
            "error": f"internal_error: {str(_exc)[:200]}",
            "http_status": None,
            "error_code": "internal_error",
            "meta": {},
        }
        print(json.dumps(_err, separators=(",", ":")))
        if "--debug" in sys.argv:
            import traceback
            traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)
