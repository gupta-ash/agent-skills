# save Artifacts — Canonical Living-Doc Spec

The single cross-skill definition of what a project folder contains, how each file is read, and
where content goes. **Schema: v4.**

**Source of truth:** `_shared/save/docs/runtime/save_artifacts.md` in agent-skills.
**Deployed to:** `~/.agents/save_artifacts.md` on every machine, by copy.
**Edit the source, never the deployed copy.**

**This file carries CONTRACT AND DOCUMENTATION ONLY**, under two rules. No token counts, no
cost-benefit findings, no measurements — a measurement here is a defect; delete it, do not refresh
it. No project-scoped identifier (`D`/`F`/`OQ`/`C-`/`M`/`W`) appears here either.

---

## The v4 file set

The current shape holds **ten hot files**, an `_archive/` directory, and a `checkpoints/`
directory. A valid legacy v4 folder may hold the historical other nine with no `AGENTS.md`;
save and restore report that shape as pre-dual-file, not damaged. Nothing else is canonical.

### Hot — loaded on every restore

| File | kind | lifetime | Holds |
|---|---|---|---|
| `AGENTS.md` | rules | permanent | Codex project instructions. Host-owned and opaque to save validation. Not written by a Stage 1 save. |
| `CLAUDE.md` | rules | permanent | Claude Code project behavioural rules. Rules only, never narrative. Not written by a Stage 1 save. |
| `CONSTRAINTS.md` | rules | permanent | User-stated hard rules: a verbatim quote plus one implication line. |
| `active.md` | rules | expiring | Temporary instructions, each with a bounded expiry and verbatim provenance. |
| `architecture.md` | spec | conditional | Invariants, contracts between parts, sequencing, ownership. |
| `design.md` | spec | conditional | APIs, schemas, algorithms, edge cases, write-path invariants. |
| `backlog.md` | plan | conditional | **Live work only**, plus the machine-managed backlog region. |
| `open_questions.md` | plan | conditional | Unresolved branches, each with a named resolution trigger. |
| `state.md` | plan | conditional | **Generated cursor.** Rewritten every save. |
| `manifest.md` | rules | permanent | **Generated router.** One row per class, never per file. |

`state.md` and `manifest.md` are GENERATED and are exempt from *requiring* an entry declaration.
**The exemption is narrow:** a declared value present but outside the vocabulary is still a
violation.

### Dual instruction-file contract

`AGENTS.md` and `CLAUDE.md` are independent permanent hot files. `new` preserves any existing
`AGENTS.md` directory entry opaquely, including a symlink; it keeps the historical rule for
`CLAUDE.md`, adopting a non-empty regular file and templating an absent or zero-byte file. A Stage 1
save writes neither instruction file. No conversion procedure is included; existing saved
instruction bytes are not normalized, appended or migrated by this public distribution.

Codex natively loads `AGENTS.md`; Claude Code natively loads `CLAUDE.md`. Restore never reads the
active host's already-loaded native file again. Claude Code uses its ancestor discovery rule.
Codex uses only the chain from its project root down to cwd; without a project root it uses cwd
only. Restore reads the other file when that file exists and differs byte-for-byte from the native
file. When the target instruction root lies outside the active discovery chain, restore reads that
folder's native file too. Byte-identical instruction files are loaded once. Distinct files are both
loaded once.

### Deferred — cold, but promotable

| File | Holds |
|---|---|
| `_archive/backlog-deferred.md` | Pending work gated on a future event. Every entry MUST carry `gate:`. |
| `_archive/open-questions-deferred.md` | Open questions gated on a future event. Same rule. |

The only cold files that are not a record: mutable and reversible. To bring an entry back, move it
into the hot file by hand, KEEP the `gate:` line, and add `gate-satisfied:` beside it — or rewrite
the value as `gate: SATISFIED - <what happened>`. Without one of those the entry still carries an
unsatisfied gate and the very next drain re-defers it. Nothing scans the archive for gates that have
come true; a parked entry stays parked until a person fetches it.

