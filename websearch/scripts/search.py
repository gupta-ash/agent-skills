#!/usr/bin/env python3
"""Unified Search Router v1.1.0

Routes search queries to the best provider per category with deterministic
fallback chains. All configuration is embedded — no file I/O needed.

Architecture:
  - Scout LLM decides WHAT to search (query formulation, mode selection)
  - Router decides WHERE to search (provider selection, fallback, key rotation)
  - Scout can override with --force-provider when Coordinator explicitly specifies

Usage:
  python3 search.py --query "S&P 500 today" --mode finance
  python3 search.py --query "Kubernetes comparison" --mode research --count 5
  python3 search.py --query "NVIDIA 10-K" --mode filings
  python3 search.py --query "AI startup CEO" --mode people
  python3 search.py --query "test" --mode news --explain-routing
  python3 search.py --query "test" --force-provider brave:news

Requires: Python 3.8+ (stdlib only)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

VERSION = "1.1.0"

# =============================================================================
# CONFIGURATION — Edit these to reconfigure routing
# =============================================================================

# Providers disabled across ALL chains (including --force-provider).
DISABLED_PROVIDERS = {"copilot-search"}
COPILOT_DISABLED_REASON = (
    "Copilot disabled: reliable search execution is not qualified (search_not_executed); "
    "diagnostics deferred."
)

# Routing table: mode -> ordered list of "provider:type" entries.
# Router tries each in order, skipping disabled providers, until one succeeds.
# Last entry in most chains is "searxng" (emergency fallback).
#
# General web is portable raw retrieval: Codex -> Brave -> Tavily -> Exa -> SearXNG.
# A successful raw response, including results:[], stops fallback. Other modes
# preserve specialist provider translations; research explicitly retains Codex.
# Explicit synthesis retains only Copilot, disabled before command construction.
# Anthropic remains an explicit override only.
# Native Public/Google/Codex orchestration belongs to OMP, not this router.
ROUTES = {
    "news":      ["tavily:news",     "brave:news",     "serper:news",    "searxng"],
    "finance":   ["brave:news",      "serper:news",    "tavily:general", "searxng"],
    "tech-news": ["brave:news",      "tavily:news",    "serper:news",    "searxng"],
    "company":   ["brave:web",       "serper:search",  "exa:auto",       "searxng"],
    "filings":   ["exa:financial-report", "serper:search", "brave:web",  "searxng"],
    "people":    ["exa:people",      "brave:web",      "serper:search",  "searxng"],
    "research":  ["brave:web",       "exa:neural",     "serper:search",  "codex-search", "searxng"],
    "local":     ["brave:web",       "tavily:general",  "serper:search", "searxng"],
    "currency":  ["brave:news",      "serper:news",    "brave:web",      "searxng"],
    "social":    ["exa:tweet",       "brave:news",     "serper:news",    "searxng"],
    "similar":   ["exa:similar"],
    "deep":      ["tavily:research", "exa:deep-search"],
    "web":       ["codex-search", "brave:web",     "tavily:general", "exa:auto", "searxng"],
    "synthesis": ["copilot-search"],
}

# Time-sensitive modes where Serper news gets auto-freshness (--tbs qdr:w)
TIME_SENSITIVE_MODES = {"news", "tech-news", "finance", "currency", "social"}

# Display names distinguish raw retrieval from provider synthesis.
_PROVIDER_DISPLAY = {
    "brave": "Brave", "tavily": "Tavily", "serper": "Serper", "exa": "Exa",
    "searxng": "SearXNG",
    "codex-search": "Codex standalone",
    "copilot-search": "Copilot",
    "anthropic-search": "Anthropic",
}


def _format_chain(entries):
    """Format a ROUTES chain as a '>'-separated display string for the epilog.

    Drops the ':sub_type' suffix and dedupes consecutive same-provider entries
    to keep the line short. Falls back to the raw provider name if no display
    label is registered.
    """
    names = []
    for entry in entries:
        provider = entry.split(":", 1)[0]
        label = _PROVIDER_DISPLAY.get(provider, provider)
        if not names or names[-1] != label:
            names.append(label)
    return " > ".join(names)

# Provider script paths (absolute)
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = {
    "brave":                os.path.join(SKILL_DIR, "providers", "brave",                "scripts", "brave_search.py"),
    "tavily":               os.path.join(SKILL_DIR, "providers", "tavily",               "scripts", "tavily_search.py"),
    "serper":               os.path.join(SKILL_DIR, "providers", "serper",               "scripts", "serper.py"),
    "exa":                  os.path.join(SKILL_DIR, "providers", "exa",                  "scripts", "exa_search.py"),
    "searxng":              os.path.join(SKILL_DIR, "providers", "searxng",              "searxng_search.py"),
    "codex-search":     os.path.join(SKILL_DIR, "providers", "codex-search",     "search.py"),
    "anthropic-search": os.path.join(SKILL_DIR, "providers", "anthropic-search", "search.py"),
    "copilot-search":   os.path.join(SKILL_DIR, "providers", "copilot-search",   "search.py"),
}

# Default key directory for provider key files
DEFAULT_KEY_DIR = str(Path.home() / ".config" / "websearch" / "keys")

# Bundled key rotation helper, also used by direct provider entrypoints.
# Prepending this path preserves the caller's remaining Python import context.
_KEYMANAGER_DIR = os.path.join(SKILL_DIR, "providers", "_shared")

# LLM-synthesis providers (server-side LLM does its own search + synthesis).
# Used for both timeout override (LLM_PROVIDER_TIMEOUT_S) and the
# silent-failure detection branch in _is_error().
LLM_PROVIDERS = ("copilot-search", "anthropic-search")

# Preserve the existing 120-second synthesis-provider watchdog class. This is
# a timeout policy, not a promise of latency, entitlement or qualification.
LLM_PROVIDER_TIMEOUT_S = 120

# Map provider name to key file basename
KEY_FILE_NAMES = {
    "brave":  "brave.json",
    "tavily": "tavily.json",
    "serper": "serper.json",
    "exa":    "exa.json",
    # searxng has no API key
}

# Freshness translation: unified value -> per-provider flag value
# Note: Brave has no "past hour" option; "hour" maps to "pd" (past day) as closest match
FRESHNESS_MAP = {
    "brave":  {"hour": "pd", "day": "pd", "week": "pw", "month": "pm", "year": "py"},
    "tavily": {"hour": "day", "day": "day", "week": "week", "month": "month", "year": "year"},
    "serper": {"hour": "qdr:h", "day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"},
    # Exa uses ISO date math — handled in _build_exa_freshness()
}

FRESHNESS_DAYS = {"hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}


# =============================================================================
# PROVIDER COMMAND BUILDERS
# =============================================================================

def _parse_provider_entry(entry):
    """Parse 'provider:type' into (provider, sub_type). e.g. 'brave:news' -> ('brave', 'news')"""
    if ":" in entry:
        provider, sub = entry.split(":", 1)
        return provider, sub
    return entry, None


def _build_exa_freshness(freshness):
    """Convert freshness string to Exa --start-date ISO format."""
    days = FRESHNESS_DAYS.get(freshness)
    if days is not None:
        dt = datetime.utcnow() - timedelta(days=max(days, 1))
        return dt.strftime("%Y-%m-%d")
    return None


def _inject_site_filter(query, domains):
    """Inject site: operators into query for Brave/Serper/SearXNG."""
    if not domains:
        return query
    parts = [f"site:{d.strip()}" for d in domains if d.strip()]
    if len(parts) == 1:
        return f"{query} {parts[0]}"
    return f"{query} ({' OR '.join(parts)})"


def _build_command(provider, sub_type, query, count, freshness, domains, timeout, mode, key_dir=None):
    """Build subprocess command for a provider.

    Returns: (cmd_list, description_string)
    """
    script = SCRIPTS.get(provider)
    if not script:
        return None, f"Unknown provider: {provider}"

    if not os.path.isfile(script):
        return None, f"Script not found: {script}"

    cmd = [sys.executable, script]
    effective_query = query

    # Resolve key file path for this provider (if applicable)
    _key_file = None
    if key_dir and provider in KEY_FILE_NAMES:
        _key_file = os.path.join(key_dir, KEY_FILE_NAMES[provider])

    # --- Provider-specific command building ---

    if provider == "brave":
        effective_query = _inject_site_filter(query, domains)
        cmd += ["--query", effective_query]
        cmd += ["--count", str(count)]
        if sub_type and sub_type in ("web", "news", "images", "videos", "suggest", "local"):
            cmd += ["--type", sub_type]
        if freshness and freshness in FRESHNESS_MAP.get("brave", {}):
            cmd += ["--freshness", FRESHNESS_MAP["brave"][freshness]]
        cmd += ["--timeout", str(timeout)]
        if _key_file:
            cmd += ["--key-file", _key_file]

    elif provider == "tavily":
        cmd += ["--query", query]
        cmd += ["--max-results", str(count)]
        if sub_type == "news":
            cmd += ["--topic", "news"]
        elif sub_type == "finance":
            cmd += ["--topic", "finance"]
        elif sub_type == "research":
            cmd += ["--search-depth", "advanced"]
        # else: general (default)
        if freshness and freshness in FRESHNESS_MAP.get("tavily", {}):
            cmd += ["--time-range", FRESHNESS_MAP["tavily"][freshness]]
        if domains:
            cmd += ["--include-domains", ",".join(d.strip() for d in domains if d.strip())]
        cmd += ["--timeout", str(timeout)]
        if _key_file:
            cmd += ["--key-file", _key_file]

    elif provider == "serper":
        effective_query = _inject_site_filter(query, domains)
        cmd += ["--query", effective_query]
        cmd += ["--count", str(count)]
        if sub_type and sub_type in ("search", "news", "images", "videos", "places",
                                      "shopping", "scholar", "patents", "autocomplete"):
            cmd += ["--type", sub_type]
        # Auto-freshness for Serper news on time-sensitive modes
        if sub_type == "news" and mode in TIME_SENSITIVE_MODES and not freshness:
            cmd += ["--tbs", "qdr:w"]
        elif freshness and freshness in FRESHNESS_MAP.get("serper", {}):
            cmd += ["--tbs", FRESHNESS_MAP["serper"][freshness]]
        cmd += ["--timeout", str(timeout)]
        if _key_file:
            cmd += ["--key-file", _key_file]

    elif provider == "exa":
        cmd += ["--query", query]
        cmd += ["--count", str(count)]
        # Map sub_type to Exa's --type, --category, --search-type
        if sub_type == "financial-report":
            cmd += ["--category", "financial report"]
        elif sub_type == "people":
            cmd += ["--category", "people"]
        elif sub_type == "tweet":
            cmd += ["--category", "tweet"]
        elif sub_type == "company":
            cmd += ["--category", "company"]
        elif sub_type == "news":
            cmd += ["--category", "news"]
        elif sub_type == "neural":
            cmd += ["--search-type", "neural"]
        elif sub_type == "similar":
            # similar mode: --query is treated as the URL to find similar content for
            cmd = [sys.executable, script, "--type", "similar"]
            cmd += ["--url", query]  # Pass query as URL for findSimilar
            cmd += ["--count", str(count)]
            cmd += ["--exclude-source-domain"]
        elif sub_type == "deep-search":
            cmd += ["--search-type", "deep"]
        elif sub_type == "fast":
            cmd += ["--search-type", "fast"]
        elif sub_type == "instant":
            cmd += ["--search-type", "instant"]
        elif sub_type == "deep-reasoning":
            cmd += ["--search-type", "deep-reasoning"]
        elif sub_type == "research":
            cmd += ["--type", "research"]
        # else: auto (default)
        if freshness:
            start_date = _build_exa_freshness(freshness)
            if start_date:
                cmd += ["--start-date", start_date]
        if domains:
            cmd += ["--include-domains", ",".join(d.strip() for d in domains if d.strip())]
        cmd += ["--timeout", str(timeout)]
        if _key_file:
            cmd += ["--key-file", _key_file]

    elif provider == "codex-search":
        cmd += ["--query", query, "--max-results", str(count), "--timeout", str(timeout)]
        if freshness and freshness in FRESHNESS_DAYS:
            # Codex accepts positive days: approximate past hour as past day.
            cmd += ["--recency-days", str(max(1, FRESHNESS_DAYS[freshness]))]
        if domains:
            cmd += ["--domains", ",".join(d.strip() for d in domains if d.strip())]

    elif provider == "searxng":
        effective_query = _inject_site_filter(query, domains)
        cmd += [effective_query]  # positional argument
        cmd += ["--count", str(count)]

    elif provider == "anthropic-search":
        # Explicit Anthropic synthesis override; never an automatic fallback.
        # The provider does not accept count/domain/date filters directly.
        # Bound its search budget using the existing max-uses translation.
        cmd += ["--query", query]
        cmd += ["--max-uses", str(min(5, max(1, (count + 4) // 4)))]  # 10→3; 5→2; 1→1
        cmd += ["--no-cache"]  # Do not reuse provider-cached evidence.

    elif provider == "copilot-search":
        # Explicit provider synthesis; query proof is checked after the child.
        cmd += ["--query", query]
        cmd += ["--max-results", str(min(10, count))]
        cmd += ["--no-cache"]  # Do not reuse provider-cached evidence.

    elif provider == "perplexity":
        # Perplexity is not yet integrated as a skill script
        return None, "Perplexity provider not yet implemented as skill"

    else:
        return None, f"No command builder for provider: {provider}"

    desc = f"{provider}:{sub_type or 'default'}"
    return cmd, desc


# =============================================================================
# OUTPUT NORMALIZER
# =============================================================================

def _extract_domain(url):
    """Extract domain from URL."""
    try:
        if not url:
            return ""
        # Handle URLs without scheme (e.g. "x.com/...")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _normalize_result(result, provider, sub_type):
    """Normalize a single result to unified schema."""
    normalized = {
        "title": None,
        "url": None,
        "domain": None,
        "snippet": None,
        "pub_date": None,
        "pub_date_raw": None,
        "position": None,
        "relevance_score": None,
        "source_name": None,
        "thumbnail_url": None,
        "extra_snippets": None,
        "page_age": None,
        "author": None,
    }

    # Common fields
    normalized["title"] = result.get("title")
    normalized["url"] = result.get("url")
    normalized["domain"] = _extract_domain(result.get("url", ""))
    normalized["position"] = result.get("position")

    if provider == "brave":
        normalized["snippet"] = result.get("description")
        normalized["page_age"] = result.get("age")
        normalized["extra_snippets"] = result.get("extra_snippets")
        normalized["source_name"] = result.get("source")
        # Brave news has page_age as a proper date string
        if sub_type == "news":
            normalized["pub_date_raw"] = result.get("page_age") or result.get("age")
        else:
            normalized["pub_date_raw"] = result.get("age")
        # breaking flag for news
        if result.get("breaking"):
            normalized["source_name"] = (normalized["source_name"] or "") + " [BREAKING]"

    elif provider == "tavily":
        normalized["snippet"] = result.get("content")
        normalized["relevance_score"] = result.get("score")
        normalized["pub_date"] = result.get("published_date")
        normalized["pub_date_raw"] = result.get("published_date")

    elif provider == "serper":
        normalized["snippet"] = result.get("description")
        normalized["pub_date_raw"] = result.get("date")
        normalized["source_name"] = result.get("source")
        # Try to parse date if it looks like a date
        date_str = result.get("date")
        if date_str and not any(w in str(date_str).lower() for w in ["ago", "hour", "day", "week", "month"]):
            normalized["pub_date"] = date_str

    elif provider == "exa":
        normalized["snippet"] = result.get("content_excerpt") or result.get("text") or result.get("summary")
        normalized["relevance_score"] = result.get("score")
        normalized["pub_date"] = result.get("published_date")
        normalized["pub_date_raw"] = result.get("published_date")
        normalized["author"] = result.get("author")

    elif provider == "codex-search":
        normalized["snippet"] = result.get("snippet")

    elif provider == "searxng":
        normalized["snippet"] = result.get("description")
        normalized["relevance_score"] = result.get("score")

    return normalized


def _normalize_output(raw_output, provider, sub_type, mode, fallback_position, duration_ms):
    """Normalize provider output to unified schema.

    LLM-synthesis providers (copilot-search, anthropic-search) preserve
    provider text and evidence metadata and set synthesized=True.
    Raw providers leave those fields as None and synthesized=False.
    """
    unified = {
        "query": raw_output.get("query"),
        "provider": provider,
        "provider_mode": sub_type or "default",
        "mode": mode,
        "fallback_position": fallback_position,
        "synthesized": False,
        "text": None,
        "candidate_sources": None,
        "pages_read": None,
        "meta": {
            "duration_ms": duration_ms,
            "result_count": 0,
        },
        "results": [],
        "supplemental": {
            "people_also_ask": None,
            "related_searches": None,
            "knowledge_graph": None,
        },
        "error": None,
        "error_code": None,
    }

    # Copy error info
    if raw_output.get("error"):
        unified["error"] = raw_output["error"]
        unified["error_code"] = raw_output.get("error_code")
        return unified

    # Provider duration (more accurate than our subprocess timing)
    provider_meta = raw_output.get("meta", {})
    if provider_meta.get("duration_ms"):
        unified["meta"]["duration_ms"] = provider_meta["duration_ms"]
    # LLM providers report timing as top-level "elapsed_ms" instead of meta.duration_ms
    elif raw_output.get("elapsed_ms"):
        unified["meta"]["duration_ms"] = raw_output["elapsed_ms"]

    # Normalize results
    raw_results = raw_output.get("results", [])
    for r in raw_results:
        unified["results"].append(_normalize_result(r, provider, sub_type))
    unified["meta"]["result_count"] = len(unified["results"])

    # Supplemental data (Serper-specific, null for others)
    supplemental = raw_output.get("supplemental", {})
    if supplemental:
        unified["supplemental"]["people_also_ask"] = supplemental.get("people_also_ask")
        unified["supplemental"]["related_searches"] = supplemental.get("related_searches")
        unified["supplemental"]["knowledge_graph"] = supplemental.get("knowledge_graph")

    # Preserve reported synthesis evidence and actual model/transport metadata.
    if provider in LLM_PROVIDERS:
        unified["synthesized"] = True
        if "text" in raw_output:
            unified["text"] = raw_output.get("text")
        if "candidate_sources" in raw_output:
            unified["candidate_sources"] = raw_output.get("candidate_sources")
        if "pages_read" in raw_output:
            unified["pages_read"] = raw_output.get("pages_read")
        if "tool_calls_made" in raw_output:
            unified["meta"]["tool_calls_made"] = raw_output.get("tool_calls_made")
        if "search_calls_made" in raw_output:
            unified["meta"]["search_calls_made"] = raw_output.get("search_calls_made")
        if "transport" in raw_output:
            unified["meta"]["transport"] = raw_output.get("transport")
        if "model" in raw_output and raw_output.get("model") is not None:
            unified["meta"]["model"] = raw_output.get("model")
        if "cached" in raw_output:
            unified["meta"]["cached"] = raw_output.get("cached")

    return unified


# =============================================================================
# EXECUTOR
# =============================================================================

def _log(msg):
    """Log to stderr."""
    print(f"[SEARCH] {msg}", file=sys.stderr)


def _run_provider(cmd, provider, sub_type, timeout):
    """Execute a provider script and return (parsed_json, exit_code, duration_ms)."""
    env = os.environ.copy()
    if os.path.isdir(_KEYMANAGER_DIR):
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = _KEYMANAGER_DIR if not existing else _KEYMANAGER_DIR + os.pathsep + existing
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",        # Force UTF-8 (Windows default cp1252 mangles vendor JSON)
            errors="replace",        # Defensive: never crash on a stray non-UTF-8 byte
            timeout=timeout + 10,  # extra 10s buffer beyond provider's own timeout
            env=env,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        exit_code = result.returncode

        if result.stdout.strip():
            try:
                parsed = json.loads(result.stdout)
                return parsed, exit_code, duration_ms
            except json.JSONDecodeError:
                return {"error": f"Invalid JSON from {provider}: {result.stdout[:200]}",
                        "error_code": "parse_error"}, 1, duration_ms

        # No stdout — check stderr
        err_msg = result.stderr.strip()[:200] if result.stderr else "No output"
        return {"error": f"{provider} produced no output: {err_msg}",
                "error_code": "no_output"}, exit_code or 1, duration_ms

    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {"error": f"{provider} timed out after {timeout+10}s",
                "error_code": "timeout"}, 1, duration_ms
    except FileNotFoundError:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {"error": f"Script not found for {provider}",
                "error_code": "not_found"}, 2, duration_ms
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {"error": f"Failed to run {provider}: {str(e)}",
                "error_code": "internal_error"}, 1, duration_ms


def _is_error(parsed, exit_code, provider=None):
    """Return True if a provider response should trigger fallback to the next provider in the chain.

    Vendor providers: empty results[] is fine (no matches != error). Only
    non-zero exit_code or explicit `error` field triggers fallback.

    LLM providers: empty `text` AND empty `results` IS an error (synthesis
    failed silently — exit 0 but no useful content); non-empty `text` with
    empty `results` is success (no markdown citations is OK).
    """
    if exit_code != 0:
        return True
    if parsed.get("error"):
        return True
    if provider in LLM_PROVIDERS:
        # LLM providers: must have either non-empty text or non-empty results
        text_val = parsed.get("text") or ""
        has_text = isinstance(text_val, str) and text_val.strip() != ""
        has_results = bool(parsed.get("results"))
        if not has_text and not has_results:
            return True  # silent failure — try next provider in the chain
    return False


def execute_search(query, mode, count, freshness, domains, timeout, force_provider, explain, key_dir=None):
    """Execute search with fallback chain.

    Returns unified JSON output dict.
    """
    # Resolve mode
    if mode not in ROUTES:
        _log(f"Unknown mode '{mode}', falling back to 'web'")
        mode = "web"

    chain = ROUTES[mode]

    # Handle --force-provider
    if force_provider:
        chain = [force_provider]

    # Filter disabled providers
    active_chain = []
    skipped_errors = []
    for entry in chain:
        provider, _ = _parse_provider_entry(entry)
        if provider in DISABLED_PROVIDERS:
            _log(f"SKIP {entry} (provider '{provider}' is disabled)")
            reason = (COPILOT_DISABLED_REASON if provider == "copilot-search"
                      else f"Provider '{provider}' is disabled")
            skipped_errors.append({"provider": entry, "error": reason,
                                   "error_code": "disabled"})
            continue
        active_chain.append(entry)

    # --explain-routing: show chain without executing
    if explain:
        return {
            "mode": mode,
            "chain": active_chain,
            "disabled_providers": sorted(DISABLED_PROVIDERS),
            "skipped_entries": [e for e in chain if e not in active_chain],
            "force_provider": force_provider,
            "freshness": freshness,
            "domains": domains,
            "auto_freshness": "serper:news gets --tbs qdr:w" if mode in TIME_SENSITIVE_MODES else None,
            "provider_errors": skipped_errors,
            "note": "Dry run — no API calls made",
        }

    if not active_chain:
        return {
            "query": query, "provider": None, "provider_mode": None,
            "mode": mode, "fallback_position": -1,
            "synthesized": False,
            "text": None,
            "candidate_sources": None,
            "pages_read": None,
            "meta": {"duration_ms": 0, "result_count": 0},
            "results": [],
            "supplemental": {"people_also_ask": None, "related_searches": None, "knowledge_graph": None},
            "error": f"All providers disabled for mode '{mode}'. Chain: {chain}. Disabled: {DISABLED_PROVIDERS}",
            "error_code": "all_disabled",
            "provider_errors": skipped_errors,
        }


    # Parse domains list
    domain_list = []
    if domains:
        domain_list = [d.strip() for d in domains.split(",") if d.strip()]

    # Execute with fallback
    errors = skipped_errors
    for i, entry in enumerate(active_chain):
        provider, sub_type = _parse_provider_entry(entry)

        cmd, desc = _build_command(provider, sub_type, query, count, freshness,
                                    domain_list, timeout, mode, key_dir=key_dir)
        if cmd is None:
            _log(f"SKIP {entry} — {desc}")
            errors.append({"provider": entry, "error": desc})
            continue

        _log(f"TRY [{i+1}/{len(active_chain)}] {entry}")

        # Synthesis providers retain their existing longer watchdog class.
        effective_timeout = (
            LLM_PROVIDER_TIMEOUT_S
            if provider in LLM_PROVIDERS
            else timeout
        )

        parsed, exit_code, duration_ms = _run_provider(cmd, provider, sub_type, effective_timeout)

        # A successful Copilot child must prove query search, not just prose or
        # an open-page fetch. Preserve genuine provider errors unchanged.
        if provider == "copilot-search" and exit_code == 0 and not parsed.get("error"):
            text = parsed.get("text")
            search_calls = parsed.get("search_calls_made")
            cited_results = parsed.get("results")
            has_citation = False
            if (isinstance(text, str) and text.strip()
                    and type(search_calls) is int and search_calls > 0
                    and isinstance(cited_results, list)):
                for cited_result in cited_results:
                    url = cited_result.get("url") if isinstance(cited_result, dict) else None
                    if (not isinstance(url, str) or not url
                            or any(char.isspace() or ord(char) < 32 for char in url)):
                        continue
                    try:
                        citation = urlparse(url)
                        if (citation.scheme in ("http", "https") and citation.hostname
                                and citation.username is None and citation.password is None):
                            citation.port  # Reject malformed ports without rewriting citations.
                            has_citation = True
                            break
                    except ValueError:
                        continue
            if not has_citation:
                parsed["error"] = "Copilot did not complete a cited web search."
                parsed["error_code"] = "search_not_executed"

        if _is_error(parsed, exit_code, provider=provider):
            err_msg = parsed.get("error") or f"exit code {exit_code}"
            err_code = parsed.get("error_code") or "unknown"
            _log(f"FAIL {entry} — {err_code}: {err_msg[:100]}")
            failure = {"provider": entry, "error": err_msg, "error_code": err_code}
            for field in ("model", "transport", "http_status", "upstream_code",
                          "upstream_type", "upstream_param"):
                if field in parsed:
                    value = parsed[field]
                    valid = type(value) is int if field == "http_status" else isinstance(value, str)
                    failure[field] = value if valid else None
            errors.append(failure)
            continue

        # Success
        _log(f"OK {entry} duration={duration_ms}ms results={len(parsed.get('results', []))}")
        result = _normalize_output(parsed, provider, sub_type, mode, i, duration_ms)

        # Attach fallback trail if we had failures
        if errors:
            result["meta"]["fallback_errors"] = errors

        return result

    # All providers failed
    _log(f"FAIL ALL providers exhausted for mode '{mode}'")
    return {
        "query": query, "provider": None, "provider_mode": None,
        "mode": mode, "fallback_position": -1,
        "synthesized": False,
        "text": None,
        "candidate_sources": None,
        "pages_read": None,
        "meta": {"duration_ms": 0, "result_count": 0},
        "results": [],
        "supplemental": {"people_also_ask": None, "related_searches": None, "knowledge_graph": None},
        "error": f"All {len(active_chain)} providers failed for mode '{mode}'",
        "error_code": "all_failed",
        "provider_errors": errors,
    }


# =============================================================================
# CLI
# =============================================================================

def main():
    # Force UTF-8 on stdout/stderr so non-ASCII (curly quotes, emoji, etc.) round-trips
    # safely on Windows (default cp1252) — fixes save#6 F18 router crash.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass  # Older Python or non-text stream — harmless to skip

    # Build mode listing dynamically from ROUTES so chain descriptions never
    # drift from the actual routing table (M3 fix). Prose blurbs stay
    # hardcoded — they describe intent, not data.
    _MODE_BLURBS = {
        "news":      "General news",
        "finance":   "Financial markets, Fed, macro",
        "tech-news": "Tech company news",
        "company":   "Company research",
        "filings":   "SEC filings, 10-K, earnings",
        "people":    "LinkedIn profiles, people search",
        "research":  "Multi-source vendor research",
        "local":     "Local events, places, hiking",
        "currency":  "Currency rates, forex, central banks",
        "social":    "X/Twitter posts, social signals",
        "similar":   "Find similar URLs",
        "deep":      "Deep vendor search",
        "synthesis": "Copilot cited search synthesis",
        "web":       "General web search — default",
    }
    _mode_lines = []
    for _m, _blurb in _MODE_BLURBS.items():
        _chain = _format_chain(ROUTES.get(_m, []))
        _mode_lines.append(f"  {_m:<11} {_blurb} ({_chain})")
    _modes_block = "\n".join(_mode_lines)

    parser = argparse.ArgumentParser(
        description="Unified Search Router — routes queries to the best provider per category",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Modes:
{_modes_block}

Examples:
  %(prog)s --query "S&P 500 today" --mode finance
  %(prog)s --query "NVIDIA 10-K" --mode filings
  %(prog)s --query "AI startup CEO" --mode people --count 5
  %(prog)s --query "test" --mode news --explain-routing
  %(prog)s --query "USD EUR" --force-provider brave:news
  %(prog)s --query "k8s vs swarm" --mode research --freshness month
  %(prog)s --query "Fed rate" --mode web --domains federalreserve.gov,ecb.europa.eu
  %(prog)s --query "fixture" --mode synthesis --explain-routing

Domain filtering tip:
  Use --domains with 'web' mode. News modes have smaller indexes and return
  very few results when domain-filtered. Use 'web' mode or broaden the list.
""",
    )
    parser.add_argument("--query", "-q", required=True, help="Search query")
    parser.add_argument("--mode", "-m", default="web", choices=sorted(ROUTES.keys()),
                        help="Search mode — determines provider routing (default: web)")
    parser.add_argument("--count", "-n", type=int, default=5,
                        help="Number of results (1-20, default: 5)")
    parser.add_argument("--force-provider", "-f",
                        help="Force specific provider:type (e.g. brave:news, exa:people)")
    parser.add_argument("--freshness", choices=["hour", "day", "week", "month", "year"],
                        help="Time filter for results")
    parser.add_argument("--domains", help="Include domains (comma-separated, e.g. reuters.com,wsj.com)")
    parser.add_argument("--timeout", type=int, default=30,
                        help="Per-provider timeout in seconds (default: 30)")
    parser.add_argument("--key-dir", default=DEFAULT_KEY_DIR,
                        help=f"Directory containing provider key files (default: {DEFAULT_KEY_DIR})")
    parser.add_argument("--explain-routing", action="store_true",
                        help="Show routing decision without executing (dry run)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug output on stderr")
    parser.add_argument("--version", "-v", action="version", version=f"%(prog)s {VERSION}")

    args = parser.parse_args()

    # Validate
    count = max(1, min(20, args.count))

    if args.debug:
        _log(f"mode={args.mode} query={args.query[:50]}... count={count}")
        _log(f"chain={ROUTES.get(args.mode, ['?'])}")
        _log(f"disabled={sorted(DISABLED_PROVIDERS) or 'none'}")
        if args.force_provider:
            _log(f"force={args.force_provider}")

    result = execute_search(
        query=args.query,
        mode=args.mode,
        count=count,
        freshness=args.freshness,
        domains=args.domains,
        timeout=args.timeout,
        force_provider=args.force_provider,
        explain=args.explain_routing,
        key_dir=args.key_dir,
    )

    # Output JSON to stdout
    json.dump(result, sys.stdout, ensure_ascii=False)
    print()  # newline after JSON

    # Exit code: 0 if results, 1 if error
    if result.get("error"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
