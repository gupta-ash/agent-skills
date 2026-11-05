# copilot-search — disabled

Bundled reference for [websearch](../../SKILL.md), not a separately discoverable skill.

> Copilot disabled: reliable search execution is not qualified (search_not_executed); diagnostics deferred.

## Public behavior

```python
copilot_search(query, context_size="medium", max_results=10,
               format="json", cache=True, *, model=None)
```

The API signature and CLI arguments remain callable only for explicit refusal. The function returns before input/model/transport environment selection, `SearchCache` construction, token access, catalog requests, cache lookup or inference. No pin, environment setting, direct-bypass flag or diagnostic exception enables it.

The shaped error has `error_code:"disabled"`, empty `text`, `results`, `candidate_sources`, and `pages_read`, zero completed tool/search calls, zero auth retries/transport fallbacks, `cached:false`, and null model/transport/status/upstream metadata. JSON, markdown and both formats preserve the reason. CLI exits 1 in every format:

```text
python -B providers/copilot-search/search.py --query "synthetic fixture" --format json
```

Router synthesis/forced Copilot returns outer `all_disabled` and nested `disabled` before command construction. Explanation still shows skipped/disabled entries. Shared webfetch independently refuses explicit Copilot selection and rejects synthesized/Copilot cache rows; no direct URL-fetch compatibility path remains. Unrelated GitHub Copilot hosting/model roles are unaffected. Offer raw retrieval separately, without automatically substituting another provider, model or cached synthesis.

## Retained inactive implementation

Catalog/model selection, scoped caching, response parsing and classified transport helpers remain in source for a future separately approved diagnostic change. They are **not a callable bypass**. Current API refusal does not read/create provider state or use the inactive `COPILOT_SEARCH_MODEL`, `COPILOT_SEARCH_BYPASS_PROXY`, or `COPILOT_PROXY_URL` knobs. No legacy environment aliases are supported.

The parser distinguishes completed search calls from open-page calls, usable citations from candidate URLs, and actual selected model/transport from guessed defaults. Router query proof requires nonempty text, a positive integer completed search count and a usable HTTP(S) citation; bare prose/open-only output cannot become successful search. Additional error provenance is limited to typed model/transport/HTTP status/upstream machine fields, never raw bodies, headers or credentials. Unknown-outcome inference is not automatically replayed.

Inactive transport/cache helpers use synthetic state and injected requests in the selected source tests. Existing account state is not a fixture or a redistribution input. Source does not authorize token refresh, proxy startup, credential changes or diagnostic calls. Subscription access is not a promise of free/unlimited operation.

## Source tests

From the repository root, the integrator may run the isolated selected suites after assembly:

```text
python -B -m pytest websearch/tests/test_copilot_search.py websearch/tests/test_search_workflows.py
```

These exercise API/CLI refusal before side-effect boundaries and retained parsing without enabling transport. Provider-local live smoke/fallback scripts are not included or required. No test or live-operation result is claimed by this documentation.
