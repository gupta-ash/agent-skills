---
name: exa
description: Neural web search with category routing (news, company, research paper, tweet, people, financial report), deep search types with structured output, and URL content extraction via Exa AI API. Structured JSON output envelope. Python 3.8+, stdlib-only.
version: 2.0.0
metadata:
  openclaw:
    requires: { env: [EXA_API_KEY], bins: [python3] }
    primaryEnv: EXA_API_KEY
    os: [linux, macos]
---

# exa

> **Bundled inside the Search Router** at `~/.agents/skills/websearch/`. This file is preserved for reference but the skill is no longer auto-discoverable. General `web` uses Codex standalone → Brave web → Tavily general → Exa auto → SearXNG, making `exa:auto` the fourth automatic candidate. Exa remains in the retained specialist chains (including `research`, `deep` and `similar`). For an explicit Exa request use `python -B ~/.agents/skills/websearch/scripts/search.py --query "<query>" --mode web --force-provider exa:auto --count 5`; specialist selection follows the router's `ROUTES` table.

Neural web search with category routing, URL content extraction, findSimilar, AI-powered answer, and async deep research via `https://api.exa.ai`. Categories route to dedicated indexes: `news`, `company`, `research paper`, `tweet`, `people`, `personal site`, `financial report`. Includes Contents endpoint for pulling text/highlights/summary from known URLs, Similar endpoint for finding related pages, Answer endpoint for cited Q&A, and Research endpoint for long-form reports. Supports six search types — `auto`, `neural`, `fast`, `deep`, `deep-reasoning`, `instant` — with deep search types offering structured JSON output via `--output-schema` and grounding citations. Python 3.8+, stdlib-only, no external dependencies. No browser required.

## When to Use

- Neural/semantic web search (finds meaning, not just keywords)
- News search with `--category news` (real-time news articles)
- Company research with `--category company` (company info, profiles)
- Academic paper search with `--category "research paper"` (arxiv, papers)
- Tweet search with `--category tweet` (Twitter/X discussions)
- People search with `--category people` (find by role/expertise)
- Personal site search with `--category "personal site"` (developer portfolios, blogs)
- Financial report search with `--category "financial report"` (SEC filings, earnings)
- Extracting text/highlights/summary from known URLs via `--type contents`
- When you need inline content with search results (`--content` flag)
- Semantic similarity search where keyword matching fails
- Any task requiring LLM-optimized search results with category-aware ranking
- Find pages similar to a known URL via `--type similar`
- Get AI-generated answers with cited sources via `--type answer`
- Run async deep research reports via `--type research`

## Quick Start

