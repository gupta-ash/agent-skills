#!/usr/bin/env python3
"""Serper.dev search skill for OpenClaw — Google search — web, news, images, videos, places, shopping, scholar, patents, autocomplete.

Pure stdlib Python (3.8+). No external dependencies.
POST to https://google.serper.dev/{type} with JSON body.
Always emits structured JSON to stdout. Diagnostics to stderr with --debug.

v1.0.0 — web search (/search) + news (/news) with auto-detection.
v2.0.0 — all endpoints: images, videos, places, shopping, scholar, patents, autocomplete.
v3.0.0 — inline content extraction (--content flag) with SSRF guards.
"""

import argparse
import html
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Key file support — shared across all search skills
import sys as _sys
_shared_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "_shared")
if _shared_dir not in _sys.path:
    _sys.path.insert(0, _shared_dir)
from keyfile import load_keys, rotate_key, DEFAULT_KEY_DIR

VERSION = "3.0.0"
BASE_URL = "https://google.serper.dev"  # hardcoded — never configurable (SSRF protection)
MAX_QUERY_LENGTH = 2048

# Compiled regex for news keyword auto-detection
_NEWS_PATTERN = re.compile(
    r"\b(?:latest|breaking|news\s+about|news\s+on|today'?s|recent\s+news|current\s+events|headlines)\b",
    re.IGNORECASE,
)

_DEBUG = False

# Credits per endpoint (shopping=2, all others=1)
_CREDITS_DEFAULT = {
    "shopping": 2,
}

# SSRF protection — block private/reserved IP ranges for content extraction
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),             # "this network" (can alias localhost)
    ipaddress.ip_network("10.0.0.0/8"),             # RFC-1918 private
    ipaddress.ip_network("100.64.0.0/10"),          # carrier-grade NAT (RFC 6598)
    ipaddress.ip_network("127.0.0.0/8"),            # loopback IPv4
    ipaddress.ip_network("169.254.0.0/16"),         # link-local (incl. cloud metadata)
    ipaddress.ip_network("172.16.0.0/12"),          # RFC-1918 private
    ipaddress.ip_network("192.168.0.0/16"),         # RFC-1918 private
    ipaddress.ip_network("198.18.0.0/15"),          # benchmark testing (RFC 2544)
    ipaddress.ip_network("240.0.0.0/4"),            # reserved for future use
    ipaddress.ip_network("::1/128"),                # loopback IPv6
    ipaddress.ip_network("fc00::/7"),               # unique-local IPv6
    ipaddress.ip_network("fe80::/10"),              # link-local IPv6
]


