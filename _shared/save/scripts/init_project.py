#!/usr/bin/env python3
"""init_project.py - save v4 SCAFFOLDER (work items W9 + W30). Verb: `new`.

WRITES the hot files of spec S3.1 that are not already accounted for, `checkpoints/` at the
TOP LEVEL, `_archive/`, and spec S3.2's two deferred archives inside it.

WHAT THE FOLDER HOLDS AFTERWARDS DEPENDS ON THE MODE, AND THE OPENING VERB HERE TWICE
PROMISED OTHERWISE -- first as "creates the TEN hot files", then as "ENSURES ten hot
paths", which is the same universal wearing a weaker word. All four outcomes, enumerated so
no later reader has to trust a summary:
  - FRESH: ten hot files written, folder COMPLETE.
  - ADOPTING one existing non-empty instruction file: nine written, folder COMPLETE.
  - ADOPTING distinct existing `AGENTS.md` and `CLAUDE.md`: eight written, folder COMPLETE.
  - `--carried`: fewer written, folder DELIBERATELY INCOMPLETE until the caller lands those
    paths. `--carried CLAUDE.md` exits 0 with that hot path ABSENT, by design.
  - A WRITE FAILURE at exit 2: partly written, and neither complete nor untouched.
The receipt counts the WRITES rather than the constants. Writes
nothing else and refuses a directory that already holds a file it WOULD WRITE -- a COLLISION,
never merely a non-empty directory. Other content is none of its business.

    python -S -I init_project.py new --root <dir> [--name <ShortName>]
    python -S -I init_project.py --selftest

CONTRACT: the destination set is the TEN files of S3.1, not v3's fifteen. `README.md`,
    `decisions.md`, `research.md`, `roadmap.md`, `misc.md`, `restart-prompt.md`, `_glossary.md`
    and `index.md` are NOT v4 hot files and are not created. Planting them would put eight files
    outside the S3.1/S3.2 file->kind table into the folder, where `validate_entries.py` reports
    each as "not in the table -- OUT OF SCOPE, kind never guessed" and checks none of it.

CONTRACT: `_archive/` and both S3.2 deferred files are created HERE, at scaffold time (W30).
    VERIFIED 2026-08-03 by reading the v3 helper (`~/.agents/scripts/migrate_v3/init_project.py`
    line 412): it creates `checkpoints/` and nothing else. A v4 folder scaffolded that way has no
    destination directory for step 6a row 5, so the move engine must either fail on the SUCCESS
    path or drop a live gated item -- and a dropped gated item is a C-4 deletion, the one outcome
    the whole demote design exists to prevent.

CONTRACT: `checkpoints/` is a TOP-LEVEL sibling of `_archive/`, never a child of it. The
    out-of-band curator's postcondition is a NON-RECURSIVE listdir of `{folder}/checkpoints`;
    relocating the directory makes a perfect save report itself torn.

CONTRACT: exit codes are the fleet table (0/1/2/6/7), not the v3 helper's. VERIFIED 2026-08-03
    by reading that helper: it returns 1 for a non-empty directory, for a bad name and for a
    write failure. In v4, 1 is RESERVED for `--selftest` and nothing else may return it -- a
    caller that reads 1 as "the tool is broken" would misread every ordinary refusal, and one
    that reads it as "a refusal" would misread a broken tool. Those are exit 2 here.

INVARIANT: a freshly scaffolded folder VALIDATES. `--selftest` runs `validate_entries.py`
    over the scaffold, in-process and again as a real `python -S -I` subprocess, and asserts
    exit 0 on both. A scaffolder that plants files its own validator rejects is broken on
    arrival, and the only way to know is to run the validator, not to reason about it.

INVARIANT: nothing planted here is FABRICATED content. A fresh project has no architecture, no
    design, no user-stated constraints and no temporary instructions, so those files are planted
    with guidance and a fenced example and NO entries. `architecture.md` / `design.md` are
    `spec`/`conditional` and `CONSTRAINTS.md` / both instruction files are `rules`/`permanent`: step 6a
    drains those only on an explicit `superseded by <id>` marker, so a placeholder entry planted
    there is permanent. The one planted entry (`backlog.md` M1) is genuine work that drains
    normally through row 2 once it is done.

INVARIANT: files are written as BYTES with LF terminators. Python's text mode is a NORMALISER --
    it writes `os.linesep`, so on this box every template would ship CRLF while the source string
    and every assertion on decoded text still read LF. Same reason `validate_entries.py`'s own
    fixture writer (`_st_write`) refuses text mode.

INVARIANT: exactly ONE ordered `<!-- BACKLOG:BEGIN -->` / `<!-- BACKLOG:END -->` pair in
    `backlog.md`, and it is EMPTY at save#0. That region is machine-managed and written at step 6b,
    AFTER 6a; rows 2 and 2b are carved out of it. Two pairs, or an END before a BEGIN, gives the
    6b writer two candidate regions or none, and the carve-out then protects the wrong span --
    at which point row 2b evicts the live backlog on every save.

INVARIANT: `state.md` carries the step-6a row-2b baseline (ruling R-G) as a DELIMITED region
    holding `backlog_entry_ids: []`, PRESENT and EMPTY -- not absent. Present-and-empty says "no
    plan entries existed at save#0", which is true. Absent means "no baseline", and R-G requires
    row 2b to NO-OP on an absent baseline: a missing baseline read as "everything disappeared"
    evicts the whole file, which is the F65 shape with a delete attached.

EXTERNAL: `python -I` implies `-P`, so the script's own directory is NOT on `sys.path` and the
    sibling import below is what makes the mandated run mode work at all.

EXTERNAL: `citemap` and `recall` are imported, not re-implemented. The selftest counts
    BACKLOG markers through `recall.load_doc`, so a marker inside a fence is not counted by
    a second, drifting fence machine.

STDLIB ONLY. No absolute build-host path appears in this file: every path arrives via argv.

MEASURED 2026-08-03, and ACCEPTED rather than silenced. `validate_entries.py --root <fresh
    scaffold>` exits 0 with 5 entries, 0 aborts and EIGHT `SHAPE` warnings -- one for each
    deliberately entry-free file (CLAUDE.md, CONSTRAINTS.md, architecture.md, design.md,
    open_questions.md, active.md and both deferred archives). Each says "NO ENTRY was parsed
    from <f>, which holds N non-blank content line(s)", and each is TRUE: those files hold
    guidance and no entry, so nothing in them was checked.

    They can be driven to zero -- the warning counts only non-blank, non-fenced lines that do
    not start with `#`, so moving every guidance line and the provenance comment inside the
    fence makes all eight disappear. That was rejected: it silences a true warning by
    reshaping the file around the instrument's definition of "content", and this project's
    whole invariant is about not shipping a view that fails toward "nothing here". The
    warnings clear on their own as real entries land, and the exit code does not move for them.
"""

import argparse
import os
import pathlib
import stat
import sys
from datetime import datetime

# --- sibling import. `-I` implies `-P`, so sys.path[0] is NOT the script's directory. ----------
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    import citemap
    import recall
except ImportError as _exc:                                     # pragma: no cover - fatal
    sys.stderr.write("error: cannot import sibling from %s: %s\n"
                     % (pathlib.Path(__file__).resolve().parent, _exc))
    sys.exit(2)


# --------------------------------------------------------------------------- exit codes
# Fixed across the v4 scripts. Do not redefine one.

EXIT_OK = 0                 # the folder was scaffolded
EXIT_SELFTEST = 1           # --selftest ONLY, and only on a failed assertion.
                            # No normal invocation may ever return 1.
EXIT_USAGE = 2              # usage or I/O: missing --root, a COLLIDING target, a bad --name,
                            # a write failure. Also argparse's own hardcoded code.
EXIT_SCAN_INTEGRITY = 6     # a file read back during the scaffold ends with an OPEN fence.
EXIT_INTERNAL = 7           # uncaught exception. Deliberately NOT 1, so a caller can tell a
                            # broken tool from a failed selftest.


# --------------------------------------------------------------------------- the destination set
# spec S3.1. TEN hot files, in the order the spec lists them. The (kind, lifetime) column is the
# file's DECLARED kind -- it is what `validate_entries.FILE_KIND` holds and what rule A1 checks
# every entry in the file against. It is repeated here as data, not as a comment, so a divergence
# between scaffolder and validator is visible in one place; the selftest asserts they agree.

HOT_FILES = (
    ("AGENTS.md",         "rules", "permanent"),
    ("CLAUDE.md",         "rules", "permanent"),
    ("architecture.md",   "spec",  "conditional"),
    ("design.md",         "spec",  "conditional"),
    ("CONSTRAINTS.md",    "rules", "permanent"),
    ("backlog.md",           "plan",  "conditional"),
    ("open_questions.md", "plan",  "conditional"),
    ("active.md",         "rules", "expiring"),
    ("state.md",          "plan",  "conditional"),   # generated: cursor
    ("manifest.md",       "rules", "permanent"),     # generated: router
)

# spec S3.2. Both `kind: plan`; every entry in either MUST carry a `gate:` (S7.1 / rule A7).
# These are step 6a ROW 5's two destinations, and row 5 is the REVERSIBLE one.
ARCHIVE_DIR = "_archive"
DEFERRED_FILES = (
    "_archive/backlog-deferred.md",
    "_archive/open-questions-deferred.md",
)

# WHAT `new` WRITES, and therefore the only paths a scaffold can destroy. This is the set
# `ensure_scaffoldable` refuses a collision with. Derived from the two constants above rather
# than typed, so a tenth hot file joins it without a second edit -- a typed copy is the shape
# that silently omits whichever member is added next.
WRITE_SET = tuple([rel for (rel, _k, _l) in HOT_FILES] + list(DEFERRED_FILES))

# ADOPTION. `CLAUDE.md` keeps the historical classifier: a readable, non-empty regular file is
# kept rather than templated. `AGENTS.md` has the narrower dual-product contract: any existing
# directory entry is preserved opaquely, including a symlink, and is never opened or classified.
# The split is deliberate. Claude Code behaviour predates the dual-file contract; Codex's file
# must not acquire validation or filesystem-shape rules from it.
#
# Each is a THIRD STATE distinct from both neighbours:
# an ordinary collision (refuse -- a template would destroy live content) and a `--carried` path
# (the caller lands the bytes AFTER this module runs, and the skip is PROVED by asserting the
# path absent). An adopted file is one this run WRITES NO TEMPLATE OVER.
#
# WHAT THAT PROMISES AND WHAT IT DOES NOT, because the sentence that stood here promised the
# second. Classification runs ONCE, before anything is written (`classify_adoptables`), and the
# write loop skips every path classified `ADOPT_KEEP` -- so this tool writes nothing there.
# THAT IS NOT A GUARANTEE THE FILE IS UNCHANGED AT EXIT. A path that is ABSENT OR EMPTY when
# classified is classified `ADOPT_TEMPLATE`, and content appearing between that moment and the
# write is REPLACED by the template, at exit 0. Nothing re-reads the path and nothing compares
# bytes, so nothing here could detect it; a single-user box still has editor autosave, a sync
# client and a second agent session. THE CONDITIONAL WRITE THAT WOULD CLOSE IT IS NOT BUILT
# (C-27: the loss is a file the operator was editing seconds earlier, and the recovery is
# their own undo or whatever version control the folder has -- a scaffold target need not be
# a repository, so no single command is promised here). What
# IS built is this disclosure -- so that no reader takes preservation for something measured.
#
# A project may already have hand-written host instructions. Preserve them
# during adoption without appending scaffolding conventions that the host would
# inject into every turn. Empty content remains eligible for template creation.
#
# ADOPTION IS KEYED ON CONTENT, NOT ON PRESENCE -- see `classify_adoptable`. A zero-byte file has
# no content to protect, so it takes the template rather than leaving the folder an empty hot file.
ADOPTABLE = ("CLAUDE.md",)
OPAQUE_PRESERVE = ("AGENTS.md",)

# The three classes `classify_adoptable` returns. The third exists because the probe must FAIL
# CLOSED: a path we cannot classify is never routed into the branch that writes a template.
ADOPT_KEEP = "keep"
ADOPT_TEMPLATE = "template"
ADOPT_REFUSE = "refuse"

# TOP LEVEL. A sibling of _archive/, never a child of it.
CHECKPOINTS_DIR = "checkpoints"

# GENERATED, and exempt from the declaration rules (spec S3.5). Nothing authors their entries, no
# step-6a row reads them, and their `##` headings are document structure rather than entries -- so
# the "every planted id must resolve" postcondition does not apply to them.
#
# Generated files are derived rather than authored entries. This scaffolder does not
# convert existing projects; collision/adoption guards apply before any template write.
GENERATED_FILES = ("state.md", "manifest.md")

# The machine-managed region of backlog.md (written at step 6b, AFTER 6a). Rows 2 and 2b are carved
# out of it: at 6a time no line inside it has been "rewritten this save" and none carries `done:`,
# so without the carve-out row 2b evicts the entire live backlog on EVERY save.
BACKLOG_BEGIN = "<!-- BACKLOG:BEGIN -->"
BACKLOG_END = "<!-- BACKLOG:END -->"

# Ruling R-G: step 6a row 2b's prior state. A list of backlog.md entry ids recorded by the PREVIOUS
# save. Not git (a project may be mid-rebase, and this must work on a throwaway folder with no
# repo) and not the previous checkpoint (that is cold, and the drain must not read cold storage).
# The delimiters are what distinguishes PRESENT-AND-EMPTY from ABSENT; R-G makes row 2b no-op on
# the latter, so the two may never be conflated.
BACKLOG_IDS_BEGIN = "<!-- BACKLOG-ENTRY-IDS:BEGIN -->"
BACKLOG_IDS_END = "<!-- BACKLOG-ENTRY-IDS:END -->"
BACKLOG_IDS_EMPTY = "backlog_entry_ids: []"

# RULING R-L (2026-08-03) settles the W9 `v3` literal as `v4`: D23 makes this flag the rollback
# discriminator -- of the four skill/folder version combinations, v3-skill-meets-v4-folder cannot
# be made safe -- and a v4 folder that declares v3 invites exactly that pairing. update_state.py
# writes v4; this constant moves with it, in the same change.
SCHEMA_VERSION = "v4"

# Relay-safe, per the v3 helper. Rejects spaces and everything a broker alias cannot carry.
NAME_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
NAME_MAX = 64


# --------------------------------------------------------------------------- templates
# Every guidance line that names a declaration key wraps it in backticks -- `kind: rules`, never a
# bare `kind: rules` at column 0. FIELD_RE opens on `[A-Za-z]` after at most three spaces, so a
# bare form in prose is a DECLARATION to the validator's parser. Backticks make it prose again.
#
# Every example entry sits inside a fence. Fence state comes from `recall.load_doc`, and a
# heading inside a fence is not a heading -- which is exactly what an EXAMPLE heading must be,
# or the scaffolder plants a fabricated entry in every fresh project and rule A2 aborts save#1.

TPL_CLAUDE = """<!-- ash_save v4 | save#0 | {ts} | scaffolded by init_project.py -->
# {project} - project rules

`kind: rules` / `lifetime: permanent` (spec S3.1). BEHAVIOURAL RULES ONLY.

save does not write this file, and step 6a does not drain it. Everything here is a standing
instruction to whoever works in this folder. An instruction with an expiry belongs in
`active.md`; one that waits on an event belongs in `backlog.md` behind a `gate:`.

Every entry is an ATX heading at level 2 or deeper and declares its own `kind:` and `lifetime:`
(spec S3.5). Nothing is planted here at save#0 - a fresh project has no rules yet.

```text
## R-RULE-1 - Stdlib only in scripts/
kind: rules
lifetime: permanent

No third-party import, no shelling out to `rg`. Every helper must run under `python -S -I`.
```
"""

TPL_AGENTS = """<!-- ash_save v4 | save#0 | {ts} | scaffolded by init_project.py -->
# {project} - Codex project rules

`kind: rules` / `lifetime: permanent` (spec S3.1). BEHAVIOURAL RULES ONLY.

save does not write this file during a save, and step 6a does not drain it. Everything here
is a standing instruction to whoever works in this folder. An instruction with an expiry belongs
in `active.md`; one that waits on an event belongs in `backlog.md` behind a `gate:`.

Every entry is an ATX heading at level 2 or deeper and declares its own `kind:` and `lifetime:`
(spec S3.5). Nothing is planted here at save#0 - a fresh project has no rules yet.

```text
## R-RULE-2 - Keep Codex project rules durable
kind: rules
lifetime: permanent

Put Codex-specific standing project instructions in AGENTS.md.
```
"""

