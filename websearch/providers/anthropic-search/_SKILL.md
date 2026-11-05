---
name: anthropic-search
description: Retained Anthropic-native `web_search_20250305` provider reference (claude-haiku-4-5) for explicitly requested diagnostic/provider use, not ordinary search summaries or automatic fallback. Returns structured JSON with `results[]` (URL list with title + age), `text` (model's synthesized answer with inline citations), and `tool_calls_made` (count of server-side searches). Caches identical queries 60s. Stdlib-only Python; no external deps. Requires account-authorized ANTHROPIC_API_KEY access; no free/unlimited entitlement or R1 qualification is implied.
---

# anthropic-search — Anthropic-native web search

> **Bundled inside the Search Router** at `~/.agents/skills/websearch/`. This provider reference is not auto-discoverable. For explicitly requested Anthropic diagnostic/provider use only: `python -B ~/.agents/skills/websearch/scripts/search.py --query "<query>" --mode synthesis --force-provider anthropic-search --count 5`. Copilot search/fetch/synthesis (`copilot-search`) is **FAILED / DISABLED**: `Copilot disabled: reliable search execution is not qualified (search_not_executed); diagnostics deferred.` No environment/model/direct-bypass or diagnostic flag enables Copilot; Copilot diagnostics require a future approved source/instruction/deployment change. This does not disable the explicit Anthropic override; the shared `SKILL.md` policy overrides historical provider claims.

Only when Anthropic itself is explicitly requested for diagnostic/provider use, invoke the forced provider through the Search Router:

```bash
# Explicit Anthropic diagnostic/provider request; never an automatic fallback:
python -B ~/.agents/skills/websearch/scripts/search.py --query "<query>" --mode synthesis --force-provider anthropic-search --count 5
```

Writes JSON to stdout. Read it directly.

## When to use

- An explicitly requested Anthropic provider diagnostic
- An explicitly requested comparison with another provider, under separate authorization/qualification
- Direct CLI/API use of the retained provider contract below

Use `websearch` raw retrieval for ordinary fresh-data requests. An ordinary answer/summary is one optional harness operation over frozen retrieved snippets, not a reason to select Anthropic. Copilot synthesis is disabled; report that and offer raw search rather than automatically substituting Anthropic.

The unforced `--mode synthesis` route is disabled because it is Copilot-only, and does not select this provider. Use router count 5 unless the user explicitly requests another supported count. Account access, model availability and rate limits are prerequisites, not free/unlimited guarantees.

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
  "results": [{"title": str, "url": str, "snippet": Optional[str], "age": Optional[str]}, ...],
  "text": str,                        # model's synthesized answer
  "tool_calls_made": int,             # number of web_search server-tool invocations
  "elapsed_ms": int,
  "model": str,                       # echo of model used
  "cached": bool,
  "error": Optional[str],
}
```

## Auth

Reads `ANTHROPIC_API_KEY` from environment. Set via:
```cmd
setx ANTHROPIC_API_KEY "sk-ant-api03-..."
```

Requires an account-authorized key from https://console.anthropic.com/settings/keys; access and rate limits depend on the account.

## Errors

| Error | Cause | Fix |
|---|---|---|
| `ANTHROPIC_API_KEY not set` | env var missing in shell at launch | `setx ANTHROPIC_API_KEY <key>` then restart shell |
| `Authentication failed` | invalid or revoked key | check console.anthropic.com |
| `Rate limit` | account rate limit exceeded | follow the account's retry guidance; no plan change is implied |