```bash
# Basic search
python3 {baseDir}/scripts/exa_search.py -q "AI safety" -n 10

# News category
python3 {baseDir}/scripts/exa_search.py -q "OpenAI announcements" --category news -n 5

# Fast search (lowest latency)
python3 {baseDir}/scripts/exa_search.py -q "NVIDIA stock price" --search-type fast -n 5

# Deep search (thorough results)
python3 {baseDir}/scripts/exa_search.py -q "AI chip companies" --search-type deep -n 10

# Deep search with structured output
python3 {baseDir}/scripts/exa_search.py -q "AI chip companies" --search-type deep --output-schema '{"type":"object","required":["companies"],"properties":{"companies":{"type":"array","items":{"type":"object","required":["name"],"properties":{"name":{"type":"string"},"description":{"type":"string"}}}}}}'

# Deep reasoning with additional queries
python3 {baseDir}/scripts/exa_search.py -q "transformer improvements" --search-type deep-reasoning --additional-queries "attention innovations,efficient transformers" -n 10

# With inline content
python3 {baseDir}/scripts/exa_search.py -q "transformer architecture" --content --content-type text --content-max-chars 3000 -n 5

# Research papers
python3 {baseDir}/scripts/exa_search.py -q "attention mechanism" --category "research paper" -n 10

# Company search
python3 {baseDir}/scripts/exa_search.py -q "AI startup healthcare" --category company -n 5

# Tweet search
python3 {baseDir}/scripts/exa_search.py -q "AI safety debate" --category tweet -n 10

# People search
python3 {baseDir}/scripts/exa_search.py -q "ML engineer distributed systems" --category people -n 5

# Financial reports
python3 {baseDir}/scripts/exa_search.py -q "AI quarterly earnings" --category "financial report" -n 5

# Date-filtered news
python3 {baseDir}/scripts/exa_search.py -q "AI regulation" --category news --start-date 2026-03-01 --end-date 2026-03-09 -n 5

# Domain filtering
python3 {baseDir}/scripts/exa_search.py -q "python tutorial" --include-domains "python.org,docs.python.org" -n 5

# Search with highlights
python3 {baseDir}/scripts/exa_search.py -q "neural networks" --content --content-type highlights --content-max-chars 2000 -n 3

# Contents mode — fetch known URLs
python3 {baseDir}/scripts/exa_search.py --type contents --urls "https://example.com/article" --content-max-chars 5000

# Contents mode — highlights from known URLs
python3 {baseDir}/scripts/exa_search.py --type contents --urls "https://example.com,https://other.com" --content-type highlights --content-max-chars 2000

# Debug diagnostics
python3 {baseDir}/scripts/exa_search.py -q "test" --debug

# Find similar pages
python3 {baseDir}/scripts/exa_search.py --type similar --url "https://arxiv.org/abs/2305.10403" -n 10

# Find similar excluding source domain
python3 {baseDir}/scripts/exa_search.py --type similar --url "https://openai.com/blog/chatgpt" --exclude-source-domain -n 5

# Answer a question with citations
python3 {baseDir}/scripts/exa_search.py --type answer -q "What is the current market cap of NVIDIA?"

# Answer with source text included
python3 {baseDir}/scripts/exa_search.py --type answer -q "How does RLHF work?" --include-source-text

# Deep research (blocking — waits for completion)
python3 {baseDir}/scripts/exa_search.py --type research -q "Compare AI agent frameworks" --research-model exa-research-pro

# Deep research (async — returns ID immediately)
python3 {baseDir}/scripts/exa_search.py --type research -q "AI regulation landscape 2026" --async

# Check research status
python3 {baseDir}/scripts/exa_search.py --type research --research-id "abc123"
```

## Authentication

Set `EXA_API_KEY` as an environment variable. The key is sent as an `x-api-key` header over HTTPS. No OAuth, no MCP, no browser flow.

```bash
export EXA_API_KEY="your-api-key-here"
```

