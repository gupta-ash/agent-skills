#!/usr/bin/env python
"""citemap.py -- project-scoped citation map: id -> defining file:line + inbound citation count.

Work item W1. The first executable piece of save v4, and the thing that makes C-4
("resume without loss of fidelity: eviction degrades to a resolvable stub, never a deletion")
CHECKABLE rather than merely asserted. v4 moves history out of the loaded set into `_archive/`;
this map is how anyone proves the citations into it still resolve. W2 (`recall check`)
consumes it; W17 (the C-4 fixer) consumes its `collisions` and `dangling` lists.

CONTRACT: build_map(root, patterns, families) returns, for every id occurring anywhere in the
    declared scope: its definition sites (file, 1-based line, title), its inbound citation count
    (every occurrence that is NOT one of its own definition sites), and the per-file breakdown of
    those citations. Derived from that: `collisions` (defined more than once) and `dangling`
    (cited, never defined). Emits a human table by default and JSON on `--json`.
CONTRACT: project_families(root, names, extra) -> (families, problems). The families are
    `FAMILY_PATTERNS` widened by the project's OWN declaration file, `.ash-id-families` at the
    project root -- one `NAME=REGEX` per line. `problems` is a list of human-readable strings the
    CALLER prints through its own warning channel; this module owns no stderr policy, because its
    six callers have six different ones (a `warn` closure, a receipt list, a `problems` list that
    aborts, bare stderr). A MALFORMED LINE IS SKIPPED AND WARNED, NEVER RAISED: the save path
    calls this, and no config condition may join step 7d as a second way to abort a save.
CONTRACT: EXIT CODES -- every reachable code, none omitted.
        0  a COMPLETE scan. A non-empty `collisions` or `dangling` list is DATA, not failure --
           this verb reports, W2's `check` is the gate.
        1  `--strict` verdict only: collisions or dangling present. NEVER a crash -- see 7.
        1  ALSO `--selftest` with a failing check. The two never co-occur: `--selftest` returns
           before any scan and ignores every other flag.
        2  usage or I/O error, including a bad or non-relative `--include` pattern.
        6  SCAN INTEGRITY FAILURE: a fenced code block was still open at EOF in at least one
           file, so the scan of that file is UNRELIABLE and no verdict may be built on it.
           Reported ALWAYS, on stderr, whether or not `--strict` was passed. See the invariant.
        7  internal error: an uncaught exception. Distinct from 1 ON PURPOSE -- a crash and a
           `--strict` verdict were the same code, so a caller gating on 1 could not tell a
           corpus verdict from a UnicodeEncodeError.
INVARIANT: an UNBALANCED FENCE IS NOT DATA, IT IS AN UNRELIABLE SCAN. The fence state machine
    has no way to know whether the text after an unclosed ``` is corpus or specimen, so every
    later heading and every later citation in that file is unclassifiable. Silently finishing
    the scan renders the file as empty -- 0 citations, 0 definitions -- and empty passes every
    gate built on it, which is this project's own F65 ("every generated view fails toward
    nothing here") landing on the one check that exists to catch it. So: the opening line is
    recorded, the file is named, and the exit code says integrity, not verdict.
INVARIANT: every family regex carries a mandatory RIGHT BOUNDARY `(?![0-9])`, the `([^0-9]|$)`
    of spec S8. Without it `^## D1` also matches `D10` and `D100`: a lookup returns the wrong
    entry while appearing to succeed, which is the whole reason D44 exists. `--selftest` asserts
    D1 does not match D10, D100 or D-PLAN-1.
INVARIANT: a DEFINITION is a column-0 ATX heading whose heading text STARTS with the id, and which
    is not inside a fenced code block. The same id in a table cell, a bullet, an indented block or
    a fence is a CITATION. Inverting this inverts the map.
INVARIANT: scope is DECLARED, never defaulted to cwd (D53). 512 distinct id strings exist across
    the fleet and `D17`, `C-6`, `F1` recur in dozens of unrelated projects, so a global or
    cwd-inferred map resolves confidently and wrongly. `--root` is required; there is no fallback.
CONTRACT: `defined_at` is the FIRST definition site in SCAN order, and scan order is the caller's
    declared PATTERN order (lexical within one pattern). That makes precedence declarable rather
    than guessed: `--include decisions.md --include '*.md'` pins canonical `decisions.md` ahead of
    the generated `decisions-digest.md`, which is lexically first and would otherwise be presented
    as D1's home. When len(definitions) > 1 the id is ALSO in `collisions` and the table prints
    `[+n COLLIDING]` -- `defined_at` alone must never be read as "the" definition.
INVARIANT: files are read as BYTES and decoded with errors='replace'. A corpus file may hold any
    byte sequence and a UnicodeDecodeError must never be able to take down a scan.
INVARIANT: all output is LF, and BOTH text channels are reconfigured to utf-8 with
    errors='replace'. newline='\\n' alone is not enough: piped stdout on this box resolves to
    cp1252 (VERIFIED 2026-08-02: `sys.stdout.encoding == 'cp1252'` under a redirect) and the
    renderer echoes heading titles VERBATIM, so one U+2192 in a title killed the whole run with
    a UnicodeEncodeError -- at exit 1, which was also the `--strict` verdict code, so a crash
    and a verdict were indistinguishable to a caller. VERIFIED 2026-08-02 on
    `projects/dev/broker`: `citemap.py --root <broker> --strict > file` exited 1 on
    '\\u2192' in position 7024. Fixed on both halves: encoding here, exit 7 for crashes below.
EXTERNAL: spec S8 (retrieval verbs, the `^## D1([^0-9]|$)` boundary form), S12.4 (id uniqueness);
    D44 (boundary), D53 (project scope), D93 (the PHASE/FEAT/TASK work-breakdown standard, whose
    parent FIELDS are ordinary citations and so are only checkable once those three families are
    registered here); implementation-plan rows W1, W2, W17.
EXTERNAL: `python -S -I` is the run mode -- no site-packages, isolated. stdlib only, no exceptions.
    On this box the interpreter is `python`; `python3` is a Store stub and does not run.
EXTERNAL: LEFT boundary excludes `:` so W17's prefix-qualified `archive:D42` reads as a DISTINCT
    id and stops resolving as `D42` -- which is the property W17's collision fix depends on. It
    does NOT exclude `-`, so a range written `D1-D71` counts both endpoints, and the SUFFIX form
    `D42-archive` still resolves as `D42` exactly as W17 states it does.
REJECTED: stripping inline code spans before counting citations (the check_refs.py prototype does
    this). In THIS corpus the normal citation form IS inline code -- `D44`, `` `C-4` `` -- so
    stripping them discards most of the signal. The prototype's case was the opposite: there a
    backticked ref was a specimen being quoted.
REJECTED: counting ids inside fenced code blocks as citations. Fences in this corpus hold regex and
    CLI specimens -- `^## D1([^0-9]|$)` is itself one -- so a fence occurrence is a MENTION of the
    grammar, not a reference to an entry. Fences are skipped for definitions AND citations.
REJECTED: suppressing "grammar specimen" citations that appear in PROSE rather than in a fence.
    "`^## D1` also matches D10/D100" is a real sentence in this corpus, so D10 and D100 are counted
    as citations of ids nothing defines (VERIFIED: research.md, decisions.md, architecture.md).
    Suppressing them would mean guessing intent from prose; the fence and heading rules are
    structural and that is not. Consequence for W2: `dangling` is a list to READ, not a count to
    assert on blindly.
REJECTED: a right boundary of `(?![0-9A-Za-z])`. It would additionally stop `F1` matching inside a
    hypothetical `F1SCORE`, but spec S8 ratifies `([^0-9]|$)` literally and W17's rename argument
    is measured against that exact form. Tightening it here would make the two disagree.
    THE COST IS REAL AND IS NOW SIGNALLED, NOT SILENCED. Under the ratified form `## D1a` is a
    definition of `D1`, so `get D1` can otherwise return D1a's bytes. The fixed
    boundary remains unchanged; find_definition records the continuation and
    list/get/check expose it as a partial id. Continuations include connectors
    (`_ . / -`) only when followed by an alphanumeric character.
INVARIANT: a LEADING BOM is stripped before scanning. `FENCE_RE` and `HEADING_RE` anchor at `^`
    with at most three spaces of indent and U+FEFF is neither, so a file beginning BOM+``` opened
    no fence here while every renderer showed the rest of it as code -- scanner and renderer
    disagreeing about which lines are live. This is a scanner correctness issue,
    not permission to change fence boundaries elsewhere. It is fixed
    because the scanner is the one that has to be right. Only the first character goes: U+FEFF
    anywhere else is ZWNBSP and is content. `recall`'s byte offsets are deliberately NOT
    shifted -- they index the file as it sits on disk, and an entry beginning at byte 0 of a
    BOM'd file owns that BOM because the file does.
REJECTED: enabling bare T/A/S/B/G/L/P/R/C numeric labels as default families.
    Such labels can be section numbers, workflow steps or quoted foreign ids.
    Enabling them implicitly adds dangling-reference noise rather than proving
    those labels belong to this project's entry scheme. Projects that define
    these families can select them explicitly, for example --family 'R=R(\\d+)'.

Usage:
    python -S -I scripts/citemap.py --root <project> [--scope c4|hot|archive|checkpoints|all]
    python -S -I scripts/citemap.py --root <project> --json
    python -S -I scripts/citemap.py --root <project> --include '*.md' --include 'docs/plans/*.md'
    python -S -I scripts/citemap.py --selftest
"""
import argparse
import fnmatch
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict

