---
name: save
description: "Save full session state to a project folder. Use: 'save', '/save', '/save deep', '/save new', 'save state', 'save session', 'save checkpoint'."
---

# save — persist the session into a project folder

Trigger (default): `/save [folder]` (slash command in CC; natural-language match in Codex/omp).
Other verbs: `/save deep` · `/save new <full-path> <ShortName>` · `/save ship` — § Verb surface.

## Interactive use and automation boundaries

Run this procedure interactively on v4 saved projects. Installation alone is not evidence that
the procedure ran successfully. Hook wiring and unattended/background invocation require a
separate supervised smoke test and the user's explicit approval; this skill enables neither.

Command lines below name the deployed path `~/.agents/skills/save/scripts/…`. For a local pre-deploy round
trip, substitute the project's own `scripts/` at every call site — and record that you did.

Two rules bind a save and live ONLY in `~/.agents/save_artifacts.md`, so read that section — not the file
whole — if you are about to touch either: **§ Manifest exclusions**, the list of names the router deliberately
does not route (an unrouted `node_modules/` or `dist/` is NOT a divergence to report), and **§ Anti-patterns**,
which forbids a project-scoped identifier in a shared file or in anything a shared tool writes into a project
folder — it lands in the host's namespace and either dangles or resolves to a different record.

## Host and helper execution contract

The authoritative skill and helper path is `~/.agents/skills/save/` in Claude Code, Codex and
OMP. Run the bundled helpers with the `python` command exposed by the host. The command examples
below are single-line and work unchanged in PowerShell and Bash unless a block is explicitly
labelled for one shell.

Do not infer the active product from which instruction files exist. Use the actual host: Codex,
Claude Code or OMP. A product-specific branch below is mandatory; never call a Claude Code-only
skill or tool from Codex or OMP and never claim that a skipped product-specific action ran.

**OMP:** resolve `~/.agents/` to the actual user-home absolute path before passing a helper path
to Python; do not assume an external command expands `~` in PowerShell. Use the host's normal
shell/Python route, quote absolute paths and capture the helper process code immediately. A
missing interpreter/helper stops the invocation; do not install dependencies or substitute frozen
v3 helpers. The explicit folder or session cwd selects the save root, not OMP's Git/config root,
skill directory or an ancestor instruction file. Method-folder re-rooting still follows Step 3a.
Use OMP's own read/write/edit tools and its real path-range read syntax, not Claude-only tool
names. Native task delegation is optional; the bounded reduction may run inline. This grants no
hook wiring, unattended save, relay rename or configuration change.

### A helper that did not exit 0 did not do its job — stop the save

Binds EVERY script this file invokes, at every call site — a class, not a list.

After every helper invocation read the exit code. `0` continues. **Anything else stops the save**, unless the
invoking step names that specific code and says what to do instead.

**Read the code FROM THE PROCESS, never from the end of a pipe.** Capture it immediately from
`$LASTEXITCODE` in PowerShell or `$?` in Bash before running another command. If a receipt needs
trimming, capture the helper's output and code first, then trim the captured text.

On a stop: name the command and the exit code, run no later step, do not commit, do not report the save as
saved. Leave any checkpoint Step 5 already wrote where it is. `new` mode has no checkpoint: say what failed and
change nothing further.

## Mission

Two writes, not one: every save writes a COLD record never auto-loaded, and patches a BOUNDED HOT set always
loaded. Content may not be lost; what changed is that where content lands is DERIVED, never chosen by how
important it feels. `~/.agents/save_artifacts.md` § The core move states that contract; the
WRITE-TIME half is here, in § What a hot file may hold.

Nothing is deleted: content leaving a hot file is MOVED to a named file under `_archive/`, keeping its id. An
entry with no id still moves (the drain walks headings) but lands grep-reachable only and the receipt warns
naming it — give an entry an id if you want it addressable by `recall get` once cold.

"When in doubt, write it down" still holds. "When in doubt, keep it hot" does not.

## Plan mode

1. Tell the user: "I'm about to save full session state, but plan mode is active and blocks the writes. Please
   exit plan mode (or have me use this host's plan-mode exit control if I authored the plan) and re-invoke `/save`."
2. Abort the invocation. Do NOT write a partial state, do NOT write a mini-execution plan into the active plan
   file, do NOT use the host's plan-mode exit control unless the user explicitly asks.
3. The user re-invokes after exiting plan mode and the save proceeds normally — a graceful no-op, not an error.

## Unattended mode (`--yes`)

Fully headless, ZERO blocking prompts: every instruction in this file to ask, confirm or wait for the user is
suspended. All correctness rules still apply — the watermark, `--save-num` on every save, the drain,
validation, never dropping content. A folder argument is REQUIRED; if absent, abort with an error, never
prompt. Defaults at each suspended gate:

- Step 3 (folder): use the passed folder verbatim.
- Maintenance: there is no maintenance prompt — nothing here offers to run `deep` and nothing chains into it.
- A constraint that looks universal: write it to the project's own `CONSTRAINTS.md`; never ask whether it
  belongs in a system prompt.
- `new` mode's generic-ShortName question (N-1): do not ask and do not invent one — print the usage and abort.

Recursion guard, single-flight lock and skill-pin are the CALLER's job, outside this skill (pin via
`--plugin-dir` or a SessionStart hook returning `reloadSkills:true`; `--add-dir` grants file scope only).

## The file set

**If a file is not here it does not exist.** Per-file kinds, lifetimes, holdings and drain destinations
are `~/.agents/save_artifacts.md`'s - a section to follow on demand, never a file to load (Step 1).

**Hot — loaded on every restore, ten current files:** `AGENTS.md` · `CLAUDE.md` · `CONSTRAINTS.md` · `active.md` ·
`architecture.md` · `design.md` · `backlog.md` · `open_questions.md` · `state.md` · `manifest.md`. Step 6
patches six of them by hand. A valid legacy v4 folder may have the historical other nine and no `AGENTS.md`.
`new` preserves any existing `AGENTS.md` directory entry opaquely; it applies the historical non-empty regular-file
adoption rule to `CLAUDE.md`. Neither instruction file is written by a Stage 1 save;
`state.md` (the cursor) and `manifest.md` (the router) are GENERATED by `update_state.py` and
`regen_manifest.py`, hand-edited only for the six preserved scalars (§ Regenerate). Between the two spec
files: `architecture.md` takes invariants, contracts between parts, sequencing, ownership, why-not;
`design.md` takes APIs, schemas, algorithms, edge cases, write-path invariants.

**Deferred — cold, but promotable:** `_archive/backlog-deferred.md`, `_archive/open-questions-deferred.md`.
**Every entry MUST carry `gate:`.** Promoting one back by hand is the ONE hand-move the drain sanctions, and
the outbound direction is still forbidden: KEEP the `gate:` line and add `gate-satisfied: <save#, what
happened>` beside it, or rewrite the value as `gate: SATISFIED - <what happened>` — without one, the next
Step 6a re-defers it.

**Cold — `_archive/` is the record: uncapped, never auto-loaded.** `decisions.md`, `research.md`, `misc.md`,
`readme.md`, `architecture-superseded.md`, `design-superseded.md`, `backlog-done.md`, `backlog-dropped.md`,
`constraints-full.md`, `active-expired.md`, `resolved-oqs.md`, `state-prose.md`.

`checkpoints/` stays at the TOP LEVEL, a sibling of `_archive/`, never underneath it — a frozen compatibility
contract.

## Stage 1 — the save sequence

Runs on EVERY invocation — the whole of `/save`, and at this version the whole of `/save deep`.

| Step | | |
|---|---|---|
| 1, 3-5 | Locate the folder, compute save# then scaffold, write the checkpoint | live |
| 6 | Patch each hot file in place, against its own current text | live |
| 6a | DEMOTE — deterministic, unskippable, no LLM in the path | live |
| (no row number — see the rule there) | Regenerate `state.md`, then `manifest.md` | live |
| 7d | VALIDATE — the only step that can abort a save | live |
| 8 | Commit, path-limited | live |