TPL_ARCHITECTURE = """<!-- ash_save v4 | save#0 | {ts} | scaffolded by init_project.py -->
# {project} - architecture

`kind: spec` / `lifetime: conditional` (spec S3.1). What the system IS: components, boundaries,
data flow, and the contracts between the parts.

Empty at save#0 by design. Nothing has been built yet, and a placeholder entry in a `spec` file
is permanent - step 6a drains this file on ONE trigger only (row 4), an explicit
`superseded by <id>` marker, which a placeholder will never carry.

When a section is replaced, mark it `superseded by <id>`; row 4 then moves it to
`_archive/architecture-superseded.md` KEEPING ITS ID, so `recall get <id>` still resolves it.

```text
## A-1 - The broker owns delivery
kind: spec
lifetime: conditional

The broker is the only writer of the delivery table; every other component reads it.
```
"""

TPL_DESIGN = """<!-- ash_save v4 | save#0 | {ts} | scaffolded by init_project.py -->
# {project} - design

`kind: spec` / `lifetime: conditional` (spec S3.1). How the parts are built: chosen mechanisms,
schemas, algorithms, and the tradeoff each one settles.

Empty at save#0 by design, for the same reason `architecture.md` is: a `spec` entry drains only
on an explicit `superseded by <id>` marker (step 6a row 4), so a placeholder never leaves.
Superseded sections move to `_archive/design-superseded.md`, keeping their ids.

```text
## D1 - Fence state comes from one machine
kind: spec
lifetime: conditional

Every reader imports `recall.load_doc`. A second fence state machine drifts from the
first, and the drift is invisible until a fixture catches it.
```
"""

TPL_CONSTRAINTS = """<!-- ash_save v4 | save#0 | {ts} | scaffolded by init_project.py -->
# {project} - constraints

`kind: rules` / `lifetime: permanent` (spec S3.1). Constraints the USER stated, in the user's own
terms. One entry per constraint, each with an id that can be cited in a decision.

Empty at save#0 by design: no constraint has been stated yet, and a `rules`/`permanent` entry
does not drain (step 6a has no row for this file). Add entries as the user states them; never
infer one.

```text
## C-1 - No remote git
kind: rules
lifetime: permanent

Every project repo is local. Stated by the user; not inferred from the absence of a remote.
```
"""

TPL_BACKLOG = """<!-- ash_save v4 | save#0 | {ts} | scaffolded by init_project.py -->
# {project} - plan

`kind: plan` / `lifetime: conditional` (spec S3.1). LIVE work only. This is the one hot file
step 6a drains on four separate triggers, so how an item is written decides where it goes:

- an item carrying an explicit `done:` marker moves to `_archive/backlog-done.md` (row 2)
- an item whose `gate:` is not yet satisfied moves to `_archive/backlog-deferred.md` (row 5)
- row 5 WINS over row 2b when both match: deferring is reversible, dropping is not
- row 2b (`_archive/backlog-dropped.md`) is for an item that DISAPPEARS. It does NOT evict an item
  that is sitting right here: an id list cannot tell a rewritten item from an untouched one, and
  a drain that guesses empties this file at exit 0. By default 6a REPORTS a disappeared id
  (`pending_drop:` on its receipt) and moves nothing; only a caller that declares its
  `--rewrote` set complete gets the eviction branch.

Rows 2 and 2b never fire inside the BACKLOG region below, and BOTH are disabled outright if the
region's delimiters are not exactly one ordered pair - the carve-out cannot say what it protects
otherwise, and both rows are ONE-WAY. Row 2 needs an EXPLICIT `done:`: a drain that cannot tell
finishing from forgetting manufactures false completions at the rate the agent is fallible,
which is why row 2b exists at all.

## M1 - State the project goal
kind: plan
lifetime: conditional

`state.md` carries a placeholder goal. Replace it with one sentence naming what this project is
for, then mark this item `done: save#<n>` so step 6a row 2 archives it with its id intact.

{backlog_begin}
<!-- Machine-managed (step 6b, which runs AFTER 6a). Items here are exempt from rows 2 and 2b. -->
<!-- Empty at save#0. -->
{backlog_end}
"""

TPL_OPEN_QUESTIONS = """<!-- ash_save v4 | save#0 | {ts} | scaffolded by init_project.py -->
# {project} - open questions

`kind: plan` / `lifetime: conditional` (spec S3.1). Questions that block or shape work, one
entry each, phrased so that an answer can be recognised when it arrives.

Step 6a drains this file two ways:

- a heading carrying `RESOLVED` moves to `_archive/resolved-oqs.md` (row 3, ONE-WAY)
- an entry whose `gate:` is not yet satisfied moves to `_archive/open-questions-deferred.md`
  (row 5, REVERSIBLE - it comes back when the gate is satisfied)

Empty at save#0. Nothing has been asked yet.

```text
## OQ1 - Does the curator need a non-recursive listdir
kind: plan
lifetime: conditional

Blocks: where `checkpoints/` may live. Answer arrives from the curator's postcondition.
```
"""

TPL_ACTIVE = """<!-- ash_save v4 | save#0 | {ts} | scaffolded by init_project.py -->
# {project} - active instructions

`kind: rules` / `lifetime: expiring` (spec S3.1). TEMPORARY instructions from the user, each
with a bounded expiry. This is the only hot file whose entries expire on a count rather than an
event.

Every entry here MUST carry an expiry: a bounded save count, default 3 saves, MAXIMUM 10. The
schema has no unbounded option, and an expiry naming an EVENT is not an expiry - that is a
`conditional` lifetime wearing an `expires:` label, and it belongs in `backlog.md` behind a `gate:`.
A bare integer is an ABSOLUTE save number, the same reading step 6a row 1 uses
(`expires <= current_save`).

Every entry also carries its provenance: the save it was asserted in, and the user's words
verbatim. Step 6a row 1 moves an expired entry to `_archive/active-expired.md`, keeping its id.

Empty at save#0 - no instruction has been given yet.

```text
## A-7 - asserted save#146 - expires save#149 - until the example-project deploy lands
"defer save deep while we're deep in building"
kind: rules
lifetime: expiring
```
"""

TPL_STATE = """<!-- ash_save v4 | save#0 | {ts} | scaffolded by init_project.py -->
# {project} - state

GENERATED CURSOR. Rewritten by save every save; do not hand-edit. Exempt from the
declaration rules (spec S3.5) because nothing authors its entries.

schema_version: {schema_version}
project: {project}
phase: discovery
save: 0
last_save_ts: {ts}
status: active
goal: (not stated yet - see backlog.md M1)
active_track: (nothing yet - scaffolded {date}, no save has run)
repo_root: (not resolved yet - spec S2.5 r3)
task_list_id: (none resolved yet - spec S7.2 R2)
hot_state: scaffolded
backlog_order: []
no_demote_streak: 0
files:
  manifest: manifest.md
  latest_checkpoint: (none yet)
  archive: _archive/
counts:
  hot_files: 10
  checkpoints: 0
  backlog_items: 1
  open_questions: 0
  active_instructions: 0

`no_demote_streak` counts consecutive saves in which step 6a moved NOTHING (spec S5.1: five in a
row while a hot file grows raises a warning - a drain that never fires is indistinguishable from
one that was never wired up). The field is SCAFFOLDED so the storage exists; NO P1 component
increments it, and `demote.py` deliberately does not write this file - two writers of one
generated cursor is a race, and step 6b owns it. Same for the pending-drop list spec S5.1 row 2b
asks the next restore to print: 6a reports it on its receipt, and nothing yet persists it here.

## Row-2b baseline

Step 6a row 2b asks whether a backlog.md item "was there last save and is not now". Its ONLY source
is the id list below, recorded by the PREVIOUS save (ruling R-G): not git, because a project may
be mid-rebase and this must work on a throwaway folder with no repo, and not the previous
checkpoint, because that is cold storage and the drain must not read it.

The delimiters matter as much as the value. PRESENT and EMPTY, as written here, means "no plan
entries existed at save#0" - which is true, since M1 is planted by this scaffolder and is first
recorded by save#1. ABSENT means "no baseline at all", and R-G requires row 2b to NO-OP and say
so in that case: a missing baseline read as "everything disappeared" evicts the whole file.

{backlog_ids_begin}
{backlog_ids_empty}
{backlog_ids_end}
"""

TPL_MANIFEST = """<!-- ash_save v4 | save#0 | {ts} | scaffolded by init_project.py -->
# {project} - manifest

GENERATED ROUTER. Rewritten by save every save; do not hand-edit. Exempt from the
declaration rules (spec S3.5) because nothing authors its entries.

## Hot set (spec S3.1)

ONE ROW PER CLASS, never per file (spec S3.1, and S3.5 names per-file rows as the generator-bug
shape an over-triggered manifest collapses back from). A class is a (kind, lifetime) pair; it is
what decides how a file is READ and which step-6a row can drain it, and it is the only thing a
router needs to say.

| class | files | drains on |
|---|---|---|
| `rules` / `permanent` | AGENTS.md, CLAUDE.md, CONSTRAINTS.md, manifest.md | nothing - no 6a row reads them |
| `spec` / `conditional` | architecture.md, design.md | `superseded by <id>` (row 4, one-way) |
| `plan` / `conditional` | backlog.md, open_questions.md, state.md | rows 2, 2b, 5 (backlog.md); rows 3, 5 (open_questions.md); nothing (state.md) |
| `rules` / `expiring` | active.md | `expires <= current save` (row 1, one-way) |

`state.md` and `manifest.md` are GENERATED and exempt from the declaration rules (spec S3.5);
they carry the `plan` and `rules` classes for routing only, and no 6a row reads either.

## Archive (spec S3.2 and step 6a destinations)

| class | direction | files |
|---|---|---|
| one-way archive | nothing returns; the id resolves from here forever | `_archive/`: active-expired.md, backlog-done.md, backlog-dropped.md, resolved-oqs.md, architecture-superseded.md, design-superseded.md |
| reversible archive | promoted back with the SAME id when the `gate:` is satisfied | `_archive/`: backlog-deferred.md, open-questions-deferred.md |

Only the two REVERSIBLE files exist at save#0 - they are the two spec S3.2 files, and every entry
in either MUST carry a `gate:` (S7.1, rule A7). The six one-way files are created by the move
engine on first use, so a fresh folder cannot lose an entry to a missing directory.

Every demote is a MOVE that KEEPS THE ENTRY'S ID: eviction degrades to a resolvable stub,
never a deletion. `recall get <id>` resolves an evicted entry from its archive file.

## Retrieval

`recall frontier [scope]` - the frontier is DERIVED on demand and is NOT a stored
file. There is no `frontier.md`, so the POINTER stays resident in the router even though
the content does not.

`recall get <id>` resolves ANY id from this root, hot or archived. Prefer it to reading
an archive file whole - that is the cost this router exists to keep out of the window.

## Checkpoints

`checkpoints/` is a TOP-LEVEL directory, a sibling of `_archive/` and never a child of it. The
out-of-band curator's postcondition is a non-recursive listdir of it.
"""

TPL_BACKLOG_DEFERRED = """<!-- ash_save v4 | save#0 | {ts} | scaffolded by init_project.py -->
# {project} - deferred plan items

`kind: plan` (spec S3.2). Destination of step 6a ROW 5: a `backlog.md` item whose `gate:` is not
yet satisfied. Row 5 is the REVERSIBLE row - when the gate is satisfied the item is promoted
back to `backlog.md` with the SAME id.

EVERY entry here MUST carry a `gate:` naming the condition that brings it back (spec S7.1, rule
A7). A placeholder is not a gate: `gate: none` and `gate: n/a - just deferred` are rejected, and
so is a sentence that disclaims a gate. A deferred entry with no gate is not deferred, it is
deleted with extra steps.

Empty at save#0.

```text
## M4 - Wire the token counter
kind: plan
lifetime: conditional
gate: the P3 budget machinery lands

Deferred, not dropped: the four P1 demote triggers are all markers and need no counter.
```
"""

TPL_OQ_DEFERRED = """<!-- ash_save v4 | save#0 | {ts} | scaffolded by init_project.py -->
# {project} - deferred open questions

`kind: plan` (spec S3.2). Destination of step 6a ROW 5: an `open_questions.md` entry whose
`gate:` is not yet satisfied. REVERSIBLE - it returns to `open_questions.md` with the same id
once the gate is satisfied.

EVERY entry here MUST carry a `gate:` naming the condition that brings it back (spec S7.1, rule
A7); a placeholder or a disclaimer is not a gate.

Empty at save#0.

```text
## OQ9 - What is the post-migration hot-set size
kind: plan
lifetime: conditional
gate: the broker pilot has run one full save cycle

Cannot be answered before there is a measurement to answer it with.
```
"""

# filename -> template. The two generated files take extra substitutions; everything else takes
# {project} / {ts} / {date} only.
TEMPLATES = {
    "AGENTS.md": TPL_AGENTS,
    "CLAUDE.md": TPL_CLAUDE,
    "architecture.md": TPL_ARCHITECTURE,
    "design.md": TPL_DESIGN,
    "CONSTRAINTS.md": TPL_CONSTRAINTS,
    "backlog.md": TPL_BACKLOG,
    "open_questions.md": TPL_OPEN_QUESTIONS,
    "active.md": TPL_ACTIVE,
    "state.md": TPL_STATE,
    "manifest.md": TPL_MANIFEST,
    "_archive/backlog-deferred.md": TPL_BACKLOG_DEFERRED,
    "_archive/open-questions-deferred.md": TPL_OQ_DEFERRED,
}


# --------------------------------------------------------------------------- input validation

def validate_name(name):
    """Raise ValueError unless `name` is relay-safe: `[A-Za-z0-9._-]` and at most 64 chars.

    `state.md`'s `project:` becomes the cross-session relay alias, so a name the broker cannot
    carry is a name no peer can address. Checked at scaffold time because a later rename has to
    propagate through state.md, the relay registry, every watermark and every checkpoint.
    """
    if not name:
        raise ValueError("--name is empty. Give the project a relay-safe short name.")
    if len(name) > NAME_MAX:
        raise ValueError("--name is %d characters; the maximum is %d." % (len(name), NAME_MAX))
    bad = sorted(set(ch for ch in name if ch not in NAME_ALLOWED))
    if bad:
        raise ValueError("--name %r contains %s. Allowed: letters, digits, dot, underscore, "
                         "hyphen -- it is the cross-session relay alias."
                         % (name, ", ".join(repr(c) for c in bad)))


def _occupied(path):
    """Is there a DIRECTORY ENTRY here? `lstat`, so a DANGLING entry counts as occupied.

    `Path.exists()` FOLLOWS the entry and answers False for a dangling one, which is the wrong
    question for "may I write here" and for "did the caller already put something here". The
    entry exists; `os.replace` acts on it; and a carried path that reads absent this way would
    be reported skipped while an entry sits at it.

    THE SEPARATING INPUT IS CONSTRUCTIBLE ON THIS BOX, AND AN EARLIER VERSION OF THIS DOCSTRING
    SAID IT WAS NOT. It claimed a dangling SYMLINK was the only separator and that this box
    could not build one -- generalising `os.symlink` raising WinError 1314 into an
    impossibility, under the words "measured rather than assumed". MEASURED 2026-08-30:
    `mklink /J link target` needs no privilege, and after `rmdir target` the broken junction
    gives `Path.exists()` False while `lstat()` succeeds at mode 0o40777 -- it reads as a
    DIRECTORY, not a link. A symlink loop separates them too. The selftest now carries an arm
    built on that junction wherever the platform can make one.

    ONLY NOT-FOUND MEANS UNOCCUPIED. Any other `OSError` -- denied traversal, delete-pending, a
    reparse point that cannot be read -- means the answer is UNKNOWN, and an earlier version
    answered False there: an occupied path routed straight into the branch that writes over it,
    which is the one outcome this predicate exists to prevent. It refuses instead of guessing.
    """
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("cannot tell whether %s is occupied -- its directory entry could not "
                         "be read (%s). Treating an unreadable entry as absent is what routes "
                         "an occupied path into the branch that writes over it, so this refuses "
                         "instead of guessing. Resolve the path and re-run." % (path, exc))


def classify_adoptables(target):
    """rel -> (state, why) for every ADOPTABLE member. ONE probe per invocation.

    THE POINT IS THAT IT IS CALLED ONCE. `ensure_scaffoldable` and the adopted set used to probe
    independently, so the same path was classified twice against a filesystem that can change in
    between -- and the second call DISCARDED `ADOPT_REFUSE`, so a path that became unclassifiable
    after the first probe was silently treated as templatable. One map, computed before anything
    is written, is both simpler and the only version whose answer is stable.
    """
    return dict((rel, classify_adoptable(target, rel)) for rel in ADOPTABLE)


