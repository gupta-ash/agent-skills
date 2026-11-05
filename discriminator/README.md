# Discriminator source

Cross-family artifact review with one canonical packet, schema and grading implementation. [SKILL.md](SKILL.md) owns supported invocation, caller obligations, review/repair allowances and result consumption. This README describes source layout, configuration, integration and known limits; source availability is not evidence that an installed host has loaded or successfully executed a review.

## Entrypoints and boundaries

| Source | Responsibility |
| --- | --- |
| `src/request.py` | Claude Code/Codex CLI argument handling, launch/collect and exit-code mapping |
| `src/packet.py` | UTF-8 evidence acquisition, locked resources and canonical ordered packet |
| `src/envelope_schema.py`, `src/envelope.py` | Model `review-envelope/2` schema, strict parsing, canonical assessments and verdict floor |
| `src/result.py` | Durable CLI handles, retained artifacts and idempotent collection |
| `src/supervisor.py`, `src/telemetry.py` | Codex attempt lifecycle/internal recovery and read-only run-record views |
| `src/family_attestation.py` | Qualification of the family reported by the selected CLI transport |
| `src/native_review.py` | Finite JSON `handshake`, `prepare`, `finish`; no inference or credential access |
| `harness/codex/transport.py`, `harness/claude/claude_reviewer.py` | Separate CLI transports, with no same-family substitution |
| `harness/omp/index.ts` | OMP `adversarial_review` registration, native model/auth/tokenizer use, cancellation and evidence publication |

Python helpers use sibling imports in the installed flat layout. Do not execute a partial mixed-source `PYTHONPATH`, register the raw source OMP index, or substitute a CLI launch for an OMP tool call. Use a complete manifest-derived disposable export for source tests.

The native helper owns exact routing: `gpt` callers select `github-copilot` / `claude-opus-5.5` / `anthropic-messages`; `claude` callers select `github-copilot` / `gpt-6-astra` / `openai-responses`. Requested, resolved and terminal routes must agree. Neither a provider name nor an environment declaration authenticates the hidden upstream model or the artifact's authorship.

Canonical delivery separates the original model document from tool-authored status, provenance and assessments. The top-level verdict can only be floored away from approval. Use its status/verdict/exit code, not `review.verdict` or a caller recount: `0` approved, `2` usable nonblocking nonapproval, `4` blocking or explicit fail, `1` no qualifying review with a null verdict. OMP returns only the bounded projection documented in SKILL; full findings and observations remain in evidence.

## Installed configuration

The supported public layout is:

```text
<home>/.agents/skills/discriminator/                 flat 27-file skill closure
<home>/.claude/agents/discriminator.md               Claude agent definition
<home>/.omp/shared/extensions/discriminator/index.ts shared OMP extension
<home>/.agents/agent-skills/runtime.json              nonsecret target-local runtime declaration
<home>/.discriminator/runs/                          retained review state, not code
```

The extension derives `<home>` from its exact shared-extension suffix. It reads Python only from that home's `.agents/agent-skills/runtime.json`:

```json
{
  "machine": "<actual-hostname>",
  "tokens": {
    "__USERPROFILE__": "<absolute-installed-home>",
    "__OMP_PYTHON__": "<absolute-python-executable>"
  }
}
```

`machine` must equal the observed hostname case-insensitively. The absolute profile token must resolve to the derived home. Python must be absolute and resolve to a regular file; the helper also checks its runtime version. Use the Python version supported by the public installer. The declaration's actual path and SHA256 join the invocation's source records and are rechecked before/after helpers, inference and publication. Other shared-runtime fields may coexist without selecting the discriminator's route.

There is no ambient HOME/PATH/Python fallback for OMP, no staging-machine declaration, relay/MCP command, active/default/named profile requirement or alternate extension location. A disposable installed-shaped home named `harness` has exactly the same contract. Normal OMP discovery must actually load the extension; copied files alone do not prove tool availability.

Legacy CLI calls instead require both `GAN_DISCRIMINATOR_CALLER_FAMILY` and `GAN_DISCRIMINATOR_CLI`, with the opposite `codex`/`claude` pair documented in SKILL and the host agent definitions. The executable overrides are **only** `DISCRIMINATOR_CODEX` and `DISCRIMINATOR_CLAUDE`, without compatibility aliases. Resolution uses an explicit internal executable argument first, that override second, then the applicable CLI on PATH. The CLI's own configuration selects its model; completion metadata must qualify the expected reported family. Authenticated CLI installations and their normal credential handling are separate prerequisites, not exported credentials.

## Resource and compatibility closure

The wire handshake remains **`ash-discriminator-native/1`**. It negotiates schema versions `{prepared:1, observation:1, result:1}` and capabilities `canonical-grading`, `image-input-refs`, `host-failure-reason`, `publication-refusal`. Its spelling is a protocol identity, not an installed namespace alias.

The helper reports actual hashes of exactly six native code dependencies: `native_review.py`, `request.py`, `packet.py`, `envelope.py`, `envelope_schema.py`, `result.py`. Preparation must match the negotiated snapshot. The adapter checks file stability throughout one invocation; compatible changed code can be accepted by a later invocation after a fresh handshake. Compatibility is not authorization to install or publish arbitrary bytes.

