# webfetch

Known-URL retrieval with raw content extraction, option-sensitive caching and evidence-gated browser escalation. [SKILL.md](SKILL.md) is the caller contract. Topic discovery belongs to [websearch](../websearch/SKILL.md); explicit OMP browser interaction belongs to its installed native `omp-web` descriptor, not a preliminary fetch or Playwright CLI substitute. That descriptor's source coordinate is `harness/omp/omp-web`; it is packaged separately in native skill roots, not underneath this shared installation.

## Setup and invocation

Use Python 3.10+ and install `trafilatura>=1.6` in that interpreter's environment. PDF text-layer extraction additionally needs `pypdf`. These dependencies are not vendored: missing trafilatura prevents extraction; missing pypdf yields `pdf_extractor_missing` for that transport attempt and allows the remaining cascade. Browser tiers require an already-provisioned supported browser and `playwright-cli` on PATH. Optional `curl_cffi` can provide the existing second HTTP attempt; its absence is not a new failure. Installing this skill does not install a browser or change authentication.

```text
python -B fetch.py --url "https://example.com/reference" --raw-required
python -B fetch.py --url "https://example.com/reference" --format markdown
python -B fetch.py --url "https://example.com/reference" --max-tier http --no-cache
```

Installed invocation uses `<HOME>/.agents/skills/webfetch/fetch.py` and the Python environment containing those dependencies. Paths and credentials are resolved from the user's normal environment, without a private machine registry, environment loader or deployment checkout. Quote paths and URLs for the active shell.

HTML becomes Markdown via trafilatura, PDF text layers via pypdf, and supported JSON/JavaScript/plain-text bodies can be returned verbatim. This is page-derived content, **not a promise of original response bytes**. `--raw-required` is explicit intent; omitting it never enables synthesis.

## Runnable cascade and disabled synthesis

The four runnable tiers are HTTP, Playwright headless, Playwright headed, and raw CDP. HTTP includes its guarded optional second transport attempt, not a second schema tier. Persisted tier indices remain `http=0`, `headless=1`, `headed=2`, reserved `copilot=3`, `cdp=4`.

Copilot search/fetch/synthesis is **disabled in code**: reliable search execution is not qualified (`search_not_executed`); diagnostics are deferred. Explicit Copilot start, ceiling or model selection returns `error_code:"disabled"`, `failure_class:"other"` before URL/DNS/cache work. CLI refusal also precedes operational database/bootstrap/reaping/provider discovery. There is no model, environment or diagnostic bypass. Ordinary fetch never discovers a Copilot script/model, sets effective `allow_synthesis=False` before the cache key/lookup, and rejects cached rows whose tier is Copilot **or** whose synthesized flag is true. Learned/locked Copilot hints fall back to an allowed unwindowed rung, not ungated CDP. Unrelated GitHub Copilot hosting/model roles are unaffected.

Headed browsing is enabled by default and creates a real visible window. `WEBFETCH_DISABLE_HEADED` opts out; the cascade and warmed-session launch guard consult the same predicate. No window placement, hiding, focus management or desktop automation is provided. A process reuses its own session per mode and opens tabs for successive fetches; independent processes have independent sessions.

`--max-tier cdp` is the default ceiling, not permission to enter CDP after an ordinary failure. Actual login evidence, or sticky captcha after headed has had its opportunity, is required. A 404, dead host, rate limit or generic exhaustion alone never licenses CDP. The user must already have started a dedicated Chrome with `--remote-debugging-port=9222`. The skill never launches or terminates that browser, copies its profile/cookies, or adopts unrelated targets. Raw DevTools extraction owns and closes only its own target.

When `next_step` starts with **ASK THE USER**, relay the applicable screened guidance rather than silently retrying around it. The same ask appears in warnings. A running dedicated browser with no site session yields distinct sign-in guidance. Nonhuman errors such as a missing page should be handled by the agent without pulling the user away unnecessarily.

## Truthful result metadata

