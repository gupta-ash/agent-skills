#!/usr/bin/env python3
"""Regenerate the v4 ``state.md`` generated cursor for one save project folder.

CONTRACT: ``--root`` is REQUIRED and is NEVER inferred from cwd (D53). 512 distinct id
    strings exist across the fleet and D17/C-6/F1 recur in dozens of unrelated projects,
    so an inferred scope resolves confidently over the wrong project.
CONTRACT: the STRUCTURED BLOCK is CONTIGUOUS - it runs from the first top-level field line
    to the first line after it that is neither a top-level field nor an indented child of
    ``files:``/``counts:``. Everything below is preserved tail. MEASURED 2026-08-03, all at
    exit 0: taking the block's end from the last field-shaped line ANYWHERE in the file let a
    column-0 prose line delete every line above itself - a tail ``save: 99`` cost 1459 bytes
    (63.8%) and the BACKLOG-ENTRY-IDS region with them, ``hot_state: <a sentence>`` cost 1013 and
    permanently rewrote the one field this file calls preserved byte-for-byte. A second
    occurrence of a top-level key no longer overwrites the first; the collision is warned.
CONTRACT: ``--checkpoint-ts`` and ``--save-num`` are VALIDATED before use, because both are
    interpolated into generated lines. MEASURED 2026-08-03: ``--checkpoint-ts $'T\\nsave: 999'``
    exited 0 with zero warnings and put a column-0 ``save: 999`` on line 2, above the real one,
    which makes the curator report the save torn; ``--save-num=-5`` wrote ``save: -5``.
CONTRACT: the ``save:`` line is written at column 0, lowercase, no space before the colon,
    and carries a BARE INTEGER. Measured 2026-08-03 against
    ``docs/oob-ingest/build/curator_entry.py:210-218``: that reader does
    ``int(line.split(":", 1)[1].strip())`` inside a ``try`` that wraps the WHOLE scan, so one
    unparsable ``save:``-prefixed line makes the curator report the save torn (ruling R-K).
CONTRACT: this tool requires an existing v4 declaration before any write (exit 4 otherwise).
    There is no conversion or version-stamping override. Fresh v4 scaffolding belongs to
    init_project.py; this writer only regenerates an already declared v4 cursor.
CONTRACT: the ``BACKLOG-ENTRY-IDS`` region is REWRITTEN every save from the post-demote
    ``backlog.md`` (ruling R-J). Measured 2026-08-03, both arms, one variable: with the baseline
    left at ``[]`` demote.py row 2b reported ``0 entries moved`` and NO warning for a deleted
    entry; with the baseline written it reported ``pending_drop: M1`` plus a named warning.
    The detector was correct; it was inert because nothing wrote its input. Well-formedness is
    decided on the marker count over the FILE AS READ, never over the preserved tail: counting
    in the tail is how the region warning came to state the opposite of what happened.
INVARIANT: ``hot_state``, ``no_demote_streak`` and ``backlog_order`` are PRESERVED
    byte-for-byte. They are the P3 instrument tier; P1 never computes, increments or
    interprets them. Absent -> the scaffolder default, never a guess.
INVARIANT: ``task_id_high_water`` is PRESERVED, NEVER SCANNED and NEVER DECREASING. This tool
    derives no value for it from ``backlog.md`` or the task store; it carries forward what it
    found, and a candidate lower than the recorded value is ignored. ABSENT IS NOT ZERO - an
    absent mark emits NO line, because F146 measured a counter reading 0 in all eleven
    committed versions of a file for exactly the reason that a missing field was read as zero.
    The cost of collapsing the two is a REUSED task id, which is permanent.
INVARIANT: a ``key: |`` / ``key: >`` field OWNS its indented continuation lines and they are
    re-emitted verbatim. The synthetic two-block-scalar fixture checks actual line conservation,
    including content after the structured block, without a private project corpus.
INVARIANT: terminators and a leading UTF-8 BOM survive the rewrite. Measured house failure: a
    whole-file rewrite flips CRLF->LF (or back) silently and ``git status``/``git diff``
    report clean throughout; measured here, a BOM'd state.md went 2230 -> 2192 B.
INVARIANT: a count that computes 0 where the previous file recorded > 0 emits a WARNING
    naming the file and both numbers, and the computed value is written anyway (F65). Never
    silently substitute the prior value; never silently write 0. This is the ``oq_open: 0``
    against a real 8 that W7 exists to fix.
INVARIANT: ACCOUNTING. Every non-blank input line is (a) re-emitted verbatim, (b) a field this
    tool deliberately recomputes, or (c) named in a warning. A line that is none of the three
    aborts the write and returns EXIT_INTERNAL with the offending lines listed. The three
    content-loss defects above are ONE failure - bytes going in and not coming out - and four
    separate patches would not have stopped the fifth instance.
EXTERNAL: reads ``backlog.md``, ``open_questions.md``, ``active.md``, ``checkpoints/`` and the
    ten hot files under ``--root``; rewrites ``--root/state.md`` and nothing else.
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
except ImportError as _exc:                                     # pragma: no cover - fatal
    sys.stderr.write("error: cannot import sibling from %s: %s\n"
                     % (pathlib.Path(__file__).resolve().parent, _exc))
    sys.exit(2)

import argparse

EXIT_OK             = 0   # rewrote state.md; or warnings only
EXIT_SELFTEST       = 1   # --selftest ONLY, and only on a failed assertion
EXIT_USAGE          = 2   # usage or I/O: missing/not-a-dir --root, unreadable file
EXIT_ABORT          = 3   # (not used by this tool - reserved, same meaning fleet-wide)
EXIT_NOT_MIGRATED   = 4   # --root is not a migrated v4 project folder. NOTHING was written.
                          # Same number and same meaning in regen_manifest.py and demote.py,
                          # which import it from here rather than restating it.
EXIT_SCAN_INTEGRITY = 6   # at least one file in scope ends with an OPEN fence
EXIT_INTERNAL       = 7   # uncaught exception, OR the write-time accounting guard found
                          # content it cannot explain and REFUSED to write. Both mean "this
                          # tool is broken"; state.md on disk is untouched either way.
                          # Deliberately NOT 1, which the table reserves for --selftest.

SCHEMA_VERSION = "v4"

# ---------------------------------------------------------------------------------------------
# The migration gate. SHARED: regen_manifest.py and demote.py import these names, so all three
# v4 writers refuse the same folders for the same reason in the same words. It lives here rather
# than in a fourth module because this file owns SCHEMA_VERSION and is the only file that ever
# WRITES it.
# ---------------------------------------------------------------------------------------------

# Column 0, and the value is whatever follows the colon. An indented variant is a child of some
# other key, not a top-level declaration.
SCHEMA_LINE_RE = re.compile(r"^schema_version[ \t]*:(?P<val>.*)$")


def read_declared_schema(root):
    """What ``<root>/state.md`` DECLARES itself to be. Returns ``(state_exists, declared)``.

    ``declared`` is None when state.md is absent, or carries no column-0 ``schema_version:``
    line outside a code fence, or carries one whose value is empty. D23 rules that ABSENT MEANS
    v3; it never means "assume current". A folder written before the field existed is exactly
    the folder this gate exists for, so the reading that is cheap when wrong is the one to take:
    calling a v4 folder v3 costs one refusal the operator can answer in a sentence, while
    calling a v3 folder v4 costs the discriminator itself and cannot be answered at all.

    Fenced lines are skipped because a ``schema_version:`` inside a code block is documentation.
    """
    path = pathlib.Path(root) / "state.md"
    if not path.is_file():
        return False, None
    _data, rows, _open_fence = recall.load_doc(path)
    for _lineno, text, _start, _end, in_fence in rows:
        if in_fence:
            continue
        m = SCHEMA_LINE_RE.match(text)
        if m:
            return True, (m.group("val").strip() or None)
    return True, None


# WHICH READ produced the `declared` value in a refusal. The two gates in this file do NOT read
# the same span: `require_migrated` reads the whole file, and `regenerate` reads only the
# contiguous structured block `parse_state` returns. A `found:` line that says "its state.md
# carries no schema_version line" is a claim about the FILE, and only the whole-file reader is
# entitled to make it. The fault it cost, reproduced before this was written: on a state.md
# whose column-0 `schema_version: v4` sat below a blank line, the block reader found nothing and
# the refusal reported the file as carrying no such line, while that WAS a line of the file. So
# the refusal now names its own scope, and the sentence is true of whatever was actually read.
SCOPE_WHOLE_FILE = "read_declared_schema - every column-0 line of state.md, outside code fences"
SCOPE_BLOCK = "parse_state - state.md's contiguous structured block, which is not the whole file"


def migration_refusal(root, tool, state_exists, declared,
                      scope=SCOPE_WHOLE_FILE, elsewhere=None):
    """Describe a non-v4 refusal without guessing or converting the saved format."""
    if not state_exists:
        found = "state.md is absent; no saved schema can be established"
    elif declared is None:
        found = "no schema_version declaration was found in the span read"
    else:
        found = "the span read declares schema_version %r, not %r" % (declared, SCHEMA_VERSION)
    out = [
        "error: %s: this is not a migrated v4 project folder, so nothing was written.\n" % tool,
        "       folder:  %s\n" % root,
        "       found:   %s.\n" % found,
        "       read:    %s.\n" % scope,
    ]
    if elsewhere is not None and elsewhere != declared:
        out.append("       NOTE:    a WHOLE-FILE read finds schema_version %r.\n" % elsewhere)
    if elsewhere == SCHEMA_VERSION:
        out.append(
            "       remedy:  recover the v4 cursor's contiguous top-level field block,\n"
            "                preserving the project's existing saved bytes, then retry.\n")
    else:
        out.append(
            "       remedy:  this distribution reads and writes v4 only. Preserve this\n"
            "                folder unchanged. Use the tooling that owns its declared format,\n"
            "                or recover its original v4 state.md if the cursor was lost.\n"
            "                No format conversion is included or performed.\n")
    return "".join(out)


def require_migrated(root, tool, _err=None):
    """Require an existing v4 declaration; no conversion bypass is supported.

    SCOPE, and it is a judgement rather than a settled question: this gates the three WRITERS.
    The read-only helpers - ``validate_entries.py``, ``recall.py``, ``citemap.py`` - are
    deliberately NOT gated, so a v3 folder stays inspectable while it waits its turn. The
    argument against that is real and is not answered here: a v4 reader resolving a v3 layout
    can return a confident wrong answer, and nothing warns about it. The trade taken is that a
    wrong ANSWER is in front of whoever asked for it, while a wrong WRITE is in front of nobody.
    """
    err = _err if _err is not None else sys.stderr
    state_exists, declared = read_declared_schema(root)
    if declared == SCHEMA_VERSION:
        return None
    err.write(migration_refusal(root, tool, state_exists, declared))
    return EXIT_NOT_MIGRATED

# D7. A UTF-8 BOM on state.md was dropped by the rewrite (measured 2026-08-03: 2230 -> 2192 B).
# regen_manifest.py preserves it; this is the same mechanism. `load_doc` strips the BOM from
# line 1's TEXT only, so it has to be re-applied to the bytes on the way out.
BOM_BYTES = citemap.BOM.encode("utf-8")

# The ten names in the current v4 hot set (spec S3.1), in manifest class order. A valid legacy
# v4 folder may predate the dual-file contract and therefore hold the other nine without
# AGENTS.md; compute_counts reports what is actually present rather than manufacturing damage.
HOT_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "CONSTRAINTS.md",
    "manifest.md",
    "architecture.md",
    "design.md",
    "backlog.md",
    "open_questions.md",
    "state.md",
    "active.md",
)

PLACEHOLDER_REPO_ROOT = "(not resolved yet - spec S2.5 r3)"
PLACEHOLDER_TASK_LIST = "(none resolved yet - spec S7.2 R2)"
PLACEHOLDER_CHECKPOINT = "(none yet)"

GENERATED_NOTE = (
    "GENERATED CURSOR. Rewritten by save every save; do not hand-edit. Exempt from the\n"
    "declaration rules (spec S3.5) because nothing authors its entries."
)

ROW2B_HEADING = "## Row-2b baseline"

BACKLOG_IDS_BEGIN = "<!-- BACKLOG-ENTRY-IDS:BEGIN -->"
BACKLOG_IDS_END = "<!-- BACKLOG-ENTRY-IDS:END -->"
BACKLOG_BEGIN = "<!-- BACKLOG:BEGIN -->"
BACKLOG_END = "<!-- BACKLOG:END -->"

BACKLOG_IDS_BEGIN_RE = re.compile(r"^\s*<!--\s*BACKLOG-ENTRY-IDS:BEGIN\s*-->\s*$", re.I)
BACKLOG_IDS_END_RE = re.compile(r"^\s*<!--\s*BACKLOG-ENTRY-IDS:END\s*-->\s*$", re.I)
BACKLOG_IDS_VALUE_RE = re.compile(r"^\s*backlog_entry_ids\s*:\s*\[(?P<body>.*)\]\s*$")
BACKLOG_BEGIN_RE = re.compile(r"^\s*<!--\s*BACKLOG:BEGIN\s*-->\s*$", re.I)
BACKLOG_END_RE = re.compile(r"^\s*<!--\s*BACKLOG:END\s*-->\s*$", re.I)

# The task-id high-water mark (E12). A preserved field like the others, with two differences
# that are the entire reason it exists -- see `merge_task_high_water`: its default is ABSENT
# rather than a value, and a candidate may only ever raise it.
TASK_HIGH_WATER_KEY = "task_id_high_water"

# A high-water mark is a BARE COUNT. Anything else (a range, prose, a block-scalar indicator) is
# not something this tool may compare, so it is carried forward verbatim instead of guessed at.
TASK_HIGH_WATER_RE = re.compile(r"^[0-9]{1,9}$")

# Scalar fields preserved from the existing file, in emit order, with their defaults.
PRESERVED_FIELDS = (
    ("phase", "discovery"),
    ("status", "active"),
    ("goal", "(not stated yet)"),
    ("active_track", "(not stated yet)"),
    ("repo_root", PLACEHOLDER_REPO_ROOT),
    ("task_list_id", PLACEHOLDER_TASK_LIST),
    ("hot_state", "scaffolded"),
    ("backlog_order", "[]"),
    # This key's default is INERT, and deliberately: `_resolve_preserved` routes the key to
    # `merge_task_high_water` BEFORE any default is consulted, because "absent" here means
    # "emit no line at all", which no default value can express. Editing this "" changes
    # nothing - measured, a mutant setting it to "0" passed all 268 selftest checks, while
    # mutating the two paths that DO own the absent case failed 1 and 2 arms respectively.
    (TASK_HIGH_WATER_KEY, ""),
    ("no_demote_streak", "0"),
)


# ---------------------------------------------------------------------------------------------
# byte / terminator layer
# ---------------------------------------------------------------------------------------------

def dominant_terminator(data):
    """Return the terminator to write back: b'\\r\\n', b'\\r' or b'\\n', whichever dominates.

    Counted on the raw bytes, not on decoded text, because decoding is what loses this.

    F7a: the CR-only branch was missing, so a CR-terminated file was silently flipped to LF
    (measured 2026-08-03: 52 lone CR in, 0 out, 52 LF out). No content was lost, but brief §9
    says restore the terminators FOUND, and `regen_manifest.detect_newline` already handled it --
    a divergence between two tools built in the same step is how one of them becomes wrong later.
    """
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    cr = data.count(b"\r") - crlf
    if crlf and crlf >= lf and crlf >= cr:
        return b"\r\n"
    if cr and cr > lf:
        return b"\r"
    return b"\n"


def join_lines(lines, term):
    """Join terminator-stripped lines with `term`, one trailing terminator at EOF."""
    body = term.join(line.encode("utf-8") for line in lines)
    return body + term if lines else b""


TMP_SUFFIX = ".state-tmp"


def write_bytes(path, data):
    """Bytes out, ATOMICALLY: temp sibling, then `os.replace`, temp unlinked in a `finally`.

    CONTRACT: a failure after the write begins leaves the PREVIOUS file byte-identical.

    Mirrors `regen_manifest.write_manifest`, and the asymmetry is why this matters more here:
    `manifest.md` is 100% derivable from disk and `state.md` is not. `state.md` carries the R-G
    row-2b baseline, and R-J rule 1 correctly forbids re-planting a region that has gone missing
    -- so one truncated write would make step 6a row 2b permanently inert for that project, which
    is the exact defect this whole work item exists to end.

    MEASURED 2026-08-03, on the pre-fix code: a read-only target was already SAFE (`open` raises
    before it truncates, exit 2, 642 B -> 642 B). It is the POST-open failure -- a full disk, a
    kill, an exception mid-render -- that destroyed the file, and `open(..., "wb")` truncates at
    open. The `finally` covers every failure path the process survives; a kill reaches no
    `finally`, which is why the temp name is suffixed rather than reusing the target's.
    """
    tmp = pathlib.Path(str(path) + TMP_SUFFIX)
    try:
        with open(str(tmp), "wb") as fh:
            fh.write(data)
        os.replace(str(tmp), str(path))
    finally:
        # A successful replace already consumed it; this only fires on a failure path.
        try:
            if tmp.exists():
                os.unlink(str(tmp))
        except OSError:                                          # pragma: no cover - race
            pass


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="update_state.py",
        description="Regenerate state.md for a v4 save project folder.",
    )
    p.add_argument("--root", default=None,
                   help="project folder (REQUIRED; never inferred from cwd, D53)")
    p.add_argument("--save-num", type=int, default=None,
                   help="save number to record; preserved from the existing file when omitted")
    p.add_argument("--checkpoint-ts", default=None,
                   help='checkpoint timestamp, "YYYY-MM-DD HH:MM:SS TZ"')
    p.add_argument("--selftest", action="store_true", help="run the in-file selftest and exit")
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

    # The migration gate, at the entry, before any read this tool would write back from.
    gate = require_migrated(root, "update_state.py")
    if gate is not None:
        return gate

    # D3 / ruling R-K. Both values are interpolated into line 1 (an HTML comment) and, for the
    # save number, into the column-0 `save:` line the curator parses. MEASURED 2026-08-03:
    # --checkpoint-ts $'T\nsave: 999' exited 0 with zero warnings and put a column-0
    # `save: 999 | written by ... -->` on LINE 2, above the real one; the curator returns on the
    # FIRST save:-prefixed line, int() raises inside the try that wraps its whole scan, and it
    # reports the save TORN. --save-num=-5 wrote `save: -5` at exit 0. regen_manifest.py already
    # rejects all four; this is the same guard, verbatim in shape.
    if args.save_num is not None and args.save_num < 0:
        sys.stderr.write("error: --save-num must be >= 0: %r\n" % args.save_num)
        return EXIT_USAGE
    ts = args.checkpoint_ts
    if ts is not None and ("\n" in ts or "\r" in ts or "-->" in ts):
        sys.stderr.write("error: --checkpoint-ts may not contain a newline or `-->`: the header "
                         "is an HTML comment and the `save:` line is column-0 (R-K).\n")
        return EXIT_USAGE

    try:
        return regenerate(root, args.save_num, ts, out)
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


# ---------------------------------------------------------------------------------------------
# the work
# ---------------------------------------------------------------------------------------------

TOP_FIELD_RE = re.compile(r"^(?P<key>[a-z_][a-z0-9_]*)\s*:(?P<val>.*)$")
CHILD_FIELD_RE = re.compile(r"^[ \t]+(?P<key>[a-z_][a-z0-9_]*)\s*:(?P<val>.*)$")

# A YAML block-scalar indicator: | or >, with optional indentation/chomping modifiers.
# Its continuation lines belong to the scalar and must survive an ordinary v4 rewrite.
BLOCK_SCALAR_RE = re.compile(r"^[|>][0-9]*[-+]?\s*$")

# Fields whose value may be a block scalar. `save`, `last_save_ts` and `schema_version` are
# regenerated and single-token by contract (R-K, R-L), so a body attached to one is dropped
# with a named warning rather than re-emitted into a line the curator parses.
BLOCK_SCALAR_KEYS = frozenset(("project",) + tuple(k for k, _d in PRESERVED_FIELDS))

TOP_KEYS = frozenset((
    "schema_version", "project", "phase", "save", "last_save_ts", "status", "goal",
    "active_track", "repo_root", "task_list_id", "hot_state", "backlog_order",
    TASK_HIGH_WATER_KEY, "no_demote_streak", "files", "counts",
))


def parse_state(path):
    """Split an existing ``state.md`` into (regenerated header, scalars, preserved tail).

    Everything from the ``schema_version:`` line through the last child of ``counts:`` is the
    STRUCTURED BLOCK and is rewritten. Everything after it - the no_demote_streak paragraph,
    the ``## Row-2b baseline`` prose and the BACKLOG-ENTRY-IDS region - is the TAIL and is
    preserved verbatim (R-J rule 1 wants a malformed region left byte-identical, so the
    default for the whole tail is "do not touch").

    D1, and it is the whole reason this parser was rewritten. The block is CONTIGUOUS: it
    starts at the first TOP_KEYS field line and ends at the first line after it that is
    neither a TOP_KEYS field nor an indented child of ``files:``/``counts:``. The previous
    cut took ``block_end`` from the LAST TOP_KEYS-shaped line ANYWHERE in the file, so any
    column-0 prose line that looked like a field silently deleted every line above it.
    MEASURED 2026-08-03, all at exit 0, on folders scaffolded by init_project.py: a tail line
    ``save: 99`` lost 1459 bytes (63.8%), DELETED the BACKLOG-ENTRY-IDS region and fabricated
    ``save: 99`` from prose; ``hot_state: <a sentence>`` lost 1013 bytes and permanently
    rewrote the one field the contract calls preserved byte-for-byte; ``goal: <prose>`` lost
    654; a stray ``status:`` line lost 1460 including the whole Row-2b heading. All four are
    irreversible: once the region is gone R-J rule 1 correctly forbids re-planting it.

    A SECOND occurrence of a top-level key no longer overwrites the first either - later
    match won the assignment, which is how prose became the value. First wins; the collision
    is warned, naming the key and both values.
    """
    info = {
        "exists": False,
        "term": b"\n",
        "bom": False,
        "fields": {},
        "field_blocks": {},
        "counts": {},
        "files": {},
        "header": [],
        "child_lines": [],
        "tail": [],
        "tail_fence": [],
        "dupes": [],
        "block_start": None,
        "block_end": 0,
        "open_fence": None,
        "data": b"",
        "rows": [],
    }
    if not path.is_file():
        return info
    data, rows, open_fence = recall.load_doc(path)
    info["exists"] = True
    info["data"] = data
    info["rows"] = rows
    info["term"] = dominant_terminator(data)
    info["bom"] = data.startswith(BOM_BYTES)          # D7
    info["open_fence"] = open_fence

    section = None
    block_start = None
    block_end = 0
    seen = {}
    i = 0
    n = len(rows)
    while i < n:
        lineno, text, _bs, _be, in_fence = rows[i]
        m = None if in_fence else TOP_FIELD_RE.match(text)
        key = m.group("key") if m is not None else None
        is_top = key is not None and key in TOP_KEYS
        c = None if in_fence else CHILD_FIELD_RE.match(text)
        is_child = c is not None and section is not None and block_start is not None

        if block_start is None:
            if not is_top:
                i += 1
                continue                       # header run: regenerated, never preserved
            block_start = lineno
        elif not is_top and not is_child:
            break                              # D1: CONTIGUOUS. First non-member ends it.

        if is_top:
            val = m.group("val").strip()
            duplicate = key in seen
            if duplicate:
                info["dupes"].append((key, seen[key], val))
            else:
                seen[key] = val
                if key not in ("files", "counts"):
                    info["fields"][key] = val
            section = key if key in ("files", "counts") else None
            block_end = lineno
            i += 1
            # D2: `key: |` / `key: >` OWNS every following blank or more-indented line.
            if BLOCK_SCALAR_RE.match(val):
                body = []
                while i < n:
                    ntext = rows[i][1]
                    if ntext.strip() and ntext[:1] not in (" ", "\t"):
                        break
                    body.append(ntext)
                    block_end = rows[i][0]
                    i += 1
                while body and not body[-1].strip():
                    body.pop()
                if not duplicate:
                    info["field_blocks"][key] = body
                section = None
            continue

        ckey = c.group("key")
        cval = c.group("val").strip()
        info["child_lines"].append((section, ckey, text))
        if ckey in info[section]:
            info["dupes"].append(("%s.%s" % (section, ckey), info[section][ckey], cval))
        else:
            info[section][ckey] = cval
        block_end = lineno
        i += 1

    info["block_start"] = block_start
    info["block_end"] = block_end
    info["header"] = [r[1] for r in rows if block_start is not None and r[0] < block_start]
    tail_rows = [(r[1], r[4]) for r in rows if r[0] > block_end]
    while tail_rows and not tail_rows[0][0].strip():
        tail_rows.pop(0)
    while tail_rows and not tail_rows[-1][0].strip():
        tail_rows.pop()
    info["tail"] = [t for t, _f in tail_rows]
    info["tail_fence"] = [f for _t, f in tail_rows]
    return info


def render_state(project, save, ts, fields, files_block, counts, tail, field_blocks=None):
    """Build the full ``state.md`` as terminator-stripped lines, in template order.

    The header, the GENERATED CURSOR note and every line from ``schema_version:`` through the
    last child of ``counts:`` are generated. ``tail`` is emitted verbatim.

    D2: a field whose value is a block-scalar indicator OWNS its indented continuation lines,
    which are re-emitted immediately below it, verbatim.
    """
    blocks = field_blocks or {}
    lines = []

    def emit(key, value):
        lines.append("%s: %s" % (key, value))
        lines.extend(blocks.get(key, ()))

    lines.append("<!-- ash_save %s | save#%s | %s | written by update_state.py -->"
                 % (SCHEMA_VERSION, save, ts))
    lines.append("# %s - state" % project)
    lines.append("")
    lines.extend(GENERATED_NOTE.split("\n"))
    lines.append("")
    lines.append("schema_version: %s" % SCHEMA_VERSION)
    emit("project", project)
    emit("phase", fields["phase"])
    # R-K: column 0, lowercase, no space before the colon, BARE INTEGER, nothing else.
    lines.append("save: %d" % save)
    lines.append("last_save_ts: %s" % ts)
    emit("status", fields["status"])
    emit("goal", fields["goal"])
    emit("active_track", fields["active_track"])
    emit("repo_root", fields["repo_root"])
    emit("task_list_id", fields["task_list_id"])
    emit("hot_state", fields["hot_state"])
    emit("backlog_order", fields["backlog_order"])
    # ABSENT IS A STATE. An absent high-water mark emits NO line -- not `task_id_high_water: 0`,
    # and not a bare key with an empty value. A start state and a used-up-zero-ids state must
    # stay distinguishable in the file, because the only consumer of this field is an id minter
    # and the cost of confusing the two is a REUSED id (F146).
    if fields.get(TASK_HIGH_WATER_KEY, ""):
        emit(TASK_HIGH_WATER_KEY, fields[TASK_HIGH_WATER_KEY])
    emit("no_demote_streak", fields["no_demote_streak"])
    lines.append("files:")
    lines.append("  manifest: %s" % files_block["manifest"])
    lines.append("  latest_checkpoint: %s" % files_block["latest_checkpoint"])
    lines.append("  archive: %s" % files_block["archive"])
    lines.append("counts:")
    for key in COUNT_KEYS:
        lines.append("  %s: %d" % (key, counts[key]))
    if tail:
        lines.append("")
        lines.extend(tail)
    return lines


COUNT_KEYS = ("hot_files", "checkpoints", "backlog_items", "open_questions", "active_instructions")

FILES_KEYS = ("manifest", "latest_checkpoint", "archive")

GENERATED_HEADER_RE = re.compile(r"^<!--\s*ash_save\b.*-->\s*$")
GENERATED_TITLE_RE = re.compile(r"^#\s+\S.*\s-\sstate\s*$")


def generated_header_line(text):
    """True for a line the header renderer reproduces from scratch (comment / title / note)."""
    return bool(GENERATED_HEADER_RE.match(text) or GENERATED_TITLE_RE.match(text)
                or text in GENERATED_NOTE.split("\n"))


def recomputed_line(text):
    """True for an input line the tool deliberately RECOMPUTES rather than preserves."""
    if generated_header_line(text):
        return True
    m = TOP_FIELD_RE.match(text)
    if m is not None and m.group("key") in TOP_KEYS:
        return True
    c = CHILD_FIELD_RE.match(text)
    if c is not None and c.group("key") in (set(COUNT_KEYS) | set(FILES_KEYS)):
        return True
    return bool(BACKLOG_IDS_VALUE_RE.match(text))


def accounting_defects(st, out_lines, accounted, limit=8):
    """D8. THE structural guard: compare what was PARSED against what was READ.

    Every non-blank input line must be exactly one of
      (a) re-emitted verbatim in the output,
      (b) a field the tool deliberately recomputed -- and, F5, only when the line stood in the
          region the renderer actually regenerates (the header lines above the structured block,
          plus the block itself). The TAIL is preserved verbatim, so a field-shaped line down
          there is prose and gets no exemption, or
      (c) named in a warning (the caller appends those lines to ``accounted``).
    A line that is none of the three is a DEFECT and the tool says so instead of exiting 0.

    This exists because D1, D2 and D5 are ONE failure wearing three masks - content silently
    not making the round trip - and four separate patches do not stop the fifth instance.
    Run against the pre-fix code it fires on all three of the original defects without knowing
    what a block scalar, a prose field or a marker region is. Blank lines are exempt (layout,
    not content); terminators are covered by the separate CRLF assertions.

    WHAT IT DOES NOT COVER, stated because the previous wording overstated it and a docstring
    that oversells a guard is this project's own signature failure. ``recomputed_line`` is a
    SHAPE test, so before F5 it exempted a line ANYWHERE in the file that merely LOOKED like
    something the renderer emits. MEASURED 2026-08-03 by dropping each shape from the rendered
    output and asking this function: six of seven were MISSED - ``  hot_files: 42``,
    ``  latest_checkpoint: ...``, ``backlog_entry_ids: [X9, X8]``, a line of the GENERATED-CURSOR
    note, ``# another project - state`` and an ``<!-- ash_save ... -->`` header quoted inside
    prose - and end to end 170 bytes left the file at exit 0 with none of them named.
    F5 closes that by scoping the shape exemption to the regenerated region: in the TAIL, a
    field-shaped or header-shaped line is PROSE and must survive like any other prose. What
    remains uncovered is narrow and named: a line that is an EXACT DUPLICATE of one the renderer
    also emits. The duplicate is indistinguishable from the real one by any positional or textual
    test, ANYWHERE in the file -- an earlier wording of this paragraph said "standing inside the
    block", which understated it, and two reviewers measured that. It is not a loss path: the
    renderer emits the tail verbatim, so a duplicate in the tail is written back and never
    reaches this guard; and a duplicate inside the block is what D1's first-occurrence rule
    already warns about by name. A SECOND residual, also not a loss path: exemption (b) is
    granted by SHAPE, so a PRESERVED field line (goal / phase / status / active_track / repo_root
    / task_list_id / hot_state / backlog_order / task_id_high_water / no_demote_streak)
    dropped from the block
    would be missed too. No input can trigger it -- only a future render_state bug could -- which
    makes it defence-in-depth that is absent rather than a hole reachable today.
    """
    if not st["exists"]:
        return []
    # F5: the shape exemption is scoped to the region the renderer actually regenerates -- the
    # header lines above the block, plus the block itself. `block_end` is the boundary; the TAIL
    # is preserved verbatim, so a field-shaped or header-shaped line down there is PROSE.
    #
    # ONE named carve-out, and it is R-J: the `backlog_entry_ids:` value line lives in the tail and
    # is the single tail line this tool rewrites. It earns the exemption only INSIDE the one
    # well-formed region -- between its BEGIN and END markers -- so a `backlog_entry_ids:`-shaped
    # line loose in prose is still prose and must survive. That distinction is what makes this a
    # carve-out rather than a hole: it was reviewer-measured miss #3 (`backlog_entry_ids: [X9, X8]`
    # dropped and not named), and an unconditional exemption would re-open it.
    block_hi = st.get("block_end", 0)
    _b, _e = file_marker_counts(st["rows"])
    region_lo, region_hi = (_b[0], _e[0]) if (len(_b) == 1 and len(_e) == 1
                                              and _b[0] < _e[0]) else (None, None)
    remaining = {}
    generated_region = {}
    for row in st["rows"]:
        text = row[1]
        if not text.strip():
            continue
        remaining[text] = remaining.get(text, 0) + 1
        if row[0] <= block_hi:
            generated_region[text] = True
        elif (region_lo is not None and region_lo < row[0] < region_hi
                and BACKLOG_IDS_VALUE_RE.match(text)):
            generated_region[text] = True
    for text in list(out_lines) + list(accounted):
        if remaining.get(text):
            remaining[text] -= 1
    lost = []
    for text, count in remaining.items():
        if count <= 0 or (generated_region.get(text) and recomputed_line(text)):
            continue
        lost.extend([text] * count)
    if not lost:
        return []
    lost.sort()
    shown = ", ".join(repr(t[:60]) for t in lost[:limit])
    more = "" if len(lost) <= limit else " (+%d more)" % (len(lost) - limit)
    return ["accounting: %d input line(s) of state.md are neither re-emitted verbatim, nor a "
            "field this tool recomputes, nor named in any warning above. They would be lost "
            "silently, which is the D1/D2/D5 failure shape: %s%s" % (len(lost), shown, more)]


COUNT_SOURCE = {
    "hot_files": "the current ten-name hot set (or nine in a legacy pre-dual-file folder)",
    "checkpoints": "checkpoints/",
    "backlog_items": "backlog.md",
    "open_questions": "open_questions.md",
    "active_instructions": "active.md",
}

ENTRY_SOURCES = (
    ("backlog_items", "backlog.md"),
    ("open_questions", "open_questions.md"),
    ("active_instructions", "active.md"),
)


def _pick(mapping, names, default=None):
    """First present key among `names`. Addendum A §1 transcribes heading_map's dict keys as
    {"line","level","byte_start",...}; measured 2026-08-03 that raised KeyError: 'line', so the
    transcription is not exact. Rather than inspect the sibling (which the addendum forbids)
    this accepts any of the plausible spellings and degrades to `default`."""
    for name in names:
        if name in mapping:
            return mapping[name]
    return default


_H_LINE = ("line", "lineno", "line_no", "start_line", "lineno_start")
_H_LEVEL = ("level", "depth", "hashes")
_H_BYTE = ("byte_start", "start", "byte", "offset", "bstart")


def backlog_ranges(rows):
    """Backlog spans as BOTH (line_lo, line_hi) and (byte_lo, byte_hi). Fence-aware.

    Two coordinate spaces because heading_map's key names are not reliably known here; a
    heading is excluded if EITHER space says it is inside. An unterminated BEGIN covers to
    EOF: spec S7.2 R1 excludes the backlog from rows 2/2b, so the safe reading of a broken
    marker is "still excluded", never "silently included".
    """
    lines = []
    bytes_ = []
    start = None
    for lineno, text, bstart, bend, in_fence in rows:
        if in_fence:
            continue
        if BACKLOG_BEGIN_RE.match(text):
            if start is None:
                start = (lineno, bstart)
        elif BACKLOG_END_RE.match(text):
            if start is not None:
                lines.append((start[0], lineno))
                bytes_.append((start[1], bend))
                start = None
    if start is not None:
        lines.append((start[0], None))
        bytes_.append((start[1], None))
    return lines, bytes_


def _in_span(value, spans):
    if value is None:
        return False
    for lo, hi in spans:
        if value >= lo and (hi is None or value <= hi):
            return True
    return False


def scan_entry_ids(path, rel, families, exclude_backlog=True):
    """-> (ids, entries, open_fence_line, exists).

    TWO NUMBERS, BECAUSE THEY ANSWER TWO DIFFERENT QUESTIONS, AND CONFLATING THEM HID EVERY
    MIGRATED FOLDER'S WORK.

    `ids`     -- id-bearing level-2 headings. This is the ROW-2b BASELINE and it must stay ids:
                 an entry with no id cannot be tracked across saves, so a heading without one
                 gives the next save nothing to compare against.
    `entries` -- how many entries the file HOLDS, under the model the DRAIN uses: a heading at
                 level 2 or deeper, outside a code fence, not nested inside the entry counted
                 before it, outside the machine-managed backlog region.

    THE DEFECT THIS FIXES. `counts[key]` was `len(ids)`, so a file whose headings carry no
    RESOLVABLE id counted ZERO. A v3 folder's headings are prose titles (`## NOW - five gates
    block a ship`) and its entries often sit at level 3; the migration carries both through
    verbatim. So EVERY MIGRATED FOLDER ON THE FLEET reported `backlog_items: 0` while holding
    live work, and `restore` announced `Pending work: 0 live items` / `Next task: none` on
    every restore. **A restore that says there is nothing to do HIDES the work, and says it
    confidently on a folder where nothing is damaged.**

    MEASURED on this project at save#107 -- same file, same save: the drain said 8 items,
    `recall list` found 7 ids, this counter said 0. Three components, three answers, and the
    outlier was the one the announce reads. `example-project` reproduced it independently on a
    folder certified clean at `verify --pass2` exit 0 / 45 assertions.

    THE ENTRY MODEL IS THE DRAIN'S, NOT A NEW ONE. `restore`'s parked count already uses it
    and the drain moves by it. A third model here is what produced the disagreement, so this does
    not add a fourth -- it adopts the one the engine acts on.
    """
    if not path.is_file():
        return [], 0, None, False
    data, rows, open_fence = recall.load_doc(path)
    line_spans, byte_spans = backlog_ranges(rows) if exclude_backlog else ([], [])
    ids = []
    for head in recall.heading_map(rel, data, rows, families):
        ident = _pick(head, ("id",))
        if not ident:
            continue
        level = _pick(head, _H_LEVEL)
        if level is not None and level != 2:
            continue
        if exclude_backlog and (_in_span(_pick(head, _H_LINE), line_spans)
                                or _in_span(_pick(head, _H_BYTE), byte_spans)):
            continue
        ids.append(ident)

    entries = 0
    shallowest = None
    for (lineno, text, _s, _e, in_fence) in rows:
        if in_fence:
            continue
        m = recall.citemap.HEADING_RE.match(text)
        if m is None:
            continue
        level = len(m.group(1))
        if level < 2:
            continue
        if shallowest is not None and level > shallowest:
            continue            # starts inside the entry counted above: BODY, not an entry
        if exclude_backlog and _in_span(lineno, line_spans):
            continue
        shallowest = level
        entries += 1
    return ids, entries, open_fence, True


_CHECKPOINT_NAME_RE = re.compile(r"^(checkpoint-\d{4}-\d{2}-\d{2}-\d{6})(?:-([2-9]|[1-9]\d+))?\.md$")


def _checkpoint_order(name):
    """Keep legacy lexical order; order canonical same-second collisions numerically."""
    match = _CHECKPOINT_NAME_RE.fullmatch(name)
    if match is None:
        return name, 1
    return match.group(1) + ".md", int(match.group(2) or 1)


def list_checkpoints(root):
    """Non-empty top-level ``checkpoint-*.md``, with numeric same-second suffix ordering.

    Non-recursive on purpose: the curator's own discovery is a top-level listdir, and both
    hand-built drains (``checkpoints/_archive/`` and ``_archive/checkpoints/``) must not be
    counted as live checkpoints.
    """
    folder = root / "checkpoints"
    if not folder.is_dir():
        return []
    found = []
    for entry in folder.iterdir():
        name = entry.name
        if not (name.startswith("checkpoint-") and name.endswith(".md")):
            continue
        try:
            if entry.is_file() and entry.stat().st_size > 0:
                found.append(name)
        except OSError:
            continue
    found.sort(key=_checkpoint_order)
    return found


def compute_counts(root, families, warn, prior):
    """-> (counts, backlog_ids, latest_checkpoint, unreliable). F65 applied per key."""
    counts = {}
    unreliable = False

    # state.md counts as present: this invocation writes it, so counting it absent would make
    # hot_files disagree with the folder the moment the write lands.
    # F4: manifest.md too, and for the same reason one step out -- `regen_manifest.py` writes it
    # in the SAME save, immediately after this tool. Counting it from disk made `hot_files`
    # stale-by-one and NON-IDEMPOTENT: MEASURED 2026-08-03 on a scaffold with manifest.md
    # removed, this tool wrote 8 with 0 warnings while the sibling reported 9 and created the
    # file, and re-running at the same save# yielded 9. That is verbatim the defect
    # `regen_manifest.py`'s own docstring names and fixed for itself.
    # The claim is WEAKER here than for state.md -- this tool does not write manifest.md -- so
    # unlike state.md the absence is WARNED rather than silently absorbed.
    generated_by_this_save = ("state.md", "manifest.md")
    counts["hot_files"] = sum(1 for name in HOT_FILES
                              if name in generated_by_this_save or (root / name).is_file())
    if not (root / "manifest.md").is_file():
        warn("manifest.md is not on disk; counting it PRESENT in hot_files because "
             "regen_manifest.py writes it later in this same save. If that step does not run, "
             "hot_files is one too high until the next save.")

    checkpoints = list_checkpoints(root)
    counts["checkpoints"] = len(checkpoints)
    latest = ("checkpoints/%s" % checkpoints[-1]) if checkpoints else PLACEHOLDER_CHECKPOINT

    backlog_ids = []
    for key, rel in ENTRY_SOURCES:
        ids, entries, open_fence, exists = scan_entry_ids(root / rel, rel, families)
        if open_fence is not None:
            recall.warn_open_fence(rel, open_fence)
            unreliable = True
        counts[key] = entries
        if key == "backlog_items":
            backlog_ids = ids
        if not exists:
            warn("%s is missing from the hot set; %s recorded as 0" % (rel, key))
        # An entry the drain will move but `recall get` can never fetch. NOT an error and
        # never an abort -- the drain moves identless entries deliberately -- but the operator is
        # the only one who can act on it, and after the move a grep is all that is left.
        #
        # THE WARNING NAMES THE OBSERVATION AND NOT A CAUSE, BECAUSE IT CANNOT KNOW THE CAUSE, AND
        # THE FIRST VERSION OF IT GOT THAT WRONG. It read "Give an entry an id" -- which is right
        # when the heading genuinely carries none, and WRONG in the other case: a heading may
        # carry a perfectly good id from a family `citemap` does not have registered. Measured on
        # a peer's folder within an hour of this warning shipping: `## OQ-MM-5:` and `## OQ-MM-9:`
        # resolve to nothing, not because they lack ids but because the `OQ` family is `OQ(\d+)`
        # -- digits must follow immediately -- and `OQ-MM-` is registered nowhere.
        # Telling that operator to "give an entry an id" sends them to fix something that is not
        # broken. So: state what was measured, name BOTH causes, and let them look.
        #
        # THE SECOND CAUSE NOW HAS A REMEDY AND THE WARNING NAMES IT. It did not when this text
        # was first written -- `citemap` accepted `--family NAME=REGEX` and nothing threaded a
        # per-project family list into this tool, so the honest advice ended at "look at a
        # heading". `.ash-id-families` is read at the top of `regenerate` now, so the operator can
        # act rather than only understand. A warning that names a diagnosis and no fix is a
        # warning the same operator reads every save and does nothing about.
        elif entries > len(ids):
            warn("%s holds %d entr%s but %d resolve%s to a known id family; %s counts the "
                 "entries. TWO CAUSES look identical here and the fix differs: the heading may "
                 "carry no id at all, or it may carry one from a family citemap does not have "
                 "registered (`OQ` is `OQ(\\d+)`, so `OQ-MM-5` matches nothing). Look at a "
                 "heading before acting. If the id is real, declare its family in `%s` at the "
                 "project root -- one `NAME=REGEX` per line, e.g. `OQ-MM=OQ-MM-(\\d+)` -- and it "
                 "resolves from the next save on. Either way the %d unresolved entr%s still drain "
                 "and keep their text, but `recall get` cannot fetch them once cold -- only "
                 "grep."
                 % (rel, entries, "y" if entries == 1 else "ies", len(ids),
                    "s" if len(ids) == 1 else "", key, citemap.DECLARED_FAMILIES_FILE,
                    entries - len(ids), "y does" if entries - len(ids) == 1 else "ies do"))

    # F65: a computed 0 against a recorded >0 is a parser failure far more often than a real
    # emptying. Name the file and BOTH numbers, and write the computed value anyway.
    for key in COUNT_KEYS:
        try:
            was = int(prior.get(key, "") or "-1")
        except (TypeError, ValueError):
            was = -1
        if counts[key] == 0 and was > 0:
            warn("count %s computed 0 but the previous state.md recorded %d (source: %s). "
                 "F65: writing the computed 0, NOT the prior value - verify the parser."
                 % (key, was, COUNT_SOURCE[key]))
    return counts, backlog_ids, latest, unreliable




