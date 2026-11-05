---
name: wiki-compile
description: "Build a detailed cross-source topic compilation from accepted wiki distillations and their original raw evidence. Use for 'compile what we have on X', 'what do my notes say about X', or 'build a compilation for this topic'. Preserves agreements, contradictions, qualifications and cited detail rather than an executive summary. OMP syntax: /skill:wiki-compile [--from-pipeline ID] <topic> [--domain DOMAIN] [--vault NAME-or-path] [--dry-run]. --from-pipeline must be first when supplied. Qualify the topic with a declared domain or pass --domain; a bare topic requires a single-domain schema."
---

# wiki-compile

On OMP use `/skill:wiki-compile`; other hosts use their native skill invocation with the same `wiki-compile` name and unchanged arguments.

Route exclusively to `wiki-compile` through the common native runtime. Read [compilation](../wiki/docs/user-manual.md#compilation) and the exact [runtime-request contract](../wiki/docs/user-manual.md#runtime-request).

1. Require a topic. If absent, respond: `Provide a topic. OMP usage: /skill:wiki-compile [--from-pipeline <id>] <topic> [--domain DOMAIN] [--vault NAME-or-path].` Stop without a run.
2. Preserve `--from-pipeline <id>` as the first argument when present; forward all other original argument values unchanged as a JSON array, not shell text. The prior run supplies accepted artifact-role records, not automatic approval of arbitrary Markdown.
3. Resolve the real installed skills root and its sibling `_workflow/workflow.py`; use the matching machine declaration's absolute `tokens.__OMP_PYTHON__`. Observe caller host/family from actual native session metadata. Missing runtime/declaration or unknown/mismatched identity blocks, with no authoring/HOME/other-user fallback.
4. Write a private absolute request outside the vault with the manual's exact fields: `schema_version:1`, `workflow:"wiki-compile"`, actual absolute `project_root`, original `arguments`, observed `caller_host`/`caller_family`, `backend:"auto"`, `profile:null`.
5. Invoke as an argv array:

   ```text
   <runtime-python> <installed-skills>/_workflow/workflow.py run --request <absolute-request.json>
   ```

6. Return the actual run/status/output/decision/error envelope. Exit0 is done/preview;2blocked;1failed/cancelled/invalid. Preserve partial effects and original evidence. No inline generation, legacy dispatcher, manual publication, approval-seeking backend switch or new run to reset a spent counter.

## Acceptance boundary

The workflow gathers accepted topic distillations and rereads supporting raws, requiring at least three distinct matching raw files. Three chapters do not prove three independent origins. Legacy raw-only entry obtains the missing capture/distillation acceptance within this same parent run; it does not silently promote old notes. Source qualifiers/conflicts and direct raw citations remain intact.

Required post-generation review and eligible L3 review are separate gates sharing the artifact's bounded allowance. Verifier FAIL is not publication acceptance; no unused allowance bypasses a failed or exhausted narrower gate. Schema paths, existing canonical targets, human protection and supersession behavior remain authoritative. Dry-run makes no state/vault/native/audit/network mutation.

For source capture use [wiki](../wiki/SKILL.md); for a goal use [wiki-strategize](../wiki-strategize/SKILL.md); for drift/claim reassessment use [wiki-reverify](../wiki-reverify/SKILL.md).
