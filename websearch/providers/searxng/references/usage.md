# SearXNG active-client usage

## Installed entrypoint

Use the declared target Python and installed shared router. Ordinary `--mode web` runs the portable chain once (standalone Codex → Brave web → Tavily general → Exa auto → SearXNG); never replay earlier selectors after a successful response. To select SearXNG explicitly:

```text
<PY> -B <HOME>/.agents/skills/websearch/scripts/search.py --query "Python TaskGroup documentation" --mode web --force-provider searxng --count 5 --timeout 30
```

Use count 5 unless the user explicitly requests another supported count (1–20). The active direct script is `<HOME>/.agents/skills/websearch/providers/searxng/searxng_search.py "Q" --count 5 [--lang CODE]`. It always outputs JSON, with no `--json` flag or configurable HTTP timeout. No alternate client or service installer is bundled.

## Configuration and requests

The active client requires the user's configured `SEARXNG_URL`; it trims a trailing slash or `/search` and sends a single GET to `/search?q=...&format=json` (plus `language` when requested) with a fixed 30-second HTTP timeout. Missing configuration errors before HTTP. The router watchdog is `--timeout` plus its existing ten-second buffer; use 30 for explicit SearXNG to cover the client's fixed timeout. The router passes the positional query/count, not `--lang`.

Client configuration, service availability and upstream engine access are separate prerequisites. Do not invent LAN routes, alter environment or provision/restart a service as a search fallback.

## Results and failures

Direct JSON is `{query, count, results:[{title,url,description,engines,score}]}`. The router normalizes description to snippet, retains provider/fallback provenance, and keeps `synthesized:false` with `text`, `candidate_sources` and `pages_read` null. A valid empty array is terminal no-match; HTTP/transport/JSON/configuration errors remain errors. There is **no** automatic Wikipedia/GitHub fallback in this active client and no guarantee that upstream Google/other engines work. Upstream restrictions/rate limits can reduce availability.

`--domains` becomes `site:` query syntax. No date/freshness filter is forwarded to SearXNG, and query syntax alone is not proof of strict filtering; inspect visible dates/URLs and retain warnings. Keep the requested specialist mode instead of assuming SearXNG is a news or financial quote feed.

## Separate evidence-only summary

Follow the top-level search workflow: links/raw requests need no synthesis. For a requested answer over nonempty results, one existing harness small/task-model operation may use only the frozen question/constraints, numbered titles/URLs/snippets/dates and warnings, with no search/fetch/browsing/tools. Cite supplied URLs only, preserve qualifiers/numbers/dates and identify evidence limits. Label the answer “summary of retrieved snippets” and actual model when exposed; never claim pages were read. Empty gets no memory answer; summary failure returns raw without another search. Copilot search/fetch/synthesis (`copilot-search`) is **FAILED / DISABLED**: `Copilot disabled: reliable search execution is not qualified (search_not_executed); diagnostics deferred.` No environment/model/direct-bypass or diagnostic flag enables it; Copilot diagnostics require a future approved source/instruction/deployment change. Explicit non-Copilot provider overrides remain available.

## Attribution

The active root client derives from **stperic's [local-websearch v1.0.0](https://clawhub.ai/stperic/skills/local-websearch)**, listed as **MIT-0** by its primary distribution. The separate earlier SearXNG resource is attributed to **Avinash Venkatswamy**, [searxng v1.0.3](https://clawhub.ai/abk234/skills/searxng), also listed as **MIT-0**; its rich support client and installer are not this active entrypoint and are not bundled. Repository LICENSE/NOTICE preserve the first-party/third-party distinction.

ClawHub's [publishing policy](https://github.com/openclaw/clawhub/blob/4c53ec1dba1c9fa32c0d911964ccd291fe6b823b/docs/cli.md) explicitly states that publishing a skill releases it under MIT-0 and permits use, modification and redistribution without attribution. This is the licensing basis beyond the UI label; a null legacy API `version.license` field is not represented as an explicit per-version declaration. [MIT-0 operative terms](https://spdx.org/licenses/MIT-0.html) apply to the attributed material; first-party repository code has its separate license.