def file_marker_counts(rows):
    """BACKLOG-ENTRY-IDS marker linenos over the FILE AS READ, outside code fences.

    D5. Counting them in the possibly-truncated TAIL is how the region warning became
    affirmatively false: once D1's truncation had deleted the region, the tail held zero of
    each and the tool reported "found 0 BEGIN and 0 END marker(s) ... Region left
    byte-identical" about a region THAT RUN had just deleted. The one diagnostic a reader
    would trust stated the opposite of what happened (brief §8, F130).
    """
    begins = [r[0] for r in rows if not r[4] and BACKLOG_IDS_BEGIN_RE.match(r[1])]
    ends = [r[0] for r in rows if not r[4] and BACKLOG_IDS_END_RE.match(r[1])]
    return begins, ends


def region_unchanged(rows, out_lines):
    """-> True / False / None. None means the input had no marker to preserve.

    D5. The "left unchanged" claim is ASSERTED against the input, never inferred from a
    control-flow branch that did not write. Line-wise rather than byte-wise so a
    mixed-terminator input cannot produce a false negative; content is what the claim is
    about.
    """
    idx = [k for k, r in enumerate(rows)
           if not r[4] and (BACKLOG_IDS_BEGIN_RE.match(r[1]) or BACKLOG_IDS_END_RE.match(r[1]))]
    if not idx:
        return None
    want = [rows[k][1] for k in range(idx[0], idx[-1] + 1)]
    span = len(want)
    for start in range(0, len(out_lines) - span + 1):
        if out_lines[start:start + span] == want:
            return True
    return False


