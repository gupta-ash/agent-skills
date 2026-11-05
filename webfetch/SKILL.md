---
name: webfetch
description: |
  Fetch content from a known URL with auto-routing through four runnable tiers. Use
  instead of WebFetch for URL extraction. In OMP, omp-web chooses topic, URL,
  or explicit-browser routing first: a URL in an explicit browser/interaction request
  does not independently trigger fetch. Portable Claude/Codex triggers remain
  'fetch X', 'get content of X', 'read this URL', 'summarize X', or a specific http(s) URL.
---

# webfetch

Smart auto-routing fetch with persistent blocklist learning. One entry point; the skill picks the right backend per domain.

**OMP precedence:** follow `omp-web` first. Ordinary known-URL reads use this installed skill with `--raw-required`; explicit browser/interactive requests use native managed browsing even for static URLs, not a preliminary fetch or Playwright CLI alternative. Keep this skill discoverable as the shared extraction and `--inspect-response` implementation. Native interactive CDP's missing qualified exact-target API requires ASK/STOP, not a broad attachment fallback; this does not remove this extraction cascade's existing human-started, evidence-gated raw CDP tier. Claude/Codex caller rules below remain unchanged.

## Quick start

    # Default: fetch + return clean markdown (json envelope)
    python ~/.agents/skills/webfetch/fetch.py --url "https://docs.example.com/guide"

    # Explicitly require page-derived text (all current successful fetches are raw)
    python ~/.agents/skills/webfetch/fetch.py --url "https://X.com/path" --raw-required

    # Markdown-only output (no json envelope)
    python ~/.agents/skills/webfetch/fetch.py --url "..." --format markdown

## Output (JSON default)

    {
      "url": "<final URL>",
      "tier": "http"|"headless"|"headed"|"cdp",
      "extractor": "trafilatura"|"pypdf"|"verbatim",
                                       // pypdf = PDF text layer; verbatim = a JSON/JS/text
                                       // body used as-is (no HTML extraction ran)
      "synthesized": false,            // synthesis is disabled, including cached synthesis
      "rewritten_from": null|"<orig>", // populated if URL was rewritten (e.g. reddit/r/ -> old.reddit/r/)
      "content": "<extracted markdown or supported textual body>",
      "bytes": 19650,
      "elapsed_ms": 720,
      "pages_read": null,              // no active model-rendered fetch tier
      "from_cache": false,
      "status": 200,                   // the status THIS TIER REPORTS -- the origin's own
                                       // when "status_is_real" is true, a placeholder the
                                       // tier substituted when it is false. BOTH keys are
                                       // absent whenever the selected result carried no
                                       // status at all -- a cache replay, a transport
                                       // failure, an unsafe URL, any exit with no response.
      "status_is_real": true,          // false = THIS TIER INVENTED THE STATUS ABOVE.
                                       // The browser tiers substitute 200 when they could
                                       // not read one. Travels WITH "status" and is absent
                                       // whenever it is. When false, do NOT report the
                                       // number as the origin's -- say it could not be read.
      "tier_attempts": [...],          // present in error envelopes
      "error_code": "disabled",       // optional on explicit disabled refusal, never success
      "warnings": [],
      "next_step": "...",              // ERROR ENVELOPES ONLY -- what to do about it
      "failure_class": "needs_login"|"rate_limited"|"hard_block"|"captcha_challenge",
                                       // error envelopes, and only when a verdict
                                       // diagnosed the stop
      "login_url": "<where the login evidence was seen>",  // needs_login stops
      "retry_after": "<the origin's own wait, verbatim>"   // rate_limited stops
    }

**Copilot FAILED / DISABLED:** `Copilot disabled: reliable search execution is not qualified (search_not_executed); diagnostics deferred.` Explicit Copilot start/ceiling/model selection returns `error_code='disabled'`, `failure_class='other'` before URL/DNS/cache work; CLI refusal also precedes database/bootstrap/reaping/provider discovery. There is no model/env/direct-bypass or diagnostic exception. Ordinary fetch never discovers a Copilot script/model, forces effective `allow_synthesis=False` before cache lookup, and rejects a cached row when either its tier is Copilot or its synthesized flag is true. Dropping `--raw-required` cannot enable synthesis. Diagnostics are future-only; unrelated GitHub Copilot hosting/model roles are unchanged. No tier promises response bytes: HTML is rendered to Markdown, while supported textual bodies can be returned verbatim.

## Never fail silently -- `next_step`, and the ASK

**Every error envelope carries `next_step`**: one sentence naming what stopped
the fetch and what can be done about it. Two shapes, and they are not
interchangeable.

- **It starts with `ASK THE USER`** -- the fetch hit a login wall, or a captcha
  no unattended browser can pass, and the only remaining route is the CDP
  browser. **RELAY THAT SENTENCE TO THE HUMAN, VERBATIM. Do not swallow it, do
  not silently retry around it, do not report a bare "fetch failed".** The same
  literal is repeated in `warnings`, so a caller that reads only warnings still
  gets it. When the user says the browser is up, retry the same fetch.
  Sibling case, no prefix and still for the user: when the CDP browser IS
  running but holds no session for the site, `next_step` reads *"the CDP browser
  is running but is not signed in to <host>; sign in there, then retry this
  fetch"*.
- **Anything else** -- a 404, a dead host, a hard block, a rate limit. Addressed
  to the calling agent, not to a person: act on it yourself and do NOT pull the
  user away from what they were doing.

`failure_class` is the machine-readable half of the same diagnosis:
`needs_login`, `rate_limited`, `hard_block`, `captcha_challenge`. It is absent
when the cascade merely ran out of rungs with nothing diagnosed -- that is
exhaustion, and `tier_attempts` is what describes it.