The ten contracts and all four assets travel together. Asset loading checks generator identity, population, hashes and schema/rubric coupling. The original rubric and both preambles are preserved byte-for-byte; in particular, the rubric's literal `ash_discriminator` is not a public path or executable alias. The locked rubric SHA256 is `d6191925bd5983a72039969e86280cd6b7d008c62fa68752de3776baf6dd8d82`. `rubric.lock.json` retains its original generator and block/section/whole-source digests but omits absolute source addresses. The original extraction document is not a runtime dependency. Do not rebuild or repin assets as part of a namespace move.

## Source export and change impact

[`../deploy/exports.json`](../deploy/exports.json) is the export declaration. Source paths below are relative to this directory; destinations are relative to the installed home, after removing the manifest's `harness/` prefix.

| Source population | Destination |
| --- | --- |
| `SKILL.md` | `.agents/skills/discriminator/SKILL.md` |
| Nine `src/*.py` modules | Same basenames in `.agents/skills/discriminator/` |
| `harness/codex/transport.py`, `harness/claude/claude_reviewer.py` | Same basenames beside the shared Python modules |
| `harness/codex/agents/discriminator.toml` | `.agents/skills/discriminator/agents/discriminator.toml` |
| Ten `src/contracts/*.md` files | `.agents/skills/discriminator/contracts/` |
| Four `src/assets/*` files | `.agents/skills/discriminator/assets/` |
| `harness/claude/agents/discriminator.md` | `.claude/agents/discriminator.md` |
| `harness/omp/index.ts` | `.omp/shared/extensions/discriminator/index.ts` |

That is **27 skill files plus two external entries**, not 29 independent implementations. This README and tests are source-only. Retained state, credentials, private engineering campaigns and qualification records are not export inputs. Changes to packet/schema/grading/native binding affect every host and the [shared workflow](../_shared/workflow/README.md) review consumer; transport changes also require their host-specific regression lane. Keep agent definitions, installer declarations and native consumers consistent when moving public paths. Installation/publication/authorized live-runtime acceptance remain separate operations.

## Source tests

Prerequisites: an explicitly selected supported Python with pytest, Bun on that test process's PATH, and an existing Ajv 8 package for strict-schema compilation. Set `DISCRIMINATOR_TEST_AJV` to its absolute package directory if Bun cannot resolve `ajv`. These are source-test dependencies, not runtime exports. Python helpers otherwise use the standard library; OMP supplies its own native packages/tokenizer. Do not install another SDK copy to repair host import resolution.

From the repository root in PowerShell, with `$Python` and `$AjvModule` set to the selected existing paths:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:DISCRIMINATOR_TEST_AJV = $AjvModule
& $Python -I -B -m pytest -p no:cacheprovider --import-mode=importlib --confcutdir=discriminator/tests discriminator/tests/test_native_review.py discriminator/tests/test_s1b_dual_family.py discriminator/tests/test_native_adapter.py
```

The fixture copies exactly discriminator's 29 entries from the actual aggregate export manifest to a unique disposable installed-shaped home, sets HOME/USERPROFILE before importing the flat modules, refuses pre-imported modules, and restores/removes only its own state. The closure regression checks the 27-file population, public external filenames, `.discriminator` state and original rubric/preamble digests.

For the finite helper/resource lane without Bun-dependent layout/schema tests:

```powershell
& $Python -I -B -m pytest -p no:cacheprovider --import-mode=importlib --confcutdir=discriminator/tests discriminator/tests/test_native_review.py -k 'not extension_input_layouts and not strict_compilation'
```

For resolver behavior only:

```powershell
& $Python -I -B -m pytest -p no:cacheprovider --import-mode=importlib --confcutdir=discriminator/tests discriminator/tests/test_native_review.py -k extension_input_layouts
```

The native helper tests cover strict handshake/input/schema/grading, immutable snapshots, loaded-code/resource binding, capacity refusals, cancellation precedence and explicit image-reference qualification. Resolver tests use synthetic declarations and unrelated-home/staging/profile decoys. The full adapter tests execute the real adapter and finite Python helpers, with explicit offline provider/auth and deterministic-tokenizer doubles; they cover both families, canonical flooring, localconfig refusal without fallback, declaration/code mutation before inference and during streaming, cancellation, capacity and retained evidence. Dual-family tests exercise CLI declaration, reported-family metadata, failure publication and public executable override precedence without launching a reviewer. None establishes live authentication, genuine SDK token accuracy, native image decoding, normal loader activation or installed review acceptance.

## Limits

- OMP makes one adapter-level stream call, with the owning cancellation signal and a two-hour cooperative abort deadline. Existing same-route provider recovery may make multiple HTTP attempts. Local settlement does not prove remote GPU stoppage, and host death may leave no receipt.
- Optional images are OMP-only explicit path/hash/media references. Actual retained image bytes, native decoding, a verified native image-token rule, supported multimodal routing and an observed full-input budget are required. No OCR/description, source-path scan, guessed image pricing, tool read roots or text-only fallback substitutes for them. Capacity failure refuses; it does not truncate evidence.
- The CLI has at most two internal attempts with independent two-hour timed waits. Codex teardown and durable record operations can exceed that wait. Cancellation checking and a following side effect are not atomic, so a surviving child may take another step or outlive a returned attempt. There is no public cross-session CLI resurrection or automatic caller-level retry authorization.
- CLI publication normally claims an absent result atomically, but empty-file replacement and the link-less fallback do not protect against a competing writer. State is retained without an automatic sweep and can grow without bound.
- Raw repository/diff rendering is a caller responsibility, not a CLI mode. Focus text is lower-precedence instruction space, not an injection-proof sandbox. Reported family/route observations are qualification inputs, not independent upstream identity proof.
