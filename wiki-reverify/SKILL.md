---
name: wiki-reverify
description: "Check wiki source drift or reassess note claims against cited raw evidence. One maintenance skill has drift_check (default, source hash/transcript drift) and content_check (per-claim semantic/cross-family review) modes. Use for 'check the vault', 'has anything drifted', 'verify my notes', 'are the citations still right', or 'audit the wiki'. OMP syntax: /skill:wiki-reverify [--mode drift_check|content_check] --vault NAME-or-path [mode-specific flags]. Note content stays read-only; permitted audit and explicit new snapshots are separate effects."
---

# wiki-reverify

On OMP use `/skill:wiki-reverify`; other hosts use their native skill invocation with the same `wiki-reverify` name and unchanged arguments.

Read [maintenance](../wiki/docs/user-manual.md#maintenance) and [runtime requests](../wiki/docs/user-manual.md#runtime-request). This is a separate maintenance operation, never a substitute for new-ingestion acceptance.

## Route once

- Missing `--mode`, or `--mode drift_check`: choose `workflow:"wiki-reverify"`.
- `--mode content_check`: choose `workflow:"wiki-content-check"`.
- Unknown value: stop with `Unknown --mode value: '{value}'. Valid: drift_check, content_check.` Do not create a run.
- Consume only the `--mode` option/value at this front. Forward every remaining original argument value unchanged in the JSON array; do not reconstruct shell text. Missing/conflicting mode values are invalid, not a reason to guess.
- Require `--vault`; accept no ingestion source operand. Without it: `Provide flags. OMP usage: /skill:wiki-reverify [--mode drift_check|content_check] --vault NAME-or-path [mode-specific flags].`

## Execute

1. Resolve the real installed skills root and `_workflow/workflow.py`; use the matching machine declaration's absolute runtime `tokens.__OMP_PYTHON__`. Observe native caller host/model family from actual session metadata. Unknown identity or unavailable installation/runtime/declaration blocks without source/HOME/other-user fallback.
2. Write a private absolute request outside the vault with `schema_version:1`, the selected workflow, actual absolute project root, stripped `arguments`, observed `caller_host`/`caller_family`, `backend:"auto"`, `profile:null`, exactly as the manual specifies.
3. Invoke as an argv array:

   ```text
   <runtime-python> <installed-skills>/_workflow/workflow.py run --request <absolute-request.json>
   ```

4. Return the actual JSON envelope and report paths. Preserve complete negative/drift findings, null tooling scores, unreviewed subjects and any failed audit/partial fan-in. Exit0 means accepted done/preview,2blocked,1failed/cancelled/invalid; detected drift itself is a report outcome, not an execution failure.

## Mode-specific boundaries

| Mode | Accepted flags and effects |
|---|---|
| drift_check | Monthly default `--freshness`; repeatable `--source-class`; optional `--snapshot-on-drift`; report-only `--backfill-mode`. Compare matching acquisition/body hash domains. Books skip; transcript delta strictly below0.05 matches. Missing baseline is disclosed, never silently replaced. |
| content_check | Union/deduplicate `--slugs`, `--slug-file`, `--wildcard`, `--all`. Resolve actual evidence, extract claims and apply typed claim/native review gates. Zero claims or tooling failure is not clean verification. `--apply` and drift-only flags are rejected. |

Both modes leave existing note bodies/frontmatter untouched. Enrolled unprotected verify-log/manifest audit writes remain explicit; semantic `last_verified_at` refresh requires complete content fan-in with at least one verified page and no tooling or rejected-review integrity failure. Drift/zero/partial/tooling/integrity results cannot refresh it, even with a clean sibling page. An explicit drift snapshot creates a new version only; never overwrite the old raw. Backfill does not compute/write baseline hashes. Protected/locked/unenrolled audit targets yield the actual skip/proposal/refusal, not fake success.

No ClickUp, scheduler registration, inline workflow, legacy dispatcher, semantic retry, approval-seeking backend switch or automatic mutation replay. For capture use [wiki](../wiki/SKILL.md), topic synthesis [wiki-compile](../wiki-compile/SKILL.md), and goal outputs [wiki-strategize](../wiki-strategize/SKILL.md).
