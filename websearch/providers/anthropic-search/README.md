# anthropic-search

> **Bundled inside the Search Router** at `~/.agents/skills/websearch/`. This provider reference is not auto-discoverable. For explicitly requested Anthropic diagnostic/provider use only: `python -B ~/.agents/skills/websearch/scripts/search.py --query "<query>" --mode synthesis --force-provider anthropic-search --count 5`. Copilot search/fetch/synthesis (`copilot-search`) is **FAILED / DISABLED**: `Copilot disabled: reliable search execution is not qualified (search_not_executed); diagnostics deferred.` No environment/model/direct-bypass or diagnostic flag enables Copilot; Copilot diagnostics require a future approved source/instruction/deployment change. This does not disable the explicit Anthropic override; shared `SKILL.md` qualification/routing policy overrides historical claims below.

Real-time web search via Anthropic's native `web_search_20250305` server tool. Stdlib-only Python wrapper around `https://api.anthropic.com/v1/messages`. Requires account-authorized `ANTHROPIC_API_KEY` access; no free/unlimited entitlement is implied.

## What

A skill that takes a query and returns Anthropic's web-search-grounded synthesis: structured `results[]` (URL list with title + age), the model's `text` answer, and `tool_calls_made` counter.

This retained **Anthropic-native** provider is available for explicitly requested diagnostic/provider use:

- An Anthropic-specific diagnostic
- An explicitly requested provider comparison, under separate authorization/qualification

It is not the ordinary answer-summary workflow or an automatic fallback for disabled Copilot. Unforced `--mode synthesis` is disabled because it is Copilot-only. For ordinary requests use `websearch` raw retrieval and, if requested, one separate harness summary over frozen snippets; for unavailable provider synthesis, offer raw search instead.

## Install

Location: `~/.agents/skills/websearch/providers/anthropic-search/` (bundled inside Search Router). Auto-discovery is via the Search Router skill, not this provider directly.

Set `ANTHROPIC_API_KEY` once:
```cmd
setx ANTHROPIC_API_KEY "sk-ant-api03-..."
```
Then restart shells.

## Usage

```bash
# Explicit Anthropic diagnostic/provider request through the installed router:
python -B ~/.agents/skills/websearch/scripts/search.py --query "what's the latest stable Python release" --mode synthesis --force-provider anthropic-search --count 5

# Direct provider CLI: markdown output
python -B ~/.agents/skills/websearch/providers/anthropic-search/search.py --query "current Bitcoin price USD" --format markdown

# Direct provider CLI: disable the provider's 60-second cache
python -B ~/.agents/skills/websearch/providers/anthropic-search/search.py --query "..." --no-cache

# Direct provider CLI: set the server-tool search-call budget
python -B ~/.agents/skills/websearch/providers/anthropic-search/search.py --query "compare H100 vs B200 specs" --max-uses 5
```

From Claude Code, Codex or OMP, request the Anthropic diagnostic/provider override explicitly; an ordinary natural-language search or summary request does not select it. Use router count 5 unless the user explicitly requests another supported count. Direct CLI/API access remains available; provider-specific flags below apply to the direct CLI, not automatically to the router.

## Parameters

| Param | Default | Meaning |
|---|---|---|
| `--query` | (required) | The search query |
| `--max-uses` | `5` | Max number of `web_search` server-tool calls the model can make |
| `--model` | `claude-haiku-4-5` | Anthropic model; availability and charges depend on the account. |
| `--format` | `json` | Output: `json` (default), `markdown`, or `both` |
| `--no-cache` | (off) | Disable the 60-second short cache |

## Output schema

```python
{
  "query": str,
  "results": [
    {
      "title": str,
      "url": str,
      "snippet": Optional[str],   # Anthropic doesn't return per-result snippets
      "age": Optional[str],       # e.g. "2 weeks ago" if surfaced
    },
    ...
  ],
  "text": str,                    # model's synthesized answer
  "tool_calls_made": int,         # number of web_search server-tool invocations
  "elapsed_ms": int,
  "model": str,                   # echo of model used (e.g. claude-haiku-4-5-20251001)
  "cached": bool,
  "error": Optional[str],
}
```

## Errors

| Error | Cause | Fix |
|---|---|---|
| `ANTHROPIC_API_KEY not set` | env var missing in shell at launch | `setx ANTHROPIC_API_KEY <key>` then restart shell |
| `Authentication failed (HTTP 401)` | invalid or revoked key | rotate key at https://console.anthropic.com/settings/keys |
| `Rate limit (HTTP 429)` | account rate limit exceeded | follow the account's retry guidance; no plan change is implied |
| `Network error` | DNS / firewall / Anthropic outage | retry; check `curl -v https://api.anthropic.com/v1/messages` |

## Architecture

```
anthropic-search
   │
   ▼ HTTPS POST
api.anthropic.com/v1/messages
   │
   ▼ executes server-side
web_search_20250305 tool
   │
   ▼ returns
content[] = [server_tool_use, web_search_tool_result, text]
```

Direct single-hop. No proxies, no SSH. Windows DNS verified resolving `api.anthropic.com` cleanly (2026-04-29).

## State files

- `~/.local/share/anthropic-search/cache.json` — 60s short cache. Safe to delete.

## Limitations

- Only does web search (no other Anthropic tools).
- **Free-tier rate limit: ~10,000 input tokens/min, ~5 RPM** (verified 2026-04-29 — back-to-back queries trigger 429). Sleep ~60s between calls under free tier, or upgrade.
- Snippets are not provided per-result by Anthropic's API (synthesized text is the substitute).
- Default `claude-haiku-4-5` model — fastest but lower quality than sonnet/opus. Override via `--model`.

## Source tests

Selected offline routing/normalization consumers are in `websearch/tests/test_search_workflows.py` in the source repository. Provider-local live smoke/registry-credential scripts are not bundled or required. Source presence does not authorize an API call.

## Reference

- Anthropic `web_search_20250305` docs: https://docs.anthropic.com/en/docs/build-with-claude/tool-use/web-search-tool
