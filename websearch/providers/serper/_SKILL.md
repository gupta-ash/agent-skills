---
name: serper
description: Web, news, images, videos, places, shopping, scholar, patents, and autocomplete search via Serper.dev API (Google Search). Auto-detects search type, structured JSON output. Python 3.8+, stdlib-only.
version: 3.0.0
metadata:
  openclaw:
    requires: { env: [SERPER_API_KEY], bins: [python3] }
    primaryEnv: SERPER_API_KEY
    os: [linux, macos]
---

# serper

> **Bundled inside the Search Router** at `~/.agents/skills/websearch/`. This file is preserved for reference but the skill is no longer auto-discoverable. Serper remains in retained specialist chains (including `news`, `finance` and `local`), not general `web` fallback. For an explicit Serper request use `python -B ~/.agents/skills/websearch/scripts/search.py --query "<query>" --mode web --force-provider serper:search --count 5`; specialist selection follows the router's `ROUTES` table.

Web, news, images, videos, places, shopping, scholar, patents, and autocomplete search via `https://google.serper.dev`. Google Search results through the Serper.dev API. Python 3.8+, stdlib-only, no external dependencies. No browser required.

## When to Use

- Web research, documentation lookup, API references
- News search (latest headlines, breaking news, current events)
- Looking up facts or current information via Google Search
- Any task requiring web search without interactive browsing
- Fallback within retained specialist chains only (such as `news` or `finance`), or an explicit `--force-provider serper:...` request; not general web fallback after Brave
- Image search (finding photos, diagrams, visual references)
- Video search (tutorials, talks, demos with duration and channel info)
- Local/places search (businesses, restaurants, services near a location)
- Shopping/product search (price comparisons, product listings — ⚠️ costs 2 credits)
- Academic paper search (scholarly articles, citations, publication info)
- Patent search (inventions, IP research, patent filings)
- Autocomplete suggestions (query expansion, related search ideas)
- Inline content extraction from search results (--content flag) — fetch and extract article text without a separate webfetch call

## Environment variables

| Variable        | Required | Description                                                   |
|-----------------|----------|---------------------------------------------------------------|
| `SERPER_API_KEY` | **yes** | Serper.dev API key; also accepted via `--api-key` argument    |

Priority: `--api-key` argument > `SERPER_API_KEY` env var.

## Usage

```bash
# Basic web search (JSON output)
python3 {baseDir}/scripts/serper.py --query "OpenClaw AI orchestration"

# News search (explicit type)
python3 {baseDir}/scripts/serper.py --query "AI agents" --type news

# Auto-detect search type (keywords like "latest", "breaking", "news about" route to /news)
python3 {baseDir}/scripts/serper.py --query "latest AI news today"

# Time-filtered search (last 24 hours)
python3 {baseDir}/scripts/serper.py --query "OpenClaw" --tbs "qdr:d"

# Locale filtering (country + language)
python3 {baseDir}/scripts/serper.py --query "KI Nachrichten" --gl de --hl de

# Debug diagnostics (stderr, no secrets printed)
python3 {baseDir}/scripts/serper.py --query "OpenClaw" --debug

# Image search
python3 {baseDir}/scripts/serper.py --query "golden gate bridge" --type images

# Video search
python3 {baseDir}/scripts/serper.py --query "python tutorial" --type videos

# Places/local search
python3 {baseDir}/scripts/serper.py --query "pizza restaurants San Francisco" --type places

# Shopping search (⚠️ costs 2 credits per query)
python3 {baseDir}/scripts/serper.py --query "wireless headphones" --type shopping

# Academic paper search
python3 {baseDir}/scripts/serper.py --query "neural networks deep learning" --type scholar

# Patent search
python3 {baseDir}/scripts/serper.py --query "natural language processing" --type patents

# Autocomplete suggestions
python3 {baseDir}/scripts/serper.py --query "how to learn" --type autocomplete

# Content extraction — fetch article text from top results
python3 {baseDir}/scripts/serper.py --query "what is machine learning" --content

# Content extraction — top 5 results with custom char limit
python3 {baseDir}/scripts/serper.py --query "climate change" --content --content-top 5 --content-max-chars 3000

# Content extraction with news search
python3 {baseDir}/scripts/serper.py --query "latest AI news" --type news --content --content-top 3
```

## Output schema

Always JSON. Required top-level keys:

| Key              | Type             | Description                                       |
|------------------|------------------|---------------------------------------------------|
| `provider`       | `string`         | Always `"serper"`                                 |
| `query`          | `string`         | The search query sent                             |
| `type`           | `string`         | Endpoint used: `"search"`, `"news"`, `"images"`, `"videos"`, `"places"`, `"shopping"`, `"scholar"`, `"patents"`, or `"autocomplete"` |
| `results`        | `array<object>`  | Search results (see below)                        |
| `error`          | `string \| null` | Error message, or `null` on success               |
| `http_status`    | `int \| null`    | HTTP status from Serper API                       |
| `error_code`     | `string \| null` | Machine-readable error code                       |
| `meta`           | `object`         | `duration_ms`, `credits_used`, `endpoint`, `auto_detected_type`, `version`, and optionally `content_extracted` (when `--content` is used) |
| `supplemental`   | `object \| undefined` | Related searches, people also ask, knowledge graph (when available) |

