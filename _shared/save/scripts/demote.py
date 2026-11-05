#!/usr/bin/env python3
"""demote.py -- step 6a of an save save: the deterministic drain of the hot set.

WHAT THIS IS. A MOVE ENGINE. It lifts a whole entry out of a hot file and appends it, byte for
byte and WITH ITS ID INTACT, to a named file under `_archive/`. Nothing here summarises, rewrites,
re-words or deletes. There is NO LLM anywhere on this path: every trigger is a MARKER an author
wrote, and every decision is a regex over that marker. `--dry-run` reports and writes nothing.

CONTRACT: this tool REFUSES a folder that is not already a migrated v4 project (exit 4), at
    entry, before the scan. The check and its wording live in `update_state.py` so all three v4
    writers refuse identically. There is no conversion or initialization bypass. Even
    --dry-run refuses a folder whose v4 declaration cannot be established.

CONTRACT: an eviction degrades to a RESOLVABLE STUB, never a deletion (C-4). The id is what makes
    it resolvable, so the id travels with the bytes and `recall get <id> --root <folder>`
    returns the entry FROM THE ARCHIVE afterwards. MEASURED 2026-08-03 on a folder scaffolded by
    the real `init_project.py`: a run moved `## A-7` out of `active.md` into
    `_archive/active-expired.md`, and a real `python -S -I recall.py get A-7 --root <folder>`
    subprocess then exited 0, reported `_archive/active-expired.md:8-11 (122 bytes)` on stderr,
    and printed those 122 bytes -- the entry, from the archive.

CONTRACT (R-H, example-project's ruling): a move that loses bytes IS a C-4 deletion, so every move is
    VERIFIED before the source is touched. The destination is written, re-read through
    `recall.load_doc` + `heading_map`, and the moved id's span there must BEGIN with the span
    re-read from the SOURCE on disk AND END there -- `_payload_ends_at`, not equality. The
    difference is load-bearing and this line used to claim equality: a span ends at the next
    heading of the same or higher level, so a LATER append at a deeper level is swallowed into the
    earlier entry's span and an equality test fails on an entry sitting there untouched. What
    `_payload_ends_at` will NOT accept is a destination span that carries MORE than the payload --
    trailing prose, or a heading nested deeper than the entry's own -- because that is content the
    source does not hold. A span that fails is dropped from the batch, the destination is rebuilt
    without it, the entry STAYS
    HOT, and the run exits 3 naming the id. MEASURED 2026-08-03 with the write path corrupted by
    one byte for one entry: exit 3, `MOVE FAILED M12` in the receipt, `FAILED (1)` block, M12's
    bytes still in `backlog.md` unchanged, `_archive/backlog-done.md` NOT created -- and the entries in
    the same run that DID verify still moved, so a failure is per-move, not per-save.

CONTRACT (W30): destination directories and files are CREATED IF MISSING. A fresh scaffold ships
    `_archive/` plus the two S3.2 deferred files and NOTHING ELSE, so six of the eight
    destinations do not exist at save#1. MEASURED 2026-08-03 on a real `init_project.py new`
    scaffold: `_archive/` held exactly `open-questions-deferred.md` and `backlog-deferred.md` before
    the run; `_archive/active-expired.md` and `_archive/backlog-done.md` were created by it. Without
    create-if-missing, row 5 would either crash on the SUCCESS path or drop a live gated item,
    which is a C-4 deletion.

CONTRACT (R-G, example-project's ruling): row 2b's ONLY prior-state source is the `backlog_entry_ids` list
    `state.md` carries between the `<!-- BACKLOG-ENTRY-IDS:BEGIN/END -->` delimiters. Not git (a
    project may be mid-rebase, and this must work on a throwaway folder with no repo); not the
    previous checkpoint (cold storage, which the drain must not read). ABSENT delimiters make row
    2b NO-OP with a named warning -- a missing baseline read as "everything disappeared" evicts
    the whole file, which is the F65 shape (a view that fails toward "nothing here") with a
    delete attached. PRESENT-and-EMPTY is a real baseline meaning "no plan entries last save".
    MEASURED 2026-08-03: with the region stripped from `state.md`, the item row 2b would otherwise
    have dropped stayed in `backlog.md`, `_archive/backlog-dropped.md` was never created, and the
    receipt carried `row 2b NO-OP (R-G)`. With the region PRESENT and EMPTY, the same file
    produced no such warning and still dropped nothing -- the two answers are distinguishable in
    the output, which is the point of keeping them distinct in the type.

INVARIANT: an unreliable scan may not license a deletion. Every file this tool reads or appends
    to is checked for an unterminated fence FIRST, and one open fence anywhere in that scope
    returns EXIT_SCAN_INTEGRITY (6) having performed NO move at all. Under an open fence every
    heading below it is invisible, so a span runs past its real end and would carry entries that
    are not the one being demoted. MEASURED 2026-08-03 on a real scaffold with one unterminated
    ``` fence in `backlog.md`: exit 6, `demote: 0 entries moved (SCAN INTEGRITY -- refused)`, and a
    SHA-256 of all 11 files identical before and after. The same refusal fires for an open fence
    in a DESTINATION (an appended entry would land inside it) and in `state.md` (a read path).

INVARIANT: NO MOVE SPAN MAY CROSS A REGION DELIMITER, AND NO CLAMP MAY TEAR AN ENTRY.
    `heading_map`'s span ends at the next heading of the same or higher level, and for the last
    item above `<!-- BACKLOG:BEGIN -->` that heading is the first item INSIDE the region -- so the
    unclamped span swallowed the delimiter and carried it into `_archive/backlog-deferred.md`.
    MEASURED 2026-08-03: `backlog.md` was left with a BEGIN and no partner, the NEXT run found no
    region, and the backlog item that the carve-out had protected on run 1 was dropped on run 2.
    The clamp that fixed it then introduced its own defect, also MEASURED: a gated item whose BODY
    QUOTED the region template unfenced was clamped MID-ENTRY -- half moved, half orphaned in
    `backlog.md` under no heading, addressable by no id and drainable by no row -- and R-H could not
    see it, because R-H compares the destination against the CLAMPED span and both sides agreed.
    `clamp_span` now clamps ONLY when the trim is provably lossless and the region is well-formed,
    and REFUSES the move otherwise. Refused entries are named in a REFUSED block of the receipt.

INVARIANT: THE BACKLOG REGION IS A THREE-VALUED ANSWER, and its two failure values DISARM the rows
    that depend on it. `None` for "absent" collapsed into `in_backlog = False`, which is the value
    that LICENSES rows 2 and 2b, so a deleted, unpaired, inverted or duplicated delimiter pair
    drained the whole of `backlog.md` one-way at exit 0 with nothing on stderr (MEASURED 2026-08-03,
    four shapes, by two independent adversaries). MALFORMED now disables rows 2 AND 2b for the
    file; ABSENT disables row 2b (no item can be inside a region that does not exist, so row 2's
    explicit `done:` is still safe). Both warn. This is R-G's reasoning applied to the OTHER half
    of row 2b's input, one function over from where R-G already guards it.

INVARIANT: ROW 2b DOES NOT EVICT A PRESENT ITEM unless the caller passes `--rewrote-complete`.
    spec S5.1 row 2b's trigger is a `backlog.md` item that SILENTLY DISAPPEARS. The predecessor read
    it as `key in baseline` over the items it could still SEE, so the predicate was "existed last
    save" -- true of every carried-over item INCLUDING one rewritten top to bottom this save.
    MEASURED 2026-08-03 by two independent adversaries: a `backlog.md` holding a rewritten M1 and an
    untouched M2 was emptied to nothing but its BACKLOG delimiters, one-way, at exit 0, with zero
    bytes on stderr; steady state kept only items NEW in the current save. An ids-only baseline
    cannot distinguish rewritten from untouched, so the default no longer guesses: the genuine
    disappearance is DETECTED and REPORTED (`pending_drop:` on the receipt, see `pending_drops`),
    and the eviction branch is available only to a caller that declares its `--rewrote` set
    exhaustive. Step 6b, which rewrites `backlog.md`, is the caller that can.

INVARIANT: rows 3 and 4 fire on a MARKER IN A STATUS POSITION, never on the phrase in prose. Row 2
    always had this discipline via `validate_entries.FIELD_RE`; rows 3 and 4 did not, and MEASURED
    2026-08-03 they archived, one-way and at exit 0: an open question headed `NOT RESOLVED`; a
    heading asking whether something is `RESOLVED yet?`; a LIVE architecture section whose body
    explained that something ELSE was `superseded by D9`; a heading reading `NOT superseded by D9`;
    and a future-tense sentence, filed with a successor id lifted from its contrasting clause. The
    first containment ("the marker must START a `-`-separated segment") was itself measured wrong
    by the orchestrator, because `(`, `[`, `]` and `;` were in the separator set: `## OQ9 · is it
    (RESOLVED) or still open`, `## OQ9 · blocked; RESOLVED in D4 is the precedent` and `## OQ9 ·
    what the [RESOLVED] tag should mean` all manufactured a "segment" whose first word was the
    marker, and all three were archived one-way at exit 0. Those four characters are now OUT of the
    set; row 3's marker must be the token IMMEDIATELY AFTER THE ID past at most one separator AND
    be followed by nothing, a separator or a date -- `## OQ9 · RESOLVED, or is it` is in position
    and is still a question -- and row 4's successor must be the very NEXT TOKEN with the clause
    ending there. ROW 4's BODY POSITION IS NOW A FIELD LINE (M9, ruled by the maintainer 2026-08-07 after the
    measurement): the marker must be written the way `kind:` and `gate:` are, at column 0 and
    outside every bullet, emphasis run, blockquote and fence. Measured over seven positions
    beforehand, a bullet, an indented sub-bullet and a `**bold**` run ALL archived the entry
    one-way -- and a bullet list is exactly how a person writes down what replaced what, so the
    commonest form of an anchored MENTION was firing the one-way move. Row 5 was measured the same
    way and needed nothing: `gate:` has always come through `validate_entries.FIELD_RE`, so of
    those seven positions only the field line ever fired. Everything that used to fire and no
    longer does produces a named warning and stays hot.

INVARIANT: `screen_batch` IS AN ORDERED DECISION SEQUENCE, NOT A SET OF GUARDS, because APPENDING
    and RECONCILING have OPPOSITE hazards. Appending creates a definition, so its hazard is a
    definition that already exists ANYWHERE in the archive set. Reconciling creates nothing and
    DELETES THE HOT COPY, so its hazard is whether the entry is still resolvable at exactly one
    site afterwards -- and the drain's SOURCES exclude `_archive/`, which makes the hot copy the
    last one this tool can read. The order is: admissible? -> is a copy already here? -> if so, is
    finishing safe (corpus-wide, and over every carried child)? -> otherwise, may this be appended?
    MEASURED 2026-08-07, five separately-correct guards inserted as independent tests produced five
    defects and every one was a PLACEMENT defect: the branch that deletes hot bytes skipped the
    cross-archive screen, and the append screens vetoed recoveries they did not apply to, stranding
    for ever a duplicate this tool had created. Pinned arm by arm in `_st_arms_sequence`.
    A HOLD REFUSES TO START A MOVE AND NEVER TO FINISH ONE, which is the same principle upstream:
    row 5's terminal hold now records the row it held, and `_reconcile_probe` admits such an entry
    to the plan -- marked `reconcile_only`, so it can reach `already` or `refused` and never
    `append` -- when a byte-identical copy is already sitting in the archive that row routes to.

INVARIANT: an id may be defined ONCE, and RECOGNISING a copy is a BYTE-EQUALITY question about ONE
    SPAN. `screen_batch` refuses an append that would give a destination a second definition of an
    id. MEASURED 2026-08-03: after an injected mid-run I/O failure (the documented recovery for
    which is to re-run), the re-run appended a SECOND copy and `recall get M12` then exited 4 --
    C-4 asks for a resolvable stub, and two copies resolve to neither. Recognising the first copy
    turns the re-run into the completion of the interrupted move: the source is spliced, nothing is
    appended, and the id resolves at exactly one site. The RECOGNITION ITSELF then shipped the
    worst defect in this file, MEASURED 2026-08-03 by the orchestrator: it asked `payload in
    existing` -- a SUBSTRING test against the destination's whole text -- so an archived `M4` with
    two paragraphs "recognised" a hot `M4` whose last paragraph the author had deleted that
    session, spliced the hot copy out, appended nothing, and printed a note asserting byte
    equality. `get M4` returned the deleted paragraph; the author's edit was reverted at exit 0.
    Recognition is now decided by `_payload_ends_at`: the payload must sit WHOLE at an entry start
    in the destination and END there -- what follows may be whitespace or the start of ANOTHER
    ENTRY, never more prose. That last clause is what tells "the archive carries a paragraph the
    author deleted" apart from "another entry was appended afterwards", and it replaced a SPAN
    comparison that a later append could silently invalidate (B4, MEASURED 2026-08-07: a deeper
    heading appended after an entry is swallowed by that entry's span, so the identless path
    duplicated and the identified path refused the recovery for ever). A destination that holds the
    id with DIFFERENT bytes is a CONFLICT (R-I): NOT moved, left HOT, and warned about by id, both
    paths and both byte counts. Appending
    would collide, overwriting would destroy an unrequested version, and splicing anyway is the
    measured deletion -- so the tool declines to guess.

INVARIANT: row 5 beats row 2b when an item matches both (plan P9 R3; INFERRED -- spec S5.1 states
    no tie-break). Deferring is REVERSIBLE and dropping is not, so the reversible branch wins.
    The same reasoning does NOT extend to rows 2/3, which require an EXPLICIT marker the author
    wrote: explicit evidence outranks a gate, and a gate outranks absence of evidence. Full order:
    1 > 3 > 4 > 2 > 5 > 2b. Rows 1/3/4 are file-exclusive, so the only live contests are
    2 > 5 > 2b inside `backlog.md` and 3 > 5 inside `open_questions.md`.

INVARIANT: a `gate:` is a CONTRACT, and when it is not one the entry STAYS HOT TERMINALLY (D81).
    Admissible in exactly two forms: ID-GATED, naming at least one id that resolves as a heading
    in `backlog.md`, `_archive/backlog-deferred.md` or `_archive/backlog-done.md`; or EVENT-GATED, opening
    with the anchored token `EXTERNAL - ` and naming an observable. MEASURED STARTING STATE:
    `gate: I hallucinated this` and `gate: waiting for the vibes to be right` BOTH DEFERRED
    SUCCESSFULLY -- the only refusal was `_is_placeholder`'s short list, so anything reading like
    a sentence got through, and D81 was ratified, narrated as enforced, and enforced nothing.
    THE HOLD IS TERMINAL: when this check (or the placeholder branch beside it) decides an entry
    stays, `decide` RETURNS and row 2b never sees it. A hold that merely `continue`d is how a
    DEFERRAL leaks into an ABANDONMENT -- two files that mean opposite things -- and that is a
    CATEGORY ERROR rather than a data-loss bug (the maintainer's ruling). Pinned by
    `_st_arms_gate_contract`, whose control was OBSERVED RED with the two `return`s replaced by
    `continue`: M82, M83 and M85 all landed in the ONE-WAY dropped archive.

INVARIANT: nothing reaches `_archive/backlog-dropped.md` by inference. Row 2b additionally REQUIRES
    a `wont-do:` DECLARATION a person wrote; the baseline / `--rewrote-complete` / no-`done:` /
    outside-the-region conditions are kept ON TOP of it, so the marker only ever NARROWS the one
    row that cannot be undone. "Not now" and "never" are different decisions and only a human
    makes the second. REPORTING and MOVING stay two mechanisms: `pending_drops` keeps its own
    subject -- ids in the baseline that no heading in `backlog.md` carries NOW -- and a PRESENT item
    is never its subject (F208).

INVARIANT: re-running is safe. Every trigger is evaluated against the file as it now sits, and a
    moved entry is no longer in the source, so the second run has nothing to match. MEASURED
    2026-08-03: run 1 moved 10 entries across five sources, run 2 printed `demote: 0 entries
    moved` and every file in the folder was byte-identical to the state run 1 left. Independently
    on a real scaffold: run 1 moved 3, run 2 moved 0, and `validate_entries.py --root <folder>`
    exited 0 over the result (6 entries, 0 aborts).

EXTERNAL: `citemap` and `recall` are imported, never re-implemented. The fence machine, the
    line splitter, the id families, the span rule and the root guard all come from them. A second
    copy of any of those drifts, and the drift is invisible until a fixture catches it. This file
    additionally CROSS-CHECKS its own heading walk against `recall.heading_map` on every
    id-bearing heading (see `_cross_check`) and exits 7 on disagreement, because a span this tool
    computes differently from the one `get` will later slice is exactly the silent-wrong-bytes
    case the C-4 contract above rules out.

SPEC GAP, handed back rather than invented (spec S5.1). Row 2b's line also requires that "the next
restore prints it and clears the pending-drop list in `state.md`". Nothing in P1 writes that field:
this tool does not rewrite `state.md` (that is step 6b's file, and two writers of one generated
cursor is a race), and no other P1 component claims it. The disappearance is therefore RECORDED on
the receipt as `pending_drop: <ids>` plus a warning, and the persistent list is left for whoever
owns 6b. The same gap covers `no_demote_streak` (spec S5.1's "five consecutive saves moving
nothing" alarm): `init_project.py` now scaffolds the field so the storage exists, but this tool
does not increment it.
D78 ratified ARMING that alarm; no code does, here or anywhere. Arming takes the seven code-side
changes F221 names, and one of them is `update_state.py`'s preserved-scalar list, which re-emits
the field unchanged every save - so an increment written here would be overwritten by the next
save. Authorized by D78, NOT BUILT; it behaves as P3 until those seven land.
"""

import os
import pathlib
import re
import sys

# --- sibling import. `-I` implies `-P`, so sys.path[0] is NOT the script's directory. -----------
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    import citemap
    import recall
    import validate_entries
    import update_state
except ImportError as _exc:                                     # pragma: no cover - fatal
    sys.stderr.write("error: cannot import sibling from %s: %s\n"
                     % (pathlib.Path(__file__).resolve().parent, _exc))
    sys.exit(2)


# --------------------------------------------------------------------------- exit codes
# Fixed across the family. Do not redefine one. 3 is this tool's own verdict, as EXIT_ABORT is
# `validate_entries`'.

EXIT_OK = 0                 # the drain ran; 0 or more entries moved
EXIT_SELFTEST = 1           # --selftest ONLY, and only on a failed assertion.
EXIT_USAGE = 2              # usage or I/O: missing/not-a-dir --root, unreadable file, bad path
EXIT_MOVE_FAILED = 3        # R-H: at least one move failed its byte-equality check
EXIT_SCAN_INTEGRITY = 6     # at least one file in scope ends with an OPEN fence. OUTRANKS 0 and 3
EXIT_INTERNAL = 7           # uncaught exception, or a span that disagrees with heading_map

# --root is not a migrated v4 project folder; NOTHING was moved. Aliased from update_state
# rather than restated, so the three v4 writers cannot drift to different exit codes for the
# same refusal. Before this gate existed the drain had NO version awareness at all: run against
# a real v3 folder, it planned a batch of entries and carried their bytes out of
# open_questions.md into an _archive/ file it created, at exit 0.
EXIT_NOT_MIGRATED = update_state.EXIT_NOT_MIGRATED


# --------------------------------------------------------------------------- the drain table
# spec S5.1 step 6a. Row 6 is NOT ARMED at v1 -- parked with spec S2.5 / R-DOC-1 -- and is
# deliberately absent rather than present-and-disabled, so nothing can switch it on by accident.

ARCHIVE_DIR = "_archive"

D_EXPIRED = "_archive/active-expired.md"            # row 1, ONE-WAY
D_DONE = "_archive/backlog-done.md"                    # row 2, ONE-WAY
D_DROPPED = "_archive/backlog-dropped.md"              # row 2b, ONE-WAY
D_RESOLVED = "_archive/resolved-oqs.md"             # row 3, ONE-WAY
D_ARCH_SUPERSEDED = "_archive/architecture-superseded.md"   # row 4, ONE-WAY
D_DESIGN_SUPERSEDED = "_archive/design-superseded.md"       # row 4, ONE-WAY
D_BACKLOG_DEFERRED = "_archive/backlog-deferred.md"       # row 5, REVERSIBLE (S3.2, gate: required)
D_OQ_DEFERRED = "_archive/open-questions-deferred.md"       # row 5, REVERSIBLE (S3.2)

# The hot files this tool reads. `AGENTS.md`, `CLAUDE.md`, `CONSTRAINTS.md` and `manifest.md` have NO drain row
# at all (spec S5.1 lists none), so they are not read: a file with no trigger cannot fire one, and
# opening it would only widen the scan-integrity scope for no gain. `state.md` is read separately
# for the R-G baseline and the current save#.
SOURCE_ROWS = {
    "active.md":         (1,),
    "backlog.md":           (2, "2b", 5),
    "open_questions.md": (3, 5),
    "architecture.md":   (4,),
    "design.md":         (4,),
}
SOURCES = ("active.md", "backlog.md", "open_questions.md", "architecture.md", "design.md")

ALL_DESTINATIONS = (D_EXPIRED, D_DONE, D_DROPPED, D_RESOLVED,
                    D_ARCH_SUPERSEDED, D_DESIGN_SUPERSEDED,
                    D_BACKLOG_DEFERRED, D_OQ_DEFERRED)

# The label that appears in the receipt beside each id. One word, so `<id> <label> -> <dest>` is
# greppable.
ROW_LABEL = {1: "expired", 2: "done", "2b": "dropped", 3: "resolved",
             4: "superseded", 5: "deferred"}

# PRECEDENCE. First match in this order wins. See the INVARIANT block in the module docstring for
# why 5 beats 2b and why 2 and 3 beat 5.
ROW_PRECEDENCE = (1, 3, 4, 2, 5, "2b")

STATE_FILE = "state.md"

# The R-G baseline region, written by `init_project.py` and by every save's step 6b. The
# delimiters are matched exactly; the payload is one `backlog_entry_ids: [...]` line.
BACKLOG_IDS_BEGIN_RE = re.compile(r"^\s*<!--\s*BACKLOG-ENTRY-IDS:BEGIN\s*-->\s*$", re.I)
BACKLOG_IDS_END_RE = re.compile(r"^\s*<!--\s*BACKLOG-ENTRY-IDS:END\s*-->\s*$", re.I)
BACKLOG_IDS_VALUE_RE = re.compile(r"^\s*backlog_entry_ids\s*:\s*\[(?P<body>.*)\]\s*$")

# The machine-managed backlog region inside `backlog.md`. Rows 2 and 2b are suppressed inside it.
BACKLOG_BEGIN_RE = re.compile(r"^\s*<!--\s*BACKLOG:BEGIN\s*-->\s*$", re.I)
BACKLOG_END_RE = re.compile(r"^\s*<!--\s*BACKLOG:END\s*-->\s*$", re.I)


# --------------------------------------------------------------------------- trigger markers
#
# Every one of these fires on something an AUTHOR WROTE. None of them infers.

# Row 2. A `done:` DECLARATION LINE, using the shared field shape so a bullet, a bold run and a
# URL cannot open one. Prose ("we are done with X") is not a field and does not fire; neither does
# `not-done:`, whose key is `not-done`. A PLACEHOLDER value (`done: none`, `done: TBD`) does not
# fire either -- row 2 is ONE-WAY, and "finished" asserted by a placeholder is exactly the false
# completion row 2b exists to avoid manufacturing.
DONE_KEYS = ("done", "completed")

# Rows 3 and 4 are MARKER rows, and a marker is something an author WROTE IN MARKER POSITION --
# not a word that appears somewhere in a sentence. Row 2 has always had this discipline (it needs
# `validate_entries.FIELD_RE`, so prose "we are done with X" cannot fire it); rows 3 and 4 did not,
# and an adversary measured what that costs. Every one of these fired at exit 0, ONE-WAY:
#
#   row 3  `## OQ8 - NOT RESOLVED: which retrieval shape wins`   -> archived as resolved
#   row 3  `## OQ5 - is the retrieval verdict RESOLVED yet?`     -> archived as resolved
#   row 3  `## OQ3 - ... a heading that carries the word RESOLVED?` -> archived as resolved
#   row 4  body: "the old cron drain was `superseded by D9`"     -> the LIVE section archived
#   row 4  head: `## D6 - a section that was NOT superseded by D9` -> archived
#   row 4  body: "will be superseded by whatever replaces it, unlike D9 which stands"
#                                        -> archived, with D9 recorded as a successor it never had
#
# The containment below is one idea applied twice: a marker must START a SEGMENT. A heading is cut
# on its separators and a body line on its leading list/emphasis run, and the marker must be the
# first thing in what remains. Every false positive above is a marker in the MIDDLE of a segment.
# Under-firing is the deliberate direction: a marker that does not fire warns and stays hot, which
# costs bytes; a marker that fires wrongly costs content, and rows 3 and 4 are both ONE-WAY.

# Heading separators, for ROW 4. A title is cut on these and each piece is tested independently,
# so `## D24 - the old surface - superseded by D9` fires and `## D24 - what a superseded by tag
# should mean` does not.
#
# `(`, `[`, `]`, `;`, `,` and `/` WERE in this set and are now deliberately OUT. None of them
# starts a new titled part of a heading: they open a PARENTHETICAL or continue a CLAUSE, and the
# "segment" each one manufactured began with a marker sitting in the MIDDLE of a sentence.
# MEASURED 2026-08-03 by the orchestrator, one-way and at exit 0 with nothing on stderr:
#   `## OQ9 · is it (RESOLVED) or still open`           -- `(` made `RESOLVED) or still open`
#   `## OQ9 · blocked; RESOLVED in D4 is the precedent` -- `;` made `RESOLVED in D4 ...`
#   `## OQ9 · what the [RESOLVED] tag should mean`      -- `[` made `RESOLVED] tag should mean`
# Every one of those is a LIVE open question archived as its own opposite.
#
# ...and a separator only separates when it SITS BETWEEN PARTS, not when it JOINS A WORD. The
# pattern was a bare `[-‐-―:·|]+`, so a hyphen glued between two letters cut a compound word in
# half and handed row 4 a "segment" that began with the marker. This is the SAME defect the row-3
# marker had (see `RESOLVED_MARKER_RE`); MEASURED here 2026-08-03, all three one-way at exit 0:
#   `## D41 · not-superseded by D9`                  -> ['D41', 'not', 'superseded by D9']
#   `## D40 · the surface-superseded by D9 question` -> [..., 'superseded by D9 question']
#   `## D42 · a pre-superseded by D9 clause`         -> [..., 'superseded by D9 clause']
# The first is `NOT superseded by D9` -- already pinned in its spaced form (D21) and still firing
# in its hyphenated one. So a separator run splits only when it is NOT glued between two
# alphanumerics: it must have a non-word character, or an edge, on at least one side. `## D24: x`
# and `## D24 - x` still split (the colon/dash faces a space); `2026-08-02` no longer does, which
# changes no verdict -- none of `2026`/`08`/`02` ever began with a marker.
TITLE_SPLIT_RE = re.compile(r"(?<![^\W_])[-‐-―:·|]+|[-‐-―:·|]+(?![^\W_])")

# A body supersession marker must be a field line: column 0 or at most three
# spaces of indentation, no bullet, emphasis or blockquote, and outside fences.
# This matches the entry grammar for kind/lifetime/gate. Mentions in prose or
# quoted/nested text cannot authorize archiving the containing entry.
#
# Stripping a list bullet or emphasis opener before matching would turn a
# mention of another entry's supersession into authority to move this entry.
#
# ROW 5 WAS MEASURED THE SAME WAY AND NEEDS NOTHING: `gate:` reaches `it.extra` only through
# `validate_entries.FIELD_RE`, so of those same seven positions ONLY the field line fired. Row 4
# is being brought to where row 5 already was, not given a new rule of its own.
#
# ONE MEASURED RESIDUAL, RECORDED RATHER THAN CLOSED. An entry a PRE-M9 drain had already archived
# on the strength of a bulleted marker is now left HOT and ARCHIVED for ever: row 4 no longer
# fires, so no row is HELD, so `_reconcile_probe` never looks at it. MEASURED 2026-08-07
# by a BEFORE/AFTER drain over one corpus in the interrupted state: D50 and D51 reconcile under
# the old rules and stay duplicated under these. THE POPULATION IS EMPTY IN PRACTICE -- nothing
# has been authored in the v4 entry format anywhere on the fleet, so no deployed drain has
# archived one -- and closing it is NOT a one-line change: a NEAR-MISS is not a HOLD, rows 1, 2
# and 3 have exactly the same shape, and making `_reconcile_probe` take "no row fired" as its
# subject is a different design (reconcile ANY hot entry byte-identical to an archived one) rather
# than a special case for row 4. That design has not been ruled on, so this is a note, not a patch.
SUPERSEDED_FIELD_RE = re.compile(r"^ {0,3}superseded\s+by\b(?P<tail>.*)$", re.I)

# Row 3. The marker is uppercase and read from the HEADING ONLY (spec S5.1: "OQ heading carries
# RESOLVED"), and it must sit in STATUS POSITION -- the token right after the id, past AT MOST ONE
# separator. That is the form this project's own corpus uses: all fourteen resolved headings in
# `open_questions.md` read `## OQ<n> RESOLVED <date> - <title>`. Status position is the only place
# in a heading where a word is a DECLARATION ABOUT THE ENTRY rather than a word in its title, and
# row 3 is ONE-WAY.
#
# `\b` before `RESOLVED` means the token inside `UNRESOLVED` does not match, which matters:
# `unresolved` is a KIND in this schema (validate_entries.KINDS) and an entry declaring it must not
# be archived as its own opposite. `## OQ11 PARTIALLY RESOLVED 2026-08-01 - ...` does not match
# either -- the token after the id is `PARTIALLY` -- and that is correct twice over: the corpus
# carries two of them, and partially resolved is not resolved.
RESOLVED_RE = re.compile(r"\bRESOLVED\b")

# The separator that may sit between the id and its status marker. AT MOST ONE of them.
STATUS_SEP_RE = re.compile(r"^[ \t]*[-‐-―:·|][ \t]*")

# ...and the marker must be STANDALONE: what follows it is nothing, or a SPACE and then a
# separator or a stamp. A comma or a lower-case word after it means the heading is still speaking.
# MEASURED 2026-08-03: `## OQ9 · RESOLVED, or is it` is IN status position and was archived one-way
# at exit 0, so position alone is not enough.
#
# THE SPACE IS THE WHOLE RULE, and its absence was the next defect. This pattern read
# `[ \t]*(?:$|[-‐-―:·|]|…)`, and `[ \t]*` permits ZERO whitespace while `-` is a separator -- so a
# hyphen INSIDE A COMPOUND WORD satisfied the branch that exists to prove the token STANDS ALONE.
# REPRODUCED by the orchestrator 2026-08-03, one-way, exit 0, nothing on stderr:
#   `## OQ9 · RESOLVED-pending-review - still open, do not archive` -> _archive/resolved-oqs.md
# `RESOLVED-pending-review` is one word; the marker is a PREFIX of it, not a status stamp. So the
# token must end at a REAL word boundary -- whitespace, or end of line -- before anything else is
# read as a stamp. The deliberate cost: `RESOLVED: we ship X`, with the colon glued on, no longer
# fires. It WARNS instead (the near-miss below names the required form) and stays hot, which is the
# recoverable direction; none of this project's twelve resolved headings uses that form -- all
# twelve read `## OQ<n> RESOLVED <date> - <title>`, space-separated, and all twelve still fire.
RESOLVED_MARKER_RE = re.compile(
    r"^RESOLVED(?:[ \t]*$|[ \t]+(?:[-‐-―:·|]|\d{4}-\d{2}-\d{2}\b|save#\d+\b))")

# Row 4. `superseded by <id>`, ANCHORED at the start of a segment, and `<id>` must be the FIRST
# TOKEN of the tail and must resolve to an id family. "superseded by a later decision" names no
# successor, so the entry it points at cannot be found and the move would strand the reader; that
# case WARNS and stays hot -- as does "superseded by whatever replaces it, unlike D9", where the
# old unanchored tail scan reached past the real object of the sentence to find `D9`.
SUPERSEDED_RE = re.compile(r"\bsuperseded[\s_-]+by\b", re.I)
SUPERSEDED_MARKER_RE = re.compile(r"^superseded\s+by\b(?P<tail>.*)$", re.I)

# Row 5. A gate is UNSATISFIED unless the author says otherwise IN THE SAME PLACE -- either a
# `gate:` value opening with the uppercase token SATISFIED, or a separate `gate-satisfied:` field
# with a non-placeholder value. There is no inference: deciding whether "channel B opens" has
# happened is not a regex's job, and a rule that guesses would move live work.
GATE_SATISFIED_RE = re.compile(r"^\s*SATISFIED\b")
GATE_SATISFIED_KEYS = ("gate-satisfied", "gate_satisfied")

# Row 5, D81. A `gate:` is a CONTRACT, not a sentence, and only a contract defers an entry.
# EVENT-GATED is the second of the two admissible forms: the value opens with the anchored
# uppercase token `EXTERNAL - ` and names an observable after it. The token is what makes the
# form machine-recognisable -- without it the same sentence is just prose -- and the trailing
# `\S` is what stops a bare `EXTERNAL -` from declaring nothing at all. Uppercase and anchored
# for the same reason `SATISFIED` is: a value a scan could find ANYWHERE in prose is a value
# prose can fire by accident.
GATE_EXTERNAL_RE = re.compile(r"^\s*EXTERNAL\s+-\s+\S")

# ID-GATED is the first form: the value names at least one id that RESOLVES AS A HEADING in one
# of these files plus `backlog.md` (D81's list, verbatim). Not "looks like an id" -- `_successor_in`
# already showed what a syntactic id test costs -- and not the whole corpus either: an entry
# gated on something that is neither planned, parked nor finished names nothing that can bring
# it back. `backlog.md` is supplied as the already-scanned item list rather than re-read.
GATE_RESOLVE_ARCHIVES = (D_BACKLOG_DEFERRED, D_DONE)

# D97. The id families that are OPEN QUESTIONS -- `OQ` and `OQ-V2`, both of which begin with
# this prefix in `citemap.FAMILY_PATTERNS`. A waiting line may NOT name one, and that is a DESIGN
# decision rather than a consequence of D81's file list: we model DEFERRAL, not DEPENDENCY. The
# prefix exists only so the refusal can SAY that, because the generic "resolves in none of ..."
# wording reads as a lookup miss and invites the next reader to widen the search -- which is the
# one lesson this refusal must not teach. It changes NOTHING about what defers.
QUESTION_FAMILY_PREFIX = "OQ"

# Row 2b. The ABANDONMENT DECLARATION -- "we will NOT do this" -- written by a PERSON.
# `_archive/backlog-dropped.md` is ONE-WAY and it means *never*, which is a different decision from
# *not now* and one only a human makes (the maintainer's ruling, design clarification section 5). Before
# this key existed the only route into that file was INFERENCE: present last save, not rewritten
# this save, no completion marker. That is abandonment deduced from an omission, and it is a
# CATEGORY ERROR rather than merely a data-loss risk. The marker is now REQUIRED and the
# inference conditions are kept ON TOP of it, so the declaration narrows row 2b and never widens
# it -- D74's bias toward not moving applied to the one row that cannot be undone.
DROP_KEYS = ("wont-do", "wont_do", "will-not-do", "will_not_do")

# Keys this tool reads that `validate_entries.FIELD_KEYS` does not carry. They are collected by a
# second, explicit pass rather than by widening the sibling's closed set, which is its own
# contract and not this tool's to edit.
EXTRA_FIELD_KEYS = (frozenset(DONE_KEYS) | frozenset(GATE_SATISFIED_KEYS)
                    | frozenset(DROP_KEYS))


# --------------------------------------------------------------------------- the item model

class Item(object):
    """One demotable heading: its id, its exact byte span, and the fields it declares.

    TWO DIFFERENT SPANS, deliberately, and conflating them is the bug this class exists to stop.

    `byte_start`/`byte_end` -- the MOVE span, ending at the next heading of the SAME OR HIGHER
    level. That is `recall.heading_map`'s rule, so the bytes moved are exactly the bytes
    `recall get <id>` will slice out of the destination afterwards, and a `###` sub-heading
    travels with the `##` entry it belongs to instead of being orphaned in the hot file.

    `body_end_line` -- the TRIGGER span, ending at the next heading of ANY level. That is
    `validate_entries.parse_entries`' rule, and it is the one the fields are read from. Reading
    triggers over the move span would let ONE `gate:` under a sub-heading defer the whole parent
    -- the same absorption defect that let one `gate:` satisfy four deferred entries in the
    validator (its `parse_entries` docstring, 2026-08-03).

    `ident` is the citemap id or None. `label` is always printable: the id when there is one, the
    `validate_entries.heading_id` fallback otherwise, marked `~` in the receipt.
    """

    __slots__ = ("rel", "ident", "label", "title", "level", "line", "body_end_line",
                 "byte_start", "byte_start_raw", "byte_end", "byte_end_raw", "entry", "extra",
                 "in_backlog", "region_state", "refusal", "row", "why", "carried",
                 "held_row", "reconcile_only")

    def __init__(self, rel, ident, label, title, level, line):
        self.rel = rel
        self.ident = ident
        self.label = label
        self.title = title
        self.level = level
        self.line = line
        self.body_end_line = line
        self.byte_start = 0
        self.byte_start_raw = 0  # heading_map's start, BEFORE the BOM adjustment
        self.byte_end = 0       # the MOVE span end, CLAMPED at a region delimiter
        self.byte_end_raw = 0   # heading_map's unclamped end, kept only for the cross-check
        self.entry = None       # a validate_entries.Entry, for its `fields` and its helpers
        self.extra = {}         # {key: [(lineno, value)]} for EXTRA_FIELD_KEYS
        self.in_backlog = False
        self.region_state = BACKLOG_ABSENT
        self.refusal = ""       # non-empty -> this entry may not move, whatever row fired
        self.row = None         # the row that fired, once decided
        self.why = ""           # the human sentence for the report
        # F3: the nested headings DROPPED in favour of this one, whose bytes nonetheless travel
        # inside its move span. [(ident, label, level, line)]. They are NOT items -- no row, no
        # move of their own -- and nothing here promotes them to items. See `scan_source`.
        self.carried = []
        # STEP 1 of `screen_batch`'s decision sequence, recorded here because `decide` is where the
        # hold happens and `screen_batch` is where it has to be honoured.
        #   `held_row`        -- the row that FIRED and was then HELD (row 5's gate contract, D81).
        #                        `row` stays None, so nothing about the receipt or the plan changes
        #                        for an entry that has no interrupted move waiting for it.
        #   `reconcile_only`  -- set by `run` when a byte-identical copy of a HELD entry is already
        #                        sitting at the destination its row routes to. It means: this entry
        #                        may FINISH a move and may never START one.
        self.held_row = None
        self.reconcile_only = False

    @property
    def nbytes(self):
        return self.byte_end - self.byte_start

    def extra_first(self, *keys):
        for key in keys:
            vals = self.extra.get(key)
            if vals:
                return vals[0][1]
        return None

    def __repr__(self):                                         # pragma: no cover - debug only
        return "<Item %s %s:%d>" % (self.label, self.rel, self.line)


def _headings(rows):
    """[(lineno, level, rest, byte_start)] for every column-0 ATX heading outside a fence.

    `citemap.HEADING_RE` is the shared matcher and `rows` already carries `in_fence` from
    `recall.load_doc`'s fence machine. Nothing is decided here that either sibling decides.
    """
    out = []
    for (lineno, text, s, _e, in_fence) in rows:
        if in_fence:
            continue
        m = citemap.HEADING_RE.match(text)
        if m:
            out.append((lineno, len(m.group(1)), m.group(3), s))
    return out


# The three answers `analyse_backlog` can give. They are kept DISTINCT for the same reason R-G
# keeps ABSENT and EMPTY distinct in `read_baseline`: the carve-out's input going missing must not
# read as "no item is in the region", which is the value that licenses eviction.
BACKLOG_OK = "ok"
BACKLOG_ABSENT = "absent"
BACKLOG_MALFORMED = "malformed"


class Region(object):
    """Where `backlog.md`'s machine-managed BACKLOG region is -- and whether it can be trusted.

    THE DEFECT THIS CLASS EXISTS TO CLOSE (MEASURED 2026-08-03 by an adversary, four shapes).
    The predecessor returned `(first, last)` or `None`, and `None` propagated into
    `it.in_backlog = False` for EVERY item. The carve-out was a bare `if it.in_backlog: continue`,
    so "there is no region" and "this item is not in the region" were the SAME VALUE -- and the
    second one licenses rows 2 and 2b. Measured consequences, each at exit 0 with zero stderr:

      * delimiters deleted           -> every plan item dropped to `_archive/backlog-dropped.md`
      * `END` above `BEGIN`          -> same, and a delimiter-COUNT check passes (1 and 1)
      * `BEGIN` with no `END`        -> a backlog item carrying `done:` archived by row 2
      * two well-formed pairs        -> region two unprotected, its items dropped

    That is the F65 shape (a view that fails toward "nothing here") with a ONE-WAY delete attached,
    one function over from where ruling R-G guards the same row's OTHER input. So the region is now
    a three-valued answer, and the two failure values DISARM the rows that depend on it:

      OK        exactly one BEGIN, exactly one END, in that order, outside any fence.
      ABSENT    no delimiter of either kind. No item can be inside a region that does not exist,
                so row 2 (an EXPLICIT `done:`) is still safe -- but row 2b, whose whole subject is
                items the region protects, NO-OPs and says so.
      MALFORMED anything else. Rows 2 AND 2b are disabled for the file, with a warning naming the
                shape. spec S7.2 R1's rule for a malformed region is "leave the file
                byte-identical, warn, and skip", and this is that rule applied to the drain.
    """

    __slots__ = ("state", "why", "first_line", "last_line", "byte_start", "byte_end", "markers")

    def __init__(self, state, why, first_line=None, last_line=None,
                 byte_start=None, byte_end=None, markers=()):
        self.state = state
        self.why = why
        self.first_line = first_line     # first INTERIOR line (the delimiters are excluded)
        self.last_line = last_line       # last INTERIOR line
        self.byte_start = byte_start     # first byte of the BEGIN line
        self.byte_end = byte_end         # first byte AFTER the END line
        self.markers = tuple(markers)    # byte offset of EVERY delimiter line, well-formed or not

    def holds(self, lineno):
        """True only when the region is trustworthy AND this heading sits strictly inside it."""
        return (self.state == BACKLOG_OK
                and self.first_line <= lineno <= self.last_line)


def analyse_backlog(rows, data_len):
    """The BACKLOG region as a three-valued answer. See `Region` for why it is not a `None`.

    Fence state is `recall.load_doc`'s, so a delimiter QUOTED INSIDE A FENCE is not a
    delimiter -- which is how a doc may write the markers as an example without disarming its own
    carve-out. An UNFENCED quote does disarm it, loudly: that is the MALFORMED branch, and warning
    while moving nothing is the correct answer to "I cannot tell which of these is the region".
    """
    begins, ends, markers = [], [], []
    end_idx = None
    for idx, (lineno, text, s, _e, in_fence) in enumerate(rows):
        if in_fence:
            continue
        if BACKLOG_BEGIN_RE.match(text):
            begins.append((lineno, s))
            markers.append(s)
        elif BACKLOG_END_RE.match(text):
            ends.append((lineno, s))
            markers.append(s)
            if end_idx is None:
                end_idx = idx
    markers.sort()

    if not begins and not ends:
        return Region(BACKLOG_ABSENT,
                      "`backlog.md` carries no `<!-- BACKLOG:BEGIN -->` / `<!-- BACKLOG:END -->` "
                      "pair, so the rows-2b carve-out has no input to read")
    if len(begins) != 1 or len(ends) != 1:
        return Region(BACKLOG_MALFORMED,
                      "`backlog.md` holds %d BACKLOG:BEGIN and %d BACKLOG:END marker(s); exactly one "
                      "ordered pair is the only shape whose interior is decidable"
                      % (len(begins), len(ends)),
                      markers=markers)
    if begins[0][0] >= ends[0][0]:
        return Region(BACKLOG_MALFORMED,
                      "`backlog.md`'s BACKLOG markers are out of order: BEGIN on line %d, END on "
                      "line %d" % (begins[0][0], ends[0][0]),
                      markers=markers)

    # first byte AFTER the END line, taken from the NEXT row's start rather than from this row's
    # `byte_end`, so nothing here depends on whether that field includes the terminator.
    after = rows[end_idx + 1][2] if end_idx + 1 < len(rows) else data_len
    return Region(BACKLOG_OK, "one ordered BACKLOG pair",
                  first_line=begins[0][0] + 1, last_line=ends[0][0] - 1,
                  byte_start=begins[0][1], byte_end=after, markers=markers)


def clamp_span(data, region, bstart, bend):
    """(clamped_end, refusal). NO MOVE SPAN MAY CROSS A BACKLOG DELIMITER -- and none may TEAR.

    SCOPE FIRST, because this function guards LESS than the module INVARIANT at the top of this
    file claims. That invariant still reads "NO MOVE SPAN MAY CROSS A REGION DELIMITER"; this
    headline says BACKLOG, and BACKLOG is the accurate word for what is actually enforced. `region.markers` is `analyse_backlog`'s, and that function
    appends ONLY under BACKLOG_BEGIN_RE / BACKLOG_END_RE, so `active.md`'s `<!-- UNRESOLVED -->` /
    `<!-- /UNRESOLVED -->` pair is in no marker set at all: it is neither clamped nor refused, and
    a span reaching past one carries it into `_archive/active-expired.md`. MEASURED 2026-08-06 on
    scaffolds that validated clean (exit 0) before the drain.

    WHAT 7d DOES NEXT RIDES ON TWO INDEPENDENT AXES, and WHICH marker left is on NEITHER of them.

      * THE EXIT CODE follows one question only: does a `kind: unresolved` entry SURVIVE in
        `active.md`? An unclosed region is ZERO WIDTH and a lone closer opens none, so a survivor
        has lost its carve-out either way and ABORTS on A1 at exit 3 -- whichever marker went, and
        equally when BOTH went. With no unresolved survivor, losing the region costs no abort and
        nothing here moves the exit code off 0.
      * THE WARNING follows how many markers REMAIN. One remaining marker is named on its own line
        (`never closed` for an orphaned opener, `closes nothing` for an orphaned closer). ZERO
        remaining markers produce NO region warning at all: `find_unresolved_regions` has no
        unmatched marker to report, and nothing in the validator looks for a region that is
        MISSING.

    WHICH MARKERS TRAVEL is decided by which entry drains, and by that alone.

      * THE CLOSER goes when the expiring entry is the LAST one inside the region. Its span ends
        at the next same-or-higher heading, and the closer sits before that heading -- a live
        `## A-2` BELOW the region did not save it -- or else the span runs to EOF. Every
        `active.md` entry carries a bounded expiry, so the region emptying is its DESIGNED end
        state, not an edge case.
      * THE OPENER goes when the entry immediately ABOVE the region drains: the next same-or-
        higher heading is then the first entry INSIDE the region, which is the (a) SWALLOW below,
        one region over.
      * BOTH go in two shapes, and then `active.md` keeps no region at all. ONE span carries the
        pair when the region is already EMPTY and the entry above it drains: the next same-or-
        higher heading is then past the closer. TWO spans do it when the entry above and the
        region's LAST entry expire on the same save. A `kind: unresolved` entry surviving between
        them is possible and changes nothing here -- it aborts on A1 with no marker line to name.
      * CONTROL THAT COULD HAVE FAILED: a live entry between the draining one and the closer keeps
        the closer and the region intact -- but that is the next heading ending the span, not a
        clamp.

    Documented, not guarded. Recovery is re-adding marker lines, and both its cost and whether
    anything PROMPTS for it track the axes above: ONE line when one marker left, and 7d names that
    line on the SAME save; TWO lines when both left, and nothing prompts for either -- 7d is silent
    at exit 0 when no unresolved entry survived, and when one did it aborts on A1 naming the ENTRY,
    never the region that used to exempt it. Adding the UNRESOLVED pair to `region.markers` (which
    also means carrying markers through the ABSENT return, where they are currently dropped) would
    REFUSE the move rather than clamp it -- `active.md`'s region state is `absent` (measured) and
    the branch below refuses every crossing that is not BACKLOG_OK -- parking the expired entry
    hot for good and making the region's designed end state unreachable.

    Two defects meet here, and fixing either one alone reintroduces the other.

    (a) THE SWALLOW. `heading_map`'s span ends at the next heading of the same or higher level,
        and for the last item ABOVE `<!-- BACKLOG:BEGIN -->` that heading is the first item INSIDE
        the region -- so the unclamped span carried the delimiter into the archive, `backlog.md` was
        left with a BEGIN and no partner, and on the NEXT save every backlog item lost its
        carve-out. Clamping at the delimiter fixes it.

    (b) THE TEAR, MEASURED 2026-08-03 by an adversary on the clamp introduced for (a). A gated
        plan item whose BODY QUOTED the region template unfenced was clamped mid-entry: the
        heading and the first sentence moved to `_archive/backlog-deferred.md`, and the rest was left
        in `backlog.md` under NO heading -- addressable by no id and drainable by no row. R-H did not
        see it, because R-H compares the destination against the CLAMPED source span and both
        sides agreed. A clamp that silently shortens an entry is a C-4 deletion wearing a move's
        receipt.

    So the clamp is applied ONLY when it is provably LOSSLESS: everything between the clamp point
    and the unclamped end must lie inside the well-formed region, or be whitespace. Anything else
    -- and every crossing at all when the region is not well-formed -- REFUSES the move and leaves
    the entry hot. An entry wrongly left hot costs bytes; an entry wrongly torn costs content.
    """
    crossed = [b for b in region.markers if bstart < b < bend]
    if not crossed:
        return bend, ""
    first = crossed[0]
    if region.state != BACKLOG_OK:
        return bend, ("its move span would carry a BACKLOG delimiter and the region is %s (%s), "
                      "so the span cannot be trimmed to a provably lossless end"
                      % (region.state, region.why))
    outside = data[first:region.byte_start] + data[region.byte_end:bend]
    if outside.strip():
        return bend, ("its move span crosses the BACKLOG region AND continues past it with %d "
                      "byte(s) of content; trimming would TEAR the entry, leaving the remainder "
                      "in the hot file under no heading" % len(outside.strip()))
    return first, ""


def _cross_check(rel, items, hmap):
    """Every id-bearing item's span must equal `recall.heading_map`'s span for the same id.

    This tool walks headings itself because `heading_map` DROPS id-less ones (its `if h["id"] is
    None: continue`) and an id-less heading is still an entry that must drain. Two walks over one
    document is the drift shape the digest forbids, so the walks are RECONCILED on every run
    rather than trusted: `get` slices with heading_map's offsets, and a span this tool computes
    differently is the silent-wrong-bytes case. Disagreement is a bug in this file, not in the
    operator's folder, so it raises and becomes exit 7.
    """
    by_key = {}
    for e in hmap:
        by_key[(e["id"], e["start_line"])] = (e["byte_start"], e["byte_end"])
    for it in items:
        if it.ident is None:
            continue
        got = by_key.get((it.ident, it.line))
        if got is None:
            raise RuntimeError(
                "span drift in %s:%d -- this walk found id %r, recall.heading_map did not. "
                "The two walks must agree; `get` slices with heading_map's offsets."
                % (rel, it.line, it.ident))
        if got != (it.byte_start_raw, it.byte_end_raw):
            raise RuntimeError(
                "span drift in %s:%d for %s -- this walk says [%d,%d), "
                "recall.heading_map says [%d,%d)."
                % (rel, it.line, it.ident, it.byte_start_raw, it.byte_end_raw, got[0], got[1]))


def scan_source(rel, path, families):
    """([Item], [carried], open_fence_line, data, Region). One hot file's headings.

    Level-1 headings are the document title and never demotable (the same rule
    `validate_entries.parse_entries` uses). A heading NESTED inside an already-selected span is
    dropped: its bytes are inside its ancestor's move span, so selecting both would move the same
    bytes twice and excise an overlapping range from the source. Outer wins, in document order.

    F3, MEASURED 2026-08-07. THE DROP IS RIGHT AND STAYS. What was wrong is that the dropped
    heading was DISCARDED -- no id, no fields, no row, no receipt line, absent from `kept` and so
    absent from `_cross_check` too -- while its BYTES still travelled inside the ancestor's move
    span, which is contiguous and cannot exclude them. The end state when the child's id was
    already archived: TWO DEFINITIONS OF ONE ID INSIDE A SINGLE
    ARCHIVE FILE, `get` exit 4, `check` COLLIDING, receipt silent.

    (A per-row byte tally stood here -- "194 / 153 / 161 / 238 B" against rows 5, 2, 3 and 4. No
    fixture in this repository produces those figures and `grep` returned the claim and nothing
    else, so they are DELETED rather than re-derived: C-30, and a corrected number would be the
    same defect with fresher digits. The structural claim above needs no tally to stand.)

    So the dropped headings are now COLLECTED -- id, label, level, line, nothing else -- both as
    the second element of this tuple and, keyed to the ancestor that carries them, on that item's
    `carried`. THREE things consume them, and the count is stated because it was wrong here: the
    SOURCES receipt bracket (`render`, via `_carried_labels`); the destination screen that REFUSES
    AS A UNIT a parent whose carried child's id would collide (`screen_batch` 4d); and
    `_reconcile_unsafe`, which will not complete an interrupted move while a carried child's id
    resolves anywhere but inside the parent's own span at the destination. The docstring used to
    say "EXACTLY TWO THINGS and no third" and omit the last one -- which is the guard that an
    emptied `it.carried` silently disables, so the false enumeration was concealing the failure
    path of the very records it was describing. The per-item view is what those three consume; the
    flat list is this function's stated contract and is asserted directly
    in `_st_arms_carried_children`.

    WHAT DID NOT CHANGE, and the rejected alternative is the reason to say so: which headings
    become items, every move span, and every field/trigger window. PROMOTING level-3 children to
    items would move MORE entries -- each becomes independently demotable -- break the parent's
    move-span arithmetic, and still leave the other level-3 predicates in this tree disagreeing.
    D74 refuses that trade.

    WHAT THIS DOES NOT CLOSE. The screen is an ID screen. A carried child with no id colliding
    anywhere still rides along, and that is intended -- its bytes belong to the ancestor. But a
    carried child is a FULL ENTRY to `validate_entries` once it lands, so an ungated child landing
    in a `GATE_REQUIRED` archive still aborts the next save at A7/exit 3. MEASURED 2026-08-07,
    identical before and after this change. The records here deliberately hold no fields, so the
    screen has no `gate:` value to test; see `gate_admissible`'s last paragraph.
    """
    data, rows, open_line = recall.load_doc(path)
    heads = _headings(rows)
    last_line = rows[-1][0] if rows else 0
    region = analyse_backlog(rows, len(data))

    # A UTF-8 BOM belongs to the FILE, not to the first entry. `recall.load_doc` strips it from
    # line 1's TEXT and leaves the byte offsets indexing the file as it sits, so a heading on line
    # 1 of a BOM'd file starts at byte 0 -- and a move span taken from there carries the BOM into
    # the middle of a destination, where nothing strips it and `HEADING_RE` no longer matches.
    # MEASURED 2026-08-03 by an adversary: R-H caught it correctly (no bytes lost) but the run
    # wedged at exit 3 FOREVER, re-failing every save with a message that never named the BOM.
    bom = len(citemap.BOM.encode("utf-8")) if data.startswith(b"\xef\xbb\xbf") else 0

    items = []
    for i, (lineno, level, rest, bstart) in enumerate(heads):
        if level < 2:
            continue
        # MOVE span: to the next heading of the same or higher level, else EOF.
        bend = len(data)
        for j in range(i + 1, len(heads)):
            if heads[j][1] <= level:
                bend = heads[j][3]
                break
        clamped, refusal = clamp_span(data, region, bstart, bend)
        # TRIGGER span: to the line before the next heading of ANY level, else the last line.
        body_end = (heads[i + 1][0] - 1) if i + 1 < len(heads) else last_line
        defn = citemap.find_definition("#" * level + " " + rest, families)
        ident = defn[0] if defn else None
        label = ident if ident else validate_entries.heading_id(rest)
        it = Item(rel, ident, label, validate_entries._strip_emphasis(rest), level, lineno)
        it.byte_start_raw = bstart
        it.byte_start = max(bstart, bom) if bom else bstart
        it.byte_end = clamped
        it.byte_end_raw = bend
        it.refusal = refusal
        it.body_end_line = max(body_end, lineno)
        it.in_backlog = region.holds(lineno)
        it.region_state = region.state
        items.append(it)

    # drop nested items (their bytes already travel inside an ancestor's move span) -- but
    # RECORD them against the ancestor whose span carries them. F3.
    kept = []
    carried = []
    for it in items:
        if kept and it.byte_start < kept[-1].byte_end:
            rec = (it.ident, it.label, it.level, it.line)
            kept[-1].carried.append(rec)
            carried.append(rec)
            continue
        kept.append(it)

    _cross_check(rel, kept, recall.heading_map(rel, data, rows, families))

    # fields: the sibling's closed set through its own attacher, plus this tool's extra keys.
    entries = []
    for it in kept:
        ent = validate_entries.Entry(rel, it.label, it.title, it.level, it.line)
        ent.end_line = it.body_end_line
        it.entry = ent
        entries.append(ent)
    validate_entries.attach_fields(entries, rows)

    by_line = {}
    for it in kept:
        for ln in range(it.line + 1, it.body_end_line + 1):
            by_line[ln] = it
    for (lineno, text, _s, _e, in_fence) in rows:
        if in_fence:
            continue
        it = by_line.get(lineno)
        if it is None:
            continue
        m = validate_entries.FIELD_RE.match(text)
        if not m:
            continue
        key = m.group(1).lower()
        if key in EXTRA_FIELD_KEYS:
            it.extra.setdefault(key, []).append((lineno, m.group(2).strip()))

    return kept, carried, open_line, data, region


# --------------------------------------------------------------------------- R-G: the baseline

def read_baseline(root):
    """(ids_or_None, note). `None` means ABSENT, and row 2b must NO-OP -- it is not `set()`.

    R-G: the ONLY source is the delimited region in `state.md`. The two answers this function can
    give mean OPPOSITE things and must never collapse into one:

        PRESENT and EMPTY  -> `set()`  -- "no plan entries existed last save". A real baseline.
                              Nothing is in it, so row 2b matches nothing and moves nothing.
        ABSENT/malformed   -> `None`   -- "there is no baseline". Row 2b no-ops and SAYS SO.

    Reading absent as empty would be harmless here by luck (row 2b needs membership, and nothing
    is a member of the empty set) -- but the inverse reading, treating absent as "every id that
    was there is gone", is the F65 shape with a delete attached, and the two are one refactor
    apart. Keeping them distinct in the TYPE is what stops that refactor.
    """
    path = root / STATE_FILE
    if not path.is_file():
        return None, "no %s under --root" % STATE_FILE
    _data, rows, _open_line = recall.load_doc(path)
    begin = None
    value = None
    for (lineno, text, _s, _e, in_fence) in rows:
        if in_fence:
            continue
        if begin is None:
            if BACKLOG_IDS_BEGIN_RE.match(text):
                begin = lineno
            continue
        if BACKLOG_IDS_END_RE.match(text):
            break
        m = BACKLOG_IDS_VALUE_RE.match(text)
        if m and value is None:
            value = m.group("body")
    else:
        if begin is not None:
            return None, ("`%s` opens a BACKLOG-ENTRY-IDS region at line %d that is never closed"
                          % (STATE_FILE, begin))
    if begin is None:
        return None, ("`%s` carries no `<!-- BACKLOG-ENTRY-IDS:BEGIN -->` region" % STATE_FILE)
    if value is None:
        return None, ("`%s`'s BACKLOG-ENTRY-IDS region (line %d) holds no `backlog_entry_ids: [...]` "
                      "line" % (STATE_FILE, begin))
    ids = set()
    for tok in value.split(","):
        tok = tok.strip().strip("'\"").strip()
        if tok:
            ids.add(tok)
    return ids, "%s (%d id(s))" % (STATE_FILE, len(ids))


# --------------------------------------------------------------------------- trigger readers

def expiry_absolute(it, current_save):
    """(absolute_save, how) or (None, why). Row 1's reading of `expires:`, in ABSOLUTE saves.

    Every branch is `validate_entries`'. A bare integer is an ABSOLUTE save number and `N saves` /
    `three saves` is RELATIVE to `_horizon_base` -- the same split rule A5 enforces, so an entry
    A5 accepts is one this row can read, and the two cannot disagree about what an author wrote.
    """
    ent = it.entry
    raw = validate_entries._expiry_value(ent)
    if raw is None:
        return None, "no `expires:` field and no expiry in the heading"
    val = validate_entries._clean_value(raw)
    if not val or validate_entries._is_placeholder(raw):
        return None, "expiry %r is a placeholder, not a bounded save count" % raw
    n = None
    m = validate_entries.RELATIVE_SAVES_RE.match(val)
    if m:
        n = int(m.group(1) or m.group(2))
    else:
        w = validate_entries.WORD_SAVES_RE.match(val)
        if w:
            n = validate_entries.WORD_NUMERALS[w.group(1).lower()]
    if n is not None:
        base, src = validate_entries._horizon_base(ent, current_save)
        if base is None:
            return None, "relative expiry %r with no base save# to count from" % val
        return base + n, "%d + %d saves (base: %s)" % (base, n, src)
    a = validate_entries.ABSOLUTE_SAVE_RE.match(val)
    if a:
        return int(a.group(1)), "absolute save#"
    return None, "expiry %r is not a save count this tool can read" % raw


def done_marker(it):
    """(value, why_not). Row 2 needs an EXPLICIT, NON-PLACEHOLDER `done:`.

    `done: TBD` and a bare `done:` do NOT fire. Row 2 is ONE-WAY, and a drain that cannot tell
    finishing from forgetting manufactures false completions at the rate the agent is fallible --
    which is the whole reason row 2b exists. A placeholder is forgetting wearing row 2's label.
    """
    for key in DONE_KEYS:
        vals = it.extra.get(key)
        if not vals:
            continue
        raw = vals[0][1]
        if not validate_entries._clean_value(raw) or validate_entries._is_placeholder(raw):
            return None, ("`%s:` at %s:%d has the placeholder value %r, which asserts nothing. "
                          "Row 2 is ONE-WAY and needs the save it finished in, e.g. "
                          "`done: save#12`." % (key, it.rel, vals[0][0], raw))
        return raw, ""
    return None, ""


def has_done_field(it):
    """True when ANY `done:`/`completed:` field is present, placeholder or not.

    Row 2b's literal condition is "NO `done:`". An item carrying a placeholder `done:` therefore
    fires NEITHER row -- it stays hot, with the warning `done_marker` produced. That is the
    correct place for it: the author said something about completion that the tool cannot read,
    and quietly filing it under `backlog-dropped.md` would answer a question nobody asked.
    """
    return any(key in it.extra for key in DONE_KEYS)


def gate_state(it):
    """('unsatisfied'|'satisfied'|'placeholder', raw) or (None, None) when there is no `gate:`.

    A gate is UNSATISFIED by default. Deciding whether "channel B opens" has actually happened is
    not a regex's job, and a rule that guessed would move live work out of the hot set on a
    coin-flip. So satisfaction must be DECLARED, in one of exactly two forms: a `gate:` value
    opening with the uppercase token `SATISFIED`, or a `gate-satisfied:` field with a real value.

    A PLACEHOLDER gate (`gate: none`, `gate: n/a - just deferred`) is reported and NOT moved. The
    two S3.2 destinations are `GATE_REQUIRED` (validate_entries, spec S7.1): filing an ungated
    entry there produces content nothing will ever bring back, which is a deletion with extra
    steps and one the validator would abort on at the next save.
    """
    raw = it.entry.first("gate")
    if raw is None:
        return None, None
    if not validate_entries._clean_value(raw) or validate_entries._is_placeholder(raw):
        return "placeholder", raw
    if GATE_SATISFIED_RE.match(raw or ""):
        return "satisfied", raw
    sv = it.extra_first(*GATE_SATISFIED_KEYS)
    if sv is not None and validate_entries._clean_value(sv) \
            and not validate_entries._is_placeholder(sv):
        return "satisfied", raw
    return "unsatisfied", raw


def gate_resolvable_ids(root, backlog_items, families):
    """The id set an ID-GATED `gate:` may point at: D81's three files, and only those.

    `backlog.md` comes from the items the run has ALREADY scanned -- re-reading it here would be a
    second walk that can disagree with the first. The two archives are read directly, because
    they are destinations and no source scan covers them.

    A file that does not exist contributes nothing and is not an error: `_archive/backlog-done.md`
    is created by the first row-2 move, so a young project simply has fewer ids to resolve
    against, and the failure direction of that is an entry that stays HOT.
    """
    ids = set()
    for it in backlog_items:
        if it.ident:
            ids.add(it.ident)
    for rel in GATE_RESOLVE_ARCHIVES:
        path = root / rel
        if not path.is_file():
            continue
        data, rows, _open = recall.load_doc(path)
        for e in recall.heading_map(rel, data, rows, families):
            ids.add(e["id"])
    return ids


def gate_admissible(raw, resolvable, families):
    """(True, "") when this `gate:` value is one of D81's two contracts AND names a condition the
    archive it would be filed in will accept; (False, why) otherwise.

    THE MEASURED STARTING STATE THIS CLOSES: `gate: waiting for the vibes to be right` and
    `gate: I hallucinated this` both DEFERRED successfully. The only refusal was
    `_is_placeholder`'s short list (`none`, `n/a`, `TBD`, `-`, and those wearing a qualifier), so
    anything that read like a sentence got through. D81 was ratified, narrated as enforced, and
    enforced nothing.

    THE COST IS NOT SYMMETRIC, and the paragraph that stood here said it was -- it asserted that a
    wrong answer costs "ONE ITEM STAYING VISIBLE IN backlog.md -- D74's ratified bias toward not
    moving". That is true of a wrong REFUSAL and FALSE of a wrong ADMISSION. A wrongly admitted
    gate files the entry in a GATE-REQUIRED archive, `validate_entries` rule A7 then ABORTS the
    next save at exit 3, and the drain's SOURCES exclude `_archive/`, so NO LATER RUN OF THIS TOOL
    CAN RECONSIDER IT. MEASURED 2026-08-07 (F1): with the wedge in place, three further
    demote+validate cycles produced validate exit 3 every time, with the same two A7 lines. So
    this function may be strict and may not be lax.

    WHICH IS WHY ADMISSION IS A CONJUNCTION, not a form test. Both branches below ALSO require
    `not validate_entries._gate_names_no_condition(raw)` -- the validator's OWN content test,
    borrowed from that module rather than reinvented here as this file's first content predicate.
    The aim is to make the WRITE gate a SUBSET of the READ gate, which closes the wedge for the
    infinite set of values A7 rejects instead of for the handful anyone wrote down. MEASURED
    2026-08-07, and it is why a form-only fix would have been useless: the wedge is reachable
    through EITHER branch -- `gate: EXTERNAL - eventually the pilot reports back` through the form
    branch, and `gate: M1 lands, whenever that is -- honestly no idea when` through the id branch
    with no EXTERNAL token anywhere in it.

    THE SUBSET IS NOT COMPLETE, AND SAYING SO IS THE POINT. This function is only consulted for the
    entry a ROW FIRED ON. A heading NESTED inside that entry rides into the archive inside its
    ancestor's move span (F3) and is a full entry to `validate_entries` when it lands there, but no
    `gate:` of its own is ever read -- so a child with `kind:` and `lifetime:` and NO gate still
    makes A7 abort the next save at exit 3. MEASURED 2026-08-07 with `## M20` gated correctly and
    `### M21` ungated: `demote` exit 0, validate exit 3, `A7 ... [M21] no gate:`, IDENTICAL before
    and after F1 and F3. F3's screen does NOT close it: that screen compares child IDS against the
    archive set, and F3 deliberately carries `(id, label, level, line)` and no fields, so there is
    no `gate:` value there to test. Closing it needs a different change -- carrying the child's
    fields, or screening ungated children out of `GATE_REQUIRED` destinations -- and that change is
    not this one. `_st_arms_carried_children` PINS the hole so it is recorded rather than
    rediscovered.

    Division of labour, unchanged: `validate_entries` keeps its detector deliberately narrow (it
    fires only on prose that EXPLICITLY disclaims a condition, never on prose that is merely
    vague), because ITS answer is the abort. This function inherits exactly that narrowness. It
    does not widen the rule; it stops the drain from writing what the reader will reject.

    Stated rather than hidden, exactly as D81 states it: the event-gated form is a DECLARATION,
    not a verification. Nothing checks that the named event ever occurs. It buys what
    `WAIVED - <reason>` buys -- the author named a class and an observable instead of typing a
    sentence, and a reader can see which gates nothing will ever close.
    """
    disclaimed = validate_entries._gate_names_no_condition(raw)
    if GATE_EXTERNAL_RE.match(raw or "") and not disclaimed:
        return True, ""
    named = [i for (i, _s, _e) in citemap.scan_occurrences(raw or "", families)]
    if any(i in resolvable for i in named) and not disclaimed:
        return True, ""
    if disclaimed:
        why = ("its wording explicitly disclaims having a condition -- the same test "
               "`validate_entries` rule A7 applies to the gate-required archive this would be "
               "filed in. Admitting it would append an entry that ABORTS the next save at exit 3, "
               "and the drain never reads `_archive/`, so no later run could take it back%s"
               % ("" if (GATE_EXTERNAL_RE.match(raw or "")
                         or any(i in resolvable for i in named))
                  else "; it also matches neither of D81's two forms"))
    elif named:
        why = ("it names %s, which resolves in none of backlog.md, %s or %s"
               % (", ".join(sorted(set(named))), D_BACKLOG_DEFERRED, D_DONE))
    else:
        why = "it names no id at all and does not open with `EXTERNAL - `"
    return False, why


def gate_question_ids(raw, families):
    """The OPEN-QUESTION ids this gate value names. D97, and it decides WORDING, never behaviour.

    the maintainer's ruling, and it is why the refusal needs its own sentence: *"we are not modeling
    dependency. we are modeling deferral."* A waiting line records WHY something is parked, not
    what it depends on. The moment it can name anything we are building a dependency graph -- and
    since there is no auto-promotion, nothing would ever walk that graph. It would become homework
    for the human: to bring one task back he would have to ask about parked tasks, spot one
    waiting on a question, ask about parked questions, unpark that, and come back.

    The RESTRICTION IS ONE-DIRECTIONAL and that is the part worth knowing: a QUESTION waiting on a
    TASK works today and is the direction actually needed, since a question usually waits on the
    work that produces the evidence to answer it. Only task-waits-on-question and
    question-waits-on-question are refused, and under this model those mean the work is PREMATURE
    -- a branch of the question wearing a task's clothes -- rather than deferred.
    """
    return [i for (i, _s, _e) in citemap.scan_occurrences(raw or "", families)
            if citemap.family_of(i, families).startswith(QUESTION_FAMILY_PREFIX)]


def abandon_marker(it):
    """(value, why_not). Row 2b's REQUIRED declaration: a person wrote "we will not do this".

    Same shape as `done_marker`, deliberately, and for the same reason: `wont-do:` is read
    through the shared field grammar, so prose ("we are not going to do this") is not a field and
    cannot fire it, and a PLACEHOLDER value (`wont-do: TBD`) asserts nothing and does not fire it
    either. `backlog-dropped.md` is ONE-WAY; a drain that cannot tell abandoning from forgetting
    manufactures abandonment at the rate the agent is fallible.
    """
    for key in DROP_KEYS:
        vals = it.extra.get(key)
        if not vals:
            continue
        raw = vals[0][1]
        if not validate_entries._clean_value(raw) or validate_entries._is_placeholder(raw):
            return None, ("`%s:` at %s:%d has the placeholder value %r, which asserts nothing. "
                          "Row 2b is ONE-WAY and `%s` means *never*, so it needs a reason, e.g. "
                          "`%s: the approach was abandoned when D81 landed`."
                          % (key, it.rel, vals[0][0], raw, D_DROPPED, key))
        return raw, ""
    return None, ""


def _title_segments(title):
    """A heading title cut on its separators, each piece stripped of emphasis and backticks. ROW 4.

    `## D24 - superseded by D9 - the old surface` -> ['D24', 'superseded by D9', 'the old surface'].
    `## D24 - what [superseded by D9] means` -> ['D24', 'what [superseded by D9] means'] -- no
    piece STARTS with the marker, which is the whole distinction. `[`, `(`, `;` and `,` are NOT
    separators (see `TITLE_SPLIT_RE`): they continue a sentence, and a sentence is prose.
    `## D24 · not-superseded by D9` -> ['D24', 'not-superseded by D9'] -- a separator GLUED between
    two alphanumerics joins a compound word instead of separating two parts, so it does not cut.
    """
    out = []
    for piece in TITLE_SPLIT_RE.split(title or ""):
        piece = piece.strip().strip("`").strip("*_").strip()
        if piece:
            out.append(piece)
    return out


def _status_tail(title, ident):
    """What a heading says AFTER its id, past AT MOST ONE separator. `None` when it opens with
    something that is not its id at all.

    `## OQ13 RESOLVED 2026-08-02 - one threshold` -> `RESOLVED 2026-08-02 - one threshold`
    `## OQ13 - RESOLVED - we ship X`              -> `RESOLVED - we ship X`
    `## OQ9 · is it (RESOLVED) or still open`     -> `is it (RESOLVED) or still open`

    STATUS POSITION is the containment. A marker anywhere else in a heading is a word inside a
    title, and a title is prose. The id-prefix guard matters on its own: `M4` is a PREFIX of `M40`,
    and consuming it would read `M40 RESOLVED`'s status off the wrong entry's id.
    """
    text = (title or "").strip()
    if ident and text.startswith(ident):
        rest = text[len(ident):]
        if rest[:1].isalnum() or rest[:1] == "_":
            return None                       # `M4` is a prefix of `M40`, not its id
    else:
        parts = text.split(None, 1)
        if len(parts) < 2:
            return None
        rest = parts[1]
    m = STATUS_SEP_RE.match(rest)
    if m:
        rest = rest[m.end():]
    return rest.strip()


def resolved_marker(it):
    """(fired, near_miss). Row 3: RESOLVED must be in STATUS POSITION in the heading.

    MEASURED 2026-08-03, every one of these ONE-WAY, at exit 0, with nothing on stderr:
      `## OQ8 - NOT RESOLVED: which retrieval shape wins`  -- the heading that says it is OPEN
      `## OQ9 · is it (RESOLVED) or still open`            -- `(` opened a "segment"
      `## OQ9 · blocked; RESOLVED in D4 is the precedent`  -- `;` opened a "segment"
      `## OQ9 · what the [RESOLVED] tag should mean`       -- `[` opened a "segment"
      `## OQ9 · RESOLVED, or is it`                        -- status position, still a question
      `## OQ9 · RESOLVED-pending-review - still open`      -- the hyphen JOINED a compound word
    The first four are the SEGMENT defect; the fifth is in status position and needed the
    STANDALONE half; the sixth needed the standalone half to demand a REAL word boundary (a space
    or end of line) rather than accepting a separator glued straight onto the token. A heading that
    merely CONTAINS the word is a near-miss and stays hot.
    """
    if not RESOLVED_RE.search(it.title or ""):
        return False, ""
    tail = _status_tail(it.title, it.ident)
    if tail is not None and RESOLVED_MARKER_RE.match(tail):
        return True, ""
    return False, ("%s %s:%d -- row 3 did not fire: the heading contains RESOLVED but not as a "
                   "STATUS MARKER (it must be the token immediately after the id, past at most "
                   "one separator, and must END there -- followed by nothing, or by a SPACE and "
                   "then a separator or a date, as in `## %s RESOLVED 2026-08-02 - <answer>`. A "
                   "separator glued straight on, as in `RESOLVED-pending`, makes it one word.) "
                   "It stays hot. Row 3 is ONE-WAY, and "
                   "`NOT RESOLVED` is the opposite of the trigger."
                   % (it.label, it.rel, it.line, it.label))


def _successor_in(tail, families):
    """The FIRST TOKEN of `tail`, if it is an id AND the clause ends there. Not "any id anywhere".

    MEASURED 2026-08-03: "This will be superseded by whatever replaces it, unlike D9 which stands"
    archived the section AND recorded `D9` as its successor -- an id lifted from the contrasting
    clause of a sentence saying the supersession had NOT happened. `superseded by <id>` names its
    object immediately or it names nothing this tool may act on.

    A token ending in `,` or `;` is REJECTED for the same reason one measured on the row-3 side:
    `superseded by D9, or is it` puts the id in exactly the right place inside a sentence that is
    still asking the question. Punctuation that CONTINUES a clause says the heading has not
    finished speaking; row 4 is ONE-WAY, so it does not get the benefit of the doubt.
    """
    tok = (tail or "").strip().split(None, 1)
    if not tok:
        return None
    if tok[0].endswith(",") or tok[0].endswith(";"):
        return None
    word = tok[0].strip("`*_\"'([{").rstrip("`*_\"')]}.,;:!?")
    if not word:
        return None
    hits = citemap.scan_occurrences(word, families)
    if hits and hits[0][0] == word:
        return word
    return None


def superseded_by(it, families):
    """(successor_id, marker_line) / (None, marker_line) / (None, None). Row 4.

    TWO POSITIONS, AND ONLY TWO. A `-`-separated SEGMENT of the heading, or a FIELD LINE in the
    body -- the line shape `validate_entries.FIELD_RE` already requires of `kind:` / `lifetime:` /
    `gate:`, which is what row 5 has always used. Both are DECLARATION positions: places where
    what is written is a statement about THIS entry rather than a sentence that mentions one.

    MEASURED 2026-08-03, all at exit 0 and all ONE-WAY: a LIVE architecture section was archived
    for explaining that something ELSE was `superseded by D9`; a heading reading `NOT superseded by
    D9` was archived; a future-tense sentence was archived with a successor taken from an unrelated
    clause; and the same heading in its HYPHENATED form -- `## D41 · not-superseded by D9` -- still
    fired after the first fix, because the separator set cut compound words in half.
    `TITLE_SPLIT_RE` now requires a real edge.

    MEASURED 2026-08-07 (M9, and the ruling that followed it): the BODY half accepted a bullet, an
    indented sub-bullet and a `**bold**` run as well, because it stripped a leading list/emphasis
    run before matching. A bullet list is how a person writes down what replaced what, so the
    highest-traffic form of an ANCHORED MENTION was firing a ONE-WAY move. See
    `SUPERSEDED_FIELD_RE` for the seven positions and their before/after.

    `architecture.md` and `design.md` are exactly where a project narrates its own supersession
    history, so this is still the highest-reachability trigger in the table -- which is the reason
    the two accepted positions are the narrow ones.
    """
    seen = None
    for seg in _title_segments(it.title):
        m = SUPERSEDED_MARKER_RE.match(seg)
        if m:
            seen = seg.strip()
            ident = _successor_in(m.group("tail"), families)
            if ident:
                return ident, seen
    for (_ln, text) in it.entry.body:
        m = SUPERSEDED_FIELD_RE.match(text or "")
        if not m:
            continue
        seen = (text or "").strip()
        ident = _successor_in(m.group("tail"), families)
        if ident:
            return ident, seen
    return None, seen


def superseded_mentioned(it):
    """True when the phrase appears anywhere the marker scan did NOT accept it.

    A trigger that silently declines is indistinguishable from a file with no trigger in it, so
    the prose case that USED to fire now warns instead of moving. The operator learns which of the
    two they wrote; the entry stays hot either way.

    THIS SCAN IS DELIBERATELY WIDER THAN THE MARKER (`SUPERSEDED_RE` allows `-` and `_` where
    `SUPERSEDED_MARKER_RE` and `SUPERSEDED_FIELD_RE` demand whitespace) and it can only ever
    produce a WARNING. M9's near-miss sentence tells the author to write the marker "as `kind:` and
    `gate:` are written", and the obvious next thing to type is `superseded-by: D9` -- MEASURED
    2026-08-07 to archive nothing and say NOTHING AT ALL, because the hyphen defeated the phrase
    scan too. A message that invites a form must not leave that form silent.
    """
    if SUPERSEDED_RE.search(it.title or ""):
        return True
    return any(SUPERSEDED_RE.search(t or "") for (_ln, t) in it.entry.body)


# --------------------------------------------------------------------------- the decision

# The clause every TERMINAL HOLD ends with. B5: the messages said "it STAYS HOT / STAYS IN
# backlog.md" full stop, and that was true of the decision and false of the outcome for one
# population -- an entry an EARLIER drain had already archived. A hold refuses to start a move; it
# has nothing to say about a move that already happened. Spelled once so the three hold sentences
# cannot drift apart.
_HOLD_TAIL = (" A hold refuses to START a move, never to FINISH one: if a BYTE-IDENTICAL copy of "
              "this entry is already in the archive this row routes to, an earlier run put it "
              "there and this run completes THAT move by removing the hot duplicate -- appending "
              "nothing, and moving nothing new.")


def decide(it, current_save, baseline, rewrote, rewrote_complete, families, gate_ids, warn):
    """Set `it.row`/`it.why` to the FIRST row in ROW_PRECEDENCE that fires, or leave it hot.

    `warn` collects operator-visible sentences. Every near-miss that could plausibly have been
    meant as a trigger produces one: a trigger that silently does not fire is indistinguishable
    from a file with no trigger in it, and the author has no way to learn which they wrote.

    ROW 2b DOES NOT EVICT A PRESENT ITEM unless the caller declares its `--rewrote` set COMPLETE,
    and -- since D81's sibling ruling -- unless a PERSON wrote a `wont-do:` on it. See
    `pending_drops` for what the default does instead, and why.

    TERMINAL HOLD (D81, design clarification section 5). Row 5 is the only branch here that can
    `return` WITHOUT setting a row. When the gate check decides an entry stays hot, no later row
    may reconsider it -- and the only later row is 2b, whose archive is ONE-WAY and means
    *never*. A hold that merely `continue`d is the shape that lets a DEFERRAL leak into an
    ABANDONMENT: not "the item was not moved" but "the item was moved to the wrong one of two
    files that mean opposite things". the maintainer's ruling is that this is a CATEGORY ERROR, so the hold
    is absolute rather than a matter of precedence.

    A HOLD REFUSES TO START A MOVE. IT DOES NOT REFUSE TO FINISH ONE (B5, MEASURED 2026-08-07).
    Every hold below therefore records `it.held_row` before returning. `run` uses that to ask ONE
    further question -- is a BYTE-IDENTICAL copy of this entry already sitting in the archive this
    row routes to? -- and, if it is, hands the entry to `screen_batch` marked `reconcile_only`.
    The reason is that the newly-refused population is exactly the population an earlier drain
    COULD already have archived, so refusing to reconcile turns a recoverable duplicate into a
    permanent one: the entry stays hot AND archived, every re-run refused, at exit 0, for ever.
    Pinned by `_st_arms_sequence`'s B5 arm, whose interrupted state is built with this tool's own
    `_build_destination` rather than by re-running it. The hold itself is UNCHANGED --
    `reconcile_only` can only ever reach `already` or `refused`, never `append` -- so nothing this
    rule touches can put a new entry into an archive.
    """
    for row in ROW_PRECEDENCE:
        if row not in SOURCE_ROWS.get(it.rel, ()):
            continue

        if row == 1:
            if current_save is None:
                continue                       # caller warned once; a compare needs a "now"
            when, how = expiry_absolute(it, current_save)
            if when is None:
                warn.append("%s %s:%d -- row 1 did not fire: %s. It stays hot; rule A5 is where "
                            "that is an abort." % (it.label, it.rel, it.line, how))
                continue
            if when <= current_save:
                it.row, it.why = 1, ("expires save#%d <= current save#%d (%s)"
                                     % (when, current_save, how))
                return
            continue

        if row == 3:
            fired, near = resolved_marker(it)
            if fired:
                it.row, it.why = 3, "heading carries the RESOLVED marker"
                return
            if near:
                warn.append(near)
            continue

        if row == 4:
            successor, marker = superseded_by(it, families)
            if successor:
                it.row, it.why = 4, "superseded by %s" % successor
                return
            if marker:
                warn.append("%s %s:%d -- row 4 did not fire: %r does not name a successor in the "
                            "position row 4 requires -- the id must be the VERY NEXT TOKEN, it "
                            "must resolve to an id family, and the clause must end there (a "
                            "trailing `,` or `;` means the sentence is still running). Otherwise "
                            "the archived section would point nowhere, or at the wrong entry. "
                            "Write `superseded by <id>`."
                            % (it.label, it.rel, it.line, marker[:70]))
            elif superseded_mentioned(it):
                warn.append("%s %s:%d -- row 4 did not fire: `superseded by` appears here but not "
                            "in MARKER POSITION. It must either open a `-`-separated part of the "
                            "heading -- and a `-` glued inside a word like `not-superseded` "
                            "separates nothing -- or be a FIELD LINE in the body: at column 0 "
                            "(up to three spaces), with no bullet, no `**`, no `>` and no fence "
                            "around it, exactly where `kind:` and `gate:` go. A bullet or a "
                            "bolded run is a MENTION (M9). The id must then be the very next "
                            "token. WRITE IT EXACTLY AS `superseded by D9` -- three words, spaces, "
                            "NO colon and NO hyphen; `superseded-by: D9` is not this marker. It "
                            "stays hot -- row 4 is ONE-WAY, and prose about some OTHER thing's "
                            "supersession is not this section's own."
                            % (it.label, it.rel, it.line))
            continue

        if row == 2:
            if it.region_state == BACKLOG_MALFORMED:
                continue                       # cannot tell what is inside the region; run warned
            if it.in_backlog:
                continue                       # carve-out: 6b writes this region AFTER 6a
            value, why_not = done_marker(it)
            if value:
                it.row, it.why = 2, "explicit `done: %s`" % value
                return
            if why_not:
                warn.append(why_not)
            continue

        if row == 5:
            state, raw = gate_state(it)
            if state == "unsatisfied":
                ok, why = gate_admissible(raw, gate_ids, families)
                if not ok:
                    questions = gate_question_ids(raw, families)
                    if questions:
                        # D97. NOT the generic sentence: this one is a design decision, and
                        # "resolves in none of ..." would send the reader off to widen a file
                        # list that is not the reason.
                        warn.append(
                            "%s %s:%d -- NOT DEFERRED: `gate: %s` names an open question (%s), "
                            "and a waiting line may not name one. This is DELIBERATE (D97), not "
                            "a lookup miss: a waiting line records WHY something is parked, not "
                            "what it depends on. If this work cannot start until that question "
                            "is answered it is PREMATURE -- fold it into the question rather "
                            "than parking it. If it is simply not a priority, park it on that "
                            "basis: `gate: EXTERNAL - <the observable that makes it matter "
                            "again>`. (A QUESTION waiting on a TASK is the supported direction "
                            "and works today.)%s"
                            % (it.label, it.rel, it.line,
                               validate_entries._clean_value(raw), ", ".join(questions),
                               _HOLD_TAIL))
                        it.held_row = 5
                        return                 # TERMINAL HOLD -- same hold, different sentence
                    warn.append(
                        "%s %s:%d -- NOT DEFERRED: `gate: %s` is not an admissible gate, because "
                        "%s. It STAYS IN backlog.md/open_questions.md and no later row may move it. "
                        "A gate is a contract (D81), in exactly one of two forms: name an id that "
                        "resolves in backlog.md, %s or %s (`gate: waiting on M12`), or open with the "
                        "anchored token `EXTERNAL - ` and name an observable "
                        "(`gate: EXTERNAL - v4 is deployed to all machines`). Fix the wording and "
                        "the next save defers it.%s"
                        % (it.label, it.rel, it.line, validate_entries._clean_value(raw), why,
                           D_BACKLOG_DEFERRED, D_DONE, _HOLD_TAIL))
                    it.held_row = 5
                    return                     # TERMINAL HOLD -- row 2b may not reconsider it
                it.row, it.why = 5, "unsatisfied `gate: %s`" % validate_entries._clean_value(raw)
                return
            if state == "placeholder":
                warn.append("%s %s:%d -- row 5 did not fire: `gate: %s` is a placeholder, and the "
                            "deferred archive REQUIRES a real gate (S7.1/A7). Filing it there "
                            "would make it unreachable. It STAYS HOT and no later row may move "
                            "it.%s" % (it.label, it.rel, it.line, raw, _HOLD_TAIL))
                it.held_row = 5
                return                         # TERMINAL HOLD -- same shape, same reason
            continue

        if row == "2b":
            if not rewrote_complete:
                continue                       # DEFAULT: a PRESENT item is never dropped
            if it.region_state != BACKLOG_OK:
                continue                       # the carve-out's input is missing; run warned once
            if it.in_backlog:
                continue                       # carve-out, and the reason row 2b is survivable
            if baseline is None:
                continue                       # R-G: no baseline -> NO-OP. Caller warned once.
            if has_done_field(it):
                continue                       # row 2b's literal condition is "NO `done:`"
            key = it.ident or it.label
            if key in rewrote:
                continue                       # the caller says it rewrote this one
            if key not in baseline:
                continue
            declared, why_not = abandon_marker(it)
            if why_not:
                warn.append(why_not)
                continue
            if not declared:
                # THE RULING, and the one condition that is not an inference. An item still
                # present in `backlog.md` is never moved to the ONE-WAY "will not do" archive
                # because it was not rewritten; `pending_drops` REPORTS the ids that really did
                # vanish, and reporting and moving stay two mechanisms.
                continue
            it.row, it.why = "2b", ("`wont-do: %s`, and in the previous save's backlog_entry_ids "
                                    "baseline and NOT named by --rewrote, under "
                                    "--rewrote-complete" % validate_entries._clean_value(declared))
            return


def pending_drops(baseline, backlog_items, backlog_scanned):
    """(ids, note). Row 2b's ACTUAL subject: ids that were there last save and are NOT there now.

    THE DEFECT THIS FUNCTION EXISTS TO CLOSE, MEASURED 2026-08-03 by two independent adversaries.
    The predecessor read row 2b as `key in baseline` over the items it could still SEE, so its
    predicate was "this item existed last save" -- true of every carried-over item, INCLUDING one
    rewritten from top to bottom this save. Measured: a `backlog.md` holding M1 (rewritten this
    session) and M2 (untouched) was emptied to nothing but its BACKLOG delimiters, at exit 0, with
    zero bytes on stderr, to a ONE-WAY archive. Steady state was that `backlog.md` retained only items
    NEW in the current save. The row's own name is "not rewritten this save"; the implementation
    fired on the rewritten case too.

    spec S5.1 row 2b's trigger column reads `backlog.md` item **silently disappears**. That is this
    function's subject, and it is the one thing an ids-only baseline CAN decide honestly: an id in
    the baseline that no heading in `backlog.md` carries now is gone. There are no bytes to move --
    the previous save's copy lives in cold storage, which R-G forbids the drain from reading -- so
    this is a REPORT, not a move. spec S5.1 also requires the next restore to print a pending-drop
    list from `state.md`; nothing in P1 writes that field (see the SPEC GAP note in the module
    docstring), so the receipt is where the record lives for now.

    A `backlog.md` that is ABSENT is not a per-item disappearance and produces no list: "the file is
    gone" and "these three items are gone" are different facts and the second must not be inferred
    from the first.

    DO NOT WIDEN THIS TO PRESENT ITEMS. When `wont-do:` was added to row 2b it was tempting to
    give this report the same subject, so that one rule covered both. It is the wrong shape and
    the near-miss is recorded as F208. REPORTING a disappearance and MOVING an entry are two
    mechanisms: a PRESENT item has not disappeared, so it can never correctly be this report's
    subject, and folding it in would add a false-positive channel to the ONE instrument that
    exists to catch silent disappearance. An instrument that cries wolf about items sitting right
    there is one the operator learns to ignore, which is the same silence by another route.
    """
    if baseline is None:
        return [], "row 2b baseline ABSENT (R-G)"
    if not backlog_scanned:
        return [], ("backlog.md is absent, so no id can be reported as individually dropped -- a "
                    "missing FILE is not a per-item disappearance")
    present = set()
    for it in backlog_items:
        present.add(it.ident or it.label)
        if it.label:
            present.add(it.label)
    gone = sorted(i for i in baseline if i not in present)
    return gone, ""


# --------------------------------------------------------------------------- the move engine

# Header planted in a destination this tool CREATES (W30). Level 1 only: a level-1 heading is the
# document title and never opens an entry (validate_entries.parse_entries), so the header cannot
# be mistaken for archived content, cannot be demoted again, and cannot absorb a field.
DEST_BLURB = {
    D_EXPIRED: ("expired active instructions",
                "Step 6a ROW 1, ONE-WAY. An `active.md` entry whose expiry reached the current "
                "save. Ids are intact: `recall get <id> --root <folder>` resolves here."),
    D_DONE: ("completed plan items",
             "Step 6a ROW 2, ONE-WAY. A `backlog.md` item that carried an EXPLICIT `done:` marker."),
    D_DROPPED: ("dropped plan items",
                "Step 6a ROW 2b, ONE-WAY, and it means WILL NOT DO -- not `not now`, which is "
                "`_archive/backlog-deferred.md`. A `backlog.md` item that carried an EXPLICIT "
                "`wont-do:` declaration a person wrote, AND was in the previous save's baseline, "
                "was NOT named by `--rewrote` under `--rewrote-complete`, carried no `done:`, and "
                "sat OUTSIDE a WELL-FORMED BACKLOG region. Nothing reaches this file by "
                "inference: an item is never abandoned because it was not rewritten. Dropped is "
                "not finished -- row 2 is the finished one. Without `--rewrote-complete` row 2b "
                "writes nothing here: an ids-only baseline cannot tell a rewritten item from an "
                "untouched one."),
    D_RESOLVED: ("resolved open questions",
                 "Step 6a ROW 3, ONE-WAY. An `open_questions.md` heading carrying RESOLVED."),
    D_ARCH_SUPERSEDED: ("superseded architecture sections",
                        "Step 6a ROW 4, ONE-WAY. A section carrying `superseded by <id>`."),
    D_DESIGN_SUPERSEDED: ("superseded design sections",
                          "Step 6a ROW 4, ONE-WAY. A section carrying `superseded by <id>`."),
    D_BACKLOG_DEFERRED: ("deferred plan items",
                      "Step 6a ROW 5, REVERSIBLE, and it means NOT NOW -- not `never`, "
                      "which is `_archive/backlog-dropped.md`. Every entry here carries an "
                      "ADMISSIBLE `gate:` -- it names an id that resolves, or it opens "
                      "`EXTERNAL - ` -- and that gate is what brings it back to `backlog.md` "
                      "with the SAME id (spec S7.1, rule A7). To bring one back, move the "
                      "entry into `backlog.md` and add a `gate-satisfied:` line -- leaving the "
                      "`gate:` line itself as the record of why it waited."),
    D_OQ_DEFERRED: ("deferred open questions",
                    "Step 6a ROW 5, REVERSIBLE. Every entry here carries an ADMISSIBLE "
                    "`gate:` that brings it back to `open_questions.md` with the SAME "
                    "id (spec S7.1, rule A7), and a `gate-satisfied:` line on the returned "
                    "entry is what stops the next save deferring it again."),
}


def dest_header(rel):
    title, blurb = DEST_BLURB[rel]
    return ("<!-- ash_save v4 | created by demote.py (step 6a) -->\n"
            "# %s\n\n%s\n\nCreated empty because the drain needed it; a fresh scaffold ships only "
            "`_archive/` and the two S3.2 deferred files.\n" % (title, blurb)).encode("utf-8")


def _eol_of(data):
    """The terminator this file already uses. A drain must not convert a file's line endings.

    A whole-file rewrite through a text layer flips every terminator and `git diff` can be blind
    to it under a normalising attribute, so this tool never decodes to write: it splices BYTES,
    and the ONE byte sequence it contributes -- the separator before an appended entry -- copies
    whatever the destination already uses.
    """
    if data.endswith(b"\r\n"):
        return b"\r\n"
    if data.endswith(b"\n"):
        return b"\n"
    if b"\r\n" in data:
        return b"\r\n"
    return b"\n"


def _write_bytes(path, data):
    """tmp + os.replace, in the destination's own directory. Bytes only: no text layer, no BOM.

    The tmp is removed if the replace fails. MEASURED 2026-08-03: a read-only source left
    `backlog.md.demote-tmp` in the project root at exit 2 -- inert (it does not match `*.md`, so no
    scan sees it) but an artefact a failed run should not leave in a folder it does not own.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".demote-tmp")
    try:
        with open(str(tmp), "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    except (IOError, OSError):
        try:
            if tmp.exists():
                tmp.unlink()
        except (IOError, OSError):               # pragma: no cover - best effort
            pass
        raise


def _build_destination(existing, payloads, created=False):
    """(new_bytes, [offset]). Appends each payload, separated, never rewrapped.

    Offsets ONLY. Returning the payload alongside would invite the verifier to compare the write
    against the same buffer the write came from, which proves nothing: R-H's reference is the
    SOURCE file on disk, re-read, and that is where `_verify_moves` gets it.

    When the destination is being CREATED, the separator follows the PAYLOAD's terminator rather
    than the header's. The header this tool composes is LF, so a CRLF entry appended to a fresh
    destination otherwise produced a mixed-EOL archive on its very first write -- harmless to
    `get`, but this tool's one rule about line endings is that it contributes none of its own.
    """
    eol = _eol_of(payloads[0]) if (created and payloads) else (_eol_of(existing)
                                                               if existing else b"\n")
    if created and eol != b"\n":
        existing = existing.replace(b"\r\n", b"\n").replace(b"\n", eol)
    out = bytearray(existing)
    offsets = []
    for payload in payloads:
        if out:
            guard = 0
            while not bytes(out).endswith(eol + eol) and guard < 4:
                out.extend(eol)
                guard += 1
        offsets.append(len(out))
        out.extend(payload)
    if out and not bytes(out).endswith(eol):
        out.extend(eol)
    return bytes(out), offsets


def source_span(root, it):
    """The entry's bytes as they sit in the hot file RIGHT NOW. R-H's one reference."""
    return (root / it.rel).read_bytes()[it.byte_start:it.byte_end]


def _verify_moves(root, rel, moves, offsets, families, notes):
    """R-H. Returns the set of item indexes whose bytes did NOT survive the write.

    TWO checks, and both are needed:

    (a) the RAW SLICE, re-read from the DESTINATION on disk at the offset the builder used, must
        equal the SOURCE span, re-read from the source on disk. Both sides come off the
        filesystem, so a corrupted, truncated or partially-written copy fails here. Comparing the
        write buffer with itself would pass over every one of those.
    (b) the id must RESOLVE in the destination through `recall.load_doc` + `heading_map`, and
        the span those produce must BEGIN with the source span and END there -- `_payload_ends_at`,
        the SAME predicate `screen_batch` STEP 2 uses. This is the C-4 check proper: it is the
        exact path `recall get <id>` takes, so passing it is the statement that `get` will
        return this entry from the archive.

        IT WAS `got.rstrip() != span.rstrip()`, AND THAT WAS A THIRD COPY OF B4's ASSUMPTION. A
        `heading_map` span ends at the next heading of the SAME OR HIGHER level, so an entry with a
        DEEPER heading after it -- appended later, or appended by this very batch -- has a span
        that legitimately includes the deeper entry. Measured 2026-08-07 on
        `_st_arms_sequence`'s B4 identified arm: `MOVE FAILED M4: ... 136 B vs 68 B`, on bytes
        check (a) had just proved IDENTICAL, so the receipt reported a byte-equality failure about
        bytes that were byte-equal. The check was also inconsistent with itself: an entry that
        carries its own `###` child in ONE payload produces exactly the same `get` result and
        PASSES, so two identical retrieval outcomes were being judged differently on the basis of
        how they were written. Trailing whitespace is still tolerated -- the separator this tool
        appends after the LAST entry falls inside that entry's span and is not part of what was
        moved -- and prose after the payload still FAILS, which is the shape that matters.

        WHAT THIS LETS THROUGH THAT DID NOT PASS BEFORE, stated rather than buried: an entry whose
        destination span runs on into ANOTHER ENTRY now moves where it previously stayed hot with
        a MOVE FAILED line. Reaching it needs a level-3 heading that is not nested inside a level-2
        item -- i.e. one that appears before any level-2 heading in its source file -- or the same
        shape arriving across two runs. D74 is why that population is named here instead of being
        left to be discovered: the bytes were verified identical by (a) in every one of those
        cases, so what changed is a false alarm, not a safety margin.

    An item with no citemap-resolvable id gets (a) only, and a note saying so -- (b) has no id to
    ask about. That is the KNOWN GAP in the module docstring, surfaced per item rather than
    assumed away.

    EVERY PATH THAT REMOVES BYTES FROM A HOT FILE COMES THROUGH HERE, including the ALREADY path,
    which appends nothing and passes the id's EXISTING span start as its offset. That path used to
    skip this function entirely (`perform` extended `moved` with it before the loop ran), so the
    one branch whose whole action is a DELETION was the one branch with no byte-equality check.
    """
    path = root / rel
    data = path.read_bytes()
    _d, rows, open_line = recall.load_doc(path)
    if open_line is not None:
        notes.append("%s ends with an OPEN fence at line %d AFTER the append -- every entry "
                     "below it is invisible to `get`." % (rel, open_line))
    hmap = recall.heading_map(rel, data, rows, families)
    by_id = {}
    for e in hmap:
        by_id.setdefault(e["id"], []).append(e)

    failed = set()
    for idx, ((it, _dest), off) in enumerate(zip(moves, offsets)):
        span = source_span(root, it)
        if data[off:off + len(span)] != span:
            notes.append("MOVE FAILED %s: the bytes at offset %d of %s are NOT the %d bytes read "
                         "from %s:%d. The entry stays hot."
                         % (it.label, off, rel, len(span), it.rel, it.line))
            failed.add(idx)
            continue
        if it.ident is None:
            notes.append("%s (%s:%d) has no citemap-resolvable id, so `recall get` cannot "
                         "address it. Its bytes were verified by raw slice only."
                         % (it.label, it.rel, it.line))
            continue
        sites = by_id.get(it.ident, ())
        here = [e for e in sites if e["byte_start"] == off]
        if not here:
            notes.append("MOVE FAILED %s: written at offset %d of %s but heading_map does not "
                         "resolve it there -- `get` would not return it." % (it.ident, off, rel))
            failed.add(idx)
            continue
        got = data[here[0]["byte_start"]:here[0]["byte_end"]]
        if not _payload_ends_at(got, 0, span):
            notes.append("MOVE FAILED %s: the span `get` would slice from %s does not begin with "
                         "the span moved out of %s:%d and end there (%d B vs %d B, and what "
                         "follows is not another entry)."
                         % (it.ident, rel, it.rel, it.line, len(got), len(span)))
            failed.add(idx)
            continue
        if len(sites) > 1:
            notes.append("%s now resolves at %d sites in %s, so `recall get %s` will exit "
                         "AMBIGUOUS (4) until one is pinned with --file."
                         % (it.ident, len(sites), rel, it.ident))
    return failed


# Test seam. `None` in normal operation. `--selftest` sets it to a callable that damages one
# payload ON ITS WAY TO THE DESTINATION -- the source is left alone, so R-H's two-sided compare
# has something real to catch. This is how the abort path is MEASURED rather than asserted.
_CORRUPT_HOOK = None


def _payload_of(root, it):
    span = source_span(root, it)
    return _CORRUPT_HOOK(it, span) if _CORRUPT_HOOK is not None else span


def _dest_spans(path, rel, families):
    """{id: [(byte_start, byte_end, span_bytes)]} for an EXISTING destination. `{}` when absent.

    SPANS, not just ids. The predecessor returned ids and `screen_batch` then asked whether the hot
    payload was `in` the destination's WHOLE TEXT -- a SUBSTRING test standing in for the
    byte-equality one its own note claimed to have made. MEASURED 2026-08-03 by the orchestrator,
    at exit 0: an archived `M4` carrying TWO paragraphs and a hot `M4` whose last paragraph the
    author had DELETED this session satisfied `payload in existing`, so the hot copy was spliced
    out of `backlog.md`, NOTHING was appended, and `recall get M4` then returned 183 bytes still
    containing the deleted paragraph. The author's edit was silently reverted and the receipt said
    the move succeeded. A verbatim FENCED quote of the hot entry, sitting under some OTHER entry in
    the destination, makes the same test true with an unrelated older body as the survivor.

    The destination's span FOR THAT ID is the only thing that can settle it -- and it is the same
    span `recall get` will slice, which is what makes the comparison mean what the note says.
    """
    if not path.is_file():
        return {}
    data, rows, _open = recall.load_doc(path)
    out = {}
    for e in recall.heading_map(rel, data, rows, families):
        out.setdefault(e["id"], []).append(
            (e["byte_start"], e["byte_end"], data[e["byte_start"]:e["byte_end"]]))
    return out


def _corpus_dest_index(root, families):
    """({id: [(rel, byte_start, byte_end)]}, {rel: (data, [entry_start])}) over EVERY existing
    destination. ONE walk, built ONCE PER RUN.

    F2, MEASURED 2026-08-07. `_dest_spans` is built per destination and `screen_batch`'s `seen` is
    `set(existing_spans)`, so the already/conflict guard's ENTIRE UNIVERSE is the one archive the
    firing row happens to route to. An id already defined in a DIFFERENT archive is invisible to
    it. The measured shape is the documented recovery: an entry sits in `_archive/backlog-deferred.md`
    from an earlier save, someone edits its MARKER (`gate:` -> `done: save#5`) while recovering, so
    row 2 now routes it to `_archive/backlog-done.md`, where the id has zero sites -- plain append,
    exit 0, no REFUSED, no WARNINGS, the old copy untouched, and `recall get` then exits 4.
    Proven to be the UNIVERSE and not the file's absence: with the routed-to destination ALREADY
    EXISTING, so `_dest_spans` really ran over it, the outcome was byte-identical.

    THAT END STATE IS THE ONE THIS TOOL CANNOT WALK BACK. Both definitions are under `_archive/`,
    the drain's SOURCES exclude `_archive/`, and so no later run can even see the pair.

    THE SECOND HALF IS THE SAME QUESTION FOR AN ENTRY WITH NO ID. `heading_map` drops identless
    headings, so the id index cannot answer "is this entry also sitting in some other archive?"
    when there is no id to ask about. Bytes are the only identity such an entry has, so the per-
    file entry starts (and the file's bytes, so the caller need not re-read them) travel alongside.
    The two together are what lets ONE rule -- "reconcile only when this is the archive set's ONLY
    copy" -- cover both kinds of item instead of one kind plus a special case.

    THE READ IS ALREADY PAID FOR: `integrity_scope` opens every existing destination on the same
    run for the open-fence gate. This walk adds no file the run was not going to open anyway, and
    it is ONE walk for both indexes rather than two over the same bytes.
    """
    by_id = {}
    by_rel = {}
    for rel in ALL_DESTINATIONS:
        path = root / rel
        if not path.is_file():
            continue
        data, rows, _open = recall.load_doc(path)
        for e in recall.heading_map(rel, data, rows, families):
            by_id.setdefault(e["id"], []).append((rel, e["byte_start"], e["byte_end"]))
        by_rel[rel] = (data, [b for (_ln, lv, _r, b) in _headings(rows) if lv >= 2])
    return by_id, by_rel


def _not_moved(it, dest):
    """The closing sentence every refusal shares, saying only what a refusal can KNOW.

    P4, MEASURED 2026-08-07. Every one of these messages used to end "NOT MOVED: the entry stays
    HOT and nothing in <dest> was touched." The first clause is this item's own fact and is true.
    The second is a claim about the WHOLE RUN, and it is FALSE whenever another item in the same
    batch appends to the same destination -- which the receipt says three lines above the warning.
    A refusal knows what happened to ITS entry; it does not know what the batch did. So the claim
    is narrowed to the one thing this branch decided, and the reader is pointed at the receipt for
    the rest.
    """
    return ("NOT MOVED: %s stays HOT (%s:%d, %d B) and NOTHING WAS APPENDED FOR IT. Other items "
            "in this run may still have appended to %s -- the SOURCES block above is the record "
            "of what was." % (_receipt_label(it), it.rel, it.line, it.nbytes, dest))


def _cross_dest_why(it, dest, sites):
    """R-I ACROSS FILES (F2). The id is defined in a DIFFERENT archive from the one row `it.row`
    routes to, and this run wants to APPEND it here.

    THIS DOES NOT REUSE `_conflict_why`, deliberately. That message hardcodes the single `dest` in
    several places -- "ALREADY DEFINED in %s", "nothing in %s was touched" -- and on a cross-file
    conflict every one of them would name the WRONG FILE: the copy is not in `dest`, it is in the
    archive `dest` is about to become a second home to. So this one names BOTH.
    """
    where = "; ".join("%s bytes %d-%d (%d B)" % (rel, s, e, e - s) for (rel, s, e) in sites)
    return ("CROSS-FILE CONFLICT (R-I): %s is ALREADY DEFINED IN ANOTHER ARCHIVE -- %s -- while "
            "row %s routes it to %s, where the id has NO site at all and no copy of these bytes. "
            "The per-destination already/conflict guard cannot see that: its universe is %s alone. "
            "Appending here would leave ONE id defined in TWO archives -- `recall get %s` "
            "exits AMBIGUOUS (4), `check` reports a collision -- and NEITHER copy would be hot "
            "afterwards, so no later run of this tool could reconcile them: the drain's SOURCES "
            "exclude `_archive/`. %s If a MARKER was edited during a recovery, delete the stale "
            "copy in %s first, then re-run."
            % (it.ident, where, it.row, dest, dest, it.ident, _not_moved(it, dest), sites[0][0]))


def _carried_labels(it):
    """The printable ids of the nested headings riding inside this item's move span. F3."""
    return [(c[0] if c[0] else "~" + c[1]) for c in it.carried]


def _carried_why(it, dest, hits):
    """R-I FOR A CARRIED CHILD (F3), ON THE APPEND PATH ONLY. The parent may move; a heading nested
    inside its span may not land where its id is already defined.

    THE CHILD IS NOT AN ITEM AND IS NOT BEING MADE ONE. It has no row, no receipt line of its own
    and no independent move. What it has is BYTES inside the parent's move span -- so the parent
    and everything under it are screened, and refused, AS A UNIT. Refusing the parent is the only
    lever there is: the child cannot be left behind without tearing the entry, which is the C-4
    deletion `clamp_span` exists to rule out.

    P7, MEASURED 2026-08-07. This message says "appending the parent would put a SECOND definition
    of that id into an archive", and as shipped it also fired on the RECONCILE path, where nothing
    is appended at all -- so the sentence was exactly inverted for the shape that reached it most
    often. That was a PLACEMENT fault, not a wording one: the screen sat above the already-branch.
    The sequence in `screen_batch` now reaches this function only where an append really is the
    decision, so the sentence is true by construction. `_reconcile_why` is the other half.
    """
    where = "; ".join(
        "%s (%s:%d, level %d) is already defined at %s"
        % (c[0], it.rel, c[3], c[2],
           ", ".join("%s bytes %d-%d (%d B)" % (r, s, e, e - s) for (r, s, e) in sites))
        for (c, sites) in hits)
    return ("CARRIED-CHILD CONFLICT (R-I): %s's move span carries %d nested heading(s) whose id is "
            "already defined in the archive set -- %s. A nested heading is NOT a separate entry "
            "here: it has no row and no move of its own, but its bytes travel inside %s's span, so "
            "appending the parent would put a SECOND definition of that id into an archive. "
            "`recall get` would then exit AMBIGUOUS (4) and `check` would report the "
            "collision -- and the drain's SOURCES exclude `_archive/`, so no later run of this "
            "tool could see the pair, let alone reconcile it. %s Reconcile the duplicate "
            "definition by hand, then re-run."
            % (_receipt_label(it), len(hits), where, _receipt_label(it), _not_moved(it, dest)))


def _reconcile_why(it, dest, off, reason):
    """THE OTHER HALF OF THE R-I SCREEN: a byte-identical copy IS at `dest`, so this run could
    finish an interrupted move -- and may not, because finishing it would delete the last copy
    anybody can still reach.

    THE HAZARD IS THE OPPOSITE ONE, which is why this is not `_conflict_why` or `_carried_why`
    with a word changed. Those refuse an APPEND, whose cost is a SECOND definition. This refuses a
    SPLICE, whose cost is that the ambiguity already in the archive set becomes permanent: the
    drain's SOURCES exclude `_archive/`, so once the hot copy is gone no run of this tool can see
    the pair, and a human editing an archive by hand is the only remaining repair. Telling the
    operator "appending would duplicate the id" here would be false in both halves -- nothing would
    be appended, and the duplicate already exists.
    """
    return ("RECONCILE REFUSED (R-I): a BYTE-IDENTICAL copy of %s is already at %s byte %d, so an "
            "earlier run wrote the destination and did not reach the source -- this run could "
            "finish that move by splicing %s:%d. It does not, because %s. Splicing now would "
            "delete the ONLY copy this tool can still read (its SOURCES exclude `_archive/`), "
            "leaving a duplicate no later run can even see. %s Reconcile the duplicate definition "
            "by hand -- the hot copy and the archived one are the same bytes, so deleting either "
            "loses nothing -- then re-run."
            % (_receipt_label(it), dest, off, it.rel, it.line, reason, _not_moved(it, dest)))


def _conflict_why(it, dest, payload, sites, same):
    """R-I (example-project's ruling, overridable). The destination already defines this id with bytes that
    are not these, or at more than one site, and the move cannot proceed. `same` is the subset of
    `sites` whose bytes DO match the hot payload and end there.

    All three other branches cost content. APPENDING gives the id another definition, so
    `recall get` exits AMBIGUOUS (4) and `recall check` reports a collision. OVERWRITING
    destroys a version nothing asked this tool to destroy. SPLICING THE SOURCE ANYWAY -- what the
    substring test did -- deletes the author's current text in favour of an older one. And the tool
    cannot break the tie: row 5 is REVERSIBLE (spec S5.1), so defer -> promote back -> edit ->
    re-defer is the DESIGNED workflow and "the hot copy is the newer one" is a guess, not a fact.
    Leaving it HOT costs bytes. The standing rule for this tool is BIAS TOWARD NOT MOVING.

    F5. `screen_batch` picks the multi-site branch on an ARITY test, and this message then asserted
    "with DIFFERENT bytes" WHATEVER the bytes were -- false as soon as one of two sites matched. So
    the message takes `same` and states which condition actually held. MEASURED 2026-08-07 by
    `_st_arms_conflict_arity` case (1): a destination holding TWO sites for one id, both
    byte-identical to a 96 B hot payload, prints `bytes 33-130 (97 B); bytes 130-226 (96 B)` with
    `len(same) == 2`. The 1 B difference is the separator newline that falls inside the FIRST
    site's `heading_map` span and outside the last one's, so printing both counts made the
    separator look like evidence of a difference in content. (The 139 B / 138 B figures this
    paragraph used to quote were produced by nothing at all; they are deleted rather than
    re-derived.)

    THE PREDICATE IS UNCHANGED, and P2 is the reason the old sentence for that could not stand: it
    said relaxing the arity test "would append into a file that already holds two definitions of
    the id", and relaxing it appends NOTHING -- it routes to the RECONCILE path, whose whole action
    is splicing the hot copy. The real cost is worse than the one that was claimed. Splicing here
    deletes the last copy the drain can read into an ambiguity that is already present in the
    destination, and the drain's SOURCES exclude `_archive/`, so nothing could ever walk it back.
    P3 is the same correction inside the message itself: it used to say a byte-for-byte match meant
    "no single site can be the interrupted run's copy", which contradicts this function's own
    definition of that signature. One of them may very well BE that copy. It is the SPLICE that is
    refused, not the identification.

    The warning has to be ACTIONABLE, so it names the id, EVERY site and BOTH byte counts.
    """
    where = "; ".join("%s bytes %d-%d (%d B)" % (dest, s[0], s[1], s[1] - s[0]) for s in sites)
    if len(sites) == 1:
        head = ("CONFLICT (R-I): %s is ALREADY DEFINED in %s with DIFFERENT bytes -- %s, versus "
                "%d B at %s:%d. This tool cannot know which of the two you want -- appending "
                "makes `recall get %s` exit AMBIGUOUS (4), overwriting destroys a version "
                "nobody asked it to destroy, and splicing the source anyway would revert an edit "
                "you made this session."
                % (it.ident, dest, where, len(payload), it.rel, it.line, it.ident))
    else:
        head = ("CONFLICT (R-I): %s is DEFINED AT %d SITES in %s -- %s, versus %d B at %s:%d. %s "
                "The id is ambiguous IN THE DESTINATION ALONE, before this run touches anything. "
                "Splicing the source to complete a move would delete the last copy the drain can "
                "read -- its SOURCES exclude `_archive/` -- and leave the ambiguity behind with "
                "nothing left to reconcile it from."
                % (it.ident, len(sites), dest, where, len(payload), it.rel, it.line,
                   ("%d of those sites %s the hot bytes BYTE FOR BYTE, so one of them may well BE "
                    "an interrupted run's copy -- which changes nothing here."
                    % (len(same), "matches" if len(same) == 1 else "match")) if same
                   else "NONE of those sites matches the hot bytes."))
    return head + (" %s Reconcile the definitions by hand, then re-run." % _not_moved(it, dest))


def _superset_why(it, dest, payload, offs):
    """R-I's refusal for the case where the destination's copy BEGINS with the payload and holds
    MORE, and there is no id for the ordinary conflict message to hang on.

    The message states the ambiguity rather than resolving it, because the bytes genuinely do not
    resolve it: a nested heading after the payload is EITHER a child the author deleted this
    session OR a deeper entry appended afterwards, and nothing in the destination distinguishes
    them. `kind:` does not -- MEASURED 2026-08-07: fixture `seq/B3`'s carried child declares
    `kind: plan` exactly as `seq/B4`'s independently-appended level-three entry does. So the tool
    declines and says so, which is D74's ratified bias toward NOT moving and R-I's own wording:
    "The tool cannot know which the author wants, so it declines and says so."
    """
    return ("SUPERSET (R-I): %s at %s:%d already sits in %s at %s, but the copy there CARRIES MORE "
            "than these %d B -- trailing prose, or a heading nested deeper than the entry's own. "
            "Those are two states the bytes cannot tell apart: a child you deleted this session, "
            "or a deeper entry appended after this one. Appending would make a SECOND copy; "
            "splicing would revert whichever of the two it is. %s Reconcile the two copies by "
            "hand, then re-run."
            % (it.label, it.rel, it.line, dest, ", ".join("byte %d" % o for o in offs),
               len(payload), _not_moved(it, dest)))


def _dest_entry_starts(path):
    """The byte offset of EVERY level>=2 heading in an EXISTING destination, id-bearing or not.
    `[]` when the file is absent. These are the only offsets at which an appended entry can sit,
    because every payload this tool writes BEGINS with its own heading.

    `_dest_spans` cannot answer for an IDENTLESS entry, and not by oversight: it is built on
    `recall.heading_map`, whose `if h["id"] is None: continue` drops exactly those headings --
    the same drop `scan_source`'s own docstring calls out as the reason this tool walks headings
    itself. So the walk here is `_headings`, which skips in-fence rows: a verbatim quote of an
    entry inside a code block is not a heading here and cannot be mistaken for a definition. That
    is the shape ruling R-I was written to kill.

    OFFSETS, NOT SPANS, and that is a CORRECTION (B4, MEASURED 2026-08-07, fixture
    `_st_arms_sequence`, B4). The predecessor returned each heading's MOVE span -- to the next
    heading of the same or higher level -- and the caller compared that span with the hot payload.
    A span is a property of the file AS IT IS NOW, not of the entry as it was appended: let a
    DEEPER heading be appended after it and the earlier entry's span SWALLOWS it, so the comparison
    fails on an entry whose bytes are sitting there untouched. Measured, the identless path then
    fell through to the unconditional append F4 exists to remove (copies 1 -> 2) and the identified
    path -- whose spans come from `heading_map` under the SAME rule -- refused the recovery for
    ever at exit 0. `_payload_ends_at` replaces the span test with one that does not depend on what
    was appended later.
    """
    if not path.is_file():
        return []
    _data, rows, _open = recall.load_doc(path)
    return [bstart for (_lineno, level, _rest, bstart) in _headings(rows) if level >= 2]


def _payload_ends_at(existing, off, payload):
    """True when `payload` sits at `off` in `existing` AND ENDS THERE. B4's replacement for span
    equality, and the only one of the two that is stable under a later append.

    "Ends there" means what follows the payload is nothing but whitespace, or the start of ANOTHER
    ENTRY -- a column-0 ATX heading NO DEEPER than the payload's own opening heading. It may NOT be
    more prose, and it may NOT be a DEEPER heading, and those two exclusions are ONE fact: each
    means the destination's copy carries content this payload does not, so the destination is a
    strict SUPERSET and the two differ. Prose after the payload is the round-trip shape
    `_dest_spans` documents, where the archive still holds a paragraph the author deleted this
    session. A DEEPER heading is that same fact wearing a heading's clothes -- a nested child,
    carried out with its ancestor, that the author has since deleted. Calling EITHER "already
    there" deletes the hot copy at exit 0 and leaves `get` answering with the removed content:
    the measured 2026-08-03 defect the substring test caused, and precisely what ruling R-I exists
    to REFUSE. A heading at the payload's own level or shallower is the opposite fact -- it is
    where the NEXT append started -- and a plain prefix test cannot tell any of these apart, which
    is why this is not a prefix test.

    DEPTH, not a fixed level, is the test, because it is the entry's OWN heading that defines what
    "inside it" means: a heading at the same level or shallower is where the next entry began; a
    heading deeper than the payload's opening is BY CONSTRUCTION inside the payload's own entry.
    Hard-coding level 2 would answer the same on today's `## <id>` corpus and silently mis-answer
    a deeper-rooted one. A payload that does not OPEN with a heading gives depth nothing to measure
    against, so it refuses -- D74's standing bias toward NOT moving.

    The fence question does not arise: every offset the callers pass came from `_headings` or
    `heading_map`, and both skip in-fence rows.
    """
    if existing[off:off + len(payload)] != payload:
        return False
    own = citemap.HEADING_RE.match(
        payload.split(b"\n", 1)[0].rstrip(b"\r").decode("utf-8", "replace"))
    if own is None:
        return False
    own_level = len(own.group(1))
    for line in existing[off + len(payload):].split(b"\n"):
        line = line.rstrip(b"\r")
        if not line.strip():
            continue
        m = citemap.HEADING_RE.match(line.decode("utf-8", "replace"))
        return m is not None and len(m.group(1)) <= own_level
    return True                                   # nothing but whitespace between here and EOF


def _superset_offsets(existing, sites, entry_starts, payload, ident):
    """Every offset where the destination's copy BEGINS with this payload and CARRIES MORE.

    The exact complement of `_already_offsets` over the same candidates: the bytes match at the
    offset, but what follows is prose, or a heading DEEPER than the payload's own -- content this
    payload does not hold. Either way the destination is a strict SUPERSET, which is R-I's
    "ALREADY DEFINED with DIFFERENT bytes" arriving from the side where there is no id to notice.

    An IDENTIFIED item mostly reaches R-I through `sites`. An IDENTLESS one has no key at all, so
    before this existed the batch fell straight through STEP 4 to the unconditional append and made
    a SECOND copy at exit 0 -- MEASURED 2026-08-07, fixture `seq/B4 identless`. Tightening
    `_payload_ends_at` without this arm trades a splice for a duplicate, which is not a fix.
    """
    cands = [s[0] for s in sites] if ident else list(entry_starts)
    return [off for off in cands
            if existing[off:off + len(payload)] == payload
            and not _payload_ends_at(existing, off, payload)]


def _already_offsets(existing, sites, entry_starts, payload, ident):
    """Every offset in the destination where THIS payload already sits, whole and ended.

    ONE test for both kinds of item; only the candidate offsets differ. An item WITH an id is
    looked up by id (`_dest_spans`' sites), because that is the span `recall get` will slice
    and the comparison has to mean what the receipt says it means. An item with NO id has no key to
    look one up with, so every level>=2 heading in the file is a candidate and the bytes decide.

    A LIST, not a first hit. Two identical copies in ONE destination is a real state -- it is what
    an unfixed duplicate looks like -- and the caller has to be able to tell one from two, because
    completing a move INTO an ambiguity is not a recovery.
    """
    cands = [s[0] for s in sites] if ident else list(entry_starts)
    return [off for off in cands if _payload_ends_at(existing, off, payload)]



def _reconcile_unsafe(it, dest, off, payload, elsewhere, elsewhere_entries):
    """`""` when finishing this interrupted move is safe, else the reason it is not. STEP 3.

    ONE QUESTION, ASKED CORPUS-WIDE: is the copy at `dest` the archive set's ONLY definition of
    this entry, and of every id riding inside its span? Splicing removes the hot copy, and the
    drain's SOURCES exclude `_archive/`, so the hot copy is the LAST one this tool can read.
    Where the answer is yes, the splice is provably lossless -- the archived bytes are identical to
    the hot bytes, so the content survives verbatim at a known address, and the corpus goes from
    two copies to one. Where the answer is no, the splice converts a duplicate that a human or a
    later run could still fix into one that nothing can see. That asymmetry is the whole rule.

    IDENTITY IS WHATEVER THE ENTRY HAS. An id where there is one -- `elsewhere`, the corpus id
    index. BYTES where there is not -- `elsewhere_entries`, the corpus entry-start index -- because
    `heading_map` drops identless headings and an entry with no id still has bytes that can sit in
    two archives at once. `recall` is structurally blind to that second case (no id, nothing to
    collide), which is a reason to be stricter here, not a licence to skip the check.

    CARRIED CHILDREN ARE PART OF THE PARENT'S IDENTITY HERE. B1 was stated about the parent's id,
    but a splice that leaves a CHILD's id in two archives is unwalkbackable in exactly the same
    way, and the child has no row of its own to refuse. So each child id must resolve at EXACTLY
    ONE site, and that site must lie INSIDE the matched span at `dest` -- i.e. it must be the
    definition that rode in with this very payload. Zero sites is also a refusal: it means the
    archived copy is not serving as the child's definition, so this run cannot claim the child is
    already safely filed.
    """
    def _where(sites):
        return "; ".join("%s byte %d" % (s[0], s[1]) for s in sites)

    if it.ident:
        sites = list(elsewhere.get(it.ident, ()))
        strays = [s for s in sites if not (s[0] == dest and s[1] == off)]
        if strays:
            return ("%s is ALSO defined elsewhere in the archive set (%s), so the id is ambiguous "
                    "ACROSS ARCHIVES before this run touches anything" % (it.ident, _where(strays)))
    else:
        strays = []
        for rel in sorted(elsewhere_entries):
            data, starts = elsewhere_entries[rel]
            for o in starts:
                if rel == dest and o == off:
                    continue
                if _payload_ends_at(data, o, payload):
                    strays.append((rel, o))
        if strays:
            return ("a BYTE-IDENTICAL copy of this entry also sits at %s, and it has no id for "
                    "`recall check` to notice, so nothing downstream would report the pair"
                    % _where(strays))

    for c in it.carried:
        cid = c[0]
        if not cid:
            continue
        csites = list(elsewhere.get(cid, ()))
        inside = [s for s in csites if s[0] == dest and off <= s[1] < off + len(payload)]
        if len(csites) != 1 or not inside:
            return ("the nested heading %s (%s:%d, level %d) that travels inside this entry's span "
                    "resolves at %d site(s) in the archive set (%s) instead of exactly one inside "
                    "the copy at %s byte %d"
                    % (cid, it.rel, c[3], c[2], len(csites), _where(csites) or "none", dest, off))
    return ""


def _held_why(it, dest):
    """STEP 4a. The entry was HELD upstream (row 5's gate contract, D81) and is in this batch only
    to FINISH a move -- and there turns out to be no move to finish.

    THE INVARIANT MADE EXECUTABLE. `run` admits a held entry only when a byte-identical copy is
    already at the destination, so in a normal run STEP 2 finds it and this branch is not reached.
    It exists because the property it guards -- a held entry may complete a move and may never
    start one -- is the whole reason holding and reconciling can coexist, and a property that lives
    only in a comment is one a later edit can lose without failing anything.
    """
    return ("HELD, AND NOTHING TO RECONCILE: row %s fired for %s but the entry is HELD (its "
            "declaration is not one this tool may file), so it may FINISH a move an earlier run "
            "began and may never START one. No byte-identical copy of it is in %s, so there is no "
            "earlier move to finish. %s Fix the declaration and the next save moves it."
            % (it.row, _receipt_label(it), dest, _not_moved(it, dest)))


def screen_batch(root, dest, batch, existing, existing_spans, entry_starts,
                 elsewhere, elsewhere_entries, notes):
    """(append, already, refused). What may be APPENDED to `dest`, what may only be RECONCILED
    with it, and what may be neither.

    `already` items carry their destination OFFSET, because the caller must hand them to
    `_verify_moves` and there is no builder offset for a write that never happens.

    ======================== THE DECISION SEQUENCE, AND IT IS THE DESIGN ========================
    This function is NOT a set of independent guards. It is ONE ORDERED PRECEDENCE, and in an
    ordered precedence PLACEMENT IS SEMANTICS. Five separately-correct guards were inserted here as
    independent tests on 2026-08-07 and all five defects that produced were PLACEMENT defects, not
    logic defects. So the order is stated ONCE, here, with the reason each step precedes the next,
    and the code below is that order and nothing else.

    THE IDEA THE ORDER COMES FROM: two DIFFERENT ACTIONS are on the table, with OPPOSITE hazards.

      APPEND    creates a NEW definition in an archive. Its hazard is "does a definition of this id
                -- or of anything this entry carries -- ALREADY exist anywhere in the archive set?"
                Any pre-existing definition turns the append into an ambiguity.
      RECONCILE creates NOTHING. It deletes the HOT copy of an entry whose byte-identical twin is
                already at the destination, finishing a move an earlier run began and did not
                complete. Its hazard is the mirror image: "after this deletion, is the entry still
                resolvable at exactly ONE site?" The drain's SOURCES exclude `_archive/`, so the
                hot copy is the LAST copy this tool can read, and deleting it is the step nothing
                walks back.

    A screen written for one hazard says nothing about the other. Apply APPEND's screens to a
    RECONCILE and a recovery is refused for a reason that does not apply to it, stranding for ever
    a duplicate THIS TOOL CREATED. Skip RECONCILE's screens because APPEND's already ran and hot
    bytes are deleted into an ambiguity. Hence:

      STEP 1  IS THE MOVE ADMISSIBLE AT ALL?  Decided upstream in `decide`. A row that fires and is
              then HELD (row 5's gate contract, D81) reaches here with `it.reconcile_only` set,
              which means: this entry may FINISH a move and may not START one. A refusal to DEFER
              is not a refusal to RECONCILE -- the duplicate an earlier run left is not evidence
              about today's gate, and holding it hot as well as archived is not a bias toward not
              moving, it is a bias toward keeping two copies. FIRST, because it is the only
              question whose answer removes an OPTION rather than choosing between options.
      STEP 2  IS A COPY ALREADY HERE?  `_already_offsets`: the payload sitting whole at a heading
              start in the destination's PRE-RUN bytes, and ENDING there. A pure lookup with no
              verdict in it. Before step 3 because "is finishing this move safe?" cannot be asked
              until you know there is a move to finish. Before step 4 because a candidate makes
              APPEND the WRONG action rather than a risky one: appending on top of a byte-identical
              copy is the duplicate this whole block exists to stop.
      STEP 3  IF THERE IS A CANDIDATE, MAY THE MOVE BE FINISHED?  `_reconcile_unsafe`, asked
              corpus-wide and over the CARRIED CHILDREN as well as the parent. Reconcile only when
              this destination's copy is the archive set's ONLY definition of the entry and of
              every id riding inside it; otherwise the hot copy stays, because it is the only lever
              left. This step did not exist before, and its absence is why the one branch that
              DELETES HOT BYTES was the one branch with no corpus-wide screen.
      STEP 4  OTHERWISE, MAY THIS BE APPENDED?  The append hazard, most specific fact first:
              4a  held by step 1                                   -> refuse (`_held_why`)
              4b  the id is in THIS destination, or in an earlier append of this batch
              4c  the id is in ANOTHER destination                 -> `_cross_dest_why`
              4d  a CARRIED CHILD's id is anywhere in the archive   -> `_carried_why`
              4e  none of the above                                -> APPEND
              4b before 4c is a MESSAGE choice, not a verdict choice: both refuse, and when the id
              is present in THIS destination the per-destination messages are the accurate ones.
              4d last because the parent's own conflict is the more specific fact and a reader
              should be told that one first.

    WHAT THE ORDER DOES NOT CHANGE: THE SET OF ENTRIES THAT GET APPENDED. Every path to `append` is
    reached under the same conditions as before or under strictly narrower ones; steps 1 and 3 can
    only take options away. D74's bias lives on the append side, because that is where new content
    enters an archive, and it is untouched. What steps 1 and 3 decide is whether a run ends with
    the corpus holding ONE copy of an already-archived entry or TWO.

    THE DEFECTS THIS FUNCTION CLOSES, all MEASURED and all ending at exit 0.

    (a) RE-RUN AFTER A PARTIAL FAILURE (2026-08-03). `perform`'s ordering is deliberate and correct
        -- the destination is written and verified BEFORE the source is spliced -- so an I/O
        failure between the two leaves the entry in BOTH places, a duplicate rather than a
        deletion. The documented recovery is to re-run. Measured, the re-run APPENDED A SECOND
        COPY: `recall get M12` then exited 4 (AMBIGUOUS). C-4 says an eviction degrades to a
        RESOLVABLE stub, and two copies is not resolvable. STEP 2 is that recognition; STEP 3 is
        what makes acting on it safe.

    (b) ID COLLISION (2026-08-03). `_verify_moves` saw a collision (`len(sites) > 1`) and
        downgraded it to a note -- after the append, with the source already spliced. Now the entry
        STAYS HOT and the run says why (STEP 4b, and STEP 3 for the same fact seen from the
        recovery side).

    (c) THE SUBSTRING TEST THAT STOOD IN FOR (a) (2026-08-03). Recognising the first copy was
        decided by `payload in existing`, against the destination's whole text. See `_dest_spans`
        for the two shapes that makes true when the bytes are NOT the same. Only bytes that sit
        whole at an entry start AND END THERE are the interrupted-run case -- `_payload_ends_at`.

    (d) THE IDENTLESS BYPASS (F4, 2026-08-07). `it.ident and it.ident in seen` SHORT-CIRCUITS on an
        item with no citemap-resolvable id, so control fell through to an unconditional append --
        and `_verify_moves` skips its id-resolution check for the same items, so nothing downstream
        noticed either. Copies went 1 -> 2 -> 3 across three re-runs, receipt constant at `1 entry
        137 B`, exit 0 throughout, and `recall check` CANNOT see it: an entry with no id has no
        id to collide. STEP 2 keys such an item on its BYTES, and STEP 3 asks the uniqueness
        question on its bytes too, corpus-wide. One rule, two notions of identity, no special case.

    (e) THE ONE-ARCHIVE UNIVERSE (F2, 2026-08-07). `seen` is `set(existing_spans)` and
        `existing_spans` covers ONE destination, so (a)-(d) were blind to an id defined in a
        DIFFERENT archive -- the state a MARKER edited mid-recovery produces. `elsewhere` is the
        corpus-wide id index and `elsewhere_entries` the corpus-wide byte index (one walk per run,
        `_corpus_dest_index`). They are consulted for TWO purposes, because there are two actions:
        STEP 3 asks them "may this splice happen?" and STEP 4c/4d ask them "may this append
        happen?". The shipped docstring claimed EXACTLY ONE purpose while the code already had two,
        and the purpose it did not name was the branch that deletes hot bytes.

    (f) THE SPAN THAT MOVED UNDER THE TEST (B4, 2026-08-07). Both already-tests compared the hot
        payload with the destination entry's SPAN, and a span ends at the next heading of the same
        or higher level -- a property of the file AS IT IS NOW. Append a DEEPER heading afterwards
        and the earlier entry's span swallows it, so the test missed on an entry sitting there
        untouched: the identless path fell through to the unconditional append (copies 1 -> 2) and
        the identified path refused the recovery for ever. `_payload_ends_at` asks a question that
        later appends cannot change.

    `seen` accumulates within the batch, so the second of two same-id items in one source is
    refused against the first one's append rather than against the file as it was before the run.
    It cannot do that job for identless items -- they have no key -- so two IDENTICAL identless
    entries in ONE source still both append, exactly as before. That is the same-run duplicate, not
    the cross-run one. STEP 2's universe is deliberately the PRE-RUN bytes, so nothing this batch
    appends can ever be mistaken by a later item for an earlier run's copy.
    """
    append, already, refused = [], [], []
    seen = set(existing_spans)
    for (it, _d) in batch:
        payload = source_span(root, it)
        sites = (existing_spans.get(it.ident) or []) if it.ident else []

        # ---- STEP 2: is a copy of these exact bytes already at this destination? --------------
        here = _already_offsets(existing, sites, entry_starts, payload, it.ident)

        if here:
            # ---- STEP 3: it is a RECOVERY. May it be completed? ------------------------------
            if it.ident and len(sites) > 1:
                # The id is ambiguous IN THIS DESTINATION ALONE. `here` is the byte-matching
                # subset, and F5's message is the one that states which condition held.
                refused.append((it, dest, _conflict_why(it, dest, payload, sites, here)))
                continue
            if len(here) > 1:
                refused.append((it, dest, _reconcile_why(
                    it, dest, here[0],
                    "%s already holds %d byte-identical copies of it, so completing the move would "
                    "leave the duplicate standing rather than resolve it" % (dest, len(here)))))
                continue
            why = _reconcile_unsafe(it, dest, here[0], payload, elsewhere, elsewhere_entries)
            if why:
                refused.append((it, dest, _reconcile_why(it, dest, here[0], why)))
                continue
            already.append((it, dest, here[0]))
            continue

        # ---- STEP 4: nothing to finish, so the question is whether to APPEND. -----------------
        if it.reconcile_only:                                              # 4a
            refused.append((it, dest, _held_why(it, dest)))
            continue
        if it.ident and it.ident in seen:                                  # 4b
            if sites:
                # BYTES that do not match: `here` is empty, so no site both matches and ends here.
                refused.append((it, dest, _conflict_why(it, dest, payload, sites, [])))
            else:
                refused.append((it, dest, "appending it would give %s a SECOND definition of %s, "
                                          "and `recall get %s` then exits AMBIGUOUS (4). C-4 "
                                          "needs a resolvable stub, not two."
                                          % (dest, it.ident, it.ident)))
            continue
        other = [s for s in elsewhere.get(it.ident, ()) if s[0] != dest] if it.ident else []
        if other:                                                          # 4c
            refused.append((it, dest, _cross_dest_why(it, dest, other)))
            continue
        hits = [(c, elsewhere[c[0]]) for c in it.carried if c[0] and c[0] in elsewhere]
        if hits:                                                           # 4d
            refused.append((it, dest, _carried_why(it, dest, hits)))
            continue
        sup = _superset_offsets(existing, sites, entry_starts, payload, it.ident)
        if sup:                                                            # 4d'
            refused.append((it, dest, _superset_why(it, dest, payload, sup)))
            continue
        if it.ident:                                                       # 4e
            seen.add(it.ident)
        append.append((it, dest))
    return append, already, refused


def perform(root, plan, families, notes, dry_run):
    """Execute the whole batch. Returns (moved, failed, refused).

    ORDER IS THE SAFETY PROPERTY. The destination is written and verified FIRST, and a source is
    rewritten only with spans that passed. If anything goes wrong between the two writes the entry
    exists in BOTH places -- a duplicate -- and never in NEITHER, which is the C-4 deletion this
    whole file exists to rule out. `screen_batch` is what makes that duplicate SURVIVABLE: the next
    run recognises it instead of making a third copy.
    """
    by_dest = {}
    for (it, dest) in plan:
        by_dest.setdefault(dest, []).append((it, dest))

    # ONCE PER RUN, not once per destination -- see `_corpus_dest_index`. Built before the loop
    # so every batch is screened against the SAME pre-run picture of the archive set. Two indexes,
    # one walk: ids for the entries that have one, entry-start offsets for the ones that do not.
    elsewhere, elsewhere_entries = _corpus_dest_index(root, families)

    moved, failed, refused = [], [], []
    for dest in ALL_DESTINATIONS:              # deterministic order, not dict order
        batch = by_dest.get(dest)
        if not batch:
            continue
        path = root / dest
        created = not path.exists()
        existing = dest_header(dest) if created else path.read_bytes()
        existing_spans = {} if created else _dest_spans(path, dest, families)
        entry_starts = [] if created else _dest_entry_starts(path)

        batch, already, blocked = screen_batch(root, dest, batch, existing, existing_spans,
                                               entry_starts, elsewhere, elsewhere_entries, notes)
        refused.extend(blocked)
        if dry_run:
            moved.extend([(it, d) for (it, d, _off) in already])
            moved.extend(batch)
            continue
        keep = list(batch)
        while keep:
            content, offsets = _build_destination(
                existing, [_payload_of(root, it) for (it, _d) in keep], created=created)
            _write_bytes(path, content)
            bad = _verify_moves(root, dest, keep, offsets, families, notes)
            if not bad:
                break
            failed.extend([keep[i] for i in sorted(bad)])
            keep = [m for i, m in enumerate(keep) if i not in bad]
            if not keep:
                if created:
                    path.unlink()
                else:
                    _write_bytes(path, existing)
                break
        moved.extend(keep)
        if created and keep:
            notes.append("created %s (W30: a fresh scaffold ships neither it nor six of the "
                         "other seven destinations)." % dest)

        # R-H ON THE PATH THAT WRITES NOTHING. An ALREADY item still REMOVES BYTES FROM A HOT FILE,
        # which is the only act in this tool that can lose content, and it is the one path that had
        # no byte-equality check -- while printing a note asserting one. Writing nothing makes the
        # check MORE necessary, not less: there is no append for R-H to catch a mistake in, so the
        # splice below is the whole move. The offset is the id's own span start in the destination,
        # taken BEFORE the appends above, which only extend the file and so cannot shift it. The
        # note is emitted only for items that PASS, so it is true when it is printed.
        for (it, _dest, off) in already:
            if _verify_moves(root, dest, [(it, _dest)], [off], families, notes):
                failed.append((it, _dest))
                continue
            moved.append((it, _dest))
            # `_receipt_label`, not `it.ident`: since F4 an IDENTLESS item reaches this
            # branch too, and `it.ident` would render as the word "None" -- in a sentence that
            # then promised `get None` resolves. The second clause is split for the same reason:
            # `get` cannot address an identless entry at ALL, so claiming one site would be false.
            notes.append("%s is ALREADY in %s, byte for byte -- VERIFIED against %s (R-H), not "
                         "against the file's text. A previous run wrote the destination and did "
                         "not reach the source. The move is COMPLETED by removing it from %s; "
                         "nothing was appended, %s."
                         % (_receipt_label(it), dest,
                            "the span `recall get` would slice there" if it.ident else
                            "the whole entry span it already occupies there",
                            it.rel,
                            ("so `get %s` still resolves at exactly one site" % it.ident)
                            if it.ident else
                            "so the copy already there was not doubled -- `recall get` cannot "
                            "address this entry either way, which is why nothing downstream "
                            "would have reported the duplicate"))

    if not dry_run:
        ok_by_source = {}
        for (it, _dest) in moved:
            ok_by_source.setdefault(it.rel, []).append(it)
        for rel, its in ok_by_source.items():
            path = root / rel
            out = bytearray(path.read_bytes())
            for it in sorted(its, key=lambda x: x.byte_start, reverse=True):
                del out[it.byte_start:it.byte_end]
            _write_bytes(path, bytes(out))
    return moved, failed, refused


# --------------------------------------------------------------------------- the run

def destination_for(it, row=None):
    """The archive one ROW routes one item to. `row` defaults to the row that fired.

    The parameter exists for `_reconcile_probe`, which has to ask "where WOULD this have gone?"
    about an entry that was HELD -- `it.row` is deliberately still `None` at that moment, because
    an entry with no interrupted move waiting for it must not acquire a row it never earned.
    """
    row = it.row if row is None else row
    if row == 1:
        return D_EXPIRED
    if row == 2:
        return D_DONE
    if row == "2b":
        return D_DROPPED
    if row == 3:
        return D_RESOLVED
    if row == 4:
        return D_ARCH_SUPERSEDED if it.rel == "architecture.md" else D_DESIGN_SUPERSEDED
    if row == 5:
        return D_BACKLOG_DEFERRED if it.rel == "backlog.md" else D_OQ_DEFERRED
    raise RuntimeError("no destination for row %r" % (row,))              # pragma: no cover


def _reconcile_probe(root, items, families, warn):
    """STEP 1's other half (B5): admit a HELD entry to the plan when -- and only when -- an earlier
    run ALREADY archived it, so this run can FINISH that move. Returns the count admitted.

    THE POPULATION IS EXACTLY THE ONE A HOLD CANNOT REACH. `decide` returns before the item ever
    becomes a plan entry, so `screen_batch`'s recovery never runs for it, and the duplicate an
    earlier drain left -- entry hot AND archived -- becomes permanent: every re-run refused, at
    exit 0, for ever. MEASURED 2026-08-07 against the PRE-ROUND binary (md5
    c52570a939d020e6a4373903d7e91a25, kept at `docs/backup/drainfix-2026-08-07/demote.py.bak`),
    which is what archived the entry in the first place. The re-run is NOT the measurement -- run 1
    splices the source, so a second run of the same binary sees an empty file and proves nothing.
    `_st_arms_sequence`'s B5 arm builds the same state from `_build_destination`, so the fixture
    ships with this file and needs no second binary to reproduce.

    THE PROBE IS THE SAME TEST `screen_batch` WILL APPLY, deliberately: `_already_offsets` against
    the destination's bytes. If the two ever disagreed, an entry would be admitted here and then
    refused by STEP 4a, which is exactly the noise this probe exists to avoid. STEP 4a stays in the
    code as the executable statement of the invariant, not as a path a normal run takes.

    IT CANNOT MOVE ANYTHING NEW. `reconcile_only` reaches `already` or `refused` and never
    `append`, so the set of entries this tool APPENDS is untouched -- D74's bias lives there. What
    changes is whether a run ends with the corpus holding one copy of an already-archived entry or
    two.
    """
    held = [it for it in items if it.row is None and it.held_row is not None and not it.refusal]
    cache = {}
    admitted = 0
    for it in held:
        dest = destination_for(it, it.held_row)
        if dest not in cache:
            path = root / dest
            cache[dest] = ((path.read_bytes(), _dest_spans(path, dest, families),
                            _dest_entry_starts(path)) if path.is_file() else None)
        got = cache[dest]
        if got is None:
            continue
        existing, spans, starts = got
        payload = source_span(root, it)
        sites = (spans.get(it.ident) or []) if it.ident else []
        if not _already_offsets(existing, sites, starts, payload, it.ident):
            continue
        it.row = it.held_row
        it.reconcile_only = True
        it.why = ("HELD by row %s, and a byte-identical copy is ALREADY in %s"
                  % (it.held_row, dest))
        admitted += 1
        warn.append("%s %s:%d -- the hold above still stands, and there is nothing left to hold: "
                    "a BYTE-IDENTICAL copy of this entry is already in %s, so an earlier run "
                    "archived it and did not reach the source. This run FINISHES that move by "
                    "removing the hot duplicate. NOTHING IS APPENDED, and the archived copy is "
                    "unchanged -- including whatever made this run refuse to defer it, which is "
                    "still there to fix." % (_receipt_label(it), it.rel, it.line, dest))
    return admitted


# --------------------------------------------------------------------------- the veto (C-60)
#
# A drain marker is an ENTRY-LOCAL test: "does this entry say it is finished?" MEASURED 2026-08-28
# over four projects' `backlog.md`, that test alone has 20.95% precision by bytes -- it moves 3.8x
# more LIVE content out of the hot set than dead -- and every one of the eight refutations turned
# on a dependency in a DIFFERENT hot file, which no entry-local test can see. So the missing half
# is not a better marker. It is a REFUSAL: the row proposes, and a hot file that still cites the
# entry holds it. The veto only ever REDUCES what moves; it can never delete and never fails a save.

# The files whose citations HOLD an entry hot. Two exclusions, and both are one rule:
# A CITATION THIS SAVE WILL ITSELF ERASE CANNOT BE A REASON TO REFUSE THE MOVE.
#
#   `manifest.md` -- regenerated IN FULL by `regen_manifest.py`, which runs AFTER this tool
#   (architecture.md R-ARCH-6, where that ordering is load-bearing). Every line in it is machine
#   authored, so nothing there is anybody's reference to anything.
#
#   `state.md`'s BACKLOG-ENTRY-IDS region -- `update_state.py` REWRITES that value line every save
#   from the POST-demote `backlog.md`, so it names EVERY live backlog id by construction. MEASURED
#   2026-08-28 on the folder `init_project.py new` actually produces: `backlog_entry_ids: [M1, D1,
#   D2]`. With the region in scope the veto would hold 100% of `backlog.md` -- the largest hot file
#   on the fleet -- for ever, including an entry carrying an explicit `done:`. NONE of the 11 v4
#   folders on this machine carries that region, so the CORPUS IS STRUCTURALLY BLIND TO IT and only
#   a constructed fixture finds it.
#
# The REST of `state.md` is in scope and earns its place: `goal` and `active_track` are PRESERVED
# byte-for-byte by `update_state.py`, so a citation there survives the save. Measured over the same
# 11 folders they are the ONLY blocker for 6 of 91 vetoed entries, and each of the six is an
# author's live sentence about the entry it names, not index noise.
#
# NOT excluded, and stated as a limit rather than guarded: `backlog_order:`. It is specified to hold
# backlog ids and `update_state.py` PRESERVES rather than rewrites it -- but it is `[]` on 11 of 11
# folders because nothing in the built set computes it (P3). If it is ever armed it joins this list.
# There is nothing there to guard against today.
VETO_SCOPE = ("CLAUDE.md", "CONSTRAINTS.md", "active.md", "architecture.md", "design.md",
              "backlog.md", "open_questions.md", STATE_FILE)

# How many blockers the receipt prints per entry before it summarises. NOT a silent cap: the tail
# is reported by FILE with a count, because a truncation nobody is told about reads as coverage.
VETO_MAX_BLOCKERS = 12

# How many CITING LINES one blocker row names before it summarises the rest. Same rule as above:
# the remainder is counted, never dropped in silence.
VETO_MAX_LINES = 4

# What a blocker's own state means for the reader, and it is the whole of C-60's actionability.
VETO_STATE_TEXT = {
    "live": "LIVE -- unfinished work",
    "moving": "leaves this save",
    "held-mutual": "also VETOED this save, and it cites this entry back -- a mutual hold",
    "held": "also VETOED this save -- not work to finish",
    "refused": "its own move was REFUSED this save -- repair that, not this entry",
    "preamble": "above the first heading -- not an entry, so nothing can finish it",
    "no-drain": "no drain row -- can never go cold on its own",
    "marked": "NOT established as unfinished work -- inspect or drop the citation",
}


class Cite(object):
    """One inbound citation of an id, carrying the BYTE OFFSET as well as the line.

    The offset is not decoration. `citemap.build_map` returns `cited_in` as file -> COUNT, which
    cannot express "this citation is inside the moving entry's own body" -- and MEASURED
    2026-08-28, 15 of 106 otherwise-blocked entries across 11 v4 folders are blocked ONLY by their
    own body. Without the offset one veto in seven is false, so the design note claiming
    `build_map` already supplies what the veto needs was WRONG about this half.

    `ident` IS THE ID THIS IS A CITATION OF, and it is not always the id being held: the
    veto screens carried children too, so a blocker can name a heading NESTED INSIDE the
    held entry. Without it the receipt sent a reader to a line that never mentions the
    entry it had just said was cited.
    """

    __slots__ = ("rel", "line", "off", "text", "ident")

    def __init__(self, rel, line, off, text, ident=None):
        self.rel = rel
        self.line = line
        self.off = off
        self.text = text
        self.ident = ident


def _masked_ranges(rel, rows):
    """(ranges, unreliable_reason). Byte ranges a GENERATOR rewrites after this tool runs.

    See VETO_SCOPE for which ranges and why. The second element is A-6: a region that OPENS
    and never closes cannot be masked honestly in either direction, so it makes the whole file
    unreliable rather than silently swallowing everything below it.
    """
    if rel != STATE_FILE:
        return [], None
    out = []
    start = None
    start_line = 0
    last_end = 0
    for (lineno, text, bstart, bend, in_fence) in rows:
        if bend > last_end:
            last_end = bend
        if in_fence:
            continue
        if BACKLOG_IDS_BEGIN_RE.match(text):
            start = bstart
            start_line = lineno
        elif BACKLOG_IDS_END_RE.match(text) and start is not None:
            out.append((start, bend))
            start = None
    if start is not None:
        # A-6. AN UNTERMINATED BEGIN IS A BLIND REGION, AND A BLIND REGION MAY NOT LICENSE A
        # MOVE. This used to mask to EOF and warn. Both available readings are wrong: mask to
        # EOF and every citation below the marker is invisible, so silence reads as "nothing
        # cites it" -- which is exactly the value that authorises the move, and requirement 6
        # forbids a move authorised by a scan that could not see. Do not mask, and every id the
        # region lists vetoes, which no-ops the drain on `backlog.md` for a reason nobody can
        # act on. So neither: the FILE is unreliable, everything is held, and the operator is
        # told which line to close. Structurally identical to an open code fence, arriving by a
        # different blinding mechanism -- my own comment here rejected two options and never
        # considered this third one.
        return [], ("a BACKLOG-ENTRY-IDS region opens at line %d and never closes, so every "
                    "citation below that line is unreadable as either machine text or author "
                    "text" % start_line)
    return out, None


def build_citation_index(root, families, warn):
    """({id: [Cite]}, [(rel, why)]) over VETO_SCOPE. The second list is files it could not trust.

    ONE fence machine (`recall.load_doc`) and ONE occurrence scanner
    (`citemap.scan_occurrences`) -- the same two every other reader in this file uses, so there is
    no second fence state machine and no second line splitter. The DEFINING occurrence is dropped
    exactly as `citemap.build_map` drops it; the entry's own BODY is dropped by `veto_blockers`,
    which needs the offset this index carries.
    """
    index = {}
    unreliable = []
    for rel in VETO_SCOPE:
        path = root / rel
        if not path.is_file():
            continue
        try:
            _data, rows, open_line = recall.load_doc(path)
        except (IOError, OSError) as exc:
            unreliable.append((rel, "it could not be read: %s" % exc))
            continue
        if open_line is not None:
            unreliable.append((rel, "a code fence opens at line %d and never closes" % open_line))
            continue
        masked, mbad = _masked_ranges(rel, rows)
        if mbad:
            unreliable.append((rel, mbad))
            continue
        for (lineno, text, bstart, _bend, in_fence) in rows:
            if in_fence:
                continue
            if any(lo <= bstart < hi for (lo, hi) in masked):
                continue
            defn = citemap.find_definition(text, families)
            here = (defn[0], defn[1]) if defn is not None else None
            for ident, start, _end in citemap.scan_occurrences(text, families):
                if here is not None and here == (ident, start):
                    continue
                index.setdefault(ident, []).append(
                    Cite(rel, lineno, bstart, text.strip(), ident))
    return index, unreliable


def veto_blockers(it, index):
    """The citations that HOLD `it` hot: every inbound one OUTSIDE its own move span.

    A citation inside the span travels to `_archive/` with the entry, so it is not a reference
    left dangling in the hot set -- it is the entry talking about itself.

    THE KEY SET IS THE ENTRY'S OWN ID PLUS EVERY ID IT CARRIES, and the second half was
    missing until 2026-08-30. A nested heading inside the move span rides out with the parent
    as a `carried` record, so its id goes cold exactly as the parent's does -- and screening
    only `it.ident` let a hot file cite the CHILD without holding anything. After the move
    that citation pointed into cold storage: the C-4 loss the veto exists to refuse.

    IT WAS FIRST DECLINED ON A MEASURED ZERO, AND THAT WAS THE WRONG TEST. No folder on this
    machine had an id-bearing parent whose carried child was cited, so the fault was
    unreachable HERE -- a property of the corpus, not of the code, and `_within` already says
    so about a different case in this same file. What settles it is the contract: the veto
    holds an entry when a hot file cites THE MOVING ID, and a carried child's id moves. C-60
    answers over-blocking with a stale-reference sweep, never a weaker veto.

    Blockers are deduplicated by CITING SITE **AND ID**, never by site alone. Site-only dedup
    became wrong the moment the key set widened: with `M1` and a carried `M2` named on ONE line,
    the second Cite was discarded HERE, before the receipt could ever see it -- so nothing
    downstream could name the id it cited, while the warning added in this same round promises
    "each with the id it actually cites". REPRODUCED 2026-08-31 on a three-shape fixture.
    One line is still ONE thing a reader edits and still renders as ONE row; what changed is that
    the row can now name every id by which that line holds the entry.
    """
    idents = ([it.ident] if it.ident else []) + [c[0] for c in it.carried if c[0]]
    if not idents:
        return []
    out, seen = [], set()
    for ident in idents:
        for c in index.get(ident, ()):
            if _within(c, it.rel, it.byte_start_raw, it.byte_end):
                continue
            if (c.rel, c.off, c.ident) in seen:
                continue
            seen.add((c.rel, c.off, c.ident))
            out.append(c)
    return out


def _within(c, rel, bstart, bend):
    """Is citation `c` inside the byte range [bstart, bend) of file `rel`?

    B-2. The left edge is `byte_start_raw`, NOT `byte_start`. `scan_source` sets
    `byte_start = max(bstart, bom) if bom else bstart`, so on a BOM file the two differ by
    exactly three bytes -- and `Cite.off` is the RAW line start the row scanner reported. Only
    a row at `bstart == 0` falls in that gap, but that row is the entry's OWN HEADING, so the
    entry self-cites, its own heading counts as an outside blocker, and the move is refused.
    It fails SAFE (a false HOLD, never a false move) and every v4 hot file opens with a
    watermark rather than an entry, so it is unreachable in practice -- fixed anyway, because
    "unreachable today" is a property of the corpus and not of the code.

    The right edge stays `byte_end`, the CLAMPED end, because that is what actually moves: a
    citation between `byte_end` and `byte_end_raw` stays hot and must still veto.
    """
    return c.rel == rel and bstart <= c.off < bend


def _entry_headings(root, rel, families, cache):
    """[(lineno, id_or_None, title)] for every heading in `rel`, outside fences. Cached per file."""
    if rel in cache:
        return cache[rel]
    out = []
    path = root / rel
    if path.is_file():
        try:
            _d, rows, _o = recall.load_doc(path)
        except (IOError, OSError):                        # pragma: no cover - the index read it
            rows = []
        for (lineno, text, _s, _e, in_fence) in rows:
            if in_fence:
                continue
            m = citemap.HEADING_RE.match(text)
            if not m:
                continue
            # AN ENTRY IS A HEADING AT LEVEL 2 OR DEEPER -- the project's own entry model, and
            # the same predicate `validate_entries` and the movers use. A level-1 heading is a
            # BANNER: never an entry, and never dropped either.
            #
            # DROPPING IT WAS THE BUG (round 3, F2). A banner ENDS the preceding entry's move
            # span -- `scan_source` stops at the next heading of the same or higher level, and 1
            # is higher than 2 -- so a citation below the banner is OUTSIDE that entry. Removed
            # from the walk, it also stopped ending ATTRIBUTION: `_entry_at` kept returning the
            # entry ABOVE the banner, the citation got no containment hit, and it fell through
            # to `live` as "finishing M13 releases this" about a citation not in M13. Kept as a
            # RESET SENTINEL, with `level` carried so `_entry_at` can refuse it.
            level = len(m.group(1))
            defn = citemap.find_definition(text, families) if level >= 2 else None
            if defn is not None:
                out.append((lineno, defn[0], defn[2], level))
            else:
                out.append((lineno, None, citemap._clean_title(m.group(3)), level))
    cache[rel] = out
    return out


def _entry_at(root, rel, line, families, cache):
    """(heading_lineno, id_or_None, title) of the entry containing `line`, or (0, None, '').

    A LEVEL-1 BANNER RESETS ATTRIBUTION rather than providing it. It is not an entry, and it also
    ENDS the preceding entry's move span, so a line below it belongs to no entry until the next
    level-2+ heading. Returning the entry above the banner claimed a containment that the span
    itself does not have (round 3, F2).
    """
    best = (0, None, "")
    for (lineno, ident, title, level) in _entry_headings(root, rel, families, cache):
        if lineno > line:
            break
        best = (0, None, "") if level < 2 else (lineno, ident, title)
    return best


def entry_marked(it, families, current_save):
    """Has this entry's author already declared a disposition that a row of its OWN file reads?

    THIS EXISTS BECAUSE `live` IS A CLAIM, NOT A LEFTOVER. `_blocker_state`'s residual asserts the
    strongest thing the receipt can say -- this is unfinished work, and finishing it is what
    releases the hold -- so it has to be ESTABLISHED. Every condition that keeps an entry out of
    the moving, held and refused sets used to land there BY ELIMINATION, and two of them were
    shipping: a row disabled before it could read the entry (a malformed BACKLOG region disarms
    rows 2 AND 2b for the whole FILE, so an entry carrying `done:` is never proposed), and a row
    that fired and was then HELD (row 5's gate contract). Rounds 1-3 each found one such condition
    and added a branch ABOVE the residual; the residual refilled every time.

    THE ROW SET IS DERIVED FROM `SOURCE_ROWS[it.rel]`, NEVER TYPED. A list spelled out here would
    silently omit whichever row is added next, which is the mechanism that kept refilling it.

    **ONLY A FIRED MARKER COUNTS. A NEAR MISS IS NOT A DISPOSITION, AND THE FIRST VERSION SAID IT
    WAS -- WHICH BUILT THE MIRROR IMAGE OF THE DEFECT IT WAS REPAIRING.** Measured 2026-08-29:
    `## OQ4 - NOT RESOLVED: which retrieval shape wins`, a heading whose entire content is that
    the question is OPEN, was reported as *NOT established as unfinished work*; so was an entry
    carrying `gate: SATISFIED`, which means the gate is OPEN and the entry is ordinary live work.
    Finished-reported-as-live had become live-reported-as-not-live. The near-miss set is exactly
    the shapes `design.md` lists as deliberately NOT firing -- `NOT RESOLVED`, `PARTIALLY
    RESOLVED`, a question about the RESOLVED tag, prose about some OTHER section's supersession --
    and every one of them describes an entry that is still open. So: `resolved_marker`'s FIRED and
    not its near miss; `superseded_by`'s successor and never `superseded_mentioned`; `done_marker`'s
    VALUE and not the bare presence of the key; and for row 5 the gate must be UNSATISFIED or a
    PLACEHOLDER, because a SATISFIED gate is the absence of a gate.
    Found by cross-family review (`gpt-5.6-sol`, 2026-08-29), and it is the finding I was least
    likely to reach alone: the docstring declared the near-miss rule DELIBERATE, so anyone checking
    my work would have read it as considered rather than wrong.
    """
    for row in SOURCE_ROWS.get(it.rel, ()):
        if row == 1:
            if expiry_absolute(it, current_save)[0] is not None:
                return "an expiry (row 1)"
        elif row == 2:
            if done_marker(it)[0]:
                return "a `done:` field (row 2)"
        elif row == "2b":
            if abandon_marker(it)[0]:
                return "a `wont-do:` field (row 2b)"
        elif row == 3:
            if resolved_marker(it)[0]:
                return "RESOLVED in its heading (row 3)"
        elif row == 4:
            if superseded_by(it, families)[0]:
                return "`superseded by` (row 4)"
        elif row == 5:
            if gate_state(it)[0] in ("unsatisfied", "placeholder"):
                return "a `gate:` field (row 5)"
    return ""


def _blocker_state(c, at, moving_spans, held_spans, refused_spans, item_spans, mutual, target,
                   families, current_save):
    """Why this citation is holding the entry. Four SPAN tests, then two file-level ones.

    The span branches, in order and the order matters: is the citation inside an entry that is
    MOVING, inside one that is HELD, inside one whose own move was REFUSED -- and finally, inside
    any SCANNED ENTRY AT ALL. Only `live` claims the citer is work someone can finish, which is
    why it is the only one the REPRIORITIZE warning fires for, and why it is the one that has to
    be established rather than inferred.

    B-3. The first version asked whether the citation's nearest heading ABOVE it was in the
    plan. `_entry_at` takes the LAST heading at or before the line, so a `###` child inside a
    moving `##` resolves to the CHILD -- and `scan_source` DROPS nested items from the item
    list entirely (they survive only as `carried`), so a child's heading line can never be a
    plan key. It fell through to `rel in SOURCE_ROWS` and was reported as LIVE WORK TO FINISH,
    inside an entry this same save was about to archive. Asking which entry's BYTES CONTAIN
    the citation handles nesting for free, because a child's bytes are the parent's bytes.

    A-3. `held` and `held-mutual` cover the also-vetoed citer. Two entries both carrying `done:`
    that cite each other are BOTH vetoed, so the post-veto plan is empty, so neither is `moving`
    -- and each was reported to the other as "LIVE -- unfinished work" with a REPRIORITIZE line
    naming work that is already finished and cannot be finished again.
    `held-mutual` is the RECIPROCAL case and the only one that may advise breaking a cycle;
    `held` is every other also-vetoed citer, where nothing more than "it is held too" has been
    established. Neither is a task to prioritise.

    `preamble` is the fifth: `_entry_at` returns `(0, None, "")` for a line above the first
    heading, and keyed on the filename that read as `live`, printing "REPRIORITIZE: finishing
    an untitled entry ...". There is no entry there and nothing to finish.

    `marked` IS THE RESIDUAL MADE HONEST, and it replaces `live` as the fallthrough -- which is
    the whole repair. `live` now requires a POSITIVE result: the citation sits inside a scanned
    entry, and that entry declares no disposition any row of its own file reads. Anything else
    says so. ONE state covers both of its causes deliberately: a fourth, fifth and sixth branch
    is what rounds 1-3 each did, and the enumeration is the defect rather than the cure. Its text
    and its advice line claim only what is established, because "already carries a drain marker"
    would be false for the no-containing-entry half.
    """
    for (bs, be) in moving_spans.get(c.rel, ()):
        if bs <= c.off < be:
            return "moving"
    for (bs, be) in held_spans.get(c.rel, ()):
        if bs <= c.off < be:
            # `held` says only that the CITER was also vetoed. It does NOT say the two cite
            # each other: M14 cites M12 while a live M15 cites M14 holds both, with no edge from
            # M12 back to M14, and calling that a mutual hold is advice for a cycle that does
            # not exist.
            #
            # MUTUALITY IS A PROPERTY OF THE PAIR, AND THE FIRST FIX KEYED IT ON THE CITER ALONE
            # (round 3, F1). A citer in any true cycle was then tagged `held-mutual` against
            # EVERY entry it blocked: M12<->M14 mutual, M14 also cites M16, and M14 was reported
            # to M16 as a mutual hold. `mutual` now holds reciprocal (citer, cited) PAIRS, and
            # both keys carry the FILE, so equal byte offsets in two different files cannot
            # collide either.
            return "held-mutual" if ((c.rel, bs, be), target) in mutual else "held"
    for (bs, be) in refused_spans.get(c.rel, ()):
        if bs <= c.off < be:
            # F4. The veto runs AFTER the span refusals, so an entry refused for byte-equality
            # or a clamped span is in neither the plan nor the vetoed list. It fell through to
            # `rel in SOURCE_ROWS` and was reported as unfinished work -- on an entry that may
            # already carry `done:` and whose move failed for a reason that has nothing to do
            # with finishing anything.
            return "refused"
    # `at` is the grouping key `(rel, heading_lineno)`, so the LINE is at[1]. Written as
    # `at[0]` this compared a FILENAME to 0, was never true on any input, and the preamble
    # state could not fire at all -- caught by the arm written for it and by nothing else.
    if at[1] == 0:
        return "preamble"
    if c.rel not in SOURCE_ROWS:
        return "no-drain"
    for (bs, be, cit) in item_spans.get(c.rel, ()):
        if bs <= c.off < be:
            # F3. THE ENTRY THAT IS CLASSIFIED AND THE ENTRY THAT IS NAMED MUST BE THE SAME ONE.
            # `scan_source` DROPS nested items, so a `###` child's citation resolves by span to
            # its retained `##` ancestor -- while `_entry_at`, which drives the DISPLAY, names
            # the child. A finished child under an unmarked parent was therefore printed as the
            # CHILD's "LIVE -- unfinished work" with a REPRIORITIZE line: the ORIGINAL DEFECT,
            # verbatim, surviving this repair through the gap between the two lookups. Measured
            # 2026-08-29 -- `### M14 - a FINISHED child` carrying `done: save#9` under a live
            # `## M13`. The reviewer that found it had no line number and no evidence quote.
            #
            # The child's markers are not available here: `carried` records (ident, label, level,
            # line) and no fields, so the honest answer is that the DISPLAYED entry's status has
            # not been established -- which is what `marked` says, and why one state still
            # suffices. Preserving child marker data would let this resolve precisely; do that
            # only if the case is measured to fire often, never on the strength of this comment.
            if at[1] != cit.line:
                return "marked"
            return "marked" if entry_marked(cit, families, current_save) else "live"
    # No scanned entry contains it, in a file that HAS drain rows. Reachable: a move span is
    # CLAMPED at a BACKLOG region delimiter, so a citation past that delimiter is inside no
    # item's span. Nothing about the citer has been established, so nothing is claimed.
    return "marked"


def _veto_advice(rows, nbytes):
    """C-60's actionable line. The REPRIORITIZE warning fires only when EVERY blocker is live.

    the maintainer, 2026-08-28: "it should not just block, it should actively tell the model - this is what
    is blocking and if needed warn on if the model should reprioritize any tasks to close it out
    entirely." A finished or passing-mention blocker is a citation to DROP, not work to prioritise,
    so naming one as work to reprioritise would be advice that cannot be taken.
    """
    if not rows:
        return ""
    states = [r[3] for r in rows]
    if all(s == "live" for s in states):
        named = []
        for (_c, ident, title, _s, _n, _ids, _ln) in rows:
            label = ident or (title[:40] if title else "an untitled entry")
            if label not in named:
                named.append(label)
        return ("to clear: EVERY blocker is LIVE WORK -- %s. REPRIORITIZE: finishing %s is the "
                "only thing holding this entry hot, and doing so releases %d B."
                % (", ".join(named), "it" if len(named) == 1 else "them", nbytes))
    parts = []
    if any(s == "no-drain" for s in states):
        parts.append("a blocker sits in a file with NO drain row, so only editing or dropping "
                     "that citation releases this")
    if any(s == "moving" for s in states):
        parts.append("a blocker leaves this save -- re-run the drain next save")
    if any(s == "held-mutual" for s in states):
        parts.append("a blocker is ITSELF vetoed this save and cites this entry back -- a "
                     "mutual hold that no amount of finishing will clear; drop ONE of the two "
                     "citations to break the cycle")
    if any(s == "held" for s in states):
        parts.append("a blocker is ITSELF vetoed this save, so it is not work to finish -- "
                     "whatever is holding IT is what to look at first")
    if any(s == "refused" for s in states):
        parts.append("a blocker's OWN move was REFUSED this save for an unrelated reason -- "
                     "read the REFUSED block; repairing that is what releases this")
    if any(s == "preamble" for s in states):
        parts.append("a blocker sits ABOVE the first heading of its file, so it belongs to no "
                     "entry and there is nothing to finish -- edit that line")
    if any(s == "marked" for s in states):
        # CAUSE-NEUTRAL BY REQUIREMENT, not by taste. This line used to end "the WARNINGS block
        # names the row that was disabled or the hold that fired, and repairing THAT is what
        # releases this" -- which ASSERTS a warning exists. Several reachable `marked` causes emit
        # none: a future expiry, a `wont-do:` while row 2b is report-only, a citation inside a
        # nested heading, and the no-containing-entry case. The receipt was pointing the operator
        # at a block that may be empty and naming a repair that may not exist -- the same species
        # of unestablished claim this state was added to end. Found by cross-family review
        # (`gpt-5.6-sol`, 2026-08-29), which also asked that the SINGLE state be kept rather than
        # split per cause; the wording is what carries the honesty, not a fourth state.
        parts.append("a blocker's own entry is NOT established as unfinished work, so finishing it "
                     "is not what releases this -- read the entry at the `file:line` named above "
                     "and either drop the citation or make it resolve; IF a warning for that entry "
                     "appears below, it names the row that declined to act on it")
    if any(s == "live" for s in states):
        parts.append("a blocker is live work")
    return ("to clear: %s. NOT a reprioritize case -- at least one blocker is a citation to drop "
            "rather than work to finish." % "; ".join(parts))


def veto_report(root, vetoed, moved, families, outcome_refused=(), all_items=(),
                current_save=None, cache=None):
    """[(it, dest, rows, advice, extra)] -- everything `render` prints, computed here.

    Built HERE and not in `render` because the three things a blocker's state depends on -- the
    project's id families, what actually became of each entry, and the folder itself -- all live
    in `run`.

    R-VETOORDER-1. `moving` USED TO BE READ OFF THE SURVIVING PLAN, AND A PLAN IS NOT AN OUTCOME.
    `perform` runs after the veto and can still REFUSE a planned entry (destination screening) or
    FAIL it (an R-H byte-equality failure), so one receipt said "leaves this save -- re-run the
    drain next save" about an entry the same receipt listed under REFUSED or FAILED as staying
    hot. It is the family of the defect save#146 repaired: the receipt asserting something the
    run had not established. This function is therefore called AFTER `perform`, `moved` is what
    actually left the hot file, and `outcome_refused` carries the span refusals, `screen_batch`'s
    blocks and the byte-equality failures together -- so an entry that did not move cannot be
    reported as moving, whichever stage stopped it.

    `cache` MUST BE WARMED BEFORE `perform` ON A REAL RUN, and the caller does that. `_entry_at`
    resolves a citation's containing heading by LINE NUMBER against the file on disk, and
    `perform` deletes the moved spans out of exactly those files -- so a walk done afterwards
    attributes each citation to whatever slid up into its line number. Warming the cache against
    the pre-move bytes is what lets the STATES be computed late while the ATTRIBUTION stays
    early. Under `--dry-run` nothing is written and the point is moot; the caller warms it either
    way rather than branching on a flag it would then have to keep in step.

    `all_items` is EVERY scanned item, which is what makes `live` a positive result rather than a
    fallthrough -- see `_blocker_state`. It is the fourth span set and is deliberately the same
    shape as the other three: containment, not heading proximity.
    """
    moving_spans = {}
    for (it, _dest) in moved:
        moving_spans.setdefault(it.rel, []).append((it.byte_start_raw, it.byte_end))
    held_spans = {}
    for (it, _dest, _b) in vetoed:
        held_spans.setdefault(it.rel, []).append((it.byte_start_raw, it.byte_end))
    refused_spans = {}
    for entry in outcome_refused:
        it = entry[0]
        refused_spans.setdefault(it.rel, []).append((it.byte_start_raw, it.byte_end))
    item_spans = {}
    for it in all_items:
        item_spans.setdefault(it.rel, []).append((it.byte_start_raw, it.byte_end, it))

    # F2. THE RECIPROCAL EDGE SET, computed rather than assumed. `vetoed` already carries every
    # blocker of every held entry, so the edges are in hand: a citation of X sitting inside Y's
    # span is the edge Y -> X. A MUTUAL hold is the pair that has BOTH Y -> X and X -> Y. Any
    # other held citer is simply also-vetoed, which is a true and much weaker statement.
    edges = set()
    own = {}
    for (it, _dest, _b) in vetoed:
        own[(it.rel, it.byte_start_raw, it.byte_end)] = it
    for (it, _dest, blockers) in vetoed:
        for c in blockers:
            for (rel, bs, be) in own:
                if rel == c.rel and bs <= c.off < be:
                    edges.add(((rel, bs, be), (it.rel, it.byte_start_raw, it.byte_end)))
    # Reciprocal PAIRS, keyed by full (rel, start, end) at BOTH ends. `edges` holds
    # (citer -> cited); a pair is mutual when the reverse edge is present too.
    mutual = set((a, b) for (a, b) in edges if (b, a) in edges)
    cache = {} if cache is None else cache
    out = []
    for (it, dest, blockers) in vetoed:
        # GROUP BY CITING ENTRY, not by occurrence. A heading and a body line that both name the
        # id are two occurrences of ONE entry, and the thing the reader finishes or edits is the
        # entry -- so occurrence rows spend the cap on repetition and make six blockers look like
        # twelve. Measured: 15 citing entries printed as 6 distinct ones filling all 12 slots.
        grouped = {}
        order = []
        for c in blockers:
            (hline, ident, title) = _entry_at(root, c.rel, c.line, families, cache)
            key = (c.rel, hline)
            if key not in grouped:
                grouped[key] = [c, ident, title,
                                _blocker_state(c, key, moving_spans, held_spans,
                                               refused_spans, item_spans, mutual,
                                               (it.rel, it.byte_start_raw, it.byte_end),
                                               families, current_save), 0, set(), set()]
                order.append(key)
            # B-4b. AGGREGATE THE GROUP; DO NOT LET THE FIRST Cite SPEAK FOR IT. Keeping only
            # the first meant a citing entry that names the id on SEVERAL LINES was rendered
            # with exactly one of them, and -- once the key set widened to carried children --
            # a second line naming a DIFFERENT id vanished entirely. A reader who edited the
            # one printed line found the hold recurring next save, which is precisely what
            # C-60 forbids: a veto that blocks without saying what to do about it. REPRODUCED
            # 2026-08-31, shapes A and B of the three-shape fixture. The occurrence count at
            # [4] keeps its old meaning; the two SETS are what the row now renders from.
            grouped[key][4] += 1
            grouped[key][5].add(c.ident)
            grouped[key][6].add(c.line)
        rows = [tuple(grouped[k]) for k in order[:VETO_MAX_BLOCKERS]]
        extra = [tuple(grouped[k]) for k in order[VETO_MAX_BLOCKERS:]]
        # B-4a. ADVICE IS COMPUTED FROM THE COMPLETE LIST, DISPLAY IS WHAT THE CAP BOUNDS.
        # `_veto_advice`'s whole job is the all-live question, and answering it from a slice
        # answers a different question: 12 live blockers plus a 13th non-live one printed
        # REPRIORITIZE, naming work whose completion would NOT release the hold. Every state
        # was already computed and then thrown away one line before it was needed.
        out.append((it, dest, rows, _veto_advice(rows + extra, it.nbytes), extra))
    return out


def integrity_scope(root):
    """Every file this run reads or appends to. `state.md` first: it is a read path too (R-E).

    Scoped to what the drain TOUCHES, not to the whole hot set. An open fence in `manifest.md`
    cannot change which entry moves out of `backlog.md`, and widening the gate to files with no drain
    row would refuse a correct save over an unrelated defect. Every file that CAN change a move --
    the five sources, the baseline, and each existing destination, into which an entry is appended
    BELOW whatever fence state the file already has -- is in.
    """
    rels = [STATE_FILE] + list(SOURCES) + list(ALL_DESTINATIONS)
    return [(rel, root / rel) for rel in rels if (root / rel).is_file()]


def run(root, save_override, rewrote, rewrote_complete, dry_run, out):
    """The whole of step 6a. Returns an exit code; writes the receipt to `out`."""
    warn = []
    notes = []
    # The project's OWN id families (`.ash-id-families` at the root). An unresolvable id is not
    # cosmetic here: row 2b's baseline IS a list of backlog ids, so a family this run cannot
    # resolve makes the baseline wrong and every comparison against it meaningless. `warn` is
    # declared first on purpose -- a bad declaration line lands in the receipt's WARNINGS section,
    # which is the one channel that cannot stop the drain.
    families, fam_problems = citemap.project_families(root)
    warn.extend(fam_problems)

    # --- 1. INTEGRITY FIRST. An unreliable scan may not license a deletion. ------------------
    unreliable = []
    for (rel, path) in integrity_scope(root):
        try:
            _data, _rows, open_line = recall.load_doc(path)
        except (IOError, OSError) as exc:
            raise ValueError("cannot read %s: %s" % (rel, exc))
        if open_line is not None:
            unreliable.append((rel, open_line))
    if unreliable:
        for (rel, open_line) in unreliable:
            recall.warn_open_fence(rel, open_line)
        sys.stderr.write(
            "error: REFUSED (exit %d) -- %d file(s) in the drain's scope end with an OPEN fence, "
            "so\n       every heading below one is invisible and a move span would carry entries "
            "that are\n       not the one being demoted. NO MOVE WAS PERFORMED.\n"
            % (EXIT_SCAN_INTEGRITY, len(unreliable)))
        out.write("demote: 0 entries moved (SCAN INTEGRITY -- refused)\n")
        return EXIT_SCAN_INTEGRITY

    # --- 2. the two auxiliary reads: "now", and row 2b's baseline ----------------------------
    shim = validate_entries.Scan()
    current_save, save_source = validate_entries.resolve_current_save(root, save_override, shim)
    baseline, baseline_note = read_baseline(root)

    if current_save is None:
        warn.append("row 1 NO-OP: the current save# is unknown (%s), and `expires <= "
                    "current_save` has no left-hand side without one. %s"
                    % (_unwrap_unknown(save_source), _save_advice(root)))
    if baseline is None:
        warn.append("row 2b NO-OP (R-G): %s. A MISSING baseline is not an empty one -- reading it "
                    "as 'everything disappeared' would evict the whole file -- so row 2b moved "
                    "nothing and this line is the record of that." % baseline_note)

    # --- 3. scan, decide -----------------------------------------------------------------------
    scanned = []
    all_items = []
    backlog_items = []
    backlog_scanned = False
    for rel in SOURCES:
        path = root / rel
        if not path.is_file():
            scanned.append((rel, None))
            continue
        # `_carried` is scan_source's F3 contract. `run` does not consume the flat list: the
        # two things it feeds -- the receipt bracket and the destination screen -- both read the
        # per-item `it.carried` view of the same records, which is keyed to the ancestor.
        items, _carried, _open_line, _data, region = scan_source(rel, path, families)
        scanned.append((rel, len(items)))
        all_items.extend(items)
        if rel == "backlog.md":
            backlog_items = items
            backlog_scanned = True
            # ONE warning per file, not one per item: the region is a property of the file.
            if region.state == BACKLOG_MALFORMED:
                warn.append("ROWS 2 AND 2b DISABLED for backlog.md: %s. The carve-out cannot say "
                            "which items the region protects, and rows 2/2b are ONE-WAY, so "
                            "nothing is drained from this file until the delimiters are a single "
                            "ordered pair. (spec S7.2 R1: warn and skip.)" % region.why)
            elif region.state == BACKLOG_ABSENT:
                warn.append("ROW 2b DISABLED for backlog.md: %s. Row 2 (an EXPLICIT `done:`) still "
                            "runs -- no item can be inside a region that does not exist -- but "
                            "row 2b's whole subject is what the region protects, and a missing "
                            "carve-out read as 'nothing is protected' drains the file."
                            % region.why)

    # D81's resolver, built ONCE and AFTER the scan: `backlog.md`'s half is the item list this run
    # already walked, so an id-gated `gate:` and the receipt cannot disagree about what backlog.md
    # defines.
    gate_ids = gate_resolvable_ids(root, backlog_items, families)

    for it in all_items:
        decide(it, current_save, baseline, rewrote, rewrote_complete, families, gate_ids, warn)
        if it.row is not None and it.ident is None:
            warn.append("%s %s:%d has NO citemap-resolvable id (no family in "
                        "citemap.FAMILY_PATTERNS matches its heading). It is still demoted -- "
                        "refusing would make the drain a no-op on the shape a fresh scaffold "
                        "ships -- but `recall get` cannot address it, before or after."
                        % (it.label, it.rel, it.line))

    # STEP 1's other half: a HELD entry an earlier run already archived is admitted to the plan so
    # this run can FINISH that move. It can only ever reach `already` or `refused`. See B5.
    _reconcile_probe(root, all_items, families, warn)

    # An entry whose span cannot be cut safely does not move, whichever row fired.
    span_refused = []
    plan = []
    for it in all_items:
        if it.row is None:
            continue
        if it.refusal:
            span_refused.append((it, destination_for(it), it.refusal))
            warn.append("%s %s:%d -- row %s fired but the MOVE WAS REFUSED: %s. It stays hot."
                        % (it.label, it.rel, it.line, it.row, it.refusal))
            continue
        plan.append((it, destination_for(it)))

    # --- 3b. THE VETO (C-60). A row fired; is any HOT file still CITING the entry? ------------
    # Runs on the PLAN, after the span refusals, because a move already refused for another
    # reason needs no second reason -- and reporting one would say the veto held an entry it
    # never reached.
    vetoed = []
    veto_rows = []
    veto_pending = None
    veto_exempt = []
    if plan:
        cindex, cbad = build_citation_index(root, families, warn)
        for (rel, why) in cbad:
            warn.append("VETO SCOPE UNRELIABLE: `%s` is in the veto's read set and %s. EVERY "
                        "proposed move is HELD HOT. An id cited below that point is invisible to "
                        "the veto, and a citation the scan could not see is indistinguishable "
                        "from one that is not there -- so silence here would read as `nothing "
                        "cites it`, which is the value that licenses the move. Nothing was "
                        "deleted and nothing was moved; fix the file and re-run." % (rel, why))
        if cbad:
            vetoed = [(it, dest, []) for (it, dest) in plan]
            plan = []
        else:
            keep = []
            for (it, dest) in plan:
                if it.reconcile_only:
                    # B5. A byte-identical copy is ALREADY in the archive; this move
                    # only DELETES the hot duplicate an interrupted run left, and
                    # appends nothing. The id already resolves from `_archive/`, so
                    # no citation is left dangling -- while holding it hot keeps TWO
                    # definitions and makes `recall get` exit 4. Vetoing here
                    # would restore the permanent duplicate `_reconcile_probe`
                    # exists to end.
                    #
                    # A-7. DISCLOSED, not silent. This whole design is about checks that did
                    # not run, so a check that was deliberately SKIPPED has to say so in the
                    # same receipt -- otherwise an exempted entry and a screened-and-cleared
                    # one are indistinguishable to the reader.
                    veto_exempt.append(it)
                    keep.append((it, dest))
                    continue
                blockers = veto_blockers(it, cindex)
                if blockers:
                    vetoed.append((it, dest, blockers))
                else:
                    keep.append((it, dest))
            plan = keep
        for (it, _dest, _b) in vetoed:
            # F1. WHEN THE SCAN WAS UNRELIABLE, NO CITATION HAS BEEN ESTABLISHED. `cbad` makes
            # every blocker list EMPTY -- the entry is held because citation status is UNKNOWN,
            # not because something cites it. Saying "a hot file still cites this entry" there
            # sends the reader looking for a citation that may not exist, and contradicts the
            # UNRELIABLE line printed a few rows further down the same receipt.
            if cbad:
                warn.append("%s %s:%d -- row %s fired but the MOVE WAS HELD: the veto's scan was "
                            "UNRELIABLE, so whether any hot file cites this entry is UNKNOWN. "
                            "Nothing is claimed to cite it. It stays hot because a move the "
                            "screening could not clear is not a move that was cleared."
                            % (it.label, it.rel, it.line, it.row))
            else:
                warn.append("%s %s:%d -- row %s fired but the MOVE WAS VETOED: a hot file still "
                            "cites this entry, or an id nested inside it, so moving it would "
                            "leave that reference pointing into cold storage. It stays hot. The "
                            "blockers are named under VETOED with the ids and lines they cite, up "
                            "to the receipt's display caps -- where a cap applies the row says how "
                            "many it did not print." % (it.label, it.rel, it.line, it.row))
        if veto_exempt:
            # A-8 (R-VETOTEXT-1). TWO CLAIMS THIS NOTE MADE THAT IT COULD NOT SUPPORT, and
            # they are the same defect B-1 repaired twenty lines below. The repair skipped
            # its neighbour, so it is stated here in its own words rather than by reference.
            #
            #   (1) IT IS EMITTED BEFORE `perform` RUNS, so "this move deletes the hot
            #   duplicate" was a claim about the future. `screen_batch` STEP 4a reaches
            #   `refused` for a reconcile-only entry by construction, and an R-H
            #   byte-equality failure can still hold it hot -- either leaves REFUSED or
            #   FAILED contradicting this note in the SAME receipt.
            #
            #   (2) A RECONCILE-ONLY ENTRY NEED NOT HAVE AN ID. `_already_offsets` takes
            #   every level>=2 heading in the destination as a candidate when `ident` is
            #   None and lets the BYTES decide, so `_reconcile_probe` admits identless
            #   entries -- for which "the id keeps resolving cold" names a thing that does
            #   not exist. MEASURED: on that input the ALREADY note in `screen_batch` said
            #   "`recall get` cannot address this entry either way" while this one
            #   promised the id resolved. One receipt, two sentences, same entry.
            _ve_noid = [i for i in veto_exempt if not i.ident]
            # A-9. THE TAIL MAY NOT PROMISE RESOLUTION EITHER, and the first version of this
            # repair did exactly that -- "keeps resolving from `_archive/` whichever way this
            # run ends". It is FALSE on a non-MOVED outcome, and the receipt says so a few
            # lines further down: a REFUSED reconcile-only entry stays HOT while its copies
            # remain archived, so the id is defined at two sites or more and `recall get`
            # exits 4. Repairing the first half of a sentence and writing a fresh universal
            # into the second half is the same defect, one clause along.
            # WHOLE SENTENCES, CHOSEN BY ARITY -- not fragments spliced through `%s`. The
            # first repair of this clause fixed the VERB and left every NOUN singular, so at
            # two entries it read "the entries stay hot BESIDE the archived copy ... until one
            # of the two is removed": plural subject, singular object, and a pair named where
            # there are several. Splicing agreement one token at a time is what let a
            # half-corrected sentence through a round that was about exactly this.
            if not _ve_noid:
                if len(veto_exempt) == 1:
                    _ve_tail = (" The archived copy carries the id: on MOVED it resolves from "
                                "`_archive/` alone; on REFUSED or FAILED the entry stays hot "
                                "beside every archived copy, so `recall get` exits 4 "
                                "until EXACTLY ONE definition of it remains.")
                else:
                    _ve_tail = (" Each archived copy carries its own id: on MOVED each "
                                "resolves from `_archive/` alone; on REFUSED or FAILED the "
                                "entries stay hot beside their archived copies, so "
                                "`recall get` exits 4 on each until EXACTLY ONE "
                                "definition of it remains.")
            else:
                _ve_tail = (" %s carr%s no id, so `recall get` cannot address %s either "
                            "way -- the archived bytes are reachable by grep alone."
                            % (", ".join(_receipt_label(i) for i in _ve_noid),
                               "ies" if len(_ve_noid) == 1 else "y",
                               "it" if len(_ve_noid) == 1 else "them"))
            notes.append("VETO EXEMPT on %d reconcile-only entr%s (%s): a byte-identical copy "
                         "is ALREADY in the archive, so COMPLETING the move would remove the "
                         "hot duplicate and append nothing, and the cross-file veto was "
                         "deliberately NOT run on %s. Whether %s then moved is decided later "
                         "-- read MOVED / REFUSED / FAILED below, never this note.%s"
                         % (len(veto_exempt), "y" if len(veto_exempt) == 1 else "ies",
                            ", ".join(_receipt_label(i) for i in veto_exempt),
                            "it" if len(veto_exempt) == 1 else "them",
                            "it" if len(veto_exempt) == 1 else "they", _ve_tail))
        blind = [it for (it, _d) in plan if not it.ident]
        if blind:
            # B-1. THIS NOTE ASSERTS THAT SCREENING WAS IMPOSSIBLE, AND ASSERTS NOTHING ABOUT
            # MOVEMENT. It is emitted BEFORE `perform` runs, so the previous wording -- "moved
            # with NO cross-file check at all" -- was a claim about the future: `screen_batch`
            # can still block the batch, and an R-H byte-equality failure can still refuse the
            # entry, either of which leaves REFUSED or FAILED contradicting this note in the
            # same receipt. `--dry-run` is NOT a second instance of that: `perform` reports the
            # batch as moved under `dry_run`, so the note and the receipt agree there.
            # B-2. TWO POPULATIONS, TWO SENTENCES. Once the screen covers carried ids, "no
            # cross-file check was performed" is FALSE for a blind entry whose children carry
            # ids -- those WERE screened, and the entry can be held by one. Appending a
            # sentence saying so while leaving the opening intact put both claims in one note
            # about one entry. That is the same half-repair caught in the exemption note
            # the same day: fix the clause you can see, ship the one you did not re-read.
            _bl_part = [i for i in blind if any(c[0] for c in i.carried)]
            _bl_none = [i for i in blind if not any(c[0] for c in i.carried)]
            if _bl_none:
                notes.append("VETO BLIND on %d entr%s with NO screenable id at all (%s): "
                             "neither its own heading nor anything nested inside it resolves "
                             "to an id, so the veto had nothing to look up and NO cross-file "
                             "check was performed on %s. Whether %s then moved is decided "
                             "later -- read MOVED / REFUSED / FAILED below, never this note."
                             % (len(_bl_none), "y" if len(_bl_none) == 1 else "ies",
                                ", ".join(_receipt_label(i) for i in _bl_none),
                                "it" if len(_bl_none) == 1 else "them",
                                "it" if len(_bl_none) == 1 else "they"))
            if _bl_part:
                notes.append("VETO BLIND (PARTIAL) on %d entr%s carrying no id of %s own "
                             "(%s): the veto could not screen %s OWN identity, but the ids "
                             "nested inside the move span WERE screened and would have held "
                             "%s -- so this is an INCOMPLETE check, not an absent one. What "
                             "is unscreened is whether anything cites the entry itself under "
                             "a name it does not have. Whether %s then moved is decided later "
                             "-- read MOVED / REFUSED / FAILED below, never this note."
                             % (len(_bl_part), "y" if len(_bl_part) == 1 else "ies",
                                "its" if len(_bl_part) == 1 else "their",
                                ", ".join(_receipt_label(i) for i in _bl_part),
                                "its" if len(_bl_part) == 1 else "their",
                                "it" if len(_bl_part) == 1 else "them",
                                "it" if len(_bl_part) == 1 else "they"))
        # R-VETOORDER-1. WARM THE HEADING WALK HERE; RUN THE REPORT AFTER `perform`. The two
        # halves have opposite timing requirements and running both early is what shipped the
        # defect. A blocker's STATE depends on what actually happened, and `perform` has not run
        # yet. A blocker's ATTRIBUTION depends on line numbers `perform` is about to shift by
        # deleting the moved spans out of these very files. Warming the cache against the
        # pre-move bytes separates them, so neither half has to be wrong for the other to be
        # right.
        veto_cache = {}
        for (_vit, _vdest, _vblockers) in vetoed:
            for _vc in _vblockers:
                _entry_headings(root, _vc.rel, families, veto_cache)
        veto_pending = (vetoed, all_items, veto_cache)

    # --- 4. move -------------------------------------------------------------------------------
    moved, failed, refused = perform(root, plan, families, notes, dry_run)
    refused = span_refused + refused
    for (it, dest, why) in refused:
        if not it.refusal:
            warn.append("%s %s:%d -- row %s fired but the MOVE WAS REFUSED: %s It stays hot."
                        % (it.label, it.rel, it.line, it.row, why))

    # R-VETOORDER-1. NOW, with the outcome in hand. `refused` here is already the union of the
    # span refusals and `screen_batch`'s blocks; `failed` is the byte-equality set. They are
    # passed together because `_blocker_state` asks one question of them -- did this entry's own
    # move stop this save -- and both answers are yes. `failed` rows are 2-tuples and refused
    # rows are 3-tuples, which is fine: the span set reads element 0 and nothing else.
    if veto_pending is not None:
        (_vetoed, _all_items, _veto_cache) = veto_pending
        veto_rows = veto_report(root, _vetoed, moved, families,
                                list(refused) + list(failed), _all_items, current_save,
                                _veto_cache)

    # --- 5. row 2b's ACTUAL subject: ids that are no longer in backlog.md at all -------------------
    dropped_ids, drop_note = pending_drops(baseline, backlog_items, backlog_scanned)
    if dropped_ids:
        warn.append("PENDING DROP (row 2b, spec S5.1): %d id(s) in the previous save's baseline "
                    "are no longer defined anywhere in backlog.md -- %s. Their bytes are not here to "
                    "move (the previous copy is in cold storage, which R-G forbids the drain from "
                    "reading), so this is the RECORD of the disappearance, not an archive of it. "
                    "spec S5.1 also asks the next restore to print a pending-drop list from "
                    "state.md; nothing in P1 writes that field yet."
                    % (len(dropped_ids), ", ".join(dropped_ids)))

    # --- 6. receipt ----------------------------------------------------------------------------
    render(out, root, scanned, moved, failed, refused, veto_rows, warn, notes,
           current_save, save_source, baseline, baseline_note, dropped_ids, drop_note,
           rewrote_complete, dry_run)
    return EXIT_MOVE_FAILED if failed else EXIT_OK


def _unwrap_unknown(source):
    """`unknown (no save: line)` -> `no save: line`. The doubled word reads as a second failure."""
    if source.startswith("unknown (") and source.endswith(")"):
        return source[len("unknown ("):-1]
    return source


def _save_advice(root):
    """Advice that matches what `state.md` actually holds.

    MEASURED 2026-08-03: with `save: soon` in state.md the tool reported "no `save:` line in
    state.md" and advised adding one. There IS one; following that advice adds a second.
    """
    path = root / STATE_FILE
    if not path.is_file():
        return "There is no %s under --root. Pass --save." % STATE_FILE
    try:
        _d, rows, _o = recall.load_doc(path)
    except (IOError, OSError):                            # pragma: no cover - integrity ran first
        return "Pass --save."
    for (lineno, text, _s, _e, in_fence) in rows:
        if not in_fence and re.match(r"^\s{0,3}save\s*:", text or ""):
            return ("%s:%d already HAS a `save:` line (%r) -- it is the VALUE that is not a save "
                    "number. Fix the value, or pass --save."
                    % (STATE_FILE, lineno, (text or "").strip()[:40]))
    return "Pass --save, or give %s a `save:` line." % STATE_FILE


def _receipt_label(it):
    """The id, or the heading fallback marked `~` so a receipt never implies an id that is not one."""
    return it.ident if it.ident else "~" + it.label


def render(out, root, scanned, moved, failed, refused, veto_rows, warn, notes,
           current_save, save_source, baseline, baseline_note, dropped_ids, drop_note,
           rewrote_complete, dry_run):
    """The 6a receipt. PER-DESTINATION ID LISTS, not a byte count on its own.

    A byte count cannot anchor a question at an entry (gate `evict-recall`): "312 B left active.md"
    tells an operator that something went, not WHAT, and the id is the only handle that survives
    the move. The one-line form is the contract other steps parse; the block under it is the
    operator's view of the same facts.

    An eviction is never SILENT. Every entry that left a hot file is named on the first line, and
    every entry that a row selected and the engine then REFUSED is named in its own block: an
    operator reading only stdout must be able to tell a correct drain from a suppressed one.
    """
    prefix = "demote: DRY RUN -- " if dry_run else "demote: "
    if not moved:
        out.write("%s0 entries moved\n" % prefix)
    else:
        parts = ["%s %s -> %s (%d B)" % (_receipt_label(it), ROW_LABEL[it.row], dest, it.nbytes)
                 for (it, dest) in moved]
        out.write("%s%s\n" % (prefix, "; ".join(parts)))
    if dropped_ids:
        out.write("pending_drop: %s\n" % ", ".join(dropped_ids))

    by_dest = {}
    for (it, dest) in moved:
        by_dest.setdefault(dest, []).append(it)
    if by_dest:
        out.write("\nDESTINATIONS (%d)\n" % len(by_dest))
        for dest in ALL_DESTINATIONS:
            its = by_dest.get(dest)
            if not its:
                continue
            total = sum(i.nbytes for i in its)
            out.write("  %-38s %2d entr%s %6d B   ids: %s\n"
                      % (dest, len(its), "y " if len(its) == 1 else "ies",
                         total, ", ".join(_receipt_label(i) for i in its)))

    out.write("\nSOURCES\n")
    verb = "would move" if dry_run else "moved"
    for (rel, n) in scanned:
        gone = [i for (i, _d) in moved if i.rel == rel]
        if n is None:
            out.write("  %-24s (absent)\n" % rel)
        else:
            out.write("  %-24s %2d item(s), %d %s%s\n"
                      % (rel, n, len(gone), verb,
                         # F3: a moved entry's byte figure includes every heading nested inside
                         # its span. Naming them is what lets a reader DECOMPOSE that figure --
                         # before this, the child left the hot file named nowhere at all.
                         ("  [" + ", ".join(
                             "%s %s%s" % (_receipt_label(i), ROW_LABEL[i.row],
                                          (" +carried " + "/".join(_carried_labels(i)))
                                          if i.carried else "")
                             for i in gone) + "]") if gone else ""))
    out.write("  save#: %s (%s) · row-2b baseline: %s\n"
              % (current_save if current_save is not None else "unknown",
                 _unwrap_unknown(save_source),
                 ("ABSENT -- %s" % baseline_note) if baseline is None else baseline_note))
    out.write("  row 2b: %s\n"
              % ("EVICTING (--rewrote-complete: the caller declared its --rewrote set exhaustive)"
                 if rewrote_complete else
                 "report-only -- a PRESENT plan item is never dropped without "
                 "--rewrote-complete; %s"
                 % (drop_note or ("%d id(s) reported as pending_drop" % len(dropped_ids)))))

    if refused:
        out.write("\nREFUSED (%d) -- a row fired; the MOVE did not. These entries STAYED HOT\n"
                  % len(refused))
        for (it, dest, why) in refused:
            out.write("  %s (%s:%d) -> %s\n      %s\n"
                      % (_receipt_label(it), it.rel, it.line, dest, why))

    # F1. TWO CLAIMS, TWO BLOCKS. An entry with named blockers IS cited; an entry held
    # because the scan could not be trusted is NOT known to be cited, and one header covering
    # both asserted the citation for both. Split by whether any blocker is actually known.
    cited_rows = [r for r in veto_rows if r[2]]
    unscreened = [r for r in veto_rows if not r[2]]
    if unscreened:
        out.write("\nHELD (%d) -- a row fired, but the veto's scan was UNRELIABLE, so whether "
                  "anything cites these is UNKNOWN. No citation is claimed. They STAYED HOT\n"
                  % len(unscreened))
        for (it, dest, _rows, _advice, _extra) in unscreened:
            out.write("  %s (%s:%d) -> %s (%d B)\n"
                      % (_receipt_label(it), it.rel, it.line, dest, it.nbytes))
            out.write("      no blocker can be named and none can be cleared; fix the file named "
                      "under WARNINGS and re-run\n")
    if cited_rows:
        out.write("\nVETOED (%d) -- a row fired; a HOT file still CITES the entry, or an id "
                  "nested inside it. These STAYED HOT\n" % len(cited_rows))
        for (it, dest, rows, advice, extra) in cited_rows:
            out.write("  %s (%s:%d) -> %s (%d B)\n"
                      % (_receipt_label(it), it.rel, it.line, dest, it.nbytes))
            for (c, ident, title, state, n, cids, clines) in rows:
                # THREE cases, three sentences. An id-less heading is not the same thing
                # as no heading, and one line claiming both says nothing an operator can
                # act on.
                if ident:
                    where = ident + ((" - " + title) if title else "")
                elif title:
                    where = 'an entry with no resolvable id, titled "%s"' % title
                else:
                    where = "no heading at all above that line"
                # EVERY CITING LINE, AND EVERY ID CITED -- not the first of each. The old row
                # printed `c.line` alone and named an id only when it DIFFERED from the held
                # entry's own, so the ordinary case printed NO id while the warning above
                # promised "each with the id it actually cites", and a citer naming the entry
                # on several lines sent the reader to exactly one of them. Both halves are one
                # defect: first-wins where the reader needs the SET. REPRODUCED 2026-08-31.
                shown = sorted(clines)
                at = ",".join(str(x) for x in shown[:VETO_MAX_LINES])
                if len(shown) > VETO_MAX_LINES:
                    at += " +%d more line(s)" % (len(shown) - VETO_MAX_LINES)
                named = sorted(i for i in cids if i)
                nested = [i for i in named if i != it.ident]
                via = ""
                if named:
                    via = " -- cites %s" % ", ".join(named)
                    if nested:
                        via += " (%s nested inside this entry)" % ", ".join(nested)
                out.write("      cited by %s:%s in %s%s%s  [%s]\n"
                          % (c.rel, at, where,
                             (" (x%d)" % n) if n > 1 else "", via, VETO_STATE_TEXT[state]))
            if extra:
                by_file = {}
                for (c, _i, _t, _s, _n, _ids, _ln) in extra:
                    by_file[c.rel] = by_file.get(c.rel, 0) + 1
                # NAMED, not counted away: a truncation nobody is told about reads as coverage.
                out.write("      ... and %d more citing entr%s, in %s\n"
                          % (len(extra), "y" if len(extra) == 1 else "ies",
                             ", ".join("%s x%d" % (f, k) for f, k in sorted(by_file.items()))))
            if advice:
                out.write("      %s\n" % advice)

    if failed:
        out.write("\nFAILED (%d) -- R-H byte-equality; these entries STAYED HOT\n" % len(failed))
        for (it, dest) in failed:
            out.write("  %s (%s:%d) -> %s\n" % (_receipt_label(it), it.rel, it.line, dest))

    if notes:
        out.write("\nNOTES (%d)\n" % len(notes))
        for n in notes:
            out.write("  %s\n" % n)

    if warn:
        out.write("\nWARNINGS (%d)\n" % len(warn))
        for w in warn:
            out.write("  %s\n" % w)


# --------------------------------------------------------------------------- selftest

_ST_CODES = []


def _st_run(argv):
    import io
    buf = io.StringIO()
    code = main(argv, _out=buf)
    _ST_CODES.append(code)
    return code, buf.getvalue()


def _st_state(save=10, baseline=(), region=True, schema="v4"):
    body = ("<!-- BACKLOG-ENTRY-IDS:BEGIN -->\nbacklog_entry_ids: [%s]\n<!-- BACKLOG-ENTRY-IDS:END -->\n"
            % ", ".join(baseline)) if region else "(no baseline region)\n"
    # `schema_version` is here because every arm below now runs through the migration gate, and
    # a fixture that does not declare v4 would be refused before its own subject was reached.
    # `schema` is a parameter so the gate arms can build the refused shapes from the same maker.
    head = "schema_version: %s\n" % schema if schema else ""
    return ("<!-- ash_save v4 -->\n# state\n\n%ssave: %d\nphase: build\n\n"
            "## Row-2b baseline\n\n%s" % (head, save, body))


def _st_mk(tmp, name, files, save=10, baseline=(), region=True, schema="v4"):
    """A throwaway project: only the files an arm needs, plus a state.md it can read."""
    root = pathlib.Path(tmp) / name
    (root / ARCHIVE_DIR).mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    if save is not None:
        (root / STATE_FILE).write_bytes(
            _st_state(save, baseline, region, schema).encode("utf-8"))
    return root


def _st_snapshot(root):
    out = {}
    for p in sorted(pathlib.Path(root).rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def _st_ids_in(root, rel):
    """The ids `recall` resolves in one file -- the same walk `get` uses."""
    path = pathlib.Path(root) / rel
    if not path.is_file():
        return []
    data, rows, _open = recall.load_doc(path)
    return [e["id"] for e in recall.heading_map(rel, data, rows, citemap.build_families())]


_ST_ACTIVE = (
    "<!-- ash_save v4 -->\n# active\n\n"
    "## A-7 - asserted save#8 - expires save#9\n"
    '"stop deep saves while we build"\nkind: rules\nlifetime: expiring\n\n'
    "## A-8 - a live instruction\nexpires: save#11\nkind: rules\nlifetime: expiring\n\n"
    "## A-9 - relative, due now\nexpires: 0 saves\nkind: rules\nlifetime: expiring\n"
)

_ST_BACKLOG = (
    "<!-- ash_save v4 -->\n# plan\n\n"
    "## M12 - finished item\nkind: plan\ndone: save#10\n\n"
    "## M13 - we are done with the prose form\nkind: plan\n\n"
    "## M14 - placeholder completion\nkind: plan\ndone: TBD\n\n"
    "## W5 - stale, in the baseline, declared abandoned\nkind: plan\n"
    "wont-do: the approach was abandoned when the v4 shape landed\n\n"
    "## W6 - fresh, not in the baseline\nkind: plan\n"
    "wont-do: also abandoned, but never in a baseline\n\n"
    "## M20 - gated\nkind: plan\ngate: EXTERNAL - channel B opens\n\n"
    "## M21 - gate already open\nkind: plan\ngate: SATISFIED - channel B opened save#9\n\n"
    "## M40 - matches row 5 AND row 2b\nkind: plan\ngate: EXTERNAL - the pilot reports\n"
    "wont-do: and it is ALSO declared abandoned -- row 5 must still win\n\n"
    "<!-- BACKLOG:BEGIN -->\n"
    "## M30 - a backlog item, in the baseline, declared abandoned\nkind: plan\n"
    "wont-do: abandoned, but the carve-out protects it anyway\n\n"
    "<!-- BACKLOG:END -->\n"
)

_ST_OQ = (
    "<!-- ash_save v4 -->\n# open questions\n\n"
    "## OQ3 - RESOLVED - the curator needs a non-recursive listdir\nkind: plan\n\n"
    "## OQ4 - still UNRESOLVED, deliberately\nkind: plan\n\n"
    "## OQ7 - cannot be answered yet\nkind: plan\n"
    "gate: EXTERNAL - the broker pilot runs one save\n"
)

_ST_ARCH = (
    "<!-- ash_save v4 -->\n# architecture\n\n"
    "## D4 - the old shape\nkind: spec\n\nsuperseded by D9\n\n"
    "## D5 - vaguely obsolete\nkind: spec\n\nsuperseded by a later decision\n"
)

_ST_DESIGN = (
    "<!-- ash_save v4 -->\n# design\n\n"
    "## D6 - the old surface\nkind: spec\n\nsuperseded by D10\n"
)

_ST_FILES = {"active.md": _ST_ACTIVE, "backlog.md": _ST_BACKLOG, "open_questions.md": _ST_OQ,
             "architecture.md": _ST_ARCH, "design.md": _ST_DESIGN}


def _st_arms_rows(ck, tmp):
    """Every row fires on its OWN trigger and NOT on a near-miss, in one pass over one project."""
    root = _st_mk(tmp, "rows", _ST_FILES, save=10, baseline=("W5", "M30", "M40"))
    before = _st_snapshot(root)
    code, text = _st_run(["--root", str(root), "--rewrote-complete"])
    ck("rows: exit", code, EXIT_OK)

    ck("row1: A-7 expired -> archive", "A-7" in _st_ids_in(root, D_EXPIRED), True)
    ck("row1: A-7 gone from active.md", "A-7" in _st_ids_in(root, "active.md"), False)
    ck("row1 near-miss: A-8 (expires save#11 > 10) stays hot",
       "A-8" in _st_ids_in(root, "active.md"), True)
    ck("row1: A-9 relative `0 saves` expired", "A-9" in _st_ids_in(root, D_EXPIRED), True)

    ck("row2: M12 done -> backlog-done", "M12" in _st_ids_in(root, D_DONE), True)
    ck("row2 near-miss: prose 'done' (M13) is not a field", "M13" in _st_ids_in(root, "backlog.md"),
       True)
    ck("row2 near-miss: `done: TBD` (M14) fires no row", "M14" in _st_ids_in(root, "backlog.md"),
       True)
    ck("row2 near-miss: M14 is not in backlog-dropped either",
       "M14" in _st_ids_in(root, D_DROPPED), False)
    ck("row2: placeholder `done:` warns", "placeholder value" in text, True)

    ck("row2b: W5 in baseline -> backlog-dropped (under --rewrote-complete)",
       "W5" in _st_ids_in(root, D_DROPPED), True)
    ck("row2b near-miss: W6 not in baseline stays hot", "W6" in _st_ids_in(root, "backlog.md"), True)

    ck("row3: OQ3 RESOLVED -> resolved-oqs", "OQ3" in _st_ids_in(root, D_RESOLVED), True)
    ck("row3 near-miss: UNRESOLVED (OQ4) does not fire",
       "OQ4" in _st_ids_in(root, "open_questions.md"), True)

    ck("row4: D4 superseded -> architecture-superseded",
       "D4" in _st_ids_in(root, D_ARCH_SUPERSEDED), True)
    ck("row4 near-miss: no successor id (D5) stays hot",
       "D5" in _st_ids_in(root, "architecture.md"), True)
    ck("row4 near-miss: warns about the missing id",
       "does not name a successor in the position row 4 requires" in text, True)
    ck("row4: D6 -> design-superseded", "D6" in _st_ids_in(root, D_DESIGN_SUPERSEDED), True)
    ck("row4: design and architecture do not share a destination",
       "D6" in _st_ids_in(root, D_ARCH_SUPERSEDED), False)

    ck("row5: M20 gated -> backlog-deferred", "M20" in _st_ids_in(root, D_BACKLOG_DEFERRED), True)
    ck("row5 near-miss: SATISFIED gate (M21) stays hot", "M21" in _st_ids_in(root, "backlog.md"),
       True)
    ck("row5: OQ7 -> open-questions-deferred", "OQ7" in _st_ids_in(root, D_OQ_DEFERRED), True)

    ck("carve-out: backlog M30 survives 6a", "M30" in _st_ids_in(root, "backlog.md"), True)
    ck("carve-out: M30 is NOT in backlog-dropped", "M30" in _st_ids_in(root, D_DROPPED), False)

    # A move span may not cross a region delimiter. M40 sits directly above BACKLOG:BEGIN and its
    # heading_map span runs through it; unclamped, the move carried the delimiter into the archive
    # and disarmed the carve-out for every later save.
    backlog_now = (root / "backlog.md").read_bytes()
    ck("region: BACKLOG:BEGIN stayed in backlog.md", b"<!-- BACKLOG:BEGIN -->" in backlog_now, True)
    ck("region: BACKLOG:END stayed in backlog.md", b"<!-- BACKLOG:END -->" in backlog_now, True)
    ck("region: no delimiter escaped into any archive",
       [d for d in ALL_DESTINATIONS if (root / d).is_file()
        and b"BACKLOG:" in (root / d).read_bytes()], [])

    ck("precedence: M40 matches row 5 and row 2b -> DEFERRED",
       "M40" in _st_ids_in(root, D_BACKLOG_DEFERRED), True)
    ck("precedence: M40 is NOT dropped", "M40" in _st_ids_in(root, D_DROPPED), False)

    # the receipt names every id, per destination, with bytes
    ck("receipt: one-line form present", text.startswith("demote: "), True)
    for ident, label in (("A-7", "expired"), ("M12", "done"), ("W5", "dropped"),
                         ("OQ3", "resolved"), ("D4", "superseded"), ("M20", "deferred")):
        ck("receipt names %s %s" % (ident, label), "%s %s -> " % (ident, label) in text, True)
    ck("receipt: per-destination block", "DESTINATIONS (" in text, True)
    ck("receipt: destination id list", "ids: A-7, A-9" in text, True)
    ck("receipt: byte counts", " B)" in text and " B   ids:" in text, True)

    # W30: six of the eight destinations did not exist before this run
    created = [d for d in ALL_DESTINATIONS if d not in before and (root / d).is_file()]
    ck("W30: destinations created when missing", len(created), 8)

    # nothing lost: every moved id resolves somewhere, and no source kept a moved id
    for ident, dest in (("A-7", D_EXPIRED), ("A-9", D_EXPIRED), ("M12", D_DONE),
                        ("W5", D_DROPPED), ("OQ3", D_RESOLVED), ("D4", D_ARCH_SUPERSEDED),
                        ("D6", D_DESIGN_SUPERSEDED), ("M20", D_BACKLOG_DEFERRED),
                        ("M40", D_BACKLOG_DEFERRED), ("OQ7", D_OQ_DEFERRED)):
        ck("moved %s resolves in %s" % (ident, dest), ident in _st_ids_in(root, dest), True)

    # R-H's own guarantee, checked from the outside: the bytes in the archive are the bytes that
    # left the hot file. `before` still holds the pre-run source.
    src = before["active.md"]
    arch = (root / D_EXPIRED).read_bytes()
    a7 = src[src.index(b"## A-7"):src.index(b"## A-8")]
    ck("R-H: A-7's bytes survived the move", a7.rstrip() in arch, True)
    return root


def _st_arms_carveout(ck, tmp):
    """MUTATION TEST. Disable the carve-out ONLY and the backlog item must be dropped.

    A carve-out that is never observed to matter is indistinguishable from a carve-out that is
    dead code. The mutation is applied to `Region.holds` rather than to the region SCAN, because
    the scan failing is now its own defence (see `_st_arms_region`): with the region unfindable
    rows 2 and 2b are disabled outright, so mutating the scan would prove the wrong thing.
    """
    root = _st_mk(tmp, "carveout", _ST_FILES, save=10, baseline=("M30",))
    real = Region.holds
    Region.holds = lambda self, lineno: False
    try:
        code, _text = _st_run(["--root", str(root), "--rewrote-complete"])
    finally:
        Region.holds = real
    ck("carve-out mutation: exit", code, EXIT_OK)
    ck("carve-out mutation: M30 IS dropped without the carve-out",
       "M30" in _st_ids_in(root, D_DROPPED), True)
    ck("carve-out mutation: M30 left backlog.md", "M30" in _st_ids_in(root, "backlog.md"), False)


# --- the BACKLOG region: absent / malformed / doubled / quoted. ------------------------------
# Each shape below was measured draining live content ONE-WAY at exit 0 with an empty stderr.

_ST_REGION_ITEMS = ("## M31 - a live backlog item\nkind: plan\ndone: save#9\n\n"
                    "## M32 - another live backlog item\nkind: plan\n\n")


def _st_backlog_with(region_text):
    return ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M33 - outside, gated\nkind: plan\ngate: EXTERNAL - the pilot reports\n\n" + region_text)


def _st_arms_region(ck, tmp):
    """The carve-out's OTHER input. A region that cannot be located may not license a drain."""
    ok = _st_backlog_with("<!-- BACKLOG:BEGIN -->\n" + _ST_REGION_ITEMS + "<!-- BACKLOG:END -->\n")
    # MALFORMED: delimiters are PRESENT and undecidable. Both rows 2 and 2b are disabled, because
    # an item may be inside what the author meant as the region and the tool cannot tell which.
    malformed = {
        "noend": _st_backlog_with("<!-- BACKLOG:BEGIN -->\n" + _ST_REGION_ITEMS),
        "nobegin": _st_backlog_with(_ST_REGION_ITEMS + "<!-- BACKLOG:END -->\n"),
        "inverted": _st_backlog_with("<!-- BACKLOG:END -->\n" + _ST_REGION_ITEMS
                                  + "<!-- BACKLOG:BEGIN -->\n"),
        "tworegions": _st_backlog_with(
            "<!-- BACKLOG:BEGIN -->\n## M31 - a live backlog item\nkind: plan\ndone: save#9\n\n"
            "<!-- BACKLOG:END -->\n\n<!-- BACKLOG:BEGIN -->\n"
            "## M32 - another live backlog item\nkind: plan\n\n<!-- BACKLOG:END -->\n"),
    }

    # the control: a WELL-FORMED region protects both items from rows 2 and 2b.
    root = _st_mk(tmp, "region_ok", {"backlog.md": ok}, save=10, baseline=("M31", "M32"))
    code, text = _st_run(["--root", str(root), "--rewrote-complete"])
    ck("region ok: exit", code, EXIT_OK)
    ck("region ok: M31 (done: INSIDE the region) is not archived by row 2",
       "M31" in _st_ids_in(root, "backlog.md"), True)
    ck("region ok: M32 (baseline member INSIDE the region) is not dropped by row 2b",
       "M32" in _st_ids_in(root, "backlog.md"), True)
    ck("region ok: the gated item OUTSIDE it still moves",
       "M33" in _st_ids_in(root, D_BACKLOG_DEFERRED), True)
    ck("region ok: no disabling warning", "ROWS 2 AND 2b DISABLED" in text, False)

    for name, body in malformed.items():
        r = _st_mk(tmp, "region_" + name, {"backlog.md": body}, save=10, baseline=("M31", "M32"))
        code, text = _st_run(["--root", str(r), "--rewrote-complete"])
        ck("region %s: exit" % name, code, EXIT_OK)
        ck("region %s: M31 survives (row 2 must not fire on an undecidable region)" % name,
           "M31" in _st_ids_in(r, "backlog.md"), True)
        ck("region %s: M32 survives (row 2b likewise)" % name,
           "M32" in _st_ids_in(r, "backlog.md"), True)
        ck("region %s: nothing at all reached backlog-dropped" % name,
           (r / D_DROPPED).is_file(), False)
        ck("region %s: nothing at all reached backlog-done" % name,
           (r / D_DONE).is_file(), False)
        ck("region %s: the eviction is NOT silent" % name,
           "ROWS 2 AND 2b DISABLED" in text, True)

    # ABSENT is a DIFFERENT answer from MALFORMED, and the difference is deliberate. With NO
    # delimiter of either kind, no item can be inside a region, so row 2's EXPLICIT `done:` is
    # still safe -- it is the author's own assertion, and spec S7.2 R6 wants it archived. Row 2b,
    # whose entire subject is what the region protects, is off: reading a missing carve-out as
    # "nothing is protected" is the F65 shape, and it drained whole files at exit 0.
    r = _st_mk(tmp, "region_noregion", {"backlog.md": _st_backlog_with(_ST_REGION_ITEMS)},
               save=10, baseline=("M31", "M32"))
    code, text = _st_run(["--root", str(r), "--rewrote-complete"])
    ck("region absent: exit", code, EXIT_OK)
    ck("region absent: an EXPLICIT `done:` still fires row 2", "M31" in _st_ids_in(r, D_DONE),
       True)
    ck("region absent: the baseline member is NOT dropped", "M32" in _st_ids_in(r, "backlog.md"),
       True)
    ck("region absent: nothing at all reached backlog-dropped", (r / D_DROPPED).is_file(), False)
    ck("region absent: says which row it turned off", "ROW 2b DISABLED" in text, True)
    ck("region absent: does NOT claim to have turned off row 2",
       "ROWS 2 AND 2b DISABLED" in text, False)

    # THE TEAR. An entry whose body quotes the delimiters is REFUSED, never trimmed mid-entry.
    torn = ("<!-- ash_save v4 -->\n# plan\n\n"
            "<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n\n"
            "## M35 - documents the markers\nkind: plan\ngate: EXTERNAL - the pilot\n\n"
            "The region is delimited by:\n<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n"
            "LAST LINE OF M35 - this sentence is part of the entry.\n")
    r = _st_mk(tmp, "region_tear", {"backlog.md": torn}, save=10, baseline=())
    before = _st_snapshot(r)
    code, text = _st_run(["--root", str(r), "--rewrote-complete"])
    ck("tear: exit", code, EXIT_OK)
    ck("tear: the entry did NOT move", "M35" in _st_ids_in(r, "backlog.md"), True)
    ck("tear: nothing was written to the deferred archive", (r / D_BACKLOG_DEFERRED).is_file(), False)
    ck("tear: the trailing half is still under its heading",
       b"LAST LINE OF M35" in (r / "backlog.md").read_bytes(), True)
    ck("tear: backlog.md is byte-identical", _st_snapshot(r), before)
    ck("tear: the refusal is named", "REFUSED (1)" in text, True)

    # ...and the LEGITIMATE clamp still works: an item directly above BEGIN moves whole, and the
    # delimiter stays behind. This is the defect the clamp was introduced for.
    above = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## M36 - gated, above the region\nkind: plan\ngate: EXTERNAL - the pilot\n\n"
             "<!-- BACKLOG:BEGIN -->\n## M37 - backlog\nkind: plan\n\n<!-- BACKLOG:END -->\n")
    r = _st_mk(tmp, "region_above", {"backlog.md": above}, save=10, baseline=())
    code, _t = _st_run(["--root", str(r), "--rewrote-complete"])
    ck("clamp: the item above BEGIN still moves", "M36" in _st_ids_in(r, D_BACKLOG_DEFERRED), True)
    ck("clamp: BEGIN stayed in backlog.md",
       b"<!-- BACKLOG:BEGIN -->" in (r / "backlog.md").read_bytes(), True)
    ck("clamp: no delimiter escaped into the archive",
       b"BACKLOG:" in (r / D_BACKLOG_DEFERRED).read_bytes(), False)

    # A FENCED delimiter is not a delimiter: a doc may show the markers without disarming itself.
    fenced = ("<!-- ash_save v4 -->\n# plan\n\n"
              "<!-- BACKLOG:BEGIN -->\n## M38 - backlog\nkind: plan\ndone: save#9\n\n"
              "<!-- BACKLOG:END -->\n\n"
              "## M39 - shows the markers\nkind: plan\n\n```text\n<!-- BACKLOG:BEGIN -->\n"
              "<!-- BACKLOG:END -->\n```\n")
    r = _st_mk(tmp, "region_fenced", {"backlog.md": fenced}, save=10, baseline=())
    code, text = _st_run(["--root", str(r), "--rewrote-complete"])
    ck("fenced markers: region is still well-formed", "DISABLED" in text, False)
    ck("fenced markers: the backlog item is still protected",
       "M38" in _st_ids_in(r, "backlog.md"), True)


def _st_arms_markers(ck, tmp):
    """Rows 3 and 4 fire on a MARKER, not on the word. Six measured false positives, pinned."""
    oq = ("<!-- ash_save v4 -->\n# open questions\n\n"
          "## OQ10 - NOT RESOLVED: which retrieval shape wins\nkind: plan\n\n"
          "## OQ11 - is the retrieval verdict RESOLVED yet?\nkind: plan\n\n"
          "## OQ12 - how should a heading carrying the word RESOLVED be treated\nkind: plan\n\n"
          "## OQ13 - RESOLVED - we ship the marker form\nkind: plan\n\n"
          "## OQ14 - still UNRESOLVED, deliberately\nkind: plan\n")
    arch = ("<!-- ash_save v4 -->\n# architecture\n\n"
            "## D20 - the live event bus\nkind: spec\n\n"
            "This section is CURRENT. It explains history: the cron drain was `superseded by D9`\n"
            "in 2026, which is why the bus exists. Nothing about THIS section is superseded.\n\n"
            "## D21 - a section that was NOT superseded by D9\nkind: spec\n\n"
            "## D22 - future tense\nkind: spec\n\n"
            "This will be superseded by whatever replaces it, unlike D9 which stands.\n\n"
            "## D23 - the genuine one\nkind: spec\n\nsuperseded by D9\n\n"
            "## D24 - the genuine heading form - superseded by D9\nkind: spec\n\n"
            "## D25 - a bulleted marker\nkind: spec\n\n- **superseded by D9** (the bus replaced "
            "it)\n")
    root = _st_mk(tmp, "markers", {"open_questions.md": oq, "architecture.md": arch}, save=10)
    code, text = _st_run(["--root", str(root)])
    ck("markers: exit", code, EXIT_OK)

    for ident, why in (("OQ10", "NOT RESOLVED"), ("OQ11", "RESOLVED yet?"),
                       ("OQ12", "the word RESOLVED as the subject")):
        ck("row3 false positive pinned: %s (%s) stays hot" % (ident, why),
           ident in _st_ids_in(root, "open_questions.md"), True)
    ck("row3: the genuine marker still fires", "OQ13" in _st_ids_in(root, D_RESOLVED), True)
    ck("row3: UNRESOLVED still does not fire",
       "OQ14" in _st_ids_in(root, "open_questions.md"), True)
    ck("row3: the near-miss is NAMED, not silent", "row 3 did not fire" in text, True)

    for ident, why in (("D20", "prose about another section's supersession"),
                       ("D21", "NOT superseded by"), ("D22", "future tense + unrelated clause")):
        ck("row4 false positive pinned: %s (%s) stays hot" % (ident, why),
           ident in _st_ids_in(root, "architecture.md"), True)
    ck("row4: D22's unrelated D9 was NOT recorded as a successor",
       "D22" in _st_ids_in(root, D_ARCH_SUPERSEDED), False)
    for ident, form in (("D23", "a marker line"), ("D24", "the heading form")):
        ck("row4: the genuine marker still fires -- %s (%s)" % (ident, form),
           ident in _st_ids_in(root, D_ARCH_SUPERSEDED), True)
    # M9. The bulleted, bolded form USED to fire and must not: a bullet list is where a person
    # writes down what replaced what, so it is the highest-traffic form of an anchored MENTION.
    ck("row4/M9: the bulleted bolded marker is a MENTION -- D25 stays hot",
       "D25" in _st_ids_in(root, "architecture.md"), True)
    ck("row4/M9: ...and it did not reach the ONE-WAY archive",
       "D25" in _st_ids_in(root, D_ARCH_SUPERSEDED), False)
    ck("row4: the near-miss is NAMED, not silent", "not in MARKER POSITION" in text, True)


def _st_arms_row2b(ck, tmp):
    """Row 2b's subject is an item that DISAPPEARED, not one that is sitting right there."""
    live = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M60 - rewritten from top to bottom this save\nkind: plan\n"
            "wont-do: declared abandoned, and named by --rewrote\n\n"
            "Every word of this body was written this session.\n\n"
            "## M61 - untouched, carried over, declared abandoned\nkind: plan\n"
            "wont-do: the approach was abandoned when D81 landed\n\n"
            "<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n")
    root = _st_mk(tmp, "r2b_live", {"backlog.md": live}, save=2, baseline=("M60", "M61"))
    before = _st_snapshot(root)
    code, text = _st_run(["--root", str(root)])
    ck("row2b default: exit", code, EXIT_OK)
    ck("row2b default: the REWRITTEN item is still hot", "M60" in _st_ids_in(root, "backlog.md"),
       True)
    ck("row2b default: the CARRIED-OVER item is still hot", "M61" in _st_ids_in(root, "backlog.md"),
       True)
    ck("row2b default: backlog.md is byte-identical -- a present item is never dropped",
       _st_snapshot(root), before)
    ck("row2b default: backlog-dropped was not even created", (root / D_DROPPED).is_file(), False)
    ck("row2b default: the receipt says the row is report-only", "row 2b: report-only" in text,
       True)

    # the SEAM: a caller that knows what it rewrote gets the eviction branch, and only for the
    # ids it did NOT name.
    root2 = _st_mk(tmp, "r2b_complete", {"backlog.md": live}, save=2, baseline=("M60", "M61"))
    code2, _t2 = _st_run(["--root", str(root2), "--rewrote-complete", "--rewrote", "M60"])
    ck("row2b complete: exit", code2, EXIT_OK)
    ck("row2b complete: the declared-rewritten item stays hot",
       "M60" in _st_ids_in(root2, "backlog.md"), True)
    ck("row2b complete: the undeclared one is dropped", "M61" in _st_ids_in(root2, D_DROPPED),
       True)

    # THE GENUINE EVENT: an id in the baseline that backlog.md no longer defines anywhere.
    gone = ("<!-- ash_save v4 -->\n# plan\n\n## M62 - the only survivor\nkind: plan\n\n"
            "<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n")
    root3 = _st_mk(tmp, "r2b_gone", {"backlog.md": gone}, save=3, baseline=("M62", "M63", "M64"))
    before3 = _st_snapshot(root3)
    code3, text3 = _st_run(["--root", str(root3)])
    ck("pending drop: exit", code3, EXIT_OK)
    ck("pending drop: the ids are REPORTED on the receipt",
       "pending_drop: M63, M64" in text3, True)
    ck("pending drop: and warned about", "PENDING DROP (row 2b" in text3, True)
    ck("pending drop: the survivor is not implicated", "M62" in text3.split("pending_drop:")[1]
       .split("\n")[0], False)
    ck("pending drop: reporting writes nothing", _st_snapshot(root3), before3)

    # a MISSING backlog.md is not three individual disappearances.
    root4 = _st_mk(tmp, "r2b_nofile", {"active.md": _ST_ACTIVE}, save=3, baseline=("M63", "M64"))
    code4, text4 = _st_run(["--root", str(root4)])
    ck("pending drop: absent backlog.md reports no ids", "pending_drop:" in text4, False)
    ck("pending drop: absent backlog.md says why", "a missing FILE is not a per-item disappearance"
       in text4, True)
    ck("pending drop: absent backlog.md exit", code4, EXIT_OK)


def _st_arms_gate_contract(ck, tmp):
    """D81: a `gate:` is a CONTRACT, and the hold it produces is TERMINAL.

    Every arm here was run once against a deliberately broken implementation and observed to go
    red before it was kept -- the mutations are the two `globals()` swaps below, and the A/B pair
    in `_st_arms_wont_do`. A check that cannot fail on the fault it guards is not a check.
    """
    plan = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M80 - ID-GATED on an item this file defines\nkind: plan\n"
            "gate: waiting on M84\n\n"
            "## M81 - EVENT-GATED\nkind: plan\n"
            "gate: EXTERNAL - v4 is deployed to all machines\n\n"
            "## M82 - the hallucinated gate, ALSO declared abandoned\nkind: plan\n"
            "gate: I hallucinated this\n"
            "wont-do: the marker is the only reason row 2b could reach it at all\n\n"
            "## M83 - the vibes gate, ALSO declared abandoned\nkind: plan\n"
            "gate: waiting for the vibes to be right\n"
            "wont-do: the same shape as the hallucinated gate, different wording\n\n"
            "## M84 - the item the id-gated entry waits on\nkind: plan\n\n"
            "## M85 - an EMPTY gate, ALSO declared abandoned\nkind: plan\n"
            "gate:\n"
            "wont-do: the empty-waiting-line half of the terminal-hold test\n\n"
            "## M86 - gated on an id that resolves NOWHERE\nkind: plan\n"
            "gate: waiting on M99\n\n"
            "<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n")
    ids = ("M80", "M81", "M82", "M83", "M84", "M85", "M86")
    root = _st_mk(tmp, "gate_contract", {"backlog.md": plan}, save=10, baseline=ids)
    code, text = _st_run(["--root", str(root), "--rewrote-complete"])
    ck("gate: exit", code, EXIT_OK)

    # (3) BOTH admissible forms defer.
    ck("gate (3): id-gated on a resolving id DEFERS",
       "M80" in _st_ids_in(root, D_BACKLOG_DEFERRED), True)
    ck("gate (3): ...and left backlog.md", "M80" in _st_ids_in(root, "backlog.md"), False)
    ck("gate (3): `EXTERNAL - <observable>` DEFERS",
       "M81" in _st_ids_in(root, D_BACKLOG_DEFERRED), True)
    ck("gate (3): ...and left backlog.md", "M81" in _st_ids_in(root, "backlog.md"), False)

    # (4) THE MEASURED STARTING STATE. Both of these deferred successfully before this check.
    for ident in ("M82", "M83"):
        ck("gate (4): %s does NOT defer" % ident, ident in _st_ids_in(root, D_BACKLOG_DEFERRED),
           False)
        ck("gate (4): %s stays in the LIVE plan" % ident, ident in _st_ids_in(root, "backlog.md"),
           True)
        ck("gate (4): the warning NAMES %s" % ident,
           ("%s backlog.md:" % ident) in text and "NOT DEFERRED" in text, True)
    ck("gate (4): the warning says what an admissible gate looks like",
       "`EXTERNAL - `" in text and "resolves in backlog.md" in text, True)

    # (1) + (2) THE TERMINAL HOLD. M82/M83/M85 each carry `wont-do:`, sit in the baseline, are
    # outside the region and are not named by --rewrote, under --rewrote-complete -- i.e. they
    # match row 2b on every condition. Row 5's hold is what keeps them out of the ONE-WAY file.
    for ident in ("M82", "M83", "M85"):
        ck("gate (1,2): %s is NOT in the ONE-WAY dropped archive" % ident,
           ident in _st_ids_in(root, D_DROPPED), False)
    ck("gate (2): the EMPTY gate stays in the live plan",
       "M85" in _st_ids_in(root, "backlog.md"), True)
    ck("gate (2): ...and is warned about as a placeholder",
       "M85 backlog.md:" in text and "is a placeholder" in text, True)

    # an id-SHAPED gate naming something that resolves nowhere is NOT id-gated.
    ck("gate: an unresolvable id does not defer", "M86" in _st_ids_in(root, "backlog.md"), True)
    ck("gate: ...and the warning names the id it could not resolve",
       "it names M99, which resolves in none of" in text, True)
    ck("gate: the plain item with no gate and no marker is untouched",
       "M84" in _st_ids_in(root, "backlog.md"), True)

    # --- THE MUTATION for (1) and (2). Force every gate to read SATISFIED, which takes row 5's
    #     unsatisfied branch -- and with it BOTH terminal `return`s -- out of play, so row 2b
    #     reaches the three items instead. That is the same end state as deleting the returns
    #     (verified separately: deleting :1219 reddens M82/M83's arms, deleting :1226 reddens
    #     M85's, and neither reddens the other's), reached by a mutation that needs no source
    #     edit. The three land in the ONE-WAY archive: the exact defect the design clarification
    #     names, a deferral leaking into an abandonment. OBSERVED RED before the arms above were
    #     kept.
    root_m = _st_mk(tmp, "gate_nonterminal", {"backlog.md": plan}, save=10, baseline=ids)
    real_gate_state = gate_state
    globals()["gate_state"] = lambda it: ("satisfied", it.entry.first("gate"))
    try:
        code_m, _tm = _st_run(["--root", str(root_m), "--rewrote-complete"])
    finally:
        globals()["gate_state"] = real_gate_state
    ck("gate MUTATION: exit", code_m, EXIT_OK)
    ck("gate MUTATION: without the terminal hold, the held items ARE dropped",
       sorted(i for i in ("M82", "M83", "M85") if i in _st_ids_in(root_m, D_DROPPED)),
       ["M82", "M83", "M85"])
    ck("gate MUTATION: ...and the control still holds them in the real run",
       sorted(i for i in ("M82", "M83", "M85") if i in _st_ids_in(root, "backlog.md")),
       ["M82", "M83", "M85"])

    # --- (7) `pending_drops` KEEPS ITS SUBJECT: ABSENT ids, never present ones. Fusing it with
    #     the row-2b marker would add a false-positive channel to the one instrument that catches
    #     silent disappearance (F208).
    ck("gate (7): no PRESENT item is reported as a pending drop", "pending_drop:" in text, False)
    fake = type("_I", (), {"ident": "M82", "label": "M82"})()
    ck("gate (7): pending_drops reports NOTHING for a present item",
       pending_drops(set(["M82"]), [fake], True)[0], [])
    ck("gate (7): ...and DOES report the same id once it is really gone -- the arm can fail",
       pending_drops(set(["M82"]), [], True)[0], ["M82"])


def _st_arms_gate_wedge(ck, tmp):
    """F1, MEASURED 2026-08-07. `gate_admissible` decided admission on FORM alone, so a value that
    WEARS one of D81's two shapes and then disclaims its own condition was appended to a
    GATE-REQUIRED archive at exit 0 -- and `validate_entries` rule A7 aborted the next save at
    exit 3, PERMANENTLY, because the drain's SOURCES exclude `_archive/`.

    NOTE WHAT IS *NOT* USED HERE. The wedge strings on record (`at some point after v4 deploys`,
    `the timing is unclear`, `some day`, `no idea when`) are ALREADY refused as BARE values -- they
    match neither form -- so a regression test built on them passes without touching this defect.
    Both arms below therefore carry a LIVE wedge: one wearing `EXTERNAL - `, one wearing a
    resolvable id and no EXTERNAL token at all. The second is the reason a form-branch-only fix
    would have been useless.

    THE VALIDATOR IS THE ORACLE, not this file's own opinion: each arm runs the real
    `validate_entries` over the folder the drain produced.
    """
    import subprocess
    here = pathlib.Path(__file__).resolve().parent

    def _validate(root):
        p = subprocess.run([sys.executable, "-S", "-I", str(here / "validate_entries.py"),
                            "--root", str(root)], capture_output=True)
        return p.returncode, p.stdout.decode("utf-8", "replace")

    plan = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M1 - a real heading, so the ID-GATED wedge RESOLVES\n"
            "kind: plan\nlifetime: conditional\n\n"
            "## M2 - the EXTERNAL-form wedge\nkind: plan\nlifetime: conditional\n"
            "gate: EXTERNAL - eventually the pilot reports back\n\n"
            "## M3 - the ID-GATED wedge, no EXTERNAL token anywhere\n"
            "kind: plan\nlifetime: conditional\n"
            "gate: M1 lands, whenever that is -- honestly no idea when\n\n"
            "## M7 - CONTROL: a clean EXTERNAL gate\nkind: plan\nlifetime: conditional\n"
            "gate: EXTERNAL - the broker pilot runs one save\n\n"
            "## M8 - CONTROL: a clean ID gate\nkind: plan\nlifetime: conditional\n"
            "gate: M1 lands\n")
    root = _st_mk(tmp, "gate_wedge", {"backlog.md": plan}, save=10, baseline=())

    ck("gate wedge: the fixture is VALID before the drain touches it", _validate(root)[0], 0)
    code, text = _st_run(["--root", str(root)])
    ck("gate wedge: exit", code, EXIT_OK)

    # --- the two wedges STAY HOT, through the EXISTING terminal-hold path.
    for ident in ("M2", "M3"):
        ck("gate wedge: %s is NOT filed in the deferred archive" % ident,
           ident in _st_ids_in(root, D_BACKLOG_DEFERRED), False)
        ck("gate wedge: %s stays in the live plan" % ident,
           ident in _st_ids_in(root, "backlog.md"), True)
        ck("gate wedge: the EXISTING not-admissible warning names %s" % ident,
           ("%s backlog.md:" % ident) in text and "NOT DEFERRED" in text, True)
    ck("gate wedge: the refusal says WHICH test refused it",
       "explicitly disclaims having a condition" in text, True)
    ck("gate wedge: ...and names the cost of admitting it",
       "ABORTS the next save at exit 3" in text, True)

    # --- CONTROL, and it must go the OTHER way: two gates that name a real condition still defer.
    #     A fix that simply stopped deferring would pass every check above and fail these.
    for ident in ("M7", "M8"):
        ck("gate wedge CONTROL: %s STILL defers" % ident,
           ident in _st_ids_in(root, D_BACKLOG_DEFERRED), True)
        ck("gate wedge CONTROL: %s left backlog.md" % ident,
           ident in _st_ids_in(root, "backlog.md"), False)

    # --- THE ORACLE. Before the fix this was exit 3 with an A7 line for M2 and one for M3, and it
    #     stayed exit 3 through three further demote+validate cycles.
    vcode, vtext = _validate(root)
    ck("gate wedge: the folder the drain produced still VALIDATES", vcode, 0)
    ck("gate wedge: no A7 violation was manufactured", "A7 " in vtext, False)
    for i in range(3):
        _c, _t = _st_run(["--root", str(root)])
        ck("gate wedge: re-run %d is still clean" % (i + 1), _validate(root)[0], 0)

    # --- THE OTHER DIRECTION OF THE SAME CONJUNCTION: a disclaimer that matches NEITHER form was
    #     refused before this change too, and must still be -- with the reason naming both halves.
    backlog2 = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## M9 - neither a form nor a condition\nkind: plan\nlifetime: conditional\n"
             "gate: honestly no idea when\n")
    root2 = _st_mk(tmp, "gate_wedge_neither", {"backlog.md": backlog2}, save=10, baseline=())
    _code2, text2 = _st_run(["--root", str(root2)])
    ck("gate wedge/neither: stays hot", "M9" in _st_ids_in(root2, "backlog.md"), True)
    ck("gate wedge/neither: the reason names BOTH failures",
       "explicitly disclaims" in text2 and "matches neither of D81's two forms" in text2, True)

    # --- AND THE UNIT ITSELF, so the conjunction is pinned on both branches independently.
    fams = citemap.build_families()
    ck("gate_admissible: clean EXTERNAL admits",
       gate_admissible("EXTERNAL - the broker pilot runs one save", set(), fams)[0], True)
    ck("gate_admissible: EXTERNAL + disclaimer REFUSED",
       gate_admissible("EXTERNAL - eventually the pilot reports back", set(), fams)[0], False)
    ck("gate_admissible: clean id admits",
       gate_admissible("M1 lands", {"M1"}, fams)[0], True)
    ck("gate_admissible: id + disclaimer REFUSED",
       gate_admissible("M1 lands, whenever that is -- honestly no idea when", {"M1"}, fams)[0],
       False)


def _st_arms_wont_do(ck, tmp):
    """Row 2b moves an item ONLY on a marker a person wrote. Without it, never.

    The A/B pair below IS the falsification: two roots identical but for the `wont-do:` line, run
    with the same arguments against the same baseline. If the marker were not load-bearing both
    would land in the same place.
    """
    def body(marker):
        return ("<!-- ash_save v4 -->\n# plan\n\n"
                "## M95 - in the baseline, not rewritten, no completion marker\nkind: plan\n"
                + marker +
                "\nThe body is identical in both arms.\n\n"
                "<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n")

    with_marker = _st_mk(tmp, "wontdo_with", {"backlog.md": body("wont-do: the maintainer ruled it out\n")},
                         save=10, baseline=("M95",))
    code_a, text_a = _st_run(["--root", str(with_marker), "--rewrote-complete"])
    ck("wont-do (6): exit", code_a, EXIT_OK)
    ck("wont-do (6): WITH the marker, the item is dropped",
       "M95" in _st_ids_in(with_marker, D_DROPPED), True)
    ck("wont-do (6): ...and left backlog.md", "M95" in _st_ids_in(with_marker, "backlog.md"), False)
    ck("wont-do (6): the receipt names it as DROPPED, by id",
       ("M95 dropped -> %s" % D_DROPPED) in text_a, True)
    ck("wont-do (6): the declaration travelled with the entry, so the archive says why",
       b"wont-do: the maintainer ruled it out" in (with_marker / D_DROPPED).read_bytes(), True)

    without = _st_mk(tmp, "wontdo_without", {"backlog.md": body("")},
                     save=10, baseline=("M95",))
    before = _st_snapshot(without)
    code_b, _tb = _st_run(["--root", str(without), "--rewrote-complete"])
    ck("wont-do (6): exit", code_b, EXIT_OK)
    ck("wont-do (6): WITHOUT the marker, the item stays in the live plan",
       "M95" in _st_ids_in(without, "backlog.md"), True)
    ck("wont-do (6): ...and backlog-dropped was not even created",
       (without / D_DROPPED).is_file(), False)
    ck("wont-do (6): ...and not one byte of the folder changed",
       _st_snapshot(without), before)

    # a PLACEHOLDER marker asserts nothing. `backlog-dropped.md` means *never*.
    ph = _st_mk(tmp, "wontdo_placeholder", {"backlog.md": body("wont-do: TBD\n")},
                save=10, baseline=("M95",))
    code_c, text_c = _st_run(["--root", str(ph), "--rewrote-complete"])
    ck("wont-do: placeholder exit", code_c, EXIT_OK)
    ck("wont-do: a placeholder marker does NOT drop", "M95" in _st_ids_in(ph, "backlog.md"), True)
    ck("wont-do: ...and warns", "which asserts nothing" in text_c, True)


def _st_arms_return_path(ck, tmp):
    """(5) THE FULL ROUND TRIP: defer, bring back, save again -- and it is STILL in the live plan.

    Both satisfaction forms are exercised. `gate-satisfied:` is the one the return path should
    write, because it keeps the ORIGINAL waiting line byte-for-byte; the `SATISFIED - ` prefix
    replaces it and is kept only for the values already written that way.
    """
    plan = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M90 - deferred, then brought back\nkind: plan\n"
            "gate: EXTERNAL - the pilot runs one save\n\n"
            "The body must survive the whole trip.\n\n"
            "<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n")
    root = _st_mk(tmp, "roundtrip", {"backlog.md": plan}, save=10, baseline=())

    # --- leg 1: it defers.
    code1, _t1 = _st_run(["--root", str(root), "--rewrote-complete"])
    ck("round trip: leg 1 exit", code1, EXIT_OK)
    ck("round trip: leg 1 deferred it", "M90" in _st_ids_in(root, D_BACKLOG_DEFERRED), True)
    ck("round trip: leg 1 removed it from the live plan",
       "M90" in _st_ids_in(root, "backlog.md"), False)

    # --- the PROMOTION, done the way the model would: the archived bytes go back into backlog.md
    #     with the waiting line marked satisfied and KEPT AS TEXT, and the archived copy is
    #     removed so the id is defined exactly once.
    arch = (root / D_BACKLOG_DEFERRED).read_bytes()
    span = arch[arch.index(b"## M90"):]
    returned = span.replace(b"gate: EXTERNAL - the pilot runs one save\n",
                            b"gate: EXTERNAL - the pilot runs one save\n"
                            b"gate-satisfied: save#11 - the pilot ran\n")
    ck("round trip: the ARCHIVED entry still carries the original gate line",
       b"gate: EXTERNAL - the pilot runs one save" in span, True)
    (root / D_BACKLOG_DEFERRED).write_bytes(arch[:arch.index(b"## M90")])
    hot = (root / "backlog.md").read_bytes()
    cut = hot.index(b"<!-- BACKLOG:BEGIN -->")
    (root / "backlog.md").write_bytes(hot[:cut] + returned.rstrip() + b"\n\n" + hot[cut:])
    ck("round trip: the id is defined exactly once after the promotion",
       (_st_ids_in(root, "backlog.md").count("M90"),
        _st_ids_in(root, D_BACKLOG_DEFERRED).count("M90")), (1, 0))

    # --- leg 2: SAVE AGAIN. This is the leg that fails without a return path.
    code2, _t2 = _st_run(["--root", str(root), "--rewrote-complete"])
    ck("round trip: leg 2 exit", code2, EXIT_OK)
    ck("round trip (5): it is STILL in the live plan after the next save",
       "M90" in _st_ids_in(root, "backlog.md"), True)
    ck("round trip (5): it did NOT bounce back out",
       "M90" in _st_ids_in(root, D_BACKLOG_DEFERRED), False)
    ck("round trip (5): the body survived the whole trip",
       b"The body must survive the whole trip." in (root / "backlog.md").read_bytes(), True)

    # --- THE MUTATION. Take the return path away -- `gate-satisfied:` stops being read -- and the
    #     item re-defers on the very next save, exactly as the design clarification predicts.
    #     OBSERVED RED. Note the gate left behind is ADMISSIBLE, which is what makes this arm
    #     able to fail: with an inadmissible one the new terminal hold would keep it hot anyway
    #     and the test would pass for the wrong reason.
    root_m = _st_mk(tmp, "roundtrip_broken", {"backlog.md": plan}, save=10, baseline=())
    _st_run(["--root", str(root_m), "--rewrote-complete"])
    arch_m = (root_m / D_BACKLOG_DEFERRED).read_bytes()
    span_m = arch_m[arch_m.index(b"## M90"):]
    (root_m / D_BACKLOG_DEFERRED).write_bytes(arch_m[:arch_m.index(b"## M90")])
    hot_m = (root_m / "backlog.md").read_bytes()
    cut_m = hot_m.index(b"<!-- BACKLOG:BEGIN -->")
    (root_m / "backlog.md").write_bytes(
        hot_m[:cut_m]
        + span_m.replace(b"gate: EXTERNAL - the pilot runs one save\n",
                         b"gate: EXTERNAL - the pilot runs one save\n"
                         b"gate-satisfied: save#11 - the pilot ran\n").rstrip()
        + b"\n\n" + hot_m[cut_m:])
    real_keys = GATE_SATISFIED_KEYS
    globals()["GATE_SATISFIED_KEYS"] = ()
    try:
        code_m, _tm = _st_run(["--root", str(root_m), "--rewrote-complete"])
    finally:
        globals()["GATE_SATISFIED_KEYS"] = real_keys
    ck("round trip MUTATION: exit", code_m, EXIT_OK)
    ck("round trip MUTATION: with no return path the item BOUNCES STRAIGHT BACK OUT",
       "M90" in _st_ids_in(root_m, D_BACKLOG_DEFERRED), True)

    # --- the other satisfaction form, on its own root.
    prefixed = plan.replace("gate: EXTERNAL - the pilot runs one save",
                            "gate: SATISFIED - EXTERNAL - the pilot ran")
    root2 = _st_mk(tmp, "roundtrip_prefix", {"backlog.md": prefixed}, save=10, baseline=())
    code3, _t3 = _st_run(["--root", str(root2), "--rewrote-complete"])
    ck("round trip: `SATISFIED - ` prefix exit", code3, EXIT_OK)
    ck("round trip: a `SATISFIED - ` gate keeps the item live",
       "M90" in _st_ids_in(root2, "backlog.md"), True)
    ck("round trip: ...and it is not deferred", (root2 / D_BACKLOG_DEFERRED).is_file(), False)


def _st_arms_question_gate(ck, tmp):
    """D97: a gate naming an OPEN QUESTION is refused as a DESIGN decision, in its own words.

    The BEHAVIOUR here is unchanged and is not the subject -- both the generic refusal and this
    one leave the item hot. The subject is the SENTENCE. The generic wording ("resolves in none
    of backlog.md, ...") reads as a lookup miss and invites the next reader to widen the file list,
    which is the one lesson this refusal must not teach; three ratified decisions in this project
    have been quietly removed by someone who tripped over an undocumented restriction.

    The run is on its OWN root, so `resolves in none of` appearing anywhere in the receipt is
    attributable to this item and nothing else.
    """
    plan = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M8 - a task parked on a question\nkind: plan\n"
            "gate: waiting on OQ7\n\n"
            "<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n")
    root = _st_mk(tmp, "question_gate", {"backlog.md": plan}, save=10, baseline=("M8",))
    code, text = _st_run(["--root", str(root), "--rewrote-complete"])
    ck("gate/D97: exit", code, EXIT_OK)
    ck("gate/D97: the item stays in the live plan", "M8" in _st_ids_in(root, "backlog.md"), True)
    ck("gate/D97: it is not deferred", (root / D_BACKLOG_DEFERRED).is_file(), False)
    ck("gate/D97: a question-id gate is refused as DELIBERATE, not a lookup miss",
       "names an open question (OQ7)" in text and "This is DELIBERATE (D97)" in text, True)
    ck("gate/D97: ...and does not invite the reader to widen the search",
       "resolves in none of" in text, False)
    ck("gate/D97: ...and names the two ways out",
       "PREMATURE" in text and "`gate: EXTERNAL - <the observable" in text, True)

    # THE SUPPORTED DIRECTION, pinned because the message above CLAIMS it works. A claim a
    # warning makes about the tool's own behaviour is a claim the tool should be held to.
    oq = ("<!-- ash_save v4 -->\n# open questions\n\n"
          "## OQ8 - waiting on the work that answers it\nkind: plan\n"
          "gate: waiting on M9\n")
    backlog2 = ("<!-- ash_save v4 -->\n# plan\n\n## M9 - the work\nkind: plan\n\n"
             "<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n")
    root2 = _st_mk(tmp, "question_waits_on_task", {"backlog.md": backlog2, "open_questions.md": oq},
                   save=10, baseline=())
    code2, _t2 = _st_run(["--root", str(root2), "--rewrote-complete"])
    ck("gate/D97: exit", code2, EXIT_OK)
    ck("gate/D97: a QUESTION waiting on a TASK is the supported direction and DEFERS",
       "OQ8" in _st_ids_in(root2, D_OQ_DEFERRED), True)
    ck("gate/D97: ...and left open_questions.md",
       "OQ8" in _st_ids_in(root2, "open_questions.md"), False)

    # a NON-question unresolvable id keeps the generic wording, which for it is correct.
    backlog3 = ("<!-- ash_save v4 -->\n# plan\n\n## M10 - gated on a task that is nowhere\n"
             "kind: plan\ngate: waiting on M99\n\n"
             "<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n")
    root3 = _st_mk(tmp, "nonquestion_gate", {"backlog.md": backlog3}, save=10, baseline=())
    code3, text3 = _st_run(["--root", str(root3), "--rewrote-complete"])
    ck("gate/D97: exit", code3, EXIT_OK)
    ck("gate/D97: a NON-question unresolvable id keeps the lookup-miss wording",
       "it names M99, which resolves in none of" in text3, True)
    ck("gate/D97: ...and does NOT get the question sentence",
       "DELIBERATE (D97)" in text3, False)


def _st_arms_collision(ck, tmp):
    """An id may be defined ONCE. A re-run completes an interrupted move; it does not duplicate."""
    plan = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M70 - finished\nkind: plan\ndone: save#9\n")
    root = _st_mk(tmp, "collide_rerun", {"backlog.md": plan}, save=10, baseline=())
    # simulate the measured partial failure: the destination was written, the source was not.
    payload = (root / "backlog.md").read_bytes()
    span = payload[payload.index(b"## M70"):]
    (root / D_DONE).write_bytes(dest_header(D_DONE) + b"\n" + span)
    code, text = _st_run(["--root", str(root)])
    ck("re-run: exit", code, EXIT_OK)
    ck("re-run: the entry left backlog.md (the move COMPLETED)",
       "M70" in _st_ids_in(root, "backlog.md"), False)
    ck("re-run: exactly ONE definition in the destination",
       _st_ids_in(root, D_DONE).count("M70"), 1)
    ck("re-run: it says what it recognised", "is ALREADY in" in text, True)

    # a DIFFERENT entry with the same id must not be appended over it.
    backlog2 = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## M71 - the live one, gated\nkind: plan\ngate: EXTERNAL - the pilot reports\n\n"
             "This body differs from the archived copy.\n")
    root2 = _st_mk(tmp, "collide_diff", {"backlog.md": backlog2}, save=10, baseline=())
    (root2 / D_BACKLOG_DEFERRED).write_bytes(
        dest_header(D_BACKLOG_DEFERRED)
        + b"\n## M71 - a STALE archived copy\nkind: plan\ngate: the pilot reports\n")
    before2 = _st_snapshot(root2)
    code2, text2 = _st_run(["--root", str(root2)])
    ck("collision: exit", code2, EXIT_OK)
    ck("collision: the live entry STAYS HOT", "M71" in _st_ids_in(root2, "backlog.md"), True)
    ck("collision: nothing was appended", _st_snapshot(root2), before2)
    ck("collision: the refusal is named", "REFUSED (1)" in text2, True)
    ck("collision: and says why -- R-I, the id is there with DIFFERENT bytes",
       "CONFLICT (R-I)" in text2 and "DIFFERENT bytes" in text2, True)

    # ...including two headings sharing one id inside a SINGLE source, which needs no help.
    backlog3 = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## M72 - first\nkind: plan\ndone: save#9\n\n"
             "## M72 - second, same id\nkind: plan\ndone: save#9\n")
    root3 = _st_mk(tmp, "collide_dup", {"backlog.md": backlog3}, save=10, baseline=())
    code3, text3 = _st_run(["--root", str(root3)])
    ck("dup-in-source: exit", code3, EXIT_OK)
    ck("dup-in-source: exactly ONE copy reached the archive",
       _st_ids_in(root3, D_DONE).count("M72"), 1)
    ck("dup-in-source: the second stays hot", "M72" in _st_ids_in(root3, "backlog.md"), True)
    ck("dup-in-source: `get` is not made ambiguous", "REFUSED (1)" in text3, True)


def _st_arms_status_position(ck, tmp):
    """A STATUS marker sits right after the id. Everything else in a heading is a TITLE.

    The `-`-separated-segment containment that closed the first six false positives was itself
    measured wrong, REPRODUCED by the orchestrator 2026-08-03: `(`, `[`, `]` and `;` were in the
    separator set, so each of them manufactured a "segment" whose first word was the marker sitting
    in the middle of a sentence. All four shapes below archived a LIVE entry, ONE-WAY, at exit 0,
    with no warning. Rows 3 and 4 are ONE-WAY (spec S5.1), so a false positive here is
    unrecoverable by design -- which is why BOTH directions are pinned: the prose shapes stay live,
    and this project's own resolved headings, copied verbatim, still fire.
    """
    oq = ("<!-- ash_save v4 -->\n# open questions\n\n"
          # the four REPRODUCED prose shapes, verbatim
          "## OQ30 · is it (RESOLVED) or still open\nkind: plan\n\n"
          "## OQ31 · blocked; RESOLVED in D4 is the precedent\nkind: plan\n\n"
          "## OQ32 · what the [RESOLVED] tag should mean\nkind: plan\n\n"
          "## OQ33 · RESOLVED, or is it\nkind: plan\n\n"
          # three REAL headings, copied verbatim out of this project's own open_questions.md
          "## OQ1 RESOLVED 2026-07-31 - Preimage mechanism that does not duplicate\n"
          "kind: plan\n\n"
          "## OQ13 RESOLVED 2026-08-02 - one threshold, one exclusion list, one number: 64\n"
          "kind: plan\n\n"
          "## OQ21 RESOLVED 2026-08-02 - id under a per-destination header, list collapses at "
          "5% of trigger (D68)\nkind: plan\n\n"
          # ...and a real one that must NOT fire: partially resolved is not resolved.
          "## OQ11 PARTIALLY RESOLVED 2026-08-01 - the deliverable split\nkind: plan\n")
    root = _st_mk(tmp, "statuspos", {"open_questions.md": oq}, save=10)
    code, text = _st_run(["--root", str(root)])
    ck("status position: exit", code, EXIT_OK)
    for ident, why in (("OQ30", "(RESOLVED) inside parentheses"),
                       ("OQ31", "after a `;`, mid-clause"),
                       ("OQ32", "[RESOLVED] inside brackets"),
                       ("OQ33", "in position, but the sentence continues")):
        ck("row3 prose stays LIVE: %s -- %s" % (ident, why),
           ident in _st_ids_in(root, "open_questions.md"), True)
        ck("row3 prose stays LIVE: %s never reached the archive" % ident,
           ident in _st_ids_in(root, D_RESOLVED), False)
    for ident in ("OQ1", "OQ13", "OQ21"):
        ck("row3 REAL corpus heading still fires: %s" % ident,
           ident in _st_ids_in(root, D_RESOLVED), True)
    ck("row3: PARTIALLY RESOLVED is not RESOLVED -- OQ11 stays hot",
       "OQ11" in _st_ids_in(root, "open_questions.md"), True)
    ck("row3: exactly the three real ones moved", len(_st_ids_in(root, D_RESOLVED)), 3)
    ck("row3: every near-miss is NAMED, not silent", text.count("row 3 did not fire"), 5)

    # the position rule itself, at the unit it is decided in
    ck("status position: the id-prefix guard -- M4 is not M40",
       _status_tail("M40 RESOLVED 2026-08-02 - x", "M4"), None)
    ck("status position: no separator is needed",
       _status_tail("OQ1 RESOLVED 2026-07-31 - x", "OQ1"), "RESOLVED 2026-07-31 - x")
    ck("status position: AT MOST ONE separator is consumed",
       _status_tail("OQ1 - - RESOLVED - x", "OQ1"), "- RESOLVED - x")

    # ROW 4, the same pair in both directions.
    arch = ("<!-- ash_save v4 -->\n# architecture\n\n"
            "## D30 · is it (superseded by D9) or still current\nkind: spec\n\n"
            "## D31 · blocked; superseded by D9 is the precedent\nkind: spec\n\n"
            "## D32 · what the [superseded by D9] tag should mean\nkind: spec\n\n"
            "## D33 · superseded by D9, or is it\nkind: spec\n\n"
            "## D34 - a live section that QUOTES the marker\nkind: spec\n\n"
            "> superseded by D9\n\n"
            "## D35 - the genuine heading form - superseded by D9\nkind: spec\n\n"
            "## D36 - the genuine body form\nkind: spec\n\nsuperseded by D9\n\n"
            "## D37 - the genuine bulleted form\nkind: spec\n\n- **superseded by D9** (it went)\n")
    root2 = _st_mk(tmp, "statuspos4", {"architecture.md": arch}, save=10)
    code2, text2 = _st_run(["--root", str(root2)])
    ck("row4 status position: exit", code2, EXIT_OK)
    for ident, why in (("D30", "(superseded by D9) inside parentheses"),
                       ("D31", "after a `;`, mid-clause"),
                       ("D32", "[superseded by D9] inside brackets"),
                       ("D33", "the clause runs past its own object -- `D9,`"),
                       ("D34", "a BLOCKQUOTE is a quotation, not a declaration")):
        ck("row4 prose stays LIVE: %s -- %s" % (ident, why),
           ident in _st_ids_in(root2, "architecture.md"), True)
        ck("row4 prose stays LIVE: %s never reached the archive" % ident,
           ident in _st_ids_in(root2, D_ARCH_SUPERSEDED), False)
    for ident, form in (("D35", "the heading form"), ("D36", "a marker line")):
        ck("row4 genuine marker still fires: %s (%s)" % (ident, form),
           ident in _st_ids_in(root2, D_ARCH_SUPERSEDED), True)
    # M9 again, in the other arm that pinned the bulleted form as genuine.
    ck("row4/M9: the bulleted bolded form is a MENTION -- D37 stays hot",
       "D37" in _st_ids_in(root2, "architecture.md"), True)
    ck("row4: exactly the two real ones moved",
       len(_st_ids_in(root2, D_ARCH_SUPERSEDED)), 2)
    ck("row4: the blockquote near-miss is NAMED", "not in MARKER POSITION" in text2, True)
    ck("row4: the runaway clause is NAMED",
       "does not name a successor in the position row 4 requires" in text2, True)


# THE TWO ROW-3 PIN LISTS, VERBATIM. `(id, heading title)`. Left group: shapes an adversary or the
# orchestrator REPRODUCED being archived ONE-WAY at exit 0 -- every one of them a LIVE entry.
# Right group: this project's own genuine resolved headings, copied out of `open_questions.md`
# byte-for-byte, never retyped. BOTH lists are load-bearing and neither may shrink: an earlier
# version of this rule over-fired on the prose AND fired on ZERO of the real headings at the same
# time (it split `2026-07-31` on the date's own dashes), so a fix measured only against the prose
# would have silently switched v4's growth stop off.
_ST_ROW3_STAY_LIVE = (
    ("OQ9", "OQ9 · RESOLVED-pending-review - still open, do not archive"),   # the hyphen defect
    ("OQ9", "OQ9 · is it (RESOLVED) or still open"),
    ("OQ9", "OQ9 · blocked; RESOLVED in D4 is the precedent"),
    ("OQ9", "OQ9 · what the [RESOLVED] tag should mean"),
    ("OQ9", "OQ9 · RESOLVED, or is it"),
    ("OQ11", "OQ11 PARTIALLY RESOLVED 2026-08-01 - the deliverable split"),
)
_ST_ROW3_MUST_FIRE = (
    ("OQ1", "OQ1 RESOLVED 2026-07-31 - Preimage mechanism that does not duplicate"),
    ("OQ2", "OQ2 RESOLVED 2026-07-31 - Is index+detail-on-demand right for all Tier-1 files?"),
    ("OQ3", "OQ3 RESOLVED 2026-07-31 - Directory-of-files vs anchors vs a database"),
    ("OQ13", "OQ13 RESOLVED 2026-08-02 - one threshold, one exclusion list, one number: 64"),
    ("OQ18", "OQ18 RESOLVED 2026-08-02 - the 20k target was artificial and is withdrawn (D66)"),
    ("OQ19", "OQ19 RESOLVED 2026-08-02 - `impl:` asks, then downgrades (D67)"),
    ("OQ21", "OQ21 RESOLVED 2026-08-02 - id under a per-destination header, list collapses at "
             "5% of trigger (D68)"),
)

# ROW 4's half of the same defect. `TITLE_SPLIT_RE` cut on any `-`, including one joining a
# compound word, so each of these handed the marker a "segment" of its own. The second is the
# already-pinned `NOT superseded by D9` false positive wearing a hyphen.
_ST_ROW4_STAY_LIVE = (
    "D40 · the surface-superseded by D9 question",
    "D41 · not-superseded by D9",
    "D42 · a pre-superseded by D9 clause",
)
# ...and the separators that still DO separate, so the tightening cannot be a silent disarm: a
# spaced dash, a colon facing a space, a spaced middot.
_ST_ROW4_MUST_FIRE = (
    "D43 - the genuine heading form - superseded by D9",
    "D44: superseded by D9",
    "D45 · superseded by D9",
)


def _st_arms_compound_marker(ck, tmp):
    """A separator GLUED to a marker joins a compound WORD. It does not stand the marker alone.

    THE DEFECT, reproduced by the orchestrator 2026-08-03, one-way, exit 0, nothing on stderr:
    `RESOLVED_MARKER_RE` read `^RESOLVED\\b[ \\t]*(?:$|[-...]|...)`. `[ \\t]*` permits ZERO
    whitespace and `-` is in the separator class, so the branch that exists to prove the token
    TERMINATES was satisfied by a hyphen inside a compound word:

        ## OQ9 · RESOLVED-pending-review - still open, do not archive
        -> demote: OQ9 resolved -> _archive/resolved-oqs.md (127 B)

    Row 4 shared the shape one level up, in `TITLE_SPLIT_RE`.

    BOTH DIRECTIONS ARE PINNED, in the unit the rule is decided in and end-to-end through `main`.
    The end-to-end pass renumbers the five `OQ9` shapes to OQ40-OQ44 for one reason only -- a file
    may not define an id twice -- and derives them from the verbatim list above rather than
    restating them, so the two passes cannot drift apart.
    """
    # --- the unit, on the VERBATIM headings ------------------------------------------------
    for ident, title in _ST_ROW3_STAY_LIVE:
        ck("row3 MUST STAY LIVE (unit): ## %s" % title,
           bool(RESOLVED_MARKER_RE.match(_status_tail(title, ident) or "")), False)
    for ident, title in _ST_ROW3_MUST_FIRE:
        ck("row3 MUST STILL FIRE (unit): ## %s" % title,
           bool(RESOLVED_MARKER_RE.match(_status_tail(title, ident) or "")), True)

    # --- and end-to-end, through the mover that would have deleted them ---------------------
    lines = ["<!-- ash_save v4 -->", "# open questions", ""]
    renumbered = []
    for n, (_ident, title) in enumerate(_ST_ROW3_STAY_LIVE[:5]):
        alias = "OQ%d" % (40 + n)
        renumbered.append(alias)
        lines += ["## %s %s" % (alias, title.split(None, 1)[1]), "kind: plan", ""]
    lines += ["## %s" % _ST_ROW3_STAY_LIVE[5][1], "kind: plan", ""]
    for _ident, title in _ST_ROW3_MUST_FIRE:
        lines += ["## %s" % title, "kind: plan", ""]
    root = _st_mk(tmp, "compound3", {"open_questions.md": "\n".join(lines) + "\n"}, save=10)
    code, text = _st_run(["--root", str(root)])
    ck("compound row3: exit", code, EXIT_OK)
    hot, gone = _st_ids_in(root, "open_questions.md"), _st_ids_in(root, D_RESOLVED)
    for alias, (_i, title) in zip(renumbered, _ST_ROW3_STAY_LIVE[:5]):
        ck("row3 MUST STAY LIVE (e2e): %s -- %s" % (alias, title.split(None, 1)[1]),
           alias in hot, True)
        ck("row3 MUST STAY LIVE (e2e): %s never reached the archive" % alias,
           alias in gone, False)
    ck("row3 MUST STAY LIVE (e2e): OQ11 PARTIALLY RESOLVED stays hot", "OQ11" in hot, True)
    ck("row3 MUST STAY LIVE (e2e): OQ11 never reached the archive", "OQ11" in gone, False)
    for ident, title in _ST_ROW3_MUST_FIRE:
        ck("row3 MUST STILL FIRE (e2e): %s -- %s" % (ident, title), ident in gone, True)
    ck("row3: exactly the seven real corpus headings moved, and nothing else", len(gone),
       len(_ST_ROW3_MUST_FIRE))
    ck("row3: every one of the six near-misses is NAMED, not silent",
       text.count("row 3 did not fire"), len(_ST_ROW3_STAY_LIVE))

    # --- row 4, the same defect through the segment splitter --------------------------------
    arch = ["<!-- ash_save v4 -->", "# architecture", ""]
    for title in _ST_ROW4_STAY_LIVE + _ST_ROW4_MUST_FIRE:
        arch += ["## %s" % title, "kind: spec", ""]
    root4 = _st_mk(tmp, "compound4", {"architecture.md": "\n".join(arch) + "\n"}, save=10)
    code4, _text4 = _st_run(["--root", str(root4)])
    ck("compound row4: exit", code4, EXIT_OK)
    hot4, gone4 = _st_ids_in(root4, "architecture.md"), _st_ids_in(root4, D_ARCH_SUPERSEDED)
    for title in _ST_ROW4_STAY_LIVE:
        ident = title.split(None, 1)[0].rstrip(":")
        ck("row4 MUST STAY LIVE: %s -- ## %s" % (ident, title), ident in hot4, True)
        ck("row4 MUST STAY LIVE: %s never reached the archive" % ident, ident in gone4, False)
    for title in _ST_ROW4_MUST_FIRE:
        ident = title.split(None, 1)[0].rstrip(":")
        ck("row4 MUST STILL FIRE: %s -- ## %s" % (ident, title), ident in gone4, True)
    ck("row4: exactly the three genuine separator forms moved", len(gone4),
       len(_ST_ROW4_MUST_FIRE))


def _st_arms_already(ck, tmp):
    """THE ALREADY PATH: the one branch that writes NOTHING, and the only one that only deletes.

    MEASURED 2026-08-03 by the orchestrator. Recognition was `payload.rstrip() in existing` -- a
    SUBSTRING test against the destination's WHOLE TEXT standing in for the byte-equality one the
    note it printed claimed to have made -- and `perform` extended `moved` with the result BEFORE
    the write/verify loop, so R-H never saw it. The one path that writes nothing was the one path
    with no byte-equality check.
    """
    import subprocess
    here = pathlib.Path(__file__).resolve().parent

    # --- (1) THE ROUND TRIP. spec S5.1 calls row 5 REVERSIBLE, so defer -> promote back -> edit ->
    #         re-defer is the DESIGNED workflow, not an exotic state: the archive holds two
    #         paragraphs and the hot copy holds one, because the author deleted the other TODAY.
    hot = ("<!-- ash_save v4 -->\n# plan\n\n"
           "## M4 - the deferred item\nkind: plan\ngate: EXTERNAL - the pilot reports\n\n"
           "First paragraph, kept.\n")
    arch = ("<!-- ash_save v4 -->\n# deferred\n\n"
            "## M4 - the deferred item\nkind: plan\ngate: EXTERNAL - the pilot reports\n\n"
            "First paragraph, kept.\n\n"
            "SECOND PARAGRAPH WHICH THE AUTHOR DELETED THIS SESSION.\n")
    hot_b, arch_b = hot.encode("utf-8"), arch.encode("utf-8")
    hot_span = hot_b[hot_b.index(b"## M4"):]
    arch_span = arch_b[arch_b.index(b"## M4"):]

    root = _st_mk(tmp, "already_roundtrip", {"backlog.md": hot, D_BACKLOG_DEFERRED: arch},
                  save=10, baseline=())
    before = _st_snapshot(root)
    code, text = _st_run(["--root", str(root)])
    ck("already/round trip: exit", code, EXIT_OK)
    ck("already/round trip: NOTHING moved", text.startswith("demote: 0 entries moved"), True)
    ck("already/round trip: not one byte of the folder changed", _st_snapshot(root), before)
    ck("already/round trip: the hot copy is still hot", "M4" in _st_ids_in(root, "backlog.md"), True)
    ck("already/round trip: it is REFUSED, not moved", "REFUSED (1)" in text, True)
    ck("already/round trip: R-I names the id", "CONFLICT (R-I): M4" in text, True)
    ck("already/round trip: R-I names BOTH sites",
       (D_BACKLOG_DEFERRED in text) and ("backlog.md:4" in text), True)
    ck("already/round trip: R-I names BOTH byte counts",
       ("(%d B)" % len(arch_span)) in text and ("versus %d B" % len(hot_span)) in text, True)
    ck("already/round trip: it is WARNED about, not only tabulated",
       "MOVE WAS REFUSED: CONFLICT (R-I)" in text, True)
    ck("already/round trip: THE DELETED PARAGRAPH DID NOT COME BACK",
       b"DELETED THIS SESSION" in (root / "backlog.md").read_bytes(), False)
    got = subprocess.run([sys.executable, "-S", "-I", str(here / "recall.py"),
                          "--root", str(root), "get", "--file", "backlog.md", "M4"],
                         capture_output=True)
    ck("already/round trip: `get M4 --file backlog.md` exit", got.returncode, 0)
    ck("already/round trip: `get` returns the HOT text, byte for byte",
       got.stdout.rstrip(), hot_span.rstrip())
    ck("already/round trip: ...and it does not carry the deleted paragraph",
       b"DELETED THIS SESSION" in got.stdout, False)

    # --- (1b) THE SAME ROUND TRIP WITH A NESTED CHILD INSTEAD OF A TRAILING PARAGRAPH, and (1)
    #          CANNOT FAIL ON IT -- prose is not a heading, so every fixture above stops one step
    #          short of this shape. `_payload_ends_at` accepted ANY following heading as "where
    #          the next append started", so a `###` child the author deleted THIS session read as
    #          an entry boundary, the destination's strict superset read as byte-equal, the hot
    #          copy was DELETED at exit 0, and the receipt said ALREADY. Measured 2026-08-07
    #          against the composite: `get M7` then answered with the child the author removed.
    #          Same defect as (1), same ruling R-I, reached through the one door (1) does not open.
    hot7 = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M7 - the item with a child\nkind: plan\ngate: EXTERNAL - the pilot reports\n\n"
            "First paragraph, kept.\n")
    arch7 = ("<!-- ash_save v4 -->\n# deferred\n\n"
             "## M7 - the item with a child\nkind: plan\ngate: EXTERNAL - the pilot reports\n\n"
             "First paragraph, kept.\n\n"
             "### A CHILD SECTION THE AUTHOR DELETED THIS SESSION\nIts body.\n")
    hot7_b, arch7_b = hot7.encode("utf-8"), arch7.encode("utf-8")
    hot7_span = hot7_b[hot7_b.index(b"## M7"):]
    root7 = _st_mk(tmp, "already_child", {"backlog.md": hot7, D_BACKLOG_DEFERRED: arch7},
                   save=10, baseline=())
    ck("already/child: the fixture really IS a strict prefix, so only DEPTH separates them",
       arch7_b[arch7_b.index(b"## M7"):].startswith(hot7_span), True)
    before7 = _st_snapshot(root7)
    code7, text7 = _st_run(["--root", str(root7)])
    ck("already/child: exit", code7, EXIT_OK)
    ck("already/child: NOTHING moved", text7.startswith("demote: 0 entries moved"), True)
    ck("already/child: not one byte of the folder changed", _st_snapshot(root7), before7)
    ck("already/child: THE HOT COPY IS STILL HOT", "M7" in _st_ids_in(root7, "backlog.md"), True)
    ck("already/child: NOT reported as already-present", "M7 is ALREADY in" in text7, False)
    ck("already/child: it is the R-I CONFLICT, named by id", "CONFLICT (R-I): M7" in text7, True)
    ck("already/child: THE DELETED CHILD DID NOT COME BACK",
       b"DELETED THIS SESSION" in (root7 / "backlog.md").read_bytes(), False)
    got7 = subprocess.run([sys.executable, "-S", "-I", str(here / "recall.py"),
                           "--root", str(root7), "get", "--file", "backlog.md", "M7"],
                          capture_output=True)
    ck("already/child: `get M7 --file backlog.md` returns the HOT text, byte for byte",
       got7.stdout.rstrip(), hot7_span.rstrip())

    # --- (2) THE FENCED-QUOTE SHAPE, the more severe one. The destination defines M5 with an OLD
    #         body AND a DIFFERENT entry there quotes the hot M5 verbatim inside a fence. The
    #         substring test matched the QUOTE: the hot entry was deleted, ZERO bytes were
    #         appended, and `get` returned an unrelated older body.
    hot5 = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M5 - the quoted item\nkind: plan\ngate: EXTERNAL - the pilot reports\n\n"
            "The body the author has now.\n")
    quoted = ("<!-- ash_save v4 -->\n# deferred\n\n"
              "## M5 - the quoted item\nkind: plan\ngate: an OLDER gate\n\n"
              "An OLDER body nobody asked for.\n\n"
              "## M6 - a DIFFERENT entry, which quotes M5 verbatim\nkind: plan\ngate: g\n\n"
              "```\n"
              "## M5 - the quoted item\nkind: plan\ngate: EXTERNAL - the pilot reports\n\n"
              "The body the author has now.\n"
              "```\n")
    root2 = _st_mk(tmp, "already_fencequote", {"backlog.md": hot5, D_BACKLOG_DEFERRED: quoted},
                   save=10, baseline=())
    hot5_b = hot5.encode("utf-8")
    ck("already/fenced quote: the fixture really does contain the payload verbatim",
       hot5_b[hot5_b.index(b"## M5"):].rstrip() in quoted.encode("utf-8"), True)
    before2 = _st_snapshot(root2)
    code2, text2 = _st_run(["--root", str(root2)])
    ck("already/fenced quote: exit", code2, EXIT_OK)
    ck("already/fenced quote: a quote elsewhere is not the id's SPAN -- no ALREADY",
       "is ALREADY in" in text2, False)
    ck("already/fenced quote: the hot entry was NOT deleted",
       "M5" in _st_ids_in(root2, "backlog.md"), True)
    ck("already/fenced quote: nothing on disk changed", _st_snapshot(root2), before2)
    ck("already/fenced quote: reported as the CONFLICT it is", "CONFLICT (R-I): M5" in text2, True)

    # --- (3) THE GENUINE INTERRUPTED RUN: the destination span IS the hot payload, byte for byte.
    #         This branch stays, its note becomes TRUE, and it is pinned.
    backlog7 = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## M7 - finished\nkind: plan\ndone: save#9\n")
    root3 = _st_mk(tmp, "already_genuine", {"backlog.md": backlog7}, save=10, baseline=())
    src3 = (root3 / "backlog.md").read_bytes()
    span3 = src3[src3.index(b"## M7"):]
    dest3 = dest_header(D_DONE) + b"\n" + span3
    (root3 / D_DONE).write_bytes(dest3)
    code3, text3 = _st_run(["--root", str(root3)])
    ck("already/genuine: exit", code3, EXIT_OK)
    ck("already/genuine: the hot copy IS removed -- the move COMPLETES",
       "M7" in _st_ids_in(root3, "backlog.md"), False)
    ck("already/genuine: exactly ONE definition survives",
       _st_ids_in(root3, D_DONE).count("M7"), 1)
    ck("already/genuine: the destination was not rewritten", (root3 / D_DONE).read_bytes(), dest3)
    ck("already/genuine: the note says what it actually compared",
       "byte for byte -- VERIFIED against the span" in text3, True)
    got3 = subprocess.run([sys.executable, "-S", "-I", str(here / "recall.py"),
                           "--root", str(root3), "get", "M7"], capture_output=True)
    ck("already/genuine: `get M7` resolves at exactly ONE site", got3.returncode, 0)
    ck("already/genuine: ...and returns the moved bytes", got3.stdout.rstrip(), span3.rstrip())

    # --- (4) AN ID THAT IS ONLY A SUBSTRING OF A LONGER ONE. `M4` is not `M40`.
    hot8 = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M4 - the short id\nkind: plan\ngate: EXTERNAL - the pilot reports\n")
    dest8 = ("<!-- ash_save v4 -->\n# deferred\n\n"
             "## M40 - the LONGER id, which merely starts with M4\nkind: plan\ngate: g\n")
    root4 = _st_mk(tmp, "already_prefix", {"backlog.md": hot8, D_BACKLOG_DEFERRED: dest8},
                   save=10, baseline=())
    code4, text4 = _st_run(["--root", str(root4)])
    ck("already/id prefix: exit", code4, EXIT_OK)
    ck("already/id prefix: M4 moved -- M40 is a DIFFERENT id",
       "M4" in _st_ids_in(root4, D_BACKLOG_DEFERRED), True)
    ck("already/id prefix: it left backlog.md", "M4" in _st_ids_in(root4, "backlog.md"), False)
    ck("already/id prefix: M40 is untouched",
       _st_ids_in(root4, D_BACKLOG_DEFERRED).count("M40"), 1)
    ck("already/id prefix: no false ALREADY", "is ALREADY in" in text4, False)
    ck("already/id prefix: no false CONFLICT", "CONFLICT (R-I)" in text4, False)

    # --- (5) THE ROUTE ITSELF. No path that removes bytes from a hot file may skip R-H, and the
    #         ALREADY path skipping it is not observable in the OUTPUT of a run that succeeds --
    #         so it is asserted on the CALL. Revert the routing in `perform` and this goes red.
    global _verify_moves
    real, calls = _verify_moves, []

    def _spy(root_, rel_, moves_, offsets_, families_, notes_):
        calls.append([i.label for (i, _d) in moves_])
        return real(root_, rel_, moves_, offsets_, families_, notes_)

    root5 = _st_mk(tmp, "already_routed", {"backlog.md": backlog7}, save=10, baseline=())
    (root5 / D_DONE).write_bytes(dest_header(D_DONE) + b"\n" + span3)
    _verify_moves = _spy
    try:
        code5, _t5 = _st_run(["--root", str(root5)])
    finally:
        _verify_moves = real
    ck("already/routing: exit", code5, EXIT_OK)
    ck("already/routing: R-H SAW the entry the ALREADY path removed", ["M7"] in calls, True)
    ck("already/routing: and the entry did leave the hot file",
       "M7" in _st_ids_in(root5, "backlog.md"), False)


def _st_arms_conflict_arity(ck, tmp):
    """F5, MEASURED 2026-08-07. `screen_batch` chooses the refusal branch by ARITY
    (`len(sites) == 1 and same` -> ALREADY, `elif sites:` -> CONFLICT), so an id resolving at MORE
    THAN ONE destination site is refused whether or not the bytes match -- and `_conflict_why`
    asserted "with DIFFERENT bytes" on every one of them.

    The arms below pin the MESSAGE, not the decision. The decision is deliberately unchanged: the
    fixture's two destination sites both match the hot payload byte for byte, and the entry still
    does not move. P2, and the reason the old sentence here could not stand: it said admitting it
    "would append into a file that already defines the id twice", and admitting it appends NOTHING
    -- a match routes to the RECONCILE path, whose whole action is splicing the HOT copy. The real
    cost is the worse one. Splicing would delete the last copy the drain can read (its SOURCES
    exclude `_archive/`) into an ambiguity the destination already has, and nothing could walk that
    back. D74 refuses the trade for a reason, but not for the reason that was written down.
    """
    entry = ("## M4 - the deferred item\nkind: plan\ngate: EXTERNAL - the pilot reports\n\n"
             "First paragraph, kept.\n")
    hot = "<!-- ash_save v4 -->\n# plan\n\n" + entry

    # --- (1) TWO SITES, BOTH MATCHING. They differ only by the separator newline the builder
    #         writes between entries -- which falls inside the FIRST site's `heading_map` span and
    #         outside the last one's, so the byte counts printed here (97 B / 96 B, hot payload
    #         96 B) are NOT evidence of a difference in content.
    root = _st_mk(tmp, "conflict_two_same",
                  {"backlog.md": hot, D_BACKLOG_DEFERRED: "<!-- ash_save v4 -->\n# deferred\n\n"
                                                    + entry + "\n" + entry},
                  save=10, baseline=())
    before = _st_snapshot(root)
    code, text = _st_run(["--root", str(root)])
    ck("conflict/two same: exit", code, EXIT_OK)
    ck("conflict/two same: REFUSED, exactly as before", "REFUSED (1)" in text, True)
    ck("conflict/two same: THE PREDICATE DID NOT MOVE -- nothing on disk changed",
       _st_snapshot(root), before)
    ck("conflict/two same: the entry is still hot", "M4" in _st_ids_in(root, "backlog.md"), True)
    ck("conflict/two same: the message states the ARITY it actually branched on",
       "M4 is DEFINED AT 2 SITES in %s" % D_BACKLOG_DEFERRED in text, True)
    ck("conflict/two same: ...and says the bytes DO match",
       "2 of those sites match the hot bytes BYTE FOR BYTE" in text, True)
    ck("conflict/two same: THE FALSE SENTENCE IS GONE", "with DIFFERENT bytes" in text, False)
    ck("conflict/two same: both sites are still named",
       text.count("%s bytes " % D_BACKLOG_DEFERRED) >= 2, True)

    # --- (2) CONTROL, and it must go the OTHER way: ONE site, bytes genuinely different. The
    #         original sentence is TRUE here and has to survive the change.
    other = ("## M4 - the deferred item\nkind: plan\ngate: EXTERNAL - the pilot reports\n\n"
             "A COMPLETELY DIFFERENT paragraph.\n")
    root2 = _st_mk(tmp, "conflict_one_diff",
                   {"backlog.md": hot, D_BACKLOG_DEFERRED: "<!-- ash_save v4 -->\n# deferred\n\n"
                                                     + other},
                   save=10, baseline=())
    before2 = _st_snapshot(root2)
    code2, text2 = _st_run(["--root", str(root2)])
    ck("conflict/one different: exit", code2, EXIT_OK)
    ck("conflict/one different: still REFUSED", "REFUSED (1)" in text2, True)
    ck("conflict/one different: nothing on disk changed", _st_snapshot(root2), before2)
    ck("conflict/one different: the DIFFERENT-bytes sentence is kept, because it is true",
       "M4 is ALREADY DEFINED in %s with DIFFERENT bytes" % D_BACKLOG_DEFERRED in text2, True)
    ck("conflict/one different: and it does NOT claim an arity it does not have",
       "DEFINED AT" in text2, False)

    # --- (3) TWO SITES, NEITHER MATCHING. Same branch, and the message must say so rather than
    #         reuse (1)'s sentence.
    root3 = _st_mk(tmp, "conflict_two_diff",
                   {"backlog.md": hot, D_BACKLOG_DEFERRED: "<!-- ash_save v4 -->\n# deferred\n\n"
                                                     + other + "\n" + other},
                   save=10, baseline=())
    code3, text3 = _st_run(["--root", str(root3)])
    ck("conflict/two different: exit", code3, EXIT_OK)
    ck("conflict/two different: names the arity",
       "M4 is DEFINED AT 2 SITES in %s" % D_BACKLOG_DEFERRED in text3, True)
    ck("conflict/two different: ...and says NONE of them matched",
       "NONE of those sites matches the hot bytes" in text3, True)
    ck("conflict/two different: it does not claim a match it did not find",
       "match the hot bytes BYTE FOR BYTE" in text3, False)

    # --- (4) ONE STEP OUT of (1): TWO sites, EXACTLY ONE matching. Neither (1)'s sentence nor
    #         (3)'s is true here, so the count has to be real rather than a two-way switch.
    root4 = _st_mk(tmp, "conflict_two_mixed",
                   {"backlog.md": hot, D_BACKLOG_DEFERRED: "<!-- ash_save v4 -->\n# deferred\n\n"
                                                     + entry + "\n" + other},
                   save=10, baseline=())
    before4 = _st_snapshot(root4)
    code4, text4 = _st_run(["--root", str(root4)])
    ck("conflict/two mixed: exit", code4, EXIT_OK)
    ck("conflict/two mixed: still REFUSED, still nothing on disk moved",
       ("REFUSED (1)" in text4) and (_st_snapshot(root4) == before4), True)
    ck("conflict/two mixed: the count is the real one, and it agrees in number",
       "1 of those sites matches the hot bytes BYTE FOR BYTE" in text4, True)
    ck("conflict/two mixed: it does not claim NONE matched",
       "NONE of those sites" in text4, False)


def _st_arms_identless_already(ck, tmp):
    """F4, MEASURED 2026-08-07. `if it.ident and it.ident in seen:` short-circuits for an item
    with no citemap-resolvable id, so the whole already/conflict block was skipped and the entry
    appended unconditionally -- and `_verify_moves` skips its id-resolution check for the same
    items, so nothing downstream reported it either. Copies went 1 -> 2 -> 3, receipt constant,
    exit 0 throughout.

    THE FIXTURE HAS TO RESTORE THE HOT COPY. A bare re-run proves nothing: run 1 splices the
    source, so run 2 scans zero items and exits clean whether the defect is present or not. What
    is rebuilt below is the INTERRUPTED-RUN state -- destination written, source NOT yet spliced --
    which `perform`'s ordering deliberately produces and which the documented recovery is to re-run
    from.

    AND `recall check` CANNOT BE THE ORACLE HERE. An identless entry has no id, so duplicating
    it produces no collision; the oracle exits 0 with three copies on disk. The destination's own
    bytes are the measurement.
    """
    plan = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## an item whose heading matches no citemap family\n"
            "kind: plan\nlifetime: conditional\ndone: save#9\n\n"
            "## M5 - CONTROL, and it HAS an id\n"
            "kind: plan\nlifetime: conditional\ndone: save#9\n")
    hot_b = plan.encode("utf-8")
    root = _st_mk(tmp, "identless_already", {"backlog.md": plan}, save=10, baseline=())

    code, text = _st_run(["--root", str(root)])
    ck("identless/first move: exit", code, EXIT_OK)
    ck("identless/first move: the receipt marks the id-less label with `~`",
       "~an done -> %s" % D_DONE in text, True)
    dest_after_1 = (root / D_DONE).read_bytes()
    ck("identless/first move: the destination holds it once",
       dest_after_1.count(b"## an item whose heading"), 1)
    ck("identless/first move: the source WAS spliced",
       b"## an item whose heading" in (root / "backlog.md").read_bytes(), False)

    # --- THE RECOVERY. Two more cycles, each from the interrupted-run state.
    for n in (2, 3):
        (root / "backlog.md").write_bytes(hot_b)
        code_n, text_n = _st_run(["--root", str(root)])
        ck("identless/recovery %d: exit" % n, code_n, EXIT_OK)
        ck("identless/recovery %d: the destination is byte-identical -- NO second copy" % n,
           (root / D_DONE).read_bytes(), dest_after_1)
        ck("identless/recovery %d: the move still COMPLETES (source spliced)" % n,
           b"## an item whose heading" in (root / "backlog.md").read_bytes(), False)
        ck("identless/recovery %d: it is reported as ALREADY, not silently" % n,
           "~an is ALREADY in %s" % D_DONE in text_n, True)
        ck("identless/recovery %d: the note never renders `it.ident` as the word None" % n,
           "None is ALREADY in" in text_n, False)
        ck("identless/recovery %d: ...and does not promise `get` resolves it" % n,
           "`recall get` cannot address this entry either way" in text_n, True)
        ck("identless/recovery %d: the id-bearing CONTROL took the same path" % n,
           "M5 is ALREADY in %s" % D_DONE in text_n, True)

    # --- CONTROL: an identless entry the destination does NOT hold still APPENDS. A fix that
    #     simply stopped appending identless entries would pass every check above and fail this.
    backlog2 = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## a brand-new identless item\nkind: plan\nlifetime: conditional\n"
             "done: save#9\n")
    root2 = _st_mk(tmp, "identless_new", {"backlog.md": backlog2}, save=10, baseline=())
    (root2 / D_DONE).write_bytes(dest_header(D_DONE) + b"\n"
                                 + b"## SOMETHING ELSE ENTIRELY\nkind: plan\n")
    code2, text2 = _st_run(["--root", str(root2)])
    ck("identless CONTROL/new: exit", code2, EXIT_OK)
    ck("identless CONTROL/new: it APPENDS",
       b"## a brand-new identless item" in (root2 / D_DONE).read_bytes(), True)
    ck("identless CONTROL/new: no false ALREADY", "is ALREADY in" in text2, False)

    # --- CONTROL, ONE STEP OUT: the archived copy is a strict SUPERSET of the hot payload (the
    #     author deleted a paragraph today). A plain prefix search would call this ALREADY and
    #     splice the edit away; `_payload_ends_at` must not, because what follows the payload is
    #     PROSE rather than the start of another entry.
    hot3 = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## an identless finished item\nkind: plan\nlifetime: conditional\n"
            "done: save#9\n\nFirst paragraph, kept.\n")
    arch3 = ("<!-- ash_save v4 -->\n# done\n\n"
             "## an identless finished item\nkind: plan\nlifetime: conditional\n"
             "done: save#9\n\nFirst paragraph, kept.\n\n"
             "SECOND PARAGRAPH THE AUTHOR DELETED THIS SESSION.\n")
    root3 = _st_mk(tmp, "identless_superset", {"backlog.md": hot3, D_DONE: arch3},
                   save=10, baseline=())
    hb3 = hot3.encode("utf-8")
    ab3 = arch3.encode("utf-8")
    ck("identless/superset: the fixture really is a strict prefix",
       ab3[ab3.index(b"## an"):].startswith(hb3[hb3.index(b"## an"):]), True)
    code3, text3 = _st_run(["--root", str(root3)])
    ck("identless/superset: exit", code3, EXIT_OK)
    ck("identless/superset: NOT called ALREADY", "is ALREADY in" in text3, False)
    ck("identless/superset: the deleted paragraph did not come back",
       b"DELETED THIS SESSION" in (root3 / "backlog.md").read_bytes(), False)

    # --- CONTROL, the other one step out: the payload sits VERBATIM in the destination, inside a
    #     FENCE, under a different entry. That is the shape ruling R-I was written to kill.
    hot4 = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## a fenced-quote identless item\nkind: plan\nlifetime: conditional\n"
            "done: save#9\n")
    arch4 = ("<!-- ash_save v4 -->\n# done\n\n"
             "## M9 - some OTHER entry, which quotes it verbatim\n"
             "kind: plan\nlifetime: conditional\n\n"
             "```\n"
             "## a fenced-quote identless item\nkind: plan\nlifetime: conditional\n"
             "done: save#9\n"
             "```\n")
    root4 = _st_mk(tmp, "identless_fenced", {"backlog.md": hot4, D_DONE: arch4},
                   save=10, baseline=())
    ck("identless/fenced: the fixture really does contain the payload verbatim",
       hot4.encode("utf-8")[hot4.encode("utf-8").index(b"## a fenced"):].rstrip()
       in arch4.encode("utf-8"), True)
    code4, text4 = _st_run(["--root", str(root4)])
    ck("identless/fenced: exit", code4, EXIT_OK)
    ck("identless/fenced: a quote inside a fence is not a definition -- no ALREADY",
       "is ALREADY in" in text4, False)
    ck("identless/fenced: so it APPENDS, and the entry is not lost",
       (root4 / D_DONE).read_bytes().count(b"## a fenced-quote identless item"), 2)


def _st_arms_cross_dest(ck, tmp):
    """F2, MEASURED 2026-08-07. The already/conflict guard's universe is the ONE archive the firing
    row routes to, so an id already defined in a DIFFERENT archive is invisible to it.

    THE STORY IS THE DOCUMENTED RECOVERY, which is why this is not exotic: the entry was deferred
    last save, someone edits the MARKER while recovering (`gate:` becomes `done: save#5`), row 2
    now routes it to `_archive/backlog-done.md` where the id has zero sites, and it is APPENDED --
    exit 0, no REFUSED, no WARNINGS, the old copy untouched, the hot copy spliced. End state: one
    id defined in TWO archives and hot in NEITHER, which no later run of this tool can even see,
    because the drain's SOURCES exclude `_archive/`.

    ARM (2) IS THE LOAD-BEARING ONE. It runs the identical fixture with the routed-to destination
    ALREADY PRESENT, so `_dest_spans` genuinely executes over it. Same outcome. That is what makes
    this a defect in the guard's UNIVERSE rather than in its handling of an absent file.
    """
    deferred = ("<!-- ash_save v4 -->\n# deferred\n\n"
                "## M4 - the entry that was deferred last save\n"
                "kind: plan\nlifetime: conditional\ngate: EXTERNAL - the pilot reports\n\n"
                "The body as it was archived.\n")
    hot = ("<!-- ash_save v4 -->\n# plan\n\n"
           "## M4 - the entry that was deferred last save\n"
           "kind: plan\nlifetime: conditional\ndone: save#5\n\n"
           "The body as it was archived.\n")
    other_done = ("<!-- ash_save v4 -->\n# done\n\n"
                  "## M77 - an unrelated finished entry\nkind: plan\nlifetime: conditional\n")

    for (tag, extra) in (("absent", {}), ("present", {D_DONE: other_done})):
        files = {"backlog.md": hot, D_BACKLOG_DEFERRED: deferred}
        files.update(extra)
        root = _st_mk(tmp, "cross_dest_" + tag, files, save=10, baseline=())
        before = _st_snapshot(root)
        code, text = _st_run(["--root", str(root)])
        ck("cross-dest/%s: exit" % tag, code, EXIT_OK)
        ck("cross-dest/%s: NOTHING moved" % tag,
           text.startswith("demote: 0 entries moved"), True)
        ck("cross-dest/%s: it is REFUSED, not silently appended" % tag,
           "REFUSED (1)" in text, True)
        ck("cross-dest/%s: not one byte of the folder changed" % tag,
           _st_snapshot(root), before)
        ck("cross-dest/%s: the entry is still HOT, so a human can reconcile it" % tag,
           "M4" in _st_ids_in(root, "backlog.md"), True)
        ck("cross-dest/%s: it did NOT reach the routed-to archive" % tag,
           "M4" in _st_ids_in(root, D_DONE), False)
        ck("cross-dest/%s: the message names the archive it is IN" % tag,
           "CROSS-FILE CONFLICT (R-I): M4 is ALREADY DEFINED IN ANOTHER ARCHIVE" in text
           and D_BACKLOG_DEFERRED in text, True)
        ck("cross-dest/%s: ...and the archive it was headed FOR" % tag,
           ("routes it to %s" % D_DONE) in text, True)
        ck("cross-dest/%s: ...and says why no re-run could undo it" % tag,
           "the drain's SOURCES exclude" in text, True)
        ck("cross-dest/%s: it is WARNED about, not only tabulated" % tag,
           "MOVE WAS REFUSED: CROSS-FILE CONFLICT" in text, True)
        ck("cross-dest/%s: the single-file message did NOT fire" % tag,
           "is ALREADY DEFINED in %s with DIFFERENT bytes" % D_DONE in text, False)

    # --- CONTROL, and it must go the OTHER way: the other archive holds a DIFFERENT id, so there
    #     is no cross-file conflict and the entry moves exactly as it did before F2. A fix that
    #     simply refused everything with a populated archive set would fail here.
    other_id = deferred.replace("## M4 - ", "## M6 - ")
    root2 = _st_mk(tmp, "cross_dest_control",
                   {"backlog.md": hot, D_BACKLOG_DEFERRED: other_id, D_DONE: other_done},
                   save=10, baseline=())
    code2, text2 = _st_run(["--root", str(root2)])
    ck("cross-dest CONTROL: exit", code2, EXIT_OK)
    ck("cross-dest CONTROL: it MOVES", "M4" in _st_ids_in(root2, D_DONE), True)
    ck("cross-dest CONTROL: ...and left backlog.md", "M4" in _st_ids_in(root2, "backlog.md"), False)
    ck("cross-dest CONTROL: no false cross-file refusal", "CROSS-FILE CONFLICT" in text2, False)
    ck("cross-dest CONTROL: the unrelated archives are intact",
       ("M6" in _st_ids_in(root2, D_BACKLOG_DEFERRED)) and ("M77" in _st_ids_in(root2, D_DONE)),
       True)

    # --- CONTROL: the SAME-destination branch must be untouched. A genuine interrupted run -- the
    #     id present in the destination it routes to and nowhere else -- still takes ALREADY, with
    #     ALREADY's message, and the move still completes.
    backlog7 = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## M7 - finished\nkind: plan\nlifetime: conditional\ndone: save#9\n")
    root3 = _st_mk(tmp, "cross_dest_same", {"backlog.md": backlog7}, save=10, baseline=())
    src3 = (root3 / "backlog.md").read_bytes()
    span3 = src3[src3.index(b"## M7"):]
    (root3 / D_DONE).write_bytes(dest_header(D_DONE) + b"\n" + span3)
    code3, text3 = _st_run(["--root", str(root3)])
    ck("cross-dest/same dest: exit", code3, EXIT_OK)
    ck("cross-dest/same dest: still ALREADY, not cross-file",
       ("M7 is ALREADY in %s" % D_DONE) in text3 and "CROSS-FILE" not in text3, True)
    ck("cross-dest/same dest: the move still COMPLETES",
       "M7" in _st_ids_in(root3, "backlog.md"), False)
    ck("cross-dest/same dest: exactly ONE definition survives",
       _st_ids_in(root3, D_DONE).count("M7"), 1)

    # --- AND THE INDEX ITSELF, so "once per run over ALL_DESTINATIONS" is pinned rather than
    #     asserted: an id in ANY destination is in the map, keyed to the file it is in -- and a
    #     hot file is NOT a destination, so an id living only in backlog.md is absent from it.
    #     Built on a folder NO run has touched: reading it after a drain would only prove the
    #     drain moved something.
    root4 = _st_mk(tmp, "cross_dest_index",
                   {"backlog.md": hot, D_BACKLOG_DEFERRED: other_id, D_DONE: other_done},
                   save=10, baseline=())
    idx, ent_idx = _corpus_dest_index(root4, citemap.build_families())
    ck("cross-dest/index: sees the deferred archive", idx.get("M6", [(None,)])[0][0],
       D_BACKLOG_DEFERRED)
    ck("cross-dest/index: sees the done archive", idx.get("M77", [(None,)])[0][0], D_DONE)
    ck("cross-dest/index: does NOT read hot files -- M4 is in backlog.md only", "M4" in idx, False)
    ck("cross-dest/index: an absent destination contributes nothing",
       any(rel == D_DROPPED for sites in idx.values() for (rel, _s, _e) in sites), False)
    # The SECOND index, over the same walk: entry starts for the items `heading_map` drops. It is
    # what makes STEP 3's uniqueness question answerable for an entry with no id at all.
    ck("cross-dest/index: the byte index covers the same destinations, and only those",
       sorted(ent_idx), sorted([D_BACKLOG_DEFERRED, D_DONE]))
    ck("cross-dest/index: the byte index carries the file's bytes with its offsets",
       all(isinstance(d, bytes) and starts and all(d[o:o + 2] == b"##" for o in starts)
           for (d, starts) in ent_idx.values()), True)
    ck("cross-dest/index: a hot file is not in the byte index either", "backlog.md" in ent_idx, False)


def _st_arms_carried_children(ck, tmp):
    """F3, MEASURED 2026-08-07. `scan_source`'s nested-item drop DISCARDED the heading entirely --
    no id, no fields, no row, no receipt line, absent from `kept` and therefore from
    `_cross_check` -- while its BYTES still travelled inside the ancestor's move span. When the
    child's id was already archived, the parent's move left TWO DEFINITIONS OF ONE ID INSIDE A
    SINGLE ARCHIVE FILE, at exit 0, with the receipt silent.

    The drop itself is right and is unchanged: the child's bytes ARE the ancestor's bytes, and
    promoting it to an item would make it independently demotable -- more entries moving, which
    D74 refuses -- and would break the parent's move-span arithmetic.
    """
    import subprocess
    here = pathlib.Path(__file__).resolve().parent

    arch = ("<!-- ash_save v4 -->\n# architecture\n\n"
            "## D4 - the old shape\nkind: spec\nlifetime: permanent\n\n"
            "superseded by D9\n\n"
            "### D40 - a nested child that rides along\n"
            "kind: spec\nlifetime: permanent\n\n"
            "The child's body.\n\n"
            "## D9 - the shape that replaced it\nkind: spec\nlifetime: permanent\n")
    superseded = ("<!-- ash_save v4 -->\n# architecture superseded\n\n"
                  "## D40 - the child, ALREADY archived here by an earlier run\n"
                  "kind: spec\nlifetime: permanent\n\nAn older body for D40.\n")

    # --- (1) THE CONTRACT. `scan_source` returns the dropped headings; the ancestor carries them.
    root0 = _st_mk(tmp, "carried_contract", {"architecture.md": arch}, save=10, baseline=())
    fams = citemap.build_families()
    kept, dropped, _open, _data, _region = scan_source(
        "architecture.md", root0 / "architecture.md", fams)
    ck("carried/contract: the drop still happens -- 2 items, not 3", len(kept), 2)
    ck("carried/contract: D40 is NOT an item", [i.ident for i in kept], ["D4", "D9"])
    ck("carried/contract: it is RETURNED instead of discarded", [c[0] for c in dropped], ["D40"])
    ck("carried/contract: level and line travel with it", (dropped[0][2], dropped[0][3]), (3, 10))
    ck("carried/contract: it is keyed to the ancestor whose span carries it",
       [c[0] for c in kept[0].carried], ["D40"])
    ck("carried/contract: the sibling carries nothing", kept[1].carried, [])
    ck("carried/contract: the parent's move span is UNCHANGED -- it still holds the child",
       b"### D40" in source_span(root0, kept[0]), True)

    # --- (2) THE SEVERE CASE. The child's id is already in the destination: REFUSED AS A UNIT.
    root = _st_mk(tmp, "carried_collide",
                  {"architecture.md": arch, D_ARCH_SUPERSEDED: superseded},
                  save=10, baseline=())
    before = _st_snapshot(root)
    code, text = _st_run(["--root", str(root)])
    ck("carried/collide: exit", code, EXIT_OK)
    ck("carried/collide: NOTHING moved", text.startswith("demote: 0 entries moved"), True)
    ck("carried/collide: REFUSED as a unit", "REFUSED (1)" in text, True)
    ck("carried/collide: not one byte of the folder changed", _st_snapshot(root), before)
    ck("carried/collide: the parent stays hot", "D4" in _st_ids_in(root, "architecture.md"), True)
    ck("carried/collide: the archive still holds exactly ONE D40",
       _st_ids_in(root, D_ARCH_SUPERSEDED).count("D40"), 1)
    ck("carried/collide: the message names the CHILD, which nothing named before",
       "CARRIED-CHILD CONFLICT (R-I)" in text and "D40 (architecture.md:10, level 3)" in text,
       True)
    ck("carried/collide: ...and says the child is not a separate entry",
       "has no row and no move of its own" in text, True)
    ck("carried/collide: it is WARNED about, not only tabulated",
       "MOVE WAS REFUSED: CARRIED-CHILD CONFLICT" in text, True)

    # --- (3) CONTROL, and it must go the OTHER way: with the child's id defined nowhere else the
    #     parent MOVES exactly as it always did -- and the receipt now lets a reader decompose the
    #     byte figure it prints.
    root2 = _st_mk(tmp, "carried_clean", {"architecture.md": arch}, save=10, baseline=())
    code2, text2 = _st_run(["--root", str(root2)])
    ck("carried/clean: exit", code2, EXIT_OK)
    ck("carried/clean: the parent MOVES", "D4" in _st_ids_in(root2, D_ARCH_SUPERSEDED), True)
    ck("carried/clean: ...and left the hot file",
       "D4" in _st_ids_in(root2, "architecture.md"), False)
    ck("carried/clean: no false carried refusal", "CARRIED-CHILD CONFLICT" in text2, False)
    ck("carried/clean: the child's bytes rode along, as they always did",
       b"### D40" in (root2 / D_ARCH_SUPERSEDED).read_bytes(), True)
    ck("carried/clean: THE RECEIPT NAMES IT -- the byte figure is decomposable",
       "[D4 superseded +carried D40]" in text2, True)
    ck("carried/clean: the sibling that carries nothing gets no bracket noise",
       "+carried" in text2.split("[D4 superseded")[1].split("]")[0] + "]", True)

    # --- (4) CONTROL: an id-less nested child is named with the `~` fallback, not dropped from
    #     the receipt for want of an id, and does not trigger the ID screen.
    arch3 = ("<!-- ash_save v4 -->\n# architecture\n\n"
             "## D4 - the old shape\nkind: spec\nlifetime: permanent\n\n"
             "superseded by D9\n\n"
             "### a nested child with no citemap id\nkind: spec\nlifetime: permanent\n\n"
             "## D9 - the replacement\nkind: spec\nlifetime: permanent\n")
    root3 = _st_mk(tmp, "carried_noid", {"architecture.md": arch3}, save=10, baseline=())
    code3, text3 = _st_run(["--root", str(root3)])
    ck("carried/no id: exit", code3, EXIT_OK)
    ck("carried/no id: the parent still moves", "D4" in _st_ids_in(root3, D_ARCH_SUPERSEDED),
       True)
    ck("carried/no id: it is named with the `~` fallback, not omitted",
       "+carried ~a" in text3, True)
    ck("carried/no id: an id-less child cannot collide, so no refusal",
       "CARRIED-CHILD CONFLICT" in text3, False)

    # --- (5) THE HOLE THIS DOES *NOT* CLOSE, PINNED SO IT IS RECORDED RATHER THAN REDISCOVERED.
    #     A carried child is a FULL ENTRY to `validate_entries` once it lands. An ungated child in
    #     a GATE_REQUIRED archive therefore aborts the next save at A7/exit 3 -- MEASURED
    #     IDENTICAL before and after F1 and F3. F3's screen is an ID screen and the records it
    #     keeps hold no fields, so there is no `gate:` here to test.
    #     WHEN THIS IS CLOSED, the two `3`s below become `0` and the third check flips to False.
    plan = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M20 - a correctly gated parent\nkind: plan\nlifetime: conditional\n"
            "gate: EXTERNAL - the broker pilot runs one save\n\n"
            "### M21 - a nested child with kind+lifetime and NO gate\n"
            "kind: plan\nlifetime: conditional\n\nThe child's body.\n")
    root4 = _st_mk(tmp, "carried_gate_hole", {"backlog.md": plan}, save=10, baseline=())

    def _validate(r):
        p = subprocess.run([sys.executable, "-S", "-I", str(here / "validate_entries.py"),
                            "--root", str(r)], capture_output=True)
        return p.returncode, p.stdout.decode("utf-8", "replace")

    ck("carried/gate hole: the fixture is VALID before the drain touches it",
       _validate(root4)[0], 0)
    code4, text4 = _st_run(["--root", str(root4)])
    ck("carried/gate hole: the parent defers", "M20" in _st_ids_in(root4, D_BACKLOG_DEFERRED), True)
    ck("carried/gate hole: the receipt at least NAMES the child now",
       "+carried M21" in text4, True)
    ck("carried/gate hole: RECORDED, NOT CLOSED -- validate still aborts",
       _validate(root4)[0], 3)
    ck("carried/gate hole: ...on A7, naming the child", "[M21]" in _validate(root4)[1], True)

    # --- (6) ONE STEP OUT: a level-4 GRANDCHILD under the level-3 child. The drop predicate
    #     compares against `kept[-1]`, which is still the level-2 ancestor, so BOTH attach to it
    #     -- and both are screened and both are named.
    arch5 = ("<!-- ash_save v4 -->\n# architecture\n\n"
             "## D4 - the old shape\nkind: spec\nlifetime: permanent\n\n"
             "superseded by D9\n\n"
             "### D40 - the child\nkind: spec\nlifetime: permanent\n\n"
             "#### D41 - the GRANDCHILD\nkind: spec\nlifetime: permanent\n\nbody\n\n"
             "## D9 - the replacement\nkind: spec\nlifetime: permanent\n")
    root5 = _st_mk(tmp, "carried_grandchild", {"architecture.md": arch5}, save=10, baseline=())
    code5, text5 = _st_run(["--root", str(root5)])
    ck("carried/grandchild: exit", code5, EXIT_OK)
    ck("carried/grandchild: BOTH attach to the level-2 ancestor and both are named",
       "[D4 superseded +carried D40/D41]" in text5, True)
    root6 = _st_mk(tmp, "carried_grandchild_collide",
                   {"architecture.md": arch5,
                    D_ARCH_SUPERSEDED: "<!-- ash_save v4 -->\n# superseded\n\n"
                                       "## D41 - the grandchild, already here\n"
                                       "kind: spec\nlifetime: permanent\n"},
                   save=10, baseline=())
    code6, text6 = _st_run(["--root", str(root6)])
    ck("carried/grandchild: the screen reaches DEPTH, not just one level",
       ("CARRIED-CHILD CONFLICT" in text6)
       and text6.startswith("demote: 0 entries moved"), True)

    # --- (7) THE OTHER RESIDUAL, PINNED FOR THE SAME REASON AS (5). `_corpus_dest_index` is
    #     built ONCE PER RUN from the PRE-RUN archives, so two hot files defining the SAME id and
    #     routing to DIFFERENT destinations in ONE run are both appended -- the second batch's
    #     index cannot see the first batch's write. The corpus was ALREADY ambiguous before the
    #     run (`check` exit 4), so this creates no collision from a clean start; what it does is
    #     put both copies beyond the drain's reach. Closing it means refreshing the index between
    #     destinations. WHEN IT IS CLOSED the last two checks flip.
    same_id_backlog = ("<!-- ash_save v4 -->\n# plan\n\n"
                    "## M9 - finished\nkind: plan\nlifetime: conditional\ndone: save#9\n")
    same_id_oq = ("<!-- ash_save v4 -->\n# open questions\n\n"
                  "## M9 - RESOLVED - the same id in another hot file\n"
                  "kind: plan\nlifetime: conditional\n")
    root7 = _st_mk(tmp, "cross_dest_same_run",
                   {"backlog.md": same_id_backlog, "open_questions.md": same_id_oq},
                   save=10, baseline=())
    code7, text7 = _st_run(["--root", str(root7)])
    ck("cross-dest/same run: exit", code7, EXIT_OK)
    ck("cross-dest/same run: RECORDED, NOT CLOSED -- no cross-file refusal fires",
       "CROSS-FILE CONFLICT" in text7, False)
    ck("cross-dest/same run: RECORDED, NOT CLOSED -- the id lands in TWO archives",
       ("M9" in _st_ids_in(root7, D_DONE)) and ("M9" in _st_ids_in(root7, D_RESOLVED)), True)


def _st_arms_m9_positions(ck, tmp):
    """Exercise supersession fields and mentions in seven positions.

    Row 4's body marker and row 5's gate use the same positions. Only a genuine
    field can authorize moving an entry; a mention must not archive its owner.

    THE ROW-5 HALF IS THE CONTROL AND IT IS WHY THE RULE IS THE RIGHT ONE. `gate:` reaches
    `it.extra` only through `validate_entries.FIELD_RE`, so of those same seven positions only the
    field line has ever fired. Row 4 is being brought to where row 5 already was; if these two
    tables ever disagree again, one of the two rows has drifted.
    """
    r4 = (("field-line", "superseded by D9", True, False),
          ("bullet", "- superseded by D9", False, True),
          ("sub-bullet", "    - superseded by D9", False, True),
          ("bold", "**superseded by D9**", False, True),
          ("prose", "The old path was superseded by D9 last year.", False, True),
          ("blockquote", "> superseded by D9", False, True),
          ("fence", "```\nsuperseded by D9\n```", False, False))
    for (tag, marker, fires, warns) in r4:
        arch = ("<!-- ash_save v4 -->\n# architecture\n\n"
                "## D4 - a live section\nkind: spec\n%s\n\n"
                "## D9 - the successor\nkind: spec\n" % marker)
        root = _st_mk(tmp, "m9_r4_" + tag.replace("-", "_"), {"architecture.md": arch}, save=10)
        code, text = _st_run(["--root", str(root)])
        ck("m9/row4 %-11s exit" % tag, code, EXIT_OK)
        ck("m9/row4 %-11s archived?" % tag, "D4" in _st_ids_in(root, D_ARCH_SUPERSEDED), fires)
        ck("m9/row4 %-11s still hot?" % tag, "D4" in _st_ids_in(root, "architecture.md"), not fires)
        # A trigger that declines SILENTLY is indistinguishable from a file with no trigger in it,
        # so every position that no longer fires must be NAMED. Two exceptions, both deliberate:
        # the field-line case FIRES (there is no near-miss to report), and the FENCE case cannot be
        # seen at all -- a fenced line is not body text to `attach_fields`, so the near-miss scan
        # has nothing to look at. That silence is pinned here rather than left to be rediscovered.
        ck("m9/row4 %-11s near-miss NAMED?" % tag, "row 4 did not fire" in text, warns)

    r5 = (("field-line", "gate: EXTERNAL - channel B opens", True),
          ("bullet", "- gate: EXTERNAL - channel B opens", False),
          ("sub-bullet", "    - gate: EXTERNAL - channel B opens", False),
          ("bold", "**gate: EXTERNAL - channel B opens**", False),
          ("prose", "The entry has gate: EXTERNAL - channel B opens as its rule.", False),
          ("blockquote", "> gate: EXTERNAL - channel B opens", False),
          ("fence", "```\ngate: EXTERNAL - channel B opens\n```", False))
    for (tag, marker, fires) in r5:
        plan = ("<!-- ash_save v4 -->\n# plan\n\n"
                "## M20 - a live plan item\nkind: plan\n%s\n" % marker)
        root = _st_mk(tmp, "m9_r5_" + tag.replace("-", "_"), {"backlog.md": plan}, save=10)
        code, _text = _st_run(["--root", str(root)])
        ck("m9/row5 %-11s exit" % tag, code, EXIT_OK)
        ck("m9/row5 %-11s deferred?" % tag, "M20" in _st_ids_in(root, D_BACKLOG_DEFERRED), fires)
        ck("m9/row5 %-11s still hot?" % tag, "M20" in _st_ids_in(root, "backlog.md"), not fires)

    # The two rows must AGREE about which of the seven positions is a declaration. Asserted as a
    # comparison rather than as two lists, so a future change to one of them fails here.
    ck("m9: rows 4 and 5 accept the SAME position, and only that one",
       [t for (t, _m, f, _w) in r4 if f], [t for (t, _m, f) in r5 if f])

    # The field-line rule is FIELD_RE's line shape, so its indent tolerance is FIELD_RE's too.
    ck("m9: 3 spaces of indent is still a field line",
       SUPERSEDED_FIELD_RE.match("   superseded by D9") is not None, True)
    ck("m9: 4 spaces is not", SUPERSEDED_FIELD_RE.match("    superseded by D9") is not None, False)
    ck("m9: a tab is not", SUPERSEDED_FIELD_RE.match("\tsuperseded by D9") is not None, False)

    # ONE STEP OUT of M9's own sentence: the message tells the author to write the marker where
    # `kind:` and `gate:` go, and the obvious next thing to type is a real field key. It must not
    # fire (it is not the marker) and it must not be SILENT (it was, until the phrase scan was
    # widened to allow the hyphen).
    arch = ("<!-- ash_save v4 -->\n# architecture\n\n"
            "## D4 - a live section\nkind: spec\nsuperseded-by: D9\n\n## D9 - x\nkind: spec\n")
    root = _st_mk(tmp, "m9_fieldkey", {"architecture.md": arch}, save=10)
    code, text = _st_run(["--root", str(root)])
    ck("m9/one-step-out: `superseded-by: D9` does NOT archive",
       "D4" in _st_ids_in(root, D_ARCH_SUPERSEDED), False)
    ck("m9/one-step-out: ...and is NOT silent about it", "row 4 did not fire" in text, True)
    ck("m9/one-step-out: ...and the warning spells the form that does work",
       "WRITE IT EXACTLY AS `superseded by D9`" in text, True)
    ck("m9/one-step-out: the widened scan is WARNING-ONLY -- the marker still needs whitespace",
       SUPERSEDED_FIELD_RE.match("superseded-by: D9") is not None, False)


def _st_arms_sequence(ck, tmp):
    """THE DECISION SEQUENCE, arm per blocker. Every one of these was MEASURED failing on
    demote.py md5 b4552cfd25fb17239b3fbefe78346c7a (2026-08-07) and judged by `recall`, the
    oracle, rather than by this tool's own receipt -- which is silent on all five. Fixtures and
    Each arm below is the fixture, so it can be re-run by anyone with only this file.

    T2 IS WHY EVERY ARM RESTORES THE HOT COPY. Re-running `demote` proves nothing about an
    interrupted run, because run 1 SPLICES the source: the second run then sees a file with nothing
    in it and "nothing moved" is trivially true. The interrupted state is the one where the
    destination HAS the entry and the source STILL HAS IT TOO, so it has to be built by putting the
    hot bytes back (B1-B4) or by writing the destination with this tool's own builder (B5).
    """
    # --- B1. The ALREADY branch never consulted `elsewhere`, and it is the ONE branch whose whole
    #     action is DELETING HOT BYTES. Byte-identical in the routed-to archive AND defined in a
    #     DIFFERENT one -> spliced, leaving one id in two archives and hot in NEITHER.
    entry = ("## M4 - the entry\nkind: plan\nlifetime: conditional\ndone: save#9\n\n"
             "The body as it was archived.\n")
    hot = "<!-- ash_save v4 -->\n# plan\n\n" + entry
    stale = ("<!-- ash_save v4 -->\n# deferred\n\n"
             "## M4 - the entry\nkind: plan\nlifetime: conditional\n"
             "gate: EXTERNAL - the pilot reports\n\nA DIFFERENT body, edited during recovery.\n")
    root = _st_mk(tmp, "seq_b1", {"backlog.md": hot}, save=10, baseline=())
    c1, _t1 = _st_run(["--root", str(root)])                        # the move that gets interrupted
    ck("seq/B1: run 1 archived it", "M4" in _st_ids_in(root, D_DONE), True)
    (root / "backlog.md").write_bytes(hot.encode("utf-8"))             # T2: RESTORE the hot copy
    (root / D_BACKLOG_DEFERRED).write_bytes(stale.encode("utf-8"))
    before = _st_snapshot(root)
    code, text = _st_run(["--root", str(root)])
    ck("seq/B1: exit", code, EXIT_OK)
    ck("seq/B1: the SPLICE is refused", "RECONCILE REFUSED" in text, True)
    ck("seq/B1: the hot copy -- the only walkback lever -- survives",
       "M4" in _st_ids_in(root, "backlog.md"), True)
    ck("seq/B1: not one byte changed anywhere", _st_snapshot(root), before)
    ck("seq/B1: the refusal names the archive the other definition is in",
       D_BACKLOG_DEFERRED in text, True)
    ck("seq/B1: and it does NOT claim an append it was never going to make",
       "would put a SECOND definition" in text, False)

    # --- B2. F3's carried screen sat ABOVE the identless already-branch, so an IDENTLESS parent
    #     carrying an IDENTIFIED child could NEVER finish a move: every re-run refused, for ever.
    hot2 = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## a plain heading that carries no id\nkind: plan\ndone: save#9\n\n"
            "Parent body.\n\n### M7 - the carried child\nkind: plan\n\nChild body.\n")
    root2 = _st_mk(tmp, "seq_b2", {"backlog.md": hot2}, save=10, baseline=())
    _st_run(["--root", str(root2)])
    ck("seq/B2: run 1 carried the child into the archive",
       "M7" in _st_ids_in(root2, D_DONE), True)
    (root2 / "backlog.md").write_bytes(hot2.encode("utf-8"))           # T2: RESTORE the hot copy
    code2, text2 = _st_run(["--root", str(root2)])
    ck("seq/B2: exit", code2, EXIT_OK)
    ck("seq/B2: the interrupted move is FINISHED, not refused", "is ALREADY in" in text2, True)
    ck("seq/B2: the hot duplicate is gone", "M7" in _st_ids_in(root2, "backlog.md"), False)
    ck("seq/B2: M7 resolves at EXACTLY one site", _st_ids_in(root2, D_DONE).count("M7"), 1)
    ck("seq/B2: re-running again is a no-op", _st_run(["--root", str(root2)])[1].splitlines()[0],
       "demote: 0 entries moved")

    # --- B3. The same root from the other side: the IDENTIFIED already-branch sat ABOVE the
    #     carried screen and the IDENTLESS one BELOW it, so two entries in ONE batch, in the SAME
    #     state, got OPPOSITE verdicts.
    hot3 = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M4 - the identified parent\nkind: plan\ndone: save#9\n\n"
            "### M7 - child of the identified parent\nkind: plan\n\n"
            "## a plain heading that carries no id\nkind: plan\ndone: save#9\n\n"
            "### M8 - child of the identless parent\nkind: plan\n")
    root3 = _st_mk(tmp, "seq_b3", {"backlog.md": hot3}, save=10, baseline=())
    _st_run(["--root", str(root3)])
    (root3 / "backlog.md").write_bytes(hot3.encode("utf-8"))           # T2: RESTORE the hot copy
    code3, _t3 = _st_run(["--root", str(root3)])
    ck("seq/B3: exit", code3, EXIT_OK)
    ck("seq/B3: the identified parent reconciled", "M4" in _st_ids_in(root3, "backlog.md"), False)
    ck("seq/B3: the identless parent reconciled TOO -- no asymmetry",
       "M8" in _st_ids_in(root3, "backlog.md"), False)
    ck("seq/B3: both children resolve at exactly one site",
       (_st_ids_in(root3, D_DONE).count("M7"), _st_ids_in(root3, D_DONE).count("M8")), (1, 1))

    # --- B4. The already-test compared the destination entry's SPAN, which ends at the next
    #     heading of the same or higher level -- a property of the file AS IT IS NOW. A DEEPER
    #     heading appended afterwards is swallowed by it, and the test then misses on an entry
    #     sitting there untouched.
    #
    #     REVISED 2026-08-07 (#93), and the revision is a TRADE, not a correction of B4's
    #     reasoning. B4's replacement accepted ANY following heading as "where the next append
    #     started", which is TRUE HERE and FALSE for the shape at `already/child`: there the deeper
    #     heading is a CHILD the author deleted this session, and accepting it deleted the hot copy
    #     at exit 0 while `get` answered with the removed content. The two are byte-identical from
    #     the destination's side -- `kind:` does not separate them either, because `seq/B3`'s
    #     carried child declares `kind: plan` exactly as this fixture's appended entry does.
    #     So the tool now REFUSES both, per R-I: "The tool cannot know which the author wants, so
    #     it declines and says so." The cost is HERE: this interrupted-run recovery no longer
    #     finishes itself. It stays hot, warns naming both sites, and one manual reconcile clears
    #     it -- visible and recoverable, which is D74's ratified direction. The alternative that
    #     would keep BOTH is a drain-marker test (`done:`/`gate:` on the deeper heading); it is
    #     UNMEASURED across the corpus, so C-19 forbids shipping it on plausibility.
    deeper = ("<!-- ash_save v4 -->\n# plan\n\n"
              "### a LEVEL THREE item appended afterwards\nkind: plan\ndone: save#9\n")
    for (tag, body, ident) in (("identified",
                                "## M4 - the identified parent\nkind: plan\ndone: save#9\n\n"
                                "Parent body.\n", "M4"),
                               ("identless",
                                "## a plain heading that carries no id\nkind: plan\n"
                                "done: save#9\n\nParent body.\n", None)):
        hot4 = "<!-- ash_save v4 -->\n# plan\n\n" + body
        r = _st_mk(tmp, "seq_b4_" + tag, {"backlog.md": hot4}, save=10, baseline=())
        _st_run(["--root", str(r)])                                  # 1. the entry is archived
        (r / "backlog.md").write_bytes(deeper.encode("utf-8"))
        _st_run(["--root", str(r)])                                  # 2. a DEEPER heading lands
        (r / "backlog.md").write_bytes(hot4.encode("utf-8"))            # 3. T2: RESTORE the hot copy
        code4, text4 = _st_run(["--root", str(r)])                   # 4. the run under test
        dest = (r / D_DONE).read_bytes()
        head = body.split("\n", 1)[0].encode("utf-8")
        ck("seq/B4 %s: exit" % tag, code4, EXIT_OK)
        ck("seq/B4 %s: the ambiguity is REFUSED, not resolved by guessing" % tag,
           "is ALREADY in" in text4, False)
        ck("seq/B4 %s: STILL exactly one copy in the destination -- refusing must not append"
           % tag, dest.count(head), 1)
        ck("seq/B4 %s: the hot copy stays HOT, so nothing is lost either way" % tag,
           head in (r / "backlog.md").read_bytes(), True)
        ck("seq/B4 %s: and it SAYS so rather than passing silently" % tag,
           ("SUPERSET (R-I)" in text4) or ("CONFLICT (R-I)" in text4), True)
        if ident:
            ck("seq/B4 %s: R-H did not veto it on a span it computed itself" % tag,
               "MOVE FAILED" in text4, False)

    # --- B5. F1's terminal hold returned before the item ever reached `screen_batch`, so the
    #     reconciliation that finishes an interrupted move never ran -- and the newly-refused
    #     population is precisely the one an earlier drain could already have archived. The
    #     destination is written with THIS TOOL'S OWN builder, so the bytes are the bytes an
    #     earlier run would have left.
    entry5 = ("## M20 - parked work\nkind: plan\n"
              "gate: EXTERNAL - eventually the pilot reports back, whenever that is\n\n"
              "The body.\n")
    hot5 = "<!-- ash_save v4 -->\n# plan\n\n" + entry5
    archived, _offs = _build_destination(dest_header(D_BACKLOG_DEFERRED),
                                         [entry5.encode("utf-8")], created=True)
    root5 = _st_mk(tmp, "seq_b5",
                   {"backlog.md": hot5, D_BACKLOG_DEFERRED: archived.decode("utf-8")},
                   save=10, baseline=())
    code5, text5 = _st_run(["--root", str(root5)])
    ck("seq/B5: exit", code5, EXIT_OK)
    ck("seq/B5: the HOLD still fires and still says why", "NOT DEFERRED" in text5, True)
    ck("seq/B5: ...and the move is FINISHED anyway", "is ALREADY in" in text5, True)
    ck("seq/B5: the hot duplicate is gone", "M20" in _st_ids_in(root5, "backlog.md"), False)
    ck("seq/B5: M20 resolves at exactly one site",
       _st_ids_in(root5, D_BACKLOG_DEFERRED).count("M20"), 1)
    ck("seq/B5: NOTHING was appended -- the archive is byte-identical to what it was",
       (root5 / D_BACKLOG_DEFERRED).read_bytes(), archived)

    # --- B5 CONTROL, and it is the one that proves the hold still holds: the SAME entry with NO
    #     archived copy must stay hot and must not reach the archive.
    root6 = _st_mk(tmp, "seq_b5_control", {"backlog.md": hot5}, save=10, baseline=())
    before6 = _st_snapshot(root6)
    code6, text6 = _st_run(["--root", str(root6)])
    ck("seq/B5 control: exit", code6, EXIT_OK)
    ck("seq/B5 control: still HELD -- nothing moved", _st_snapshot(root6), before6)
    ck("seq/B5 control: the entry is still hot", "M20" in _st_ids_in(root6, "backlog.md"), True)
    ck("seq/B5 control: the deferred archive was not created",
       (root6 / D_BACKLOG_DEFERRED).exists(), False)
    ck("seq/B5 control: and the hold sentence says what a hold does not decide",
       "refuses to START a move, never to FINISH one" in text6, True)


def _st_arms_sequence_units(ck, tmp):
    """The two predicates the sequence rests on, at the unit they are decided in, plus STEP 4a --
    which a normal run cannot reach and which is therefore the one branch a fixture cannot pin.

    STEP 4a is the executable form of "a held entry may FINISH a move and may never START one".
    `_reconcile_probe` admits a held entry only when a copy IS at the destination, so STEP 2 finds
    it and 4a is skipped. A branch that only a comment defends is a branch a later edit can delete
    without failing anything, so it is called here directly.
    """
    pay = b"## M1 - x\nkind: plan\n\nBody.\n"
    ck("unit/ends_at: exact, at EOF", _payload_ends_at(pay, 0, pay), True)
    ck("unit/ends_at: exact, then the separator", _payload_ends_at(pay + b"\n", 0, pay), True)
    ck("unit/ends_at: then ANOTHER entry -- that is where the next append started",
       _payload_ends_at(pay + b"\n## M2 - y\nkind: plan\n", 0, pay), True)
    ck("unit/ends_at: then a SHALLOWER heading -- also a boundary, never content",
       _payload_ends_at(pay + b"\n# a document-level section\n", 0, pay), True)
    ck("unit/ends_at: then a DEEPER heading -- a carried CHILD this payload does NOT hold, so the "
       "destination is a strict SUPERSET; R-I's CONFLICT, never 'already'",
       _payload_ends_at(pay + b"\n### M2 - y\nkind: plan\n", 0, pay), False)
    ck("unit/ends_at: deeper still -- the same fact, and a test hard-coded to ### would miss it",
       _payload_ends_at(pay + b"\n#### a grandchild\n", 0, pay), False)
    ck("unit/ends_at: a payload that does not OPEN with a heading refuses -- depth has nothing to "
       "measure against, and D74's bias is toward NOT moving",
       _payload_ends_at(b"prose only\n", 0, b"prose only\n"), False)
    ck("unit/ends_at: then PROSE -- the paragraph the author deleted; NOT already",
       _payload_ends_at(pay + b"\nA paragraph the author deleted today.\n", 0, pay), False)
    ck("unit/ends_at: CRLF after the payload is still whitespace",
       _payload_ends_at(pay + b"\r\n\r\n", 0, pay), True)
    ck("unit/ends_at: a byte short is not a match", _payload_ends_at(pay[:-1], 0, pay), False)
    ck("unit/ends_at: the payload is not at that offset", _payload_ends_at(b"x" + pay, 0, pay),
       False)

    ck("unit/offsets: an id looks itself up by id",
       _already_offsets(pay, [(0, len(pay), pay)], [], pay, "M1"), [0])
    ck("unit/offsets: no id -> every entry start is a candidate and the BYTES decide",
       _already_offsets(pay, [], [0], pay, None), [0])
    ck("unit/offsets: a candidate whose bytes differ is not one",
       _already_offsets(b"## M9 - z\n", [], [0], pay, None), [])

    # STEP 4a, called directly: held, and nothing at the destination to finish.
    root = _st_mk(tmp, "seq_held_unit",
                  {"backlog.md": "<!-- ash_save v4 -->\n# plan\n\n## M1 - x\nkind: plan\n\nBody.\n"},
                  save=10, baseline=())
    items, _c, _o, _d, _r = scan_source("backlog.md", root / "backlog.md", citemap.build_families())
    it = items[0]
    it.row, it.held_row, it.reconcile_only = 5, 5, True
    app, alr, ref = screen_batch(root, D_BACKLOG_DEFERRED, [(it, D_BACKLOG_DEFERRED)],
                                 dest_header(D_BACKLOG_DEFERRED), {}, [], {}, {}, [])
    ck("unit/4a: a HELD entry with nothing to finish is NOT appended", app, [])
    ck("unit/4a: ...and is not reconciled either", alr, [])
    ck("unit/4a: ...it is refused, and the refusal says which invariant it is",
       "may FINISH a move an earlier run began and may never START one" in ref[0][2], True)
    # ...and the SAME item without the flag appends, so the flag is what did it.
    it2 = scan_source("backlog.md", root / "backlog.md", citemap.build_families())[0][0]
    it2.row = 5
    app2, _a2, _r2 = screen_batch(root, D_BACKLOG_DEFERRED, [(it2, D_BACKLOG_DEFERRED)],
                                  dest_header(D_BACKLOG_DEFERRED), {}, [], {}, {}, [])
    ck("unit/4a: the CONTROL -- without the flag the same item appends", len(app2), 1)


def _st_arms_bom_and_eol(ck, tmp):
    """A BOM belongs to the FILE. A CRLF entry keeps its terminators, in a NEW destination too."""
    root = _st_mk(tmp, "bom", {}, save=10, baseline=())
    (root / "backlog.md").write_bytes(
        "﻿## M80 - the very first line, after a BOM\nkind: plan\ndone: save#9\n\n"
        "## M81 - the neighbour\nkind: plan\n".encode("utf-8"))
    code, text = _st_run(["--root", str(root)])
    ck("BOM: exit is 0, not the wedged 3", code, EXIT_OK)
    ck("BOM: the entry moved", "M80" in _st_ids_in(root, D_DONE), True)
    ck("BOM: `get` resolves it in the archive (no BOM landed mid-file)",
       _st_ids_in(root, D_DONE), ["M80"])
    ck("BOM: no BOM bytes reached the destination",
       b"\xef\xbb\xbf" in (root / D_DONE).read_bytes(), False)
    ck("BOM: the file keeps its own BOM", (root / "backlog.md").read_bytes()[:3], b"\xef\xbb\xbf")
    ck("BOM: the neighbour is untouched", "M81" in _st_ids_in(root, "backlog.md"), True)

    root2 = _st_mk(tmp, "crlf", {}, save=10, baseline=())
    (root2 / "backlog.md").write_bytes(
        b"# plan\r\n\r\n## M82 - crlf\r\nkind: plan\r\ndone: save#9\r\n\r\n"
        b"## M83 - crlf survivor\r\nkind: plan\r\n")
    code2, _t2 = _st_run(["--root", str(root2)])
    ck("CRLF: exit", code2, EXIT_OK)
    dest = (root2 / D_DONE).read_bytes()
    ck("CRLF: the payload kept its terminators", b"## M82 - crlf\r\n" in dest, True)
    ck("CRLF: the NEW destination is not mixed-EOL",
       [i for i in range(len(dest) - 1)
        if dest[i:i + 1] == b"\n" and dest[i - 1:i] != b"\r"], [])
    ck("CRLF: the source kept CRLF", b"## M83 - crlf survivor\r\n"
       in (root2 / "backlog.md").read_bytes(), True)

    # LOW-1: a failed replace leaves no `.demote-tmp` behind.
    root3 = _st_mk(tmp, "litter", {"backlog.md": "# plan\n\n## M84 - x\nkind: plan\ndone: save#9\n"},
                   save=10, baseline=())
    (root3 / D_DONE).parent.mkdir(parents=True, exist_ok=True)
    (root3 / D_DONE).mkdir()                       # a directory where a file must go
    code3, _t3 = _st_run(["--root", str(root3)])
    ck("litter: a blocked destination is a usage error", code3, EXIT_USAGE)
    ck("litter: no .demote-tmp survives in the project",
       sorted(p.name for p in pathlib.Path(root3).rglob("*.demote-tmp")), [])
    ck("litter: and the source was not touched", "M84" in _st_ids_in(root3, "backlog.md"), True)


def _st_arms_diagnostics(ck, tmp):
    """A diagnosis an operator can act on. `save: soon` is not `no save: line`."""
    root = pathlib.Path(tmp) / "badsave"
    (root / ARCHIVE_DIR).mkdir(parents=True, exist_ok=True)
    (root / "active.md").write_bytes(_ST_ACTIVE.encode("utf-8"))
    (root / STATE_FILE).write_bytes(b"schema_version: v4\n# state\n\nsave: soon\n")
    code, text = _st_run(["--root", str(root)])
    ck("bad save: exit", code, EXIT_OK)
    ck("bad save: row 1 no-ops", "row 1 NO-OP" in text, True)
    ck("bad save: does NOT advise adding a line that is already there",
       "give state.md a `save:` line" in text, False)
    ck("bad save: names the line it found", "already HAS a `save:` line" in text, True)
    ck("bad save: the receipt does not double the word `unknown`",
       "unknown (unknown" in text, False)


def _st_arms_rg(ck, tmp):
    """R-G: an ABSENT baseline makes row 2b NO-OP and SAY SO. It never means 'evict everything'."""
    files = {"backlog.md": _ST_BACKLOG}
    root = _st_mk(tmp, "rg_absent", files, save=10, baseline=("W5", "M40"), region=False)
    code, text = _st_run(["--root", str(root), "--rewrote-complete"])
    ck("R-G absent: exit", code, EXIT_OK)
    ck("R-G absent: W5 (would-be row 2b) stays hot", "W5" in _st_ids_in(root, "backlog.md"), True)
    ck("R-G absent: nothing at all in backlog-dropped", (root / D_DROPPED).is_file(), False)
    ck("R-G absent: warns", "row 2b NO-OP (R-G)" in text, True)
    ck("R-G absent: row 5 still ran (it has no baseline dependency)",
       "M20" in _st_ids_in(root, D_BACKLOG_DEFERRED), True)
    ck("R-G absent: no pending_drop is invented from a missing baseline",
       "pending_drop:" in text, False)

    root2 = _st_mk(tmp, "rg_empty", files, save=10, baseline=())
    code2, text2 = _st_run(["--root", str(root2), "--rewrote-complete"])
    ck("R-G empty: exit", code2, EXIT_OK)
    ck("R-G empty: present-and-empty is a REAL baseline, not a no-op",
       "row 2b NO-OP (R-G)" in text2, False)
    ck("R-G empty: nothing matches it, so nothing is dropped",
       (root2 / D_DROPPED).is_file(), False)
    ck("R-G empty: W5 stays hot", "W5" in _st_ids_in(root2, "backlog.md"), True)

    # Missing state refuses. A declared v4 cursor without a baseline still safely no-ops.
    root3 = _st_mk(tmp, "rg_missing_state", files, save=None)
    ck("R-G no state.md: refused",
       _st_run(["--root", str(root3), "--save", "10", "--rewrote-complete"])[0],
       EXIT_NOT_MIGRATED)
    (root3 / "state.md").write_bytes(b"schema_version: v4\n")
    code3, text3 = _st_run(["--root", str(root3), "--save", "10", "--rewrote-complete"])
    ck("R-G no state.md: exit", code3, EXIT_OK)
    ck("R-G no state.md: row 2b no-ops", "row 2b NO-OP (R-G)" in text3, True)

    # no save# at all -> row 1 no-ops rather than guessing "now"
    root4 = _st_mk(tmp, "no_save", {"active.md": _ST_ACTIVE}, save=None)
    (root4 / "state.md").write_bytes(b"schema_version: v4\n")
    code4, text4 = _st_run(["--root", str(root4)])
    ck("no save#: exit", code4, EXIT_OK)
    ck("no save#: row 1 no-ops", "row 1 NO-OP" in text4, True)
    ck("no save#: A-7 stays hot", "A-7" in _st_ids_in(root4, "active.md"), True)


def _st_arms_rh(ck, tmp):
    """R-H: a move whose bytes do not survive is ABORTED, named, and leaves the entry hot."""
    root = _st_mk(tmp, "rh", {"backlog.md": _ST_BACKLOG}, save=10, baseline=())
    before = _st_snapshot(root)

    def corrupt(it, span):
        return span.replace(b"finished item", b"finishXd item") if it.ident == "M12" else span

    globals()["_CORRUPT_HOOK"] = corrupt
    try:
        code, text = _st_run(["--root", str(root)])
    finally:
        globals()["_CORRUPT_HOOK"] = None

    ck("R-H: exit 3", code, EXIT_MOVE_FAILED)
    ck("R-H: names the id", "MOVE FAILED M12" in text, True)
    ck("R-H: FAILED block lists it", "FAILED (1)" in text, True)
    ck("R-H: M12 is still in backlog.md", "M12" in _st_ids_in(root, "backlog.md"), True)
    ck("R-H: M12's bytes in backlog.md are untouched",
       before["backlog.md"][before["backlog.md"].index(b"## M12"):
                         before["backlog.md"].index(b"## M13")]
       in (root / "backlog.md").read_bytes(), True)
    ck("R-H: the entries that DID verify still moved (a failure is per-move, not per-save)",
       "M20" in _st_ids_in(root, D_BACKLOG_DEFERRED), True)
    ck("R-H: M12 is NOT in backlog-done", "M12" in _st_ids_in(root, D_DONE), False)
    ck("R-H: the destination it was alone in was not left behind",
       (root / D_DONE).is_file(), False)
    ck("R-H: the OTHER destinations still got their entries",
       "M20" in _st_ids_in(root, D_BACKLOG_DEFERRED), True)


def _st_arms_idempotent(ck, tmp):
    """Re-running is safe: the second pass moves nothing and changes no byte."""
    root = _st_mk(tmp, "idem", _ST_FILES, save=10, baseline=("W5", "M30", "M40"))
    code1, text1 = _st_run(["--root", str(root), "--rewrote-complete"])
    ck("idempotent: run 1 exit", code1, EXIT_OK)
    ck("idempotent: run 1 moved entries", "demote: 0 entries moved" in text1, False)
    after1 = _st_snapshot(root)
    code2, text2 = _st_run(["--root", str(root), "--rewrote-complete"])
    ck("idempotent: run 2 exit", code2, EXIT_OK)
    ck("idempotent: run 2 moved nothing", text2.startswith("demote: 0 entries moved"), True)
    ck("idempotent: not one byte changed", _st_snapshot(root), after1)


def _st_arms_integrity(ck, tmp):
    """An OPEN FENCE anywhere in scope -> exit 6, and NOT ONE MOVE performed."""
    broken = _ST_BACKLOG + "\n## M50 - after an unterminated fence\n```text\nno close\n"
    root = _st_mk(tmp, "fence", dict(_ST_FILES, **{"backlog.md": broken}),
                  save=10, baseline=("W5",))
    before = _st_snapshot(root)
    code, text = _st_run(["--root", str(root)])
    ck("integrity: exit 6", code, EXIT_SCAN_INTEGRITY)
    ck("integrity: receipt says refused", "SCAN INTEGRITY -- refused" in text, True)
    ck("integrity: NOTHING changed on disk", _st_snapshot(root), before)
    ck("integrity: no destination was created",
       any((root / d).is_file() for d in ALL_DESTINATIONS), False)

    # ...and the fence does not have to be in a SOURCE. A destination is appended to, so an open
    # fence there would swallow the appended entry.
    root2 = _st_mk(tmp, "fence_dest", {"active.md": _ST_ACTIVE}, save=10)
    (root2 / D_EXPIRED).write_bytes(b"# expired\n\n```text\nnever closed\n")
    before2 = _st_snapshot(root2)
    code2, _t2 = _st_run(["--root", str(root2)])
    ck("integrity: open fence in a DESTINATION also refuses", code2, EXIT_SCAN_INTEGRITY)
    ck("integrity: destination untouched", _st_snapshot(root2), before2)

    # ...and in state.md, which is a read path too (R-E)
    root3 = _st_mk(tmp, "fence_state", {"active.md": _ST_ACTIVE}, save=None)
    (root3 / STATE_FILE).write_bytes(
        b"schema_version: v4\n# state\n\nsave: 10\n\n```text\nopen\n")
    code3, _t3 = _st_run(["--root", str(root3)])
    ck("integrity: open fence in state.md refuses", code3, EXIT_SCAN_INTEGRITY)


def _st_arms_dryrun_usage(ck, tmp):
    root = _st_mk(tmp, "dry", _ST_FILES, save=10, baseline=("W5",))
    before = _st_snapshot(root)
    code, text = _st_run(["--root", str(root), "--dry-run"])
    ck("dry-run: exit", code, EXIT_OK)
    ck("dry-run: says so", text.startswith("demote: DRY RUN -- "), True)
    ck("dry-run: names what would move", "A-7 expired -> " in text, True)
    ck("dry-run: wrote NOTHING", _st_snapshot(root), before)

    root2 = _st_mk(tmp, "rewrote", {"backlog.md": _ST_BACKLOG}, save=10, baseline=("W5",))
    code2, _t2 = _st_run(["--root", str(root2), "--rewrote-complete", "--rewrote", "W5"])
    ck("--rewrote: exit", code2, EXIT_OK)
    ck("--rewrote: exempts W5 from row 2b", "W5" in _st_ids_in(root2, "backlog.md"), True)

    ck("usage: no --root", _st_run([])[0], EXIT_USAGE)
    ck("usage: --root missing dir", _st_run(["--root", str(pathlib.Path(tmp) / "nope")])[0],
       EXIT_USAGE)
    afile = pathlib.Path(tmp) / "a_file.md"
    afile.write_bytes(b"x\n")
    ck("usage: --root is a file", _st_run(["--root", str(afile)])[0], EXIT_USAGE)

    # An EMPTY directory has no cursor, so D23 reads it as v3 and the gate refuses it before
    # the drain can report a cheerful no-op over a folder that is not a project at all.
    empty = pathlib.Path(tmp) / "empty_proj"
    empty.mkdir()
    ck("empty folder: refused", _st_run(["--root", str(empty)])[0], EXIT_NOT_MIGRATED)
    (empty / "state.md").write_bytes(b"schema_version: v4\n")
    code3, text3 = _st_run(["--root", str(empty)])
    ck("empty v4 project: exit", code3, EXIT_OK)
    ck("empty v4 project: moved nothing", text3.startswith("demote: 0 entries moved"), True)

    # --- the migration gate as its own subject -------------------------------------------
    # This drain MOVES bytes between files, so it is the writer with the most to lose. Before the
    # gate existed, a run on a real v3 folder planned a batch of entries and carried their bytes
    # out of open_questions.md into an _archive/ file it created, at exit 0, no version awareness.
    gate_files = {"active.md": _ST_ACTIVE}
    for _gname, _gschema in (("gate_v3", "v3"), ("gate_none", None)):
        _gr = _st_mk(tmp, _gname, gate_files, save=10, schema=_gschema)
        _gbefore = _st_snapshot(_gr)
        _gcode, _gtext = _st_run(["--root", str(_gr)])
        ck("gate: %s refused" % _gname, _gcode, EXIT_NOT_MIGRATED)
        ck("gate: %s moved nothing" % _gname, _st_snapshot(_gr), _gbefore)
        ck("gate: %s printed no receipt" % _gname, _gtext, "")
        # --dry-run does not get a pass: a plan against a folder that may not be drained is
        # still an answer the caller would act on.
        ck("gate: %s refused under --dry-run too" % _gname,
           _st_run(["--root", str(_gr), "--dry-run"])[0], EXIT_NOT_MIGRATED)
        # ...and the refusal is stable, which the defect it replaces was not.
        ck("gate: %s refused again" % _gname,
           _st_run(["--root", str(_gr)])[0], EXIT_NOT_MIGRATED)
        ck("gate: %s still untouched" % _gname, _st_snapshot(_gr), _gbefore)
    # The v4 twin of the same corpus proceeds -- the arm that fails if the gate refuses
    # everything, which a refusal-shaped bug would otherwise pass every check above.
    _gok = _st_mk(tmp, "gate_v4", gate_files, save=10)
    ck("gate: v4 twin proceeds", _st_run(["--root", str(_gok)])[0], EXIT_OK)
    ck("gate: exit code is 4", EXIT_NOT_MIGRATED, 4)
    ck("gate: exit code is not the drain's own verdict",
       EXIT_NOT_MIGRATED == EXIT_MOVE_FAILED, False)


def _st_arms_acceptance(ck, tmp):
    """THE ACCEPTANCE TEST, end to end, on a folder scaffolded by the real init_project.py.

    1. a save moves an EXPIRED active.md entry into _archive/active-expired.md
    2. the entry KEEPS ITS ID
    3. a REAL `python -S -I recall.py get A-7 --root <folder>` subprocess returns it FROM THE
       ARCHIVE -- run, not asserted in prose
    4. the receipt NAMES it by id
    """
    import subprocess
    here = pathlib.Path(__file__).resolve().parent
    root = pathlib.Path(tmp) / "acceptance"
    root.mkdir(parents=True, exist_ok=True)

    scaf = subprocess.run([sys.executable, "-S", "-I", str(here / "init_project.py"),
                           "new", "--root", str(root), "--name", "Acceptance"],
                          capture_output=True)
    ck("acceptance: init_project.py new exit", scaf.returncode, 0)
    ck("acceptance: _archive/active-expired.md absent at save#0",
       (root / D_EXPIRED).is_file(), False)

    entry = ('\n## A-7 - asserted save#4 - expires save#4\n'
             '"stop the deep save while we are still building"\n'
             'kind: rules\nlifetime: expiring\n')
    with open(str(root / "active.md"), "ab") as fh:
        fh.write(entry.encode("utf-8"))
    src = (root / "active.md").read_bytes()
    # rindex, not index: the scaffolded active.md carries a FENCED `## A-7` example, and the
    # engine correctly ignores it (fenced lines are not headings). A test that anchored on the
    # first occurrence would be measuring the fence, not the move.
    span = src[src.rindex(b"## A-7"):]

    code, text = _st_run(["--root", str(root), "--save", "5"])
    ck("acceptance: demote exit", code, EXIT_OK)
    ck("acceptance: (1) destination created and holds the entry",
       (root / D_EXPIRED).is_file(), True)
    ck("acceptance: (2) the id survived the move", "A-7" in _st_ids_in(root, D_EXPIRED), True)
    ck("acceptance: A-7 left active.md", "A-7" in _st_ids_in(root, "active.md"), False)
    ck("acceptance: the moved bytes are the source bytes",
       span.rstrip() in (root / D_EXPIRED).read_bytes(), True)
    ck("acceptance: (4) the receipt names it by id",
       ("A-7 expired -> %s" % D_EXPIRED) in text, True)
    ck("acceptance: (4) the per-destination block names it",
       (D_EXPIRED in text) and ("ids: A-7" in text), True)

    got = subprocess.run([sys.executable, "-S", "-I", str(here / "recall.py"),
                          "get", "A-7", "--root", str(root)], capture_output=True)
    ck("acceptance: (3) recall get A-7 exit", got.returncode, 0)
    ck("acceptance: (3) get resolved it in the ARCHIVE",
       D_EXPIRED in got.stderr.decode("utf-8", "replace").replace("\\", "/"), True)
    ck("acceptance: (3) get returned the entry's own bytes",
       got.stdout.rstrip() == span.rstrip(), True)
    sys.stderr.write("\n--- ACCEPTANCE: recall get A-7 --root <scaffold> ---\n")
    sys.stderr.write("exit=%d\n" % got.returncode)
    sys.stderr.write("stderr: %s" % got.stderr.decode("utf-8", "replace"))
    sys.stderr.write("stdout:\n%s\n" % got.stdout.decode("utf-8", "replace"))
    sys.stderr.write("--- end acceptance ---\n\n")

    # the scaffold still validates after the drain, through the real validator CLI
    val = subprocess.run([sys.executable, "-S", "-I", str(here / "validate_entries.py"),
                          "--root", str(root)], capture_output=True)
    ck("acceptance: validate_entries.py over the drained scaffold", val.returncode, 0)


def _st_arms_contract(ck, _tmp):
    """The claims this file makes about its siblings, checked against the siblings themselves."""
    ck("exit codes agree with validate_entries",
       (EXIT_OK, EXIT_SELFTEST, EXIT_USAGE, EXIT_SCAN_INTEGRITY, EXIT_INTERNAL),
       (validate_entries.EXIT_OK, validate_entries.EXIT_SELFTEST, validate_entries.EXIT_USAGE,
        validate_entries.EXIT_SCAN_INTEGRITY, validate_entries.EXIT_INTERNAL))
    ck("every source is in the S3.1 file->kind table",
       [r for r in SOURCES if r not in validate_entries.FILE_KIND], [])
    ck("both row-5 destinations are the S3.2 deferred pair",
       sorted((D_BACKLOG_DEFERRED, D_OQ_DEFERRED)), sorted(validate_entries.GATE_REQUIRED))
    ck("row 1's destination is the one A6 reads", D_EXPIRED, validate_entries.EXPIRED_FILE)
    ck("row 1's source is the one A5/A6 govern", "active.md", validate_entries.ACTIVE_FILE)
    ck("every destination lives under _archive/",
       [d for d in ALL_DESTINATIONS if not d.startswith(ARCHIVE_DIR + "/")], [])

    # NO DESTINATION HEADER MAY CITE ONE OF *THIS* PROJECT'S IDENTIFIERS.
    #
    # `dest_header()` is written into an archive file the drain CREATES, inside whatever project
    # is being saved. An id from this project's own ledger in that prose therefore lands in the
    # HOST's identifier namespace, where it resolves to whatever they call `D81` -- or to nothing.
    # THE DANGLE IS THE LUCKY OUTCOME; a host that has its own `D81` goes GREEN with the citation
    # pointing at the wrong record, and no check anywhere reports it.
    #
    # MEASURED: both row-5 blurbs cited `D81`, so every project that ever deferred an entry got
    # it written into `_archive/backlog-deferred.md` and `_archive/open-questions-deferred.md`.
    # It went unseen for a long time because the header is emitted ONLY on creation -- a folder
    # whose deferred archives already exist never reaches the branch. An older v4 layout
    # may have neither file, so the created-fresh path must remain valid.
    #
    # THE ASSERTION COVERS EVERY DESTINATION, not the two that were wrong. A per-destination
    # spot-check would have to be extended by hand each time a destination is added, which is the
    # way this class survived three separate rounds of hand-fixing in the first place.
    _hdr_fams = citemap.build_families()
    _hdr_leak = sorted({
        "%s:%s" % (_rel, _id)
        for _rel in ALL_DESTINATIONS if _rel in DEST_BLURB
        for _line in dest_header(_rel).decode("utf-8").splitlines()
        for _id, _s, _e in citemap.scan_occurrences(_line, _hdr_fams)})
    ck("no destination header cites one of our ids", _hdr_leak, [])
    # Prove the detector is live rather than vacuous: the same scan must find a planted id.
    ck("...and that scan DOES catch one when present",
       sorted({_id for _id, _s, _e
               in citemap.scan_occurrences("carries a gate (D81) that returns it", _hdr_fams)}),
       ["D81"])
    # Every destination must HAVE a header, or the loop above silently covers fewer than it looks.
    ck("every destination has a blurb to check",
       [d for d in ALL_DESTINATIONS if d not in DEST_BLURB], [])
    ck("checkpoints/ is never a destination",
       [d for d in ALL_DESTINATIONS if "checkpoints" in d], [])
    ck("row 6 is not armed", 6 in ROW_PRECEDENCE, False)
    ck("row 5 precedes row 2b", ROW_PRECEDENCE.index(5) < ROW_PRECEDENCE.index("2b"), True)
    # D81's resolution set is the three files the ADR names, and no others. Widening it would
    # admit a gate pointing at something neither planned, parked nor finished.
    ck("D81's id-gate resolves against backlog.md + the two ADR archives",
       sorted(("backlog.md",) + GATE_RESOLVE_ARCHIVES),
       sorted(("backlog.md", "_archive/backlog-deferred.md", "_archive/backlog-done.md")))
    # The abandonment marker has to be COLLECTED to be read: `extra` is filled from
    # EXTRA_FIELD_KEYS, so a key missing from that set is a field `abandon_marker` never sees.
    ck("row 2b's `wont-do:` keys are actually collected",
       [k for k in DROP_KEYS if k not in EXTRA_FIELD_KEYS], [])
    ck("...and so are the two that mark a waiting line satisfied",
       [k for k in GATE_SATISFIED_KEYS if k not in EXTRA_FIELD_KEYS], [])
    # THE CLASS CHECK for the marker rows. Every ONE-WAY trigger must be anchored: a `.search()`
    # over free text is the shape that archived six live entries (see `_st_arms_markers`).
    ck("the row-3 and row-4 marker patterns are ANCHORED",
       [p.pattern for p in (RESOLVED_MARKER_RE, SUPERSEDED_MARKER_RE)
        if not p.pattern.startswith("^")], [])
    ck("the region answer is three-valued, not a None",
       sorted({BACKLOG_OK, BACKLOG_ABSENT, BACKLOG_MALFORMED}),
       sorted(["absent", "malformed", "ok"]))
    ck("every row in SOURCE_ROWS has a label and a slot in the order",
       sorted(set(r for rs in SOURCE_ROWS.values() for r in rs), key=str),
       sorted(set(ROW_LABEL) & set(ROW_PRECEDENCE), key=str))
    body = pathlib.Path(__file__).read_text(encoding="utf-8")
    ck("no build-host absolute path in this file",
       [ln for ln in body.splitlines() if re.search(r"[A-Za-z]:/(?:dev|Users|ash)/", ln)], [])
    # The needle is assembled at runtime. Spelled literally, this check would match ITSELF and
    # pass forever regardless of the file's contents -- a test whose subject is its own source
    # text has to avoid quoting what it looks for.
    ck("this file has no second fence machine", ("FENCE_RE" + " = ") in body, False)
    ck("this file has no second heading matcher", ("HEADING_RE" + " = ") in body, False)



def _st_arms_veto(ck, tmp):
    """The cross-file VETO (C-60) and its actionable receipt.

    WHAT IS TRUE, and it is narrower than what this docstring used to claim. Every arm that
    asserts the veto HELD something is paired with a control -- the same fixture with the citation
    removed, or the same citation moved into a scope that must not veto -- because an arm
    asserting only that the veto held something would pass just as well against a veto that holds
    EVERYTHING, and holding everything is the failure mode this design is one line away from
    (see V6). That pairing covers the HOLD/RELEASE question and nothing else.

    WHAT WAS FALSE, and it was the claim "EVERY arm below has a CONTROL that fires". The arms
    about the receipt's CONTENT -- which state a blocker is in, and whether the REPRIORITIZE line
    fires -- had no such pairing. V9a, V9b and V13 each built a fixture with exactly ONE blocker,
    so `states` was a one-element list, so `all(s == "live")` and `any(s == "live")` return the
    same value on every one of them and no arm anywhere could tell the two apart. That is not a
    hypothetical: it is the precise mutation that produces B-4a, where a capped `rows` slice fed
    the all-live decision and 12 live blockers plus a 13th non-live one printed REPRIORITIZE.

    V16..V22 are the missing half. Each one builds a state the old arms could not reach -- MIXED
    blocker states (V16), a non-live blocker PAST the display cap (V17), a citation nested inside
    a moving entry (V18), a citation belonging to no entry at all (V19), the mutual-veto deadlock
    (V20), both edges of the move span on a BOM file (V21), and a masked region that never closes
    (V22). Read them as the answer to "what input would make this arm go red"; an arm for which
    that question has no answer is a receipt, not a check.
    """
    backlog = ("<!-- ash_save v4 -->\n# plan\n\n"
               "## M12 - finished item\nkind: plan\ndone: save#10\n\n"
               "## M13 - a live item\nkind: plan\n")
    arch = ("<!-- ash_save v4 -->\n# architecture\n\n"
            "## D4 - the write path\nkind: spec\nThe contract M12 implements is stated here.\n")

    # V1 + V2. The PAIR is the test: one fixture where a hot file cites the moving entry, one
    # identical but for that sentence. Neither reading means anything without the other.
    held = _st_mk(tmp, "veto_on", {"backlog.md": backlog, "architecture.md": arch}, save=10)
    code, text = _st_run(["--root", str(held)])
    ck("veto: exit is still 0 -- a veto is not a failure", code, EXIT_OK)
    ck("veto: the entry did NOT reach the archive", "M12" in _st_ids_in(held, D_DONE), False)
    ck("veto: the entry is STILL in backlog.md", "M12" in _st_ids_in(held, "backlog.md"), True)
    ck("veto: the receipt has a VETOED block", "VETOED (1)" in text, True)
    ck("veto: nothing was reported as moved", text.startswith("demote: 0 entries moved"), True)

    free = _st_mk(tmp, "veto_off", {"backlog.md": backlog}, save=10)
    code2, text2 = _st_run(["--root", str(free)])
    ck("veto CONTROL: with no citation the SAME entry moves", "M12" in _st_ids_in(free, D_DONE),
       True)
    ck("veto CONTROL: ...and left backlog.md", "M12" in _st_ids_in(free, "backlog.md"), False)
    ck("veto CONTROL: ...and no VETOED block is printed", "VETOED" in text2, False)
    ck("veto CONTROL: exit", code2, EXIT_OK)

    # V8. C-60: the block must name the citing FILE:LINE, the citing ENTRY's id AND title, and
    # whether that entry is live. A refusal that does not say what to do is the thing the maintainer refused.
    ck("veto report: names the citing file:line", "cited by architecture.md:6" in text, True)
    ck("veto report: names the citing ENTRY's id", " in D4" in text, True)
    ck("veto report: names the citing entry's TITLE", "the write path" in text, True)
    ck("veto report: tags the blocker's state", VETO_STATE_TEXT["live"] in text, True)
    ck("veto report: says the entry stayed hot", "STAYED HOT" in text, True)

    # V9a. Every blocker LIVE -> the REPRIORITIZE warning, naming the work that releases the hold.
    ck("veto advice: REPRIORITIZE fires when every blocker is live", "REPRIORITIZE" in text, True)
    ck("veto advice: ...and names the blocking work", "EVERY blocker is LIVE WORK -- D4" in text,
       True)

    # V9b. Its CONTROL, and the intersection C-60 draws: a blocker in a file with NO drain row can
    # never go cold, so telling the reader to "finish" it is advice that cannot be taken.
    cons = ("<!-- ash_save v4 -->\n# constraints\n\n"
            "## C-1 - a standing rule\nkind: rules\nlifetime: permanent\n"
            "The rule M12 encodes is permanent.\n")
    nod = _st_mk(tmp, "veto_nodrain", {"backlog.md": backlog, "CONSTRAINTS.md": cons}, save=10)
    _c, tnod = _st_run(["--root", str(nod)])
    ck("veto: a CONSTRAINTS.md citation vetoes too", "M12" in _st_ids_in(nod, "backlog.md"), True)
    ck("veto advice: no-drain blocker is NOT a reprioritize case", "REPRIORITIZE" in tnod, False)
    ck("veto advice: ...and says why", "no drain row" in tnod, True)

    # V3. A self-reference inside the entry's OWN move span travels to the archive WITH it, so it
    # is not a hot reference left dangling. `citemap.build_map` drops only the DEFINING occurrence
    # and cannot express this one -- measured, it is 15 of 106 blocked entries on the real fleet.
    selfcite = ("<!-- ash_save v4 -->\n# plan\n\n"
                "## M12 - finished item\nkind: plan\ndone: save#10\n"
                "M12 is the one that shipped, and M12 is named twice on purpose.\n\n"
                "## M13 - a live item\nkind: plan\n")
    slf = _st_mk(tmp, "veto_self", {"backlog.md": selfcite}, save=10)
    _c3, t3 = _st_run(["--root", str(slf)])
    ck("veto: an entry citing ITSELF in its own span still moves",
       "M12" in _st_ids_in(slf, D_DONE), True)
    ck("veto: ...and no VETOED block is printed for it", "VETOED" in t3, False)

    # V4. Cold stores never veto: the entry is MOVING TO JOIN THEM.
    cold = _st_mk(tmp, "veto_cold",
                  {"backlog.md": backlog,
                   "_archive/misc.md": "# misc\n\nM12 was discussed at length.\n",
                   "checkpoints/checkpoint-1.md": "# cp\n\nM12 was decided here.\n"}, save=10)
    _c4, t4 = _st_run(["--root", str(cold)])
    ck("veto: a citation in _archive/ does NOT veto", "M12" in _st_ids_in(cold, D_DONE), True)
    ck("veto: ...nor one in checkpoints/", "VETOED" in t4, False)

    # AGENTS.md is a passive Codex instruction file. It is restored and counted, but its prose
    # never enters save's drain-veto graph. CLAUDE.md remains in VETO_SCOPE unchanged.
    passive = _st_mk(tmp, "veto_agents_passive",
                     {"backlog.md": backlog,
                      "AGENTS.md": "# Codex rules\n\nM12 is host-owned prose.\n"}, save=10)
    _cp, tp = _st_run(["--root", str(passive)])
    ck("veto: an AGENTS.md citation does NOT veto",
       "M12" in _st_ids_in(passive, D_DONE), True)
    ck("veto: passive AGENTS.md produces no VETOED block", "VETOED" in tp, False)

    claude = _st_mk(tmp, "veto_claude_control",
                    {"backlog.md": backlog,
                     "CLAUDE.md": "# Claude rules\n\nM12 is standing prose.\n"}, save=10)
    _cc, tc = _st_run(["--root", str(claude)])
    ck("veto CONTROL: a CLAUDE.md citation still vetoes",
       "M12" in _st_ids_in(claude, "backlog.md"), True)
    ck("veto CONTROL: CLAUDE.md still produces a VETOED block", "VETOED" in tc, True)

    # V5. `manifest.md` is regenerated IN FULL after this tool runs, so nothing in it is a
    # reference that will still exist when the save ends.
    man = _st_mk(tmp, "veto_manifest",
                 {"backlog.md": backlog,
                  "manifest.md": "# manifest\n\nrouter row mentioning M12\n"}, save=10)
    _c5, t5 = _st_run(["--root", str(man)])
    ck("veto: a citation in the GENERATED manifest.md does NOT veto",
       "M12" in _st_ids_in(man, D_DONE), True)
    ck("veto: ...and no VETOED block is printed for it", "VETOED" in t5, False)

    # V6. THE ONE THAT MATTERS. `update_state.py` rewrites BACKLOG-ENTRY-IDS every save from the
    # post-demote backlog, so that region names EVERY live backlog id by construction. In scope,
    # the veto would hold 100% of backlog.md for ever -- including an entry carrying `done:`.
    reg = _st_mk(tmp, "veto_region", {"backlog.md": backlog}, save=10, baseline=("M12", "M13"))
    _c6, t6 = _st_run(["--root", str(reg)])
    ck("veto: the BACKLOG-ENTRY-IDS region does NOT veto", "M12" in _st_ids_in(reg, D_DONE), True)
    ck("veto: ...and no VETOED block is printed for it", "VETOED" in t6, False)

    # V7. Its INTERSECTION, which is where V6 could be over-broad: `state.md`'s PROSE is PRESERVED
    # byte-for-byte by the same generator, so a citation there DOES survive the save and must
    # veto. V6 and V7 disagree on exactly one thing -- which part of one file was read.
    prose = _st_mk(tmp, "veto_stateprose", {"backlog.md": backlog}, save=10,
                   baseline=("M12", "M13"))
    sp = prose / STATE_FILE
    sp.write_bytes(sp.read_bytes() + b"\nactive_track: M12 is the next thing to land.\n")
    _c7, t7 = _st_run(["--root", str(prose)])
    ck("veto: state.md PROSE outside the region DOES veto",
       "M12" in _st_ids_in(prose, "backlog.md"), True)
    ck("veto: ...and the receipt names state.md as the blocker", "cited by state.md:" in t7, True)

    # V13. A blocker that is ITSELF leaving this save is not live work to reprioritise.
    chain = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## M12 - finished item\nkind: plan\ndone: save#10\n\n"
             "## M14 - also finished, and it mentions M12\nkind: plan\ndone: save#10\n")
    ch = _st_mk(tmp, "veto_chain", {"backlog.md": chain}, save=10)
    _c8, t8 = _st_run(["--root", str(ch)])
    ck("veto chain: the CITING entry, cited by nobody, moves", "M14" in _st_ids_in(ch, D_DONE),
       True)
    ck("veto chain: the CITED entry is held", "M12" in _st_ids_in(ch, "backlog.md"), True)
    ck("veto chain: the blocker is tagged as leaving, not as live",
       VETO_STATE_TEXT["moving"] in t8, True)
    ck("veto chain: ...so it is NOT a reprioritize case", "REPRIORITIZE" in t8, False)
    ck("veto chain: ...and the reader is told to re-run next save",
       "re-run the drain next save" in t8, True)

    # V13b. R-VETOORDER-1. THE SAME FIXTURE, EXCEPT THAT THE CITING ENTRY'S OWN MOVE IS REFUSED
    # BY `perform`. V13 above is the control and they differ in exactly one thing: whether
    # `_archive/backlog-done.md` already defines M14 with different bytes. The plan is identical
    # in both, which is the whole point -- a state read off the plan cannot tell them apart, so
    # this arm is red against any build that computes `moving` before `perform` runs. The
    # receipt used to say M14 "leaves this save -- re-run the drain next save" while listing
    # M14 under REFUSED as staying hot, in the same receipt.
    ch2 = _st_mk(tmp, "veto_order", {"backlog.md": chain}, save=10)
    (ch2 / D_DONE).write_bytes(
        dest_header(D_DONE) + b"\n## M14 - a DIFFERENT entry that already owns this id\n"
        b"kind: plan\ndone: save#4\n")
    _c8b, t8b = _st_run(["--root", str(ch2)])
    # THE CONTROL HAS TO PIN WHICH OUTCOME, NOT JUST THAT M14 STAYED HOT. Found by cross-family
    # review of this very arm: `_c8b` was captured and never asserted, and `"REFUSED" in t8b`
    # also matches VETO_STATE_TEXT["refused"], which the arm below is separately asserting. So
    # if destination screening regressed and M14 instead FAILED R-H byte-equality, `failed` is
    # folded into `outcome_refused` by design, the veto text still reads `refused`, M14 is still
    # hot -- and all six checks passed while `_st_run` returned EXIT_MOVE_FAILED. The arm written
    # to close a hollow assertion was hollow in the same way, one level down.
    ck("veto order CONTROL: exit 0 -- a REFUSAL is not a move failure", _c8b, EXIT_OK)
    ck("veto order CONTROL: the citing entry's own move IS refused",
       "M14" in _st_ids_in(ch2, "backlog.md"), True)
    ck("veto order CONTROL: ...named in the REFUSED BLOCK, not just the word somewhere",
       "REFUSED (1)" in t8b, True)
    ck("veto order CONTROL: ...and NOT under FAILED -- that is a different outcome",
       "FAILED (" in t8b, False)
    ck("veto order: the CITED entry is still held", "M12" in _st_ids_in(ch2, "backlog.md"), True)
    ck("veto order: a blocker perform REFUSED is not tagged as leaving this save",
       VETO_STATE_TEXT["moving"] in t8b, False)
    ck("veto order: ...it is tagged against what actually happened to it",
       VETO_STATE_TEXT["refused"] in t8b, True)
    ck("veto order: ...so the reader is NOT told to re-run the drain next save",
       "re-run the drain next save" in t8b, False)

    # V13c. THE OTHER HALF OF R-VETOORDER-1: THE WARMED CACHE, AND ONLY A REAL RUN REACHES IT.
    # V13b tests the STATE; this tests the ATTRIBUTION. `perform` deletes M10's span out of
    # backlog.md, shifting every line below it, so a heading walk done AFTER the move resolves
    # the citation's line number against the wrong entry. MEASURED with the cache discarded: the
    # receipt blamed M18 -- an unrelated live entry that names nothing -- and tagged it `marked`,
    # the live-reported-as-not-live mirror this file was already repaired for once.
    # A `--dry-run` comparison CANNOT test this, and a byte-identical dry-run receipt is not
    # evidence about it: nothing is rewritten under dry-run, so no line moves and a cold walk is
    # accidentally right. Raised as next-step 4 by cross-family review, and it was correct.
    shift = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## M10 - finished, cited by nobody, so it MOVES\nkind: plan\ndone: save#10\n"
             + "padding line so the mover shifts the entries below it\n" * 6 +
             "\n## M12 - finished item, cited from BELOW the mover\nkind: plan\ndone: save#10\n\n"
             "## M16 - live work, no marker, and it mentions M12\nkind: plan\n"
             "Status: still open; it references M12 in passing.\n\n"
             "## M18 - a DIFFERENT live entry that must not be blamed\nkind: plan\n"
             "Status: unrelated, and it names nothing.\n")
    sh = _st_mk(tmp, "veto_cacheshift", {"backlog.md": shift}, save=10)
    _c8c, t8c = _st_run(["--root", str(sh)])
    ck("veto cache: exit 0", _c8c, EXIT_OK)
    ck("veto cache CONTROL: the mover really LEFT backlog.md -- a real write, not a dry run",
       "M10" in _st_ids_in(sh, "backlog.md"), False)
    ck("veto cache CONTROL: ...and landed in the archive", "M10" in _st_ids_in(sh, D_DONE), True)
    ck("veto cache: the citation is attributed to M16, whose BYTES contain it",
       "in M16 - " in t8c, True)
    ck("veto cache: ...and NOT to M18, where the post-move line number points",
       "in M18 - " in t8c, False)
    ck("veto cache: ...and the state is LIVE, not the not-established mirror",
       VETO_STATE_TEXT["live"] in t8c, True)

    # V11. An OPEN FENCE in a veto-scope file that `integrity_scope` deliberately does NOT cover.
    # A citation below it is invisible, and invisible reads as "nothing cites it" -- the value
    # that licenses the move. Hold everything, delete nothing, and still exit 0 (C-53).
    # TWO proposed moves, deliberately: with one, "hold the cited entry" and "hold EVERY
    # proposed move" are the same assertion and the arm cannot tell them apart. M15 is cited by
    # nobody, so a veto that held only what is cited would let it through.
    fence = _st_mk(tmp, "veto_fence",
                   {"backlog.md": backlog + "\n## M15 - a second finished item, cited by nobody"
                                            "\nkind: plan\ndone: save#10\n",
                    "CLAUDE.md": "# rules\n\n```text\nan unterminated fence\n"}, save=10)
    before = _st_snapshot(fence)
    c9, t9 = _st_run(["--root", str(fence)])
    ck("veto fence: exit is still 0 -- nothing blocks a save (C-53)", c9, EXIT_OK)
    ck("veto fence: NOTHING moved", "M12" in _st_ids_in(fence, D_DONE), False)
    ck("veto fence: EVERY proposed move is held, including the one nobody cites",
       sorted(_st_ids_in(fence, D_DONE)), [])
    ck("veto fence: ...and the uncited one is still in backlog.md",
       "M15" in _st_ids_in(fence, "backlog.md"), True)
    ck("veto fence: not one byte changed", _st_snapshot(fence), before)
    ck("veto fence: the receipt says the scope was unreliable",
       "VETO SCOPE UNRELIABLE" in t9 and "CLAUDE.md" in t9, True)
    # F1. An unreliable scan produces an EMPTY blocker list, so NOTHING has been established
    # about citations. Reporting these under a header that asserts "a HOT file still CITES the
    # entry" sends the reader hunting for a citation that may not exist, and contradicts the
    # UNRELIABLE line a few rows further down the same receipt.
    ck("veto fence: held entries are NOT reported as cited", "still CITES the entry" in t9, False)
    ck("veto fence: they get their own block, which claims only that the status is UNKNOWN",
       "HELD (" in t9 and "is UNKNOWN" in t9, True)
    ck("veto fence: ...and the warning does not claim a citation either",
       "a hot file still cites this entry" in t9, False)

    # V15. The cap bounds CITING ENTRIES, not occurrences, and the tail is NAMED.
    many = ["<!-- ash_save v4 -->\n# plan\n\n",
            "## M12 - finished item\nkind: plan\ndone: save#10\n\n"]
    for i in range(VETO_MAX_BLOCKERS + 3):
        many.append("## M%d - a live item that mentions M12\nkind: plan\n"
                    "M12 is referenced again here.\n\n" % (20 + i))
    cap = _st_mk(tmp, "veto_cap", {"backlog.md": "".join(many)}, save=10)
    _c12, t12 = _st_run(["--root", str(cap)])
    cited_lines = [l for l in t12.splitlines() if "cited by " in l]
    ck("veto cap: prints at most VETO_MAX_BLOCKERS rows",
       len(cited_lines) <= VETO_MAX_BLOCKERS, True)
    ck("veto cap: each row is a DISTINCT citing entry",
       len({l.split(" in ", 1)[1] for l in cited_lines}), len(cited_lines))
    ck("veto cap: a repeated citation is counted, not repeated",
       any("(x2)" in l for l in cited_lines), True)
    ck("veto cap: the dropped tail is NAMED by file, never counted away",
       "more citing entries, in backlog.md x3" in t12, True)

    # V14. B5 x veto -- the INTERSECTION where the two predicates disagree, and the
    # only state in which the veto must NOT hold a cited entry. An earlier run
    # archived M20 and did not reach the source, so this run's whole job is deleting
    # the hot duplicate. Holding it would rebuild the permanent duplicate.
    entry_b5 = ("## M20 - parked work\nkind: plan\n"
                "gate: EXTERNAL - eventually the pilot reports back, whenever that is\n\n"
                "The body.\n")
    hot_b5 = "<!-- ash_save v4 -->\n# plan\n\n" + entry_b5
    arch_b5 = ("<!-- ash_save v4 -->\n# architecture\n\n"
               "## D8 - the contract\nkind: spec\nM20 is named right here.\n")
    archived_b5, _o5 = _build_destination(dest_header(D_BACKLOG_DEFERRED),
                                          [entry_b5.encode("utf-8")], created=True)
    b5 = _st_mk(tmp, "veto_b5",
                {"backlog.md": hot_b5, "architecture.md": arch_b5,
                 D_BACKLOG_DEFERRED: archived_b5.decode("utf-8")}, save=10)
    _c11, t11 = _st_run(["--root", str(b5)])
    ck("veto x B5: a CITED entry already archived still loses its hot duplicate",
       "M20" in _st_ids_in(b5, "backlog.md"), False)
    ck("veto x B5: ...and the veto did not report it", "VETOED" in t11, False)
    ck("veto x B5: ...and it resolves at exactly one site",
       _st_ids_in(b5, D_BACKLOG_DEFERRED).count("M20"), 1)
    ck("veto x B5: ...and NOTHING was appended to the archive",
       (b5 / D_BACKLOG_DEFERRED).read_bytes(), archived_b5)
    # A-7's residue. The exemption is right -- the id already resolves cold, so nothing dangles,
    # and holding it hot would rebuild the duplicate `_reconcile_probe` exists to end. What was
    # wrong is that the receipt did not SAY the veto had been skipped, and this whole design is
    # about checks that silently did not run.
    ck("veto x B5: ...and the receipt DISCLOSES that the veto was skipped on it",
       "VETO EXEMPT" in t11 and "M20" in t11, True)

    # V23. HELD BUT NOT MUTUAL -- the state no fixture reached, and the reason F2 was filed.
    # Membership in `held_spans` proves only that the CITER was also vetoed. Here M14 cites M12
    # and a LIVE M15 cites M14: both are held, and there is no edge from M12 back to M14. The
    # first version called that "a mutual hold" and told the reader to drop one of two
    # citations -- advice for a cycle that does not exist. Reciprocity is computed now, so this
    # fixture must report the weaker, true statement.
    nomut = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## M12 - finished item\nkind: plan\ndone: save#10\n\n"
             "## M14 - also finished, and it names M12\nkind: plan\ndone: save#10\n\n"
             "## M15 - a live item that needs M14\nkind: plan\nStill waiting on M14.\n")
    nm = _st_mk(tmp, "veto_heldnotmutual", {"backlog.md": nomut}, save=10)
    _c23, t23 = _st_run(["--root", str(nm)])
    ck("veto held-not-mutual: both finished entries are held",
       sorted(i for i in _st_ids_in(nm, "backlog.md") if i in ("M12", "M14")), ["M12", "M14"])
    ck("veto held-not-mutual: the citer is reported as ALSO VETOED",
       VETO_STATE_TEXT["held"] in t23, True)
    ck("veto held-not-mutual: ...and NOT as a cycle, which nothing established",
       VETO_STATE_TEXT["held-mutual"] in t23, False)
    ck("veto held-not-mutual: ...so no advice to break a cycle", "break the cycle" in t23, False)
    # The receipt legitimately contains `live` -- M15 really is live and really does cite M14,
    # in a DIFFERENT block. So assert on the blocker LINE the renderer emits for M12's citer,
    # never on a substring carved out of the formatted page.
    # The renderer writes `cited by <file>:<line> in <entry>`, so the CITING ENTRY is what
    # follows " in ". A bare `"M14" in l` also matched M15's block, whose TITLE contains M14 --
    # the same trap as matching a state tag a second formatter emits. Split on the separator the
    # renderer actually uses.
    _m12row = [l for l in t23.splitlines()
               if "cited by " in l and l.split(" in ", 1)[-1].startswith("M14")]
    ck("veto held-not-mutual: exactly one blocker line names M14", len(_m12row), 1)
    ck("veto held-not-mutual: ...and it is not called unfinished work",
       VETO_STATE_TEXT["live"] in (_m12row[0] if _m12row else ""), False)

    # V24. A BLOCKER WHOSE OWN MOVE WAS REFUSED, not vetoed. The veto runs AFTER the span
    # refusals, so a refused entry is in neither the plan nor the vetoed list -- it fell through
    # to `rel in SOURCE_ROWS` and was reported as LIVE work to finish, on an entry that carries
    # `done:` and whose move failed for a reason that has nothing to do with finishing anything.
    # The tear is the cheapest real refusal: an entry whose body quotes the BACKLOG delimiters.
    # ROW 5, not row 2. Quoting the delimiters is what forces the tear, and it also puts TWO
    # BEGIN and TWO END markers in the file -- at which point the engine disables rows 2 AND 2b
    # outright, because "exactly one ordered pair is the only shape whose interior is decidable".
    # Built on `done:` this fixture proposed nothing, so nothing was refused and nothing vetoed,
    # and the arm was asserting against a run in which its own subject never executed. Row 5 is
    # untouched by that shape.
    torn = ("<!-- ash_save v4 -->\n# plan\n\n"
            "<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n\n"
            "## M12 - parked work\nkind: plan\ngate: EXTERNAL - the pilot\n\n"
            "## M35 - parked, and it documents the markers\nkind: plan\n"
            "gate: EXTERNAL - the pilot\n"
            "It also names M12 here.\n\n"
            "The region is delimited by:\n<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n"
            "LAST LINE OF M35 - this sentence is part of the entry.\n")
    tr = _st_mk(tmp, "veto_refusedblocker", {"backlog.md": torn}, save=10, baseline=())
    _c24, t24 = _st_run(["--root", str(tr)])
    ck("veto refused-blocker: the tear is still refused", "REFUSED (1)" in t24, True)
    ck("veto refused-blocker: the cited entry is held", "M12" in _st_ids_in(tr, "backlog.md"),
       True)
    ck("veto refused-blocker: the blocker's state names the REFUSAL",
       VETO_STATE_TEXT["refused"] in t24, True)
    ck("veto refused-blocker: ...and does NOT call a finished entry unfinished work",
       VETO_STATE_TEXT["live"] in t24, False)
    ck("veto refused-blocker: ...so it is not a reprioritize case", "REPRIORITIZE" in t24, False)
    ck("veto refused-blocker: the advice points at the REFUSED block",
       "read the REFUSED block" in t24, True)

    # V26. A LEVEL-1 BANNER IS NOT AN ENTRY. `_entry_headings` matched every heading, so text
    # under `# plan` resolved to an id-less entry titled "plan", was labelled live, and produced
    # "REPRIORITIZE: finishing plan". V19's preamble arm passed only because ITS fixture put the
    # citation ABOVE the banner -- an arm named after the right case, asserting a property this
    # fault does not disturb, which is what B-5 was about in the first place.
    banner = ("<!-- ash_save v4 -->\n# plan\n\n"
              "This line sits under the banner and names M12; there is no entry here.\n\n"
              "## M12 - finished item\nkind: plan\ndone: save#10\n")
    bn = _st_mk(tmp, "veto_banner", {"backlog.md": banner}, save=10)
    _c26, t26 = _st_run(["--root", str(bn)])
    ck("veto banner: the entry is held", "M12" in _st_ids_in(bn, "backlog.md"), True)
    ck("veto banner: text under a LEVEL-1 banner is preamble, not an entry",
       VETO_STATE_TEXT["preamble"] in t26, True)
    ck("veto banner: ...and is NOT reported as live work", VETO_STATE_TEXT["live"] in t26, False)
    ck("veto banner: ...so the banner is not offered as work to finish",
       "REPRIORITIZE" in t26, False)
    ck("veto banner: ...and the banner's title is not presented as an entry",
       'titled "plan"' in t26, False)

    # V27. F1. MUTUALITY IS A PROPERTY OF THE PAIR, NOT OF THE CITER. M12 and M14 cite each
    # other -- a true cycle -- and M14 ALSO cites M16. All three are vetoed. Keyed on the citer's
    # own byte span, M14 was tagged `held-mutual` against EVERY entry it blocked, so M16 was told
    # to break an M14/M16 cycle that does not exist. The two states must appear in DIFFERENT
    # blocks of one receipt, which is the smallest input that separates them.
    graph = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## M12 - finished, and it names M14\nkind: plan\ndone: save#10\n\n"
             "## M14 - finished, and it names M12 and M16\nkind: plan\ndone: save#10\n\n"
             "## M16 - finished, and it names nobody\nkind: plan\ndone: save#10\n")
    gr = _st_mk(tmp, "veto_graph", {"backlog.md": graph}, save=10)
    _c27, t27 = _st_run(["--root", str(gr)])

    def _blk(text, ident):
        """The blocker lines printed under ONE vetoed entry's heading in the receipt."""
        out, inside = [], False
        for line in text.splitlines():
            if line.startswith("  " + ident + " ("):
                inside = True
                continue
            if inside:
                if line.startswith("      cited by "):
                    out.append(line)
                elif line.strip() and not line.startswith("      "):
                    break
        return out

    ck("veto graph: all three finished entries are held",
       sorted(i for i in _st_ids_in(gr, "backlog.md") if i in ("M12", "M14", "M16")),
       ["M12", "M14", "M16"])
    _m12 = _blk(t27, "M12")
    _m16 = _blk(t27, "M16")
    ck("veto graph: M12 has a blocker line", len(_m12) >= 1, True)
    ck("veto graph: M16 has a blocker line", len(_m16) >= 1, True)
    ck("veto graph: the RECIPROCAL pair M12<->M14 is reported as a mutual hold",
       any(VETO_STATE_TEXT["held-mutual"] in l for l in _m12), True)
    ck("veto graph: the NON-reciprocal pair M14->M16 is NOT",
       any(VETO_STATE_TEXT["held-mutual"] in l for l in _m16), False)
    ck("veto graph: ...it is reported as merely also-vetoed",
       any(VETO_STATE_TEXT["held"] in l for l in _m16), True)
    ck("veto graph: ...so M16 is not told to break a cycle that does not exist",
       "break the cycle" in "\n".join(_m16), False)

    # V28. F2. A LEVEL-1 BANNER AFTER AN ENTRY RESETS ATTRIBUTION. The banner ENDS M13's move
    # span -- `scan_source` stops at the next heading of the same or HIGHER level, and 1 is
    # higher than 2 -- so prose below it is outside M13. Dropping banners from the walk made
    # `_entry_at` keep returning M13, the citation got no containment hit, and it fell through
    # to `live`: "finishing M13 releases this", about a citation that is not in M13. V26's
    # banner precedes the first entry, so it only ever tested the reset-at-the-top case.
    later = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## M13 - a live entry\nkind: plan\nNothing here names anything.\n\n"
             "# notes\n\n"
             "This prose sits under a LATER banner and names M12.\n\n"
             "## M12 - finished item\nkind: plan\ndone: save#10\n")
    lt = _st_mk(tmp, "veto_laterbanner", {"backlog.md": later}, save=10)
    _c28, t28 = _st_run(["--root", str(lt)])
    ck("veto later-banner: the cited entry is held", "M12" in _st_ids_in(lt, "backlog.md"), True)
    ck("veto later-banner: a citation below a LATER banner belongs to no entry",
       VETO_STATE_TEXT["preamble"] in t28, True)
    ck("veto later-banner: ...and is NOT attributed to the entry above the banner",
       "in M13" in t28, False)
    ck("veto later-banner: ...nor called that entry's live work",
       VETO_STATE_TEXT["live"] in t28, False)
    ck("veto later-banner: ...so nothing is offered to finish", "REPRIORITIZE" in t28, False)

    # V16. MIXED BLOCKER STATES -- the arm whose absence B-5 is about. Every arm above builds a
    # ONE-element `states` list, on which `all(s == "live")` and `any(s == "live")` are the same
    # function. Two blockers in DIFFERENT states is the smallest input that tells them apart: one
    # live entry in `backlog.md`, one no-drain entry in `CONSTRAINTS.md`. REPRIORITIZE must NOT
    # fire, because finishing the live one would not release the hold.
    mixed = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## M12 - finished item\nkind: plan\ndone: save#10\n\n"
             "## M13 - a live item\nkind: plan\nThis one still depends on M12.\n")
    mx = _st_mk(tmp, "veto_mixed", {"backlog.md": mixed, "CONSTRAINTS.md": cons}, save=10)
    _c16, t16 = _st_run(["--root", str(mx)])
    ck("veto mixed: the entry is held", "M12" in _st_ids_in(mx, "backlog.md"), True)
    ck("veto mixed: BOTH blocker states are named",
       VETO_STATE_TEXT["live"] in t16 and VETO_STATE_TEXT["no-drain"] in t16, True)
    ck("veto mixed: REPRIORITIZE does NOT fire when only SOME blockers are live",
       "REPRIORITIZE" in t16, False)
    # The ADVICE, not the state tag: lowercase "no drain row" is a substring of
    # VETO_STATE_TEXT["no-drain"], so matching it here would be satisfied by a tag a different
    # formatter emits and would assert nothing about the advice line.
    ck("veto mixed: ...and the ADVICE names the no-drain half",
       "with NO drain row" in t16, True)
    ck("veto mixed: ...and the live half", "a blocker is live work" in t16, True)

    # V17. B-4a. THE CAP MUST BOUND DISPLAY, NEVER THE DECISION. `veto_report` computed every
    # blocker's state, sliced to VETO_MAX_BLOCKERS, and handed only the slice to `_veto_advice` --
    # so blocker 13's state was computed and thrown away one line before the all-live question
    # was asked. Exactly VETO_MAX_BLOCKERS live citers in `backlog.md`, then one in `state.md`
    # prose: `state.md` is LAST in VETO_SCOPE and is not in SOURCE_ROWS, so the no-drain blocker
    # is deterministically the 13th and lands in the tail.
    capmix = ["<!-- ash_save v4 -->\n# plan\n\n",
              "## M12 - finished item\nkind: plan\ndone: save#10\n\n"]
    for i in range(VETO_MAX_BLOCKERS):
        capmix.append("## M%d - a live item\nkind: plan\nIt depends on M12.\n\n" % (40 + i))
    cm = _st_mk(tmp, "veto_capmix", {"backlog.md": "".join(capmix)}, save=10)
    cmp_state = cm / STATE_FILE
    cmp_state.write_bytes(cmp_state.read_bytes()
                          + b"\nactive_track: M12 is quoted in the cursor prose.\n")
    _c17, t17 = _st_run(["--root", str(cm)])
    ck("veto cap-mix: the entry is held", "M12" in _st_ids_in(cm, "backlog.md"), True)
    ck("veto cap-mix: the display is still capped",
       len([l for l in t17.splitlines() if "cited by " in l]) <= VETO_MAX_BLOCKERS, True)
    ck("veto cap-mix: the tail is named and holds the state.md blocker",
       "in state.md x1" in t17, True)
    ck("veto cap-mix: REPRIORITIZE does NOT fire -- a blocker PAST THE CAP is not live",
       "REPRIORITIZE" in t17, False)
    # "with NO drain row" is emitted ONLY by `_veto_advice`. Lowercase "no drain row" is also a
    # substring of VETO_STATE_TEXT["no-drain"], so matching that would be satisfied by the state
    # TAG of some other blocker and would assert nothing about the advice -- and here the tail
    # blocker's tag is never printed at all, which is what made the difference visible.
    ck("veto cap-mix: ...and the ADVICE names the reason it did not",
       "with NO drain row" in t17, True)

    # V18. B-3. A `###` child inside a moving `##` is NOT live work to finish. `_entry_at` takes
    # the LAST heading at or before the line, so this citation resolves to the CHILD -- and
    # `scan_source` DROPS nested items from the item list entirely, so a child's heading line can
    # never be a plan key. Keyed on the heading it fell through to `rel in SOURCE_ROWS` and was
    # reported as LIVE WORK, inside an entry this same save archives. State is decided by SPAN
    # CONTAINMENT; the DISPLAY still names the child, which is the entry a reader would edit.
    nested = ("<!-- ash_save v4 -->\n# plan\n\n"
              "## M12 - finished item\nkind: plan\ndone: save#10\n\n"
              "## M14 - also finished\nkind: plan\ndone: save#10\n\n"
              "### M99 - a child carried inside M14's span\nkind: plan\n"
              "The child is what names M12.\n")
    nst = _st_mk(tmp, "veto_nested", {"backlog.md": nested}, save=10)
    _c18, t18 = _st_run(["--root", str(nst)])
    ck("veto nested: the CITING parent moves", "M14" in _st_ids_in(nst, D_DONE), True)
    ck("veto nested: the CITED entry is held", "M12" in _st_ids_in(nst, "backlog.md"), True)
    ck("veto nested: a citation inside a MOVING entry's span is tagged as leaving",
       VETO_STATE_TEXT["moving"] in t18, True)
    ck("veto nested: ...and NOT as live work to finish", VETO_STATE_TEXT["live"] in t18, False)
    ck("veto nested: ...so it is not a reprioritize case", "REPRIORITIZE" in t18, False)
    ck("veto nested: the DISPLAY still names the CHILD, which is the entry a reader edits",
       "M99 - a child carried inside" in t18, True)

    # V19. B-3, second half. A citation ABOVE the first heading belongs to no entry at all.
    # `_entry_at` returns `(0, None, "")` there, which keyed on the filename read as `live` and
    # printed "REPRIORITIZE: finishing an untitled entry ..." -- naming work that does not exist.
    pre = ("<!-- ash_save v4 -->\nThis line names M12 before any heading at all.\n\n"
           "# plan\n\n## M12 - finished item\nkind: plan\ndone: save#10\n")
    pr = _st_mk(tmp, "veto_preamble", {"backlog.md": pre}, save=10)
    _c19, t19 = _st_run(["--root", str(pr)])
    ck("veto preamble: the entry is held", "M12" in _st_ids_in(pr, "backlog.md"), True)
    ck("veto preamble: a citation belonging to no entry has its OWN state",
       VETO_STATE_TEXT["preamble"] in t19, True)
    ck("veto preamble: ...and is NOT reported as live work", VETO_STATE_TEXT["live"] in t19, False)
    ck("veto preamble: ...so nothing is offered to finish", "REPRIORITIZE" in t19, False)
    ck("veto preamble: the receipt says where it actually sits",
       "no heading at all above that line" in t19, True)

    # V20. A-3. THE MUTUAL-VETO DEADLOCK, and it is the one run B missed. Two entries both
    # carrying `done:` that cite each other are BOTH vetoed, so the post-veto plan is EMPTY, so
    # neither is `moving` -- and each was reported to the other as "LIVE -- unfinished work" with
    # a REPRIORITIZE line naming work that is already finished and cannot be finished again.
    # `_blocker_state`'s docstring said there were only three shapes. This is a fourth.
    deadlock = ("<!-- ash_save v4 -->\n# plan\n\n"
                "## M12 - finished item, and it names M14\nkind: plan\ndone: save#10\n\n"
                "## M14 - finished item\nkind: plan\ndone: save#10\nThis one names M12.\n")
    dl = _st_mk(tmp, "veto_deadlock", {"backlog.md": deadlock}, save=10)
    _c20, t20 = _st_run(["--root", str(dl)])
    ck("veto deadlock: BOTH entries are held", sorted(_st_ids_in(dl, "backlog.md")), ["M12", "M14"])
    ck("veto deadlock: ...and neither reached the archive", _st_ids_in(dl, D_DONE), [])
    ck("veto deadlock: both are reported", "VETOED (2)" in t20, True)
    ck("veto deadlock: a PROVEN cycle is reported as a mutual hold",
       VETO_STATE_TEXT["held-mutual"] in t20, True)
    ck("veto deadlock: ...and is NOT reported as unfinished work",
       VETO_STATE_TEXT["live"] in t20, False)
    ck("veto deadlock: ...so no finished work is offered to be finished again",
       "REPRIORITIZE" in t20, False)
    ck("veto deadlock: the advice says how to break the cycle", "mutual hold" in t20, True)

    # V21. B-2. BOTH EDGES OF THE MOVE SPAN, on the one input where they differ. `Cite.off` is a
    # RAW line start; `scan_source` sets `byte_start = max(bstart, bom) if bom else bstart`. On a
    # BOM file those differ by exactly three bytes, so only a row at `bstart == 0` falls in the
    # gap -- and that row is the entry's own heading, which then counts as an outside blocker and
    # refuses the move. It fails SAFE, and it is unreachable on any real hot file because they all
    # open with a watermark. Fixed anyway: "unreachable" is a fact about the corpus, not the code.
    bom_self = ("\ufeff## M12 - M12 is the finished one\nkind: plan\ndone: save#10\n\n"
                "## M13 - a live item\nkind: plan\n")
    bs = _st_mk(tmp, "veto_bom", {"backlog.md": bom_self}, save=10)
    _c21, t21 = _st_run(["--root", str(bs)])
    ck("veto BOM: an entry's own heading is inside its own span even behind a BOM",
       "M12" in _st_ids_in(bs, D_DONE), True)
    ck("veto BOM: ...so no false blocker is reported", "VETOED" in t21, False)

    # V21b. The RIGHT edge, which the same comparison decides and which fails the other way. The
    # next heading starts at exactly `byte_end`, so a citation there is OUTSIDE the span and MUST
    # block. `<` and `<=` differ on exactly this input and on no other.
    bnd = ("<!-- ash_save v4 -->\n# plan\n\n"
           "## M12 - finished item\nkind: plan\ndone: save#10\n\n"
           "## M13 - the successor to M12\nkind: plan\n")
    bd = _st_mk(tmp, "veto_bound", {"backlog.md": bnd}, save=10)
    _c21b, t21b = _st_run(["--root", str(bd)])
    ck("veto boundary: a citation at exactly byte_end is OUTSIDE the span and blocks",
       "M12" in _st_ids_in(bd, "backlog.md"), True)
    ck("veto boundary: ...and is named as a blocker", "cited by backlog.md:" in t21b, True)

    # V22. A-6. A MASKED REGION THAT NEVER CLOSES IS A BLIND REGION, AND A BLIND REGION MAY NOT
    # LICENSE A MOVE. Masking to EOF made every citation below the marker invisible, and invisible
    # reads as "nothing cites it" -- which is the value that authorises the move. Structurally
    # what an open fence forbids, arriving by a different blinding mechanism.
    openreg = _st_mk(tmp, "veto_openregion", {"backlog.md": backlog}, save=10,
                     baseline=("M13",))
    orp = openreg / STATE_FILE
    orp.write_bytes(orp.read_bytes().replace(b"<!-- BACKLOG-ENTRY-IDS:END -->\n", b""))
    before_or = _st_snapshot(openreg)
    c22, t22 = _st_run(["--root", str(openreg)])
    ck("veto open-region: exit is still 0 -- nothing blocks a save (C-53)", c22, EXIT_OK)
    ck("veto open-region: NOTHING moved", "M12" in _st_ids_in(openreg, D_DONE), False)
    ck("veto open-region: not one byte changed", _st_snapshot(openreg), before_or)
    ck("veto open-region: the receipt says the SCOPE was unreliable, not just that a region is odd",
       "VETO SCOPE UNRELIABLE" in t22 and "state.md" in t22, True)
    ck("veto open-region: ...and names the line to close",
       "never closes" in t22, True)

    # V12. An entry with no resolvable id cannot be screened by an id-keyed veto. It still moves
    # -- refusing would make the drain a no-op on a fresh scaffold -- but the receipt SAYS SO,
    # because a check that silently did not run is the failure this project keeps meeting.
    noid = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## a finished item with no id at all\nkind: plan\ndone: save#10\n")
    ni = _st_mk(tmp, "veto_noid", {"backlog.md": noid}, save=10)
    _c10, t10 = _st_run(["--root", str(ni)])
    # FILESYSTEM STATE, not the receipt's own account of itself. The old pair asked the receipt
    # whether it had moved anything, which is the thing under test answering the question about
    # itself; `_st_ids_in` cannot help here because the entry has no id, so the bytes are the
    # only handle it has.
    ck("veto blind: an id-less entry still moves -- read from disk, not from the receipt",
       b"no id at all" in (ni / D_DONE).read_bytes(), True)
    ck("veto blind: ...and it LEFT backlog.md",
       b"no id at all" in (ni / "backlog.md").read_bytes(), False)
    ck("veto blind: ...and the receipt says it was NOT screened", "VETO BLIND" in t10, True)
    # B-1. The note is emitted BEFORE `perform` runs, so it may not claim the entry moved --
    # `screen_batch` can still block the batch and R-H can still refuse it, either of which
    # would leave REFUSED or FAILED contradicting this note in the same receipt.
    ck("veto blind: the note claims IMPOSSIBLE SCREENING, never a completed move",
       "moved with NO cross-file check" in t10, False)
    ck("veto blind: ...and points the reader at the block that does decide movement",
       "read MOVED / REFUSED / FAILED below" in t10, True)

    # V23 + V24. THE RESIDUAL ITSELF. `live` is reached by ELIMINATION and makes the strongest
    # claim of the seven states -- this is unfinished work, and finishing it releases the hold.
    # Rounds 1-3 each found one condition landing there and added a branch ABOVE it; the residual
    # was never touched, so the next condition to arrive landed in it again. These two arms are
    # the two conditions that were still landing there when the veto shipped, and they are a
    # CLASS: any future state that keeps an entry out of the three span sets reads `live` too.
    #
    # V23. A row-5 TERMINAL HOLD (an inadmissible `gate:`) leaves `row=None`, so the entry is in
    # the plan, the vetoed set and the span-refused set NOWHERE. It needs no malformed region and
    # no unusual folder. The same run's own WARNING says that entry "STAYS IN backlog.md ... and
    # no later row may move it" -- the drain already held the fact its receipt then contradicted.
    holdcite = ("<!-- ash_save v4 -->\n# plan\n\n"
                "## M12 - finished item\nkind: plan\ndone: save#10\n\n"
                "## M13 - held by a gate that names no condition\nkind: plan\n"
                "gate: when we feel like it\n"
                "Context: this was the work behind M12.\n")
    hc = _st_mk(tmp, "veto_heldrow", {"backlog.md": holdcite}, save=10)
    _c23, t23 = _st_run(["--root", str(hc)])
    # STIMULUS CONTROL FIRST. A fixture that proposes nothing produces the same silent pass as a
    # fixture the fix handles, and this project has already shipped one arm that never reached
    # its own subject. Assert the veto FIRED before asserting anything about what it said.
    ck("veto class V23: the veto fired -- the arm reached its subject", "VETOED (1)" in t23, True)
    ck("veto class V23: a row-5 HELD citer is NOT tagged LIVE",
       VETO_STATE_TEXT["live"] in t23, False)
    ck("veto class V23: ...so REPRIORITIZE does not name it as work to finish",
       "REPRIORITIZE" in t23, False)
    ck("veto class V23: ...and the veto still HELD the entry -- the fix must not weaken it",
       "M12" in _st_ids_in(hc, "backlog.md"), True)

    # V24. The instance that SHIPPED at save#147. A doubled BACKLOG pair disables rows 2 AND 2b
    # for the whole FILE, so an entry carrying `done:` is never proposed and reaches none of the
    # three span sets. THE MOVER MUST COME FROM A ROW THE REGION DOES NOT DISABLE: the first
    # attempt put both entries in `backlog.md`, so nothing was proposed, nothing was vetoed, and
    # the arm passed against a run in which its own subject never executed.
    doubled = ("<!-- ash_save v4 -->\n# plan\n\n"
               "## M12 - a FINISHED entry that cites OQ3\nkind: plan\n"
               "done: save#10 - finished and marked\n"
               "Context: this was the work behind OQ3.\n\n"
               "<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n"
               "<!-- BACKLOG:BEGIN -->\n<!-- BACKLOG:END -->\n")
    oq3 = ("<!-- ash_save v4 -->\n# open questions\n\n"
           "## OQ3 - RESOLVED - the answer\nkind: plan\n")
    db = _st_mk(tmp, "veto_malformed", {"backlog.md": doubled, "open_questions.md": oq3}, save=10)
    _c24, t24 = _st_run(["--root", str(db)])
    ck("veto class V24: the veto fired -- the arm reached its subject", "VETOED (1)" in t24, True)
    ck("veto class V24: a citer carrying `done:` in a drain-disabled file is NOT tagged LIVE",
       VETO_STATE_TEXT["live"] in t24, False)
    ck("veto class V24: ...so REPRIORITIZE does not name work that is already finished",
       "REPRIORITIZE" in t24, False)

    # V25. THE FALLTHROUGH ITSELF, and it is a UNIT arm rather than an end-to-end one BECAUSE NO
    # FIXTURE REACHES IT. That is a finding and it is recorded rather than papered over: two
    # constructions were tried against the real interface and neither arrives here. A citation
    # PAST the BACKLOG region resolves inside the machine-managed item's own span and is
    # correctly `live`; a citation in the GAP after the region is refused upstream by the
    # span-tear check ("trimming would TEAR the entry") before the veto runs at all. So the
    # branch is DEFENSIVE -- it guards the next condition that keeps an entry out of every span
    # set, which is precisely the arrival the residual kept failing to survive. A direct call is
    # the only honest way to hold it; calling this end-to-end coverage would be the exact species
    # of unestablished claim this whole change exists to stop.
    orphan = Cite("backlog.md", 9, 999999, "M12 named where no scanned entry contains it")
    ck("veto class V25: an unattributable citation is NOT claimed as live work (UNIT)",
       _blocker_state(orphan, ("backlog.md", 4), {}, {}, {}, {}, set(),
                      ("backlog.md", 0, 10), citemap.build_families(), 10),
       "marked")

    # V26 + V27 + V28. THE MIRROR-IMAGE DEFECT AND THE ONE THAT SURVIVED THE REPAIR. All three
    # were found by cross-family review as CRITICALS, all three reproduced here before any fix,
    # and all three are failures of the SAME discipline the `marked` state exists to enforce:
    # say only what has been established.
    #
    # V26. A row-3 NEAR MISS is not a disposition. `## OQ4 - NOT RESOLVED: ...` is a heading whose
    # entire content is that the question is OPEN, and the first `entry_marked` called it `marked`
    # -- so finished-reported-as-live had become live-reported-as-not-live, in the function
    # written to end the first. Its docstring called the near-miss rule DELIBERATE, which is why
    # a reader checking my work would have read it as considered rather than wrong.
    nearmiss = {"backlog.md": ("<!-- ash_save v4 -->\n# plan\n\n"
                               "## M12 - finished item\nkind: plan\ndone: save#10\n"),
                "open_questions.md": ("<!-- ash_save v4 -->\n# open questions\n\n"
                                      "## OQ4 - NOT RESOLVED: which retrieval shape wins\n"
                                      "kind: plan\nThe answer decides M12.\n")}
    nm = _st_mk(tmp, "veto_nearmiss", nearmiss, save=10)
    _c26, t26 = _st_run(["--root", str(nm)])
    ck("veto class V26: the veto fired -- the arm reached its subject", "VETOED (1)" in t26, True)
    ck("veto class V26: a `NOT RESOLVED` heading IS live work, not a disposition",
       VETO_STATE_TEXT["live"] in t26, True)
    ck("veto class V26: ...so REPRIORITIZE names it", "REPRIORITIZE" in t26, True)

    # V27. A SATISFIED gate is the ABSENCE of a gate: the entry is ordinary live work. Keying on
    # "is there a `gate:` field" tagged it as parked.
    satgate = {"backlog.md": ("<!-- ash_save v4 -->\n# plan\n\n"
                             "## M12 - finished item\nkind: plan\ndone: save#10\n\n"
                             "## M13 - live work whose gate already opened\nkind: plan\n"
                             "gate: SATISFIED - channel B opened save#9\n"
                             "This is the work behind M12.\n")}
    sg = _st_mk(tmp, "veto_satgate", satgate, save=10)
    _c27, t27 = _st_run(["--root", str(sg)])
    ck("veto class V27: a SATISFIED gate is live work, not a parked entry",
       VETO_STATE_TEXT["live"] in t27, True)
    ck("veto class V27: ...and an UNSATISFIED one is still not",
       VETO_STATE_TEXT["live"] in _st_run(["--root", str(_st_mk(
           tmp, "veto_unsatgate",
           {"backlog.md": satgate["backlog.md"].replace(
               "gate: SATISFIED - channel B opened save#9",
               "gate: EXTERNAL - channel B opens")}, save=10))])[1],
       False)

    # V28. THE ORIGINAL DEFECT, SURVIVING THE REPAIR THROUGH THE DISPLAY/CLASSIFICATION GAP.
    # `scan_source` drops nested items, so a `###` child's citation classifies against its
    # retained `##` ancestor while `_entry_at` NAMES the child. A finished child under an
    # unmarked parent printed as the CHILD's "LIVE -- unfinished work" with a REPRIORITIZE line
    # telling the operator to finish work already carrying `done:`. The reviewer that found this
    # had no line number and no evidence quote for it.
    nested = {"backlog.md": ("<!-- ash_save v4 -->\n# plan\n\n"
                            "## M12 - finished item\nkind: plan\ndone: save#10\n\n"
                            "## M13 - a live parent with no marker of its own\nkind: plan\n\n"
                            "### M14 - a FINISHED child\nkind: plan\ndone: save#9\n"
                            "This child was the work behind M12.\n")}
    nst = _st_mk(tmp, "veto_nested_marked", nested, save=10)
    _c28, t28 = _st_run(["--root", str(nst)])
    ck("veto class V28: the veto fired -- the arm reached its subject", "VETOED (1)" in t28, True)
    ck("veto class V28: a FINISHED nested child is NOT called live work",
       VETO_STATE_TEXT["live"] in t28, False)
    ck("veto class V28: ...so REPRIORITIZE does not tell the operator to finish it",
       "REPRIORITIZE" in t28, False)
    ck("veto class V28: ...and the receipt still NAMES the child, which is what a reader edits",
       "M14" in t28, True)

    # V29. THE ADVICE MAY NOT ASSERT A WARNING EXISTS. Several `marked` causes emit none, so the
    # old wording pointed the operator at a block that can be empty and named a repair that may
    # not exist -- the same unestablished claim the state was added to end.
    ck("veto class V29: the marked advice does not assert a WARNINGS entry exists",
       "the WARNINGS block names the row" in t28, False)
    ck("veto class V29: ...and says what to do instead", "drop the citation" in t28, True)

    # V30. THE PREDICATE CONTRACT `entry_marked` RESTS ON -- and the one question cross-family
    # review structurally COULD NOT answer. `--source` inlines a single file, so the reviewer
    # never saw `resolved_marker`, `superseded_by`, `gate_state`, `done_marker`, `abandon_marker`
    # or `expiry_absolute`; it inferred their shapes from call sites here and returned no finding
    # on them. That is an UNANSWERED QUESTION, not a pass -- and round 2 tightened the dependency
    # by switching every one to index [0]. So each row gets a FIRES case and a DOES-NOT-FIRE
    # case: a predicate I misread shows up as a row whose two cases agree, which no assertion
    # about a single case could reveal.
    # The fixture directory is named by INDEX, not by the label: the labels carry backticks and
    # colons, which are illegal in a Windows path, and deriving a directory name from prose made
    # the whole suite exit 7 on an OSError. The label belongs in the assertion, not on disk.
    for (pidx, (rel, why, body, want)) in enumerate((
            ("active.md", "row 1 absolute expiry",
             "## A-8 - asserted save#8\nexpires: save#11\nkind: rules\nlifetime: expiring\n", True),
            ("active.md", "row 1 no expiry",
             "## A-8 - asserted save#8\nkind: rules\nlifetime: expiring\n", False),
            ("backlog.md", "row 2 real value",
             "## M12 - x\nkind: plan\ndone: save#10 - shipped\n", True),
            ("backlog.md", "row 2 PLACEHOLDER value -- row 2 will not fire, so it is unfinished",
             "## M12 - x\nkind: plan\ndone: TBD\n", False),
            ("backlog.md", "row 2b real wont-do",
             "## M12 - x\nkind: plan\nwont-do: the approach was abandoned\n", True),
            ("backlog.md", "row 2b `not-done:` matches nothing",
             "## M12 - x\nkind: plan\nnot-done: still open\n", False),
            ("open_questions.md", "row 3 status position",
             "## OQ9 - RESOLVED - the answer\nkind: plan\n", True),
            ("open_questions.md", "row 3 NEAR MISS `NOT RESOLVED` is an OPEN question",
             "## OQ9 - NOT RESOLVED: which shape wins\nkind: plan\n", False),
            ("design.md", "row 4 real successor",
             "## D4 - x\nkind: spec\nlifetime: conditional - until y\nSuperseded by D9.\n", True),
            ("design.md", "row 4 MENTION with no first-token id",
             "## D4 - x\nkind: spec\nlifetime: conditional - until y\n"
             "Superseded by a later decision, unlike D9.\n", False),
            ("backlog.md", "row 5 unsatisfied",
             "## M12 - x\nkind: plan\ngate: EXTERNAL - channel B opens\n", True),
            ("backlog.md", "row 5 placeholder",
             "## M12 - x\nkind: plan\ngate: none\n", True),
            ("backlog.md", "row 5 SATISFIED is the ABSENCE of a gate",
             "## M12 - x\nkind: plan\ngate: SATISFIED - it opened save#9\n", False),
            ("backlog.md", "row 5 no gate",
             "## M12 - x\nkind: plan\nOrdinary live work.\n", False))):
        pr = _st_mk(tmp, "pred_%02d" % pidx,
                    {rel: "<!-- ash_save v4 -->\n# t\n\n" + body}, save=10)
        pfams = citemap.build_families()
        pitems, _pc, _po, _pd, _prg = scan_source(rel, pr / rel, pfams)
        ck("veto class V30: %s" % why,
           bool(pitems) and bool(entry_marked(pitems[0], pfams, 10)), want)



def _st_arms_veto_notes(ck, tmp):
    """R-VETOTEXT-1. THE THREE NOTES, AND THE ONE SILENCE. Every arm below was run against the
    UNFIXED binary first (`proto.py`), so each must-redden assertion is a real red rather than a
    typo asserting on output the fixture never produces (R-CLAUDE-47).
    """
    # --- N1. `VETO EXEMPT` is emitted BEFORE `perform`, so it may not report the outcome.
    #     The B5 shape: a terminal hold whose entry is ALREADY archived byte-identically.
    entry = ("## M20 - parked work\nkind: plan\n"
             "gate: EXTERNAL - eventually the pilot reports back, whenever that is\n\n"
             "The body.\n")
    hot = "<!-- ash_save v4 -->\n# plan\n\n" + entry
    archived, _off = _build_destination(dest_header(D_BACKLOG_DEFERRED),
                                        [entry.encode("utf-8")], created=True)
    r1 = _st_mk(tmp, "vetotext_exempt",
                {"backlog.md": hot, D_BACKLOG_DEFERRED: archived.decode("utf-8")},
                save=10, baseline=())
    _c1, t1 = _st_run(["--root", str(r1)])
    ck("vetotext/exempt CONTROL: exit -- a red here is a crash, not the repair", _c1, EXIT_OK)
    ck("vetotext/exempt CONTROL: the exemption really fired", "VETO EXEMPT on 1" in t1, True)
    ck("vetotext/exempt: the note no longer reports an outcome `perform` has not produced",
       "this move deletes the hot duplicate" in t1, False)
    ck("vetotext/exempt: ...it defers the outcome to the blocks that record one",
       "read MOVED / REFUSED / FAILED below, never this note" in t1, True)
    ck("vetotext/exempt: ...and says what completing it WOULD do, conditionally",
       "COMPLETING the move would remove the hot duplicate" in t1, True)
    ck("vetotext/exempt: the tail no longer promises resolution on EVERY outcome",
       "whichever way this run ends" in t1, False)
    ck("vetotext/exempt: ...it names the MOVED case as the one that resolves",
       "on MOVED it resolves from `_archive/` alone" in t1, True)
    ck("vetotext/exempt: ...and names the exit-4 consequence of the other outcomes",
       "exits 4 until EXACTLY ONE definition of it remains" in t1, True)
    ck("vetotext/exempt CONTROL: the behaviour is UNCHANGED -- the move still completes",
       "M20" in _st_ids_in(r1, "backlog.md"), False)
    ck("vetotext/exempt CONTROL: ...and landed in the archive it was already in",
       _st_ids_in(r1, D_BACKLOG_DEFERRED).count("M20"), 1)

    # --- N1b. THE NON-MOVED RECEIPT TEST. Every exemption arm ran the MOVED path, which is the
    #     one outcome where "the id keeps resolving" happens to be true -- so the universal
    #     survived every check written to catch universals. TWO byte-identical copies in the
    #     destination make `screen_batch` refuse the entry as ambiguous IN THE DESTINATION
    #     ALONE, so the note is emitted, the entry stays HOT, and the archived copies stay put:
    #     three definitions, and `recall get` exits 4. Raised as the critical finding of
    #     review round 1, and it was right.
    dbl, _offd = _build_destination(dest_header(D_BACKLOG_DEFERRED),
                                    [entry.encode("utf-8"), entry.encode("utf-8")], created=True)
    r1b = _st_mk(tmp, "vetotext_exempt_refused",
                 {"backlog.md": hot, D_BACKLOG_DEFERRED: dbl.decode("utf-8")},
                 save=10, baseline=())
    _c1b, t1b = _st_run(["--root", str(r1b)])
    ck("vetotext/refused CONTROL: exit", _c1b, EXIT_OK)
    ck("vetotext/refused CONTROL: the exemption note still fires on this entry",
       "VETO EXEMPT on 1" in t1b, True)
    ck("vetotext/refused CONTROL: ...and the outcome really is NOT a move",
       "REFUSED (1)" in t1b, True)
    ck("vetotext/refused: the entry stayed HOT, which the old tail said was still fine",
       "M20" in _st_ids_in(r1b, "backlog.md"), True)
    ck("vetotext/refused: ...beside the archived copies, so the id is NOT singly resolvable",
       _st_ids_in(r1b, D_BACKLOG_DEFERRED).count("M20") >= 1, True)
    ck("vetotext/refused: and the note does not claim resolution regardless of outcome",
       "whichever way this run ends" in t1b, False)

    # --- N2. THE SAME NOTE ON AN ENTRY WITH NO ID. `_already_offsets` lets the BYTES decide
    #     when `ident` is None, so `_reconcile_probe` admits identless entries -- and the note
    #     promised an id resolved while the ALREADY note in the SAME receipt said `get` cannot
    #     address the entry at all. Two sentences, one receipt, one entry, contradicting.
    entry2 = ("## a parked heading that matches no citemap family\nkind: plan\n"
              "gate: EXTERNAL - eventually the pilot reports back, whenever that is\n\n"
              "The body.\n")
    hot2 = "<!-- ash_save v4 -->\n# plan\n\n" + entry2
    archived2, _off2 = _build_destination(dest_header(D_BACKLOG_DEFERRED),
                                          [entry2.encode("utf-8")], created=True)
    r2 = _st_mk(tmp, "vetotext_exempt_noid",
                {"backlog.md": hot2, D_BACKLOG_DEFERRED: archived2.decode("utf-8")},
                save=10, baseline=())
    _c2, t2 = _st_run(["--root", str(r2)])
    ck("vetotext/exempt-noid CONTROL: exit", _c2, EXIT_OK)
    ck("vetotext/exempt-noid CONTROL: an identless entry really does reach the exemption",
       "VETO EXEMPT on 1" in t2, True)
    ck("vetotext/exempt-noid: the note no longer promises an id that does not exist",
       "the id keeps resolving cold" in t2, False)
    # SCOPED TO THE EXEMPT LINE, never to a count over the whole receipt. `>= 2` was the
    # first draft and it PASSED ON THE UNFIXED BINARY: two other notes -- the id-less
    # label line and `screen_batch`'s ALREADY note -- already carry that phrase, so the
    # count was 2 before the repair and 3 after. An assertion that is true on the fault
    # it guards is a receipt, not a check (R-CLAUDE-66), and only the control showed it.
    _ve_line = [l for l in t2.splitlines() if "VETO EXEMPT" in l]
    ck("vetotext/exempt-noid CONTROL: there is exactly one VETO EXEMPT line to read",
       len(_ve_line), 1)
    ck("vetotext/exempt-noid: ...and THAT line is the sentence that stopped contradicting "
       "the two beside it",
       "cannot address" in (_ve_line[0] if _ve_line else ""), True)

    # --- N3. THE CARRIED-CHILD SCOPE, DISCLOSED. A no-id parent carrying `M40`, which a live
    #     CONSTRAINTS.md rule cites. VETO BLIND already fires here (the parent has no id), and
    #     it is where every carried-child instance measured on this machine actually sits.
    cons = ("<!-- ash_save v4 -->\n# constraints\n\n"
            "## C-1 - a live rule that names M40\nkind: rules\nlifetime: permanent\n"
            "This rule depends on M40 and is not going anywhere.\n")
    hot3 = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## a finished parent that matches no citemap family\n"
            "kind: plan\ndone: save#10\n\n"
            "### M40 - a nested child riding inside the parent's span\n"
            "kind: plan\nIts own body.\n")
    r3 = _st_mk(tmp, "vetotext_carried_noid",
                {"backlog.md": hot3, "CONSTRAINTS.md": cons}, save=10, baseline=())
    _c3, t3 = _st_run(["--root", str(r3)])
    ck("vetotext/carried CONTROL: exit", _c3, EXIT_OK)
    ck("vetotext/carried: the cited CHILD now holds its parent -- the parent stays hot",
       b"finished parent" in (r3 / "backlog.md").read_bytes(), True)
    ck("vetotext/carried: ...so the child did NOT go cold under a live citation",
       "M40" in _st_ids_in(r3, D_DONE), False)
    ck("vetotext/carried: ...and the receipt says the move was VETOED",
       "VETOED" in t3, True)
    ck("vetotext/carried: the blocker named is the rule that cites the child",
       "CONSTRAINTS.md" in t3, True)

    # --- N3b. THE BLIND NOTE STILL EXISTS, and now says what is actually missing. An entry with
    #     no id AND no carried ids is genuinely unscreenable; the note used to claim the carried
    #     ids were unscreened too, which stopped being true the moment the screen was widened.
    hot3b = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## a finished parent that matches no citemap family\n"
             "kind: plan\ndone: save#10\n\nNothing nested inside it.\n")
    r3b = _st_mk(tmp, "vetotext_blind_plain",
                 {"backlog.md": hot3b, "CONSTRAINTS.md": cons}, save=10, baseline=())
    _c3b, t3b = _st_run(["--root", str(r3b)])
    ck("vetotext/blind CONTROL: the note fires for a genuinely unscreenable entry",
       "VETO BLIND on 1" in t3b, True)
    # THIS FIXTURE HAS NO SCREENABLE ID AT ALL -- no own id, nothing nested -- so it belongs
    # to the absent-check population and must NOT be described as a partial one. The
    # partial branch is arm N3c's subject, and keeping the two apart is the whole repair.
    ck("vetotext/blind: it names having nothing to look up as the reason",
       "neither its own heading nor anything nested inside it resolves to an id" in t3b, True)
    ck("vetotext/blind: ...and no longer says carried ids go unscreened",
       "never screened either" in t3b, False)
    ck("vetotext/blind: ...nor claims a partial check it did not perform",
       "WERE screened and would have held" in t3b, False)


    # --- N1c. THE COUNT IN THE ADVICE. Round 2's CRITICAL, on mechanical arithmetic: N1b leaves
    #     ONE hot entry beside TWO archived copies, so removing "one of the two" leaves two and
    #     `get` is still ambiguous. The fixture round 1 told me to add falsified the sentence I
    #     wrote in the same commit. The condition must be a COUNT that holds at any arity.
    ck("vetotext/count CONTROL: the refusal fixture really does leave THREE definitions",
       ("M20" in _st_ids_in(r1b, "backlog.md"))
       and _st_ids_in(r1b, D_BACKLOG_DEFERRED).count("M20") == 2, True)
    ck("vetotext/count: the advice no longer assumes exactly two definitions",
       "one of the two is removed" in t1b, False)
    ck("vetotext/count: ...it names the condition that is true at any count",
       "EXACTLY ONE definition of it remains" in t1b, True)

    # --- N3c. VETO BLIND MUST NOT DENY A CHECK IT PERFORMED. Round 2's HIGH. Once carried ids
    #     are screened, "no cross-file check was performed" is false for a blind entry whose
    #     children carry ids. Two populations, two sentences, and neither may claim the other's
    #     case. `r3` is the id-less parent carrying a CITED M40 -- it is vetoed, so it never
    #     reaches the note; `r3d` below carries an UNCITED id, which does.
    hot3d = ("<!-- ash_save v4 -->\n# plan\n\n"
             "## a finished parent that matches no citemap family\n"
             "kind: plan\ndone: save#10\n\n"
             "### M41 - a nested child nobody cites\nkind: plan\nIts own body.\n")
    plain3d = ("<!-- ash_save v4 -->\n# constraints\n\n"
               "## C-1 - a rule that cites nothing\nkind: rules\nlifetime: permanent\n"
               "It names no entry at all.\n")
    r3d = _st_mk(tmp, "vetotext_blind_partial",
                 {"backlog.md": hot3d, "CONSTRAINTS.md": plain3d}, save=10, baseline=())
    _c3d, t3d = _st_run(["--root", str(r3d)])
    ck("vetotext/blind-partial CONTROL: the parent has no id but carries one, and it MOVES",
       "M41" in _st_ids_in(r3d, D_DONE), True)
    ck("vetotext/blind-partial: it is reported as an INCOMPLETE check, not an absent one",
       "VETO BLIND (PARTIAL)" in t3d, True)
    ck("vetotext/blind-partial: ...and the note does NOT deny that a check happened",
       "NO cross-file check was performed" in t3d, False)
    ck("vetotext/blind-partial: ...it says what WAS screened",
       "WERE screened and would have held" in t3d, True)
    ck("vetotext/blind-partial: ...and names what is actually missing",
       "could not screen its OWN identity" in t3d, True)
    ck("vetotext/blind-none CONTROL: an entry with NO id anywhere still gets the absent-check note",
       "NO cross-file check was performed" in t3b, True)
    ck("vetotext/blind-none: ...and is NOT described as a partial check",
       "VETO BLIND (PARTIAL)" in t3b, False)

    # --- N4. FORMERLY THE PIN; NOW THE FIX. This arm used to assert that an id-bearing
    #     parent MOVED, took its cited child cold, and printed nothing -- recorded as an
    #     accepted hole on the grounds that no folder on this machine contained one. Review
    #     round 1 overturned that and was right: a zero count in one corpus is a property of
    #     the corpus, and the veto's own contract holds an entry when a hot file cites THE
    #     MOVING ID -- which a carried child's id is. The expectations are inverted.
    hot4 = ("<!-- ash_save v4 -->\n# plan\n\n"
            "## M20 - a finished parent WITH an id\n"
            "kind: plan\ndone: save#10\n\n"
            "### M40 - a nested child riding inside the parent's span\n"
            "kind: plan\nIts own body.\n")
    r4 = _st_mk(tmp, "vetotext_carried_idparent",
                {"backlog.md": hot4, "CONSTRAINTS.md": cons}, save=10, baseline=())
    _c4, t4 = _st_run(["--root", str(r4)])
    ck("vetotext/carried-id CONTROL: exit", _c4, EXIT_OK)
    ck("vetotext/carried-id: the parent is HELD even though its OWN id is cited by nobody",
       "M20" in _st_ids_in(r4, "backlog.md"), True)
    ck("vetotext/carried-id: ...so it did NOT reach the archive",
       "M20" in _st_ids_in(r4, D_DONE), False)
    ck("vetotext/carried-id: ...and the cited child stayed hot with it",
       "M40" in _st_ids_in(r4, "backlog.md"), True)
    ck("vetotext/carried-id: the receipt reports it as VETOED rather than silently moving",
       "VETOED" in t4, True)

    # --- N4b. THE CONTROL WITHOUT WHICH N4 MEANS NOTHING. A veto that blocks everything also
    #     satisfies "the parent is held", and over-blocking is the actual failure mode of a
    #     widened screen. Same shape, same parent, same child -- but NOTHING cites the child.
    #     It must still move.
    plain = ("<!-- ash_save v4 -->\n# constraints\n\n"
             "## C-1 - a live rule that cites nothing\nkind: rules\nlifetime: permanent\n"
             "It names no entry at all.\n")
    r4b = _st_mk(tmp, "vetotext_carried_uncited",
                 {"backlog.md": hot4, "CONSTRAINTS.md": plain}, save=10, baseline=())
    _c4b, t4b = _st_run(["--root", str(r4b)])
    ck("vetotext/uncited CONTROL: exit", _c4b, EXIT_OK)
    ck("vetotext/uncited: an UNCITED carried child does not hold its parent -- the move happens",
       "M20" in _st_ids_in(r4b, D_DONE), True)
    ck("vetotext/uncited: ...the child travels with it",
       "M40" in _st_ids_in(r4b, D_DONE), True)
    ck("vetotext/uncited: ...and nothing was vetoed", "VETOED" in t4b, False)

    # --- N5. THE MIXED ARITY, which neither pure fixture reaches. The tail branches on whether
    #     ANY exempt entry lacks an id, so a run holding one of each is the case where a wrong
    #     `%`-argument order or a stray plural actually shows. Found by running the branch, not
    #     by reading it -- the two pure arities both rendered correctly while this was untested.
    e_id = ("## M20 - parked work\nkind: plan\n"
            "gate: EXTERNAL - eventually the pilot reports back, whenever that is\n\nBody one.\n")
    e_noid = ("## a parked heading that matches no citemap family\nkind: plan\n"
              "gate: EXTERNAL - eventually the pilot reports back, whenever that is\n\nBody two.\n")
    mixed = [e_id, e_noid]
    hot5 = "<!-- ash_save v4 -->\n# plan\n\n" + "\n".join(mixed)
    arch5, _off5 = _build_destination(dest_header(D_BACKLOG_DEFERRED),
                                      [e.encode("utf-8") for e in mixed], created=True)
    r5 = _st_mk(tmp, "vetotext_exempt_mixed",
                {"backlog.md": hot5, D_BACKLOG_DEFERRED: arch5.decode("utf-8")},
                save=10, baseline=())
    _c5, t5 = _st_run(["--root", str(r5)])
    _ve5 = [l for l in t5.splitlines() if "VETO EXEMPT" in l]
    ck("vetotext/mixed CONTROL: exit", _c5, EXIT_OK)
    ck("vetotext/mixed CONTROL: both entries reached the exemption in ONE note",
       len(_ve5) == 1 and "VETO EXEMPT on 2" in _ve5[0], True)
    ck("vetotext/mixed CONTROL: the note names both, id-bearing and id-less",
       ("M20" in _ve5[0]) and ("~a" in _ve5[0]), True)
    # EXCLUSIVITY, not mere presence. `"~a carries no id" in line` is ALSO satisfied by a
    # false tail reading `M20, ~a carries no id`, so the arm never proved what its name
    # claims. Named mechanically by review round 1 on the e07b6eb packet.
    _tail5 = _ve5[0].split("never this note.", 1)[-1]
    ck("vetotext/mixed CONTROL: the tail is separable from the deferral sentence",
       _tail5 != _ve5[0], True)
    ck("vetotext/mixed: the id-less one is named as unaddressable",
       "~a carries no id" in _tail5, True)
    ck("vetotext/mixed: ...and the ID-BEARING entry is NOT swept into that subject",
       "M20" in _tail5, False)
    # BRANCH GUARD, not a must-redden arm: this string did not exist before the branch did,
    # so it passes on the unfixed binary by construction. It guards the FUTURE regression --
    # a tail made unconditional -- rather than the fault this round repaired.
    ck("vetotext/mixed BRANCH GUARD: the id-bearing tail must not fire for a mixed pair",
       "keeps resolving from `_archive/`" in _ve5[0], False)


    # --- N5b. THE PLURAL NO-ID BRANCH, which nothing exercised. The arm deleted from N5 claimed to
    #     test agreement and accepted BOTH verb forms, so it could not fail on the fault it named --
    #     and N5's subject is singular anyway, so it was mislabelled as well. All three reviews of
    #     the previous tip named it independently, twice on a mechanical basis. This is the fixture
    #     that makes the claim testable: two id-less entries, so the tail MUST read `carry` and
    #     `them`, and a regression to the singular has somewhere to go red.
    e_noid2 = ("## another parked heading outside every citemap family\nkind: plan\n"
               "gate: EXTERNAL - eventually the pilot reports back, whenever that is\n\nBody three.\n")
    pair = [e_noid, e_noid2]
    hot5b = "<!-- ash_save v4 -->\n# plan\n\n" + "\n".join(pair)
    arch5b, _off5b = _build_destination(dest_header(D_BACKLOG_DEFERRED),
                                        [e.encode("utf-8") for e in pair], created=True)
    r5b = _st_mk(tmp, "vetotext_exempt_bothnoid",
                 {"backlog.md": hot5b, D_BACKLOG_DEFERRED: arch5b.decode("utf-8")},
                 save=10, baseline=())
    _c5b, t5b = _st_run(["--root", str(r5b)])
    _ve5b = [l for l in t5b.splitlines() if "VETO EXEMPT" in l]
    ck("vetotext/plural-noid CONTROL: two id-less entries, one note",
       len(_ve5b) == 1 and "VETO EXEMPT on 2" in _ve5b[0], True)
    _tail5b = _ve5b[0].split("never this note.", 1)[-1]
    ck("vetotext/plural-noid: a two-member subject takes the PLURAL verb",
       "carry no id" in _tail5b, True)
    ck("vetotext/plural-noid: ...and not the singular, which is the regression this catches",
       "carries no id" in _tail5b, False)
    ck("vetotext/plural-noid: ...and the object agrees too",
       "cannot address them" in _tail5b, True)

    # --- N6. THE ALL-IDS PLURAL, the other half of the same branch. Singular "the archived copy
    #     carries the id, so IT keeps resolving" under a two-entry subject was what N5 surfaced.
    both_ids = [e_id, e_id.replace("M20 - parked work", "M21 - more parked work")]
    hot6 = "<!-- ash_save v4 -->\n# plan\n\n" + "\n".join(both_ids)
    arch6, _off6 = _build_destination(dest_header(D_BACKLOG_DEFERRED),
                                      [e.encode("utf-8") for e in both_ids], created=True)
    r6 = _st_mk(tmp, "vetotext_exempt_bothids",
                {"backlog.md": hot6, D_BACKLOG_DEFERRED: arch6.decode("utf-8")},
                save=10, baseline=())
    _c6, t6 = _st_run(["--root", str(r6)])
    _ve6 = [l for l in t6.splitlines() if "VETO EXEMPT" in l]
    ck("vetotext/both-ids CONTROL: two entries, one note",
       len(_ve6) == 1 and "VETO EXEMPT on 2" in _ve6[0], True)
    # AGREEMENT ACROSS THE WHOLE CLAUSE, not just the verb. Round 1's critical named the
    # singular nouns too, and the first repair fixed only `stay`/`stays`.
    ck("vetotext/both-ids: the verb agrees with its plural subject",
       "the entries stay hot beside their archived copies" in _ve6[0], True)
    ck("vetotext/both-ids: ...and so do the NOUNS -- no singular object under a plural subject",
       "BESIDE the archived copy" in _ve6[0] or "beside that copy" in _ve6[0], False)
    ck("vetotext/both-ids: ...and it does not name one pair where there are several",
       "one of the two is removed" in _ve6[0], False)
    ck("vetotext/both-ids: ...while the SINGULAR fixture still reads singular throughout",
       "The archived copy carries the id" in t1
       and "the entry stays hot beside every archived copy" in t1, True)
    ck("vetotext/both-ids BRANCH GUARD: no id-less clause when every entry has an id",
       "cannot address" in _ve6[0], False)

    # ----------------------------------------------------------------- N5. ROUND 3's FINDING.
    # A blocker row used to speak for its whole group through its FIRST `Cite`: one line number
    # and, via a clause that fired only when the cited id DIFFERED from the held entry's own,
    # usually no id at all. So a citing entry naming the entry on two lines printed one of them,
    # and a second line naming a CARRIED CHILD's id printed nothing -- while the WARNING above
    # promised "each with the id it actually cites". A reader who edited the single printed line
    # found the hold recurring next save, which is exactly what C-60 forbids.
    #
    # THE OLD `via` CLAUSE HAD NO ARM AT ALL -- that is why the row format could change here with
    # 954 checks unmoved and nothing red. These arms are the pin it never had.
    n5_backlog = ("<!-- ash_save v4 -->\n# backlog\n\n"
                  "## M1 - the parent, finished and drainable\n"
                  "kind: plan\nlifetime: conditional - until it moves\n"
                  "done: save#9 - finished\n\nParent body.\n\n"
                  "### M2 - a nested child with its OWN id, riding out in the parent's span\n"
                  "kind: plan\nlifetime: conditional - until it moves\n\nChild body.\n")
    n5_head = ("<!-- ash_save v4 -->\n# constraints\n\n"
               "## C-9 - the citing entry\nkind: rules\nlifetime: permanent\n\n")

    def _n5_row(name, citer):
        r = _st_mk(tmp, name, {"backlog.md": n5_backlog, "CONSTRAINTS.md": n5_head + citer},
                   save=10, baseline=())
        _c, t = _st_run(["--root", str(r)])
        rows = [l for l in t.splitlines() if "cited by " in l]
        return t, (rows[0] if rows else "")

    # (a) TWO LINES, TWO IDS. Both must be named; naming one is the defect.
    tA, rowA = _n5_row("vetotext_n5a",
                       "This clause depends on M1 for sequencing.\n"
                       "This other clause depends on M2.\n")
    ck("vetotext/N5a: the entry is held", "VETOED (1)" in tA, True)
    ck("vetotext/N5a: BOTH citing lines are named, not just the first",
       "CONSTRAINTS.md:8,9" in rowA, True)
    ck("vetotext/N5a: BOTH cited ids are named", "cites M1, M2" in rowA, True)
    ck("vetotext/N5a: the carried one is identified AS carried",
       "(M2 nested inside this entry)" in rowA, True)
    ck("vetotext/N5a: the id of the held entry is NOT called nested",
       "(M1" in rowA.split("nested inside")[0].split("cites")[-1]
       and "M1 nested inside" in rowA, False)

    # (b) BOTH IDS ON ONE LINE. One line, one row -- and still both ids. This is the half the
    #     site-only dedup discarded before the receipt could see it.
    tC, rowC = _n5_row("vetotext_n5c",
                       "This clause depends on M1 and on M2 together.\n")
    ck("vetotext/N5b: one citing line is named once", "CONSTRAINTS.md:8 " in rowC, True)
    ck("vetotext/N5b: ...and BOTH ids on it are still named", "cites M1, M2" in rowC, True)

    # (c) CONTROL, and it must go the OTHER way: a citer naming ONLY the entry's own id gets one
    #     line, one id, and NO nested clause. This is what catches over-reporting -- a repair
    #     that names every id everywhere would redden here and nowhere else.
    tD, rowD = _n5_row("vetotext_n5d", "This clause depends on M1 alone.\n")
    ck("vetotext/N5c CONTROL: still held", "VETOED (1)" in tD, True)
    ck("vetotext/N5c CONTROL: exactly one line named", "CONSTRAINTS.md:8 " in rowD, True)
    ck("vetotext/N5c CONTROL: the id IS named -- the old row named none here",
       "cites M1" in rowD, True)
    ck("vetotext/N5c CONTROL: ...and M2 is not claimed", "M2" in rowD, False)
    ck("vetotext/N5c CONTROL: ...and nothing is called nested",
       "nested inside this entry" in rowD, False)
    ck("vetotext/N5c CONTROL: ...and no multiplicity is claimed for one citation",
       "(x" in rowD, False)

    # (d) THE WARNING'S PROMISE AND THE ROW MUST AGREE. The round-3 finding was precisely that
    #     they did not: the promise was universal and the row delivered it in one branch only.
    _w5 = [l for l in tA.splitlines() if "MOVE WAS VETOED" in l]
    ck("vetotext/N5d: the warning describes the ids and lines it names, CAP-AWARE",
       len(_w5) == 1 and "with the ids and lines they cite" in _w5[0]
       and "up to the receipt's display caps" in _w5[0], True)
    ck("vetotext/N5d: ...and claims no universal it cannot keep",
       ("EVERY id" in _w5[0]) or ("EVERY line" in _w5[0]), False)
    ck("vetotext/N5d: ...and it no longer promises the singular it could not deliver",
       any("the id it actually cites" in l for l in tA.splitlines()), False)

    # (e) THE LINE CAP IS DISCLOSED, NEVER SILENT. Same rule as VETO_MAX_BLOCKERS: a truncation
    #     nobody is told about reads as coverage.
    many = "".join("Line %d depends on M1 here.\n" % i for i in range(VETO_MAX_LINES + 3))
    tE, rowE = _n5_row("vetotext_n5e", many)
    ck("vetotext/N5e: the cap bounds the printed lines",
       len(rowE.split(" in ")[0].split(":")[-1].split(",")) <= VETO_MAX_LINES, True)
    ck("vetotext/N5e: ...and the remainder is COUNTED, not dropped in silence",
       "+3 more line(s)" in rowE, True)

    # ----------------------------------------------------------- N6. THE CLOSING RE-REVIEW's HIGH.
    # The repair above replaced a false universal with ANOTHER ONE, in the same commit that built
    # the cap refuting it: the warning promised "EVERY id it cites and EVERY line it cites from"
    # while `VETO_MAX_LINES` prints four lines plus a count and `VETO_MAX_BLOCKERS` sends later
    # groups to `extra`, which prints file-level counts and no ids at all. FOUR FOR FOUR on repairs
    # shipping a fresh universal in their own tail (R-CLAUDE-82). Reproduced 2026-08-31 in ONE
    # receipt: a row reading "+3 more line(s)" three lines above a warning promising every line.
    #
    # THE ATTACK POINT THAT FOUND IT WAS MINE. My own brief said to look for "a group whose lines
    # exceed the cap, a blocker in `extra` rather than `rows`" -- and I did not apply it to the
    # sentence I had just written. The arms below are the application.
    _n6warn = [l for l in tE.splitlines() if "MOVE WAS VETOED" in l]
    ck("vetotext/N6a: the capped run still warns", len(_n6warn) == 1, True)
    ck("vetotext/N6a: the warning no longer promises EVERY line",
       "EVERY line it cites from" in _n6warn[0], False)
    ck("vetotext/N6a: ...nor EVERY id", "EVERY id it cites" in _n6warn[0], False)
    ck("vetotext/N6a: it names the caps instead of denying them",
       "up to the receipt's display caps" in _n6warn[0], True)
    ck("vetotext/N6a: ...and points at the count the row prints",
       "says how many it did not print" in _n6warn[0], True)
    # THE PAIR THAT MAKES IT A CHECK RATHER THAN A RECEIPT: the row and the warning must be
    # consistent IN THE SAME RECEIPT. This is the assertion the old text would have failed.
    ck("vetotext/N6a PAIR: the row truncates AND the warning admits truncation, together",
       ("+3 more line(s)" in rowE) and ("up to the receipt's display caps" in _n6warn[0]), True)
    # CONTROL: an UNCAPPED run must still get a warning, and it must not invent a cap that
    # did not apply -- the wording is cap-AWARE, not cap-ASSERTING.
    _n6ctl = [l for l in tD.splitlines() if "MOVE WAS VETOED" in l]
    ck("vetotext/N6b CONTROL: the uncapped run warns too", len(_n6ctl) == 1, True)
    ck("vetotext/N6b CONTROL: ...with no truncation claimed anywhere in its row",
       "more line(s)" in rowD, False)
    # N6c. THE ASSERTION THE PAIR ABOVE WAS MISSING, and the final cross-family round named it a
    # critical. NEITHER of those two checks reads the warning's own words: one counts warning
    # LINES and the other reads the ROW. So a warning that falsely asserted a cap HAD applied
    # passed both -- and passed the WHOLE suite, because N5d and N6a assert only substrings that
    # such a regression leaves intact.
    #
    # REPRODUCED BEFORE REPAIRING, with both stimuli, because a mutant that never ran renders
    # exactly like a blind arm (R-CLAUDE-47). Splicing "a cap applied on this run" into the
    # warning (1) reached a real receipt on an UNCAPPED folder -- one citing line, no truncation,
    # the claim false on its face -- and (2) left the shipped selftest at 980 checks 0 failed,
    # byte-identical to the unmutated count. R-CLAUDE-66, verbatim: a check that asserts what you
    # just did cannot fail on the fault it guards. That is the SIXTH arm in this round that could
    # not fail on the fault it was named for -- five were already caught and recorded before it,
    # each by a different instrument.
    #
    # THE SENTENCE IS PINNED WHOLE, and the literal below is TYPED rather than read from the
    # source: deriving it would compare the code to itself and could never redden (R-CLAUDE-45).
    # It is pinned on BOTH fixtures, which is what makes a cap-assertion firing only on the
    # CAPPED run reddenable as well. Equality subsumes N5d's and N6a's substring checks; those
    # stay, because they name WHICH clause carries the claim and this one only says the sentence
    # is unchanged.
    _VETO_WARN_TAIL = (
        "the MOVE WAS VETOED: a hot file still cites this entry, or an id nested inside it, "
        "so moving it would leave that reference pointing into cold storage. It stays hot. "
        "The blockers are named under VETOED with the ids and lines they cite, up to the "
        "receipt's display caps -- where a cap applies the row says how many it did not print.")
    ck("vetotext/N6c PIN: the UNCAPPED run's warning is the pinned sentence, exactly",
       _n6ctl[0].endswith(_VETO_WARN_TAIL), True)
    ck("vetotext/N6c PIN: ...and the CAPPED run's warning is that same sentence, unchanged",
       _n6warn[0].endswith(_VETO_WARN_TAIL), True)


def _selftest():
    import tempfile
    import shutil
    checks = []

    def ck(label, got, want):
        checks.append((label, got, want, got == want))

    tmp = tempfile.mkdtemp(prefix="demote_selftest_")
    try:
        _st_arms_rows(ck, tmp)
        _st_arms_carveout(ck, tmp)
        _st_arms_region(ck, tmp)
        _st_arms_markers(ck, tmp)
        _st_arms_status_position(ck, tmp)
        _st_arms_compound_marker(ck, tmp)
        _st_arms_row2b(ck, tmp)
        _st_arms_gate_contract(ck, tmp)
        _st_arms_gate_wedge(ck, tmp)
        _st_arms_wont_do(ck, tmp)
        _st_arms_return_path(ck, tmp)
        _st_arms_question_gate(ck, tmp)
        _st_arms_collision(ck, tmp)
        _st_arms_already(ck, tmp)
        _st_arms_conflict_arity(ck, tmp)
        _st_arms_identless_already(ck, tmp)
        _st_arms_cross_dest(ck, tmp)
        _st_arms_carried_children(ck, tmp)
        _st_arms_m9_positions(ck, tmp)
        _st_arms_sequence(ck, tmp)
        _st_arms_sequence_units(ck, tmp)
        _st_arms_bom_and_eol(ck, tmp)
        _st_arms_diagnostics(ck, tmp)
        _st_arms_rg(ck, tmp)
        _st_arms_rh(ck, tmp)
        _st_arms_idempotent(ck, tmp)
        _st_arms_integrity(ck, tmp)
        _st_arms_dryrun_usage(ck, tmp)
        _st_arms_acceptance(ck, tmp)
        _st_arms_contract(ck, tmp)
        _st_arms_veto(ck, tmp)
        _st_arms_veto_notes(ck, tmp)
        ck("no invocation returned EXIT_SELFTEST (1)", EXIT_SELFTEST in _ST_CODES, False)
        ck("no invocation returned EXIT_INTERNAL (7)", EXIT_INTERNAL in _ST_CODES, False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [c for c in checks if not c[3]]
    for label, got, want, ok in checks:
        if not ok:
            sys.stderr.write("FAIL %s: got %r want %r\n" % (label, got, want))
    sys.stderr.write("%d checks, %d failed\n" % (len(checks), len(bad)))
    return EXIT_SELFTEST if bad else EXIT_OK


# --------------------------------------------------------------------------- cli

def build_parser():
    import argparse
    p = argparse.ArgumentParser(
        prog="demote.py",
        description="save step 6a: the deterministic, unskippable drain of the hot set. "
                    "Moves whole entries -- ids intact -- into named files under _archive/.")
    p.add_argument("--root", help="the project folder. REQUIRED: the scope is DECLARED, never "
                                  "inferred from cwd (D53).")
    p.add_argument("--save", type=int, default=None,
                   help="the current save number. Defaults to state.md's `save:` line. Row 1 "
                        "NO-OPs without one.")
    p.add_argument("--rewrote", action="append", default=[], metavar="ID",
                   help="an id the caller rewrote this save; exempt from row 2b. Repeatable.")
    p.add_argument("--rewrote-complete", action="store_true",
                   help="assert that --rewrote names EVERY backlog.md id rewritten this save. Only "
                        "then may row 2b evict a PRESENT item. Without it row 2b is report-only: "
                        "an ids-only baseline cannot tell a rewritten item from an untouched one, "
                        "and guessing evicted live work at exit 0.")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would move and write NOTHING.")
    p.add_argument("--selftest", action="store_true", help="run the in-file fixtures and exit.")
    return p


def main(argv=None, _out=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except (AttributeError, ValueError):
        pass
    out = _out if _out is not None else recall._PipeSafe(sys.stdout)
    args = build_parser().parse_args(argv)

    if args.selftest:
        return _selftest()

    if not args.root:
        sys.stderr.write("error: --root is required: the scope is DECLARED, never inferred "
                         "from cwd (D53).\n")
        return EXIT_USAGE
    root = pathlib.Path(args.root)
    if not root.is_dir():
        sys.stderr.write("error: --root is not a directory: %s\n" % args.root)
        return EXIT_USAGE
    root = root.resolve()

    # THE MIGRATION GATE. It runs here, at the entry, before the scan. `--dry-run` does NOT exempt
    # a folder from THE GATE: `--dry-run` against a folder that does not declare v4 is refused
    # with exit 4 and prints nothing on stdout, exactly like a real run, because a plan against a
    # folder that may not be drained is still an answer a caller acts on. What `--dry-run`
    # suppresses is the WRITES, and only on a folder THE GATE has already admitted.
    gate = update_state.require_migrated(root, "demote.py")
    if gate is not None:
        return gate

    try:
        return run(root, args.save, set(args.rewrote), args.rewrote_complete, args.dry_run, out)
    except (ValueError, IOError, OSError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return EXIT_USAGE


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
