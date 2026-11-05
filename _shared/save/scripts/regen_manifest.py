#!/usr/bin/env python
"""save v4 -- W8. Regenerate `manifest.md`: the ROUTER that makes the archive addressable.

The manifest answers ONE question: given this root, where does a thing LIVE and what can drain
it. It is not an inventory. It is rewritten whole every save and is exempt from the declaration
rules (spec S3.5) because nothing authors its entries.

CONTRACT: `--root` is REQUIRED and is NEVER inferred from cwd (D53). The id families are shared,
    not per-project -- `D17` and `C-6` are each defined under their own heading in many unrelated
    project folders -- so an inferred scope resolves confidently over the wrong project and
    writes a router describing a folder the caller never named.

CONTRACT: ONE ROW PER CLASS, never one row per file. A class is a `(kind, lifetime)` pair -- it
    is what decides how a file is READ and which step-6a row can drain it, and it is the only
    thing a router needs to say. A per-file router spends a row on every checkpoint, so its own
    cost grows with the archive it exists to keep OUT of context, and it grows again at every
    save. spec S3.5 names the per-file shape as the generator-bug shape an over-triggered
    manifest collapses back from.

INVARIANT: the tables enumerate WHAT IS ON DISK, never the declared destination list. MEASURED
    2026-08-03: a folder freshly scaffolded by `init_project.py` holds 2 of the 8 declared
    archive destinations -- the six one-way files are created by the move engine on first use --
    so a router printed from the list names six files that do not exist, and a file on disk that
    the list does not know gets dropped silently. Unknown gets its OWN row, named.

INVARIANT: a file v4 retired never enters a hot class row. A folder that has not migrated still
    holds retired names on disk, and its v3 manifest routes them as Tier-1/Tier-2 -- i.e. as live
    state. They go in a clearly labelled UNCLASSIFIED section instead, so nothing is dropped and
    nothing is loaded by mistake.

INVARIANT: BOTH hand-built checkpoint drain nestings are seen -- `checkpoints/_archive/` and
    `_archive/checkpoints/`. MEASURED 2026-08-03: two projects invented incompatible drains
    (broker chose the first, subagents the second) and both exist on disk today. The router
    describes the layout it FINDS; a root with neither produces no drain row at all.

INVARIANT: `manifest.md` is counted PRESENT BY CONSTRUCTION -- this tool is writing it in the
    same run. MEASURED 2026-08-03: `scan_root()` runs before `write_manifest()`, so on any root
    where the file was not already on disk the generated router declared ITSELF `(absent)` and
    understated `hot_present` by one -- false at the instant the bytes landed, and run 1 differed
    from run 2 (hot_present 5 -> 6 and 8 -> 9; bytes 3992 -> 3983). A router that is wrong about
    its own existence cannot be idempotent.

INVARIANT: the write temp NEVER survives this process, and can never be routed even when it does.
    MEASURED 2026-08-03: with `manifest.md` read-only, `os.replace` raises WinError 5, this tool
    exits 2, and `manifest.md.regen-tmp` was LEFT in the hot folder -- where the next successful
    run enumerated it as an unrouted non-markdown top-level file. Two halves, because one is not
    enough: a `finally` removes it on every failure path this process can see, and `*.regen-tmp`
    is in the exclusion set so a leak from a KILLED process -- which no `finally` reaches -- is
    still not routed.

INVARIANT: line endings survive the rewrite. Python text mode is a NORMALISER: on Windows it
    inflates an LF file to CRLF one byte per line, `git status` reports clean throughout, and
    only the size moves. This tool reads bytes, renders with `\\n`, and re-terminates with the
    terminator the previous `manifest.md` actually used.

EXTERNAL: the out-of-band curator's postcondition is a NON-RECURSIVE listdir of `checkpoints/`
    (`curator_entry.py`, verified 2026-08-03). So the checkpoint count printed here is
    non-recursive too, and a drain directory's contents are counted separately and never folded
    into it -- a router that reported the recursive count would disagree with the gate.

EXTERNAL: `recall frontier [scope]` is NAMED here on purpose. D60 withdrew `frontier.md` and
    replaced the file with a derivation, so the pointer must stay resident even though the
    content is not; a router that omits it leaves the derivation undiscoverable.

CONTRACT: this tool REFUSES a folder that is not already a migrated v4 project (exit 4), at
    entry, before it reads anything it would write back. The shared v4 declaration check
    cannot be bypassed. Fresh projects are scaffolded by init_project.py, not converted here.

exit: 0 written | 1 --selftest failure ONLY | 2 usage or I/O | 4 not a migrated v4 folder,
nothing written | 6 scan integrity (an open fence in a file read back) | 7 internal error.
"""

import argparse
import os
import pathlib
import sys
import time

# --- sibling import. `-I` implies `-P`, so sys.path[0] is NOT the script's directory. -----------
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    import citemap
    import recall
    import update_state
except ImportError as _exc:                                     # pragma: no cover - fatal
    sys.stderr.write("error: cannot import sibling from %s: %s\n"
                     % (pathlib.Path(__file__).resolve().parent, _exc))
    sys.exit(2)

EXIT_OK             = 0
EXIT_SELFTEST       = 1
EXIT_USAGE          = 2
EXIT_ABORT          = 3
EXIT_SCAN_INTEGRITY = 6
EXIT_INTERNAL       = 7

# --root is not a migrated v4 project folder; NOTHING was written. Aliased from update_state
# rather than restated, so the three v4 writers cannot drift to different numbers for the same
# refusal. `require_migrated` and its message come from there too, for the same reason.
EXIT_NOT_MIGRATED = update_state.EXIT_NOT_MIGRATED

SCHEMA_VERSION = "v4"
MANIFEST_NAME = "manifest.md"
STATE_NAME = "state.md"
ARCHIVE_DIR = "_archive"
CHECKPOINTS_DIR = "checkpoints"

BOM_BYTES = citemap.BOM.encode("utf-8")

# The v4 hot set, spec S3.1. FOUR classes, ten files. `(kind, lifetime, members, drains_on)`.
HOT_CLASSES = (
    ("rules", "permanent",
     ("AGENTS.md", "CLAUDE.md", "CONSTRAINTS.md", "manifest.md"),
     "nothing - no 6a row reads them"),
    ("spec", "conditional",
     ("architecture.md", "design.md"),
     "`superseded by <id>` (row 4, one-way)"),
    ("plan", "conditional",
     ("backlog.md", "open_questions.md", "state.md"),
     "rows 2, 2b, 5 (backlog.md); rows 3, 5 (open_questions.md); nothing (state.md)"),
    ("rules", "expiring",
     ("active.md",),
     "`expires <= current save` (row 1, one-way)"),
)
HOT_FILES = frozenset(n for _k, _l, members, _d in HOT_CLASSES for n in members)

# Named in v3, NOT hot in v4. On disk only in an unmigrated folder (W18 migrates).
RETIRED_IN_V4 = (
    "restart-prompt.md", "index.md", "decisions-active.md", "roadmap.md", "frontier.md",
    "decisions-digest.md", "research.md", "misc.md", "README.md", "decisions.md",
)
RETIRED_SET = frozenset(RETIRED_IN_V4)

# step-6a destinations under `_archive/`. Declared, NOT assumed present -- see INVARIANT above.
ARCHIVE_ONE_WAY = (
    "active-expired.md", "backlog-done.md", "backlog-dropped.md", "resolved-oqs.md",
    "architecture-superseded.md", "design-superseded.md",
)
ARCHIVE_REVERSIBLE = ("backlog-deferred.md", "open-questions-deferred.md")
ARCHIVE_DECLARED = frozenset(ARCHIVE_ONE_WAY) | frozenset(ARCHIVE_REVERSIBLE)

# The two hand-built checkpoint drains. `(label, relative path parts)`.
DRAIN_NESTINGS = (
    ("checkpoints-nested", (CHECKPOINTS_DIR, ARCHIVE_DIR)),
    ("archive-nested", (ARCHIVE_DIR, CHECKPOINTS_DIR)),
)