def adoptable_present(target):
    """The `ADOPTABLE` members classified `ADOPT_KEEP` at `target`. Returns a tuple.

    COMPUTE THIS ONCE, BEFORE ANYTHING IS WRITTEN, AND THREAD THE RESULT THROUGH. `scaffold`
    writes `TPL_CLAUDE` when the file is absent or empty, so a second call AFTER the write would
    report the file this run just created as adopted -- the predicate reading its own output. That
    is why `verify_scaffold` takes `adopted` as an argument instead of recomputing it: the two
    calls would disagree, and the one that runs in the mode where the fault cannot occur is the
    one that looks right.
    """
    return tuple(rel for rel, (state, _why) in sorted(classify_adoptables(target).items())
                 if state == ADOPT_KEEP)


def classify_adoptable(target, rel):
    """(state, why) for one ADOPTABLE path. Three states, and the third is the whole point.

    `ADOPT_KEEP`      a READABLE, NON-EMPTY REGULAR FILE -- adopt it, write no template.
    `ADOPT_TEMPLATE`  absent, or an empty regular file -- nothing to protect, write the template.
    `ADOPT_REFUSE`    ANYTHING ELSE -- stop before writing, and say which thing it was.

    THIS PROBE FAILS CLOSED, AND THE EARLIER VERSION FAILED OPEN. It read `path.is_file() and
    st_size > 0` under a bare `except OSError: continue`, so a file whose metadata could not be
    read was classified NOT-adopted -- which is the branch that writes the template. The one
    outcome this whole feature exists to prevent is a save#0 template landing on a user's
    hand-written charter, and an unreadable-but-present file was routed straight into it at exit
    0. An indeterminate probe may not license the destructive branch: when we cannot tell, we
    refuse and say so.

    `lstat`, NOT `stat`, AND THE DIFFERENCE IS LOAD-BEARING. `Path.exists()` and `is_file()`
    FOLLOW symlinks, so a DANGLING symlink named `CLAUDE.md` reports ABSENT while a directory
    entry sits there. What happens next is NOT that the write resolves through the link: this
    module's `atomic_write` ends in `os.replace(tmp, path)`, which REPLACES THE LINK ENTRY
    itself -- so the link is silently destroyed and the template takes its name. Either way the
    caller is not told. A symlink is refused rather than followed or clobbered: adopting one
    would also make "this file was not written by us" a claim about bytes we never looked at.

    A DIRECTORY AT THIS PATH WAS THE OTHER FAIL-OPEN. `exists()` is true for it, so the collision
    check skipped it as ADOPTABLE, adoption returned false, and the run created every other file
    plus a `.tmp-write` sidecar before `os.replace` failed on the directory -- leaving a partly
    scaffolded folder. Refusing first leaves the folder untouched.
    """
    path = _under(target, rel)
    try:
        st = path.lstat()
    except FileNotFoundError:
        return ADOPT_TEMPLATE, "absent"
    except OSError as exc:
        return ADOPT_REFUSE, "its metadata could not be read (%s)" % exc
    if stat.S_ISLNK(st.st_mode):
        return ADOPT_REFUSE, "it is a symlink; adopt the file it points at, or replace the link"
    if stat.S_ISDIR(st.st_mode):
        return ADOPT_REFUSE, "it is a directory"
    if not stat.S_ISREG(st.st_mode):
        return ADOPT_REFUSE, "it is not a regular file"
    if st.st_size == 0:
        return ADOPT_TEMPLATE, "empty"
    try:
        with path.open("rb") as fh:
            fh.read(1)
    except OSError as exc:
        # Present and non-empty but unreadable. Adopting it would leave a hot file no later step
        # can load; templating it would destroy it. Neither is ours to choose silently.
        return ADOPT_REFUSE, "it could not be opened for reading (%s)" % exc
    return ADOPT_KEEP, "present and non-empty"


def ensure_scaffoldable(target, classes=None):
    """Raise ValueError if `target` already holds a file this scaffolder WOULD WRITE.

    THE TEST IS COLLISION, NOT EMPTINESS, and the whole fix is in that distinction. The
    refusal exists because the templates would replace real content with save#0 boilerplate --
    a whole-project deletion at exit 0. That hazard is bounded by WRITE_SET: the hot files and
    the two deferred archives. A `notes.md`, an `ld.yml`, a `docs/` tree or a session's
    working files are not in it and were never at risk from a template.

    EMPTINESS WAS THE WRONG PROXY AND IT BLOCKED THE ORDINARY CASE. `save` fires
    mid-session at a context threshold, in a folder the session has been working in, so the
    folder is almost never empty at the moment adoption is needed -- and the old test refused
    every real project. the maintainer, 2026-08-29: "The trigger cannot be directory not empty ... It
    might have created files during the session." MEASURED the same day: two fleet folders
    reached a `.ash_save/` sub-root instead of scaffolding in place, and one of them got there
    because an agent worked around THIS refusal across three turns.

    `ADOPTABLE` IS SUBTRACTED AND `carried` IS NOT, and the asymmetry is the point. An adopted
    path is kept as-is, so there is nothing to destroy and nothing to refuse -- the subtraction
    is UNCONDITIONAL here even though `scaffold` only skips the write when the file has content,
    because an empty file is not live content either way. A carried path is the opposite: the
    caller supplies it AFTER this module runs, and `verify_scaffold` asserts it ABSENT to prove
    the skip engaged -- citing THIS refusal as its ground. Subtracting `carried` here would let a
    carry run against a path that already holds content, and that assertion would then fire on a
    file nothing wrote.

    An EMPTY existing directory is still fine: that is the shape `mkdir` leaves behind.
    Returns "created" or "reused".
    """
    if not target.exists():
        return "created"
    if not target.is_dir():
        raise ValueError("--root exists and is not a directory: %s" % target)
    # An ADOPTABLE path is exempt from the collision set -- but only once it has been CLASSIFIED.
    # `ADOPT_REFUSE` is neither a collision nor an adoption: it is a path we could not judge, and
    # judging it wrong in either direction destroys something. Refuse here, before any mkdir.
    classes = classes if classes is not None else classify_adoptables(target)
    for rel in ADOPTABLE:
        state, why = classes[rel]
        if state == ADOPT_REFUSE:
            raise ValueError("refusing to scaffold: %s is an ADOPTABLE path this scaffolder "
                             "will not classify -- %s. Adopting and templating are both writes "
                             "against something this tool cannot describe, so it does neither "
                             "and changes nothing. Resolve the path and re-run. (Not every case "
                             "here would destroy content -- a symlink to a good file would read "
                             "fine -- but the tool cannot tell those apart from the ones that "
                             "would, which is the whole reason it stops.)"
                             % (_under(target, rel), why))
    # `_occupied`, NOT `.exists()`, AND THE DIFFERENCE IS DAMAGE RATHER THAN STYLE. `exists()`
    # FOLLOWS the entry, so a DANGLING one at a write-set path reads ABSENT, the collision is
    # missed, and `os.replace` meets it instead. REPRODUCED 2026-08-30 with a broken junction
    # at `design.md`: 2 of 9 hot files written, an orphaned `design.md.tmp-write` left behind,
    # and a raw `[WinError 5] Access is denied` -- a PARTLY SCAFFOLDED FOLDER, which is exactly
    # the outcome this gate exists to make impossible. `_occupied` was written for this case
    # and was reaching only the carried paths.
    collisions = sorted(rel for rel in WRITE_SET
                        if rel not in ADOPTABLE and rel not in OPAQUE_PRESERVE
                        and _occupied(_under(target, rel)))
    if collisions:
        shown = ", ".join(collisions[:8]) + (", ..." if len(collisions) > 8 else "")
        raise ValueError("refusing to scaffold: %s already holds %d file(s) this scaffolder "
                         "WRITES (%s). A template would replace live content with save#0 "
                         "boilerplate. Anything ELSE in the directory is fine and is not "
                         "what this checks -- only a COLLISION with the write set is. %s is "
                          "not counted here: AGENTS.md is preserved opaquely whenever its directory "
                          "entry exists; CLAUDE.md is ADOPTED when it has content and TEMPLATED "
                          "when it is empty."
                          % (target, len(collisions), shown,
                             ", ".join(ADOPTABLE + OPAQUE_PRESERVE)))
    return "reused"


# --------------------------------------------------------------------------- writing

def atomic_write(path, text):
    """Write `text` as UTF-8 BYTES with LF terminators, via a sibling tmp + os.replace.

    BYTES, never text mode. Python's text mode writes `os.linesep`, so on this box every template
    would ship CRLF while the source string and every assertion on decoded text still read LF --
    the flip is invisible to `git diff` under a normalising config and shows up only as one extra
    byte per line. Returns the byte count written.
    """
    data = text.replace("\r\n", "\n").encode("utf-8")
    tmp = path.with_name(path.name + ".tmp-write")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))
    return len(data)


def render(rel, subs):
    """The template for `rel`, substituted. KeyError here is a template/subs mismatch (exit 7)."""
    return TEMPLATES[rel].format(**subs)


def substitutions(project, now):
    return {
        "project": project,
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "schema_version": SCHEMA_VERSION,
        "backlog_begin": BACKLOG_BEGIN,
        "backlog_end": BACKLOG_END,
        "backlog_ids_begin": BACKLOG_IDS_BEGIN,
        "backlog_ids_empty": BACKLOG_IDS_EMPTY,
        "backlog_ids_end": BACKLOG_IDS_END,
    }


def _under(target, rel):
    """`target` joined to a POSIX-relative template path, flavour-safely.

    `Path / PurePosixPath` mixes path flavours; splitting on "/" and joining the segments is the
    portable form and is what keeps `_archive/backlog-deferred.md` landing in a subdirectory on
    Windows rather than becoming a filename with a slash in it.
    """
    return target.joinpath(*rel.split("/"))


# --------------------------------------------------------------------------- the carry rule
# THE MACHINE-MANAGED REGIONS, DERIVED FROM THE RENDERED TEMPLATES RATHER THAN LISTED. A caller
# may hand this scaffolder a set of paths it will supply itself (`carried`, below) -- but not
# every scaffolded name can accept foreign bytes, and the discriminator is not a matter of taste.
# `backlog.md` carries an ordered BACKLOG:BEGIN/END pair that step 6b rewrites on every save and
# that rows 2/2b are carved out of; `state.md` carries the R-G row-2b baseline region. A body
# without those markers put at either path does not merely look wrong, it makes
# `verify_scaffold`'s own marker checks fail -- and if it slipped past them, the first drain would
# evict the live backlog. So those two names are REFUSED as carry targets.
#
# COMPUTED FROM `render`, NOT FROM THE RAW TEMPLATE STRING, and the difference is the whole check.
# The markers reach the file through `{backlog_begin}` / `{backlog_ids_begin}` substitutions, so
# `BACKLOG_BEGIN in TPL_BACKLOG` is FALSE while the rendered file plainly contains it -- MEASURED
# 2026-08-10, and a check keyed on the raw string would have reported ZERO machine-managed files
# and cheerfully authorised a carry over `backlog.md`.

MACHINE_REGION_MARKERS = (BACKLOG_BEGIN, BACKLOG_END, BACKLOG_IDS_BEGIN, BACKLOG_IDS_END)


def machine_region_rels():
    """Every scaffolded rel whose RENDERED template carries a machine-managed region marker.

    A tuple, in HOT_FILES-then-DEFERRED_FILES order. DERIVED on every call: adding a marker to a
    template, or a template to `TEMPLATES`, changes this set with no edit here and no edit in the
    migrator that consumes it. The probe substitutions are throwaway -- only the markers are read
    back, and none of them is interpolated from `project` or a timestamp."""
    subs = substitutions("probe", datetime(2000, 1, 1))
    out = []
    for rel in [r for (r, _k, _l) in HOT_FILES] + list(DEFERRED_FILES):
        body = render(rel, subs)
        if any(marker in body for marker in MACHINE_REGION_MARKERS):
            out.append(rel)
    return tuple(out)


def scaffold(target, project, now, carried=()):
    """Create the folder. Returns (dirs_created, [(rel, bytes)], adopted). Raises ValueError.

    Order matters only in that directories precede the files inside them. `checkpoints/` is made
    directly under `target`, NEVER under `_archive/`.

    THREE STATES, NOT TWO, and conflating any pair of them is how this function breaks.
    WRITTEN: no file there, so a template lands. CARRIED: the caller supplies the bytes AFTER
    this module's postconditions run, and the skip is proved by asserting the path ABSENT.
    ADOPTED: the file is already there WITH CONTENT, and this function writes nothing to it --
    proved by asserting the path PRESENT. Carried and adopted assert OPPOSITE things about the
    same path, which is why they are separate arguments and separate sets.

    `carried` identifies absent, non-machine-managed paths whose bytes the caller supplies
    afterwards. It does not import, copy or convert another format. Existing paths are
    never carryable; state.md and backlog.md cannot be skipped, so no v3 cursor can bypass
    the ordinary collision guard. Normal existing host instruction adoption is separate.

    ADOPTION CANNOT USE THAT ESCAPE, AND THAT IS THE HALF THE DESIGN NOTE DID NOT COVER. An
    adopted file's bytes are already on disk when the postconditions run, so the same loop sees
    them: `## File Organization` and every other ordinary prose heading matches no family, each
    becomes a `problem`, and the run dies at exit 7 -- on a file this tool did not write and
    cannot fix (R-CLAUDE-77). `verify_scaffold` therefore EXEMPTS the adopted set from the
    planted-id and planted-citation scans, which is sound because those scans ask what THIS
    SCAFFOLDER PLANTED and an adopted file is exactly the thing it did not plant.

    A `carried` naming a MACHINE-MANAGED file is refused rather than honoured -- see
    `machine_region_rels`. The remedy there is a rename in the source, never a carry."""
    # ONE classification for the whole invocation, computed before anything is written: once a
    # template lands the answer changes, and the value threaded to `verify_scaffold` must be the
    # one that was true on entry.
    classes = classify_adoptables(target)
    ensure_scaffoldable(target, classes)
    adopted = tuple(rel for rel, (state, _why) in sorted(classes.items())
                    if state == ADOPT_KEEP)
    adopted += tuple(rel for rel in OPAQUE_PRESERVE if _occupied(_under(target, rel)))

    carried = frozenset(carried)
    # A CARRIED PATH THAT ALREADY EXISTS IS A CONTRADICTION, AND IT MUST BE CAUGHT HERE.
    # `carried` means "I will supply this AFTER the scaffold", so `verify_scaffold` asserts the
    # path ABSENT. `ensure_scaffoldable` used to guarantee that for free, because every carried
    # rel was also a collision -- but ADOPTABLE members are now exempt from the collision set, so
    # `--carried CLAUDE.md` against a folder that already holds one sails past it, writes the
    # other nine files, and only then trips the absence assertion: EXIT 7 ON A PARTIALLY
    # SCAFFOLDED FOLDER, under a message ("the skip did not engage", "would have been written
    # over") that is false in both clauses -- nothing was written and the file is intact.
    # The old build refused this input cleanly at exit 2. Restoring that is what this check is.
    # WHAT THE FLAG NAMES IS SETTLED BEFORE WHAT SITS AT IT, AND THE ORDER USED TO BE THE OTHER
    # WAY ROUND. The occupancy check below emits a PER-PATH remedy ("move the file aside to carry
    # it"), which is only true for a path this scaffolder actually writes. Running it first meant
    # `--carried notes.md` against an existing `notes.md` told the operator to move the file
    # aside -- and the next run then refused, because `notes.md` is not a scaffolded path at all.
    # The advice sent them to do work that could not help. Validating the NAME first makes every
    # remedy below reachable only for a path where it holds.
    known = set(r for (r, _k, _l) in HOT_FILES) | set(DEFERRED_FILES)
    unknown = sorted(carried - known)
    if unknown:
        raise ValueError("refusing to skip %d path(s) this scaffolder does not write: %s. "
                         "`--carried` names a scaffolded hot file or deferred archive; a path "
                         "outside that set is a caller bug, and honouring it silently would let a "
                         "typo turn the skip into a no-op. Drop the flag -- moving the file will "
                         "not make it carryable."
                         % (len(unknown), ", ".join(unknown)))
    protected = sorted(carried & set(machine_region_rels()))
    if protected:
        raise ValueError("refusing to skip %d MACHINE-MANAGED path(s): %s. Their templates carry "
                         "an ordered region this project's machinery rewrites on every save, so a "
                         "hand-written body at that path is not a carry candidate -- it would put "
                         "prose inside a region the drain rewrites. Rename it IN THE SOURCE."
                         % (len(protected), ", ".join(protected)))

    present_carried = sorted(r for r in carried if _occupied(_under(target, r)))
    if present_carried:
        # THE REMEDY IS PER-PATH, AND STATING ONE FOR ALL OF THEM WAS FALSE. This fires on ANY
        # carried rel, most of which have no adopt branch at all; and even for an ADOPTABLE one,
        # "drop the flag and it will be adopted" holds only when the file has CONTENT -- an empty
        # one is TEMPLATED, so the advice would have promised preservation and delivered a write.
        _adoptable_here = [r for r in present_carried
                           if ((r in ADOPTABLE and
                                classes.get(r, (None, ""))[0] == ADOPT_KEEP)
                               or r in OPAQUE_PRESERVE)]
        _hint = (" For %s, dropping the flag ADOPTS what is there."
                 % ", ".join(_adoptable_here)) if _adoptable_here else ""
        raise ValueError("refusing to scaffold: %d carried path(s) already exist: %s. `--carried` "
                         "means the caller supplies the file AFTER the scaffold, so something "
                         "already sitting there is a contradiction rather than a state to "
                         "resolve.%s Move the file aside to carry it."
                         % (len(present_carried), ", ".join(present_carried), _hint))

    target.mkdir(parents=True, exist_ok=True)

    dirs = []
    for name in (CHECKPOINTS_DIR, ARCHIVE_DIR):
        d = target / name
        d.mkdir(exist_ok=True)
        dirs.append(name + "/")

    subs = substitutions(project, now)
    written = []
    for (rel, _kind, _life) in HOT_FILES:
        if rel in carried or rel in adopted:
            continue
        written.append((rel, atomic_write(target / rel, render(rel, subs))))
    for rel in DEFERRED_FILES:
        if rel in carried:
            continue
        written.append((rel, atomic_write(_under(target, rel), render(rel, subs))))
    return dirs, written, adopted