def rewrite_backlog_ids(tail, tail_fence, backlog_ids, warn, file_begins=None, file_ends=None):
    """RULING R-J. Replace the value line inside the one well-formed BACKLOG-ENTRY-IDS region.

    -> (new_tail, status) where status is one of written / missing / duplicated / inverted /
    no-value. Every non-``written`` status leaves the region unchanged: R-J rule 1 says
    do not repair and do not append a second region, and rule 2 says PRESENT-and-EMPTY (``[]``)
    and ABSENT mean opposite things to demote.py - collapsing them would turn "no baseline,
    row 2b is a no-op and says so" into "the baseline is empty, everything is a silent drop".

    D5: the marker counts quoted in every warning below come from the FILE AS READ
    (``file_begins``/``file_ends``), not from the tail this function was handed.
    """
    pairs = list(zip(tail, tail_fence))
    begins = [i for i, (t, f) in enumerate(pairs) if not f and BACKLOG_IDS_BEGIN_RE.match(t)]
    ends = [i for i, (t, f) in enumerate(pairs) if not f and BACKLOG_IDS_END_RE.match(t)]
    fb = list(file_begins) if file_begins is not None else []
    fe = list(file_ends) if file_ends is not None else []
    nb = len(fb) if file_begins is not None else len(begins)
    ne = len(fe) if file_ends is not None else len(ends)

    # R-J rule 1 is a statement about state.md, not about a substring of it, so the
    # well-formedness DECISION is taken on the file's own marker count (D5). Taking it on the
    # tail's meant a second region above the structured block was invisible and the writer
    # silently picked one of the two.
    if nb != 1 or ne != 1:
        warn("BACKLOG-ENTRY-IDS region not writable: the file as read holds %d BEGIN and %d END "
             "marker(s) outside code fences (need exactly 1 of each); the preserved tail "
             "holds %d and %d. No line in the region is being rewritten; not repaired, no "
             "second region appended (R-J rule 1)%s."
             % (nb, ne, len(begins), len(ends),
                "; demote.py row 2b will read 'no baseline' and no-op"
                if (nb == 0 or ne == 0) else ""))
        return list(tail), ("missing" if (nb == 0 or ne == 0) else "duplicated")
    if fb and fe and fe[0] <= fb[0]:
        warn("BACKLOG-ENTRY-IDS region not writable: END at state.md line %d precedes BEGIN at "
             "line %d (inverted). No line in the region is being rewritten (R-J rule 1)."
             % (fe[0], fb[0]))
        return list(tail), "inverted"
    if len(begins) != 1 or len(ends) != 1:
        warn("BACKLOG-ENTRY-IDS region not writable: the one region in state.md is not inside "
             "the preserved tail (BEGIN is at line %s, at or above the structured block). "
             "No line in the region is being rewritten (R-J rule 1)."
             % (fb[0] if fb else "?"))
        return list(tail), "misplaced"
    begin, end = begins[0], ends[0]
    if end <= begin:
        warn("BACKLOG-ENTRY-IDS region not writable: END at tail line %d precedes BEGIN at %d "
             "(inverted). No line in the region is being rewritten (R-J rule 1)."
             % (end + 1, begin + 1))
        return list(tail), "inverted"

    value_idx = [i for i in range(begin + 1, end) if BACKLOG_IDS_VALUE_RE.match(tail[i])]
    if len(value_idx) != 1:
        warn("BACKLOG-ENTRY-IDS region not writable: %d 'backlog_entry_ids:' line(s) strictly "
             "between the markers (need exactly 1). No line in the region is being rewritten "
             "(R-J rule 1)." % len(value_idx))
        return list(tail), "no-value"

    new_tail = list(tail)
    # R-J rule 3: ids only, comma-space separated, in backlog.md order. No paths, no titles.
    new_tail[value_idx[0]] = "backlog_entry_ids: [%s]" % ", ".join(backlog_ids)
    return new_tail, "written"


def high_water_int(raw):
    """The integer a high-water value denotes, or None when it is not a bare count."""
    val = (raw or "").strip()
    return int(val) if TASK_HIGH_WATER_RE.match(val) else None


def merge_task_high_water(prior, candidate):
    """The task-id high-water mark to carry forward. NEVER SCANNED, NEVER DECREASING.

    NEVER SCANNED. This tool derives no value here. It does not walk ``backlog.md`` headings and it
    does not read the task store; both are counted elsewhere in this same run and NEITHER is
    admissible as a source. The reason is recorded in the design: the verb that already computes
    high-water marks (``recall frontier``) is forbidden from reading ``_archive/``, so once
    ``TASK21`` is completed and archived it is invisible to any scan built on it -- the next save
    mints ``TASK21`` again and two different pieces of work sit at one permanent address. The
    only input is what the previous file said, plus a candidate the id minter hands over.

    NEVER DECREASING. A candidate lower than the recorded value is IGNORED, silently and by
    design: this is a monotone counter, and the only way a lower number can be right is if a
    reused id is right, which it never is.

    ABSENT IS NOT ZERO, and this is the arm with a measured failure behind it. F146: a counter in
    this project's own record read 0 in ALL ELEVEN committed versions of the file, because a
    MISSING field was read as zero and re-written as zero, and a start state was therefore
    indistinguishable from a real count of zero. So absent-in / absent-out: the empty string is
    the absent marker end to end, and `render_state` emits no line for it.

    A value this function cannot compare -- prose, a range, a block-scalar indicator -- is
    carried forward unchanged rather than replaced. Preserving something unparseable keeps the
    file honest; substituting a guess for it is how the id gets reused.
    """
    prior_s = (prior or "").strip()
    cand_s = (candidate or "").strip()
    if not cand_s:
        return prior_s
    if not prior_s:
        return cand_s
    p, c = high_water_int(prior_s), high_water_int(cand_s)
    if p is None or c is None:
        return prior_s
    return cand_s if c > p else prior_s


def _resolve_preserved(prior, task_high_water=None):
    """Preserved scalars: the existing value, else the scaffolder default. Never computed.

    ``task_high_water`` is the id minter's candidate. It is the ONE preserved field with no
    scaffolder default -- absent stays absent -- and the one that merges rather than overwrites.
    """
    fields = {}
    for key, default in PRESERVED_FIELDS:
        val = prior.get(key, "")
        if key == TASK_HIGH_WATER_KEY:
            fields[key] = merge_task_high_water(val, task_high_water)
            continue
        fields[key] = val if val != "" else default
    return fields