`--format markdown` prints both on failure (`> fetch failed: ...` then
`> next step: ...`), so choosing it loses nothing.

## Cascade (internal)

1. HTTP + Chrome UA + trafilatura — HTML extraction, PDFs via pypdf, and JSON/JavaScript/plain-text bodies verbatim where supported.
2. Playwright headless + trafilatura — JavaScript-rendered pages.
3. playwright headed + trafilatura — sophisticated bot detection. **Enabled by default**, opening a real Chrome window. The only headed opt-out is `WEBFETCH_DISABLE_HEADED=1`; it remains a gap, not a ceiling, and evidence-gated CDP can remain eligible past it.
4. **Reserved persisted slot: Copilot FAILED / DISABLED**, not runnable and not an allowed explicit ceiling/start/model. A learned/locked Copilot hint falls back to an allowed unwindowed rung, never directly to CDP.
5. raw CDP over DevTools — dedicated user-started Chrome on 9222. Entered only on `NEEDS_LOGIN`, or diagnosed captcha after headed has had its opportunity, and only if a browser answers. No playwright-cli, shell or config file is used for this tier.

URL rewrites are applied before tier selection (path-scoped, e.g. `reddit.com/r/* → old.reddit.com/r/*`).

**Default `--max-tier cdp`** -- but the ceiling only makes tier 5 ELIGIBLE. The escalation gate decides ENTRY: a `NEEDS_LOGIN` verdict, or a captcha diagnosed in this run once the headed tier has run. So an ordinary failure -- a 404, a dead host -- never reaches it even with a browser running, while a login wall or an uncleared captcha does (ceiling and port permitting). It needs the dedicated Chrome up with `--remote-debugging-port=9222`. **The skill never launches that browser itself; it ASKS the user to start it** (R4) -- that window is the one holding the user's own sign-ins, and where it sits is the user's call. If nothing answers, the fetch stops with the diagnosis and an `ASK THE USER` sentence in `next_step` and in `warnings`; **relay it**.

## Playwright-cli invocation (D19)

When inspecting the retained Playwright headless/headed internals (not a replacement interactive workflow):

- **Persistent profile (numbered, claim-on-open):**
  `playwright-cli --profile ~/.cache/webfetch/playwright open <URL>`
  First instance lands on `playwright`; concurrent instances auto-pick
  `playwright2`, `playwright3`, ... up to 10. Override base via
  `WEBFETCH_PLAYWRIGHT_PROFILE` env, cap via
  `WEBFETCH_PLAYWRIGHT_PROFILE_MAX`. Cookies/logins are per-profile-dir
  (login state lives where you first established it; concurrent claims on
  `*2..N` are fresh).

- **CDP attach to user's Chrome (Tier 4):**
  (nothing to configure -- the tier connects to the DevTools port directly)
  Config requires `"isolated": false`. Opens new tabs visibly in your real
  Chrome and closes them after the fetch. Prerequisite: Chrome launched with
  `--remote-debugging-port=9222`.

See README § Playwright-cli invocation patterns for full detail.

## State

Single SQLite file at `~/.local/share/webfetch/state.db` (WAL mode for multi-harness concurrency). Contains:
- `blocklist` — per-domain tier hints with threshold-gated promotion (3 same-class failures within 1h) / demotion (5 consecutive successes); disabled outcomes do not promote
- `cache` — 15-min URL cache (composite key includes raw_required + max_tier + effective allow_synthesis=false); old Copilot/synthesized rows cannot be replayed
- `fetch_log` — append-only fetch log (sensitive query params redacted)
- `rewrites` — path-scoped URL rewrites (auto-bootstrapped from `default_blocklist.json`)
- `config` — bootstrap and schema state; old Copilot path data is not discovered or used

To clear all state: `rm -rf ~/.local/share/webfetch/`. Next fetch re-bootstraps from `default_blocklist.json`.

## Caller patterns
For OMP, apply the precedence exception above and the native `omp-web` workflow. The following portable extraction/summary/topic patterns remain the Claude/Codex rules; an explicit OMP browser request is not an independent URL-fetch trigger.


- Need raw content of a known URL → call this skill, use `content` directly.
- Need a summary of a known URL → fetch it, then use one existing harness summary operation over `content` only. Do not promise a fixed model, latency, price or entitlement.
- Need an answer about a topic where you don't know the URL → follow [websearch](../websearch/SKILL.md): raw search plus at most one optional evidence-only summary, not this fetch skill. Copilot search/fetch/synthesis is FAILED / DISABLED; do not invoke it. OMP's existing explicitly requested native Codex synthesis option is OMP-only, never a fetch tier.

## Requirements

- Python 3.10+; core transport/state logic uses the standard library, with extraction dependencies below.
- `trafilatura>=1.6` — **must be pre-installed** (`pip install --user trafilatura>=1.6`); skill exits with code 2 if missing
- `pypdf` — **must be pre-installed to read PDF URLs** (`pip install --user pypdf`); used by tier 1 to extract a PDF text layer.
  **Unlike trafilatura, a missing pypdf is NOT fatal** — the skill does not exit; the PDF fails that one tier with `pdf_extractor_missing` and the cascade continues to the tiers above.
  Install these packages in the Python environment used to invoke the skill. No private provisioning manifest or machine-specific dependency checker is required.
- `playwright-cli` and an already-provisioned supported browser for headless/headed tiers; raw CDP requires the user-started dedicated 9222 browser
- No Copilot proxy, token or model prerequisite applies: that fetch/synthesis tier is source-disabled, with diagnostics deferred

See `README.md` for full design notes (modules, SQLite schema, SSRF guards, debugging, ops).