Get a key at [dashboard.exa.ai](https://dashboard.exa.ai).

## CLI Flags

### Shared Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--type` / `-t` | choice | `search` | Operation mode: `search`, `contents`, `similar`, `answer`, `research` |
| `--timeout` | int | `30` | HTTP timeout in seconds |
| `--raw` | bool | `false` | Include raw API response in output |
| `--debug` | bool | `false` | Include diagnostics in `meta.debug` |

### Search Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--query` / `-q` | str | required | Search query |
| `--count` / `-n` | int | `10` | Number of results (1–100) |
| `--category` | choice | none | Category filter: `news`, `company`, `research paper`, `tweet`, `people`, `personal site`, `financial report` |
| `--search-type` | choice | `auto` | Search algorithm: `auto`, `neural`, `fast`, `deep`, `deep-reasoning`, `instant` |
| `--content` | bool | `false` | Enable inline content retrieval (off by default) |
| `--content-type` | choice | `text` | Content type: `text`, `highlights`, `summary` |
| `--content-max-chars` | int | `5000` | Max characters per result |
| `--start-date` | str | null | Results published after (ISO 8601, e.g. `2026-03-01`) |
| `--end-date` | str | null | Results published before (ISO 8601) |
| `--start-crawl-date` | str | null | Results crawled after (ISO 8601) |
| `--end-crawl-date` | str | null | Results crawled before (ISO 8601) |
| `--include-domains` | str | null | Comma-separated domain whitelist |
| `--exclude-domains` | str | null | Comma-separated domain blacklist |
| `--include-text` | str | null | Text that must appear in results |
| `--exclude-text` | str | null | Text that must not appear in results |
| `--max-age-hours` | int | null | Content freshness: `-1`=cache, `0`=always livecrawl, `N`=hour threshold |

### Deep Search Flags (deep/deep-reasoning only)

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--output-schema` | str (JSON) | null | JSON schema for structured output |
| `--additional-queries` | str (comma-sep) | null | Extra query variations for broader coverage |

### Contents Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--urls` | str | required | Comma-separated URLs to fetch (max 100) |
| `--content-type` | choice | `text` | Content type: `text`, `highlights`, `summary` |
| `--content-max-chars` | int | `5000` | Max characters per URL |
| `--max-age-hours` | int | null | Content freshness: `-1`=cache, `0`=always livecrawl, `N`=hour threshold |

### Similar Mode Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--url` | str | required | Source URL to find similar pages for |
| `--exclude-source-domain` | bool | `false` | Exclude results from source URL's domain |

Also reuses: `--count`, `--category`, `--content`, `--content-type`, `--content-max-chars`, `--include-domains`, `--exclude-domains`, `--start-date`, `--end-date`, `--start-crawl-date`, `--end-crawl-date`, `--include-text`, `--exclude-text`, `--max-age-hours`, `--timeout`, `--raw`, `--debug`

### Answer Mode Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--include-source-text` | bool | `false` | Include full source text in citations |

Uses `--query` (required) and shared flags.

### Research Mode Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--research-model` | choice | `exa-research` | Model: `exa-research-fast`, `exa-research`, `exa-research-pro` |
| `--async` | bool | `false` | Return research ID immediately without waiting |
| `--research-id` | str | null | Check status of existing research task (skips creation) |
| `--poll-interval` | int | `5` | Seconds between polls (blocking mode only) |
| `--max-wait` | int | `300` | Max seconds to wait (blocking mode only) |

Uses `--query` (required for creation) and shared flags.

## Category Reference

The `--category` flag routes queries to dedicated Exa indexes optimized for each content type:

| Category | Use Case | Tips |
|----------|----------|------|
| *(none)* | General web search | Best for broad queries; uses default index |
| `news` | News articles and headlines | Use for P1–P4 briefs; combine with `--start-date`/`--end-date` for date ranges |
| `company` | Company research and profiles | Returns company profiles; use singular form ("AI startup", not "AI startups") |
| `research paper` | Academic papers | arxiv, paperswithcode; use specific technical queries |
| `tweet` | Twitter/X posts | Real-time discussions; fastest category for trending topics |
| `people` | People by role/expertise | LinkedIn profiles; describe what they work on ("ML engineer at startup") |
| `personal site` | Personal websites and blogs | Developer portfolios, personal blogs, individual projects |
| `financial report` | Financial filings and reports | SEC filings, gov speeches, earnings reports; useful for P1 Financial Markets Brief |

**Project mapping:**
- P1 (Financial Markets Brief): `--category "financial report"` or `--category news`
- P2 (Tech Industry Brief): `--category news`
- P3 (AI Industry Brief): `--category news` or `--category "research paper"`
- P4 (AI Agents Brief): `--category news` or `--category tweet`

## Search Types

The `--search-type` flag controls the search algorithm:

| Type | Best For | Speed |
|------|----------|-------|
| `instant` | Real-time, lowest latency | Fastest |
| `fast` | Quick lookups, autocomplete | Fast |
| `auto` | Most queries (default) | Medium |
| `neural` | Semantic/embeddings search | Medium |
| `deep` | Research, enrichment, structured output | Slow (4–12s) |
| `deep-reasoning` | Complex multi-step reasoning | Slowest |

## Structured Output (Deep Search)

When using `--search-type deep` or `--search-type deep-reasoning` with `--output-schema`, Exa returns structured JSON conforming to the provided schema plus grounding citations.

### Availability

- Requires `--search-type deep` or `--search-type deep-reasoning`
- Requires `--output-schema` with a valid JSON schema string
- Not available with other search types (`auto`, `neural`, `fast`, `instant`)

### Output Envelope Keys

When structured output is active, two additional top-level keys appear in the response:

| Key | Type | Description |
|-----|------|-------------|
| `structured_output` | `object` | Parsed JSON conforming to the provided `--output-schema` |
| `grounding` | `array<object>` | Citation evidence for each extracted field |

Each grounding entry:

| Key | Type | Description |
|-----|------|-------------|
| `field` | `string` | JSON path of the field (e.g. `companies[0].name`) |
| `citations` | `array<string>` | Source URLs supporting the value |
| `confidence` | `float` | Confidence score (0.0–1.0) |

### Example: Structured Output Response

```json
{
  "provider": "exa",
  "query": "AI chip companies",
  "type": "search",
  "results": [
    {
      "title": "Top AI Chip Companies in 2026",
      "url": "https://example.com/ai-chips",
      "published_date": "2026-03-01T00:00:00.000Z",
      "author": null,
      "score": 0.312,
      "position": 1
    }
  ],
  "structured_output": {
    "companies": [
      {"name": "NVIDIA", "description": "Leading GPU and AI accelerator manufacturer"},
      {"name": "AMD", "description": "CPU and GPU maker with MI300 AI accelerators"}
    ]
  },
  "grounding": [
    {"field": "companies[0].name", "citations": ["https://example.com/ai-chips"], "confidence": 0.98},
    {"field": "companies[1].name", "citations": ["https://example.com/ai-chips"], "confidence": 0.95}
  ],
  "error": null,
  "http_status": 200,
  "error_code": null,
  "meta": {
    "duration_ms": 6420,
    "endpoint": "/search",
    "category": null,
    "search_type": "deep",
    "result_count": 1,
    "content_enabled": false,
    "version": "2.0.0"
  }
}
```

## Output Schema

Always JSON. Required top-level keys:

| Key | Type | Description |
|-----|------|-------------|
| `provider` | `string` | Always `"exa"` |
| `query` | `string` | The search query sent |
| `type` | `string` | Operation type: `"search"`, `"contents"`, `"similar"`, `"answer"`, or `"research"` |
| `results` | `array<object>` | Search or contents results (see below) |
| `error` | `string \| null` | Error message, or `null` on success |
| `http_status` | `int \| null` | HTTP status from Exa API |
| `error_code` | `string \| null` | Machine-readable error code |
| `meta` | `object` | `duration_ms`, `endpoint`, `version`, `category`, `search_type`, `result_count`, `content_enabled`, and optionally `content_type`, `cost_dollars`, `debug` |

Additional top-level keys (present when applicable):
- `raw_response` (only with `--raw`)
- `answer` (present in answer and research modes — synthesized answer text or research report)
- `structured_output` (deep/deep-reasoning with `--output-schema` — parsed JSON matching the schema)
- `grounding` (deep/deep-reasoning with `--output-schema` — citation evidence per field)
- `failed_results` (contents mode — URLs that failed extraction or were blocked by SSRF guard)

### Search Results

| Key | Type | Description |
|-----|------|-------------|
| `title` | `string` | Page title |
| `url` | `string` | Page URL |
| `published_date` | `string \| null` | Publication date (ISO 8601) |
| `author` | `string \| null` | Author name |
| `score` | `float \| null` | Relevance score |
| `position` | `int` | Result rank (1-based) |
| `content_excerpt` | `string \| null \| undefined` | Full text (only with `--content --content-type text`) |
| `highlights` | `array \| undefined` | Key excerpts (only with `--content --content-type highlights`) |
| `highlight_scores` | `array \| undefined` | Relevance scores for highlights |
| `summary` | `string \| undefined` | AI summary (only with `--content --content-type summary`) |

### Contents Results

| Key | Type | Description |
|-----|------|-------------|
| `url` | `string` | Fetched URL |
| `title` | `string` | Page title |
| `published_date` | `string \| null` | Publication date |
| `author` | `string \| null` | Author name |
| `position` | `int` | Result rank (1-based) |
| `content_excerpt` | `string \| null` | Full text (with `--content-type text`) |
| `highlights` | `array \| undefined` | Key excerpts (with `--content-type highlights`) |
| `highlight_scores` | `array \| undefined` | Relevance scores for highlights |
| `summary` | `string \| undefined` | AI summary (with `--content-type summary`) |

### Answer Results

| Key | Type | Description |
|-----|------|-------------|
| `position` | `int` | Citation rank (1-based) |
| `url` | `string` | Source URL |
| `title` | `string` | Source title |
| `published_date` | `string \| null` | Publication date |
| `author` | `string \| null` | Author name |
| `content_excerpt` | `string \| undefined` | Source text (only with `--include-source-text`) |

## Error Codes

| Code | HTTP Status | Meaning |
|------|-------------|---------|
| `auth_error` | 401 | Missing or invalid API key |
| `quota_exceeded` | 402 | Credits exhausted |
| `access_denied` | 403 | Blocked content or insufficient permissions |
| `rate_limited` | 429 | Too many requests (10 QPS limit) |
| `client_error` | 4xx | Bad request |
| `server_error` | 5xx | Server error |
| `network_error` | — | Connection/timeout failure |
| `validation_error` | — | Invalid query or parameters |
| `config_error` | — | `EXA_API_KEY` not set |
| `internal_error` | — | Unexpected exception (stack trace only with `--debug`) |
| `timeout_error` | — | Research task didn't complete within `--max-wait` seconds |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | API/network error |
| `2` | Config/validation error |

## Example Output (Success — Search)

```json
{
  "provider": "exa",
  "query": "AI agents latest developments",
  "type": "search",
  "results": [
    {
      "title": "AI Agents Are Reshaping Enterprise Software",
      "url": "https://example.com/ai-agents",
      "published_date": "2026-03-07T10:30:00.000Z",
      "author": "Jane Smith",
      "score": 0.198,
      "position": 1
    }
  ],
  "error": null,
  "http_status": 200,
  "error_code": null,
  "meta": {
    "duration_ms": 542,
    "endpoint": "/search",
    "category": "news",
    "search_type": "auto",
    "result_count": 1,
    "content_enabled": false,
    "version": "2.0.0"
  }
}
```

## Example Output (Success — Search with Content)

```json
{
  "provider": "exa",
  "query": "transformer architecture explained",
  "type": "search",
  "results": [
    {
      "title": "The Illustrated Transformer",
      "url": "https://jalammar.github.io/illustrated-transformer/",
      "published_date": "2025-06-27T00:00:00.000Z",
      "author": "Jay Alammar",
      "score": 0.215,
      "position": 1,
      "content_excerpt": "The Transformer model uses self-attention to process input sequences..."
    }
  ],
  "error": null,
  "http_status": 200,
  "error_code": null,
  "meta": {
    "duration_ms": 890,
    "endpoint": "/search",
    "category": null,
    "search_type": "auto",
    "result_count": 1,
    "content_enabled": true,
    "content_type": "text",
    "cost_dollars": 0.005,
    "version": "2.0.0"
  }
}
```

## Example Output (Success — Contents)

```json
{
  "provider": "exa",
  "query": "",
  "type": "contents",
  "results": [
    {
      "url": "https://example.com/article",
      "title": "Article Title",
      "published_date": "2026-03-05T00:00:00.000Z",
      "author": "Author Name",
      "position": 1,
      "content_excerpt": "Full extracted content from the article..."
    }
  ],
  "error": null,
  "http_status": 200,
  "error_code": null,
  "meta": {
    "duration_ms": 1200,
    "endpoint": "/contents",
    "urls_requested": 2,
    "urls_fetched": 1,
    "urls_failed": 1,
    "content_type": "text",
    "version": "2.0.0"
  },
  "failed_results": [
    {"url": "https://192.168.1.1/secret", "error": "blocked_by_ssrf_guard"}
  ]
}
```

## Example Output (Error)

```json
{
  "provider": "exa",
  "query": "",
  "type": "search",
  "results": [],
  "error": "validation_error: --query is required",
  "http_status": null,
  "error_code": "validation_error",
  "meta": {
    "duration_ms": 0,
    "endpoint": "/search",
    "version": "2.0.0"
  }
}
```

## Example Output (Success — Answer)

```json
{
  "provider": "exa",
  "query": "What is RLHF?",
  "type": "answer",
  "results": [
    {
      "position": 1,
      "url": "https://example.com/rlhf-explained",
      "title": "RLHF Explained",
      "published_date": "2026-01-15",
      "author": "ML Researcher"
    }
  ],
  "answer": "RLHF (Reinforcement Learning from Human Feedback) is a technique...",
  "error": null,
  "http_status": 200,
  "error_code": null,
  "meta": {
    "duration_ms": 1200,
    "endpoint": "/answer",
    "citation_count": 5,
    "version": "2.0.0"
  }
}
```

## Example Output (Success — Research, async)

```json
{
  "provider": "exa",
  "query": "Compare AI agent frameworks",
  "type": "research",
  "results": [],
  "answer": null,
  "error": null,
  "http_status": 201,
  "error_code": null,
  "meta": {
    "duration_ms": 946,
    "endpoint": "/research/v1",
    "research_id": "r_abc123",
    "research_status": "pending",
    "research_model": "exa-research-fast",
    "version": "2.0.0"
  }
}
```

## Content Recommendations

Based on integration testing with the Exa API:

- **For news briefs (P1–P4):** Use `--content --content-type text --content-max-chars 3000` — provides full article text inline, eliminating the need for a separate `webfetch` call for ~80% of articles.
- **Highlights are too fragmented** for composing news briefs — they return disconnected sentence fragments. Use `text` for briefs, `highlights` only for quick scanning.
- **Content ON adds ~$0.005/query** with zero latency penalty on top of search — cost-effective for daily briefs.
- **Livecrawl (`--max-age-hours 0`) adds ~7x latency** — avoid for daily briefs where cached content is acceptable. Use livecrawl only for breaking news where freshness is critical.
- **Exa content is reliable for ~80% of articles** — use `webfetch` as fallback for paywalled or bot-protected sites where Exa returns empty content.
- **Summary mode** uses AI to generate a summary — useful for quick overviews but adds latency and may lose important details.

## Security Notes

- **Never commit `.env`** — add it to `.gitignore`.
- API key is only sent in the `x-api-key` header to `api.exa.ai`; it never appears in stdout, stderr, or logs.
- All HTTP calls go ONLY to `https://api.exa.ai/*` — hardcoded, no user-configurable endpoint (SSRF protection).
- `--debug` adds sanitized diagnostics (request body without query) to `meta.debug`; never exposes key values.
- Unhandled exceptions produce stable JSON `{"error": "internal_error: ..."}` without stack traces (unless `--debug`).
- Pure stdlib Python — only `urllib.request` for HTTP, no third-party dependencies.
- **Contents SSRF guards**: When `--type contents` is used, all user-provided URLs are validated via `_is_ssrf_safe()` — hostnames are DNS-resolved and checked against 12 blocked CIDR networks (RFC-1918, link-local, loopback, carrier-grade NAT, IPv4-mapped IPv6). Only `http://` and `https://` schemes allowed.

## Source tests

Selected offline specialist/credential-inheritance consumers live in `websearch/tests/test_search_workflows.py` in the source repository. No provider-local installed smoke script is bundled; live API use requires the user's own authorization and credentials.