def regenerate(root, save_num, checkpoint_ts, out, _warn=None, task_high_water=None):
    """Rewrite ``<root>/state.md``. Capabilities (a)+(b) landed.

    ``task_high_water`` is the seam for whatever mints task ids: pass a candidate and it may
    RAISE the recorded mark, never lower it. Omit it -- as every caller does today -- and the
    recorded value is carried through untouched. This tool never derives one itself.

    """
    warnings = []
    accounted = []      # input lines deliberately dropped, each NAMED in a warning (D8 case c)

    def warn(msg):
        warnings.append(msg)
        sys.stderr.write("warning: %s\n" % msg)

    state_path = root / "state.md"
    st = parse_state(state_path)

    # Bind the actual structured block from which the rewrite is derived. A declaration
    # elsewhere in the file is diagnosed, never copied over or silently converted.
    incoming_schema = st["fields"].get("schema_version", "")
    if incoming_schema != SCHEMA_VERSION:
        _whole_exists, _whole_declared = read_declared_schema(root)
        sys.stderr.write(migration_refusal(root, "update_state.py", st["exists"],
                                           incoming_schema or None,
                                           scope=SCOPE_BLOCK,
                                           elsewhere=_whole_declared))
        return EXIT_NOT_MIGRATED

    unreliable = False
    if st["open_fence"] is not None:
        recall.warn_open_fence("state.md", st["open_fence"])
        unreliable = True

    project = st["fields"].get("project", "") or root.name
    fields = _resolve_preserved(st["fields"], task_high_water)

    # D1: first occurrence wins. A later match used to overwrite the first, which is how a
    # column-0 prose line became the recorded value of a preserved field.
    for key, first, second in st["dupes"]:
        warn("state.md declares %s more than once (D1): KEEPING the first value %r and "
             "IGNORING %r. One of the two is prose, not a field." % (key, first, second))

    # The v4 declaration check above precedes diagnostics and every possible write.

    if save_num is None:
        try:
            save = int(st["fields"].get("save", "0"))
        except (TypeError, ValueError):
            warn("state.md save: is not an integer (%r); recording 0"
                 % st["fields"].get("save"))
            save = 0
    else:
        save = save_num
    # `--checkpoint-ts` preserves on omit, exactly as `--save-num` does above. A frozen `save:`
    # is caught by the curator's advancing check; a frozen `last_save_ts` is caught by nothing.
    # WARNING ONLY -- never an abort, nothing may block a save -- and the preserve BEHAVIOUR is
    # deliberately unchanged: omitting the flag still yields the old value, just not silently.
    if checkpoint_ts is not None:
        ts = checkpoint_ts
    else:
        ts = st["fields"].get("last_save_ts", "")
        if ts and save_num is not None:
            # Emitted BEFORE the write, so it may describe only what was REQUESTED, never a
            # completed effect. `%s` not `%d`: the path that exists to WARN must not raise.
            # This warning is emitted BEFORE the cursor is written, so it may only describe what
            # was REQUESTED -- never a completed effect. A later step can still refuse the write
            # (the writer rejects a non-numeric save number), and a warning announcing an advance
            # that then did not happen is a false report from a run that failed.
            warn("--checkpoint-ts was not passed, so last_save_ts is being PRESERVED at %r "
                 "rather than advanced, while a NEW save number (%s) was REQUESTED for this "
                 "run. If that number is an ADVANCE on the one already recorded and the write "
                 "completes, the cursor reads as saved NOW and stamped THEN, and every "
                 "watermark this run writes carries the stale value. Nothing "
                 "downstream catches THAT shape: the out-of-band curator opens state.md in "
                 "exactly one place and reads only the line beginning `save:`, which an "
                 "advancing number satisfies -- while restore's staleness check keys on "
                 "last_save_ts. Pass --checkpoint-ts with the checkpoint's own timestamp "
                 "(save SKILL.md step 7)." % (ts, save))
        elif ts:
            # NEITHER flag passed. Say ONLY that nothing new was requested. Do NOT call the pair
            # self-consistent (an earlier ts-omitting run leaves a mismatch this run carries
            # forward) and do NOT say `save:` is always carried (an unparsable one becomes 0).
            warn("neither --save-num nor --checkpoint-ts was passed, so this run REQUESTS no "
                 "new save number and no new timestamp -- whatever the cursor already holds is "
                 "what is carried, last_save_ts being %r. (One exception, warned about "
                 "separately above: an unparsable stored `save:` is NOT carried, it is "
                 "normalised to 0.) That is not a guarantee the two "
                 "agree: an earlier run that omitted only --checkpoint-ts leaves an advanced "
                 "`save:` beside a stale timestamp, and this run carries such a mismatch "
                 "forward rather than creating or repairing one. If you meant to record a "
                 "save, pass BOTH flags (save SKILL.md step 7)." % (ts,))
        else:
            warn("--checkpoint-ts was not passed and state.md carries no last_save_ts to "
                 "preserve, so that field and every watermark this run writes are being left "
                 "EMPTY. Pass --checkpoint-ts with the checkpoint's own timestamp "
                 "(save SKILL.md step 7).")

    # The project's OWN id families, loaded from `.ash-id-families` at the root. THIS is the call
    # site that matters: `--family` reaches 2 of the 7 tools and neither is on the save path, so
    # before this a project using an unregistered family counted zero resolvable ids here, forever,
    # at exit 0. Problems are WARNED and never raised -- step 7d is the only abort in this
    # pipeline and a typo in an optional config file may not join it.
    families, fam_problems = citemap.project_families(root)
    for problem in fam_problems:
        warn(problem)
    counts, backlog_ids, latest, counts_unreliable = compute_counts(
        root, families, warn, st["counts"])
    unreliable = unreliable or counts_unreliable

    files_block = {
        "manifest": "manifest.md",
        "latest_checkpoint": latest,
        "archive": "_archive/",
    }

    # D8 case (c). Two classes of input line are deliberately dropped by the v4 template; both
    # are NAMED here so the accounting guard below can discharge them, and so a reader learns
    # what left the file. Silence is what this whole change is about.
    header_defects = []
    for text in st["header"]:
        if not text.strip() or generated_header_line(text):
            continue
        if (BACKLOG_IDS_BEGIN_RE.match(text) or BACKLOG_IDS_END_RE.match(text)
                or BACKLOG_IDS_VALUE_RE.match(text)):
            # R-J rule 2: NEVER delete the region. A marker above the structured block would
            # be dropped by the header renderer, so this is a defect, not a warning.
            header_defects.append(
                "state.md carries a BACKLOG-ENTRY-IDS line ABOVE the structured block, where the "
                "generated header would drop it. R-J rule 2 forbids deleting the region "
                "(PRESENT-and-EMPTY and ABSENT mean opposite things to demote.py): %r"
                % text[:70])
            continue
        warn("state.md carries content ABOVE the structured block that the generated header "
             "does not reproduce. It is DROPPED: %r" % text[:70])
        accounted.append(text)
    for section, ckey, text in st["child_lines"]:
        allowed = COUNT_KEYS if section == "counts" else FILES_KEYS
        if ckey in allowed:
            continue
        warn("state.md carries %s.%s, which is not part of the v4 %s block (the five counts "
             "and the three files entries are the whole set - there is no decisions count and "
             "no roadmap_* count). It is DROPPED: %r" % (section, ckey, section, text.strip()))
        accounted.append(text)

    tail = list(st["tail"])
    tail_fence = list(st["tail_fence"])
    file_begins, file_ends = file_marker_counts(st["rows"])
    tail, ids_status = rewrite_backlog_ids(tail, tail_fence, backlog_ids, warn,
                                        file_begins, file_ends)
    # D6 / R-K. Before D1 this loop was DEAD CODE: parse_state had already consumed every
    # unfenced column-0 `save:` line out of the tail, so it fired only on a FENCED one (where
    # it cannot help - the curator still reads the right number) and was SILENT on the
    # unfenced one that had just destroyed 1419 bytes. With the block contiguous the tail
    # keeps those lines and this is a live guard. Generalised to every top-level key: a tail
    # line shaped like a field is prose, and saying so is also how the D8 accounting guard
    # discharges it as "named in a warning".
    for line, fenced in zip(tail, tail_fence):
        m = TOP_FIELD_RE.match(line)
        if m is None or m.group("key") not in TOP_KEYS:
            continue
        where = " INSIDE A CODE FENCE" if fenced else ""
        if m.group("key") == "save":
            warn("preserved prose carries a second column-0 'save:' line%s - ruling R-K "
                 "hazard. The curator's reader is fence-blind and returns on the FIRST "
                 "save:-prefixed line, which is the generated one above, so this save is "
                 "read correctly; no other reader is guaranteed to do the same: %r"
                 % (where, line[:70]))
        else:
            warn("preserved prose carries a column-0 line shaped like the top-level field "
                 "%r%s. It is kept verbatim as prose and is NOT read as a field (D1): %r"
                 % (m.group("key"), where, line[:70]))


    # D2: block-scalar bodies. `save`, `last_save_ts` and `schema_version` are regenerated and
    # single-token by contract, so a body attached to one is dropped -- but NAMED, never
    # silently, because a silent drop is exactly the defect this fix exists to close.
    field_blocks = {}
    for key, body in st["field_blocks"].items():
        if key in BLOCK_SCALAR_KEYS:
            field_blocks[key] = body
        elif body:
            warn("field %r carries a %d-line block scalar, but %r is regenerated every save "
                 "and must be a single token (R-K/R-L). Those %d line(s) are DROPPED: %r"
                 % (key, len(body), key, len(body), body[0][:60]))
            accounted.extend(body)

    lines = render_state(project, save, ts, fields, files_block, counts, tail, field_blocks)

    # D5. Every non-`written` status above CLAIMS the region was left alone. Prove it against
    # the input rather than inferring it from the branch that did not write.
    defects = []
    defects.extend(header_defects)
    if ids_status != "written":
        verdict = region_unchanged(st["rows"], lines)
        if verdict is False:
            defects.append(
                "the BACKLOG-ENTRY-IDS region was reported %r and therefore unchanged, but the "
                "marker block present in the INPUT does not appear intact in the bytes about "
                "to be written. That claim would be FALSE (D5/F130)." % ids_status)

    # D8: the single accounting check. Runs LAST, over the bytes about to be written.
    defects.extend(accounting_defects(st, lines, accounted))

    term = st["term"] if st["exists"] else b"\n"
    new_bytes = join_lines(lines, term)
    if st["bom"]:                                     # D7: it arrived with a BOM, it leaves
        new_bytes = BOM_BYTES + new_bytes             # with one.
    changed = new_bytes != st["data"]
    written = not defects
    if defects:
        for msg in defects:
            warn("DEFECT, REFUSING TO WRITE: %s" % msg)
    else:
        write_bytes(state_path, new_bytes)

    out.write("STATE-UPDATE:BEGIN\n")
    out.write("project: %s\n" % project)
    out.write("save: %d\n" % save)
    # Three-way, matching `dominant_terminator`'s three return values and `regen_manifest`'s own
    # label. It was two-way, so a CR-only file was written back CORRECTLY (F7a works) and then
    # reported as `terminator: lf` -- a false statement in the machine-readable block, which is
    # the one place a caller is entitled to trust.
    out.write("terminator: %s\n"
              % {b"\r\n": "crlf", b"\r": "cr"}.get(term, "lf"))
    out.write("bom: %s\n" % ("yes" if st["bom"] else "no"))
    out.write("written: %s\n" % ("yes" if written else "no"))
    out.write("changed: %s\n" % ("yes" if changed else "no"))
    for key in COUNT_KEYS:
        out.write("counts.%s: %d\n" % (key, counts[key]))
    out.write("backlog_entry_ids: [%s]\n" % ", ".join(backlog_ids))
    out.write("backlog_entry_ids_status: %s\n" % ids_status)
    out.write("defects: %d\n" % len(defects))
    out.write("warnings: %d\n" % len(warnings))
    out.write("STATE-UPDATE:END\n")
    if _warn is not None:
        _warn.extend(warnings)
    if defects:
        sys.stderr.write("error: state.md NOT written; %d defect(s) above. The file on disk "
                         "is untouched, so nothing was lost - fix the tool, then re-run.\n"
                         % len(defects))
        return EXIT_INTERNAL
    return recall.integrity_code(unreliable, EXIT_OK)


# ---------------------------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------------------------

_FIXTURE_STATE = """<!-- ash_save v4 | save#3 | 2026-08-01 10:00:00 UTC | written by update_state.py -->
# demo - state

GENERATED CURSOR. Rewritten by save every save; do not hand-edit. Exempt from the
declaration rules (spec S3.5) because nothing authors its entries.

schema_version: v4
project: demo
phase: implementation
save: 3
last_save_ts: 2026-08-01 10:00:00 UTC
status: active
goal: ship the thing
active_track: build step 4
repo_root: /somewhere/demo
task_list_id: 901234
hot_state: armed
backlog_order: [B2, B1]
no_demote_streak: 4
files:
  manifest: manifest.md
  latest_checkpoint: (none yet)
  archive: _archive/
counts:
  hot_files: 9
  checkpoints: 2
  backlog_items: 5
  open_questions: 8
  active_instructions: 1

no_demote_streak counts consecutive saves in which step 6a moved nothing. It is the
instrument tier and P1 never touches it.

## Row-2b baseline

The ids below are the backlog.md entries as of the END of the previous save, after step 6a ran.
Row 2b compares this list against the current backlog.md.

<!-- BACKLOG-ENTRY-IDS:BEGIN -->
backlog_entry_ids: [M1, M4]
<!-- BACKLOG-ENTRY-IDS:END -->
"""


# Synthetic two-block-scalar and opaque-tail conservation fixture. It is not a saved session.
_FIXTURE_BLOCK_SCALARS_V3 = """<!-- ash_save | save#16 | 2000-01-01 10:00:00 UTC -->
schema_version: v3
project: synthetic-example
goal: |
  Preserve every line of this synthetic example's multi-line goal.
  The second line includes Unicode: a note → an archive entry.
  This line must not disappear when the v4 cursor is regenerated.
phase: implementation
save: 16
last_save_ts: 2000-01-01 10:00:00 UTC
status: active
active_track: |
  Build the synthetic example's selected behavior.
  Keep this second block independent of the goal above.
  A blank-looking value is not a reason to drop its continuation.
counts:
  decisions: 3
  findings: 4
  oq_open: 0
  oq_deferred: 0
  oq_resolved: 0
  pending: 0
  roadmap_done: 0
  roadmap_in_flight: 0
  roadmap_planned: 0
files:
  latest_checkpoint: checkpoints/checkpoint-2000-01-01-100000.md
  restart: restart-prompt.md
docs_last_touched: 2000-01-01 10:00:00 UTC (save#16)
tier1_tokens: 64
tier1_tokens_method: synthetic-fixture
maintenance_runs: 2
last_maintenance_save: 16
saves_since_maintenance: 0
maintenance_threshold: 7
"""


_FIXTURE_BACKLOG = """# plan

## M1 - first

body

## M4 - second

body

<!-- BACKLOG:BEGIN -->

## M9 - backlogged, must never enter the baseline

body

<!-- BACKLOG:END -->

## M7 - third

body
"""

_STATE_HEAD = """<!-- ash_save v4 | save#1 | old | written by update_state.py -->
# demo - state

schema_version: v4
project: demo
phase: design
save: 1
last_save_ts: old
status: active
goal: g
active_track: t
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
  hot_files: 4
  checkpoints: 1
  backlog_items: 3
  open_questions: 0
  active_instructions: 0

## Row-2b baseline

prose
"""

_REGION_OK = """
<!-- BACKLOG-ENTRY-IDS:BEGIN -->
backlog_entry_ids: []
<!-- BACKLOG-ENTRY-IDS:END -->
"""

_REGION_DUP = """
<!-- BACKLOG-ENTRY-IDS:BEGIN -->
backlog_entry_ids: []
<!-- BACKLOG-ENTRY-IDS:END -->

<!-- BACKLOG-ENTRY-IDS:BEGIN -->
backlog_entry_ids: []
<!-- BACKLOG-ENTRY-IDS:END -->
"""

_REGION_FENCED = """
```
<!-- BACKLOG-ENTRY-IDS:BEGIN -->
backlog_entry_ids: [DECOY]
<!-- BACKLOG-ENTRY-IDS:END -->
```

<!-- BACKLOG-ENTRY-IDS:BEGIN -->
backlog_entry_ids: []
<!-- BACKLOG-ENTRY-IDS:END -->
"""

_REGION_INVERTED = """
<!-- BACKLOG-ENTRY-IDS:END -->
backlog_entry_ids: []
<!-- BACKLOG-ENTRY-IDS:BEGIN -->
"""

# D1. Four column-0 prose lines, each of which used to be read as a top-level field, delete
# every line above itself and overwrite the real value. All four sit in the preserved tail.
_REGION_D1_TRAP = """
hot_state: is preserved byte-for-byte; P1 never writes a new value (spec S5).
goal: a sentence about the goal field, which is prose and not a field.
status: and this one is prose about status.
save: 99

<!-- BACKLOG-ENTRY-IDS:BEGIN -->
backlog_entry_ids: []
<!-- BACKLOG-ENTRY-IDS:END -->
"""


def _mkproj(tmp, name, region, plan=_FIXTURE_BACKLOG):
    """A project folder with backlog.md and a state.md whose tail carries `region`."""
    root = pathlib.Path(tmp) / name
    root.mkdir()
    _write_fixture(root, _STATE_HEAD + region, b"\n", "state.md")
    if plan is not None:
        _write_fixture(root, plan, b"\n", "backlog.md")
    return root


def _region_value(root):
    """The single `backlog_entry_ids:` value line outside any fence, or None / '<multi>'."""
    text = _read(pathlib.Path(root) / "state.md").decode("utf-8")
    hits = [l for l in text.split("\n") if BACKLOG_IDS_VALUE_RE.match(l)]
    if not hits:
        return None
    return hits[0] if len(hits) == 1 else "<multi:%d>" % len(hits)