Step ids are the design's and are non-contiguous: Step 2's folder-signal computation is DROPPED (Step 3 takes
the working directory) and steps 6b, 7a, 7b, 7c and 7e are NOT BUILT at this version and run nothing. Do not
renumber, do not conclude a missing step was forgotten and re-derive it, and if you arm one, arm the code in
the same change.

**No size condition ever fails a save**, and neither does an unresolvable pointer. The one abort is 7d.

### Step 1 - the artifact spec is a POINTER, not a load

**Do NOT read `~/.agents/save_artifacts.md` on a save.** THIS BODY IS THE BINDING COPY at write time
(C-55) and already carries what that read was for: the routing rules (§ What a hot file may hold), the file
set (§ The file set) and the watermark form (§ Version watermark). Where the two ever disagreed this body
already won, so loading it can change no routing decision, no drain, no count and no commit.

Follow the pointer only to settle a cross-skill question or to CHANGE the contract. It remains the canonical
cross-skill definition (C-25) and has NOT moved. Two things live only there, and this body cites each by section
where it needs them: § The entry declaration (the field-line grammar and the closed key set) and its
file-set table (each file's kind). Read that section, never the file whole.

Never delete or re-file a documented cold file because the manifest router labels it `unclassified archive` or
unknown to v4; read that as "present under `_archive/` and not a drain destination". Widening the generator's
list is a code change and out of scope here.

### Step 3 — the folder is the working directory; do NOT prompt

The current working directory IS the project folder. Use it — do not ask, do not propose, do not wait for a
reply. An explicit path argument (`/save <folder>`) overrides it; nothing else does. **Do not reinstate the
folder prompt.**

### Step 3a — the method-folder redirect: RE-ROOT, never refuse

Run these three tests at the TOP of Step 4, BEFORE the cursor is read. The order is load-bearing and test 1
MUST precede test 2.

| # | Test | Outcome |
|---|---|---|
| 1 | `{folder}/state.md` declares `schema_version: v4` | normal v4 behaviour, unchanged |
| 2 | `{folder}/intent.md` exists AND (`{folder}/phases/` OR `{folder}/slice-list.md`) exists | method-folder mode |
| 3 | anything else | Step 4's existing branches |

Method-folder mode: `{folder}` becomes `{folder}/.ash_save` for the WHOLE of the rest of this skill — the
cursor read, the checkpoint, the hot-file patches, the drain, both generators, validation and the commit all
take that as their root. The project root is NEVER read, written, drained or opened.

Scaffold `.ash_save` on first use with
`python ~/.agents/skills/save/scripts/init_project.py new --root {folder}/.ash_save --name <ShortName>`,
then proceed as an ordinary v4 save.

**`checkpoints/` goes UNDER `.ash_save/` with everything else — do not move it back to the project root,
however reasonable that looks.** The contract is a relationship under ONE root, not an absolute path. Nothing
needs marking inert and no partial-scaffold verb is to be invented.

### Step 4 — read the cursor and the class test, then ensure the folder

Read `{folder}/state.md` BEFORE anything is written, and take both values from it in the same pass. Cursor and
class test come FIRST, scaffolding second — the design's table has the reverse order and is the copy that
needs correcting; follow THIS file. Neither read writes anything, and that ordering is load-bearing on both
branches.

- **This save's number = `state.md`'s `save:` value + 1**, never counted from the checkpoints.
- If the `save:` line is present but does not parse, STOP and tell the user. Do not guess a replacement.
- If `state.md` is absent the folder was never scaffolded. **`new` mode refuses a COLLISION, never a
  non-empty directory:** exit `2` only when the target already holds a file the scaffolder WRITES — a hot
  file, or one of the two deferred archives. **A `docs/` tree, a stray `.yml`, a session's working files,
  and `checkpoints/`+`_archive/` alone are all FINE and scaffold in place, untouched.** Emptiness was the
  old test and it was wrong: a save fires mid-session, in a folder the session has been working in, so the
  folder is almost never empty at the moment adoption is needed.
  **AN EXISTING `AGENTS.md` DIRECTORY ENTRY OR NON-EMPTY REGULAR `CLAUDE.md` IS NOT A COLLISION.**
  `AGENTS.md` is preserved opaquely, including a symlink; `CLAUDE.md` keeps its historical classifier.
  No template is written there and nothing is appended (C-59, the user's ruling). **The claim is about what
  the tool DOES, not about the bytes** — nothing compares the file across the run, so do not restate
  this as a byte-preservation guarantee. So the single most common
  project shape — a real project with a hand-written charter and nothing else of ours — scaffolds in
  place at exit 0. It used to refuse at exit `2` unconditionally, which meant `new` could adopt no
  existing project at all, and THAT dead end is what three separate agents worked around by inventing a
  `.ash_save/` sub-root. A zero-byte `AGENTS.md` entry is still preserved opaquely. A zero-byte regular
  `CLAUDE.md` still takes its own template because that historical classifier is keyed on content.
  - No collision: that is not a save, it is a scaffold — run § `new` Mode instead, then let the user work
    before the first save.
  - A COLLISION with no `state.md` means a TORN or FOREIGN folder — some hot files exist, the cursor does
    not. Apply Step 3a's test 2 FIRST: a method folder lands in exactly this arm and is a re-root, not a
    dead end. Only when that test also fails: say so and stop. **Do NOT invent a sub-root to get around
    the refusal** — measured 2026-08-29, that is what an agent did when this arm read wider than it does
    now, and the state it wrote is undiscoverable by `restore`'s own glob.
    **WHAT TO DO INSTEAD, because a prohibition with no positive branch is what keeps losing to the dead
    end.** The remedy is always one of three, and never a new root: (1) if the collision is an instruction file
    alone, there is no collision — re-read the paragraph above and scaffold in place; (2) if real v4 files
    are present without a cursor, the folder is TORN — say exactly which files exist, and stop for the
    user rather than writing over them; (3) if the folder belongs to another scheme entirely, it is
    FOREIGN — name it as such and stop. **Stopping and naming the files is a complete answer here.**
    Inventing `.ash_save/` is not a fourth option: it produces state that `restore`'s own glob cannot
    find, so the work is not lost so much as unreachable, which is worse because nothing reports it.

Hold this save's number: every file written or edited in this invocation carries it and it is passed to every
helper. Never bump it twice in one invocation.

FAIL LOUD ON NOT-v4. Anything but `schema_version: v4`, including an absent declaration,
stops this save with exit `4` and no writes. Name the folder and observed declaration. This
distribution includes no conversion tools: preserve the folder unchanged and use the tooling
that owns its declared format, or recover its original v4 cursor if damaged. Do not guess its
version or recommend an unavailable migration skill. Step 3a's method-folder test still takes
precedence. No helper option or API may bypass the non-v4 refusal.

Do not create `checkpoints/` or `_archive/` before that read. Only once `state.md` reads as `v4`: ensure
`{folder}/`, `{folder}/checkpoints/` and `{folder}/_archive/` exist. A hot file missing from a populated
folder: create it with its watermark line and its `# ` heading and nothing else, **except `AGENTS.md` and
`CLAUDE.md`**. A Stage 1 save never creates or changes either instruction file. Report a missing `AGENTS.md`
as a valid legacy pre-dual-file state; report a missing `CLAUDE.md` as externally damaged. Continue the save
without creating either. Do not send an
already-v4 folder to `new`: it correctly refuses that shape. The project owner may add the
missing host instruction file explicitly; never overwrite one that exists.

### Step 5 — write the checkpoint

Generate a local-time timestamp `YYYY-MM-DD-HHMMSS` and write a NEW file at
`{folder}/checkpoints/checkpoint-{timestamp}.md` using § The checkpoint template; first line is the watermark.

- The name must be new and the file must be non-empty; never rewrite an existing checkpoint in place.
- Same-second collision → append `-2`, `-3`.
- Cold half: uncapped, worth nothing against any budget — be exhaustive.
- The session's NARRATIVE goes here and nowhere else — what broke, what was measured, what a check returned,
  who said what, what you corrected — and writing it here means not writing it into a hot file.

### Step 6 — patch each hot file in place

Six files are patched by hand: `CONSTRAINTS.md`, `active.md`, `architecture.md`, `design.md`, `backlog.md`,
`open_questions.md`. `AGENTS.md` and `CLAUDE.md` are NOT written by a Stage 1 save. `state.md` and `manifest.md` are generated,
with one exception you also patch here: the six preserved scalars in `state.md` (`phase`, `status`, `goal`,
`active_track`, `repo_root`, `task_list_id`) — no flag sets them, so this is the only place a meaningful value
is ever written.

Patch each file against its OWN CURRENT TEXT: read it, change the sections the session changed, leave the rest
alone. **Never regenerate a hot file from `checkpoints/` or `_archive/`** — that wire is cut on purpose. No
`## Update — {date}` wrappers in a hot file; the per-save narrative is the checkpoint.

#### What a hot file may hold — and where everything else goes

A hot file holds CURRENT STATE ONLY: standing rules, what the project IS right now, what is still to do.
Everything that already happened is cold. Write-time test: would a session that has never seen this project
need this sentence in order to CONTINUE THE WORK? If it only explains how the work got here, it is history.

BANNED from every hot file — nothing is deleted, each class has a destination:

| do not write this into a hot file | it goes to |
|---|---|
| peer conversation — relay ids, who said what, who verified whom, what a peer replied | the checkpoint |
| verification receipts — hashes, selftest counts, gate output, exit codes, `VERIFIED AT SOURCE` | the checkpoint |
| session narrative — what was measured, what broke, what was caught, what a run returned | the checkpoint |
| a correction of an earlier version of the same text | nothing. Delete it |
| a transferable lesson the AGENT derived | `CONSTRAINTS.md`, as a rule, in ONE LINE |
| a contract, schema, gate table, algorithm or invariant | `architecture.md` / `design.md`, as a `spec` entry |

The correction row is a rule, not a preference: writing the corrected sentence IS the fix — delete the
superseded text, never keep it beside the correction.

Rows 3 and 5 can both claim the same sentence (narrative → COLD; transferable lesson → HOT in
`CONSTRAINTS.md`). Do not resolve that from the table — the deciding test is the one stated in § Step 6
ends by REDUCING.

**NEITHER `AGENTS.md` NOR `CLAUDE.md` IS A DESTINATION IN THIS TABLE OR ANYWHERE ELSE IN THIS FILE, AND THAT IS THE WHOLE
POINT OF THE ROW ABOVE.** No Stage 1 save writes either — each holds the project's own facts
and the rules its OWNER wrote by hand, and a save that adds to it turns the one always-injected file
into the dumping ground it must never be. **A rule the AGENT earned is not the owner's rule**, so it
goes to `CONSTRAINTS.md` beside the user's, under that file's own agent-derived heading, never here.
This is stated three times in this body on purpose: the destination row and the prohibition used to sit
far apart, and the two drifted into contradicting each other unnoticed.

Required of every item in `backlog.md` and `open_questions.md`:

1. Its own heading at the file's top entry level — not a bold run, not a list item, not a table row. A bold
   line is prose: Step 6a cannot see it, `recall get` cannot fetch it, any marker inside it fires nothing.
2. A real `done:` FIELD when finished — `done: save#N - <one line>` at indent 0-3. A bold `DONE` in the body
   fires nothing.
3. Under 1,200 bytes from its heading to the byte before the next heading; past that, move the excess to the
   checkpoint and leave the ask. The ceiling is a WARNING, never a failure.
4. This shape:

```
## <id> - <the remaining ask, one line>
kind: plan
lifetime: conditional - until <the ending event>
Status:  <where it stands>
Context: <only what a fresh session needs in order to act>
Action:  <the next concrete step>
Blocks:  <what it waits on, or none>
```

`Status:`/`Context:`/`Action:`/`Blocks:` are PROSE LABELS, not fields — none is in the closed key set, so
nothing parses them and nothing can break because of them.

None of these four can fail a save: 7d reports each breach as a WARNING naming file, line and id, and the save
completes.

#### Every entry declares a kind and a lifetime

`~/.agents/save_artifacts.md` § The entry declaration is the authority for the field-line grammar, the
closed key set and derived loading; its file-set table maps each file to its kind. What YOU must do:

- Declare both. `kind:` is `rules` · `spec` · `plan` · `record` · `unresolved`; `lifetime:` is `permanent` ·
  `conditional` · `expiring`.
- Kind: a standing instruction about behaviour that could not be false → `rules`; falsifiable about how the
  subject is built right now → `spec`; not done yet → `plan`; names the road not taken, or a date/save# on
  which something happened → `record`.
- Lifetime: naming the event that would end it is absurd → `permanent`; you can name the event →
  `conditional`, **and you MUST write that name down on the same line**; it stops being true even if nobody
  touches this project again → `expiring`, in bounded saves whatever happens. `for now` is not a lifetime:
  name the event or take `permanent`. An entry that can do neither does not enter a hot file.
- Loading is DERIVED from those two, never declared. **Never write "hot" or "cold".**
- Kind questions disagree → SPLIT the entry into two, one per kind, each with its own id, cross-referenced.
  Never filed under both in one place, never under a guessed single kind. This is the normal case.
- Kinds agree but lifetimes do not → take the SHORTER lifetime.
- You cannot split it → `kind: unresolved` naming BOTH candidate kinds, inside `active.md`'s
  `<!-- UNRESOLVED -->` region, closed with `<!-- /UNRESOLVED -->`, with the default 3-save expiry. It is a
  DEADLINE, not a fifth kind: at expiry it is split or it demotes, and it is never renewed.

One kind per file, enforced at entry level: an entry whose declared kind is not its file's declared kind ABORTS
the save at 7d. The check reads the declared kind only, never the body — so declare the answer to the kind
questions, not the answer that keeps the save quiet.

#### No new ADRs

`_archive/decisions.md` is FROZEN: never extended, never deleted. Rationale lives inside the `spec` entry it
qualifies, as `why-not: <alternative> — <one line>` and `refuted: <claim> — <the measurement that killed it>`.
One line each; a why-not needing a paragraph is a `record` — put it in the checkpoint.

#### How content LEAVES a hot file: you write a marker, Step 6a moves it

| you write | on | 6a moves it to |
|---|---|---|
| `done:` | a `backlog.md` item | `_archive/backlog-done.md` |
| `RESOLVED` in the heading | an `open_questions.md` entry — status position only | `_archive/resolved-oqs.md` |
| `superseded by <id>` | an `architecture.md`/`design.md` section — opening a heading part, or leading a body line; see below | `_archive/architecture-superseded.md` · `_archive/design-superseded.md` |
| `gate: waiting on <id>` or `gate: EXTERNAL - <observable>` — those two forms only | a `backlog.md`/`open_questions.md` item | the two deferred archives |
| `wont-do:` | a `backlog.md` item — will not do, not "not now" | `_archive/backlog-dropped.md`, only under `--rewrote-complete` |
| `expires save#N` | an `active.md` entry — mandatory at write time | `_archive/active-expired.md` |

A near-miss does NOT fire: it warns, names the form it wanted, and leaves the entry HOT — recoverable, but only
if the warnings are read. The two that catch authors:

- `RESOLVED` fires only in STATUS POSITION — `## OQ7 RESOLVED 2026-08-05 - <answer>`,
  `## OQ7 - RESOLVED - <answer>`, bare `## OQ7 RESOLVED`. Not: `## OQ7 - the verdict is RESOLVED`,
  `RESOLVED: <answer>`, `RESOLVED (2026-08-05)`.
- `superseded by <id>` fires opening a `-`-separated heading part, or leading a body line — only
  indentation, one list bullet and one emphasis opener may precede it, and the id must be the very next token
  and resolve to a real entry. Fires: `## D4 - superseded by D9`, `Superseded by D9.`, `- superseded by D9`,
  `**superseded by D9**`. Not: `## D4 - the old shape, superseded by D9`, `> superseded by D9`.

A `gate:` in any wording but the two forms above leaves the item hot. Prose naming an event
(`gate: when the broker migration lands`) resolves no id and is REFUSED: the item stays where it is, the drain
warns naming it and why, and still exits `0`. A gate naming an OPEN QUESTION is refused deliberately — a
waiting line records why something is parked, never what it depends on. So is a gate failing the validator's
disclaimer test, which the drain applies before admitting one: a FIXED WORD LIST of outright disclaimers,
uncertainty words AND ordinary vague-time idioms, so `gate: we will revisit it eventually` is caught. On a hot
item the drain refuses it, exits `0`, and WARNS — read that warning and fix the wording in place, in the hot
file; the same value on an entry ALREADY in a deferred archive ABORTS the save at 7d. Fix that IN PLACE,
inside the deferred archive, until the value names a condition in words that list does not match — do NOT
move the entry back into `backlog.md`, which is the promotion path for a gate that has actually been
*satisfied* and does not fix the wording that aborted. Leave the MARKER line alone while you do it: change
the marker and you have not escaped the byte comparison, you have MOVED it — a different row routes the
entry to a different archive file, and where the id resolves NOWHERE there the entry is appended at exit
`0`, no refusal and no warning, the id left defined in two files. A sentence that names no
condition and uses none of those words DOES pass: it is a word list, not a judge — do not describe it as the
latter, and do NOT assume vagueness by itself is safe. Cheapest of all: write the observable in
condition-shaped words the first time — name WHAT HAPPENS, not when.

A save can move nothing and still report success — READ THE WARNINGS. The refusal is intended, not a bug to
route around.

#### A marker is only HALF the test — a hot file that still CITES the entry VETOES the move

Two questions gate every move and BOTH must pass: does the entry carry a marker, and is any hot file
still CITING its id? A marker is an ENTRY-LOCAL test, and the reference that would be left dangling is
somewhere else entirely. So the row PROPOSES and the veto REFUSES: a cited entry stays hot and is named
under `VETOED` in the receipt, with each blocker's `file:line`, the citing entry's own id and title, and
WHICH STATE that citing entry is in. There are eight, and only the FIRST is work you can finish:

- **LIVE -- unfinished work** -- ESTABLISHED, never inferred: the citation sits INSIDE a scanned
  entry, and that entry declares no disposition any drain row of its own file reads. The ONLY
  state that is work you can finish, and the only one REPRIORITIZE fires for. Finish it.
  It used to be the RESIDUAL, reached by eliminating the others, so every unforeseen condition
  landed here and was announced as work to go and do.
- **leaves this save** -- the citation sits inside another entry that IS moving, so it goes with it.
  Re-run the drain next save; there is nothing to do now.
- **also VETOED this save, and it cites this entry back** -- a true MUTUAL HOLD. No amount of
  finishing clears it, because both are already finished. Drop ONE of the two citations. This
  is decided per PAIR: a blocker that is in a cycle with some OTHER entry is not in one with
  this one, and is reported as merely also-vetoed.
- **also VETOED this save** -- that entry is held too, and nothing more is claimed. It does NOT
  mean the two cite each other; whatever is holding IT is what to look at first.
- **its own move was REFUSED this save** -- it was not vetoed at all; its move failed for an
  unrelated reason. Read the REFUSED block. Repairing that is what releases this entry.
- **above the first heading** -- the citation belongs to no entry at all. There is nothing to
  finish; edit the line. An entry is a heading at level 2 or deeper, so a level-1 banner is not
  one and text under it is preamble -- including a banner that appears AFTER an entry, which
  ends that entry both for the move span and for attribution.
- **no drain row** -- the citing file can never go cold on its own, so only editing or dropping
  that citation releases the entry.
- **NOT established as unfinished work -- inspect or drop the citation** -- the residual, made honest.
  EITHER the citing entry already declares a disposition a row of its file reads (`done:`,
  `gate:`, `wont-do:`, RESOLVED, `superseded by`, an expiry) that no row acted on this save,
  OR no scanned entry contains the citation at all. The two causes share ONE state
  deliberately: splitting them further is the mistake this state exists to end, and the
  marker set is DERIVED from the citing file's own rows rather than typed. Inspect or drop
  the citation. Do NOT go looking for work to finish, and do not assume a WARNING names the
  reason -- several causes emit none.

**The REPRIORITIZE warning fires only when EVERY blocker is LIVE**, because in every other case
naming the blocker as work to prioritise would be advice that cannot be taken.

**The veto only ever REDUCES what moves.** It can never delete, never moves anything a marker did not
already select, and never fails a save — the exit code stays `0`.

What does NOT veto, and not one of these is a judgement call:

- a citation in `_archive/` or `checkpoints/` — the entry is MOVING TO JOIN THEM.
- a citation inside the entry's own move span — it travels to the archive with the entry.
- `manifest.md`, and `state.md`'s `BACKLOG-ENTRY-IDS` region — a generator rewrites both AFTER the
  drain, from the post-drain files, so neither citation survives this save. The REST of `state.md` DOES
  veto: `goal` and `active_track` are preserved byte for byte, so a citation there outlives the save.

**Clearing a veto is an EDIT, never a flag — there is no override.** When the receipt says every blocker
is LIVE WORK it names that work: finishing it is what releases the entry. When a blocker sits in a file
with no drain row, the only thing that releases it is dropping or rewording that citation, in place, in
the hot file. **Do NOT move the entry by hand instead** — that is the deletion the whole drain exists to
rule out.

**An entry with no resolvable id CANNOT be screened at all.** The veto screens by id, so it does not
refuse the entry and no cross-file check is performed; the receipt says `VETO BLIND`. Read whether it
actually moved from the MOVED / REFUSED / FAILED blocks, not from that note -- the note is written
before the move is attempted, and a later refusal can still contradict it. Give an entry an id if you
want it screened, before the move and after it.

**A move that only deletes a hot DUPLICATE is exempt, and the receipt says `VETO EXEMPT`.** When a
byte-identical copy of the entry is already in the archive, the move appends nothing and the id keeps
resolving from cold storage, so no citation can be left dangling -- while holding it hot would leave
the id defined twice and make a lookup ambiguous. The exemption is stated rather than silent, because
an exempted entry and a screened-and-cleared one are otherwise indistinguishable to a reader.

**A file in the veto's read scope ending with an OPEN FENCE holds EVERY proposed move**, warns, and
still exits `0`. Those entries are listed under `HELD`, NOT under `VETOED`: when the scan could
not be trusted, no citation has been established and none is claimed -- `VETOED` is only for
entries whose blockers are actually named. A citation the scan could not see is indistinguishable from one that is not there, and
"not there" is the value that licenses a move. **A machine-managed region in `state.md` that opens and
never closes is the same refusal**, reached by a different blinding mechanism: everything below the
unclosed marker can be read neither as machine text nor as author text, so every proposed move is held
and the receipt names the line to close. Neither refusal deletes anything and neither changes the exit
code.

**Never move an entry out of a hot file by hand.** Mark it and let the drain do it. And integrate before you
mark: before writing `RESOLVED` or `superseded by`, make sure whatever forward work needs is in
`architecture.md` / `design.md` as a spec entry with its `why-not:` line. After the mark it is cold, and cold
is reachable but not resident.

`active.md` is the only file that expires. `expires` is MANDATORY and BOUNDED — default 3 saves out, maximum
10; the schema has no unbounded option.

- A BARE INTEGER IS AN ABSOLUTE SAVE NUMBER, NEVER A COUNT: at save 14 the default-3 horizon is
  `expires save#17`, while `expires: 3` means expired at save 3 — it validates clean and the drain archives the
  entry on the very save you wrote it. For a count, SPELL THE UNIT (`3 saves`, `three saves`, `in 3 saves`,
  `+3`), resolved relative to the entry's asserted save#, still bounded at ten.
- Relative forms hold for the `expires:`/`expiry:` FIELD and nowhere else: in a HEADING the parser keeps the
  digits and discards the unit, so `expires in 3 saves` there means save#3 and archives the entry one-way on
  the save you wrote it, and `expires: three saves` in a heading has no digit and aborts A5. In a heading write
  `expires save#N` and nothing else; write every relative form as a FIELD. TRAP: the validator's abort message
  says "for a relative bound write `3 saves`" while pointing at the HEADING line — obeying it there arms the
  fault. Add the `expires:` field instead; when both are present the field wins.
- Provenance is a verbatim user quote plus the save# it was said in, and THE AGENT CANNOT RENEW: a renewal
  needs a fresh quote from the CURRENT session and creates a NEW entry.

`backlog.md` carries a machine-managed region delimited by `<!-- BACKLOG:BEGIN -->` and `<!-- BACKLOG:END -->`
— **exactly one ORDERED pair**. **Leave that region BYTE-IDENTICAL**; everything outside it is yours. No
markers at all disables row 2b only (`ROW 2b DISABLED for backlog.md`, and row 2 still runs); an unpaired or
out-of-order pair prints `ROWS 2 AND 2b DISABLED` and takes the `done:` drain for the whole file down with it,
at exit `0`.

**Never drop content:** anything this session produced with no hot home goes to `_archive/misc.md`, whole —
cold, uncapped, enumerated in the manifest, reachable by grep.

#### Step 6 ends by REDUCING, so that Step 6a has something to drain

Runs on EVERY save, AFTER patching the files and BEFORE `demote.py`, so the drain sees the markers this pass
wrote and evicts on the same save. Skip it and finished work is never marked, the drain moves nothing, and a
hot file can only grow — silently, at exit `0`.

FIRST GET THE LIST — do not reduce from memory:

```text
python ~/.agents/skills/save/scripts/validate_entries.py --root {folder} --report
```

`--report` cannot fail this save: it exits `0` even with a non-empty abort set and never writes. Take its
`A9`..`A13` warnings as this pass's worklist. Ignore its `A1`..`A8` abort lines here — those are 7d's business.
A non-zero exit from this ADVISORY run is not a stop: note it and carry on.

Reduce `backlog.md` and `open_questions.md` first, then each remaining hot file:

1. Mark what is finished: every `A9` — a body announcing its own completion with no `done:` field — gets
   `done: save#N - <one line>` and 6a evicts it this same save. Do NOT infer completion from tone; the body has
   to say it. An answered `open_questions.md` entry gets `RESOLVED` in status position instead.
2. Promote work that is not an entry: a bullet, bold run or table row carrying its own status is work the drain
   cannot see — give it a heading at the file's top entry level, an id, and a declaration.
3. Route narrative out: off-contract content (the six banned classes) is MOVED into the checkpoint this save is
   already writing, or into the hot file where it belongs. A move, not a deletion.
4. Reduce an oversize item: keep the ask and the four labelled lines, move the history to the checkpoint; if it
   must stay addressable by id, move it whole to `_archive/misc.md` and leave one line naming where it went.
5. Bound `state.md`'s `goal` and `active_track` (`A13` reports their size): carried forward BYTE-FOR-BYTE every
   save, and `state.md` has NO drain row, so nothing else in the pipeline can shrink them. Keep only what a
   fresh session needs to continue RIGHT NOW; route the rest to the file that owns it, and standing RULES found
   in that prose to `CONSTRAINTS.md` as one line each — HOT, not archived; a rule that is not loaded is not
   obeyed. Never to `AGENTS.md` or `CLAUDE.md`: no Stage 1 save writes either file.

THE DECIDING TEST for items 3 and 5, and it is not optional:

> Do not ask "is this narrative". Ask: would deleting this sentence make some future measurement cost full
> price again? If YES it is a RULE — one line in `CONSTRAINTS.md` — and it stays HOT whatever it looks like.
> Only if NO does it go to the checkpoint. **ONE LINE is the contract, not a suggestion**: the incident that
> taught the rule is what goes to the checkpoint, and a rule that arrives as a paragraph is the mechanism by
> which a hot file silently grows without any drain row ever firing.

Do the editing in a fresh-context worker when the active host exposes one, and adjudicate the result yourself:
in Claude Code use its subagent mechanism; in Codex use native agent collaboration when available. If the host
has no worker surface, perform the bounded pass inline rather than pretending a Claude-only tool exists. The
pass proposes edits against the file's own current text; you accept or reject each one. Four bounds, each a
thing the pass must NOT do:

- It NEVER deletes content, with one exception — the correction-of-earlier-text class, whose superseded text is
  by construction already in a checkpoint. If you are not certain of that, MOVE it rather than delete it.
- It NEVER rewrites an entry's MEANING to make it shorter: it moves history out and leaves the ask in the words
  that were there.
- It NEVER touches the machine-managed BACKLOG region.
- It CANNOT fail the save. If it cannot run, say so in the report and continue to Step 6a. A skipped reduction
  costs bytes; a refused save costs the session.

### Step 6a — DEMOTE

Deterministic, unskippable, no LLM in the path. **No in-project instruction may disable it.**

```text
python ~/.agents/skills/save/scripts/demote.py --root {folder} --save {N} --rewrote {ID} [--rewrote {ID} ...] [--rewrote-complete]
```

- `--save {N}` is this save's number — the same integer as everything else in this invocation.
- `--rewrote {ID}` for every `backlog.md` entry you rewrote in Step 6 (repeatable). Writing `wont-do:` on an
  entry IS rewriting it, but row 2b skips anything in the `--rewrote` set — so mark a `wont-do:` entry and
  LEAVE IT OUT of `--rewrote`, or expect the drop on a later save.
- `--rewrote-complete` asserts that list is EXHAUSTIVE, and only then may a present item be evicted as
  abandoned. Assert it only when it is true; it is a necessary condition, never the trigger on its own.
  `_archive/backlog-dropped.md` is ONE-WAY. Without `--rewrote-complete` that row writes nothing at all — the
  safe default.

The receipt is stdout and it is the ONLY record of the move: its first line names every entry that left and
where it went, or reads `demote: 0 entries moved`. Put that first line, any `pending_drop:` line, the WHOLE `VETOED`
block if there is one, and every NOTES and WARNING line in your report to the user — an eviction nobody is told about is indistinguishable from
a deletion.

**Write every hot-file entry at `##`.** A `###` inside a `##` entry is NOT an entry to the drain: it rides into
the archive inside its parent's bytes, named nowhere, and a marker written on it is INERT and unwarned, so
"read the warnings" cannot catch it. Split any live `###` child into its own `##` entry before you mark the
parent, and promote a sub-entry to `##` before marking it. A `###` IS an entry to 7d — it must declare its own
`kind:` and `lifetime:`, plus a `gate:` once the drain has filed its parent into a deferred archive, or the
save aborts on it. 6a runs before 7d, so fix them in place, IN THE ARCHIVE.

Only `0` continues; here `3` is a move that failed its byte-equality check and `6` a file in scope ending with
an open fence. On `3` or `6` additionally: run no later step, and IN
PARTICULAR DO NOT WRITE THE CURSOR — the cursor is the record of a completed save. `6` outranks everything: the
scan did not read the whole corpus, so its verdict is not an answer. Name the file, leave the checkpoint where
Step 5 wrote it, tell the user.

### Regenerate the cursor and the router — placed by a rule, not by a row number

Regenerate after the last step that mutates `backlog.md` and before 7d validates — at this version, immediately
after 6a. **Do NOT record a row number for this** — it is wrong the moment the set of armed steps changes. Do
NOT fold it into Step 8: 7d validates the tree about to be committed, so anything generated after 7d ships
without having been validated.

```text
python ~/.agents/skills/save/scripts/update_state.py --root {folder} --save-num {N} --checkpoint-ts "{YYYY-MM-DD HH:MM:SS TZ, from Step 5}"
python ~/.agents/skills/save/scripts/regen_manifest.py --root {folder} --save-num {N} --checkpoint-ts "{the same timestamp}"
```

In THAT order — the manifest generator reads the cursor. Both must exit `0`.

- `--save-num` is NOT optional on any save; pass it to BOTH, and the reason differs. On `update_state.py`
  nothing else advances `save:`, and a number that does not strictly advance is read downstream as a torn save;
  on `regen_manifest.py` omitting it merely reads the cursor `update_state.py` just wrote — accidental
  correctness that breaks silently when the order changes.
- `--checkpoint-ts` is NOT optional either: `update_state.py` PRESERVES the existing value when the flag is
  omitted, so omitting it while passing `--save-num` writes a new save number beside the PREVIOUS save's
  `last_save_ts`, and `restore`'s staleness check keys on that timestamp.

**Never hand-edit what the two generators compute:** all of `manifest.md`, and in `state.md` the watermark, the
title, the GENERATED CURSOR note, `schema_version:`, `project:`, `save:`, `last_save_ts:`, the `files:` and
`counts:` blocks, and the BACKLOG-ENTRY-IDS value line. Everything BELOW `counts:` except that value line is
the PRESERVED TAIL, carried forward verbatim — an edit there persists for the life of the project.

The six preserved scalars are the exception and hand-editing them is the DOCUMENTED way to set them: `phase`,
`status`, `goal`, `active_track`, `repo_root`, `task_list_id` are carried forward verbatim and no flag exposes
any of them, so a line nobody edits keeps the scaffolder's placeholder forever.
**TWO OF THE SIX TAKE A CLOSED SET OF VALUES AND NOTHING VALIDATES EITHER — do not coin one:**
`phase` is `discovery` | `design` | `implementation` | `maintenance` | `shipped`, and `status` is `active`.
The scaffolder writes `discovery`; every later value is set here, by hand, and persists for the life of the
project. Edit those six in STEP 6, in
place against the file's own current text like any other patch, and let STEP 8 commit `state.md`. The rest of
the preserved list — `hot_state`, `backlog_order`, `task_id_high_water`, `no_demote_streak` — is machine
bookkeeping this version does not compute: leave it alone.

### Step 7d — VALIDATE — the only abort

```text
python ~/.agents/skills/save/scripts/validate_entries.py --root {folder} --save {N}
```

Read-only: it never writes and never fixes. **This is the ONLY step that may abort a save.** The rules are the
validator's, not this file's: the abort set is CLOSED at `A1`..`A8` and nothing may be added to it, and
the six content warnings `A9`..`A14` can NEVER fail a save — each names the file, the line and the entry id
(`A13` names the field, `A14` the dangling id). You do not evaluate these rules; the script does. Run it and
read the code.

Exit codes: `0` valid or warnings only, continue · `2` usage or I/O · `3` the abort set is non-empty · `6` scan
integrity, a file in scope ends with an open fence · `7` internal error. `2` and `7` mean the validator did not
run, so the tree about to be committed was never checked — that is not a pass. `6` outranks both `0` and `3`
(under an unterminated fence every entry below it is invisible to the scan): **treat 6 as a stop, not as a
pass.**

On abort (exit `3`): move the checkpoint Step 5 wrote to `checkpoints/aborted-{timestamp}.md` — same timestamp,
TOP LEVEL, a sibling of the other checkpoints, never under `_archive/`. Name every failing entry and the
`file:line` the script printed, and stop. Undo NOTHING else — leave the drain's moves on disk, leave the
regenerated `state.md` and `manifest.md` where they are, and COMMIT NOTHING (Step 8 never runs). The report
MUST say all three: `save:` has advanced without a commit so the aborted number is skipped;
`latest_checkpoint:` names a path that no longer exists and `counts.checkpoints` is one too high; and
`manifest.md`'s Checkpoints router carries the same two stale values.

Report `A12` as a LOWER BOUND, never as a total: it matches phrasings that actually occur and narrative using
none of them is invisible to it, so a small `A12` count is not evidence that a file is clean.

NOT in the abort set, deliberately: a size breach, a split-trigger breach, an unresolvable pointer and an
over-budget `why-not:`/`refuted:` line. A dangling reference is now `A14`, warning-only: it fires only where a
HOT file cites an id defined nowhere. Cold-only citations are history (C-44), and duplicate ids in
legacy byte-exact preimages are not this warning's population. A `--file` run skips it and says so.

### Step 8 — commit, path-limited

Commit an EXPLICIT PATH LIST, built from every path this save wrote — derive it from what actually changed on
disk, never from a remembered category.

**The command is `git commit -m "<message>" -- <paths>`, and the PATHSPEC is what makes the commit
path-limited, not the `git add` that precedes it.** `git add <paths>` followed by a bare `git commit` commits
the WHOLE INDEX, sweeping in a peer's staged work under this save's message. Both spellings leave a working
tree that looks correct — the wrong one is silent, not loud.

- A foreign staged index in the same tree MUST survive this save untouched. `git add -A` stays forbidden, AND
  an explicit `git add <paths>` followed by a bare `git commit` fails in exactly the same way — the pathspec
  form is the only spelling that holds this property.
- **A PATHSPEC MATCHES ONLY WHAT GIT ALREADY KNOWS, so `git add` the NEW files first — and on every single
  save the new file is the CHECKPOINT.** Step 5 writes a checkpoint that has never been tracked, so naming
  it in the pathspec matches nothing; and ONE unmatched path aborts the WHOLE commit, taking the paths that
  WOULD have matched down with it. MEASURED 2026-09-01, four arms in a scratch repo:
  `git commit -m … -- checkpoints/checkpoint-….md backlog.md` returns
  `error: pathspec 'checkpoints/…' did not match any file(s) known to git`, **exit 1, nothing committed,
  `backlog.md` still modified**. The sequence that works is `git add <the new checkpoint>` and then the SAME
  `git commit -- <paths>`.
  **THIS DOES NOT WEAKEN THE BULLET ABOVE, AND THE DISTINCTION IS THE COMMIT, NOT THE ADD.** What sweeps a
  peer's index is a BARE `git commit` after staging; a `git commit -- <paths>` is bounded by its pathspec
  however the files got staged. Measured in the same run with a peer's `A  peers_file.md` staged across the
  whole sequence: the commit contained exactly the two pathspec'd files and that entry survived staged and
  uncommitted. `git add -A` is still forbidden — stage the paths you name, nothing else.
  **Where a project GITIGNORES `checkpoints/` or `manifest.md`, do the opposite: leave them OUT of the
  pathspec** rather than adding them, and never `git add -f`. The pathspec you build must contain only
  paths git will accept — tracked already, or staged by you in the line above.
- Do NOT turn that rule back into a checklist. The obvious categories leave things out, and the worst omission
  is that THE DRAIN MODIFIES THE HOT FILE IT DRAINS FROM — commit the archived copy without its source and the
  tree holds the same id twice with `Saved` printed beside it. Memory aid, not a list: `git status` on the
  paths this project owns, minus anything you did not write. The two most-missed members are `state.md` and
  `manifest.md`.
- The archive destinations must land in the SAME COMMIT as the stubs and ids that point into them.
- Nothing detects a crash between the write and the commit at this version; the recovery is to RUN THE SAVE
  AGAIN (6a is idempotent over a completed move and Step 4 recomputes the number), and the git half is neither
  completed nor rolled back.
- A crash INSIDE THE DRAIN is a narrower window and the byte-comparison covers it: `demote.py` writes and
  verifies the destination FIRST and splices every source at the end, so an interruption between the two
  leaves the entry in both places. The re-run recognises an **ID-BEARING** entry already at the destination —
  id resolving at exactly ONE site there, payload matching that site's span byte for byte — and then splices
  the hot copy and appends nothing. DIFFERENT bytes, or the id resolving at TWO sites, is a CONFLICT: not
  moved, left hot, warned by id with both paths. **An entry whose heading resolves to NO citemap id is not
  recognised at all — the re-run appends a SECOND COPY at exit `0`, with no duplicate warning, and the receipt
  still reads `1 entry`.** That is the second reason to give an entry an id; § Mission is the first.

If there is no repository to commit to, THE SAVE STILL HAPPENED: skip the commit, say so on its own line in the
report, and still report the save as saved. `git` exits `128` there, and that is the ONE benign code. The
user's fix is `git init` in the folder, with whatever `.gitignore`/`.gitattributes` seeds the workspace
expects.

**EXIT 1 IS A STOP, AND THIS PARAGRAPH USED TO SAY IT WAS NOT.** It read that `1` means the pathspec named a
path the enclosing repo ignores and was therefore not a stop — which invited exactly the wrong conclusion,
because **every exit-1 cause measured here committed NOTHING AT ALL**, the good paths included. An agent
following the old sentence reports a save as saved over a tree where nothing landed.

**AND THE TWO CAUSES ARE INDISTINGUISHABLE — same code AND same message**, measured 2026-09-01:
`error: pathspec '<p>' did not match any file(s) known to git` is what git prints whether `<p>` is IGNORED or
merely UNTRACKED. So the code cannot tell you which you hit, and neither can the text. Both mean the same
thing about the outcome, which is why one rule covers both: **you built the pathspec wrong — an ignored path
does not belong in it, and an untracked one needed `git add` first. Fix the list and re-run the commit.**

**DO NOT DECIDE THIS FROM THE EXIT CODE. CONFIRM THE COMMIT EXISTS.** Read `git log -1 --format=%H` before and
after, or `git status --porcelain` over the paths you named: a clean result for them is the only evidence the
commit happened, and it costs one command. Any other nonzero `git` code is unnamed and still stops the save.

#### Then report to the user

```
Saved {folder} as save #{N}.

  - checkpoint: checkpoints/checkpoint-{timestamp}.md
  - hot files patched: {names}
  - demote: {the receipt's first line, verbatim}
  - state.md and manifest.md regenerated
  {IF a pending_drop line fired: - pending drop: {ids} — vanished from backlog.md with no `done:` marker}
  {IF the commit was skipped: - commit: SKIPPED — {why: not a git repository, or ignored by the enclosing one}}
  {IF the receipt printed a NOTES block: one bulleted line each, verbatim}
  {IF any helper emitted WARNING lines: one bulleted line each, verbatim}

To resume in a fresh session: /restore {folder}
```

**Quote helper output; never paraphrase it.**

## Stage 2 — `/save deep`

At this version `deep` runs Stage 1 and nothing else: accept the verb, run the sequence above, and say in the
report that the maintenance work is not built yet.

## Verb surface

| verb | |
|---|---|
| `/save [folder]` | Stage 1 |
| `/save deep [folder]` | Stage 1 — § Stage 2 |
| `/save new <full-path> <ShortName>` | Scaffold a new project folder — § `new` Mode |
| `/save ship` | The design names this verb and specifies no steps for it. This version does not implement it: say so and STOP, rather than guessing what shipping should do |
| `/restore [folder]` | The read half — a separate skill |

## `new` Mode

Bootstraps a NEW project folder and sets the cross-session relay name. It SKIPS THE SAVE FLOW ENTIRELY — no
session to summarise, no hot file to patch, no checkpoint, no drain.

Args: (1) full-path, required, absolute, folder basename = ShortName; (2) ShortName, required,
`^[a-zA-Z0-9._-]{1,64}$`.

### Step N-1 — validate the arguments

- Both arguments present?
- ShortName is the USER'S LITERAL NAME (default: the folder basename) — preserve case, underscores and hyphens
  VERBATIM, never PascalCase it or otherwise re-style it, because it becomes the cross-session relay alias.
- ShortName matches the pattern; and is NOT generic — a bare version like `v2.0`, a bare number, or a
  placeholder like `test`/`tmp`/`demo`. If it is, say so and ask for a real one.
- The path must be absolute (Windows: a drive letter; POSIX: a leading `/`).

If any check fails: print the usage, abort, and do NOT proceed.

### Step N-2 — run the scaffolder

```text
python ~/.agents/skills/save/scripts/init_project.py new --root <full-path> --name <ShortName>
```

The verb and `--root` are REQUIRED and the flag form is the only one accepted. `--name` is optional — PASS IT
ANYWAY; omitted, the scaffolder falls back to the folder's basename, handing the relay alias to whatever the
path happened to end in.

It creates the folder, `checkpoints/` AT THE TOP LEVEL and `_archive/`; writes the two deferred files and
the hot files that are not ADOPTED -- ten on a fresh folder, NINE when it adopts one instruction file,
EIGHT when it adopts distinct existing `AGENTS.md` and `CLAUDE.md`; sets `state.md` to `save: 0`,
`phase: discovery`, `project: {ShortName}`; and REFUSES only
a directory that already holds a file IT WRITES.

**Exit codes: `0` scaffolded; `2` means one of TWO different things and they need opposite responses.**
Read the first `error:` line, which says which.

- **A REFUSAL — a COLLIDING target, a bad name, a bad `--carried` path.** The line opens `refusing to
  scaffold:` or is a usage message. Nothing was written. STOP and report. Do NOT scaffold by hand.
- **A WRITE FAILURE — the raw OS error, with no `refusing` and no `COMPLETED`.** The folder may be
  PARTLY written: the loop stops where it failed. **Do not re-run** — the files that did land make the
  folder a collision and the second run refuses it, which reads like a different fault. Inspect the
  folder, say what is in it, and report. (Measured: a write that fails mid-loop printed
  `error: [WinError 5] Access is denied: ...design.md.tmp-write -> ...design.md` over a folder holding
  2 of 10 hot files and an orphaned `.tmp-write`.)
- **A POST-SCAFFOLD IDENTIFIER CLASH — the line opens `the scaffold COMPLETED`.** Every write that run
  planned succeeded; what is wrong is an id with MORE THAN ONE definition once an adopted `CLAUDE.md`
  is counted, so `recall get <id>` would exit 4 from save#0 (C-4). **The duplicates are not
  necessarily split across files** — both can sit inside the adopted charter, with no template defining
  the id at all. Read the sites the message names rather than assuming which file is at fault; it says
  which are adopted and how many renames leave exactly one definition. **Do not report a failed scaffold
  and do not re-run `new`** — a second run refuses the folder as a collision, correctly, and that is not
  the remedy. Rename, then **CONTINUE TO N-3**: the folder still has no relay name, and skipping that
  step is the damage reading this exit code as a failure invites.


### Step N-3 — set the relay name

This is product-specific. `relay-name` is a Claude Code session-registry skill (A11); Codex does
not expose that identity substrate through this skill.

**Claude Code:** preserve the existing behavior and run:
Before invoking it, require the optional `agent-messaging` installation's existing
`~/.agents/skills/relay-name/scripts/set_name.py`. If absent, report `Relay name NOT set:
agent-messaging relay-name is a prerequisite`; preserve the successful scaffold and do not
fabricate rename success or substitute an old private helper.

```bash
cd "<full-path>" && python ~/.agents/skills/relay-name/scripts/set_name.py --name "<ShortName>"
```

**The `cd` is REQUIRED:** the helper takes no path argument and writes `.relay-name.json` at its working
directory, and `<full-path>/.relay-name.json` is the ONLY file `restore` reads to re-apply the alias. Drop
the `cd` and two projects break — the new folder never gets the file, and whichever project cwd sits in has its
own overwritten. That `cd` pins the SUBPROCESS's working directory, not the session's. Do NOT use
`Skill("relay-name", "--name <ShortName>")` here — that form cannot set a working directory, so it writes
the file in the wrong folder.

- `0`: name set, continue.
- `2` (invalid name): ERROR — fix the name and re-run.
- `1` (no session resolvable): WARNING — the scaffold succeeded; the user can set the name manually.
- `4` (registry lock busy): WARNING treated exactly like `1` — continue to N-4, quote the helper's own message,
  re-run the name step in a moment. Named deliberately so a busy lock does not halt `new` after the folder is
  built.

**Codex:** do not run the helper, do not invoke a Claude-only `Skill` surface, and do not create or
rewrite `.relay-name.json`. Continue to N-4 with `Relay name NOT set by save in Codex — the
Claude Code relay-name step is not available.` This is a truthful skip, not an error in the scaffold.

**OMP:** do not run the Claude helper, create `.relay-name.json`, or derive a broker identity from
the folder/project slug. Continue to N-4 with `Relay name NOT set by save in OMP — the
Claude Code relay-name step is not available.` OMP's native relay is a separate contract; this
save does not rename the session through it.

### Step N-4 — print the summary

```
Initialized {ShortName} at {full-path}
   - {N} hot files scaffolded, plus _archive/ and the two deferred files
{FOR EACH scaffolder ADOPTED line: '   - {AGENTS.md or CLAUDE.md} ADOPTED: it was already there and nothing was written to it'}
   - checkpoints/ created at the top level
   - state.md: save: 0, phase: discovery, project: {ShortName}
{IF the relay helper exited 0: Relay name set: {ShortName}}
{IF it exited 1: Relay name NOT set — {the helper's own message, verbatim}. Set it by hand before relying on cross-session messaging.}
{IF running in Codex: Relay name NOT set by save in Codex — the Claude Code relay-name step is not available.}
{IF running in OMP: Relay name NOT set by save in OMP — the Claude Code relay-name step is not available.}

Note: your session's working directory is unchanged. Use absolute paths for this project.
Note: no git repository was created here. To version this folder, `git init` it (with whatever
      .gitignore / .gitattributes seeds this workspace expects) before the first save — otherwise
      Step 8 will skip the commit and say so.
For save:     /save {full-path}
For restore:  /restore {full-path}   (in a fresh session)

Next: start working. When you have made meaningful progress, run:
   /save {full-path}
```

### In `new` mode, do NOT

- Work around a COLLISION refusal — the helper refuses only a target already holding a file it writes, and
  that refusal is protecting live content. Never force it, and never scaffold into a sub-directory to
  dodge it. A merely non-empty directory is not a refusal and needs no workaround. **An existing
  `AGENTS.md` directory entry or non-empty regular `CLAUDE.md` is not a refusal either** — `AGENTS.md`
  is preserved opaquely and `CLAUDE.md` is adopted; neither needs a flag.
- In Claude Code, run the relay-name step. In Codex, skip it and do not emulate the Claude Code-only helper. In OMP, skip it: do not run the Claude helper or create `.relay-name.json`.
- Try to change the session's working directory. Skills cannot; print the note instead.
- Add files beyond the file set. Extensions live under `_archive/` or a subdirectory, after the first save.
  (`.relay-name.json` is not an exception: not a v4 artifact, dot-prefixed, excluded from the manifest router.)
- Set `phase` to anything but `discovery`.
- Run `new` against a folder that is ALREADY AN `save` PROJECT — one carrying `state.md` or any other
  hot file besides `AGENTS.md` and `CLAUDE.md`. **This line used to read "against an existing project" and that is now
  wrong in the case that matters most:** adopting an existing project is exactly what `new` is for since
  2026-08-30. Hand-written instruction files and nothing else of ours are the ordinary adoption shape, not a
  violation. What stays forbidden is re-scaffolding a folder this tool already manages.

## Version watermark

Every file this skill writes or edits carries `<!-- ash_save v4 | save#N | YYYY-MM-DD HH:MM:SS TZ -->` on its
FIRST LINE.

- REPLACE the previous watermark, never accumulate them. `N` is this save's number, computed once in Step 4
  and reused for every file in the invocation.
- `state.md` and `manifest.md` carry their own generator-written watermark — do NOT hand-edit either watermark,
  or anything else those two generators compute; the preserved scalars are the one exception.
- The scaffolder writes a FOUR-field watermark of its own (`… | save#0 | YYYY-MM-DD HH:MM:SS | scaffolded by
  init_project.py -->`, no timezone). Do NOT "correct" a scaffolded file to the three-field form; the next
  save's patch replaces it normally.
- The drain does not re-stamp: a destination file it creates gets
  `<!-- ash_save v4 | created by demote.py (step 6a) -->` with no save number or timestamp, and a hot file it
  drained from keeps its old watermark — so the drain's receipt, not the watermark, is the record of what
  moved. The re-stamp is a code change outside this phase's scope.

`save:` is the CURSOR FIELD in `state.md` and must strictly advance every save; `save#N` is the WATERMARK
stamped on a file. Never interchange them. A file's watermark may lag the cursor — usually because nothing
touched it, but on `active.md`, `backlog.md`, `open_questions.md`, `architecture.md` or `design.md` it may
instead mean 6a drained an entry out of that very file this save. Check the drain receipt before concluding a
file was untouched. **The CURSOR never lags itself**; writing one where the other is meant is how a save that
worked comes to be reported as torn.

## The checkpoint template

Write EVERY section: it is cold, uncapped, counts against no budget, and for some of what it holds there is no
second copy. No hot file holds a decision LOG, so § Key Decisions is the record of WHY — except a
`why-not:`/`refuted:` line, which lives inside the `spec` entry it qualifies and travels with it into the
superseded archive; anything needing more than that one line belongs in the checkpoint. NOTHING regenerates
from the checkpoint: no hot file may be built from `checkpoints/`. Write it for a human and for a future
session, not for a generator.

```markdown
<!-- ash_save v4 | save#N | YYYY-MM-DD HH:MM:SS TZ -->
# Session Checkpoint — {timestamp}

## Summary
One or two sentences: what this session was about, and why.

## Primary Task
What the user asked for — verbatim where possible. If the task evolved, capture both the original ask and the
current understanding.

## Current Status
What is completed and what is not. Be specific: file names, function names, line numbers, commit hashes. This
tells the next session what it may trust.

## Files Touched
### Created
- (new files, full paths)
### Modified
- (existing files changed, and what changed in each)
### Read/Referenced
- (consulted, not changed)

## Key Decisions
Choices made, approaches selected, trade-offs, and the reasoning. Capture decisions DISCUSSED but NOT
IMPLEMENTED — there is no ADR file to fall back on.

## Errors & Fixes
What went wrong, the root cause, the fix applied, and why it worked. If nothing went wrong: "None."

## Important Context
What a fresh session needs that is not obvious from the artifacts and is not already in a hot file: platform
and environment constraints, preferences the user expressed this session, names/acronyms/jargon introduced this
session. Do not restate `AGENTS.md`, `CLAUDE.md` or `CONSTRAINTS.md` — they are loaded on every restore.

## Pending Work
Only what THIS session left unfinished, and why it stopped. Do not duplicate `backlog.md`.

## Living Docs Diff
Which hot files this save changed, one line each, saying what changed.

## Restart Instructions
1. `/restore {folder}`
2. {anything project-specific a fresh session must do before working}
3. {the next thing to pick up}

## References
Specs, plans, issues, PRs. Full paths for local references.
```

## Anti-patterns

Do NOT:

- Write to `docs/checkpoints/` at a repo root, or nest `checkpoints/` under `_archive/`. It is a top-level
  sibling of `_archive/`, and that is a frozen contract.
- Create a file that is not in § The file set.
- Summarise vaguely. Name the file, the symbol, the line, what changed and why.
- Omit file paths.
