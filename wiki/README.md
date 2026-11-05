# Wiki family

Four discoverable fronts share one runtime and five stable workflow IDs:

| Front | Workflow | Responsibility |
|---|---|---|
| `wiki` | `wiki-ingest` | Preserve immutable evidence, create detailed source-unit/topic distillations, optionally compile accepted knowledge; explicit control initialization |
| `wiki-compile` | `wiki-compile` | Detailed cross-source synthesis over accepted distillations and supporting raws |
| `wiki-strategize` | `wiki-strategize` | Permitted strategy, strategy-card, inference or plan from accepted synthesis |
| `wiki-reverify` | `wiki-reverify` / `wiki-content-check` | Source drift reporting / read-only claim reassessment |

On OMP invoke `/skill:wiki`, `/skill:wiki-compile`, `/skill:wiki-strategize` or `/skill:wiki-reverify`. Other hosts retain their native invocation mechanism with these same names. Read the [family user manual](docs/user-manual.md) for arguments, requests, controls, reports and recovery.

## Runtime layout and ownership

`runtime/workflows.py` is the canonical domain registry. The fronts do not implement separate dispatchers or grant approval. The sibling shared runtime is authored at `_shared/workflow/src` and installed as `_workflow/`; it owns Controller/Store, native admission, attempt accounting, run/status/cancel/resume and the sole canonical publication manager. A source tree alone is not an installed or native-qualified environment.

`runtime/schema.py` resolves `.wiki-schema.md`; `vault_controls.py` plans and applies explicitly authorized controls without relocating corpus. `capture.py`, `coverage.py`, `citations.py` and `provenance.py` validate source membership, hash-bound evidence and lifecycle records. `tasks.py` freezes complete task resources and applies mechanical artifact checks. `audit.py` separates maintenance audit from note mutation; `actions.py` owns optional post-publication action receipts. Extractors under `scripts/` acquire/parse supported resources but do not grant publication or native access.

The `wiki-compile/` directory contains a hyphen and is not a Python import identifier. Domain code loads its `scripts/wiki_topic_match.py` by a file-relative runtime loader, not `import wiki-compile` or an underscore compatibility package. `wiki/runtime/workflows.py` remains the only registry.

## Evidence and compatibility

- Preserve the five workflow IDs, schema 3.0/3.1 contracts, existing ownership markers, project state and native binding handshake. Public front renaming is not a saved-state migration.
- `canonical-schema.md` is 3.1. `_archive/canonical-schema-v3.0.md` is the public 3.0 resource, not vault retirement history. Its generic examples and public skill paths are adapted; it is not claimed byte-identical to a private upstream snapshot. Existing 3.0 pins retain date/null verification semantics, while 3.1 acceptance uses actual UTC timestamps.
- Runtime resource hashes bind the installed bytes at preparation. Persisted TaskSpecs keep their original hash-bound resource snapshots; source edits do not authorize rewriting those snapshots or accepting changed raw inputs.
- Original captures remain immutable evidence. Detailed topic distillations and cross-source syntheses have distinct artifact roles. File/chapter counts do not establish independent origins.
- Capture/fidelity/coverage, exact raw citations and qualifying native review are separate requirements. Structural success or a worker's completion message is not semantic acceptance.
- Candidate repair stays within the original artifact, declared outputs/read grants and cumulative review/repair limits. Delivered FAIL, NO_REVIEW, exhaustion and uncertain native submission cannot be cleared by a new backend or identity.
- Publication rereads human protection and expected preimages. Already accepted outputs survive later failures; uncertain effects are reconciled, not replayed or automatically rolled back.

The background resources are named `wiki_lint.md` and `wiki_verify.md`. They are not additional discoverable fronts or permission to reconstruct standalone workflows. Task duties, selected schema and canonical review contracts remain authoritative.

## Optional ClickUp actions

ClickUp is disabled unless ingestion explicitly passes `--clickup`, and runs only after accepted knowledge publication. Ordinary wiki operation does not import/probe the helper or inspect routing/authentication. The invoked helper requires `CLICKUP_ROUTES_FILE`, a readable UTF-8 JSON object with nonempty string destinations for `openclaw`, `claude`, `ai`, `tech`, `trading`, `health`, `default`. No personal account/list identifiers are shipped. Additional explicit routing keys are supported. See the [routing example](docs/user-manual.md#optional-clickup).

A helper dry-run reads routing and describes requests, without reading auth, constructing clients, state writes or networking. Live mutation additionally requires operator-provided authentication. Prefix/default routing, priority 4, `wiki` plus domain tags, exact URL/destination deduplication and one-POST durable intent/reconciliation are retained. Changing routes cannot silently retarget an existing action journal. This repository change grants no authority to mutate any ClickUp account.

## Dependencies and limitations

Use the loaded installation's declared absolute Python and accepted native configuration; do not guess a machine/user path or bypass admission. PyYAML is required by schema/runtime consumers. Format-specific extraction may require its optional libraries or tools, including Calibre for MOBI/AZW3 conversion. `CALIBRE_CONVERT_TIMEOUT` overrides its default 120-second conversion timeout with a positive integer. Missing real evidence or an unavailable extractor/model route is a refusal, not invented output.

This distribution omits private engineering campaigns, original run records and run-specific admission APIs. Such ledgers are not ordinary portable public runs. No native end-to-end PASS, platform qualification, installed activation or successful public recovery from semantic FAIL is claimed here. The public resume interface selects only a recorded decision or still-authorized work; it cannot admit arbitrary replacement candidates.

## Consumer checks

The selected tests use disposable homes/vaults/projects, real validators/publication/state APIs and finite non-native protocol doubles or loopback HTTP. They do not establish native qualification or authorize live data. Campaign-dependent publication/remediation test drivers and private completed-run fixtures are intentionally excluded; ordinary recovery, controls/preimages, extraction review, citations, publication identity, optional-action and maintenance cases remain.

Engineering-only candidate-subject and missing-evidence admission callbacks are not part of the public runtime. The callback-only `test_candidate_review_subject.py` is excluded. Ordinary preparation coverage remains in `test_book_conversion_provenance.py` (exact selected inventory, coverless conversion proof and real-cover grants), `test_fidelity_content_inputs.py` (same-source chapter byte aliases, selected scope and settled-prefix refusal), `test_distillation_image_inputs.py` (ordered hash-bound images and exact file grants), and `test_missing_capture_evidence.py` (raw/scope/fidelity bindings and invalid-prefix refusal). These consumers call actual preparation handlers rather than private engineering admission APIs.

From the source root, the integration owner can run:

```text
<python> -I -B -m unittest discover -s wiki/tests -p test_*.py
<python> -I -B wiki-compile/scripts/wiki_topic_match.py --test
```

Tests were not executed as part of this portability/privacy edit. Shared-runtime and export/installed-layout checks must be run separately by their integration owner after all component changes land. No build, model, service, installation, publish or account operation is implied.