# --------------------------------------------------------------------------- postconditions
# Run on EVERY scaffold, not only under --selftest. These read the files back, so this path
# carries the integrity obligation: an open fence in a written file means the structural checks
# below scanned a file they could not see the whole of, and exit 6 says so.

def _rows(path):
    return recall.load_doc(path)


def count_markers(rows, marker):
    """Lines equal to `marker` OUTSIDE any fence. Fence state is recall's, never a second one."""
    return [ln for (ln, text, _s, _e, in_fence) in rows
            if not in_fence and text.strip() == marker]


def verify_scaffold(target, carried=(), adopted=()):
    """(problems, unreliable, clashes). THREE values, and this line said two until save#162.

    A non-empty `problems` list is a broken TOOL, not a bad input: it becomes a RuntimeError
    and exit 7. `clashes` is the opposite -- an id the FOLDER'S OWN adopted charter shares
    with a template, which is the operator's to rename and is reported at EXIT_USAGE. They
    are returned apart because they mean different things and take different exit codes; a
    caller writing `problems, unreliable = verify_scaffold(root)` against the old contract
    raises ValueError on the NORMAL path.

    Checked here rather than only in the selftest because a template edit that unbalances a fence
    or duplicates a BACKLOG marker is silent at write time: the bytes land, the exit code is 0,
    and the damage surfaces one save later inside the drain -- where a mis-delimited BACKLOG
    region makes row 2b evict the live backlog.

    `carried` MUST BE THE SAME SET `scaffold` WAS GIVEN. Two things follow, and the second is the
    one that makes this more than bookkeeping. A carried path is subtracted from the
    must-have-been-written list, because this run deliberately did not write it. And a carried
    path is then asserted ABSENT: `ensure_scaffoldable` refuses a COLLISION with the write set --
    and does NOT subtract `carried` from it, precisely so this assertion keeps its ground --
    so nothing can legitimately sit there yet, and a file appearing at a skipped path means the
    skip did not
    engage and a template landed after all -- which is precisely the failure the skip exists to
    prevent, and the failure a bare exit 0 would report as success.

    `adopted` IS THE MIRROR OF `carried` AND ASSERTS THE OPPOSITE THING. A carried path must be
    ABSENT (the caller has not landed it yet); an adopted path must be PRESENT and is satisfied by
    that presence alone -- it is the file the folder already had, and nothing here wrote to it.
    THE CLAIM IS ABOUT WHAT WE DID, NOT ABOUT THE BYTES. Nothing compares the file's content
    across the run, so `kept byte-for-byte` -- which the receipt used to say -- asserted more
    than any check here establishes: an editor replacing the file mid-run would leave every
    check green under a receipt promising the bytes were unchanged.

    ADOPTED FILES ARE EXEMPT FROM THE TWO PLANTED-CONTENT SCANS, and without that exemption
    adoption cannot work at all. Both scans ask a question about WHAT THIS SCAFFOLDER PLANTED:
    does every planted heading carry an addressable id, and does every planted citation resolve.
    An adopted file was planted by nobody here. Run them over one anyway and an ordinary charter
    fails on its own prose -- `## File Organization`, `## Project Context` and `## Git` each match
    no `citemap.FAMILY_PATTERNS` family, so each becomes a `problem`, and `problems` is a
    RuntimeError and exit 7. VERIFIED 2026-08-30 against `citemap.find_definition`: all three
    return None.

    AN ADOPTED FILE ALSO MAY NOT MOVE THE EXIT CODE VIA `unreliable`. An unbalanced fence in a
    hand-written charter is real and is WARNED about, but this tool does not write that file and
    cannot repair it, so the operator's only way to clear an integrity refusal would be to stop
    writing their own file the way they meant to (R-CLAUDE-77 -- measured 2026-08-30, when a
    CLAUDE.md check aborted every save on an ordinary prose charter). It warns and continues."""
    problems = []
    unreliable = False
    carried = frozenset(carried)
    adopted = frozenset(adopted)

    for name in (CHECKPOINTS_DIR, ARCHIVE_DIR):
        if not (target / name).is_dir():
            problems.append("%s/ was not created" % name)
    if (target / ARCHIVE_DIR / CHECKPOINTS_DIR).exists():
        problems.append("checkpoints/ landed under _archive/; it is a TOP-LEVEL sibling of it")

    # `os.path.lexists`, not `.exists()`: a DANGLING entry at a carried path is something the
    # caller has to resolve, and following the entry reports it absent. Deliberately NOT
    # `_occupied` -- that one REFUSES on indeterminate metadata, and this is a postcondition
    # running AFTER the writes, where a raised exception surfaces as exit 7 ("the tool, not
    # your folder") on what is a folder condition. Same lstat question, no new exit path.
    for rel in sorted(carried):
        if os.path.lexists(str(_under(target, rel))):
            problems.append("%s was named as CARRIED -- this scaffolder was told not to write it "
                            "-- yet a file exists there. The skip did not engage; whatever the "
                            "caller intended to put at that path would have been written over"
                            % rel)

    expected = [rel for (rel, _k, _l) in HOT_FILES if rel not in carried] \
        + [rel for rel in DEFERRED_FILES if rel not in carried]
    for rel in expected:
        path = _under(target, rel)
        if rel in OPAQUE_PRESERVE and rel in adopted:
            if not os.path.lexists(str(path)):
                problems.append("%s was preserved opaquely, yet its directory entry disappeared "
                                "between the pre-write scan and this check" % rel)
            continue
        if not path.is_file():
            if rel in adopted:
                problems.append("%s was ADOPTED -- this scaffolder was told the folder already "
                                "held it, so it deliberately wrote no template -- yet nothing is "
                                "there now. It was removed between the pre-write scan and this "
                                "check, leaving the slot empty in a folder reported as "
                                "scaffolded" % rel)
            else:
                problems.append("%s was not written" % rel)
            continue
        if path.stat().st_size == 0:
            problems.append("%s is empty" % rel)
            continue
        _data, rows, open_line = _rows(path)
        if recall.warn_open_fence(rel, open_line) and rel not in adopted:
            # An adopted file WARNS and stops there: this tool did not write it and cannot repair
            # it, so an integrity refusal would be unclearable by any action on our side
            # (R-CLAUDE-77). `warn_open_fence` has already printed by the time we reach this.
            unreliable = True

    plan = target / "backlog.md"
    if plan.is_file():
        _data, rows, _open = _rows(plan)
        begins = count_markers(rows, BACKLOG_BEGIN)
        ends = count_markers(rows, BACKLOG_END)
        if len(begins) != 1 or len(ends) != 1:
            problems.append("backlog.md has %d BACKLOG:BEGIN and %d BACKLOG:END markers; exactly "
                            "one of each is required" % (len(begins), len(ends)))
        elif begins[0] >= ends[0]:
            problems.append("backlog.md BACKLOG markers are out of order: BEGIN on line %d, END on "
                            "line %d" % (begins[0], ends[0]))

    state = target / "state.md"
    if state.is_file():
        _data, rows, _open = _rows(state)
        b = count_markers(rows, BACKLOG_IDS_BEGIN)
        e = count_markers(rows, BACKLOG_IDS_END)
        v = count_markers(rows, BACKLOG_IDS_EMPTY)
        if len(b) != 1 or len(e) != 1 or b[0] >= e[0]:
            problems.append("state.md has %d BACKLOG-ENTRY-IDS:BEGIN and %d END markers (R-G "
                            "baseline region); exactly one ordered pair is required"
                            % (len(b), len(e)))
        elif len(v) != 1 or not (b[0] < v[0] < e[0]):
            problems.append("state.md row-2b baseline is not `%s` inside the region; R-G "
                            "distinguishes PRESENT-AND-EMPTY from ABSENT and they may not be "
                            "conflated" % BACKLOG_IDS_EMPTY)

    # EVERY PLANTED ID MUST RESOLVE. spec C-4 makes the id the handle an evicted entry keeps, so
    # an entry whose heading matches no `citemap.FAMILY_PATTERNS` family is unaddressable by
    # `recall get` the moment step 6a moves it -- and the LIVE seeded plan item is exactly the
    # shape a fresh project drains first. MEASURED 2026-08-03: this scaffolder planted SIX such
    # ids (`R-1`, `DS-1`, `P-1`, `OQ-1`, `P-4`, `OQ-9`); `recall list backlog.md` reported "0 ids
    # defined" over a fresh scaffold and `get P-1` exited 3. FENCED examples are checked too: an
    # example is a template a user copies, and a copied id that resolves to nothing is the same
    # defect one save later. `state.md` and `manifest.md` are exempt -- they are GENERATED (spec
    # S3.5), no 6a row reads them, and their `##` headings are structure, not entries.
    #
    # The declaration is read from the TARGET, which is what makes this correct under `--carried`:
    # a folder that already carries `.ash-id-families` is judged against the families that project
    # actually uses, not against the built-ins alone. Declaration problems go to STDERR and NEVER
    # into `problems` -- that list becomes a RuntimeError and exit 7 ("the tool, not your folder"),
    # and a typo in an optional config file is neither.
    families, _fam_problems = citemap.project_families(target)
    for _fp in _fam_problems:
        sys.stderr.write("warning: %s\n" % _fp)
    for rel in [r for (r, _k, _l) in HOT_FILES
                if r not in GENERATED_FILES and r not in adopted
                and r not in OPAQUE_PRESERVE] \
            + list(DEFERRED_FILES):
        path = _under(target, rel)
        if not path.is_file():
            continue
        _data, rows, _open = _rows(path)
        for (lineno, text, _s, _e, _in_fence) in rows:
            m = citemap.HEADING_RE.match(text or "")
            if not m or len(m.group(1)) < 2:
                continue
            head = "#" * len(m.group(1)) + " " + m.group(3)
            if citemap.find_definition(head, families) is None:
                problems.append("%s:%d plants the heading %r, whose id matches no "
                                "citemap.FAMILY_PATTERNS family -- `recall get` could never "
                                "address it (C-4)" % (rel, lineno, (text or "").strip()[:60]))

    _cit, _clashes = planted_citation_problems(target, families, report_exclude=adopted)
    problems.extend(_cit)

    return problems, unreliable, _clashes


def planted_citation_problems(target, families, report_exclude=()):
    """Every id a planted line CITES must resolve inside the project the boilerplate landed in.

    THIS IS THE MIRROR OF THE PLANTED-HEADING CHECK ABOVE, AND IT IS THE HALF THAT WAS MISSING.
    That one asks whether a planted HEADING is addressable, so it is structurally blind to a
    planted CITATION -- and it exempts `GENERATED_FILES` outright, although their PROSE ships into
    every project exactly as a template's does.

    MEASURED 2026-08-11: the `CLAUDE.md` template cited `D72`, and the manifest generator cited
    `C-4` and `D60`. All three are save's OWN ids, resolving to nothing in the project the
    boilerplate was written into, and `migrate_inplace verify` reported all three as dangling --
    so NO migrated project could pass its own gate. Third appearance of this family (the first two
    were found by reviewers, after shipping).

    THE ASSERTION IS DELIBERATELY THE ONE `verify` MAKES: an id cited from a scanned file with no
    definition among them. A fresh scaffold that passes here is a folder that passes there, and
    the two cannot drift into disagreeing about it.

    HEADINGS ARE NOT THE SUBJECT. A planted `## D1: ...` DEFINES `D1`, so it resolves and is not
    reported; what this catches is prose naming an id nothing here defines.

    `report_exclude` FILTERS THE REPORTING, AND DELIBERATELY NOT THE MAP. An adopted file must
    stop PRODUCING findings -- its author's citations are theirs, not scaffolded boilerplate --
    but it must keep CONTRIBUTING DEFINITIONS, and an earlier version of this got that wrong by
    dropping it from `rels`. `citemap.build_map` supplies both directions from one scan, so
    removing a file removed the ids it DEFINES: an id defined in the adopted `CLAUDE.md` and
    cited by a written template then resolved to nothing, and files THIS TOOL DID WRITE were
    reported dangling -- exit 7, after the writes. Scan everything; filter the citers."""
    rels = [r for (r, _k, _l) in HOT_FILES if r not in OPAQUE_PRESERVE] \
        + list(DEFERRED_FILES)
    files = [(rel, _under(target, rel)) for rel in rels if _under(target, rel).is_file()]
    if not files:
        # (problems, clashes) -- BOTH early returns owe the tuple the normal path returns.
        # Returning a bare list here raised `ValueError: not enough values to unpack` at the
        # call site, so the problem this branch exists to REPORT was destroyed by the report.
        return ["no hot or deferred file is on disk to scan for planted citations"], []
    try:
        mp = citemap.build_map(target, (), families, files=files)
    except Exception as exc:                              # noqa: BLE001 - reported, never hidden
        return ["planted-citation check could not run: citemap raised %s" % exc], []
    skip = frozenset(report_exclude)
    out = []
    # SEPARATE LIST, SEPARATE EXIT CODE. `problems` means "a broken TOOL" and becomes exit 7; an
    # id the FOLDER'S OWN charter shares with a template is a folder condition the operator
    # resolves, so it is returned apart and `main` reports it at EXIT_USAGE. The suite's
    # "no normal invocation returned EXIT_INTERNAL" guard is what surfaced the confusion.
    clashes = []
    for row in mp["ids"]:
        # AN ID DEFINED TWICE IS NOT A CLEAN ROW, AND `if row["definitions"]: continue` CALLED IT
        # ONE. That skip was correct while this scan saw only files we wrote -- they cannot
        # collide with each other -- and stopped being correct the moment the ADOPTED charter
        # started contributing definitions to the same map. REPRODUCED 2026-08-31: a charter
        # defining `## M1` against `backlog.md`'s own seeded `M1` scaffolds at exit 0, and
        # `recall get M1` then exits 4, ambiguous. C-4 makes the id the handle an evicted
        # entry keeps; a folder that starts life with an unresolvable one has already lost it.
        if len(row["definitions"]) > 1:
            where = ", ".join("%s:%s" % (d["file"], d["line"]) for d in row["definitions"])
            # OWNERSHIP IS DERIVED FROM EACH DEFINITION'S FILE, NEVER ASSUMED FROM THE COUNT.
            # The advice here used to read "the scaffolded one is this tool's, the other is the
            # folder's own" -- a two-site story asserted over every shape. Put TWO definitions of
            # one id inside the adopted charter, with no template defining it at all, and that
            # sentence was false in both halves while the detection itself was right. `skip` is
            # the adopted set, so the sites partition exactly and nothing has to be inferred.
            # SITES, NOT FILES, AND THE COLLAPSE TO FILE NAMES GAVE INCOMPLETE ADVICE. With
            # TWO definitions inside the adopted charter and one in a template, "rename the
            # scaffolded one" leaves two behind and `recall get` still exits 4 -- while
            # the workflow, reading a resolved-sounding remedy, says carry on. What has to
            # be true at the end is that EXACTLY ONE definition remains anywhere.
            _their_sites = [d for d in row["definitions"] if d["file"] in skip]
            _our_sites = [d for d in row["definitions"] if d["file"] not in skip]
            _theirs = sorted(set(d["file"] for d in _their_sites))
            _ours = sorted(set(d["file"] for d in _our_sites))
            if _ours and _theirs and len(_their_sites) == 1:
                _advice = ("Rename the %d scaffolded definition(s) in %s -- %s is ADOPTED, so "
                           "this tool did not write it and will not change it, and its single "
                           "definition then stands alone."
                           % (len(_our_sites), ", ".join(_ours), ", ".join(_theirs)))
            elif _ours and _theirs:
                _advice = ("The ADOPTED file(s) (%s) hold %d definitions of this id ON THEIR "
                           "OWN, so renaming the %d scaffolded definition(s) in %s is NOT "
                           "enough -- rename until EXACTLY ONE definition is left anywhere. "
                           "This tool did not write the adopted file and will not change it."
                           % (", ".join(_theirs), len(_their_sites), len(_our_sites),
                              ", ".join(_ours)))
            elif _ours:
                _advice = ("No site is in an ADOPTED file: every one is at a path this "
                           "scaffolder writes (%s). Report it -- nothing in your folder "
                           "produced it." % ", ".join(_ours))
            else:
                _advice = ("All %d definitions are in ADOPTED file(s) (%s), which this tool "
                           "did not write and will not change. Rename until EXACTLY ONE is "
                           "left." % (len(_their_sites), ", ".join(_theirs)))
            clashes.append("`%s` is DEFINED AT %d SITES (%s). `recall get %s` exits 4 and "
                           "cannot pick between them, so the id addresses nothing from save#0. %s"
                           % (row["id"], len(row["definitions"]), where, row["id"], _advice))
            continue
        if row["definitions"]:
            continue
        citers = sorted(c for c in row["cited_in"] if c not in skip)
        if not citers:
            continue
        out.append("%s cites `%s`, which nothing in a fresh project defines -- scaffolded "
                   "boilerplate must not carry save's own reference keys, and "
                   "`migrate_inplace verify` reports exactly this as a dangling identifier"
                   % (", ".join(citers), row["id"]))
    return out, clashes