# --------------------------------------------------------------------------- families
#
# name -> core pattern. Derived by scanning the corpus, not assumed; the plan names
# D, F, OQ, C-, A-, T, R- and the scan additionally found D-PLAN-N, OQ-V2-N, R-<WORD>-N,
# W-n, M-n and CR-n. `D` and `D-PLAN` share a prefix and are DIFFERENT families: `D\d+`
# cannot match `D-PLAN-1` because a hyphen, not a digit, follows the `D`.
#
# `M` carries the only decimal extension: the copilot pilot corpus defines `## M4.5` and
# `## M5.5` alongside `## M5` (VERIFIED by heading scan), so `M\d+` alone would record a
# definition of `M4` at a heading that defines `M4.5`. The `(?:\.\d+)*` group is greedy but
# requires a digit after the dot, so a sentence-final "M5." still reads as `M5`.
#
# PHASE / FEAT / TASK are D93's three-level work-breakdown standard -- PHASE (a body of work
# that can be parked as a whole), FEATURE (the unit the definition of done applies to, the
# smallest thing that can be DONE), TASK (a step toward a feature). They are registered here
# and not left to `--family` because the flag reaches 2 of 7 tools and not the save path.
# The hierarchy is a FIELD on the child (`phase: PHASE3`, `feature: FEAT7`) and BOTH fields are
# ordinary citations, which is the entire mechanism by which a mis-typed parent becomes a
# dangling reference the gate already runs can see. That makes a missed family here a SILENT
# WRONG ANSWER at exit 0, not a cosmetic gap -- this is a resolver, not a lister.
#
# Three constraints travel with them, and each one is load-bearing:
#
# CASE-SENSITIVE. `re` is case-sensitive here and no family compiles with IGNORECASE, so this
#   costs nothing to hold -- but it is what keeps the namespace at one. D93 measured `phase<n>`
#   lowercase at 13,978 occurrences fleet-wide against ONE `PHASE<n>`; a case-insensitive read
#   takes the family from 1 id to ~14,000, every one of them ordinary English prose.
# BARE FORM ONLY. `FEAT` is free across ~30,000 fleet markdown files in both cases and both
#   forms; `PHASE` and `TASK` are free ONLY bare. The prefixed forms are already taken --
#   `TASK-` collides with the `R-` family's own `R-TASK-1`. Requiring a digit immediately after
#   the letters is what makes the bare form safe: `PHASE 3`, `phase`, `TASK-` and a sentence
#   containing the word "feature" all fail to match for want of that digit.
# AND THE LETTERS MAY NOT MATCH INSIDE A COMPOUND LABEL, which is the one constraint the shared
#   LEFT_BOUNDARY does not already give. It excludes `[0-9A-Za-z_:]`, and `-` is deliberately
#   NOT in that set (a range written `D1-D71` must count both endpoints, and W17's rename form
#   `D42-archive` must still resolve as `D42`). So `OQ-PHASE2-1` -- a heading that exists in a
#   real project today -- would split into a spurious `PHASE2`: an id CITED and never defined,
#   landing in the one list the field-citation design above depends on being readable. The
#   per-family guard `(?<![-./])` closes it. Those three characters are not chosen freely: they
#   are exactly SUFFIX_RE's connector set minus `_`, which LEFT_BOUNDARY already covers -- the
#   characters this file has ALREADY measured as the ones that join an id to a longer label.
#   Preceded by one of them, the letters are part of a longer label and not a bare id, which is
#   the same sentence as "bare form only" read from the left. The known cost is a range written
#   `PHASE1-PHASE3`, which counts only `PHASE1`; that trade is deliberate, because an id this
#   map resolves WRONGLY is worse than one it resolves not at all, and D93 names the compound
#   split explicitly while the range form is hypothetical.
FAMILY_PATTERNS = (
    ("D",       r"D(\d+)"),
    ("D-PLAN",  r"D-PLAN-(\d+)"),
    ("F",       r"F(\d+)"),
    ("OQ",      r"OQ(\d+)"),
    ("OQ-V2",   r"OQ-V2-(\d+)"),
    ("C-",      r"C-(\d+)"),
    ("A-",      r"A-(\d+)"),
    ("T-",      r"T-(\d+)"),
    ("R-",      r"R-[A-Z]{2,6}-(\d+)"),
    ("W",       r"W(\d+)"),
    ("M",       r"M(\d+(?:\.\d+)*)"),
    ("CR-",     r"CR-(\d+)"),
    ("PHASE",   r"(?<![-./])PHASE(\d+)"),
    ("FEAT",    r"(?<![-./])FEAT(\d+)"),
    ("TASK",    r"(?<![-./])TASK(\d+)"),
)

# The two boundaries. RIGHT is mandatory and is spec S8's `([^0-9]|$)` in lookahead form.
LEFT_BOUNDARY = r"(?<![0-9A-Za-z_:])"
RIGHT_BOUNDARY = r"(?![0-9])"

# --------------------------------------------------------------------------- scopes
#
# `c4` is W17's declared scope, quoted: "the hot set + `_archive/**` + `checkpoints/**` --
# nothing else; an undeclared scope reports noise and the operator learns to ignore it."
SCOPES = {
    "hot":         ("*.md",),
    "archive":     ("_archive/**/*.md",),
    "checkpoints": ("checkpoints/**/*.md",),
    "c4":          ("*.md", "_archive/**/*.md", "checkpoints/**/*.md"),
    "all":         ("**/*.md",),
}
DEFAULT_SCOPE = "c4"

