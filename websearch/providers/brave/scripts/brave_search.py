#!/usr/bin/env python3

import argparse
import gzip
import hashlib
import html
import ipaddress
import json
import os
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Key file support — shared across all search skills
import sys as _sys
_shared_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "_shared")
if _shared_dir not in _sys.path:
    _sys.path.insert(0, _shared_dir)
from keyfile import load_keys, rotate_key, DEFAULT_KEY_DIR

DEFAULT_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
ALLOWED_ENDPOINT_PREFIXES = ("https://api.search.brave.com/",)
MAX_QUERY_LENGTH = 2000
MAX_RETRY_AFTER_SECONDS = 60

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _normalize_ip(addr):
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped
    return addr


def _is_ssrf_safe(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return False
    for family, _, _, _, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            addr = _normalize_ip(ipaddress.ip_address(ip_str))
        except ValueError:
            return False
        for net in _BLOCKED_NETWORKS:
            if addr.version == net.version and addr in net:
                return False
    return True

VERSION = "2.0.0"

_BASE_URL = "https://api.search.brave.com"

_ENDPOINTS = {
    "web":     "/res/v1/web/search",
    "images":  "/res/v1/images/search",
    "videos":  "/res/v1/videos/search",
    "news":    "/res/v1/news/search",
    "suggest": "/res/v1/suggest/search",
    "local":   "/res/v1/web/search",
}

_LOCAL_POIS_PATH = "/res/v1/local/pois"
_LOCAL_DESCRIPTIONS_PATH = "/res/v1/local/descriptions"

_COUNT_LIMITS = {
    "web":     (1, 20),
    "images":  (1, 100),
    "videos":  (1, 50),
    "news":    (1, 50),
    "suggest": (1, 20),
    "local":   (1, 20),
}

def _format_web_results(brave_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    web = brave_json.get("web") if isinstance(brave_json, dict) else None
    items = web.get("results") if isinstance(web, dict) else None
    if not isinstance(items, list):
        return results
    for pos, r in enumerate(items, 1):
        if not isinstance(r, dict):
            continue
        entry: Dict[str, Any] = {
            "title": r.get("title") or "",
            "url": r.get("url") or r.get("link") or "",
            "description": r.get("description") or r.get("snippet") or "",
            "age": r.get("age"),
            "position": pos,
        }
        extra = r.get("extra_snippets")
        if isinstance(extra, list) and extra:
            entry["extra_snippets"] = extra
        results.append(entry)
    return results


def _format_image_results(brave_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    items = brave_json.get("results") if isinstance(brave_json, dict) else None
    if not isinstance(items, list):
        return results
    for pos, r in enumerate(items, 1):
        if not isinstance(r, dict):
            continue
        props = r.get("properties") or {}
        thumb = r.get("thumbnail") or {}
        results.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "image_url": props.get("url") or "",
            "source": r.get("source") or "",
            "thumbnail": thumb.get("src") or "",
            "width": props.get("width"),
            "height": props.get("height"),
            "position": pos,
        })
    return results


def _format_video_results(brave_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    items = brave_json.get("results") if isinstance(brave_json, dict) else None
    if not isinstance(items, list):
        return results
    for pos, r in enumerate(items, 1):
        if not isinstance(r, dict):
            continue
        video = r.get("video") or {}
        results.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "description": r.get("description") or "",
            "age": r.get("age"),
            "duration": video.get("duration"),
            "views": video.get("views"),
            "creator": video.get("creator"),
            "publisher": video.get("publisher"),
            "position": pos,
        })
    return results


def _format_news_results(brave_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    items = brave_json.get("results") if isinstance(brave_json, dict) else None
    if not isinstance(items, list):
        return results
    for pos, r in enumerate(items, 1):
        if not isinstance(r, dict):
            continue
        meta_url = r.get("meta_url") or {}
        results.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "description": r.get("description") or "",
            "age": r.get("age"),
            "page_age": r.get("page_age"),
            "source": meta_url.get("hostname"),
            "breaking": bool(r.get("breaking", False)),
            "position": pos,
        })
    return results