EXCLUDED_DIRS = frozenset((
    ".git", ".venv", "__pycache__", "_smoke", "dist", "node_modules", "verification",
    "fiddler_extracts", "mitm_captures",
))
EXCLUDED_DIR_PREFIX = "_run_"
EXCLUDED_EXTS = (".har", ".saz", ".tgz", ".zip")

# This tool's own write temp. Excluded EVERYWHERE, not merely deleted on the failure paths this
# process can see: a killed process leaves one behind and reaches no `finally` at all.
TMP_SUFFIX = ".regen-tmp"

NO_ARCHIVE_FILES = "(none present)"
NO_ARCHIVE_DIR = "(no `_archive/` directory at this root)"


# --------------------------------------------------------------------------- small helpers ----

def _safe_inline(text, limit=120):
    """One line, no comment-terminator, no control characters. The header is an HTML comment."""
    flat = " ".join(str(text).split())
    flat = flat.replace("-->", "--&gt;").replace("|", "/")
    if len(flat) > limit:
        flat = flat[:limit - 3] + "..."
    return flat


def detect_newline(data):
    """The terminator a file ACTUALLY uses, or None when it has no line terminator at all."""
    if not data:
        return None
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    cr = data.count(b"\r") - crlf
    if crlf == 0 and lf == 0 and cr == 0:
        return None
    if crlf >= lf and crlf >= cr:
        return "\r\n"
    if lf >= cr:
        return "\n"
    return "\r"


def _read_bytes_or_none(path):
    try:
        return path.read_bytes()
    except (OSError, ValueError):
        return None


def choose_newline(root):
    """Preserve, never impose. Previous manifest, else state.md, else LF.

    A whole-file rewrite is exactly where the silent flip happens; see the INVARIANT above.
    """
    for name in (MANIFEST_NAME, STATE_NAME):
        data = _read_bytes_or_none(root / name)
        if data is None:
            continue
        found = detect_newline(data)
        if found is not None:
            return found, name
    return "\n", "(default)"


def had_bom(root):
    data = _read_bytes_or_none(root / MANIFEST_NAME)
    return bool(data and data.startswith(BOM_BYTES))


def excluded_dir(name):
    return (name.startswith(".") or name in EXCLUDED_DIRS
            or name.startswith(EXCLUDED_DIR_PREFIX))


def excluded_file(name):
    lowered = name.lower()
    return (name.startswith(".") or lowered.endswith(EXCLUDED_EXTS)
            or lowered.endswith(TMP_SUFFIX))


def list_dir(path):
    """Non-recursive. Returns `(files, dirs)`, sorted, exclusions applied.

    Non-recursive is not an optimisation: the curator's postcondition is a non-recursive listdir
    and a router that disagreed with the gate would be worse than no router.
    """
    files, dirs = [], []
    try:
        entries = list(os.scandir(str(path)))
    except (OSError, ValueError):
        return [], []
    for entry in entries:
        try:
            is_dir = entry.is_dir()
        except OSError:                                         # pragma: no cover - race
            continue
        if is_dir:
            if not excluded_dir(entry.name):
                dirs.append(entry.name)
        else:
            if not excluded_file(entry.name):
                files.append(entry.name)
    return sorted(files), sorted(dirs)


def _nonempty_md(path):
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:                                             # pragma: no cover - race
        return False


# ------------------------------------------------------------------------------ state read ----

def read_state(root):
    """`(project, save, schema, unreliable, warnings)` from `state.md`, fence-aware.

    R-K: the curator's parser takes the first line starting `save:` at column 0 and `int()`s the
    remainder. This reader mirrors that, but skips fenced lines and SAYS SO when it had to --
    a `save:` the curator would read and this tool would not is a torn save waiting to happen.
    """
    warnings = []
    path = root / STATE_NAME
    if not path.is_file():
        warnings.append("warning: no %s under --root; project name falls back to the folder "
                        "basename and save# to --save-num (or 0)." % STATE_NAME)
        return None, None, None, False, warnings
    try:
        _data, rows, open_fence = recall.load_doc(path)
    except (OSError, ValueError) as exc:
        warnings.append("warning: cannot read %s: %s" % (STATE_NAME, exc))
        return None, None, None, False, warnings

    unreliable = recall.warn_open_fence(STATE_NAME, open_fence)

    def _pick(prefix):
        hits = [(lineno, text, in_fence) for lineno, text, _bs, _be, in_fence in rows
                if text.startswith(prefix)]
        live = [h for h in hits if not h[2]]
        return live, hits

    live_save, all_save = _pick("save:")
    if len(all_save) > 1:
        warnings.append("warning: %s holds %d lines starting `save:` (R-K allows exactly one; "
                        "one unparsable match kills the curator's whole scan)."
                        % (STATE_NAME, len(all_save)))
    save = None
    if live_save:
        raw = live_save[0][1].split(":", 1)[1].strip()
        try:
            save = int(raw)
        except ValueError:
            warnings.append("warning: %s line %d has an unparsable `save:` value %r; the "
                            "out-of-band curator reports the save TORN on this (R-K)."
                            % (STATE_NAME, live_save[0][0], raw))
    elif all_save:
        warnings.append("warning: the only `save:` line in %s is inside a code fence; this tool "
                        "skips it and the curator's naive parser does not." % STATE_NAME)

    live_project, _all_project = _pick("project:")
    project = live_project[0][1].split(":", 1)[1].strip() if live_project else None

    live_schema, _all_schema = _pick("schema_version:")
    schema = live_schema[0][1].split(":", 1)[1].strip() if live_schema else None
    if schema is not None and schema != SCHEMA_VERSION:
        warnings.append("warning: %s declares schema_version %r, not %r -- this root is not a "
                        "migrated v4 folder. The router describes what it FINDS; retired files "
                        "land in the UNCLASSIFIED section (W18 migrates)."
                        % (STATE_NAME, schema, SCHEMA_VERSION))
    return project, save, schema, unreliable, warnings


# ---------------------------------------------------------------------------------- the scan --

def scan_root(root):
    """Enumerate DISK. Every table below is rendered from this dict and from nothing else."""
    top_files, _top_dirs = list_dir(root)
    top_set = set(top_files)

    hot = []
    hot_present = 0
    for kind, lifetime, members, drains in HOT_CLASSES:
        marked = []
        for name in members:
            # `manifest.md` is PRESENT BY CONSTRUCTION -- this run is writing it, and this scan
            # runs BEFORE the write. Asking disk here made the router declare ITSELF `(absent)`
            # on every root where the file did not already exist: false the instant the bytes
            # landed, and run 1 then differed from run 2. See the INVARIANT in the module
            # docstring for the measurement. Never mark it absent.
            present = (name == MANIFEST_NAME) or (name in top_set)
            hot_present += 1 if present else 0
            marked.append((name, present))
        hot.append((kind, lifetime, marked, drains))

    retired = [n for n in top_files if n in RETIRED_SET]
    unrecognised = [n for n in top_files
                    if n.lower().endswith(".md") and n not in HOT_FILES and n not in RETIRED_SET]
    nonmd_root = [n for n in top_files if not n.lower().endswith(".md")]

    arch_path = root / ARCHIVE_DIR
    arch_dir = arch_path.is_dir()
    arch_files, arch_subdirs = list_dir(arch_path) if arch_dir else ([], [])
    arch_one_way = [n for n in arch_files if n in ARCHIVE_ONE_WAY]
    arch_reversible = [n for n in arch_files if n in ARCHIVE_REVERSIBLE]
    arch_unknown = [n for n in arch_files if n not in ARCHIVE_DECLARED]
    arch_md = [n for n in arch_files if n.lower().endswith(".md")]

    cp_path = root / CHECKPOINTS_DIR
    cp_dir = cp_path.is_dir()
    cp_files = update_state.list_checkpoints(root)
    cp_latest = cp_files[-1] if cp_files else None

    drains = []
    for label, parts in DRAIN_NESTINGS:
        target = root.joinpath(*parts)
        if not target.is_dir():
            continue
        names, _sub = list_dir(target)
        held = [n for n in names if n.lower().endswith(".md")]
        drains.append((label, "/".join(parts) + "/", len(held)))

    return {
        "hot": hot,
        "hot_present": hot_present,
        "hot_total": len(HOT_FILES),
        "retired": retired,
        "unrecognised": unrecognised,
        "nonmd_root": nonmd_root,
        "archive_dir": arch_dir,
        "arch_one_way": arch_one_way,
        "arch_reversible": arch_reversible,
        "arch_unknown": arch_unknown,
        "arch_md": arch_md,
        "arch_subdirs": [d for d in arch_subdirs if d != CHECKPOINTS_DIR],
        "cp_dir": cp_dir,
        "cp_count": len(cp_files),
        "cp_latest": cp_latest,
        "drains": drains,
    }