Each result object (web search):

| Key           | Type                  | Description                                   |
|---------------|-----------------------|-----------------------------------------------|
| `title`       | `string`              | Page title                                    |
| `url`         | `string`              | Page URL                                      |
| `description` | `string`              | Snippet / description                         |
| `position`    | `int`                 | Result rank (1-based)                         |
| `date`        | `string \| undefined` | Publication/crawl date (when available)        |
| `sitelinks`   | `array \| undefined`  | Related pages from same site (when available) |
| `content_excerpt` | `string \| null \| undefined` | Extracted article text (only present when `--content` is used; null if extraction failed) |

Each sitelink object:

| Key      | Type     | Description          |
|----------|----------|----------------------|
| `title`  | `string` | Sitelink page title  |
| `url`    | `string` | Sitelink page URL    |

**Sitelinks** are Google's grouped results — related pages from the same domain shown underneath a result (e.g., multiple Reddit threads, documentation sections). They appear only on top results where Google provides them. Do NOT use `webfetch` on sitelink URLs unless you need full content — the title alone is often enough to understand the related page.

Each result object (news search) includes additional fields:

| Key      | Type             | Description               |
|----------|------------------|---------------------------|
| `date`   | `string \| null` | Publication date          |
| `source` | `string \| null` | News source name          |
| `content_excerpt` | `string \| null \| undefined` | Extracted article text (only present when `--content` is used; null if extraction failed) |

Each result object (images):

| Key | Type | Description |
|-----|------|-------------|
| `title` | `string` | Image title |
| `url` | `string` | Page URL |
| `image_url` | `string` | Direct image URL |
| `source` | `string` | Source website |
| `width` | `int \| null` | Image width in pixels |
| `height` | `int \| null` | Image height in pixels |
| `position` | `int` | Result rank (1-based) |

Each result object (videos):

| Key | Type | Description |
|-----|------|-------------|
| `title` | `string` | Video title |
| `url` | `string` | Video URL |
| `description` | `string` | Video snippet/description |
| `date` | `string \| null` | Upload/publish date |
| `duration` | `string \| null` | Video duration |
| `channel` | `string \| null` | Channel/creator name |
| `position` | `int` | Result rank (1-based) |

Each result object (places):

| Key | Type | Description |
|-----|------|-------------|
| `title` | `string` | Business/place name |
| `address` | `string` | Street address |
| `rating` | `float \| null` | Average rating |
| `rating_count` | `int \| null` | Number of reviews |
| `category` | `string \| null` | Business category |
| `phone` | `string \| null` | Phone number |
| `website` | `string \| null` | Business website URL |
| `position` | `int` | Result rank (1-based) |

Each result object (shopping — ⚠️ 2 credits):

| Key | Type | Description |
|-----|------|-------------|
| `title` | `string` | Product name |
| `url` | `string` | Product listing URL |
| `price` | `string \| null` | Price with currency |
| `source` | `string \| null` | Retailer/store name |
| `rating` | `float \| null` | Product rating |
| `rating_count` | `int \| null` | Number of reviews |
| `position` | `int` | Result rank (1-based) |

Each result object (scholar):

| Key | Type | Description |
|-----|------|-------------|
| `title` | `string` | Paper title |
| `url` | `string` | Paper URL |
| `description` | `string` | Abstract snippet |
| `publication_info` | `string` | Authors, journal, year summary |
| `year` | `int \| null` | Publication year |
| `cited_by` | `int \| null` | Citation count |
| `pdf_url` | `string \| undefined` | Direct PDF link (when available) |
| `position` | `int` | Result rank (1-based) |
| `content_excerpt` | `string \| null \| undefined` | Extracted article text (only present when `--content` is used; null if extraction failed) |

Each result object (patents):

| Key | Type | Description |
|-----|------|-------------|
| `title` | `string` | Patent title |
| `url` | `string` | Google Patents URL |
| `description` | `string` | Patent description/abstract |
| `publication_number` | `string \| null` | Patent publication number |
| `inventor` | `string \| null` | Inventor name(s) |
| `assignee` | `string \| null` | Patent assignee/owner |
| `priority_date` | `string \| null` | Priority date |
| `filing_date` | `string \| null` | Filing date |
| `grant_date` | `string \| null` | Grant date |
| `position` | `int` | Result rank (1-based) |
| `content_excerpt` | `string \| null \| undefined` | Extracted article text (only present when `--content` is used; null if extraction failed) |

Each result object (autocomplete):

