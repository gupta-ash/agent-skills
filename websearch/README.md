# websearch

The public search front is [SKILL.md](SKILL.md). It owns one stdlib Python subprocess router, eight bundled provider entrypoints and a shared key-file helper. Provider `_SKILL.md` files are references, not additional discoverable skills. Python 3.10+ supports the complete retained provider population.

## Invocation and routing

```text
python -B scripts/search.py --query "example reference" --mode web --count 5
python -B scripts/search.py --query "example reference" --mode research --freshness week
python -B scripts/search.py --query "fixture" --mode web --explain-routing
python -B scripts/search.py --query "fixture" --mode synthesis --explain-routing
```

Installed invocation uses `<HOME>/.agents/skills/websearch/scripts/search.py`. `--explain-routing` resolves policy without provider command construction or execution; other calls can contact external services. Source availability is not account entitlement or permission to operate a service.

`scripts/search.py` defines `ROUTES`, `SCRIPTS`, builders, subprocess execution and normalized output. Public provider IDs/directories are **`codex-search`**, **`copilot-search`**, and **`anthropic-search`**; their Python API functions are respectively `codex_search`, `copilot_search`, and `anthropic_search`. IDs are not Python identifiers. No legacy provider alias is registered.

| Mode | Ordered selectors |
|---|---|
| `web` | `codex-search`, `brave:web`, `tavily:general`, `exa:auto`, `searxng` |
| `news` | `tavily:news`, `brave:news`, `serper:news`, `searxng` |
| `finance` | `brave:news`, `serper:news`, `tavily:general`, `searxng` |
| `tech-news` | `brave:news`, `tavily:news`, `serper:news`, `searxng` |
| `company` | `brave:web`, `serper:search`, `exa:auto`, `searxng` |
| `filings` | `exa:financial-report`, `serper:search`, `brave:web`, `searxng` |
| `people` | `exa:people`, `brave:web`, `serper:search`, `searxng` |
| `research` | `brave:web`, `exa:neural`, `serper:search`, `codex-search`, `searxng` |
| `local` | `brave:web`, `tavily:general`, `serper:search`, `searxng` |
| `currency` | `brave:news`, `serper:news`, `brave:web`, `searxng` |
| `social` | `exa:tweet`, `brave:news`, `serper:news`, `searxng` |
| `similar` | `exa:similar` |
| `deep` | `tavily:research`, `exa:deep-search` |
| `synthesis` | `copilot-search` — disabled before execution |

Each eligible selector is attempted at most once. A real operational/schema/provider error advances the chain; valid `results:[]` is terminal no-match, not permission to improve relevance by searching again. An unknown outcome is not automatically replayed. Explicit non-Copilot `--force-provider` overrides remain available; Anthropic and Serper are not automatic general-web substitutes. News/finance/currency are search results, not licensed quote feeds.

The router preserves provider, subtype, mode, fallback position/errors and constraint warnings. Raw results remain `synthesized:false` with `text`, `candidate_sources`, and `pages_read` null. A requested answer may use one separate existing harness summary over frozen titles/URLs/snippets/dates only, with no second retrieval or tools; label it “summary of retrieved snippets” and never claim pages were read. Empty results get no answer from memory. Native OMP options are separate from this router.

Freshness and domain constraints are translated, not guaranteed: hour rounds to day for Codex/Brave/Tavily; Serper supports hour and retains default week freshness for time-sensitive news; Exa uses ISO start dates. Brave/Serper/SearXNG receive `site:` query syntax. SearXNG has no freshness parameter and uses a fixed 30-second HTTP timeout. Explicit synthesis overrides do not support date/domain filters. Preserve warnings and inspect returned URLs/dates.

## Credentials and private state

No private registry bridge or environment-loader script is used. Subprocesses inherit the full normal caller environment. `_KEYMANAGER_DIR` remains **bundled `providers/_shared`** and is prepended to child `PYTHONPATH` without discarding the rest of that path. Direct provider entrypoints use the same local helper.

Brave/Tavily/Exa/Serper accept generic `BRAVE_API_KEY`, `TAVILY_API_KEY`, `EXA_API_KEY`, and `SERPER_API_KEY` environment credentials with their retained comma/numbered fallback behavior. File-first key loading and rotation use `~/.config/websearch/keys/{brave,tavily,exa,serper}.json`. Override the directory with router `--key-dir`; a direct provider accepts `--key-file`:

```text
python -B scripts/search.py --query "example" --force-provider brave:web --key-dir "/example/private-keys"
python -B providers/brave/scripts/brave_search.py --query "example" --key-file "/example/private-keys/brave.json"
```

```json
{"active":0,"keys":["synthetic-key-one","synthetic-key-two"],"rotated_at":null}
```

These are synthetic examples, never real credentials. Rotation updates key-file metadata; do not use live key files as fixtures. Missing/invalid files fall back to the provider's normal environment behavior. The public default is home-relative on all supported platforms, not a drive-rooted `/tmp` convention.

`codex-search` uses a nonblank `CODEX_ACCESS_TOKEN` with optional `CODEX_ACCOUNT_ID`, otherwise the normal `~/.codex/auth.json` token/account pair. It never mixes sources, reads OMP credentials, refreshes/login-writes, retries, follows authenticated redirects or caches results. It makes one query-only POST to the observed Codex alpha/search endpoint; endpoint stability, quotas, charges and entitlement are account-dependent.

