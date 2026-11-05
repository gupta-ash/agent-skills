---
name: brave
description: Web, image, video, news, suggest, and local search via Brave Search API. Key fallback, retry with backoff, circuit breaker, caching, content extraction, SSRF protection. Python 3.8+, stdlib-only.
version: 2.0.0
metadata:
  openclaw:
    requires: { env: [BRAVE_API_KEY], bins: [python3] }
    primaryEnv: BRAVE_API_KEY
    os: [linux, macos]
---

# brave

> **Bundled inside the Search Router** at `~/.agents/skills/websearch/`. This reference is not independently discoverable. Invoke `python ~/.agents/skills/websearch/scripts/search.py --query "<query>" --mode web` (or an explicitly selected specialist mode); `ROUTES` owns provider selection.

Web, image, video, news, suggest, and local search via `https://api.search.brave.com/res/v1/`. Python 3.8+, stdlib-only, no external dependencies. No browser required.

## When to Use

- Web research, documentation lookup, API references
- News search (latest headlines, breaking news, current events)
- Image search (finding photos, diagrams, visual references)
- Video search (tutorials, talks, demos with duration and creator info)
- Local/places search (businesses, restaurants, services near a location)
- Autocomplete/suggest (query expansion, related search ideas)
- Looking up facts or current information
- Fetching content from specific URLs via `--content` (inline extraction without a separate webfetch call)
- Any task requiring web search without interactive browsing
- Second general `web` candidate (`brave:web`), after standalone Codex, selected by the shared Search Router's `ROUTES` table

## Environment variables

| Variable                | Required | Description                                                    |
|-------------------------|----------|----------------------------------------------------------------|
| `BRAVE_API_KEY`         | **yes**  | API key(s) for Brave Search; comma-separated for multi-key fallback (`"k1,k2,k3"`) |
| `BRAVE_API_KEY_1..3`    | fallback | Individual key env vars; used only if `BRAVE_API_KEY` is empty |
| `BRAVE_SEARCH_ENDPOINT` | no       | Override API URL (blocked unless `--allow-custom-endpoint`)    |

## Usage

```bash
# Basic web search (JSON output)
python3 {baseDir}/scripts/brave_search.py --query "OpenClaw" --count 5

# Web search with locale filters
python3 {baseDir}/scripts/brave_search.py --query "site:github.com openclaw" --country US --search-lang en

# News search with freshness filter (last 24 hours)
python3 {baseDir}/scripts/brave_search.py --query "AI news" --type news --freshness pd --count 10

# Image search
python3 {baseDir}/scripts/brave_search.py --query "sunset photos" --type images --count 20

# Video search
python3 {baseDir}/scripts/brave_search.py --query "python tutorial" --type videos --count 10

# Autocomplete / suggest
python3 {baseDir}/scripts/brave_search.py --query "how to" --type suggest --count 5

# Local search with AI descriptions
python3 {baseDir}/scripts/brave_search.py --query "pizza near me" --type local --local-descriptions

# Content extraction (top 3 web results)
python3 {baseDir}/scripts/brave_search.py --query "OpenClaw" --count 5 --content --content-top 3

# Content extraction with news search
python3 {baseDir}/scripts/brave_search.py --query "AI breakthroughs" --type news --content --content-top 3

# Cache + retry configuration
python3 {baseDir}/scripts/brave_search.py --query "OpenClaw" --retry-attempts 3 --cache-ttl-seconds 120

# Debug diagnostics (no secrets printed)
python3 {baseDir}/scripts/brave_search.py --query "OpenClaw" --debug

# Raw API payload included in output
python3 {baseDir}/scripts/brave_search.py --query "OpenClaw" --raw

# Cache maintenance (no API call)
python3 {baseDir}/scripts/brave_search.py --cache-clear-all-only

# Extra snippets + safesearch + goggles
python3 {baseDir}/scripts/brave_search.py --query "climate change" --extra-snippets --safesearch strict --goggles "https://raw.githubusercontent.com/user/goggles/main/filter.goggle"
```

## Output schema

Always JSON. Required top-level keys:

| Key              | Type              | Description                                       |
|------------------|-------------------|---------------------------------------------------|
| `provider`       | `string`          | Always `"brave"`                                  |
| `query`          | `string`          | The search query sent                             |
| `type`           | `string`          | Endpoint used: `"web"`, `"images"`, `"videos"`, `"news"`, `"suggest"`, or `"local"` |
| `used_key_index` | `int \| null`     | Index (0-based) of the key that succeeded         |
| `results`        | `array<object>`   | Search results (see per-type tables below)        |
| `error`          | `string \| null`  | Error message, or `null` on success               |
| `http_status`    | `int \| null`     | HTTP status from Brave API                        |
| `error_code`     | `string \| null`  | Machine-readable error code                       |
| `rate_limit`     | `object \| null`  | Parsed rate-limit headers                         |
| `meta`           | `object`          | `duration_ms`, `attempted_key_indexes`, `version`, `endpoint`, and optionally `debug` |

