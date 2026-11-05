#!/usr/bin/env python
"""Stdlib ClickUp REST API v2 client.

Only read requests may retry. A mutation has one submission: a lost response is
an unknown effect for its caller to reconcile, never permission to submit again.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.0.0"
API_BASE = "https://api.clickup.com/api/v2"
MAX_RETRIES = 3
RATE_LIMIT_THRESHOLD = 10  # start backing off when remaining < this


class ClickUpError(Exception):
    """ClickUp API error with structured info."""

    def __init__(self, code: str, message: str, http_status: int = 0):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class ClickUpClient:
    """Stdlib-only ClickUp REST API v2 client."""

    def __init__(
        self,
        api_token: Optional[str] = None,
        team_id: Optional[str] = None,
        timeout: int = 30,
        *,
        api_base: str = API_BASE,
    ):
        self.api_token = api_token or os.environ.get("CLICKUP_API_TOKEN", "")
        self.team_id = team_id or os.environ.get("CLICKUP_TEAM_ID", "")
        self.timeout = timeout
        self.api_base = api_base.rstrip("/")
        self._rate_remaining: Optional[int] = None
        self._rate_reset: Optional[float] = None

        if not self.api_token:
            raise ClickUpError("config_error", "CLICKUP_API_TOKEN not set")

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Tuple[int, Any]:
        """Make one mutation request; retain bounded read-only retry behavior."""
        url = f"{self.api_base}{path}"
        if params:
            # Filter out None values
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                url += "?" + urllib.parse.urlencode(filtered, doseq=True)

        headers = {
            "Authorization": self.api_token,
            "Content-Type": "application/json",
        }

        data = json.dumps(body).encode("utf-8") if body else None

        last_error = None
        read_only = method.upper() in {"GET", "HEAD"}
        attempts = MAX_RETRIES if read_only else 1
        for attempt in range(attempts):
            # Rate limit backoff
            if self._rate_remaining is not None and self._rate_remaining < RATE_LIMIT_THRESHOLD:
                if self._rate_reset:
                    wait = max(0, self._rate_reset - time.time()) + 0.5
                    if wait > 0 and wait < 60:
                        time.sleep(wait)

            try:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    # Update rate limit tracking
                    remaining = resp.headers.get("X-RateLimit-Remaining")
                    reset = resp.headers.get("X-RateLimit-Reset")
                    if remaining is not None:
                        self._rate_remaining = int(remaining)
                    if reset is not None:
                        self._rate_reset = float(reset) / 1000.0  # ms to seconds

                    resp_body = resp.read().decode("utf-8")
                    if resp_body:
                        return resp.status, json.loads(resp_body)
                    return resp.status, {}

            except urllib.error.HTTPError as e:
                status = e.code
                try:
                    err_body = json.loads(e.read().decode("utf-8"))
                    err_msg = err_body.get("err", str(err_body))
                except Exception:
                    err_msg = str(e)

                if status == 401:
                    raise ClickUpError("auth_error", f"Authentication failed: {err_msg}", status)
                elif status == 404:
                    raise ClickUpError("not_found", f"Resource not found: {err_msg}", status)
                elif status == 429:
                    if not read_only:
                        raise ClickUpError("rate_limited", f"Rate limited: {err_msg}", status)
                    # Rate limited — retry after backoff
                    retry_after = e.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else (2 ** attempt) + 1
                    if wait > 60:
                        raise ClickUpError("rate_limited", f"Rate limited, retry after {wait}s", status)
                    last_error = ClickUpError("rate_limited", f"Rate limited (attempt {attempt + 1})", status)
                    time.sleep(wait)
                    continue
                elif 400 <= status < 500:
                    raise ClickUpError("client_error", f"Client error {status}: {err_msg}", status)
                elif 500 <= status < 600:
                    if not read_only:
                        raise ClickUpError("server_error", f"Server error {status}: {err_msg}", status)
                    last_error = ClickUpError("server_error", f"Server error {status}: {err_msg}", status)
                    time.sleep(2 ** attempt)
                    continue
                else:
                    raise ClickUpError("client_error", f"HTTP {status}: {err_msg}", status)

            except (urllib.error.URLError, OSError, TimeoutError) as e:
                if not read_only:
                    raise ClickUpError("network_error", f"Mutation response unavailable: {e}") from e
                last_error = ClickUpError("network_error", f"Network error: {e}")
                time.sleep(2 ** attempt)
                continue

        if last_error:
            raise last_error
        raise ClickUpError("network_error", "Request failed after all retries")

    def get(self, path: str, params: Optional[Dict] = None) -> Tuple[int, Any]:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: Optional[Dict] = None) -> Tuple[int, Any]:
        return self._request("POST", path, body=body)

    def put(self, path: str, body: Optional[Dict] = None) -> Tuple[int, Any]:
        return self._request("PUT", path, body=body)


def make_envelope(
    action: str,
    results: Any = None,
    error: Optional[Dict] = None,
    http_status: int = 200,
    start_time: Optional[float] = None,
) -> Dict:
    """Create standard output envelope."""
    duration_ms = int((time.time() - start_time) * 1000) if start_time else 0
    return {
        "provider": "clickup",
        "action": action,
        "results": results if results is not None else [],
        "error": error,
        "http_status": http_status,
        "meta": {
            "duration_ms": duration_ms,
            "version": VERSION,
        },
    }


def error_envelope(action: str, err: ClickUpError, start_time: Optional[float] = None) -> Dict:
    """Create error output envelope."""
    return make_envelope(
        action=action,
        error={
            "code": err.code,
            "message": str(err),
            "http_status": err.http_status,
        },
        http_status=err.http_status or 0,
        start_time=start_time,
    )


def output_json(data: Dict) -> None:
    """Print JSON to stdout and exit."""
    print(json.dumps(data, indent=2, default=str))


def run_action(action: str, func):
    """Wrapper to run a skill action with error handling."""
    start = time.time()
    try:
        result = func(start)
        output_json(result)
        sys.exit(0)
    except ClickUpError as e:
        output_json(error_envelope(action, e, start))
        sys.exit(1)
    except Exception as e:
        output_json(error_envelope(action, ClickUpError("client_error", str(e)), start))
        sys.exit(1)