### Cold — the record. Uncapped, never auto-loaded.

`_archive/` also holds the one-way drain destinations listed in § The drain, each created by the
move engine on first use.

An existing v4 folder may retain older format records or preimages, named in the router under
an `unclassified archive` row: read that as *present under `_archive/` and not a drain
destination*, never as deletion authority. These records are preserved, not re-converted.

### `checkpoints/`

**Top level, a sibling of `_archive/`, NEVER a child of it.** A frozen compatibility contract, not a
preference: the out-of-band curator's postcondition is a non-recursive listing of
`{folder}/checkpoints`.

### Not v4

**`schema_version` in `state.md` is the folder's class test.** Anything but `v4`, including absent,
means the folder is not v4; **the test is the declaration, never a filename.** The three tools that
write into a project folder each refuse such a root, name the folder and the remedy, and write
nothing, with exit `4`. **On a non-v4 root, say so and stop.** No format conversion is
included. Preserve the folder unchanged; use its owning format's tools, or recover its original
v4 cursor if damaged. **Do not scaffold, regenerate or cite a file retired by v4.** A known
older layout is not a damaged v4 project; never infer its version from absent files.

---

## The core move

**Hot artifacts hold CURRENT STATE ONLY. History moves to `_archive/` and stays resolvable by id, so
an eviction degrades to a resolvable stub and never a deletion.**

- **Nothing is deleted.** Content leaving a hot file is MOVED, keeping its id, and stays reachable
  by grep and by `recall`. An entry with no id still moves, reachable by grep only.
- **Nothing enters a hot file because it seems important.** A hot file holds what the project IS now
  and what is still to do. Everything that already happened is cold. The session's own narrative is
  cold and uncapped — that is what the checkpoint is for, and there you should be exhaustive.

**The WRITE-TIME contract is in `save/SKILL.md` § What a hot file may hold** — the content
classes banned from a hot file with the destination for each, and the required shape of a
`backlog.md` / `open_questions.md` item. Breaches are `validate_entries.py` warnings `A9`–`A12`, and
not one of them can fail a save.

---

## The entry declaration

An **entry** is a heading at level 2 or deeper. A heading that has a deeper child and whose body
declares no field is a section banner, not an entry, and needs no declaration.

Every entry declares two fields in its body:

    kind: spec
    lifetime: conditional - until the pilot re-derives the threshold

- `kind:` — one of `rules` · `spec` · `plan` · `record` · `unresolved`. Two kinds on one entry is a
  violation.
- `lifetime:` — one of `permanent` · `conditional` · `expiring`. A `conditional` MUST name its
  ending event on the same line.
- A **field line** is `key: value` at indent 0–3, key matching `[A-Za-z][A-Za-z0-9_.-]*`, outside a
  code fence. A field may sit anywhere in the entry body, not only under the heading.
- Only a closed key set is interpreted. Anything else on a `key: value` line is prose — `Note:` and
  `Decision:` are sentence openers, not fields.

**Loading is DERIVED from `(kind, lifetime)`, never declared.** `record` is cold, always. `plan`
with an unsatisfied `gate:` is deferred — cold but promotable. `rules`, `spec` and ungated `plan`
are hot. **Nobody writes `hot` or `cold` anywhere.**

`kind: unresolved` is a **deadline, not a fifth kind**: it names both candidate kinds, carries a
bounded expiry, and lives in a delimited region of `active.md`.

**An expiry is a SAVE NUMBER, never a date.** An entry is out of force when its expiry is `<=` the
current `save:`. The drain's test is an integer comparison.

---

## Routing table — where does content go?

Ask what the content IS, not how important it feels.

| The content is… | Goes to |
|---|---|
| a behavioural rule the AGENT derived | `CONSTRAINTS.md`, under its agent-derived heading, in ONE LINE |
| a hard rule the USER stated, in their words | `CONSTRAINTS.md` |
| a temporary instruction with a bounded expiry | `active.md` |
| an invariant, a contract between parts, ownership, sequencing | `architecture.md` |
| an API, a schema, an algorithm, an edge case | `design.md` |
| work still to do | `backlog.md` |
| a question with a named resolution trigger | `open_questions.md` |
| work waiting on a future event | `backlog.md` with a `gate:` — the drain files it |
| what happened this session: what broke, what was decided, what was tried | the checkpoint |
| the reasoning behind a choice | a `why-not:` / `refuted:` line INSIDE the entry it qualifies |