def _write_fixture(root, text, term=b"\n", name="state.md"):
    """Write a fixture with an explicit terminator. Returns the path."""
    path = pathlib.Path(root) / name
    body = term.join(line.encode("utf-8") for line in text.split("\n"))
    write_bytes(path, body)
    return path


def _read(path):
    with open(str(path), "rb") as fh:
        return fh.read()


def _summary(text):
    """Parse the STATE-UPDATE:BEGIN/END report block into a dict.

    D9. The harness passed a ``_Sink()`` to ``main()``, so it NEVER parsed its own
    machine-readable report - and NINE mutants survived on exactly that hole: every field of
    the block could lie while the selftest stayed green (counts.* all 999, save off by 100,
    backlog_entry_ids [BOGUS], backlog_entry_ids_status deleted, project not preserved, ...).
    ``backlog_entry_ids_status`` is the ONLY way a caller learns the R-J region was not written,
    so an unparsed report is an unverified contract.
    """
    out = {}
    inside = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == "STATE-UPDATE:BEGIN":
            inside = True
            continue
        if stripped == "STATE-UPDATE:END":
            inside = False
            continue
        if inside and ":" in line:
            key, val = line.split(":", 1)
            out[key.strip()] = val.strip()
    return out


def _selftest():
    import tempfile
    import shutil
    import io
    checks = []

    def ck(label, got, want):
        checks.append((label, got, want, got == want))

    tmp = tempfile.mkdtemp(prefix="update_state_selftest_")
    try:
        ck("terminator/crlf", dominant_terminator(b"a\r\nb\r\n"), b"\r\n")
        ck("terminator/lf", dominant_terminator(b"a\nb\n"), b"\n")
        ck("usage/no-root", main([], _out=_Sink()), EXIT_USAGE)
        ck("usage/bad-root", main([str("--root"), str(pathlib.Path(tmp) / "nope")],
                                  _out=_Sink()), EXIT_USAGE)

        # --- D3 / R-K: --checkpoint-ts and --save-num are interpolated, so they are VALIDATED.
        d3 = pathlib.Path(tmp) / "d3"
        d3.mkdir()
        _write_fixture(d3, _FIXTURE_STATE, b"\n")
        d3_before = _read(d3 / "state.md")
        for label, extra in (("newline", ["--checkpoint-ts", "T\nsave: 999"]),
                             ("cr", ["--checkpoint-ts", "T\rsave: 999"]),
                             ("comment-close", ["--checkpoint-ts", "x --> y"]),
                             ("negative-save", ["--save-num=-5", "--checkpoint-ts", "T"])):
            ck("d3/%s-exit" % label,
               main(["--root", str(d3)] + extra, _out=_Sink()), EXIT_USAGE)
        ck("d3/fixture-nonempty", len(d3_before) > 400, True)
        ck("d3/nothing-written", _read(d3 / "state.md"), d3_before)
        # and the good value still passes
        ck("d3/good-ts-exit", main(["--root", str(d3), "--save-num", "5",
                                    "--checkpoint-ts", "2026-08-03 12:00:00 UTC"],
                                   _out=_Sink()), EXIT_OK)
        d3_text = _read(d3 / "state.md").decode("utf-8")
        ck("d3/one-save-line",
           len([l for l in d3_text.split("\n") if l.startswith("save:")]), 1)
        ck("d3/save-value", "\nsave: 5\n" in d3_text, True)

        # --- (a)+(b): parse, preserve, render, terminators ------------------------------
        lf_root = pathlib.Path(tmp) / "lf"
        lf_root.mkdir()
        _write_fixture(lf_root, _FIXTURE_STATE, b"\n")
        code = main(["--root", str(lf_root), "--save-num", "4",
                     "--checkpoint-ts", "2026-08-03 12:00:00 UTC"], _out=_Sink())
        ck("lf/exit", code, EXIT_OK)
        got = _read(lf_root / "state.md")
        ck("lf/no-crlf", got.count(b"\r\n"), 0)
        ck("lf/nonempty", got != b"", True)
        text = got.decode("utf-8")
        ck("lf/save-line", "\nsave: 4\n" in text, True)
        ck("lf/schema-v4", "\nschema_version: v4\n" in text, True)
        ck("lf/preserve-hot-state", "\nhot_state: armed\n" in text, True)
        ck("lf/preserve-streak", "\nno_demote_streak: 4\n" in text, True)
        ck("lf/preserve-backlog-order", "\nbacklog_order: [B2, B1]\n" in text, True)
        ck("lf/preserve-goal", "\ngoal: ship the thing\n" in text, True)
        ck("lf/preserve-phase", "\nphase: implementation\n" in text, True)
        ck("lf/preserve-repo-root", "\nrepo_root: /somewhere/demo\n" in text, True)
        ck("lf/preserve-task-list", "\ntask_list_id: 901234\n" in text, True)
        ck("lf/ts", "\nlast_save_ts: 2026-08-03 12:00:00 UTC\n" in text, True)
        ck("lf/tail-row2b", ROW2B_HEADING in text, True)
        ck("lf/tail-prose", "instrument tier and P1 never touches it." in text, True)
        ck("lf/region-markers", text.count(BACKLOG_IDS_BEGIN), 1)
        ck("lf/save-count", len([l for l in text.split("\n") if l.startswith("save:")]), 1)

        # --- E12: the task-id high-water mark -------------------------------------------
        # Unit arms first: the property lives in `merge_task_high_water`, not in the render.
        ck("hwm/absent-both", merge_task_high_water("", None), "")
        ck("hwm/absent-prior-adopts-candidate", merge_task_high_water("", "7"), "7")
        ck("hwm/present-preserved", merge_task_high_water("42", None), "42")
        ck("hwm/lower-candidate-ignored", merge_task_high_water("42", "7"), "42")
        ck("hwm/equal-candidate", merge_task_high_water("42", "42"), "42")
        # OFF ARM for the two above: a merge hard-wired to "return prior" passes both of them,
        # and would also make the field permanently unraisable. This is the arm that fails on it.
        ck("hwm/higher-candidate-raises", merge_task_high_water("42", "43"), "43")
        # ...and the comparison is on INTEGERS. "10" sorts BELOW "9" as a string, so a string
        # compare discards the raise every time the mark crosses a power of ten - the failure
        # would first appear at id 10 and then hide until id 100.
        ck("hwm/not-lexicographic", merge_task_high_water("9", "10"), "10")
        # NEVER SCANNED, stated as a test: nothing in the merge reads a file, so a value this
        # tool cannot compare is carried forward rather than replaced by a derived guess.
        ck("hwm/unparseable-candidate-kept", merge_task_high_water("42", "lots"), "42")
        ck("hwm/unparseable-prior-kept", merge_task_high_water("lots", "42"), "lots")

        # End to end. The fixture carries NO mark, so the rewritten file must carry none either
        # - not `task_id_high_water: 0`, not a bare key. ABSENT IS A STATE, and F146 is a
        # measured counter that read 0 in eleven committed versions for want of this arm.
        ck("hwm/absent-in-fixture", TASK_HIGH_WATER_KEY in _FIXTURE_STATE, False)
        ck("hwm/absent-stays-absent", TASK_HIGH_WATER_KEY in text, False)

        hw_root = pathlib.Path(tmp) / "hwm"
        hw_root.mkdir()
        _write_fixture(hw_root, _FIXTURE_STATE.replace(
            "backlog_order: [B2, B1]",
            "backlog_order: [B2, B1]\ntask_id_high_water: 42"), b"\n")
        ck("hwm/e2e-exit", main(["--root", str(hw_root), "--save-num", "5",
                                 "--checkpoint-ts", "T"], _out=_Sink()), EXIT_OK)
        hwtext = _read(hw_root / "state.md").decode("utf-8")
        ck("hwm/e2e-preserved", "\ntask_id_high_water: 42\n" in hwtext, True)
        ck("hwm/e2e-one-line",
           len([l for l in hwtext.split("\n") if l.startswith(TASK_HIGH_WATER_KEY)]), 1)
        # a lower candidate, through the real entry point, does not lower the recorded mark
        ck("hwm/e2e-lower-exit",
           regenerate(hw_root, 6, "T", _Sink(), _warn=[], task_high_water="7"), EXIT_OK)
        ck("hwm/e2e-lower-ignored",
           "\ntask_id_high_water: 42\n" in _read(hw_root / "state.md").decode("utf-8"), True)
        # and a higher one raises it: the OFF arm for the line above
        ck("hwm/e2e-higher-exit",
           regenerate(hw_root, 7, "T", _Sink(), _warn=[], task_high_water="99"), EXIT_OK)
        ck("hwm/e2e-higher-raises",
           "\ntask_id_high_water: 99\n" in _read(hw_root / "state.md").decode("utf-8"), True)
        # an absent mark plus a candidate ADOPTS it, end to end - this is how the field is born
        ck("hwm/e2e-born-exit",
           regenerate(lf_root, 5, "T", _Sink(), _warn=[], task_high_water="3"), EXIT_OK)
        ck("hwm/e2e-born",
           "\ntask_id_high_water: 3\n" in _read(lf_root / "state.md").decode("utf-8"), True)

        crlf_root = pathlib.Path(tmp) / "crlf"
        crlf_root.mkdir()
        src = _write_fixture(crlf_root, _FIXTURE_STATE, b"\r\n")
        before_crlf = _read(src).count(b"\r\n")
        ck("crlf/fixture-nonempty", before_crlf > 20, True)
        code = main(["--root", str(crlf_root), "--save-num", "9",
                     "--checkpoint-ts", "2026-08-03 12:00:00 UTC"], _out=_Sink())
        ck("crlf/exit", code, EXIT_OK)
        after = _read(crlf_root / "state.md")
        ck("crlf/lone-lf", after.count(b"\n") - after.count(b"\r\n"), 0)
        ck("crlf/still-crlf", after.count(b"\r\n") > 20, True)
        ck("crlf/save-line", b"\r\nsave: 9\r\n" in after, True)

        # A folder without a cursor refuses; only init_project creates fresh v4 state.
        fresh = pathlib.Path(tmp) / "fresh"
        fresh.mkdir()
        ck("gate/fresh-refuses",
           main(["--root", str(fresh), "--save-num", "1",
                 "--checkpoint-ts", "2026-08-03 12:00:00 UTC"], _out=_Sink()),
           EXIT_NOT_MIGRATED)
        ck("gate/fresh-wrote-nothing", (fresh / "state.md").exists(), False)
        write_bytes(fresh / "state.md", b"schema_version: v4\n")
        code = main(["--root", str(fresh), "--save-num", "1",
                     "--checkpoint-ts", "2000-01-01 10:00:00 UTC"], _out=_Sink())
        ck("fresh/exit", code, EXIT_OK)
        ftext = _read(fresh / "state.md").decode("utf-8")
        ck("fresh/project-from-basename", "\nproject: fresh\n" in ftext, True)
        ck("fresh/defaults", "\nhot_state: scaffolded\n" in ftext, True)
        # --- (c)+(e): counts, BACKLOG carve-out, and the region writer -------------------
        ok_root = _mkproj(tmp, "region_ok", _REGION_OK)
        ck("region/ok-exit", main(["--root", str(ok_root), "--save-num", "2",
                                   "--checkpoint-ts", "T"], _out=_Sink()), EXIT_OK)
        val = _region_value(ok_root)
        ck("region/ok-written", val, "backlog_entry_ids: [M1, M4, M7]")
        # OVER-FIRE arm: an id inside the BACKLOG region must never enter the baseline.
        ck("region/backlog-excluded", "M9" in (val or ""), False)
        # OFF arm: a writer that did nothing would leave the scaffolded `[]` behind.
        ck("region/off-arm", val == "backlog_entry_ids: []", False)
        otext = _read(ok_root / "state.md").decode("utf-8")
        ck("region/ok-marker-count", otext.count(BACKLOG_IDS_BEGIN), 1)
        ck("region/ok-prose-kept", "prose" in otext, True)
        ck("counts/backlog-items", "\n  backlog_items: 3\n" in otext, True)

        # ---- THE COUNT IS ENTRIES, THE BASELINE IS IDS, AND THEY ARE ALLOWED TO DIFFER.
        # The fixture is the shape a MIGRATED folder actually has, because that is where this
        # was found: prose section titles at level 2 that resolve to no id, real entries a level
        # deeper, and one ordinary id-bearing entry so the arm cannot pass by counting everything
        # or nothing. Before the fix this file counted 1; the drain counted 4; `restore`
        # announced the difference to the operator as "Pending work: 1 live item".
        _mixed = (
            "<!-- fixture -->\n# backlog\n\n"
            "## NOW - five gates block a ship\n"          # level 2, NO resolvable id
            "kind: plan\nlifetime: conditional - until they pass\n\n"
            "Prose body.\n\n"
            "### R-ONE-1 - a real entry, one level deeper\n"   # nested -> BODY, not an entry
            "kind: plan\nlifetime: conditional - until it ships\n\n"
            "## NEXT - the wider sequence\n"              # level 2, NO resolvable id
            "kind: plan\nlifetime: conditional - until step 12\n\n"
            "## M1 - an ordinary id-bearing entry\n"      # level 2 WITH a resolvable id
            "kind: plan\nlifetime: conditional - until done\n\n"
            "```text\n## M99 - fenced worked example, counts as nothing\n```\n\n"
            "<!-- BACKLOG:BEGIN -->\n## M50 - machine-managed, excluded\n<!-- BACKLOG:END -->\n")
        _mx = _mkproj(tmp, "mixed_levels", _REGION_OK, plan=_mixed)
        ck("counts/mixed-exit", main(["--root", str(_mx), "--save-num", "2",
                                      "--checkpoint-ts", "T"], _out=_Sink()), EXIT_OK)
        _mtext = _read(_mx / "state.md").decode("utf-8")
        # THREE entries: the two prose sections and M1. The `###` is nested under the first, the
        # fenced heading is not a heading, and the BACKLOG region is excluded.
        ck("counts/mixed-counts-ENTRIES-not-ids", "\n  backlog_items: 3\n" in _mtext, True)
        # OFF ARM, and it is the whole point: the OLD behaviour counted resolvable level-2 ids,
        # which here is M1 alone. If this ever reads 1 again the regression is back.
        ck("counts/mixed-is-not-the-old-id-count", "\n  backlog_items: 1\n" in _mtext, False)
        # The BASELINE is still ids only -- an entry with no id has nothing to track across saves.
        ck("counts/mixed-baseline-is-ids-only", _region_value(_mx),
           "backlog_entry_ids: [M1]")
        # The operator is TOLD the entries are not addressable, since only they can fix it.
        _wmx = []
        ck("counts/mixed-rerun-exit", regenerate(_mx, 3, "T", _Sink(), _warn=_wmx), EXIT_OK)
        ck("counts/mixed-warns-about-unaddressable",
           any("known id family" in w and "backlog.md" in w for w in _wmx), True)
        # ...and that warning now names the REMEDY, not just the diagnosis. It did not when it
        # was first written -- there was none to name.
        ck("counts/mixed-warning-names-the-declaration-file",
           any(citemap.DECLARED_FAMILIES_FILE in w for w in _wmx), True)

        # ---- `.ash-id-families` ON THE SAVE PATH. -------------------------------------------
        # This is the arm that proves the loader is WIRED rather than merely written: every arm in
        # citemap's own suite passes on a loader no caller ever calls. The fixture uses `OQ-MM-`,
        # which is a MEASURED real instance (peer example-project) and resolves to nothing under the
        # built-ins because `OQ` is `OQ(\d+)` -- digits must follow immediately.
        #
        # WHAT MOVES IS THE ROW-2B BASELINE, NOT THE COUNT, and the arms pin both halves of that.
        # MEASURED here: `backlog_items` reads 3 with and without the declaration, because the
        # count is ENTRIES (pinned by counts/mixed-counts-ENTRIES-not-ids above) and an entry is
        # counted whether or not its heading resolves. The BASELINE is ids only, so without the
        # declaration it is `[M1]` -- two of the three entries silently absent from the list
        # demote.py row 2b compares the next save against, which is the defect, at exit 0.
        _idf_plan = (
            "<!-- fixture -->\n# backlog\n\n"
            "## OQ-MM-5 - the peer's own question family\n"
            "kind: plan\nlifetime: conditional - until it lands\n\n"
            "## OQ-MM-9 - a second one\n"
            "kind: plan\nlifetime: conditional - until it lands\n\n"
            "## M1 - a BUILT-IN family too, so the arm cannot pass by resolving nothing\n"
            "kind: plan\nlifetime: conditional - until done\n")
        _nod = _mkproj(tmp, "idfam_absent", _REGION_OK, plan=_idf_plan)
        _wnod = []
        ck("idfam/absent-exit", regenerate(_nod, 2, "T", _Sink(), _warn=_wnod), EXIT_OK)
        ck("idfam/absent: the baseline LOSES both unregistered entries",
           _region_value(_nod), "backlog_entry_ids: [M1]")
        ck("idfam/absent: and the operator is warned",
           any("known id family" in w for w in _wnod), True)

        _yesd = _mkproj(tmp, "idfam_declared", _REGION_OK, plan=_idf_plan)
        _write_fixture(_yesd, "OQ-MM=OQ-MM-(\\d+)\n", b"\n", citemap.DECLARED_FAMILIES_FILE)
        _wyes = []
        ck("idfam/declared-exit", regenerate(_yesd, 2, "T", _Sink(), _warn=_wyes), EXIT_OK)
        ck("idfam/declared: ALL THREE entries are in the row-2b baseline now",
           _region_value(_yesd), "backlog_entry_ids: [OQ-MM-5, OQ-MM-9, M1]")
        ck("idfam/declared: ...so the unresolvable-entry warning goes quiet",
           any("known id family" in w for w in _wyes), False)
        # The count is unchanged BY DESIGN and is asserted on both sides, so that a future change
        # making counts id-derived cannot pass this suite silently.
        _t_nod = _read(_nod / "state.md").decode("utf-8")
        _t_yes = _read(_yesd / "state.md").decode("utf-8")
        ck("idfam/count is ENTRIES, identical either way",
           ("\n  backlog_items: 3\n" in _t_nod, "\n  backlog_items: 3\n" in _t_yes),
           (True, True))
        # A MALFORMED DECLARATION MAY NEVER ABORT A SAVE. Step 7d (validate_entries) is the only
        # abort in this pipeline and no config condition may join it -- so a file whose every
        # line is broken still exits 0, still writes state.md, and only warns.
        _badd = _mkproj(tmp, "idfam_malformed", _REGION_OK, plan=_idf_plan)
        _write_fixture(_badd, "no equals sign here\nBAD=OQ-MM-([0-9\n", b"\n",
                       citemap.DECLARED_FAMILIES_FILE)
        _wbad = []
        ck("idfam/malformed: the save STILL SUCCEEDS",
           regenerate(_badd, 2, "T", _Sink(), _warn=_wbad), EXIT_OK)
        ck("idfam/malformed: state.md was still written",
           "\n  backlog_items: 3\n" in _read(_badd / "state.md").decode("utf-8"), True)
        # The filter is `<file>:` and not the bare filename: the unresolvable-entry warning now
        # NAMES the declaration file as the remedy, so a bare-name filter counts three and the
        # arm stops measuring the loader.
        ck("idfam/malformed: both bad lines are warned about, by line number",
           len([w for w in _wbad if (citemap.DECLARED_FAMILIES_FILE + ":") in w]), 2)

        # a marker inside a code fence is NOT a marker
        fen_root = _mkproj(tmp, "region_fenced", _REGION_FENCED)
        ck("region/fenced-exit", main(["--root", str(fen_root), "--save-num", "2",
                                       "--checkpoint-ts", "T"], _out=_Sink()), EXIT_OK)
        ftxt = _read(fen_root / "state.md").decode("utf-8")
        ck("region/fenced-decoy-untouched", "backlog_entry_ids: [DECOY]" in ftxt, True)
        ck("region/fenced-real-written", "backlog_entry_ids: [M1, M4, M7]" in ftxt, True)
        ck("region/fenced-markers", ftxt.count(BACKLOG_IDS_BEGIN), 2)

        # duplicated region -> byte-identical, warned, NOT repaired
        dup_root = _mkproj(tmp, "region_dup", _REGION_DUP)
        dup_before = _read(dup_root / "state.md")
        ck("region/dup-exit", main(["--root", str(dup_root), "--save-num", "2",
                                    "--checkpoint-ts", "T"], _out=_Sink()), EXIT_OK)
        dtxt = _read(dup_root / "state.md").decode("utf-8")
        ck("region/dup-untouched", dtxt.count("backlog_entry_ids: []"), 2)
        ck("region/dup-no-write", "M1" in dtxt, False)
        ck("region/dup-not-repaired", dtxt.count(BACKLOG_IDS_BEGIN), 2)
        ck("region/dup-fixture-nonempty", dup_before.count(b"BACKLOG-ENTRY-IDS:BEGIN"), 2)

        # inverted region -> byte-identical, warned
        inv_root = _mkproj(tmp, "region_inv", _REGION_INVERTED)
        ck("region/inv-exit", main(["--root", str(inv_root), "--save-num", "2",
                                    "--checkpoint-ts", "T"], _out=_Sink()), EXIT_OK)
        itxt = _read(inv_root / "state.md").decode("utf-8")
        ck("region/inv-untouched", "backlog_entry_ids: []" in itxt, True)
        ck("region/inv-no-write", "M1" in itxt, False)

        # absent region -> NEVER planted into an existing file (ABSENT != [])
        abs_root = _mkproj(tmp, "region_absent", "")
        wabs = []
        ck("region/absent-exit", regenerate(abs_root, 2, "T", _Sink(), _warn=wabs), EXIT_OK)
        atxt = _read(abs_root / "state.md").decode("utf-8")
        ck("region/absent-stays-absent", BACKLOG_IDS_BEGIN in atxt, False)
        ck("region/absent-prose-kept", "prose" in atxt, True)
        # D5: the warning must not claim a region was "left byte-identical" when there is none
        ck("d5/absent-no-false-identity-claim",
           any("byte-identical" in w for w in wabs), False)
        ck("d5/absent-counts-from-file",
           any("the file as read holds 0 BEGIN and 0 END" in w for w in wabs), True)

        # D5: markers counted in the FILE, not in the tail. Here the tail is truncated at the
        # duplicated region on purpose, and the numbers reported must still be the file's.
        d5 = _mkproj(tmp, "d5_counts", _REGION_DUP)
        w5 = []
        ck("d5/dup-exit", regenerate(d5, 2, "T", _Sink(), _warn=w5), EXIT_OK)
        ck("d5/dup-counts-from-file",
           any("the file as read holds 2 BEGIN and 2 END" in w for w in w5), True)
        d5txt = _read(d5 / "state.md").decode("utf-8")
        ck("d5/dup-region-really-intact", d5txt.count(BACKLOG_IDS_BEGIN), 2)
        ck("d5/dup-value-really-intact", d5txt.count("backlog_entry_ids: []"), 2)
        # and the claim is asserted, not inferred: region_unchanged() sees the input block
        ck("d5/assert-true",
           region_unchanged(recall.load_doc(d5 / "state.md")[1],
                            d5txt.split("\n")), True)
        ck("d5/assert-detects-loss",
           region_unchanged(recall.load_doc(d5 / "state.md")[1],
                            [l for l in d5txt.split("\n") if BACKLOG_IDS_END not in l]), False)
        ck("d5/assert-none-when-no-region",
           region_unchanged(recall.load_doc(abs_root / "state.md")[1], []), None)

        # D5, the load-bearing arm: a SECOND region above the structured block. The tail sees
        # 1 BEGIN / 1 END and would happily write; only a count taken over the FILE sees 2.
        d5b = pathlib.Path(tmp) / "d5_above_block"
        d5b.mkdir()
        _write_fixture(d5b, _FIXTURE_BACKLOG, b"\n", "backlog.md")
        _write_fixture(d5b,
                       "<!-- ash_save v4 | save#1 | old | written by update_state.py -->\n"
                       "# demo - state\n"
                       + BACKLOG_IDS_BEGIN + "\nbacklog_entry_ids: [STALE]\n" + BACKLOG_IDS_END + "\n"
                       + _STATE_HEAD.split("# demo - state\n", 1)[1] + _REGION_OK,
                       b"\n", "state.md")
        d5b_before = _read(d5b / "state.md")
        ck("d5/above-fixture-two-regions", d5b_before.count(b"BACKLOG-ENTRY-IDS:BEGIN"), 2)
        w5b = []
        ck("d5/above-exit", regenerate(d5b, 2, "T", _Sink(), _warn=w5b), EXIT_INTERNAL)
        ck("d5/above-counts-from-file",
           any("the file as read holds 2 BEGIN and 2 END" in w for w in w5b), True)
        ck("d5/above-tail-count-also-named",
           any("the preserved tail holds 1 and 1" in w for w in w5b), True)
        ck("d5/above-refused-r-j-rule-2",
           any("R-J rule 2 forbids deleting the region" in w for w in w5b), True)
        ck("d5/above-nothing-written", _read(d5b / "state.md"), d5b_before)
        ck("d5/above-stale-region-intact",
           b"backlog_entry_ids: [STALE]" in _read(d5b / "state.md"), True)

        # empty backlog.md -> `[]`, and the region is never deleted (R-J rule 2)
        empty_root = _mkproj(tmp, "region_empty", _REGION_OK, plan="# plan\n\nno entries\n")
        ck("region/empty-exit", main(["--root", str(empty_root), "--save-num", "2",
                                      "--checkpoint-ts", "T"], _out=_Sink()), EXIT_OK)
        ck("region/empty-value", _region_value(empty_root), "backlog_entry_ids: []")
        etxt = _read(empty_root / "state.md").decode("utf-8")
        ck("region/empty-region-kept", etxt.count(BACKLOG_IDS_END), 1)
        ck("counts/f65-zero-written", "\n  backlog_items: 0\n" in etxt, True)

        # region survives a CRLF round trip with its terminators
        crlf_region = pathlib.Path(tmp) / "region_crlf"
        crlf_region.mkdir()
        _write_fixture(crlf_region, _STATE_HEAD + _REGION_OK, b"\r\n", "state.md")
        _write_fixture(crlf_region, _FIXTURE_BACKLOG, b"\r\n", "backlog.md")
        ck("region/crlf-exit", main(["--root", str(crlf_region), "--save-num", "2",
                                     "--checkpoint-ts", "T"], _out=_Sink()), EXIT_OK)
        cbytes = _read(crlf_region / "state.md")
        ck("region/crlf-value", b"\r\nbacklog_entry_ids: [M1, M4, M7]\r\n" in cbytes, True)
        ck("region/crlf-no-lone-lf", cbytes.count(b"\n") - cbytes.count(b"\r\n"), 0)

        # idempotence: a second run over the same folder changes nothing
        first = _read(ok_root / "state.md")
        main(["--root", str(ok_root), "--save-num", "2", "--checkpoint-ts", "T"], _out=_Sink())
        ck("region/idempotent", _read(ok_root / "state.md"), first)
        ck("region/idempotent-nonempty", len(first) > 400, True)
        # --- D9: the STATE-UPDATE report is PARSED and cross-checked against the file -----
        # The harness used to pass _Sink() to main() and never read its own machine-readable
        # report, so every field of it could lie and stay green. Nine mutants survived there.
        rep_root = pathlib.Path(tmp) / "reported_project"
        rep_root.mkdir()
        _write_fixture(rep_root, _STATE_HEAD + _REGION_OK, b"\n", "state.md")
        _write_fixture(rep_root, _FIXTURE_BACKLOG, b"\n", "backlog.md")
        _write_fixture(rep_root, "# oq\n\n## OQ1 - a\n\nx\n\n## OQ2 - b\n\ny\n", b"\n",
                       "open_questions.md")
        _write_fixture(rep_root, "# active\n\n## A-7 - one\n\nz\n", b"\n", "active.md")
        for name in HOT_FILES:
            if not (rep_root / name).is_file():
                _write_fixture(rep_root, "# %s\n" % name, b"\n", name)
        (rep_root / "checkpoints").mkdir()
        _write_fixture(rep_root / "checkpoints", "a\n", b"\n", "checkpoint-2026-08-01-1.md")

        buf = io.StringIO()
        ck("d9/exit", main(["--root", str(rep_root), "--save-num", "12",
                            "--checkpoint-ts", "2026-08-03 12:00:00 UTC"], _out=buf), EXIT_OK)
        sm = _summary(buf.getvalue())
        ck("d9/report-parsed", len(sm) >= 14, True)
        disk = _read(rep_root / "state.md").decode("utf-8")
        ck("d9/disk-nonempty", len(disk) > 500, True)
        # every reported field is checked against the FILE, never against itself
        ck("d9/save-reported", sm.get("save"), "12")
        ck("d9/save-on-disk", "\nsave: 12\n" in disk, True)
        # project is PRESERVED from the file, and the folder basename deliberately differs
        ck("d9/basename-differs", rep_root.name != "demo", True)
        ck("d9/project-reported", sm.get("project"), "demo")
        ck("d9/project-on-disk", "\nproject: demo\n" in disk, True)
        ck("d9/written", sm.get("written"), "yes")
        ck("d9/changed", sm.get("changed"), "yes")
        ck("d9/terminator", sm.get("terminator"), "lf")
        ck("d9/bom", sm.get("bom"), "no")
        ck("d9/defects", sm.get("defects"), "0")
        ck("d9/ids-reported", sm.get("backlog_entry_ids"), "[M1, M4, M7]")
        ck("d9/ids-on-disk", "\nbacklog_entry_ids: [M1, M4, M7]\n" in disk, True)
        ck("d9/ids-status", sm.get("backlog_entry_ids_status"), "written")
        # counts: reported == on disk, AND the right numbers
        for _k, _want in (("hot_files", "10"), ("checkpoints", "1"), ("backlog_items", "3"),
                          ("open_questions", "2"), ("active_instructions", "1")):
            ck("d9/counts.%s reported" % _k, sm.get("counts.%s" % _k), _want)
            ck("d9/counts.%s on disk" % _k, "\n  %s: %s\n" % (_k, _want) in disk, True)
        # a second identical run reports changed: no
        buf2 = io.StringIO()
        ck("d9/rerun-exit", main(["--root", str(rep_root), "--save-num", "12",
                                  "--checkpoint-ts", "2026-08-03 12:00:00 UTC"],
                                 _out=buf2), EXIT_OK)
        ck("d9/rerun-changed-no", _summary(buf2.getvalue()).get("changed"), "no")

        # D9 arm: an open fence in state.md ITSELF -> exit 6, and the status is still reported
        ofs = _mkproj(tmp, "d9_openfence_state", _REGION_OK)
        _write_fixture(ofs, _STATE_HEAD + _REGION_OK + "\n```\nnever closed\n", b"\n",
                       "state.md")
        buf3 = io.StringIO()
        ck("d9/state-open-fence-exit6",
           main(["--root", str(ofs), "--save-num", "2", "--checkpoint-ts", "T"], _out=buf3),
           EXIT_SCAN_INTEGRITY)
        sm3 = _summary(buf3.getvalue())
        ck("d9/state-open-fence-still-reports", sm3.get("backlog_entry_ids_status"), "written")
        ck("d9/state-open-fence-wrote", sm3.get("written"), "yes")
        ck("d9/state-open-fence-kept-fence",
           "never closed" in _read(ofs / "state.md").decode("utf-8"), True)

        # D9 arm: hot_files on a PARTIAL folder is the real count, not a constant
        part = pathlib.Path(tmp) / "d9_partial"
        part.mkdir()
        _write_fixture(part, _STATE_HEAD + _REGION_OK, b"\n", "state.md")
        _write_fixture(part, _FIXTURE_BACKLOG, b"\n", "backlog.md")
        buf4 = io.StringIO()
        ck("d9/partial-exit", main(["--root", str(part), "--save-num", "2",
                                    "--checkpoint-ts", "T"], _out=buf4), EXIT_OK)
        sm4 = _summary(buf4.getvalue())
        # F4 moved this expectation from 2 to 3, and the move is the point: `state.md` AND
        # `manifest.md` are both counted present by construction (this save generates both), so
        # a folder holding only backlog.md on disk reports 3. The arm still distinguishes a real
        # count from a constant -- 3 here against 10 on the full folder -- which is what mutant
        # U27 survived on when the only corpora were complete scaffolds.
        ck("d9/partial-hot-files", sm4.get("counts.hot_files"), "3")
        ck("d9/partial-hot-files-on-disk",
           "\n  hot_files: 3\n" in _read(part / "state.md").decode("utf-8"), True)
        # F4: absence of manifest.md is counted present but NEVER silently - unlike state.md,
        # this tool does not write it.
        wman = []
        regenerate(part, 2, "T", _Sink(), _warn=wman)
        ck("d9/partial-warns-manifest-absent",
           any("manifest.md is not on disk" in w for w in wman), True)
        ck("d9/partial-differs-from-full", sm4.get("counts.hot_files") == sm.get(
            "counts.hot_files"), False)
        # the missing-hot-file warning is a real assertion, not a tautology over an empty list
        wpart = []
        ck("d9/partial-rerun", regenerate(part, 2, "T", _Sink(), _warn=wpart), EXIT_OK)
        ck("d9/partial-warns-oq",
           any("open_questions.md is missing from the hot set" in w for w in wpart), True)
        ck("d9/partial-warns-active",
           any("active.md is missing from the hot set" in w for w in wpart), True)
        ck("d9/partial-does-not-warn-backlog",
           any("backlog.md is missing from the hot set" in w for w in wpart), False)

        # --- D8: the structural accounting guard ------------------------------------------
        _files_stub = {"manifest": "manifest.md", "latest_checkpoint": "(none yet)",
                       "archive": "_archive/"}
        _counts_stub = dict((k, 0) for k in COUNT_KEYS)

        # It catches the D2 shape WITHOUT knowing what a block scalar is: render the live v3
        # corpus with the pre-fix behaviour (bodies dropped) and the guard fires on the bytes.
        d8live = pathlib.Path(tmp) / "d8_live"
        d8live.mkdir()
        _write_fixture(d8live, _FIXTURE_BLOCK_SCALARS_V3, b"\n", "state.md")
        st8 = parse_state(d8live / "state.md")
        _v3_children = [t for _s, k, t in st8["child_lines"]
                        if k not in (set(COUNT_KEYS) | set(FILES_KEYS))]
        ck("d8/v3-children-found", len(_v3_children), 10)
        pre_fix = render_state("save", 17, "T", _resolve_preserved(st8["fields"]),
                               _files_stub, _counts_stub, st8["tail"], None)
        d2_lost = accounting_defects(st8, pre_fix, _v3_children)
        ck("d8/catches-d2", len(d2_lost), 1)
        post_fix = render_state("save", 17, "T", _resolve_preserved(st8["fields"]),
                                _files_stub, _counts_stub, st8["tail"], st8["field_blocks"])
        ck("d8/clean-with-d2-fixed", accounting_defects(st8, post_fix, _v3_children), [])

        # It catches the D1 shape the same way: a truncated tail, no knowledge of markers.
        d8trap = _mkproj(tmp, "d8_trap", _REGION_D1_TRAP)
        stt = parse_state(d8trap / "state.md")
        truncated = render_state("demo", 2, "T", _resolve_preserved(stt["fields"]),
                                 _files_stub, _counts_stub, [], stt["field_blocks"])
        d1_lost = accounting_defects(stt, truncated, [])
        ck("d8/catches-d1", len(d1_lost), 1)
        ck("d8/catches-d1-names-region",
           "BACKLOG-ENTRY-IDS" in (d1_lost[0] if d1_lost else ""), True)
        ck("d8/catches-d1-names-heading",
           "Row-2b baseline" in (d1_lost[0] if d1_lost else ""), True)
        intact = render_state("demo", 2, "T", _resolve_preserved(stt["fields"]),
                              _files_stub, _counts_stub, stt["tail"], stt["field_blocks"])
        ck("d8/clean-when-intact", accounting_defects(stt, intact, []), [])

        # End to end: an injected loss makes the tool REFUSE TO WRITE and exit 7, not exit 0.
        d8 = _mkproj(tmp, "d8_guard", _REGION_OK)
        d8_before = _read(d8 / "state.md")
        ck("d8/fixture-nonempty", len(d8_before) > 400, True)
        _real_render = render_state

        def _lossy_render(*a, **kw):
            return [l for l in _real_render(*a, **kw) if l != "prose"]

        globals()["render_state"] = _lossy_render
        try:
            w8 = []
            rep8 = io.StringIO()
            ck("d8/refuses-exit", regenerate(d8, 2, "T", rep8, _warn=w8), EXIT_INTERNAL)
            ck("d8/refuses-named",
               any("REFUSING TO WRITE" in w and "accounting:" in w for w in w8), True)
            ck("d8/refuses-names-line", any("'prose'" in w for w in w8), True)
            ck("d8/report-written-no", _summary(rep8.getvalue()).get("written"), "no")
            ck("d8/report-defects-1", _summary(rep8.getvalue()).get("defects"), "1")
        finally:
            globals()["render_state"] = _real_render
        ck("d8/nothing-written", _read(d8 / "state.md"), d8_before)
        ck("d8/clean-exit-after-restore", regenerate(d8, 2, "T", _Sink()), EXIT_OK)
        ck("d8/wrote-after-restore", _read(d8 / "state.md") != d8_before, True)

        # --- D7: a UTF-8 BOM survives the rewrite ----------------------------------------
        bomroot = pathlib.Path(tmp) / "bom"
        bomroot.mkdir()
        _write_fixture(bomroot, _FIXTURE_STATE, b"\n")
        _bd = _read(bomroot / "state.md")
        write_bytes(bomroot / "state.md", BOM_BYTES + _bd)
        ck("d7/fixture-has-bom", _read(bomroot / "state.md").startswith(BOM_BYTES), True)
        ck("d7/exit", regenerate(bomroot, 4, "T", _Sink()), EXIT_OK)
        bout = _read(bomroot / "state.md")
        ck("d7/bom-preserved", bout.startswith(BOM_BYTES), True)
        ck("d7/one-bom-only", bout.count(BOM_BYTES), 1)
        ck("d7/save-line-still-col0", b"\nsave: 4\n" in bout, True)
        ck("d7/schema-line-parsed",
           b"\nschema_version: v4\n" in bout, True)
        # OFF arm: a file WITHOUT a BOM must not acquire one
        ck("d7/no-bom-stays-none", _read(lf_root / "state.md").startswith(BOM_BYTES), False)

        # Two independent synthetic scalar blocks survive a normal v4 rewrite.
        live = pathlib.Path(tmp) / "live_v3"
        live.mkdir()
        _write_fixture(live, _FIXTURE_BLOCK_SCALARS_V3, b"\n", "state.md")
        live_before = _read(live / "state.md")
        ck("d2/corpus-has-two-block-scalars",
           live_before.count(b"\ngoal: |\n") + live_before.count(b"\nactive_track: |\n"), 2)
        # The v3 fixture refuses and remains byte-identical; it is never converted.
        ck("gate/live-v3-refused",
           regenerate(live, 17, "T", _Sink(), _warn=[]), EXIT_NOT_MIGRATED)
        ck("gate/live-v3-untouched", _read(live / "state.md"), live_before)
        live = pathlib.Path(tmp) / "synthetic_blocks_v4"
        live.mkdir()
        _write_fixture(live, _FIXTURE_BLOCK_SCALARS_V3.replace("schema_version: v3", "schema_version: v4"),
                       b"\n", "state.md")
        scalar_before = [(key, text) for _line, key, text in parse_state(live / "state.md")["child_lines"]
                         if key in ("goal", "active_track")]
        wl = []
        ck("d2/exit", regenerate(live, 17, "2000-01-01 12:00:00 UTC", _Sink(), _warn=wl), EXIT_OK)
        ltxt = _read(live / "state.md").decode("utf-8")
        scalar_after = [(key, text) for _line, key, text in parse_state(live / "state.md")["child_lines"]
                        if key in ("goal", "active_track")]
        ck("d2/exact-scalar-body-conservation", scalar_after, scalar_before)
        ck("d2/goal-indicator-kept", "\ngoal: |\n" in ltxt, True)
        ck("d2/active-track-indicator-kept", "\nactive_track: |\n" in ltxt, True)
        # the OFF arm: a dropped body leaves a BARE `goal: |` and the file collapses
        ck("d2/not-empty-goal", "\ngoal: |\nphase:" in ltxt, False)
        # the v3 tail below the block is preserved too (D1 contiguity)
        ck("d2/tail-kept", "tier1_tokens_method: synthetic-fixture" in ltxt, True)
        ck("d2/tail-kept-2", "maintenance_threshold: 7" in ltxt, True)
        ck("d2/save-advanced", "\nsave: 17\n" in ltxt, True)
        ck("d2/one-save-line",
           len([l for l in ltxt.split("\n") if l.startswith("save:")]), 1)
        ck("d2/remains-v4", "\nschema_version: v4\n" in ltxt, True)
        v4root = pathlib.Path(tmp) / "already_v4"
        v4root.mkdir()
        _write_fixture(v4root, _FIXTURE_BLOCK_SCALARS_V3.replace("schema_version: v3",
                                                        "schema_version: v4"), b"\n", "state.md")
        w4 = []
        ck("d4/v4-exit", regenerate(v4root, 17, "T", _Sink(), _warn=w4), EXIT_OK)
        ck("d4/v4-not-warned", any("rollback discriminator" in w for w in w4), False)
        ck("d4/v4-still-v4",
           "\nschema_version: v4\n" in _read(v4root / "state.md").decode("utf-8"), True)

        # --- D1: a column-0 prose line in the TAIL is not a field ------------------------
        # Each of these four used to set block_end from the LAST match anywhere in the file,
        # deleting everything above it and overwriting the real value with the prose.
        trap = _mkproj(tmp, "d1_trap", _REGION_D1_TRAP)
        trap_before = _read(trap / "state.md")
        ck("d1/fixture-nonempty", len(trap_before) > 700, True)
        ck("d1/fixture-has-region", trap_before.count(b"BACKLOG-ENTRY-IDS:BEGIN"), 1)
        w1 = []
        ck("d1/exit", regenerate(trap, 2, "T", _Sink(), _warn=w1), EXIT_OK)
        ttxt = _read(trap / "state.md").decode("utf-8")
        # the four preserved fields keep their REAL values, not the prose
        ck("d1/hot-state-real", "\nhot_state: scaffolded\n" in ttxt, True)
        ck("d1/goal-real", "\ngoal: g\n" in ttxt, True)
        ck("d1/status-real", "\nstatus: active\n" in ttxt, True)
        ck("d1/save-real", "\nsave: 2\n" in ttxt, True)
        ck("d1/save-not-fabricated", "\nsave: 99\n" in ttxt.split("## Row-2b")[0], False)
        # the prose survives, in the tail, verbatim
        ck("d1/prose-hot-state",
           "hot_state: is preserved byte-for-byte; P1 never writes a new value (spec S5)."
           in ttxt, True)
        ck("d1/prose-goal",
           "goal: a sentence about the goal field, which is prose and not a field." in ttxt,
           True)
        ck("d1/prose-status", "status: and this one is prose about status." in ttxt, True)
        ck("d1/prose-save", "\nsave: 99\n" in ttxt, True)
        # the Row-2b heading and the region are still there
        ck("d1/row2b-heading", ROW2B_HEADING in ttxt, True)
        ck("d1/region-markers", ttxt.count(BACKLOG_IDS_BEGIN), 1)
        ck("d1/region-written", "backlog_entry_ids: [M1, M4, M7]" in ttxt, True)
        # and nothing was silently dropped: the file did not shrink
        ck("d1/no-byte-loss", len(_read(trap / "state.md")) >= len(trap_before), True)
        # the D6 second-save: hazard warning is now REACHABLE (it was dead code)
        ck("d1/second-save-warned",
           any("save:" in w and "hazard" in w for w in w1), True)
        # D6: it fires on the UNFENCED one, and names the other three prose fields too
        ck("d6/unfenced-not-labelled-fenced",
           any("hazard" in w and "INSIDE A CODE FENCE" not in w for w in w1), True)
        ck("d6/hot-state-prose-named",
           any("'hot_state'" in w and "NOT read as a field" in w for w in w1), True)
        ck("d6/goal-prose-named", any("'goal'" in w and "NOT read as a field" in w for w in w1),
           True)
        ck("d6/status-prose-named",
           any("'status'" in w and "NOT read as a field" in w for w in w1), True)
        # and a FENCED save: line is labelled as such rather than reported as a live hazard
        fen_save = _mkproj(tmp, "d6_fenced_save", "\n```\nsave: 42\n```\n" + _REGION_OK)
        w6 = []
        ck("d6/fenced-exit", regenerate(fen_save, 3, "T", _Sink(), _warn=w6), EXIT_OK)
        ck("d6/fenced-labelled",
           any("INSIDE A CODE FENCE" in w for w in w6), True)
        ck("d6/fenced-preserved",
           "save: 42" in _read(fen_save / "state.md").decode("utf-8"), True)

        # a top-level key declared TWICE inside the block: first wins, collision named
        dup_key = _mkproj(tmp, "d1_dupkey", _REGION_OK)
        _write_fixture(dup_key,
                       (_STATE_HEAD + _REGION_OK).replace("goal: g\n", "goal: g\ngoal: SECOND\n"),
                       b"\n", "state.md")
        w2 = []
        ck("d1/dup-exit", regenerate(dup_key, 2, "T", _Sink(), _warn=w2), EXIT_OK)
        dktxt = _read(dup_key / "state.md").decode("utf-8")
        ck("d1/dup-first-wins", "\ngoal: g\n" in dktxt, True)
        ck("d1/dup-second-not-value", "\ngoal: SECOND\n" in dktxt, False)
        ck("d1/dup-warned",
           any("goal" in w and "'SECOND'" in w and "more than once" in w for w in w2), True)

        # --- (g): a full nine-hot-file folder with BOTH hand-built checkpoint drains -----
        full = pathlib.Path(tmp) / "full"
        full.mkdir()
        _write_fixture(full, _STATE_HEAD + _REGION_OK, b"\n", "state.md")
        _write_fixture(full, _FIXTURE_BACKLOG, b"\n", "backlog.md")
        for name in HOT_FILES:
            if not (full / name).is_file():
                _write_fixture(full, "# %s\n" % name, b"\n", name)
        (full / "_archive").mkdir()
        _write_fixture(full / "_archive", "# deferred\n", b"\n", "backlog-deferred.md")
        (full / "checkpoints").mkdir()
        _write_fixture(full / "checkpoints", "a\n", b"\n", "checkpoint-2026-08-01-000000.md")
        _write_fixture(full / "checkpoints", "b\n", b"\n", "checkpoint-2026-08-02-000000.md")
        _write_fixture(full / "checkpoints", "", b"\n", "checkpoint-2026-08-03-empty.md")
        (full / "checkpoints" / "_archive").mkdir()
        _write_fixture(full / "checkpoints" / "_archive", "c\n", b"\n", "checkpoint-old-1.md")
        (full / "_archive" / "checkpoints").mkdir()
        _write_fixture(full / "_archive" / "checkpoints", "d\n", b"\n", "checkpoint-old-2.md")
        warns = []
        ck("full/exit", regenerate(full, 7, "2026-08-03 12:00:00 UTC", _Sink(), _warn=warns),
           EXIT_OK)
        gtext = _read(full / "state.md").decode("utf-8")
        ck("full/hot-files-10", "\n  hot_files: 10\n" in gtext, True)
        # Neither hand-built drain nesting may inflate the count; the empty one is excluded.
        ck("full/checkpoints-2", "\n  checkpoints: 2\n" in gtext, True)
        ck("full/latest-checkpoint",
           "\n  latest_checkpoint: checkpoints/checkpoint-2026-08-02-000000.md\n" in gtext, True)
        ck("full/region", "backlog_entry_ids: [M1, M4, M7]" in gtext, True)
        ck("full/no-warnings", warns, [])

        # A same-second save uses -2, -3, ...; lexical order picks the older bare name.
        for cp_name in ("checkpoint-2026-08-02-000000-2.md",
                        "checkpoint-2026-08-02-000000-10.md",
                        "checkpoint-2026-08-01-000000-99.md"):
            _write_fixture(full / "checkpoints", "collision\n", b"\n", cp_name)
        ck("collision/exit", regenerate(full, 7, "T", _Sink()), EXIT_OK)
        collision_text = _read(full / "state.md").decode("utf-8")
        ck("collision/latest-numeric-suffix",
           "\n  latest_checkpoint: checkpoints/checkpoint-2026-08-02-000000-10.md\n"
           in collision_text, True)
        ck("collision/count-keeps-all", "\n  checkpoints: 5\n" in collision_text, True)

        # A v4 folder created before the dual instruction-file contract legitimately has the
        # historical nine-file set. Missing AGENTS.md is not a torn save and does not block the
        # state rewrite; the count remains an observation of disk rather than a required constant.
        (full / "AGENTS.md").unlink()
        legacy_warns = []
        ck("legacy-nine/exit",
           regenerate(full, 8, "2026-08-03 13:00:00 UTC", _Sink(), _warn=legacy_warns), EXIT_OK)
        legacy_text = _read(full / "state.md").decode("utf-8")
        ck("legacy-nine/hot-files-9", "\n  hot_files: 9\n" in legacy_text, True)
        ck("legacy-nine/no-missing-agents-damage-warning",
           [w for w in legacy_warns if "AGENTS.md" in w], [])

        # --- omitting --checkpoint-ts must WARN, and must still preserve. ---
        # The passed-flag arms are the sensitivity control: a warning that fires on every run,
        # including the correct one, says nothing at all.
        tsf = _mkproj(tmp, "tsfreeze", _REGION_OK)
        wtsf = []
        ck("tsfreeze/preserved-exit",
           regenerate(tsf, 8, None, _Sink(), _warn=wtsf), EXIT_OK)
        _tsw = [w for w in wtsf if "--checkpoint-ts" in w]
        ck("tsfreeze/preserved-warns", len(_tsw), 1)
        # It must NAME the value carried and the save it is carried into: "flag missing" alone
        # cannot be told from a warning the caller meant to trigger.
        ck("tsfreeze/preserved-names-value",
           bool(_tsw) and "'old'" in _tsw[0] and "8" in _tsw[0], True)
        # This WARNS; it does not change what is written. Assert the preserve, never assume it.
        ck("tsfreeze/preserved-still-preserves",
           "last_save_ts: old" in _read(tsf / "state.md").decode("utf-8").splitlines(), True)

        # DIFFERENTIAL, and deliberately NOT a wording filter: filtering for the flag name lets
        # any re-worded warning satisfy an arm named for silence. The fixture emits unrelated
        # warnings, so == [] is wrong too. Passing the flag must remove exactly ours, add none.
        tsf_ok = _mkproj(tmp, "tsfreeze_ok", _REGION_OK)
        wtsf_ok = []
        regenerate(tsf_ok, 8, "2026-08-23 22:19:47 PDT", _Sink(), _warn=wtsf_ok)
        # ORDER-INDEPENDENT: a positional compare assumes our warning lands at index 0, which is
        # a property of this fixture and not of the code (an unparsable `save:` warns first).
        # MULTISET difference. `w not in list` is multiplicity-blind: wtsf=[OURS,X] against
        # wtsf_ok=[X,X] passes every arm while a DUPLICATE was added. list.remove drops one
        # occurrence per match, which is the whole of the semantics needed and needs no import.
        _removed = list(wtsf)
        _added = []
        for _w in wtsf_ok:
            if _w in _removed:
                _removed.remove(_w)
            else:
                _added.append(_w)
        ck("tsfreeze/passed-removes-exactly-one", len(_removed), 1)
        ck("tsfreeze/passed-removes-ours",
           bool(_removed) and "--checkpoint-ts was not passed" in _removed[0], True)
        # The silence half: passing the flag must ADD nothing. A re-worded warning emitted only
        # on the passed path shows up here and nowhere else, which is what the wording filter
        # this replaced could not see.
        ck("tsfreeze/passed-adds-nothing", _added, [])

        # With NEITHER flag passed the advancing claims would be FALSE and the curator DOES
        # catch the result. Different branch, different sentence, and an arm that proves it.
        tsf_both = _mkproj(tmp, "tsfreeze_both", _REGION_OK)
        wtsf_both = []
        regenerate(tsf_both, None, None, _Sink(), _warn=wtsf_both)
        # Keyed on THIS branch's opening, not the flag name: every branch names the flag, so a
        # flag-name filter is satisfied by a SIBLING branch's warning and cannot fail here.
        _tsb = [w for w in wtsf_both if w.startswith("neither --save-num")]
        ck("tsfreeze/both-omitted-warns", len(_tsb), 1)
        # Keyed on strings that EXIST in the sibling branch, so each arm can actually fail. An
        # absence-assertion on a phrase removed from BOTH branches can never go red.
        ck("tsfreeze/both-omitted-borrows-no-advancing-text",
           bool(_tsb) and "was REQUESTED for this " not in _tsb[0]
           and "Nothing downstream catches" not in _tsb[0], True)
        # It must carry both facts: only the REQUEST is absent, and a pre-existing mismatch is
        # carried forward rather than resolved.
        ck("tsfreeze/both-omitted-names-both-flags",
           bool(_tsb) and "--save-num" in _tsb[0] and "REQUESTS no " in _tsb[0], True)
        ck("tsfreeze/both-omitted-claims-no-consistency",
           bool(_tsb) and "self-consistent" not in _tsb[0]
           and "carries such a mismatch" in _tsb[0], True)

        # A non-integer save_num raises in the CURSOR WRITER, on the unmodified build too. This
        # arm does NOT assert the call succeeds -- that is pre-existing behaviour this change
        # does not own. It asserts only that the WARNING path is total and ran BEFORE the raise.
        # Read via stderr: `_warn` is extended only on a successful run, so it stays empty here.
        class _Cap(object):
            def __init__(self):
                self.text = ""
            def write(self, s):
                self.text += s
            def flush(self):
                pass

        tsf_str = _mkproj(tmp, "tsfreeze_str", _REGION_OK)
        _cap, _real_err = _Cap(), sys.stderr
        sys.stderr = _cap
        try:
            regenerate(tsf_str, "9", None, _Sink(), _warn=[])
            _raised = None
        except Exception as _exc:       # noqa: BLE001 - WHICH exception, and from where, IS the arm
            _raised = type(_exc).__name__
        finally:
            sys.stderr = _real_err
        ck("tsfreeze/non-int-writer-raises-not-us", _raised, "TypeError")
        # `"save: %d" % 9.5` does NOT refuse -- it writes `save: 9`, truncating silently. So the
        # writer's `%d` is a serialisation backstop, NOT integer-contract enforcement.
        tsf_flt = _mkproj(tmp, "tsfreeze_flt", _REGION_OK)
        wflt = []
        ck("tsfreeze/float-save-num-exit", regenerate(tsf_flt, 9.5, None, _Sink(), _warn=wflt),
           EXIT_OK)
        _wf = [w for w in wflt if "--checkpoint-ts was not passed" in w]
        ck("tsfreeze/float-warns-requested-not-done",
           bool(_wf) and "was REQUESTED" in _wf[0], True)
        # The value must appear AS PASSED: `%d` renders 9.5 as 9 silently, so an arm that only
        # checks a warning appeared cannot tell the specifiers apart. This one fails on regress.
        ck("tsfreeze/float-reported-untruncated",
           bool(_wf) and "9.5" in _wf[0], True)
        ck("tsfreeze/non-int-warning-path-is-total",
           "--checkpoint-ts was not passed" in _cap.text, True)

        # No prior value to preserve is a DIFFERENT shape -- the field is written empty rather
        # than stale -- so it gets its own sentence and its own arm.
        _NL = chr(10)
        tsf_none = _mkproj(tmp, "tsfreeze_none", _REGION_OK)
        _write_fixture(tsf_none, _NL.join(
            l for l in _read(tsf_none / "state.md").decode("utf-8").splitlines()
            if not l.startswith("last_save_ts:")) + _NL, b"" + _NL.encode(), "state.md")
        wtsf_none = []
        regenerate(tsf_none, 8, None, _Sink(), _warn=wtsf_none)
        ck("tsfreeze/absent-field-warns-empty",
           [w for w in wtsf_none if "--checkpoint-ts" in w and "EMPTY" in w] != [], True)

        # F65: the warning must NAME the file and BOTH numbers, and the computed 0 is written.
        f65 = _mkproj(tmp, "f65", _REGION_OK, plan="# plan\n\nno entries here\n")
        w65 = []
        ck("f65/exit", regenerate(f65, 2, "T", _Sink(), _warn=w65), EXIT_OK)
        ck("f65/warned", any("backlog_items" in w and "recorded 3" in w for w in w65), True)
        ck("f65/computed-zero-written",
           "\n  backlog_items: 0\n" in _read(f65 / "state.md").decode("utf-8"), True)
        # exit 6 OUTRANKS the verdict on every path that reads a file
        of_root = _mkproj(tmp, "openfence", _REGION_OK,
                          plan="# plan\n\n## M1 - one\n\n```\nnever closed\n")
        ck("integrity/exit6", main(["--root", str(of_root), "--save-num", "2",
                                    "--checkpoint-ts", "T"], _out=_Sink()),
           EXIT_SCAN_INTEGRITY)
        ck("integrity/still-wrote",
           BACKLOG_IDS_BEGIN in _read(of_root / "state.md").decode("utf-8"), True)

        # ---- F1. The write is ATOMIC: a POST-OPEN failure leaves the previous file intact. ----
        # The read-only case was already safe before this fix (open raises before it truncates).
        # The fatal one was a failure AFTER the open, because open(..., "wb") truncates at open:
        # a full disk, a kill, an exception mid-render destroyed state.md and with it the R-G
        # baseline, which R-J rule 1 then correctly refuses to re-plant -- row 2b permanently
        # inert for that project. Simulated here by making the rename fail, which is the last
        # step and therefore the strictest arm: everything has been written to the temp already.
        atom = pathlib.Path(tmp) / "f1_atomic"
        atom.mkdir()
        _write_fixture(atom, _STATE_HEAD + _REGION_OK, b"\n", "state.md")
        _write_fixture(atom, _FIXTURE_BACKLOG, b"\n", "backlog.md")
        before = _read(atom / "state.md")
        _real_replace = os.replace

        def _boom(src, dst):                                     # pragma: no cover - fixture
            raise OSError(5, "simulated post-open failure")

        os.replace = _boom
        try:
            _rc_atom = regenerate(atom, 9, "T", _Sink(), _warn=[])
        except OSError:
            _rc_atom = "raised"
        finally:
            os.replace = _real_replace
        after = _read(atom / "state.md")
        ck("f1/previous-file-byte-identical", after, before)
        ck("f1/previous-file-nonempty", len(after) > 0, True)
        ck("f1/no-temp-left", (atom / ("state.md" + TMP_SUFFIX)).exists(), False)
        # and the tool still works on the very next run, from the intact file
        ck("f1/recovers-next-run", regenerate(atom, 9, "T", _Sink(), _warn=[]), EXIT_OK)
        ck("f1/recovered-has-region",
           BACKLOG_IDS_BEGIN in _read(atom / "state.md").decode("utf-8"), True)

        # ---- F2. TOTAL loss is the loudest case, not the quietest. ----
        # Damaged/absent declarations refuse without reconstructing a cursor from defaults.
        for _name, _body, _want_empty in (("f2_zero", b"", True),
                                          ("f2_ws", b"   \n\n\t\n", True),
                                          ("f2_prose", b"just prose, no field at all\n", False)):
            _r = pathlib.Path(tmp) / _name
            _r.mkdir()
            _write_fixture(_r, _FIXTURE_BACKLOG, b"\n", "backlog.md")
            write_bytes(_r / "state.md", _body)
            ck("gate/%s-refused" % _name,
               regenerate(_r, 3, "T", _Sink(), _warn=[]), EXIT_NOT_MIGRATED)
            ck("gate/%s-untouched" % _name, _read(_r / "state.md"), _body)

        # A scaffold-shaped v4 cursor receives the actual backlog baseline.
        fresh2 = _mkproj(tmp, "f3_fresh", _REGION_OK)
        buff = io.StringIO()
        ck("f3/exit", main(["--root", str(fresh2), "--save-num", "1",
                            "--checkpoint-ts", "T"], _out=buff), EXIT_OK)
        smf = _summary(buff.getvalue())
        _fresh_txt = _read(fresh2 / "state.md").decode("utf-8")
        ck("f3/status-written", smf.get("backlog_entry_ids_status"), "written")
        ck("f3/baseline-not-empty", smf.get("backlog_entry_ids") == "[]", False)
        ck("f3/baseline-matches-backlog-items",
           smf.get("backlog_entry_ids", "").count(",") + 1, int(smf.get("counts.backlog_items", "0")))
        ck("f3/region-present-once", _fresh_txt.count(BACKLOG_IDS_BEGIN), 1)
        ck("f3/region-on-disk-not-empty", "backlog_entry_ids: []" in _fresh_txt, False)
        fresh3 = pathlib.Path(tmp) / "missing_cursor"
        fresh3.mkdir()
        _write_fixture(fresh3, _FIXTURE_BACKLOG, b"\n", "backlog.md")
        ck("missing-cursor/refuses", regenerate(fresh3, 1, "T", _Sink()), EXIT_NOT_MIGRATED)
        ck("missing-cursor/not-created", (fresh3 / "state.md").exists(), False)

        # F7a/label: all three terminators round-trip AND are reported by their own name.
        for _tn, _term in (("lf", b"\n"), ("crlf", b"\r\n"), ("cr", b"\r")):
            _td = pathlib.Path(tmp) / ("f7a_" + _tn)
            _td.mkdir()
            _write_fixture(_td, _STATE_HEAD + _REGION_OK, _term, "state.md")
            _write_fixture(_td, _FIXTURE_BACKLOG, b"\n", "backlog.md")
            _tb = io.StringIO()
            ck("f7a/%s-exit" % _tn, main(["--root", str(_td), "--save-num", "4",
                                          "--checkpoint-ts", "T"], _out=_tb), EXIT_OK)
            _sm = _summary(_tb.getvalue())
            ck("f7a/%s-label" % _tn, _sm.get("terminator"), _tn)
            _got = _read(_td / "state.md")
            _dom = max((("crlf", _got.count(b"\r\n")),
                        ("lf", _got.count(b"\n") - _got.count(b"\r\n")),
                        ("cr", _got.count(b"\r") - _got.count(b"\r\n"))), key=lambda x: x[1])[0]
            ck("f7a/%s-on-disk" % _tn, _dom, _tn)

        # --- the migration gate, as its own subject ---------------------------------------
        # Everything above proves the gate does not get in the way of a v4 folder. These arms
        # prove it stops a folder that is not one, and that the reading of "absent" is D23's.
        ck("gate/exit-code-is-4", EXIT_NOT_MIGRATED, 4)
        ck("gate/exit-code-is-not-ok", EXIT_NOT_MIGRATED == EXIT_OK, False)
        ck("gate/exit-code-is-not-usage", EXIT_NOT_MIGRATED == EXIT_USAGE, False)
        ck("gate/exit-code-is-not-internal", EXIT_NOT_MIGRATED == EXIT_INTERNAL, False)

        _g = pathlib.Path(tmp) / "gatearms"
        _g.mkdir()
        for _gn, _gbody, _want_exists, _want_declared in (
                ("g_v4", "schema_version: v4\nproject: g\nsave: 1\n", True, "v4"),
                ("g_v3", "schema_version: v3\nproject: g\nsave: 1\n", True, "v3"),
                ("g_none", "project: g\nsave: 1\n", True, None),
                ("g_empty_value", "schema_version:\nproject: g\nsave: 1\n", True, None),
                ("g_indented", "  schema_version: v4\nproject: g\nsave: 1\n", True, None),
                # D23's rule, as code: a schema_version that lives only inside a code fence is
                # documentation, so the folder still reads as ABSENT, and ABSENT MEANS v3.
                ("g_fenced", "project: g\n```text\nschema_version: v4\n```\nsave: 1\n",
                 True, None)):
            _gr = _g / _gn
            _gr.mkdir()
            write_bytes(_gr / "state.md", _gbody.encode("utf-8"))
            ck("gate/read-%s" % _gn, read_declared_schema(_gr), (_want_exists, _want_declared))
        _gmissing = _g / "g_missing"
        _gmissing.mkdir()
        ck("gate/read-g_missing", read_declared_schema(_gmissing), (False, None))

        # require_migrated: only a literal v4 declaration proceeds. Everything else refuses,
        # including the two shapes D23 rules as v3 by absence.
        ck("gate/require-v4-proceeds",
           require_migrated(_g / "g_v4", "t", _err=io.StringIO()), None)
        for _gn in ("g_v3", "g_none", "g_empty_value", "g_indented", "g_fenced", "g_missing"):
            ck("gate/require-%s-refuses" % _gn,
               require_migrated(_g / _gn, "t", _err=io.StringIO()), EXIT_NOT_MIGRATED)


        # --- the two-parser split: the refusal must not describe the file it did not read ---
        # `main` gates through `read_declared_schema` (whole file) and `regenerate` gates
        # through `parse_state` (contiguous block only). MEASURED 2026-08-08 on the fixture
        # below - a column-0 `schema_version: v4` sitting below a blank line, so outside the
        # block - the refusal said the file carried no schema_version line while that WAS a
        # line of the file, and its remedy sent the operator to the migration skill, which
        # forwards an unreadable schema line on to a v3 migration. The fault is a FALSE
        # SENTENCE, not a wrong write: these arms also re-assert that nothing is written.
        _gsplit = _g / "g_schema_outside_block"
        _gsplit.mkdir()
        write_bytes(_gsplit / "state.md",
                    b"project: g\nsave: 1\n\nschema_version: v4\n")
        ck("gate/split-whole-file-finds-v4", read_declared_schema(_gsplit), (True, "v4"))
        ck("gate/split-block-finds-nothing",
           parse_state(_gsplit / "state.md")["fields"].get("schema_version", ""), "")
        _gsbefore = _read(_gsplit / "state.md")
        _gserr = io.StringIO()
        _gsprev, sys.stderr = sys.stderr, _gserr
        try:
            _gscode = main(["--root", str(_gsplit), "--save-num", "4",
                            "--checkpoint-ts", "T"], _out=_Sink())
        finally:
            sys.stderr = _gsprev
        _gstext = _gserr.getvalue()
        ck("gate/split-refuses", _gscode, EXIT_NOT_MIGRATED)
        ck("gate/split-untouched", _read(_gsplit / "state.md"), _gsbefore)
        # The arm the defect exists for, and it is checked against the FILE rather than
        # against a remembered string: if the message asserts that state.md lacks the line -
        # a claim about the file, not about a named span - a whole-file read must agree.
        ck("gate/split-no-unscoped-absence-claim",
           ("state.md carries no schema_version" not in _gstext)
           or read_declared_schema(_gsplit)[1] is None, True)
        # It names the reader that produced the finding, and reports the read that disagrees.
        ck("gate/split-names-its-reader", "parse_state" in _gstext, True)
        ck("gate/split-reports-whole-file-read", "WHOLE-FILE read" in _gstext, True)
        ck("gate/split-quotes-what-that-read-found", repr(SCHEMA_VERSION) in _gstext, True)

        # End to end through the CLI on a v3 folder: refuses, and the bytes do not move.
        _gcli = _g / "g_v3_cli"
        _gcli.mkdir()
        _write_fixture(_gcli, _FIXTURE_BLOCK_SCALARS_V3, b"\n", "state.md")
        _gbefore = _read(_gcli / "state.md")
        ck("gate/cli-v3-refuses",
           main(["--root", str(_gcli), "--save-num", "9", "--checkpoint-ts", "T"],
                _out=_Sink()), EXIT_NOT_MIGRATED)
        ck("gate/cli-v3-untouched", _read(_gcli / "state.md"), _gbefore)
        ck("gate/cli-v3-still-declares-v3",
           b"\nschema_version: v3\n" in _read(_gcli / "state.md"), True)
        # ...and the SECOND run refuses identically. The defect this replaces was silent on the
        # second run, because the first had already destroyed what the warning was about.
        ck("gate/cli-v3-refuses-again",
           main(["--root", str(_gcli), "--save-num", "9", "--checkpoint-ts", "T"],
                _out=_Sink()), EXIT_NOT_MIGRATED)
        ck("gate/cli-v3-untouched-again", _read(_gcli / "state.md"), _gbefore)
    except Exception as _exc:                                    # noqa: BLE001 - deliberate
        # F6b. The suite is ONE linear block, so before this an exception anywhere in it
        # propagated to cli(), which printed "internal error:" and returned 7 with NO FAIL lines
        # and NO "N checks, M failed" line at all -- every check after the throw silently
        # skipped. The exit stayed non-zero so there was never a false green, but the DIAGNOSTIC
        # was destroyed, which is precisely the failure regen_manifest.py's own comments record
        # four mutants landing on. MEASURED here 2026-08-03: the non-atomic-write mutant killed
        # the suite at an unrelated arm and reported 0 named failures.
        # Recording it as a check keeps the verdict non-zero AND says where it died.
        # The pair is (False, True) and NOT (str(exc), "") -- an exception whose str() is empty
        # would have compared EQUAL and recorded the abort as a PASSING check. MEASURED
        # 2026-08-03 by a reviewer against the first cut of this guard: a bare AssertionError()
        # mid-suite gave exit 0 and "115 checks, 0 failed" over a run that stopped at 115 of 236.
        # Nothing in this file can raise an empty-message exception today, so the shipped count
        # was honest -- but brief §8 asks verifiers to inject bare asserts, which arms it. A
        # guard for meaningless green checks that is itself a meaningless green check is the
        # exact shape this project keeps finding, so the message goes in the LABEL where it
        # cannot decide the verdict.
        ck("selftest/aborted-by-exception:%s:%s" % (type(_exc).__name__, str(_exc)[:120]),
           False, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [c for c in checks if not c[3]]
    for label, got, want, ok in checks:
        if not ok:
            sys.stderr.write("FAIL %s: got %r want %r\n" % (label, got, want))
    sys.stderr.write("%d checks, %d failed\n" % (len(checks), len(bad)))
    return EXIT_SELFTEST if bad else EXIT_OK


class _Sink(object):
    """Swallows report output during the selftest."""

    def write(self, _s):
        return None

    def flush(self):
        return None


if __name__ == "__main__":
    sys.exit(cli())