`anthropic-search` requires `ANTHROPIC_API_KEY` and explicit provider selection. Its direct API may cache for 60 seconds under `~/.local/share/anthropic-search`; the router passes `--no-cache`. Its provider synthesis is not the ordinary evidence-summary path. `searxng` requires explicit `SEARXNG_URL`; no local/LAN default or server is installed. Missing configuration errors before HTTP. Upstream engines can still deny or limit requests.

## Disabled Copilot

Copilot search/fetch/synthesis is disabled in source because reliable search execution is not qualified (`search_not_executed`); diagnostics are deferred. Router synthesis and forced Copilot return outer `all_disabled` and nested `disabled` before command construction. The direct API/CLI returns disabled before model/transport selection, cache construction, token access, catalog requests or inference. Evidence is empty, counts/retries/fallbacks zero, cache false and selection/status/upstream metadata null; CLI exits 1 in every format.

No model, environment or direct-bypass option enables it. Retained internal parsing/catalog/transport algorithms are inactive implementation, not a diagnostic exception or service-start instruction. Offer raw retrieval separately rather than silently substitute a provider, model or cached synthesis. This does not disable unrelated GitHub Copilot hosting/model roles. [The provider reference](providers/copilot-search/README.md) defines the refusal surface.

## Runtime ownership and exports

Keep these relative entrypoints with their references:

- `scripts/search.py` and `SKILL.md`;
- `providers/_shared/keyfile.py`;
- `providers/codex-search/{search.py,_SKILL.md,README.md}`;
- `providers/copilot-search/{search.py,_SKILL.md,README.md}`;
- `providers/anthropic-search/{search.py,_SKILL.md,README.md}`;
- `providers/brave/{scripts/brave_search.py,_SKILL.md,SECURITY.md}`;
- `providers/tavily/{scripts/tavily_search.py,_SKILL.md,SECURITY.md}`;
- `providers/exa/{scripts/exa_search.py,_SKILL.md}`;
- `providers/serper/{scripts/serper.py,_SKILL.md,SECURITY.md}`;
- `providers/searxng/{searxng_search.py,_SKILL.md,references/usage.md,LICENSE,NOTICE}`.

The repository's explicit export declaration is the membership authority. Do not recursively export source tests, authoring prompts/history, old SearXNG support/install scripts, private loaders or duplicate multi-machine trees. OMP's `omp-web` descriptor/reference are owned by `webfetch/harness/omp/omp-web`; shared [webfetch](../webfetch/SKILL.md) owns known-URL extraction/inspection. Changing a provider ID affects routes, script mapping, builders, normalized metadata, forced selectors, tests and references together.

## Attribution and redistribution evidence

Brave/Tavily/Exa/Serper and the shared key helper are first-party authored wrappers covered by the repository's first-party licensing. Their retained OpenClaw metadata, user agents and security notes acknowledge the original integration context; those compatibility labels alone do not establish imported third-party copyright. No provider dependency is silently omitted or replaced for lack of a copied license file.

The independent Codex protocol client acknowledges **Mateus DCC**, [pi-gpt-search](https://github.com/mateusdcc/pi-gpt-search/tree/7b0fe64cee8c3d4c8e7b0bc053615145beda69e0), revision `7b0fe64cee8c3d4c8e7b0bc053615145beda69e0`. Its pinned [package declaration](https://github.com/mateusdcc/pi-gpt-search/blob/7b0fe64cee8c3d4c8e7b0bc053615145beda69e0/package.json) says MIT; the pinned repository tree contains no full LICENSE/copyright notice. This client independently implements the observed protocol rather than vendoring or translating that source. No absent notice is invented.

The active root SearXNG client derives from **stperic**, [local-websearch v1.0.0](https://clawhub.ai/stperic/skills/local-websearch), whose primary listing declares **MIT-0**. Its selected client was matched by the integrating owner to the public `sundial-org/awesome-openclaw-skills` mirror's `skills/searxng-2/searxng_search.py` (SHA-256 `e23e4b3966d5ef8fe8dcd5b040e8a37b84e4f7ad75e8cc8762b9ac1abb649eda`). The separate earlier resource attribution names **Avinash Venkatswamy**, [searxng v1.0.3](https://clawhub.ai/abk234/skills/searxng), also declared **MIT-0** by its primary listing. That resource's rich client/installer is not the selected active root client and is not bundled. Preserve both origins without applying an unrelated repository-wide license or inventing copyright names. Root LICENSE/NOTICE packaging is owned by the integrator.

ClawHub's [publisher-wide license policy](https://github.com/openclaw/clawhub/blob/4c53ec1dba1c9fa32c0d911964ccd291fe6b823b/docs/cli.md) explicitly says publication releases a skill under MIT-0 and permits use, modification and redistribution without attribution. This primary rule, not a UI-default label alone, supplies the registry grant for those published skills. Their legacy API `version.license` fields are null; no explicit authored per-version declaration or copyright name is invented. See the [MIT-0 terms](https://spdx.org/licenses/MIT-0.html).

## Pending integrator checks

The selected suites use synthetic credentials/state and injected network/subprocess boundaries, including meaningful real command/subprocess/normalization consumers. They are not live authentication proof. From the repository root, with disposable HOME/USERPROFILE/cache/temp paths, credentials removed, and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`:

```text
python -B -m pytest websearch/tests/test_codex_search.py websearch/tests/test_copilot_search.py websearch/tests/test_search_workflows.py
```

No tests, builds, linters, formatters, smoke runs, model calls or service launches were executed for this cutover. Do not recursively collect omitted provider-local live smoke scripts or provision services to prove portability.