**Since v4 no new ADR is written.** Architecture and design are SPECS, not decisions; rationale
travels with the entry it qualifies and goes cold with it.

**Nothing is ever dropped.** Content with no clear home goes to `_archive/misc.md`, moved to a real
home when one emerges.

---

## The drain — deterministic, unskippable, no model in the path

Every trigger is a marker an author WROTE. None is inferred and none is a judgement.

| Source | Trigger | Destination |
|---|---|---|
| `active.md` | expiry `<=` current save | `_archive/active-expired.md` |
| `backlog.md` | an explicit `done:` | `_archive/backlog-done.md` |
| `backlog.md` | an explicit `wont-do:` a person wrote, absent from the file, outside the machine region | `_archive/backlog-dropped.md` |
| `open_questions.md` | `RESOLVED` in the heading, in status position | `_archive/resolved-oqs.md` |
| `architecture.md`, `design.md` | `superseded by <id>` | `_archive/{architecture,design}-superseded.md` |
| `backlog.md`, `open_questions.md` | an unsatisfied `gate:` | the two deferred files |

`AGENTS.md`, `CLAUDE.md`, `CONSTRAINTS.md` and `manifest.md` have no source drain row. `AGENTS.md`
is also excluded from validation, reference scanning and cross-file drain vetoes; save does not
open it. The existing Claude Code behaviour for `CLAUDE.md` remains unchanged.

**A gate is a CONTRACT, not a sentence.** It either names an id that resolves, or it opens with an
explicit external marker. A `gate:` that reads as prose does not defer an entry — it holds it hot
and says so.

**Every move keeps the entry's id, and safety is verified BEFORE the source is touched:** the
destination is written, re-read, and the moved entry's span there must equal the span re-read from
the source. A span that fails is dropped from the batch, the entry stays hot, and the run says which
id. Failure is per-move, never per-save.

**A HOT FILE THAT STILL CITES THE ENTRY VETOES THE MOVE.** A marker is entry-local; the reference it
would leave dangling is not. The row proposes and the veto refuses: the entry stays hot, and the receipt
names each blocker's `file:line`, the citing entry's id and title, and which of five states that entry
is in -- live work; leaving this save; itself vetoed AND citing back, which is a mutual hold, computed per
PAIR rather than assumed from the citer; itself vetoed without any such cycle; refused this save for an
unrelated reason; belonging to no entry at all; or sitting in a file with no drain row. Only the first is work that can be finished, and
the reprioritize warning fires only when every blocker is in it; naming any of the others as work to
prioritise would be advice that cannot be taken. A hold is always attributable rather than silent -- and an entry the veto could not SCREEN is
reported separately from one it screened and blocked, because an unreliable scan establishes no
citation and claiming one would send a reader after a reference that may not exist. An
entry with no resolvable id cannot be screened by an id-keyed veto at all, and the receipt says so; so
is a move that only deletes a hot duplicate of an entry already archived byte-identically, which is
exempt because nothing can be left dangling. `_archive/` and `checkpoints/` never veto — the
entry is moving to join them — nor does a citation inside the entry's own span, nor a generated region
that a generator rewrites after the drain has run. **The veto only ever REDUCES what moves: it cannot
delete, and it cannot fail a save. There is no override flag — a veto is cleared by editing the
citation.**

**A destination that already defines the moving id with DIFFERENT bytes is a CONFLICT:** not moved,
left hot, reported by id with both paths. The engine cannot break that tie.

---

## Retrieval and the resolvability contract

Files plus bounded patterns. **No database anywhere on the restore path**, and **the agent never
writes a pattern.** `recall` has exactly five verbs — `list`, `get`, `find`, `check`,
`frontier`. There is no sixth.