Additional top-level keys (present when applicable):
- `raw_response` (only with `--raw`, truncated if >100KB)
- `circuit_breakers` (array of per-key state)
- `cache` (hit/miss, ttl, cleared counts)

### Web results

| Key               | Type                | Description                                  |
|-------------------|---------------------|----------------------------------------------|
| `title`           | `string`            | Page title                                   |
| `url`             | `string`            | Page URL                                     |
| `description`     | `string`            | Snippet / description                        |
| `age`             | `string \| null`    | Relative age (e.g., "2 hours ago")           |
| `extra_snippets`  | `array<string>`     | Additional snippets (with `--extra-snippets`) |
| `position`        | `int`               | Result rank (1-based)                        |
| `content_excerpt` | `string \| null`    | Extracted text (only with `--content`)       |
| `content_error`   | `string \| null`    | Per-URL error (only with `--content`)        |

### Image results

| Key         | Type             | Description                   |
|-------------|------------------|-------------------------------|
| `title`     | `string`         | Image title                   |
| `url`       | `string`         | Page URL                      |
| `image_url` | `string`         | Direct image URL              |
| `source`    | `string`         | Source website                 |
| `thumbnail` | `string`         | Thumbnail URL                 |
| `width`     | `int \| null`    | Image width in pixels         |
| `height`    | `int \| null`    | Image height in pixels        |
| `position`  | `int`            | Result rank (1-based)         |

### Video results

| Key          | Type             | Description                   |
|--------------|------------------|-------------------------------|
| `title`      | `string`         | Video title                   |
| `url`        | `string`         | Video URL                     |
| `description`| `string`         | Video snippet/description     |
| `age`        | `string \| null` | Relative age                  |
| `duration`   | `string \| null` | Video duration                |
| `views`      | `string \| null` | View count                    |
| `creator`    | `string \| null` | Content creator name          |
| `publisher`  | `string \| null` | Publisher/platform            |
| `position`   | `int`            | Result rank (1-based)         |

### News results

| Key               | Type             | Description                                  |
|-------------------|------------------|----------------------------------------------|
| `title`           | `string`         | Article title                                |
| `url`             | `string`         | Article URL                                  |
| `description`     | `string`         | Article snippet                              |
| `age`             | `string \| null` | Relative age (e.g., "3 hours ago")           |
| `page_age`        | `string \| null` | Absolute page age from Brave                 |
| `source`          | `string \| null` | News source name                             |
| `breaking`        | `bool`           | Whether flagged as breaking news             |
| `position`        | `int`            | Result rank (1-based)                        |
| `content_excerpt` | `string \| null` | Extracted text (only with `--content`)       |
| `content_error`   | `string \| null` | Per-URL error (only with `--content`)        |

### Suggest results

| Key        | Type     | Description                    |
|------------|----------|--------------------------------|
| `query`    | `string` | Suggested search query         |
| `position` | `int`    | Suggestion rank (1-based)      |

### Local results

| Key            | Type                  | Description                          |
|----------------|-----------------------|--------------------------------------|
| `title`        | `string`              | Business/place name                  |
| `url`          | `string \| null`      | Business website URL                 |
| `address`      | `string \| null`      | Street address                       |
| `phone`        | `string \| null`      | Phone number                         |
| `rating`       | `float \| null`       | Average rating                       |
| `rating_count` | `int \| null`         | Number of reviews                    |
| `price_range`  | `string \| null`      | Price indicator (e.g., "$$")         |
| `categories`   | `array<string>`       | Business categories                  |
| `description`  | `string \| null`      | AI description (with `--local-descriptions`) |
| `position`     | `int`                 | Result rank (1-based)                |

## Error codes

| Code                 | Meaning                                             |
|----------------------|-----------------------------------------------------|
| `missing_query`      | `--query` not provided or empty                     |
| `missing_api_keys`   | No API keys found in environment                    |
| `query_too_long`     | Query exceeds 2000 characters                       |
| `endpoint_blocked`   | Custom endpoint rejected (SSRF protection)          |
| `auth_error`         | 401/403 from Brave API                              |
| `quota_exceeded`     | 402 or quota-related error                          |
| `rate_limited`       | 429 Too Many Requests                               |
| `server_error`       | 5xx from Brave API                                  |
| `network_error`      | Connection/timeout failure                          |
| `client_error`       | Other 4xx                                           |
| `circuit_open`       | Key skipped due to open circuit breaker             |
| `internal_error`     | Unexpected exception (stack trace only w/ `--debug`) |
| `no_local_results`   | Local search found no location results in step 1    |
| `local_pois_failed`  | Local POI lookup (step 2) failed after locations were found |

## Exit codes