# ------------------------------------------------------------------------------- the render ---

def _row(cells):
    return "| " + " | ".join(cells) + " |"


def _join(names):
    return ", ".join(names) if names else NO_ARCHIVE_FILES


def render(scan, project, save, ts):
    """The router, as a list of lines with NO terminators. One row per class, everywhere."""
    lines = []
    add = lines.append

    add("<!-- ash_save %s | save#%d | %s | written by regen_manifest.py -->"
        % (SCHEMA_VERSION, save, _safe_inline(ts, 60)))
    add("# %s - manifest" % _safe_inline(project, 80))
    add("")
    add("GENERATED ROUTER. Rewritten by save every save; do not hand-edit. Exempt from the")
    add("declaration rules (spec S3.5) because nothing authors its entries.")
    add("")

    # -- hot set -------------------------------------------------------------------------------
    add("## Hot set (spec S3.1)")
    add("")
    add("ONE ROW PER CLASS, never per file (spec S3.1, and S3.5 names per-file rows as the "
        "generator-bug")
    add("shape an over-triggered manifest collapses back from). A class is a (kind, lifetime) "
        "pair; it is")
    add("what decides how a file is READ and which step-6a row can drain it, and it is the only "
        "thing a")
    add("router needs to say.")
    add("")
    add(_row(["class", "files", "drains on"]))
    add("|---|---|---|")
    for kind, lifetime, marked, drains in scan["hot"]:
        cell = ", ".join(n if present else "%s (absent)" % n for n, present in marked)
        add(_row(["`%s` / `%s`" % (kind, lifetime), cell, drains]))
    add("")
    add("`(absent)` marks a member this class DECLARES that is not on disk at this root; the row "
        "stays,")
    add("because the router's job is to name the destination whether or not it is occupied yet. "
        "%d of %d"
        % (scan["hot_present"], scan["hot_total"]))
    add("hot files present.")
    add("")
    add("`state.md` and `manifest.md` are GENERATED and exempt from the declaration rules "
        "(spec S3.5);")
    add("they carry the `plan` and `rules` classes for routing only, and no 6a row reads either.")
    add("")

    # -- unclassified --------------------------------------------------------------------------
    add("## Unclassified (on disk, NOT hot in v4)")
    add("")
    if not scan["retired"] and not scan["unrecognised"]:
        add("None - every top-level markdown file at this root is a declared v4 hot file.")
    else:
        add("These files are on disk and belong to NO hot class. They are named here rather than "
            "dropped")
        add("silently, and they are deliberately NOT in the hot-set table above: nothing loads "
            "them on a")
        add("restore and no step-6a row reads them.")
        add("")
        add(_row(["class", "files", "note"]))
        add("|---|---|---|")
        if scan["retired"]:
            # NO PROJECT-SCOPED IDENTIFIER IN EMITTED PROSE -- the third instance in this
            # one file, after `C-4` and `D60` above. `W18` is a work item in THIS
            # generator's own backlog, and this cell is written into the router of every
            # project the fleet scaffolds, so it named our plan inside their namespace.
            # The reader of a router needs to know the files are relocated by the
            # migration; they do not need, and cannot resolve, the number we track it under.
            add(_row(["`UNCLASSIFIED` / `retired-in-v4`", ", ".join(scan["retired"]),
                      "v4 retired these; an unmigrated v3 folder still carries them "
                      "until the migration relocates them"]))
        if scan["unrecognised"]:
            add(_row(["`UNCLASSIFIED` / `unrecognised`", ", ".join(scan["unrecognised"]),
                      "not a v4 hot file and not a name v4 retired; routed nowhere"]))
    if scan["nonmd_root"]:
        add("")
        add("%d non-markdown file(s) at the top level are not routed by this manifest: %s"
            % (len(scan["nonmd_root"]), ", ".join(scan["nonmd_root"])))
    add("")

    # -- archive -------------------------------------------------------------------------------
    add("## Archive (spec S3.2 and step 6a destinations)")
    add("")
    add(_row(["class", "direction", "files"]))
    add("|---|---|---|")
    if not scan["archive_dir"]:
        add(_row(["one-way archive", "nothing returns; the id resolves from here forever",
                  NO_ARCHIVE_DIR]))
        add(_row(["reversible archive",
                  "promoted back with the SAME id when the `gate:` is satisfied",
                  NO_ARCHIVE_DIR]))
    else:
        add(_row(["one-way archive", "nothing returns; the id resolves from here forever",
                  "`_archive/`: " + _join(scan["arch_one_way"])]))
        add(_row(["reversible archive",
                  "promoted back with the SAME id when the `gate:` is satisfied",
                  "`_archive/`: " + _join(scan["arch_reversible"])]))
        if scan["arch_unknown"]:
            add(_row(["unclassified archive",
                      "unknown to v4; named here rather than dropped silently",
                      "`_archive/`: " + ", ".join(scan["arch_unknown"])]))
    add("")
    add("Rows enumerate what is ON DISK directly under `_archive/`, not the declared destination "
        "list:")
    add("the six one-way files are created by the move engine on FIRST USE, so a fresh folder "
        "carries")
    add("only the two reversible ones. %d of %d declared destinations present, %d unclassified."
        % (len(scan["arch_one_way"]) + len(scan["arch_reversible"]), len(ARCHIVE_DECLARED),
           len(scan["arch_unknown"])))
    if scan["arch_subdirs"]:
        add("")
        add("Nested directories under `_archive/` (not routed as entry files): %s"
            % ", ".join("`%s/`" % d for d in scan["arch_subdirs"]))
    add("")
    # `(C-4)` REMOVED HERE FOR THE SAME REASON AS THE D-NUMBER BELOW. `C-4` is a constraint id in
    # THIS generator's own CONSTRAINTS.md; every other project numbers its constraints differently,
    # so emitting it routed readers to whatever that project calls C-4. Same false-green hazard,
    # same one-word fix: state the property, not the citation.
    add("Every demote is a MOVE that KEEPS THE ENTRY'S ID: eviction degrades to a "
        "resolvable stub,")
    add("never a deletion. `recall get <id>` resolves an evicted entry from its archive file.")
    add("")

    # -- retrieval -----------------------------------------------------------------------------
    add("## Retrieval")
    add("")
    # NO PROJECT-SCOPED IDENTIFIER IN EMITTED PROSE, AND THIS LINE IS WHY THE RULE EXISTS.
    # It used to read "D60 withdrew `frontier.md`". D60 is a decision in THIS generator's OWN ledger,
    # and this generator writes a router into every project on the fleet -- so it emitted its author's
    # decision number into other people's identifier namespace, where it resolves to whatever that
    # project happens to call D60, or to nothing.
    # THE DANGLE IS THE LUCKY OUTCOME. `example-project` caught this because its own D60 ("bootstrap
    # runs the Claude Code native installer in a CHILD process") sits OUTSIDE its owned set, so the
    # citation dangled loudly and its verify went red. A project whose D60 is INSIDE its owned set
    # goes GREEN with the citation pointing at the wrong record -- a FALSE GREEN in the identifier
    # check, with no dangle and no warning.
    # A router cannot soundly cite a project-scoped id: the generator cannot know whose ledger it is
    # writing into. The sentence says everything a router's reader needs without one.
    add("`recall frontier [scope]` - the frontier is DERIVED on demand and is NOT a stored")
    add("file. `frontier.md` was withdrawn and replaced by this derivation, so the")
    add("POINTER stays resident in the router even though the content does not.")
    add("")
    add("`recall get <id>` resolves ANY id from this root, hot or archived. Prefer it to "
        "reading")
    add("an archive file whole - that is the cost this router exists to keep out of the window.")
    add("")

    # -- checkpoints ---------------------------------------------------------------------------
    add("## Checkpoints")
    add("")
    add("`checkpoints/` is a TOP-LEVEL directory, a sibling of `_archive/` and never a child of")
    add("it. The out-of-band curator's postcondition is a non-recursive listdir of it, so the")
    add("count below is non-recursive and never folds in a drain directory's contents.")
    add("")
    if not scan["cp_dir"]:
        add("No `checkpoints/` directory at this root.")
    else:
        add("%d non-empty `checkpoint-*.md` at the top level." % scan["cp_count"])
        if scan["cp_latest"]:
            add("Latest: `%s/%s`" % (CHECKPOINTS_DIR, scan["cp_latest"]))
    add("")
    if not scan["drains"]:
        add("No checkpoint drain directory is present under this root.")
    else:
        add("Checkpoint drain(s) FOUND on disk. Two projects invented incompatible nestings and")
        add("both exist in the wild; this router describes the layout it finds, not a preferred")
        add("one:")
        add("")
        add(_row(["drain", "path", "files"]))
        add("|---|---|---|")
        for label, rel, count in scan["drains"]:
            add(_row([label, "`%s`" % rel, str(count)]))
    add("")

    # -- footer --------------------------------------------------------------------------------
    add("---")
    add("")
    add("Not routed here: dot-prefixed names, `.git`, `.venv`, `__pycache__`, `_smoke`, `dist`,")
    add("`node_modules`, `verification`, `fiddler_extracts`, `mitm_captures`, `_run_*`, and")
    add("`.har` / `.saz` / `.tgz` / `.zip`. `_archive/` is NOT excluded - routing to it is the "
        "whole job.")
    return lines