JSON is the default. Success includes URL, tier, extractor, `synthesized:false`, content, byte count, timing, `from_cache`, warnings, and inspection/action metadata. Rewrites populate `rewritten_from`. Error envelopes include `error`, `next_step` and attempts; diagnosed failures carry `failure_class`, with actual login URL or Retry-After only when observed. Markdown output also prints failure and next-step guidance; it does not turn an error into a blank successful page.

`status` and `status_is_real` travel **together**. An observed origin response uses `status_is_real:true`; browser capture may substitute 200 only with `status_is_real:false`. Never report that manufactured number as the origin's status. Cache replays and exits without a response omit both keys. A usable document with unknown status is distinct from observed success, failed navigation, and a document whose status pairing was withdrawn after replacement.

`TierResult` and the cascade preserve observed redirect/status provenance. Replaced documents, SPA navigation, unrelated frame/subresource events and late events cannot manufacture main-document status. Transport success is not proof of interaction completion or access entitlement.

## Shared inspection and legitimate access

`inspect_response` is the shared pure policy used by the cascade and OMP native browsing. Its CLI consumes one JSON object from **stdin**:

```text
python -B fetch.py --inspect-response
```

This path dispatches before operational state/transport imports and does no fetching, reaping, probing or database work. The installed native `omp-web` procedure defines its exact response/history schema and private subprocess handling (source coordinate: `harness/omp/omp-web/references/cdp.md`, not a shared-installation dependency). `response` and `headed_allowed` are required; unknown/duplicate keys, wrong types, nonfinite numbers and oversized requests fail closed as structured `invalid_input`. A missing extraction dependency yields `inspection_unavailable`, not a permissive fallback. Request size is at most 8 MiB UTF-8 and body at most 4 MiB. Keep captures and inspection buffers private; do not persist response bodies/headers/cookies as public fixtures or receipts.

Policy actions are `accept`, `headless`, `headed`, `probe_cdp`, `cdp`, `ask_user`, `check_access`, and `stop`. Preserve prior history and append each returned observation exactly once. An availability probe reclassifies the identical response with the original prior history; it is not another navigation. Never re-enter an executed tier or replay a started/unknown non-idempotent action.

Manufactured status is not real response evidence. HTTP login/modal/form structure is only an unconfirmed `access_gate_hint`: confirm actual visibility in headless before success/cache, or stop `access_unverified` when the ceiling prevents confirmation. A headerless sign-in document alone cannot authorize authenticated CDP.

Actual HTTP 402 is `payment_required`. A validated visible blocking modal/form or exact explicit user report can be `subscription_required`, including on status 200, after hard/legal-block precedence. Candidate validation requires visibility, a subscription/purchase/member control, **and** an explicit restriction phrase. Generic subscribe text, paid metadata, article mentions or a teaser alone do not prove a wall. Unknown entitlement asks once and waits; denied entitlement stops. Confirmation alone does not erase the wall, authorize purchase/paywall bypass, permit credential autofill, or grant CDP. Collect fresh authorized evidence after a human action. Blocked content must not enter the successful cache or be replaced with model-written text.

Native interactive OMP CDP remains unavailable without a qualified public exact-target attachment capability; the native procedure asks/stops rather than broad-selecting a tab. That limitation does not disable the separate evidence-gated human-started raw extraction tier.

## Public state and concurrency

State is created lazily at `~/.local/share/webfetch/state.db` with SQLite WAL and a busy timeout. Public profiles live at `~/.cache/webfetch/playwright`, with numbered concurrent instances `playwright2` through the configured cap. These are new public namespaces: **no private installation state, profiles, cookies or session records are discovered, migrated or adopted**. Existing saved data is not a reason to redirect the public defaults into another installation's directories.

Tables hold domain blocklist hints, 15-minute raw URL cache, redacted fetch log, path-scoped rewrites and config. `default_blocklist.json` bootstraps rewrites/domain hints. Cache keys separate relevant options and access-policy version; either a synthesized flag or Copilot tier prevents replay. Promotion requires three qualifying same-class failures within one hour; demotion requires five consecutive successes. Cascade IDs prevent concurrent failed rungs from being counted as independent fetches. Disabled and transient outcomes do not promote.

