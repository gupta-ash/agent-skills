---
name: wiki
description: "Capture a book, PDF, document, article, transcript, image, thread, URL or supplied text into an Obsidian wiki as preserved raw evidence and detailed, cited topic distillations. Use for 'ingest this', 'add this to the vault', 'read this book into my wiki', or source material paired with a vault. Also handles explicit --init control initialization. OMP syntax: /skill:wiki <source> [--vault NAME-or-path] [--no-compile] [--clickup] [--dry-run], or /skill:wiki --init --vault NAME-or-path."
---

# wiki

On OMP use `/skill:wiki`; other hosts use their native skill invocation with the same `wiki` name and unchanged arguments.

Route exclusively to the shared runtime's `wiki-ingest` workflow. `--init` is a control-only branch of that workflow, not a content-generation command.

Read the [family manual](docs/user-manual.md), particularly [runtime requests](docs/user-manual.md#runtime-request) and [ingestion](docs/user-manual.md#ingestion). Preserve the original source operand and flag values as an argument array; do not interpolate them into a shell expression.

## Execute

1. Resolve the actual installed skills root from this loaded skill's location. Require its sibling `_workflow/workflow.py` and the referenced manual/resources. Resolve the absolute runtime Python from the matching machine declaration's `tokens.__OMP_PYTHON__`; missing installation/declaration/runtime is a prerequisite refusal, not permission to use authoring or another user's files.
2. Observe the active native caller host and model family from its actual session metadata. Host is `omp|codex|claude`; family is `gpt|claude`. Unknown/mismatched identity blocks; do not infer family from a skill name or select one to obtain approval.
3. Create a private absolute request JSON outside the vault using the manual's exact schema: `schema_version:1`, `workflow:"wiki-ingest"`, the actual absolute project root, original `arguments` array, observed `caller_host`/`caller_family`, `backend:"auto"`, `profile:null`. Resolve execution-only overrides only when explicitly supported; do not add semantic overrides or sticky options.
4. Invoke the existing runtime as an argv array:

   ```text
   <runtime-python> <installed-skills>/_workflow/workflow.py run --request <absolute-request.json>
   ```

5. Report the actual JSON envelope, run ID, accepted outputs, remaining decision/error and any partial publication. Exit0 means done/preview;2blocked;1failed/cancelled/invalid. A worker completion or review receipt alone does not establish knowledge acceptance. Do not retry a blocked semantic gate, change backend to seek approval, or roll back already accepted outputs.

No source operand: request a source using `/skill:wiki <source> --vault <name-or-path>` on OMP, unless this is the explicit `--init` branch. Do not substitute invented input.

## Boundaries

- Preserve raw bytes/transcripts, metadata, images/uncertainty and detailed source-unit/topic knowledge. Missing capture, fidelity, coverage or required review blocks strict acceptance.
- `--no-compile` disables conditional synthesis; otherwise the workflow owns the retained conditional compilation decision. Do not independently chain extra runs or allocate fresh review allowances.
- ClickUp is disabled unless this invocation explicitly includes `--clickup`; only after accepted knowledge publication. Invoking its helper requires a valid operator-owned `CLICKUP_ROUTES_FILE` JSON map. Dry-run describes proposed actions without auth/network/database mutation. Do not inspect optional configuration, credentials or helpers when disabled.
- Respect the selected schema's paths, tiers, facts-only/status/custody restrictions and protected controls. `--init` adopts controls only; it does not relocate corpus or create topic/strategy scaffolding.
- Missing runtime, input, safe native route or required review fails closed. Never execute the workflow inline or through a legacy dispatcher/standalone bypass.

Use [wiki-compile](../wiki-compile/SKILL.md) for cross-source topic synthesis, [wiki-strategize](../wiki-strategize/SKILL.md) for goals and [wiki-reverify](../wiki-reverify/SKILL.md) for separate drift/claim maintenance.