def write_manifest(path, lines, newline, bom):
    """Bytes out, with the terminator the previous file used. Atomic replace.

    The `finally` is half of a two-half invariant, not belt-and-braces. MEASURED 2026-08-03: a
    read-only target makes `os.replace` raise WinError 5, and the temp was LEFT in the hot
    folder for the next run to route as an unrouted top-level non-markdown file. This half
    covers every failure path the process survives; the `TMP_SUFFIX` exclusion covers the one it
    does not (a kill reaches no `finally`).
    """
    body = newline.join(lines) + newline
    data = body.encode("utf-8")
    if bom:
        data = BOM_BYTES + data
    tmp = path.with_name(path.name + TMP_SUFFIX)
    try:
        with open(str(tmp), "wb") as fh:
            fh.write(data)
        os.replace(str(tmp), str(path))
    finally:
        # A successful replace already consumed it; this only fires on a failure path.
        try:
            if tmp.exists():
                os.unlink(str(tmp))
        except OSError:                                         # pragma: no cover - race
            pass
    return data


# ------------------------------------------------------------------------------------ driver --

def regen(root, save_num, ts, out):
    """The whole job. Returns `(exit_code, data_bytes)`."""
    project, state_save, _schema, unreliable, warnings = read_state(root)
    for text in warnings:
        sys.stderr.write(text + "\n")

    if project is None or not project:
        project = root.name
    if save_num is None:
        save_num = state_save if state_save is not None else 0

    scan = scan_root(root)
    newline, newline_src = choose_newline(root)
    bom = had_bom(root)
    lines = render(scan, project, save_num, ts)
    data = write_manifest(root / MANIFEST_NAME, lines, newline, bom)

    out.write("--- BEGIN REGEN SUMMARY ---\n")
    out.write("root: %s\n" % root.as_posix())
    out.write("project: %s\n" % project)
    out.write("save: %d\n" % save_num)
    out.write("newline: %s (from %s)\n"
              % ({"\r\n": "crlf", "\n": "lf", "\r": "cr"}.get(newline, "?"), newline_src))
    out.write("bom: %s\n" % ("yes" if bom else "no"))
    out.write("hot_class_rows: %d\n" % len(scan["hot"]))
    out.write("hot_present: %d\n" % scan["hot_present"])
    out.write("hot_absent: %d\n" % (scan["hot_total"] - scan["hot_present"]))
    out.write("unclassified_retired: %d\n" % len(scan["retired"]))
    out.write("unclassified_unrecognised: %d\n" % len(scan["unrecognised"]))
    out.write("archive_dir: %s\n" % ("yes" if scan["archive_dir"] else "no"))
    out.write("archive_oneway: %d\n" % len(scan["arch_one_way"]))
    out.write("archive_reversible: %d\n" % len(scan["arch_reversible"]))
    out.write("archive_unclassified: %d\n" % len(scan["arch_unknown"]))
    out.write("archive_md_total: %d\n" % len(scan["arch_md"]))
    out.write("checkpoints_top: %d\n" % scan["cp_count"])
    out.write("drain_rows: %d\n" % len(scan["drains"]))
    for _label, rel, count in scan["drains"]:
        out.write("drain: %s %d\n" % (rel, count))
    out.write("lines_written: %d\n" % len(lines))
    out.write("bytes_written: %d\n" % len(data))
    out.write("--- END REGEN SUMMARY ---\n")

    return recall.integrity_code(unreliable, EXIT_OK), data


