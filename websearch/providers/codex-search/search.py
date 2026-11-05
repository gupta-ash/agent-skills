#!/usr/bin/env python3
"""Independent stdlib client for the observed Codex alpha/search query protocol."""

import argparse
import http.client
import json
import math
import os
from pathlib import Path
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


_ENDPOINT = "https://chatgpt.com/backend-api/codex/alpha/search"
_LOGIN = "Sign in with the normal Codex CLI login, then run the search again."
_MESSAGES = {
    "invalid_input": "Provide a nonblank query, max_results from 1 to 20, and positive finite timeout/recency days; domains must be a list of nonblank strings.",
    "auth_error": "Codex credentials are missing, invalid or expired. " + _LOGIN,
    "forbidden": "Codex search access was denied. Check the signed-in account's search entitlement.",
    "rate_limited": "Codex search is rate limited. Wait before making another search.",
    "http_error": "Codex search returned an HTTP error or redirect. Check service availability; authorization was not forwarded to a redirect.",
    "timeout": "Codex search timed out. Check connectivity or choose a longer timeout for a later call.",
    "network_error": "Codex search could not reach the service. Check network and proxy connectivity.",
    "parse_error": "Codex search returned invalid JSON or an unsupported result schema. Check provider protocol compatibility.",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _credentials():
    token = os.environ.get("CODEX_ACCESS_TOKEN", "")
    if token.strip():
        account = os.environ.get("CODEX_ACCOUNT_ID")
    else:
        data = json.loads((Path.home() / ".codex" / "auth.json").read_text(encoding="utf-8"))
        tokens = data.get("tokens") if isinstance(data, dict) else None
        if not isinstance(tokens, dict):
            raise ValueError("invalid credentials")
        token, account = tokens.get("access_token"), tokens.get("account_id")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("invalid credentials")
    if account is not None and not isinstance(account, str):
        raise ValueError("invalid credentials")
    # Invalid header values must fail without leaking urllib's exception text.
    for value in (token, account):
        if value and ("\r" in value or "\n" in value):
            raise ValueError("invalid credentials")
    return token, account if account and account.strip() else None


def _results(payload, max_results):
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("invalid schema")
    rows = payload["results"]
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("url"), str):
            raise ValueError("invalid result")
        url = row["url"]
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme.lower() not in ("http", "https") or not parsed.hostname
                or any(c.isspace() or ord(c) < 32 for c in url)):
            raise ValueError("invalid URL")
        # Accessing port also rejects malformed/out-of-range authorities.
        parsed.port
        for field in ("title", "snippet", "domain", "type", "ref_id"):
            if row.get(field) is not None and not isinstance(row[field], str):
                raise ValueError("invalid result field")
    return [{"title": row.get("title"), "url": row["url"],
             "snippet": row.get("snippet"), "position": i}
            for i, row in enumerate(rows[:max_results], 1)]


def codex_search(query: str, max_results: int = 10,
                     recency_days: int | None = None,
                     domains: list[str] | None = None,
                     timeout: float = 30.0) -> dict:
    """Return raw ordered query results, or a sanitized error envelope; never retry."""
    started = time.monotonic()
    envelope = {"query": query if isinstance(query, str) else "", "results": [],
                "meta": {"duration_ms": 0}, "error": None, "error_code": None}

    def finish(code=None):
        envelope["meta"]["duration_ms"] = round((time.monotonic() - started) * 1000)
        if code:
            envelope["error_code"] = code
            envelope["error"] = _MESSAGES[code]
        return envelope

    if (not isinstance(query, str) or not query.strip()
            or type(max_results) is not int or not 1 <= max_results <= 20
            or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0
            or (recency_days is not None and (type(recency_days) is not int or recency_days <= 0))
            or (domains is not None and (not isinstance(domains, list)
                or any(not isinstance(d, str) or not d.strip() for d in domains)))):
        return finish("invalid_input")
    try:
        token, account = _credentials()
    except (OSError, ValueError, TypeError, UnicodeError):
        return finish("auth_error")

    search_query = {"q": query}
    if recency_days is not None:
        search_query["recency"] = recency_days
    if domains is not None:
        search_query["domains"] = domains
    body = {"id": str(uuid.uuid4()), "model": "gpt-4o",
            "commands": {"search_query": [search_query], "response_length": "short"}}
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json",
               "User-Agent": "codex-cli/0.147.0-alpha.6.5"}
    if account:
        headers["ChatGPT-Account-ID"] = account
    try:
        request = urllib.request.Request(_ENDPOINT, data=json.dumps(body).encode("utf-8"),
                                         headers=headers, method="POST")
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        code = {401: "auth_error", 403: "forbidden", 429: "rate_limited"}.get(exc.code, "http_error")
        exc.close()
        return finish(code)
    except (TimeoutError, socket.timeout):
        return finish("timeout")
    except urllib.error.URLError as exc:
        return finish("timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "network_error")
    except (OSError, ValueError, UnicodeError, http.client.HTTPException):
        return finish("network_error")
    try:
        envelope["results"] = _results(json.loads(payload), max_results)
    except (ValueError, TypeError, UnicodeError):
        return finish("parse_error")
    return finish()


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default can echo user input; keep all failure output JSON.
        print(json.dumps({"query": "", "results": [], "meta": {"duration_ms": 0},
                          "error": _MESSAGES["invalid_input"], "error_code": "invalid_input"}))
        raise SystemExit(2)


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--recency-days", type=int)
    parser.add_argument("--domains", help="Comma-separated domain constraints")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    result = codex_search(args.query, args.max_results, args.recency_days,
                              [d.strip() for d in args.domains.split(",")] if args.domains is not None else None,
                              args.timeout)
    print(json.dumps(result, ensure_ascii=True))
    return 2 if result["error_code"] == "invalid_input" else (1 if result["error"] else 0)


if __name__ == "__main__":
    sys.exit(main())