def _normalize_ip(ip: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    """Normalize IPv4-mapped IPv6 addresses to IPv4 for consistent blocking.

    Prevents bypasses like ::ffff:127.0.0.1 evading IPv4 blocklist checks.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _is_ssrf_safe(hostname: str) -> Tuple[bool, Optional[str]]:
    """Check if hostname resolves to a public (non-private) IP address.

    Returns (is_safe, resolved_ip_str). The caller should connect to the
    resolved IP directly to prevent DNS rebinding (TOCTOU) attacks.
    """
    try:
        addr_infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC,
                                         socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as e:
        _debug(f"SSRF check: DNS resolution failed for {hostname}: {e}")
        return False, None

    first_ip_str = None
    for family, _, _, _, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        if first_ip_str is None:
            first_ip_str = ip_str
        try:
            ip = _normalize_ip(ipaddress.ip_address(ip_str))
        except ValueError:
            continue
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                _debug(f"SSRF blocked: {hostname} resolves to {ip_str} (in {network})")
                return False, None

    return True, first_ip_str


class ContentExtractor:
    """Protocol for content extraction backends."""

    def extract(self, html_text: str) -> str:
        """Extract main text content from HTML. Override in subclasses."""
        raise NotImplementedError


class StdlibExtractor(ContentExtractor):
    """Regex-based HTML content extractor using Python stdlib only.

    Extraction priority: <article> > <main> > <body>
    Strips scripts, styles, navigation, and other non-content elements.
    """

    # Tags to remove entirely (content + tags)
    _STRIP_TAGS_RE = re.compile(
        r"<\s*(script|style|nav|footer|header|aside|noscript|svg|iframe)"
        r"[\s>].*?</\s*\1\s*>",
        re.IGNORECASE | re.DOTALL,
    )

    # All HTML tags
    _TAG_RE = re.compile(r"<[^>]+>")

    # Multiple whitespace (spaces/tabs/newlines) → single space or newline
    _WHITESPACE_RE = re.compile(r"[ \t]+")
    _MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

    def extract(self, html_text: str) -> str:
        """Extract readable text from HTML."""
        text = html_text

        # 1. Try to find the best content container
        text = self._extract_container(text)

        # 2. Remove unwanted tags and their content
        text = self._STRIP_TAGS_RE.sub("", text)

        # 3. Strip all remaining HTML tags
        text = self._TAG_RE.sub(" ", text)

        # 4. Decode HTML entities
        text = html.unescape(text)

        # 5. Normalize whitespace
        text = self._WHITESPACE_RE.sub(" ", text)
        text = self._MULTI_NEWLINE_RE.sub("\n\n", text)
        text = text.strip()

        return text

    def _extract_container(self, html_text: str) -> str:
        """Find the best content container: article > main > body.

        For <article>, selects the longest match (most likely the main content
        when multiple <article> elements exist, e.g., sidebar teasers).
        """
        # Try <article> first — use longest match for main content
        matches = re.findall(
            r"<article[\s>].*?</article>",
            html_text, re.IGNORECASE | re.DOTALL,
        )
        if matches:
            return max(matches, key=len)

        # Try <main>
        match = re.search(
            r"<main[\s>].*?</main>",
            html_text, re.IGNORECASE | re.DOTALL,
        )
        if match:
            return match.group(0)

        # Fall back to <body>
        match = re.search(
            r"<body[\s>].*?</body>",
            html_text, re.IGNORECASE | re.DOTALL,
        )
        if match:
            return match.group(0)

        # No container found — use full text
        return html_text


# Default content extractor instance
_default_extractor = StdlibExtractor()


def _fetch_content(url: str, timeout: int, max_chars: int,
                   extractor: ContentExtractor) -> Optional[str]:
    """Fetch a URL and extract text content.

    Returns extracted text (truncated to max_chars) or None on any failure.
    SSRF-safe: resolves DNS and validates IP before fetching.

    Note: There is a theoretical TOCTOU window between DNS check and fetch
    (DNS rebinding). This is acceptable because: (1) Scout runs in a Docker
    container with network:bridge — no internal services to reach, (2) URLs
    come from Serper API (Google search results), not user input, (3) stdlib
    urllib doesn't support connecting to a pre-resolved IP with correct SNI.
    """
    try:
        # Parse URL and validate scheme
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            _debug(f"Content fetch: blocked non-HTTP scheme {parsed.scheme} for {url[:100]}")
            return None
        hostname = parsed.hostname
        if not hostname:
            _debug(f"Content fetch: no hostname in URL: {url[:100]}")
            return None

        # SSRF guard — resolve hostname and check IP against blocklist
        is_safe, _ = _is_ssrf_safe(hostname)
        if not is_safe:
            return None

        # Fetch using original URL (DNS re-resolves, see docstring for rationale)
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Mozilla/5.0 (compatible; SerperSkill/3.0; +https://openclaw.ai)")
        req.add_header("Accept", "text/html,application/xhtml+xml,*/*")

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Only process HTML responses
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                _debug(f"Content fetch: skipping non-HTML ({content_type[:50]}) for {url[:100]}")
                return None

            # Read with size limit (10MB max raw HTML to prevent memory issues)
            raw = resp.read(10 * 1024 * 1024)

        # Decode
        html_text = raw.decode("utf-8", errors="replace")

        # Extract text
        text = extractor.extract(html_text)

        # Truncate to max_chars with word boundary
        if len(text) > max_chars:
            truncated = text[:max_chars].rsplit(" ", 1)[0]
            text = (truncated if truncated else text[:max_chars]) + "..."

        if not text.strip():
            _debug(f"Content fetch: empty extraction for {url[:100]}")
            return None

        _debug(f"Content fetch: extracted {len(text)} chars from {url[:100]}")
        return text

    except Exception as e:
        _debug(f"Content fetch failed for {url[:100]}: {str(e)[:100]}")
        return None


def _extract_content_for_results(
    results: List[Dict[str, Any]],
    top_n: int,
    timeout: int,
    max_chars: int,
    extractor: ContentExtractor,
) -> int:
    """Fetch and extract content for the top N results concurrently.

    Adds 'content_excerpt' field to each result dict (in-place).
    All results get the field for schema consistency — null if not extracted.
    Returns the count of successful extractions.
    """
    # Initialize content_excerpt=null for ALL results (schema consistency)
    for result in results:
        result["content_excerpt"] = None

    # Only process results that have a URL
    targets = []
    for i, result in enumerate(results[:top_n]):
        url = result.get("url", "")
        if url and url.startswith("http"):
            targets.append((i, url))

    if not targets:
        return 0

    _debug(f"Content extraction: fetching {len(targets)} URLs (top {top_n}, timeout={timeout}s)")

    extracted_count = 0

    # Use ThreadPoolExecutor for concurrent fetching
    pool_size = min(len(targets), 10)
    with ThreadPoolExecutor(max_workers=pool_size) as executor:
        future_to_idx = {
            executor.submit(_fetch_content, url, timeout, max_chars, extractor): idx
            for idx, url in targets
        }

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                content = future.result()
            except Exception:
                content = None

            results[idx]["content_excerpt"] = content
            if content is not None:
                extracted_count += 1

    return extracted_count


def _debug(msg: str) -> None:
    """Print diagnostic message to stderr if --debug is active."""
    if _DEBUG:
        print(f"[serper:debug] {msg}", file=sys.stderr)


def _get_api_key(cli_key: Optional[str], key_file: Optional[str] = None) -> Optional[str]:
    """Resolve API key: key file > --api-key arg > SERPER_API_KEY env var."""
    if key_file:
        keys = load_keys(key_file, env_fallback=None)
        if keys:
            _debug("API key source: key file")
            return keys[0]
    if cli_key and cli_key.strip():
        _debug("API key source: --api-key argument")
        return cli_key.strip()
    env_key = os.getenv("SERPER_API_KEY", "").strip()
    if env_key:
        _debug("API key source: SERPER_API_KEY env var")
        return env_key
    _debug("API key: not found")
    return None


def _detect_type(query: str) -> Tuple[str, bool]:
    """Auto-detect search type from query keywords.

    Returns (type, auto_detected).
    """
    if _NEWS_PATTERN.search(query):
        _debug(f"Auto-detected type: news (matched keyword in query)")
        return "news", True
    _debug("Auto-detected type: search (no news keywords found)")
    return "search", False


def _build_payload(query: str, count: int, gl: Optional[str],
                   hl: Optional[str], tbs: Optional[str],
                   page: Optional[int]) -> Dict[str, Any]:
    """Build JSON request payload for the Serper API."""
    payload: Dict[str, Any] = {"q": query, "num": count}
    if gl:
        payload["gl"] = gl
    if hl:
        payload["hl"] = hl
    if tbs:
        payload["tbs"] = tbs
    if page is not None:
        payload["page"] = page
    _debug(f"Request payload: {json.dumps(payload)}")
    return payload


def _make_request(endpoint: str, payload: Dict[str, Any],
                  api_key: str, timeout: int) -> Tuple[int, Dict[str, Any]]:
    """POST JSON to Serper API endpoint.

    Returns (http_status, parsed_response_body).
    Raises urllib.error.HTTPError or urllib.error.URLError on failure.
    """
    url = f"{BASE_URL}/{endpoint}"
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("X-API-KEY", api_key)
    req.add_header("Content-Type", "application/json")

    _debug(f"POST {url} (timeout={timeout}s, body_bytes={len(body)})")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.getcode()
        raw = resp.read()

    data = json.loads(raw.decode("utf-8", errors="replace"))
    _debug(f"Response: status={status}, keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
    return status, data


def _format_search_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract web search results from Serper response."""
    results: List[Dict[str, Any]] = []
    organic = data.get("organic")
    if not isinstance(organic, list):
        return results
    for item in organic:
        if not isinstance(item, dict):
            continue
        result: Dict[str, Any] = {
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "description": item.get("snippet", ""),
            "position": item.get("position", len(results) + 1),
        }
        # Include date if present (organic results often have dates)
        if item.get("date"):
            result["date"] = item["date"]
        # Include sitelinks if present (grouped/related results from same site)
        raw_sitelinks = item.get("sitelinks")
        if isinstance(raw_sitelinks, list) and raw_sitelinks:
            result["sitelinks"] = [
                {"title": sl.get("title", ""), "url": sl.get("link", "")}
                for sl in raw_sitelinks
                if isinstance(sl, dict)
            ]
        results.append(result)
    return results


def _format_news_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract news results from Serper response."""
    results: List[Dict[str, Any]] = []
    news = data.get("news")
    if not isinstance(news, list):
        return results
    for item in news:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "description": item.get("snippet", ""),
            "position": item.get("position", len(results) + 1),
            "date": item.get("date"),
            "source": item.get("source"),
        })
    return results


def _format_images_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract image search results from Serper response."""
    results: List[Dict[str, Any]] = []
    images = data.get("images")
    if not isinstance(images, list):
        return results
    for item in images:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "image_url": item.get("imageUrl", ""),
            "source": item.get("source", ""),
            "width": item.get("imageWidth"),
            "height": item.get("imageHeight"),
            "position": item.get("position", len(results) + 1),
        })
    return results