Browser sessions are named `webfetch-headless-<pid>` / `webfetch-headed-<pid>`. The public reaper recognizes only its public session family. Numbered profiles are guarded by OS-held locks beneath the public state root's `locks/`, outside the guarded profiles. Lock files are stable rendezvous objects: **never unlink them or sweep that directory** while users may be active. Unknown listings, missing live-owner directories and detected truncation refuse unsafe claims. Attached sessions do not own numbered profiles.

Startup cleanup reaps only provably dead owner PIDs. Explicit `--reap-zombies` closes/deletes listed public sessions, including live ones: it is an administrative action, not an ordinary fetch retry. Failed close/delete reports incomplete cleanup. A listing truncated exactly on a newline can look like a shorter valid listing, so cleanup counts describe listed records and are not proof that every orphan was found. Persistent profile contents are not removed by session-record cleanup.

## Configuration

Only the public names below are supported; there are no old environment aliases.

| Variable | Meaning |
|---|---|
| `WEBFETCH_DISABLE_HEADED` | `1`, `true`, `yes` opt out of headed, case-insensitive. |
| `WEBFETCH_ALLOW_PRIVATE` | Same truth values permit private/loopback/link-local HTTP targets; default denied. `--allow-private` is the per-call form. |
| `WEBFETCH_CHALLENGE_WAIT` | Challenge settle budget in seconds, default 12; malformed values warn and use the default. |
| `WEBFETCH_PLAYWRIGHT_PROFILE` | Explicit public profile base; relative overrides resolve against import-time cwd. |
| `WEBFETCH_PLAYWRIGHT_PROFILE_MAX` | Integer numbered-profile cap, default 10. |
| `WEBFETCH_PROFILE_SWEEP_BUDGET` | Numeric total profile-claim sweep budget in seconds, default 20. |

Use valid numeric values for the cap and sweep budget; those settings are read at import. `playwright-cli` subprocesses inherit the caller environment, force `NO_UPDATE_NOTIFIER=1`, and run in the public state directory rather than the caller project. An already-selected CLI path and profile/config arguments are absolute. Existing `.playwright` workspace markers in that directory's ancestors can affect upstream session partitioning; this skill does not rewrite those markers.

SSRF defenses admit only HTTP(S), reject credentials in URLs and private/reserved resolved addresses, and revalidate every redirect. `--allow-private` is an explicit trust override, not a default for local services. Sensitive query parameters are redacted from logs, and sensitive URLs do not enter the normal cache. This does not make arbitrary fetched text trusted instructions.

## Ownership, exports and pending checks

Runtime closure is `SKILL.md`, this README, `fetch.py`, `tiers.py`, `extract.py`, `state.py`, `safety.py`, and `default_blocklist.json`, with flat imports/resources preserved. Native OMP additionally consumes only `harness/omp/omp-web/SKILL.md` and `references/cdp.md` in its native skill roots; it is not another Python implementation. The repository's explicit export declaration owns membership. No recursive history/test export, private environment helper, registry bridge or multi-machine duplicate is needed.

Transport changes affect cascade, extraction, safety, state and native inspection consumers; keep strict status and access tests with them. Source-only tests use injected browser/network boundaries and synthetic HTML in `tests/synthetic_pages.py`; no captured HTML is required. The old live failing-URL smoke is replaced by offline state/cascade/cache smoke tests. Live provider/browser authentication and actual installed OMP capability remain external prerequisites, not inferred from source tests.

Pending integrator command from the repository root, in a disposable HOME/USERPROFILE/cache/temp environment with credentials removed and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`:

```text
python -B -m pytest webfetch/tests
```

No tests, builds, linters, formatters, smoke runs, model calls or service launches were executed for this cutover. Publication and installation are separate operations.
