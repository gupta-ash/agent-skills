#!/usr/bin/env python
"""
anthropic-search — server-side web search via Anthropic's native web_search_20250305 tool.
Stdlib-only. Reads ANTHROPIC_API_KEY from env. 60s short cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

# Force UTF-8 stdout (Windows cp1252 default crashes on Unicode)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_USES = 5
CACHE_TTL_S = 60
CACHE_DIR = Path.home() / ".local" / "share" / "anthropic-search"
CACHE_FILE = CACHE_DIR / "cache.json"


# ---------- cache ----------

def _cache_key(query: str, max_uses: int, model: str) -> str:
    h = hashlib.sha256(f"{query}|{max_uses}|{model}".encode("utf-8")).hexdigest()
    return h[:16]


def cache_get(key: str) -> Optional[Dict[str, Any]]:
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    entry = data.get(key)
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > CACHE_TTL_S:
        return None
    return entry.get("value")


def cache_put(key: str, value: Dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}
    # Opportunistic GC: drop expired entries
    now = time.time()
    data = {k: v for k, v in data.items() if now - v.get("ts", 0) <= CACHE_TTL_S}
    data[key] = {"ts": now, "value": value}
    CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")


# ---------- API call ----------

class AnthropicSearchError(Exception):
    pass


def call_anthropic(query: str, max_uses: int, model: str, timeout: int = 60) -> Dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AnthropicSearchError("ANTHROPIC_API_KEY not set in environment")

    body = {
        "model": model,
        "max_tokens": 2048,
        "tools": [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": max_uses,
            }
        ],
        "messages": [{"role": "user", "content": query}],
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return payload
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        if e.code == 401:
            raise AnthropicSearchError(f"Authentication failed (HTTP 401): {body_text[:200]}")
        if e.code == 429:
            raise AnthropicSearchError(f"Rate limit (HTTP 429): {body_text[:200]}")
        raise AnthropicSearchError(f"HTTP {e.code}: {body_text[:300]}")
    except urllib.error.URLError as e:
        raise AnthropicSearchError(f"Network error: {e}")


# ---------- response parsing ----------

def parse_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Walk content[] for server_tool_use, web_search_tool_result, text blocks."""
    results: List[Dict[str, Any]] = []
    text_parts: List[str] = []
    tool_calls = 0
    seen_urls = set()

    content = payload.get("content", [])
    for block in content:
        btype = block.get("type")
        if btype == "server_tool_use":
            tool_calls += 1
        elif btype == "web_search_tool_result":
            inner = block.get("content", [])
            if isinstance(inner, list):
                for item in inner:
                    if item.get("type") == "web_search_result":
                        url = item.get("url")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            results.append({
                                "title": item.get("title"),
                                "url": url,
                                "snippet": None,  # Anthropic doesn't return per-result snippets
                                "age": item.get("page_age"),
                            })
        elif btype == "text":
            text_parts.append(block.get("text", ""))

    return {
        "results": results,
        "text": "".join(text_parts).strip(),
        "tool_calls_made": tool_calls,
        "model": payload.get("model"),
        "stop_reason": payload.get("stop_reason"),
        "usage": payload.get("usage"),
    }


# ---------- markdown rendering ----------

def to_markdown(parsed: Dict[str, Any], query: str, elapsed_ms: int) -> str:
    out: List[str] = []
    out.append(f"# Search results: {query!r}\n")
    out.append(parsed.get("text", "(no synthesized text)").strip())
    out.append("\n\n---")
    results = parsed.get("results", [])
    out.append(f"\n**Sources** (n={len(results)})\n")
    for i, r in enumerate(results, 1):
        title = r.get("title") or "(untitled)"
        url = r.get("url", "")
        age = r.get("age")
        age_str = f" — {age}" if age else ""
        out.append(f"{i}. [{title}]({url}){age_str}")
    out.append(f"\n_Searched {parsed.get('tool_calls_made', 0)} times in {elapsed_ms}ms via Anthropic native web_search_20250305 ({parsed.get('model','?')})._")
    return "\n".join(out)


# ---------- public API ----------

def anthropic_search(
    query: str,
    max_uses: int = DEFAULT_MAX_USES,
    model: str = DEFAULT_MODEL,
    format: str = "json",
    cache: bool = True,
) -> Dict[str, Any]:
    """Public API. See SKILL.md for full schema."""
    if not query or not query.strip():
        return {"query": query, "results": [], "text": "", "tool_calls_made": 0,
                "elapsed_ms": 0, "model": model, "cached": False, "error": "empty query"}

    key = _cache_key(query, max_uses, model)
    if cache:
        cached = cache_get(key)
        if cached is not None:
            cached["cached"] = True
            cached["elapsed_ms"] = 0
            return _shape_output(cached, query, format, 0)

    t0 = time.time()
    try:
        payload = call_anthropic(query, max_uses, model)
    except AnthropicSearchError as e:
        elapsed_ms = int((time.time() - t0) * 1000)
        return {"query": query, "results": [], "text": "", "tool_calls_made": 0,
                "elapsed_ms": elapsed_ms, "model": model, "cached": False, "error": str(e)}

    elapsed_ms = int((time.time() - t0) * 1000)
    parsed = parse_response(payload)
    out = {
        "query": query,
        "results": parsed["results"],
        "text": parsed["text"],
        "tool_calls_made": parsed["tool_calls_made"],
        "elapsed_ms": elapsed_ms,
        "model": parsed.get("model") or model,
        "cached": False,
        "error": None,
    }
    if cache:
        cache_put(key, dict(out))
    return _shape_output(out, query, format, elapsed_ms)


def _shape_output(parsed: Dict[str, Any], query: str, format: str, elapsed_ms: int) -> Dict[str, Any]:
    if format == "json":
        return parsed
    if format == "markdown":
        return {"markdown": to_markdown(parsed, query, parsed.get("elapsed_ms", elapsed_ms))}
    if format == "both":
        return {"json": parsed, "markdown": to_markdown(parsed, query, parsed.get("elapsed_ms", elapsed_ms))}
    return parsed  # unknown format → fallback to json


# ---------- CLI ----------

def main():
    p = argparse.ArgumentParser(description="anthropic-search — server-side web search via Anthropic native web_search_20250305")
    p.add_argument("--query", "-q", required=True, help="Search query")
    p.add_argument("--max-uses", type=int, default=DEFAULT_MAX_USES, help=f"Max web_search invocations (default {DEFAULT_MAX_USES})")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"Anthropic model (default {DEFAULT_MODEL})")
    p.add_argument("--format", choices=["json", "markdown", "both"], default="json")
    p.add_argument("--no-cache", action="store_true", help="Disable 60s short cache")
    args = p.parse_args()

    out = anthropic_search(
        query=args.query,
        max_uses=args.max_uses,
        model=args.model,
        format=args.format,
        cache=not args.no_cache,
    )
    if args.format == "markdown":
        sys.stdout.write(out.get("markdown", ""))
        sys.stdout.write("\n")
    else:
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2 if sys.stdout.isatty() else None)
        sys.stdout.write("\n")
    if isinstance(out, dict) and out.get("error"):
        sys.exit(2)


if __name__ == "__main__":
    main()