# ATX heading at column 0. `deco` absorbs a bold/italic/code wrapper so `## **D42** title`
# is still a definition of D42; no heading in the pilot corpus uses one, but nothing forbids it.
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+([*_`]{0,3})(.*)$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# U+FEFF as an escape, never as a literal: a literal BOM in this source is invisible in
# every editor and survives exactly one careless re-save.
BOM = "\ufeff"
# A drive-qualified path (`Z:/x`) is absolute on Windows and is not caught by
# PurePosixPath.is_absolute, which only knows a leading '/'. The volume letter is an
# obviously-fictional one on purpose -- the fleet portability check flags a BUILD-HOST
# drive letter even inside a comment, and two benign warnings are what hide the third.
# Naming that letter here would trip the same check, so it is not spelled out. Do not
# "correct" it to a drive that exists on the machine you are reading this on (same
# reason as the `Z:/elsewhere` fixture below).
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
TITLE_LEAD_RE = re.compile(r"^[\s:\-\u2013\u2014\u00b7|.,]+")
TITLE_TRAIL_RE = re.compile(r"[\s#*_`]+$")
# The run that CONTINUES a matched id in a heading -- `## D1a` -> "a", `## D1_x` -> "_x",
# `## D38-CANDIDATE` -> "-CANDIDATE", `## M7b.5` -> "b.5", `## D2/D3` -> "/D3". Alphanumerics,
# plus a connector (`_ . / -`) ONLY when another alphanumeric follows it.
#
# A suffix can continue with an underscore, dot, slash or hyphen, not only a
# letter. Require an alphanumeric after a connector so an ordinary heading's
# trailing full stop is not misreported as part of its identifier.
SUFFIX_RE = re.compile(r"(?:[0-9A-Za-z]|[_./-](?=[0-9A-Za-z]))+")


class Family(object):
    """One id family: the scanning regex and the anchored definition regex."""

    __slots__ = ("name", "core", "scan", "anchored")

    def __init__(self, name, core):
        self.name = name
        self.core = core
        self.scan = re.compile(LEFT_BOUNDARY + "(" + core + ")" + RIGHT_BOUNDARY)
        self.anchored = re.compile("(" + core + ")" + RIGHT_BOUNDARY)

    def boundary_form(self):
        return LEFT_BOUNDARY + "(" + self.core + ")" + RIGHT_BOUNDARY


def build_families(names=None, extra=None):
    """Return the ordered family list. `extra` is a list of 'NAME=REGEX' strings."""
    table = [Family(n, c) for n, c in FAMILY_PATTERNS]
    if names:
        wanted = set(names)
        unknown = wanted - {n for n, _ in FAMILY_PATTERNS}
        if unknown:
            raise ValueError("unknown family/families: %s (known: %s)"
                             % (", ".join(sorted(unknown)),
                                ", ".join(n for n, _ in FAMILY_PATTERNS)))
        table = [f for f in table if f.name in wanted]
    for spec in (extra or ()):
        if "=" not in spec:
            raise ValueError("--family expects NAME=REGEX, got %r" % spec)
        name, core = spec.split("=", 1)
        name, core = name.strip(), core.strip()
        if not name or not core:
            raise ValueError("--family expects NAME=REGEX, got %r" % spec)
        try:
            re.compile(core)
        except re.error as exc:
            raise ValueError("--family %s: bad regex: %s" % (name, exc))
        table = [f for f in table if f.name != name] + [Family(name, core)]
    if not table:
        raise ValueError("no families selected")
    return table


# --------------------------------------------------------------- the per-project declaration
#
# WHY A FILE AND NOT A FLAG. `--family NAME=REGEX` has existed since W1 and reaches 2 of the 7
# tools; NEITHER of those two is on the save path. So a project whose headings use an id from an
# unregistered family counts ZERO resolvable ids, forever, at exit 0 -- state.md's counts are
# wrong, demote row 2b's baseline is wrong, and `recall get` cannot fetch the entry once it
# goes cold. Three MEASURED instances, all real folders:
#   peer `example-project`  `## OQ-MM-5:` / `## OQ-MM-9:`  -- `OQ` is `OQ(\d+)`, digits must follow
#                                                           immediately, so `OQ-MM-` matches nothing
#   this project          `R-UNBLOCK-1`                  -- `R-` is `R-[A-Z]{2,6}-(\d+)` and
#                                                           UNBLOCK is SEVEN letters
#   peer `sysprompt`      `Q13`                          -- no `Q` family exists at all
# A flag cannot fix that class: the operator would have to remember to pass it on every invocation
# of every tool, and the save path takes no such flag from anyone. The declaration has to live
# with the project, be read without being asked for, and cost nothing when absent.
#
# WHY DOT-PREFIXED. Both scanners already drop it for free: `collect_files` refuses any path with
# a dot-prefixed component (see the rule below), and `regen_manifest`'s router excludes
# dot-prefixed names. So the file is not corpus, is not routed, is not in the hot set, and adds
# zero tokens to a loaded project. No routing change is needed, which is the point -- a config
# file that needed its own row in the manifest would be a second thing to keep in sync.
DECLARED_FAMILIES_FILE = ".ash-id-families"


def load_declared_families(root):
    """`.ash-id-families` at `root` -> (specs, problems). NEVER raises.

    `specs` are 'NAME=REGEX' strings in FILE ORDER, ready to hand to `build_families(extra=...)`.
    `problems` are human-readable strings for the caller to print; each names the path, the
    1-based line number and the reason, because "bad declaration file" over a 20-line file sends
    the operator to read all twenty.

    ABSENT IS THE NORMAL CASE AND IS SILENT -- ([], []). Every project that has never needed a
    family of its own must behave exactly as it did before this existed, with no new line on
    stderr; a warning every project sees is a warning every operator learns to skip.

    ONE BAD LINE COSTS ONE LINE. It is recorded and skipped and the rest of the file still loads,
    for the same reason the save path may not abort on it: a typo in an optional config is not a
    reason to lose a save. Reading as BYTES with errors="replace" is this toolchain's convention
    throughout (see `read_lines`) -- a mojibake NAME then fails the regex/`=` checks and is
    reported as a bad line, rather than killing the run with a UnicodeDecodeError.
    """
    path = pathlib.Path(root) / DECLARED_FAMILIES_FILE
    specs, problems = [], []
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return specs, problems
    except OSError as exc:
        # Present but unreadable is NOT the absent case and may not be silent: the project
        # declared families and is getting none of them.
        problems.append("%s: cannot be read (%s); NO declared id families are in effect for this "
                        "run" % (path, exc))
        return specs, problems

    text = raw.decode("utf-8", errors="replace")
    if text.startswith(BOM):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for lineno, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            problems.append("%s:%d: a declaration is NAME=REGEX and this line has no `=`: %r. "
                            "LINE SKIPPED; the rest of the file still loads."
                            % (path, lineno, stripped[:80]))
            continue
        name, core = stripped.split("=", 1)
        name, core = name.strip(), core.strip()
        if not name or not core:
            problems.append("%s:%d: a declaration is NAME=REGEX with BOTH halves non-empty, got "
                            "name=%r regex=%r. LINE SKIPPED; the rest of the file still loads."
                            % (path, lineno, name, core))
            continue
        try:
            re.compile(core)
        except re.error as exc:
            problems.append("%s:%d: family %s has a regex that does not compile: %s. LINE "
                            "SKIPPED; the rest of the file still loads." % (path, lineno, name, exc))
            continue
        specs.append("%s=%s" % (name, core))
    return specs, problems


def project_families(root, names=None, extra=None):
    """`build_families` widened by the project's declaration -> (families, problems).

    This is what every caller holding a project root should use. `build_families` stays PURE --
    no filesystem, no I/O -- so the fixture suites that pin the boundary grammar keep testing the
    grammar and nothing else.
    """
    specs, problems = load_declared_families(root)

    # A declared NAME that equals a built-in is LEGAL and is the intended way to widen one family
    # for one project (`R-=R-[A-Z]{2,8}-(\d+)` is what makes this project's own `R-UNBLOCK-1`
    # resolve). It may not be SILENT: from the outside, a family behaving differently here than in
    # every other project is indistinguishable from a bug in this file, and the operator has no
    # way to discover the cause except by reading a dotfile they may not know exists.
    builtin = {n for n, _ in FAMILY_PATTERNS}
    for spec in specs:
        nm = spec.split("=", 1)[0]
        if nm in builtin:
            problems.append("NOTE: %s declares family %s, which is a BUILT-IN. The project's "
                            "pattern REPLACES the built-in one for this run: %s"
                            % (DECLARED_FAMILIES_FILE, nm, spec))

    # THE ORDERING IS THE WHOLE PRECEDENCE RULE. `build_families` replaces by name in loop order
    # and LAST WINS, so the declared specs go FIRST and an explicit `--family` on the command line
    # still overrides the file. Reverse these two lists and the file silently beats the flag --
    # which would leave an operator no way to override a declaration except by editing it.
    return build_families(names, list(specs) + list(extra or ())), problems


# --------------------------------------------------------------------------- file layer

def read_lines(path):
    """Bytes -> replace-decoded text -> lines, with CRLF and lone CR normalised away.

    Splitting on '\\n' after that normalisation makes our line numbers agree with `grep -n`
    on both LF and CRLF files. str.splitlines() would additionally break on \\x0b, \\x0c,
    \\x1c-\\x1e and U+2028/U+2029, which no editor and no grep counts as a line break --
    one form feed in a corpus file and every line number below it would be wrong.

    A LEADING BOM is dropped. `FENCE_RE` and `HEADING_RE` are both anchored at `^` with at most
    three spaces of indent, and U+FEFF is neither -- so a file whose first line is a BOM
    followed immediately by ``` opened no fence here while every renderer showed the rest of
    the file as code. Strip the first BOM so scanner and renderer agree about
    which lines are live. Only the FIRST
    character is stripped: U+FEFF anywhere else is ZWNBSP, ordinary content, and not ours to
    remove. Line numbering is untouched -- the BOM is not a line.
    """
    text = path.read_bytes().decode("utf-8", errors="replace")
    if text.startswith(BOM):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.split("\n")


def collect_files(root, patterns, excludes=()):
    """Resolve declared glob patterns under root. Deterministic order, no duplicates.

    Any path with a dot-prefixed component is dropped: `.git` and friends are not corpus.

    Order is PATTERN order, then lexical within a pattern -- not global lexical. The `c4` scope
    declares the hot set first, so a colliding id's PRIMARY `defined_at` is its hot definition and
    the archive twin is the `(+n)`. W17 renames the archive heading and never the surviving hot
    one, so the map must not present them in an order that inverts which is which.

    A pattern that is ABSOLUTE or contains a `..` component is REFUSED here, up front. This is
    the glob half of `recall.resolve_under_root`'s job and it is stated rather than relied
    on: absolutes reached `Path.glob` as a NotImplementedError, and `..` patterns globbed fine
    and were then discarded one path at a time by the dot-prefix rule below -- a SILENT DROP,
    which surfaces to the caller as "no match" (exit 3), a claim about the corpus rather than
    about the flag. `find` made that concrete: it appends the cold store to whatever scope was
    declared, so `--include '../../dev/broker/*.md'` dropped the foreign pattern, matched in
    `checkpoints/`, and returned 0 with a scope the caller declared and never got (VERIFIED
    2026-08-02). Declared scope is D53; a pattern that cannot be honoured is an error, not a
    quiet nothing.
    """
    for pat in patterns:
        norm = str(pat).replace("\\", "/")
        if norm.startswith("/") or _DRIVE_RE.match(norm) or \
                ".." in pathlib.PurePosixPath(norm).parts:
            raise ValueError("bad scope pattern %r (patterns are RELATIVE to --root and may "
                             "not contain '..': the scope is DECLARED, D53)" % (pat,))
    seen = {}
    order = []
    for pat in patterns:
        batch = []
        try:
            matched = list(root.glob(pat))
        except (NotImplementedError, ValueError, OSError) as exc:
            # pathlib raises NotImplementedError("Non-relative patterns are unsupported") for an
            # ABSOLUTE --include, which escaped as a traceback at exit 1 -- outside the contracted
            # exit set for both this script and recall.py. A bad pattern is a usage error.
            raise ValueError("bad scope pattern %r (patterns are RELATIVE to --root): %s"
                             % (pat, exc))
        for p in matched:
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if any(part.startswith(".") for part in rel.parts):
                continue
            key = rel.as_posix()
            if any(fnmatch.fnmatch(key, ex) for ex in excludes):
                continue
            if key in seen:
                continue
            seen[key] = p
            batch.append(key)
        order.extend(sorted(batch))
    return [(k, seen[k]) for k in order]


# --------------------------------------------------------------------------- scanning

def _clean_title(s):
    s = TITLE_LEAD_RE.sub("", s)
    s = TITLE_TRAIL_RE.sub("", s)
    return " ".join(s.split())


def find_definition(line, families):
    """If `line` is a column-0 heading whose text starts with an id, return
    (id, offset, title, suffix).

    Longest match wins when two families could both anchor, so a family that is a prefix of
    another can never steal the shorter reading.

    `suffix` is the alphanumeric run that CONTINUED the id in the heading text: `## D1a` matches
    `D1` under spec S8's ratified `([^0-9]|$)` boundary and yields suffix "a". It is a SIGNAL,
    never a behaviour change -- the id recorded is still `D1`, exactly as S8 says. Callers that
    render a definition site MUST surface it, because the heading a human reads (`D1a`) and the
    id the map resolves (`D1`) are then different strings.
    """
    m = HEADING_RE.match(line)
    if not m:
        return None
    offset = m.start(3)                   # where the heading TEXT starts in the raw line
    rest = m.group(3)
    best = None
    for fam in families:
        am = fam.anchored.match(rest)
        if am and (best is None or len(am.group(1)) > len(best[0])):
            best = (am.group(1), am.end(1))
    if best is None:
        return None
    ident, end = best
    tail = rest[end:]
    sm = SUFFIX_RE.match(tail)
    return ident, offset, _clean_title(tail), (sm.group(0) if sm else "")


def scan_occurrences(line, families):
    """All family matches on a line as (id, start, end), with contained matches dropped."""
    hits = []
    for fam in families:
        for m in fam.scan.finditer(line):
            hits.append((m.group(1), m.start(1), m.end(1)))
    if len(hits) < 2:
        return hits
    hits.sort(key=lambda h: (h[1], -(h[2] - h[1])))
    kept = []
    for ident, s, e in hits:
        if any(s >= ks and e <= ke and (s, e) != (ks, ke) for _, ks, ke in kept):
            continue
        kept.append((ident, s, e))
    return kept


def build_map(root, patterns, families, excludes=(), files=None):
    """Scan the declared file set and return the citation map.

    `files` overrides globbing with an EXPLICIT [(rel, path)] list. Callers that already hold a
    file set must use it rather than feeding the kept relative PATHS back in as glob PATTERNS:
    a path is not a pattern, and a filename holding `[` or `]` re-globs to something else or to
    nothing at all. Measured 2026-08-02: `frontier` on a directory containing `a[1].md` reported
    2 files / 48 bytes from its own kept list while scanning only `b.md` for ids -- one table,
    two file sets, and nothing in the output said so.
    """
    if files is None:
        files = collect_files(root, patterns, excludes)
    definitions = defaultdict(list)      # id -> [ {file, line, title, suffix} ]
    citations = defaultdict(Counter)     # id -> Counter(file -> n)
    fenced_hits = Counter()              # id -> n  (diagnostic only, not counted)
    unterminated = []                    # [{file, line}] -- an OPEN fence at EOF: scan unreliable
    lines_scanned = 0

    for rel, path in files:
        try:
            lines = read_lines(path)
        except OSError as exc:
            raise IOError("cannot read %s: %s" % (rel, exc))
        fence = None                      # the opening fence marker, or None
        fence_line = None                 # the line the OPEN fence started on
        for lineno, raw in enumerate(lines, 1):
            lines_scanned += 1
            fm = FENCE_RE.match(raw)
            if fm:
                marker = fm.group(1)
                if fence is None:
                    fence = marker[0] * len(marker)
                    fence_line = lineno
                    continue
                if marker[0] == fence[0] and len(marker) >= len(fence) \
                        and not fm.group(2).strip():
                    fence = None
                    fence_line = None
                    continue
            if fence is not None:
                for ident, _s, _e in scan_occurrences(raw, families):
                    fenced_hits[ident] += 1
                continue

            defn = find_definition(raw, families)
            def_span = None
            if defn is not None:
                ident, offset, title, suffix = defn
                definitions[ident].append({"file": rel, "line": lineno, "title": title,
                                           "suffix": suffix})
                def_span = (ident, offset)

            for ident, start, _end in scan_occurrences(raw, families):
                if def_span is not None and def_span == (ident, start):
                    continue              # the defining occurrence is not an inbound citation
                citations[ident][rel] += 1
        if fence is not None:
            # NOT a warning to file away and carry on. Everything after `fence_line` was
            # classified as fenced, so this file contributed no definitions and no citations
            # from that line down -- and "no citations" is what a gate reads as clean.
            unterminated.append({"file": rel, "line": fence_line, "marker": fence})

    ids = sorted(set(definitions) | set(citations), key=sort_key(families))
    partial = [i for i in ids if any(d.get("suffix") for d in definitions.get(i, ()))]
    return {
        "root": root.as_posix(),
        "scope": {"patterns": list(patterns),
                  "excludes": list(excludes),
                  "files": [rel for rel, _ in files]},
        "families": {f.name: f.boundary_form() for f in families},
        "ids": [
            {
                "id": i,
                "family": family_of(i, families),
                "defined_at": ("%s:%d" % (definitions[i][0]["file"], definitions[i][0]["line"]))
                              if i in definitions else None,
                "title": definitions[i][0]["title"] if i in definitions else None,
                "definitions": definitions.get(i, []),
                "citations": sum(citations[i].values()),
                "cited_in": dict(sorted(citations[i].items())),
                "partial": [d["file"] + ":" + str(d["line"]) + " -> " + i + d["suffix"]
                            for d in definitions.get(i, ()) if d.get("suffix")],
            }
            for i in ids
        ],
        "collisions": [i for i in ids if len(definitions.get(i, ())) > 1],
        "dangling": [i for i in ids if i not in definitions],
        "orphans": [i for i in ids if i in definitions and not citations.get(i)],
        "partial_ids": partial,
        "unterminated_fences": unterminated,
        "fenced_only_diagnostic": dict(sorted(fenced_hits.items(), key=sort_key(families,
                                                                               key=lambda kv: kv[0]))),
        "totals": {
            "files": len(files),
            "lines": lines_scanned,
            "ids": len(ids),
            "defined": sum(1 for i in ids if i in definitions),
            "definition_sites": sum(len(v) for v in definitions.values()),
            "citations": sum(sum(c.values()) for c in citations.values()),
            "collisions": sum(1 for i in ids if len(definitions.get(i, ())) > 1),
            "dangling": sum(1 for i in ids if i not in definitions),
            "orphans": sum(1 for i in ids if i in definitions and not citations.get(i)),
            "partial_ids": len(partial),
            "unterminated_fences": len(unterminated),
            "fence_occurrences_skipped": sum(fenced_hits.values()),
        },
    }


def fence_warning(mp):
    """One multi-line stderr warning for an unreliable scan, or '' when the scan is sound."""
    uf = mp.get("unterminated_fences") or ()
    if not uf:
        return ""
    lines = ["warning: SCAN INTEGRITY -- %d file(s) end with an OPEN code fence. Everything "
             "below" % len(uf),
             "         the opening line was classified as fenced, so this scan found no "
             "definition and",
             "         no citation there. A result computed from it is not evidence of anything."]
    for u in uf:
        lines.append("         %s:%d  fence opened with %s and never closed"
                     % (u["file"], u["line"], u["marker"]))
    return "\n".join(lines) + "\n"


def family_of(ident, families):
    best = None
    for fam in families:
        m = fam.anchored.match(ident)
        if m and m.end(1) == len(ident):
            if best is None or len(fam.core) > len(best[1]):
                best = (fam.name, fam.core)
    return best[0] if best else "?"


_NUM_RE = re.compile(r"\d+")


def sort_key(families, key=None):
    """Order ids by family (declaration order), then numerically, then lexically."""
    order = {f.name: n for n, f in enumerate(families)}

    def inner(item):
        ident = key(item) if key else item
        fam = family_of(ident, families)
        nums = tuple(int(x) for x in _NUM_RE.findall(ident))
        return (order.get(fam, len(order)), fam, nums, ident)
    return inner


# --------------------------------------------------------------------------- reporting

def render(mp, summary_only=False, limit=0):
    out = []
    t = mp["totals"]
    out.append("citemap: %s" % mp["root"])
    out.append("  scope     : %s" % " ".join(mp["scope"]["patterns"]))
    out.append("  files     : %d (%d lines)" % (t["files"], t["lines"]))
    out.append("  families  : %s" % ", ".join(sorted(mp["families"])))
    out.append("  ids       : %d  (%d defined, %d definition sites, %d citations)"
               % (t["ids"], t["defined"], t["definition_sites"], t["citations"]))
    out.append("  collisions: %d      dangling: %d      orphans: %d"
               % (t["collisions"], t["dangling"], t["orphans"]))
    out.append("  fence occurrences skipped: %d" % t["fence_occurrences_skipped"])
    if t["unterminated_fences"]:
        out.append("  UNTERMINATED FENCES: %d  -- THIS SCAN IS UNRELIABLE, see below"
                   % t["unterminated_fences"])
    if t["partial_ids"]:
        out.append("  partial-id headings: %d  (heading text continues the id, e.g. `## D1a`)"
                   % t["partial_ids"])

    if not summary_only:
        rows = mp["ids"]
        if limit:
            rows = rows[:limit]
        out.append("")
        out.append("%-12s %-34s %5s  %s" % ("ID", "DEFINED AT", "CITED", "TITLE"))
        out.append("%-12s %-34s %5s  %s" % ("-" * 12, "-" * 34, "-" * 5, "-" * 40))
        for r in rows:
            where = r["defined_at"] or "-- DANGLING --"
            if len(r["definitions"]) > 1:
                where += "  [+%d COLLIDING]" % (len(r["definitions"]) - 1)
            if r.get("partial"):
                where += "  [PARTIAL id]"
            title = (r["title"] or "")[:56]
            out.append("%-12s %-34s %5d  %s" % (r["id"], where, r["citations"], title))
        if limit and len(mp["ids"]) > limit:
            out.append("... %d more (use --limit 0)" % (len(mp["ids"]) - limit))

    if mp.get("unterminated_fences"):
        out.append("")
        out.append("UNTERMINATED FENCES (%d) -- the scan of these files is UNRELIABLE. Everything"
                   % len(mp["unterminated_fences"]))
        out.append("below the opening line read as fenced, so it yielded neither definitions nor")
        out.append("citations. An empty result from a truncated scan is not a clean result.")
        for u in mp["unterminated_fences"]:
            out.append("  %s:%d  opened with %s, never closed" % (u["file"], u["line"], u["marker"]))
    if mp.get("partial_ids"):
        out.append("")
        out.append("PARTIAL-ID HEADINGS (%d) -- the heading text CONTINUES the id it matched, so"
                   % len(mp["partial_ids"]))
        out.append("the string a reader sees and the id this map resolves are different. Spec S8's")
        out.append("`([^0-9]|$)` right boundary is ratified (C-18); this is the signal, not a fix.")
        by_id = {r["id"]: r for r in mp["ids"]}
        for i in mp["partial_ids"]:
            for p in by_id[i]["partial"]:
                out.append("  %-12s %s" % (i, p))

    if mp["collisions"]:
        out.append("")
        out.append("COLLISIONS (%d) -- one id, more than one column-0 heading:" % len(mp["collisions"]))
        by_id = {r["id"]: r for r in mp["ids"]}
        for i in mp["collisions"]:
            sites = ", ".join("%s:%d" % (d["file"], d["line"]) for d in by_id[i]["definitions"])
            out.append("  %-12s %s" % (i, sites))
    if mp["dangling"]:
        out.append("")
        out.append("DANGLING (%d) -- cited, never defined in scope:" % len(mp["dangling"]))
        by_id = {r["id"]: r for r in mp["ids"]}
        for i in mp["dangling"]:
            out.append("  %-12s cited %dx in %s"
                       % (i, by_id[i]["citations"], ", ".join(by_id[i]["cited_in"])))
    return "\n".join(out)


# --------------------------------------------------------------------------- selftest

def _selftest():
    """Fixture suite. No files written, no corpus needed; runs under `python -S -I`."""
    fams = build_families()
    by_name = {f.name: f for f in fams}
    checks = []

    def ck(label, got, want):
        checks.append((label, got, want, got == want))

    def scan(s):
        return [h[0] for h in scan_occurrences(s, fams)]

    # --- D44: the right boundary. The reason this file exists.
    ck("D1 does not match D10", by_name["D"].scan.search("see D10 here").group(1), "D10")
    ck("D1 not found in D10", "D1" in scan("see D10 here"), False)
    ck("D1 not found in D100", "D1" in scan("see D100 here"), False)
    ck("D1 not found in D-PLAN-1", "D1" in scan("see D-PLAN-1 here"), False)
    ck("D-PLAN-1 found in D-PLAN-1", scan("see D-PLAN-1 here"), ["D-PLAN-1"])
    ck("D1 found bare", scan("see D1 here"), ["D1"])
    ck("D1 found at EOL", scan("see D1"), ["D1"])
    ck("D-PLAN-1 not D-PLAN-17", "D-PLAN-1" in scan("D-PLAN-17"), False)
    ck("C-1 not C-10", "C-1" in scan("C-10"), False)
    ck("OQ1 not OQ-V2-1", "OQ1" in scan("OQ-V2-1"), False)
    ck("OQ-V2-1 found", scan("OQ-V2-1"), ["OQ-V2-1"])
    ck("CR-3 does not yield R-", scan("CR-3"), ["CR-3"])
    ck("R-DOC-1 found", scan("R-DOC-1"), ["R-DOC-1"])

    # --- left boundary: W17's rename semantics, both halves.
    ck("suffix D42-archive still reads D42", scan("D42-archive"), ["D42"])
    ck("prefix archive:D42 does NOT read D42", scan("archive:D42"), [])
    ck("D1 not found inside MD1", scan("MD1"), [])
    ck("D1 not found inside AD1", scan("AD1"), [])
    ck("range D1-D71 counts both", scan("D1-D71"), ["D1", "D71"])
    ck("backticked id counts", scan("see `D42` now"), ["D42"])
    ck("bolded id counts", scan("see **D42** now"), ["D42"])

    # --- definitions are column-0 headings only.
    ck("heading defines", find_definition("## D42: title here", fams)[0], "D42")
    ck("heading title", find_definition("## D42: title here", fams)[2], "title here")
    ck("heading em-dash title", find_definition("## C-4 \u2014 fidelity", fams)[2], "fidelity")
    ck("decorated heading defines", find_definition("## **D42** title", fams)[0], "D42")
    ck("indented heading is not one", find_definition("  ## D42: t", fams), None)
    ck("table cell is not a definition", find_definition("| `D42` | x |", fams), None)
    ck("bullet is not a definition", find_definition("- D42 is relevant", fams), None)
    ck("mid-heading id is not a definition",
       find_definition("## See D42 for context", fams), None)
    ck("h6 defines", find_definition("###### F9: x", fams)[0], "F9")
    ck("no-space hash is not a heading", find_definition("##D42: t", fams), None)
    ck("M4.5 defines M4.5", find_definition("## M4.5 - disposal", fams)[0], "M4.5")
    ck("M5 defines M5", find_definition("## M5 startup model", fams)[0], "M5")
    ck("M5. reads as M5", scan("shipped in M5."), ["M5"])

    # --- D93's three-level work breakdown: PHASE / FEAT / TASK.
    #     The hierarchy is a FIELD on the child and both fields are ordinary citations, so a
    #     family missing from the table here is not a gap in a listing -- it is `check` reporting
    #     PASS over a parent link it never looked at.
    ck("PHASE1 found bare", scan("see PHASE1 here"), ["PHASE1"])
    ck("FEAT7 found bare", scan("see FEAT7 here"), ["FEAT7"])
    ck("TASK21 found bare", scan("see TASK21 here"), ["TASK21"])
    # The whole tuple, not just [0]: an UNREGISTERED family makes find_definition return None,
    # and `None[0]` aborts the suite at exit 7 instead of naming the check that failed.
    ck("PHASE1 defines", find_definition("## PHASE1: discovery", fams),
       ("PHASE1", 3, "discovery", ""))
    ck("FEAT7 defines", find_definition("## FEAT7 \u2014 the drain", fams),
       ("FEAT7", 3, "the drain", ""))
    ck("TASK21 defines", find_definition("## TASK21: write the scanner", fams),
       ("TASK21", 3, "write the scanner", ""))
    ck("PHASE family named", family_of("PHASE1", fams), "PHASE")
    ck("FEAT family named", family_of("FEAT7", fams), "FEAT")
    ck("TASK family named", family_of("TASK21", fams), "TASK")
    # the mandatory right boundary, on all three
    ck("PHASE1 not found in PHASE10", "PHASE1" in scan("PHASE10"), False)
    ck("FEAT7 not found in FEAT70", "FEAT7" in scan("FEAT70"), False)
    ck("TASK21 not found in TASK210", "TASK21" in scan("TASK210"), False)

    # NEGATIVE CONTROL: the letters must never match INSIDE a compound label. `OQ-PHASE2-1` is a
    # heading that exists in a real project today, and LEFT_BOUNDARY alone does not stop it --
    # `-` is deliberately absent from that class so `D1-D71` counts both endpoints. Without the
    # per-family `(?<![-./])` guard this line yields a spurious `PHASE2`: an id cited and never
    # defined, in the one list D93's field-citation design depends on staying readable.
    ck("OQ-PHASE2-1 does NOT split into PHASE2", "PHASE2" in scan("see OQ-PHASE2-1 here"), False)
    ck("OQ-PHASE2-1 as a HEADING does not define PHASE2",
       find_definition("## OQ-PHASE2-1: a foreign compound label", fams), None)
    ck("R-TASK-1 stays one id and yields no TASK", scan("see R-TASK-1 here"), ["R-TASK-1"])
    ck("a path component is not a bare id", scan("docs/PHASE2.md"), [])
    ck("a dotted label is not a bare id", scan("v1.TASK4"), [])
    ck("a hyphenated prefix is not a bare id", scan("PRE-FEAT9"), [])

    # NEGATIVE CONTROL: CASE. D93 measured `phase<n>` lowercase at 13,978 occurrences fleet-wide
    # against ONE `PHASE<n>`, so a case-insensitive family is a ~14,000-id namespace of prose.
    ck("lowercase task21 does NOT match", scan("see task21 here"), [])
    ck("lowercase phase1 does NOT match", scan("see phase1 here"), [])
    ck("lowercase feat7 does NOT match", scan("see feat7 here"), [])
    ck("mixed-case Phase1 does NOT match", scan("see Phase1 here"), [])
    ck("mixed-case Task21 does NOT match", scan("see Task21 here"), [])

    # NEGATIVE CONTROL: ORDINARY PROSE. The bare form is safe only because a digit must follow
    # the letters immediately -- that is what "bare form only" buys.
    ck("the word phase in prose is not an id", scan("the next phase is planning"), [])
    ck("the word task in prose is not an id", scan("this task is a step toward a feature"), [])
    ck("PHASE with a space before the number is not an id", scan("PHASE 3 begins tomorrow"), [])
    ck("TASK with a space before the number is not an id", scan("TASK 21 is next"), [])
    ck("a bare family word with no number is not an id", scan("PHASE and TASK and FEAT"), [])

    # F194, pinned in the resolver that causes it: the parent FIELD is only a checkable citation
    # when a space follows the colon. LEFT_BOUNDARY excludes `:`, so the no-space form emits
    # ZERO citations and warns about nothing -- the field parses, the hierarchy link vanishes.
    ck("`phase: PHASE3` emits a checkable citation", scan("phase: PHASE3"), ["PHASE3"])
    ck("`feature: FEAT7` emits a checkable citation", scan("feature: FEAT7"), ["FEAT7"])
    ck("F194: `phase:PHASE3` emits NOTHING (the silent failure)", scan("phase:PHASE3"), [])
    ck("F194: `feature:FEAT7` emits NOTHING", scan("feature:FEAT7"), [])

    # A compound label written as a HEADING that STARTS with the family letters is where the
    # prohibition can still be signalled: the id records as PHASE2 and the partial-id machinery
    # reports that the heading said something else. Note the title -- `_clean_title` strips the
    # leading `-` and the REST OF THE LABEL stays in it, which is the second half of the signal.
    ck("## PHASE2-1 records the atomic id and flags the rest as PARTIAL",
       find_definition("## PHASE2-1: compound", fams), ("PHASE2", 3, "1: compound", "-1"))

    # --- the D1a signal. The BOUNDARY does not move (spec S8 is ratified, C-18); what moves is
    #     that the caller is told the heading said something the id does not.
    ck("## D1a still resolves to D1 (S8's ratified boundary)",
       find_definition("## D1a: the a-variant", fams)[0], "D1")
    ck("## D1a reports the suffix that continued it",
       find_definition("## D1a: the a-variant", fams)[3], "a")
    ck("a clean heading reports no suffix", find_definition("## D1: first", fams)[3], "")
    ck("a clean heading with a word next reports no suffix",
       find_definition("## D42 title here", fams)[3], "")
    # A connector followed by an alphanumeric can continue an id; an ordinary
    # numbered heading such as `## D1. Title` must not count as a partial id.
    for _text, _want in (("D1a", "a"), ("D1_x", "_x"), ("D38-CANDIDATE", "-CANDIDATE"),
                         ("D1.5", ".5"), ("D2/D3", "/D3"), ("M7b.5", "b.5"),
                         ("OQ1-FUP-1", "-FUP-1"), ("F2-operational", "-operational")):
        ck("## %s continues its id" % _text,
           find_definition("## " + _text + ": t", fams)[3], _want)
    for _text in ("D1. a numbered heading", "D1 - dashed title", "D1: colon title",
                  "D1, comma", "D1 (parenthesised)", "D1"):
        ck("## %s does NOT continue its id" % _text,
           find_definition("## " + _text, fams)[3], "")
    ck("a continued heading still records the SHORT id (boundary unchanged)",
       find_definition("## D1_x: underscore variant", fams)[0], "D1")

    # --- end-to-end over an in-memory corpus, via a temp dir the caller owns.
    import io
    import tempfile, os

    def quiet_main(argv):
        """main() with both text channels captured -- the exit code is what is under test."""
        so, se = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        try:
            return main(argv)
        finally:
            sys.stdout, sys.stderr = so, se

    doc_a = ("# Decisions\n"
             "\n"
             "## D1: first\n"
             "Body cites D2 and D10 and D100.\n"
             "\n"
             "## D2: second\n"
             "Body cites D1.\n"
             "\n"
             "```\n"
             "## D999: inside a fence, not a definition, and D1 here is not a citation\n"
             "```\n"
             "\n"
             "Trailing cite of D404 which nothing defines.\n")
    doc_b = "# Digest\n\n## D1: first\nA second definition of D1.\n"
    tmp = tempfile.mkdtemp(prefix="citemap_selftest_")
    try:
        with open(os.path.join(tmp, "decisions.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(doc_a)
        os.mkdir(os.path.join(tmp, "_archive"))
        with open(os.path.join(tmp, "_archive", "digest.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(doc_b)
        mp = build_map(pathlib.Path(tmp), SCOPES["c4"], fams)
        idx = {r["id"]: r for r in mp["ids"]}
        ck("e2e D1 defined at decisions.md:3", idx["D1"]["defined_at"], "decisions.md:3")
        ck("e2e D1 collides", mp["collisions"], ["D1"])
        ck("e2e D1 has 2 definition sites", len(idx["D1"]["definitions"]), 2)
        ck("e2e collision primary is the HOT file, twin is the archive",
           [d["file"] for d in idx["D1"]["definitions"]],
           ["decisions.md", "_archive/digest.md"])
        ck("e2e D1 cited twice (fence excluded)", idx["D1"]["citations"], 2)
        ck("e2e D2 cited once", idx["D2"]["citations"], 1)
        ck("e2e D10 dangling", idx["D10"]["defined_at"], None)
        ck("e2e D999 absent (fence)", "D999" in idx, False)
        ck("e2e dangling set", mp["dangling"], ["D10", "D100", "D404"])
        ck("e2e file count", mp["totals"]["files"], 2)
        ck("e2e ordering is numeric", [r["id"] for r in mp["ids"]],
           ["D1", "D2", "D10", "D100", "D404"])
        # scope is declared: the hot scope must not see the archive twin
        mp_hot = build_map(pathlib.Path(tmp), SCOPES["hot"], fams)
        ck("hot scope excludes _archive", mp_hot["collisions"], [])
        ck("hot scope file count", mp_hot["totals"]["files"], 1)
        # precedence is DECLARABLE: pattern order decides which site is `defined_at`
        with open(os.path.join(tmp, "aaa-digest.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("# gen\n\n## D2: second\n")
        mp_lex = build_map(pathlib.Path(tmp), ("*.md",), fams)
        ck("lexical order puts the generated twin first",
           {r["id"]: r["defined_at"] for r in mp_lex["ids"]}["D2"], "aaa-digest.md:3")
        mp_pin = build_map(pathlib.Path(tmp), ("decisions.md", "*.md"), fams)
        ck("declared pattern order pins the canonical file",
           {r["id"]: r["defined_at"] for r in mp_pin["ids"]}["D2"], "decisions.md:6")
        ck("pinning does not change the collision verdict", mp_pin["collisions"], ["D2"])
        os.remove(os.path.join(tmp, "aaa-digest.md"))
        # bytes-not-text: an undecodable byte must not abort the scan
        with open(os.path.join(tmp, "bad.md"), "wb") as fh:
            fh.write(b"## D7: bad byte \xff here\ncites D1\n")
        mp_bad = build_map(pathlib.Path(tmp), SCOPES["hot"], fams)
        ck("undecodable byte does not abort", "D7" in {r["id"] for r in mp_bad["ids"]}, True)
        # CRLF line numbers agree with LF line numbers
        with open(os.path.join(tmp, "crlf.md"), "wb") as fh:
            fh.write(b"# t\r\n\r\n## W9: crlf heading\r\n")
        mp_crlf = build_map(pathlib.Path(tmp), ("crlf.md",), fams)
        ck("CRLF heading at line 3",
           {r["id"]: r["defined_at"] for r in mp_crlf["ids"]}["W9"], "crlf.md:3")

        # --- THE CRITICAL. An unclosed fence made every later line count as neither definition
        #     nor citation, so `check` printed "PASS: every citation resolves" at exit 0 over a
        #     file it had not actually read. A sound scan of the SAME text finds 2 defs + 2
        #     dangling; the difference between the two is the whole defect.
        with open(os.path.join(tmp, "openfence.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("# T\n\n## D1: first\nbody\n\n```\nunclosed\n\n"
                     "## D2: second\ncites D50 and D51\n")
        mp_of = build_map(pathlib.Path(tmp), ("openfence.md",), fams)
        ck("unterminated fence is DETECTED", mp_of["totals"]["unterminated_fences"], 1)
        ck("unterminated fence names the file", mp_of["unterminated_fences"][0]["file"],
           "openfence.md")
        ck("unterminated fence names the OPENING line", mp_of["unterminated_fences"][0]["line"], 6)
        ck("unterminated fence records the marker", mp_of["unterminated_fences"][0]["marker"],
           "```")
        ck("the truncated scan really did hide D2", "D2" in {r["id"] for r in mp_of["ids"]}, False)
        ck("a warning string is produced", "SCAN INTEGRITY" in fence_warning(mp_of), True)
        ck("the report names the file and line",
           "openfence.md:6" in render(mp_of), True)
        ck("main exits 6, NOT 0, on an unterminated fence",
           quiet_main(["--root", tmp, "--include", "openfence.md", "--summary"]), EXIT_SCAN_INTEGRITY)
        ck("integrity outranks --strict's own code",
           quiet_main(["--root", tmp, "--include", "openfence.md", "--summary", "--strict"]),
           EXIT_SCAN_INTEGRITY)
        # the SAME text with the fence closed: the verdict the gate should have been giving
        with open(os.path.join(tmp, "openfence.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("# T\n\n## D1: first\nbody\n\n```\nclosed\n```\n\n"
                     "## D2: second\ncites D50 and D51\n")
        mp_cf = build_map(pathlib.Path(tmp), ("openfence.md",), fams)
        ck("closing the fence clears the integrity flag",
           mp_cf["totals"]["unterminated_fences"], 0)
        ck("closing the fence reveals the hidden definition",
           mp_cf["totals"]["defined"], 2)
        ck("closing the fence reveals the hidden dangling ids", mp_cf["dangling"],
           ["D50", "D51"])
        ck("a sound scan exits 0", quiet_main(["--root", tmp, "--include", "openfence.md",
                                         "--summary"]), EXIT_OK)
        ck("a sound scan still honours --strict",
           quiet_main(["--root", tmp, "--include", "openfence.md", "--summary", "--strict"]),
           EXIT_STRICT)
        os.remove(os.path.join(tmp, "openfence.md"))

        # --- an ABSOLUTE --include is a usage error, not a NotImplementedError traceback.
        try:
            build_map(pathlib.Path(tmp), (os.path.join(tmp, "decisions.md"),), fams)
            ck("absolute pattern raises ValueError", "no exception", "ValueError")
        except ValueError as exc:
            ck("absolute pattern raises ValueError", "RELATIVE to --root" in str(exc), True)
        ck("absolute --include exits 2, not 1",
           quiet_main(["--root", tmp, "--include", os.path.join(tmp, "decisions.md")]), EXIT_USAGE)

        # --- a path is not a pattern. build_map(files=...) takes an explicit set so a filename
        #     holding glob metacharacters cannot silently drop out of the scan.
        with open(os.path.join(tmp, "a[1].md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# A\n\n## D77: bracket file\ncites D78\n")
        bracket = pathlib.Path(tmp) / "a[1].md"
        mp_glob = build_map(pathlib.Path(tmp), ("a[1].md",), fams)
        ck("re-globbing a bracket filename finds NOTHING", mp_glob["totals"]["files"], 0)
        mp_expl = build_map(pathlib.Path(tmp), ("<explicit>",), fams,
                            files=[("a[1].md", bracket)])
        ck("an explicit file list scans it", mp_expl["totals"]["files"], 1)
        ck("an explicit file list finds its ids",
           [r["id"] for r in mp_expl["ids"]], ["D77", "D78"])
        os.remove(str(bracket))

        # --- the D1a signal survives end to end.
        with open(os.path.join(tmp, "partial.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# P\n\n## D81a: the a-variant\nbody\n")
        mp_p = build_map(pathlib.Path(tmp), ("partial.md",), fams)
        ck("## D81a is still recorded as D81 (boundary unchanged)", mp_p["partial_ids"], ["D81"])
        ck("the partial site names the heading it really read",
           {r["id"]: r for r in mp_p["ids"]}["D81"]["partial"], ["partial.md:3 -> D81a"])
        ck("the table flags it", "[PARTIAL id]" in render(mp_p), True)
        os.remove(os.path.join(tmp, "partial.md"))

        # --- D93's work breakdown, end to end. The point of this fixture is not that three
        #     headings parse: it is that the PARENT FIELDS resolve. `phase: PHASE1` and
        #     `feature: FEAT7` are ordinary inbound citations and nothing else, which is the
        #     entire mechanism by which a mis-typed parent shows up as a dangling reference in
        #     the gate that already runs. Line 13 is the negative control and it is a heading
        #     that exists in a real project: `OQ-PHASE2-1` must contribute NO id at all.
        with open(os.path.join(tmp, "wbs.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Plan\n"
                     "\n"
                     "## PHASE1: discovery\n"
                     "Body cites FEAT7.\n"
                     "\n"
                     "## FEAT7: the drain\n"
                     "phase: PHASE1\n"
                     "Body cites TASK21.\n"
                     "\n"
                     "## TASK21: write the scanner\n"
                     "feature: FEAT7\n"
                     "\n"
                     "## OQ-PHASE2-1: a foreign compound label\n"
                     "\n"
                     "Prose: the next phase of this task is a feature; task21 stays lowercase.\n")
        mp_wbs = build_map(pathlib.Path(tmp), ("wbs.md",), fams)
        idx_wbs = {r["id"]: r for r in mp_wbs["ids"]}

        def wbs(ident, field):
            """One id's field, or None. `.get` and not `[...]`, because the fault these arms
            guard is an UNREGISTERED family -- and that is exactly the fault that makes the id
            absent, so `idx_wbs[ident]` raises KeyError and the run dies at exit 7 naming a
            dictionary instead of the check. Every arm below has to be able to FAIL."""
            return (idx_wbs.get(ident) or {}).get(field)

        ck("e2e PHASE1 defined at wbs.md:3", wbs("PHASE1", "defined_at"), "wbs.md:3")
        ck("e2e FEAT7 defined at wbs.md:6", wbs("FEAT7", "defined_at"), "wbs.md:6")
        ck("e2e TASK21 defined at wbs.md:10", wbs("TASK21", "defined_at"), "wbs.md:10")
        ck("e2e the titles come back too", wbs("TASK21", "title"), "write the scanner")
        ck("e2e `phase: PHASE1` is an inbound citation", wbs("PHASE1", "citations"), 1)
        ck("e2e FEAT7 cited twice: body + the `feature:` field",
           wbs("FEAT7", "citations"), 2)
        ck("e2e TASK21 cited once", wbs("TASK21", "citations"), 1)
        ck("e2e OQ-PHASE2-1 yields NO spurious PHASE2", "PHASE2" in idx_wbs, False)
        ck("e2e the id set is EXACTLY the three registered families",
           [r["id"] for r in mp_wbs["ids"]], ["PHASE1", "FEAT7", "TASK21"])
        ck("e2e prose and lowercase contribute nothing", mp_wbs["dangling"], [])
        ck("e2e no collisions", mp_wbs["collisions"], [])
        ck("e2e a sound scan of it exits 0",
           quiet_main(["--root", tmp, "--include", "wbs.md", "--summary"]), EXIT_OK)
        # ...and the same corpus with ONE mis-typed parent field is the dangling reference D93
        # says it is -- the whole reason the families belong in the table rather than behind
        # `--family`, which reaches 2 of 7 tools and not the save path.
        with open(os.path.join(tmp, "wbs.md"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n## TASK22: mistyped parent\nfeature: FEAT8\n")
        mp_typo = build_map(pathlib.Path(tmp), ("wbs.md",), fams)
        ck("e2e a mistyped parent field DANGLES", mp_typo["dangling"], ["FEAT8"])
        ck("e2e --strict catches it",
           quiet_main(["--root", tmp, "--include", "wbs.md", "--summary", "--strict"]),
           EXIT_STRICT)
        os.remove(os.path.join(tmp, "wbs.md"))

        # --- a LEADING BOM must not hide a fence. `FENCE_RE` is `^ {0,3}`; U+FEFF is neither a
        #     space nor a backtick, so BOM+``` on line 1 opened NOTHING and the code block
        #     scanned as live definitions and citations. Scanner and renderer must
        #     agree about the opening fence.
        bomf = os.path.join(tmp, "bomfence.md")
        with open(bomf, "wb") as fh:
            fh.write(b"\xef\xbb\xbf```\n## D85: hidden by a BOM-prefixed fence\ncites D86\n")
        ck("read_lines drops the leading BOM",
           read_lines(pathlib.Path(bomf))[0], "```")
        mp_b = build_map(pathlib.Path(tmp), ("bomfence.md",), fams)
        ck("the BOM'd fence really opens", len(mp_b["unterminated_fences"]), 1)
        ck("...on line 1", mp_b["unterminated_fences"][0]["line"], 1)
        ck("...so nothing inside it is a definition", mp_b["totals"]["defined"], 0)
        ck("...and nothing inside it is a citation", mp_b["totals"]["citations"], 0)
        ck("...and the run is REFUSED, not reported",
           quiet_main(["--root", tmp, "--include", "bomfence.md"]), EXIT_SCAN_INTEGRITY)
        # ...while a BOM'd file with no fence still maps, on the right line numbers
        with open(bomf, "wb") as fh:
            fh.write(b"\xef\xbb\xbf# T\r\n\r\n## D87: real\r\ncites D88\r\n")
        mp_b2 = build_map(pathlib.Path(tmp), ("bomfence.md",), fams)
        ck("a BOM'd file with no fence still maps", mp_b2["unterminated_fences"], [])
        ck("...with the heading on its real line",
           [(d["file"], d["line"]) for i in mp_b2["ids"] for d in i["definitions"]],
           [("bomfence.md", 3)])
        ck("...and its citation counted", mp_b2["totals"]["citations"], 1)
        # a U+FEFF that is NOT at byte 0 is ZWNBSP, ordinary content, and stays
        with open(bomf, "wb") as fh:
            fh.write(b"# T\n\n## D89: zwnbsp \xef\xbb\xbf inside\nbody\n")
        ck("a U+FEFF mid-file is left alone",
           "﻿" in read_lines(pathlib.Path(bomf))[2], True)
        os.remove(bomf)

        # --- a pattern that ESCAPES --root is refused, not silently dropped. `..` globbed fine
        #     and was then discarded path-by-path by the dot-prefix rule, which reads to a
        #     caller as "no match" -- a claim about the corpus instead of about the flag.
        #     THE DRIVE LETTER BELOW IS DELIBERATELY NOT THIS BUILD HOST'S. What the case tests
        #     is a drive-letter ABSOLUTE pattern, as distinct from the POSIX absolute beside it;
        #     which letter it carries is incidental. A real build-host root here is a literal in
        #     executed code that ships to every machine, which the fleet portability check reads
        #     as a satellite-breaking path -- so it is written as an obviously-fictional volume.
        #     Do not "correct" it back to a path that exists on the machine you are reading this on.
        for _bad in ("../*.md", "../../elsewhere/*.md", "a/../../b/*.md",
                     "/abs/*.md", "Z:/elsewhere/*.md"):
            try:
                collect_files(pathlib.Path(tmp), (_bad,))
                _ok = False
            except ValueError:
                _ok = True
            ck("collect_files refuses the escaping pattern %r" % _bad, _ok, True)
        for _good in ("*.md", "**/*.md", "_archive/**/*.md", "./decisions.md", "a[1].md"):
            try:
                collect_files(pathlib.Path(tmp), (_good,))
                _ok = True
            except ValueError:
                _ok = False
            ck("collect_files still accepts %r" % _good, _ok, True)
        ck("an escaping --include is exit 2, not a quiet empty scope",
           quiet_main(["--root", tmp, "--include", "../*.md"]), EXIT_USAGE)

        # --- `.ash-id-families`: the per-project family declaration. ------------------------
        #     THE DEFECT THIS CLOSES IS SILENT, PERMANENT AND AT EXIT 0: a project whose headings
        #     use an unregistered family resolves ZERO ids, so state.md's counts are wrong, demote
        #     row 2b's baseline is wrong, and `recall get` cannot fetch the entry once it is
        #     cold. Three MEASURED instances -- and the first three arms are the controls that
        #     prove each one really is unresolvable under the built-ins, because every arm below
        #     is worthless if the built-ins already matched.
        ck("decl/control: OQ-MM-5 resolves to NOTHING under the built-ins (peer example-project)",
           scan("see OQ-MM-5 here"), [])
        ck("decl/control: R-UNBLOCK-1 too -- UNBLOCK is 7 letters, `R-` caps at 6",
           scan("see R-UNBLOCK-1 here"), [])
        ck("decl/control: Q13 too -- there is no `Q` family (peer sysprompt)",
           scan("see Q13 here"), [])

        # `first(...)` and never `probs[0]`: the fault every arm below guards is a problem list
        # that came back EMPTY, and that is exactly the value on which `[0]` raises IndexError --
        # which aborts the whole suite at exit 7 instead of naming the check that failed. Same
        # rule as the `find_definition` tuple arms above: every arm has to be able to FAIL.
        def first(seq):
            return seq[0] if seq else ""

        decl = os.path.join(tmp, "decl")

        os.mkdir(decl)
        declfile = os.path.join(decl, DECLARED_FAMILIES_FILE)

        def wr_decl(text):
            with open(declfile, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

        # ABSENT IS THE NORMAL CASE AND MUST BE SILENT. Every project that has never needed a
        # family of its own has to behave exactly as it did before this existed -- a warning
        # every project sees on every save is a warning every operator learns to skip.
        ck("decl/absent: no specs", load_declared_families(decl)[0], [])
        ck("decl/absent: NO problems -- silence is the contract",
           load_declared_families(decl)[1], [])
        ck("decl/absent: the table is exactly the built-ins, in order",
           [f.name for f in project_families(decl)[0]], [n for n, _ in FAMILY_PATTERNS])

        # blank lines, indented blanks and `#` comments contribute nothing and are not problems
        wr_decl("# the peer's own question family\n"
                "\n"
                "   \n"
                "\t# an indented comment is still a comment\n"
                "OQ-MM=OQ-MM-(\\d+)\n")
        ck("decl/skips: blanks and comments yield one spec and no problems",
           load_declared_families(decl), (["OQ-MM=OQ-MM-(\\d+)"], []))
        fams_d, probs_d = project_families(decl)
        ck("decl/good: the family is registered", "OQ-MM" in [f.name for f in fams_d], True)
        ck("decl/good: a clean declaration is silent", probs_d, [])
        ck("decl/good: the declared table matches the id the built-ins could not",
           [h[0] for h in scan_occurrences("see OQ-MM-5 here", fams_d)], ["OQ-MM-5"])
        ck("decl/good: the built-in families all survive alongside it",
           [n for n, _ in FAMILY_PATTERNS] == [f.name for f in fams_d[:len(FAMILY_PATTERNS)]],
           True)

        # A BAD LINE COSTS ONE LINE. Never an exception, never an abort: the save path calls this,
        # and step 7d is the only abort in that pipeline. The arms check BOTH sides of the bad
        # line still load, because "skip" implemented as "stop" passes a one-sided check.
        wr_decl("OQ-MM=OQ-MM-(\\d+)\n"
                "this line has no equals sign at all\n"
                "Q=Q(\\d+)\n")
        specs_b, probs_b = load_declared_families(decl)
        ck("decl/no-eq: the good lines on BOTH sides still load",
           specs_b, ["OQ-MM=OQ-MM-(\\d+)", "Q=Q(\\d+)"])
        ck("decl/no-eq: exactly one problem", len(probs_b), 1)
        ck("decl/no-eq: it names the file AND the 1-based line number",
           first(probs_b).startswith("%s:2:" % declfile), True)

        wr_decl("Q=Q(\\d+)\n"
                "BAD=Q([0-9\n"
                "R2=R2-(\\d+)\n")
        specs_r, probs_r = load_declared_families(decl)
        ck("decl/bad-regex: the good lines on BOTH sides still load",
           specs_r, ["Q=Q(\\d+)", "R2=R2-(\\d+)"])
        ck("decl/bad-regex: exactly one problem", len(probs_r), 1)
        ck("decl/bad-regex: named by file:line",
           first(probs_r).startswith("%s:2:" % declfile), True)
        ck("decl/bad-regex: NOT raised -- project_families still returns a usable table",
           "Q" in [f.name for f in project_families(decl)[0]], True)

        # an empty NAME and an empty REGEX are two distinct malformations of the same line shape
        wr_decl("=Q(\\d+)\n"
                "NOREGEX=\n"
                "Q=Q(\\d+)\n")
        specs_e, probs_e = load_declared_families(decl)
        ck("decl/empty-half: only the sound line loads", specs_e, ["Q=Q(\\d+)"])
        ck("decl/empty-half: BOTH malformations are reported", len(probs_e), 2)

        # PRESENT BUT UNREADABLE IS NOT THE ABSENT CASE and may not be silent: the project
        # declared families and is getting none of them. A directory at the path is the portable
        # way to force an OSError that is NOT FileNotFoundError (PermissionError on Windows,
        # IsADirectoryError elsewhere) -- the arm asserts the SHAPE, never the errno text.
        unread = os.path.join(tmp, "decl_unreadable")
        os.mkdir(unread)
        os.mkdir(os.path.join(unread, DECLARED_FAMILIES_FILE))
        specs_u, probs_u = load_declared_families(unread)
        ck("decl/unreadable: no specs", specs_u, [])
        ck("decl/unreadable: exactly one problem, and it names the path",
           len(probs_u) == 1 and DECLARED_FAMILIES_FILE in first(probs_u), True)

        # SHADOWING A BUILT-IN IS LEGAL AND IS THE POINT -- it is how this project's own
        # `R-UNBLOCK-1` becomes resolvable -- but it may not be SILENT. From the outside, one
        # family behaving differently here than in every other project is indistinguishable from
        # a bug in this file, and the operator cannot discover the cause except by reading a
        # dotfile they may not know exists.
        wr_decl("R-=R-[A-Z]{2,8}-(\\d+)\n")
        fams_s, probs_s = project_families(decl)
        ck("decl/shadow: the built-in is REPLACED, not duplicated",
           len([f for f in fams_s if f.name == "R-"]), 1)
        ck("decl/shadow: exactly one problem", len(probs_s), 1)
        ck("decl/shadow: it is a NOTE naming the family and saying it REPLACES the built-in",
           first(probs_s).startswith("NOTE:") and "R-" in first(probs_s)
           and "REPLACES" in first(probs_s), True)
        ck("decl/shadow: R-UNBLOCK-1 resolves now",
           [h[0] for h in scan_occurrences("see R-UNBLOCK-1 here", fams_s)], ["R-UNBLOCK-1"])
        ck("decl/shadow: and the narrower built-in form still does too",
           [h[0] for h in scan_occurrences("see R-DOC-1 here", fams_s)], ["R-DOC-1"])

        # PRECEDENCE. `build_families` replaces by name in loop order and LAST WINS, so the
        # declared specs are passed FIRST and an explicit `--family` overrides the file. Reverse
        # the two and the file silently beats the flag, leaving the operator no way to override a
        # declaration except by editing it.
        wr_decl("Q=Q(\\d+)\n")
        fams_p, _probs_p = project_families(decl, None, ["Q=QQ(\\d+)"])
        ck("decl/precedence: exactly one Q family", len([f for f in fams_p if f.name == "Q"]), 1)
        ck("decl/precedence: the CLI --family WINS over the file",
           [f.core for f in fams_p if f.name == "Q"], ["QQ(\\d+)"])

        # END TO END, over a real file: a heading that resolved to NOTHING resolves after the
        # declaration lands beside it. This is the arm that would fail if the loader were wired
        # to nothing -- every arm above it passes on a loader no caller ever calls.
        with open(os.path.join(decl, "backlog.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Backlog\n"
                     "\n"
                     "## OQ-MM-5: the peer's own family\n"
                     "Body cites OQ-MM-9.\n"
                     "\n"
                     "## OQ-MM-9: the second one\n")
        os.remove(declfile)
        mp_pre = build_map(pathlib.Path(decl), ("backlog.md",), project_families(decl)[0])
        ck("decl/e2e: WITHOUT the declaration the file resolves ZERO ids",
           [r["id"] for r in mp_pre["ids"]], [])
        wr_decl("OQ-MM=OQ-MM-(\\d+)\n")
        mp_post = build_map(pathlib.Path(decl), ("backlog.md",), project_families(decl)[0])
        ck("decl/e2e: WITH it, both headings define",
           [r["id"] for r in mp_post["ids"]], ["OQ-MM-5", "OQ-MM-9"])
        ck("decl/e2e: ...on the right lines",
           [r["defined_at"] for r in mp_post["ids"]], ["backlog.md:3", "backlog.md:6"])
        ck("decl/e2e: ...and the inbound citation is counted",
           [r["citations"] for r in mp_post["ids"]], [0, 1])
        ck("decl/e2e: the CLI loads it from --root WITHOUT being asked",
           quiet_main(["--root", decl, "--include", "backlog.md", "--summary"]), EXIT_OK)
        # ...and the dotfile is not corpus: `collect_files` drops dot-prefixed components, so the
        # declaration costs zero tokens and needs no manifest row.
        ck("decl/e2e: the declaration file itself is NOT scanned",
           [rel for rel, _p in collect_files(pathlib.Path(decl), SCOPES["all"])],
           ["backlog.md"])

        # A DECLARED FAMILY GETS THE SAME TWO BOUNDARIES AS A BUILT-IN, AND THIS IS THE ARM THAT
        # SAYS SO. Raised by peer `discriminator`, whose folder wants `Q=Q(\d+)` and which
        # could not test the hazard there: `OQ5` CONTAINS `Q5`, so an UNANCHORED declared family
        # would make every `OQ<n>` id in every OTHER project also resolve as a `Q` id -- a silent
        # wrong answer inside a BUILT-IN family, caused by a dotfile in an unrelated folder, and
        # invisible to the project that introduced it. It is closed by construction, because
        # `project_families` routes through `build_families` and every `Family` is wrapped in
        # LEFT_BOUNDARY/RIGHT_BOUNDARY regardless of where its pattern came from. Closed by
        # construction is exactly what regresses silently when someone later "optimises" the
        # declared path to skip the wrapper, so it is PINNED here rather than left to a reader of
        # `boundary_form` to notice.
        fams_q = build_families(extra=["Q=Q(\\d+)"])
        ck("decl/boundary: the declared family is wrapped in BOTH boundaries",
           [f for f in fams_q if f.name == "Q"][0].boundary_form(),
           "(?<![0-9A-Za-z_:])(Q(\\d+))(?![0-9])")
        ck("decl/boundary: `Q13` still resolves -- anchoring does not break the real case",
           [i for i, _s, _e in scan_occurrences("## Q13 - a real one", fams_q)], ["Q13"])
        ck("decl/boundary: `OQ5` yields OQ5 and NOT a spurious Q5",
           [i for i, _s, _e in scan_occurrences("## OQ5 - an OQ id", fams_q)], ["OQ5"])
        ck("decl/boundary: `OQ12`/`OQ-V2-3` in prose yield no Q ids",
           [i for i, _s, _e in scan_occurrences("see OQ12 and OQ-V2-3 here", fams_q)],
           ["OQ12", "OQ-V2-3"])
        ck("decl/boundary: `Quality` and `IQ7` yield nothing at all",
           [i for i, _s, _e in scan_occurrences("the word Quality and IQ7 here", fams_q)], [])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [c for c in checks if not c[3]]
    for label, got, want, ok in checks:
        if not ok:
            print("FAIL  %-42s got=%r want=%r" % (label, got, want))
    print("selftest: %d checks, %d failed" % (len(checks), len(bad)))
    return 1 if bad else 0


# --------------------------------------------------------------------------- cli

EXIT_OK = 0
EXIT_STRICT = 1
EXIT_USAGE = 2
EXIT_SCAN_INTEGRITY = 6
EXIT_INTERNAL = 7


def _emit(text):
    """Write the report to stdout, treating a stdout that cannot be written to as "discard it".

    `citemap.py ... >&-` closes fd 1, CPython sets `sys.stdout` to None, and `None.write` raised
    AttributeError -- which the cli() wrapper correctly caught and reported as 7, INTERNAL
    ERROR. One line rather than a traceback, but still the wrong answer: nothing internal broke,
    a caller said "discard the output", and the SCAN VERDICT (0/1/6) is what it was going to
    gate on. The report is unreachable either way; the exit code does not have to be.
    """
    try:
        sys.stdout.write(text)
    except (BrokenPipeError, ValueError, OSError, AttributeError):
        pass


def main(argv=None):
    # encoding is as load-bearing as newline. See the INVARIANT above: a redirect makes stdout
    # cp1252 here and the renderer echoes heading titles VERBATIM, so one U+2192 in a title
    # aborted the run at exit 1 -- the same code `--strict` uses for a real verdict.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        prog="citemap.py",
        description="Project-scoped citation map: id -> defining file:line + inbound count.",
        epilog="exit: 0 complete scan | 1 --strict verdict or --selftest failure | 2 usage/IO "
               "| 6 scan integrity (unterminated fence) | 7 internal error")
    ap.add_argument("--root", help="project root. REQUIRED -- scope is declared, never defaulted (D53).")
    ap.add_argument("--scope", default=None, choices=sorted(SCOPES),
                    help="named file set (default: %s)" % DEFAULT_SCOPE)
    ap.add_argument("--include", action="append", default=[], metavar="GLOB",
                    help="glob relative to --root; repeatable; overrides --scope")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="drop matching paths (matched against the root-relative posix path)")
    ap.add_argument("--families", default=None, metavar="A,B,C",
                    help="restrict to these families (default: all built-in)")
    ap.add_argument("--family", action="append", default=[], metavar="NAME=REGEX",
                    help="add or override one family; repeatable; wins over %s"
                         % DECLARED_FAMILIES_FILE)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the table")
    ap.add_argument("--summary", action="store_true", help="counts only, no per-id table")
    ap.add_argument("--limit", type=int, default=0, metavar="N", help="cap table rows (0 = all)")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any collision or dangling id is present")
    ap.add_argument("--selftest", action="store_true", help="run the fixture suite and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    if args.root is None:
        ap.error("--root is required: the scope is DECLARED, never inferred from cwd (D53). "
                 "512 distinct id strings exist across the fleet and D17/C-6/F1 recur in "
                 "dozens of unrelated projects, so an inferred scope resolves wrongly.")
    root = pathlib.Path(args.root)
    if not root.is_dir():
        print("error: --root is not a directory: %s" % args.root, file=sys.stderr)
        return EXIT_USAGE

    if args.include:
        patterns = tuple(args.include)
        if args.scope:
            print("error: pass --scope or --include, not both", file=sys.stderr)
            return EXIT_USAGE
    else:
        patterns = SCOPES[args.scope or DEFAULT_SCOPE]

    try:
        # `--root` is the project, so its declaration is loaded WITHOUT being asked for; `--family`
        # layers on top and wins on a name collision (see project_families).
        families, fam_problems = project_families(
            root,
            [s.strip() for s in args.families.split(",") if s.strip()] if args.families else None,
            args.family)
        for problem in fam_problems:
            sys.stderr.write("warning: %s\n" % problem)
        mp = build_map(root.resolve(), patterns, families, tuple(args.exclude))
    except (ValueError, IOError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    if not mp["totals"]["files"]:
        print("error: scope %s matched no files under %s"
              % (" ".join(patterns), root), file=sys.stderr)
        return EXIT_USAGE

    if args.json:
        _emit(json.dumps(mp, indent=2, sort_keys=False) + "\n")
    else:
        _emit(render(mp, args.summary, args.limit) + "\n")

    # Integrity outranks verdict. A `--strict` PASS built on a truncated scan is the failure
    # mode this exists to stop, so the integrity code wins even when nothing else is wrong.
    if mp["unterminated_fences"]:
        sys.stderr.write(fence_warning(mp))
        return EXIT_SCAN_INTEGRITY
    if args.strict and (mp["collisions"] or mp["dangling"]):
        return EXIT_STRICT
    return EXIT_OK


def cli(argv=None):
    """main() plus the two things that must not turn a verdict into a different number.

    A downstream `| head` closes stdout early; CPython then fails its own shutdown flush and the
    process exits 120 (VERIFIED 2026-08-02, 3/3 on the broker corpus via recall's report),
    which is not in the exit table and reads to a caller as an unknown failure. And an uncaught
    exception used to exit 1, colliding with the `--strict` verdict.
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
        # Silence the interpreter's own shutdown flush, which would otherwise exit 120.
        try:
            import os
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
    return code


if __name__ == "__main__":
    sys.exit(cli())
