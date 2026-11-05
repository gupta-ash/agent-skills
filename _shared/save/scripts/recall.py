#!/usr/bin/env python
"""recall.py -- the deterministic retrieval interface for save v4. EXACTLY FIVE VERBS.

Work item W2, built on W1's `citemap.py`. D34's reason for existing: without a named verb the
model invents an ad-hoc grep each time and silently gets wrong answers. `check` is the half that
matters -- it is gate `C-4 check`, and it is what proves v4 can move history into `_archive/`
without losing it. C-4: "resume without loss of fidelity: eviction degrades to a resolvable stub,
never a deletion." A stub is only resolvable if something resolves it; this is that something.

CONTRACT: five verbs, and there is no sixth. `list` (heading map for a file or destination),
    `get` (id -> exact span -> RAW BYTES), `find` (scoped search that ALWAYS covers the cold
    store), `check` (the gate), `frontier` (counts-only artifact inventory). The five-verb limit
    is C-7 (minimise the verb surface) and is itself ratified design, not an implementation
    convenience -- spec S8's table has five rows and `check --impl` is PARKED, not a sixth.
CONTRACT: EXIT CODES, STABLE ACROSS ALL FIVE VERBS -- a caller gates on these. Every reachable
    code is listed; none is omitted.
        0  success; for `check`, CLEAN (no dangling citation, no colliding id).
        1  `--selftest` only, and only when a check failed. No verb ever returns 1, so a 1 from
           a verb invocation is a bug in this file, not a corpus verdict -- and since the last
           pass it cannot happen by accident either: an uncaught exception is 7, not 1.
        2  usage or I/O error: missing/!dir --root, unknown family, bad regex, unreadable file,
           an --artifact that escapes --root or names cold storage, a non-relative --include.
           2 is argparse's own hardcoded error code, so nothing else may claim it.
        3  UNRESOLVED. `check`: dangling citations (and no collisions). `get`: the id is defined
           nowhere in scope. `list`: the path matched no file. `find`: no match.
        4  AMBIGUOUS. `check`: colliding ids (and no dangling). `get`: the id is defined at more
           than one site and no `--file` pinned which one -- `get` NEVER picks for you.
        5  BOTH (`check` only): dangling citations AND colliding ids.
        6  SCAN INTEGRITY FAILURE, from EVERY verb that reads a file, not just `check`. At
           least one file in scope ends with an OPEN code fence, so the scan of that file is
           unreliable and no answer computed from it is evidence. It outranks 0, 3, 4 and 5
           alike: those are verdicts about a corpus that was read, and this says it was not.
           The report or the span is still emitted -- an operator has to see what the truncated
           scan produced -- but the CODE says it is not an answer.
        7  INTERNAL ERROR: an uncaught exception. Distinct from 1 on purpose, so a caller can
           tell a broken tool from a failed selftest.
    3/4/5 read the same way in every verb: 3 = resolves to nothing, 4 = resolves to more than one
    thing, 5 = both at once. Anything non-zero other than 2 and 7 is a corpus verdict, not a
    crash -- except 6, which is a refusal to give a verdict at all.
INVARIANT: AN UNBALANCED FENCE VOIDS THE GATE; IT DOES NOT PASS IT. The fence state machine, on
    a file whose fence never closes, classifies every later line as fenced -- so the file
    contributes no definition and no citation, and `check` then reports "0 dangling, 0 colliding
    ... PASS: every citation resolves" over a file it did not read. VERIFIED 2026-08-02 on a
    fixture (`## D1`, an unclosed ```, `## D2`, two citations to undefined ids): exit 0, "1
    defined, 0 citations"; with the fence closed, exit 3, "2 defined, 1 citations". This is F65
    ("every generated view fails toward nothing here") landing on the check that exists to catch
    it, and nothing-here renders as clean. So the fence is NOT reset and the scan NOT resumed --
    the file and the OPENING line are named and the exit code says integrity, not verdict.
    `get`, `list`, `find` and `frontier` are the SAME root cause on the retrieval side, and they
    return 6 for it too. A hidden heading never terminates the previous entry's span, so `get`
    hands back a still-literal byte slice that silently contains the NEXT entry's body, while
    the hidden entry itself reads as "defined nowhere". Fixing this on `check` alone left both
    shapes live at exit 0 and exit 3 with nothing but new stderr text (VERIFIED 2026-08-02 on
    five fixtures) -- and a caller gates on $?, not on stderr. The caller that matters here is
    the migration's round-trip byte-compare, the thing that proves an evicted entry still
    returns its ORIGINAL bytes; clean success plus wrong bytes is the worst output this tool
    can produce. Every verb that reads a file therefore returns 6 when that file's scan is
    unsound, and `integrity_code` is the single place that decides it.
INVARIANT: EVERY caller-supplied path is resolved through `resolve_under_root`, and there is no
    other join. `pathlib` makes `root / <absolute>` == `<absolute>` -- the root is DISCARDED --
    so any verb taking a path had a way out of the declared scope, and `list`, which used the
    caller's own string as the `rel` it reported, printed another project's file under this
    project's root at exit 0 (VERIFIED 2026-08-02: it returned a PEER project's ids; `--json`
    put this project's `root` beside that foreign path -- no count is quoted here, because a
    corpus count rots on the next save and this file ships to every machine, C-30). That is
    D53 broken by the flag that
    declares the scope. A guard per verb is exactly how the first half of the class was closed
    and how the second half survived it, so absolutes are refused before the join and escapes
    after resolution, uniformly, at exit 2 -- never a silent drop, which reads as exit 3 and is
    a claim about the corpus rather than about the flag.
INVARIANT: `get` writes RAW BYTES to stdout -- no line-number prefix, no header, no trailing
    newline of its own. The span it emits is a literal byte slice of the file, so
    `recall get X | cmp - <(slice)` is a valid round-trip test and `list`+`get` over every
    id in a file reconstructs that file's bytes exactly. A line-number prefix, or stripping the
    span's trailing blank lines, silently destroys that property while still looking correct.
    Provenance (`file:start-end`) goes to STDERR so a pipe stays clean.
INVARIANT: `find` searches `_archive/**` AND `checkpoints/**` ALWAYS, appended after whatever
    scope the caller declared and NOT removable by `--exclude`. Spec S8.1 is the reason: "a miss
    in the hot set is not evidence of absence; it is the normal case." Once v4 moves history
    cold, search is the cold store's ONLY discovery channel -- a `find` that honoured an
    exclusion of `_archive/` would report absence of content that is present, which is F65.
INVARIANT: `frontier` NEVER reads `checkpoints/` or `_archive/` (spec S8.2, "Input scope: the
    artifact ... Never `checkpoints/` or `_archive/`"). Enforced on the path components of the
    ROOT-RELATIVE path -- `--root` + `--artifact` + the globbed remainder -- never on the
    artifact-relative remainder alone. Testing the remainder alone let the artifact CONSUME the
    forbidden component: `--artifact _archive` made `rel` = `backlog-done.md`, which names nothing
    forbidden, so the guard passed and the cold store was read at exit 0 with "cold paths
    refused: 0" (VERIFIED 2026-08-02; `--artifact checkpoints` read 13 files). `--artifact` is
    additionally required to stay INSIDE `--root`: `--artifact ../../dev/broker` inventoried a
    different project while the payload still reported this project's `root`, which is D53's
    declared-scope rule broken by the very flag that declares the scope. Both are usage errors
    (exit 2), not silent drops; the count of paths dropped by the per-file guard is reported.
INVARIANT: `frontier` is COUNTS ONLY. Spec S8.2 measured the fully-enumerated form at 4,729
    tokens = 118.2% of the cap it would have had, and rejected it as a digest. It emits no id
    list and no file list, ever -- `list` and `check` are where names live. It is in P1 only
    because W8's manifest router is required to name it.
INVARIANT: scope is DECLARED, never defaulted to cwd (D53), in every verb including `get`.
    `--root` is required and there is no fallback.
INVARIANT: files are read as BYTES and decoded with errors='replace'. Line numbers are computed
    on the bytes by splitting on CRLF | CR | LF, which reproduces citemap's
    normalise-then-split("\\n") decomposition exactly, so the two agree on every line number and
    `get`'s byte offsets are still exact. This is safe only because 0x0A and 0x0D can never occur
    inside a UTF-8 multi-byte sequence -- per-line decode therefore equals whole-file decode.
INVARIANT: an ENTRY's span runs from its own heading line to the byte before the next column-0
    ATX heading of the SAME OR HIGHER level (outside a fence), or EOF. Nested lower-level
    headings belong to the entry. Non-id headings terminate a span exactly as id headings do --
    an entry does not own the section after it merely because that section has no id.
EXTERNAL: `python -I` DOES NOT put the script's own directory on sys.path -- `-I` implies `-P`.
    VERIFIED on this box (3.12.3): `python -S -I` from `docs/tmp/` printed a sys.path whose first
    entry was `python312.zip`, and `import citemap` raised ModuleNotFoundError even with the file
    adjacent. So the sibling import below is done with an explicit sys.path insert of
    `Path(__file__).parent`. Without it this script cannot run in its own mandated run mode, and
    it fails at import time rather than anywhere informative.
EXTERNAL: spec S8 (the five-verb table, the `^## D1([^0-9]|$)` boundary), S8.2 (frontier: cost,
    bound, artifact-only input scope), S8.3 (over-ceiling restore; an omitted hot file joins
    `find`'s scope for the session). Plan rows W1 (citemap), W2 (this file), W8 (manifest router
    names `frontier`), W17 (consumes `check`'s collision list), W18. C-4, C-7, D34, D44, D53.
EXTERNAL: `rg` is a shell FUNCTION on this box, not a binary, and does not resolve from a Python
    process (spec S8). `find` is therefore pure `re`, never a subprocess.
REJECTED: a sixth verb. Several were obvious and useful -- `resolve` (id -> file:line without the
    body, which `check --json` already carries), `stub` (emit the eviction pointer W17 writes),
    `diff` (hot vs archived twin). Each was rejected on C-7 alone, not on merit. The verb surface
    is the interface the model has to remember under load; the moment it needs a lookup table the
    model is back to inventing greps, which is the failure D34 exists to stop.
REJECTED: `get` choosing a definition site when an id has several. The corpus has 67 colliding
    ids TODAY (VERIFIED, citemap on this project), all `decisions.md` vs its generated digest, so
    "pick the first" would return generated lossy text for two-thirds of this project's decisions
    while reporting success. Exit 4 and print the sites; the caller pins with `--file`.
REJECTED: trimming blank lines off the end of a span. It reads better and it breaks the only
    property that makes eviction auditable -- that the concatenation of an id's span with its
    neighbours' is byte-identical to the file. `--selftest` asserts that reconstruction.
REJECTED: making `check` pass by default on this corpus. It exits 5 here (VERIFIED). The corpus
    carries real dangling ids and real collisions; a gate tuned until it is green is not a gate.
    `--ignore ID` exists for ids DECLARED external (a peer project's `D74`), never inferred.
REJECTED: `--quiet` on `check`. The report IS the deliverable; the exit code alone cannot tell an
    operator which of 34 dangling ids is the one that matters.

Usage:
    python -S -I scripts/recall.py --root <project> list <path>
    python -S -I scripts/recall.py --root <project> get <id> [--file FILE[:LINE]]
    python -S -I scripts/recall.py --root <project> get <file> <id>
    python -S -I scripts/recall.py --root <project> find <pattern> [--fixed] [-i]
    python -S -I scripts/recall.py --root <project> check [--ignore ID]
    python -S -I scripts/recall.py --root <project> frontier [--artifact SUBDIR]
    python -S -I scripts/recall.py --selftest
"""
import argparse
import bisect
import json
import os
import pathlib
import re
import sys

