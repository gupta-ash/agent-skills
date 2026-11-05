#!/usr/bin/env python
"""Disabled Copilot search/fetch provider with retained offline parsing helpers.

Every public API/CLI request returns the source-owned disabled error before
model/transport selection, cache access, authentication or inference. Existing
five-positional-argument calls and the keyword-only model pin remain callable,
but no flag, pin or environment setting enables the retained implementation.
Its historical transport and evidence algorithms await future diagnostics.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional


COPILOT_DISABLED_REASON = (
    "Copilot disabled: reliable search execution is not qualified (search_not_executed); "
    "diagnostics deferred."
)

PROXY_URL = "http://localhost:4143/responses"
DIRECT_URL = "https://api.enterprise.githubcopilot.com/responses"
TOKEN_REFRESH_URL = "https://api.github.com/copilot_internal/v2/token"
GH_TOKEN_PATH = Path.home() / ".local" / "share" / "copilot-proxy" / "github_token"
STATE_DIR = Path.home() / ".local" / "share" / "copilot-search"
TOKEN_CACHE_PATH = STATE_DIR / "cached_ide_token.json"
RESULT_CACHE_PATH = STATE_DIR / "cache.json"
CACHE_TTL_S = 60
TOKEN_REFRESH_LEAD_S = 300
DEFAULT_TIMEOUT_S = 60
EDITOR_VERSION = "vscode/1.117.0"
EDITOR_PLUGIN_VERSION = "copilot-chat/0.45.1"
USER_AGENT = "GitHubCopilotChat/0.45.1"
COPILOT_INTEGRATION_ID = "vscode-chat"
COPILOT_INTENT = "conversation-panel"
GITHUB_API_VERSION = "2025-04-01"
PREFERRED_MODEL = "gpt-5.5"
MAX_OUTPUT_TOKENS = 2048
_CACHE_VERSION = "copilot-search-r2-v1"
_MODEL_RE = re.compile(r"gpt-[56](?:[.-][a-z0-9]+)*\Z")
_SAFE_FIELD_RE = re.compile(r"[A-Za-z0-9_.:/\[\]-]{1,128}\Z")
_MD_LINK_RE = re.compile(r"\[([^\]]{1,200})\]\(([^)\s]+)\)")


class CopilotSearchError(Exception):
    """A classified failure containing only bounded, safe provenance."""

    def __init__(self, message: str, error_code: str = "transport_error", *,
                 http_status: int | None = None, upstream_code: str | None = None,
                 upstream_type: str | None = None, upstream_param: str | None = None,
                 definite_connect: bool = False):
        super().__init__(message)
        self.error_code = error_code
        self.http_status = http_status if type(http_status) is int else None
        self.upstream_code = _safe_field(upstream_code)
        self.upstream_type = _safe_field(upstream_type)
        self.upstream_param = _safe_field(upstream_param)
        self.definite_connect = definite_connect


def _safe_field(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_FIELD_RE.fullmatch(value) else None


def _error_code(status: int | None, code: str | None, param: str | None,
                kind: str | None) -> str:
    if status is not None and 300 <= status < 400:
        return "redirect_error"
    if status in (401, 403):
        return "auth_error"
    if status == 429:
        return "rate_limited"
    if status in (408, 504):
        return "timeout"
    label = f"{code or ''} {kind or ''}".lower()
    if "model" in label or param == "model":
        return "model_unavailable"
    if ("tool" in label or "web_search" in label
            or param in ("tools", "tool_choice")
            or (param or "").startswith(("tools[", "tools."))):
        return "unsupported_search"
    if status in (404, 405) or "integrat" in label or "capability" in label:
        return "capability_unavailable"
    return "upstream_error"


def _upstream_error(payload: object, stage: str, status: int | None = None) -> CopilotSearchError:
    detail = payload.get("error") if isinstance(payload, dict) else None
    detail = detail if isinstance(detail, dict) else {}
    code = _safe_field(detail.get("code"))
    kind = _safe_field(detail.get("type"))
    param = _safe_field(detail.get("param"))
    classification = _error_code(status, code, param, kind)
    suffix = f" (HTTP {status})" if status is not None else ""
    return CopilotSearchError(
        f"Copilot {stage} request rejected{suffix}.", classification,
        http_status=status, upstream_code=code, upstream_type=kind,
        upstream_param=param,
    )


def _http_error(error: urllib.error.HTTPError, stage: str) -> CopilotSearchError:
    # Only selected machine fields leave this boundary, never messages/bodies.
    try:
        payload = json.loads(error.read(65536).decode("utf-8"))
    except (ValueError, UnicodeError, OSError, AttributeError, TypeError):
        payload = None
    finally:
        error.close()
    return _upstream_error(payload, stage, error.code)


def _transport_error(error: BaseException, stage: str) -> CopilotSearchError:
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    timed_out = isinstance(reason, (TimeoutError, socket.timeout))
    # These failures prove no HTTP request could have been accepted. Resets,
    # TLS/HTTP failures, string-only URLErrors and timeouts do not prove that.
    definite = isinstance(reason, (ConnectionRefusedError, socket.gaierror))
    if isinstance(reason, OSError) and not timed_out:
        definite = definite or reason.errno in (
            errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH,
        ) or getattr(reason, "winerror", None) in (10051, 10061, 10065, 11001)
    return CopilotSearchError(
        f"Copilot {stage} {'timed out; outcome is unknown' if timed_out else 'transport failed'}.",
        "timeout" if timed_out else "transport_error", definite_connect=definite,
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Raising here also rejects nonstandard redirects without forwarding auth.
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)


def _read_json(req: urllib.request.Request, timeout: int = DEFAULT_TIMEOUT_S) -> object:
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(req, timeout=timeout) as response:
        if 300 <= response.status < 400:
            raise CopilotSearchError("Copilot redirect refused.", "redirect_error",
                                     http_status=response.status)
        raw = response.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise CopilotSearchError("Copilot returned malformed JSON.", "parse_error") from None


def _get(url: str, headers: dict) -> object:
    return _read_json(urllib.request.Request(url, headers=headers, method="GET"))


def _post(url: str, body: dict, headers: dict) -> object:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST",
    )
    return _read_json(request)


def _client_headers(token: str | None = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "copilot-integration-id": COPILOT_INTEGRATION_ID,
        "editor-version": EDITOR_VERSION,
        "editor-plugin-version": EDITOR_PLUGIN_VERSION,
        "openai-intent": COPILOT_INTENT,
        "User-Agent": USER_AGENT,
        "x-github-api-version": GITHUB_API_VERSION,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _models_url(endpoint: str) -> str:
    try:
        parts = urllib.parse.urlsplit(endpoint)
        parts.port  # Validate port syntax even when a default HTTP(S) port is used.
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment
                or parts.path not in ("/responses", "/v1/responses")):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise CopilotSearchError("Invalid Copilot Responses endpoint configuration.",
                                 "invalid_input") from None
    return urllib.parse.urlunsplit(parts._replace(path=parts.path[:-9] + "models"))


class IDETokenManager:
    """Retain the private GitHub-token/IDE-token refresh mechanism, no OMP store."""

    def get_token(self) -> str:
        cached = self._read_cache()
        if cached and cached["exp"] - TOKEN_REFRESH_LEAD_S > time.time():
            return cached["token"]
        return self._refresh()

    def _read_cache(self) -> Optional[dict]:
        try:
            with open(TOKEN_CACHE_PATH, encoding="utf-8") as handle:
                cached = json.load(handle)
            if (isinstance(cached, dict) and isinstance(cached.get("token"), str)
                    and cached["token"] and type(cached.get("exp")) in (int, float)):
                return cached
        except (OSError, ValueError):
            pass
        return None

    def _refresh(self) -> str:
        try:
            gh_token = GH_TOKEN_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            raise CopilotSearchError("Private GitHub OAuth token is unavailable.", "auth_error") from None
        if not gh_token:
            raise CopilotSearchError("Private GitHub OAuth token is empty.", "auth_error")
        request = urllib.request.Request(TOKEN_REFRESH_URL, headers={
            "Authorization": f"token {gh_token}",
            "Editor-Version": EDITOR_VERSION,
            "Editor-Plugin-Version": EDITOR_PLUGIN_VERSION,
            "User-Agent": USER_AGENT,
        })
        try:
            data = _read_json(request, timeout=10)
        except urllib.error.HTTPError as error:
            raise _http_error(error, "token refresh") from None
        except (urllib.error.URLError, OSError) as error:
            # Auth acquisition is not permission for a new transport transition.
            raise _transport_error(error, "token refresh") from None
        if not isinstance(data, dict) or not isinstance(data.get("token"), str) or not data["token"]:
            raise CopilotSearchError("IDE token refresh returned invalid credentials.", "auth_error")
        token = data["token"]
        exp = int(time.time()) + 1500
        for part in token.split(";"):
            if part.startswith("exp="):
                try:
                    exp = int(part.split("=", 1)[1])
                except ValueError:
                    raise CopilotSearchError("IDE token expiry is invalid.", "auth_error") from None
                break
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            TOKEN_CACHE_PATH.write_text(json.dumps({"token": token, "exp": exp}), encoding="utf-8")
        except OSError:
            raise CopilotSearchError("Private IDE token cache is not writable.", "auth_error") from None
        return token


def _select_model(catalog: object, requested: str | None) -> str:
    """Select exactly one eligible GPT-5/6 Responses model, never sweep models."""
    if requested is not None and (not isinstance(requested, str) or not requested.strip()):
        raise CopilotSearchError("An explicit model pin must be nonempty.", "invalid_input")
    rows = catalog.get("data") if isinstance(catalog, dict) else catalog
    if not isinstance(rows, list):
        raise CopilotSearchError("Copilot model catalog is malformed.", "capability_unavailable")
    seen: dict[str, dict] = {}
    eligible: set[str] = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not row["id"] or row["id"] != row["id"].strip()):
            raise CopilotSearchError("Copilot model catalog contains an invalid ID.", "capability_unavailable")
        model_id = row["id"]
        if model_id in seen:
            if row != seen[model_id]:
                raise CopilotSearchError("Copilot model catalog contains conflicting IDs.",
                                         "capability_unavailable")
            continue
        seen[model_id] = row
        caps = row.get("capabilities")
        endpoints = row.get("supported_endpoints")
        if not isinstance(caps, dict) or not isinstance(endpoints, list):
            continue
        supports = caps.get("supports")
        policy = row.get("policy")
        disabled = policy is False or policy in ("disabled", "blocked")
        if isinstance(policy, dict):
            disabled = (policy.get("state") in ("disabled", "blocked")
                        or policy.get("enabled") is False)
        if (_MODEL_RE.fullmatch(model_id) and caps.get("type") == "chat"
                and "/responses" in endpoints and isinstance(supports, dict)
                and supports.get("tool_calls") is True and not disabled):
            eligible.add(model_id)
    if requested is not None:
        if requested not in eligible:
            raise CopilotSearchError("Pinned Copilot model is not eligible.", "model_unavailable")
        return requested
    if not eligible:
        raise CopilotSearchError("No eligible Copilot hosted-search candidate.", "capability_unavailable")
    return PREFERRED_MODEL if PREFERRED_MODEL in eligible else min(eligible)


class SearchCache:
    """Versioned 60-second private cache; qualification always precedes lookup."""

    @staticmethod
    def _key(query: str, context_size: str, max_results: int, *, endpoint: str, model: str) -> str:
        identity = (COPILOT_INTEGRATION_ID, EDITOR_VERSION, EDITOR_PLUGIN_VERSION,
                    COPILOT_INTENT, USER_AGENT, GITHUB_API_VERSION)
        return json.dumps((_CACHE_VERSION, endpoint, identity, model, query,
                           context_size, max_results), separators=(",", ":"))

    def _read(self) -> dict:
        try:
            with open(RESULT_CACHE_PATH, encoding="utf-8") as handle:
                store = json.load(handle)
            return store if isinstance(store, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, query: str, context_size: str, max_results: int, *, endpoint: str,
            model: str) -> Optional[dict]:
        entry = self._read().get(self._key(query, context_size, max_results,
                                         endpoint=endpoint, model=model))
        if (not isinstance(entry, dict) or type(entry.get("expires_at")) not in (int, float)
                or entry["expires_at"] <= time.time()):
            return None
        value = entry.get("value")
        return value if isinstance(value, dict) and value.get("error") is None else None

    def put(self, query: str, context_size: str, max_results: int, value: dict, *,
            endpoint: str, model: str) -> None:
        store = self._read()
        # Do not prune unrelated or old-format state while adding the new key.
        store[self._key(query, context_size, max_results, endpoint=endpoint, model=model)] = {
            "expires_at": time.time() + CACHE_TTL_S, "value": value,
        }
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            RESULT_CACHE_PATH.write_text(json.dumps(store), encoding="utf-8")
        except OSError:
            pass  # An optional cache write does not change completed evidence.


def _build_body(query: str, context_size: str, model: str) -> dict:
    return {
        "model": model,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
        "store": False,
        "tools": [{"type": "web_search", "search_context_size": context_size}],
        "tool_choice": "required",
        "input": [{"role": "user", "content": query}],
        "include": ["web_search_call.action.sources"],
    }


def _usable_url(value: object) -> str | None:
    if not isinstance(value, str) or not value or any(char.isspace() or ord(char) < 32 for char in value):
        return None
    try:
        parts = urllib.parse.urlsplit(value)
        if (parts.scheme in ("http", "https") and parts.hostname
                and parts.username is None and parts.password is None):
            parts.port  # Reject malformed port syntax without rewriting the URL.
            return value
    except ValueError:
        pass
    return None


def _parse_response(resp: dict, max_results: int) -> dict:
    """Separate citations, candidate URLs and completed open-page evidence.

    This parser deliberately does not impose the query router's search-only
    proof requirement: a valid FETCHED_URL/open_page response needs no Markdown
    citations. Failed/in-progress tool calls contribute no counts or evidence.
    """
    if not isinstance(resp, dict):
        raise CopilotSearchError("Copilot response is not an object.", "parse_error")
    if resp.get("error"):
        raise _upstream_error(resp, "inference")
    if resp.get("status") not in (None, "completed"):
        raise CopilotSearchError("Copilot response did not complete.", "parse_error")
    output = resp.get("output")
    if not isinstance(output, list):
        raise CopilotSearchError("Copilot response output is malformed.", "parse_error")
    text_parts: list[str] = []
    structured: list[dict] = []
    candidate_sources: list[str] = []
    pages_read: list[str] = []
    tool_calls_made = 0
    search_calls_made = 0
    for block in output:
        if not isinstance(block, dict):
            raise CopilotSearchError("Copilot output item is malformed.", "parse_error")
        if block.get("type") == "web_search_call":
            if block.get("status") != "completed" or block.get("error"):
                continue
            action = block.get("action")
            if not isinstance(action, dict) or not isinstance(action.get("type"), str):
                raise CopilotSearchError("Copilot tool action is malformed.", "parse_error")
            if action["type"] == "search":
                tool_calls_made += 1
                search_calls_made += 1
                sources = action.get("sources", [])
                if not isinstance(sources, list):
                    raise CopilotSearchError("Copilot search sources are malformed.", "parse_error")
                for source in sources:
                    url = _usable_url(source.get("url")) if isinstance(source, dict) else None
                    if url and url not in candidate_sources:
                        candidate_sources.append(url)
            elif action["type"] == "open_page":
                tool_calls_made += 1
                url = _usable_url(action.get("url"))
                if url and url not in pages_read:
                    pages_read.append(url)
        elif block.get("type") == "message":
            if block.get("status") not in (None, "completed") or block.get("error"):
                continue
            content = block.get("content")
            if not isinstance(content, list):
                raise CopilotSearchError("Copilot message content is malformed.", "parse_error")
            for part in content:
                if not isinstance(part, dict):
                    raise CopilotSearchError("Copilot message part is malformed.", "parse_error")
                if part.get("type") != "output_text":
                    continue
                if not isinstance(part.get("text"), str):
                    raise CopilotSearchError("Copilot output text is malformed.", "parse_error")
                text_parts.append(part["text"])
                annotations = part.get("annotations", [])
                if not isinstance(annotations, list):
                    raise CopilotSearchError("Copilot citations are malformed.", "parse_error")
                for annotation in annotations:
                    if not isinstance(annotation, dict):
                        raise CopilotSearchError("Copilot citation is malformed.", "parse_error")
                    if annotation.get("type") != "url_citation":
                        continue
                    citation = annotation.get("url_citation", annotation)
                    if not isinstance(citation, dict):
                        raise CopilotSearchError("Copilot URL citation is malformed.", "parse_error")
                    url = _usable_url(citation.get("url"))
                    if url:
                        title = citation.get("title")
                        structured.append({"title": title if isinstance(title, str) and title else url,
                                           "url": url, "snippet": None})
    text = "".join(text_parts)
    evidence = set(candidate_sources) | set(pages_read)
    results: list[dict] = []
    seen: set[str] = set()
    # Structured attribution wins the title/order for each URL. Markdown is only
    # attribution when grounded in an actual completed tool's returned evidence.
    for citation in structured:
        if citation["url"] not in seen:
            results.append(citation)
            seen.add(citation["url"])
    for match in _MD_LINK_RE.finditer(text):
        url = _usable_url(match.group(2))
        if url and url in evidence and url not in seen:
            results.append({"title": match.group(1).strip(), "url": url, "snippet": None})
            seen.add(url)
    if max_results > 0:
        results = results[:max_results]
    return {"results": results, "candidate_sources": candidate_sources,
            "pages_read": pages_read, "text": text, "tool_calls_made": tool_calls_made,
            "search_calls_made": search_calls_made}


def _to_markdown(parsed: dict, query: str, context_size: str, elapsed_ms: int) -> str:
    lines = [f'# Search results: "{query}"', ""]
    if parsed.get("error"):
        lines.extend([f"**Error** ({parsed['error_code']}): {parsed['error']}", ""])
    if parsed.get("text"):
        lines.extend([parsed["text"].rstrip(), ""])
    results = parsed.get("results") or []
    if results:
        lines.extend(["---", f"**Sources** (n = {len(results)})", ""])
        for index, result in enumerate(results, 1):
            suffix = f" — {result['snippet']}" if result.get("snippet") else ""
            lines.append(f"{index}. [{result['title']}]({result['url']}){suffix}")
        lines.append("")
    if parsed.get("pages_read"):
        lines.append(f"_Pages deep-read: {len(parsed['pages_read'])}_")
    if parsed.get("candidate_sources"):
        lines.append(f"_Candidate sources considered: {len(parsed['candidate_sources'])}_")
    lines.append(
        f"_Completed {parsed.get('search_calls_made', 0)} searches / "
        f"{parsed.get('tool_calls_made', 0)} search+open calls in {elapsed_ms}ms via "
        f"Copilot Enterprise / {parsed.get('model') or 'unselected'} / "
        f"{parsed.get('transport') or 'unresolved'} / web_search ({context_size})._"
    )
    return "\n".join(lines)


def _shape_output(value: dict, query: str, context_size: str, format: str) -> dict:
    if format == "json":
        return value
    return {**value, "markdown": _to_markdown(value, query, context_size, value.get("elapsed_ms", 0))}


def copilot_search(
    query: str,
    context_size: str = "medium",
    max_results: int = 10,
    format: str = "json",
    cache: bool = True,
    *,
    model: str | None = None,
) -> dict:
    """Refuse search and fetch before selecting credentials, models or transport.

    The implementation below remains inactive for future authorized diagnostics;
    no request argument or environment setting enables it.
    """
    return _shape_output({
        "query": query, "context_size": context_size, "results": [],
        "candidate_sources": [], "pages_read": [], "text": "",
        "tool_calls_made": 0, "search_calls_made": 0,
        "elapsed_ms": 0, "cached": False, "model": None, "transport": None,
        "auth_retries": 0, "transport_fallbacks": 0,
        "error": COPILOT_DISABLED_REASON, "error_code": "disabled",
        "http_status": None, "upstream_code": None,
        "upstream_type": None, "upstream_param": None,
    }, query, context_size, format)

    # UNREACHABLE / UNQUALIFIED: retained at the user's request for future
    # approved diagnostics only. This body, including environment resolution
    # moved behind the refusal, is unexercised after the current edits.
    # No current argument, environment setting, model pin or CLI route enables it.
    if context_size not in ("low", "medium", "high"):
        raise ValueError(f"context_size must be low|medium|high, got {context_size!r}")
    if format not in ("json", "markdown", "both"):
        raise ValueError(f"format must be json|markdown|both, got {format!r}")
    if max_results < 0:
        raise ValueError(f"max_results must be >= 0, got {max_results}")
    started = time.monotonic()
    selected: str | None = None
    requested: str | None = None
    transport = "direct" if os.environ.get("COPILOT_SEARCH_BYPASS_PROXY") == "1" else "proxy"
    auth_retries = 0
    transport_fallbacks = 0
    cache_obj = SearchCache() if cache else None

    def envelope() -> dict:
        return {"query": query, "context_size": context_size, "results": [],
                "candidate_sources": [], "pages_read": [], "text": "",
                "tool_calls_made": 0, "search_calls_made": 0,
                "elapsed_ms": int((time.monotonic() - started) * 1000), "cached": False,
                "model": selected or requested, "transport": transport,
                "auth_retries": auth_retries, "transport_fallbacks": transport_fallbacks,
                "error": None, "error_code": None, "http_status": None,
                "upstream_code": None, "upstream_type": None, "upstream_param": None}

    try:
        if model is not None:
            if not isinstance(model, str) or not model.strip():
                raise CopilotSearchError("An explicit model pin must be nonempty.", "invalid_input")
            requested = model
        else:
            env_model = os.environ.get("COPILOT_SEARCH_MODEL", "")
            requested = env_model if env_model.strip() else None
        while True:
            endpoint = (DIRECT_URL if transport == "direct" else
                        os.environ.get("COPILOT_PROXY_URL", PROXY_URL))
            models_url = _models_url(endpoint)
            token_manager = IDETokenManager() if transport == "direct" else None
            headers = _client_headers(token_manager.get_token() if token_manager else None)

            def request(stage: str, url: str, body: dict | None = None) -> object:
                nonlocal auth_retries
                while True:
                    try:
                        response = _get(url, headers) if body is None else _post(url, body, headers)
                        if isinstance(response, dict) and response.get("error"):
                            raise _upstream_error(response, stage)
                        return response
                    except urllib.error.HTTPError as error:
                        if transport == "direct" and error.code == 401 and auth_retries == 0:
                            error.close()
                            auth_retries += 1
                            try:
                                TOKEN_CACHE_PATH.unlink(missing_ok=True)
                            except OSError:
                                raise CopilotSearchError("Private IDE token cache cannot be refreshed.",
                                                         "auth_error") from None
                            headers["Authorization"] = f"Bearer {token_manager.get_token()}"
                            continue
                        raise _http_error(error, stage) from None
                    except (urllib.error.URLError, OSError) as error:
                        raise _transport_error(error, stage) from None

            try:
                catalog = request("catalog", models_url)
                try:
                    selected = _select_model(catalog, selected if selected is not None else requested)
                except CopilotSearchError as error:
                    if requested is None and selected is not None and error.error_code == "model_unavailable":
                        raise CopilotSearchError(
                            "Automatically selected Copilot model is unavailable on direct requalification.",
                            "capability_unavailable",
                        ) from None
                    raise
                if cache_obj:
                    cached_value = cache_obj.get(query, context_size, max_results,
                                                 endpoint=endpoint, model=selected)
                    if cached_value is not None:
                        value = {**cached_value, "cached": True, "model": selected,
                                 "transport": transport, "auth_retries": auth_retries,
                                 "transport_fallbacks": transport_fallbacks,
                                 "elapsed_ms": int((time.monotonic() - started) * 1000)}
                        return _shape_output(value, query, context_size, format)
                raw = request("inference", endpoint, _build_body(query, context_size, selected))
                parsed = _parse_response(raw, max_results)
                if isinstance(raw, dict) and raw.get("model") not in (None, selected):
                    raise CopilotSearchError("Copilot response model does not match the selected model.",
                                             "parse_error")
                value = {**envelope(), **parsed}
                if cache_obj:
                    cache_obj.put(query, context_size, max_results, value,
                                  endpoint=endpoint, model=selected)
                return _shape_output(value, query, context_size, format)
            except CopilotSearchError as error:
                if transport == "proxy" and transport_fallbacks == 0 and error.definite_connect:
                    transport_fallbacks += 1
                    transport = "direct"
                    continue
                raise
    except CopilotSearchError as error:
        value = {**envelope(), "error": str(error), "error_code": error.error_code,
                 "http_status": error.http_status, "upstream_code": error.upstream_code,
                 "upstream_type": error.upstream_type, "upstream_param": error.upstream_param}
    except Exception:
        # No repr, traceback, raw response, credential path or unrestricted body.
        value = {**envelope(), "error": "Copilot provider could not complete the request.",
                 "error_code": "internal_error"}
    return _shape_output(value, query, context_size, format)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(
        description="Copilot search/fetch is FAILED / DISABLED. " + COPILOT_DISABLED_REASON,
        epilog=("Flags are retained for explicit refusal only. Diagnostics are future-only; "
                "there is no CLI or environment bypass."),
    )
    parser.add_argument("--query", required=True, help="Search query or fetch prompt to refuse as disabled.")
    parser.add_argument("--format", choices=("json", "markdown", "both"), default="json",
                        help="Format of the disabled response.")
    parser.add_argument("--context-size", choices=("low", "medium", "high"), default="medium",
                        help="Retained context value; does not enable execution.")
    parser.add_argument("--max-results", type=int, default=10,
                        help="Retained result limit; disabled responses contain no results.")
    parser.add_argument("--no-cache", action="store_true",
                        help="Retained flag; no cache is accessed while disabled.")
    parser.add_argument("--model", default=None,
                        help="Retained model pin; ignored while disabled, with no diagnostic bypass.")
    args = parser.parse_args()
    result = copilot_search(args.query, args.context_size, args.max_results, args.format,
                                not args.no_cache, model=args.model)
    if args.format == "markdown":
        sys.stdout.write(result.get("markdown", "") + "\n")
    else:
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
