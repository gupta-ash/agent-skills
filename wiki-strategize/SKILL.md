---
name: wiki-strategize
description: "Produce a goal-oriented strategy, strategy-card, inference or plan from accepted cross-source wiki synthesis. Use for 'what should we do about X', 'build a strategy', 'draw conclusions from my notes', or 'make a plan from the wiki'. Requires supporting accepted synthesis and vault permission for Tier C. OMP syntax: /skill:wiki-strategize [--from-pipeline ID] <Goal> [--type strategy|inference|strategy-card|plan] [--vault NAME-or-path] [--dry-run]. --from-pipeline must be first when supplied."
---

# wiki-strategize

On OMP use `/skill:wiki-strategize`; other hosts use their native skill invocation with the same `wiki-strategize` name and unchanged arguments.

Route exclusively to `wiki-strategize` through the common native runtime. Read [goal-oriented outputs](../wiki/docs/user-manual.md#goal-oriented-outputs) and [runtime requests](../wiki/docs/user-manual.md#runtime-request).

1. Require a Goal. If absent, respond: `Provide a Goal. OMP usage: /skill:wiki-strategize [--from-pipeline <id>] <Goal> [--type strategy|inference|strategy-card|plan] [--vault NAME-or-path].` Stop without dispatch.
2. Preserve the complete Goal and other original argument values in a JSON argument array. If `--from-pipeline` is supplied it remains first. Do not interpolate input into shell code, invent a goal or substitute a different operation.
3. Resolve the actual installed skills root and its `_workflow/workflow.py`; use the matching machine declaration's absolute runtime `tokens.__OMP_PYTHON__`. Observe actual caller host/model family from native session metadata. Missing prerequisites or unknown/mismatched identity block, with no source/HOME/foreign-user fallback.
4. Write the private absolute request outside the vault using the exact manual schema: `schema_version:1`, `workflow:"wiki-strategize"`, actual absolute `project_root`, original `arguments`, observed `caller_host`/`caller_family`, `backend:"auto"`, `profile:null`.
5. Invoke as an argv array:

   ```text
   <runtime-python> <installed-skills>/_workflow/workflow.py run --request <absolute-request.json>
   ```

6. Report actual run/status/accepted outputs/decision/error, including disclosed partial publication. Exit0 is done/preview;2blocked;1failed/cancelled/invalid. Never execute inline, manually publish, use a legacy dispatcher or allocate another artifact/run to reset review/repair caps.

## Evidence and policy

- The selected vault must permit Tier C before any worker dispatch. Facts-only vaults cannot be bypassed by choosing a type, raw-only override or alternate path.
- Inputs require accepted synthesis, not arbitrary `type: compilation` or a `topic_distill` alone. Missing synthesis coverage blocks with a compile recommendation; do not generate around it.
- Preserve the four output types, reasoning chain, counter-considerations, evidence quality and falsifiable review triggers. Direct raw fallback must remain justified and cannot replace the accepted-synthesis prerequisite.
- Post-generation review and the bounded verification/review sequence share the original artifact identity/counters. FAIL/NO_REVIEW/unqualified or exhausted gates stop; no backend switch for approval. Tier C does not authorize generic hub rewrites or optional ClickUp actions.
- Dry-run has no database, vault, native, audit or network mutation. Respect custom strategy paths and protected supersession semantics.

Use [wiki-compile](../wiki-compile/SKILL.md) to build missing topic synthesis, [wiki](../wiki/SKILL.md) to capture evidence, or [wiki-reverify](../wiki-reverify/SKILL.md) for separate maintenance.