# --------------------------------------------------------------------------- selftest
# Every fixture is written to a tempfile.mkdtemp and removed in a finally. Nothing under a project
# folder, nothing left behind.

_ST_CODES = []


def _st_make_dangling(link, target):
    """Leave a DANGLING directory entry at `link`. True only if the platform really made one.

    A DANGLING entry is the input that separates `lstat` from `exists()`, and this module used to
    carry a docstring asserting this box could not build one -- generalising `os.symlink` raising
    WinError 1314 into an impossibility. `mklink /J` needs no privilege: make a junction, remove
    its target, and the entry stays while `exists()` goes False.

    THE RETURN IS THE STIMULUS CHECK, NOT THE ATTEMPT. It verifies the separation directly
    (`lstat` succeeds AND `exists()` is False) and returns False otherwise, so a platform that
    cannot build one SKIPS the arm rather than running it against nothing. A mutant that reddens
    no arm and a stimulus that never landed render identically; checking the separation here is
    what tells them apart (R-CLAUDE-47).
    """
    import shutil
    import subprocess
    try:
        target.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            rc = subprocess.call(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if rc != 0:
                return False
        else:
            os.symlink(str(target), str(link))
        shutil.rmtree(str(target), ignore_errors=True)
    except (OSError, ValueError):
        return False
    try:
        link.lstat()
    except OSError:
        return False
    return not link.exists()


def _st_run(argv):
    """(code, stdout_text, stderr_text). Drives main() IN-PROCESS: the assertion is on the
    returned code itself, not on a subprocess's shell-mangled status. An exception is captured
    and recorded as EXIT_INTERNAL so one broken arm cannot take the rest down with it."""
    import io
    import traceback
    out = io.StringIO()
    err = io.StringIO()
    real_err = sys.stderr
    sys.stderr = err
    try:
        code = main(argv, _out=out)
    except SystemExit as exc:                       # argparse's own exit path
        code = exc.code if isinstance(exc.code, int) else EXIT_USAGE
    except Exception:                               # noqa: BLE001 - recorded, not swallowed
        traceback.print_exc(file=err)
        code = EXIT_INTERNAL
    finally:
        sys.stderr = real_err
    _ST_CODES.append(code)
    return code, out.getvalue(), err.getvalue()


def _st_ids_of(root, rel):
    """The ids `recall get` resolves in one scaffolded file -- the same walk `get` uses."""
    data, rows, _open = _rows(_under(root, rel))
    return [e["id"] for e in recall.heading_map(rel, data, rows, citemap.build_families())]


def _st_recall_get(root, ident):
    """(exit, stdout) from a REAL `python -S -I recall.py get <id> --root <root>` subprocess.

    A subprocess, not an in-process call: C-4's promise is about the CLI an operator (and step 7)
    actually runs, and an id that resolves only through an import is not the claim being made.
    """
    import subprocess
    here = pathlib.Path(__file__).resolve().parent
    proc = subprocess.run([sys.executable, "-S", "-I", str(here / "recall.py"),
                           "get", ident, "--root", str(root)], capture_output=True)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def _st_validate_inproc(root):
    """validate_entries.main() over `root`, in-process. (code, stdout) or (None, reason)."""
    import io
    try:
        import validate_entries
    except ImportError as exc:                      # pragma: no cover - sibling missing
        return None, "cannot import validate_entries: %s" % exc
    out = io.StringIO()
    err = io.StringIO()
    real_err = sys.stderr
    sys.stderr = err
    try:
        code = validate_entries.main(["--root", str(root)], _out=out)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else EXIT_USAGE
    finally:
        sys.stderr = real_err
    return code, out.getvalue()


def _st_validate_subprocess(root):
    """`python -S -I validate_entries.py --root <root>` as a REAL child. (code, tail_of_stderr).

    The in-process arm proves the corpus is clean; this one proves the mandated RUN MODE works.
    `-I` implies `-P`, so the child has neither cwd nor the script's directory on sys.path, and a
    sibling-import regression is invisible to every in-process test. `sys.executable` is resolved
    at runtime, so no build-host absolute path is baked in.
    """
    import subprocess
    script = pathlib.Path(__file__).resolve().parent / "validate_entries.py"
    if not script.is_file():                        # pragma: no cover - sibling missing
        return None, "no validate_entries.py beside %s" % pathlib.Path(__file__).name
    proc = subprocess.run([sys.executable, "-S", "-I", str(script), "--root", str(root)],
                          capture_output=True)
    tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    return proc.returncode, (tail[-1] if tail else "")


def _selftest():
    import shutil
    import tempfile
    checks = []

    def ck(label, got, want):
        checks.append((label, got, want, got == want))

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="init_project_selftest_"))
    try:
        # ---- arm 1: a fresh scaffold succeeds and produces the whole declared set. -----------
        root = tmp / "fresh"
        code, out, _err = _st_run(["new", "--root", str(root), "--name", "fresh"])
        ck("fresh scaffold -> exit 0", code, EXIT_OK)

        for (rel, _k, _l) in HOT_FILES:
            ck("hot file planted: %s" % rel, _under(root, rel).is_file(), True)
        ck("exactly TEN hot files declared", len(HOT_FILES), 10)
        ck("checkpoints/ at TOP LEVEL", (root / CHECKPOINTS_DIR).is_dir(), True)
        ck("checkpoints/ NOT under _archive/",
           (root / ARCHIVE_DIR / CHECKPOINTS_DIR).exists(), False)
        ck("_archive/ created (W30)", (root / ARCHIVE_DIR).is_dir(), True)
        for rel in DEFERRED_FILES:
            ck("S3.2 deferred file planted: %s" % rel, _under(root, rel).is_file(), True)
        ck("nothing else at top level",
           sorted(p.name for p in root.iterdir()),
           sorted([r for (r, _k, _l) in HOT_FILES] + [CHECKPOINTS_DIR, ARCHIVE_DIR]))
        ck("nothing else in _archive/",
           sorted(p.name for p in (root / ARCHIVE_DIR).iterdir()),
           sorted(r.split("/")[-1] for r in DEFERRED_FILES))
        ck("checkpoints/ is EMPTY at save#0", list((root / CHECKPOINTS_DIR).iterdir()), [])

        # ---- arm 2: the postcondition checker agrees, and the report says so. ----------------
        problems, unreliable, _cl = verify_scaffold(root)
        ck("verify_scaffold: no problems", problems, [])
        ck("verify_scaffold: no open fence", unreliable, False)
        ck("report names the scaffolded root", str(root) in out, True)

        # ---- arm 3: EXACTLY ONE ordered BACKLOG pair. ----------------------------------------
        _d, rows, _o = _rows(root / "backlog.md")
        begins = count_markers(rows, BACKLOG_BEGIN)
        ends = count_markers(rows, BACKLOG_END)
        ck("backlog.md: one BACKLOG:BEGIN", len(begins), 1)
        ck("backlog.md: one BACKLOG:END", len(ends), 1)
        ck("backlog.md: BEGIN precedes END", begins[0] < ends[0], True)
        ck("backlog.md: BACKLOG region holds no entry heading",
           [ln for (ln, text, _s, _e, f) in rows
            if not f and begins[0] < ln < ends[0] and text.startswith("##")], [])

        # ---- arm 4: state.md's declared fields, and the R-G baseline. ------------------------
        state_text = (root / "state.md").read_bytes().decode("utf-8")
        ck("state.md: schema_version", "schema_version: %s" % SCHEMA_VERSION in state_text, True)
        ck("state.md: save 0", "\nsave: 0\n" in state_text, True)
        ck("state.md: phase discovery", "\nphase: discovery\n" in state_text, True)
        ck("state.md: project name substituted", "\nproject: fresh\n" in state_text, True)
        _d, srows, _o = _rows(root / "state.md")
        ck("state.md: R-G region present (not ABSENT)",
           len(count_markers(srows, BACKLOG_IDS_BEGIN)), 1)
        ck("state.md: R-G baseline is EMPTY",
           len(count_markers(srows, BACKLOG_IDS_EMPTY)), 1)

        # ---- arm 5: LF terminators, byte-for-byte. -------------------------------------------
        crlf = [rel for rel in [r for (r, _k, _l) in HOT_FILES] + list(DEFERRED_FILES)
                if b"\r\n" in _under(root, rel).read_bytes()]
        ck("every planted file is LF, not CRLF", crlf, [])

        # ---- arm 6: the file->kind table agrees with the validator's. ------------------------
        try:
            import validate_entries as _ve
            mine = dict((rel, (k, l)) for (rel, k, l) in HOT_FILES)
            theirs = dict((rel, _ve.FILE_KIND[rel]) for rel in mine if rel in _ve.FILE_KIND)
            ck("all ten hot files are in the validator's FILE_KIND table",
               sorted(set(mine) - set(_ve.FILE_KIND)), [])
            ck("kind/lifetime agree with validate_entries.FILE_KIND",
               sorted(r for r in theirs if theirs[r] != mine[r]), [])
            ck("both deferred files are in FILE_KIND",
               sorted(r for r in DEFERRED_FILES if r not in _ve.FILE_KIND), [])
            ck("both deferred files are GATE_REQUIRED",
               sorted(r for r in DEFERRED_FILES if r not in _ve.GATE_REQUIRED), [])
        except ImportError as exc:                  # pragma: no cover - sibling missing
            ck("validate_entries importable", "ImportError: %s" % exc, "importable")

        # ---- arm 7: THE ARM THAT TIES IT TOGETHER. ------------------------------------------
        vcode, vout = _st_validate_inproc(root)
        ck("validate_entries (in-process) over a fresh scaffold -> exit 0", vcode, EXIT_OK)
        ck("validate_entries printed an EMPTY abort set",
           "ABORT SET (0)" in (vout or ""), True)
        scode, stail = _st_validate_subprocess(root)
        ck("validate_entries under `python -S -I` (subprocess) -> exit 0", scode, EXIT_OK)
        ck("subprocess run printed no traceback", "Traceback" in (stail or ""), False)

        # ---- arm 8: the refusal is COLLISION, not EMPTINESS. --------------------------------
        # THE INTERSECTION IS THE ONLY PLACE THE TWO PREDICATES DISAGREE, and it is therefore
        # the only arm that can tell this build from the one before it: a directory that is
        # NON-EMPTY and holds nothing the scaffolder writes. `ld.yml` is not a decoration --
        # it is the shape of the real folder that exposed this (one .yml plus a session's
        # working files), and the old predicate refused it.
        occupied = tmp / "occupied"
        occupied.mkdir()
        (occupied / "notes.md").write_bytes(b"real content\n")
        (occupied / "ld.yml").write_bytes(b"session work\n")
        (occupied / "docs").mkdir()
        code, _out, err = _st_run(["new", "--root", str(occupied), "--name", "occupied"])
        ck("NON-EMPTY with NO collision -> scaffolds (exit 0)", code, EXIT_OK)
        ck("every hot file landed beside the pre-existing content",
           sorted(r for (r, _k, _l) in HOT_FILES if not (occupied / r).is_file()), [])
        ck("pre-existing notes.md untouched", (occupied / "notes.md").read_bytes(),
           b"real content\n")
        ck("pre-existing ld.yml untouched", (occupied / "ld.yml").read_bytes(),
           b"session work\n")
        ck("pre-existing docs/ still a directory", (occupied / "docs").is_dir(), True)

        # ...and the COLLISION half, which is the guarantee that must NOT regress. One arm per
        # class of write-set member -- a hand-written hot file, a GENERATED one, and a deferred
        # archive nested under `_archive/` -- because a check keyed only on top-level names
        # would pass while the nested member went unguarded.
        # `CLAUDE.md` USED TO BE THE HAND-WRITTEN REPRESENTATIVE HERE AND IS NOW `ADOPTABLE`, so
        # `CONSTRAINTS.md` takes that slot. The class still needs a member: dropping the row
        # rather than replacing it would have left the hand-written class untested, which is the
        # shape where a regression hides. The adoption arms below assert the OTHER half -- that
        # exempting one member did not exempt the rest.
        for _i, _rel in enumerate(("CONSTRAINTS.md", "state.md", "_archive/backlog-deferred.md")):
            coll = tmp / ("collide%d" % _i)
            coll.mkdir()
            _p = _under(coll, _rel)
            _p.parent.mkdir(parents=True, exist_ok=True)
            _p.write_bytes(b"LIVE CONTENT\n")
            code, _out, err = _st_run(["new", "--root", str(coll), "--name", "collide"])
            ck("collision on %s -> exit 2 (NOT 1: that is --selftest's)" % _rel,
               code, EXIT_USAGE)
            # R-HOLLOWARM-1. A BARE SUBSTRING TEST CANNOT FAIL ON THE FAULT IT NAMES. Any
            # stderr mentioning the path satisfies `_rel in err`, so under a mutant where NO
            # refusal fired at all this arm stayed green because a different diagnostic named
            # the file. Measured by mutation 2026-08-29 -- the three sibling arms (exit 2,
            # bytes intact, no template written) all reddened and this one did not -- then
            # reproduced by cross-family review. Requiring the REFUSAL FRAGMENT as well as the
            # path is what makes the arm assert that THIS refusal is the thing that spoke.
            ck("collision on %s is NAMED in the refusal, WITH the refusal fragment" % _rel,
               ("refusing to scaffold" in (err or "")) and (_rel in (err or "")), True)
            ck("collision on %s left the live bytes" % _rel, _p.read_bytes(), b"LIVE CONTENT\n")
            ck("collision on %s wrote NO template" % _rel,
               sorted(r for (r, _k, _l) in HOT_FILES if r != _rel and (coll / r).exists()), [])

        # ---- ADOPTION (ADOPTABLE). The third state: present BEFORE the scaffold, kept as-is.
        # THE ARMS THAT DISCRIMINATE ARE THE EXIT-CODE AND RECEIPT ONES, NOT ALL OF THEM. The
        # claim that stood here was "EVERY ARM HERE GOES RED ON THE PRE-2026-08-30 BUILD", and
        # this project's own mutant run refutes it: removing ADOPTABLE reddened 7 of 13. The
        # untouched-bytes and no-`cites` arms hold on the old build too, because a refusal that
        # writes nothing also leaves bytes untouched. They are kept -- they are the C-4
        # guarantee -- but they are not evidence the change landed, and the arms below that ARE
        # evidence now say so in their own text.
        #
        # THE FIXTURE IS DELIBERATELY AN ORDINARY PROSE CHARTER, NOT AN save-SHAPED ONE, and
        # that is the arm's whole point. `## File Organization`, `## Project Context` and `## Git`
        # each match NO citemap family -- verified against `citemap.find_definition`, all three
        # return None -- so under an adoption that did not also exempt the adopted file from
        # `verify_scaffold`'s planted-id loop, each becomes a `problem`, `problems` becomes a
        # RuntimeError, and the run dies at EXIT_INTERNAL. A fixture written in save's own
        # `## R-RULE-1` style would resolve, pass, and prove nothing about the real case: every
        # real charter is prose. That substitution is exactly how an arm ends up named after the
        # right case while asserting a property the fault does not disturb.
        _CHARTER = (b"# example-project - project rules\n\n"
                    b"## File Organization\n\nArtifacts live under docs/.\n\n"
                    b"## Project Context\n\nSee C-4 and D60 for the archive contract.\n\n"
                    b"## Git\n\nOne repo per project.\n")
        adopt = tmp / "adopted"
        adopt.mkdir()
        (adopt / "CLAUDE.md").write_bytes(_CHARTER)
        code, out_txt, err = _st_run(["new", "--root", str(adopt), "--name", "adopted"])
        ck("ADOPT: an existing prose CLAUDE.md scaffolds (exit 0, NOT 2 and NOT 7)",
           code, EXIT_OK)
        ck("ADOPT: the charter's bytes are untouched -- nothing written, nothing appended",
           (adopt / "CLAUDE.md").read_bytes(), _CHARTER)
        ck("ADOPT: every other hot file landed",
           sorted(r for (r, _k, _l) in HOT_FILES
                  if r != "CLAUDE.md" and not (adopt / r).is_file()), [])
        ck("ADOPT: both deferred archives landed",
           sorted(r for r in DEFERRED_FILES if not _under(adopt, r).is_file()), [])
        # EXACT receipt line, not a substring of it. A bare `"ADOPTED" in out` was satisfiable by
        # any diagnostic mentioning the word; this pins the sentence the operator actually reads,
        # so an edit to it reddens rather than passing on a coincidence.
        ck("ADOPT: the receipt DISCLOSES the adoption in the exact words the operator reads",
           "  NOT scaffolded (ADOPTED, already present): CLAUDE.md -- THIS SCAFFOLDER WROTE "
           "NOTHING THERE and appended nothing." in (out_txt or ""), True)
        # NEGATIVES OVER THE WHOLE STREAM, MINUS THE PATHS IT ECHOES. The positive above is a
        # substring test, so a superset passes it -- appending "Bytes were preserved." satisfied
        # both that check and a negative keyed only on the old phrase. Hence the whole stream.
        # BUT THE STREAM CARRIES THE TARGET PATH TWICE (`scaffolded <path>`, `next: validate ...
        # <path>`), AND A PATH IS NOT PROSE. MEASURED 2026-08-30, both builds under the same
        # `TMP`/`TEMP` root named `preserved-root`: the pre-fix arm reported 162 checks 1 FAILED
        # at exit 1 on a CORRECT receipt, the fixed one 162 checks 0 failed. The vector is the
        # temp ROOT the environment supplies, not the random suffix `tempfile` appends.
        # Blanking the paths keeps the whole-stream coverage and removes the only text in it this
        # tool did not compose. NOT narrowed to the ADOPTED line: a preservation claim anywhere in
        # the receipt is the thing being forbidden, and one line is exactly the coverage that let
        # the earlier wording through.
        _receipt = out_txt or ""
        for _p in (str(adopt), str(tmp)):
            _receipt = _receipt.replace(_p, "<path>")
        ck("ADOPT: the receipt makes NO byte-preservation claim in any wording",
           any(w in _receipt.lower()
               for w in ("byte-for-byte", "byte for byte", "bytes were preserved",
                         "unchanged", "preserved")), False)
        ck("ADOPT: ...and the path-blanking left the receipt itself intact to be scanned",
           "NOT scaffolded (ADOPTED, already present)" in _receipt, True)
        ck("ADOPT: the receipt counts the hot files this run wrote",
           ("%d hot file(s)" % (len(HOT_FILES) - 1)) in (out_txt or ""), True)
        # The citation half, which is a SEPARATE loop from the heading half and fails separately:
        # the charter cites `C-4` and `D60`, save's own keys, which resolve to nothing here.
        ck("ADOPT: ids the charter CITES are not judged as planted boilerplate",
           "cites" in (err or ""), False)

        # Both host-native instruction files are first-class hot files. A project may carry
        # distinct product instructions in each; neither may be treated as an unrelated file,
        # templated over, or collapsed into the other.
        dual = tmp / "dual-instructions"
        dual.mkdir()
        _AGENTS = (b"# Codex rules\r\n\r\n## Build\r\n\r\nCODEX_SENTINEL\r\n")
        _CLAUDE = (b"# Claude rules\n\n## Review\n\nCLAUDE_SENTINEL\n")
        (dual / "AGENTS.md").write_bytes(_AGENTS)
        (dual / "CLAUDE.md").write_bytes(_CLAUDE)
        code, dual_out, _err = _st_run(["new", "--root", str(dual), "--name", "dual-instructions"])
        ck("DUAL INSTRUCTIONS: distinct AGENTS.md and CLAUDE.md scaffold at exit 0", code, EXIT_OK)
        ck("DUAL INSTRUCTIONS: AGENTS.md bytes are untouched",
           (dual / "AGENTS.md").read_bytes(), _AGENTS)
        ck("DUAL INSTRUCTIONS: CLAUDE.md bytes are untouched",
           (dual / "CLAUDE.md").read_bytes(), _CLAUDE)
        ck("DUAL INSTRUCTIONS: every non-instruction hot file landed",
           sorted(r for (r, _k, _l) in HOT_FILES
                  if r not in ADOPTABLE and not (dual / r).is_file()), [])
        ck("DUAL INSTRUCTIONS: receipt names AGENTS.md adoption",
           "NOT scaffolded (ADOPTED, already present): AGENTS.md" in (dual_out or ""), True)
        ck("DUAL INSTRUCTIONS: receipt names CLAUDE.md adoption",
           "NOT scaffolded (ADOPTED, already present): CLAUDE.md" in (dual_out or ""), True)
        ck("DUAL INSTRUCTIONS: receipt counts only the eight written hot files",
           ("%d hot file(s)" % (len(HOT_FILES) - 2)) in (dual_out or ""), True)

        # AGENTS.md is PASSIVE rather than CLAUDE.md-style adoptable. Its headings, citations,
        # declarations and filesystem shape are outside save validation. A duplicate M1 here
        # therefore cannot turn a valid scaffold into an identifier-clash refusal.
        passive = tmp / "passive-agents"
        passive.mkdir()
        _PASSIVE = (b"# Codex rules\r\n\r\n## M1 - deliberately duplicates the seeded plan id\r\n"
                    b"This is host-owned prose with no save declarations.\r\n")
        (passive / "AGENTS.md").write_bytes(_PASSIVE)
        code, passive_out, err = _st_run(
            ["new", "--root", str(passive), "--name", "passive-agents"])
        ck("PASSIVE AGENTS: an opaque charter never enters identifier validation", code, EXIT_OK)
        ck("PASSIVE AGENTS: its bytes are untouched", (passive / "AGENTS.md").read_bytes(),
           _PASSIVE)
        ck("PASSIVE AGENTS: no identifier-clash report was emitted",
           "DEFINED AT" in (err or ""), False)
        ck("PASSIVE AGENTS: receipt names the preserved file",
           "NOT scaffolded (ADOPTED, already present): AGENTS.md" in (passive_out or ""), True)

        # The opaque promise is about the directory entry, not its target. A dangling link is
        # preserved as-is and the remainder still scaffolds. This is the separating input: the
        # historical CLAUDE.md classifier below must continue to refuse the same shape.
        opaque_link = tmp / "opaque-agents-link"
        opaque_link.mkdir()
        if _st_make_dangling(opaque_link / "AGENTS.md", opaque_link / "_agents_target"):
            code, _out, _err = _st_run(
                ["new", "--root", str(opaque_link), "--name", "opaque-agents-link"])
            ck("PASSIVE AGENTS: a dangling link is preserved, not refused", code, EXIT_OK)
            ck("PASSIVE AGENTS: the dangling directory entry still exists",
               os.path.lexists(str(opaque_link / "AGENTS.md")), True)
            ck("PASSIVE AGENTS: every other hot file landed",
               sorted(r for (r, _k, _l) in HOT_FILES
                      if r != "AGENTS.md" and not (opaque_link / r).is_file()), [])
        else:
            sys.stderr.write("note: passive-AGENTS symlink arm SKIPPED -- this platform produced "
                             "no dangling directory entry.\n")

        claude_link = tmp / "claude-link-control"
        claude_link.mkdir()
        if _st_make_dangling(claude_link / "CLAUDE.md", claude_link / "_claude_target"):
            code, _out, err = _st_run(
                ["new", "--root", str(claude_link), "--name", "claude-link-control"])
            ck("CLAUDE CONTROL: the historical dangling-link refusal is unchanged",
               code, EXIT_USAGE)
            ck("CLAUDE CONTROL: its refusal still identifies the adoptable path",
               "CLAUDE.md" in (err or "") and "refusing to scaffold" in (err or ""), True)
            ck("CLAUDE CONTROL: nothing else was written before the refusal",
               sorted(p.name for p in claude_link.iterdir()), ["CLAUDE.md"])
        else:
            sys.stderr.write("note: CLAUDE-link control SKIPPED -- this platform produced no "
                             "dangling directory entry.\n")

        # ADOPTION IS KEYED ON CONTENT, NOT PRESENCE. A zero-byte file has nothing to protect, so
        # it takes the template -- otherwise the folder keeps an empty hot file forever.
        empty_c = tmp / "emptyclaude"
        empty_c.mkdir()
        (empty_c / "CLAUDE.md").write_bytes(b"")
        code, _out, _err = _st_run(["new", "--root", str(empty_c), "--name", "emptyclaude"])
        ck("ADOPT: a ZERO-BYTE CLAUDE.md is templated, not adopted (exit 0)", code, EXIT_OK)
        ck("ADOPT: and that template actually landed",
           (empty_c / "CLAUDE.md").stat().st_size > 0, True)

        # R-CLAUDE-77. An unbalanced fence in a file THIS TOOL DOES NOT WRITE may warn and may
        # never move the exit code: the operator's only way to clear it would be to stop writing
        # their own charter the way they meant to. The same fence in a file we DID write is still
        # an integrity failure -- that arm is elsewhere and is not weakened here.
        # THE FIXTURE DIRECTORY IS DELIBERATELY NOT NAMED AFTER THE THING UNDER TEST. It was
        # `adoptfence`, and the refusal path prints the folder -- so `"fence" in err.lower()`
        # was satisfied by the PATH whether or not any fence warning fired: an arm keyed on a
        # token the fixture's own name supplies cannot fail on the fault it guards.
        fenced = tmp / "openblock"
        fenced.mkdir()
        _NL = bytes([10])
        (fenced / "CLAUDE.md").write_bytes(_NL.join(
            [b"# c", b"", b"## Rules", b"", b"```text", b"unclosed", b""]))
        code, _out, err = _st_run(["new", "--root", str(fenced), "--name", "openblock"])
        ck("ADOPT: an OPEN FENCE in the adopted charter does not move the exit code",
           code, EXIT_OK)
        # Pinned to `warn_open_fence`'s own words, not a substring the fixture could supply.
        ck("ADOPT: ...but the OPEN FENCE is warned about, in the warning's own words",
           "SCAN INTEGRITY -- CLAUDE.md ends with an OPEN code fence" in (err or ""), True)

        # EXEMPTING ONE MEMBER DID NOT OPEN THE GATE FOR THE REST. This is the pair R-CLAUDE-48
        # asks for: `does the new thing fire` and `does the old thing still fire` are two
        # questions about one predicate, and the defect would live between them.
        both = tmp / "adoptplus"
        both.mkdir()
        (both / "CLAUDE.md").write_bytes(_CHARTER)
        (both / "design.md").write_bytes(b"LIVE CONTENT\n")
        code, _out, err = _st_run(["new", "--root", str(both), "--name", "adoptplus"])
        ck("ADOPT: an adoptable file BESIDE a real collision still refuses (exit 2)",
           code, EXIT_USAGE)
        ck("ADOPT: the refusal names the COLLIDING file, not the adopted one",
           ("design.md" in (err or "")) and ("refusing to scaffold" in (err or "")), True)
        # THE DISCRIMINATING HALF: the old build listed CLAUDE.md in the collision set too, so an
        # arm that only checked for `design.md` passed there unchanged. The COUNT separates them
        # -- one collision here, two on the old build.
        # NOT a bare `"CLAUDE.md" not in err`: the refusal names it in a trailing explanatory
        # clause ("An existing CLAUDE.md is NOT a collision: it is adopted as-is"), so that
        # assertion FAILS ON CORRECT OUTPUT. Measured here, which is why the arm reads the count.
        ck("ADOPT: exactly ONE collision is reported, so CLAUDE.md was not counted as one",
           "already holds 1 file(s)" in (err or ""), True)
        ck("ADOPT: the refused run left the colliding bytes alone",
           (both / "design.md").read_bytes(), b"LIVE CONTENT\n")

        # --carried AND ALREADY PRESENT IS A CONTRADICTION, REFUSED EARLY. Before this check the
        # run reached verify_scaffold's carried-absence assertion and died at EXIT_INTERNAL on a
        # PARTIALLY SCAFFOLDED folder, under a message false in both clauses ("the skip did not
        # engage", "would have been written over") -- nothing was written and the file was intact.
        cc = tmp / "carriedpresent"
        cc.mkdir()
        (cc / "CLAUDE.md").write_bytes(_CHARTER)
        code, _out, err = _st_run(["new", "--root", str(cc), "--name", "carriedpresent",
                                   "--carried", "CLAUDE.md"])
        ck("ADOPT: --carried on an already-present path refuses at exit 2, not exit 7",
           code, EXIT_USAGE)
        ck("ADOPT: ...and refuses BEFORE writing, so the folder is untouched",
           sorted(r for (r, _k, _l) in HOT_FILES if r != "CLAUDE.md" and (cc / r).exists()), [])
        ck("ADOPT: ...leaving the charter exactly as it was",
           (cc / "CLAUDE.md").read_bytes(), _CHARTER)

        # THE PROBE FAILS CLOSED. A directory at CLAUDE.md used to slip through `.exists()`:
        # exempt from the collision set, false for adoption, so every other file was written and
        # then `os.replace` failed on the directory -- a partly scaffolded folder at exit 7.
        dirc = tmp / "dirclaude"
        dirc.mkdir()
        (dirc / "CLAUDE.md").mkdir()
        code, _out, err = _st_run(["new", "--root", str(dirc), "--name", "dirclaude"])
        ck("ADOPT: a DIRECTORY at CLAUDE.md is refused (exit 2), not silently templated",
           code, EXIT_USAGE)
        # THE THREE CHECKS ABOVE AND BELOW ALL PASS ON A FAIL-OPEN CLASSIFIER, WHICH IS WHY THE
        # NEXT ONE EXISTS. If classification regressed to ADOPT_TEMPLATE, `atomic_write` would
        # create `CLAUDE.md.tmp-write` and `os.replace` would then raise IsADirectoryError --
        # main maps it to exit 2, its text contains "directory", and a HOT_FILES-only listing
        # never looks at the sidecar. Every arm stays green while the probe is broken. Assert the
        # CLASSIFIER's own answer, pin the refusal to this tool's sentence rather than to a word
        # the OS also uses, and read the WHOLE directory so a sidecar cannot hide beside it.
        ck("ADOPT: ...because the CLASSIFIER refused it, not because a write failed later",
           classify_adoptable(dirc, "CLAUDE.md")[0], ADOPT_REFUSE)
        ck("ADOPT: ...the refusal is this tool's own, and says which thing it was",
           ("refusing to scaffold" in (err or "")) and ("it is a directory" in (err or "")), True)
        ck("ADOPT: ...and nothing was written before the refusal -- no template, NO SIDECAR",
           sorted(p.name for p in dirc.iterdir()), ["CLAUDE.md"])

        # THE ADOPTED FILE STILL CONTRIBUTES DEFINITIONS, AND THIS ARM HAD TO BE REBUILT TO SEE
        # IT. Filtering the adopted file out of the citation MAP (rather than out of the
        # REPORTING) removes the ids it DEFINES, so a file THIS TOOL WROTE that cites one is
        # reported dangling and the run exits 7 after writing.
        # THE FIRST VERSION OF THIS ARM WAS HOLLOW AND A MUTANT PROVED IT: a charter defining
        # `R-RULE-9` that NOTHING CITES produces no finding either way, so the arm stayed green
        # on the broken build. `cited_in` empty means no row is ever emitted. A definition is
        # only observable THROUGH a citation, so the fixture must plant one -- in a file the
        # scaffolder itself wrote, which is the case the finding is actually about.
        defs = tmp / "adoptdefs"
        defs.mkdir()
        (defs / "CLAUDE.md").write_bytes(b"# d\n\n## R-RULE-9 - a rule the charter defines\n"
                                         b"kind: rules\nlifetime: permanent\n\nBody.\n")
        code, _out, err = _st_run(["new", "--root", str(defs), "--name", "adoptdefs"])
        ck("ADOPT: a charter that DEFINES an id still scaffolds (exit 0)", code, EXIT_OK)
        with (defs / "architecture.md").open("a", encoding="utf-8") as _fh:
            _fh.write("\nSee R-RULE-9 for the rule this depends on.\n")
        _dfams, _ = citemap.project_families(defs)
        ck("ADOPT: an id DEFINED in the charter and CITED by a written file is not dangling",
           planted_citation_problems(defs, _dfams, report_exclude=("CLAUDE.md",))[0], [])
        _dprobs, _, _dcl = verify_scaffold(defs, (), ("CLAUDE.md",))
        ck("ADOPT: ...so the whole postcondition set stays clean on that folder", _dprobs, [])

        # AN ID DEFINED IN BOTH THE CHARTER AND A TEMPLATE MUST BE REPORTED. This became
        # reachable only when the adopted file started contributing definitions: before that,
        # every definition in the map came from files we wrote, which cannot collide with each
        # other. `backlog.md` seeds `## M1`, so a charter defining M1 produces two sites.
        # REPRODUCED 2026-08-31 on the unfixed build: scaffold exit 0, then `recall get M1`
        # exit 4 -- a folder born with an id that addresses nothing (C-4).
        coll2 = tmp / "idclash"
        coll2.mkdir()
        _NL2 = bytes([10])
        (coll2 / "CLAUDE.md").write_bytes(_NL2.join(
            [b"# c", b"", b"## M1 - the owner rule", b"kind: rules",
             b"lifetime: permanent", b"", b"Body.", b""]))
        code, _out, err = _st_run(["new", "--root", str(coll2), "--name", "idclash"])
        # PIN THE CODE, NOT ITS NEGATION. `code == EXIT_OK, False` was satisfied by 3, 4, 5 and
        # 6 as well, and the suite-wide guards reject only 1 and 7 -- so the documented exit-2
        # contract could regress while this arm stayed green.
        ck("ADOPT: an id defined by BOTH the charter and a template exits 2, the documented code",
           code, EXIT_USAGE)
        ck("ADOPT: ...and the report names the id and both sites",
           ("M1" in (err or "")) and ("DEFINED AT 2 SITES" in (err or "")), True)
        ck("ADOPT: ...and says the folder is COMPLETE, so exit 2 is not read as a failed scaffold",
           "the scaffold COMPLETED" in (err or ""), True)
        ck("ADOPT: ...and attributes the sites by FILE rather than assuming one is the tool's",
           "CLAUDE.md is ADOPTED" in (err or ""), True)

        # ...AND THE SHAPE THE OWNERSHIP SENTENCE USED TO BE FALSE ON: both definitions inside the
        # adopted charter, with no template defining the id at all. The old advice named one site
        # as "this tool's" over a folder where neither was.
        coll3 = tmp / "idclash-both-theirs"
        coll3.mkdir()
        (coll3 / "CLAUDE.md").write_bytes(_NL2.join(
            [b"# c", b"", b"## R-RULE-9 - first", b"kind: rules", b"lifetime: permanent", b"",
             b"Body.", b"", b"## R-RULE-9 - second", b"kind: rules", b"lifetime: permanent", b"",
             b"Body.", b""]))
        code, _out, err = _st_run(["new", "--root", str(coll3), "--name", "idclashboth"])
        ck("ADOPT: two definitions INSIDE the charter are reported at exit 2", code, EXIT_USAGE)
        ck("ADOPT: ...and the advice does NOT claim a scaffolded site this tool wrote",
           "All 2 definitions are in ADOPTED file(s)" in (err or ""), True)
        ck("ADOPT: ...and it says what has to be TRUE at the end, not which file to touch",
           "EXACTLY ONE" in (err or ""), True)

        # THE MIXED THREE-SITE CASE, WHICH IS WHERE COLLAPSING SITES TO FILE NAMES GAVE
        # ADVICE THAT DOES NOT RESOLVE THE PROBLEM. Two `M1` in the adopted charter plus
        # `backlog.md`'s own seeded one: "rename the scaffolded one" leaves TWO behind, so
        # `recall get M1` still exits 4 while the receipt reads as though it is settled.
        # The two-site arm above CANNOT see this -- there, renaming the scaffolded one IS
        # sufficient, so the wrong advice and the right advice have the same effect.
        coll4 = tmp / "idclash-three-sites"
        coll4.mkdir()
        (coll4 / "CLAUDE.md").write_bytes(_NL2.join(
            [b"# c", b"", b"## M1 - the owner rule", b"kind: rules", b"lifetime: permanent",
             b"", b"Body.", b"", b"## M1 - the owner rule, said twice", b"kind: rules",
             b"lifetime: permanent", b"", b"Body.", b""]))
        code, _out, err = _st_run(["new", "--root", str(coll4), "--name", "idclashthree"])
        ck("ADOPT: two adopted definitions PLUS a template one exit 2", code, EXIT_USAGE)
        ck("ADOPT: ...the report counts all THREE sites", "DEFINED AT 3 SITES" in (err or ""),
           True)
        ck("ADOPT: ...and says renaming the scaffolded one is NOT ENOUGH",
           "is NOT enough" in (err or ""), True)
        ck("ADOPT: ...naming how many the adopted file holds on its own",
           "hold 2 definitions of this id ON THEIR OWN" in (err or ""), True)

        # A DANGLING ENTRY AT A WRITE-SET PATH IS A COLLISION -- THE ARM THAT DID NOT EXIST,
        # AND WHOSE ABSENCE WAS WRITTEN DOWN IN THIS FILE AS AN IMPOSSIBILITY. REPRODUCED on the
        # pre-fix build 2026-08-30: a broken junction at `design.md` slipped the `.exists()` gate,
        # 2 of 9 hot files were written, `os.replace` then failed with a raw WinError 5, and the
        # folder was left partly scaffolded with an orphaned `design.md.tmp-write` in it.
        dang = tmp / "danglingentry"
        dang.mkdir()
        if _st_make_dangling(dang / "design.md", dang / "_junction_target"):
            code, _out, err = _st_run(["new", "--root", str(dang), "--name", "danglingentry"])
            ck("DANGLING: an entry at a write-set path is a COLLISION, refused at exit 2",
               code, EXIT_USAGE)
            ck("DANGLING: ...the refusal names the path and calls it a collision",
               ("design.md" in (err or "")) and ("already holds" in (err or "")), True)
            ck("DANGLING: ...and NOTHING was written -- no template, no .tmp-write sidecar",
               sorted(p.name for p in dang.iterdir()), ["design.md"])
        else:
            sys.stderr.write("note: DANGLING arm SKIPPED -- this platform produced no entry "
                             "whose lstat succeeds while exists() is False. The arm is not a "
                             "pass; nothing was measured about the collision gate here.\n")

        # _occupied REFUSES INDETERMINATE METADATA rather than answering "absent". The stimulus
        # is a name the filesystem rejects outright, which lstat reports as a plain OSError
        # (EINVAL) and not FileNotFoundError. Where a platform treats it as merely absent there
        # is no stimulus, and the arm SKIPS rather than asserting something it did not exercise.
        _badpath = tmp / "inva*lid"
        try:
            _badpath.lstat()
            _indeterminate = False
        except FileNotFoundError:
            _indeterminate = False
        except OSError:
            _indeterminate = True
        if _indeterminate:
            try:
                _occupied(_badpath)
                _occ_result = "returned without refusing"
            except ValueError:
                _occ_result = "refused"
            except OSError:
                _occ_result = "leaked a raw OSError"
            ck("_occupied REFUSES an entry whose metadata cannot be read, never 'absent'",
               _occ_result, "refused")
        else:
            sys.stderr.write("note: indeterminate-metadata arm SKIPPED -- this platform reports "
                             "the fixture path as plainly absent, so nothing was measured about "
                             "_occupied's non-FileNotFoundError branch here.\n")

        # BOTH EARLY RETURNS OF planted_citation_problems OWE THE TUPLE THE NORMAL PATH RETURNS.
        # A bare list there raised `ValueError: not enough values to unpack` at the call site --
        # so the branch that exists to REPORT a condition destroyed the report instead. Wrapped
        # rather than unpacked directly, because an exception here would surface as exit 7 from
        # the whole suite instead of as a red arm naming the defect.
        noscan = tmp / "noscan"
        noscan.mkdir()
        _nsfam, _ = citemap.project_families(noscan)
        try:
            _ns = planted_citation_problems(noscan, _nsfam)
            _ns_arity = len(_ns)
        except Exception as _exc:                            # noqa: BLE001 - reported as an arm
            _ns, _ns_arity = ([], []), "raised %s" % type(_exc).__name__
        ck("ARITY: planted_citation_problems on a tree with no hot file returns two values",
           _ns_arity, 2)
        ck("ARITY: ...and REPORTS the condition instead of losing it",
           any("no hot or deferred file" in _p for _p in _ns[0]), True)
        try:
            _vs_arity = len(verify_scaffold(noscan))
        except Exception as _exc:                            # noqa: BLE001 - reported as an arm
            _vs_arity = "raised %s" % type(_exc).__name__
        ck("ARITY: verify_scaffold survives that tree and returns three values", _vs_arity, 3)

        # ...AND THE OTHER EARLY RETURN, WHICH NO FIXTURE CAN REACH: citemap raising. Patched in
        # place and restored in a finally -- the alternative is a corpus built to break a library,
        # which tests the corpus rather than this branch.
        _rootfam, _ = citemap.project_families(root)
        _real_build_map = citemap.build_map

        def _raising_build_map(*_a, **_k):
            raise RuntimeError("probe: citemap failure")

        try:
            citemap.build_map = _raising_build_map
            try:
                _cb = planted_citation_problems(root, _rootfam)
                _cb_arity = len(_cb)
            except Exception as _exc:                        # noqa: BLE001 - reported as an arm
                _cb, _cb_arity = ([], []), "raised %s" % type(_exc).__name__
        finally:
            citemap.build_map = _real_build_map
        ck("ARITY: the citemap-failure early return also returns two values", _cb_arity, 2)
        ck("ARITY: ...and names the failure it caught",
           any("citemap raised" in _p for _p in _cb[0]), True)
        ck("ARITY: ...and the patch was restored", citemap.build_map is _real_build_map, True)

        # a scaffolded folder is itself non-empty: re-running must refuse, not overwrite.
        code, _out, _err = _st_run(["new", "--root", str(root), "--name", "fresh"])
        ck("re-scaffolding an existing project -> exit 2", code, EXIT_USAGE)

        # an EMPTY existing directory is fine -- that is what `mkdir` leaves behind.
        pre = tmp / "premade"
        pre.mkdir()
        code, _out, _err = _st_run(["new", "--root", str(pre), "--name", "premade"])
        ck("empty existing directory -> exit 0", code, EXIT_OK)

        # ---- arm 9: usage refusals, all exit 2. ---------------------------------------------
        code, _out, err = _st_run(["init", "--root", str(tmp / "verb"), "--name", "verb"])
        ck("verb `init` -> exit 2", code, EXIT_USAGE)
        ck("verb refusal names `new` (W9)", "new" in err, True)
        ck("verb refusal did not create the folder", (tmp / "verb").exists(), False)

        code, _out, _err = _st_run(["new", "--name", "noroot"])
        ck("missing --root -> exit 2", code, EXIT_USAGE)

        code, _out, _err = _st_run(["new", "--root", str(tmp / "bad"), "--name", "bad name"])
        ck("--name with a space -> exit 2", code, EXIT_USAGE)
        ck("bad --name did not create the folder", (tmp / "bad").exists(), False)

        code, _out, _err = _st_run(["new", "--root", str(tmp / "long"), "--name", "x" * 65])
        ck("--name over 64 chars -> exit 2", code, EXIT_USAGE)

        afile = tmp / "afile"
        afile.write_bytes(b"x")
        code, _out, _err = _st_run(["new", "--root", str(afile), "--name", "afile"])
        ck("--root that is a FILE -> exit 2", code, EXIT_USAGE)

        # ---- arm 10: --name defaults to the folder basename. ---------------------------------
        dflt = tmp / "defaulted"
        code, _out, _err = _st_run(["new", "--root", str(dflt)])
        ck("--name omitted -> exit 0", code, EXIT_OK)
        ck("--name defaults to the basename",
           "\nproject: defaulted\n" in (dflt / "state.md").read_bytes().decode("utf-8"), True)

        # ---- arm 11: no build-host absolute path in what we PLANT. ---------------------------
        planted = b"".join(_under(root, rel).read_bytes()
                           for rel in [r for (r, _k, _l) in HOT_FILES] + list(DEFERRED_FILES))
        ck("planted bytes carry no drive-letter path", b":/Users" in planted or b":\\" in planted,
           False)

        # ---- arm 13: EVERY PLANTED ID RESOLVES, and the live one does so END TO END. ---------
        # MEASURED 2026-08-03: six planted ids matched no citemap family, so the one entry a
        # fresh project ships was unaddressable by `recall get` before AND after step 6a
        # moved it -- a C-4 stub with no handle. Both halves are checked: the structural
        # postcondition (which now runs on every scaffold, not just here) and the real
        # `recall get` CLI over the seeded item.
        families = citemap.build_families()
        unresolved = []
        for rel in [r for (r, _k, _l) in HOT_FILES if r not in GENERATED_FILES] \
                + list(DEFERRED_FILES):
            _d, hrows, _o = _rows(_under(root, rel))
            for (lineno, text, _s, _e, _f) in hrows:
                m = citemap.HEADING_RE.match(text or "")
                if m and len(m.group(1)) >= 2 and citemap.find_definition(
                        "#" * len(m.group(1)) + " " + m.group(3), families) is None:
                    unresolved.append("%s:%d %s" % (rel, lineno, (text or "").strip()[:40]))
        ck("every planted id (fenced examples included) resolves to a citemap family",
           unresolved, [])
        ck("the LIVE seeded plan item resolves", _st_ids_of(root, "backlog.md"), ["M1"])
        got = _st_recall_get(root, "M1")
        ck("`recall get M1` over a fresh scaffold -> exit 0", got[0], EXIT_OK)
        ck("`recall get M1` returned the seeded entry", "State the project goal" in got[1],
           True)
        ck("GENERATED_FILES are the two the spec exempts (S3.5)",
           sorted(GENERATED_FILES), ["manifest.md", "state.md"])

        # ...and a scaffold with an unresolvable id is REFUSED as a broken tool, not shipped.
        broken = tmp / "brokenid"
        code, _out, _err = _st_run(["new", "--root", str(broken), "--name", "brokenid"])
        ck("control scaffold ok", code, EXIT_OK)
        (broken / "backlog.md").write_bytes(
            (broken / "backlog.md").read_bytes().replace(b"## M1 - State", b"## P-1 - State"))
        probs, _unrel, _cl = verify_scaffold(broken)
        ck("verify_scaffold CATCHES an unresolvable planted id",
           [p for p in probs if "matches no citemap" in p] != [], True)

        # ---- arm 14: state.md carries the S3.1 cursor fields. --------------------------------
        # spec S3.1: "phase, save#, counts, hot_state, backlog_order (S7.2 R7),
        # no_demote_streak (S5.1), the resolved task-list id (S7.2 R2), the owning repo root
        # (S2.5 r3)". Three of those had neither storage nor a writer; the storage is here now.
        st = (root / "state.md").read_bytes().decode("utf-8")
        for field in ("phase", "save", "hot_state", "backlog_order", "no_demote_streak",
                      "task_list_id", "repo_root", "counts"):
            ck("state.md carries `%s:`" % field, ("\n%s:" % field) in st, True)
        ck("no_demote_streak starts at 0", "\nno_demote_streak: 0\n" in st, True)
        ck("state.md says who does NOT write no_demote_streak",
           "NO P1 component" in st, True)

        # ---- arm 15: manifest.md is ONE ROW PER CLASS, never per file. -----------------------
        # spec S3.1: "One row per class, never per file"; S3.5 names per-file rows as the
        # generator-bug shape. MEASURED 2026-08-03: 17 per-file rows (9 hot + 8 archive).
        man = (root / "manifest.md").read_bytes().decode("utf-8")
        rows_with_pipes = [ln for ln in man.splitlines()
                           if ln.startswith("|") and not set(ln) <= set("|- ")]
        # A per-FILE row is one whose FIRST cell names a file. A CLASS row may still LIST files in
        # a later cell -- that is the router doing its job, not a row per file.
        per_file = [ln for ln in rows_with_pipes
                    if ln.split("|")[1].strip().strip("`")
                    in [r for (r, _k, _l) in HOT_FILES] + [d for d in DEFERRED_FILES]]
        ck("manifest.md has no per-FILE row", per_file, [])
        ck("manifest.md's hot table is one row per (kind, lifetime) class",
           len([ln for ln in rows_with_pipes if "` / `" in ln]),
           len(set((k, l) for (_r, k, l) in HOT_FILES)))
        ck("manifest.md says so in words", "ONE ROW PER CLASS" in man, True)

        # ---- arm 16: THE CARRY RULE -- what `--carried` REFUSES, and what it HONOURS. --------
        # MEASURED 2026-08-10 by blanking each guard in turn: with the unknown-path guard removed
        # this suite still reported 80 checks / 0 failed while `--carried typo_does_not_exist.md`
        # scaffolded at exit 0, the skip silently a no-op; with the MACHINE-MANAGED guard removed
        # it likewise stayed 80/0 while a carry over `backlog.md` was authorised. Two real
        # refusals and one real honouring had no arm at all. OF THE 20 CHECKS BELOW, THE 12
        # REFUSAL ARMS WERE OBSERVED RED against the corresponding blanked guard and GREEN against
        # the shipped one -- 4 on the unknown-path build, 8 on the MACHINE-MANAGED build (both
        # names x 4 arms), re-measured 2026-08-10 at 106 checks / 4 failed and 106 / 8 failed.
        # THE REMAINING 8 ARE CONTROLS FOR THESE TWO GUARDS AND CANNOT GO RED TO EITHER BLANKING:
        # they exercise a LEGAL carry and `verify_scaffold`, neither of which either guard gates,
        # so they stayed GREEN under BOTH blanked builds. That is what a control is FOR -- without
        # them every arm above also passes on a scaffolder that refuses every carry -- but a
        # certification that claims red for them claims evidence no mutation can produce, and this
        # sentence did until it was corrected. They are marked at their own `ck` lines, and marked
        # "control for the --carried guards" rather than a bare "CONTROL" because that is the only
        # thing they are controls FOR: the receipt-count arm among them is a LIVE arm against a
        # different defect, and goes RED when `hot_written` is rendered from `len(HOT_FILES)`
        # instead of the writes -- MEASURED 2026-08-10, 106 checks / 1 failed. An unqualified
        # "CONTROL" would have understated it in the same direction this whole block corrects.
        unk = tmp / "carry-unknown"
        code, _out, err = _st_run(["new", "--root", str(unk), "--name", "carry-unknown",
                                   "--carried", "typo_does_not_exist.md"])
        ck("--carried naming a path this scaffolder does not write -> exit 2", code, EXIT_USAGE)
        ck("the unknown-path refusal NAMES the offending path",
           "typo_does_not_exist.md" in err, True)
        ck("the unknown-path refusal says WHY (a typo would turn the skip into a no-op)",
           "this scaffolder does not write" in err, True)
        ck("a REFUSED --carried plants NOTHING",
           sorted(p.name for p in unk.iterdir()) if unk.is_dir() else [], [])

        # ...and the MACHINE-MANAGED half, for BOTH names `machine_region_rels()` derives. The
        # remedy there is a rename in the source: a carry would put a hand-written body inside a
        # region the drain rewrites on every save.
        for _rel in ("backlog.md", "state.md"):
            mroot = tmp / ("carry-machine-" + _rel.replace(".md", ""))
            code, _out, err = _st_run(["new", "--root", str(mroot), "--name", mroot.name,
                                       "--carried", _rel])
            ck("--carried %s (MACHINE-MANAGED) -> exit 2" % _rel, code, EXIT_USAGE)
            ck("the %s refusal says MACHINE-MANAGED" % _rel, "MACHINE-MANAGED" in err, True)
            ck("the %s refusal names the path and the remedy" % _rel,
               _rel in err and "Rename it IN THE SOURCE" in err, True)
            ck("a refused MACHINE-MANAGED carry plants NOTHING at %s" % _rel,
               sorted(p.name for p in mroot.iterdir()) if mroot.is_dir() else [], [])

        # ...and the POSITIVE control, without which every check above also passes on a
        # scaffolder that refuses EVERY carry. This is the migration's real case: a v3 folder
        # already holds a hand-written CLAUDE.md, and the template is what destroys it.
        carry = tmp / "carry-claude"
        code, cout, _err = _st_run(["new", "--root", str(carry), "--name", "carry-claude",
                                    "--carried", "CLAUDE.md"])
        ck("--carried CLAUDE.md (a LEGAL carry) -> exit 0 (control for the --carried guards)", code, EXIT_OK)
        ck("the carried path was NOT written (control for the --carried guards)",
           _under(carry, "CLAUDE.md").exists(), False)
        ck("every OTHER hot file was still written (control for the --carried guards)",
           sorted(r for (r, _k, _l) in HOT_FILES if not _under(carry, r).is_file()),
           ["CLAUDE.md"])
        ck("both deferred archives were still written (control for the --carried guards)",
           sorted(r for r in DEFERRED_FILES if not _under(carry, r).is_file()), [])
        ck("the receipt announces the skip BY NAME (control for the --carried guards)",
           "NOT scaffolded (CARRIED by the caller): CLAUDE.md" in cout, True)
        ck("the receipt counts what this run WROTE (one fewer than HOT_FILES), not "
           "len(HOT_FILES) (control for the --carried guards)",
           ("\n%d hot file(s) (spec S3.1)" % (len(HOT_FILES) - 1)) in cout, True)
        cprobs, _cunrel, _ccl = verify_scaffold(carry, ("CLAUDE.md",))
        ck("verify_scaffold accepts a scaffold with a carried path missing (control for the --carried guards)", cprobs, [])

        # ...and verify_scaffold's own half of the same rule: a file AT a carried path means the
        # skip did not engage, which is the one failure a bare exit 0 would report as success.
        # ALSO A CONTROL for the two guards above: it gates `verify_scaffold`, not `--carried`,
        # so blanking either refusal leaves it green.
        _under(carry, "CLAUDE.md").write_bytes(b"# hand-written\n")
        cprobs, _cunrel, _ccl = verify_scaffold(carry, ("CLAUDE.md",))
        ck("verify_scaffold CATCHES a file sitting at a CARRIED path (control for the --carried guards)",
           [p for p in cprobs if "CARRIED" in p] != [], True)

        # ---- arm 17: machine_region_rels() reads the RENDERED body, never the raw template. ---
        # The markers reach the file through `{backlog_begin}` / `{backlog_ids_begin}`
        # substitutions, so the raw template strings contain NONE of them. MEASURED 2026-08-10:
        # 0 of 11 raw templates carry a marker; 2 do once rendered, two markers each. A check
        # keyed on the raw string therefore reports ZERO machine-managed files -- and arm 16's
        # guard, which is `carried & set(machine_region_rels())`, would then authorise a carry
        # over backlog.md. This arm pins the rendered-vs-raw distinction ITSELF, so a rewrite
        # that optimises `render` away goes red HERE and says why, not only in an exit code.
        mrr = machine_region_rels()
        ck("machine_region_rels() is exactly the two ordered-region files",
           list(mrr), ["backlog.md", "state.md"])
        ck("no DEFERRED archive is machine-managed",
           sorted(r for r in mrr if r in DEFERRED_FILES), [])
        probe_subs = substitutions("probe", datetime(2000, 1, 1))
        raw_keyed = tuple(rel for rel in [r for (r, _k, _l) in HOT_FILES] + list(DEFERRED_FILES)
                          if any(m in TEMPLATES[rel] for m in MACHINE_REGION_MARKERS))
        ck("a RAW-string-keyed computation reports ZERO machine-managed files",
           list(raw_keyed), [])
        ck("machine_region_rels() DISAGREES with that raw-keyed computation", mrr != raw_keyed,
           True)
        ck("each machine-managed file RENDERS both of its region markers",
           sorted((rel, len([m for m in MACHINE_REGION_MARKERS if m in render(rel, probe_subs)]))
                  for rel in mrr),
           [("backlog.md", 2), ("state.md", 2)])
        ck("the set does not depend on the probe substitutions (no marker is interpolated from "
           "`project` or a timestamp)",
           tuple(rel for rel in [r for (r, _k, _l) in HOT_FILES] + list(DEFERRED_FILES)
                 if any(m in render(rel, substitutions("other", datetime(2030, 12, 31)))
                        for m in MACHINE_REGION_MARKERS)),
           mrr)

        # ---- arm 12: 1 is RESERVED for --selftest; no ordinary invocation returned it. -------
        ck("no normal invocation returned EXIT_SELFTEST", sorted(set(_ST_CODES)),
           sorted(set(c for c in _ST_CODES if c != EXIT_SELFTEST)))
        ck("no normal invocation returned EXIT_INTERNAL", EXIT_INTERNAL in _ST_CODES, False)
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)

    bad = [c for c in checks if not c[3]]
    for label, got, want, ok in checks:
        if not ok:
            sys.stderr.write("FAIL %s: got %r want %r\n" % (label, got, want))
    sys.stderr.write("%d checks, %d failed\n" % (len(checks), len(bad)))
    return EXIT_SELFTEST if bad else EXIT_OK