def _format_videos_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract video search results from Serper response."""
    results: List[Dict[str, Any]] = []
    videos = data.get("videos")
    if not isinstance(videos, list):
        return results
    for item in videos:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "description": item.get("snippet", ""),
            "date": item.get("date"),
            "duration": item.get("duration"),
            "channel": item.get("channel"),
            "position": item.get("position", len(results) + 1),
        })
    return results


def _format_places_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract place/local business results from Serper response."""
    results: List[Dict[str, Any]] = []
    places = data.get("places")
    if not isinstance(places, list):
        return results
    for item in places:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get("title", ""),
            "address": item.get("address", ""),
            "rating": item.get("rating"),
            "rating_count": item.get("ratingCount"),
            "category": item.get("category"),
            "phone": item.get("phoneNumber"),
            "website": item.get("website"),
            "position": item.get("position", len(results) + 1),
        })
    return results


def _format_shopping_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract shopping/product results from Serper response. Costs 2 credits."""
    results: List[Dict[str, Any]] = []
    shopping = data.get("shopping")
    if not isinstance(shopping, list):
        return results
    for item in shopping:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "price": item.get("price"),
            "source": item.get("source"),
            "rating": item.get("rating"),
            "rating_count": item.get("ratingCount"),
            "position": item.get("position", len(results) + 1),
        })
    return results


def _format_scholar_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract Google Scholar results from Serper response."""
    results: List[Dict[str, Any]] = []
    organic = data.get("organic")
    if not isinstance(organic, list):
        return results
    for item in organic:
        if not isinstance(item, dict):
            continue
        # publicationInfo and citedBy can be dicts or plain values
        pub_info = item.get("publicationInfo")
        cited_by = item.get("citedBy")
        result: Dict[str, Any] = {
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "description": item.get("snippet", ""),
            "publication_info": pub_info.get("summary", "") if isinstance(pub_info, dict) else (pub_info or ""),
            "year": item.get("year"),
            "cited_by": cited_by.get("total") if isinstance(cited_by, dict) else cited_by,
            "position": item.get("position", len(results) + 1),
        }
        # Optional PDF URL
        if item.get("pdfUrl"):
            result["pdf_url"] = item["pdfUrl"]
        results.append(result)
    return results