def build_parser():
    parser = argparse.ArgumentParser(
        prog="regen_manifest.py",
        description="save v4 W8. Regenerate manifest.md: the router that makes the archive "
                    "addressable. ONE ROW PER CLASS, never per file.")
    parser.add_argument("--root", metavar="DIR", default=None,
                        help="REQUIRED. The v4 project folder. The scope is DECLARED, never "
                             "inferred from cwd (D53).")
    parser.add_argument("--save-num", metavar="N", type=int, default=None,
                        help="the save number for the header. Defaults to state.md's `save:` "
                             "line, then 0.")
    parser.add_argument("--checkpoint-ts", metavar="TS", default=None,
                        help="the timestamp for the header. Defaults to now. Named to match "
                             "update_state.py.")
    parser.add_argument("--selftest", action="store_true",
                        help="run the in-file fixture suite and exit. THE ONLY invocation that "
                             "may return 1. Fixtures live in a tempdir removed on the way out.")
    return parser


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

    # The migration gate, before the read that this tool writes back from. Against the previous
    # cut, a run on a real v3 folder replaced that folder's v3 manifest.md with a much smaller v4
    # router at exit 0, with no backup, while the v3 tool doing the same job writes one. The
    # warning that fired was the only thing standing between a v3 router and a v4 rewrite of it.
    gate = update_state.require_migrated(root, "regen_manifest.py")
    if gate is not None:
        return gate

    if args.save_num is not None and args.save_num < 0:
        sys.stderr.write("error: --save-num must be >= 0: %r\n" % args.save_num)
        return EXIT_USAGE
    ts = args.checkpoint_ts
    if ts is None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    elif "\n" in ts or "\r" in ts or "-->" in ts:
        sys.stderr.write("error: --checkpoint-ts may not contain a newline or `-->`: the header "
                         "is an HTML comment.\n")
        return EXIT_USAGE

    try:
        code, _data = regen(root, args.save_num, ts, out)
    except (ValueError, IOError, OSError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return EXIT_USAGE
    return code


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


# ------------------------------------------------------------------------------- selftest -----

_STATE_TPL = (
    "<!-- ash_save v4 | save#%(save)d | fixture | written by update_state.py -->\n"
    "# %(project)s - state\n"
    "\n"
    "schema_version: %(schema)s\n"
    "project: %(project)s\n"
    "phase: implementation\n"
    "save: %(save)d\n"
    "status: active\n"
)


def _selftest():
    import shutil
    import tempfile
    import io

    checks = []

    def ck(label, got, want):
        checks.append((label, got, want, got == want))

    def wr(path, text, newline="\n"):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "wb") as fh:
            fh.write(text.replace("\n", newline).encode("utf-8"))

    def scaffold(base, name, hot=HOT_FILES, archive=ARCHIVE_REVERSIBLE, newline="\n",
                 schema="v4", save=3, checkpoints=0):
        """A v4-shaped folder: the ten hot files, `_archive/` with the two reversible ones."""
        root = base / name
        root.mkdir(parents=True, exist_ok=True)
        for fn in sorted(hot):
            if fn == STATE_NAME:
                continue
            wr(root / fn, "# %s\n\nfixture\n" % fn, newline)
        if STATE_NAME in hot:
            wr(root / STATE_NAME,
               _STATE_TPL % {"project": name, "save": save, "schema": schema}, newline)
        (root / ARCHIVE_DIR).mkdir(exist_ok=True)
        for fn in archive:
            wr(root / ARCHIVE_DIR / fn, "# %s\n\nfixture\n" % fn, newline)
        (root / CHECKPOINTS_DIR).mkdir(exist_ok=True)
        for i in range(checkpoints):
            wr(root / CHECKPOINTS_DIR / ("checkpoint-2026-08-0%d-000000.md" % (i + 1)),
               "# checkpoint\n", newline)
        return root

    def run(root, extra=()):
        buf = io.StringIO()
        argv = ["--root", str(root), "--checkpoint-ts", "2026-08-03 00:00:00 TST"] + list(extra)
        code = main(argv, _out=buf)
        data = _read_bytes_or_none(root / MANIFEST_NAME)
        text = data.decode("utf-8") if data is not None else ""
        return code, buf.getvalue(), text, (data or b"")

    def summary(report):
        got = {}
        inside = False
        for line in report.splitlines():
            if line.startswith("--- BEGIN REGEN SUMMARY"):
                inside = True
                continue
            if line.startswith("--- END REGEN SUMMARY"):
                inside = False
                continue
            if inside and ": " in line:
                key, _sep, val = line.partition(": ")
                got.setdefault(key, val)
        return got

    def table_rows(text, header_cells):
        """The data rows of the ONE table whose header matches. Never a regex over the file."""
        head = _row(header_cells)
        lines = text.splitlines()
        try:
            start = lines.index(head)
        except ValueError:
            return None
        rows = []
        for line in lines[start + 2:]:
            if not line.startswith("|"):
                break
            rows.append(line)
        return rows

    def section(text, title):
        lines = text.splitlines()
        try:
            start = lines.index("## " + title)
        except ValueError:
            return None
        body = []
        for line in lines[start + 1:]:
            if line.startswith("## "):
                break
            body.append(line)
        return "\n".join(body)

    def blob(rows):
        """F130: `"\\n".join(None)` is a TRACEBACK, and a traceback destroys the diagnostic in
        exactly the case the check exists to name. An empty blob fails the check by NAME."""
        return "\n".join(rows or [])

    def nth_line(text, idx):
        """F130: a bare `text.splitlines()[idx]` raises IndexError on a truncated or zero-byte
        manifest -- the harness then exits 7 BEFORE printing a single FAIL. Four content-loss
        mutants landed there on 2026-08-03. Return a NAMED sentinel instead."""
        parts = text.splitlines()
        if len(parts) > idx:
            return parts[idx]
        return "(no line %d - manifest is %d bytes, %d lines)" % (idx, len(text), len(parts))

    def sm_int(sm, key):
        """F130: `int(sm[key])` is a KeyError when the summary block is missing or truncated."""
        raw = sm.get(key)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return "(summary has no parsable %s: %r)" % (key, raw)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="regen_manifest_selftest_"))
    try:
        # ---- A. a fresh v4-shaped folder ------------------------------------------------------
        a = scaffold(tmp, "alpha", checkpoints=2)
        code, report, text, data = run(a)
        sm = summary(report)
        ck("A/exit", code, EXIT_OK)
        # F130: this guard is RECORDED **and GATES**. Recording it alone was the instrument
        # defect: the next line did `text.splitlines()[0]`, so a content-loss mutant killed the
        # harness with IndexError (exit 7) BEFORE any FAIL printed -- the verdict stayed
        # non-zero, so no false green, but the diagnostic was destroyed in exactly the case it
        # was written for. MEASURED 2026-08-03: four mutants (M18, M24, M25 the zero-byte write,
        # M27) landed there.
        a_ok = len(text) > 800
        ck("A/nonempty", a_ok, True)
        if not a_ok:
            ck("A/content-lost-block-skipped", len(text), "> 800 bytes")
        if a_ok:
            ck("A/header", nth_line(text, 0),
               "<!-- ash_save v4 | save#3 | 2026-08-03 00:00:00 TST | written by "
               "regen_manifest.py -->")
            ck("A/title", nth_line(text, 1), "# alpha - manifest")
            ck("A/save-from-state", sm.get("save"), "3")
            ck("A/project-from-state", sm.get("project"), "alpha")
            hot_rows = table_rows(text, ["class", "files", "drains on"])
            ck("A/hot-table-found", hot_rows is not None, True)
            ck("A/hot-rows-is-4", len(hot_rows or []), 4)         # REQ: one row per CLASS
            ck("A/hot-present-10", sm.get("hot_present"), "10")
            ck("A/no-absent-marker", "(absent)" in blob(hot_rows or ["x"]), False)
            # REQ 2 -- `recall frontier` NAMED in the router
            ck("A/req2-frontier-named", "recall frontier" in text, True)
            # REQ 3 -- no retired name anywhere in the hot-set table
            hot_blob = blob(hot_rows)
            ck("A/req3-retired-in-hot", sorted(n for n in RETIRED_IN_V4 if n in hot_blob), [])
            # REQ 1 -- every regular .md directly under _archive/ is named by a row
            arch_rows = table_rows(text, ["class", "direction", "files"])
            arch_blob = blob(arch_rows)
            ck("A/req1-archive-named",
               sorted(n for n in ARCHIVE_REVERSIBLE if n not in arch_blob), [])
            ck("A/oneway-none-present", NO_ARCHIVE_FILES in arch_blob, True)
            ck("A/no-unclassified-archive-row", "unclassified archive" in arch_blob, False)
            # REQ 4 (negative) -- neither nesting on disk, so NO drain row names either path
            ck("A/req4-neg-checkpoints-nested", "checkpoints/_archive/" in text, False)
            ck("A/req4-neg-archive-nested", "_archive/checkpoints/" in text, False)
            ck("A/req4-neg-no-drain", "No checkpoint drain directory is present" in text, True)
            ck("A/drain-rows", sm.get("drain_rows"), "0")
            ck("A/cp-count", sm.get("checkpoints_top"), "2")
            # NOT per-file: two checkpoints on disk, but no row per checkpoint
            ck("A/no-per-file-checkpoint-rows",
               len([ln for ln in text.splitlines()
                    if ln.startswith("|") and "checkpoint-" in ln]), 0)
            ck("A/one-row-per-class-stated", "ONE ROW PER CLASS" in text, True)
            ck("A/unclassified-empty", "None - every top-level markdown file" in text, True)
            ck("A/lf-stays-lf", data.count(b"\r\n"), 0)
            ck("A/lf-line-count", data.count(b"\n"), sm_int(sm, "lines_written"))
            ck("A/no-bom", data.startswith(BOM_BYTES), False)
            # idempotent under a fixed ts (manifest.md already exists on the second pass)
            code2, report2, text2, data2 = run(a)
            ck("A/idempotent-bytes", data2, data)
            ck("A/idempotent-exit", code2, EXIT_OK)
            ck("A/save-num-overrides", summary(run(a, ["--save-num", "41"])[1]).get("save"), "41")

        # ---- B. archive fully populated + an unknown file + exclusions -------------------------
        b = scaffold(tmp, "bravo", archive=tuple(ARCHIVE_DECLARED) + ("mystery.md", "junk.zip"))
        code, report, text, data = run(b)
        sm = summary(report)
        ck("B/exit", code, EXIT_OK)
        arch_rows = table_rows(text, ["class", "direction", "files"])
        ck("B/arch-rows-is-3", len(arch_rows or []), 3)          # one-way, reversible, unknown
        arch_blob = "\n".join(arch_rows or [])
        ck("B/req1-every-md-named",
           sorted(n for n in sorted(ARCHIVE_DECLARED) + ["mystery.md"] if n not in arch_blob), [])
        ck("B/unknown-row", "unclassified archive" in arch_blob, True)
        ck("B/mystery-not-in-hot",
           "mystery.md" in blob(table_rows(text, ["class", "files", "drains on"])), False)
        ck("B/excluded-zip-unnamed", "junk.zip" in text, False)
        ck("B/oneway-count", sm.get("archive_oneway"), "6")
        ck("B/reversible-count", sm.get("archive_reversible"), "2")
        ck("B/unclassified-archive-count", sm.get("archive_unclassified"), "1")
        ck("B/archive-md-total", sm.get("archive_md_total"), "9")

        # ---- C. a folder still carrying every RETIRED v3 filename ------------------------------
        # The subject here is the router, not the schema: what it does with the ten names v4
        # retired plus one it has never heard of. The folder therefore declares v4 -- the schema
        # half moved to arm C2 below, where it is a refusal rather than a warning.
        c = scaffold(tmp, "charlie", hot=frozenset(["state.md", "CONSTRAINTS.md"]),
                     archive=())
        for fn in RETIRED_IN_V4:
            wr(c / fn, "# %s\n" % fn)
        wr(c / "handoff-something.md", "# stray\n")
        code, report, text, data = run(c)
        sm = summary(report)
        ck("C/exit", code, EXIT_OK)
        hot_blob = "\n".join(table_rows(text, ["class", "files", "drains on"]) or [])
        # REQ 3 done-condition, on the corpus that actually has the files
        ck("C/req3-retired-in-hot", sorted(n for n in RETIRED_IN_V4 if n in hot_blob), [])
        unc = section(text, "Unclassified (on disk, NOT hot in v4)") or ""
        ck("C/req3-retired-listed", sorted(n for n in RETIRED_IN_V4 if n not in unc), [])
        ck("C/retired-count", sm.get("unclassified_retired"), "10")
        ck("C/unrecognised-count", sm.get("unclassified_unrecognised"), "1")
        ck("C/unrecognised-named", "handoff-something.md" in unc, True)
        ck("C/hot-absent-marked", "CLAUDE.md (absent)" in hot_blob, True)
        ck("C/agents-hot-absent-marked", "AGENTS.md (absent)" in hot_blob, True)
        # 2 on disk (state.md, CONSTRAINTS.md) + manifest.md, which this run is WRITING and so
        # counts PRESENT BY CONSTRUCTION. It was 2 before that fix, and the file it described
        # was on disk by the time anyone could read the claim.
        ck("C/hot-present-3", sm.get("hot_present"), "3")
        ck("C/manifest-never-absent", "manifest.md (absent)" in hot_blob, False)
        ck("C/hot-rows-still-4", len(table_rows(text, ["class", "files", "drains on"]) or []), 4)

        # ---- C2. the migration gate: an UNMIGRATED folder is refused, not routed ---------------
        # The same corpus, declaring v3. Before this gate existed, this run rewrote the router at
        # exit 0 -- on a real project folder it replaced a v3 manifest.md with a much smaller v4
        # one, with no backup, where the v3 tool doing the same job writes one.
        c2 = scaffold(tmp, "charlie_v3", hot=frozenset(["state.md", "CONSTRAINTS.md"]),
                      archive=(), schema="v3")
        for fn in RETIRED_IN_V4:
            wr(c2 / fn, "# %s\n" % fn)
        code2, report2, text2, _d2 = run(c2)
        ck("C2/v3-refused", code2, EXIT_NOT_MIGRATED)
        ck("C2/v3-no-manifest-written", (c2 / MANIFEST_NAME).exists(), False)
        ck("C2/v3-no-report", text2, "")
        # ...and the second run refuses identically, because nothing about the folder changed.
        ck("C2/v3-refused-again", run(c2)[0], EXIT_NOT_MIGRATED)
        # An absent schema_version is D23's v3, not "assume current" -- so it refuses too.
        c3 = scaffold(tmp, "charlie_bare", hot=frozenset(["state.md", "CONSTRAINTS.md"]),
                      archive=())
        wr(c3 / STATE_NAME, "project: charlie_bare\nsave: 4\n")
        ck("C2/absent-schema-refused", run(c3)[0], EXIT_NOT_MIGRATED)
        ck("C2/absent-schema-no-manifest", (c3 / MANIFEST_NAME).exists(), False)
        ck("C2/exit-code-is-4", EXIT_NOT_MIGRATED, 4)
        ck("C2/exit-code-aliased-not-restated",
           EXIT_NOT_MIGRATED, update_state.EXIT_NOT_MIGRATED)
        ck("C2/exit-code-is-not-this-tools-abort",
           EXIT_NOT_MIGRATED == EXIT_ABORT, False)

        # ---- D. drain nesting 1: checkpoints/_archive/ -----------------------------------------
        d = scaffold(tmp, "delta", checkpoints=1)
        wr(d / CHECKPOINTS_DIR / ARCHIVE_DIR / "checkpoint-2026-07-01-000000.md", "# old\n")
        code, report, text, data = run(d)
        sm = summary(report)
        ck("D/exit", code, EXIT_OK)
        drain_rows = table_rows(text, ["drain", "path", "files"])
        ck("D/req4-drain-row-count", len(drain_rows or []), 1)
        ck("D/req4-names-nesting", "`checkpoints/_archive/`" in "\n".join(drain_rows or []), True)
        ck("D/req4-other-nesting-absent", "_archive/checkpoints/" in text, False)
        ck("D/drain-file-count", sm.get("drain"), "checkpoints/_archive/ 1")
        ck("D/top-count-excludes-drain", sm.get("checkpoints_top"), "1")

        # ---- E. drain nesting 2: _archive/checkpoints/ -----------------------------------------
        e = scaffold(tmp, "echo", checkpoints=1)
        wr(e / ARCHIVE_DIR / CHECKPOINTS_DIR / "checkpoint-2026-07-01-000000.md", "# old\n")
        wr(e / ARCHIVE_DIR / CHECKPOINTS_DIR / "checkpoint-2026-07-02-000000.md", "# old\n")
        code, report, text, data = run(e)
        sm = summary(report)
        ck("E/exit", code, EXIT_OK)
        drain_rows = table_rows(text, ["drain", "path", "files"])
        ck("E/req4-drain-row-count", len(drain_rows or []), 1)
        ck("E/req4-names-nesting", "`_archive/checkpoints/`" in "\n".join(drain_rows or []), True)
        ck("E/req4-other-nesting-absent", "checkpoints/_archive/" in text, False)
        ck("E/drain-file-count", sm.get("drain"), "_archive/checkpoints/ 2")
        # the drain dir is NOT counted as an archive entry file
        ck("E/drain-not-archive-md", sm.get("archive_md_total"), "2")

        # ---- F. BOTH nestings at once ----------------------------------------------------------
        f = scaffold(tmp, "foxtrot", checkpoints=1)
        wr(f / CHECKPOINTS_DIR / ARCHIVE_DIR / "checkpoint-2026-07-01-000000.md", "# old\n")
        wr(f / ARCHIVE_DIR / CHECKPOINTS_DIR / "checkpoint-2026-07-02-000000.md", "# old\n")
        code, report, text, data = run(f)
        sm = summary(report)
        drain_rows = table_rows(text, ["drain", "path", "files"])
        ck("F/req4-both-rows", len(drain_rows or []), 2)
        drain_blob = blob(drain_rows)
        ck("F/req4-both-named",
           ("`checkpoints/_archive/`" in drain_blob, "`_archive/checkpoints/`" in drain_blob),
           (True, True))
        ck("F/drain-rows-summary", sm.get("drain_rows"), "2")

        # ---- G. over-fire guards (a change that makes it over-trigger must go red) --------------
        g = scaffold(tmp, "golf")
        wr(g / ARCHIVE_DIR / CHECKPOINTS_DIR, "not a directory\n")   # a FILE named checkpoints
        wr(g / "backlog-done.md", "# decoy at root, not in _archive\n")
        code, report, text, data = run(g)
        sm = summary(report)
        ck("G/exit", code, EXIT_OK)
        ck("G/file-named-checkpoints-is-not-a-drain", sm.get("drain_rows"), "0")
        ck("G/no-drain-sentence", "No checkpoint drain directory is present" in text, True)
        arch_blob = "\n".join(table_rows(text, ["class", "direction", "files"]) or [])
        ck("G/root-decoy-not-in-archive", "backlog-done.md" in arch_blob, False)
        ck("G/root-decoy-is-unrecognised", sm.get("unclassified_unrecognised"), "1")
        ck("G/archive-oneway-still-0", sm.get("archive_oneway"), "0")

        # ---- H. CRLF in, CRLF out (and the reverse) --------------------------------------------
        h = scaffold(tmp, "hotel", newline="\r\n")
        wr(h / MANIFEST_NAME, "# stale manifest\n", "\r\n")
        code, report, text, data = run(h)
        sm = summary(report)
        ck("H/crlf-preserved", data.count(b"\r\n"), sm_int(sm, "lines_written"))
        ck("H/crlf-no-bare-lf", data.count(b"\n") - data.count(b"\r\n"), 0)
        ck("H/newline-reported", sm.get("newline"), "crlf (from manifest.md)")
        # same tree, LF manifest: must come back LF
        wr(h / MANIFEST_NAME, "# stale manifest\n", "\n")
        code, report, text, data = run(h)
        ck("H/lf-preserved-over-crlf-state", data.count(b"\r\n"), 0)
        ck("H/lf-nonempty", len(data) > 800, True)
        # no manifest at all: falls back to state.md's terminator, which here is CRLF
        (h / MANIFEST_NAME).unlink()
        code, report, text, data = run(h)
        ck("H/fallback-to-state", summary(report).get("newline"), "crlf (from state.md)")
        ck("H/fallback-crlf-bytes", data.count(b"\r\n") > 0, True)
        # BOM preserved
        with open(str(h / MANIFEST_NAME), "wb") as fh:
            fh.write(BOM_BYTES + b"# stale\r\n")
        code, report, text, data = run(h)
        ck("H/bom-preserved", data.startswith(BOM_BYTES), True)
        ck("H/bom-reported", summary(report).get("bom"), "yes")

        # ---- I. state.md edge cases ------------------------------------------------------------
        # Missing state.md refuses and preserves an existing manifest byte-for-byte.
        i_dir = scaffold(tmp, "india")
        (i_dir / STATE_NAME).unlink()
        # This scaffold DOES lay down a manifest.md, so absence is the wrong assertion here:
        # what the refusal has to protect is the bytes already on disk.
        i_manifest_before = _read_bytes_or_none(i_dir / MANIFEST_NAME)
        ck("I/no-state-manifest-existed", i_manifest_before is not None, True)
        ck("I/no-state-refused", run(i_dir)[0], EXIT_NOT_MIGRATED)
        ck("I/no-state-manifest-untouched",
           _read_bytes_or_none(i_dir / MANIFEST_NAME), i_manifest_before)
        # a `save:` inside a fence must not be read as the save number
        j = scaffold(tmp, "juliet")
        wr(j / STATE_NAME,
           "schema_version: v4\nproject: juliet\n```text\nsave: 999\n```\nsave: 7\n")
        code, report, text, data = run(j)
        ck("I/fenced-save-skipped", summary(report).get("save"), "7")
        ck("I/fenced-save-exit", code, EXIT_OK)
        # an OPEN fence in state.md: exit 6 OUTRANKS the verdict, and the file is still written
        k = scaffold(tmp, "kilo")
        wr(k / STATE_NAME, "schema_version: v4\nproject: kilo\nsave: 5\n```text\nnot closed\n")
        code, report, text, data = run(k)
        ck("I/open-fence-exit-6", code, EXIT_SCAN_INTEGRITY)
        ck("I/open-fence-still-wrote", len(text) > 800, True)

        # ---- J. usage errors -------------------------------------------------------------------
        ck("J/no-root", main([], _out=io.StringIO()), EXIT_USAGE)
        ck("J/root-missing", main(["--root", str(tmp / "nope")], _out=io.StringIO()), EXIT_USAGE)
        ck("J/root-is-a-file",
           main(["--root", str(a / STATE_NAME)], _out=io.StringIO()), EXIT_USAGE)
        ck("J/negative-save",
           main(["--root", str(a), "--save-num", "-1"], _out=io.StringIO()), EXIT_USAGE)
        ck("J/ts-with-comment-close",
           main(["--root", str(a), "--checkpoint-ts", "x --> y"], _out=io.StringIO()),
           EXIT_USAGE)

        # ---- K. mutation direction: turning the mechanism OFF must go red ----------------------
        # Proven by construction: A's archive holds the two reversible files and B's holds all
        # nine; if the enumerator returned the DECLARED list instead of disk, A would name six
        # files that do not exist. Assert the difference directly.
        a_arch = "\n".join(table_rows(run(a)[2], ["class", "direction", "files"]) or [])
        b_arch = "\n".join(table_rows(run(b)[2], ["class", "direction", "files"]) or [])
        ck("K/disk-not-list-A", sorted(n for n in ARCHIVE_ONE_WAY if n in a_arch), [])
        ck("K/disk-not-list-B", sorted(n for n in ARCHIVE_ONE_WAY if n not in b_arch), [])
        ck("K/two-corpora-differ", a_arch == b_arch, False)

        # ---- L. GATE HOLES: the two mutants NO REAL CORPUS CAN DISTINGUISH ---------------------
        # MEASURED 2026-08-03: a fleet scan of 225 real `checkpoints/` directories found 0
        # zero-byte files and 0 files without the `checkpoint-` prefix. So dropping the
        # `_nonempty_md` guard, and dropping the `checkpoint-` prefix from the `cp_files`
        # filter, both stayed GREEN on every corpus available -- F129 says use the real corpus,
        # and here the real corpus provably cannot decide it. These fixtures are SYNTHETIC on
        # purpose: they are the only shapes that turn those two mutants red.
        l_dir = scaffold(tmp, "lima", checkpoints=2)
        wr(l_dir / CHECKPOINTS_DIR / "checkpoint-2026-08-09-000000.md", "")       # ZERO BYTES
        wr(l_dir / CHECKPOINTS_DIR / "notes.md", "# no checkpoint- prefix\n")     # wrong prefix
        code, report, text, data = run(l_dir)
        sm = summary(report)
        ck("L/exit", code, EXIT_OK)
        ck("L/nonempty", len(text) > 800, True)                  # F130: guard the comparisons
        # Both synthetic names sort ABOVE the two real ones, so each mutant moves the COUNT and
        # the LATEST. Assert both: either alone could be a coincidence of the fixture.
        ck("L/zero-byte-checkpoint-not-counted", sm.get("checkpoints_top"), "2")
        ck("L/zero-byte-checkpoint-not-named", "checkpoint-2026-08-09-000000.md" in text, False)
        ck("L/unprefixed-md-not-counted", "notes.md" in text, False)
        ck("L/latest-is-a-real-checkpoint",
           "Latest: `checkpoints/checkpoint-2026-08-02-000000.md`" in text, True)

        for suffix in ("-2", "-10"):
            wr(l_dir / CHECKPOINTS_DIR / ("checkpoint-2026-08-02-000000%s.md" % suffix),
               "# collision\n")
        code, report, text, data = run(l_dir)
        ck("L/collision-exit", code, EXIT_OK)
        ck("L/collision-latest-numeric-suffix",
           "Latest: `checkpoints/checkpoint-2026-08-02-000000-10.md`" in text, True)
        ck("L/collision-count-keeps-all", summary(report).get("checkpoints_top"), "4")

        # ---- M. the write temp: removed on failure, and NOT ROUTABLE even when it survives ----
        import stat as _stat
        m = scaffold(tmp, "mike")
        m_tmp = m / (MANIFEST_NAME + TMP_SUFFIX)
        # half 1 -- the measured trigger. MEASURED 2026-08-03: with the target read-only,
        # `os.replace` raises WinError 5, this tool exits 2, and `manifest.md.regen-tmp` was
        # LEFT in the hot folder.
        os.chmod(str(m / MANIFEST_NAME), _stat.S_IREAD)
        try:
            code, report, text, data = run(m)
        finally:
            os.chmod(str(m / MANIFEST_NAME), _stat.S_IWRITE)
        ck("M/readonly-target-exit-2", code, EXIT_USAGE)
        ck("M/temp-removed-on-failed-replace", m_tmp.exists(), False)
        ck("M/failed-replace-left-target-intact",
           (m / MANIFEST_NAME).read_bytes(), b"# manifest.md\n\nfixture\n")
        # half 2 -- a leak from a KILLED process, which reaches no `finally` at all. The scan
        # runs BEFORE the write, so this is what the exclusion set has to stop: without it the
        # next successful run enumerates the leak as an unrouted top-level non-markdown file.
        wr(m_tmp, "leaked bytes from a killed run\n")
        ck("M/leak-planted", m_tmp.is_file(), True)
        code, report, text, data = run(m)
        sm = summary(report)
        ck("M/leak-run-exit", code, EXIT_OK)
        ck("M/leak-nonempty", len(text) > 800, True)             # F130: guard the comparisons
        ck("M/leak-not-routed", TMP_SUFFIX in text, False)
        ck("M/leak-no-nonmd-sentence", "not routed by this manifest" in text, False)
        ck("M/leak-consumed-by-next-write", m_tmp.exists(), False)

        # ---- N. the router is PRESENT BY CONSTRUCTION, and IDEMPOTENT FROM ABSENT --------------
        # Case H unlinks `manifest.md` too, but asserts only the newline fallback and the BOM --
        # which is exactly why this got through. MEASURED 2026-08-03: on a root where the file
        # was not already on disk the generated router said `manifest.md (absent)` and
        # understated `hot_present` by one, so run 1 differed from run 2 (hot_present 5 -> 6 and
        # 8 -> 9; bytes 3992 -> 3983). The claim was false at the instant the bytes landed.
        n = scaffold(tmp, "november", checkpoints=1)
        (n / MANIFEST_NAME).unlink()
        ck("N/manifest-absent-before-run-1", (n / MANIFEST_NAME).exists(), False)
        code1, report1, text1, data1 = run(n)
        sm1 = summary(report1)
        ck("N/run1-exit", code1, EXIT_OK)
        ck("N/run1-nonempty", len(data1) > 800, True)            # F130: two empties compare equal
        ck("N/run1-hot-present-10", sm1.get("hot_present"), "10")
        ck("N/run1-never-declares-itself-absent", "manifest.md (absent)" in text1, False)
        code2, report2, text2, data2 = run(n)
        ck("N/run2-exit", code2, EXIT_OK)
        ck("N/run2-hot-present-10", summary(report2).get("hot_present"), "10")
        ck("N/idempotent-bytes-from-absent", data2, data1)
        # and on a PARTIAL root, where the count is not the full nine either way
        p = scaffold(tmp, "papa", hot=frozenset(["state.md", "CONSTRAINTS.md", "backlog.md"]),
                     archive=())
        code1, report1, text1, data1 = run(p)
        code2, report2, text2, data2 = run(p)
        ck("N/partial-nonempty", len(data1) > 800, True)         # F130: guard before comparing
        ck("N/partial-hot-present-4-both-runs",
           (summary(report1).get("hot_present"), summary(report2).get("hot_present")),
           ("4", "4"))
        ck("N/partial-never-declares-itself-absent", "manifest.md (absent)" in text1, False)
        ck("N/partial-idempotent-bytes", data2, data1)

        # ---- O: THE EMITTED ROUTER MAY NOT NAME ONE OF *THIS* PROJECT'S IDENTIFIERS.
        # This generator writes a router into EVERY project on the fleet, so an id from its own
        # ledger in that prose lands in the HOST's identifier namespace. It then resolves to
        # whatever that project happens to call `D60` -- or to nothing. THE DANGLE IS THE LUCKY
        # OUTCOME: it goes red loudly. A host that HAS a `D60` of its own goes GREEN with the
        # citation pointing at the wrong record, and no check anywhere reports it.
        #
        # THIS ARM EXISTS BECAUSE THE CLASS RECURRED FOUR TIMES IN THIS ONE FILE. `C-4` and `D60`
        # were removed by hand in one round; `W18` survived that round untouched and was found by
        # a sweep in the next. Every fix before this arm was a human re-reading prose, which is
        # exactly the instrument that missed it twice. A test is the only thing that makes the
        # fifth instance impossible.
        #
        # THE FIXTURE MUST REACH THE CONDITIONAL ROWS OR THE ARM CANNOT FAIL. The `W18` cell
        # renders only when `scan["retired"]` is non-empty, so a bare v4 scaffold never reaches
        # it -- an arm built on one would pass while the defect shipped. The retired names, the
        # unrecognised name and the non-md files below are what force every row onto the page.
        idroot = scaffold(tmp, "idclean", checkpoints=2)
        _clean = "# fixture" + chr(10) + chr(10) + "No identifier here." + chr(10)
        for _fn in ("decisions.md", "research.md", "index.md", "restart-prompt.md",
                    "roadmap.md", "frontier.md", "decisions-active.md", "misc.md"):
            wr(idroot / _fn, _clean)
        wr(idroot / "some-handoff-note.md", _clean)
        wr(idroot / "notes.bak", _clean)
        wr(idroot / ARCHIVE_DIR / "backlog-done.md", _clean)
        _idcode, _idrep, idtext, _idraw = run(idroot)
        ck("O/id-clean-exit", _idcode, EXIT_OK)
        # Guard the guard: if these rows stop rendering the arm silently stops testing anything.
        ck("O/id-clean-retired-row-rendered", "retired-in-v4" in idtext, True)
        ck("O/id-clean-unrecognised-row-rendered", "unrecognised" in idtext, True)
        _idfams = citemap.build_families()
        _leaked = sorted({ident
                          for _line in idtext.splitlines()
                          for ident, _s, _e in citemap.scan_occurrences(_line, _idfams)})
        ck("O/the router cites NO identifier of ours", _leaked, [])
        # And prove the detector is live rather than vacuous: the same scan over prose that DOES
        # carry one must find it. Without this the arm above passes when `scan_occurrences` is
        # broken, which is the failure mode it is meant to rule out.
        ck("O/...and the same scan DOES catch one when present",
           sorted({ident for ident, _s, _e
                   in citemap.scan_occurrences("retired by W18 and D60", _idfams)}),
           ["D60", "W18"])
    except Exception as _exc:                                    # noqa: BLE001 - deliberate
        # F6b, ported from update_state.py so the two tools built in the same step do not
        # diverge on their own diagnostics. The suite is ONE linear block, so before this an
        # exception anywhere in it propagated to cli(), which returned 7 with NO FAIL lines and
        # NO "N checks, M failed" line -- every check after the throw silently skipped. Exit
        # stayed non-zero so there was never a false green, but the diagnostic was destroyed in
        # exactly the total-content-loss case this file's own comments record four mutants
        # landing on. The pair is (False, True), never (str(exc), ""): an exception whose str()
        # is empty would compare EQUAL and record the abort as a PASSING check.
        ck("selftest/aborted-by-exception:%s:%s" % (type(_exc).__name__, str(_exc)[:120]),
           False, True)
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)

    bad = [c for c in checks if not c[3]]
    for label, got, want, ok in checks:
        if not ok:
            sys.stderr.write("FAIL %s: got %r want %r\n" % (label, got, want))
    sys.stderr.write("%d checks, %d failed\n" % (len(checks), len(bad)))
    return EXIT_SELFTEST if bad else EXIT_OK


if __name__ == "__main__":
    sys.exit(cli())