| Key | Type | Description |
|-----|------|-------------|
| `value` | `string` | Suggested search query |
| `position` | `int` | Suggestion rank (1-based) |

## Error codes

| Code               | Meaning                                          |
|--------------------|--------------------------------------------------|
| `validation_error` | Invalid query or parameters (empty, too long)    |
| `config_error`     | No API key found in env or arguments             |
| `auth_error`       | 401/403 from Serper API (invalid key)            |
| `rate_limited`     | 429 Too Many Requests                            |
| `server_error`     | 5xx from Serper API                              |
| `network_error`    | Connection/DNS/timeout failure                   |
| `parse_error`      | Unexpected response format from Serper API       |
| `internal_error`   | Unexpected exception (stack trace only w/ `--debug`) |

## Exit codes

| Code | Meaning                              |
|------|--------------------------------------|
| `0`  | Success                              |
| `1`  | API/network error                    |
| `2`  | Config/validation error              |

## Flags

**Search:** `--query` / `-q`, `--type` / `-t` (search, news, images, videos, places, shopping, scholar, patents, autocomplete), `--count` / `-n` (1-100, default 10), `--gl` (country code), `--hl` (language), `--tbs` (time filter: qdr:h/d/w/m/y), `--page` (pagination)

**Auth:** `--api-key` (overrides SERPER_API_KEY env var)

**Safety:** `--debug` (stderr diagnostics, no secrets printed)

**Content:** `--content` (off by default — enable inline content extraction), `--content-top` (1-10, default 3), `--content-timeout` (per-URL timeout in seconds, default 10), `--content-max-chars` (max text per URL, default 5000)

**Credit costs:** All endpoints cost 1 credit per query, except `shopping` which costs **2 credits** per query. Actual credit usage is reported in `meta.credits_used`.

## Example output (success)

```json
{
  "provider": "serper",
  "query": "What is Claude Cowork",
  "type": "search",
  "results": [
    {
      "title": "Get started with Cowork | Claude Help Center",
      "url": "https://support.claude.com/en/articles/13345190",
      "description": "When using Cowork, Claude can open, edit...",
      "position": 1,
      "date": "1 day ago",
      "sitelinks": [
        {"title": "What is Cowork?", "url": "https://support.claude.com/...#h_9fe40dc9c6"},
        {"title": "Get started", "url": "https://support.claude.com/...#h_bfc64d50fa"}
      ]
    },
    {
      "title": "What exactly is Claude Cowork? : r/ClaudeAI",
      "url": "https://www.reddit.com/r/ClaudeAI/comments/...",
      "description": "Claude Cowork is Anthropic's take on agentic workflows...",
      "position": 2,
      "date": "Feb 26, 2026",
      "sitelinks": [
        {"title": "Tried Claude Cowork last night...", "url": "https://www.reddit.com/..."}
      ]
    }
  ],
  "error": null,
  "http_status": 200,
  "error_code": null,
  "meta": {
    "duration_ms": 287,
    "credits_used": 1,
    "endpoint": "/search",
    "auto_detected_type": false,
    "version": "3.0.0"
  },
  "supplemental": {
    "related_searches": ["Claude Cowork vs Code", "How to use Claude Cowork"],
    "people_also_ask": [
      {"question": "Is Claude Cowork free?", "snippet": "...", "title": "...", "url": "..."}
    ]
  }
}
```

## Example output (error)

```json
{
  "provider": "serper",
  "query": "",
  "type": "search",
  "results": [],
  "error": "validation_error: --query is required and must not be empty",
  "http_status": null,
  "error_code": "validation_error",
  "meta": {
    "duration_ms": 0
  }
}
```

## Security notes

- **Never commit `.env`** -- add it to `.gitignore`.
- API key is only sent in the `X-API-KEY` header to `google.serper.dev`; it never appears in stdout, stderr, or logs.
- All HTTP calls go ONLY to `https://google.serper.dev/*` -- hardcoded, no user-configurable endpoint (SSRF protection).
- Query length capped at 2048 characters.
- `--debug` adds sanitized diagnostics (key presence, not key value) to stderr.
- Unhandled exceptions produce stable JSON `{"error": "internal_error: ..."}` without stack traces (unless `--debug`).
- Pure stdlib Python -- only `urllib.request` for HTTP, no third-party dependencies.
- **Content extraction SSRF guards**: When `--content` is used, all URLs are validated before fetching — hostnames are resolved via DNS and checked against a blocklist of private/reserved IP ranges (RFC-1918, link-local, loopback, carrier-grade NAT, IPv4-mapped IPv6). Only `http://` and `https://` schemes are allowed. Raw HTML is capped at 10MB per URL.

## Source tests

Selected offline specialist/credential-inheritance consumers live in `websearch/tests/test_search_workflows.py` in the source repository. No provider-local installed smoke script is bundled; live API use requires the user's own authorization and credentials.
