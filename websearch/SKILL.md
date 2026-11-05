---
name: websearch
description: |
  Portable raw-first web search with retained specialist routes and one optional
  evidence-only harness summary; Copilot search/synthesis is FAILED / DISABLED.
  TRIGGER: 'search X', 'look up Y', 'find articles',
  'latest news', 'current price', 'research topic', or facts needing fresh data.
  Modes: web | research | deep | synthesis | news | tech-news | finance | company
  | filings | people | local | currency | social | similar.
  Invocation: python -B ~/.agents/skills/websearch/scripts/search.py --query "..." --mode web --count 5
---

# Raw retrieval first

Use the declared target Python and installed `~/.agents/skills/websearch/scripts/search.py`, not source/staging. Run the portable router once:

```text
<PY> -B <HOME>/.agents/skills/websearch/scripts/search.py --query "Q" --mode web --count 5 --timeout 15
```

Keep an explicitly requested supported count (1–20); otherwise use 5. General web tries standalone Codex → Brave web → Tavily general → Exa auto → SearXNG. Advance only on a real provider/operational/schema error, not to improve relevance. Successful `results:[]` is terminal: report no matching results. Retain provider, fallback trail and filter warnings; missing/disabled providers are not successful searches. Do not replay an unknown-outcome request automatically. Native Public/Google are explicit-only OMP routes, never automatic fallbacks.

Select the existing specialist mode for news, finance, tech-news, company, filings, people, research, local, currency, social, similar or deep. Their twelve chains and endpoint/category/date/domain translations remain distinct; `research` is not a way to bypass general web priority. `similar` takes the source URL as `--query`. Finance/currency results are search/news, not licensed quote feeds. Explicit non-Copilot provider overrides remain available; Serper and Anthropic are not automatic general-web substitutes.

Use `--freshness hour|day|week|month|year` and `--domains a.example,b.example` where supported. Codex/Brave/Tavily round hour to day; Serper supports hour and defaults its news freshness to week for time-sensitive modes. Exa uses an ISO start date (hour rounds to day). Domain filtering is a provider parameter or `site:` query syntax, not proof of strict enforcement. SearXNG has a fixed 30-second HTTP timeout and no date filter; synthesis overrides do not support these filters. Retain unsupported/relaxed-constraint warnings and check visible dates/URLs. Do not configure endpoints, install tools or change authentication to complete a search.

# One optional summary, no second retrieval

For links/raw JSON only, return the evidence without synthesis. If the user asks for an answer/summary and results are nonempty, freeze the raw envelope and use **one existing small/task-model operation in this harness**. Give it the original question/constraints, numbered titles, supplied URLs, snippets, dates and warnings. Instruct it to answer only from that evidence, cite only supplied URLs, preserve numbers/dates/qualifiers, identify disagreements and state when snippets cannot answer. Do not permit search, fetch, browsing or external tools in that operation. Use an existing tool-free facility where available; an ordinary task is not tool-free merely because `tools:[]` was passed. No model installation or new inference client is required.

Label the separate answer **summary of retrieved snippets** and identify its actual model when exposed. Keep raw `synthesized:false`, `text:null`, `candidate_sources:null` and `pages_read:null` unchanged; the summary is not provider synthesis. Never claim pages were read. Empty retrieval gets no model answer from memory. If the summary facility fails/is unavailable, return retained raw evidence and label summary unavailable; do not search again or fetch pages.

# Explicit provider synthesis

**FAILED / DISABLED:** `copilot-search` and its fetch/synthesis uses refuse in code, not just instructions:

> Copilot disabled: reliable search execution is not qualified (search_not_executed); diagnostics deferred.

Portable explicit provider synthesis is unavailable. Offer the raw workflow separately; do not silently retrieve, substitute another provider/model, simulate a failed call, or replay an unknown outcome. Router synthesis and forced Copilot return `all_disabled` with the nested `disabled` reason before command construction. The direct API/CLI returns `disabled` before model/transport selection, cache, token, catalog or inference work; model pins, environment variables and direct-bypass flags cannot enable it. `--explain-routing` reports the empty active chain and disabled/skipped entries without execution.

Diagnostics require a separately approved source/instruction change, not a current CLI exception. OMP may retain an explicitly requested supported native Codex synthesis option when available, never as a fetch tier or portable substitute. This disablement does **not** disable unrelated GitHub Copilot model-hosting providers or change model roles. See the [provider reference](providers/copilot-search/README.md).