- `get` resolves an id to its exact span and writes RAW BYTES — no line-number prefix, no header. It
  NEVER picks between two definition sites; it reports the ambiguity and prints each site as a
  ready-made argument. **Ambiguity is a question to answer, not a failure to work around.**
- `find` ALWAYS also covers `_archive/` and `checkpoints/`, appended after whatever scope the caller
  gave; that coverage cannot be excluded.
- `frontier` never reads `checkpoints/` or `_archive/`, and is counts-only.
- **Scope is DECLARED in every verb.** The project root is required and is never inferred from the
  working directory.
- **Every id pattern carries a right boundary.** A bare prefix match also matches the longer
  numbers.
- **An unbalanced code fence VOIDS the verdict.** The report or span is still emitted; the exit
  status says it is not an answer.

**Resolvability is proved, not asserted.** `recall check` resolves every cold citation and
asserts id-uniqueness across the hot files and `_archive/` together. There is no stub-marker scheme:
the whole entry moves with its id intact, and resolution is a lookup that returns it.

---

## `state.md` — the generated cursor

Rewritten every save from bounded inputs. **Do not hand-edit**, with one exception noted below.

    schema_version: v4
    project: <short name>
    phase: <discovery|design|implementation|maintenance|shipped>
    save: <integer>
    last_save_ts: <YYYY-MM-DD HH:MM:SS TZ>
    status: active
    goal: |
      <prose, carried forward verbatim>
    active_track: |
      <prose, carried forward verbatim>
    repo_root: <path or a stated sentinel>
    hot_state: <scaffolded|over_ceiling>
    backlog_order: []
    no_demote_streak: 0
    files:
      manifest: manifest.md
      latest_checkpoint: checkpoints/checkpoint-<ts>.md
      archive: _archive/
    counts:
      hot_files / checkpoints / backlog_items / open_questions / active_instructions

- `save:` — column 0, lowercase, no space before the colon, a bare integer. ONE unparsable
  `save:`-prefixed line makes the out-of-band curator report the save torn.
- `goal` and `active_track` are PROSE carried forward verbatim. They are the one part a person
  edits, and they are edited in place before the save runs, not after.
- `hot_state`, `no_demote_streak` and `backlog_order` are preserved byte-for-byte. Nothing in the
  built set computes or interprets them.
- **The counts include only entries whose id resolves** — an entry titled in plain prose, or at a
  deeper level, is real but uncounted. A count that disagrees with the file is USUALLY this, not a
  torn save; do not report it as one.
- **The generator refuses rather than guesses.** Every non-blank input line must be re-emitted
  verbatim, be a field it deliberately recomputes, or be named in a warning. A line that is none of
  the three aborts the write and lists the offending lines, leaving the file on disk untouched.

---

## `manifest.md` — the generated router

**One row per CLASS, never one row per file.** A class is a `(kind, lifetime)` pair. A manifest that
has collapsed into a per-file listing is a generator bug.

It enumerates what is ON DISK under the root — never a declared list — so it is regenerated AFTER
the drain.

### Manifest exclusions

Not routed: dot-prefixed names; the directories `.git`, `.venv`, `__pycache__`, `_smoke`, `dist`,
`node_modules`, `verification`, `fiddler_extracts`, `mitm_captures`; any directory whose name begins
`_run_`; the extensions `.har`, `.saz`, `.tgz`, `.zip`; and the generator's own write-temp suffix.

`_archive/` is **NOT** excluded — routing to it is the whole job.

**Any other tool that asks "does the router name every file here?" MUST apply this same exclusion
list.** Share the predicate, or assert the copies equal; do not write a second list from memory.

---

## Version watermark