# --- sibling import. `-I` implies `-P`, so sys.path[0] is NOT the script's directory. -----------
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    import citemap
except ImportError as _exc:                                     # pragma: no cover - fatal
    sys.stderr.write("error: cannot import citemap.py from %s: %s\n"
                     % (pathlib.Path(__file__).resolve().parent, _exc))
    sys.exit(2)

EXIT_OK = 0
EXIT_SELFTEST = 1
EXIT_USAGE = 2
EXIT_UNRESOLVED = 3
EXIT_AMBIGUOUS = 4
EXIT_BOTH = 5
EXIT_SCAN_INTEGRITY = 6
EXIT_INTERNAL = 7

# `find` may never lose these, whatever the caller declares (spec S8.1).
FIND_FORCED = ("_archive/**/*.md", "checkpoints/**/*.md")
# `frontier` may never read these, whatever the caller declares (spec S8.2).
FRONTIER_FORBIDDEN = ("_archive", "checkpoints")

_EOL_RE = re.compile(rb"\r\n|\r|\n")
_STATUS_RE = re.compile(r"^([A-Z]{3,})\b")


class _PipeSafe(object):
    """stdout proxy that treats a closed downstream pipe as end-of-output, not as a failure.

    `check | head` closes the pipe while the report is still being written. Without this the
    write raises BrokenPipeError out of the middle of a verb, the verdict the caller gates on is
    never returned, and the process exits 120 or 1. The bytes are already lost either way -- the
    consumer stopped reading -- so the only question is whether the EXIT CODE survives, and the
    contract says it must. `.buffer` is wrapped too: `get` writes its raw span there.

    The wrapped stream may be None. `python ... >&-` (stdout CLOSED, not piped) makes CPython
    set `sys.stdout` to None, so `self._s.write` raised AttributeError -- which is neither a
    BrokenPipeError nor an OSError, escaped every handler, and printed a traceback at exit 1
    from all five verbs (VERIFIED 2026-08-02; the docstring's own exit table says no verb ever
    returns 1). AttributeError is therefore caught alongside the pipe errors, and `.buffer` is
    always a sink object rather than sometimes None, so `get`'s `out.buffer.write` has
    something to call. A closed stdout is a caller saying "discard the output", not an error.
    """

    __slots__ = ("_s", "buffer")

    def __init__(self, stream, wrap_buffer=True):
        self._s = stream
        self.buffer = (_PipeSafe(getattr(stream, "buffer", None), False)
                       if wrap_buffer else None)

    def write(self, chunk):
        try:
            return self._s.write(chunk)
        except (BrokenPipeError, ValueError, OSError, AttributeError):
            return 0

    def flush(self):
        try:
            self._s.flush()
        except (BrokenPipeError, ValueError, OSError, AttributeError):
            pass

    def __getattr__(self, name):
        return getattr(self._s, name)


# --------------------------------------------------------------------------- byte/line layer

def byte_lines(data):
    """Byte spans of every line, terminator INCLUDED, one entry per citemap line.

    Splits on CRLF | CR | LF, which is the decomposition citemap produces by normalising then
    splitting on '\\n'. The trailing element after the last terminator is always present, so a
    file ending in a newline yields a final empty line exactly as str.split('\\n') does.
    """
    out = []
    start = 0
    for m in _EOL_RE.finditer(data):
        out.append((start, m.end()))
        start = m.end()
    out.append((start, len(data)))
    return out


def decode_line(seg):
    """One raw line slice -> text with its terminator removed. errors='replace', never raises."""
    if seg.endswith(b"\r\n"):
        seg = seg[:-2]
    elif seg.endswith(b"\n") or seg.endswith(b"\r"):
        seg = seg[:-1]
    return seg.decode("utf-8", errors="replace")


def load_doc(path):
    """(bytes, rows, open_fence_line) -- rows are (lineno, text, byte_start, byte_end, in_fence).

    The fence state machine mirrors citemap's byte for byte, including the treatment of the
    fence DELIMITER lines themselves: citemap `continue`s on them, so they are neither a
    definition nor a citation, and they are reported here as in_fence=True.

    The third element is the 1-based line a fence OPENED on and never closed, or None. It is
    returned rather than logged because every caller must decide what to do with it and none of
    them may ignore it: an unclosed fence means every heading below it is invisible, so a span
    runs past its real end and a defined id reads as undefined. Silence here is the F65 failure.

    A leading BOM is stripped from LINE 1's TEXT and from nothing else. `FENCE_RE`/`HEADING_RE`
    are anchored at `^` with at most three spaces of indent and U+FEFF is neither, so a file
    beginning BOM+``` would otherwise leave its rendered code block live to the
    scanner. The BYTE OFFSETS are deliberately left alone: they
    index the file as it sits on disk, `get` re-reads those bytes and slices with them, and an
    entry starting at byte 0 of a BOM'd file owns that BOM because the file does. Stripping the
    bytes instead would shift every offset by three and quietly break the round-trip compare
    that is the only reason these offsets are exact.
    """
    data = path.read_bytes()
    rows = []
    fence = None
    fence_line = None
    for lineno, (s, e) in enumerate(byte_lines(data), 1):
        text = decode_line(data[s:e])
        if lineno == 1 and text.startswith(citemap.BOM):
            text = text[1:]
        fm = citemap.FENCE_RE.match(text)
        if fm:
            marker = fm.group(1)
            if fence is None:
                fence = marker[0] * len(marker)
                fence_line = lineno
                rows.append((lineno, text, s, e, True))
                continue
            if marker[0] == fence[0] and len(marker) >= len(fence) and not fm.group(2).strip():
                fence = None
                fence_line = None
                rows.append((lineno, text, s, e, True))
                continue
        rows.append((lineno, text, s, e, fence is not None))
    return data, rows, (fence_line if fence is not None else None)


def warn_open_fence(rel, open_line):
    """The stderr warning every non-`check` verb owes its caller. Returns True when it fired."""
    if open_line is None:
        return False
    sys.stderr.write(
        "warning: SCAN INTEGRITY -- %s ends with an OPEN code fence (opened at line %d).\n"
        "         Every heading below that line is invisible to this scan, so a span may run\n"
        "         past its real end and a defined id may read as undefined. This result is\n"
        "         computed from a TRUNCATED scan. Close the fence, then re-run.\n"
        % (rel, open_line))
    return True


def integrity_code(unreliable, verdict):
    """The code a READ verb must return. Integrity outranks verdict, in every verb, always.

    `check` got this in the last pass and the four read verbs did not, so under an unterminated
    fence `get` still handed back a span that swallowed the WHOLE NEXT ENTRY at exit 0, and
    `get`/`list` still called a present definition "defined nowhere" at exit 3 (VERIFIED
    2026-08-02 on five fixtures: f_simple, f_nested, f_tilde, f_crlf, f_reopen). Only the
    stderr text had changed, and a caller does not read stderr -- it reads $?. The caller that
    matters is the migration's round-trip byte-compare, the thing that proves an evicted entry
    still returns its ORIGINAL bytes; clean success plus wrong bytes is the worst output this
    tool can produce and is precisely what `check`'s exit 6 was written to stop.

    So 6 outranks 0, 3 AND 4 here exactly as it outranks 3/4/5 in `check`: 0/3/4 are statements
    about a file that was read, and 6 says it was not. The bytes and the report are still
    emitted -- an operator needs to see what the truncated scan produced -- but the code says
    they are not evidence.
    """
    return EXIT_SCAN_INTEGRITY if unreliable else verdict


def load_ignore_file(path):
    """Ids DECLARED EXTERNAL, one per line, `#` opening a comment. Returns a set.

    Lifted out of `verb_check`, where it was inline, the moment it acquired a SECOND
    caller: `validate_entries.py`'s A14 reads the same register from a well-known path.
    Two hand-rolled parsers of one grammar is what architecture.md R-ARCH-5 rules out --
    it names five such duplications and the principle is the same one. This pair would
    have diverged SILENTLY AND ONLY IN PART, which is harder to notice than diverging
    outright: a reader that did not strip comments still handles a BARE id line
    correctly. It fails only on a line carrying a TRAILING comment, where the comment
    becomes part of the identifier and that one declaration stops matching -- so the
    faulty reader suppresses LESS, not more. MEASURED, not reasoned: removing the split
    below left the commented-id selftest arm GREEN, which is why a second arm exists for
    the trailing-comment case.

    OSError PROPAGATES, because the two callers owe their operators different answers and
    neither answer is this function's to pick. `check` refuses with EXIT_USAGE: the
    operator NAMED the file on the command line, so an unreadable one is a broken
    invocation. A14 reports the failure and carries on with NOTHING suppressed -- it reads
    a well-known path nobody asked it to read, and a warning-only limb that aborted over
    an optional file would block a save (C-53).
    """
    ids = set()
    text = pathlib.Path(path).read_bytes().decode("utf-8", errors="replace")
    for line in text.split("\n"):
        line = line.split("#", 1)[0].strip()
        if line:
            ids.add(line)
    return ids


def heading_map(rel, data, rows, families):
    """Every id-defining heading in one document, in file order, with its exact span.

    A span ends at the next column-0 heading of the same or higher level, whether or not that
    heading defines an id, or at EOF. `byte_end` is that heading's first byte, so
    data[byte_start:byte_end] is a literal slice and adjacent spans concatenate exactly.

    `end_line` is found by BISECTING the already-built row-start list, not by rescanning every
    row for every heading. The rescan was O(headings x lines) and measured quadratic on this box
    2026-08-02 -- 200 headings/4,201 lines = 0.025s, 1600/33,601 = 1.204s, i.e. 4x the time for
    2x the input. Cheap at today's sizes and not cheap on `decisions.md`, which is the file v4
    exists to shrink and which grows until v4 runs.
    """
    heads = []
    for lineno, text, s, _e, in_fence in rows:
        if in_fence:
            continue
        m = citemap.HEADING_RE.match(text)
        if not m:
            continue
        defn = citemap.find_definition(text, families)
        heads.append({"line": lineno, "level": len(m.group(1)), "byte_start": s,
                      "id": defn[0] if defn else None,
                      "suffix": defn[3] if defn else "",
                      "title": defn[2] if defn else citemap._clean_title(m.group(3))})
    row_starts = [r[2] for r in rows]          # strictly increasing: one entry per line
    row_lines = [r[0] for r in rows]
    out = []
    for i, h in enumerate(heads):
        end = len(data)
        for j in range(i + 1, len(heads)):
            if heads[j]["level"] <= h["level"]:
                end = heads[j]["byte_start"]
                break
        # the LAST row that starts strictly before `end` -- identical to the old linear scan
        k = bisect.bisect_left(row_starts, end) - 1
        end_line = row_lines[k] if k >= 0 else h["line"]
        if end_line < h["line"]:
            end_line = h["line"]
        if h["id"] is None:
            continue
        out.append({"file": rel, "id": h["id"], "title": h["title"], "level": h["level"],
                    "suffix": h["suffix"],
                    "start_line": h["line"], "end_line": end_line,
                    "lines": end_line - h["line"] + 1,
                    "byte_start": h["byte_start"], "byte_end": end,
                    "bytes": end - h["byte_start"]})
    return out


