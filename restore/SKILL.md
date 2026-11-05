---
name: restore
description: "Load a project's saved state. Reads the project's hot files, then announces what it loaded and what is parked. Use: 'restore', '/restore', 'restore session', 'restore state', 'resume work'."
---

# restore — Load a Project's Working Set

Trigger: `/restore` — slash command in CC, natural-language match in Codex/omp.

## Host and instruction-file contract

Use the actual active host; never infer it from which files exist. Codex natively discovers
`AGENTS.md`. Claude Code natively discovers `CLAUDE.md`. OMP has its own context discovery and
precedence; use its actual injected paths as specified in Step 4, not Codex's Git-boundary rule.
All three use `~/.agents/skills/restore/`, and bundled Python helpers are invoked with `python`.
In OMP resolve helper paths to absolute user-home paths before passing them to Python, especially
in PowerShell; use the normal shell/interpreter and capture the process exit immediately. Missing
Python/helpers stops the affected step with its specified unavailable/failure report, never a
fabricated count or installation workaround.

The durable v4 hot set contains both instruction files. They are independent: never copy one over
the other, concatenate them, or treat one as an alias. Step 4 loads each distinct file exactly once
while preserving native host discovery. A host-native file already loaded from cwd or an ancestor
is not read again; the other product's file is read only when its bytes differ from the native file.

On invocation you MUST ground yourself in prior work before ANYTHING else: take no action, write no code, make no suggestions until the hot files are read.

What you load is a WORKING SET, deliberately incomplete. Read the standing rule at the bottom of this file — not optional — before you answer anything.

## Unattended mode (`--yes`)

`--yes` runs restore headless with ZERO blocking prompts: every instruction anywhere in this file that would PROMPT, ASK, OFFER, WAIT FOR, or SEEK CONFIRMATION / ACKNOWLEDGEMENT / A DECISION from the user is suspended. That suspension scopes to human-directed interaction ONLY — all grounding READS, Step 2's version check, Step 6's file-state check, Step 6.5's relay-name reapply and Step 7's parked count still run normally. A folder argument is REQUIRED and must contain `state.md`; if absent or invalid, abort nonzero BEFORE Step 2 — no discovery, no multi-candidate ask.