# --------------------------------------------------------------------------- cli

def build_parser():
    p = argparse.ArgumentParser(
        prog="init_project.py",
        description="save v4 SCAFFOLDER. Creates the hot files of spec S3.1, "
                    "checkpoints/ at the top level, _archive/, and S3.2's two deferred "
                    "archives -- MINUS any path `--carried` names, which the caller supplies "
                     "itself, MINUS any existing AGENTS.md directory entry, which is preserved "
                     "opaquely, and MINUS an existing non-empty regular CLAUDE.md, which is ADOPTED. "
                    "Refuses a directory that already holds a file it writes.",
        epilog="exit: 0 scaffolded | 2 covers THREE DIFFERENT STATES and the first error line "
               "tells them apart. A REFUSAL opens `refusing to scaffold:` or is a usage "
               "message, and NOTHING was written. A WRITE FAILURE is the raw OS error and "
               "the folder may be PARTLY written -- inspect it; a re-run will refuse it as "
               "a collision. A POST-SCAFFOLD IDENTIFIER CLASH opens `the scaffold "
               "COMPLETED`: every write this run planned succeeded and a duplicate id needs "
               "renaming | 6 scan integrity, an open fence in a file read back | 7 internal "
               "error. 1 is --selftest's own failure code and nothing else returns it.")
    p.add_argument("verb", nargs="?", default=None,
                   help="`new` -- the only verb (W9). The v3 name `init` is refused.")
    p.add_argument("--root", metavar="DIR",
                   help="REQUIRED. The project folder to scaffold. Created if missing; refused "
                        "ONLY if it already holds a file this scaffolder writes -- not merely "
                        "because it is non-empty. The scope is DECLARED, never inferred "
                        "from cwd (D53).")
    p.add_argument("--name", metavar="SHORT", default=None,
                   help="Relay-safe short name for state.md's `project:` (the cross-session "
                        "relay alias). Defaults to the folder's basename.")
    p.add_argument("--carried", action="append", default=None, metavar="REL", dest="carried",
                   help="Skip a declared absent non-machine-managed scaffolded path that the caller "
                        "will supply afterwards. Repeatable. Existing paths, unknown names, state.md "
                        "and backlog.md refuse. This does not import or convert another format.")
    p.add_argument("--selftest", action="store_true",
                   help="Run the in-file fixture suite and exit. THE ONLY invocation that may "
                        "return 1. Fixtures live in a tempdir removed on the way out; no file "
                        "under --root is read or written.")
    return p