# --------------------------------------------------------------------------- scope helpers

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def resolve_under_root(root, spec, flag):
    """THE ONLY place a CALLER-SUPPLIED path is joined to --root. (rel_posix, path) or ValueError.

    `pathlib` makes `root / <absolute>` == `<absolute>`: the left operand is DISCARDED, silently
    and by design. Every verb that took a path from the caller therefore had a way out of the
    declared scope, and the one that did not re-derive its `rel` afterwards printed the foreign
    path under this project's root at exit 0 -- VERIFIED 2026-08-02: `list <an absolute path to a
    PEER project's decisions.md>` under `--root .` returned that peer's ids instead of this
    project's, and `--json` put this project's `root` beside that foreign file. `../` escaped the
    same way (VERIFIED the same day, same result, via a `../`-relative spec).

    NEITHER THE PATH NOR THE COUNT IS WRITTEN DOWN HERE, and that is C-30 rather than vagueness:
    this file ships to every machine, where a build-host path names nothing and a corpus count
    rots on the next save. The reproduction is the SHAPE -- any absolute or `../` spec under a
    `--root` that does not contain it -- and the shape is what the arms below exercise.

    That is D53 broken by the flag that declares the scope: a project-scoped map resolving
    confidently over a project it was never pointed at. A guard per verb is how the first half
    of this class was closed and how the second half survived, so there is one function and
    every verb calls it. Absolutes are refused BEFORE the join (the join cannot report what it
    discarded); escapes are refused after resolution, because `..` only shows up once the path
    is normalised. Refusal is a usage error (exit 2), never a silent drop -- a dropped path
    reads as "not found" (exit 3), which is a claim about the corpus rather than about the flag.

    Returns the ROOT-RELATIVE posix path and the RESOLVED path. The rel is re-derived here and
    never taken from the caller's string, so nothing downstream can print an unanchored path.
    """
    s = str(spec).replace("\\", "/")
    if s.startswith("/") or s.startswith("//") or _DRIVE_RE.match(s) \
            or pathlib.PurePosixPath(s).is_absolute():
        raise ValueError(
            "%s %r is an ABSOLUTE path. Paths are RELATIVE to --root: the scope is DECLARED "
            "(D53),\n       and `root / <absolute>` discards the root entirely, so an absolute "
            "path reads a\n       different project while this one's root is what gets "
            "reported." % (flag, spec))
    p = root / s
    try:
        rp = p.resolve()
    except OSError as exc:
        raise ValueError("%s %r cannot be resolved: %s" % (flag, spec, exc))
    try:
        rel = rp.relative_to(root)
    except ValueError:
        raise ValueError(
            "%s %r resolves to %s, which is OUTSIDE --root (%s). Every\n       path this tool "
            "accepts names something INSIDE the declared project; reporting\n       another "
            "project's content under this project's root is exactly what D53 forbids."
            % (flag, spec, rp.as_posix(), root.as_posix()))
    return (rel.as_posix() if rel.parts else "."), rp


def resolve_scope(args):
    """Declared patterns for this invocation. --include overrides --scope; never both."""
    if args.include:
        if args.scope:
            raise ValueError("pass --scope or --include, not both")
        return tuple(args.include)
    return citemap.SCOPES[args.scope or citemap.DEFAULT_SCOPE]


def families_from(args, root):
    """The families for this invocation: built-ins, widened by the project's declaration.

    `root` and not `args.root`: the caller has already resolved and directory-checked it, and
    `get` on a cold id is exactly the verb the declaration exists to make work -- an id from an
    unregistered family is unfetchable once the drain has moved it.
    """
    names = [s.strip() for s in args.families.split(",") if s.strip()] if args.families else None
    families, problems = citemap.project_families(root, names, args.family)
    for problem in problems:
        sys.stderr.write("warning: %s\n" % problem)
    return families


def maps_for(root, patterns, families, excludes=(), files=None):
    """[(rel, data, rows, entries, open_fence_line)] for every file in the declared scope.

    `files` overrides globbing with an EXPLICIT [(rel, path)] list -- see citemap.build_map for
    why a kept path must never be fed back in as a glob pattern.
    """
    docs = []
    src = files if files is not None else citemap.collect_files(root, patterns, excludes)
    for rel, path in src:
        data, rows, open_line = load_doc(path)
        docs.append((rel, data, rows, heading_map(rel, data, rows, families), open_line))
    return docs


def literal_or_glob(root, spec, excludes=()):
    """[(rel, path)] for a caller-supplied path spec, LITERAL first, glob second.

    `get --file 'a[1].md'` must pin `a[1].md`, not glob it. A path is not a pattern: `[1]` is a
    character class, so the literal file matches nothing and `get` refused the very pin its own
    ambiguity message told the caller to use (VERIFIED 2026-08-02: exit 4 printing
    `--file a[1].md`, then exit 3 "defined nowhere" on that exact flag, while `list a[1].md`
    resolved it). Filenames win over patterns because a filename is what a caller copies out of
    an error message. The join goes through `resolve_under_root`, so an absolute or escaping
    `--file` is a usage error here rather than a literal miss that falls through to the glob.
    """
    rel_spec, p = resolve_under_root(root, spec, "--file")
    try:
        if p.is_file():
            return [(rel_spec, p)]
    except OSError:
        pass
    return citemap.collect_files(root, (spec,), excludes)


# --------------------------------------------------------------------------- verb: list

def verb_list(args, root, families, out):
    # `resolve_under_root` and NOT `root / target`: this line is where the escape lived. The
    # `is_file()` branch below used the CALLER'S string as the reported `rel`, so an absolute
    # target read another project and printed its path under this project's root at exit 0.
    target, p = resolve_under_root(root, args.path, "list")
    paths = []
    if p.is_file():
        paths = [(target, p)]
    elif p.is_dir():
        pat = (target + "/**/*.md") if target != "." else "**/*.md"
        paths = citemap.collect_files(root, (pat,), tuple(args.exclude))
    else:
        paths = citemap.collect_files(root, (target,), tuple(args.exclude))
    if not paths:
        sys.stderr.write("error: no file matched %r under %s\n" % (target, root))
        return EXIT_UNRESOLVED

    docs = []
    unreliable = []
    for rel, path in paths:
        try:
            data, rows, open_line = load_doc(path)
        except OSError as exc:
            sys.stderr.write("error: cannot read %s: %s\n" % (rel, exc))
            return EXIT_USAGE
        warn_open_fence(rel, open_line)
        if open_line is not None:
            unreliable.append((rel, open_line))
        docs.append((rel, len(data), len(rows), heading_map(rel, data, rows, families),
                     open_line))

    if args.json:
        out.write(json.dumps({
            "root": root.as_posix(),
            "target": target,
            "files": [{"file": rel, "bytes": nb, "lines": nl,
                       "unterminated_fence_line": of,
                       "entries": [{k: e[k] for k in ("id", "title", "level", "start_line",
                                                      "end_line", "lines", "byte_start",
                                                      "byte_end", "bytes", "suffix")}
                                   for e in ents]}
                      for rel, nb, nl, ents, of in docs],
            "totals": {"files": len(docs), "entries": sum(len(e) for _, _, _, e, _ in docs),
                       "unterminated_fences": sum(1 for d in docs if d[4] is not None)},
        }, indent=2) + "\n")
        return integrity_code(unreliable, EXIT_OK)

    total = 0
    for rel, nb, nl, ents, open_line in docs:
        out.write("%s  (%d lines, %d bytes, %d ids)%s\n"
                  % (rel, nl, nb, len(ents),
                     "   [UNRELIABLE: open fence at line %d]" % open_line
                     if open_line is not None else ""))
        if ents:
            out.write("  %-12s %-15s %7s  %s\n" % ("ID", "LINES", "BYTES", "TITLE"))
            for e in ents:
                # The heading says `D1a`; the id S8's boundary resolves is `D1`. Say so here,
                # or `get D1` hands back D1a's bytes and nothing ever mentioned the difference.
                mark = "  [PARTIAL: heading reads %s%s]" % (e["id"], e["suffix"]) \
                    if e["suffix"] else ""
                out.write("  %-12s %-15s %7d  %s%s\n"
                          % (e["id"], "%d-%d" % (e["start_line"], e["end_line"]),
                             e["bytes"], e["title"][:60], mark))
        total += len(ents)
    out.write("\n%d file(s), %d ids defined\n" % (len(docs), total))
    if unreliable:
        out.write("REFUSED (exit %d): %d file(s) above end with an OPEN code fence, so every\n"
                  "heading below the opening line is invisible and the spans printed here may\n"
                  "run past their real end. This map is NOT evidence. Close the fence, re-run.\n"
                  % (EXIT_SCAN_INTEGRITY, len(unreliable)))
    return integrity_code(unreliable, EXIT_OK)


# --------------------------------------------------------------------------- verb: get

def verb_get(args, root, families, out):
    ident = args.ident
    pin_file, pin_line = None, None
    if args.file:
        spec = args.file.replace("\\", "/")
        m = re.match(r"^(.*):(\d+)$", spec)
        if m:
            pin_file, pin_line = m.group(1), int(m.group(2))
        else:
            pin_file = spec

    patterns = resolve_scope(args)
    pinned = None
    if pin_file:
        patterns = (pin_file,)
        pinned = literal_or_glob(root, pin_file, tuple(args.exclude))
        if not pinned:
            sys.stderr.write("error: --file %s matched no file under %s\n" % (pin_file, root))
            return EXIT_UNRESOLVED
    sites = []
    unreliable = []
    for rel, _data, _rows, ents, open_line in maps_for(root, patterns, families,
                                                       tuple(args.exclude), files=pinned):
        if open_line is not None:
            unreliable.append((rel, open_line))
        for e in ents:
            if e["id"] == ident and (pin_line is None or e["start_line"] == pin_line):
                sites.append((rel, e))
    for rel, open_line in unreliable:
        warn_open_fence(rel, open_line)
    if not sites:
        sys.stderr.write("error: %s is defined nowhere in scope %s under %s\n"
                         % (ident, " ".join(patterns), root))
        if unreliable:
            sys.stderr.write("       ...but %d file(s) in scope have an unterminated fence, so "
                             "'nowhere' is NOT evidence of absence.\n" % len(unreliable))
        return integrity_code(unreliable, EXIT_UNRESOLVED)
    if len(sites) > 1:
        sys.stderr.write("error: %s is defined at %d sites -- pin one with --file "
                         "(get NEVER picks for you):\n" % (ident, len(sites)))
        for rel, e in sites:
            sys.stderr.write("    --file %s:%d      %s\n" % (rel, e["start_line"], e["title"][:60]))
        return integrity_code(unreliable, EXIT_AMBIGUOUS)

    rel, e = sites[0]
    data = (root / rel).read_bytes()
    if e["suffix"]:
        sys.stderr.write("warning: PARTIAL id -- the heading reads `%s%s`, this span was matched "
                         "as `%s`\n         under spec S8's ratified `([^0-9]|$)` right "
                         "boundary. They are different strings.\n"
                         % (e["id"], e["suffix"], e["id"]))
    sys.stderr.write("%s:%d-%d (%d bytes)\n" % (rel, e["start_line"], e["end_line"], e["bytes"]))
    # `out.buffer`, not `out`: the payload is BYTES. sys.stdout.buffer bypasses the text layer
    # entirely, so no encode, no newline translation, no BOM -- the slice as it sits on disk.
    out.buffer.write(data[e["byte_start"]:e["byte_end"]])
    out.buffer.flush()
    if unreliable:
        # The bytes went out. The exit code is what says they are not the entry's bytes: under
        # an open fence the next heading is invisible, so this span runs to the file's end and
        # carries the WHOLE of every entry below it. Exit 0 here is the silent-wrong-bytes case.
        sys.stderr.write("error: REFUSED (exit %d) -- the span above was cut by a TRUNCATED "
                         "scan and may\n       contain entries that are not %s. It is emitted "
                         "so you can see what the scan\n       produced; it is NOT %s's "
                         "bytes.\n" % (EXIT_SCAN_INTEGRITY, ident, ident))
    return integrity_code(unreliable, EXIT_OK)


