# Shared ordinary workflow runtime

This source-only component is flattened to `.agents/skills/_workflow` by the explicit package map. It is not a discoverable skill or a deployment controller. The five registry IDs remain `wiki-ingest`, `wiki-compile`, `wiki-strategize`, `wiki-reverify`, and `wiki-content-check`; their domain module is `wiki/runtime/workflows.py`.

## Public boundary

`Controller(workflow_factory, *, artifact_validator=None, adapter_factory=None, skills_root=None, workflow_restorer=None, native_configuration=None)` exposes only `run`, `status`, `cancel`, and `resume`. Ordinary internal recovery reconciles saved LOCAL effects and actual native settlement; it never resubmits unknown work. Resume may select only an exact persisted decision. Domain repair, complete extraction-bundle reviews, citations, preimages, publication and same-artifact ceilings remain ordinary workflow behavior.

Private source-engineering preparation/admission/recovery APIs, source-policy authorities, bounded fourth/fifth-repair exceptions and fixed historical readiness campaigns are not part of this distribution. Existing private engineering ledgers refuse `SOURCE_RECOVERY_CONFLICT` before terminal fast-return or execution; old grants do not become generic public grants. No automatic ledger/project conversion is performed.

## Standalone native inputs

`resolve_machine(*, config_root=None, hostname=None)` and `resolve_native_configuration(request, skills_root, *, config_root=None, hostname=None)` use the actual installed layout and `.agents/agent-skills/{runtime.json,omp-install.json}`. The actual OS account/profile and hostname must match. No cwd, staging, relay-profile, other-user or source checkout fallback exists. Removed capability/qualification receipt fields are rejected, not nullable aliases.

`preflight_native(request, skills_root, *, native_configuration=None)` performs static declaration/path/current-source admission only. Its closed output has schema_version, machine, caller_host, caller_family, backend, configuration, task_backends, input_refs, unavailable—no cases or manufactured readiness rows. Auto selects OMP. Caller-review eligibility is separate from task eligibility; Codex tasks remain disabled before attempt/reservation/native launch. Optional invalid Claude fallback does not invalidate an otherwise valid OMP route.

Each route carries closed checked source_refs, selected current executables/interpreter/runtime, exact model including absence, settings and the public canonical discriminator extension/positive27 helper closure where selected. Unknown notes/tests/caches do not enter that owned closure. Frozen admission hashes are rechecked before code loading and submission; a normal OMP update requires a fresh admission, not a version allowlist or old qualification receipt. Real SDK public exports, package/session/model/tool/schema checks still occur in adapter prepare.

Ordinary `_checked_refs` admits a closed, current-hash reference set; duplicate resolved paths (including slash/case aliases on Windows) refuse rather than being normalized or silently discarded. Identical bytes at distinct paths, including distinct hardlink names, remain separately checked references. The removed private successor-admission normalizer is not a public compatibility API. Read-only status can inspect retained ordinary run evidence after current inputs drift; it does not refresh, qualify or grant live admission, and progress still rechecks the current hashes.

## Durable execution and effects

Tasks and reservations are durable before invocation. Native binding ACK follows committed identity; output must match strict task/run/step/item/attempt/schema and actual hashes. Completed semantic FAIL is not acceptance. An explicit source-bound `allow_task_failure: true` may complete a non-review report item diagnostically only when its exact current attempt settled as failed, no TaskResult was accepted, the retained attempt/report errors agree, and adapter teardown exited naturally and cleanly. That completion carries no artifacts or native success data; it preserves `tooling_failure: true` and `run_status: failed` so downstream reporting/audit can finish truthfully failed. Cancellation, terminal/stale identities and unknown settlement still refuse advancement. Cancellation is latched and late results cannot publish. Finite native cohorts settle before synchronous validation so the controller continues servicing binding barriers; teardown remains natural and explicit.

Fallback is only auto+OMP's observed adapter process-creation failure before submission to an eligible declared Claude route. No fallback on missing configuration, permissions, API incompatibility, semantic FAIL, NO_REVIEW, cancellation, unknown effects or explicit backend choice. Store ownership/epochs/short atomic reservation transactions and publication intent/preimage/compare-and-set identity remain strict. No source recovery authority bypasses these checks.

## Development and verification

Develop in source worktrees, never installed roots. Runtime source is under `src/`, native boundaries under `harness/`; tests and this guide are source-only. The root installer provides versioned package Check/Install/separate offline Verify, not a fleet/staging prerequisite. See the wiki family manual for ordinary front invocations.

Parent-run isolated selectors: eight `tests/test_{contracts,store,publication,controller,workflow,native_admission,fallback_adapters,review_extension}.py` suites, plus `bun test tests/omp_adapter.test.ts tests/omp_review_extension.test.ts`. `tests/omp_sdk_smoke.ts` is an explicit real-SDK no-child-inference fault smoke; `tests/contract_checkpoint.py` is an opt-in fresh synthetic worker/reviewer exercise. Build contexts from actual preflight source_refs, never fabricated success objects. Tests, installation, Verify and real model execution are distinct evidence; no result is claimed by this guide.
