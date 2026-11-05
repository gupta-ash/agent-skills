# Codex standalone query search

An independently authored Python 3.10+ standard-library client for the observed Codex `alpha/search` protocol. No OMP, Pi, Bun, API-key manager or third-party module is required. This is raw query search, not synthesized inference or arbitrary-URL fetching; session-dependent open/find/click operations are not implemented.

## Invocation

Use the installed shared skill paths (with the target's Python executable and home):

```text
python -B ~/.agents/skills/websearch/scripts/search.py --query "release notes" --mode web --domains example.org --freshness week --count 5 --timeout 15
python -B ~/.agents/skills/websearch/providers/codex-search/search.py --query "release notes" --max-results 5 --timeout 15 --recency-days 7 --domains example.org,example.com
```

The portable general `web` chain is Codex standalone → Brave web → Tavily general → Exa auto → SearXNG. The specialist `research` chain remains Brave web → Exa neural → Serper search → Codex standalone → SearXNG, so Codex is penultimate there. Codex participates only in `web` and `research`; other specialist routes retain their existing semantics. Router freshness maps hour/day to one day (hour is an approximation), week to 7, month to 30, year to 365. The subprocess watchdog remains active in addition to the HTTP timeout. Use count 5 normally; an explicitly requested supported count is retained.

Ordinary retrieval is raw and never automatically invokes provider synthesis. Copilot search/fetch/synthesis (`copilot-search`) is **FAILED / DISABLED**: `Copilot disabled: reliable search execution is not qualified (search_not_executed); diagnostics deferred.` No environment/model/direct-bypass or diagnostic flag enables it; Copilot diagnostics require a future approved source/instruction/deployment change. Offer raw search instead, with no automatic Anthropic or Brave fallback; explicit non-Copilot provider overrides remain available.

For a requested answer, follow the [shared workflow](../../SKILL.md): one harness small/task-model summary over frozen evidence only, with no new retrieval or tools. Keep the raw envelope unchanged; empty results get no fabricated answer and summary failure returns raw evidence.

Python API:

```python
codex_search(query: str, max_results: int = 10,
                 recency_days: int | None = None,
                 domains: list[str] | None = None,
                 timeout: float = 30.0) -> dict
```

`query` must be nonblank; result count is 1–20; timeout is positive and finite; optional recency is a positive integer. Domains, when provided, are nonblank strings. Omitted recency/domains are omitted on the wire. Count is local ordered truncation, not a request field.

## Authentication and limits

A nonempty `CODEX_ACCESS_TOKEN` takes precedence, with only its accompanying optional `CODEX_ACCOUNT_ID`. Otherwise the client reads `~/.codex/auth.json`, using `tokens.access_token` and optional `tokens.account_id`. It never mixes an environment token with a file account, uses `OPENAI_API_KEY`, loads project `.env` or OMP storage, or copies/rewrites credentials. Missing, unreadable or malformed login data fails before sending a request. Report normal Codex CLI authentication as a prerequisite; do not run login, refresh credentials or change configuration automatically during the search workflow.

Exactly one UUID and one POST to `https://chatgpt.com/backend-api/codex/alpha/search` per authenticated call; redirects are refused without forwarding authorization. Headers use bearer authorization, JSON content type, `codex-cli/0.147.0-alpha.6.5`, and optional account ID. The protocol body has `id`, `model: "gpt-4o"`, and `commands` containing `search_query` (`q`, optional `recency`/`domains`) and `response_length: "short"`. The model literal is a protocol field, not evidence of billed inference. No retries, refresh, alternative endpoint, credential store, result cache or persisted references.

Account entitlement, endpoint stability, quotas, quality, permanent free access and zero account charges are not guaranteed by this client. Authentication success does not imply search entitlement.

## Output and errors

Success is `{"query": "…", "results": [{"title": null, "url": "https://example.org/", "snippet": null, "position": 1}], "meta": {"duration_ms": 0}, "error": null, "error_code": null}`. Titles/snippets may be strings or null. Order is retained. The router preserves useful snippets/URLs and sets `synthesized:false`, `text:null`, `candidate_sources:null`, `pages_read:null`.

The response must be an object with an explicit `results` array. Every row, including rows beyond the requested count, must have a usable HTTP(S) URL; optional title/snippet/domain/type/ref_id must be strings or null. Unknown fields are ignored. Output-only bodies, malformed rows and unusable URLs are `parse_error`, not fabricated no-match. Explicit `results:[]` is success and stops router fallback.

Failures retain the envelope with empty results and sanitized actionable errors, never response bodies, headers or credential values. CLI exit 2: `invalid_input` (including argument errors). CLI exit 1: 401/login failure `auth_error`, 403 `forbidden`, 429 `rate_limited`, other HTTP/redirect `http_error`, `timeout`, `network_error`, invalid JSON/schema `parse_error`. Success exits 0.

## Protocol attribution

Protocol reference: [mateusdcc/pi-gpt-search](https://github.com/mateusdcc/pi-gpt-search/tree/7b0fe64cee8c3d4c8e7b0bc053615145beda69e0), revision `7b0fe64cee8c3d4c8e7b0bc053615145beda69e0`, author **Mateus DCC**, package-declared **MIT**. See its [transport](https://github.com/mateusdcc/pi-gpt-search/blob/7b0fe64cee8c3d4c8e7b0bc053615145beda69e0/src/codex-provider.ts) and [commands](https://github.com/mateusdcc/pi-gpt-search/blob/7b0fe64cee8c3d4c8e7b0bc053615145beda69e0/src/commands.ts). The referenced tree has no upstream LICENSE/full copyright notice; none is invented or claimed retained here. This implementation independently follows the observed wire protocol, rather than vendoring or translating upstream source.

## Offline regression suite

From the skill directory: `python -I -S -B -m unittest discover -s tests -p test_codex_search.py`. Tests use disposable auth and mocked transport only; they do not read real credentials, call the endpoint or exercise account entitlement. `_SKILL.md` is a bundled provider reference, not a second discoverable skill.