# --------------------------------------------------------------------------- verb: find

def verb_find(args, root, families, out):
    try:
        rx = re.compile(re.escape(args.pattern) if args.fixed else args.pattern,
                        re.IGNORECASE if args.ignore_case else 0)
    except re.error as exc:
        sys.stderr.write("error: bad --pattern regex: %s\n" % exc)
        return EXIT_USAGE

    declared = resolve_scope(args)
    patterns = tuple(declared) + tuple(p for p in FIND_FORCED if p not in declared)
    files = citemap.collect_files(root, patterns, tuple(args.exclude))
    # The cold store is NOT removable. Anything --exclude dropped from it is put back.
    have = {rel for rel, _ in files}
    for rel, path in citemap.collect_files(root, FIND_FORCED, ()):
        if rel not in have:
            files.append((rel, path))
    files.sort(key=lambda t: t[0])

    hits = []
    zones = {"hot": 0, "_archive": 0, "checkpoints": 0}
    unreliable = []
    for rel, path in files:
        zone = rel.split("/")[0] if rel.split("/")[0] in ("_archive", "checkpoints") else "hot"
        zones[zone] += 1
        try:
            _data, rows, open_line = load_doc(path)
        except OSError as exc:
            sys.stderr.write("error: cannot read %s: %s\n" % (rel, exc))
            return EXIT_USAGE
        if open_line is not None:
            unreliable.append((rel, open_line))
        for lineno, text, _s, _e, _fence in rows:
            if rx.search(text):
                hits.append({"file": rel, "line": lineno, "zone": zone, "text": text.strip()})
    for rel, open_line in unreliable:
        warn_open_fence(rel, open_line)

    shown = hits if args.max <= 0 else hits[:args.max]
    if args.json:
        out.write(json.dumps({
            "root": root.as_posix(), "pattern": args.pattern, "fixed": bool(args.fixed),
            "scope": list(patterns), "files_searched": zones,
            "totals": {"matches": len(hits), "shown": len(shown),
                       "files_with_matches": len({h["file"] for h in hits})},
            "matches": shown,
        }, indent=2) + "\n")
        return integrity_code(unreliable, EXIT_OK if hits else EXIT_UNRESOLVED)

    out.write("find %r in %s\n" % (args.pattern, root.as_posix()))
    out.write("  searched: %d hot, %d _archive, %d checkpoints  (cold store ALWAYS searched)\n"
              % (zones["hot"], zones["_archive"], zones["checkpoints"]))
    for h in shown:
        out.write("%s:%d: %s\n" % (h["file"], h["line"], h["text"][:160]))
    if len(shown) < len(hits):
        out.write("... TRUNCATED: showing %d of %d matches (use --max 0)\n" % (len(shown), len(hits)))
    out.write("\n%d match(es) in %d file(s)\n" % (len(hits), len({h["file"] for h in hits})))
    if unreliable:
        out.write("REFUSED (exit %d): %d searched file(s) end with an OPEN code fence. `find`\n"
                  "reads every line whatever its fence state, so the hits above are real -- but\n"
                  "the file they came from is not being scanned the way a renderer reads it, and\n"
                  "a MISS in such a file is not evidence of absence. Close the fence, re-run.\n"
                  % (EXIT_SCAN_INTEGRITY, len(unreliable)))
    return integrity_code(unreliable, EXIT_OK if hits else EXIT_UNRESOLVED)


# --------------------------------------------------------------------------- verb: check

