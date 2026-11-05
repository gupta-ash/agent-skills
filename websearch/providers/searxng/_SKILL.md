---
name: searxng
description: Search the web using a self-hosted SearXNG metasearch engine. Aggregates Google, Brave, DuckDuckGo, and more without API keys.
homepage: https://docs.searxng.org
metadata: {"moltbot":{"emoji":"🔍","requires":{"bins":["python3"],"env":["SEARXNG_URL"]}}}
---

# SearXNG provider reference

Bundled inside the installed `~/.agents/skills/websearch/`; this `_SKILL.md` is a reference, not a separately discoverable skill. Ordinary portable web uses the router once: standalone Codex → Brave web → Tavily general → Exa auto → SearXNG. A successful raw result, including empty results, stops; SearXNG is reached only after genuine earlier errors.

```text
<PY> -B <HOME>/.agents/skills/websearch/scripts/search.py --query "Q" --mode web --count 5 --timeout 30
# Only when SearXNG is explicitly selected/required:
<PY> -B <HOME>/.agents/skills/websearch/scripts/search.py --query "Q" --mode web --force-provider searxng --count 5 --timeout 30
```

Retain an explicitly requested supported count (1–20). The active provider-root `searxng_search.py` requires the user's configured `SEARXNG_URL` base endpoint, sends one `/search` query with `format=json`, uses a fixed 30-second HTTP timeout and returns title/URL/description/engines/score. It has no Wikipedia/GitHub fallback. The router emits its normalized raw envelope (`synthesized:false`, null synthesis fields). See [usage](references/usage.md) for active CLI boundaries.

Missing configuration returns a genuine error before HTTP. Do not invent an endpoint, modify environment, install/restart a service or probe unrelated services to make search work. No API key is required by this client, but upstream engines can deny/limit requests.

Router `--domains` uses `site:` query syntax, not strict enforcement; no freshness parameter is forwarded. Check visible URLs/dates and retain unsupported-filter warnings. Choose specialist modes for news/finance/etc. rather than silently replacing them with general web.

For an answer, the top-level workflow permits one separate existing small/task-model summary of frozen supplied snippets only, no second search/fetch/tools; label it separately and never claim pages were read. Empty means no matching results. If summary fails, return raw without retrieval replay. Copilot search/fetch/synthesis (`copilot-search`) is **FAILED / DISABLED**: `Copilot disabled: reliable search execution is not qualified (search_not_executed); diagnostics deferred.` No environment/model/direct-bypass or diagnostic flag enables it; Copilot diagnostics require a future approved source/instruction/deployment change. Explicit non-Copilot provider overrides remain available; SearXNG is not a synthesis fallback.