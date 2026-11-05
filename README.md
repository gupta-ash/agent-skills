# agent-skills

Ten independently selectable skills for Claude Code, Codex and [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi). These are agent instructions plus explicitly packaged helpers/resources—not a central agent, deployment controller, or service. Relay skills belong to [`agent-messaging`](https://github.com/gupta-ash/agent-messaging), not this repository.

## Catalog

| Front | Behavior |
|---|---|
| `discriminator` | Cross-family artifact review with strict native helper/resource binding and truthful failure results |
| `obsidian` | Local vault resolution, read/write/search, frontmatter, tags and wikilink-aware moves |
| `save` | Interactive v4 project checkpoints, bounded working set and lossless cold archive moves |
| `restore` | Host-aware grounding from the saved v4 working set, with on-demand cold retrieval |
| `webfetch` | Tiered retrieval/extraction, explicit content/status gates and isolated transport state |
| `websearch` | Search-mode routing through bundled provider implementations with explicit credentials |
| `wiki` | Immutable capture, extraction, reviewed topic distillation and control initialization |
| `wiki-compile` | Cross-source synthesis over accepted evidence |
| `wiki-reverify` | Source-drift and read-only claim reassessment |
| `wiki-strategize` | Explicitly permitted goal-oriented strategies from accepted synthesis |

Provider `_SKILL.md` resources and `_workflow` are not discoverable fronts. `omp-web` is a selected native OMP web descriptor, not an eleventh catalog skill. There is no ebook downloader or project-format converter in this distribution.

## Prerequisites and boundaries

The scripted installation platform is Windows PowerShell **5.1+**, invoked locally by the actual target user. Select an existing base **Python 3.11+** using `-PythonExe`; the installer does not provision Python, Node, Bun, OMP, harnesses, browser binaries, credentials, or accounts. See each component's README for its Python/library/executable requirements. Obsidian/wiki and OMP configuration integration need PyYAML in the selected Python; native OMP workflows also need the actual supported OMP package and its selected Bun runtime, public SDK capabilities, local settings, and compatible discriminator helper dependencies. A save/Obsidian-only selection does not require OMP or wiki configuration.

Portable source usage is supported by individual Python helpers where their dependencies/platform allow it. This is not a claim of tested Linux/macOS harness installers or native tool readiness. Real model calls require the user's explicit authorization and correctly configured providers. Static preflight, preview, init, status and cancellation do not qualify a model or invent a successful review.

## Versioned install, upgrade and independent Verify

Obtain the committed `install.ps1`, archive and checksum/receipt from the **same exact release** of [`gupta-ash/agent-skills`](https://github.com/gupta-ash/agent-skills/releases). The initial release tag is `v1.0.0`. Save the script locally; never pipe a remote response into `iex`. A changed script must be replaced explicitly with that release's frontdoor, not silently self-updated.

Invoke the script directly so PowerShell arrays remain arrays:

```powershell
$WorkRoot = Join-Path $HOME 'install-work/agent-skills-v1.0.0'
& ./install.ps1 -Version v1.0.0 -WorkRoot $WorkRoot -Harness @('claude','codex','omp') -Mode Check
& ./install.ps1 -Version v1.0.0 -WorkRoot $WorkRoot -Harness @('claude','codex','omp') -Mode Install
& ./install.ps1 -Version v1.0.0 -WorkRoot $WorkRoot -Mode Verify
```

Use a local absolute directory owned by the invoking user, outside source/install/state roots. Download, extraction, child temporary activity and caches stay in WorkRoot. Backups retained there are private preservation, not disposable extraction; do not purge them with temporary downloads.

For a bounded selection:

```powershell
& ./install.ps1 -Version v1.0.0 -WorkRoot $WorkRoot -Harness @('codex') -Skills @('save','obsidian') -PythonExe '<absolute-existing-python.exe>' -Mode Check
```

`-Mode` defaults to `Install`. `-UserRoot` defaults to the actual profile; a different account/profile is not a relocation mechanism. `-RuntimeConfigDir <absolute-directory>` supplies first-install local `runtime.json`/`omp-install.json` only when selected features need them. It is not a staging root or runtime dependency after installation. No secret/token is accepted on this command line.

First install requires `-Harness`; omitted `-Skills` selects all ten fronts. On upgrades and Verify, omitted selectors reuse retained selections. Explicit additions expand the selected union; omission never uninstalls an existing selection. Unknown names refuse. To upgrade, use the new exact tag's script and repeat Check → Install → a **separate** Verify. Same-version execution verifies actual files/configuration before reporting a no-op; a VERSION marker is not evidence.

An existing recognizable installation without a receipt requires explicit `-AdoptExisting`. Adoption verifies identity/roots, privately backs up overwritten owned files and refuses foreign registrations or edited immutable code. It does not merge this public distribution with private `ash_` skills. Unknown files, user configuration, credentials, databases, logs and sessions are preserved. No implicit activation, autostart, hooks or remote-host operation occurs.

Check calculates the complete selected change before live writes. Install repeats preflight. Verify is read-only and no-network: it uses the retained closure under `.agents/installations/agent-skills/`, not source, extraction or downloads, and does not repair prerequisites. Expected-version mismatch, unsafe members, digest/OID/tag mismatch, drift, wrong roots/accounts and distribution collisions fail. A partial write/registration failure is reported as partial; it never stamps successful installation or claims whole-install atomicity.

## Selection closure and harness discovery

- Restore includes **all seven save helpers** and both shared references.
- Any wiki front includes wiki domain resources, `_workflow`, the Obsidian resolver and the complete discriminator.
- Selecting discriminator directly or as a dependency preserves its **27 runtime files**, regardless of harness; `harness/codex` in a source path is not a reason to omit shared code.
- Native OMP web selection includes both shared web implementations and the descriptor/reference pair.
- Codex uses `.agents/skills`. Claude receives only selected native fronts, or preserves an existing same-user canonical skills junction rather than replacing that parent link.
- OMP appends the actual canonical skills directory to `skills.customDirectories` through its public configuration API, preserving other entries, and registers only selected extension/native web resources. It does not enable every foreign provider or duplicate source trees.

Use a fresh selected-harness session for real discovery. In OMP, inspect actual `omp skill list <fixture> --json` and invoke `/skill:wiki`, `/skill:wiki-compile`, `/skill:wiki-reverify`, or `/skill:wiki-strategize` explicitly. Copying files alone does not prove discovery, MCP/tool readiness, model execution or a workflow PASS.

## Local configuration and private state

Public native configuration lives outside discoverable skill directories:

```text
<UserRoot>/.agents/agent-skills/runtime.json
<UserRoot>/.agents/agent-skills/omp-install.json
<UserRoot>/.agents/installations/agent-skills/installed.json
```

The private distribution uses a different namespace. The installer refuses mixed `_workflow`, selected-file, runtime or discriminator-tool ownership; it does not automatically migrate variants. Initial validated inputs are retained locally and upgrades preserve them, refusing conflicts rather than overwriting them.

`runtime.json` identifies the actual hostname/account/profile, selected base Python and existing OMP root. A minimal shape (replace every placeholder with the actual local value) is:

```json
{
  "machine": "<actual-hostname>",
  "tokens": {
    "__USERPROFILE__": "<absolute-actual-profile>",
    "__OMP_PYTHON__": "<absolute-existing-base-python>"
  },
  "omp_managed": {"enabled": true, "install_root": "<absolute-existing-omp-root>"},
  "workflow_native": {
    "omp": {
      "agent_dir": "<absolute-existing-agent-directory>",
      "config_files": ["<absolute-existing-settings-file>"],
      "model": "<catalog-provider>/<selected-model>"
    }
  }
}
```

Optional declared Claude/Codex routes require actual executables and explicit private-state exclusions. OMP's schema-1 `omp-install.json` names package `@oh-my-pi/pi-coding-agent` and contained `package_tree_relative`, `executable_relative`, `payload_relative`, and `installer.runtime_relative` entries from the actual installation. Do not invent these paths or reuse someone else's profile. Removed qualification/capability receipt fields are rejected, not nullable compatibility aliases. Startup admits static paths/declarations/current source hashes; actual SDK/session/tool/model checks remain in native preparation.

For ordinary OMP caller upgrades, omit the optional `workflow_native.omp.review_extension` code pin: admission derives the canonical installed public extension, current owned helper closure and selected Python. An explicitly supplied extension InputRef remains strictly hash-bound and must be reviewed/updated explicitly when its bytes change; upgrades do not rewrite preserved local configuration to refresh that pin.

Each new native admission binds the current selected Bun, declared launcher/payload and package identity along with its checked source/configuration closure. Changing those bytes after admission refuses before code loading or submission. A normal upstream OMP update is evaluated through a fresh admission and actual public capabilities, not a historical version allowlist or fabricated qualification receipt. Unknown neighboring notes/tests/caches are preserved and do not enter the positively declared 27-file discriminator closure.

Existing OMP package-manager cache hardlinks for the read-only SDK inputs `dist/cli.js` and `package.json` are supported when their paths are contained regular files with no reparse ancestors. This does not relax owned-target/executable protections, copy or reinstall the SDK, or authorize runtime provisioning.

Obsidian uses `VAULT_<NAME>_LOCATION` or explicit `--vault`. Exactly one configured vault is the default; zero/multiple require selection. ClickUp task creation additionally requires `CLICKUP_ROUTES_FILE` pointing to a JSON mapping with nonempty destinations for `openclaw`, `claude`, `ai`, `tech`, `trading`, `health`, and `default`; configuring that helper does not authorize mutation. Disabled/dry-run actions do not inspect auth or make network changes.

Existing `.ash_save`, v4 watermarks, wiki schema 3.0/3.1, ownership markers, workflow IDs and native handshake identifiers retain their wire/data formats. There is no automatic project/vault migration. Public fetch state uses `.local/share/webfetch` and `.cache/webfetch`; public review state uses `.discriminator`. These namespaces do not adopt private `ash_*` state.

## Honest limitations and failure behavior

Copilot search/fetch and search synthesis remain disabled where the accepted implementation disables them; forcing a disabled route refuses before auth/network/cache work. `webfetch --inspect-response` is available for strict synthetic response inspection. Browser access walls and manufactured status are not relabelled content success.

Codex task execution remains disabled before attempt/reservation/native launch; Codex caller reviews remain supported. Native fallback is limited to auto-selected OMP's observed process-creation failure before submission and an eligible declared Claude route—not semantic FAIL, missing config, permission denial, API incompatibility, cancellation or unknown effects. A native FAIL is not a public engineering override: ordinary resume uses only persisted permitted decisions and cannot manufacture source-recovery authority. Private engineering ledgers are refused with `SOURCE_RECOVERY_CONFLICT`.

No historical engineer-assisted wiki campaign establishes ordinary public PASS. This repository does not ship private qualification campaigns, source-recovery authorities, credentials or saved sessions. Real activation and live provider acceptance remain separately authorized user operations.

## Uninstall and source development

There is no inferred uninstall on upgrade. Retain the installation receipt and private backups. Review its owned immutable file list and selected configuration contributions; remove only unchanged owned files and those exact contributions, preserving unrelated settings, parent junctions, local configuration and all user data. Locally edited or foreign same-name files are conflicts, not deletion authority. Do not recursively delete `.agents`, `.claude`, `.omp`, vaults or project folders.

Author in a source worktree, never an installed tree. `deploy/exports.json` is the explicit projection; tests and engineering documents are source-only. See component READMEs for isolated test commands. Builds/tests/model calls are not implied by source authorship. Source publication, release archive acceptance, installation, Verify and real harness execution are distinct claims.

## License and imported providers

First-party code is MIT-licensed; see [LICENSE](LICENSE). Imported providers retain their upstream attribution and require their original redistribution terms. See [websearch/README.md](websearch/README.md) for provider-specific provenance/rights status. A first-party MIT notice does not relicense third-party work or establish a grant missing from its provenance.