def verb_check(args, root, families, out):
    """Gate C-4. Every citation in the declared scope resolves; every id is unique hot+archive."""
    patterns = resolve_scope(args)
    ignore = set(args.ignore or ())
    if args.ignore_file:
        try:
            ignore |= load_ignore_file(args.ignore_file)
        except OSError as exc:
            sys.stderr.write("error: cannot read --ignore-file: %s\n" % exc)
            return EXIT_USAGE

    try:
        mp = citemap.build_map(root, patterns, families, tuple(args.exclude))
    except (ValueError, IOError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return EXIT_USAGE
    if not mp["totals"]["files"]:
        sys.stderr.write("error: scope %s matched no files under %s\n"
                         % (" ".join(patterns), root))
        return EXIT_USAGE

    by_id = {r["id"]: r for r in mp["ids"]}
    dangling = [i for i in mp["dangling"] if i not in ignore]
    collisions = [i for i in mp["collisions"] if i not in ignore]
    unterminated = mp["unterminated_fences"]
    partial = mp["partial_ids"]

    # Second pass: WHERE each dangling id is cited. citemap counts per file; a report that says
    # "cited 41x in research.md" is not actionable, and `check`'s job is to say exactly where.
    want = set(dangling)
    sites = {i: [] for i in dangling}
    if want:
        for rel, path in citemap.collect_files(root, patterns, tuple(args.exclude)):
            _data, rows, _open = load_doc(path)
            for lineno, text, _s, _e, in_fence in rows:
                if in_fence:
                    continue
                for ident, _a, _b in citemap.scan_occurrences(text, families):
                    if ident in want:
                        sites[ident].append({"file": rel, "line": lineno, "text": text.strip()})

    n_d, n_c = len(dangling), len(collisions)
    # INTEGRITY OUTRANKS VERDICT. 3/4/5 are statements about a corpus that was read; 6 says it
    # was not. `check` asserts "every citation in scope resolves" -- over a file whose fence
    # never closed, that assertion has no evidence either way, and the truncated scan's own
    # answer is the CLEAN one. Returning 0 here is the exact failure this gate exists to stop.
    if unterminated:
        code = EXIT_SCAN_INTEGRITY
    elif not (n_d or n_c):
        code = EXIT_OK
    elif n_d and n_c:
        code = EXIT_BOTH
    else:
        code = EXIT_UNRESOLVED if n_d else EXIT_AMBIGUOUS

    if args.json:
        out.write(json.dumps({
            "root": root.as_posix(), "scope": list(patterns),
            "ignored": sorted(ignore),
            "totals": dict(mp["totals"], dangling=n_d, collisions=n_c),
            "unterminated_fences": unterminated,
            "partial_ids": [{"id": i, "sites": by_id[i]["partial"]} for i in partial],
            "dangling": [{"id": i, "citations": by_id[i]["citations"],
                          "sites": sites[i][:args.max] if args.max > 0 else sites[i]}
                         for i in dangling],
            "collisions": [{"id": i, "definitions": by_id[i]["definitions"],
                            "citations": by_id[i]["citations"]} for i in collisions],
            "exit": code,
        }, indent=2) + "\n")
        if unterminated:
            sys.stderr.write(citemap.fence_warning(mp))
        return code

    t = mp["totals"]
    out.write("check: %s\n" % root.as_posix())
    out.write("  scope     : %s\n" % " ".join(patterns))
    out.write("  files     : %d (%d lines)\n" % (t["files"], t["lines"]))
    out.write("  ids       : %d  (%d defined, %d definition sites, %d citations)\n"
              % (t["ids"], t["defined"], t["definition_sites"], t["citations"]))
    if ignore:
        out.write("  ignored   : %s\n" % " ".join(sorted(ignore)))
    if partial:
        out.write("  partial   : %d id(s) whose heading text CONTINUES the id matched\n"
                  % len(partial))
    out.write("  VERDICT   : %s\n"
              % ("SCAN INTEGRITY FAILURE -- %d file(s) end with an open fence; no verdict "
                 "is possible" % len(unterminated) if unterminated
                 else "%d dangling, %d colliding" % (n_d, n_c)))

    if unterminated:
        out.write("\nUNTERMINATED FENCES (%d) -- this scan is UNRELIABLE and NO verdict is being\n"
                  "made. Everything below each opening line read as fenced, so those files\n"
                  "contributed no definition and no citation -- and an empty contribution is\n"
                  "indistinguishable from a clean one. Close the fence, then re-run the gate.\n"
                  % len(unterminated))
        for u in unterminated:
            out.write("  %s:%d  opened with %s, never closed\n"
                      % (u["file"], u["line"], u["marker"]))
        out.write("\n  The counts below were computed from that truncated scan. Read them as\n"
                  "  a LOWER BOUND, never as a result.\n")
    if partial:
        out.write("\nPARTIAL-ID HEADINGS (%d) -- the heading text continues the id it matched, so\n"
                  "the string a reader sees and the id resolved here differ. Spec S8's\n"
                  "`([^0-9]|$)` boundary is ratified (C-18); this is a signal, not a defect fix.\n"
                  % len(partial))
        for i in partial:
            for p in by_id[i]["partial"]:
                out.write("  %-12s %s\n" % (i, p))

    if collisions:
        out.write("\nCOLLIDING IDS (%d) -- one id, more than one column-0 heading. An id that\n"
                  "resolves to two bodies cannot be a stub target; W17 renames the archive twin.\n"
                  % n_c)
        for i in collisions:
            out.write("  %-12s cited %dx\n" % (i, by_id[i]["citations"]))
            for d in by_id[i]["definitions"]:
                out.write("      %s:%d  %s\n" % (d["file"], d["line"], (d["title"] or "")[:60]))
    if dangling:
        out.write("\nDANGLING CITATIONS (%d) -- cited in scope, defined nowhere in scope. Each is\n"
                  "a reference that would survive an eviction pointing at nothing.\n" % n_d)
        for i in dangling:
            out.write("  %-12s cited %dx\n" % (i, by_id[i]["citations"]))
            shown = sites[i] if args.max <= 0 else sites[i][:args.max]
            for s in shown:
                out.write("      %s:%d: %s\n" % (s["file"], s["line"], s["text"][:110]))
            if len(shown) < len(sites[i]):
                out.write("      ... %d more site(s) (use --max 0)\n" % (len(sites[i]) - len(shown)))

    out.write("\n%s (exit %d)\n"
              % ("REFUSED: the scan is not sound, so no closure claim is made."
                 if code == EXIT_SCAN_INTEGRITY else
                 "PASS: every citation resolves and every id is unique."
                 if code == EXIT_OK else
                 "FAIL: the declared scope is not referentially closed.", code))
    if unterminated:
        sys.stderr.write(citemap.fence_warning(mp))
    return code


# --------------------------------------------------------------------------- verb: frontier

def _id_number(ident, families):
    """The id's NUMBER part, via the family regex's own capture group. '' when there isn't one.

    Not `re.findall(r'\\d+', ident)[-1]`: that reads `M4.5` as 5 and `OQ-V2-14` as 14, so one
    family's range arithmetic is right only by accident. The family core carries exactly one
    inner group and it is the number -- `D(\\d+)`, `R-[A-Z]{2,6}-(\\d+)`, `M(\\d+(?:\\.\\d+)*)`.
    Tested with `m.re.groups`, NOT `m.lastindex`: the groups are NESTED (citemap wraps the core
    in its own group, giving `(D(\\d+))`), and for a nested pair the OUTER group closes last, so
    lastindex is 1 even though group 2 matched. That read silently returned '' for every family
    and blanked the whole RANGE column.
    """
    for fam in families:
        m = fam.anchored.match(ident)
        if m and m.end(1) == len(ident) and m.re.groups >= 2 and m.group(2) is not None:
            return m.group(2)
    return ""


def verb_frontier(args, root, families, out):
    """Counts only. Artifact scope. Never checkpoints/, never _archive/ (spec S8.2)."""
    # The guard must be anchored at --root, not at the artifact. Both halves were escapable:
    #   `--artifact ../../dev/broker` inventoried a DIFFERENT project while the payload still
    #   printed this project's `root` -- D53's declared scope broken by the scope flag itself;
    #   `--artifact _archive` made the forbidden component part of the artifact, so the
    #   per-file `rel` no longer contained it and the cold store was read at "refused: 0".
    # The first half is now `resolve_under_root`'s, shared with `list` and `get --file`, because
    # this guard was written here and the same class then survived in the two verbs that did
    # not have one. The cold-storage half stays: it is frontier's alone (spec S8.2).
    try:
        art_rel_posix, art = resolve_under_root(root, args.artifact or ".", "--artifact")
    except ValueError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return EXIT_USAGE
    if not art.is_dir():
        sys.stderr.write("error: --artifact is not a directory: %s\n" % art)
        return EXIT_USAGE
    art_rel = pathlib.PurePosixPath(art_rel_posix)
    if any(part in FRONTIER_FORBIDDEN for part in art_rel.parts):
        sys.stderr.write("error: --artifact %r names cold storage (%s). `frontier` NEVER reads\n"
                         "       checkpoints/ or _archive/ (spec S8.2) -- and naming one AS the\n"
                         "       artifact does not make it warm. Use `list` or `find` for those.\n"
                         % (args.artifact, "/".join(art_rel.parts)))
        return EXIT_USAGE
    # Default to the artifact's own top-level documents, not recursive foreign
    # corpora, temporary fixtures or nested projects that happen to sit below it.
    # Widen deliberately with --artifact, --scope or --include; do not widen by default.
    # resolve_scope, NOT args.include: reading args.include directly discarded --scope in
    # silence, so all four scope values produced byte-identical output (VERIFIED 2026-08-02).
    patterns = resolve_scope(args) if (args.include or args.scope) else ("*.md",)

    kept, dropped = [], 0
    art_parts = tuple(p for p in art_rel.parts if p != ".")
    for rel, path in citemap.collect_files(art, patterns, tuple(args.exclude)):
        if any(part in FRONTIER_FORBIDDEN
               for part in art_parts + pathlib.PurePosixPath(rel).parts):
            dropped += 1
            continue
        kept.append((rel, path))
    if not kept:
        sys.stderr.write("error: artifact scope %s matched no files under %s\n"
                         % (" ".join(patterns), art))
        return EXIT_UNRESOLVED

    # `files=kept`, never `tuple(rel for rel, _ in kept)`: the kept RELATIVE PATHS are not glob
    # PATTERNS. A filename holding `[` or `]` re-globs elsewhere and drops out of the id scan
    # while still counting toward the files and bytes printed below -- one table, two file sets
    # (VERIFIED 2026-08-02: `a[1].md` + `b.md` reported 2 files and scanned 1).
    mp = citemap.build_map(art, patterns, families, (), files=kept)
    unreliable = mp["unterminated_fences"]
    if unreliable:
        sys.stderr.write(citemap.fence_warning(mp))
    nbytes = 0
    files_with_ids = set()
    status = {}
    per_family = {}
    for rel, path in kept:
        nbytes += path.stat().st_size
    for r in mp["ids"]:
        fam = r["family"]
        d = per_family.setdefault(fam, {"defined": 0, "cited": 0, "nums": [], "sub": 0})
        d["cited"] += r["citations"]
        if r["definitions"]:
            d["defined"] += 1
            for defn in r["definitions"]:
                files_with_ids.add(defn["file"])
            m = _STATUS_RE.match(r["title"] or "")
            if m:
                status[m.group(1)] = status.get(m.group(1), 0) + 1
            num = _id_number(r["id"], families)
            if num.isdigit():
                d["nums"].append(int(num))
            elif num:
                d["sub"] += 1        # a decimal extension (M4.5) has no place on an integer axis
    fam_rows = []
    for fam in sorted(per_family, key=lambda f: -per_family[f]["defined"]):
        d = per_family[fam]
        lo = min(d["nums"]) if d["nums"] else None
        hi = max(d["nums"]) if d["nums"] else None
        gaps = (hi - lo + 1 - len(set(d["nums"]))) if d["nums"] else 0
        fam_rows.append({"family": fam, "defined": d["defined"], "cited": d["cited"],
                         "low": lo, "high": hi, "gaps": gaps, "sub": d["sub"]})

    payload = {
        "root": root.as_posix(),
        "artifact": art.as_posix(),
        "scope": list(patterns),
        "excluded_cold_paths": dropped,
        "totals": {"files": len(kept), "bytes": nbytes, "lines": mp["totals"]["lines"],
                   "ids_defined": mp["totals"]["defined"],
                   "definition_sites": mp["totals"]["definition_sites"],
                   "citations": mp["totals"]["citations"],
                   "unresolved_ids": mp["totals"]["dangling"],
                   "colliding_ids": mp["totals"]["collisions"],
                   "unmapped_files": len(kept) - len(files_with_ids)},
        "families": fam_rows,
        "status_tokens": dict(sorted(status.items(), key=lambda kv: -kv[1])),
    }
    if args.json:
        out.write(json.dumps(payload, indent=2) + "\n")
        return integrity_code(unreliable, EXIT_OK)

    t = payload["totals"]
    out.write("frontier: %s\n" % payload["artifact"])
    out.write("  scope     : %s   (cold paths refused: %d)\n"
              % (" ".join(patterns), dropped))
    out.write("  files     : %d (%d lines, %d bytes)   unmapped (define no id): %d\n"
              % (t["files"], t["lines"], t["bytes"], t["unmapped_files"]))
    out.write("  ids       : %d defined at %d sites, %d citations\n"
              % (t["ids_defined"], t["definition_sites"], t["citations"]))
    out.write("  unresolved: %d      colliding: %d\n"
              % (t["unresolved_ids"], t["colliding_ids"]))
    out.write("  %-8s %8s %8s %10s %6s %5s\n"
              % ("FAMILY", "DEFINED", "CITED", "RANGE", "GAPS", "SUB"))
    for r in fam_rows:
        out.write("  %-8s %8d %8d %10s %6d %5d\n"
                  % (r["family"], r["defined"], r["cited"],
                     "-" if r["low"] is None else "%d..%d" % (r["low"], r["high"]),
                     r["gaps"], r["sub"]))
    if payload["status_tokens"]:
        out.write("  status    : %s\n" % "  ".join("%s=%d" % kv
                                                   for kv in payload["status_tokens"].items()))
    out.write("\ncounts only by design (spec S8.2). Names: `list`, `check`, `find`.\n")
    if unreliable:
        out.write("REFUSED (exit %d): %d inventoried file(s) end with an OPEN code fence, so\n"
                  "every count above is a LOWER BOUND computed from a truncated scan, not an\n"
                  "inventory. Close the fence, then re-run.\n"
                  % (EXIT_SCAN_INTEGRITY, len(unreliable)))
    return integrity_code(unreliable, EXIT_OK)


# --------------------------------------------------------------------------- selftest

def _selftest():
    import tempfile
    import shutil
    import io as io_mod
    checks = []

    def ck(label, got, want):
        checks.append((label, got, want, got == want))

    fams = citemap.build_families()

    # -- byte_lines reproduces citemap's line decomposition, terminator by terminator.
    for raw in (b"", b"a", b"a\n", b"a\nb", b"a\r\nb\r\n", b"a\rb", b"a\n\n\nb\n"):
        want = raw.decode().replace("\r\n", "\n").replace("\r", "\n").split("\n")
        got = [decode_line(raw[s:e]) for s, e in byte_lines(raw)]
        ck("line split %r" % raw, got, want)

    doc = ("# Head\n"
           "\n"
           "preamble cites D9\n"
           "\n"
           "## D1: first entry\n"
           "body of D1, cites D2\n"
           "\n"
           "### Sub-heading, no id, inside D1's span\n"
           "more body\n"
           "\n"
           "## Not an id heading\n"
           "this terminates D1\n"
           "\n"
           "## D2: second entry\n"
           "```\n"
           "## D999: fenced, not a definition\n"
           "```\n"
           "tail\n")
    tmp = tempfile.mkdtemp(prefix="recall_selftest_")
    try:
        p = pathlib.Path(tmp) / "decisions.md"
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(doc)
        data, rows, _of = load_doc(p)
        ents = heading_map("decisions.md", data, rows, fams)
        idx = {e["id"]: e for e in ents}

        ck("ids in file order", [e["id"] for e in ents], ["D1", "D2"])
        ck("D999 fenced is not an entry", "D999" in idx, False)
        ck("D1 starts at line 5", idx["D1"]["start_line"], 5)
        ck("D1 ends before the next h2", idx["D1"]["end_line"], 10)
        ck("D1 span swallows its sub-heading",
           "### Sub-heading" in data[idx["D1"]["byte_start"]:
                                     idx["D1"]["byte_end"]].decode(), True)
        ck("a NON-id heading terminates a span",
           data[idx["D1"]["byte_end"]:].decode().startswith("## Not an id heading"), True)
        ck("D2 runs to EOF", idx["D2"]["byte_end"], len(data))
        ck("span is a literal byte slice",
           data[idx["D1"]["byte_start"]:idx["D1"]["byte_end"]].decode().startswith("## D1: first"),
           True)
        # RECONSTRUCTION: preamble + every top-level span, concatenated, IS the file. Proved on a
        # doc whose every h2 carries an id -- with a non-id h2 present the id spans do not tile
        # the file, which is correct and is what the previous two checks assert.
        r = pathlib.Path(tmp) / "recon.md"
        with open(r, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Head\n\nlead\n\n## D3: a\none\n\n## D4: b\ntwo\n")
        rd, rr, _of = load_doc(r)
        re_ents = heading_map("recon.md", rd, rr, fams)
        rebuilt = rd[:re_ents[0]["byte_start"]] + b"".join(
            rd[e["byte_start"]:e["byte_end"]] for e in re_ents)
        ck("reconstruction is byte-identical", rebuilt, rd)
        os.remove(str(r))

        # -- heading_map agrees with citemap on every definition site. If these drift, `get`
        #    returns a span for an id `check` does not believe is defined there.
        mp = citemap.build_map(pathlib.Path(tmp), ("*.md",), fams)
        ck("heading_map == citemap definitions",
           sorted((e["id"], e["file"], e["start_line"]) for e in ents),
           sorted((i["id"], d["file"], d["line"])
                  for i in mp["ids"] for d in i["definitions"]))

        # -- CRLF: line numbers agree, and the span keeps its CRLF bytes.
        q = pathlib.Path(tmp) / "crlf.md"
        with open(q, "wb") as fh:
            fh.write(b"# t\r\n\r\n## W9: crlf\r\nbody\r\n\r\n## W10: next\r\n")
        cd, cr, _of = load_doc(q)
        ce = {e["id"]: e for e in heading_map("crlf.md", cd, cr, fams)}
        ck("CRLF heading at line 3", ce["W9"]["start_line"], 3)
        ck("CRLF span keeps CRLF", cd[ce["W9"]["byte_start"]:ce["W9"]["byte_end"]],
           b"## W9: crlf\r\nbody\r\n\r\n")
        ck("CRLF W10 to EOF", cd[ce["W10"]["byte_start"]:ce["W10"]["byte_end"]],
           b"## W10: next\r\n")

        # -- undecodable bytes: scan survives AND `get` returns the ORIGINAL byte, not U+FFFD.
        b = pathlib.Path(tmp) / "bad.md"
        with open(b, "wb") as fh:
            fh.write(b"## D7: bad \xff byte\nbody\n")
        bd, br, _of = load_doc(b)
        be = heading_map("bad.md", bd, br, fams)
        ck("undecodable byte does not abort", [e["id"] for e in be], ["D7"])
        ck("get returns the raw undecodable byte",
           b"\xff" in bd[be[0]["byte_start"]:be[0]["byte_end"]], True)

        # -- end-to-end through main(), including the exit codes callers gate on.
        buf = []

        class _Out(object):
            """Stands in for sys.stdout. `.buffer` is the BYTE sink `get` writes to."""

            def __init__(self):
                import io
                self.buffer = io.BytesIO()

            def write(self, s):
                buf.append(s)

        sink = _Out()
        errbuf = []

        def run(argv):
            """One main() call with BOTH channels captured -- stderr is where the warnings go."""
            import io
            del buf[:]
            del errbuf[:]
            sink.buffer.seek(0)
            sink.buffer.truncate(0)
            real_err, cap = sys.stderr, io.StringIO()
            sys.stderr = cap
            try:
                return main(argv, _out=sink)
            finally:
                sys.stderr = real_err
                errbuf.append(cap.getvalue())

        ck("list exit 0", run(["--root", tmp, "list", "decisions.md"]), EXIT_OK)
        ck("list names both ids", "D1" in "".join(buf) and "D2" in "".join(buf), True)
        ck("list on a missing path exits 3", run(["--root", tmp, "list", "nope.md"]),
           EXIT_UNRESOLVED)
        ck("get of an undefined id exits 3", run(["--root", tmp, "get", "D404"]), EXIT_UNRESOLVED)
        ck("find with no match exits 3", run(["--root", tmp, "find", "zzzznotpresent"]),
           EXIT_UNRESOLVED)
        ck("find with a match exits 0", run(["--root", tmp, "find", "second entry"]), EXIT_OK)
        ck("check exits 3 on dangling", run(["--root", tmp, "check"]), EXIT_UNRESOLVED)
        ck("check names the dangling id D9", "D9" in "".join(buf), True)
        ck("--ignore clears the declared external",
           run(["--root", tmp, "check", "--ignore", "D9"]), EXIT_OK)
        ck("frontier exits 0", run(["--root", tmp, "frontier"]), EXIT_OK)
        ck("frontier emits no id names",
           "D1:" in "".join(buf) or "first entry" in "".join(buf), False)

        # collision + the cold-store rules
        os.mkdir(os.path.join(tmp, "_archive"))
        with open(os.path.join(tmp, "_archive", "old.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("# old\n\n## D1: an archived twin\nCOLDONLYTOKEN\n")
        ck("check exits 5 on dangling+collision", run(["--root", tmp, "check"]), EXIT_BOTH)
        ck("check exits 4 on collision alone",
           run(["--root", tmp, "check", "--ignore", "D9"]), EXIT_AMBIGUOUS)
        ck("get exits 4 when the id collides", run(["--root", tmp, "get", "D1"]), EXIT_AMBIGUOUS)
        ck("get resolves once pinned",
           run(["--root", tmp, "get", "--file", "decisions.md", "D1"]), EXIT_OK)
        # THE round-trip: what `get` emitted is byte-identical to the file's own slice.
        ck("get emits the exact bytes on disk", sink.buffer.getvalue(),
           data[idx["D1"]["byte_start"]:idx["D1"]["byte_end"]])
        ck("get emits no line-number prefix",
           sink.buffer.getvalue().startswith(b"## D1: first entry\n"), True)
        ck("get emits nothing on stdout's text channel", buf, [])
        ck("spec S8's `get <file> <id>` positional form works",
           run(["--root", tmp, "get", "decisions.md", "D2"]), EXIT_OK)
        ck("both get forms agree", sink.buffer.getvalue(),
           data[idx["D2"]["byte_start"]:idx["D2"]["byte_end"]])
        ck("find reaches the cold store", run(["--root", tmp, "find", "COLDONLYTOKEN"]), EXIT_OK)
        ck("find reaches it even when --scope is hot",
           run(["--root", tmp, "--scope", "hot", "find", "COLDONLYTOKEN"]), EXIT_OK)
        ck("find reaches it even when _archive is EXCLUDED",
           run(["--root", tmp, "--exclude", "_archive/*", "find", "COLDONLYTOKEN"]), EXIT_OK)
        ck("frontier REFUSES the cold store",
           run(["--root", tmp, "--include", "_archive/**/*.md", "frontier"]), EXIT_UNRESOLVED)
        ck("frontier refuses cold paths inside a wide --include",
           run(["--root", tmp, "--include", "**/*.md", "frontier"]) == EXIT_OK
           and "refused: 1" in "".join(buf), True)
        ck("frontier's DEFAULT is the artifact's own doc set, not **/*.md",
           run(["--root", tmp, "frontier"]) == EXIT_OK and "refused: 0" in "".join(buf), True)
        # the number axis: nested groups, and a decimal extension kept OFF the integer axis
        ck("_id_number reads D12 as 12", _id_number("D12", fams), "12")
        ck("_id_number reads OQ-V2-14 as 14", _id_number("OQ-V2-14", fams), "14")
        ck("_id_number reads M4.5 as 4.5, not 5", _id_number("M4.5", fams), "4.5")
        ck("_id_number reads R-DOC-1 as 1", _id_number("R-DOC-1", fams), "1")
        ck("get --json is refused, not ignored",
           run(["--root", tmp, "--json", "get", "--file", "decisions.md", "D1"]), EXIT_USAGE)
        ck("--root is mandatory (exit 2)", run(["list", "decisions.md"]), EXIT_USAGE)

        # -- the text channel must survive a corpus character cp1252 cannot encode. Piped stdout
        #    resolves to cp1252 here, and `find` echoes corpus lines verbatim; before main()
        #    reconfigured to utf-8 this exact case raised UnicodeEncodeError out of the gate.
        a = pathlib.Path(tmp) / "arrow.md"
        with open(a, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# a\n\n## D5: unicode → body\nsee → discussion §5.7\n")
        ck("find survives a non-cp1252 corpus char",
           run(["--root", tmp, "find", "discussion"]), EXIT_OK)
        ck("the arrow reaches the report", "→" in "".join(buf), True)
        ck("stdout was reconfigured to utf-8", sys.stdout.encoding.lower().replace("-", ""),
           "utf8")
        os.remove(str(a))

        # ================= THE CRITICAL: an unterminated fence must VOID the gate ============
        # Before the fix this exact fixture printed "1 defined, 0 citations ... PASS (exit 0)".
        # The gate's whole claim is "every citation in scope resolves"; over a file whose fence
        # never closes there are no citations to resolve BECAUSE THE FILE WAS NOT READ.
        of = pathlib.Path(tmp) / "openfence.md"
        with open(of, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# T\n\n## D301: first\nbody\n\n```\nunclosed\n\n"
                     "## D302: second\ncites D350 and D351\n")
        ck("check EXITS 6, not 0, on an unterminated fence",
           run(["--root", tmp, "--include", "openfence.md", "check"]), EXIT_SCAN_INTEGRITY)
        ck("check names the file and the OPENING line", "openfence.md:6" in "".join(buf), True)
        ck("check REFUSES rather than passing", "REFUSED" in "".join(buf), True)
        ck("check never claims PASS on a truncated scan", "PASS:" in "".join(buf), False)
        ck("check warns on stderr too", "SCAN INTEGRITY" in "".join(errbuf), True)
        ck("check --json carries the integrity failure",
           run(["--root", tmp, "--include", "openfence.md", "--json", "check"]),
           EXIT_SCAN_INTEGRITY)
        ck("check --json names the fence",
           json.loads("".join(buf))["unterminated_fences"][0]["line"], 6)
        # the get/list/find half of the SAME root cause: a hidden heading never ends the prior
        # span. The last pass fixed `check` and changed only the STDERR TEXT of these three,
        # which is why the adversary found `get D1` still returning a swallowed span at EXIT 0
        # on five separate fixtures. A caller gates on $?, not on stderr -- and the caller that
        # matters is the migration's round-trip byte-compare. Each verb is asserted on BOTH
        # shapes of the failure: the SWALLOWED SPAN and the VANISHED DEFINITION.
        # -- get, shape 1: the span swallows the entry the fence hid.
        ck("get EXITS 6, not 0, when the span was cut by a truncated scan",
           run(["--root", tmp, "--include", "openfence.md", "get", "D301"]),
           EXIT_SCAN_INTEGRITY)
        ck("the wrong span really does swallow the hidden entry",
           b"## D302" in sink.buffer.getvalue(), True)
        ck("get WARNS that the scan was truncated", "SCAN INTEGRITY" in "".join(errbuf), True)
        ck("...and says the bytes are not the id's bytes",
           "NOT `D301`'s" in "".join(errbuf) or "NOT D301's" in "".join(errbuf), True)
        # -- get, shape 2: the hidden entry reads as absent. 6 outranks 3 -- 3 is a claim about
        #    a corpus that was read, and this corpus was not.
        ck("get EXITS 6, not 3, when the id it cannot find was hidden by a fence",
           run(["--root", tmp, "--include", "openfence.md", "get", "D302"]),
           EXIT_SCAN_INTEGRITY)
        ck("'nowhere' is qualified, not asserted",
           "NOT evidence of absence" in "".join(errbuf), True)
        # -- list, both shapes at once: D301's span runs to EOF and D302 is absent from the map.
        lst = run(["--root", tmp, "list", "openfence.md"]), "".join(buf)
        ck("list EXITS 6, not 0, on a file whose scan is unsound", lst[0], EXIT_SCAN_INTEGRITY)
        ck("list flags the file as unreliable", "UNRELIABLE: open fence at line 6" in lst[1],
           True)
        ck("list says it is REFUSING rather than reporting", "REFUSED" in lst[1], True)
        ck("list, shape 2: the hidden definition is missing from the map it printed",
           "D302" in lst[1], False)
        ck("list --json exits 6 as well",
           run(["--root", tmp, "--include", "openfence.md", "--json", "list",
                "openfence.md"]), EXIT_SCAN_INTEGRITY)
        ck("...and the JSON names the opening line",
           json.loads("".join(buf))["files"][0]["unterminated_fence_line"], 6)
        # -- find, both shapes: a hit inside the swallowed region, and a miss that is not
        #    evidence of absence. `find` reads every line whatever the fence state, so its hits
        #    are real -- but the file is not being scanned the way it renders, and 0 hits there
        #    means nothing. Both exit 6.
        ck("find EXITS 6, not 0, when a searched file's scan is unsound",
           run(["--root", tmp, "--include", "openfence.md", "find", "unclosed"]),
           EXIT_SCAN_INTEGRITY)
        ck("find warns as well", "SCAN INTEGRITY" in "".join(errbuf), True)
        ck("find EXITS 6, not 3, when it finds nothing in an unsound file",
           run(["--root", tmp, "--include", "openfence.md", "find", "zzznotpresentanywhere"]),
           EXIT_SCAN_INTEGRITY)
        ck("find --json exits 6 too",
           run(["--root", tmp, "--include", "openfence.md", "--json", "find", "unclosed"]),
           EXIT_SCAN_INTEGRITY)
        # -- frontier: counts computed off a truncated scan are a lower bound, not an inventory.
        ck("frontier EXITS 6 on an unsound artifact",
           run(["--root", tmp, "--include", "openfence.md", "frontier"]), EXIT_SCAN_INTEGRITY)
        ck("frontier --json exits 6 too",
           run(["--root", tmp, "--include", "openfence.md", "--json", "frontier"]),
           EXIT_SCAN_INTEGRITY)
        # the SAME bytes with the fence closed -- the verdict that was being suppressed
        with open(of, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# T\n\n## D301: first\nbody\n\n```\nclosed\n```\n\n"
                     "## D302: second\ncites D350 and D351\n")
        ck("closing the fence restores a real verdict",
           run(["--root", tmp, "--include", "openfence.md", "check"]), EXIT_UNRESOLVED)
        ck("...and it names the citations the truncated scan hid",
           "D350" in "".join(buf) and "D351" in "".join(buf), True)
        ck("...and the hidden entry is now retrievable",
           run(["--root", tmp, "--include", "openfence.md", "get", "D302"]), EXIT_OK)
        ck("...and D301's span now stops where it should",
           run(["--root", tmp, "--include", "openfence.md", "get", "D301"]) == EXIT_OK
           and b"## D302" not in sink.buffer.getvalue(), True)
        ck("...and list is a report again, not a refusal",
           run(["--root", tmp, "list", "openfence.md"]) == EXIT_OK
           and "D302" in "".join(buf) and "REFUSED" not in "".join(buf), True)
        ck("...and find is a verdict again",
           run(["--root", tmp, "--include", "openfence.md", "find", "zzznotpresentanywhere"]),
           EXIT_UNRESOLVED)
        ck("...and frontier inventories again",
           run(["--root", tmp, "--include", "openfence.md", "frontier"]), EXIT_OK)
        os.remove(str(of))

        # ================= frontier: the artifact guard is anchored at --root ===============
        ck("frontier REFUSES an artifact that IS the cold store",
           run(["--root", tmp, "frontier", "--artifact", "_archive"]), EXIT_USAGE)
        ck("...and says why", "cold storage" in "".join(errbuf), True)
        ck("frontier REFUSES an artifact that escapes --root",
           run(["--root", tmp, "frontier", "--artifact", ".."]), EXIT_USAGE)
        ck("...and says why", "OUTSIDE --root" in "".join(errbuf), True)
        ck("frontier REFUSES a cold artifact reached the long way round",
           run(["--root", tmp, "frontier", "--artifact", "./_archive/."]), EXIT_USAGE)

        # ================= frontier: --scope is HONOURED, not discarded =====================
        hot = run(["--root", tmp, "--scope", "hot", "frontier"]), "".join(buf)
        c4 = run(["--root", tmp, "--scope", "c4", "frontier"]), "".join(buf)
        ck("frontier --scope hot succeeds", hot[0], EXIT_OK)
        ck("frontier --scope c4 refuses the cold files it swept in",
           c4[0] == EXIT_OK and "refused: 1" in c4[1], True)
        ck("frontier --scope actually changes the output", hot[1] == c4[1], False)
        ck("frontier --scope archive resolves to nothing but cold paths",
           run(["--root", tmp, "--scope", "archive", "frontier"]), EXIT_UNRESOLVED)
        ck("frontier default is still the artifact's own doc set",
           run(["--root", tmp, "frontier"]) == EXIT_OK and "refused: 0" in "".join(buf), True)

        # ================= a kept PATH is not a glob PATTERN ================================
        os.mkdir(os.path.join(tmp, "art1"))
        with open(os.path.join(tmp, "art1", "a[1].md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("# A\n\n## D310: bracket file\ncites D311\n")
        with open(os.path.join(tmp, "art1", "b.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("# B\n\n## D312: plain file\n")
        ck("frontier scans a bracketed filename it counted",
           run(["--root", tmp, "frontier", "--artifact", "art1"]) == EXIT_OK
           and "2 defined at 2 sites" in "".join(buf), True)
        ck("...and its citation is counted too", "1 citations" in "".join(buf), True)

        # ================= `--file` pins a LITERAL path, not a glob =========================
        with open(os.path.join(tmp, "a[1].md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# A\n\n## D320: bracket twin\n")
        with open(os.path.join(tmp, "plain.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# P\n\n## D320: plain twin\n")
        ck("a colliding id in a bracketed file is ambiguous",
           run(["--root", tmp, "get", "D320"]), EXIT_AMBIGUOUS)
        ck("the pin `get` printed is a pin `get` HONOURS",
           run(["--root", tmp, "get", "--file", "a[1].md", "D320"]), EXIT_OK)
        ck("...and it returns that file's bytes",
           sink.buffer.getvalue(), b"## D320: bracket twin\n")
        ck("the positional `get <file> <id>` form pins it too",
           run(["--root", tmp, "get", "a[1].md", "D320"]), EXIT_OK)
        os.remove(os.path.join(tmp, "a[1].md"))
        os.remove(os.path.join(tmp, "plain.md"))

        # ================= the D1a signal reaches list and get ==============================
        with open(os.path.join(tmp, "partial.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# P\n\n## D331a: the a-variant\nbody\n")
        pdoc, prows, _of = load_doc(pathlib.Path(tmp) / "partial.md")
        pents = heading_map("partial.md", pdoc, prows, fams)
        ck("## D331a is still recorded as D331 (S8's boundary is ratified)",
           pents[0]["id"], "D331")
        ck("the entry carries the suffix that continued it", pents[0]["suffix"], "a")
        ck("list SAYS the heading reads D331a",
           run(["--root", tmp, "list", "partial.md"]) == EXIT_OK
           and "PARTIAL: heading reads D331a" in "".join(buf), True)
        ck("get warns before handing back a partial-id span",
           run(["--root", tmp, "get", "--file", "partial.md", "D331"]) == EXIT_OK
           and "PARTIAL id" in "".join(errbuf), True)
        ck("check counts it", run(["--root", tmp, "--include", "partial.md", "check"]) == EXIT_OK
           and "PARTIAL-ID HEADINGS (1)" in "".join(buf), True)
        os.remove(os.path.join(tmp, "partial.md"))

        # ================= heading_map's end_line: bisect == the old linear scan ============
        def brute_end_line(rws, byte_end, first_line):
            el = first_line
            for ln, _tx, sb, _eb, _fc in rws:
                if sb < byte_end:
                    el = ln
                else:
                    break
            return el
        ck("bisect end_line agrees with the linear scan it replaced",
           [e["end_line"] for e in ents],
           [brute_end_line(rows, e["byte_end"], e["start_line"]) for e in ents])
        ck("...on a CRLF document too",
           [e["end_line"] for e in heading_map("crlf.md", cd, cr, fams)],
           [brute_end_line(cr, e["byte_end"], e["start_line"])
            for e in heading_map("crlf.md", cd, cr, fams)])

        # ================= an absolute --include is a usage error, not a traceback ==========
        ck("absolute --include exits 2",
           run(["--root", tmp, "--include", os.path.join(tmp, "decisions.md"), "check"]),
           EXIT_USAGE)
        ck("...and says patterns are relative", "RELATIVE to --root" in "".join(errbuf), True)

        # ============ EVERY caller-supplied path is anchored at --root (D53) ================
        # `pathlib` makes `root / <absolute>` == `<absolute>`: the left operand is DISCARDED.
        # `--artifact` was anchored in the previous pass and the same class then survived in
        # `list`, which took the caller's string as its reported `rel` and so printed ANOTHER
        # PROJECT's file under this project's root at exit 0 (VERIFIED 2026-08-02: it returned
        # that peer's ids under `--root <this project>`, and `--json` put the two side by
        # side). One resolver now, called by every verb, and one test per join site.
        outside = pathlib.Path(tmp).parent
        abs_spec = (pathlib.Path(tmp) / "decisions.md").as_posix()
        for flag_argv, label in (
                (["list", abs_spec], "list <absolute>"),
                (["list", "../"], "list ../"),
                (["get", "D1", "--file", abs_spec], "get --file <absolute>"),
                (["get", "D1", "--file", "../x.md"], "get --file ../"),
                (["get", abs_spec, "D1"], "get <absolute> <id>"),
                (["frontier", "--artifact", outside.as_posix()], "frontier --artifact <abs>"),
                (["frontier", "--artifact", ".."], "frontier --artifact ..")):
            ck("%s is refused (exit 2), not resolved" % label,
               run(["--root", tmp] + flag_argv), EXIT_USAGE)
        ck("the refusal names the rule", "D53" in "".join(errbuf), True)
        ck("an absolute path is refused BEFORE the join can discard the root",
           run(["--root", tmp, "list", abs_spec]) == EXIT_USAGE
           and "ABSOLUTE path" in "".join(errbuf), True)
        ck("an escaping path is refused AFTER resolution, and says where it went",
           run(["--root", tmp, "list", "../"]) == EXIT_USAGE
           and "OUTSIDE --root" in "".join(errbuf), True)
        # the resolver itself, at the unit level: rel is RE-DERIVED, never the caller's string
        ck("resolve_under_root re-derives the rel",
           resolve_under_root(pathlib.Path(tmp).resolve(), "./sub/../decisions.md", "x")[0],
           "decisions.md")
        ck("resolve_under_root maps the root itself to '.'",
           resolve_under_root(pathlib.Path(tmp).resolve(), ".", "x")[0], ".")
        for bad in (abs_spec, "..", "../elsewhere.md", "/etc/passwd", "C:/Windows"):
            try:
                resolve_under_root(pathlib.Path(tmp).resolve(), bad, "x")
                ok = False
            except ValueError:
                ok = True
            ck("resolve_under_root refuses %r" % bad, ok, True)
        # ...and the paths that must still work
        for good in ("decisions.md", "./decisions.md", "*.md", "**/*.md", "_archive/old.md"):
            try:
                resolve_under_root(pathlib.Path(tmp).resolve(), good, "x")
                ok = True
            except ValueError:
                ok = False
            ck("resolve_under_root still accepts %r" % good, ok, True)
        ck("a glob still reaches the globber through the resolver",
           run(["--root", tmp, "list", "*.md"]), EXIT_OK)
        ck("a directory still resolves through the resolver",
           run(["--root", tmp, "list", "_archive"]), EXIT_OK)
        ck("a missing path is still 3, not 2",
           run(["--root", tmp, "list", "no/such/file.md"]), EXIT_UNRESOLVED)

        # ================= a leading BOM must not hide a fence from the scanner ============
        # BOM+``` on line 1 defeated `FENCE_RE`'s `^ {0,3}` anchor, so nothing opened and the
        # code block scanned as live definitions and citations instead of code.
        bom = pathlib.Path(tmp) / "bom.md"
        with open(bom, "wb") as fh:
            fh.write(b"\xef\xbb\xbf```\n## D340: hidden by a BOM-prefixed fence\ncites D341\n")
        ck("a BOM'd opening fence really opens", load_doc(bom)[2], 1)
        _bd, _br, _bf = load_doc(bom)
        ck("...so the fenced heading is NOT a definition",
           [e["id"] for e in heading_map("bom.md", _bd, _br, fams)], [])
        ck("...and the file is refused, not silently read",
           run(["--root", tmp, "--include", "bom.md", "check"]), EXIT_SCAN_INTEGRITY)
        # a BOM'd file with NO fence still maps, and the byte offsets still index the raw file
        with open(bom, "wb") as fh:
            fh.write(b"\xef\xbb\xbf## D342: first line is the heading\r\nbody\r\n")
        bmd, bmr, bmf = load_doc(bom)
        bme = heading_map("bom.md", bmd, bmr, fams)
        ck("a BOM'd heading on line 1 is still a definition", [e["id"] for e in bme], ["D342"])
        ck("the span's bytes are the FILE's bytes, BOM included",
           bmd[bme[0]["byte_start"]:bme[0]["byte_end"]],
           b"\xef\xbb\xbf## D342: first line is the heading\r\nbody\r\n")
        ck("citemap agrees the BOM'd heading is a definition on line 1",
           [(d["file"], d["line"]) for i in citemap.build_map(
               pathlib.Path(tmp), ("bom.md",), fams)["ids"] for d in i["definitions"]],
           [("bom.md", 1)])
        os.remove(str(bom))

        # ================= the PARTIAL signal covers every way a heading continues an id ====
        # Connectors can continue an id too; validate their partial-id signal
        # without treating an ordinary numbered heading as a continuation.
        for text, want in (("D350a", "a"), ("D350_x", "_x"), ("D350-CANDIDATE", "-CANDIDATE"),
                           ("D350.5", ".5"), ("D350/D351", "/D351"), ("D350b.5", "b.5"),
                           ("D350", ""), ("D350: a normal title", ""),
                           ("D350. a numbered heading", ""), ("D350 - dashed title", "")):
            ck("suffix of `## %s`" % text,
               citemap.find_definition("## " + text, fams)[3], want)
        sfx = pathlib.Path(tmp) / "suffix.md"
        with open(sfx, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# S\n\n## D351_x: underscore variant\nbody\n")
        ck("list tags the underscore variant PARTIAL",
           run(["--root", tmp, "list", "suffix.md"]) == EXIT_OK
           and "PARTIAL: heading reads D351_x" in "".join(buf), True)
        ck("get warns before handing back its span",
           run(["--root", tmp, "get", "--file", "suffix.md", "D351"]) == EXIT_OK
           and "D351_x" in "".join(errbuf), True)
        ck("check counts it",
           run(["--root", tmp, "--include", "suffix.md", "check"]) == EXIT_OK
           and "PARTIAL-ID HEADINGS (1)" in "".join(buf), True)
        ck("citemap counts it too",
           citemap.build_map(pathlib.Path(tmp), ("suffix.md",), fams)["partial_ids"], ["D351"])
        os.remove(str(sfx))

        # ================= a CLOSED stdout is "discard this", not a crash ==================
        # `>&-` makes CPython set sys.stdout to None. `_PipeSafe.write` caught BrokenPipeError,
        # ValueError and OSError -- and None.write raises AttributeError, which escaped every
        # handler and printed a traceback at exit 1 from all five verbs (VERIFIED 2026-08-02).
        # 1 is the code this file's own exit table promises no verb ever returns.
        dead = _PipeSafe(None)
        ck("a None stdout absorbs a text write", dead.write("x"), 0)
        ck("a None stdout still exposes a byte sink", dead.buffer is not None, True)
        ck("...which absorbs a byte write too", dead.buffer.write(b"x"), 0)
        ck("...and flushing it does not raise", dead.flush(), None)
        _real_err, _cap = sys.stderr, io_mod.StringIO()
        sys.stderr = _cap
        try:
            # `check` is at 5 here (dangling D9 + the D1 archive twin) -- the point is that the
            # VERDICT survives a stdout that cannot be written to at all.
            dead_check = main(["--root", tmp, "check"], _out=dead)
            dead_get = main(["--root", tmp, "get", "--file", "decisions.md", "D1"], _out=dead)
        finally:
            sys.stderr = _real_err
        ck("a verb still returns its VERDICT with stdout gone", dead_check, EXIT_BOTH)
        ck("...including get, whose payload is bytes", dead_get, EXIT_OK)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [c for c in checks if not c[3]]
    for label, got, want, ok in checks:
        if not ok:
            sys.stdout.write("FAIL  %-46s got=%r want=%r\n" % (label, got, want))
    sys.stdout.write("selftest: %d checks, %d failed\n" % (len(checks), len(bad)))
    return 1 if bad else 0


# --------------------------------------------------------------------------- cli

def build_parser():
    ap = argparse.ArgumentParser(
        prog="recall.py",
        description="Deterministic retrieval for save v4. Five verbs, no sixth (C-7).",
        epilog="exit: 0 ok | 1 --selftest failure | 2 usage/IO | 3 unresolved | 4 ambiguous "
               "| 5 both (check only) | 6 scan integrity: an unterminated code fence, so no "
               "answer is possible -- from EVERY verb that reads a file | 7 internal error")
    ap.add_argument("--root", help="project root. REQUIRED -- scope is DECLARED, never cwd (D53).")
    ap.add_argument("--scope", default=None, choices=sorted(citemap.SCOPES),
                    help="named file set (default: %s)" % citemap.DEFAULT_SCOPE)
    ap.add_argument("--include", action="append", default=[], metavar="GLOB",
                    help="glob relative to --root; repeatable; overrides --scope")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="drop matching paths (never applies to find's cold store)")
    ap.add_argument("--families", default=None, metavar="A,B,C", help="restrict id families")
    ap.add_argument("--family", action="append", default=[], metavar="NAME=REGEX",
                    help="add or override one id family; repeatable")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--max", type=int, default=200, metavar="N",
                    help="cap listed matches/sites (0 = all). Truncation is always announced.")
    ap.add_argument("--selftest", action="store_true", help="run the fixture suite and exit")

    sub = ap.add_subparsers(dest="verb")

    # The same flags again, AFTER the verb, because `check --max 2` is how a human types it and
    # argparse otherwise rejects it. default=SUPPRESS is load-bearing: without it the subparser
    # writes its own default over a value the caller passed before the verb, so
    # `--json check` would silently emit the text report.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--max", type=int, default=argparse.SUPPRESS)

    p = sub.add_parser("list", parents=[common],
                       help="heading map for a file or destination")
    p.add_argument("path", help="file, directory or glob, relative to --root")

    p = sub.add_parser("get", parents=[common], help="id -> exact span -> RAW BYTES on stdout")
    p.add_argument("a", metavar="ID | FILE", help="the id, or the file when two args are given")
    p.add_argument("b", nargs="?", metavar="ID", help="the id (spec S8's `get <file> <id>` form)")
    p.add_argument("--file", default=None, metavar="FILE[:LINE]",
                   help="pin the definition site when an id collides")

    p = sub.add_parser("find", parents=[common],
                       help="scoped search; ALWAYS covers _archive/ and checkpoints/")
    p.add_argument("pattern", help="python regex, or a literal with --fixed")
    p.add_argument("--fixed", action="store_true", help="treat the pattern as a literal string")
    p.add_argument("-i", "--ignore-case", action="store_true")

    p = sub.add_parser("check", parents=[common],
                       help="THE GATE: every citation resolves, every id is unique")
    p.add_argument("--ignore", action="append", default=[], metavar="ID",
                   help="id DECLARED external (a peer project's). Repeatable. Never inferred.")
    p.add_argument("--ignore-file", default=None, metavar="PATH",
                   help="file of declared-external ids, one per line, # comments allowed")

    p = sub.add_parser("frontier", parents=[common],
                       help="counts-only artifact inventory (never cold storage)")
    p.add_argument("--artifact", default=None, metavar="SUBDIR",
                   help="artifact root relative to --root (default: --root itself)")
    return ap


def main(argv=None, _out=None):
    # encoding='utf-8', errors='replace' is NOT cosmetic. Piped stdout on this box resolves to
    # cp1252 (VERIFIED: sys.stdout.encoding == 'cp1252'), and `check`/`find` echo corpus lines
    # verbatim -- `docs/plans/spec.md` alone holds 48 U+2192 (RE-DERIVED 2026-08-02; it held 40
    # when this note was first written, which is why undated measured figures do not survive
    # contact with a live corpus), and cp1252 cannot encode any of them, so the gate would die
    # with a UnicodeEncodeError traceback INSTEAD of printing its verdict and the caller would
    # see a crash where it expected an exit code. errors='replace' also mirrors the read side: a
    # corpus byte can be anything and must never take down a scan. `get` is unaffected -- it
    # writes to out.buffer, below the text layer, and stays byte-exact.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except (AttributeError, ValueError):
        pass
    out = _out if _out is not None else _PipeSafe(sys.stdout)

    ap = build_parser()
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.verb:
        ap.print_help(sys.stderr)
        return EXIT_USAGE
    if not args.root:
        sys.stderr.write("error: --root is required: the scope is DECLARED, never inferred from "
                         "cwd (D53). 512 distinct id strings exist across the fleet and "
                         "D17/C-6/F1 recur in dozens of unrelated projects.\n")
        return EXIT_USAGE
    root = pathlib.Path(args.root)
    if not root.is_dir():
        sys.stderr.write("error: --root is not a directory: %s\n" % args.root)
        return EXIT_USAGE
    root = root.resolve()

    # spec S8's `get <file> <id>` and the shorter `get <id>` are both accepted.
    if args.verb == "get":
        if args.json:
            # Refused rather than ignored. `get` emits the span's bytes and nothing else; a
            # caller that passed --json expects a parseable envelope and would instead receive
            # raw markdown on a channel it is about to hand to a JSON parser.
            sys.stderr.write("error: `get` returns RAW BYTES, never JSON. Use `list --json` for "
                             "an id's span metadata, then `get` for its bytes.\n")
            return EXIT_USAGE
        if args.b is not None:
            if args.file:
                sys.stderr.write("error: pass `get <file> <id>` or `get <id> --file <file>`, "
                                 "not both\n")
                return EXIT_USAGE
            args.file, args.ident = args.a, args.b
        else:
            args.ident = args.a

    try:
        families = families_from(args, root)
        if args.verb == "list":
            return verb_list(args, root, families, out)
        if args.verb == "get":
            return verb_get(args, root, families, out)
        if args.verb == "find":
            return verb_find(args, root, families, out)
        if args.verb == "check":
            return verb_check(args, root, families, out)
        if args.verb == "frontier":
            return verb_frontier(args, root, families, out)
    except (ValueError, IOError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return EXIT_USAGE
    except OSError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return EXIT_USAGE
    sys.stderr.write("error: unknown verb %r\n" % args.verb)
    return EXIT_USAGE


def cli(argv=None):
    """main() plus the two things that must not turn a verdict into a different number.

    A downstream consumer closing stdout early -- `check | head`, which is how an operator reads
    a long report -- makes CPython fail its own shutdown flush and exit 120 (VERIFIED
    2026-08-02, 3/3 on `projects/dev/broker`: `check --max 0 | head -1` returned 120 instead of
    the corpus verdict 5). 120 is in no exit table here and reads to a caller as an unknown
    failure. The docstring promises stable exit codes across all five verbs, so the promise is
    kept rather than withdrawn: the dropped write is not an error, and the verdict survives.

    And an uncaught exception exited 1 -- a code the exit table reserves for `--selftest` and
    explicitly promises no verb ever returns, so an internal fault was indistinguishable from a
    clean selftest failure. `citemap.py` got this wrapper in the previous pass and this file did
    not, which is how `>&-` reached a caller as a traceback at exit 1 here and as one line at
    exit 7 there (VERIFIED 2026-08-02, all five verbs). Same asymmetry, same fix: 7 is INTERNAL
    ERROR, it means the tool broke rather than the corpus, and it is not 1.
    """
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
        # Redirect the fd so the interpreter's own shutdown flush cannot raise and re-exit 120.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
    return code


if __name__ == "__main__":
    sys.exit(cli())
