---
name: tavily
description: Web search with topic routing (general/news/finance) and URL content extraction via Tavily REST API. Structured JSON output envelope. Python 3.8+, stdlib-only.
version: 1.0.0
metadata:
  openclaw:
    requires: { env: [TAVILY_API_KEY], bins: [python3] }
    primaryEnv: TAVILY_API_KEY
    os: [linux, macos]
---

# tavily

> **Bundled inside the Search Router** at `~/.agents/skills/websearch/`. This reference is not independently discoverable. Invoke `python ~/.agents/skills/websearch/scripts/search.py --query "<query>" --mode web` (or a named specialist mode); `ROUTES` owns provider selection.

Web search with topic routing and URL content extraction via `https://api.tavily.com`. Topics route to different internal search agents: `general` (broad web), `news` (news sources with `published_date`), `finance` (financial data). Includes Extract endpoint for pulling content from known URLs. Python 3.8+, stdlib-only, no external dependencies. No browser required.

## When to Use

- Web research, documentation lookup, API references
- News search with `--topic news` (returns `published_date` per result)
- Financial data search with `--topic finance` (P1 Financial Markets Brief)
- Extracting full content from known URLs via `--type extract`
- Looking up facts or current information
- Any task requiring LLM-optimized search results
- Third general `web` candidate (`tavily:general`), after standalone Codex and Brave web, selected by the shared Search Router's `ROUTES` table

## Quick Start

```bash
# General web search
python3 scripts/tavily_search.py -q "AI agents" -n 10

# News search (returns published_date per result)
python3 scripts/tavily_search.py -q "AI news" --topic news --time-range day

# Finance search (for P1 Financial Markets Brief)
python3 scripts/tavily_search.py -q "stock market" --topic finance

# Extract content from known URLs
python3 scripts/tavily_search.py --type extract --urls "https://example.com"

# Extract multiple URLs
python3 scripts/tavily_search.py --type extract --urls "https://example.com,https://other.com"

# Advanced search with domain filtering
python3 scripts/tavily_search.py -q "ML papers" --include-domains "arxiv.org,github.com" -n 10

# Date-ranged news search
python3 scripts/tavily_search.py -q "AI regulation" --topic news --start-date 2026-03-01 --end-date 2026-03-08

# Deep search with full page content
python3 scripts/tavily_search.py -q "transformer architecture" --search-depth advanced --include-raw-content

# Debug diagnostics
python3 scripts/tavily_search.py -q "OpenClaw" --debug
```

## Authentication

Set `TAVILY_API_KEY` as an environment variable. The key is sent as a `Bearer` token in the `Authorization` header. No OAuth, no MCP, no browser flow.

```bash
export TAVILY_API_KEY="tvly-your-api-key-here"
```

