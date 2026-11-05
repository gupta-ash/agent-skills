#!/usr/bin/env python3
"""Exa AI search skill for OpenClaw — neural web search with category routing + URL content extraction.

Pure stdlib Python (3.8+). No external dependencies.
POST/GET to https://api.exa.ai endpoints.
Always emits structured JSON to stdout. Diagnostics to stderr with --debug.

v1.0.0 — search with category routing (news, company, research paper, tweet,
          people, personal site, financial report) + inline content retrieval.
v1.1.0 — findSimilar, answer with citations, research with async polling.
v2.0.0 — deep search types (fast/deep/deep-reasoning/instant), structured output.
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

VERSION = "2.0.0"
_BASE_URL = "https://api.exa.ai"
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB cap on response.read()
_DEBUG = False

# SSRF protection — block private/reserved IP ranges
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

    Parses URL, validates scheme (http/https only), resolves DNS,
    checks all resolved IPs against blocklist.
    Returns True if safe, False if blocked.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            _debug(f"SSRF check: blocked non-HTTP scheme {parsed.scheme}")
            return False
        hostname = parsed.hostname
        if not hostname:
            _debug(f"SSRF check: no hostname in URL: {url[:100]}")
            return False
        addr_infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC,
                                         socket.SOCK_STREAM)
    except (socket.gaierror, OSError, ValueError) as e:
        _debug(f"SSRF check: DNS resolution failed for {url[:100]}: {e}")
        return False

    for family, _, _, _, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = _normalize_ip(ipaddress.ip_address(ip_str))
        except ValueError:
            continue
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                _debug(f"SSRF blocked: resolves to {ip_str} (in {network})")
                return False

    return True


def _debug(msg: str) -> None:
    """Print diagnostic message to stderr if --debug is active."""
    if _DEBUG:
        print(f"[exa:debug] {msg}", file=sys.stderr)


def _detect_error_code(status: int) -> str:
    """Map HTTP status code to a structured error code string."""
    if status == 401:
        return "auth_error"
    if status == 402:
        return "quota_exceeded"
    if status == 403:
        return "access_denied"
    if status == 429:
        return "rate_limited"
    if 400 <= status <= 499:
        return "client_error"
    if 500 <= status <= 599:
        return "server_error"
    return "unknown_error"


def _make_output(query: str, search_type: str, results: List[Dict[str, Any]],
                 error: Optional[str], http_status: Optional[int],
                 error_code: Optional[str],
                 meta: Dict[str, Any]) -> Dict[str, Any]:
    """Build the standard output envelope."""
    meta.setdefault("version", VERSION)
    return {
        "provider": "exa",
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
    """POST JSON to Exa API with x-api-key auth.

    Returns (http_status, parsed_response_body).
    Raises urllib.error.HTTPError or urllib.error.URLError on failure.
    """
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("x-api-key", api_key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("Accept-Encoding", "gzip")
    req.add_header("User-Agent", f"openclaw-exa/{VERSION}")

    _debug(f"POST {url} (timeout={timeout_s}s, body_bytes={len(data)})")

    with urllib.request.urlopen(req, timeout=timeout_s) as response:
        status = response.getcode()
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError(f"response exceeds {_MAX_RESPONSE_BYTES} bytes")
        enc = response.headers.get("Content-Encoding", "")
        if "gzip" in enc.lower() or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return status, json.loads(raw.decode("utf-8", errors="replace"))


def _http_get_json(url: str, api_key: str,
                   timeout_s: int = 30) -> Tuple[int, Dict[str, Any]]:
    """GET JSON from Exa API with x-api-key auth.

    Returns (http_status, parsed_response_body).
    Raises urllib.error.HTTPError or urllib.error.URLError on failure.
    """
    req = urllib.request.Request(url, method="GET")
    req.add_header("x-api-key", api_key)
    req.add_header("Accept", "application/json")
    req.add_header("Accept-Encoding", "gzip")
    req.add_header("User-Agent", f"openclaw-exa/{VERSION}")

    _debug(f"GET {url} (timeout={timeout_s}s)")

    with urllib.request.urlopen(req, timeout=timeout_s) as response:
        status = response.getcode()
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError(f"response exceeds {_MAX_RESPONSE_BYTES} bytes")
        enc = response.headers.get("Content-Encoding", "")
        if "gzip" in enc.lower() or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return status, json.loads(raw.decode("utf-8", errors="replace"))


def _format_search_results(data: Dict[str, Any], content_enabled: bool,
                           content_type: str) -> List[Dict[str, Any]]:
    """Extract results from Exa API search response and normalize.

    When content_enabled is True, includes text/highlights/summary fields
    based on content_type.
    """
    results = []
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return results
    for pos, r in enumerate(items, 1):
        if not isinstance(r, dict):
            continue
        entry: Dict[str, Any] = {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "published_date": r.get("publishedDate"),
            "author": r.get("author"),
            "score": r.get("score"),
            "position": pos,
        }
        # Content fields — only present when --content is used
        if content_enabled:
            if content_type == "text":
                entry["content_excerpt"] = r.get("text")
            if content_type == "highlights":
                entry["highlights"] = r.get("highlights", [])
                entry["highlight_scores"] = r.get("highlightScores", [])
            if content_type == "summary":
                entry["summary"] = r.get("summary")
        results.append(entry)
    return results


def _handle_search(args, api_key, started_at):
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

    # Validate deep-search-only flags
    _deep_types = ("deep", "deep-reasoning")
    if args.output_schema and args.search_type not in _deep_types:
        _emit(_make_output(
            query=query, search_type="search", results=[],
            error="validation_error: --output-schema requires --search-type deep or deep-reasoning",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/search"}))
        return 2
    if args.additional_queries and args.search_type not in _deep_types:
        _emit(_make_output(
            query=query, search_type="search", results=[],
            error="validation_error: --additional-queries requires --search-type deep or deep-reasoning",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/search"}))
        return 2

    # Parse and validate --output-schema JSON
    parsed_schema = None
    if args.output_schema:
        try:
            parsed_schema = json.loads(args.output_schema)
        except (json.JSONDecodeError, ValueError) as e:
            _emit(_make_output(
                query=query, search_type="search", results=[],
                error=f"validation_error: --output-schema is not valid JSON: {str(e)[:100]}",
                http_status=None, error_code="validation_error",
                meta={"duration_ms": elapsed_ms(), "endpoint": "/search"}))
            return 2

    # Build request body
    body: Dict[str, Any] = {
        "query": query,
        "type": args.search_type,  # auto/neural/fast/deep/deep-reasoning/instant
        "numResults": args.count,
    }
    if args.category:
        body["category"] = args.category
    if args.start_date:
        body["startPublishedDate"] = args.start_date
    if args.end_date:
        body["endPublishedDate"] = args.end_date
    if args.start_crawl_date:
        body["startCrawlDate"] = args.start_crawl_date
    if args.end_crawl_date:
        body["endCrawlDate"] = args.end_crawl_date
    if args.include_domains:
        body["includeDomains"] = [d.strip() for d in args.include_domains.split(",") if d.strip()]
    if args.exclude_domains:
        body["excludeDomains"] = [d.strip() for d in args.exclude_domains.split(",") if d.strip()]
    if args.include_text:
        body["includeText"] = [args.include_text]
    if args.exclude_text:
        body["excludeText"] = [args.exclude_text]

    # Inline content retrieval
    if args.content:
        contents_config: Dict[str, Any] = {}
        if args.content_type == "text":
            contents_config["text"] = {"maxCharacters": args.content_max_chars}
        elif args.content_type == "highlights":
            contents_config["highlights"] = {"maxCharacters": args.content_max_chars}
        elif args.content_type == "summary":
            contents_config["summary"] = {"query": query}
        if args.max_age_hours is not None:
            contents_config["maxAgeHours"] = args.max_age_hours
        body["contents"] = contents_config

    # Deep search parameters
    if parsed_schema is not None:
        body["outputSchema"] = parsed_schema
    if args.additional_queries:
        body["additionalQueries"] = [q.strip() for q in args.additional_queries.split(",") if q.strip()]

    url = _BASE_URL + "/search"
    _debug(f"POST {url}")

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
        if error_code in ("quota_exceeded", "auth_error"):
            rotate_key(args.key_file)
        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("error") or data.get("message") or "")
        _emit(_make_output(
            query=query, search_type="search", results=[],
            error=f"{error_code}: {msg}" if msg else f"http_{status}",
            http_status=status, error_code=error_code,
            meta={"duration_ms": elapsed_ms(), "endpoint": "/search",
                  "category": args.category, "search_type": args.search_type}))
        return 1
    except urllib.error.URLError as e:
        _emit(_make_output(
            query=query, search_type="search", results=[],
            error=f"network_error: {str(e.reason)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/search"}))
        return 1
    except Exception as e:
        _emit(_make_output(
            query=query, search_type="search", results=[],
            error=f"network_error: {str(e)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/search"}))
        return 1

    results = _format_search_results(data, args.content, args.content_type)

    # Deep search output (type=deep/deep-reasoning)
    structured_output = None
    grounding = None
    if isinstance(data, dict) and "output" in data:
        output_obj = data["output"]
        if isinstance(output_obj, dict):
            structured_output = output_obj.get("content")
            grounding = output_obj.get("grounding")
        elif isinstance(output_obj, str):
            structured_output = output_obj

    # Extract cost
    cost = None
    cost_data = data.get("costDollars") if isinstance(data, dict) else None
    if isinstance(cost_data, dict):
        cost = cost_data.get("total")

    meta: Dict[str, Any] = {
        "duration_ms": elapsed_ms(),
        "endpoint": "/search",
        "category": args.category,
        "search_type": args.search_type,
        "result_count": len(results),
        "content_enabled": args.content,
    }
    if args.content:
        meta["content_type"] = args.content_type
    if cost is not None:
        meta["cost_dollars"] = cost
    if args.search_type in ("deep", "deep-reasoning"):
        meta["has_structured_output"] = structured_output is not None

    out = _make_output(
        query=query, search_type="search", results=results,
        error=None, http_status=status, error_code=None,
        meta=meta)
    if args.raw:
        out["raw_response"] = data
    if structured_output is not None:
        out["structured_output"] = structured_output
    if grounding is not None:
        out["grounding"] = grounding
    if args.debug:
        sanitized_body = {k: v for k, v in body.items() if k != "query"}
        out["meta"]["debug"] = {"body": sanitized_body}
    _emit(out)
    return 0


def _handle_contents(args, api_key, started_at):
    """Handle --type contents requests — fetch content for specific URLs."""
    def elapsed_ms():
        return int((time.time() - started_at) * 1000)

    # 1. Validate --urls
    if not args.urls:
        _emit(_make_output(
            query="", search_type="contents", results=[],
            error="validation_error: --urls is required for contents mode",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/contents"}))
        return 2

    url_list = [u.strip() for u in args.urls.split(",") if u.strip()]
    if not url_list:
        _emit(_make_output(
            query="", search_type="contents", results=[],
            error="validation_error: --urls must contain at least one URL",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/contents"}))
        return 2

    if len(url_list) > 100:
        _emit(_make_output(
            query="", search_type="contents", results=[],
            error="validation_error: max 100 URLs per contents request",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/contents"}))
        return 2

    # 2. SSRF check each URL
    safe_urls = []
    ssrf_blocked = []
    for u in url_list:
        if _is_ssrf_safe(u):
            safe_urls.append(u)
        else:
            ssrf_blocked.append({"url": u, "error": "blocked_by_ssrf_guard"})

    if not safe_urls:
        out = _make_output(
            query="", search_type="contents", results=[],
            error="all URLs blocked by SSRF guard",
            http_status=None, error_code="config_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/contents",
                  "urls_requested": len(url_list)})
        out["failed_results"] = ssrf_blocked
        _emit(out)
        return 2

    # 3. Build request body
    body: Dict[str, Any] = {"urls": safe_urls}

    # Content retrieval config — for contents mode, default to text
    ct = args.content_type  # text/highlights/summary
    if ct == "text":
        body["text"] = {"maxCharacters": args.content_max_chars}
    elif ct == "highlights":
        body["highlights"] = {"maxCharacters": args.content_max_chars}
    elif ct == "summary":
        body["summary"] = {"query": "summarize this content"}

    if args.max_age_hours is not None:
        body["maxAgeHours"] = args.max_age_hours

    # 4. POST to /contents
    api_url = _BASE_URL + "/contents"
    _debug(f"POST {api_url} ({len(safe_urls)} URLs, content_type={ct})")

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
        if error_code in ("quota_exceeded", "auth_error"):
            rotate_key(args.key_file)
        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("error") or data.get("message") or "")
        out = _make_output(
            query="", search_type="contents", results=[],
            error=f"{error_code}: {msg}" if msg else f"http_{status}",
            http_status=status, error_code=error_code,
            meta={"duration_ms": elapsed_ms(), "endpoint": "/contents",
                  "urls_requested": len(url_list)})
        out["failed_results"] = ssrf_blocked
        _emit(out)
        return 1
    except urllib.error.URLError as e:
        out = _make_output(
            query="", search_type="contents", results=[],
            error=f"network_error: {str(e.reason)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/contents",
                  "urls_requested": len(url_list)})
        out["failed_results"] = ssrf_blocked
        _emit(out)
        return 1
    except Exception as e:
        out = _make_output(
            query="", search_type="contents", results=[],
            error=f"network_error: {str(e)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/contents",
                  "urls_requested": len(url_list)})
        out["failed_results"] = ssrf_blocked
        _emit(out)
        return 1

    # 5. Parse results
    results = []
    items = data.get("results") if isinstance(data, dict) else None
    if isinstance(items, list):
        for pos, r in enumerate(items, 1):
            if not isinstance(r, dict):
                continue
            entry: Dict[str, Any] = {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "published_date": r.get("publishedDate"),
                "author": r.get("author"),
                "position": pos,
            }
            if ct == "text":
                entry["content_excerpt"] = r.get("text")
            if ct == "highlights":
                entry["highlights"] = r.get("highlights", [])
                entry["highlight_scores"] = r.get("highlightScores", [])
            if ct == "summary":
                entry["summary"] = r.get("summary")
            results.append(entry)

    # 6. Parse statuses for failures
    api_failed = []
    statuses = data.get("statuses") if isinstance(data, dict) else None
    if isinstance(statuses, list):
        for s in statuses:
            if isinstance(s, dict) and s.get("status") == "error":
                error_info = s.get("error", {})
                api_failed.append({
                    "url": s.get("id", ""),
                    "error": error_info.get("tag", "unknown") if isinstance(error_info, dict) else str(error_info),
                })

    all_failed = ssrf_blocked + api_failed

    # 7. Build output
    cost = None
    cost_data = data.get("costDollars") if isinstance(data, dict) else None
    if isinstance(cost_data, dict):
        cost = cost_data.get("total")

    meta: Dict[str, Any] = {
        "duration_ms": elapsed_ms(),
        "endpoint": "/contents",
        "urls_requested": len(url_list),
        "urls_fetched": len(results),
        "urls_failed": len(all_failed),
        "content_type": ct,
    }
    if cost is not None:
        meta["cost_dollars"] = cost

    out = _make_output(
        query="", search_type="contents", results=results,
        error=None, http_status=status, error_code=None,
        meta=meta)
    out["failed_results"] = all_failed
    if args.raw:
        out["raw_response"] = data
    if args.debug:
        sanitized_body = {k: v for k, v in body.items()}
        out["meta"]["debug"] = {"body": sanitized_body}
    _emit(out)
    return 0


def _handle_similar(args, api_key, started_at):
    """Handle --type similar requests — find pages similar to a given URL."""
    def elapsed_ms():
        return int((time.time() - started_at) * 1000)

    source_url = (args.url or "").strip()
    if not source_url:
        _emit(_make_output(
            query="", search_type="similar", results=[],
            error="validation_error: --url is required for similar mode",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/findSimilar"}))
        return 2

    # Build request body
    body: Dict[str, Any] = {
        "url": source_url,
        "numResults": args.count,
    }
    if args.exclude_source_domain:
        body["excludeSourceDomain"] = True
    if args.category:
        body["category"] = args.category
    if args.start_date:
        body["startPublishedDate"] = args.start_date
    if args.end_date:
        body["endPublishedDate"] = args.end_date
    if args.start_crawl_date:
        body["startCrawlDate"] = args.start_crawl_date
    if args.end_crawl_date:
        body["endCrawlDate"] = args.end_crawl_date
    if args.include_domains:
        body["includeDomains"] = [d.strip() for d in args.include_domains.split(",") if d.strip()]
    if args.exclude_domains:
        body["excludeDomains"] = [d.strip() for d in args.exclude_domains.split(",") if d.strip()]
    if args.include_text:
        body["includeText"] = [args.include_text]
    if args.exclude_text:
        body["excludeText"] = [args.exclude_text]

    # Inline content retrieval
    if args.content:
        contents_config: Dict[str, Any] = {}
        if args.content_type == "text":
            contents_config["text"] = {"maxCharacters": args.content_max_chars}
        elif args.content_type == "highlights":
            contents_config["highlights"] = {"maxCharacters": args.content_max_chars}
        elif args.content_type == "summary":
            contents_config["summary"] = {"query": source_url}
        if args.max_age_hours is not None:
            contents_config["maxAgeHours"] = args.max_age_hours
        body["contents"] = contents_config

    url = _BASE_URL + "/findSimilar"
    _debug(f"POST {url}")

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
        if error_code in ("quota_exceeded", "auth_error"):
            rotate_key(args.key_file)
        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("error") or data.get("message") or "")
        _emit(_make_output(
            query=source_url, search_type="similar", results=[],
            error=f"{error_code}: {msg}" if msg else f"http_{status}",
            http_status=status, error_code=error_code,
            meta={"duration_ms": elapsed_ms(), "endpoint": "/findSimilar",
                  "source_url": source_url}))
        return 1
    except urllib.error.URLError as e:
        _emit(_make_output(
            query=source_url, search_type="similar", results=[],
            error=f"network_error: {str(e.reason)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/findSimilar",
                  "source_url": source_url}))
        return 1
    except Exception as e:
        _emit(_make_output(
            query=source_url, search_type="similar", results=[],
            error=f"network_error: {str(e)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/findSimilar",
                  "source_url": source_url}))
        return 1

    results = _format_search_results(data, args.content, args.content_type)

    # Extract cost
    cost = None
    cost_data = data.get("costDollars") if isinstance(data, dict) else None
    if isinstance(cost_data, dict):
        cost = cost_data.get("total")

    meta: Dict[str, Any] = {
        "duration_ms": elapsed_ms(),
        "endpoint": "/findSimilar",
        "source_url": source_url,
        "exclude_source_domain": args.exclude_source_domain,
        "result_count": len(results),
        "content_enabled": args.content,
    }
    if args.content:
        meta["content_type"] = args.content_type
    if cost is not None:
        meta["cost_dollars"] = cost

    out = _make_output(
        query=source_url, search_type="similar", results=results,
        error=None, http_status=status, error_code=None,
        meta=meta)
    if args.raw:
        out["raw_response"] = data
    if args.debug:
        sanitized_body = {k: v for k, v in body.items() if k != "url"}
        out["meta"]["debug"] = {"body": sanitized_body}
    _emit(out)
    return 0


def _handle_answer(args, api_key, started_at):
    """Handle --type answer requests — get an AI-generated answer with citations."""
    def elapsed_ms():
        return int((time.time() - started_at) * 1000)

    query = (args.query or "").strip()
    if not query:
        _emit(_make_output(
            query="", search_type="answer", results=[],
            error="validation_error: --query is required for answer mode",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/answer"}))
        return 2

    # Build request body
    body: Dict[str, Any] = {"query": query}
    if args.include_source_text:
        body["text"] = True

    url = _BASE_URL + "/answer"
    _debug(f"POST {url}")

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
        if error_code in ("quota_exceeded", "auth_error"):
            rotate_key(args.key_file)
        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("error") or data.get("message") or "")
        _emit(_make_output(
            query=query, search_type="answer", results=[],
            error=f"{error_code}: {msg}" if msg else f"http_{status}",
            http_status=status, error_code=error_code,
            meta={"duration_ms": elapsed_ms(), "endpoint": "/answer"}))
        return 1
    except urllib.error.URLError as e:
        _emit(_make_output(
            query=query, search_type="answer", results=[],
            error=f"network_error: {str(e.reason)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/answer"}))
        return 1
    except Exception as e:
        _emit(_make_output(
            query=query, search_type="answer", results=[],
            error=f"network_error: {str(e)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/answer"}))
        return 1

    # Extract answer text
    answer_text = ""
    if isinstance(data, dict):
        answer_text = data.get("answer", "")

    # Extract citations
    results = []
    citations = data.get("citations") if isinstance(data, dict) else None
    if isinstance(citations, list):
        for pos, c in enumerate(citations, 1):
            if not isinstance(c, dict):
                continue
            entry: Dict[str, Any] = {
                "position": pos,
                "url": c.get("url", ""),
                "title": c.get("title", ""),
                "published_date": c.get("publishedDate"),
                "author": c.get("author"),
            }
            if args.include_source_text:
                entry["content_excerpt"] = c.get("text")
            results.append(entry)

    # Extract cost
    cost = None
    cost_data = data.get("costDollars") if isinstance(data, dict) else None
    if isinstance(cost_data, dict):
        cost = cost_data.get("total")

    meta: Dict[str, Any] = {
        "duration_ms": elapsed_ms(),
        "endpoint": "/answer",
        "citation_count": len(results),
    }
    if cost is not None:
        meta["cost_dollars"] = cost

    out = _make_output(
        query=query, search_type="answer", results=results,
        error=None, http_status=status, error_code=None,
        meta=meta)
    out["answer"] = answer_text
    if args.raw:
        out["raw_response"] = data
    if args.debug:
        sanitized_body = {k: v for k, v in body.items() if k != "query"}
        out["meta"]["debug"] = {"body": sanitized_body}
    _emit(out)
    return 0


def _handle_research(args, api_key, started_at):
    """Handle --type research requests — async deep research with polling."""
    def elapsed_ms():
        return int((time.time() - started_at) * 1000)

    # --- Status check mode (--research-id) ---
    if args.research_id:
        return _poll_research(args.research_id, api_key, args.timeout,
                              started_at, emit_final=True,
                              key_file=args.key_file)

    # --- Create mode ---
    query = (args.query or "").strip()
    if not query:
        _emit(_make_output(
            query="", search_type="research", results=[],
            error="validation_error: --query is required for research mode",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/research/v1"}))
        return 2

    # POST to create research task
    body: Dict[str, Any] = {
        "instructions": query,
        "model": args.research_model,
    }

    create_url = _BASE_URL + "/research/v1"
    _debug(f"POST {create_url} (model={args.research_model})")

    try:
        status, data = _http_post_json(create_url, body, api_key, args.timeout)
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        try:
            raw_body = e.read(_MAX_RESPONSE_BYTES) or b""
            data = json.loads(raw_body.decode("utf-8", errors="replace")) if raw_body else {}
        except Exception:
            data = {}
        error_code = _detect_error_code(status)
        if error_code in ("quota_exceeded", "auth_error"):
            rotate_key(args.key_file)
        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("error") or data.get("message") or "")
        _emit(_make_output(
            query=query, search_type="research", results=[],
            error=f"{error_code}: {msg}" if msg else f"http_{status}",
            http_status=status, error_code=error_code,
            meta={"duration_ms": elapsed_ms(), "endpoint": "/research/v1",
                  "research_model": args.research_model}))
        return 1
    except urllib.error.URLError as e:
        _emit(_make_output(
            query=query, search_type="research", results=[],
            error=f"network_error: {str(e.reason)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/research/v1"}))
        return 1
    except Exception as e:
        _emit(_make_output(
            query=query, search_type="research", results=[],
            error=f"network_error: {str(e)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/research/v1"}))
        return 1

    research_id = data.get("researchId", "") if isinstance(data, dict) else ""
    if not research_id:
        _emit(_make_output(
            query=query, search_type="research", results=[],
            error="server_error: no researchId in response",
            http_status=status, error_code="server_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/research/v1"}))
        return 1

    _debug(f"Research task created: {research_id}")

    # --- Async mode: return immediately ---
    if args.async_mode:
        meta: Dict[str, Any] = {
            "duration_ms": elapsed_ms(),
            "endpoint": "/research/v1",
            "research_id": research_id,
            "research_status": "pending",
            "research_model": args.research_model,
        }
        out = _make_output(
            query=query, search_type="research", results=[],
            error=None, http_status=status, error_code=None,
            meta=meta)
        out["answer"] = None
        if args.raw:
            out["raw_response"] = data
        _emit(out)
        return 0

    # --- Blocking mode: poll until done ---
    _debug(f"Polling every {args.poll_interval}s, max {args.max_wait}s")
    waited = 0
    while waited < args.max_wait:
        time.sleep(args.poll_interval)
        waited += args.poll_interval
        _debug(f"Poll {waited}s/{args.max_wait}s")

        try:
            poll_status, poll_data = _http_get_json(
                f"{_BASE_URL}/research/v1/{research_id}",
                api_key, args.timeout)
        except Exception as e:
            _debug(f"Poll error: {e}")
            continue  # Retry on transient errors

        research_status = poll_data.get("status", "") if isinstance(poll_data, dict) else ""

        if research_status == "completed":
            return _emit_research_result(query, research_id, poll_data,
                                          args, started_at)

        if research_status in ("failed", "canceled"):
            _emit(_make_output(
                query=query, search_type="research", results=[],
                error=f"research_{research_status}: task {research_id} {research_status}",
                http_status=None, error_code="server_error",
                meta={"duration_ms": elapsed_ms(), "endpoint": "/research/v1",
                      "research_id": research_id,
                      "research_status": research_status,
                      "research_model": args.research_model}))
            return 1

    # Timeout
    _emit(_make_output(
        query=query, search_type="research", results=[],
        error=f"timeout_error: research {research_id} not complete after {args.max_wait}s — use --research-id to check later",
        http_status=None, error_code="timeout_error",
        meta={"duration_ms": elapsed_ms(), "endpoint": "/research/v1",
              "research_id": research_id,
              "research_status": "running",
              "research_model": args.research_model}))
    return 1


def _poll_research(research_id: str, api_key: str, timeout: int,
                   started_at: float, emit_final: bool = True,
                   key_file: Optional[str] = None) -> int:
    """Poll a research task by ID and emit its current status."""
    def elapsed_ms():
        return int((time.time() - started_at) * 1000)

    try:
        status, data = _http_get_json(
            f"{_BASE_URL}/research/v1/{research_id}",
            api_key, timeout)
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        error_code = _detect_error_code(status)
        if error_code in ("quota_exceeded", "auth_error") and key_file:
            rotate_key(key_file)
        _emit(_make_output(
            query="", search_type="research", results=[],
            error=f"{error_code}: research task {research_id}",
            http_status=status, error_code=error_code,
            meta={"duration_ms": elapsed_ms(), "endpoint": "/research/v1",
                  "research_id": research_id}))
        return 1
    except urllib.error.URLError as e:
        _emit(_make_output(
            query="", search_type="research", results=[],
            error=f"network_error: {str(e.reason)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/research/v1",
                  "research_id": research_id}))
        return 1
    except Exception as e:
        _emit(_make_output(
            query="", search_type="research", results=[],
            error=f"network_error: {str(e)[:200]}",
            http_status=None, error_code="network_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": "/research/v1",
                  "research_id": research_id}))
        return 1

    research_status = data.get("status", "") if isinstance(data, dict) else ""

    if research_status == "completed":
        query = data.get("instructions", "") if isinstance(data, dict) else ""
        return _emit_research_result(query, research_id, data, None, started_at)

    # Not complete — emit current status
    query = data.get("instructions", "") if isinstance(data, dict) else ""
    meta: Dict[str, Any] = {
        "duration_ms": elapsed_ms(),
        "endpoint": "/research/v1",
        "research_id": research_id,
        "research_status": research_status,
        "research_model": data.get("model", "") if isinstance(data, dict) else "",
    }
    out = _make_output(
        query=query, search_type="research", results=[],
        error=None, http_status=status, error_code=None,
        meta=meta)
    out["answer"] = None
    _emit(out)
    return 0


def _emit_research_result(query: str, research_id: str,
                           data: Dict[str, Any], args: Any,
                           started_at: float) -> int:
    """Emit a completed research result."""
    def elapsed_ms():
        return int((time.time() - started_at) * 1000)

    # Extract output
    output_obj = data.get("output") if isinstance(data, dict) else None
    answer = ""
    if isinstance(output_obj, dict):
        content = output_obj.get("content", "")
        if isinstance(content, str):
            answer = content
        elif isinstance(content, dict):
            answer = json.dumps(content, indent=2)
    elif isinstance(output_obj, str):
        answer = output_obj

    # Extract cost
    cost = None
    cost_data = data.get("costDollars") if isinstance(data, dict) else None
    if isinstance(cost_data, dict):
        cost = cost_data.get("total")

    meta: Dict[str, Any] = {
        "duration_ms": elapsed_ms(),
        "endpoint": "/research/v1",
        "research_id": research_id,
        "research_status": "completed",
        "research_model": data.get("model", "") if isinstance(data, dict) else "",
    }
    if cost is not None:
        meta["cost_dollars"] = cost
    if isinstance(cost_data, dict):
        if cost_data.get("numSearches") is not None:
            meta["num_searches"] = cost_data["numSearches"]
        if cost_data.get("numPages") is not None:
            meta["num_pages"] = cost_data["numPages"]

    out = _make_output(
        query=query, search_type="research", results=[],
        error=None, http_status=200, error_code=None,
        meta=meta)
    out["answer"] = answer
    if args and hasattr(args, 'raw') and args.raw:
        out["raw_response"] = data
    _emit(out)
    return 0


def main() -> int:
    """CLI entry point."""
    global _DEBUG
    started_at = time.time()

    parser = argparse.ArgumentParser(
        description="Exa AI search (OpenClaw skill) — neural web search with category routing + URL content extraction"
    )
    parser.add_argument("--query", "-q", default=None, help="Search query")
    parser.add_argument("--type", "-t", dest="mode",
                        choices=["search", "contents", "similar", "answer", "research"], default="search",
                        help="Operation mode (default: search)")
    parser.add_argument("--count", "-n", type=int, default=10,
                        help="Number of results 1-100 (default: 10)")
    parser.add_argument("--category", default=None,
                        choices=["news", "company", "research paper", "tweet",
                                 "people", "personal site", "financial report"],
                        help="Category filter")
    parser.add_argument("--search-type", default="auto",
                        choices=["auto", "neural", "fast", "deep", "deep-reasoning", "instant"],
                        help="Search algorithm (default: auto)")
    # Deep search flags (deep/deep-reasoning only)
    parser.add_argument("--output-schema", default=None,
                        help="JSON schema for structured output (deep/deep-reasoning only)")
    parser.add_argument("--additional-queries", default=None,
                        help="Comma-separated extra query variations (deep/deep-reasoning only)")
    # Content flags
    parser.add_argument("--content", action="store_true",
                        help="Enable inline content retrieval (off by default)")
    parser.add_argument("--content-type", default="text",
                        choices=["text", "highlights", "summary"],
                        help="Content type (default: text)")
    parser.add_argument("--content-max-chars", type=int, default=5000,
                        help="Max characters per result (default: 5000)")
    # Date filters
    parser.add_argument("--start-date", default=None,
                        help="Results published after (ISO 8601, e.g. 2026-03-01)")
    parser.add_argument("--end-date", default=None,
                        help="Results published before (ISO 8601)")
    parser.add_argument("--start-crawl-date", default=None,
                        help="Results crawled after (ISO 8601)")
    parser.add_argument("--end-crawl-date", default=None,
                        help="Results crawled before (ISO 8601)")
    # Domain filters
    parser.add_argument("--include-domains", default=None,
                        help="Comma-separated domains to include")
    parser.add_argument("--exclude-domains", default=None,
                        help="Comma-separated domains to exclude")
    # Text filters
    parser.add_argument("--include-text", default=None,
                        help="Text that must appear in results")
    parser.add_argument("--exclude-text", default=None,
                        help="Text that must not appear in results")
    # Content freshness
    parser.add_argument("--max-age-hours", type=int, default=None,
                        help="Content freshness: -1=cache, 0=always crawl, N=hour threshold")
    # Contents mode
    parser.add_argument("--urls", default=None,
                        help="Comma-separated URLs for contents mode")
    # Similar mode
    parser.add_argument("--url", default=None,
                        help="Source URL for similar mode")
    parser.add_argument("--exclude-source-domain", action="store_true",
                        help="Exclude results from source URL's domain (similar mode)")
    # Answer mode
    parser.add_argument("--include-source-text", action="store_true",
                        help="Include full source text in answer citations")
    # Research mode
    parser.add_argument("--research-model", default="exa-research",
                        choices=["exa-research-fast", "exa-research", "exa-research-pro"],
                        help="Research model (default: exa-research)")
    parser.add_argument("--async", dest="async_mode", action="store_true",
                        help="Return research ID immediately without waiting")
    parser.add_argument("--research-id", default=None,
                        help="Check status of existing research task")
    parser.add_argument("--poll-interval", type=int, default=5,
                        help="Seconds between status polls (default: 5)")
    parser.add_argument("--max-wait", type=int, default=300,
                        help="Max seconds to wait for research (default: 300)")
    # Shared
    parser.add_argument("--key-file", default=os.path.join(DEFAULT_KEY_DIR, "exa.json"),
                        help=f"Path to key file for key rotation (default: {DEFAULT_KEY_DIR}/exa.json)")
    parser.add_argument("--timeout", type=int, default=30,
                        help="HTTP timeout seconds (default: 30)")
    parser.add_argument("--raw", action="store_true",
                        help="Include raw API response in output")
    parser.add_argument("--debug", action="store_true",
                        help="Include diagnostics in meta")
    args = parser.parse_args()

    _DEBUG = args.debug

    def elapsed_ms():
        return int((time.time() - started_at) * 1000)

    # Validate count
    if args.count < 1 or args.count > 100:
        _emit(_make_output(
            query=args.query or "", search_type=args.mode, results=[],
            error=f"validation_error: --count must be 1-100, got {args.count}",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": None}))
        return 2

    # Get API key
    _all_keys = load_keys(args.key_file, env_fallback="EXA_API_KEY")
    if not _all_keys:
        _emit(_make_output(
            query=args.query or "", search_type=args.mode, results=[],
            error="config_error: EXA_API_KEY not set (checked key file and env)",
            http_status=None, error_code="config_error",
            meta={"duration_ms": elapsed_ms(), "endpoint": None}))
        return 2
    api_key = _all_keys[0]

    # Route by mode
    if args.mode == "search":
        return _handle_search(args, api_key, started_at)
    elif args.mode == "contents":
        return _handle_contents(args, api_key, started_at)
    elif args.mode == "similar":
        return _handle_similar(args, api_key, started_at)
    elif args.mode == "answer":
        return _handle_answer(args, api_key, started_at)
    elif args.mode == "research":
        return _handle_research(args, api_key, started_at)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as _exc:
        _err = {
            "provider": "exa",
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