def _format_patents_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract Google Patents results from Serper response."""
    results: List[Dict[str, Any]] = []
    organic = data.get("organic")
    if not isinstance(organic, list):
        return results
    for item in organic:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "description": item.get("snippet", ""),
            "publication_number": item.get("publicationNumber"),
            "inventor": item.get("inventor"),
            "assignee": item.get("assignee"),
            "priority_date": item.get("priorityDate"),
            "filing_date": item.get("filingDate"),
            "grant_date": item.get("grantDate"),
            "position": item.get("position", len(results) + 1),
        })
    return results


def _format_autocomplete_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract autocomplete suggestions from Serper response."""
    results: List[Dict[str, Any]] = []
    suggestions = data.get("suggestions")
    if not isinstance(suggestions, list):
        return results
    for i, item in enumerate(suggestions):
        if isinstance(item, dict):
            results.append({
                "value": item.get("value", ""),
                "position": i + 1,
            })
        elif isinstance(item, str):
            results.append({
                "value": item,
                "position": i + 1,
            })
    return results


# Formatter registry — map search type to its result extractor
_FORMATTERS = {
    "search": _format_search_results,
    "news": _format_news_results,
    "images": _format_images_results,
    "videos": _format_videos_results,
    "places": _format_places_results,
    "shopping": _format_shopping_results,
    "scholar": _format_scholar_results,
    "patents": _format_patents_results,
    "autocomplete": _format_autocomplete_results,
}