- Step 2 — never run discovery or the multi-candidate ask; the passed folder is authoritative. The version check is NOT suspended and MUST still run: on ANY non-`v4` folder, whatever the reason it is not v4, it aborts nonzero. It is a guard that stops, not a question that waits.
- Step 4 — record which hot files are missing and continue; `state.md` is the only file whose absence aborts.
- The constraint check — never re-confirm or pause. A `--yes` restore proposes no plan and takes no action: skip-and-record-continue for an optional/idempotent conflicting action, and abort nonzero only when a REQUIRED restore invariant cannot be satisfied.
- Step 5 — read the checkpoint pointers silently; never load a checkpoint body and never ask whether to.
- Step 7 — skip "Ready to begin?", stop after the announce, exit 0. The REST of Step 7 is NOT suspended: the parked count runs and the announce prints.
- Every user-facing report / warn / surface / acknowledge (missing files, file-state divergence, Grounding Rule 7's stale-state warning keyed on `last_save_ts`) — fold into the single final announce and continue; never wait, never load `_archive/` whole.

## What To Do

Run Steps 1, 2, 4, 5, 6, 6.5 and 7. There is no Step 3 and no Step 4.5 — those numbers are retired, and nothing after them is renumbered.

### Step 1 - the artifact spec is a POINTER, not a load

**Do NOT read `~/.agents/save_artifacts.md` on a restore.** Step 4's list is authoritative here by this body's own words, so the spec could never override it and loading it changed nothing this skill does. `AGENTS.md` and `CLAUDE.md` are the ninth and tenth hot files. Step 4 defines which file the active host already loaded, the containment test, and the byte-equality rule that prevents loading the same instructions twice.

It remains the canonical cross-skill definition (C-25) and has NOT moved. Follow the pointer only when a cross-skill question actually has to be settled, and read the section you need rather than the file whole.

### Step 2 — Locate the project folder

Search for a project folder containing `state.md`, using these signals in order:

1. An explicit user mention of a folder.
2. cwd, if it is itself under `projects/{name}/` and contains `state.md`.
3. An active plan-file slug matching an existing `projects/**/{slug}/state.md`.
4. Glob `projects/**/state.md` — exactly one match, propose it; several, list them with each one's `last_save_ts` and `goal` line and ask the user which.
5. Nothing found — tell the user "No saved project state found here."

The current working directory IS the project folder; use it, do not ask. An explicit path argument overrides it, nothing else does. If several candidates exist, the one containing the working directory wins outright.

**OMP:** check the explicit folder first, otherwise the actual session cwd, regardless of a
`projects/` path segment. Apply the version/method tests below at that selected root. Do not
substitute the Git root, nearest `.omp` directory, skill directory or an injected ancestor's root.
If cwd has neither saved state nor the method-folder signals, use only the remaining discovery
signals above; never scan an unrelated home or unrelated project tree. `--yes` still forbids discovery.

#### Then check what version the folder is, before Step 4 reads anything

Read `state.md`'s `schema_version` line and run these three tests IN ORDER; only the first two continue and the order is load-bearing.

1. `{folder}/state.md` declares `schema_version: v4` → continue to Step 4.
2. `{folder}/intent.md` exists AND (`{folder}/phases/` OR `{folder}/slice-list.md`) exists → method-folder mode below; do not stop. This arm fires whether `state.md` declares something else or is absent altogether.
3. Anything else → STOP, on these two arms:
   - Unrecognised `schema_version` value → say the folder declares `schema_version: <value>` and that this restore reads v4 only. Do not call an unrecognised value "pre-v4".
   - `schema_version` absent → say the folder's `state.md` declares no `schema_version` at all, and do NOT name a version: there is none to name.

Both stopping arms exit `4` without writes. This distribution reads v4 only and includes no
format conversion. Preserve the folder unchanged; use the tooling that owns its declared
format, or recover its original v4 `state.md` if the cursor was lost. Do not guess a version or
recommend unavailable migration commands. `update_state.py`, `regen_manifest.py` and
`demote.py` use the same exit `4` refusal and write nothing on non-v4 roots.

#### Method-folder mode — a SPLIT READ

A project built under the `sw_doc` methodology keeps its own living artifacts at its root, and since that method's C-18 none of its OWN control files carries a v4 hot-file name — the collision this paragraph used to warn about was removed by renaming. `AGENTS.md` and `CLAUDE.md` are the TWO names still shared, and neither is a collision: they are the project's own host instructions under both schemes, which is why Step 4's containment and equality tests apply to the PROJECT ROOT here. `save` re-roots to `{folder}/.ash_save/` and never reads, writes or drains the project root. A restore here reads TWO places: `{folder}/.ash_save/` is session-authoritative (the eight non-instruction tool-read files); the `{folder}/` root is project-authoritative — LIST it and take what is actually there, plus `phases/**` and `_closed/**` where the slice contracts live. **Do NOT work from a remembered list**: several of those files are created on FIRST USE, so the set can differ between two healthy live projects at the same moment, not merely over time.

- The checkpoint pointer resolves under `{folder}/.ash_save/checkpoints/`, NOT the project root.
- THE ANNOUNCE MUST COUNT THE ROOT FILES. `.ash_save/CONSTRAINTS.md` is a scaffolded template, empty by construction — do not count it.
- Per-field sources: `Constraints loaded:` ← root `ash_constraints.md`; `Open questions:` ← root `open_decisions.md`; `Pending work:` ← root `work_not_phased.md`; `Active instructions:` ← `.ash_save/active.md`; `Parked:` ← `.ash_save/_archive/`'s two deferred files; `Checkpoints:` ← `.ash_save/checkpoints/`; `save:`/`phase:` ← `.ash_save/state.md`.
- THE ANNOUNCE LABELS NAME THE ROOT FILES IN THIS MODE. THREE template lines carry a filename — `Pending work:` and `Next task:` name `backlog.md`, and `Open questions:` names `open_questions.md` — because that is the ordinary v4 case. Here substitute `work_not_phased.md` and `open_decisions.md` respectively, so each label names the file its count actually came from. `Constraints loaded:` carries NO filename, so there is nothing to substitute in it and none is to be inserted; the per-field bullet above is where its source is stated.
- 7a's predicate is unchanged and correct on the root files, and requires no `kind:`/`lifetime:` declaration.
- Step 6's count comparison disagrees on EVERY healthy restore in this mode — DO NOT FLAG IT. Name both sides on the `Notes:` line ("`counts` describe `.ash_save/`; the constraint, question and work counts describe the project root") and move on.
- Name the method artifacts that have no v4 slot on the `Notes:` line, and the set is DERIVED rather than remembered: the root entries you just listed, MINUS everything DOT-PREFIXED — `.ash_save/` above all, which is the v4 location itself and never a method artifact, along with `.git/`, `.claude/`, `.gitignore` and `.relay-name.json` — MINUS the three the per-field bullet sources, and MINUS `AGENTS.md` and `CLAUDE.md`, which are v4 hot files, not method artifacts, and are handled by Step 4. The directories `phases/` and `_closed/` are part of that set, not exceptions to it. Never name a file you did not see in the listing.
- 7d's `Read:` receipt stays the eight non-instruction tool-read files of Step 4 under `.ash_save/`, plus either root instruction file Step 4 manually read; do not widen it to the other root artifacts — report the root reads in the announce body instead.
- `.relay-name.json` and Step 6.5's `cd` stay at the PROJECT ROOT; do NOT re-root either to `.ash_save/`.
- Stale-state (Grounding Rule 7) and the parked count run UNCHANGED; do not suppress either.

### Step 4 — Read the eight non-instruction hot files, then the instruction-file branch

The hot SET is ten. The ordered core below remains eight. `AGENTS.md` and `CLAUDE.md` follow the
host-aware branch after it so a native instruction file is never loaded twice.

**OMP instruction branch (instead of numbered steps 1–4 below):** resolve the instruction root
and cwd to absolute paths; method mode uses the PROJECT ROOT. Account for `AGENTS.md` and
`CLAUDE.md` independently against the exact paths actually injected into this session. OMP can
select another context file at the same depth, fall back to `CLAUDE.md`, or load workspace rules
above a Git root; neither file existence nor ancestor containment proves that a file was loaded.
Do not re-read an injected file. After the eight core reads, read each remaining instruction file
once, except a raw-byte-identical counterpart whose content was already loaded at this instruction
root. Use the exact `IDENTICAL`/`DISTINCT` comparison and failure rule in numbered step 4 below.
If neither is injected, read `AGENTS.md` first when present, then compare/read `CLAUDE.md`. If
only `CLAUDE.md` was injected, compare before reading `AGENTS.md`. When both were injected,
re-read neither; report any native duplicate injection rather than claiming this restore undid it.
Record manual unrelated-root reads on `Notes:`; injected/skipped-identical files stay out of
`Read:`. Unknown injection provenance is not permission to claim a single-load restore: stop and
report the missing provenance rather than guess. Then apply numbered step 5's missing-file rules.

**Claude Code / Codex instruction branch (unchanged):**

Set `native` from the actual host: `AGENTS.md` in Codex, `CLAUDE.md` in Claude Code. Set `other`
to the remaining instruction file. Resolve cwd and the located project root to absolute paths.
In method-folder mode the instruction root is the PROJECT ROOT, never `.ash_save/`.

1. Decide whether the host already loaded `native` from that instruction root:
   - **Claude Code:** yes when the instruction root is cwd or an ancestor of cwd. Preserve the
     existing Claude Code rule; do not apply Codex's repository boundary to it.
   - **Codex:** when Codex has a project root, its discovery chain runs from that project root
     down to cwd. The instruction root is native-loaded only when it lies on that bounded chain.
     An ancestor above the project root is not loaded merely because it contains cwd. When Codex
     has no project root, only cwd is searched, so only `instruction root == cwd` is native-loaded.
   Do not read a native-loaded file again.
2. Otherwise — sibling, descendant, unrelated tree, or outside the host's bounded discovery
   chain — read the located folder's `native` file
   last, after the eight core files, when it exists. State on `Notes:` that the located project's
   native rules were read because the host's injected rules belong to a different discovery chain.
3. If `other` exists and `native` does not, read `other`.
4. If both exist, compare their raw bytes without printing either file. The portable fallback is:
   `python -c "import pathlib,sys; print('IDENTICAL' if pathlib.Path(sys.argv[1]).read_bytes() == pathlib.Path(sys.argv[2]).read_bytes() else 'DISTINCT')" "<native-path>" "<other-path>"`.
   `IDENTICAL` means the host-native load already supplied the same instructions, so do not read
   `other`. `DISTINCT` means read `other` once. A comparison that does not print exactly one of
   those words is not a verdict: stop the restore rather than guess between omission and double-load.
5. Missing instruction files join Step 7's missing-file note. Do not create either on restore.
   `AGENTS.md` alone may be absent in a valid v4 folder created before the dual-file contract;
   label that case `legacy pre-dual-file folder`, not damage. Any missing core file or a missing
   `CLAUDE.md` retains the historical investigation path.

This is the dual-file invariant: byte-identical instruction files are loaded once; distinct files
are both loaded once; neither file is copied, appended, normalized, or overwritten.

Read each of the eight core files if it exists; record any that are missing and say so in the Step 7
announce. In method-folder mode read the eight from `{folder}/.ash_save/` and the project root's own
artifacts in addition. A missing `AGENTS.md` with the historical other nine hot files is a valid
legacy pre-dual-file folder and does not by itself warrant investigation. Any other missing hot
file means a save was torn or something deleted it outside a session and warrants investigation.
Do NOT offer "a document nobody has started yet" as an explanation.

The order below is DECLARED for reading — the order you read in, and the order 7c's `NOT LOADED` line reports against.

1. `state.md` — the generated cursor (`phase`, `save`, `last_save_ts`, `goal`, `active_track`, `hot_state`, `repo_root`, `files`, `counts`). READ IT FIRST: the announce is built from it, and `hot_state` decides whether this step runs to completion.
2. `manifest.md` — the generated router, one row per class of file and never one per file; it is how you find anything not in this list, including the archive and the checkpoint history.
3. `CONSTRAINTS.md` — user-stated hard rules. Check every entry BEFORE proposing any plan, design or significant change. It is a registry of entries, not a checklist, and a violation needs explicit user re-confirmation.
4. `active.md` — temporary instructions with a mandatory expiry. An expiry is a SAVE NUMBER, never a date and never an event, and an entry is out of force when its expiry is `<=` `state.md`'s `save:`. A bare integer (and `expires save#N`) is that number ABSOLUTELY; the relative counts `N saves`, `three saves` (numerals one–ten spelled out), `in N saves` and `+N` count from the entry's horizon base — the save it asserts it was written at (`save:` / `said-in:` / `asserted:`, or `asserted save#N` in the heading, with `said`, `said in`, `written`, `written in` also accepted for `asserted`), failing that the last save# its provenance quote carries, failing that the CURRENT save; a base ABOVE the current save is clamped down to it. They are relative only in the `expires:` / `expiry:` FIELD — in a heading the parser keeps the digits and discards the unit — and when an entry carries both a field and a heading form, the field wins. Check the number, do not assume.
5. `backlog.md` — live work only, plus the machine-managed region between `BACKLOG:BEGIN`/`BACKLOG:END`. This is the pending list and nothing else is.
6. `open_questions.md` — unresolved branches, each with a named resolution trigger.
7. `architecture.md` — invariants, contracts, protocols between parts, sequencing, ownership.
8. `design.md` — APIs, schemas, algorithms, edge cases, write-path invariants.

Never assume one `Read` returned the whole file: pass explicit `offset`/`limit`, advance `offset` by the lines you actually received, and stop only when a read returns none. That loop, not any file size, is what proves you reached the end.

**OMP read loop:** use the actual `read` path selectors, e.g. `file.md:1-150`, then
`file.md:151-300`, advancing by the numbered lines actually returned until EOF. Repeat the
selector on every call; a repeated bare path does not advance. Read any named elisions with
explicit ranges; truncated lines require `:raw` for their exact range. Do not pass nonexistent
`offset`/`limit` arguments to OMP's read tool. Only reaching EOF without unresolved omissions
earns `whole` in the receipt; a failed/partial loop earns `zero`.

If `state.md` says `hot_state: over_ceiling`, load the eight core files in the declared order, then
the instruction branch, stop when out of room, and announce exactly which files you did not reach
(7c). Every file you skipped joins your search scope for the rest of the session; you may not treat
anything as absent from this project until you have searched it.

`_archive/` and `checkpoints/` are never auto-loaded and never read whole for routine grounding; their pointers are already in `state.md` (`files.latest_checkpoint`, `files.archive`) and `manifest.md`'s routers. Resolve a single entry with `recall get` rather than opening an archive file whole.

Read `manifest.md`'s `unclassified archive` / *unknown to v4* label as "present under `_archive/` and not a drain destination", never as "not ours".

### Step 5 — Note the checkpoint history; do not load it

Do NOT list or read `checkpoints/`: take the count from `state.md` → `counts.checkpoints` and the path from `files.latest_checkpoint` / `manifest.md`'s Checkpoints router. Report the count in the Step 7 announce and name the latest path as loaded-on-request. Read the latest checkpoint only when a task actually needs its narrative, and say so — never by reflex.

### Step 6 — Verify the folder matches what the cursor claims

- Confirm each hot file `manifest.md` lists is on disk and each owned hot file on disk is listed; flag
  either direction. A current scaffold lists ten. A legacy v4 folder whose nine-file manifest predates
  the dual-instruction contract may omit `AGENTS.md`; Step 4 already accepts that shape, so its missing
  `AGENTS.md` is not a divergence. Nine or ten, not eight — this checks what EXISTS.
- Confirm `state.md` → `files.latest_checkpoint` exists; a field reading that there is none yet is a fresh project, not a fault.
- Compare `state.md` → `counts` against what you read (plan items, open questions, active instructions). A disagreement means a torn save, an edit afterwards, the two counting different sets, or a fence-blind count of your own.

`state.md`'s counts include only entries whose id RESOLVES, so an entry titled in plain prose, or a real entry at level 3, is uncounted while being entirely real. That is not a torn save; do not report it as one. Print what the file says, name the divergence, and say which causes you could not rule out.

Re-count with 7a's predicate before you report anything: the scaffolded fenced worked examples make a fence-blind count of your own read one higher than the cursor on a folder where nothing is wrong.

Flag any divergence to the user before proceeding, and keep flagging any document or file-state mismatch you notice for the REST of this session, not only here.

### Step 6.5 — Apply the relay name only where the host supports this contract

Read `<project-dir>/.relay-name.json`, `<project-dir>` being the folder located in Step 2; shape `{name, set_by_sid, set_at}`. That file is the ONLY source of the session's friendly name — there is NO `project:`-slug derivation and NO folder/workspace fallback of any kind. In method-folder mode pin both this read and the `cd` below to the PROJECT ROOT, not `.ash_save/`.

- Absent, unreadable or malformed, or `name` missing/empty → skip and apply no name. In Claude Code emit
  `Relay name: (none applied by this restore — run /relay-name <name> to set one)`. In Codex emit
  `Relay name: (none applied by this restore in Codex — Claude Code-only relay-name step unavailable)`.
  Say "by this restore", not "none".
- `name` present but invalid (`^[A-Za-z0-9._\-]+$` mismatch or len > 64) → emit `Relay name: SKIPPED (invalid '<name>')` and do not invoke the script.
- Otherwise branch on the active product.

**Claude Code:** re-apply it background-detached, so restore never blocks on broker network I/O. Run exactly:
First require the optional `agent-messaging` installation's existing
`~/.agents/skills/relay-name/scripts/set_name.py`. If absent, report `Relay name NOT applied:
agent-messaging relay-name is a prerequisite`, perform no rename/log creation, and continue
the read-only grounding with that explicit unavailable result. Never claim a successful rename.

```bash
mkdir -p ~/.claude/relay && cd "<project-dir>" && python ~/.agents/skills/relay-name/scripts/set_name.py --name "<name-from-file>" > ~/.claude/relay/last-set-name-<name-from-file>.log 2>&1 &
disown
```

The `cd` must be the Step 2 folder: `set_name.py` writes `.relay-name.json` at the caller's working directory and takes no path argument, so without it you overwrite another project's file. The `&` backgrounds the whole `&&` list, so the `cd` takes effect only inside that subshell — your own working directory is unchanged. Use the bash block, never `Skill("relay-name", ...)`. The log filename is KEYED BY NAME and must stay that way — never a single fixed path. Emit `Relay name: <name-from-file> (requested; receipt: ~/.claude/relay/last-set-name-<name-from-file>.log)`; the word is `requested`, not a claim that the rename happened.

**Codex:** `relay-name` is Claude Code-only (A11). Do not run its script, create its
`~/.claude/relay` log, or pretend a Claude session registry exists. Emit `Relay name:
<name-from-file> (not applied by this restore in Codex — Claude Code-only relay-name step)` and
continue. This preserves the output label while reporting the action truthfully.

**OMP:** do not run the Claude helper, write its log/registry or change a native broker identity.
For an absent/unreadable/malformed file or missing name emit `Relay name: (none applied by this
restore in OMP — Claude Code-only relay-name step unavailable)`. Preserve the invalid-name
diagnostic above. For a valid name emit `Relay name: <name-from-file> (not applied by this restore
in OMP — Claude Code-only relay-name step)` and continue. Never derive a name from `project:` or
the folder. OMP's native relay is separate and is not invoked by this restore.

### Step 7 — Count what is parked, then announce

#### 7a. The parked count

`_archive/backlog-deferred.md` and `_archive/open-questions-deferred.md` hold real work gated by a `gate:` line. Promotion is surfaced HERE and nowhere else — nothing is asked at save time. Count them per file and report both plus the total. Use the block for the active shell; they run the same Python source.

**Bash:**

```bash
python -S -I - "<project-folder>" <<'PY'
import os
import pathlib
import sys

sys.path.insert(0, os.path.expanduser("~/.agents/skills/save/scripts"))
import recall

root = pathlib.Path(sys.argv[1])
total = 0
complete = True
parts = []
for rel in ("_archive/backlog-deferred.md", "_archive/open-questions-deferred.md"):
    path = root / rel
    if not path.is_file():
        parts.append("%s: 0 (absent; created on first use)" % rel)
        continue
    _data, rows, open_fence = recall.load_doc(path)
    if open_fence is not None:
        parts.append("%s: NOT COUNTABLE, a fence opens at line %d and never closes"
                     % (rel, open_fence))
        complete = False
        continue
    n = 0
    shallowest = None
    for (_ln, text, _s, _e, in_fence) in rows:
        if in_fence:
            continue
        m = recall.citemap.HEADING_RE.match(text)
        if m is None:
            continue
        level = len(m.group(1))
        if level < 2:
            continue
        if shallowest is not None and level > shallowest:
            continue  # starts inside the entry counted above: BODY, not an entry
        shallowest = level
        n += 1
    total += n
    parts.append("%s: %d" % (rel, n))
print("Parked: %s - %s" % (total if complete else "INCOMPLETE", "; ".join(parts)))
PY
```

**PowerShell:**

```powershell
@'
import os
import pathlib
import sys

sys.path.insert(0, os.path.expanduser("~/.agents/skills/save/scripts"))
import recall

root = pathlib.Path(sys.argv[1])
total = 0
complete = True
parts = []
for rel in ("_archive/backlog-deferred.md", "_archive/open-questions-deferred.md"):
    path = root / rel
    if not path.is_file():
        parts.append("%s: 0 (absent; created on first use)" % rel)
        continue
    _data, rows, open_fence = recall.load_doc(path)
    if open_fence is not None:
        parts.append("%s: NOT COUNTABLE, a fence opens at line %d and never closes"
                     % (rel, open_fence))
        complete = False
        continue
    n = 0
    shallowest = None
    for (_ln, text, _s, _e, in_fence) in rows:
        if in_fence:
            continue
        m = recall.citemap.HEADING_RE.match(text)
        if m is None:
            continue
        level = len(m.group(1))
        if level < 2:
            continue
        if shallowest is not None and level > shallowest:
            continue
        shallowest = level
        n += 1
    total += n
    parts.append("%s: %d" % (rel, n))
print("Parked: %s - %s" % (total if complete else "INCOMPLETE", "; ".join(parts)))
'@ | python -S -I - "<project-folder>"
$parked_count_rc = $LASTEXITCODE
```

In Bash capture `$?` immediately after the Python process. In PowerShell use
`$parked_count_rc` above. A pipeline or formatting command must not replace the Python exit code.

An item is a heading that is (a) at level 2 or deeper, (b) not inside a code fence, and (c) not nested inside the entry counted before it — all three clauses are load-bearing. A TAB separates as well as a space; there is no indent allowance at all. A level-3 child that declares its own `kind`/`lifetime` and, in these two archives, a `gate:`, is ordinary; an UNDECLARED prose subheading there is not — clause (c) counts both the same way because it follows the mover, but meeting one in a v4 folder means the last save aborted or something edited the file afterwards, which is a Step 6 flag. Do NOT substitute a hand-written test of any kind — not `text.startswith("## ")`, not a bare `grep` for the heading marker, not `recall list`, not a regex of your own; use the fence-aware, nesting-aware count above, and never widen its column-0 anchor to admit an indented heading. A deeper level is still an entry until something shallower has been counted in that file. A free-standing level-3 entry appended AFTER a level-2 one is indistinguishable from that entry's subheading by ANY test, so that shape under-counts by one — and the drain produces it on ordinary saves, with nobody hand-editing anything. If you find an entry parked at level 3 under a level-2 neighbour — whether you put it there by hand or the drain filed it there itself — promote it to `##`, or `recall get` returns it inside the level-2 entry's span at exit 0 with no warning. Before you re-measure any of this: a mixed fixture `## M1`, `##<TAB>M2`, `### M4` counts **2, not 3** — that is the mover's own entry model, not a bug.

A MARKDOWN TABLE ROW IS NEVER AN ENTRY, so a project that keeps its plan as a table is invisible to this count and nothing warns. There, count the IDS — rows whose first table cell opens with that project's own work id — not the ROWS, and say so on the `Notes:` line: the count is not the work list. Converting that table to heading entries is the owning project's to do, never yours, and `.ash-id-families` is not the remedy.

Only `NOT COUNTABLE` makes the total `INCOMPLETE`; an ABSENT file does not — its count is 0. Do not "repair" an absent deferred file by writing an empty one. Carry `INCOMPLETE` through to the announce verbatim rather than substituting a digit.

If the command does not run at all, say THAT — never a count. Both diagnostics above are printed BY the snippet, so they reach you only when it ran; when it does not, python exits nonzero, prints a traceback, and there is no `Parked:` line anywhere in the output. Announce `Parked: UNAVAILABLE - the count command did not run: <last line of the error>` and treat it exactly as `INCOMPLETE`; it is not zero and it is not a number.

The `Parked:` line prints on EVERY restore, whether or not anything is parked and whether or not the project is over its budget; zero parked is a real and useful answer. Do NOT print a *condition now met* count or an *unclassifiable* count — omit them, never print them as zero.

#### 7b. The announce

```
Restored {folder} | save: {N} | phase: {phase}

Read: absent[{paths}] whole[{paths}] zero[{paths}]
Parked: {total, or the word INCOMPLETE} - _archive/backlog-deferred.md: {count, or the diagnostic}; _archive/open-questions-deferred.md: {count, or the diagnostic}
Pending work: {count} live items in backlog.md
Open questions: {count} in open_questions.md
Constraints loaded: {count} user-stated constraints. Each is checked before any plan.
Active instructions: {count} in force in active.md
Relay name: {relay_announce_value}
Checkpoints: {count} in history; latest {path} (not loaded)
Notes: {Step 4 missing-file causes, Step 6 divergences; omit this whole line when there are none}

Next task: {first live item in backlog.md}

Ready to begin?
```

`{N}` is `state.md`'s `save:` field, the CURSOR. It is not the `save#N` watermark stamped in a file header, and the two must never be swapped or printed under each other's name — print the field, labelled as the field.

Take the counts from the files you read, not from memory: pending work ← `backlog.md`'s live section, open questions ← `open_questions.md`, constraints ← `CONSTRAINTS.md`'s entry headings, active instructions ← the unexpired entries in `active.md`, checkpoints ← `state.md` → `counts.checkpoints`. Where `counts` and the file disagree, print what the file says and flag it per Step 6. In method-folder mode the first three come from the PROJECT ROOT, not from `.ash_save/`.

All four counts use 7a's predicate plus each source's own filter: `backlog.md`'s live section only, excluding headings between `BACKLOG:BEGIN`/`BACKLOG:END`; `active.md`'s unexpired entries only. The scaffolder plants a worked example inside a text fence in `CONSTRAINTS.md`, `open_questions.md` and `active.md` and nothing ever removes it, so `grep -c '^## '` reads exactly one too high on each — a bare grep gets three of the four counts wrong.

`Constraints loaded:` is the only one of the four that nothing cross-checks: `state.md` → `counts` has no constraints counter and Step 6 compares only the other three. Check the SHAPE, not just the number — if `CONSTRAINTS.md`'s entries sit one level deeper than its banner, clause (c) drops every one of them as body and the count is measuring banners, which belongs on `Notes:`.

If `backlog.md` holds no live item, print `Next task: none - backlog.md has no live items` rather than leaving the field empty. If `files.latest_checkpoint` reads that there is none yet, print `Checkpoints: {count} in history; none written yet` and drop the `(not loaded)` tail, keeping the `{count}` substitution rather than hard-coding a zero.

The template is the minimum, not the maximum: Step 4's missing hot files with the cause when you have one, and Step 6's divergences both land on `Notes:`. Omit the line entirely when there is nothing to say — never print `Notes: none`.

`Parked:` has TWO shapes: a number or `INCOMPLETE` takes the per-file tail above; `UNAVAILABLE` takes NO tail — emit `Parked: UNAVAILABLE - the count command did not run: {last line of the error}` and nothing after it. Inventing per-file values there is forbidden.

Emit the single `{relay_announce_value}` variant Step 6.5 produced (three arms: requested / no-file-or-malformed skip / invalid skip); do NOT include all alternates literally.

#### 7c. Over-ceiling announcement — only when `state.md` says `hot_state: over_ceiling`

A SEPARATE output, sharing no label with `Parked:`:

```
OVER CEILING - loaded in the declared order and stopped.
NOT LOADED: {file}, {file}
Read them with recall - /save deep does not reduce the hot set at this version
```

**Nothing writes `over_ceiling` today** — only a hand-edit of `state.md` can make this announcement fire. It is specified and inert on purpose. Do not read the silence as the feature being live.

Name the files, not sizes: nothing here counts tokens, so say the sizes are not available rather than printing one. A file named in `NOT LOADED` is not thereby handled — it joins your search scope for the rest of the session, and a filename in an announcement is not a retrieval channel.

#### 7d. The `Read:` receipt

Its universe is exactly the eight core files of Step 4 by their primary paths, plus each instruction file
that Step 4 manually read. Never list the host-native file merely because the host injected it, and never
list a byte-identical `other` file that Step 4 deliberately skipped. Never include a path mentioned only
inside a step's prose, a checkpoint, or anything under `_archive/`. One bucket each, JSON-encoded
project-relative path, comma-separated: `absent` = named and not on disk, and it outranks the other two;
`whole` = the read loop terminated on that file; `zero` = everything else (never attempted, errored, or the
loop never reached the end) and states no cause; direct or delegated makes no difference — what is recorded
is whether the loop terminated; a zero-byte file's first read ends the loop, so it lands in `whole` despite
the bucket's name. Omit empty buckets; emit even when all `whole`. It records the restore and never changes
what you load. A partly-read file goes in `zero`; which files an over-ceiling restore stopped short of is
reported separately by name in 7c's `NOT LOADED`.

There is no line arithmetic here: do NOT print a `{read}/{total}` pair or a `part` bucket sized from it, and NEVER go outside the read — to a `wc -l`, a file size, or anything else — to fetch a figure.

---

## CRITICAL: The standing rule

In force from the moment the announce prints until the session ends. It is not part of any step and it does not expire.

> What Step 4 loaded is the WORKING SET, deliberately incomplete. Before you answer any question, propose
> any plan, or write any code: scan the hot spec for relevant ids, then grep `_archive/` AND `checkpoints/`
> for the task's own terms — ALWAYS, not only when the first step hits. A miss in the hot set is not
> evidence of absence; it is the normal case.
>
> Budget: 40,000 tokens of ledger reads per session, cumulative, not per task.
>
> A title is not an answer: never cite an id or restate its title as fact without reading its body this
> session. Absence of content is not evidence of absence — you will not feel the gap.

`recall find` covers `_archive/` and `checkpoints/` by construction and is the shortest way to satisfy that rule; `get` resolves one entry to its exact span without opening the file it lives in — unless a deeper-level entry stands between it and the next heading at its own level or shallower, when the span silently swallows the neighbour at exit 0 (7a). Invoke exactly:

```text
python -S -I ~/.agents/skills/save/scripts/recall.py --root <project-folder> find <term>
python -S -I ~/.agents/skills/save/scripts/recall.py --root <project-folder> get <id>
python -S -I ~/.agents/skills/save/scripts/recall.py --root <project-folder> get <id> --file <path>:<line>
```

- If the helper is not deployed at that path all three commands fail before they run — and a search that did not run is not a clean search. Say so and fall back to reading `_archive/` and `checkpoints/` directly for the term. NEVER report an empty result from a command that never executed.
- Exit 4 = the id is defined at more than one site; `get` prints every site as a ready-made `--file` argument, so re-run with one of them — never a failure to work around by reading the file whole.
- Exit 6 = the scan was TRUNCATED and the output is not evidence though bytes print anyway, so check the exit code: discard what came back, fix the unclosed fence in the file it names, re-run. It applies to every verb and outranks every other code, success included.
- `--root` is REQUIRED and is never inferred from the working directory.

## CRITICAL: Grounding Rules

1. Read all eight core files in Step 4, then execute its host-aware `AGENTS.md` / `CLAUDE.md` branch exactly. `backlog.md`, `open_questions.md` and `CONSTRAINTS.md` encode what is unresolved and what you are not allowed to do; none of them is skimmable.
2. The working set is not the project; a miss in what you loaded is not evidence of absence — see the standing rule.
3. Rationale lives in the entry it qualifies, as a `why-not:` / `refuted:` line, and travels with that entry into `_archive/` when it drains — so never assume the working set holds none. Pre-v4 ADRs are in `_archive/decisions.md`, frozen, never extended or deleted, and not loaded; fetch one with `recall get`, and expect exit 4 there more often than anywhere else. Since v4 no new ADR is written, so do not go looking for one. Anything discussed but never written into an entry is in that session's checkpoint under Key Decisions. Nothing scans the archive for gates that have come true, so a parked entry stays parked until you go and get it; reverse a deferral by hand — move the entry back and add a `gate-satisfied:` line, or open the `gate:` value with the token `SATISFIED`. When a decision's rationale or its rejected alternatives matter, read that entry — do not reconstruct it.
4. Do NOT take any action until the steps are complete and the user has chosen what to work on.
5. Do NOT assume anything beyond what the files state; you have ZERO memory of the prior session.
6. Do NOT retry failed approaches — where the record documents one that failed, learn from it; the latest checkpoint's errors-and-fixes narrative is the fastest place to find recent ones.
7. Stale-state: if `state.md`'s `last_save_ts` is more than 7 days old, warn the user that the codebase may have diverged significantly and suggest verifying assumptions before acting on anything loaded.