Every file `save` writes or regenerates opens with an HTML comment naming the schema and, where
its writer has them, the save number and the timestamp. **There is a FAMILY of forms, not one form,
and which one a file carries is decided by WHICH WRITER produced it:**

    <!-- ash_save v4 | save#<N> | <TS> | written by <tool> -->    the two generators
    <!-- ash_save v4 | save#<N> | <TS> -->                        a hot file the agent patches by hand
    <!-- ash_save v4 | save#0 | <TS, no TZ> | scaffolded by init_project.py -->
    <!-- ash_save v4 | created by demote.py (step 6a) -->         an archive destination, on creation

**Do NOT "correct" one form into another.** They are not variants of a single intended form; each
records which writer last owned the file, and rewriting one loses that. A pre-v4 file may carry no
`v4` token at all - a survivor, not damage, and nothing rewrites it.

**NOTHING VALIDATES ANY OF THIS.** No helper asserts a watermark form, so this section is
documentation and can never be a gate. **The watermark is a provenance record, not a cache key** -
never gate behaviour on it, and never compare two files by their watermarks. The timestamp may not
contain a newline, a carriage return, or a comment terminator.

`save/SKILL.md` § Version watermark is the BINDING copy at write time (C-55) and carries the
per-writer detail; this section is the cross-skill statement of the same contract.

---

## Modes

| Mode | What it does |
|---|---|
| default | The save sequence: write the checkpoint, patch each hot file in place, drain, regenerate the cursor and the router, validate, commit path-limited. |
| `deep` | At this version, identical to default. |
| `new <full-path> <ShortName>` | Scaffold a project folder: the ten hot files, `_archive/` with the two deferred files, and `checkpoints/`. **Refuses a COLLISION — a directory already holding a file it writes — never a merely non-empty one.** Any existing `AGENTS.md` directory entry is preserved opaquely; an existing non-empty regular `CLAUDE.md` is adopted under the historical rule. There is no partial-scaffold mode. |
| `ship` | Reserved. |

**`init` is retired; the verb is `new`.**

Two rules the save sequence depends on:

1. **The save number comes from `state.md`, plus one — never from counting the checkpoints.** If the
   `save:` line is present but unparsable, STOP and say so.
2. **A helper that did not exit zero did not do its job — stop the save.** This binds every helper
   at every call site. On a stop: name the command and the code, run no later step, do not commit,
   and do not report the save as saved. **Read the code from the process, not from the end of a
   pipe.**

**The only step that may abort a save is validation**, and every abort is a missing or contradictory
DECLARATION. **No size condition anywhere fails or truncates a save**, and neither does an
unresolvable pointer.

---

## Anti-patterns

- **Regenerating a living doc from history.** Hot files are patched in place against their own
  current text.
- **Summarising into an archive.** The drain moves whole entries byte for byte. Nothing summarises,
  re-words, or deletes.
- **Writing `hot` or `cold` into a file.** Loading is derived; a declared tier drifts.
- **A count without names.** Name the ids; the id is the only handle that survives a move.
- **Keeping content hot because it feels important.** Importance decides nothing. What the content
  IS decides where it goes.
- **A calendar date as an expiry.** The test is an integer comparison against the save number.
- **A check whose fixture cannot reach the branch it guards.** If the failure mode cannot make the
  check go red, the check is decorative. Prove it by re-introducing the defect.
- **A project-scoped identifier in a shared file, or in anything a shared tool writes into a project
  folder.** It lands in the host's namespace, where it either dangles or — worse — resolves to a
  different record and reports nothing.

---

## Consumers

**NOTHING LOADS THIS FILE ON A ROUTINE RUN.** It is the canonical cross-skill definition (C-25),
followed as a POINTER when a cross-skill question has to be settled or the contract is being changed.
`save` and `restore` each carry the binding copy of what they need in their own body (C-55),
and each says so in its own Step 1.

| Points here | For | How |
|---|---|---|
| `save` | § The entry declaration - the field-line grammar and the closed key set; the file-set table's per-file kind | on demand, cited by section |
| `restore` | the artifact contract, if Step 4's list ever has to be reconciled | on demand |

**If this file disagrees with a skill's own body, the skill body wins and the disagreement is a
defect to report.** Both `save` and `restore` say so in their own Step 1, and each proceeds
on its own list.