def _format_supplemental(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract supplemental data: relatedSearches, peopleAlsoAsk, knowledgeGraph."""
    supplemental: Dict[str, Any] = {}

    # Related searches
    related = data.get("relatedSearches")
    if isinstance(related, list) and related:
        supplemental["related_searches"] = [
            item.get("query", "") for item in related
            if isinstance(item, dict) and item.get("query")
        ]

    # People also ask
    paa = data.get("peopleAlsoAsk")
    if isinstance(paa, list) and paa:
        supplemental["people_also_ask"] = [
            {
                "question": item.get("question", ""),
                "snippet": item.get("snippet", ""),
                "title": item.get("title", ""),
                "url": item.get("link", ""),
            }
            for item in paa
            if isinstance(item, dict) and item.get("question")
        ]

    # Knowledge graph (condensed)
    kg = data.get("knowledgeGraph")
    if isinstance(kg, dict) and kg.get("title"):
        supplemental["knowledge_graph"] = {
            "title": kg.get("title", ""),
            "type": kg.get("type", ""),
            "description": kg.get("description", ""),
            "website": kg.get("website"),
        }

    return supplemental


def _make_output(query: str, search_type: str, results: List[Dict[str, Any]],
                 error: Optional[str], http_status: Optional[int],
                 error_code: Optional[str],
                 meta: Dict[str, Any],
                 supplemental: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build the standard output envelope."""
    output = {
        "provider": "serper",
        "query": query,
        "type": search_type,
        "results": results,
        "error": error,
        "http_status": http_status,
        "error_code": error_code,
        "meta": meta,
    }
    if supplemental:
        output["supplemental"] = supplemental
    return output


def _emit(output: Dict[str, Any]) -> None:
    """Print JSON output to stdout."""
    print(json.dumps(output, separators=(",", ":")))


def main() -> int:
    global _DEBUG

    parser = argparse.ArgumentParser(
        description="Serper.dev search (OpenClaw skill) — Google search — web, news, images, videos, places, shopping, scholar, patents, autocomplete"
    )
    parser.add_argument("--query", "-q", required=True, help="Search query")
    parser.add_argument("--type", "-t", dest="search_type",
                        choices=["search", "news", "images", "videos", "places",
                                 "shopping", "scholar", "patents", "autocomplete"],
                        default=None, help="Search type (default: auto-detect search vs news)")
    parser.add_argument("--count", "-n", type=int, default=10,
                        help="Number of results (1-100, default 10)")
    parser.add_argument("--gl", default=None, help="Country code (e.g., us, de)")
    parser.add_argument("--hl", default=None, help="Language code (e.g., en, de)")
    parser.add_argument("--tbs", default=None,
                        help="Time filter (qdr:h, qdr:d, qdr:w, qdr:m, qdr:y)")
    parser.add_argument("--page", type=int, default=None, help="Pagination page number")
    parser.add_argument("--api-key", default=None,
                        help="Serper API key (overrides SERPER_API_KEY env var)")
    parser.add_argument("--timeout", type=int, default=15,
                        help="HTTP request timeout in seconds (default 15)")
    parser.add_argument("--key-file", default=os.path.join(DEFAULT_KEY_DIR, "serper.json"),
                        help=f"Path to key file for key rotation (default: {DEFAULT_KEY_DIR}/serper.json)")
    parser.add_argument("--debug", action="store_true",
                        help="Print diagnostics to stderr (no secrets)")
    # Content extraction flags
    parser.add_argument("--content", action="store_true",
                        help="Enable inline content extraction for top results")
    parser.add_argument("--content-top", type=int, default=3,
                        help="Number of top result URLs to extract (1-10, default 3)")
    parser.add_argument("--content-timeout", type=int, default=10,
                        help="Per-URL fetch timeout in seconds (default 10)")
    parser.add_argument("--content-max-chars", type=int, default=5000,
                        help="Max extracted text per URL (default 5000)")
    args = parser.parse_args()

    _DEBUG = args.debug
    started_at = time.time()

    def elapsed_ms() -> int:
        return int((time.time() - started_at) * 1000)

    query = (args.query or "").strip()

    # --- Validation ---

    if not query:
        out = _make_output(
            query="", search_type="search", results=[],
            error="validation_error: --query is required and must not be empty",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": 0, "credits_used": 0, "endpoint": None,
                  "auto_detected_type": False, "version": VERSION},
        )
        _emit(out)
        return 2

    if len(query) > MAX_QUERY_LENGTH:
        out = _make_output(
            query=query[:MAX_QUERY_LENGTH], search_type="search", results=[],
            error=f"validation_error: query exceeds {MAX_QUERY_LENGTH} characters ({len(query)} given)",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": 0, "credits_used": 0, "endpoint": None,
                  "auto_detected_type": False, "version": VERSION},
        )
        _emit(out)
        return 2

    if args.count < 1 or args.count > 100:
        out = _make_output(
            query=query, search_type="search", results=[],
            error=f"validation_error: --count must be 1-100, got {args.count}",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": 0, "credits_used": 0, "endpoint": None,
                  "auto_detected_type": False, "version": VERSION},
        )
        _emit(out)
        return 2

    if args.content_top < 1 or args.content_top > 10:
        out = _make_output(
            query=query, search_type="search", results=[],
            error=f"validation_error: --content-top must be 1-10, got {args.content_top}",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": 0, "credits_used": 0, "endpoint": None,
                  "auto_detected_type": False, "version": VERSION},
        )
        _emit(out)
        return 2

    if args.content_timeout < 1:
        out = _make_output(
            query=query, search_type="search", results=[],
            error=f"validation_error: --content-timeout must be >= 1, got {args.content_timeout}",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": 0, "credits_used": 0, "endpoint": None,
                  "auto_detected_type": False, "version": VERSION},
        )
        _emit(out)
        return 2

    if args.content_max_chars < 100:
        out = _make_output(
            query=query, search_type="search", results=[],
            error=f"validation_error: --content-max-chars must be >= 100, got {args.content_max_chars}",
            http_status=None, error_code="validation_error",
            meta={"duration_ms": 0, "credits_used": 0, "endpoint": None,
                  "auto_detected_type": False, "version": VERSION},
        )
        _emit(out)
        return 2

    # --- API Key ---

    api_key = _get_api_key(args.api_key, args.key_file)
    if not api_key:
        out = _make_output(
            query=query, search_type="search", results=[],
            error="config_error: no API key found — set SERPER_API_KEY or pass --api-key",
            http_status=None, error_code="config_error",
            meta={"duration_ms": elapsed_ms(), "credits_used": 0, "endpoint": None,
                  "auto_detected_type": False, "version": VERSION},
        )
        _emit(out)
        return 2

    # --- Type detection ---

    auto_detected = False
    if args.search_type:
        search_type = args.search_type
        _debug(f"Explicit type: {search_type}")
    else:
        search_type, auto_detected = _detect_type(query)

    endpoint = search_type  # matches Serper API path: /search, /news, /images, etc.

    if search_type == "shopping":
        _debug("WARNING: Shopping endpoint costs 2 credits per query")

    # --- Build payload ---

    payload = _build_payload(
        query=query,
        count=args.count,
        gl=args.gl,
        hl=args.hl,
        tbs=args.tbs,
        page=args.page,
    )

    # --- Make request ---

    http_status = None
    try:
        http_status, data = _make_request(
            endpoint=endpoint,
            payload=payload,
            api_key=api_key,
            timeout=args.timeout,
        )
    except urllib.error.HTTPError as e:
        http_status = e.code
        _debug(f"HTTPError: status={http_status}")

        # Map status to error code
        if http_status in (401, 403):
            error_code = "auth_error"
            error_msg = f"auth_error: HTTP {http_status} — invalid or missing API key"
        elif http_status == 429:
            error_code = "rate_limited"
            error_msg = f"rate_limited: HTTP 429 — too many requests"
        elif 500 <= http_status <= 599:
            error_code = "server_error"
            error_msg = f"server_error: HTTP {http_status}"
        else:
            error_code = "client_error"
            error_msg = f"client_error: HTTP {http_status}"

        # Try to read error body for detail
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            err_data = json.loads(err_body)
            detail = err_data.get("message") or err_data.get("error") or ""
            if detail:
                error_msg = f"{error_code}: HTTP {http_status} — {str(detail)[:200]}"
        except Exception:
            pass

        if error_code in ("auth_error", "rate_limited"):
            rotate_key(args.key_file)

        out = _make_output(
            query=query, search_type=search_type, results=[],
            error=error_msg, http_status=http_status, error_code=error_code,
            meta={
                "duration_ms": elapsed_ms(),
                "credits_used": 0,
                "endpoint": f"/{endpoint}",
                "auto_detected_type": auto_detected,
                "version": VERSION,
            },
        )
        _emit(out)
        return 1

    except urllib.error.URLError as e:
        _debug(f"URLError: {e.reason}")
        out = _make_output(
            query=query, search_type=search_type, results=[],
            error=f"network_error: {str(e.reason)[:200]}",
            http_status=None, error_code="network_error",
            meta={
                "duration_ms": elapsed_ms(),
                "credits_used": 0,
                "endpoint": f"/{endpoint}",
                "auto_detected_type": auto_detected,
                "version": VERSION,
            },
        )
        _emit(out)
        return 1

    except Exception as e:
        _debug(f"Unexpected request error: {e}")
        out = _make_output(
            query=query, search_type=search_type, results=[],
            error=f"network_error: {str(e)[:200]}",
            http_status=None, error_code="network_error",
            meta={
                "duration_ms": elapsed_ms(),
                "credits_used": 0,
                "endpoint": f"/{endpoint}",
                "auto_detected_type": auto_detected,
                "version": VERSION,
            },
        )
        _emit(out)
        return 1

    # --- Parse response ---

    # Read actual credits from response (fall back to per-endpoint default)
    default_credits = _CREDITS_DEFAULT.get(search_type, 1)
    actual_credits = data.get("credits", default_credits) if isinstance(data, dict) else default_credits

    try:
        formatter = _FORMATTERS.get(search_type, _format_search_results)
        results = formatter(data)
        supplemental = _format_supplemental(data)
    except Exception as e:
        _debug(f"Parse error: {e}")
        out = _make_output(
            query=query, search_type=search_type, results=[],
            error=f"parse_error: failed to extract results — {str(e)[:200]}",
            http_status=http_status, error_code="parse_error",
            meta={
                "duration_ms": elapsed_ms(),
                "credits_used": actual_credits,
                "endpoint": f"/{endpoint}",
                "auto_detected_type": auto_detected,
                "version": VERSION,
            },
        )
        _emit(out)
        return 1

    # --- Content extraction (if requested) ---

    content_extracted = 0
    if args.content and results:
        content_extracted = _extract_content_for_results(
            results=results,
            top_n=args.content_top,
            timeout=args.content_timeout,
            max_chars=args.content_max_chars,
            extractor=_default_extractor,
        )
        _debug(f"Content extraction: {content_extracted}/{min(len(results), args.content_top)} successful")

    # --- Success ---

    out = _make_output(
        query=query, search_type=search_type, results=results,
        error=None, http_status=http_status, error_code=None,
        meta={
            "duration_ms": elapsed_ms(),
            "credits_used": actual_credits,
            "endpoint": f"/{endpoint}",
            "auto_detected_type": auto_detected,
            "version": VERSION,
            **({"content_extracted": content_extracted} if args.content else {}),
        },
        supplemental=supplemental if supplemental else None,
    )
    _emit(out)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as _exc:
        _err_out = {
            "provider": "serper",
            "query": "",
            "type": "search",
            "results": [],
            "error": f"internal_error: {str(_exc)[:200]}",
            "http_status": None,
            "error_code": "internal_error",
            "meta": {"duration_ms": 0, "credits_used": 0, "endpoint": None,
                     "auto_detected_type": False, "version": VERSION},
        }
        print(json.dumps(_err_out, separators=(",", ":")))
        if "--debug" in sys.argv:
            import traceback
            traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)
