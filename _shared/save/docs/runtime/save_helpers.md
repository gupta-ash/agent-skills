# Save helpers — v4 operational reference

This distribution supports v4 saved projects only. The procedural [save](skills/save/SKILL.md) and [restore](skills/restore/SKILL.md) fronts define host-specific behavior; this reference does not enable hooks, unattended saves, format conversion, or extra maintenance stages.

## Installed helper closure

All seven standard-library Python helpers live together at `~/.agents/skills/save/scripts/`. Resolve `~` to the actual user's absolute home when launching an external process from PowerShell or OMP. Never copy one helper without its siblings.

| Helper | Responsibility |
|---|---|
| `citemap.py` | Scoped identifier/citation map, declaration families, fence-aware scanning |
| `recall.py` | `list`, `get`, `find`, `check`, `frontier`; raw-byte retrieval and integrity checks |
| `init_project.py` | Fresh v4 scaffolding with explicit adoption/collision rules |
| `demote.py` | Verified lossless moves from hot files into `_archive/` |
| `update_state.py` | Regenerate the v4 cursor and computed counts while preserving manual fields |
| `regen_manifest.py` | Regenerate the bounded router and cold-store pointers |
| `validate_entries.py` | Check entry grammar and required save invariants |

Source helpers are authored once under `_shared/save/scripts/`. `deploy/exports.json` declares their installed destinations; source tests/history are not installation inputs. Restore depends on this complete save helper/reference closure, not duplicate scripts under restore.

## Invocation and failure rules

```text
python <save-scripts>/init_project.py new --root <new-project> --name Example
python <save-scripts>/recall.py --root <project> find <term>
python <save-scripts>/recall.py --root <project> get <id>
python <save-scripts>/citemap.py --root <project> --strict
```

For save-time generator/drain options and ordering, follow the installed save front rather than inventing a second procedure. Pass an explicit project root; do not infer scope from the helper's cwd. Capture the helper process exit immediately (`$LASTEXITCODE` in PowerShell; `$?` in Bash), before formatting or piping its output. Nonzero stops the invoking step except for explicitly documented corpus-verdict/optional-action branches.

The three v4 writers refuse a non-v4 declaration with exit `4` and no writes. No converter, version-stamping override or API bypass is included. Preserve such a folder unchanged and use its format's owning tools, or recover the original v4 cursor if lost. Fresh `init_project.py new` scaffolding is separate and retains its existing collision/adoption rules.

`recall get` emits exact raw bytes on stdout and provenance on stderr. Do not insert line numbers or trim the returned entry. Cold files and checkpoints are not automatically loaded by restore; retrieve a specific entry or checkpoint when needed.

## Preservation and recovery

Demotion writes and verifies the archive destination before splicing the hot source. Interrupted work is reported, not silently stamped successful. Inspect the reported affected files before retrying. Unknown project files, original v4 watermarks, `.ash_save` metadata, `.ash-id-families`, and historical records retain their existing formats. This public distribution does not migrate a private installation or an existing project.

Optional Claude relay naming requires the separately installed `agent-messaging` helper at `~/.agents/skills/relay-name/scripts/set_name.py`. Missing messaging is an explicit unavailable prerequisite, never a successful rename. Codex and OMP do not run the Claude-only naming branch.