Get a key at [tavily.com](https://tavily.com).

## CLI Flags

### Shared Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--type` / `-t` | choice | `search` | Operation type: `search` or `extract` |
| `--timeout` | int | `30` | HTTP timeout in seconds |
| `--raw` | bool | `false` | Include raw API response in output |
| `--debug` | bool | `false` | Include diagnostics in `meta.debug` |

### Search Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--query` / `-q` | str | required | Search query (max 400 chars) |
| `--topic` | choice | `general` | Topic routing: `general`, `news`, `finance` |
| `--max-results` / `-n` | int | `10` | Max results (0–20) |
| `--search-depth` | choice | `basic` | Depth: `ultra-fast`, `fast`, `basic`, `advanced` |
| `--time-range` | str | null | Time filter: `day`, `week`, `month`, `year` |
| `--start-date` | str | null | Results after this date (`YYYY-MM-DD`) |
| `--end-date` | str | null | Results before this date (`YYYY-MM-DD`) |
| `--include-domains` | str | null | Comma-separated domains to include (max 300) |
| `--exclude-domains` | str | null | Comma-separated domains to exclude (max 150) |
| `--country` | str | null | Boost results from country (only with `--topic general`) |
| `--include-images` | bool | `false` | Return image URLs alongside results |
| `--include-raw-content` | bool | `false` | Include full page markdown per result |

### Extract Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--urls` | str | required | Comma-separated URLs to extract (max 20) |
| `--extract-depth` | choice | `basic` | Extraction depth: `basic` or `advanced` |
| `--extract-format` | choice | `markdown` | Output format: `markdown` or `text` |

## Topic Routing

The `--topic` flag routes queries to different internal Tavily search agents:

| Topic | Use Case | Notes |
|-------|----------|-------|
| `general` | Broad web search | Default. Supports `--country` flag |
| `news` | News articles and headlines | Results include `published_date` field |
| `finance` | Financial data and market info | Optimized for stock/market queries |

**Project mapping:**
- P1 (Financial Markets Brief): `--topic finance`
- P2 (Tech Industry Brief): `--topic news`
- P3 (AI Industry Brief): `--topic news`
- P4 (AI Agents Brief): `--topic news`

## Search Depth

| Depth | Latency | Relevance | Content Type | Credits |
|-------|---------|-----------|--------------|---------|
| `ultra-fast` | Lowest | Lower | NLP summary | 1 |
| `fast` | Low | Good | Chunks | 1 |
| `basic` | Medium | High | NLP summary | 1 |
| `advanced` | Higher | Highest | Chunks | 2 |

**When to use each:**
- `ultra-fast` — Real-time chat, autocomplete
- `fast` — Need chunks but latency matters
- `basic` — General-purpose, balanced (default)
- `advanced` — Precision matters, detailed content extraction

## Output Schema

Always JSON. Required top-level keys:

| Key | Type | Description |
|-----|------|-------------|
| `provider` | `string` | Always `"tavily"` |
| `query` | `string` | The search query sent |
| `type` | `string` | Operation type: `"search"` or `"extract"` |
| `results` | `array<object>` | Search or extract results |
| `error` | `string \| null` | Error message, or `null` on success |
| `http_status` | `int \| null` | HTTP status from Tavily API |
| `error_code` | `string \| null` | Machine-readable error code |
| `meta` | `object` | `duration_ms`, `endpoint`, `version`, `topic`, `search_depth`, and optionally `debug` |

Additional top-level keys (present when applicable):
- `raw_response` (only with `--raw`)
- `failed_results` (extract mode — URLs that failed extraction or were blocked by SSRF guard)

### Search Results

| Key | Type | Description |
|-----|------|-------------|
| `title` | `string` | Page title |
| `url` | `string` | Page URL |
| `content` | `string` | Extracted text snippet |
| `score` | `float` | Relevance score (0–1) |
| `published_date` | `string \| undefined` | Publication date (only with `--topic news`) |
| `raw_content` | `string \| undefined` | Full page markdown (only with `--include-raw-content`) |
| `position` | `int` | Result rank (1-based) |

### Extract Results

| Key | Type | Description |
|-----|------|-------------|
| `url` | `string` | Extracted URL |
| `raw_content` | `string` | Full extracted content (markdown or text) |
| `position` | `int` | Result rank (1-based) |

Failed extractions appear in the `failed_results` array:

| Key | Type | Description |
|-----|------|-------------|
| `url` | `string` | Failed URL |
| `error` | `string` | Error reason (e.g., `"blocked_by_ssrf_guard"`) |

## Error Codes

| Code | HTTP Status | Meaning |
|------|-------------|---------|
| `auth_error` | 401 | Missing or invalid API key |
| `rate_limited` | 429 | Rate limit exceeded |
| `quota_exceeded` | 432 | Plan usage limit (Tavily-specific) |
| `payg_cap_exceeded` | 433 | Pay-as-you-go cap (Tavily-specific) |
| `client_error` | 400 | Bad request |
| `server_error` | 5xx | Server error |
| `network_error` | 0 | Connection/timeout failure |
| `validation_error` | — | Invalid query or parameters |
| `config_error` | — | `TAVILY_API_KEY` not set |
| `internal_error` | — | Unexpected exception (stack trace only with `--debug`) |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | API/network error |
| `2` | Config/validation error |

## Credits Budget

| Operation | Cost |
|-----------|------|
| Search (ultra-fast / fast / basic) | 1 credit |
| Search (advanced) | 2 credits |
| Extract (basic) | 1 credit per 5 URLs |
| Extract (advanced) | 2 credits per 5 URLs |

Daily brief estimate: 4 projects x ~5 searches x 1 credit = ~20 credits/day = ~600/month (within free tier of 1,000).

## Example Output (Success — Search)

```json
{
  "provider": "tavily",
  "query": "AI agents latest developments",
  "type": "search",
  "results": [
    {
      "title": "AI Agents Are Reshaping Enterprise Software",
      "url": "https://example.com/ai-agents",
      "content": "AI agents are increasingly being deployed...",
      "score": 0.92,
      "published_date": "2026-03-07T10:30:00Z",
      "position": 1
    }
  ],
  "error": null,
  "http_status": 200,
  "error_code": null,
  "meta": {
    "duration_ms": 342,
    "endpoint": "/search",
    "version": "1.0.0",
    "topic": "news",
    "search_depth": "basic",
    "result_count": 1
  }
}
```

## Example Output (Success — Extract)

```json
{
  "provider": "tavily",
  "query": "",
  "type": "extract",
  "results": [
    {
      "url": "https://example.com/article",
      "raw_content": "# Article Title\n\nFull markdown content...",
      "position": 1
    }
  ],
  "error": null,
  "http_status": 200,
  "error_code": null,
  "meta": {
    "duration_ms": 1200,
    "endpoint": "/extract",
    "version": "1.0.0",
    "urls_requested": 2,
    "urls_extracted": 1,
    "urls_failed": 1
  },
  "failed_results": [
    {"url": "https://192.168.1.1/secret", "error": "blocked_by_ssrf_guard"}
  ]
}
```

## Example Output (Error)

```json
{
  "provider": "tavily",
  "query": "",
  "type": "search",
  "results": [],
  "error": "config_error: TAVILY_API_KEY not set",
  "http_status": null,
  "error_code": "config_error",
  "meta": {
    "duration_ms": 0,
    "endpoint": null,
    "version": "1.0.0"
  }
}
```

## Security Notes

- **Never commit `.env`** — add it to `.gitignore`.
- API key is only sent in the `Authorization: Bearer` header over HTTPS; it never appears in stdout, stderr, or logs.
- All HTTP calls go to `https://api.tavily.com/*` — hardcoded, no user-configurable endpoint.
- **Extract SSRF guards**: All user-provided URLs are validated via `_is_ssrf_safe()` — hostnames are DNS-resolved and checked against 12 blocked CIDR networks. IPv4-mapped IPv6 addresses normalized. Only `http://` and `https://` schemes allowed.
- Search queries do NOT need SSRF guards — they are strings, not URLs.
- Query length capped at 400 characters.
- `--debug` adds sanitized diagnostics (request body without query) to `meta.debug`; never exposes key values.
- Unhandled exceptions produce stable JSON `{"error": "internal_error: ..."}` without stack traces (unless `--debug`).
- Pure stdlib Python — only `urllib.request` for HTTP, no third-party dependencies.

## Source tests

Selected offline router/credential-inheritance consumers live in `websearch/tests/test_search_workflows.py` in the source repository. Provider-local live smoke and endpoint scripts are not bundled or required for portability.