def main(argv=None, _out=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except (AttributeError, ValueError):
        pass
    out = _out if _out is not None else recall._PipeSafe(sys.stdout)
    args = build_parser().parse_args(argv)

    # Before the --root check: the suite builds and declares its own roots, so requiring one here
    # would make the tool untestable without pointing it at a real project.
    if args.selftest:
        return _selftest()

    if args.verb != "new":
        sys.stderr.write("error: the verb is `new` (W9)%s. Usage: init_project.py new --root "
                         "<dir> [--name <ShortName>]\n"
                         % ("" if args.verb is None else ", not %r" % args.verb))
        return EXIT_USAGE
    if not args.root:
        sys.stderr.write("error: --root is required: the scope is DECLARED, never inferred "
                         "from cwd (D53).\n")
        return EXIT_USAGE

    target = pathlib.Path(args.root)
    if not target.is_absolute():
        target = (pathlib.Path.cwd() / target)
    target = pathlib.Path(os.path.normpath(str(target)))
    project = args.name if args.name is not None else target.name
    carried = tuple(args.carried or ())

    try:
        validate_name(project)
        # The v3 helper's warning, kept: `project:` is the cross-session relay alias, so a
        # re-styled name clobbers the peer's chosen name on every restore. A deliberate
        # divergence is legal, so this is a warning and not a refusal.
        if project.lower() != target.name.lower():
            sys.stderr.write("warning: --name %r differs from the folder basename %r. state.md "
                             "`project:` is the cross-session relay alias and takes %r verbatim.\n"
                             % (project, target.name, project))
        dirs, written, adopted = scaffold(target, project, datetime.now(), carried)
    except (ValueError, IOError, OSError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return EXIT_USAGE

    problems, unreliable, clashes = verify_scaffold(target, carried, adopted)
    if clashes:
        # EXIT 2 HERE MEANS SOMETHING THE EPILOG'S OTHER EXIT-2 CASES DO NOT, AND SAYING SO IS
        # THE WHOLE OF THIS BRANCH. Every other 2 is a REFUSAL that wrote nothing; this one is
        # reached only after the scaffold COMPLETED. A caller that reads 2 as "the scaffold
        # failed" would report a failure over a finished folder and skip whatever it does next.
        # The folder is right, an identifier in it is not, and the operator renames a heading.
        # THE CLAIM IS ABOUT THE WRITES, NOT ABOUT COMPLETENESS. "Every template landed" is
        # false under `--carried`, where one deliberately did not -- the shape this project
        # keeps shipping, a universal written in the same breath as the flag that refutes it.
        sys.stderr.write("error: the scaffold COMPLETED -- every file this run was going to "
                         "write was written, and nothing below is a write failure. What "
                         "follows is %d identifier problem(s) in %s, NOT a failed scaffold: "
                         "exit 2 here means RENAME UNTIL ONE DEFINITION REMAINS, then carry on. Re-running "
                         "`new` will refuse this folder as a collision, which is correct and "
                         "is not the remedy.%s"
                         % (len(clashes), target, chr(10)))
        for _c in clashes:
            sys.stderr.write("error: " + _c + chr(10))
        return EXIT_USAGE
    if problems:
        # A failed postcondition on our OWN write is a broken tool, not a bad input: it means a
        # template edit unbalanced a fence or duplicated a delimiter. RuntimeError reaches cli()
        # and becomes exit 7, which is the code that says "the tool, not your folder".
        raise RuntimeError("scaffold postcondition failed: " + "; ".join(problems))

    out.write("scaffolded %s\n" % target)
    for name in dirs:
        out.write("  dir   %s\n" % name)
    for (rel, nbytes) in written:
        out.write("  file  %-36s %6d B\n" % (rel, nbytes))
    # THE COUNTS ARE OF WHAT THIS RUN WROTE, not of the constants. `len(HOT_FILES)` stood here and
    # was right only while nothing could be skipped: under `--carried` it would announce nine hot
    # files over a folder holding eight, which is the shape of receipt this project has had to
    # correct three times -- a number rendered from a plan rather than from the writes.
    hot_written = len([r for (r, _b) in written if r in set(x[0] for x in HOT_FILES)])
    deferred_written = len([r for (r, _b) in written if r in set(DEFERRED_FILES)])
    out.write("\n%d hot file(s) (spec S3.1), %d deferred archive(s) (spec S3.2), %d directories.\n"
              % (hot_written, deferred_written, len(dirs)))
    for rel in sorted(carried):
        out.write("  NOT scaffolded (CARRIED by the caller): %s -- no template was written "
                  "there.\n" % rel)
    for rel in sorted(adopted):
        out.write("  NOT scaffolded (ADOPTED, already present): %s -- THIS SCAFFOLDER WROTE "
                  "NOTHING THERE and appended nothing.\n" % rel)
    out.write("state.md: schema_version %s, save 0, phase discovery, row-2b baseline PRESENT "
              "and EMPTY (R-G).\n" % SCHEMA_VERSION)
    out.write("backlog.md: one ordered BACKLOG region, empty; step 6b writes it, rows 2/2b are "
              "carved out of it.\n")
    out.write("next: validate with `validate_entries.py --root %s`, then run the first save.\n"
              % target)
    return recall.integrity_code(unreliable, EXIT_OK)


def cli(argv=None):
    try:
        code = main(argv)
    except BrokenPipeError:
        code = EXIT_OK
    except SystemExit:
        raise
    except Exception as exc:                                    # noqa: BLE001 - deliberate
        sys.stderr.write("internal error: %s: %s\n" % (type(exc).__name__, exc))
        return EXIT_INTERNAL
    try:
        sys.stdout.flush()
    except (BrokenPipeError, ValueError, OSError, AttributeError):
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
    return code


if __name__ == "__main__":
    sys.exit(cli())