def _format_suggest_results(brave_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    items = brave_json.get("results") if isinstance(brave_json, dict) else None
    if not isinstance(items, list):
        return results
    for pos, r in enumerate(items, 1):
        if not isinstance(r, dict):
            continue
        results.append({
            "query": r.get("query") or r.get("value") or "",
            "position": pos,
        })
    return results


def _format_local_results(pois_data: Any) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    items = pois_data if isinstance(pois_data, list) else []
    for pos, r in enumerate(items, 1):
        if not isinstance(r, dict):
            continue
        addr_obj = r.get("postal_address") or {}
        rating_obj = r.get("rating") or {}
        results.append({
            "title": r.get("name") or r.get("title") or "",
            "url": r.get("url") or r.get("provider_url"),
            "address": addr_obj.get("displayAddress"),
            "phone": r.get("phone") or (r.get("contact") or {}).get("telephone"),
            "rating": rating_obj.get("ratingValue"),
            "rating_count": rating_obj.get("ratingCount"),
            "price_range": r.get("price_range"),
            "categories": r.get("categories") or [],
            "description": r.get("description"),
            "position": pos,
        })
    return results


_FORMATTERS = {
    "web":     _format_web_results,
    "images":  _format_image_results,
    "videos":  _format_video_results,
    "news":    _format_news_results,
    "suggest": _format_suggest_results,
    "local":   _format_local_results,
}


def _build_params(args, search_type: str) -> Dict[str, Any]:
    params: Dict[str, Any] = {"q": args.query.strip()}

    count_min, count_max = _COUNT_LIMITS.get(search_type, (1, 20))
    params["count"] = max(count_min, min(args.count, count_max))

    # offset: not for suggest
    if search_type != "suggest" and args.offset > 0:
        params["offset"] = max(0, args.offset)

    if args.country:
        params["country"] = args.country

    if args.search_lang:
        params["search_lang"] = args.search_lang

    # ui_lang: web, videos, news, local only
    if search_type in ("web", "videos", "news", "local") and args.ui_lang:
        params["ui_lang"] = args.ui_lang

    # safesearch: not for suggest; images only accepts off/strict (no moderate)
    if search_type != "suggest" and getattr(args, "safesearch", None):
        ss = args.safesearch
        if search_type == "images" and ss == "moderate":
            ss = "strict"  # Brave Image API only accepts off/strict
        params["safesearch"] = ss

    # freshness: web, videos, news, local only
    if search_type in ("web", "videos", "news", "local") and getattr(args, "freshness", None):
        params["freshness"] = args.freshness

    # spellcheck: not for suggest
    if search_type != "suggest":
        params["spellcheck"] = "1" if getattr(args, "spellcheck", True) else "0"

    # extra_snippets: web and news only
    if search_type in ("web", "news") and getattr(args, "extra_snippets", False):
        params["extra_snippets"] = "1"

    # goggles: web and news only
    if search_type in ("web", "news") and getattr(args, "goggles", None):
        params["goggles"] = args.goggles

    # result_filter: web only
    if search_type == "web" and getattr(args, "result_filter", None):
        params["result_filter"] = args.result_filter

    # local forces result_filter=locations
    if search_type == "local":
        params["result_filter"] = "locations"

    return {k: v for k, v in params.items() if v is not None}


def _parse_keys(key_file: Optional[str] = None) -> List[str]:
    # Priority 1: key file (if provided and exists)
    if key_file:
        keys_from_file = load_keys(key_file, env_fallback=None)
        if keys_from_file:
            return keys_from_file[:3]
    # Priority 2: BRAVE_API_KEY env var (comma-separated or single)
    keys_csv = os.getenv("BRAVE_API_KEY", "").strip()
    keys: List[str] = []
    if keys_csv:
        keys = [key.strip() for key in keys_csv.split(",") if key.strip()]

    if not keys:
        for index in (1, 2, 3):
            key_value = os.getenv(f"BRAVE_API_KEY_{index}", "").strip()
            if key_value:
                keys.append(key_value)

    return keys[:3]


def _validate_endpoint(endpoint: str, allow_custom: bool) -> Optional[str]:
    """Return None if endpoint is allowed, or an error message string."""
    if allow_custom:
        return None
    for prefix in ALLOWED_ENDPOINT_PREFIXES:
        if endpoint.startswith(prefix):
            return None
    return f"endpoint_blocked: {endpoint[:80]} is not in the allowlist. Use --allow-custom-endpoint to override."


def _safe_error_message(text: str, max_len: int = 240) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    return normalized[:max_len]


def _is_retryable_error_code(error_code: str) -> bool:
    return error_code in ("rate_limited", "server_error", "network_error")


def _decode_body(raw: bytes, content_encoding: str) -> bytes:
    if not raw:
        return raw
    if "gzip" in (content_encoding or "").lower() or raw.startswith(b"\x1f\x8b"):
        return gzip.decompress(raw)
    return raw


def _as_int(value: Any) -> Optional[int]:
    text = str(value).strip()
    match = re.search(r"-?\d+", text)
    if match:
        try:
            return int(match.group(0))
        except Exception:
            pass
    try:
        return int(text)
    except Exception:
        return None


def _sleep_with_backoff(attempt: int, base_delay_ms: int, max_delay_ms: int, retry_after_seconds: Optional[int]) -> None:
    exponential_ms = min(max_delay_ms, base_delay_ms * (2 ** max(0, attempt - 1)))
    jitter_ms = random.uniform(0, float(exponential_ms))
    backoff_seconds = jitter_ms / 1000.0
    if retry_after_seconds is not None:
        capped = min(retry_after_seconds, MAX_RETRY_AFTER_SECONDS)
        backoff_seconds = max(backoff_seconds, float(max(0, capped)))
    time.sleep(backoff_seconds)


def _parse_retry_after_seconds(value: str) -> Optional[int]:
    seconds = _as_int(value)
    if seconds is not None:
        return max(0, seconds)

    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = int((dt - datetime.now(timezone.utc)).total_seconds())
        return max(0, delta)
    except Exception:
        return None


def _extract_rate_limit_headers(headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
    normalized = {str(k).lower(): str(v) for k, v in (headers or {}).items()}

    retry_after_raw = normalized.get("retry-after")
    retry_after_seconds = _parse_retry_after_seconds(retry_after_raw) if retry_after_raw else None

    known_map = {
        "x-ratelimit-limit": "limit",
        "x-ratelimit-remaining": "remaining",
        "x-ratelimit-reset": "reset",
        "x-rate-limit-limit": "limit",
        "x-rate-limit-remaining": "remaining",
        "x-rate-limit-reset": "reset",
    }

    parsed_known: Dict[str, Any] = {}
    for source_name, target_name in known_map.items():
        if source_name in normalized:
            parsed_known[target_name] = _as_int(normalized[source_name]) if target_name in ("limit", "remaining", "reset") else normalized[source_name]

    raw_subset: Dict[str, str] = {
        key: value
        for key, value in normalized.items()
        if any(token in key for token in ("rate", "quota", "limit", "retry-after", "remaining", "reset"))
    }

    if retry_after_seconds is None and not parsed_known and not raw_subset:
        return None

    return {
        "retry_after_seconds": retry_after_seconds,
        "known": parsed_known,
        "raw": raw_subset,
    }


def _new_circuit_breaker_state() -> Dict[str, Any]:
    return {
        "state": "closed",
        "failures": 0,
        "open_until": 0.0,
    }


def _circuit_is_available(cb_state: Dict[str, Any], now: float) -> bool:
    state = cb_state.get("state", "closed")
    open_until = float(cb_state.get("open_until", 0.0) or 0.0)
    if state == "open" and now < open_until:
        return False
    if state == "open" and now >= open_until:
        cb_state["state"] = "half-open"
    return True


def _circuit_on_success(cb_state: Dict[str, Any]) -> None:
    cb_state["state"] = "closed"
    cb_state["failures"] = 0
    cb_state["open_until"] = 0.0


def _circuit_on_failure(cb_state: Dict[str, Any], threshold: int, reset_timeout_ms: int, now: float) -> None:
    failures = int(cb_state.get("failures", 0) or 0) + 1
    cb_state["failures"] = failures
    if failures >= max(1, threshold):
        cb_state["state"] = "open"
        cb_state["open_until"] = now + (max(1, reset_timeout_ms) / 1000.0)
    else:
        cb_state["state"] = "closed"


def _cache_file_path(cache_dir: str, cache_key: str) -> Path:
    return Path(cache_dir).expanduser().resolve() / f"{cache_key}.json"


def _build_cache_key(endpoint: str, params: Dict[str, Any], content_opts: Dict[str, Any]) -> str:
    payload = {
        "endpoint": endpoint,
        "params": params,
        "content": content_opts,
        "schema": 1,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cache_read(cache_dir: str, cache_key: str) -> Optional[Dict[str, Any]]:
    cache_path = _cache_file_path(cache_dir, cache_key)
    if not cache_path.exists():
        return None
    try:
        content = json.loads(cache_path.read_text(encoding="utf-8"))
        expires_at = float(content.get("expires_at", 0.0) or 0.0)
        if time.time() >= expires_at:
            return None
        payload = content.get("payload")
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _cache_write(cache_dir: str, cache_key: str, payload: Dict[str, Any], ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    cache_path = _cache_file_path(cache_dir, cache_key)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "expires_at": time.time() + float(ttl_seconds),
        "payload": payload,
    }
    cache_path.write_text(json.dumps(envelope, separators=(",", ":")), encoding="utf-8")


def _cache_delete(cache_dir: str, cache_key: str) -> bool:
    cache_path = _cache_file_path(cache_dir, cache_key)
    if not cache_path.exists():
        return False
    try:
        cache_path.unlink()
        return True
    except Exception:
        return False


def _cache_delete_all(cache_dir: str) -> int:
    base = Path(cache_dir).expanduser().resolve()
    if not base.exists() or not base.is_dir():
        return 0

    deleted = 0
    for entry in base.glob("*.json"):
        try:
            entry.unlink()
            deleted += 1
        except Exception:
            continue
    return deleted


def _cache_prune_expired(cache_dir: str) -> int:
    base = Path(cache_dir).expanduser().resolve()
    if not base.exists() or not base.is_dir():
        return 0

    now = time.time()
    removed = 0
    for entry in base.glob("*.json"):
        try:
            content = json.loads(entry.read_text(encoding="utf-8"))
            expires_at = float(content.get("expires_at", 0.0) or 0.0)
            if now >= expires_at:
                entry.unlink()
                removed += 1
        except Exception:
            try:
                entry.unlink()
                removed += 1
            except Exception:
                continue
    return removed


def _cache_enforce_max_entries(cache_dir: str, max_entries: int) -> int:
    if max_entries <= 0:
        return 0
    base = Path(cache_dir).expanduser().resolve()
    if not base.exists() or not base.is_dir():
        return 0

    entries = list(base.glob("*.json"))
    if len(entries) <= max_entries:
        return 0

    entries.sort(key=lambda p: p.stat().st_mtime)
    to_remove = entries[: max(0, len(entries) - max_entries)]
    removed = 0
    for entry in to_remove:
        try:
            entry.unlink()
            removed += 1
        except Exception:
            continue
    return removed


def _detect_error_code(status: int, body: Any) -> str:
    message = ""
    if isinstance(body, dict):
        raw_message = body.get("message") or body.get("error") or ""
        message = str(raw_message).lower()

    if status == 429 or "rate limit" in message or "too many requests" in message:
        return "rate_limited"
    if status in (401, 403):
        return "auth_error"
    if status == 402 or any(token in message for token in ("quota", "credit", "billing")):
        return "quota_exceeded"
    if 500 <= status <= 599:
        return "server_error"
    if 400 <= status <= 499:
        return "client_error"
    if status <= 0:
        return "network_error"
    return "unknown_error"


def _extract_charset_from_content_type(content_type: str) -> str:
    if not content_type:
        return "utf-8"
    match = re.search(r"charset=([\w\-]+)", content_type, flags=re.IGNORECASE)
    if not match:
        return "utf-8"
    return match.group(1).strip() or "utf-8"


def _extract_main_text_from_html(html_text: str, max_chars: int) -> str:
    article_match = re.search(r"<article\b[^>]*>(.*?)</article>", html_text, flags=re.IGNORECASE | re.DOTALL)
    target = article_match.group(1) if article_match else html_text

    target = re.sub(r"<script\b[^>]*>.*?</script>", " ", target, flags=re.IGNORECASE | re.DOTALL)
    target = re.sub(r"<style\b[^>]*>.*?</style>", " ", target, flags=re.IGNORECASE | re.DOTALL)
    target = re.sub(r"<noscript\b[^>]*>.*?</noscript>", " ", target, flags=re.IGNORECASE | re.DOTALL)
    target = re.sub(r"<[^>]+>", " ", target)
    target = html.unescape(target)
    target = re.sub(r"\s+", " ", target).strip()

    if len(target) <= max_chars:
        return target

    cutoff = target.rfind(" ", 0, max_chars)
    if cutoff < max_chars // 2:
        cutoff = max_chars
    return target[:cutoff].rstrip() + "…"


def _fetch_content_excerpt(url: str, timeout_s: int, max_chars: int) -> Dict[str, Optional[str]]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"content_excerpt": "", "content_error": "unsupported_url_scheme"}

    if not _is_ssrf_safe(url):
        return {"content_excerpt": "", "content_error": "blocked_by_ssrf_guard"}

    req = urllib.request.Request(url)
    req.add_header("User-Agent", f"openclaw-brave/{VERSION}")
    req.add_header("Accept", "text/html,application/xhtml+xml")
    req.add_header("Accept-Encoding", "gzip")

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            raw = response.read(10_000_001)
            if len(raw) > 10_000_000:
                return {"content_excerpt": "", "content_error": "response_too_large"}
            headers = dict(response.headers.items())
            content_type = headers.get("Content-Type", "")

        if content_type and "html" not in content_type.lower():
            return {"content_excerpt": "", "content_error": "unsupported_content_type"}

        raw = _decode_body(raw, headers.get("Content-Encoding", ""))
        charset = _extract_charset_from_content_type(content_type)
        decoded = raw.decode(charset, errors="replace")
        excerpt = _extract_main_text_from_html(decoded, max_chars=max_chars)
        if not excerpt:
            return {"content_excerpt": "", "content_error": "empty_content"}
        return {"content_excerpt": excerpt, "content_error": None}

    except urllib.error.HTTPError as e:
        return {"content_excerpt": "", "content_error": f"http_{int(getattr(e, 'code', 0) or 0)}"}
    except Exception as e:
        return {"content_excerpt": "", "content_error": f"fetch_error:{_safe_error_message(str(e), max_len=120)}"}


def _enrich_results_with_content(
    results: List[Dict[str, Any]],
    top_n: int,
    concurrency: int,
    timeout_s: int,
    max_chars: int,
) -> None:
    if not results:
        return

    target_indexes: List[int] = []
    for index, result in enumerate(results):
        url_value = str(result.get("url") or "").strip()
        if url_value:
            target_indexes.append(index)
        if len(target_indexes) >= top_n:
            break

    if not target_indexes:
        return

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        future_to_index = {
            executor.submit(
                _fetch_content_excerpt,
                str(results[i].get("url") or ""),
                max(1, timeout_s),
                max(200, max_chars),
            ): i
            for i in target_indexes
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                content = future.result()
            except Exception as exc:
                content = {"content_excerpt": "", "content_error": f"fetch_error:{_safe_error_message(str(exc), max_len=120)}"}

            results[index]["content_excerpt"] = content.get("content_excerpt") or ""
            if content.get("content_error"):
                results[index]["content_error"] = content.get("content_error")


def _http_get_json(
    endpoint: str,
    params: Dict[str, Any],
    api_key: str,
    timeout_s: int = 30,
) -> Tuple[int, Dict[str, str], Any]:
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
    url = f"{endpoint}?{qs}"

    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("Accept-Encoding", "gzip")
    req.add_header("User-Agent", f"openclaw-brave/{VERSION}")
    req.add_header("X-Subscription-Token", api_key)

    with urllib.request.urlopen(req, timeout=timeout_s) as response:
        status = response.getcode()
        headers = dict(response.headers.items())
        raw = response.read()

    raw = _decode_body(raw, headers.get("Content-Encoding", ""))
    if not raw:
        return status, headers, {}

    decoded = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        payload = {"raw_text": decoded}
    return status, headers, payload


def _request_with_retry(
    endpoint: str,
    params: Dict[str, Any],
    api_key: str,
    timeout_s: int,
    retry_attempts: int,
    retry_base_delay_ms: int,
    retry_max_delay_ms: int,
) -> Tuple[int, Dict[str, str], Any, str, Optional[Dict[str, Any]], str]:
    last_status = 0
    last_headers: Dict[str, str] = {}
    last_body: Any = {}
    last_error_code = "network_error"
    last_reason = "request_failed"

    for attempt in range(1, max(1, retry_attempts) + 1):
        try:
            status, headers, data = _http_get_json(endpoint=endpoint, params=params, api_key=api_key, timeout_s=max(1, timeout_s))
            rate_limit = _extract_rate_limit_headers(headers)
            if status < 400:
                return status, headers, data, "", rate_limit, ""

            error_code = _detect_error_code(status, data)
            message = ""
            if isinstance(data, dict):
                message = str(data.get("message") or data.get("error") or "")
            reason = _safe_error_message(message) if message else f"http_{status}"

            last_status = status
            last_headers = headers
            last_body = data
            last_error_code = error_code
            last_reason = reason

            if attempt < max(1, retry_attempts) and _is_retryable_error_code(error_code):
                retry_after_seconds = rate_limit.get("retry_after_seconds") if isinstance(rate_limit, dict) else None
                _sleep_with_backoff(
                    attempt=attempt,
                    base_delay_ms=max(1, retry_base_delay_ms),
                    max_delay_ms=max(1, retry_max_delay_ms),
                    retry_after_seconds=retry_after_seconds,
                )
                continue

            return status, headers, data, error_code, rate_limit, reason

        except urllib.error.HTTPError as e:
            status = int(getattr(e, "code", 0) or 0)
            headers = dict(e.headers.items()) if e.headers else {}
            body_raw = e.read() or b""
            body_raw = _decode_body(body_raw, headers.get("Content-Encoding", ""))
            decoded = body_raw.decode("utf-8", errors="replace")
            try:
                body_data: Any = json.loads(decoded) if decoded else {}
            except json.JSONDecodeError:
                body_data = {"raw_text": decoded[:1200]}

            rate_limit = _extract_rate_limit_headers(headers)
            error_code = _detect_error_code(status, body_data)
            message = ""
            if isinstance(body_data, dict):
                message = str(body_data.get("message") or body_data.get("error") or "")
            reason = _safe_error_message(message) if message else f"http_{status}"

            last_status = status
            last_headers = headers
            last_body = body_data
            last_error_code = error_code
            last_reason = reason

            if attempt < max(1, retry_attempts) and _is_retryable_error_code(error_code):
                retry_after_seconds = rate_limit.get("retry_after_seconds") if isinstance(rate_limit, dict) else None
                _sleep_with_backoff(
                    attempt=attempt,
                    base_delay_ms=max(1, retry_base_delay_ms),
                    max_delay_ms=max(1, retry_max_delay_ms),
                    retry_after_seconds=retry_after_seconds,
                )
                continue

            return status, headers, body_data, error_code, rate_limit, reason

        except Exception as e:
            last_status = 0
            last_headers = {}
            last_body = {}
            last_error_code = "network_error"
            last_reason = _safe_error_message(str(e))

            if attempt < max(1, retry_attempts):
                _sleep_with_backoff(
                    attempt=attempt,
                    base_delay_ms=max(1, retry_base_delay_ms),
                    max_delay_ms=max(1, retry_max_delay_ms),
                    retry_after_seconds=None,
                )
                continue

            return 0, {}, {}, last_error_code, None, last_reason

    return last_status, last_headers, last_body, last_error_code, _extract_rate_limit_headers(last_headers), last_reason

def _should_fallback_to_next_key(status: int, body: Any) -> bool:
    error_code = _detect_error_code(status, body)
    if error_code in ("auth_error", "quota_exceeded", "rate_limited"):
        return True
    if isinstance(body, dict):
        msg = (body.get("message") or body.get("error") or "")
        if isinstance(msg, str) and any(token in msg.lower() for token in ("quota", "credit", "billing", "rate limit", "rate-limit")):
            return True
    return False


def _sanitize_raw_for_output(raw: Any, max_bytes: int = 100000) -> Any:
    """Truncate raw_response if serialized form is too large."""
    try:
        serialized = json.dumps(raw, separators=(",", ":"))
        if len(serialized) <= max_bytes:
            return raw
        return {"_truncated": True, "_message": f"raw_response exceeded {max_bytes} bytes", "_preview": serialized[:2000]}
    except Exception:
        return {"_truncated": True, "_message": "raw_response not serializable"}


def _handle_local_search(
    web_data: Dict[str, Any],
    keys: List[str],
    key_idx: int,
    args,
    started_at: float,
) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
    """Two-step local search: extract POI IDs from web response, fetch details.
    Returns (results, error, error_code)."""
    locations = web_data.get("locations") if isinstance(web_data, dict) else None
    loc_results = locations.get("results") if isinstance(locations, dict) else None
    if not isinstance(loc_results, list) or not loc_results:
        return [], "no_local_results: query did not return location results", "no_local_results"

    poi_ids = []
    for loc in loc_results:
        loc_id = loc.get("id") if isinstance(loc, dict) else None
        if loc_id:
            poi_ids.append(str(loc_id))
    poi_ids = poi_ids[:20]  # Brave limit

    if not poi_ids:
        return [], "no_local_results: no POI IDs in location results", "no_local_results"

    # Step 2: Fetch POI details
    pois_endpoint = _BASE_URL + _LOCAL_POIS_PATH

    # Build URL with repeated ids params
    id_params = "&".join(f"ids={urllib.parse.quote(pid)}" for pid in poi_ids)
    other_parts: Dict[str, str] = {}
    if args.search_lang:
        other_parts["search_lang"] = args.search_lang
    if args.ui_lang:
        other_parts["ui_lang"] = args.ui_lang
    other_params = urllib.parse.urlencode(other_parts)
    full_params = id_params + ("&" + other_params if other_params else "")

    pois_url = pois_endpoint + "?" + full_params
    try:
        req = urllib.request.Request(pois_url)
        req.add_header("Accept", "application/json")
        req.add_header("Accept-Encoding", "gzip")
        req.add_header("X-Subscription-Token", keys[key_idx])
        req.add_header("User-Agent", f"openclaw-brave/{VERSION}")
        with urllib.request.urlopen(req, timeout=max(1, args.timeout)) as response:
            raw = response.read()
            raw = _decode_body(raw, response.headers.get("Content-Encoding", ""))
            charset = _extract_charset_from_content_type(response.headers.get("Content-Type", ""))
            pois_data = json.loads(raw.decode(charset, errors="replace"))
    except Exception as e:
        return [], f"local_pois_failed: {_safe_error_message(str(e))}", "local_pois_failed"

    pois_list = pois_data.get("results") if isinstance(pois_data, dict) else []
    if not isinstance(pois_list, list):
        pois_list = []

    # Optional: fetch descriptions
    descriptions: Dict[str, str] = {}
    if getattr(args, "local_descriptions", False) and poi_ids:
        try:
            desc_endpoint = _BASE_URL + _LOCAL_DESCRIPTIONS_PATH
            desc_url = desc_endpoint + "?" + id_params
            req = urllib.request.Request(desc_url)
            req.add_header("Accept", "application/json")
            req.add_header("Accept-Encoding", "gzip")
            req.add_header("X-Subscription-Token", keys[key_idx])
            req.add_header("User-Agent", f"openclaw-brave/{VERSION}")
            with urllib.request.urlopen(req, timeout=max(1, args.timeout)) as response:
                raw = response.read()
                raw = _decode_body(raw, response.headers.get("Content-Encoding", ""))
                charset = _extract_charset_from_content_type(response.headers.get("Content-Type", ""))
                desc_data = json.loads(raw.decode(charset, errors="replace"))
            for d in (desc_data.get("results") or []):
                if isinstance(d, dict) and d.get("id"):
                    descriptions[d["id"]] = d.get("description") or ""
        except Exception:
            pass  # descriptions are optional; failure is not fatal

    # Merge descriptions into POI data
    for poi in pois_list:
        if isinstance(poi, dict):
            poi_id = poi.get("id")
            if poi_id and poi_id in descriptions:
                poi["description"] = descriptions[poi_id]

    return _format_local_results(pois_list), None, None


def _build_output(
    query: str,
    used_key_index: Optional[int],
    results: List[Dict[str, Any]],
    error: Optional[str],
    http_status: Optional[int],
    error_code: Optional[str],
    rate_limit: Optional[Dict[str, Any]],
    meta: Optional[Dict[str, Any]],
    raw_response: Optional[Any],
    include_raw: bool,
    search_type: str = "web",
) -> Dict[str, Any]:
    effective_meta = meta or {}
    effective_meta.setdefault("endpoint", _ENDPOINTS.get(search_type, "/res/v1/web/search"))
    effective_meta.setdefault("version", VERSION)
    output: Dict[str, Any] = {
        "provider": "brave",
        "query": query,
        "type": search_type,
        "used_key_index": used_key_index,
        "results": results,
        "error": error,
        "http_status": http_status,
        "error_code": error_code,
        "rate_limit": rate_limit,
        "meta": effective_meta,
    }
    if include_raw:
        output["raw_response"] = _sanitize_raw_for_output(raw_response)
    return output

def main() -> int:
    started_at = time.time()
    parser = argparse.ArgumentParser(description="Brave Search (OpenClaw skill) - Python")
    parser.add_argument("--query", "-q", required=False, help="Search query")
    parser.add_argument("--count", "-n", type=int, default=10, help="Results per page (clamped to 1..20)")
    parser.add_argument("--offset", type=int, default=0, help="Pagination offset")
    parser.add_argument("--country", default=None, help="2-letter country code (e.g., US, DE)")
    parser.add_argument("--search-lang", default="en", help="Content language filter (ISO 639-1, e.g., en, de)")
    parser.add_argument("--ui-lang", default="en-US", help="UI language (e.g., en-US)")
    parser.add_argument("--format", choices=["json"], default="json", help="Output format (json only for now)")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds")
    parser.add_argument("--key-file", default=os.path.join(DEFAULT_KEY_DIR, "brave.json"),
                        help=f"Path to key file for key rotation (default: {DEFAULT_KEY_DIR}/brave.json)")
    parser.add_argument("--raw", action="store_true", help="Include raw Brave API payload in output as raw_response")
    parser.add_argument("--content", action="store_true", help="Fetch and extract text content for top search results")
    parser.add_argument("--content-top", type=int, default=3, help="Top N results to fetch when --content is set")
    parser.add_argument("--content-concurrency", type=int, default=3, help="Max parallel URL fetches for --content")
    parser.add_argument("--content-timeout", type=int, default=10, help="Per-URL timeout seconds for --content")
    parser.add_argument("--content-max-chars", type=int, default=2500, help="Max characters in extracted content excerpt")
    parser.add_argument("--retry-attempts", type=int, default=3, help="Max retry attempts for transient errors per key")
    parser.add_argument("--retry-base-delay-ms", type=int, default=250, help="Base delay for exponential backoff")
    parser.add_argument("--retry-max-delay-ms", type=int, default=5000, help="Max delay cap for backoff")
    parser.add_argument("--cb-failure-threshold", type=int, default=2, help="Consecutive key failures before circuit opens")
    parser.add_argument("--cb-reset-timeout-ms", type=int, default=30000, help="Circuit open duration before half-open")
    parser.add_argument("--cache-ttl-seconds", type=int, default=0, help="Optional cache TTL seconds; 0 disables cache")
    parser.add_argument("--cache-dir", default="~/.cache/openclaw/brave", help="Directory for optional cache files")
    parser.add_argument("--cache-clear", action="store_true", help="Delete cache entry for the current query before execution")
    parser.add_argument("--cache-clear-all", action="store_true", help="Delete all cache entries in --cache-dir before execution")
    parser.add_argument("--cache-clear-all-only", action="store_true", help="Clear entire cache directory and exit without calling API")
    parser.add_argument("--cache-prune-expired", action="store_true", help="Prune expired/corrupt cache entries before execution")
    parser.add_argument("--cache-max-entries", type=int, default=500, help="Max cache entry count; oldest entries are deleted")
    parser.add_argument("--debug", action="store_true", help="Include sanitized diagnostics in meta; never prints secrets")
    parser.add_argument("--allow-custom-endpoint", action="store_true", help="Allow BRAVE_SEARCH_ENDPOINT to point to non-Brave URLs (SSRF risk)")
    parser.add_argument("--type", "-t", choices=["web", "images", "videos", "news", "suggest", "local"],
                        default="web", help="Search type / endpoint")
    parser.add_argument("--freshness", default=None,
                        help="Time filter: pd (past day), pw (past week), pm (past month), py (past year), or YYYY-MM-DDtoYYYY-MM-DD")
    parser.add_argument("--safesearch", choices=["off", "moderate", "strict"], default="moderate",
                        help="Content safety filter")
    parser.add_argument("--result-filter", default=None,
                        help="Comma-separated result types for web search (e.g., discussions,faq,infobox,news,videos,web,locations)")
    parser.add_argument("--extra-snippets", action="store_true",
                        help="Request up to 5 extra snippets per result (requires Data for AI plan)")
    parser.add_argument("--goggles", default=None,
                        help="Goggles URL or definition for custom result re-ranking")
    parser.add_argument("--local-descriptions", action="store_true",
                        help="Also fetch AI-generated descriptions for local results")
    args = parser.parse_args()

    if args.cache_clear_all_only:
        cleared_all = _cache_delete_all(cache_dir=args.cache_dir)
        out = {
            "provider": "brave",
            "query": args.query or "",
            "type": args.type,
            "used_key_index": None,
            "results": [],
            "error": None,
            "http_status": 200,
            "error_code": None,
            "rate_limit": None,
            "meta": {
                "duration_ms": int((time.time() - started_at) * 1000),
                "cache_dir": str(Path(args.cache_dir).expanduser()),
                "cache_pruned_expired": 0,
                "endpoint": _ENDPOINTS.get(args.type, "/res/v1/web/search"),
                "version": VERSION,
            },
            "cache": {
                "hit": False,
                "ttl_seconds": max(0, args.cache_ttl_seconds),
                "cleared": False,
                "cleared_all": cleared_all,
            },
            "circuit_breakers": [],
        }
        print(json.dumps(out, separators=(",", ":")))
        return 0

    if not (args.query and str(args.query).strip()):
        out = _build_output(
            query="",
            used_key_index=None,
            results=[],
            error="missing_query: set --query",
            http_status=None,
            error_code="missing_query",
            rate_limit=None,
            meta={"duration_ms": int((time.time() - started_at) * 1000)},
            raw_response=None,
            include_raw=args.raw,
            search_type=args.type,
        )
        print(json.dumps(out, separators=(",", ":")))
        return 2

    query = str(args.query).strip()

    if len(query) > MAX_QUERY_LENGTH:
        out = _build_output(
            query=query[:MAX_QUERY_LENGTH],
            used_key_index=None,
            results=[],
            error=f"query_too_long: max {MAX_QUERY_LENGTH} chars, got {len(query)}",
            http_status=None,
            error_code="query_too_long",
            rate_limit=None,
            meta={"duration_ms": int((time.time() - started_at) * 1000)},
            raw_response=None,
            include_raw=False,
            search_type=args.type,
        )
        print(json.dumps(out, separators=(",", ":")))
        return 2

    keys = _parse_keys(key_file=args.key_file)
    if not keys:
        out = _build_output(
            query=query,
            used_key_index=None,
            results=[],
            error="missing_api_keys: set BRAVE_API_KEY or BRAVE_API_KEY_1..3 (checked key file and env)",
            http_status=None,
            error_code="missing_api_keys",
            rate_limit=None,
            meta={"duration_ms": int((time.time() - started_at) * 1000)},
            raw_response=None,
            include_raw=args.raw,
            search_type=args.type,
        )
        print(json.dumps(out, separators=(",", ":")))
        return 2

    search_type = args.type
    endpoint_path = _ENDPOINTS[search_type]
    if search_type == "web":
        endpoint = os.getenv("BRAVE_SEARCH_ENDPOINT", _BASE_URL + endpoint_path).strip() or (_BASE_URL + endpoint_path)
    else:
        endpoint = _BASE_URL + endpoint_path

    endpoint_error = _validate_endpoint(endpoint, args.allow_custom_endpoint)
    if endpoint_error:
        out = _build_output(
            query=query,
            used_key_index=None,
            results=[],
            error=endpoint_error,
            http_status=None,
            error_code="endpoint_blocked",
            rate_limit=None,
            meta={"duration_ms": int((time.time() - started_at) * 1000)},
            raw_response=None,
            include_raw=False,
            search_type=search_type,
        )
        print(json.dumps(out, separators=(",", ":")))
        return 2

    params = _build_params(args, search_type)

    content_opts = {
        "enabled": bool(args.content) and search_type in ("web", "news"),
        "top": max(1, args.content_top),
        "concurrency": max(1, args.content_concurrency),
        "timeout": max(1, args.content_timeout),
        "max_chars": max(200, args.content_max_chars),
    }

    cache_ttl = max(0, args.cache_ttl_seconds)
    # Cap local type cache TTL at 4 hours since POI IDs are ephemeral
    if search_type == "local" and cache_ttl > 14400:
        cache_ttl = 14400
    cache_key = _build_cache_key(endpoint=endpoint, params=params, content_opts=content_opts)

    cache_cleared = False
    cache_cleared_all = 0
    cache_pruned_expired = 0
    cache_pruned_overflow = 0

    if args.cache_prune_expired:
        cache_pruned_expired = _cache_prune_expired(cache_dir=args.cache_dir)

    if args.cache_clear_all:
        cache_cleared_all = _cache_delete_all(cache_dir=args.cache_dir)

    if args.cache_clear:
        cache_cleared = _cache_delete(cache_dir=args.cache_dir, cache_key=cache_key)

    if cache_ttl > 0:
        cached = _cache_read(cache_dir=args.cache_dir, cache_key=cache_key)
        if isinstance(cached, dict):
            cached.setdefault("provider", "brave")
            cached.setdefault("query", query)
            cached.setdefault("used_key_index", None)
            cached.setdefault("results", [])
            cached.setdefault("error", None)
            cached.setdefault("http_status", 200)
            cached.setdefault("error_code", None)
            cached.setdefault("rate_limit", None)
            cached.setdefault("meta", {})
            if not args.raw and "raw_response" in cached:
                cached.pop("raw_response", None)
            cached["meta"]["duration_ms"] = int((time.time() - started_at) * 1000)
            cached["meta"]["cache_pruned_expired"] = cache_pruned_expired
            cached["meta"]["cache_pruned_overflow"] = cache_pruned_overflow
            cached["cache"] = {
                "hit": True,
                "ttl_seconds": cache_ttl,
                "cleared": cache_cleared,
                "cleared_all": cache_cleared_all,
            }
            print(json.dumps(cached, separators=(",", ":")))
            return 0

    last_error = "all_keys_failed"
    last_status: Optional[int] = None
    last_raw_response: Optional[Any] = None
    last_error_code: Optional[str] = None
    last_rate_limit: Optional[Dict[str, Any]] = None
    circuit_states: List[Dict[str, Any]] = [_new_circuit_breaker_state() for _ in keys]
    attempted_key_indexes: List[int] = []

    for idx, key in enumerate(keys):
        cb_state = circuit_states[idx]
        now = time.time()
        if not _circuit_is_available(cb_state, now):
            wait_seconds = int(max(0.0, float(cb_state.get("open_until", 0.0) - now)))
            last_error_code = "circuit_open"
            last_error = f"key_{idx}_skipped: code=circuit_open; wait_seconds={wait_seconds}"
            continue

        attempted_key_indexes.append(idx)

        status, headers, data, error_code, rate_limit, reason = _request_with_retry(
            endpoint=endpoint,
            params=params,
            api_key=key,
            timeout_s=max(1, args.timeout),
            retry_attempts=max(1, args.retry_attempts),
            retry_base_delay_ms=max(1, args.retry_base_delay_ms),
            retry_max_delay_ms=max(1, args.retry_max_delay_ms),
        )

        last_status = status if status > 0 else last_status
        last_raw_response = data
        last_rate_limit = rate_limit

        if status >= 400 or status <= 0:
            last_error_code = error_code or _detect_error_code(status, data)
            reason_text = _safe_error_message(reason) if reason else (f"http_{status}" if status else "request_failed")
            last_error = f"key_{idx}_failed: code={last_error_code}; status={status}; reason={reason_text}"

            _circuit_on_failure(
                cb_state=cb_state,
                threshold=max(1, args.cb_failure_threshold),
                reset_timeout_ms=max(1, args.cb_reset_timeout_ms),
                now=time.time(),
            )

            if _should_fallback_to_next_key(status, data) or _is_retryable_error_code(last_error_code or ""):
                # Rotate key file so next invocation starts with next key
                if _should_fallback_to_next_key(status, data) and last_error_code in ("auth_error", "quota_exceeded"):
                    rotate_key(args.key_file)
                continue

            out = _build_output(
                query=query,
                used_key_index=None,
                results=[],
                error=last_error,
                http_status=last_status,
                error_code=last_error_code,
                rate_limit=last_rate_limit,
                meta={
                    "duration_ms": int((time.time() - started_at) * 1000),
                    "attempted_key_indexes": attempted_key_indexes,
                    "cache_pruned_expired": cache_pruned_expired,
                    "cache_pruned_overflow": cache_pruned_overflow,
                },
                raw_response=last_raw_response,
                include_raw=args.raw,
                search_type=search_type,
            )
            out["circuit_breakers"] = [
                {
                    "index": i,
                    "state": state.get("state", "closed"),
                    "failures": int(state.get("failures", 0) or 0),
                }
                for i, state in enumerate(circuit_states)
            ]
            out["cache"] = {
                "hit": False,
                "ttl_seconds": cache_ttl,
                "cleared": cache_cleared,
                "cleared_all": cache_cleared_all,
            }
            print(json.dumps(out, separators=(",", ":")))
            return 1

        _circuit_on_success(cb_state)

        # Route to appropriate formatter / local 2-step handler
        formatter = _FORMATTERS[search_type]
        local_error = None
        local_error_code = None
        if search_type == "local":
            results, local_error, local_error_code = _handle_local_search(data, keys, idx, args, started_at)
            if local_error:
                out = _build_output(
                    query=query,
                    used_key_index=idx,
                    results=results,
                    error=local_error,
                    http_status=status,
                    error_code=local_error_code,
                    rate_limit=last_rate_limit,
                    meta={
                        "duration_ms": int((time.time() - started_at) * 1000),
                        "attempted_key_indexes": attempted_key_indexes,
                        "cache_pruned_expired": cache_pruned_expired,
                        "cache_pruned_overflow": cache_pruned_overflow,
                    },
                    raw_response=data,
                    include_raw=args.raw,
                    search_type=search_type,
                )
                out["circuit_breakers"] = [
                    {
                        "index": i,
                        "state": state.get("state", "closed"),
                        "failures": int(state.get("failures", 0) or 0),
                    }
                    for i, state in enumerate(circuit_states)
                ]
                out["cache"] = {
                    "hit": False,
                    "ttl_seconds": cache_ttl,
                    "cleared": cache_cleared,
                    "cleared_all": cache_cleared_all,
                }
                print(json.dumps(out, separators=(",", ":")))
                return 1
        else:
            results = formatter(data if isinstance(data, dict) else {})

        if args.content and results and search_type in ("web", "news"):
            content_top = max(1, min(args.content_top, len(results)))
            content_concurrency = max(1, min(args.content_concurrency, 10))
            content_timeout = max(1, min(args.content_timeout, 60))
            content_max_chars = max(200, min(args.content_max_chars, 10000))
            _enrich_results_with_content(
                results=results,
                top_n=content_top,
                concurrency=content_concurrency,
                timeout_s=content_timeout,
                max_chars=content_max_chars,
            )

        out = _build_output(
            query=query,
            used_key_index=idx,
            results=results,
            error=None,
            http_status=status,
            error_code=None,
            rate_limit=last_rate_limit,
            meta={
                "duration_ms": int((time.time() - started_at) * 1000),
                "attempted_key_indexes": attempted_key_indexes,
                "cache_pruned_expired": cache_pruned_expired,
                "cache_pruned_overflow": cache_pruned_overflow,
            },
            raw_response=data,
            include_raw=args.raw,
            search_type=search_type,
        )
        out["circuit_breakers"] = [
            {
                "index": i,
                "state": state.get("state", "closed"),
                "failures": int(state.get("failures", 0) or 0),
            }
            for i, state in enumerate(circuit_states)
        ]
        out["cache"] = {
            "hit": False,
            "ttl_seconds": cache_ttl,
            "cleared": cache_cleared,
            "cleared_all": cache_cleared_all,
        }

        if cache_ttl > 0:
            _cache_write(cache_dir=args.cache_dir, cache_key=cache_key, payload=out, ttl_seconds=cache_ttl)
            if args.cache_max_entries > 0:
                cache_pruned_overflow = _cache_enforce_max_entries(
                    cache_dir=args.cache_dir,
                    max_entries=max(1, args.cache_max_entries),
                )
                out["meta"]["cache_pruned_overflow"] = cache_pruned_overflow

        if args.debug:
            out["meta"]["debug"] = {
                "endpoint": endpoint,
                "search_type": search_type,
                "key_count": len(keys),
                "params": {k: v for k, v in params.items() if k != "q"},
                "content_opts": content_opts,
                "retry_attempts": args.retry_attempts,
                "cb_failure_threshold": args.cb_failure_threshold,
            }

        print(json.dumps(out, separators=(",", ":")))
        return 0

    out = _build_output(
        query=query,
        used_key_index=None,
        results=[],
        error=last_error,
        http_status=last_status,
        error_code=last_error_code,
        rate_limit=last_rate_limit,
        meta={
            "duration_ms": int((time.time() - started_at) * 1000),
            "attempted_key_indexes": attempted_key_indexes,
            "cache_pruned_expired": cache_pruned_expired,
            "cache_pruned_overflow": cache_pruned_overflow,
        },
        raw_response=last_raw_response,
        include_raw=args.raw,
        search_type=search_type,
    )
    out["circuit_breakers"] = [
        {
            "index": i,
            "state": state.get("state", "closed"),
            "failures": int(state.get("failures", 0) or 0),
        }
        for i, state in enumerate(circuit_states)
    ]
    out["cache"] = {
        "hit": False,
        "ttl_seconds": cache_ttl,
        "cleared": cache_cleared,
        "cleared_all": cache_cleared_all,
    }
    print(json.dumps(out, separators=(",", ":")))
    return 1

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as _exc:
        _err_out = {
            "provider": "brave",
            "query": "",
            "used_key_index": None,
            "results": [],
            "error": f"internal_error: {_safe_error_message(str(_exc))}",
            "http_status": None,
            "error_code": "internal_error",
            "rate_limit": None,
            "meta": {},
        }
        print(json.dumps(_err_out, separators=(",", ":")))
        if "--debug" in sys.argv:
            import traceback
            traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)
