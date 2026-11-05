---
name: codex-search
description: Raw query search through the observed Codex alpha/search protocol; stdlib-only standalone provider bundled under websearch, without synthesis or URL fetching.
---

# Codex standalone search — bundled reference

This `_SKILL.md` is not a separately discoverable top-level skill. Invoke the installed shared router with the target's Python executable and home:

```text
python -B ~/.agents/skills/websearch/scripts/search.py --query "QUERY" --mode web --force-provider codex-search --count 5 --timeout 15
```

For ordinary general retrieval omit `--force-provider`: portable `web` runs Codex standalone → Brave web → Tavily general → Exa auto → SearXNG. Specialist `research` retains Brave web → Exa neural → Serper search → Codex standalone → SearXNG. Codex is first in `web`, penultimate in `research`, and participates only in these two modes. For constraints add `--domains example.org,example.com --freshness week`. Codex maps hour/day to one day (hour is approximate), week/month/year to 7/30/365 days. Explicit empty results stop fallback; provider errors continue the chain. Raw results retain URLs, snippets and order, not synthesized answers. Use count 5 normally; preserve an explicitly requested supported count.

Raw retrieval never automatically invokes provider synthesis. Copilot search/fetch/synthesis (`copilot-search`) is **FAILED / DISABLED**: `Copilot disabled: reliable search execution is not qualified (search_not_executed); diagnostics deferred.` No environment/model/direct-bypass or diagnostic flag enables it; Copilot diagnostics require a future approved source/instruction/deployment change. Offer raw search instead, with no automatic Anthropic or Brave fallback; explicit non-Copilot provider overrides remain available. For a requested answer follow the [shared workflow](../../SKILL.md): one harness small/task-model summary over frozen evidence only, without new retrieval or tools. Keep raw metadata unchanged; empty results get no fabricated answer and summary failure returns raw evidence.

Authentication: nonempty `CODEX_ACCESS_TOKEN` with only optional `CODEX_ACCOUNT_ID`; otherwise `~/.codex/auth.json` fields `tokens.access_token` and optional `tokens.account_id`. Never combine the sources. On authentication failure report normal Codex CLI authentication as a prerequisite; do not run login, refresh credentials or change configuration automatically during search. No OPENAI_API_KEY, OMP storage, token refresh, writes, retries or cache. Credentials and response bodies must not be printed.

Direct CLI using the installed provider:

```text
python -B ~/.agents/skills/websearch/providers/codex-search/search.py --query "QUERY" --max-results 5 --timeout 15 --recency-days 7 --domains example.org
```

Count 1–20; positive timeout/recency; absent options stay absent on the wire. One redirect-refusing POST, with local ordered truncation. Success emits query/results/meta/error/error_code JSON (exit 0); invalid input exits 2, other sanitized actionable errors exit 1. See [README](README.md) for schema, protocol and error details. This is query-only, not open/find/click or arbitrary-URL fetch. Search entitlement, permanent free access, quality and zero account charges are unproved.

Independently implemented protocol reference: Mateus DCC's [pi-gpt-search](https://github.com/mateusdcc/pi-gpt-search/tree/7b0fe64cee8c3d4c8e7b0bc053615145beda69e0), revision `7b0fe64cee8c3d4c8e7b0bc053615145beda69e0`, package-declared MIT. No upstream LICENSE/full copyright notice exists in that tree; none is invented or claimed retained here.