| Code | Meaning                              |
|------|--------------------------------------|
| `0`  | Success                              |
| `1`  | API/network error (all keys failed)  |
| `2`  | Input/config validation error        |

## Flags

**Search:** `--query` / `-q`, `--type` / `-t` (web, images, videos, news, suggest, local; default: web), `--count` / `-n` (clamped per type: web 1-20, images 1-100, videos/news 1-50, suggest 1-20, local 1-20), `--offset` (>=0, not for suggest), `--country`, `--search-lang`, `--ui-lang`, `--timeout`

**Content:** `--content` (web and news types only; ignored for others), `--content-top`, `--content-concurrency`, `--content-timeout`, `--content-max-chars`

**Retry/CB:** `--retry-attempts`, `--retry-base-delay-ms`, `--retry-max-delay-ms`, `--cb-failure-threshold`, `--cb-reset-timeout-ms`

**Cache:** `--cache-ttl-seconds`, `--cache-dir`, `--cache-clear`, `--cache-clear-all`, `--cache-clear-all-only`, `--cache-prune-expired`, `--cache-max-entries`

**Auth:** `--allow-custom-endpoint` (permit non-Brave `BRAVE_SEARCH_ENDPOINT`)

**Safety:** `--debug`, `--raw` (truncated at 100KB), `--safesearch` (off/moderate/strict), `--freshness` (pd/pw/pm/py or date range), `--extra-snippets` (web/news, Data for AI plan), `--goggles` (web/news), `--result-filter` (web only), `--local-descriptions` (local only)

## Credit costs

All endpoints are available on the Brave Search Free plan for basic usage. Image, video, news, and suggest endpoints may require a paid plan for higher volume. Local POI/descriptions endpoints require the Data for AI plan. The `--extra-snippets` flag also requires the Data for AI plan. Actual key usage is tracked via the `used_key_index` field and key fallback is automatic.

## Example output (success)

```json
{
  "provider": "brave",
  "query": "OpenClaw",
  "type": "web",
  "used_key_index": 0,
  "results": [
    {"title": "OpenClaw GitHub", "url": "https://github.com/openclaw", "description": "...", "age": "2 days ago", "extra_snippets": [], "position": 1}
  ],
  "error": null,
  "http_status": 200,
  "error_code": null,
  "rate_limit": null,
  "meta": {"duration_ms": 342, "attempted_key_indexes": [0], "version": "2.0.0", "endpoint": "/res/v1/web/search"},
  "circuit_breakers": [{"index": 0, "state": "closed", "failures": 0}],
  "cache": {"hit": false, "ttl_seconds": 0, "cleared": false, "cleared_all": 0}
}
```

## Example output (error)

```json
{
  "provider": "brave",
  "query": "OpenClaw",
  "type": "web",
  "used_key_index": null,
  "results": [],
  "error": "missing_api_keys: set BRAVE_API_KEY or BRAVE_API_KEY_1..3",
  "http_status": null,
  "error_code": "missing_api_keys",
  "rate_limit": null,
  "meta": {"duration_ms": 1}
}
```

## Security notes

- **Never commit `.env`** -- add it to `.gitignore`.
- API keys are only sent in the `X-Subscription-Token` header; they never appear in stdout, stderr, or logs.
- `BRAVE_SEARCH_ENDPOINT` is blocked from pointing to non-Brave URLs unless `--allow-custom-endpoint` is explicitly passed (SSRF protection).
- **Content extraction SSRF guards**: When `--content` is used, all URLs are validated before fetching -- hostnames are resolved via DNS and checked against a blocklist of 12 private/reserved IP ranges (RFC-1918 `10/8`, `172.16/12`, `192.168/16`; loopback `127/8`; link-local `169.254/16`; unspecified `0/8`; carrier-grade NAT `100.64/10`; benchmarking `198.18/15`; reserved `240/4`; IPv6 loopback `::1/128`, unique-local `fc00::/7`, link-local `fe80::/10`). IPv4-mapped IPv6 addresses (e.g., `::ffff:127.0.0.1`) are normalized before checking. Only `http://` and `https://` schemes are allowed. Raw HTML is capped at 10MB per URL.
- `--raw` output is truncated at 100KB and never includes request headers or API keys.
- `--debug` adds sanitized diagnostics (key count, not key values) to `meta.debug`.
- Unhandled exceptions produce stable JSON `{"error": "internal_error: ..."}` without stack traces (unless `--debug`).
- `Retry-After` is capped at 60 seconds to prevent denial-of-service via sleep.
- Pure stdlib Python -- only `urllib.request` for HTTP, no third-party dependencies.

## Source tests

Selected offline command/subprocess/credential-inheritance consumer tests live in `websearch/tests/test_search_workflows.py` in the source repository. Provider-local live smoke scripts are not bundled. Do not invent an installed `tests/` path or contact a live API as a portability check.
