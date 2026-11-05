#!/usr/bin/env python3
"""validate_entries.py -- step 7d VALIDATE for save v4. Reads; never writes.

Third sibling of `recall.py` and `citemap.py` in this `scripts/` folder. It answers one
question about a project's living docs: does every entry declare a kind and a lifetime that the
v4 taxonomy (spec S2) allows, in a file whose declared kind (spec S3.1/S3.2) permits it?

CONTRACT: the exit code IS the verdict. 0 valid / warnings-only / --report; 2 usage or I/O;
3 the step-7d abort set is non-empty; 6 scan integrity; 7 internal error. 1 belongs to
`--selftest` and to nothing else -- a caller that cannot tell a broken tool from a failed
selftest has no verdict at all. VERIFIED 2026-08-03: across the 390-check suite the only codes
any normal invocation returned were 0, 2, 3 and 6 (asserted by `_st_arms_invariants`).

CONTRACT: THIS TOOL WRITES NO FILE, EVER (ruling R-D). There is no --fix, no --apply, no
backup, not even for the T-2 lifetime resolution. Every finding is a line on stdout or stderr.
A validator that edits is a migrator, and spec S5.4 names the prose-rewriting pass "the only
content-destroying step in v4"; it is not this step and this step must not grow into it.

CONTRACT: every reported violation names the entry id AND file:line. "3 violations" without
names is a defect -- the operator cannot act on a count.

INVARIANT: exit 6 OUTRANKS the abort verdict on every path that reads a file, and it outranks
--report's forced 0 as well. Decided in exactly one place, `recall.integrity_code`; never
hand-rolled here. MEASURED: a checker that fell back to "nothing here" under an unterminated
fence reported PASS over a file it had not read. That shipped once in this project and was
caught only by an adversary (see `recall.integrity_code`, verified 2026-08-02). An
unread file is not a clean report. EVERY PATH means every read site that feeds the A1..A8 ABORT
SET -- `scan_file`, `load_expired_implications` AND `resolve_current_save`. The third was missed
when the guard was
swept to the first two, and a fenced `state.md` under `--file` scoping then lost exit 6 and
silently deleted a real A6 (measured 2026-08-03). Adding a read site means adding a guard; the
suite now has an arm per site that runs with the file OUT of the entry scan set, because an arm
that leaves it in cannot tell which guard fired.
**A14 ADDS A FOURTH READ PATH AND IS DELIBERATELY OUTSIDE THIS INVARIANT.** It is warning-only,
so it may not set exit 6 and does not touch `scan.unreliable`: it reports its OWN scan as VOID
instead, because a warning that changed the exit code would block a save (C-53) -- the exact
outcome this invariant, followed literally, would produce. The sentence above said "all THREE
read sites" and went stale the moment A14 landed; it is scoped rather than deleted because the
abort-set half of it is still true and still load-bearing. Found by cross-family review
(`gpt-5.6-sol`, 2026-08-29), and it is the same shape as the two status sentences in
`architecture.md` that outlived what they described.

INVARIANT: no second fence state machine, no second path guard, no second line splitter, no
second stdout wrapper, and no second VALUE tokeniser. `recall.load_doc` /
`.resolve_under_root` / `._PipeSafe` and `citemap.collect_files` / `.FENCE_RE` / `.HEADING_RE`
are imported, not re-implemented. A duplicate drifts from the original and the drift is
invisible until a fixture catches it -- measured here: A4 re-split a `kind:` value with its own
copy of the token logic and read `spec`+`rules` out of a line A2 reported, from the same
characters, as the unrecognised kind `'unresolved (spec'`. One tokeniser now: `_vocab_tokens`.

INVARIANT: what the tool CANNOT see, it says out loud. A file that parses to zero entries, a
setext or indented heading, a check whose input is missing -- each is a named warning, never
silence. "ABORT SET (0)" over an empty entry set is a universal quantifier that reads as a
pass, and that is the same wrong verdict as exit 6's, one level up.

INVARIANT: the abort set is CLOSED at A1..A8. Nothing may be added to it here. A size breach,
a trigger breach, an unresolvable `impl:`, an over-budget `why-not:`/`refuted:` line and a
dangling reference are all NOT aborts -- each of those is a ratified decision (D61, D67, W23)
and getting one wrong REVERSES it. `impl:` is PARKED entirely at Phase 1 (spec S2.5) and is
therefore not implemented at all, not even as a warning. Findings that have no member -- a
malformed performed split, an entry whose lifetime is longer than its file's, a shape the
parser cannot see -- are WARNINGS and never move the exit code.

INVARIANT: every member of the abort set has a BROKEN fixture that must abort AND be named, and
a CLEAN control that must exit 0. The false-positive half is not optional: an over-eager
validator that aborts real saves gets switched off, and then nothing is checked at all.
MEASURED 2026-08-02, first green run of the suite: 251 checks, 0 failed -- and building it
caught two of its OWN defects, an arm that passed on the substring in its fixture's directory
name and a "well-formed split" fixture that was itself an A1. Both were fixture bugs; both
would have read as coverage. MEASURED AGAIN 2026-08-03, after adversarial review: four of the
green arms were "documented by measurement" fixtures written to CONFIRM a decision, and a
fixture written to confirm a decision cannot also probe it -- all four sat on top of a
fail-OPEN. `_st_arms_regressions` is the pin for each defect they hid.

INVARIANT: A CHECK THAT CANNOT BE CLEARED IS NOT A CHECK. A6's renewal-freshness ground has an
EVALUATION MOMENT -- the save on which the entry declares it was newly asserted -- and fires
only there. MEASURED 2026-08-03 on ONE FILE THAT NEVER CHANGED ON DISK: a legitimate renewal
made at save 5 gave exit 0 at `--save 5`, exit 3 at `--save 6` and exit 3 at `--save 7`, and
the only way to clear it was to rewrite the user's verbatim quote to a save it was not said in
-- the rule protecting verbatim provenance demanding that provenance be falsified. The gate is
the entry's declared asserted save#; the EVIDENCE is still, and only, the save# the user's own
quote carries. THE GATE IS `A >= C`, NOT `A == C`: a declared assertion save# in the FUTURE is
incoherent, and under `==` it SHUT THE GATE -- `asserted save#99 · expires save#105` with a
stale save-1 quote measured exit 0 at `--save 5/20/50/100` on 2026-08-03 while the honest
`asserted save#5` twin aborted at `--save 5`. A5's horizon base is clamped `min(A, C)` for the
same reason (`_horizon_base`): that same lie read a 100-save hot life as a 6-save one. Why the
asymmetry is not self-certification, why understating the gate buys an agent nothing, and the
one-line principle behind both clamps: `rule_a6_provenance`. Pinned by `_st_arms_a6_moment`.

EXTERNAL: `python -S -I` is the mandated run mode. `-I` implies `-P`, so the script's own
directory is NOT on sys.path and the sibling import fails without the explicit insert below
(VERIFIED on this box 2026-08-03, CPython 3.12).

EXTERNAL: stdout on this box resolves to cp1252 when piped. This report echoes corpus heading
text, which carries U+00B7 and U+2192 among others, so without the utf-8 reconfigure in
main() the tool dies with a UnicodeEncodeError traceback INSTEAD of printing its verdict and
the caller sees a crash where it expected an exit code.

EXTERNAL: --root is REQUIRED and never defaults to cwd (D53). No path in this file is a
build-host path; every path arrives through argv.
"""

import argparse
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


# --------------------------------------------------------------------------- exit codes
# Fixed across all three scripts. Do not redefine one.

EXIT_OK = 0                 # valid; or --report mode; or warnings only
EXIT_SELFTEST = 1           # --selftest ONLY, and only on a failed assertion.
                            # No normal invocation may ever return 1.
EXIT_USAGE = 2              # usage or I/O: missing/not-a-dir --root, unreadable file, a path
                            # escaping --root. Also argparse's own hardcoded code.
EXIT_ABORT = 3              # the step-7d abort set is non-empty. This tool's primary verdict.
EXIT_SCAN_INTEGRITY = 6     # at least one file in scope ends with an OPEN fence.
EXIT_INTERNAL = 7           # uncaught exception. Deliberately NOT 1.


# --------------------------------------------------------------------------- the taxonomy

KINDS = ("rules", "spec", "plan", "record", "unresolved")
LIFETIMES = ("permanent", "conditional", "expiring")

# Shorter first. T-2 (ruling R-C) resolves a disagreement DOWNWARD, to the smallest index.
LIFETIME_ORDER = {"expiring": 0, "conditional": 1, "permanent": 2}

# spec S6 r1: the expiry schema is a bounded save count. Default 3, MAX 10, no unbounded option.
EXPIRY_DEFAULT_SAVES = 3
EXPIRY_MAX_SAVES = 10


# --------------------------------------------------------------------------- the file -> kind table
# spec S3.1 + S3.2. THE authority for rule A1. A file absent from this table is OUT OF SCOPE:
# it is reported as skipped and never guessed at. Guessing a file's kind would make A1 -- the
# type rule, the whole point of step 7d -- an opinion.

FILE_KIND = {
    "AGENTS.md":                            ("rules", "permanent"),
    "CLAUDE.md":                            ("rules", "permanent"),
    "architecture.md":                      ("spec",  "conditional"),
    "design.md":                            ("spec",  "conditional"),
    "CONSTRAINTS.md":                       ("rules", "permanent"),
    "backlog.md":                              ("plan",  "conditional"),
    "open_questions.md":                    ("plan",  "conditional"),
    "active.md":                            ("rules", "expiring"),
    "state.md":                             ("plan",  "conditional"),
    "manifest.md":                          ("rules", "permanent"),
    "_archive/backlog-deferred.md":            ("plan",  None),
    "_archive/open-questions-deferred.md":  ("plan",  None),
}

# Codex owns AGENTS.md's syntax and discovery. save counts and restores the file, but never
# validates its declarations, reference graph, fence shape or identifiers. This is stronger than
# DECLARATION_EXEMPT: the file is passive input, not an entry corpus.
PASSIVE_FILES = frozenset(("AGENTS.md",))

# Regenerated from bounded inputs on every save (spec S3.5). Nothing AUTHORS their entries, so
# there is no author to hold to A2/A3. The exemption is from REQUIRING a declaration -- an
# unrecognised value that IS present is still A2/A3, because a generator emitting a value
# outside the vocabulary is a generator bug and silence would hide it.
# C-57 (the maintainer, 2026-08-18): a file may legitimately permit MORE THAN ONE lifetime, so the warning
# compares against a SET rather than FILE_KIND's single value. `CONSTRAINTS.md` is the case he
# ruled: an instruction may be permanent, or temporary as `conditional - until <event>` retired by
# hand. Before this table the shipped validator warned against C-39 and C-50 BY NAME -- against his
# own constraints. A file absent here permits exactly its FILE_KIND lifetime.
LIFETIME_ALLOWED = {
    "CONSTRAINTS.md": ("permanent", "conditional"),
}

# EXEMPT FROM *REQUIRING* A DECLARATION, for two OPPOSITE reasons -- which is why this constant
# is no longer named for either. `state.md` and `manifest.md` are regenerated from bounded inputs
# every save and nothing AUTHORS their entries, so there is no author to hold to A2/A3.
# `AGENTS.md` and `CLAUDE.md` are the mirror image: ONLY a human authors them and NO save save step writes them in any
# mode (C-59), so an abort on its SHAPE is one the tool can never clear. MEASURED 2026-08-30: an
# ordinary prose charter with three `##` headings returned exit 3, 6 aborts, save blocked, and
# the only remedy open to the operator was to stop writing prose in the one file that is theirs.
# C-53, verbatim: "Nothing should ever block a save. That will render the system unusable."
# THE EXEMPTION IS FROM REQUIRING A DECLARATION, NEVER FROM THE VOCABULARY: a value that IS
# present and outside it is still A2/A3, because that is a typo in a rule the operator meant to
# write. CLAUDE.md remains inside that historical validator behaviour. AGENTS.md is instead
# listed in PASSIVE_FILES and bypasses the validator altogether.
DECLARATION_EXEMPT = frozenset(("state.md", "manifest.md", "AGENTS.md", "CLAUDE.md"))

# spec S7.1: every entry in a deferred archive carries a promotion gate, or it is unreachable
# content that nothing will ever bring back.
GATE_REQUIRED = frozenset(("_archive/backlog-deferred.md",
                           "_archive/open-questions-deferred.md"))

# The one file the UNRESOLVED carve-out and rules A5/A6 apply to (spec S2.4 T-3, S6).
ACTIVE_FILE = "active.md"

# Where an already-expired active entry lands (spec S2.4 T-3). A6's renewal detector reads it.
EXPIRED_FILE = "_archive/active-expired.md"

# ---- A13: the two UNBOUNDED prose fields in the generated cursor.
#
# `state.md` is a GENERATED file, so it is exempt from needing entry declarations and no drain row
# reads it -- and the combination left it outside the scope of EVERY content check. A9/A10/A11 read
# the two plan files; A12 adds the two spec files; nothing has ever looked at `state.md`.
#
# `goal` and `active_track` are block-scalar PROSE that `update_state.py` carries forward
# BYTE-FOR-BYTE every save by design, so neither ever shrinks unless a person rewrites it. MEASURED
# 2026-08-17 across all 8 v4 folders on this machine, enumerated with
# `grep -l "schema_version: v4"` rather than typed: active_track = 213 / 1,713 / 1,898 / 1,995 /
# 3,164 / 3,570 / 8,254 / 81,757 B, and goal = 162 .. 592 B. On the worst folder `active_track` is
# 81,757 of an 82,986 B `state.md` -- 98.5% of the file, ~20% of that project's whole restore cost,
# in the one file everybody treats as a small generated cursor. Classified per segment, 0.09% of it
# was a statement of current work; 42% was standing rules that belong in `CLAUDE.md`.
#
# CEILING IN BYTES, NOT TOKENS, AND THAT IS C-54 RATHER THAN CONVENIENCE. design.md S2 specifies a
# 1,000-TOKEN split trigger for this file, and C-54 defers the token counter AND forbids gating
# anything on it -- so the specified trigger cannot be evaluated by anything that exists. C-54's own
# second half requires reduction to key on signals that need no measurement. A byte count is one.
#
# 2,000 B is THE MAINTAINER'S RULING (2026-08-17), tightening the 4,000 B this was proposed at: "if goal is
# 592B max with a budget of 4000B, then we donot need 4000B budget. drop the budget to max 2000B
# and apply the fix to goal also." At 2,000 B it fires on 4 of the 8 folders and on no `goal`.
STATE_FILE = "state.md"
STATE_PROSE_FIELDS = ("goal", "active_track")
STATE_FIELD_CEILING_BYTES = 2000

# A top-level field whose value is a YAML block-scalar indicator, at column 0. `update_state.py`
# writes these at column 0 by contract (R-K), so no indent is allowed here: an indented `goal: |`
# is a CHILD of `files:`/`counts:` or prose, not the cursor field.
STATE_BLOCK_FIELD_RE = re.compile(
    r"^(?P<key>[a-z_][a-z0-9_]*)\s*:\s*[|>][0-9]*[-+]?\s*$")

# Default discovery scope: the living-doc set sits at the project root plus `_archive/`.
# Deliberately NOT `**/*.md` -- `docs/` holds plans, wip and artifacts that are not entries.
DEFAULT_PATTERNS = ("*.md", "_archive/*.md")

# --------------------------------------------------------------------------- entry parsing

# A declaration field. Column 0 through 3 (CommonMark's non-code indent), a bare key, a colon.
# `[A-Za-z]` first so a `**bold**` line, a list bullet and a URL cannot open one.
FIELD_RE = re.compile(r"^ {0,3}([A-Za-z][A-Za-z0-9_.-]*)[ \t]*:[ \t]*(.*)$")

# The closed set of keys this tool interprets. Anything else on a `key: value` line is prose and
# is ignored -- `Note:` and `Decision:` are common sentence openers and must not become fields.
# `impl` is deliberately ABSENT: spec S2.5 parks the whole rule at Phase 1, so it matches
# nothing and is not implemented at all (D67).
FIELD_KEYS = frozenset((
    "kind", "lifetime", "expires", "expiry", "gate", "candidates", "candidate",
    "quote", "save", "said-in", "asserted", "implication", "load", "hot", "cold",
    "split-of", "split",
))

# A kind:/lifetime: value may name more than one token, and S2.2 REQUIRES a `conditional` to
# name its event on the line ("If you can name it -> conditional, and you must write the name
# down") -- so a qualifier is mandated content, not sloppiness, and must not be read as a second
# token. These are the separators real authors use between tokens AND between a token and its
# qualifier: list punctuation, brackets, an em/en/hyphen dash surrounded by spaces, `=`, and the
# conjunctions that introduce a named event.
VALUE_SPLIT_RE = re.compile(
    r"[,/;|()\[\]]"
    r"|\s+-{1,2}\s+|\s*[–—]\s*|\s*=\s*"
    r"|\s+(?:\+|and|vs\.?|or|when|until|unless|if|while|because|once|after|pending|for)\s+",
    re.I)
KIND_SPLIT_RE = VALUE_SPLIT_RE          # kept: A4 and the selftest reference this name.

# Anything that reads as a save number: "save 41", "save#41", "s41", "#41", "41".
SAVE_NUM_RE = re.compile(r"(?:save\s*#?\s*|s\s*#|#)?(\d{1,6})", re.I)


class Entry(object):
    """One heading and everything under it, with its declaration fields already extracted.

    `fields` maps a closed-set key to a list of (lineno, value). A list, not a scalar, because
    A8 and T-2 exist precisely to report the case where a key appears twice with two answers --
    collapsing to last-wins here would delete the finding before any rule could see it.
    """

    __slots__ = ("rel", "ident", "title", "level", "line", "end_line", "fields", "body",
                 "in_unresolved")

    def __init__(self, rel, ident, title, level, line):
        self.rel = rel
        self.ident = ident
        self.title = title
        self.level = level
        self.line = line
        self.end_line = line
        self.fields = {}
        self.body = []          # [(lineno, text)] -- non-fenced body lines, for S6's heading form
        self.in_unresolved = False

    def values(self, key):
        return [v for (_ln, v) in self.fields.get(key, ())]

    def first(self, key):
        """The VALUE of the first occurrence, not the (lineno, value) pair. Use where() for the
        line. Returning the pair here read as a string everywhere it was consumed and surfaced
        as `AttributeError: 'tuple' object has no attribute 'strip'` at exit 7 (VERIFIED
        2026-08-03 on the A5/A6 fixture) -- the v3 corpus declares no `expires:` or `quote:` at
        all, so no in-repo file reaches this path."""
        vals = self.fields.get(key)
        return vals[0][1] if vals else None

    def where(self, key):
        """file:line of a field, falling back to the heading when the field is absent."""
        vals = self.fields.get(key)
        return vals[0][0] if vals else self.line

    def __repr__(self):                                         # pragma: no cover - debug only
        return "<Entry %s %s:%d>" % (self.ident, self.rel, self.line)


def _strip_emphasis(text):
    """Drop the wrapping `**` / `__` / backticks a heading may carry. Content only."""
    s = text.strip()
    for _ in range(3):
        for mark in ("**", "__", "*", "_", "`"):
            if len(s) > 2 * len(mark) and s.startswith(mark) and s.endswith(mark):
                s = s[len(mark):-len(mark)].strip()
                break
        else:
            break
    return s


def heading_id(rest):
    """The id out of `## <id> - <title>`. A label for the operator, not a resolvable reference.

    The house form separates id from title with U+00B7. Falling back to the first token keeps a
    heading that does not use the form addressable rather than anonymous: a violation reported
    against "(unnamed)" is a violation the operator cannot find, which is the same defect as
    reporting a bare count.
    """
    s = _strip_emphasis(rest)
    if not s:
        return "(untitled)"
    for sep in ("·", " -- ", " — ", " | "):
        if sep in s:
            head = s.split(sep, 1)[0].strip()
            if head:
                return _strip_emphasis(head)[:60]
    tok = s.split()[0].strip().rstrip(".:;,")
    return (tok or s)[:60]


def parse_entries(rel, rows):
    """rows -> ([Entry], [section], [shape_warning]), in document order.

    AN ENTRY IS EVERY ATX HEADING AT LEVEL >= 2, and its body runs to the line before the NEXT
    heading of ANY level -- innermost attachment. Level 1 is the document title, never an entry.

    WHY NOT "the shallowest level the file uses", which is what this did until 2026-08-03.
    Deriving a single entry level and treating deeper headings as body means a file shaped

        ## BACKLOG                 <- a section banner, declares nothing
        ### R-OOB-1 - ...          <- the real entries
        ### R-DOC-1 - ...

    parses to ONE entry. `attach_fields` then hoovered the whole subtree, so ONE `kind:` /
    `gate:` / `expires:` / `quote:` anywhere under the banner satisfied the rule for every
    sub-entry. Measured, three ways, all at exit 0 with a printed "ABORT SET (0)": four deferred
    entries with no `gate:` passed A7; three `active.md` entries with no `expires:` and no
    `quote:` passed A5 and A6; 22 undeclared entries in a sectioned `open_questions.md` took the
    file from 44 aborts to 0. Adding a `## Resolved` / `## Still open` banner is an ordinary
    editorial act that changes no entry content, and it turned the tool's entire measured output
    off. This project's own `roadmap.md` (renamed `backlog.md` by S3.4) is already that shape.

    A SECTION BANNER IS NOT AN ENTRY. A heading that (a) has at least one child heading and
    (b) declares no field of its own in its DIRECT body is a section: it is reported, and it is
    not checked for A2/A3/A7. Aborting `## BACKLOG` for not declaring a kind is noise, and the
    findings belong on the entries underneath it, which is now where they land. A heading that
    DOES declare fields is an entry even when it has children -- otherwise `kind:` on a banner
    would buy silence for everything below it, which is the bug above wearing a hat.

    KNOWN COST, accepted deliberately. A genuine entry that uses `###` sub-headings for internal
    structure now has those sub-headings checked as entries, so they abort A2/A3 until they
    declare or the author uses bold instead. That is the fail-CLOSED direction: a false abort is
    named, on a line, and costs one line to fix; a false pass is invisible and this file's whole
    INVARIANT is about not shipping one. There is no mechanical way to tell that shape apart from
    the banner shape -- they are byte-identical in structure -- so the tie goes to scrutiny.

    Headings inside a fence are not headings; fence state comes from `recall.load_doc`, never
    from a second state machine here.
    """
    heads = []
    for (lineno, text, _s, _e, in_fence) in rows:
        if in_fence:
            continue
        m = citemap.HEADING_RE.match(text)
        if m:
            heads.append((lineno, len(m.group(1)), m.group(3)))

    last_line = rows[-1][0] if rows else 0
    field_lines = set()
    for (lineno, text, _s, _e, in_fence) in rows:
        if in_fence:
            continue
        m = FIELD_RE.match(text)
        if m and m.group(1).lower() in FIELD_KEYS:
            field_lines.add(lineno)

    entries = []
    sections = []
    for idx, (lineno, level, rest) in enumerate(heads):
        if level < 2:
            continue
        nxt = heads[idx + 1] if idx + 1 < len(heads) else None
        body_end = (nxt[0] - 1) if nxt else last_line
        has_child = bool(nxt) and nxt[1] > level
        declares = any(ln in field_lines for ln in range(lineno + 1, body_end + 1))
        if has_child and not declares:
            sections.append((lineno, heading_id(rest)))
            continue
        ent = Entry(rel, heading_id(rest), _strip_emphasis(rest), level, lineno)
        ent.end_line = max(body_end, lineno)
        entries.append(ent)
    return entries, sections, detect_unseen_headings(rows, heads)


# CommonMark shapes that are headings to a human reader and to a renderer, but not to
# `citemap.HEADING_RE` (which is ATX at column 0, deliberately -- it is the shared matcher and a
# second one here would be the drift this file forbids). They are DETECTED so the operator is
# told, and never parsed: an entry the tool cannot see is the "clean PASS over content nobody
# checked" shape, and silence about it is the part that makes it dangerous.
SETEXT_RE = re.compile(r"^ {0,3}(=+|-{2,})\s*$")
INDENTED_ATX_RE = re.compile(r"^ {1,3}(#{1,6})[ \t]+\S")


def detect_unseen_headings(rows, heads):
    """[(lineno, why)] for heading-shaped lines the shared ATX matcher does not accept."""
    out = []
    prev = None
    known = set(ln for (ln, _lv, _r) in heads)
    for (lineno, text, _s, _e, in_fence) in rows:
        if in_fence:
            prev = None
            continue
        if lineno not in known and INDENTED_ATX_RE.match(text):
            out.append((lineno, "an ATX heading indented %d space(s). Headings are recognised at "
                                "column 0 only; this line was read as body and any entry it was "
                                "meant to open does not exist."
                                % (len(text) - len(text.lstrip(" ")))))
        elif SETEXT_RE.match(text) and prev and prev.strip() and not prev.startswith("#"):
            out.append((lineno, "a SETEXT heading (`%s` underlined at line %d). Only ATX `##` "
                                "headings open an entry; this section was read as body."
                                % (prev.strip()[:40], lineno - 1)))
        prev = text
    return out


def attach_fields(entries, rows):
    """Fill every entry's `fields` and `body` from the lines it owns. Fenced lines are neither.

    The whole entry body is scanned, not just the run of lines directly under the heading. Two
    reasons, both load-bearing: A8 has to SEE a second `kind:` wherever the author put it, and a
    declaration that drifted six lines down is still a declaration to every human reader. The
    cost is that a prose line opening `kind:` at column 0 outside a fence counts -- which the A8
    message makes visible by naming both lines, so the author can see exactly what was read.

    "The whole body" now ENDS AT THE NEXT HEADING OF ANY LEVEL (see `parse_entries`), so a field
    under a sub-heading belongs to the sub-heading. Absorption across a heading boundary is what
    let one `gate:` satisfy four deferred entries.
    """
    if not entries:
        return
    by_line = {}
    for ent in entries:
        for ln in range(ent.line + 1, ent.end_line + 1):
            by_line[ln] = ent
    for (lineno, text, _s, _e, in_fence) in rows:
        if in_fence:
            continue
        ent = by_line.get(lineno)
        if ent is None:
            continue
        if text.strip():
            ent.body.append((lineno, text))
        m = FIELD_RE.match(text)
        if not m:
            continue
        key = m.group(1).lower()
        if key not in FIELD_KEYS:
            continue
        ent.fields.setdefault(key, []).append((lineno, m.group(2).strip()))


def _vocab_tokens(raw, vocab):
    """ONE tokeniser for every `kind:` / `lifetime:` / `candidates:` value in this file.

    Returns (recognised, unrecognised) -- recognised is [token] in order of appearance with
    duplicates collapsed; unrecognised is [] when at least one token WAS recognised.

    Two decisions, both from measured false positives (2026-08-03):

    A QUALIFIER IS NOT A SECOND TOKEN. S2.2 mandates that a `conditional` name its event on the
    line, so `lifetime: conditional -- until channel B opens` is the SPEC-COMPLIANT form and it
    aborted A3, with a message that ended by telling the author to "name the event" they had just
    named. Same for `kind: spec (S2.1 question 2)`. So: if any piece of the value resolves to a
    vocabulary token, the pieces that do not are the qualifier and are IGNORED. Only a value with
    NO recognised token anywhere is reported, and then the WHOLE raw value is named rather than
    the fragment -- `kind: unresolved (spec or rules)` used to be reported as the unrecognised
    kind `'unresolved (spec'`, a string the author never wrote.

    ONE TOKENISER, NOT TWO. A4 used to re-split the same line with its own copy of this logic and
    the two disagreed: A4 read `spec` and `rules` out of `unresolved (spec or rules)` while A2
    reported a mangled fragment from the same characters. Two parsers over one line is the same
    class of defect as two fence state machines -- they drift, and the drift is invisible.
    """
    recognised = []
    seen = set()
    leftovers = []
    for piece in VALUE_SPLIT_RE.split(raw or ""):
        tok = _strip_emphasis(piece or "").strip().lower().strip("`*_ ().\"'")
        if not tok:
            continue
        if tok in vocab:
            if tok not in seen:
                seen.add(tok)
                recognised.append(tok)
        else:
            leftovers.append(tok)
    if recognised:
        return recognised, []
    whole = _strip_emphasis(raw or "").strip().strip("`").rstrip(".")
    return [], ([whole] if whole else leftovers)


def declared_kinds(entry):
    """Every DISTINCT kind token the entry declares, with the line each came from.

    Returns [(kind_token, lineno)], order of appearance, duplicates of the same token collapsed.
    An unrecognised value is returned as-is so A2 can name it; A8 counts only recognised ones,
    because "spec, and probably rules" must not read as two kinds on the strength of a typo.
    """
    out = []
    seen = set()
    for (lineno, raw) in entry.fields.get("kind", ()):
        toks, bad = _vocab_tokens(raw, KINDS)
        for tok in toks + bad:
            if tok in seen:
                continue
            seen.add(tok)
            out.append((tok, lineno))
    return out


def declared_lifetimes(entry):
    """Same shape as declared_kinds, for `lifetime:`. T-2 (R-C) consumes this."""
    out = []
    seen = set()
    for (lineno, raw) in entry.fields.get("lifetime", ()):
        toks, bad = _vocab_tokens(raw, LIFETIMES)
        for tok in toks + bad:
            if tok in seen:
                continue
            seen.add(tok)
            out.append((tok, lineno))
    return out


# --------------------------------------------------------------------------- UNRESOLVED region

UNRESOLVED_OPEN_RE = re.compile(r"^\s*<!--\s*UNRESOLVED\s*-->\s*$", re.I)
UNRESOLVED_CLOSE_RE = re.compile(r"^\s*<!--\s*(?:/\s*UNRESOLVED|END\s+UNRESOLVED)\s*-->\s*$", re.I)


def find_unresolved_regions(rows):
    """[(open_line, close_line)] for every CLOSED `<!-- UNRESOLVED -->` region, plus warnings.

    Returns (regions, warnings). Markers inside a fence are not markers.

    TERMINATION RULE -- decided here, and it is the whole point of this function.
    A region requires an EXPLICIT closing marker: `<!-- /UNRESOLVED -->` or
    `<!-- END UNRESOLVED -->`. An open marker with no closer produces a region of ZERO WIDTH --
    it exempts nothing -- and a warning naming the line it opened on.

    Why not EOF. The region is the ONE exemption from S5.2's one-kind rule and from S6 rules
    3-4. An EOF-terminated region means a single stray marker near the top of `active.md`
    silently exempts every entry below it from the type rule, and the file still reports clean.
    That is the unterminated-fence bug wearing a different hat: a delimiter that fails OPEN and
    swallows the rest of the document. This project shipped that bug once already (see the
    module INVARIANT on exit 6), which is why the carve-out fails CLOSED instead.

    Failing closed is also the only direction that is safe to get wrong. An over-narrow region
    subjects entries to A1/A8 that were meant to be exempt: the operator sees named violations
    plus a warning that says exactly which line to close, and adds one line. An over-wide region
    suppresses findings and says nothing at all.
    """
    regions = []
    warnings = []
    open_line = None
    for (lineno, text, _s, _e, in_fence) in rows:
        if in_fence:
            continue
        if UNRESOLVED_OPEN_RE.match(text):
            if open_line is not None:
                warnings.append(
                    (lineno, "a second `<!-- UNRESOLVED -->` opens at line %d while the region "
                             "opened at line %d is still open; regions do not nest"
                             % (lineno, open_line)))
                continue
            open_line = lineno
            continue
        if UNRESOLVED_CLOSE_RE.match(text):
            if open_line is None:
                warnings.append(
                    (lineno, "`<!-- /UNRESOLVED -->` at line %d closes nothing (no open marker "
                             "above it)" % lineno))
                continue
            regions.append((open_line, lineno))
            open_line = None
    if open_line is not None:
        warnings.append(
            (open_line,
             "`<!-- UNRESOLVED -->` opened at line %d and is never closed. The region exempts "
             "NOTHING and every entry below it was checked in full. Close it with "
             "`<!-- /UNRESOLVED -->`; an EOF-terminated region would silently exempt the rest "
             "of the file from the type rule." % open_line))
    return regions, warnings


def mark_unresolved(entries, regions):
    """Set `in_unresolved` on entries the carve-out actually covers. Returns [(entry, warning)].

    TWO conditions, both required: the entry's HEADING line falls inside a CLOSED region, AND the
    entry declares `kind: unresolved`.

    The heading, not the span: an entry that starts inside the region and runs past the closing
    marker is one entry and it is in the region. The alternative -- membership by overlap --
    would let a single trailing entry drag the exemption past the closer, which is the same
    fail-open the termination rule above exists to stop.

    The kind test is the half that was missing, and it made the region a blanket amnesty rather
    than a deadline. Measured 2026-08-03: an entry declaring `kind: record` + `lifetime:
    permanent` + `expires: save 999999`, with no `quote:`, placed between the two markers,
    produced ABORT SET (0), ZERO warnings and exit 0 -- A1, A6 and A8 all short-circuit on
    `in_unresolved` and A5 was defeated separately (see `rule_a5_expiry`). Deleting the two
    marker lines from the same file gave A1 + A6 and exit 3. Region membership cost nothing and
    bought exemption from three rules.

    S2.4 T-3 scopes the carve-out to entries that ARE unresolved: "An entry the author cannot
    split writes `kind: unresolved` ... and lands in a delimited `<!-- UNRESOLVED -->` region."
    `rule_a1_type` already reasons about the inverse direction (`unresolved` OUTSIDE the region
    is misfiled); this is the same argument run the other way, and the asymmetry was the bug.
    """
    if not regions:
        return []
    notes = []
    for ent in entries:
        inside = any(lo < ent.line < hi for (lo, hi) in regions)
        if not inside:
            continue
        if "unresolved" in [tok for (tok, _ln) in declared_kinds(ent)]:
            ent.in_unresolved = True
        else:
            notes.append((ent, "sits inside the `<!-- UNRESOLVED -->` region but does not "
                               "declare `kind: unresolved`, so the carve-out does NOT cover it "
                               "and it was checked in full (S2.4 T-3). The region is a deadline "
                               "for entries that cannot be split, not a quiet area."))
    return notes


# --------------------------------------------------------------------------- violations

class Violation(object):
    """One abort-set member. rule + id + file:line + message. All four, always."""

    __slots__ = ("rule", "ident", "rel", "line", "message")

    def __init__(self, rule, entry, line, message):
        self.rule = rule
        self.ident = entry.ident
        self.rel = entry.rel
        self.line = line
        self.message = message

    def render(self):
        return "%s  %s:%d  [%s]  %s" % (self.rule, self.rel, self.line, self.ident, self.message)


class Warning_(object):
    """A finding that is explicitly NOT an abort. Exit stays 0 on warnings alone."""

    __slots__ = ("kind", "ident", "rel", "line", "message")

    def __init__(self, kind, ident, rel, line, message):
        self.kind = kind
        self.ident = ident
        self.rel = rel
        self.line = line
        self.message = message

    def render(self):
        who = ("[%s]  " % self.ident) if self.ident else ""
        return "%s  %s:%d  %s%s" % (self.kind, self.rel, self.line, who, self.message)


# --------------------------------------------------------------------------- A1 .. A8

def rule_a1_type(entry, file_kind, exempt=False, rel=None):
    """A1 TYPE VIOLATION (spec S5.2): the entry's declared kind is not the file's declared kind.

    Exempt inside the UNRESOLVED region -- that region is the ONE carve-out from S5.2 (S2.4 T-3).
    `kind: unresolved` OUTSIDE the region therefore lands here, and correctly: T-3 says an
    unresolved entry lives IN the region, so one that does not is misfiled, not untyped.

    Exempt in a GENERATED file for the same reason A2/A3 are (S3.5): `state.md` and `manifest.md`
    are rebuilt from bounded inputs every save, so an A1 there names a defect in the generator
    and prints it at an author who cannot act on it. A manifest that indexes the living docs and
    names each one's kind -- the most natural thing a manifest holds -- aborted the save.
    """
    if entry.in_unresolved or exempt or file_kind is None:
        return []
    out = []
    for (tok, lineno) in declared_kinds(entry):
        if tok not in KINDS:
            continue                      # A2 owns an unrecognised value; do not report twice.
        if tok == file_kind:
            continue
        if tok == "unresolved":
            out.append(Violation(
                "A1", entry, lineno,
                "declares `kind: unresolved` but sits OUTSIDE the `<!-- UNRESOLVED -->` region. "
                "T-3 puts unresolved entries in that region with a 3-save expiry; here it is "
                "just an entry whose kind is not this file's kind (%s)." % file_kind))
        elif tok == "record":
            out.append(Violation(
                "A1", entry, lineno,
                "TYPE VIOLATION: declares `kind: record` in a file whose declared kind is `%s`. "
                "`record` is COLD always, with no exception (S2.3), and every cold file is out "
                "of this tool's scope -- so there is no hot file to move it to. Either it is "
                "not a record (re-type it), or it belongs in the cold archive, not here."
                % file_kind))
        else:
            out.append(Violation(
                "A1", entry, lineno,
                "TYPE VIOLATION: declares `kind: %s` in a file whose declared kind is `%s`. "
                "Move the entry to the file that holds `%s`, or re-type it."
                % (tok, file_kind, tok)))

    # A1's LIFETIME half (C-57). NOT a new abort -- the set stays CLOSED at A1..A8. A1 already
    # owned "this entry's declaration disagrees with its file's" and compared only half of it.
    #
    # ONE VALUE, ONE DIRECTION, and every word of that scope is load-bearing:
    #  * `expiring` means "dies at save number N", and step 6a row 1 reads active.md and NO other
    #    file. An expiry written elsewhere is honoured by nothing: the entry LOOKS temporary and IS
    #    permanent. Measured save#126 -- the real #57 instruction planted in CONSTRAINTS.md warned
    #    and the save completed clean.
    #  * Every OTHER lifetime disagreement stays a WARNING. `permanent` inside a conditional file is
    #    14 live entries across dev/example-project and dev/subagents; aborting those would refuse two
    #    peers' saves over a defect nobody ruled on.
    #  * MORE THAN ONE declared lifetime is EXEMPT, because T-2 (ruling R-C) resolves a
    #    disagreement DOWNWARD to the shortest value -- which IS `expiring` -- and its own fixture
    #    puts that pair in architecture.md and requires exit 0. Aborting there would assert a
    #    property this design deliberately produces. THE STATED COST: an author can dodge this
    #    abort by declaring two lifetimes. That is not silent -- every such entry is named, with
    #    both declarations and their line numbers, in the T-2 LIFETIME RESOLUTIONS block.
    #
    # Measured before shipping: fires on 0 entries across all 8 v4 folders on this machine.
    if rel is not None and rel != ACTIVE_FILE and not exempt:
        _fk, _fl = FILE_KIND.get(rel, (None, None))
        lifes = declared_lifetimes(entry)
        if _fl is not None and len(lifes) == 1:
            (tok, lineno) = lifes[0]
            if tok == "expiring":
                out.append(Violation(
                    "A1", entry, lineno,
                    "TEMPORAL VIOLATION: declares `lifetime: expiring` in %s. An expiry is a SAVE "
                    "NUMBER, and step 6a row 1 reads active.md and no other file -- so an expiry "
                    "written here is honoured by NOTHING: the entry looks temporary and is "
                    "permanent (C-57). Move it to active.md, or re-declare it here as "
                    "`lifetime: conditional - until <event>` and retire it by hand." % rel))
    return out


def rule_a2_kind(entry, exempt):
    """A2 (spec S2.4): no declared kind, or a kind value outside the vocabulary."""
    kinds = declared_kinds(entry)
    if not kinds:
        if exempt:
            return []
        return [Violation("A2", entry, entry.line,
                          "no `kind:` declared. A save FAILS on an entry with no declared kind "
                          "(S2.4). One of: %s." % ", ".join(KINDS))]
    out = []
    for (tok, lineno) in kinds:
        if tok not in KINDS:
            out.append(Violation("A2", entry, lineno,
                                 "unrecognised kind %r. One of: %s." % (tok, ", ".join(KINDS))))
    return out


def rule_a3_lifetime(entry, exempt):
    """A3 (spec S2.4): no declared lifetime, or a lifetime value outside the vocabulary.

    `for now` is not a lifetime (S2.2) -- it arrives here as an unrecognised value and is named
    as one. Naming the event is `conditional`; refusing to is `permanent`.
    """
    lifes = declared_lifetimes(entry)
    if not lifes:
        if exempt:
            return []
        return [Violation("A3", entry, entry.line,
                          "no `lifetime:` declared. A save FAILS on an entry with no declared "
                          "lifetime (S2.4). One of: %s." % ", ".join(LIFETIMES))]
    out = []
    for (tok, lineno) in lifes:
        if tok not in LIFETIMES:
            out.append(Violation("A3", entry, lineno,
                                 "unrecognised lifetime %r. One of: %s. (`for now` is not a "
                                 "lifetime -- name the event, or take `permanent`.)"
                                 % (tok, ", ".join(LIFETIMES))))
    return out


def rule_a4_unresolved(entry):
    """A4 (spec S2.4 T-3): `kind: unresolved` needs TWO candidate kinds named AND an expiry.

    Applies INSIDE the UNRESOLVED region as well. The region is a DEADLINE, not a hiding place:
    an unresolved entry with one candidate is a decision the author already made and did not
    write down, and one with no expiry never comes back up.
    """
    kinds = [tok for (tok, _ln) in declared_kinds(entry)]
    if "unresolved" not in kinds:
        return []
    out = []
    named = set()
    for (lineno, raw) in list(entry.fields.get("candidates", ())) \
            + list(entry.fields.get("candidate", ())) \
            + list(entry.fields.get("kind", ())):
        toks, _bad = _vocab_tokens(raw, KINDS)      # the SAME tokeniser A2 uses (see _vocab_tokens)
        for tok in toks:
            if tok != "unresolved":
                named.add(tok)
    if len(named) < 2:
        out.append(Violation(
            "A4", entry, entry.where("kind"),
            "`kind: unresolved` names %d candidate kind%s (%s); T-3 requires TWO. Write "
            "`candidates: <kind>, <kind>`."
            % (len(named), "" if len(named) == 1 else "s",
               ", ".join(sorted(named)) if named else "none")))
    if not _expiry_value(entry):
        out.append(Violation(
            "A4", entry, entry.where("kind"),
            "`kind: unresolved` with no `expires:`. `unresolved` is a DEADLINE, not a fifth "
            "kind -- default %d saves, and at expiry the entry is split or demoted, never "
            "renewed." % EXPIRY_DEFAULT_SAVES))
    return out


# spec S6's ONLY worked active.md entry (spec.md:476) carries its expiry, its asserted save# and
# its verbatim quote in the HEADING and in a bare quoted body line, not in fields:
#
#   ## A-7 - asserted save#146 - expires save#149 - until the example-project deploy lands
#   "defer save deep while we're deep in building"
#
# R-A ruled the field form for `kind` and `lifetime` ONLY. It said nothing about relocating the
# other three, and the field names `expires:` / `quote:` / `save:` appear NOWHERE in spec.md
# (verified by grep, 2026-08-03). Reading fields only made the spec's own example abort A5 + A6 --
# every active.md written to the published schema would have failed this validator, and the
# messages told the author to add a field that, to their eye, was already there. Both forms are
# accepted; the field form wins when both are present, because it is the one R-A ruled on.
HEAD_EXPIRES_RE = re.compile(r"\bexpir(?:es|y|ing)\b[^0-9\n]{0,12}?(save\s*#?\s*\d{1,6}|\d{1,6})",
                             re.I)
HEAD_ASSERTED_RE = re.compile(r"\b(?:asserted|said(?:\s+in)?|written)\b[^0-9\n]{0,12}?"
                              r"save\s*#?\s*(\d{1,6})", re.I)
BARE_QUOTE_RE = re.compile(r'^\s*[>*\-\s]*["“](.{3,})["”]\s*(\(.*\))?\s*$')


def _expiry_value(entry):
    """The entry's expiry text, from `expires:`/`expiry:`, else out of the heading (S6's form)."""
    for key in ("expires", "expiry"):
        v = entry.first(key)
        if v is not None:
            return v
    m = HEAD_EXPIRES_RE.search(entry.title or "")
    if m:
        return m.group(1)
    return None


def _expiry_line(entry):
    for key in ("expires", "expiry"):
        if key in entry.fields:
            return entry.fields[key][0][0]
    return entry.line


UNBOUNDED_RE = re.compile(
    r"^(never|none|no|n/?a|tbd|todo|unbounded|indefinite(ly)?|forever|open|-+|—|∞|\?+)$", re.I)
# "N saves", "+N", "in N saves", "after N saves" -- a RELATIVE count. A BARE integer is NOT here;
# see rule_a5_expiry for why it must read as an absolute save#.
RELATIVE_SAVES_RE = re.compile(
    r"^(?:in|after|within|\+)?\s*(\d{1,6})\s*saves?$|^\+\s*(\d{1,6})$", re.I)
# "three saves" is the same field as "3 saves" to every human reader, and the schema's whole
# range is 1..10 -- ten words. Measured 2026-08-03 on fixture `hd11_three`: `expires: three
# saves` fell through every branch to A5's catch-all and aborted as "not a bounded save count,
# so it is UNBOUNDED" -- a FALSE abort on a bounded, in-range, legal expiry. A false abort is
# cheap to see and expensive to trust: it teaches the operator to read past the abort set.
# Only 1..10 are spelled: past the maximum the catch-all's verdict (A5) is already correct.
WORD_NUMERALS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
WORD_SAVES_RE = re.compile(
    r"^(?:in|after|within)?\s*(%s)\s+saves?$" % "|".join(WORD_NUMERALS), re.I)
ABSOLUTE_SAVE_RE = re.compile(r"^(?:save\s*#?\s*|s\s*#\s*|#)?(\d{1,6})$", re.I)
TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")
# The qualifier delimiters a PLACEHOLDER may wear. Deliberately not VALUE_SPLIT_RE: that one
# splits on `/`, which cuts `n/a` in half and made `gate: n/a -- just deferred` pass.
PLACEHOLDER_TAIL_RE = re.compile(r"\s+-{1,2}\s+|\s*[–—]\s*|[(;,]")


def _clean_value(raw):
    """Strip emphasis, backticks and ONE trailing parenthetical from a field value."""
    val = _strip_emphasis(raw or "").strip().strip("`").strip()
    return TRAILING_PAREN_RE.sub("", val).strip()


def _is_placeholder(raw):
    """True for `none` / `n/a` / `TBD` / `-` and the same wearing a qualifier.

    A7 and A6 ground (a) are presence checks, and the natural way to write "there is no gate" is
    to write `gate: none`. Measured: `gate: none`, `gate: n/a - just deferred` and
    `quote: TBD (save 14)` all passed at exit 0. S7.1's requirement is the NAMED CONDITION that
    brings the entry back, and S6 r4's is the user's VERBATIM WORDS; a placeholder is neither.
    This is not a new abort -- it is A7/A6 recognising their own absent case, using the vocabulary
    A5 already carried for exactly this.
    """
    val = _clean_value(raw)
    head = PLACEHOLDER_TAIL_RE.split(val, 1)[0].strip() if val else ""
    return bool(UNBOUNDED_RE.match(val) or (head and UNBOUNDED_RE.match(head)))


# `gate:` values that are a full sentence and still name no condition. Measured 2026-08-03 on
# fixture `hl3_gate_prose`: `Gate: we never worked out what would bring this back.` passed A7 at
# exit 0 -- a value that says IN WORDS that no gate exists, admitted because it is neither empty
# nor one of `_is_placeholder`'s one-word forms. S7.1's requirement is "the named condition that
# brings it back"; a sentence whose content is "we do not have one" answers it with the same
# `none` that `_is_placeholder` already refuses, at greater length.
#
# THIS IS A DISCLAIMER DETECTOR, NOT A GATE CLASSIFIER, and the difference is the whole design.
# "Does this prose name a real condition?" cannot be decided mechanically, and A7 is an ABORT --
# a rule that fires on a guess is worse than one that misses (the same reasoning that keeps
# `_normalise_implication` exact rather than fuzzy). So this fires ONLY on prose that explicitly
# disclaims having a condition. Anything merely vague still passes HERE -- and that is a division
# of labour, not a gap. Gate-FORM strictness lives in the DRAIN (D81): the same "does this name a
# real condition?" test, moved to the tool where a wrong guess costs ONE ITEM LEFT VISIBLE in
# `backlog.md` instead of the whole save. Same rule, opposite failure direction -- and it is the
# failure direction, not the rule's wording, that decides which tool is allowed to enforce it.
GATE_DISCLAIMER_RE = re.compile(
    r"\b(?:"
    r"never\s+(?:worked\s+(?:it\s+)?out|figured\s+(?:it\s+)?out|decided|determined|"
    r"established|settled|agreed)"
    r"|(?:do(?:n'?t| not)|didn'?t|did\s+not)\s+know"
    r"|no\s+idea"
    r"|who\s+knows"
    r"|not\s+sure|unsure|unclear|undecided|undefined|not\s+(?:yet\s+)?(?:defined|decided)"
    r"|no\s+(?:real\s+|specific\s+|actual\s+|known\s+)?(?:gate|condition|trigger)\b"
    r"|nothing\s+(?:specific|in\s+particular)"
    r"|some\s?day|eventually|at\s+some\s+point"
    r"|when\s+we\s+(?:get\s+(?:to\s+it|round(?:ed)?\s+to\s+it|around\s+to\s+it)|feel\s+like\s+it)"
    r")\b", re.I)


def _gate_names_no_condition(raw):
    """True when a `gate:` value explicitly states that no condition is known. See above."""
    return bool(GATE_DISCLAIMER_RE.search(_strip_emphasis(raw or "")))


def _quote_field(entry):
    """(lineno, text) of the entry's provenance quote: the `quote:` field, else a bare quoted
    body line (S6's form). A placeholder value is NOT a quote."""
    for (lineno, raw) in entry.fields.get("quote", ()):
        if raw.strip() and not _is_placeholder(raw):
            return lineno, raw
    if entry.fields.get("quote"):
        return entry.fields["quote"][0][0], ""       # present but a placeholder -> absent
    for (lineno, text) in entry.body:
        m = BARE_QUOTE_RE.match(text)
        if m:
            return lineno, text.strip()
    return None, None


def _asserted_save(entry):
    """The save# the entry asserts it was written at, or None.

    Order: the declared field (`save:` / `said-in:` / `asserted:`), then the heading's
    `asserted save#N` (S6's form). Deliberately NOT scraped from the quote text -- see
    `rule_a6_provenance`.
    """
    for key in ("save", "said-in", "asserted"):
        v = entry.first(key)
        if v is None:
            continue
        m = SAVE_NUM_RE.search(v)
        if m:
            return int(m.group(1))
    m = HEAD_ASSERTED_RE.search(entry.title or "")
    if m:
        return int(m.group(1))
    return None


def _quote_save(entry):
    """The save# the provenance quote was said in, or None.

    PRECEDENCE, and it was backwards. A declared/heading save# outranks anything scraped out of
    the quote text, and within the quote text the LAST save-shaped token wins, not the first.
    The value is user-authored VERBATIM prose -- that is the entire point of S6 r4 -- and the
    provenance marker is conventionally appended at the end. Measured 2026-08-03: a genuine
    renewal quoted at the current save, `quote: "we lost 130 ids in save 12's run, so defer
    save deep" (said in save 14)`, aborted A6 with the message asserting the quote was "from
    save 12, not the current save 14" -- a false statement about the operator's own line, and
    adding `save: 14` to the entry did not rescue it because the scrape only ran when the regex
    found NOTHING.
    """
    base = _asserted_save(entry)
    if base is not None:
        return base
    _ln, raw = _quote_field(entry)
    if not raw:
        return None
    found = re.findall(r"save\s*#?\s*(\d{1,6})", raw, re.I)
    return int(found[-1]) if found else None


# The provenance MARKER on a quote: everything after the quote's own closing mark. S6 r4 makes
# provenance one unit -- "verbatim quote plus the save# it was said in" -- and by convention the
# save# is appended to the quote: `"..." (save 14)`, `"..." (said in save 14)`, `"..." said in
# save 14`. A save number INSIDE the quotation marks is the USER'S PROSE, not a claim about when
# they said it: `"we lost 130 ids in save 12's run, so defer save deep" (said in save 14)`
# was said in 14 and merely mentions 12. Splitting at the closing mark is what tells the two
# apart, and it is why `_quote_save`'s positional "last token anywhere wins" heuristic is not
# reused here.
QUOTE_TAIL_RE = re.compile(r'["”](?P<tail>[^"“”]*)$')
QUOTE_PROV_SAVE_RE = re.compile(r"save\s*#?\s*(\d{1,6})", re.I)


def _quote_borne_save(entry):
    """The save# THE QUOTE ITSELF carries, or None. A6's freshness evidence, and its ONLY one.

    DELIBERATELY BLIND to `save:` / `said-in:` / `asserted:` and to the heading's `asserted
    save#N` -- everything `_asserted_save` reads. That blindness is the rule.

    S6 r3 exists to stop the AGENT renewing an expired instruction on its own authority. Every
    one of those fields is written by that same agent in that same save, so reading freshness
    out of one makes r3 SELF-CERTIFYING: the agent asserts it did not do the thing, and the check
    that it did not do the thing is that assertion. Measured 2026-08-03, both halves at exit 0
    over a real renewal: an expired entry re-stated verbatim under a new id with its ORIGINAL
    save-1 quote passed because the new entry's heading said `asserted save#5` (fixture g), and
    passed again with `asserted: save 5` + `said-in: save 5` + `save: 5` added (fixture h).
    Neither fixture supplied one word of fresh user speech.

    The two facts are genuinely different and their disagreement IS the stale-renewal signature:
    `asserted save#5` says when the ENTRY was written, `(save 1)` says when the USER SPOKE. A
    renewal is fresh only if the second one is now.

    `_asserted_save` and `_quote_save` keep their existing readings -- A5's horizon base may
    legitimately use a declared field, because A5 asks how far out the expiry is, not who
    authorised it.
    """
    _ln, raw = _quote_field(entry)
    if not raw:
        return None
    m = QUOTE_TAIL_RE.search(raw)
    # Quoted -> only the marker after the closing mark counts. Unquoted (no closing mark at all,
    # e.g. `quote: ship the gate (save 3)`) -> the whole value is the marker's only possible
    # home, so scan it, last token wins.
    scope = m.group("tail") if m else raw
    found = QUOTE_PROV_SAVE_RE.findall(scope)
    return int(found[-1]) if found else None


def _horizon_base(entry, current_save):
    """(base_save, source) for A5's max-10 horizon, or (None, None).

    Three sources, in order: the entry's own asserted save# (field or heading), the save# its
    mandatory provenance quote carries, and the CURRENT save#. The third is what closed the hole:
    `rule_a5_expiry` used to `return []` when no `save:` field was present, so `expires: save 900`
    at current save 14 -- 886 saves out against a stated maximum of 10 -- passed at exit 0, and
    the SKIPPED warning it printed was a working recipe for unbounded hot content. Using the
    current save is strictly CONSERVATIVE: an entry is asserted at or before now, so
    `target - current <= target - asserted` and the check can only under-fire, never over-fire.

    THE BASE IS CLAMPED AT THE CURRENT SAVE, and that clamp is the whole reason the sentence
    above holds. "An entry is asserted at or before now" is an ASSUMPTION about a field the agent
    writes, not a fact about it -- and a base in the FUTURE inverts the conservatism into its
    opposite, because the horizon is `target - base` and a larger base makes any target look
    nearer. Measured 2026-08-03: `asserted save#99 · expires save#105` at `--save 5` read
    `105 - 99 = 6` and passed A5's max-10 bound, while the entry's real hot life was ONE HUNDRED
    saves from now. Hot life is measured from NOW (S6 r1: default 3 saves, max 10, the two
    constants at the top of this file, and no unbounded option), so a self-declared base may only
    ever SHORTEN the horizon, never stretch it past what the current save allows --
    `min(declared, current)`. See the PRINCIPLE block in `rule_a6_provenance`: a field the agent
    writes may narrow what it is accountable for, never widen it.
    """
    base = _asserted_save(entry)
    src = "the asserted save#"
    if base is None:
        base = _quote_save(entry)
        src = "the save# in its provenance quote"
    if base is None:
        if current_save is not None:
            return current_save, "the CURRENT save#"
        return None, None
    if current_save is not None and base > current_save:
        return current_save, ("the CURRENT save# (%s says %d, a save that HAS NOT HAPPENED, and "
                              "a declared base may not stretch the horizon past now)"
                              % (src, base))
    return base, src


def rule_a5_expiry(entry, current_save=None):
    """A5 (spec S6 r1): an `active.md` entry with no expiry, an unbounded one, or one > 10 saves.

    The schema has NO unbounded option: default 3 saves, max 10 (the two constants at the top of
    this file). Applies inside the UNRESOLVED region too.

    A BARE INTEGER IS AN ABSOLUTE SAVE#, not a relative count. This is forced by spec S5.1 row 1,
    the demote trigger this whole file exists to arm: `expires <= current_save`. The drain reads
    the field as an absolute save number, and S6's example writes `expires save#149`. Reading a
    bare integer as relative made the two steps disagree about one field: `expires: 3` at save 14
    validated CLEAN and the drain demoted it on that same save, while `expires: 17` -- the correct
    absolute form, exactly the default-3 horizon -- aborted as "17 saves, the maximum is 10".
    A validator and the drain it validates for cannot read one field two ways.

    An expiry that names an EVENT rather than a save is reported as unbounded. That is not
    pedantry: `active.md`'s declared lifetime is `expiring`, and `expiring` means "stops being
    true after a bounded number of saves, regardless of whether anything happens" (S2.2). An
    event-shaped expiry is a `conditional` lifetime wearing an `expires:` label, and if the event
    never fires the entry stays hot forever -- the exact growth this phase exists to stop.
    """
    raw = _expiry_value(entry)
    line = _expiry_line(entry)
    if raw is None:
        return [Violation("A5", entry, entry.line,
                          "`active.md` entry with no `expires:`. Every hot entry here is "
                          "`expiring` (S3.1) and the schema has no unbounded option: default %d "
                          "saves, max %d. (A save# in the heading -- `expires save#149` -- "
                          "counts; nothing here has one.)"
                          % (EXPIRY_DEFAULT_SAVES, EXPIRY_MAX_SAVES))]
    val = _clean_value(raw)
    if not val or UNBOUNDED_RE.match(val):
        return [Violation("A5", entry, line,
                          "`expires: %s` is UNBOUNDED. The schema has no unbounded option "
                          "(S6 r1): a bounded save count, max %d." % (raw, EXPIRY_MAX_SAVES))]
    wm = WORD_SAVES_RE.match(val)
    if wm:                       # "three saves" -> "3 saves", then the ordinary relative path.
        val = "%d saves" % WORD_NUMERALS[wm.group(1).lower()]
    m = RELATIVE_SAVES_RE.match(val)
    if m:
        n = int(m.group(1) or m.group(2))
        if n > EXPIRY_MAX_SAVES:
            return [Violation("A5", entry, line,
                              "`expires: %s` is %d saves; the maximum is %d (S6 r1, default %d)."
                              % (raw, n, EXPIRY_MAX_SAVES, EXPIRY_DEFAULT_SAVES))]
        return []
    m = ABSOLUTE_SAVE_RE.match(val)
    if m:
        target = int(m.group(1))
        base, src = _horizon_base(entry, current_save)
        if base is None:
            return []           # horizon not computable -> WARNING, emitted by collect_warnings.
        if target - base > EXPIRY_MAX_SAVES:
            return [Violation("A5", entry, line,
                              "`expires: %s` is %d saves past %s (%d); the maximum is %d "
                              "(S6 r1). A bare integer is an ABSOLUTE save# -- the same reading "
                              "the step-6a drain uses (`expires <= current_save`). For a "
                              "relative bound write `%d saves`."
                              % (raw, target - base, src, base, EXPIRY_MAX_SAVES,
                                 EXPIRY_DEFAULT_SAVES))]
        return []
    return [Violation("A5", entry, line,
                      "`expires: %s` is not a bounded save count, so it is UNBOUNDED. "
                      "`active.md` is `expiring`: the bound is a number of SAVES, not an event "
                      "(an event is a `conditional` lifetime and belongs in another file)."
                      % raw)]


ID_SEPARATORS = ("·", " -- ", " — ", " – ", " | ", " - ")
# Heading segments that carry METADATA, not the entry's implication: S6's form puts the asserted
# save#, the expiry and the triggering event in the heading, and those differ between an expired
# entry and its renewal by construction.
META_SEGMENT_RE = re.compile(r"^\s*(asserted|expires?|expiry|save|said|written)\b", re.I)

# The IMPLICATION line of S6's own worked entry (spec.md:476):
#
#     ## A-7 · asserted save#146 · expires save#149 · until the example-project deploy lands
#     "defer save deep while we're deep in building"
#     -> Skip Stage 2 maintenance. The save# counter will fire; that is expected.
#
# Line 1 is the id and its bookkeeping, line 2 is the provenance quote, line 3 is THE
# IMPLICATION -- what the entry actually asks of the reader. S6 r3 is written in exactly those
# terms ("an entry whose IMPLICATION matches an expired one"), so this is the text A6 keys on.
ARROW_LINE_RE = re.compile(r"^\s*(?:[>*+]\s*|-\s+)?(?:-->|->|=>|[→⇒])\s*(?P<body>.+?)\s*$")


def _implication_source(entry):
    """The entry's IMPLICATION text, or None. A6 ground (c) matches on this and nothing else.

    Ranked, and the ranking is the fix for a measured defect:
      1. a declared `implication:` field;
      2. the `-> ...` body line(s) -- S6's own form, above;
      3. LAST RESORT, the heading with the id and every metadata segment removed, and ONLY if a
         substantive segment survives.

    THE ID IS NEVER PART OF THE KEY. S6 r3 says a renewal "creates a NEW entry", so a new id is
    the EXPECTED shape of a renewal, not an exception to it: any key carrying the id is
    structurally incapable of ever matching. Measured 2026-08-03 on two projects identical but
    for the id -- `## R9 - Broker restarted before each deploy` against an expired `## R4 -
    <same text>` gave ABORT SET (0), exit 0; renaming the active entry to `R4` produced the A6.

    NOR IS THE HEADING'S BOOKKEEPING. Rank 3 used to end `return " ".join(kept or parts)`, so a
    heading made ENTIRELY of metadata -- which is what S6's form is once the id and the trailing
    trigger are gone -- fell back to the metadata itself. `## A-1 · asserted save#1 · expires
    save#4` and `## A-2 · asserted save#5 · expires save#8` both reduced to the key "asserted
    expires": they matched, but so would every other pair of entries written in that form,
    whatever their content. That is a FUZZY matcher wearing an exact matcher's clothes, and A6 is
    an abort -- a rule that fires on a guess is worse than one that misses. When nothing
    substantive survives, rank 3 now yields None and ground (c) does not fire on the heading; the
    two fixtures above key on their `-> Skip the gate.` lines instead, which is their real
    content, and that is where the A6 they earn comes from.

    Rank 3 is kept, rather than dropped for ranks 1-2 alone, because a real corpus writes plenty
    of entries whose whole content is their title (`## R4 · Always run the linter before commit`)
    and dropping it would delete ground (c) for all of them.
    """
    v = entry.first("implication")
    if v:
        return v
    arrows = []
    for (_lineno, text) in entry.body:
        m = ARROW_LINE_RE.match(text)
        if m:
            arrows.append(m.group("body"))
    if arrows:
        return " ".join(arrows)
    title = _strip_emphasis(entry.title or "")
    parts = None
    for sep in ID_SEPARATORS:
        if sep in title:
            parts = [p.strip() for p in title.split(sep) if p.strip()]
            break
    if not parts:
        # No separator: the id is the first token (`heading_id`'s own fallback), so drop exactly
        # that token rather than keeping a key the id can move.
        ident = (entry.ident or "").strip()
        if ident and title.lower().startswith(ident.lower()):
            rest = title[len(ident):].lstrip(" \t:.-")
            return rest or None
        return title or None
    if parts[0].strip().lower() == (entry.ident or "").strip().lower():
        parts = parts[1:]
    kept = [p for p in parts if not META_SEGMENT_RE.match(p)]
    return " ".join(kept) if kept else None


def _normalise_implication(entry):
    """The comparison key A6 matches renewals on, or None. Built from `_implication_source`.

    Deliberately EXACT-match after case/whitespace/punctuation folding. A fuzzy matcher here
    would invent renewals, and A6 is an ABORT -- a rule that fires on a guess is worse than one
    that misses. The folding is one-way and lossy but not approximate: two strings that differ by
    reformatting fold together, two strings that differ by a word do not.

    A SAVE# INSIDE THE IMPLICATION IS CONTENT, NOT BOOKKEEPING, AND IS KEPT. This line used to
    read `s = re.sub(r"\\bsave\\s*#?\\s*\\d{1,6}\\b", " ", s)`, which folded `-> Defer the
    migration until save 4.` and `-> Defer the migration until save 12.` onto the single key
    "defer the migration until" -- so an entry deferring to save 12 matched an EXPIRED entry that
    had deferred to save 4 and aborted A6 as its renewal (measured 2026-08-03, fixture
    `fp7_savestrip`, exit 3). Those are two different instructions with two different deadlines;
    a matcher that cannot tell them apart is the fuzzy matcher the paragraph above forbids,
    wearing the strip's clothes. The DEADLINE IS THE INSTRUCTION here, which is exactly why the
    strip is wrong on this text and right one code path over: `_implication_source` rank 3 drops
    the HEADING's `asserted save#N` / `expires save#N` segments via `META_SEGMENT_RE`, and those
    are bookkeeping that differs between an expired entry and its renewal BY CONSTRUCTION. Two
    different texts, two different readings, and the earlier version applied the heading's
    reading to the body's text.
    """
    src = _implication_source(entry)
    s = _strip_emphasis(src or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def rule_a6_provenance(entry, expired_implications, current_save):
    """A6 (spec S6 r3 + r4): provenance is mandatory, and a RENEWAL needs a FRESH user quote.

    Grounds, each named in its own message:
      (a) no `quote:` at all              -- S6 r4, provenance is mandatory;
      (b) no save# for the quote anywhere -- S6 r4, the quote AND the save# it was said in;
      (c) a RENEWAL -- its implication matches an entry already in the expired archive -- whose
          QUOTE does not itself carry the current save#, evaluated ONLY on the save the entry
          declares it was asserted in -- S6 r3.

    (a) and (b) are PRESENCE checks about the entry, so they accept every form S6 writes,
    including the heading's `asserted save#146` (see the module block above `_expiry_value`).

    GROUND (c) HAS AN EVALUATION MOMENT: the save on which the entry is NEWLY ASSERTED. It fires
    iff the entry restates an already-expired implication AND `A >= C` AND `Q != C`, where

        A = the entry's DECLARED asserted save# (`_asserted_save`: the `asserted:`/`said-in:`/
            `save:` field, or the heading's `asserted save#N`)   -- the GATE, "look at me now";
        Q = the save# THE QUOTE ITSELF carries (`_quote_borne_save`)  -- the EVIDENCE;
        C = the current save#.

    `A >= C`, NOT `A == C`, AND THE `>` HALF IS A CLOSED DEFECT, NOT A GENEROSITY. `A > C` says
    the entry was asserted on a save that HAS NOT HAPPENED. That is not a fact about the world,
    it is an incoherent declaration, and the only safe reading of an incoherent declaration is to
    EVALUATE -- never to skip. Under `==` it skipped: measured 2026-08-03, `## A-2 · asserted
    save#99 · expires save#105` carrying a stale `(save 1)` quote whose implication was already
    in the expired archive returned exit 0 at `--save 5`, `--save 20`, `--save 50` and
    `--save 100`, while the identical entry declaring an honest `asserted save#5 · expires
    save#8` aborted A6 at `--save 5`. One lie about a number defeated the rule outright, for an
    unbounded number of saves. A future A now forces the gate OPEN and Q is tested exactly as
    normal -- which is why a future assertion carrying a genuinely fresh quote still passes. A
    future A is NOT an abort of its own; the abort set is CLOSED at A1..A8 and this adds nothing
    to it. It only refuses to let a self-declaration shut the gate.

    WITHOUT THE GATE THE RULE HAD NO EVALUATION MOMENT AND RE-FIRED FOREVER. Measured
    2026-08-03 on one file that never changed on disk: an entry legally renewed at save 5 (its
    quote carries `(said in save 5)`), expiring at save 8, gave exit 0 at `--save 5`, exit 3 at
    `--save 6` and exit 3 at `--save 7`. The only way to clear it was to rewrite the user's
    verbatim quote to a save it was not said in -- the rule protecting verbatim provenance
    demanded that provenance be falsified. Renewal is an EVENT; the check belongs on the save the
    event happened. That is the `A < C` half of the gate, and it stays.

    THIS IS NOT A RETURN TO SELF-CERTIFICATION, AND THE ASYMMETRY IS THE WHOLE ARGUMENT. A is
    written by the agent, so it may only ever decide WHETHER TO LOOK -- it is never the proof. Q
    remains the sole EVIDENCE and keeps its strict reading (`_quote_borne_save` is blind to every
    agent-written field, deliberately; see its docstring). The attack this must survive is an
    agent claiming an entry is FRESH: that still requires the USER'S QUOTE to carry the current
    save#, which the agent cannot fabricate without falsifying the user's own words on the page a
    human reads. The opposite lie -- claiming the entry is OLD, to close the gate and dodge the
    check -- buys the agent nothing: the horizon A5 bounds is measured FROM the asserted save#
    (`_horizon_base` prefers it over everything), so understating A shortens the entry's legal
    life by exactly the amount it was understated, and an entry that wanted a full horizon busts
    A5's `EXPIRY_MAX_SAVES` bound instead. Lying "this is old" only gets the entry demoted
    sooner. DO NOT "SIMPLIFY" THE GATE AWAY: dropping `A < C` restores the re-fire above, and
    letting A answer the freshness question restores the self-certification `_quote_borne_save`
    exists to stop.

    THE PRINCIPLE, and it is in a comment because this is the THIRD time the same lesson has bitten
    this one rule: A FIELD THE AGENT WRITES MAY NARROW WHAT IT IS ACCOUNTABLE FOR, NEVER WIDEN IT.
    Reading A at all was already the concession, and it was justified by ONE property and no
    other: claiming to be OLD is SELF-PUNISHING, because the same declaration shortens A5's
    horizon. Claiming to be in the FUTURE is the exact opposite -- self-REWARDING on both rules at
    once (it shut A6's gate AND stretched A5's base), so it must not be honoured on either.
      round 1: freshness was read from a self-declared field         -> self-certifying;
      round 2: the renewal match was keyed on the entry id           -> never fired (S6 r3 renews
               under a NEW id by its own words);
      round 3: a self-declared field was allowed to shut the gate    -> this one.
    Same lesson, three shapes. Before widening what A may decide, ask which direction the lie
    pays in -- and if it pays at all, it is not a gate, it is a request.

    (c) IS NOT A PRESENCE CHECK AND ITS EVIDENCE DOES NOT ACCEPT THOSE FORMS. It reads
    `_quote_borne_save` and nothing else. S6 r3 constrains THE AGENT ("the agent cannot renew"),
    and `asserted:` / `said-in:` / `save:` and the heading's asserted save# are all written by
    that agent in that save -- so letting any of them answer the freshness question makes r3
    self-certifying, which is the exact failure r3 was written to prevent. The freshness evidence
    is the save# carried by the user's own quote. Two fixtures, both at exit 0 before this
    changed, both a verbatim re-statement of an expired entry under a new id with its ORIGINAL
    save-1 quote: fixture g passed on the heading's `asserted save#5` alone, fixture h passed with
    `asserted: save 5`, `said-in: save 5` and `save: 5` added. Neither carried one word of fresh
    user speech. Both still abort, because both declare `A == 5` and are validated at save 5.

    Ground (c) matching is on the normalised IMPLICATION and never on the id or the heading's
    bookkeeping -- see `_implication_source`. A renewal creates a NEW entry with a NEW id by
    S6 r3's own words, so a key containing the id can never match.

    Exempt inside the UNRESOLVED region: S2.4 T-3 exempts it from S6 rules 3-4 by name.

    Ground (c) is SKIPPED, with a warning, in exactly two cases, and NEVER silently:
      * the current save# is unknown (C) -- see `resolve_current_save`. A freshness check with no
        notion of "now" would either pass everything or fail everything, and both are lies.
      * the entry declares no asserted save# at all (A) -- there is no gate to open, so the
        evaluation moment cannot be located. Grounds (a) and (b) still apply, so an entry with no
        provenance whatsoever is still caught there.
    Both warnings are emitted by `collect_warnings` and both name the entry.
    """
    if entry.in_unresolved:
        return []
    line, raw = _quote_field(entry)
    if not raw:
        return [Violation("A6", entry, line if line else entry.line,
                          "no `quote:`. Provenance is mandatory on every `active.md` entry "
                          "(S6 r4): the user's verbatim words plus the save# they were said in. "
                          "A bare quoted line under the heading counts; a placeholder "
                          "(`none`, `TBD`) does not.")]
    said = _quote_save(entry)
    if said is None:
        return [Violation("A6", entry, line,
                          "`quote:` carries no save#. Provenance is the quote AND the save# it "
                          "was said in (S6 r4); without it a renewal cannot be told from a "
                          "restatement. Add `save: <n>`, or `asserted save#<n>` in the heading.")]
    if current_save is None:
        return []
    # THE GATE. Not the evidence -- see the asymmetry paragraph above. `>=`, never `==`: a
    # declared assertion save# in the FUTURE is incoherent, and an incoherent declaration is
    # EVALUATED, never obeyed. See the PRINCIPLE block above.
    asserted = _asserted_save(entry)
    if asserted is None or asserted < current_save:
        return []
    key = _normalise_implication(entry)
    if not key or key not in expired_implications:
        return []
    src_rel, src_line = expired_implications[key]
    fresh = _quote_borne_save(entry)
    if fresh == current_save:
        return []
    if fresh is None:
        return [Violation(
            "A6", entry, line,
            "RENEWAL with no fresh quote: this restates an already-expired entry (%s:%d), and "
            "its quote carries no save# OF ITS OWN. S6 r3 renews on a verbatim user quote from "
            "the CURRENT session (save %d) and nothing else -- `save:`/`said-in:`/`asserted:` "
            "and `asserted save#<n>` in the heading are written by the agent this rule "
            "constrains, so they cannot answer it. Mark the quote itself: "
            "`quote: \"<their words>\" (said in save %d)`."
            % (src_rel, src_line, current_save, current_save))]
    return [Violation(
        "A6", entry, line,
        "RENEWAL with no fresh quote: this restates an already-expired entry "
        "(%s:%d) but its quote is from save %d, not the current save %d. S6 r3 "
        "renews on a FRESH user quote only -- otherwise an entry outlives the "
        "interest that created it." % (src_rel, src_line, fresh, current_save))]


def rule_a7_gate(entry, rel):
    """A7 (spec S7.1): every entry in a deferred archive declares its promotion `gate:`.

    Required on EVERY entry in `_archive/backlog-deferred.md` and
    `_archive/open-questions-deferred.md`. A deferred entry with no gate is not deferred, it is
    deleted with extra steps: nothing states the condition that would bring it back.

    A PLACEHOLDER IS NOT A GATE. `gate: none` / `gate: n/a - just deferred` used to satisfy this,
    which is the rule's own stated requirement ("the named condition that brings it back")
    answered with the word "none". See `_is_placeholder`.

    NEITHER IS A SENTENCE THAT DISCLAIMS ONE. `gate: we never worked out what would bring this
    back` is the same answer at greater length; see `_gate_names_no_condition`, which is
    deliberately a disclaimer detector and not a classifier of whether prose names a gate.
    """
    if rel not in GATE_REQUIRED:
        return []
    gates = [v for v in entry.values("gate")
             if v.strip() and not _is_placeholder(v) and not _gate_names_no_condition(v)]
    if gates:
        return []
    stated = [v for v in entry.values("gate") if v.strip()]
    return [Violation("A7", entry, entry.where("gate") if stated else entry.line,
                      "%s in %s. S7.1 requires a promotion gate on every deferred "
                      "entry -- the named condition that brings it back. Without one it is "
                      "unreachable content."
                      % ("`gate: %s` names no condition" % stated[0] if stated
                         else "no `gate:`", rel))]


def rule_a8_multi_kind(entry, exempt=False):
    """A8 (T-1, ruling R-B): more than one kind declared, outside the UNRESOLVED region.

    This is a DETECTOR, not a splitter. It never rewrites prose -- spec S5.4 calls a
    prose-rewriting pass "the only content-destroying step in v4", and that step is not this
    one. The remedy is stated in the message and performed by a human or by the save's own
    authoring step.

    24.1% of real decision entries carry two kinds (S2.4), so this is the NORMAL case, not an
    exotic one. Author's escape hatch when the split genuinely cannot be made: `kind: unresolved`
    with both candidates named, in the `<!-- UNRESOLVED -->` region, with an expiry (T-3) -- at
    which point A4 and A5 take over and the deadline is real.
    """
    if entry.in_unresolved or exempt:
        return []
    kinds = [(tok, ln) for (tok, ln) in declared_kinds(entry) if tok in KINDS]
    if len(kinds) < 2:
        return []
    names = ", ".join("`%s` (line %d)" % (tok, ln) for (tok, ln) in kinds)
    return [Violation("A8", entry, kinds[0][1],
                      "declares %d kinds: %s. Split into two entries, one per kind, each with "
                      "its own id, cross-referenced. Never filed under both in one place and "
                      "never under a guessed single kind (T-1)." % (len(kinds), names))]


def validate_split(entries):
    """The other half of R-B: when a split HAS been performed, check it was performed properly.

    A performed split declares itself with `split-of:` (or `split:`) naming the OTHER half's id.
    Structural inference is deliberately not used -- guessing which two entries "were probably
    one" would manufacture findings out of ordinary cross-references.

    Four checks, from T-1: each half declares exactly ONE kind; the two halves declare DIFFERENT
    kinds; their ids differ; and each cites the other.

    Findings here are WARNINGS, not aborts. The abort set is CLOSED at A1..A8 and a malformed
    split is not a member of it. A8 already fires on the un-split case, which is the one the
    verdict turns on.
    """
    out = []
    by_id = {}
    for ent in entries:
        by_id.setdefault(ent.ident, ent)
    for ent in entries:
        targets = _split_targets(ent)
        if not targets:
            continue
        line = ent.where("split-of") if "split-of" in ent.fields else ent.where("split")
        for tid in targets:
            if tid == ent.ident:
                out.append(Warning_("SPLIT", ent.ident, ent.rel, line,
                                    "declares a split of ITSELF (%s). The two halves of a split "
                                    "have different ids (T-1)." % tid))
                continue
            peer = by_id.get(tid)
            if peer is None:
                continue      # a target outside scope is a dangling reference; recall
                              # `check` owns that class and this tool must not duplicate it.
            mine = [t for (t, _l) in declared_kinds(ent) if t in KINDS]
            theirs = [t for (t, _l) in declared_kinds(peer) if t in KINDS]
            if len(mine) != 1:
                out.append(Warning_("SPLIT", ent.ident, ent.rel, line,
                                    "is one half of a split with %s but declares %d kinds. Each "
                                    "half of a split declares exactly one (T-1)."
                                    % (tid, len(mine))))
            if len(theirs) != 1:
                out.append(Warning_("SPLIT", ent.ident, ent.rel, line,
                                    "is one half of a split with %s, which declares %d kinds. "
                                    "Each half declares exactly one (T-1)." % (tid, len(theirs))))
            if len(mine) == 1 and len(theirs) == 1 and mine[0] == theirs[0]:
                out.append(Warning_("SPLIT", ent.ident, ent.rel, line,
                                    "and %s both declare `kind: %s`. A split puts ONE kind in "
                                    "each half; two halves of the same kind are one entry with "
                                    "two ids, not a split (T-1)." % (tid, mine[0])))
            if ent.ident not in _split_targets(peer):
                out.append(Warning_("SPLIT", ent.ident, ent.rel, line,
                                    "cites %s as its other half, but %s does not cite back. The "
                                    "cross-reference is what stops a reader losing the other "
                                    "half of the thought (T-1)." % (tid, tid)))
    return out


_REF_RE = re.compile(r"\b([A-Za-z]{1,4}-?\d{1,4}[a-z]?)\b")


def _split_targets(entry):
    """Ids named on this entry's `split-of:` / `split:` lines. Empty when it claims no split."""
    ids = []
    for key in ("split-of", "split"):
        for raw in entry.values(key):
            for tok in _REF_RE.findall(raw):
                if tok not in ids:
                    ids.append(tok)
    return ids


# --------------------------------------------------------------------------- warnings

LOADING_KEYS = ("load", "hot", "cold")


def collect_warnings(entry, rel, current_save):
    """Findings that are explicitly NOT aborts. Exit stays 0 when these are all that fired.

    `load:` / `hot:` / `cold:` -- spec S2.3: "Nobody ever writes hot or cold." Loading is DERIVED
    from kind and lifetime and is never chosen, and that is the point: making loading a
    consequence is what stops "this feels important" from putting things in context. A declared
    one is inert (this tool ignores it when deciding anything) but it is a sign the author
    believes they are steering loading, so it is named. Never an abort -- the abort set is
    CLOSED at A1..A8.

    Everything else here is a check that could not be COMPLETED, or a mismatch the closed abort
    set has no member for, reported as such. A check that silently degrades to "pass" is the
    exit-6 defect in miniature.
    """
    out = []
    for key in LOADING_KEYS:
        for (lineno, raw) in entry.fields.get(key, ()):
            out.append(Warning_(
                "WARN", entry.ident, rel, lineno,
                "declares `%s: %s`. Loading is DERIVED from kind and lifetime, never declared "
                "(S2.3) -- this line steers nothing and was ignored. record -> COLD always; "
                "plan with an unsatisfied gate -> DEFERRED; rules/spec/ungated plan -> HOT."
                % (key, raw)))

    # The file's declared LIFETIME (S3.1) is not part of any abort -- A1 compares kinds, and the
    # abort set is CLOSED, so this cannot be A9. It is still worth naming: an entry declaring
    # `permanent` in `active.md` (whose lifetime is `expiring`) is precisely the "keeps dead
    # content hot" outcome R-C's own rationale says this phase exists to stop.
    _fk, file_life = FILE_KIND.get(rel, (None, None))
    if file_life and not entry.in_unresolved:
        allowed = LIFETIME_ALLOWED.get(rel, (file_life,))
        _lifes = declared_lifetimes(entry)
        for (tok, lineno) in _lifes:
            if tok not in LIFETIMES or tok in allowed:
                continue
            # A1's lifetime half owns this one -- but ONLY where A1 actually FIRES, and the
            # two predicates are NOT the same. A1 bails on a GENERATED-exempt file and on more
            # than one declared lifetime. Yielding regardless made `state.md` / `manifest.md`
            # declaring a single `lifetime: expiring` report the condition ZERO times at exit 0,
            # where the unmodified build warned. That is a REGRESSION, found by cross-family
            # review and reproduced against a control that fires. SILENT IS WORSE THAN
            # DUPLICATED, so this mirrors A1's own conditions and reads the SAME
            # `DECLARATION_EXEMPT` constant A1's caller does, so the two cannot drift apart.
            if (tok == "expiring" and rel != ACTIVE_FILE
                    and rel not in DECLARATION_EXEMPT and len(_lifes) == 1):
                continue
            # Wording preserved verbatim for the single-allowed case: the shipped selftest
            # asserts on this substring, and a message is an interface once a test reads it.
            if len(allowed) == 1:
                what = "whose declared lifetime is `%s`" % allowed[0]
            else:
                what = "which permits %s" % " or ".join("`%s`" % a for a in allowed)
            out.append(Warning_(
                "WARN", entry.ident, rel, lineno,
                "declares `lifetime: %s` in %s, %s (S3.1). Not an abort -- the abort set is "
                "closed at A1..A8 -- but a longer lifetime than the file's disarms the demotion "
                "the file exists for." % (tok, rel, what)))

    if rel == ACTIVE_FILE and not entry.in_unresolved:
        raw = _expiry_value(entry)
        if raw is not None and ABSOLUTE_SAVE_RE.match(_clean_value(raw)) \
                and _horizon_base(entry, current_save)[0] is None:
            out.append(Warning_(
                "SKIPPED", entry.ident, rel, _expiry_line(entry),
                "`expires: %s` names an absolute save# but no base could be found for the "
                "horizon -- no `save:`/`asserted:` field, no save# in the heading, no save# in "
                "the provenance quote, and the current save# is unknown -- so A5's max-%d check "
                "was SKIPPED for this entry. Pass --save <n>, or add `save: <n>`."
                % (raw, EXPIRY_MAX_SAVES)))
        if current_save is None and _quote_field(entry)[1]:
            out.append(Warning_(
                "SKIPPED", entry.ident, rel, entry.where("quote"),
                "the current save# is unknown, so A6's RENEWAL freshness check was SKIPPED for "
                "this entry (its provenance was still checked). Pass --save <n>, or give "
                "state.md a `save:` line."))
        # The OTHER half of the same hole. A6 ground (c) evaluates on the save the entry declares
        # it was asserted in (`A >= C`, see `rule_a6_provenance`); an entry that declares no
        # asserted save# at all supplies no A, so the evaluation moment cannot be located and the
        # ground is skipped. That is a check that did not COMPLETE, and it is named rather than
        # allowed to read as a pass -- a silently-degraded check is the exit-6 defect in
        # miniature. Grounds (a) and (b) ran, so an entry with no provenance at all is still an
        # abort; what is missing here is only the renewal comparison.
        elif current_save is not None and _quote_field(entry)[1] \
                and _asserted_save(entry) is None:
            out.append(Warning_(
                "SKIPPED", entry.ident, rel, entry.where("quote"),
                "declares no assertion save#, so A6's RENEWAL freshness check (ground (c)) was "
                "NOT EVALUATED for this entry -- that ground fires only on the save an entry is "
                "newly asserted in, and this entry does not say which save that is. Its "
                "provenance was still checked (grounds (a) and (b)). Add `asserted save#%d` to "
                "the heading, or a `save: %d` field, on the save you assert it."
                % (current_save, current_save)))
    return out


# --------------------------------------------------------------------------- T-2 (ruling R-C)

def resolve_lifetimes(entries):
    """T-2: kinds agree, lifetimes do not -> take the SHORTER lifetime. RESOLVES, never mutates.

    Returns [(entry, chosen, [(token, line), ...])] for every entry that declared more than one
    recognised lifetime. Writes no file (R-D). The report goes to stderr; the entry is left
    exactly as the author wrote it and a human makes the edit.

    WHY THE DIRECTION MATTERS, and why it is not arbitrary. Lifetime is what arms expiry-driven
    demotion at spec S6a row 1: `expiring` content leaves the hot set on a save count, whether or
    not anyone revisits it. Taking the LONGER of two declared lifetimes disarms that -- an entry
    the author half-believed was `expiring` becomes `permanent` and stays hot forever. That is
    precisely the unbounded growth of the hot set that this whole phase exists to stop, and it
    would arrive through the one mechanism that is supposed to prevent it. Taking the SHORTER
    lifetime can only cost a re-assertion: a `conditional` entry wrongly resolved to `expiring`
    comes back up within its bounded save count (default 3, max 10) and the author restates it.
    Over-expiry is recoverable in one save; under-expiry is recoverable only by someone
    noticing, which is the failure mode with no owner.
    """
    out = []
    for ent in entries:
        lifes = [(t, ln) for (t, ln) in declared_lifetimes(ent) if t in LIFETIMES]
        if len(lifes) < 2:
            continue
        # S2.4 T-2's precondition, as written: "When kinds AGREE but lifetimes do not." Two
        # disagreeing kinds is T-1's case and A8's abort; resolving a lifetime on top of it
        # reported a resolution for an entry that is going to be split into two anyway.
        if len([t for (t, _l) in declared_kinds(ent) if t in KINDS]) > 1:
            continue
        chosen = min(lifes, key=lambda p: LIFETIME_ORDER[p[0]])[0]
        out.append((ent, chosen, lifes))
    return out


# ------------------------------------------------------- A9 .. A12: the content contract
#
# `save/SKILL.md` § What a hot file may hold states the contract these four check: a hot file
# holds CURRENT STATE ONLY, and a work item has a required shape.
#
# THEY ARE WARNINGS AND CAN NEVER BE ANYTHING ELSE. C-53, the maintainer 2026-08-16, verbatim: "Warnings
# should be warning. Nothing should ever block a save. That will render the system unusable." A
# save is how a session's work survives the session ending, so refusing one to enforce tidiness
# trades the thing this system protects for the thing it tidies. A refused save does not produce
# a clean file; it produces a lost session.
#
# THE ABORT SET IS CLOSED AT A1..A8 AND THESE DO NOT JOIN IT. `_ST_FINDING_RE` parses `A[1-8]`
# only, so a content warning cannot be read back as an abort by the suite either -- and
# `_st_arms_content` asserts exactly that, because the cheapest way to break C-53 later is to
# widen that regex to `A\d+` without noticing what else it then swallows.

# A9/A10/A11 are about WORK ITEMS, so they run on the two plan files and nowhere else.
CONTENT_ITEM_FILES = ("backlog.md", "open_questions.md")

# A12 additionally reads the two spec files. It deliberately does NOT read CONSTRAINTS.md or
# active.md: both are REQUIRED by their own contract to carry a verbatim user quote with the
# save# it was said in, which is exactly the shape A12's narrative class matches. Firing there
# would flag the thing the file exists to hold.
CONTENT_PROSE_FILES = ("backlog.md", "open_questions.md", "architecture.md", "design.md")

# The item ceiling. A CONTRACT, not a measurement -- the same class as active.md's ten-save
# expiry bound. An item is the remaining ASK plus what a fresh session needs in order to act.
ITEM_CEILING_BYTES = 1200

# ONE completion vocabulary, named ONCE and used by BOTH A9 and A11, and reproduced verbatim in
# SKILL.md's A9 row and its reduction pass. A cross-family review found the document naming SIX
# words while the code fired on NINE, so an author obeying the contract could be warned about a
# word the contract never mentions. One list, one place, quoted everywhere else.
COMPLETION_WORDS = ("DONE", "CLOSED", "SHIPPED", "COMPLETE", "COMPLETED",
                    "MERGED", "FIXED", "RETIRED", "DELETED")

# ALL-CAPS and standing alone. Lower case is ordinary prose ("the resolver is done"), and
# requiring capitals is what keeps A9 off every third sentence.
COMPLETION_RE = re.compile(
    r"(?<![A-Za-z])(%s)(?![A-Za-z])" % "|".join(COMPLETION_WORDS))

# A NEGATED status is the opposite of a completion, and A9 must not read one as a marker.
# `Status: NOT DONE` announces that the work is unfinished; warning there tells the author to
# mark a LIVE entry `done:`, and step 6a would evict it on the next save. The window is short on
# purpose - it looks only at what immediately precedes the word, so a negation about something
# else earlier in the sentence does not suppress a real announcement.
NEGATION_RE = re.compile(
    r"(?i)\b(?:not|never|isn'?t|aren'?t|wasn'?t|no longer|far from|nowhere near)\b"
    r"[\s\w'-]{0,12}$")

# The drain's completion keys (demote.py row 2). They are NOT in FIELD_KEYS -- this tool
# interprets `done:` for nothing else -- so A9 reads the body itself rather than `entry.fields`.
DONE_KEYS = ("done", "completed")
DONE_PLACEHOLDERS = frozenset(("", "-", "--", "none", "n/a", "na", "tbd", "todo", "?"))

# `RESOLVED` is open_questions.md's marker. Matched PERMISSIVELY on the heading: A9 is a warning,
# so declining to fire on an entry that already carries a plausible marker is the safe direction.
# The lookbehind is what stops `UNRESOLVED` reading as a satisfied marker.
RESOLVED_RE = re.compile(r"(?<![A-Z])RESOLVED")

# A11: work written below entry level. A bullet or table row carrying its own status is work
# `demote.py` cannot select and `recall get` cannot fetch, so it can only accumulate.
# DELIBERATELY NARROW: a table row with NO status marker is the separate table-row blind spot,
# which is an open item and not this rule's business -- C-19, no mechanism without a measured
# failure, and the measured failure there is a confusing count rather than a lost entry.
BELOW_ENTRY_LEAD_RE = re.compile(
    r"^ {0,7}(?:[-*+]|\d{1,3}[.)])\s"          # a list bullet or a numbered item
    r"|^ {0,7}\|"                              # a table row
    r"|^ {0,7}\*\*")                           # a STANDALONE BOLD RUN - the dominant shape
BELOW_ENTRY_STATUS_RE = re.compile(
    r"(?<![A-Za-z])(?:%s)(?![A-Za-z])"
    r"|(?<![A-Za-z])(?:done|completed|wont-do|gate)[ \t]*:" % "|".join(COMPLETION_WORDS))

# WHAT A11 CANNOT SEE, stated because an unstated limit is a defect and a stated one is not.
# A11 walks ENTRY BODIES, so a bullet or table row written ABOVE the first heading belongs to no
# entry and is never examined. Measured 0 occurrences across three corpora; the remedy is
# restructuring this per-entry collector into a per-file one, which C-19 does not license
# against a failure nobody has met. SKILL.md's A11 row carries the same sentence.

# Inline code spans are stripped before any of the above applies: a file that DOCUMENTS a marker
# must not be reported as carrying one. Measured need, not caution -- both SKILL.md and every
# procedure that explains the drain would otherwise light up.
INLINE_CODE_RE = re.compile(r"`[^`]*`")

# A12's classes. Every pattern is one real hot files actually contain: a rule nobody triggers is
# a rule not worth shipping. HIGH PRECISION OVER RECALL, deliberately -- A12 is declared a LOWER
# BOUND in SKILL.md, and a warning that over-fires gets ignored, which costs more than a miss.
OFF_CONTRACT_CLASSES = (
    ("peer-conversation", re.compile(
        r"\brelay[ \t]*#?\d{3,}\b"
        r"|\brecipient_verified\b"
        r"|\bthey (?:re-)?(?:measured|verified|reported|confirmed|declined|ruled|agreed)\b"
        r"|\bpaged (?:by|me)\b", re.I)),
    # A RECEIPT IS A RESULT, NOT A NOUN. `sha256`, `byte-identical` and `merge-base` are
    # ordinary vocabulary in files whose whole job is algorithms and invariants, and matching
    # them bare reported valid specification as history - measured firing on a peer's
    # architecture.md. Every alternative below needs an observed OUTCOME: an assertion, a
    # count, or a hash actually quoted.
    ("verification-receipt", re.compile(
        r"\bVERIFIED AT SOURCE\b"
        r"|\b\d+ checks?, \d+ failed\b"
        r"|\bselftest[ \t]+\d"
        r"|\b(?:sha256|sha|blob|hash|tip|commit|absorbed at)[ \t]+[0-9a-f]{7,40}\b"
        r"|\bmerge-base[ \t]+--is-ancestor\b"
        r"|\bbyte-identical[ \t]+(?:to|across|both|on|either)\b", re.I)),
    ("session-narrative", re.compile(
        r"\bsave#\d+\b|\bthis session\b"
        r"|\bI (?:measured|found|ran|checked|caught|shipped|built|wrote|sent|told|corrected)\b"
        r"|\bMEASURED (?:here|on|across|at|save)\b", re.I)),
    ("correction-of-earlier-text", re.compile(
        r"\bCORRECTED save#|\bthis line (?:read|said)\b|\bthe original statement\b"
        r"|\bwas (?:wrong|false|an undercount)\b|\bthe stale text\b|\bkept one save\b",
        re.I)),
)


def _strip_inline_code(text):
    """Text with inline-code spans blanked. Documenting a marker must not read as writing one."""
    return INLINE_CODE_RE.sub(" ", text)


def _is_known_field_line(text):
    """True when the line is a field the toolchain interprets, or a drain marker.

    A12 skips these. `done: save#6 - shipped` is a MARKER, not narrative, and reporting the very
    field the contract asks authors to write would be the rule arguing with itself.
    """
    m = FIELD_RE.match(text)
    if not m:
        return False
    key = m.group(1).lower()
    return key in FIELD_KEYS or key in DONE_KEYS or key == "wont-do"


def _has_done_field(entry):
    """A real `done:` / `completed:` field carrying a value the drain would act on."""
    for (_ln, text) in entry.body:
        m = FIELD_RE.match(text)
        if m and m.group(1).lower() in DONE_KEYS:
            if _clean_value(m.group(2)).lower() not in DONE_PLACEHOLDERS:
                return True
    return False


def entry_byte_span(entry, line_spans):
    """The entry's exact size in bytes, its heading line through its last line.

    The Entry model carries no offsets; `rows` does -- each row is
    (lineno, text, byte_start, byte_end, in_fence) -- so the caller passes the map built once per
    file. Returns None when the span cannot be resolved, and A10 then SKIPS rather than guessing:
    a size computed from a span nobody checked is worse than no size at all.
    """
    head = line_spans.get(entry.line)
    tail = line_spans.get(entry.end_line)
    if head is None or tail is None or tail[1] < head[0]:
        return None
    return tail[1] - head[0]


def collect_content_warnings(entry, rel, line_spans):
    """A9..A12 -- the hot-file content contract. WARNINGS ONLY; the exit code never moves (C-53).

    Every finding names the file, the line and the entry id. A count is not actionable: the id is
    the only handle an operator can act on, which is this file's own standing contract.
    """
    out = []
    if entry.in_unresolved:
        return out

    body = [(ln, _strip_inline_code(text)) for (ln, text) in entry.body]

    # ---- A9: the body announces completion and carries no marker step 6a can fire on.
    if rel in CONTENT_ITEM_FILES:
        hit = None
        for (ln, text) in body:
            # A line that is ITSELF below entry level announces the status of NESTED work, not
            # of this entry. A11 reports those. Reading one as the entry's own completion is how
            # A9 told an author to mark a LIVE entry `done:` - and step 6a would then evict it,
            # which is a C-4 content loss reached by following the tool's own advice.
            if BELOW_ENTRY_LEAD_RE.match(text):
                continue
            m = COMPLETION_RE.search(text)
            if m is None:
                continue
            if NEGATION_RE.search(text[:m.start()]):
                continue
            hit = (ln, m.group(1))
            break
        if hit is not None and not _has_done_field(entry) \
                and RESOLVED_RE.search(_strip_inline_code(entry.title)) is None:
            marker = ("`RESOLVED` in the heading, in status position"
                      if rel == "open_questions.md" else "`done:` field")
            out.append(Warning_(
                "A9", entry.ident, rel, hit[0],
                "the body announces `%s` but the entry carries no %s, so step 6a cannot see it "
                "and it stays hot indefinitely. Add the marker and the next drain evicts it; if "
                "the entry is NOT finished, reword the line. Not an abort -- the abort set is "
                "CLOSED at A1..A8 and no content condition may ever fail a save (C-53)."
                % (hit[1], marker)))

    # ---- A10: the entry is over the item ceiling.
    if rel in CONTENT_ITEM_FILES:
        size = entry_byte_span(entry, line_spans)
        if size is not None and size > ITEM_CEILING_BYTES:
            out.append(Warning_(
                "A10", entry.ident, rel, entry.line,
                "is %d bytes against the %d-byte item ceiling. An item is the remaining ASK plus "
                "what a fresh session needs in order to act on it; the excess is history and "
                "belongs in the checkpoint. Not an abort -- no size condition anywhere in v4 "
                "fails or truncates a save." % (size, ITEM_CEILING_BYTES)))

    # ---- A11: work written below entry level.
    if rel in CONTENT_ITEM_FILES:
        for (ln, text) in body:
            if BELOW_ENTRY_LEAD_RE.match(text) and BELOW_ENTRY_STATUS_RE.search(text):
                out.append(Warning_(
                    "A11", entry.ident, rel, ln,
                    "a list item or table row here carries its own status marker, so this is "
                    "WORK written below entry level. Step 6a selects whole ENTRIES, so a marker "
                    "in a bullet fires nothing and `recall get` cannot fetch it. Give it its "
                    "own heading at this file's top entry level. Not an abort (C-53)."))

    # ---- A12: off-contract content. ONE warning per entry, naming each class and its first line.
    if rel in CONTENT_PROSE_FILES:
        first = []
        for (label, rx) in OFF_CONTRACT_CLASSES:
            for (ln, text) in body:
                if _is_known_field_line(text):
                    continue
                if rx.search(text):
                    first.append((label, ln))
                    break
        if first:
            where = ", ".join("%s (first at %s:%d)" % (lab, rel, ln) for (lab, ln) in first)
            out.append(Warning_(
                "A12", entry.ident, rel, first[0][1],
                "carries off-contract content: %s. A hot file holds CURRENT STATE ONLY and this "
                "belongs in the checkpoint (save/SKILL.md § What a hot file may hold). THIS "
                "COUNT IS A LOWER BOUND -- it matches phrasings that actually occur, so "
                "narrative using none of them is invisible to it and a small result is NOT "
                "evidence of a clean file. Not an abort (C-53)." % where))

    return out


def collect_state_field_warnings(rel, rows):
    """A13 -- the two unbounded prose fields in `state.md`. WARNING ONLY; exit never moves (C-53).

    FILE-LEVEL, not entry-level, and that is forced rather than chosen: `collect_content_warnings`
    takes an Entry, and `goal` / `active_track` are FIELDS in a generated file that has no entries
    at all. There is nothing for the per-entry path to hang this on.

    The value of `key: |` is every following line that is blank or indented, which is exactly the
    span `update_state.py` preserves byte-for-byte (its D2) and exactly the span `restore`
    loads. Size is measured over that span and no further -- a field that ends the block must not
    swallow `repo_root:` or the `counts:` block below it.

    A field with an INLINE value (`goal: ship the thing`) is skipped rather than measured. It
    cannot carry a multi-line accumulation, so it cannot exhibit the failure, and a check that
    reports on a shape that cannot fail is decoration.
    """
    out = []
    if rel != STATE_FILE:
        return out
    n = len(rows)
    for i in range(n):
        (lineno, text, _bs, _be, in_fence) = rows[i]
        if in_fence:
            continue
        m = STATE_BLOCK_FIELD_RE.match(text)
        if m is None or m.group("key") not in STATE_PROSE_FIELDS:
            continue
        j = i + 1
        first = None
        last = None
        while j < n:
            (_ln, btext, bstart, bend, _bf) = rows[j]
            if btext.strip() and not btext[:1].isspace():
                break                      # first non-blank, non-indented line ENDS the value
            if first is None:
                first = bstart
            last = bend
            j += 1
        size = 0 if (first is None or last is None or last < first) else (last - first)
        if size > STATE_FIELD_CEILING_BYTES:
            out.append(Warning_(
                "A13", m.group("key"), rel, lineno,
                "the `%s` block is %d bytes against the %d-byte ceiling. This field is PROSE that "
                "update_state.py carries forward byte-for-byte every save, so it never shrinks "
                "unless a person rewrites it -- and every restore of this project pays for it. "
                "Route each part to the file that already owns it (save/SKILL.md S What a hot "
                "file may hold): a standing rule to CONSTRAINTS.md in one line, work to backlog.md, a "
                "contract to architecture.md/design.md, a quote from the user to CONSTRAINTS.md, "
                "session narrative to the checkpoint. Keep only what a fresh session needs in "
                "order to continue RIGHT NOW. BEFORE MOVING A NARRATIVE-SHAPED LINE TO THE "
                "CHECKPOINT, APPLY THE TEST: would deleting this sentence make some future "
                "measurement cost full price again? If yes it is a RULE and stays HOT in "
                "CONSTRAINTS.md, whatever it looks like. Not an abort -- no size condition anywhere in "
                "v4 fails or truncates a save (C-53)."
                % (m.group("key"), size, STATE_FIELD_CEILING_BYTES)))
    return out


# ---- A14: REFERENCE INTEGRITY -- step 7d's third limb, which architecture.md R-ARCH-6 has always
# assigned here and which nothing ever built. Until now a save committed dangling citations at
# exit 0 and in SILENCE: the validator passed over an unresolvable target without a word, so a
# clean 7d read as evidence that every pointer resolved. It never was.
#
# Warning-only: the abort set remains A1..A8. A dangling reference written by
# an earlier tool or by hand must be visible without stranding a future save.
# Scan hot citers only: immutable historical records are not a live repair
# queue. Do not report collisions here; hot entries and their cold preimages
# can intentionally share ids while preserving different historical bytes.
# This check answers whether a hot file points at nothing, not whether every
# occurrence across historical snapshots has one identical definition.
#
# The verdict this limb gives is therefore NARROWER than `recall check`'s and the two do not
# contradict each other -- A14 answers "does a HOT file point at nothing", which is the question a
# save can act on. `citemap.build_map` is the shared substrate (architecture.md R-ARCH-5: no second
# fence machine, no second occurrence scanner), so the two can never disagree about a given id.
A14_MAX_SITES = 3

# THE DECLARED-EXTERNAL REGISTER, read from a WELL-KNOWN PATH and never from a flag.
# Step 7d invokes this validator with no arguments of its own, so an id the operator has
# already adjudicated as external has no other route into this limb -- and without one A14
# reports as unresolved exactly the citations somebody decided, in writing, resolve
# elsewhere. Same file and same grammar as `recall check --ignore-file`, through the
# same parser (`recall.load_ignore_file`), so the GRAMMAR cannot drift between them.
# The FILE still can: `check` reads whatever path its caller names, and this limb reads
# only the one below. That is deliberate -- see the flag note above.
#
# A THIRD NARROWING, and it is narrower than the two above: it suppresses only ids a human
# WROTE DOWN. It does NOT decide whether a declaration is still needed. Staleness is a
# separate question with a separate instrument, and a second one built here would be the
# second implementation of one predicate that R-ARCH-5 exists to prevent.
#
# SUPPRESSION IS NEVER SILENT. Every suppressed id is named in one note carrying the
# register's path, because a limb that quietly stopped reporting would be indistinguishable
# from one that had gone blind -- which is the failure this whole limb was written to end.
A14_DECLARED_EXTERNAL = "_archive/migration-declared-external.txt"
# The active validation hot files, derived from FILE_KIND rather than typed: the two `_archive/` rows are
# deferred storage, and a citation there is cold by definition.
A14_HOT_FILES = tuple(r for r in FILE_KIND if "/" not in r and r not in PASSIVE_FILES)


def _load_declared_external(root):
    """(ids, problem). An absent register is the ordinary case and is not a problem.

    Only a MIGRATED folder has one -- `init_project.py new` does not create it -- and
    reporting its absence would put a warning on every clean project in the fleet.

    ABSENCE IS DECIDED BY ATTEMPTING THE READ, never by `is_file()`. That guard reported
    a DIRECTORY at the register's path as absent, so a present-but-unreadable register
    was silent -- found by cross-family review 2026-09-03, and NOT a hypothetical: a
    directory sitting where a data file belongs is a shape this corpus has produced
    before. Only FileNotFoundError means absent; every other OSError is reported.

    An UNREADABLE one is reported and suppresses nothing, which is the safe direction for a
    warning-only limb: the dangling list below is then complete rather than filtered, so the
    operator sees more than they asked for instead of less. Caught HERE rather than left to
    the caller's wrapper, because that wrapper says REFERENCE INTEGRITY NOT ESTABLISHED --
    false here, since the scan itself ran fine and only the exemption list did not.

    SCOPE, because the sentence above is easy to read wider than it is true: this
    function is called ONLY when something is already dangling. Review asked for it to
    be called before that early return so an unreadable register is reported even on a
    clean scan; DECLINED, and the reason is A14's own narrowing #1. The register can
    only ever REMOVE warnings, so a scan with nothing dangling is clean whether or not
    the register reads -- and a warning an author cannot act on is the noise C-44 and
    C-19 already ruled out of this limb.
    """
    path = root / A14_DECLARED_EXTERNAL
    try:
        return recall.load_ignore_file(path), None
    except FileNotFoundError:
        return set(), None                 # no register on this folder: the usual case
    except OSError as exc:                 # a DIRECTORY here lands HERE, not in absence
        return set(), (
            "DECLARED-EXTERNAL REGISTER UNREADABLE -- `%s` is present but could not be read (%s: %s). "
            "NOTHING was suppressed, so any DANGLING warning in this report may name an id already "
            "adjudicated external. The reference scan itself is unaffected and ran normally. Not an "
            "abort (C-53)." % (A14_DECLARED_EXTERNAL, type(exc).__name__, exc))


def collect_reference_warnings(root):
    """A14 -- every citation in a HOT file resolves to a definition. WARNING ONLY (C-53).

    THE BROAD `except Exception` IS THE POINT OF THIS WRAPPER AND IT IS NOT LAZINESS. A14 is
    warning-only, so ANY escape from it changes the exit code -- `main` turns an uncaught
    exception into exit 7, and a warning-only rule would then BLOCK A SAVE, which is the one
    thing C-53 forbids absolutely: "Warnings should be warning. Nothing should ever block a save.
    That will render the system unusable."

    The first version caught four types around `build_map` only, and everything after it -- the
    `mp[...]` accesses, `load_doc`, `scan_occurrences`, the report loop -- ran unguarded. Found by
    cross-family review (`gpt-5.6-sol`, 2026-08-29): the docstring's guarantee was broader than
    the code, which is this project's most-repeated defect shape, here in a limb written to end
    exactly that. A NARROW catch inside a warning-only rule is a latent abort.
    """
    try:
        return _reference_warnings(root)
    except Exception as exc:                                   # noqa: BLE001 -- see above
        return [Warning_("A14", None, STATE_FILE, 0,
                         "REFERENCE INTEGRITY NOT ESTABLISHED -- the scan raised %s: %s. This is "
                         "not a clean result: no citation was checked. Run `recall check "
                         "--root <folder>` by hand. Reported rather than raised BECAUSE it is "
                         "warning-only: an escape here would exit 7 and block the save (C-53)."
                         % (type(exc).__name__, exc))]


def _reference_warnings(root):
    """A14's body. Every exit from here is a list; `collect_reference_warnings` owns the guard."""
    out = []
    families, _problems = citemap.project_families(root)
    mp = citemap.build_map(root, citemap.SCOPES["c4"], families, tuple(PASSIVE_FILES))
    if mp["unterminated_fences"]:
        return [Warning_("A14", None, STATE_FILE, 0,
                         "REFERENCE INTEGRITY VOID -- %d file(s) in scope end with an OPEN fence, "
                         "so every heading below one is invisible and 'nothing dangles' is the "
                         "answer a truncated scan gives either way. Close the fence, then re-run. "
                         "Not an abort (C-53)." % len(mp["unterminated_fences"]))]

    # ONE definition of what is flagged, used by BOTH the site scan and the report. It was two
    # independent expressions of the same concept, which is not merely untidy: a mutant that
    # perturbed either one alone was INERT, so neither could be held by an arm. Collisions are
    # absent here by design -- see the block comment above.
    flagged = list(mp["dangling"])
    want = set(flagged)
    if not want:
        return out
    sites = {}
    for ident in want:
        sites[ident] = []
    for rel in A14_HOT_FILES:
        path = root / rel
        if not path.is_file():
            continue
        try:
            _data, rows, _open = recall.load_doc(path)
        except (IOError, OSError):
            continue
        for (lineno, text, _s, _e, in_fence) in rows:
            if in_fence:
                continue
            for (ident, _a, _b) in citemap.scan_occurrences(text, families):
                if ident in want:
                    sites[ident].append((rel, lineno))

    declared, declared_problem = _load_declared_external(root)
    suppressed = []
    for ident in flagged:
        hits = sites.get(ident) or []
        if not hits:
            continue                    # cold-only citer: history, and C-44 closes its repair
        if ident in declared:
            suppressed.append(ident)
            continue
        shown = hits[:A14_MAX_SITES]
        tail = ("" if len(hits) <= A14_MAX_SITES
                else " (+%d more hot site(s))" % (len(hits) - A14_MAX_SITES))
        out.append(Warning_(
            "A14", ident, shown[0][0], shown[0][1],
            "DANGLING: `%s` is cited from %d hot site(s) and defined NOWHERE in this project -- "
            "%s%s. A reader following it with `recall get %s` gets nothing. Either define it, "
            "or edit the citation to name something that resolves. An id cited only from "
            "`_archive/` or `checkpoints/` is history and C-44 closes it, so this warning fires "
            "ONLY on hot citers. Not an abort: the set is closed at A1..A8 (C-53)."
            % (ident, len(hits), ", ".join("%s:%d" % s for s in shown), tail, ident)))
    if suppressed:
        out.append(Warning_(
            "A14", None, A14_DECLARED_EXTERNAL, 0,
            "DECLARED EXTERNAL: %d hot-cited dangling id(s) are NOT reported as dangling, because `%s` "
            "declares them external -- %s. They are DECLARED, not resolvable: following one with "
            "`recall get` still returns nothing, and this note exists so that reads as a decision "
            "somebody recorded rather than as a clean scan. Whether each declaration is still "
            "needed is a different question and this limb does not answer it. Not an abort (C-53)."
            % (len(suppressed), A14_DECLARED_EXTERNAL, ", ".join(sorted(suppressed)))))
    if declared_problem:
        out.append(Warning_("A14", None, A14_DECLARED_EXTERNAL, 0, declared_problem))
    return out


def render_lifetime_resolutions(resolutions, err):
    if not resolutions:
        return
    err.write("\nT-2 LIFETIME RESOLUTION (%d) -- reported only; NO FILE WAS CHANGED.\n"
              % len(resolutions))
    err.write("  Two declared lifetimes resolve to the SHORTER one (expiring < conditional <\n"
              "  permanent). Taking the longer one would disarm expiry-driven demotion and keep\n"
              "  dead content hot -- the growth this phase exists to stop.\n")
    for (ent, chosen, lifes) in resolutions:
        err.write("  %s:%d  [%s]  %s -> `%s`\n"
                  % (ent.rel, ent.line, ent.ident,
                     ", ".join("`%s` (line %d)" % (t, ln) for (t, ln) in lifes), chosen))


# --------------------------------------------------------------------------- scanning

class Scan(object):
    """Everything one invocation produced. The report renderer's only input."""

    __slots__ = ("checked", "skipped", "entries", "violations", "warnings",
                 "resolutions", "unreliable", "current_save", "save_source")

    def __init__(self):
        self.checked = []          # [(rel, n_entries, n_sections)]
        self.skipped = []          # [(rel, reason)]
        self.entries = 0
        self.violations = []
        self.warnings = []
        self.resolutions = []
        self.unreliable = False    # at least one file in scope ended with an OPEN fence
        self.current_save = None
        self.save_source = "unknown"


def resolve_current_save(root, override, scan):
    """(save_number, source). --save wins; else `state.md`'s own `save:` line; else (None, ...).

    A6's freshness ground needs a notion of "now". It is not invented: an unknown save# makes
    that ground SKIP with a per-entry warning rather than pass or fail everything. `state.md` is
    the generated file that records the save counter (S3.5), so it is the natural source, and
    --save exists because a validator may run before state.md is regenerated.

    THIS IS A READ PATH, so it carries the integrity obligation (R-E) exactly as the other two
    do. It did not, and that was a shipped defect: the third read site was missed when the guard
    was swept to the first two. Measured 2026-08-03 -- `state.md` whose `save:` line sits behind
    an unterminated fence, scanned as `--file active.md` (which drops state.md from the entry
    scan set, so `scan_file`'s guard never sees it): the save# resolved to "unknown", A6's
    renewal ground silently returned [], and the tool printed "ABORT SET (0)" and exited 0 over a
    real abort. It also printed "no `save:` line in state.md" -- a false statement about a file
    that has one, on line 4. Reporting the EFFECT as the CAUSE is how an unswept read path hides.

    The integrity check runs even when --save overrides the VALUE: `--save` supplies a number,
    not evidence that state.md was read whole. And an unreadable state.md now RAISES (exit 2)
    like `load_expired_implications`, instead of degrading to a silent skip -- two auxiliary
    reads in one file may not disagree about what an I/O error means.
    """
    path = root / "state.md"
    if not path.is_file():
        return (override, "--save") if override is not None else (None, "unknown (no state.md)")
    try:
        _data, rows, open_line = recall.load_doc(path)
    except (IOError, OSError) as exc:
        raise ValueError("cannot read state.md: %s" % exc)
    if recall.warn_open_fence("state.md", open_line):
        scan.unreliable = True
    if override is not None:
        return override, "--save"
    for (_ln, text, _s, _e, in_fence) in rows:
        if in_fence:
            continue
        m = FIELD_RE.match(text)
        if not m or m.group(1).lower() not in ("save", "save-number", "save_no"):
            continue
        n = SAVE_NUM_RE.search(m.group(2))
        if n:
            return int(n.group(1)), "state.md"
    return None, "unknown (no `save:` line in state.md)"


def load_expired_implications(root, scan):
    """{normalised implication: (rel, line)} from `_archive/active-expired.md`. A6's corpus.

    Absent file -> empty map, and A6's renewal ground simply never fires. That is correct: with
    no expired archive there is nothing to renew. It is reported in the report's SOURCES block
    so the operator can see the check ran against an empty corpus rather than assume it passed.
    """
    path = root / EXPIRED_FILE
    if not path.is_file():
        return {}, False
    try:
        _data, rows, open_line = recall.load_doc(path)
    except (IOError, OSError) as exc:
        raise ValueError("cannot read %s: %s" % (EXPIRED_FILE, exc))
    if recall.warn_open_fence(EXPIRED_FILE, open_line):
        scan.unreliable = True
    entries, _sections, _shape = parse_entries(EXPIRED_FILE, rows)
    attach_fields(entries, rows)
    out = {}
    for ent in entries:
        key = _normalise_implication(ent)
        if key and key not in out:
            out[key] = (EXPIRED_FILE, ent.line)
    return out, True


def scan_file(rel, path, scan, expired_implications):
    """Read one in-table file and run every rule over it. Integrity is recorded, never swallowed."""
    try:
        _data, rows, open_line = recall.load_doc(path)
    except (IOError, OSError) as exc:
        raise ValueError("cannot read %s: %s" % (rel, exc))
    if recall.warn_open_fence(rel, open_line):
        scan.unreliable = True

    file_kind, _file_life = FILE_KIND[rel]
    exempt = rel in DECLARATION_EXEMPT

    entries, sections, shape_warnings = parse_entries(rel, rows)
    attach_fields(entries, rows)

    for (lineno, why) in shape_warnings:
        scan.warnings.append(Warning_("SHAPE", "", rel, lineno, why))

    # A non-generated in-table file with real content and NO entry is the wrong-verdict shape
    # this file's INVARIANT names: nothing was checked, and "ABORT SET (0)" is a universal
    # quantifier printed over an empty set. Measured at exit 0 on level-1 headings, on setext
    # headings, on indented ATX, and on a 200-line prose file. It cannot be an abort -- the set
    # is CLOSED at A1..A8 -- so it is a named warning, which is the difference between an
    # operator who knows and one who does not. `state.md`/`manifest.md` legitimately have none.
    if not entries and not exempt:
        substantive = [ln for (ln, text, _s, _e, fenced) in rows
                       if not fenced and text.strip() and not text.strip().startswith("#")]
        if substantive:
            scan.warnings.append(Warning_(
                "SHAPE", "", rel, substantive[0],
                "NO ENTRY was parsed from %s, which holds %d non-blank content line(s). Nothing "
                "in this file was checked by A1..A8 and its absence from the abort set means "
                "nothing. An entry is an ATX heading at column 0, level >= 2 (`## <id> - "
                "<title>`); level-1, indented and setext headings do not open one."
                % (rel, len(substantive))))

    if rel == ACTIVE_FILE:
        regions, region_warnings = find_unresolved_regions(rows)
        for (ent, why) in mark_unresolved(entries, regions):
            scan.warnings.append(Warning_("REGION", ent.ident, rel, ent.line, why))
        for (lineno, msg) in region_warnings:
            scan.warnings.append(Warning_("REGION", "", rel, lineno, msg))
    else:
        # The carve-out is scoped to active.md by S2.4 T-3. A marker anywhere else exempts
        # nothing, and saying so is cheaper than letting an author believe it worked.
        for (lineno, text, _s, _e, in_fence) in rows:
            if not in_fence and UNRESOLVED_OPEN_RE.match(text):
                scan.warnings.append(Warning_(
                    "REGION", "", rel, lineno,
                    "`<!-- UNRESOLVED -->` in %s exempts nothing. The carve-out is defined for "
                    "active.md only (S2.4 T-3); every entry in this file was checked in full."
                    % rel))

    # A9..A12 need each entry's exact BYTE span, which the Entry model does not carry. The rows
    # do: every row is (lineno, text, byte_start, byte_end, in_fence). Built ONCE per file rather
    # than rescanned per entry.
    line_spans = dict((ln, (s, e)) for (ln, _t, s, e, _f) in rows)

    for ent in entries:
        scan.violations.extend(rule_a1_type(ent, file_kind, exempt, rel))
        scan.violations.extend(rule_a2_kind(ent, exempt))
        scan.violations.extend(rule_a3_lifetime(ent, exempt))
        scan.violations.extend(rule_a4_unresolved(ent))
        if rel == ACTIVE_FILE:
            scan.violations.extend(rule_a5_expiry(ent, scan.current_save))
            scan.violations.extend(rule_a6_provenance(
                ent, expired_implications, scan.current_save))
        scan.violations.extend(rule_a7_gate(ent, rel))
        scan.violations.extend(rule_a8_multi_kind(ent, exempt))
        scan.warnings.extend(collect_warnings(ent, rel, scan.current_save))
        scan.warnings.extend(collect_content_warnings(ent, rel, line_spans))

    scan.warnings.extend(validate_split(entries))
    # A13 is FILE-level, so it runs outside the per-entry loop. It self-scopes to `state.md`; the
    # call is unconditional so that adding a second in-scope file later is a one-line change to
    # STATE_PROSE_FIELDS' guard rather than a second call site to remember.
    scan.warnings.extend(collect_state_field_warnings(rel, rows))
    scan.resolutions.extend(resolve_lifetimes(entries))
    scan.checked.append((rel, len(entries), len(sections)))
    scan.entries += len(entries)


def run_scan(root, patterns, only):
    """Discover, classify, and check. Returns a Scan. Reads only; writes nothing (R-D)."""
    scan = Scan()
    scan.current_save, scan.save_source = resolve_current_save(root, only.get("save"), scan)
    expired_implications, _present = load_expired_implications(root, scan)

    files = citemap.collect_files(root, patterns)
    wanted = only.get("files")
    for (rel, path) in files:
        if wanted is not None and rel not in wanted:
            continue
        if rel in PASSIVE_FILES:
            scan.skipped.append((rel, "host-owned Codex instructions -- restored and counted, "
                                      "but never validated or reference-scanned by save"))
            continue
        if rel == EXPIRED_FILE:
            scan.skipped.append((rel, "A6's renewal corpus, not an entry file"))
            continue
        if rel not in FILE_KIND:
            scan.skipped.append((rel, "not in the S3.1/S3.2 file->kind table -- OUT OF SCOPE, "
                                      "kind never guessed"))
            continue
        scan_file(rel, path, scan, expired_implications)

    if wanted is not None:
        seen = set(rel for (rel, _n, _sec) in scan.checked) | set(r for (r, _m) in scan.skipped)
        for rel in wanted:
            if rel not in seen:
                scan.skipped.append((rel, "no such file under --root"))
    # A14 is SCAN-level, not file-level: reference integrity is a property of the corpus, and a
    # per-file call could only ever ask "does this file define what it cites", which is a
    # different and useless question. Skipped under `--file`, because a citation is dangling
    # relative to every DEFINITION in the project and a narrowed run has not read them.
    if wanted is None:
        scan.warnings.extend(collect_reference_warnings(root))
    else:
        scan.skipped.append(("A14 reference integrity",
                             "skipped under --file: a dangling verdict needs the whole corpus"))
    return scan


# --------------------------------------------------------------------------- report

RULE_TEXT = {
    "A1": "TYPE VIOLATION -- entry kind != file kind (S5.2)",
    "A2": "no declared kind, or an unrecognised one (S2.4)",
    "A3": "no declared lifetime, or an unrecognised one (S2.4)",
    "A4": "`unresolved` with < 2 candidates, or no expiry (S2.4 T-3)",
    "A5": "active.md expiry missing, unbounded, or > 10 saves (S6 r1)",
    "A6": "active.md provenance / renewal without a fresh quote (S6 r3-r4)",
    "A7": "deferred-archive entry with no `gate:` (S7.1)",
    "A8": "more than one kind declared, outside UNRESOLVED (T-1)",
}


def render_report(scan, root, report_mode, out):
    """The operator-facing report. Every violation names rule + file:line + entry id.

    A bare count is not a report: "3 violations" tells an operator that something is wrong and
    nothing about where, which is the same as telling them nothing. The counts at the bottom are
    a summary OF the named list above them, never a substitute for it.
    """
    out.write("validate_entries -- save v4 step 7d VALIDATE\n")
    out.write("root: %s\n" % root.as_posix())
    out.write("current save#: %s (source: %s)\n"
              % (scan.current_save if scan.current_save is not None else "unknown",
                 scan.save_source))
    out.write("\nCHECKED (%d file%s, %d entr%s)\n"
              % (len(scan.checked), "" if len(scan.checked) == 1 else "s",
                 scan.entries, "y" if scan.entries == 1 else "ies"))
    if not scan.checked:
        out.write("  (none -- no file under --root matched the S3.1/S3.2 table)\n")
    for (rel, n, nsec) in scan.checked:
        kind, life = FILE_KIND[rel]
        out.write("  %-38s kind=%-10s lifetime=%-12s %3d entries%s%s\n"
                  % (rel, kind, life if life else "(per-entry)", n,
                     ("  +%d section banner%s" % (nsec, "" if nsec == 1 else "s")) if nsec else "",
                     "   [no declaration required -- A1/A2/A3/A8 exempt]" if rel in DECLARATION_EXEMPT else ""))

    if scan.skipped:
        out.write("\nSKIPPED (%d)\n" % len(scan.skipped))
        for (rel, why) in scan.skipped:
            out.write("  %-38s %s\n" % (rel, why))

    if scan.violations:
        out.write("\nABORT SET (%d)\n" % len(scan.violations))
        order = sorted(scan.violations, key=lambda v: (v.rule, v.rel, v.line))
        for v in order:
            out.write("  %s\n" % v.render())
        out.write("\n  by rule:\n")
        counts = {}
        for v in scan.violations:
            counts[v.rule] = counts.get(v.rule, 0) + 1
        for rule in sorted(counts):
            out.write("    %s  %4d  %s\n" % (rule, counts[rule], RULE_TEXT.get(rule, "")))
    else:
        # Deliberately scoped to the entries that were actually parsed. The old wording -- "every
        # entry declares a kind and a lifetime its file allows" -- is a universal quantifier, and
        # over an empty entry set it is vacuously true and reads to an operator as a pass.
        out.write("\nABORT SET (0) -- no A1..A8 violation among the %d entr%s parsed above.%s\n"
                  % (scan.entries, "y" if scan.entries == 1 else "ies",
                     "" if scan.entries else
                     " NOTE: that is ZERO entries -- see the SHAPE warnings; nothing was checked."))

    if scan.warnings:
        out.write("\nWARNINGS (%d) -- NOT aborts; the exit code does not move for these\n"
                  % len(scan.warnings))
        for w in sorted(scan.warnings, key=lambda w: (w.kind, w.rel, w.line)):
            out.write("  %s\n" % w.render())

    if scan.resolutions:
        out.write("\nT-2 LIFETIME RESOLUTIONS (%d) -- reported only; NO FILE WAS CHANGED\n"
                  % len(scan.resolutions))
        for (ent, chosen, lifes) in scan.resolutions:
            out.write("  %s:%d  [%s]  %s -> `%s`\n"
                      % (ent.rel, ent.line, ent.ident,
                         ", ".join("`%s` (line %d)" % (t, ln) for (t, ln) in lifes), chosen))

    out.write("\nSUMMARY: %d entries, %d aborts, %d warnings, %d lifetime resolutions.\n"
              % (scan.entries, len(scan.violations), len(scan.warnings),
                 len(scan.resolutions)))
    if scan.unreliable:
        out.write("SCAN INTEGRITY: at least one file in scope ends with an OPEN code fence. "
                  "This report is computed from a TRUNCATED scan and is not evidence "
                  "(exit %d).\n" % EXIT_SCAN_INTEGRITY)
    if report_mode:
        out.write("MODE: --report (migration, spec S9 step 6). The abort set above is "
                  "PUBLISHED, not enforced; the exit code is %d unless scan integrity "
                  "overrides it.\n" % EXIT_OK)
    out.write("NOTE: this tool wrote no file. It never does (R-D).\n")


# --------------------------------------------------------------------------- selftest
#
# The suite is IN-FILE and behind `--selftest`, and it is the only invocation that may return
# EXIT_SELFTEST (1). Every fixture is written under a `tempfile.mkdtemp` and removed in a
# `finally`: nothing is written under the project folder and nothing is left behind (R-D binds
# the tool; the suite honours the same rule for its own artifacts).
#
# WHY EACH ARM EXISTS. A checker never observed FAILING is not known to check anything -- so
# every member of the closed abort set A1..A8 gets a BROKEN fixture that must abort AND be named
# in the report. And a checker never observed PASSING is worse than none -- an over-eager
# validator that aborts real saves gets turned off, and then nothing is checked at all -- so
# every member also gets a CLEAN control that must exit 0. The false-positive half is the
# expensive one to build and the one that decides whether this tool survives contact.

def _st_write(root, rel, text, eol="\n", bom=False):
    """Write a fixture with EXACTLY the terminator asked for. Bytes, never text mode.

    Python's text mode is a NORMALISER: it writes `os.linesep`, so an LF fixture becomes CRLF on
    this box and the CRLF arm would silently test nothing (it would pass either way). Written as
    bytes, the arm actually distinguishes the two.
    """
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = text.replace("\r\n", "\n")
    if eol != "\n":
        body = body.replace("\n", eol)
    data = body.encode("utf-8")
    if bom:
        data = citemap.BOM.encode("utf-8") + data
    path.write_bytes(data)
    return path


def _st_project(tmp, name):
    root = tmp / name
    root.mkdir(parents=True, exist_ok=True)
    return root


# Every exit code the suite observed, in order. `_st_arms_invariants` asserts over the whole
# set at the end: 1 is RESERVED for --selftest itself, so a normal invocation returning it would
# make a broken tool indistinguishable from a failed suite, and 7 anywhere means an arm raised.
_ST_CODES = []


def _st_run(argv):
    """(code, stdout_text, stderr_text). Drives main() IN-PROCESS -- the assertion is on the
    returned code itself, not on a subprocess's shell-mangled status.

    stderr is swapped for a buffer so the suite's own output stays readable and so an arm can
    assert on the SCAN INTEGRITY warning. An exception is captured rather than propagated: one
    broken arm must not take the other fifty down with it, and it is recorded as EXIT_INTERNAL
    (7) so it shows up as a failure rather than as silence.
    """
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


# One rendered abort-set line: two leading spaces, rule, file:line, [id]. The `by rule:` summary
# block is indented FOUR spaces and therefore cannot match -- a count must never be mistaken for
# a named finding, which is the defect the CONTRACT at the top of this file names.
_ST_FINDING_RE = re.compile(r"^ {2}(A[1-8]) {2}(\S+):(\d+) {2}\[([^\]]*)\]")


def _st_findings(out_text):
    """[(rule, rel, line, ident)] parsed back OUT of the rendered report.

    Parsed from the report rather than read off the Scan object on purpose: the contract is that
    the operator can act on what is PRINTED, so the arms assert against the printed form.
    """
    found = []
    for line in out_text.splitlines():
        m = _ST_FINDING_RE.match(line)
        if m:
            found.append((m.group(1), m.group(2), int(m.group(3)), m.group(4)))
    return found


def _st_rules(out_text):
    return sorted(set(f[0] for f in _st_findings(out_text)))


def _st_ids(out_text):
    return sorted(set(f[3] for f in _st_findings(out_text)))


# --------------------------------------------------------------------------- arms: acceptance

def _st_arms_acceptance(ck, tmp):
    """The plan's own DONE-WHEN, all three arms."""
    # (i) a POISONED architecture.md ABORTS.
    root = _st_project(tmp, "acc_poison")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D1 · The broker owns delivery\n"
              "kind: rules\n"
              "lifetime: conditional\n"
              "\n"
              "The broker is the only writer of the delivery table.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("acc-i  poisoned architecture.md -> exit 3", code, EXIT_ABORT)
    ck("acc-i  names rule A1", _st_rules(out), ["A1"])
    ck("acc-i  names the entry id", _st_ids(out), ["D1"])

    # (ii) a CLEAN control does NOT abort. THE ARM THAT MATTERS: an over-eager validator that
    # aborts real saves is worse than none.
    root = _st_project(tmp, "acc_clean")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D1 · The broker owns delivery\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "The broker is the only writer of the delivery table.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("acc-ii clean architecture.md -> exit 0", code, EXIT_OK)
    ck("acc-ii clean architecture.md -> no findings", _st_findings(out), [])
    ck("acc-ii the entry WAS parsed (1 entry checked)", "1 entries" in out, True)

    # (iii) an UNRESOLVED region is EXEMPT.
    root = _st_project(tmp, "acc_unresolved")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "<!-- UNRESOLVED -->\n"
              "\n"
              "## U1 · Is the delivery rule a rule or a spec\n"
              "kind: unresolved\n"
              "candidates: rules, spec\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "\n"
              "Undecided at save 14.\n"
              "\n"
              "<!-- /UNRESOLVED -->\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("acc-iii UNRESOLVED region exempt -> exit 0", code, EXIT_OK)
    ck("acc-iii UNRESOLVED region exempt -> no findings", _st_findings(out), [])


# --------------------------------------------------------------------------- arms: A1 .. A4

def _st_arms_a1_a4(ck, tmp):
    """One BROKEN fixture and one CLEAN control per rule. Both halves, every rule."""
    # ---- A1 TYPE VIOLATION, second shape: a `plan` entry in design.md (`spec`).
    root = _st_project(tmp, "a1_broken")
    _st_write(root, "design.md",
              "# Design\n"
              "\n"
              "## D2 · Ship the broker pilot in October\n"
              "kind: plan\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("A1 broken  plan entry in design.md -> exit 3", code, EXIT_ABORT)
    ck("A1 broken  rule set", _st_rules(out), ["A1"])
    ck("A1 broken  id named", _st_ids(out), ["D2"])
    ck("A1 broken  message names both kinds",
       "`kind: plan`" in out and "`spec`" in out, True)

    root = _st_project(tmp, "a1_clean")
    _st_write(root, "design.md",
              "# Design\n"
              "\n"
              "## D2 · The retrieval layer is a spec\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("A1 clean   spec entry in design.md -> exit 0", code, EXIT_OK)
    ck("A1 clean   no findings", _st_findings(out), [])

    # ---- A2 no declared kind / unrecognised kind.
    root = _st_project(tmp, "a2_missing")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D3 · The delivery table\n"
              "lifetime: conditional\n"
              "\n"
              "Body with no kind declaration at all.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("A2 broken  no `kind:` -> exit 3", code, EXIT_ABORT)
    ck("A2 broken  rule set", _st_rules(out), ["A2"])
    ck("A2 broken  id named", _st_ids(out), ["D3"])

    root = _st_project(tmp, "a2_bogus")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D4 · The delivery table\n"
              "kind: guideline\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("A2 broken  unrecognised kind -> exit 3", code, EXIT_ABORT)
    # A1 must NOT double-report an unrecognised token: A2 owns the vocabulary.
    ck("A2 broken  rule set (A1 does not double-report)", _st_rules(out), ["A2"])
    ck("A2 broken  the bad value is quoted back", "'guideline'" in out, True)

    root = _st_project(tmp, "a2_clean")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D5 · The delivery table\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("A2 clean   declared kind -> exit 0", code, EXIT_OK)
    ck("A2 clean   no findings", _st_findings(out), [])

    # ---- A3 no declared lifetime / unrecognised lifetime.
    root = _st_project(tmp, "a3_missing")
    _st_write(root, "CONSTRAINTS.md",
              "# Constraints\n"
              "\n"
              "## C1 · No remote, ever\n"
              "kind: rules\n"
              "\n"
              "Body with no lifetime declaration.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("A3 broken  no `lifetime:` -> exit 3", code, EXIT_ABORT)
    ck("A3 broken  rule set", _st_rules(out), ["A3"])
    ck("A3 broken  id named", _st_ids(out), ["C1"])

    root = _st_project(tmp, "a3_bogus")
    _st_write(root, "CONSTRAINTS.md",
              "# Constraints\n"
              "\n"
              "## C2 · No remote, ever\n"
              "kind: rules\n"
              "lifetime: for now\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("A3 broken  `for now` is not a lifetime -> exit 3", code, EXIT_ABORT)
    ck("A3 broken  rule set (bogus lifetime)", _st_rules(out), ["A3"])
    ck("A3 broken  the bad value is quoted back", "'for now'" in out, True)

    root = _st_project(tmp, "a3_clean")
    _st_write(root, "CONSTRAINTS.md",
              "# Constraints\n"
              "\n"
              "## C3 · No remote, ever\n"
              "kind: rules\n"
              "lifetime: permanent\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("A3 clean   declared lifetime -> exit 0", code, EXIT_OK)
    ck("A3 clean   no findings", _st_findings(out), [])

    # ---- A4 `unresolved` with < 2 candidates, or with no expiry. The region is a DEADLINE, not
    # a hiding place: A4 fires INSIDE it.
    root = _st_project(tmp, "a4_one_candidate")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "<!-- UNRESOLVED -->\n"
              "\n"
              "## U2 · Delivery ordering\n"
              "kind: unresolved\n"
              "candidates: rules\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "\n"
              "Body.\n"
              "\n"
              "<!-- /UNRESOLVED -->\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("A4 broken  one candidate, inside the region -> exit 3", code, EXIT_ABORT)
    ck("A4 broken  rule set (one candidate)", _st_rules(out), ["A4"])
    ck("A4 broken  id named", _st_ids(out), ["U2"])

    root = _st_project(tmp, "a4_no_expiry")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "<!-- UNRESOLVED -->\n"
              "\n"
              "## U3 · Delivery ordering\n"
              "kind: unresolved\n"
              "candidates: rules, spec\n"
              "lifetime: expiring\n"
              "\n"
              "Body.\n"
              "\n"
              "<!-- /UNRESOLVED -->\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("A4 broken  no expiry -> exit 3", code, EXIT_ABORT)
    # A5 fires too and that is correct: active.md's own expiry rule is not suspended by the
    # carve-out either. Asserting the exact SET documents which rules own which half.
    ck("A4 broken  rule set (no expiry -> A4 + A5)", _st_rules(out), ["A4", "A5"])

    root = _st_project(tmp, "a4_clean")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "<!-- UNRESOLVED -->\n"
              "\n"
              "## U4 · Delivery ordering\n"
              "kind: unresolved\n"
              "candidates: rules, spec\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "\n"
              "Body.\n"
              "\n"
              "<!-- /UNRESOLVED -->\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("A4 clean   two candidates + expiry -> exit 0", code, EXIT_OK)
    ck("A4 clean   no findings", _st_findings(out), [])


# --------------------------------------------------------------------------- arms: A5 .. A8

# The CLEAN active.md entry every A5/A6 arm mutates one line of. It passes every rule, so any
# finding an arm produces is attributable to the single line that arm changed.
_ST_ACTIVE_CLEAN = (
    "# Active\n"
    "\n"
    "## AC1 · Restart the broker before each deploy\n"
    "kind: rules\n"
    "lifetime: expiring\n"
    "expires: 3 saves\n"
    "save: 14\n"
    "quote: \"restart the broker before each deploy\" (save 14)\n"
    "\n"
    "Body.\n"
)


def _st_active(**swap):
    """The clean active.md with named lines replaced or dropped (value None drops the line)."""
    out = []
    for line in _ST_ACTIVE_CLEAN.split("\n"):
        key = line.split(":", 1)[0].strip().lower() if ":" in line else None
        if key in swap:
            new = swap[key]
            if new is None:
                continue
            out.append(new)
            continue
        out.append(line)
    return "\n".join(out)


def _st_arms_a5_a8(ck, tmp):
    # ---- the control FIRST: if this does not pass, no A5/A6 arm below proves anything.
    root = _st_project(tmp, "a5_clean")
    _st_write(root, "active.md", _ST_ACTIVE_CLEAN)
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("A5 clean   bounded expiry -> exit 0", code, EXIT_OK)
    ck("A5 clean   no findings", _st_findings(out), [])
    ck("A5 clean   no warnings either", "WARNINGS (" in out, False)

    # ---- A5 unbounded / over-max / missing / event-shaped.
    for (name, swapped, label) in (
        ("a5_never", {"expires": "expires: never"}, "unbounded `never`"),
        ("a5_twenty", {"expires": "expires: 20 saves"}, "20 saves > max 10"),
        ("a5_missing", {"expires": None}, "no `expires:` at all"),
        ("a5_event", {"expires": "expires: when the broker pilot lands"}, "event-shaped"),
        ("a5_horizon", {"expires": "expires: save 40"}, "26 saves past the asserted save#"),
    ):
        root = _st_project(tmp, name)
        _st_write(root, "active.md", _st_active(**swapped))
        code, out, _err = _st_run(["--root", str(root), "--save", "14"])
        ck("A5 broken  %s -> exit 3" % label, code, EXIT_ABORT)
        ck("A5 broken  %s -> rule set" % label, _st_rules(out), ["A5"])
        ck("A5 broken  %s -> id named" % label, _st_ids(out), ["AC1"])

    # An absolute expiry with NO `save:` field still has a horizon: the entry's provenance quote
    # carries one, and failing that the CURRENT save# does. This used to `return []` and pass at
    # exit 0 -- `expires: save 900` at save 14 is 886 saves out against a stated maximum of 10,
    # and the SKIPPED warning it printed was a working recipe for unbounded hot content. The
    # current-save base is strictly conservative (an entry is asserted at or before now).
    root = _st_project(tmp, "a5_base_current")
    _st_write(root, "active.md", _st_active(**{"expires": "expires: save 40", "save": None,
                                               "quote": None}))
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("A5 base    no `save:` -> the CURRENT save# is the horizon base", code, EXIT_ABORT)
    ck("A5 base    ...and A5 is what fired", "A5" in _st_rules(out), True)
    ck("A5 base    the message names the base it used", "CURRENT save#" in out, True)

    root = _st_project(tmp, "a5_base_quote")
    _st_write(root, "active.md", _st_active(**{"expires": "expires: save 40", "save": None}))
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("A5 base    the provenance quote's save# is a base too", code, EXIT_ABORT)
    ck("A5 base    ...it outranks the current save#", "provenance quote" in out, True)

    # The ONLY genuinely-uncomputable case: no field, no quote, no --save, no state.md. It must
    # SKIP with a named warning, never pass silently -- a check that degrades to "pass" is the
    # exit-6 defect in miniature.
    root = _st_project(tmp, "a5_no_base")
    _st_write(root, "active.md", _st_active(**{"expires": "expires: save 40", "save": None,
                                               "quote": "quote: \"do the thing\""}))
    code, out, _err = _st_run(["--root", str(root)])
    ck("A5 skip    no base ANYWHERE -> A5 skips, not aborts", "A5" in _st_rules(out), False)
    ck("A5 skip    the skip is REPORTED", "was SKIPPED" in out, True)
    ck("A5 skip    the current save# is reported unknown", "current save#: unknown" in out, True)

    # ---- A6 provenance: no quote / a quote with no save# / a stale renewal.
    root = _st_project(tmp, "a6_no_quote")
    _st_write(root, "active.md", _st_active(**{"quote": None}))
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("A6 broken  no `quote:` -> exit 3", code, EXIT_ABORT)
    ck("A6 broken  no `quote:` -> rule set", _st_rules(out), ["A6"])
    ck("A6 broken  no `quote:` -> id named", _st_ids(out), ["AC1"])

    root = _st_project(tmp, "a6_no_save")
    _st_write(root, "active.md",
              _st_active(**{"quote": "quote: \"restart the broker before each deploy\"",
                            "save": None}))
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("A6 broken  quote with no save# -> exit 3", code, EXIT_ABORT)
    ck("A6 broken  quote with no save# -> rule set", _st_rules(out), ["A6"])

    root = _st_project(tmp, "a6_stale_renewal")
    # `save: 14` is the CURRENT save and is load-bearing: ground (c) evaluates only on the save an
    # entry declares it was newly asserted in (`A >= C`). This entry says "I am being asserted
    # now" and its quote says the user last spoke at save 9 -- which is exactly the stale renewal.
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## AC2 · Restart the broker before each deploy\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "save: 14\n"
              "implication: broker restarted before each deploy\n"
              "quote: \"restart the broker before each deploy\" (save 9)\n"
              "\n"
              "Body.\n")
    _st_write(root, "_archive/active-expired.md",
              "# Expired\n"
              "\n"
              "## AX1 · Restart the broker before each deploy\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "implication: broker restarted before each deploy\n"
              "\n"
              "Expired at save 12.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("A6 broken  stale renewal -> exit 3", code, EXIT_ABORT)
    ck("A6 broken  stale renewal -> rule set", _st_rules(out), ["A6"])
    ck("A6 broken  stale renewal names the expired source",
       "_archive/active-expired.md:3" in out, True)
    ck("A6 broken  the expired archive is SKIPPED, not entry-validated",
       "A6's renewal corpus" in out, True)

    # The same renewal WITH a fresh quote. This is the arm that stops A6 aborting every
    # legitimate re-assertion -- the false-positive half of the renewal rule.
    root = _st_project(tmp, "a6_fresh_renewal")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## AC2 · Restart the broker before each deploy\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "save: 14\n"
              "implication: broker restarted before each deploy\n"
              "quote: \"restart the broker before each deploy\" (save 14)\n"
              "\n"
              "Body.\n")
    _st_write(root, "_archive/active-expired.md",
              "# Expired\n"
              "\n"
              "## AX1 · Restart the broker before each deploy\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "implication: broker restarted before each deploy\n"
              "\n"
              "Expired at save 12.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("A6 clean   renewal WITH a fresh quote -> exit 0", code, EXIT_OK)
    ck("A6 clean   renewal WITH a fresh quote -> no findings", _st_findings(out), [])

    # ---- A7 a deferred-archive entry with no promotion gate. Both gate files.
    for (name, rel, ident) in (
        ("a7_backlog", "_archive/backlog-deferred.md", "P1"),
        ("a7_oq", "_archive/open-questions-deferred.md", "OQ1"),
    ):
        root = _st_project(tmp, name)
        _st_write(root, rel,
                  "# Deferred\n"
                  "\n"
                  "## %s · Rebuild the retrieval index\n"
                  "kind: plan\n"
                  "lifetime: conditional\n"
                  "\n"
                  "Body.\n" % ident)
        code, out, _err = _st_run(["--root", str(root)])
        ck("A7 broken  %s with no `gate:` -> exit 3" % rel, code, EXIT_ABORT)
        ck("A7 broken  %s -> rule set" % rel, _st_rules(out), ["A7"])
        ck("A7 broken  %s -> id named" % rel, _st_ids(out), [ident])

    root = _st_project(tmp, "a7_clean")
    _st_write(root, "_archive/backlog-deferred.md",
              "# Deferred\n"
              "\n"
              "## P1 · Rebuild the retrieval index\n"
              "kind: plan\n"
              "lifetime: conditional\n"
              "gate: when the broker pilot lands\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("A7 clean   gate declared -> exit 0", code, EXIT_OK)
    ck("A7 clean   no findings", _st_findings(out), [])

    # ---- A8 two kinds on one entry, outside the region.
    root = _st_project(tmp, "a8_broken")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D6 · The broker owns delivery and we ship it in October\n"
              "kind: spec, rules\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("A8 broken  two kinds -> exit 3", code, EXIT_ABORT)
    # A1 fires too, necessarily: of two kinds at most one can be the file's.
    ck("A8 broken  rule set (A1 + A8)", _st_rules(out), ["A1", "A8"])
    ck("A8 broken  both kind tokens are named", "`spec` (line 4)" in out
       and "`rules` (line 4)" in out, True)
    ck("A8 broken  the remedy is stated, and it is not a rewrite",
       "Split into two entries" in out, True)

    root = _st_project(tmp, "a8_clean")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D6 · The broker owns delivery\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("A8 clean   one kind -> exit 0", code, EXIT_OK)
    ck("A8 clean   no findings", _st_findings(out), [])

    # A8 is the rule the UNRESOLVED region exists to defer -- for an entry that DECLARES
    # `kind: unresolved`. That is the T-3 escape hatch and it must not abort.
    root = _st_project(tmp, "a8_region")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "<!-- UNRESOLVED -->\n"
              "\n"
              "## U5 · Delivery ordering\n"
              "kind: unresolved\n"
              "candidates: rules, spec\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "\n"
              "Body.\n"
              "\n"
              "<!-- /UNRESOLVED -->\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("A8 exempt  declared-unresolved INSIDE the region -> exit 0", code, EXIT_OK)
    ck("A8 exempt  no findings", _st_findings(out), [])

    # ...and the half that was missing. The region is a DEADLINE, not a hiding place: an entry
    # that does NOT declare `kind: unresolved` gets no exemption from it. Measured before the
    # fix: `kind: record` + `lifetime: permanent` + `expires: save 999999` + no quote, placed
    # between the markers, gave ABORT SET (0), ZERO warnings and exit 0. Region membership cost
    # nothing and bought exemption from A1, A6 and A8 at once.
    root = _st_project(tmp, "a8_region_amnesty")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "<!-- UNRESOLVED -->\n"
              "\n"
              "## U6 · permanent hot content, wrong kind, no provenance\n"
              "kind: record\n"
              "lifetime: permanent\n"
              "expires: save 999999\n"
              "\n"
              "Body.\n"
              "\n"
              "<!-- /UNRESOLVED -->\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("region     in-region entry NOT declaring unresolved -> exit 3", code, EXIT_ABORT)
    ck("region     ...A1, A5 and A6 all still apply", _st_rules(out), ["A1", "A5", "A6"])
    ck("region     ...and the operator is told why it was not exempt",
       "does not declare `kind: unresolved`" in out, True)
    root = _st_project(tmp, "a8_region_two_kinds")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "<!-- UNRESOLVED -->\n"
              "\n"
              "## U7 · Delivery ordering\n"
              "kind: rules, spec\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "quote: \"decide the ordering\" (save 14)\n"
              "\n"
              "<!-- /UNRESOLVED -->\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("region     two kinds in-region WITHOUT `kind: unresolved` -> A8 fires",
       "A8" in _st_rules(out), True)


# --------------------------------------------------------------------------- arms: fences

# Everything above the fence is a CLEAN entry, so the only thing an arm's verdict can be about
# is the fence. The A1 poison is added separately for the ordering arm.
_ST_FENCE_HEAD = (
    "# Architecture\n"
    "\n"
    "## D7 · The delivery table\n"
    "kind: spec\n"
    "lifetime: conditional\n"
    "\n"
)


def _st_arms_fences(ck, tmp):
    """Exit 6 outranks everything, and all three fence shapes that evaded an earlier fix.

    An unterminated fence makes every heading below it invisible, so the file contributes
    nothing and a checker that fails toward "nothing here" reports PASS over a file it never
    read. 0 and 3 are claims about a corpus that was READ; 6 says it was not.
    """
    cases = (
        # (name, tail, expected, label)
        ("f_backtick_open", "```\nexample\n", EXIT_SCAN_INTEGRITY, "``` never closed"),
        ("f_backtick_shut", "```\nexample\n```\n", EXIT_OK, "``` closed"),
        ("f_tilde_open", "~~~\nexample\n", EXIT_SCAN_INTEGRITY, "~~~ never closed"),
        ("f_tilde_shut", "~~~\nexample\n~~~\n", EXIT_OK, "~~~ closed"),
        # A ~~~ fence is NOT closed by ``` -- different character.
        ("f_tilde_wrong", "~~~\nexample\n```\n", EXIT_SCAN_INTEGRITY,
         "~~~ 'closed' by ```"),
        ("f_four_open", "````\nexample\n", EXIT_SCAN_INTEGRITY, "```` never closed"),
        # ...and a 4-backtick fence is NOT closed by 3 backticks: the closer must be at least
        # as long as the opener. This is the shape that evaded an earlier fix.
        ("f_four_short", "````\nexample\n```\n", EXIT_SCAN_INTEGRITY,
         "```` 'closed' by ```"),
        ("f_four_shut", "````\nexample\n````\n", EXIT_OK, "```` closed by ````"),
        ("f_info_open", "```python\nx = 1\n", EXIT_SCAN_INTEGRITY,
         "```python never closed"),
        # An info string on the CLOSER means it is another opener, not a closer.
        ("f_info_twice", "```python\nx = 1\n```python\n", EXIT_SCAN_INTEGRITY,
         "```python 'closed' by ```python"),
        ("f_info_shut", "```python\nx = 1\n```\n", EXIT_OK, "```python closed by ```"),
    )
    for (name, tail, want, label) in cases:
        root = _st_project(tmp, name)
        _st_write(root, "architecture.md", _ST_FENCE_HEAD + tail)
        code, out, err = _st_run(["--root", str(root)])
        ck("fence      %s -> exit %d" % (label, want), code, want)
        if want == EXIT_SCAN_INTEGRITY:
            ck("fence      %s -> stderr names the open line" % label,
               "SCAN INTEGRITY" in err, True)
            ck("fence      %s -> the report says it is not evidence" % label,
               "TRUNCATED scan" in out, True)

    # ---- ORDERING. Both an open fence AND an obvious A1: 6, not 3. The report is still
    # emitted -- an operator has to see what the truncated scan produced -- but the CODE says it
    # is not an answer.
    root = _st_project(tmp, "f_order")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D8 · The broker owns delivery\n"
              "kind: rules\n"
              "lifetime: conditional\n"
              "\n"
              "```\n"
              "example\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("fence      open fence + A1 -> exit 6, NOT 3", code, EXIT_SCAN_INTEGRITY)
    ck("fence      open fence + A1 -> the A1 is still printed", _st_rules(out), ["A1"])
    ck("fence      open fence + A1 -> and flagged as not evidence",
       "TRUNCATED scan" in out, True)

    # ---- 6 beats --report's forced 0 as well. An unread file is not a clean report.
    code, out, _err = _st_run(["--root", str(root), "--report"])
    ck("fence      --report + open fence -> exit 6", code, EXIT_SCAN_INTEGRITY)

    # ---- A declaration INSIDE a closed fence is not a declaration. The entry then has no
    # `kind:` at all, which is A2 -- not A1, and certainly not a silent pass.
    root = _st_project(tmp, "f_fenced_decl")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D9 · The delivery table\n"
              "lifetime: conditional\n"
              "\n"
              "```\n"
              "kind: rules\n"
              "```\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("fence      fenced `kind:` is not a declaration -> exit 3", code, EXIT_ABORT)
    ck("fence      fenced `kind:` -> A2, not A1", _st_rules(out), ["A2"])

    # ---- A heading inside a fence is not a heading, so it cannot become an entry.
    root = _st_project(tmp, "f_fenced_heading")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D10 · The delivery table\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "```\n"
              "## NOT-AN-ENTRY · sample heading\n"
              "```\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("fence      fenced heading is not an entry -> exit 0", code, EXIT_OK)
    ck("fence      fenced heading -> exactly 1 entry", "1 entries" in out, True)


# --------------------------------------------------------------------------- arms: path guard

def _st_arms_paths(ck, tmp):
    """`root / <absolute>` == `<absolute>`: pathlib DISCARDS the root, silently and by design.

    That exact bug shipped in recall's `list` verb and read ANOTHER project's file at exit 0
    while reporting this project's root beside it. There is one guard
    (`recall.resolve_under_root`) and this tool calls it; these arms prove the call happens.
    """
    root = _st_project(tmp, "p_scope")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D11 · The broker owns delivery\n"
              "kind: rules\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    _st_write(root, "design.md",
              "# Design\n"
              "\n"
              "## D12 · The retrieval layer\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    outside = _st_project(tmp, "p_outside")
    _st_write(outside, "architecture.md",
              "# Architecture\n"
              "\n"
              "## X1 · Another project entirely\n"
              "kind: rules\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")

    # An ABSOLUTE --file is refused BEFORE the join -- the join cannot report what it discarded.
    code, out, err = _st_run(["--root", str(root),
                              "--file", str(outside / "architecture.md")])
    ck("path       absolute --file -> exit 2", code, EXIT_USAGE)
    ck("path       absolute --file -> refused, no report emitted", out, "")
    ck("path       absolute --file -> refusal names the flag", "ABSOLUTE path" in err, True)
    ck("path       absolute --file -> the other project was NOT read", "X1" in out, False)

    # A `..` escape is refused AFTER resolution -- `..` only shows up once normalised.
    code, out, err = _st_run(["--root", str(root), "--file", "../p_outside/architecture.md"])
    ck("path       ../ --file -> exit 2", code, EXIT_USAGE)
    ck("path       ../ --file -> refused, no report emitted", out, "")
    ck("path       ../ --file -> refusal says OUTSIDE --root", "OUTSIDE --root" in err, True)

    # Refusal is a USAGE error, never a silent drop: a dropped path reads as "not found", which
    # is a claim about the corpus rather than about the flag.
    ck("path       ../ --file -> not reported as 'no such file'",
       "no such file" in err or "no such file" in out, False)

    # A legitimate relative --file scopes the scan and nothing else runs.
    code, out, _err = _st_run(["--root", str(root), "--file", "design.md"])
    ck("path       --file design.md -> exit 0 (the poison is out of scope)", code, EXIT_OK)
    ck("path       --file design.md -> architecture.md was not checked",
       "architecture.md" in out, False)
    code, out, _err = _st_run(["--root", str(root), "--file", "architecture.md"])
    ck("path       --file architecture.md -> exit 3", code, EXIT_ABORT)

    # A named file that does not exist is REPORTED as absent, not silently skipped.
    code, out, _err = _st_run(["--root", str(root), "--file", "nope.md"])
    ck("path       --file nope.md -> exit 0 with a named skip", code, EXIT_OK)
    ck("path       --file nope.md -> the absence is reported", "no such file" in out, True)

    # --root itself: required, must exist, must be a directory.
    code, out, err = _st_run([])
    ck("path       no --root -> exit 2", code, EXIT_USAGE)
    ck("path       no --root -> says the scope is DECLARED (D53)", "D53" in err, True)
    code, _out, _err = _st_run(["--root", str(tmp / "does_not_exist")])
    ck("path       missing --root dir -> exit 2", code, EXIT_USAGE)
    code, _out, _err = _st_run(["--root", str(root / "design.md")])
    ck("path       --root is a file, not a dir -> exit 2", code, EXIT_USAGE)


# --------------------------------------------------------------------------- arms: NOT aborts

def _st_arms_not_aborts(ck, tmp):
    """One arm per member of the NOT-ABORTS list. Each is a RATIFIED decision; an arm that goes
    red here means the tool has REVERSED one of them, which is worse than a missed finding."""
    # ---- D61: "no size condition fails a save." 60 entries and a 1,200-line body.
    body = "\n".join("filler line %d" % i for i in range(1, 1201))
    big = ["# Architecture", ""]
    for i in range(1, 61):
        big.append("## D%03d · Entry number %d" % (i, i))
        big.append("kind: spec")
        big.append("lifetime: conditional")
        big.append("")
        big.append("Body.")
        big.append("")
    big.append(body)
    big.append("")
    root = _st_project(tmp, "n_size")
    _st_write(root, "architecture.md", "\n".join(big))
    code, out, _err = _st_run(["--root", str(root)])
    ck("not-abort  size / trigger breach -> exit 0 (D61)", code, EXIT_OK)
    ck("not-abort  size breach -> all 60 entries were checked", "60 entries" in out, True)

    # ---- D67 + spec S2.5: `impl:` is PARKED at Phase 1. It matches NOTHING -- not an abort and
    # not even a warning. An unresolvable one is invisible to this tool by design.
    # The fixture dir is deliberately NOT named after the field: the report echoes `root:`, so a
    # dir called `n_impl` would put the substring in stdout and make this arm pass on its own
    # path name. (MEASURED: it did, on the first run of this suite.)
    root = _st_project(tmp, "n_parked_field")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D20 · The delivery table\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "impl: scripts/nothing_here.py::no_such_function\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("not-abort  unresolvable `impl:` -> exit 0 (D67)", code, EXIT_OK)
    ck("not-abort  `impl:` is not even warned about (parked, S2.5)",
       "impl" in out.lower(), False)

    # ---- W23: "Over budget is NOT an abort."
    root = _st_project(tmp, "n_budget")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D21 · The delivery table\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "why-not: %s\n"
              "refuted: %s\n"
              "\n"
              "Body.\n" % ("x" * 900, "y" * 900))
    code, out, _err = _st_run(["--root", str(root)])
    ck("not-abort  over-budget why-not:/refuted: -> exit 0 (W23)", code, EXIT_OK)
    ck("not-abort  over-budget -> no findings", _st_findings(out), [])

    # ---- A dangling reference is `recall check`'s verb, which exists. Not duplicated here.
    root = _st_project(tmp, "n_dangling")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D22 · The delivery table\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "Supersedes D999, which is defined nowhere in this project.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("not-abort  dangling reference -> exit 0", code, EXIT_OK)
    ck("not-abort  dangling reference -> no findings", _st_findings(out), [])

    # ---- S2.3: "Nobody ever writes hot or cold." WARN, and the exit code does not move.
    root = _st_project(tmp, "n_load")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D23 · The delivery table\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "load: hot\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("not-abort  declared `load:` -> exit 0", code, EXIT_OK)
    ck("not-abort  declared `load:` -> WARNED, not aborted", "WARNINGS (1)" in out, True)
    ck("not-abort  declared `load:` -> not in the abort set", _st_findings(out), [])
    ck("not-abort  the warning says the line steers nothing", "steers nothing" in out, True)


# --------------------------------------------------------------------------- arms: the region

def _st_arms_region(ck, tmp):
    """The carve-out FAILS CLOSED. An unterminated marker exempts NOTHING.

    An EOF-terminated region would let one stray marker exempt the rest of active.md from the
    type rule while the file still reported clean -- the unterminated-fence bug wearing a
    different hat, and this project shipped that bug once.

    The probe entry declares `kind: unresolved` with two candidates, which is T-3's escape hatch:
    INSIDE a closed region it is exempt from A1 and A8, OUTSIDE one it takes an A1 for being
    misfiled. One entry, both directions, no other rule moving underneath it.
    """
    entry = ("## U8 · Delivery ordering\n"
             "kind: unresolved\n"
             "candidates: rules, spec\n"
             "lifetime: expiring\n"
             "expires: 3 saves\n"
             "save: 14\n"
             "quote: \"sort it out\" (save 14)\n"
             "\n"
             "Body.\n")

    # Unterminated: the entry below it is checked IN FULL, so its `kind: spec` in a `rules` file
    # is an A1 and the file aborts.
    root = _st_project(tmp, "r_open")
    _st_write(root, "active.md", "# Active\n\n<!-- UNRESOLVED -->\n\n" + entry)
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("region     unterminated -> exempts NOTHING, exit 3", code, EXIT_ABORT)
    ck("region     unterminated -> the A1 still fires", _st_rules(out), ["A1"])
    ck("region     unterminated -> warned, naming the open line",
       "opened at line 3 and is never closed" in out, True)

    # Terminated: the same entry is exempt.
    root = _st_project(tmp, "r_shut")
    _st_write(root, "active.md",
              "# Active\n\n<!-- UNRESOLVED -->\n\n" + entry + "\n<!-- /UNRESOLVED -->\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("region     terminated -> exempt, exit 0", code, EXIT_OK)
    ck("region     terminated -> no findings", _st_findings(out), [])

    # `<!-- END UNRESOLVED -->` is the other accepted closer.
    root = _st_project(tmp, "r_end")
    _st_write(root, "active.md",
              "# Active\n\n<!-- UNRESOLVED -->\n\n" + entry + "\n<!-- END UNRESOLVED -->\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("region     `END UNRESOLVED` closes it too -> exit 0", code, EXIT_OK)

    # Membership is by HEADING line, not span overlap: an entry whose heading is BELOW the
    # closer is not exempt, even though the entry above it runs past the closer.
    root = _st_project(tmp, "r_membership")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "<!-- UNRESOLVED -->\n"
              "\n"
              "## U9 · Inside\n"
              "kind: unresolved\n"
              "candidates: rules, spec\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "\n"
              "<!-- /UNRESOLVED -->\n"
              "\n"
              "## U10 · Outside\n"
              "kind: spec\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "save: 14\n"
              "quote: \"sort it out\" (save 14)\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("region     membership is by heading line -> exit 3", code, EXIT_ABORT)
    ck("region     only the entry BELOW the closer is reported", _st_ids(out), ["U10"])
    ck("region     and it is an A1", _st_rules(out), ["A1"])

    # A closer with no opener closes nothing, and says so.
    root = _st_project(tmp, "r_stray_close")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "<!-- /UNRESOLVED -->\n"
              "\n"
              "## U11 · Delivery ordering\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "save: 14\n"
              "quote: \"sort it out\" (save 14)\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("region     stray closer -> exit 0 with a warning", code, EXIT_OK)
    ck("region     stray closer -> 'closes nothing'", "closes nothing" in out, True)

    # Regions do not nest; the second opener is reported and ignored.
    root = _st_project(tmp, "r_nested")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "<!-- UNRESOLVED -->\n"
              "\n"
              "<!-- UNRESOLVED -->\n"
              "\n"
              "## U12 · Delivery ordering\n"
              "kind: unresolved\n"
              "candidates: rules, spec\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "\n"
              "<!-- /UNRESOLVED -->\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("region     nested opener -> exit 0, warned", code, EXIT_OK)
    ck("region     nested opener -> 'do not nest'", "do not nest" in out, True)

    # The carve-out is scoped to active.md by T-3. A marker anywhere else exempts nothing.
    root = _st_project(tmp, "r_wrong_file")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "<!-- UNRESOLVED -->\n"
              "\n"
              "## D24 · The broker owns delivery\n"
              "kind: rules\n"
              "lifetime: conditional\n"
              "\n"
              "<!-- /UNRESOLVED -->\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("region     marker outside active.md -> exempts nothing, exit 3", code, EXIT_ABORT)
    ck("region     marker outside active.md -> the A1 fires", _st_rules(out), ["A1"])
    ck("region     marker outside active.md -> and is warned about",
       "exempts nothing" in out, True)

    # A marker inside a fence is not a marker.
    root = _st_project(tmp, "r_fenced_marker")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "```\n"
              "<!-- UNRESOLVED -->\n"
              "```\n"
              "\n"
              "## U13 · Delivery ordering\n"
              "kind: spec\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "save: 14\n"
              "quote: \"sort it out\" (save 14)\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("region     fenced marker opens nothing -> exit 3", code, EXIT_ABORT)
    ck("region     fenced marker -> the A1 fires", _st_rules(out), ["A1"])


# --------------------------------------------------------------------------- arms: encodings

def _st_arms_encodings(ck, tmp):
    """CRLF and BOM. Both are POISONED fixtures on purpose.

    A clean CRLF/BOM fixture exits 0 whether the terminator was handled or the file parsed to
    ZERO entries, so it cannot tell the two apart. A poisoned one can: only a scan that actually
    saw the entry can report the violation, and only a correct one can report its line number.
    """
    poison = ("## D25 · The broker owns delivery\n"
              "kind: rules\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")

    root = _st_project(tmp, "e_crlf")
    _st_write(root, "architecture.md", "# Architecture\n\n" + poison, eol="\r\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("encoding   CRLF poisoned -> exit 3", code, EXIT_ABORT)
    ck("encoding   CRLF -> the entry was seen", _st_ids(out), ["D25"])
    ck("encoding   CRLF -> line number is right (kind: on line 4)",
       [f[2] for f in _st_findings(out)], [4])

    root = _st_project(tmp, "e_crlf_clean")
    _st_write(root, "architecture.md",
              "# Architecture\n\n## D26 · The delivery table\nkind: spec\n"
              "lifetime: conditional\n\nBody.\n", eol="\r\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("encoding   CRLF clean -> exit 0", code, EXIT_OK)
    ck("encoding   CRLF clean -> 1 entry (not zero)", "1 entries" in out, True)

    # BOM immediately before the heading: HEADING_RE is anchored at `^` and U+FEFF is not an
    # indent character, so an unstripped BOM makes the heading invisible and the file reports a
    # clean PASS over content nobody checked.
    root = _st_project(tmp, "e_bom")
    _st_write(root, "architecture.md", poison, bom=True)
    code, out, _err = _st_run(["--root", str(root)])
    ck("encoding   BOM + poisoned heading on line 1 -> exit 3", code, EXIT_ABORT)
    ck("encoding   BOM -> the heading was seen", _st_ids(out), ["D25"])
    ck("encoding   BOM -> line number is right (kind: on line 2)",
       [f[2] for f in _st_findings(out)], [2])

    # BOM + ``` on line 1: the fence must still be SEEN, or a renderer shows the rest of the
    # file as code while the scanner reads it as prose.
    root = _st_project(tmp, "e_bom_fence")
    _st_write(root, "architecture.md",
              "```\n## D27 · The broker owns delivery\nkind: rules\n", bom=True)
    code, out, _err = _st_run(["--root", str(root)])
    ck("encoding   BOM + ``` on line 1 opens a fence -> exit 6", code, EXIT_SCAN_INTEGRITY)
    ck("encoding   BOM + ``` -> nothing below it became an entry", _st_findings(out), [])

    # CRLF inside a fence: the terminator must not break fence matching either.
    root = _st_project(tmp, "e_crlf_fence")
    _st_write(root, "architecture.md",
              "# Architecture\n\n## D28 · The delivery table\nkind: spec\n"
              "lifetime: conditional\n\n```\nkind: rules\n", eol="\r\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("encoding   CRLF + unclosed fence -> exit 6", code, EXIT_SCAN_INTEGRITY)


# --------------------------------------------------------------------------- arms: generated

def _st_arms_generated(ck, tmp):
    """state.md and manifest.md are regenerated from bounded inputs every save (S3.5).

    Nothing AUTHORS their entries, so there is no author to hold to A2/A3 -- nor to A1 or A8. The
    exemption stops at the VALUE: a value that IS present and is outside the vocabulary is still
    A2/A3, because a generator emitting one is a generator bug and silence would hide it. But a
    generated `manifest.md` that indexes the living docs and names each one's kind -- the most
    natural thing a manifest holds -- used to abort A1 with a message addressed to an author who
    cannot edit the file. Nobody can hand-fix generated output, so an abort there is a save that
    cannot be made to pass.
    """
    for rel in ("state.md", "manifest.md"):
        root = _st_project(tmp, "g_" + rel.replace(".", "_"))
        _st_write(root, rel,
                  "# Generated\n"
                  "\n"
                  "## G1 · A generated row\n"
                  "\n"
                  "No kind, no lifetime -- nothing authored this.\n")
        code, out, _err = _st_run(["--root", str(root)])
        ck("generated  %s with no declarations -> exit 0" % rel, code, EXIT_OK)
        ck("generated  %s -> no findings" % rel, _st_findings(out), [])
        ck("generated  %s -> the exemption is stated in the report" % rel,
           "A1/A2/A3/A8 exempt" in out, True)

    # CLAUDE.md is the OPPOSITE case to the two generated files and lands in the same set: a
    # hand-written charter that no save step writes. Before 2026-08-30 this exact shape
    # returned EXIT_ABORT and nothing save could do would clear it.
    root = _st_project(tmp, "g_claude_charter")
    _st_write(root, "CLAUDE.md",
              "# rpi_migration - project charter\n"
              "\n"
              "## Scope\n"
              "\n"
              "Three Pis: kitchen, garage, shed.\n"
              "\n"
              "## How we work\n"
              "\n"
              "Never flash a card without a verified backup image first.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("charter    a hand-written CLAUDE.md with no declarations -> exit 0", code, EXIT_OK)
    ck("charter    ...and no findings", _st_findings(out), [])
    ck("charter    ...and no SHAPE warning claiming nothing was checked",
       "NO ENTRY was parsed from CLAUDE.md" in out, False)

    root = _st_project(tmp, "g_agents_charter")
    _st_write(root, "AGENTS.md",
              "# codex project instructions\n"
              "\n"
              "## Scope\n"
              "\n"
              "Keep Codex work inside this project.\n"
              "\n"
              "## Verification\n"
              "\n"
              "Run the project checks before a completion claim.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("charter    a hand-written AGENTS.md with no declarations -> exit 0", code, EXIT_OK)
    ck("charter    AGENTS.md produces no findings", _st_findings(out), [])
    ck("charter    AGENTS.md has no SHAPE warning claiming nothing was checked",
       "NO ENTRY was parsed from AGENTS.md" in out, False)

    # AGENTS.md is passive, not merely declaration-exempt. These bytes would trigger a bad
    # vocabulary finding, an identifier collision, a dangling A14 citation and scan-integrity
    # failure in an active corpus file. None belongs to save; the explicit SKIPPED row is the
    # evidence that the file was deliberately excluded rather than silently missed.
    root = _st_project(tmp, "g_agents_passive")
    _st_write(root, "AGENTS.md",
              "# Codex project instructions\n\n"
              "## M1 - host-owned prose\n"
              "kind: banana\n"
              "lifetime: forever\n\n"
              "D999 is a Codex-local reference.\n\n"
              "```text\nopen\n")
    code, out, err = _st_run(["--root", str(root)])
    ck("charter    passive AGENTS.md cannot block validation", code, EXIT_OK)
    ck("charter    passive AGENTS.md produces no finding", _st_findings(out), [])
    ck("charter    passive AGENTS.md produces no A14 warning", _st_content(out, "A14"), [])
    ck("charter    passive AGENTS.md produces no fence-integrity failure",
       "OPEN code fence" in (err or ""), False)
    ck("charter    passive AGENTS.md is disclosed as SKIPPED",
       "host-owned Codex instructions" in out, True)

    # The exemption does NOT reach the vocabulary: a misdeclared value is still an abort.
    root = _st_project(tmp, "g_claude_bogus")
    _st_write(root, "CLAUDE.md",
              "# Charter\n"
              "\n"
              "## R-1 - a rule the operator meant to declare\n"
              "kind: rulez\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("charter    a MISDECLARED value in CLAUDE.md still aborts", code, EXIT_ABORT)
    ck("charter    ...as A2", _st_rules(out), ["A2"])

    root = _st_project(tmp, "g_bogus_value")
    _st_write(root, "state.md",
              "# Generated\n"
              "\n"
              "## G2 · A generated row\n"
              "kind: guideline\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("generated  a value OUTSIDE the vocabulary still aborts", code, EXIT_ABORT)
    ck("generated  ...as A2", _st_rules(out), ["A2"])

    root = _st_project(tmp, "g_wrong_kind")
    _st_write(root, "manifest.md",
              "# Manifest\n"
              "\n"
              "## G3 · design.md\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "## G4 · backlog.md\n"
              "kind: plan\n"
              "lifetime: conditional\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("generated  a manifest naming other files' kinds -> exit 0", code, EXIT_OK)
    ck("generated  ...the exemption covers A1 and A8 too", _st_findings(out), [])

    # ---- the current save# resolution chain: --save, else state.md's `save:` line, else
    # unknown. A6's freshness ground is the only consumer and it SKIPS when the answer is
    # unknown rather than passing or failing everything.
    root = _st_project(tmp, "g_save_source")
    _st_write(root, "state.md", "# State\n\nsave: 14\n")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## AC3 · Restart the broker before each deploy\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "save: 14\n"
              "implication: broker restarted before each deploy\n"
              "quote: \"restart the broker before each deploy\" (save 9)\n"
              "\n"
              "Body.\n")
    _st_write(root, "_archive/active-expired.md",
              "# Expired\n"
              "\n"
              "## AX1 · Restart the broker before each deploy\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "implication: broker restarted before each deploy\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("generated  save# read from state.md -> A6 fires without --save", code, EXIT_ABORT)
    ck("generated  ...and the source is named", "source: state.md" in out, True)
    ck("generated  ...the rule is A6", _st_rules(out), ["A6"])

    # `--save 9` OVERRIDES state.md, and the check this arm exists for is the SOURCE line. Exit 0
    # here now comes from ground (c)'s gate rather than from a fresh quote: the entry declares it
    # was asserted at save 14, so at save 9 it is not being newly asserted and the renewal ground
    # does not evaluate. Both readings agree on the code; only `source: --save` distinguishes the
    # two invocations, which is what this arm asserts.
    code, out, _err = _st_run(["--root", str(root), "--save", "9"])
    ck("generated  --save overrides state.md -> exit 0", code, EXIT_OK)
    ck("generated  ...and the source is named", "source: --save" in out, True)

    # No state.md and no --save: the freshness ground SKIPS with a named warning. It never
    # passes silently and never invents a "now".
    root = _st_project(tmp, "g_no_save")
    _st_write(root, "active.md", _ST_ACTIVE_CLEAN)
    code, out, _err = _st_run(["--root", str(root)])
    ck("generated  unknown save# -> exit 0", code, EXIT_OK)
    ck("generated  unknown save# -> the skip is REPORTED", "was SKIPPED" in out, True)
    ck("generated  unknown save# -> and the source says so", "unknown" in out, True)


# --------------------------------------------------------------------------- arms: --report, T-2

def _st_arms_report_and_t2(ck, tmp):
    """Migration mode publishes the type rule and never fails on it (S9 step 6). T-2 resolves
    DOWNWARD and writes nothing."""
    root = _st_project(tmp, "m_report")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D29 · The broker owns delivery\n"
              "kind: rules\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    code_plain, out_plain, _err = _st_run(["--root", str(root)])
    code_rep, out_rep, _err = _st_run(["--root", str(root), "--report"])
    ck("report     without --report -> exit 3", code_plain, EXIT_ABORT)
    ck("report     with --report -> exit 0", code_rep, EXIT_OK)
    ck("report     the abort set is IDENTICAL, only the code moves",
       _st_findings(out_rep), _st_findings(out_plain))
    ck("report     the mode is stated in the report", "MODE: --report" in out_rep, True)

    # ---- T-2: two declared lifetimes resolve to the SHORTER one. The file order here is
    # permanent-then-expiring, so a first-wins or last-wins bug would pick `permanent`.
    root = _st_project(tmp, "t2_two_lines")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D30 · The delivery table\n"
              "kind: spec\n"
              "lifetime: permanent\n"
              "lifetime: expiring\n"
              "\n"
              "Body.\n")
    code, out, err = _st_run(["--root", str(root)])
    ck("T-2        two lifetimes -> exit 0 (a resolution is not an abort)", code, EXIT_OK)
    ck("T-2        resolves DOWNWARD to `expiring`", "-> `expiring`" in out, True)
    ck("T-2        both declarations are named with their lines",
       "`permanent` (line 5)" in out and "`expiring` (line 6)" in out, True)
    ck("T-2        stdout says no file was changed", "NO FILE WAS CHANGED" in out, True)
    ck("T-2        stderr says it too", "NO FILE WAS CHANGED" in err, True)
    ck("T-2        and the direction is justified, not asserted",
       "keep\n  dead content hot" in err or "dead content hot" in err, True)

    root = _st_project(tmp, "t2_one_line")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D31 · The delivery table\n"
              "kind: spec\n"
              "lifetime: permanent, conditional\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("T-2        two on ONE line -> exit 0", code, EXIT_OK)
    ck("T-2        resolves to `conditional`", "-> `conditional`" in out, True)

    # A single declared lifetime is not a disagreement and must not be reported as one.
    root = _st_project(tmp, "t2_none")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D32 · The delivery table\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    code, out, err = _st_run(["--root", str(root)])
    ck("T-2        one lifetime -> no resolution invented", "LIFETIME RESOLUTION" in out, False)
    ck("T-2        ...and nothing on stderr either", "LIFETIME RESOLUTION" in err, False)
    ck("T-2        ...exit 0", code, EXIT_OK)


# --------------------------------------------------------------------------- arms: split (R-B)

def _st_arms_split(ck, tmp):
    """R-B's second half: when a split HAS been performed, validate it. WARNINGS only.

    The abort set is CLOSED at A1..A8 and a malformed split is not a member of it. A8 already
    fires on the un-split case, which is the one the verdict turns on.
    """
    # A properly performed split: one kind each, different ids, each cites the other.
    #
    # WHERE THE HALVES CAN SIT, and why these two arms run in --report mode. `validate_split`
    # runs per FILE, and a split by definition puts a DIFFERENT kind in each half -- so at most
    # one half can match any single file's declared kind, and the other half is an A1 by
    # construction. In real use the halves land in two files and neither run sees both. (MEASURED
    # while writing this arm: the first version put a `spec` half in `_archive/backlog-deferred.md`
    # and took an A1, because that file's declared kind is `plan`. The tool was right and the
    # fixture was wrong.) active.md's UNRESOLVED region used to host them, but the region now
    # exempts only entries that DECLARE `kind: unresolved`, which a performed split does not.
    # So the aborts are PUBLISHED (--report) and the assertion is on the split WARNING, which is
    # what these arms are about; the cross-FILE case is the arm at the bottom of this group.
    root = _st_project(tmp, "s_good")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## U20 · Rebuild the retrieval index\n"
              "kind: plan\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "quote: \"split it\" (save 14)\n"
              "split-of: U21\n"
              "\n"
              "Body.\n"
              "\n"
              "## U21 · The retrieval index contract\n"
              "kind: spec\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "quote: \"split it\" (save 14)\n"
              "split-of: U20\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14", "--report"])
    ck("split      well-formed split -> no split warning", "does not cite back" in out, False)
    ck("split      well-formed split -> report mode exit 0", code, EXIT_OK)

    # One-way citation: the cross-reference is what stops a reader losing the other half.
    root = _st_project(tmp, "s_oneway")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## U20 · Rebuild the retrieval index\n"
              "kind: plan\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "quote: \"split it\" (save 14)\n"
              "split-of: U21\n"
              "\n"
              "Body.\n"
              "\n"
              "## U21 · The retrieval index contract\n"
              "kind: spec\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "quote: \"split it\" (save 14)\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14", "--report"])
    ck("split      one-way citation -> 'does not cite back'", "does not cite back" in out, True)
    ck("split      one-way citation -> reported as a WARNING, not an abort",
       "A9" in out or "does not cite back" in _st_rules(out), False)

    # Two halves of the SAME kind are one entry with two ids, not a split.
    root = _st_project(tmp, "s_same_kind")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D33 · The broker owns delivery\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "split-of: D34\n"
              "\n"
              "Body.\n"
              "\n"
              "## D34 · The delivery contract\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "split-of: D33\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("split      same kind in both halves -> WARNED, exit 0", code, EXIT_OK)
    ck("split      same kind -> the message says so", "both declare `kind: spec`" in out, True)
    ck("split      same kind -> NOT an abort", _st_findings(out), [])

    # A split of ITSELF.
    root = _st_project(tmp, "s_self")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D35 · The broker owns delivery\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "split-of: D35\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("split      split of ITSELF -> WARNED, exit 0", code, EXIT_OK)
    ck("split      split of ITSELF -> the message says so", "split of ITSELF" in out, True)

    # A target outside this file is a DANGLING REFERENCE -- `recall check`'s class, which
    # exists. This tool must not duplicate it and must not invent a finding for it.
    root = _st_project(tmp, "s_outside")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D36 · The broker owns delivery\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "split-of: P9\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("split      target outside scope -> exit 0", code, EXIT_OK)
    ck("split      target outside scope -> no finding invented", _st_findings(out), [])
    ck("split      target outside scope -> no warning either", "WARNINGS (" in out, False)


# --------------------------------------------------------------------------- arms: doc shape

def _st_arms_shape(ck, tmp):
    """EVERY ATX heading at level >= 2 is an entry; fields attach to the INNERMOST one.

    The two shapes this group pins are the two wrong verdicts the old "shallowest level wins"
    rule produced: a `###`-only file parsing to ZERO entries (a clean PASS over content nobody
    checked), and a `##` banner ABSORBING its `###` sub-entries so one declaration under the
    banner satisfied the rule for all of them.
    """
    root = _st_project(tmp, "h_h3_poison")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "### D40 · The broker owns delivery\n"
              "kind: rules\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("shape      ###-only file, poisoned -> exit 3", code, EXIT_ABORT)
    ck("shape      ###-only file -> the entry was seen", _st_ids(out), ["D40"])

    root = _st_project(tmp, "h_h3_clean")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "### D41 · The delivery table\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("shape      ###-only file, clean -> exit 0", code, EXIT_OK)
    ck("shape      ###-only file -> 1 entry, not zero", "1 entries" in out, True)

    # THE MASKING SHAPE. A `##` banner that declares nothing is a SECTION, not an entry, and the
    # `###` entries under it are checked individually. The old rule made the banner the only
    # entry and absorbed the whole subtree, so ONE declaration satisfied all of them; this
    # project's own roadmap.md (renamed backlog.md by S3.4) is exactly this shape.
    root = _st_project(tmp, "h_banner_mask")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## BACKLOG\n"
              "\n"
              "### D42 · The broker owns delivery\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "### D43 · The delivery table\n"
              "\n"
              "No declarations at all under this one.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("shape      banner + ### -> the undeclared sub-entry is FOUND", code, EXIT_ABORT)
    ck("shape      banner + ### -> it is named by its own id", _st_ids(out), ["D43"])
    ck("shape      banner + ### -> A2 and A3, not the banner's", _st_rules(out), ["A2", "A3"])
    ck("shape      banner + ### -> the banner is reported as a section",
       "section banner" in out, True)

    # A heading that DOES declare is an entry even with children -- otherwise `kind:` on a banner
    # would buy silence for everything below it, which is the same bug wearing a hat. Measured:
    # writing `kind: plan` + `lifetime: conditional` under each of two banners took a real
    # open_questions.md from 44 aborts to 0.
    root = _st_project(tmp, "h_banner_declares")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## S2 · Section that declares\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "### D44 · A sub-entry that declares nothing\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("shape      a declaring banner does NOT cover its children", code, EXIT_ABORT)
    ck("shape      ...the child is named, the banner is clean", _st_ids(out), ["D44"])

    # ...and the declaring banner is itself an ENTRY, checked like any other. Without this arm a
    # "every heading with a child is a section" rule passes every other arm in this group while
    # silently exempting the one heading that declared something.
    root = _st_project(tmp, "h_banner_checked")
    _st_write(root, "design.md",
              "# Design\n"
              "\n"
              "## S3 · Section that declares the WRONG kind\n"
              "kind: plan\n"
              "lifetime: conditional\n"
              "\n"
              "### D56 · A correctly declared child\n"
              "kind: spec\n"
              "lifetime: conditional\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("shape      a declaring banner is CHECKED, not exempted", _st_ids(out), ["S3"])
    ck("shape      ...as an A1", _st_rules(out), ["A1"])

    # Fields attach to the INNERMOST heading, so a `kind:` under a sub-heading belongs to the
    # sub-heading. Absorption across a heading boundary is what let one `gate:` satisfy four
    # deferred entries; A8 still sees a second `kind:` anywhere in the entry's OWN body.
    root = _st_project(tmp, "h_subsection")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D45 · The delivery table\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "### Detail\n"
              "kind: rules\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("shape      a kind under a sub-heading belongs to the SUB-heading",
       _st_ids(out), ["Detail"])
    ck("shape      ...and it is an A1, not the parent's A8", _st_rules(out), ["A1"])

    root = _st_project(tmp, "h_two_kinds_one_body")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D46 · The delivery table\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "kind: rules\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("shape      two kinds in ONE body -> A8 still sees both", _st_rules(out), ["A1", "A8"])
    ck("shape      ...and both lines are named", "(line 4)" in out and "(line 7)" in out, True)

    # A non-generated in-table file with real content and NO entry is the wrong-verdict shape the
    # module INVARIANT names. It cannot be an abort (the set is CLOSED at A1..A8), so it is a
    # named SHAPE warning -- the difference between an operator who knows and one who does not.
    root = _st_project(tmp, "h_no_headings")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "Prose only. No entry-level heading anywhere in this file.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("shape      no level->=2 heading -> exit 0 (no abort exists for it)", code, EXIT_OK)
    ck("shape      no level->=2 heading -> WARNED, not silent", "NO ENTRY was parsed" in out, True)
    ck("shape      ...and the ABORT SET line says nothing was checked",
       "nothing was checked" in out, True)

    root = _st_project(tmp, "h_setext")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "D47 the gateway\n"
              "----------------\n"
              "kind: rules\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("shape      a SETEXT heading is DETECTED, not silently skipped",
       "SETEXT heading" in out, True)

    root = _st_project(tmp, "h_indented")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "  ## D48 · indented two spaces\n"
              "kind: rules\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("shape      an INDENTED ATX heading is DETECTED", "indented 2 space(s)" in out, True)

    # state.md legitimately parses to zero entries -- it is generated, and the exemption is what
    # keeps the warning above from firing on it.
    root = _st_project(tmp, "h_generated_zero")
    _st_write(root, "state.md", "# State\n\nsave: 14\n\nA table with no headings.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("shape      a GENERATED zero-entry file is not warned about",
       "NO ENTRY was parsed" in out, False)


# --------------------------------------------------------------------------- arms: scope

def _st_arms_scope(ck, tmp):
    """A file NOT in the S3.1/S3.2 table is OUT OF SCOPE: reported as skipped, kind never
    guessed. Guessing would make A1 -- the whole point of step 7d -- an opinion."""
    root = _st_project(tmp, "sc_offtable")
    _st_write(root, "notes.md",
              "# Notes\n"
              "\n"
              "## N1 · Scratch\n"
              "kind: banana\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("scope      off-table file -> exit 0", code, EXIT_OK)
    ck("scope      off-table file -> SKIPPED, not checked", "OUT OF SCOPE" in out, True)
    ck("scope      off-table file -> no finding invented", _st_findings(out), [])

    # docs/ is not in DEFAULT_PATTERNS at all: plans, wip and artifacts are not entries.
    root = _st_project(tmp, "sc_docs")
    _st_write(root, "docs/plans/spec.md",
              "# Spec\n"
              "\n"
              "## D50 · Not an entry file\n"
              "kind: banana\n"
              "\n"
              "Body.\n")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D51 · The delivery table\n"
              "kind: spec\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("scope      docs/ is not discovered -> exit 0", code, EXIT_OK)
    ck("scope      docs/ is not even listed", "docs/plans/spec.md" in out, False)


# --------------------------------------------------------------------------- arms: R-D, report

def _st_snapshot(root):
    """{rel: bytes} for every file under root. The evidence for the writes-nothing contract."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


_ST_SUMMARY_RE = re.compile(r"^SUMMARY: (\d+) entries, (\d+) aborts, (\d+) warnings", re.M)


def _st_arms_contract(ck, tmp):
    """R-D (writes no file, ever) and the report contract (a count is never the whole report)."""
    root = _st_project(tmp, "c_nowrite")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D60 · The broker owns delivery\n"
              "kind: rules\n"
              "lifetime: permanent\n"
              "lifetime: expiring\n"
              "load: hot\n"
              "\n"
              "Body.\n")
    _st_write(root, "CONSTRAINTS.md",
              "# Constraints\n"
              "\n"
              "## C9 · No remote, ever\n"
              "kind: rules\n"
              "\n"
              "Body.\n")
    before = _st_snapshot(root)
    code, out, _err = _st_run(["--root", str(root)])
    after = _st_snapshot(root)
    ck("R-D        a run with aborts + warnings + a T-2 resolution -> exit 3", code, EXIT_ABORT)
    ck("R-D        no file was created or removed",
       sorted(after.keys()), sorted(before.keys()))
    ck("R-D        not one byte of any file changed", after, before)
    ck("R-D        and the report says so", "wrote no file" in out, True)

    # The report contract: the SUMMARY count is a summary OF the named list, never a substitute
    # for it. Every counted abort must appear as a named line.
    m = _ST_SUMMARY_RE.search(out)
    ck("report     the SUMMARY line is present", bool(m), True)
    if m:
        ck("report     every counted abort is also NAMED", len(_st_findings(out)), int(m.group(2)))
        ck("report     every named abort carries a file:line",
           all(f[1] and f[2] > 0 for f in _st_findings(out)), True)
        ck("report     every named abort carries an entry id",
           all(f[3] for f in _st_findings(out)), True)
        ck("report     both files contributed", sorted(set(f[1] for f in _st_findings(out))),
           ["CONSTRAINTS.md", "architecture.md"])

    # The `by rule:` block is indented FOUR spaces precisely so it cannot be mistaken for a
    # named finding by anything parsing this report -- including these arms.
    ck("report     the by-rule block is present", "  by rule:" in out, True)


# --------------------------------------------------------------------------- arms: invariants

_ST_CONTENT_CLEAN = (
    "# Backlog\n"
    "\n"
    "## M1 - ship the resolver\n"
    "kind: plan\n"
    "lifetime: conditional - until the resolver ships\n"
    "Status:  in flight\n"
    "Context: the resolver reads the declared families file.\n"
    "Action:  wire it into update_state.\n"
    "Blocks:  none.\n"
)


def _st_content(out_text, rule):
    """[(rel, line, ident)] for ONE content rule, parsed back out of the RENDERED report.

    Parsed from the printed form for the same reason `_st_findings` is: the contract is that an
    operator can act on what is PRINTED.
    """
    rx = re.compile(r"^ {2}%s {2}(\S+):(\d+) {2}\[([^\]]*)\]" % rule)
    found = []
    for line in out_text.splitlines():
        m = rx.match(line)
        if m:
            found.append((m.group(1), int(m.group(2)), m.group(3)))
    return found


def _st_arms_content(ck, tmp):
    """A9..A12, the hot-file content contract. Every rule gets a MATCHED PAIR: the same fixture
    with ONE line changed, asserted in BOTH directions. A rule that only ever meets its positive
    arm is a rule that might be firing on something else entirely."""
    # ---- the control FIRST: an in-contract item fires none of the four. Without this, every
    # "silent" arm below could be silent because the rule is broken rather than because the
    # fixture is clean.
    root = _st_project(tmp, "content_clean")
    _st_write(root, "backlog.md", _ST_CONTENT_CLEAN)
    code, out, _err = _st_run(["--root", str(root)])
    ck("content    clean item -> exit 0", code, EXIT_OK)
    for rule in ("A9", "A10", "A11", "A12"):
        ck("content    clean item -> %s silent" % rule, _st_content(out, rule), [])

    # ---- one MATCHED PAIR per rule. In each pair exactly one thing differs.
    for (rule, name, added, fires) in (
        # A9: the prose announcement is IDENTICAL in both arms -- only the `done:` field differs,
        # so the arm proves the FIELD is what silences it, not the absence of the announcement.
        # ENTRY-LEVEL prose, deliberately NOT a bold run and NOT a bullet: after the
        # review fix those belong to A11, and an A9 fixture written that way exercises
        # the wrong rule. The announcement is IDENTICAL in both arms below - only the
        # `done:` field differs - so the pair proves the FIELD is what silences A9.
        ("A9", "a9_prose", "The resolver is DONE as of save#6.\n", True),
        ("A9", "a9_field",
         "The resolver is DONE as of save#6.\ndone: save#6 - shipped.\n", False),
        ("A10", "a10_over", "Context continues. " * 70 + "\n", True),
        ("A10", "a10_under", "Context continues.\n", False),
        ("A11", "a11_bullet", "- **M9** ship the second resolver - DONE\n", True),
        ("A11", "a11_plain", "- the second resolver is described in design.md\n", False),
        ("A12", "a12_receipt", "VERIFIED AT SOURCE on main; 469 checks, 0 failed.\n", True),
        ("A12", "a12_clean", "The resolver is described in design.md section 5.\n", False),
    ):
        root = _st_project(tmp, name)
        _st_write(root, "backlog.md", _ST_CONTENT_CLEAN + added)
        code, out, _err = _st_run(["--root", str(root)])
        hits = _st_content(out, rule)
        ck("content    %s %s -> exit 0 (C-53)" % (rule, name), code, EXIT_OK)
        ck("content    %s %s -> %s" % (rule, name, "fires" if fires else "silent"),
           bool(hits), fires)
        if fires:
            ck("content    %s %s -> names the id" % (rule, name),
               sorted(set(h[2] for h in hits)), ["M1"])
            ck("content    %s %s -> names the file" % (rule, name),
               sorted(set(h[0] for h in hits)), ["backlog.md"])

    # ---- C-53 AS A REGRESSION GUARD, not as a comment. All four fire at once: the exit code
    # must still be 0 and NONE of them may be parsed back as an abort. The cheapest way to break
    # this later is to widen `_ST_FINDING_RE` from `A[1-8]` to `A\d+`, which would silently
    # reclassify every content warning as a finding and make the suite agree with itself.
    root = _st_project(tmp, "content_all_four")
    _st_write(root, "backlog.md", _ST_CONTENT_CLEAN
              + "The resolver is DONE as of save#6.\n"
              + "- **M9** ship the second resolver - DONE\n"
              + "VERIFIED AT SOURCE on main; 469 checks, 0 failed.\n"
              + "Context continues. " * 70 + "\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("content    all four fire -> exit 0 (C-53)", code, EXIT_OK)
    ck("content    all four fire -> ABORT SET still empty", _st_findings(out), [])
    ck("content    all four fire -> all four named",
       sorted(r for r in ("A9", "A10", "A11", "A12") if _st_content(out, r)),
       ["A10", "A11", "A12", "A9"])

    # ---- SCOPE, both halves. A9/A10/A11 are about WORK ITEMS, so architecture.md is not their
    # business; A12 does read it. A rule with no asserted scope grows one.
    root = _st_project(tmp, "content_scope_arch")
    _st_write(root, "architecture.md",
              "# Architecture\n"
              "\n"
              "## D1 - the broker owns delivery\n"
              "kind: spec\n"
              "lifetime: conditional - until the broker is replaced\n"
              "**DONE save#6 - shipped.**\n"
              "- **M9** ship the second resolver - DONE\n"
              "VERIFIED AT SOURCE on main.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("content    architecture.md -> exit 0", code, EXIT_OK)
    for rule in ("A9", "A10", "A11"):
        ck("content    architecture.md -> %s out of scope" % rule, _st_content(out, rule), [])
    ck("content    architecture.md -> A12 IS in scope",
       bool(_st_content(out, "A12")), True)

    # ---- SCOPE: CONSTRAINTS.md is exempt from A12 BY CONTRACT. Its entries are REQUIRED to
    # carry a verbatim user quote with the save# it was said in -- exactly what the narrative
    # class matches -- so firing there would flag the thing the file exists to hold.
    root = _st_project(tmp, "content_scope_constraints")
    _st_write(root, "CONSTRAINTS.md",
              "# Constraints\n"
              "\n"
              "## C-1 - restore budget\n"
              "kind: rules\n"
              "lifetime: permanent\n"
              "\"keep the hot load under 100k\" (2026-08-16, save#118)\n"
              "**Implication** target 50k, hard ceiling 100k.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("content    CONSTRAINTS.md -> exit 0", code, EXIT_OK)
    ck("content    CONSTRAINTS.md -> A12 exempt (its quotes are mandated)",
       _st_content(out, "A12"), [])

    # ---- documenting a marker must not read as writing one: inline code is blanked first.
    root = _st_project(tmp, "content_backticks")
    _st_write(root, "backlog.md",
              _ST_CONTENT_CLEAN + "Write `DONE` as a `done:` field, never as prose.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("content    a marker inside backticks -> exit 0", code, EXIT_OK)
    ck("content    a marker inside backticks -> A9 silent", _st_content(out, "A9"), [])
    ck("content    a marker inside backticks -> A11 silent", _st_content(out, "A11"), [])

    # ---- REVIEW FIXES. Each arm is the defect the cross-family review named, re-introduced.
    # [0] NESTED status belongs to A11 and must NOT reach A9. This is the must-fix: A9 was
    # telling the author to mark the ENCLOSING entry done, and 6a would have evicted it.
    root = _st_project(tmp, "content_nested_status")
    _st_write(root, "backlog.md",
              _ST_CONTENT_CLEAN + "- **M9** ship the second resolver - DONE\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("review[0]  nested `- **M9** DONE` -> A9 SILENT (it is M9 that is done, not M1)",
       _st_content(out, "A9"), [])
    ck("review[0]  nested `- **M9** DONE` -> A11 still fires",
       bool(_st_content(out, "A11")), True)
    ck("review[0]  nested -> exit 0", code, EXIT_OK)

    # [0] NEGATION. `NOT DONE` announces the opposite of completion.
    for (name, line, fires) in (
        ("neg_not", "Status:  NOT DONE - still in flight.\n", False),
        ("neg_never", "This was never SHIPPED.\n", False),
        ("neg_plain", "Status:  DONE - it shipped.\n", True),
    ):
        root = _st_project(tmp, "content_%s" % name)
        _st_write(root, "backlog.md", _ST_CONTENT_CLEAN + line)
        code, out, _err = _st_run(["--root", str(root)])
        ck("review[0]  %-9s -> A9 %s" % (name, "fires" if fires else "silent"),
           bool(_st_content(out, "A9")), fires)
        ck("review[0]  %-9s -> exit 0" % name, code, EXIT_OK)

    # [1] A STANDALONE BOLD RUN is below entry level. SKILL.md promised this and the code did
    # not do it - a document claiming what the code does not, which is the failure this project
    # records most often.
    root = _st_project(tmp, "content_bold_run")
    _st_write(root, "backlog.md",
              _ST_CONTENT_CLEAN + "**DONE save#6 - the second resolver shipped.**\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("review[1]  standalone bold run carrying a marker -> A11 fires",
       bool(_st_content(out, "A11")), True)
    ck("review[1]  ...and A9 stays silent, because it is below entry level",
       _st_content(out, "A9"), [])
    ck("review[1]  bold run -> exit 0", code, EXIT_OK)

    # [2] A RECEIPT IS A RESULT, NOT A NOUN. The three bare terms are ordinary vocabulary in a
    # spec file; a real receipt still has to fire, or the fix is just a deletion.
    root = _st_project(tmp, "content_spec_nouns")
    _st_write(root, "design.md",
              "# Design\n"
              "\n"
              "## S1 - the move algorithm\n"
              "kind: spec\n"
              "lifetime: conditional - until the move algorithm changes\n"
              "The destination span must be byte-identical and is keyed by sha256; the caller\n"
              "may use merge-base to order two revisions.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("review[2]  bare sha256 / byte-identical / merge-base in design.md -> A12 SILENT",
       _st_content(out, "A12"), [])
    ck("review[2]  spec nouns -> exit 0", code, EXIT_OK)

    root = _st_project(tmp, "content_real_receipt")
    _st_write(root, "design.md",
              "# Design\n"
              "\n"
              "## S1 - the move algorithm\n"
              "kind: spec\n"
              "lifetime: conditional - until the move algorithm changes\n"
              "Verified: blob cc1ef41e001b9217, selftest 471 checks, 0 failed.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("review[2]  a REAL receipt (quoted hash + a count) -> A12 still fires",
       bool(_st_content(out, "A12")), True)
    ck("review[2]  real receipt -> exit 0", code, EXIT_OK)

    # [3] ONE vocabulary. The three words the document used to omit must behave like the six it
    # named, or the contract and the code disagree again.
    for word in ("COMPLETE", "COMPLETED", "DELETED"):
        root = _st_project(tmp, "content_vocab_%s" % word.lower())
        _st_write(root, "backlog.md", _ST_CONTENT_CLEAN + "Status:  %s save#6.\n" % word)
        code, out, _err = _st_run(["--root", str(root)])
        ck("review[3]  `%s` fires A9 exactly as the six named words do" % word,
           bool(_st_content(out, "A9")), True)
        ck("review[3]  `%s` -> exit 0" % word, code, EXIT_OK)


def _st_arms_a13(ck, tmp):
    """A13 -- the `state.md` prose-field ceiling. Every arm is a MATCHED PAIR on ONE variable.

    The arm that earns the rest is NO-SWALLOW. The value of `key: |` ends at the first non-blank
    unindented line, and if that boundary were wrong the check would measure the whole file and
    fire on every project on the fleet -- while still looking exactly like a working check on the
    one folder that is genuinely over. So the fixture pairs a TINY field with a LARGE tail.
    """
    def state(goal_body, track_body, tail=""):
        return ("<!-- ash_save v4 | save#1 | 2026-08-17 00:00:00 PDT -->\n"
                "# t - state\n\nschema_version: v4\nproject: t\nphase: implementation\n"
                "save: 1\nlast_save_ts: 2026-08-17 00:00:00 PDT\nstatus: active\n"
                "goal: |\n%s"
                "active_track: |\n%s"
                "repo_root: (not resolved yet)\ntask_list_id: (none)\nhot_state: scaffolded\n"
                "backlog_order: []\nno_demote_streak: 0\n"
                "files:\n  manifest: manifest.md\n  latest_checkpoint: (none)\n"
                "  archive: _archive/\n"
                "counts:\n  hot_files: 9\n  checkpoints: 0\n  backlog_items: 0\n"
                "  open_questions: 0\n  active_instructions: 0\n%s" % (goal_body, track_body, tail))

    def line(n):
        return "  " + ("x" * n) + "\n"

    # ---- NO-SWALLOW. Tiny fields, 6 KB of tail prose. Silent, or the boundary is wrong.
    root = _st_project(tmp, "a13_noswallow")
    _st_write(root, "state.md", state(line(5), line(5), "\n" + ("TAIL PROSE. " * 500) + "\n"))
    code, out, _err = _st_run(["--root", str(root), "--file", "state.md"])
    ck("A13        tiny fields + 6KB tail -> silent (the value stops at the field)",
       _st_content(out, "A13"), [])
    ck("A13        no-swallow arm -> exit 0", code, EXIT_OK)

    # ---- THE BOUNDARY, both sides. `> ceiling`, so exactly-at is SILENT and one over FIRES.
    # Sizes are computed from the fixture, never typed: the span is the continuation line's own
    # bytes, terminator included, which is what `_st_write` puts on disk.
    at = STATE_FIELD_CEILING_BYTES - 3            # 2 indent + n + 1 newline == ceiling
    root = _st_project(tmp, "a13_at")
    _st_write(root, "state.md", state(line(5), line(at)))
    _code, out, _err = _st_run(["--root", str(root), "--file", "state.md"])
    ck("A13        active_track exactly AT the ceiling -> silent", _st_content(out, "A13"), [])

    root = _st_project(tmp, "a13_over")
    _st_write(root, "state.md", state(line(5), line(at + 1)))
    _code, out, _err = _st_run(["--root", str(root), "--file", "state.md"])
    hits = _st_content(out, "A13")
    ck("A13        one byte OVER the ceiling -> fires", bool(hits), True)
    ck("A13        the finding names the FIELD, not an entry id",
       [h[2] for h in hits], ["active_track"])

    # ---- `goal` is covered too (the maintainer, 2026-08-17). Same ceiling, same code path, other field.
    root = _st_project(tmp, "a13_goal")
    _st_write(root, "state.md", state(line(at + 1), line(5)))
    _code, out, _err = _st_run(["--root", str(root), "--file", "state.md"])
    ck("A13        an oversize `goal` fires too", [h[2] for h in _st_content(out, "A13")],
       ["goal"])

    # ---- INLINE value is skipped, not measured. It cannot accumulate across lines, so it cannot
    # exhibit the failure; reporting on a shape that cannot fail is decoration.
    root = _st_project(tmp, "a13_inline")
    _st_write(root, "state.md",
              state(line(5), "").replace("active_track: |\n", "active_track: " + "y" * 3000 + "\n"))
    _code, out, _err = _st_run(["--root", str(root), "--file", "state.md"])
    ck("A13        a 3000-byte INLINE value is skipped, not measured",
       _st_content(out, "A13"), [])

    # ---- IT CANNOT ABORT (C-53). An oversize field on an otherwise valid tree still exits 0.
    root = _st_project(tmp, "a13_never_aborts")
    _st_write(root, "state.md", state(line(5), line(at + 4000)))
    code, out, _err = _st_run(["--root", str(root), "--file", "state.md"])
    ck("A13        fires", bool(_st_content(out, "A13")), True)
    ck("A13        ... and the save is NOT blocked -- exit stays 0 (C-53)", code, EXIT_OK)

    # ---- SCOPE. The same oversize block in a file that is NOT state.md fires nothing: A13 is
    # about the generated cursor's two fields, not about block scalars in general.
    root = _st_project(tmp, "a13_scope")
    _st_write(root, "backlog.md",
              "# Backlog\n\n## M1 - ship it\nkind: plan\n"
              "lifetime: conditional - until it ships\n"
              "active_track: |\n" + line(at + 1))
    _code, out, _err = _st_run(["--root", str(root), "--file", "backlog.md"])
    ck("A13        an oversize block in backlog.md -> A13 silent (state.md only)",
       _st_content(out, "A13"), [])


def _st_arms_a14(ck, tmp):
    """A14 -- reference integrity. THE TWO CONTROLS ARE THE TEST, not the firing arm.

    An arm asserting only that A14 fires would pass just as well against a limb that warns on
    EVERYTHING, and warning on everything is this check's one real failure mode: the same corpus
    reports 98 dangling ids and 85 collisions over the `c4` scope, of which 18 and 0 respectively
    are things a save can act on. So each firing arm is paired with a silent one built from the
    exact shape that must NOT fire.
    """
    HOT = ("<!-- ash_save v4 | save#1 -->\n# t - architecture\n\n"
           "## D1 - the write path\nkind: spec\nlifetime: conditional - until x\n%s\n")
    STATE = ("<!-- ash_save v4 | save#1 -->\n# t - state\n\nschema_version: v4\nproject: t\n"
             "phase: implementation\nsave: 1\nlast_save_ts: 2026-08-17 00:00:00 PDT\n"
             "status: active\ngoal: |\n  t\nactive_track: |\n  t\n"
             "repo_root: (not resolved yet)\nhot_state: scaffolded\nbacklog_order: []\n"
             "no_demote_streak: 0\n")

    def mk(name, arch_body, extra=None):
        root = _st_project(tmp, name)
        (root / "_archive").mkdir(parents=True, exist_ok=True)
        _st_write(root, "state.md", STATE)
        _st_write(root, "architecture.md", HOT % arch_body)
        for (rel, text) in (extra or {}).items():
            _st_write(root, rel, text)
        return root

    # ---- FIRES: a HOT file cites an id defined nowhere.
    root = mk("a14_hot", "The contract D99 states is implemented here.")
    code, out, _err = _st_run(["--root", str(root)])
    hits = _st_content(out, "A14")
    ck("A14        a HOT citation of an undefined id fires", [h[2] for h in hits], ["D99"])
    ck("A14        ...and names the citing file:line", hits and hits[0][0], "architecture.md")
    ck("A14        ...and NEVER moves the exit code (C-53)", code, EXIT_OK)

    # A dangling reference cited only from historical storage is not a hot-file
    # repair action and must not drown out actionable live references.
    root = mk("a14_cold", "Nothing dangles here.",
              {"_archive/misc.md": "# misc\n\nD99 was discussed at length.\n",
               "checkpoints/checkpoint-1.md": "# cp\n\nD99 was decided here.\n"})
    code, out, _err = _st_run(["--root", str(root)])
    ck("A14 CONTROL a COLD-only dangling citation is silent", _st_content(out, "A14"), [])
    ck("A14 CONTROL ...and exit stays 0", code, EXIT_OK)

    # ---- CONTROL 2: the DESIGNED collision, IN THE SHAPE THAT ACTUALLY OCCURS. The first
    # version of this arm used a BYTE-IDENTICAL preimage. Classifying all 85 collisions on the
    # live corpus (2026-08-29, prompted by a review finding about a different half) showed **0 of
    # 85 are byte-identical** and 77 are one hot definition plus one archive preimage with
    # DIFFERENT bytes -- the hot copy has been rewritten since the snapshot was taken. So the
    # control was exercising a shape the corpus never produces while the real one went untested.
    # The preimage below therefore carries the ORIGINAL, longer text.
    root = mk("a14_collision", "Nothing dangles here.",
              {"_archive/architecture-preimage.md":
                   "<!-- ash_save v4 -->\n# preimage\n\n## D1 - the write path\nkind: spec\n"
                   "lifetime: conditional - until x\nThe pre-migration body, which the hot copy "
                   "no longer carries.\n"})
    code, out, _err = _st_run(["--root", str(root)])
    ck("A14 CONTROL a DESIGNED id collision is NOT reported", _st_content(out, "A14"), [])
    ck("A14 CONTROL ...and exit stays 0", code, EXIT_OK)

    # ---- THE DECLARED-EXTERNAL REGISTER. A migrated folder carries one; each line names an
    # id somebody adjudicated, in writing, as defined outside this project. Without it A14
    # reports as unresolved exactly the citations a human already decided resolve elsewhere,
    # and there is no flag to tell it otherwise -- step 7d passes none.
    root = mk("a14_declared", "The contract D99 states is implemented here.",
              {A14_DECLARED_EXTERNAL: "# a comment, and a blank line follows\n\nD99\n"})
    code, out, _err = _st_run(["--root", str(root)])
    ck("A14        a DECLARED-EXTERNAL id is not reported as dangling",
       _st_content(out, "A14"), [])
    ck("A14        ...and the suppression is DISCLOSED, never silent",
       "DECLARED EXTERNAL: 1 hot-cited dangling id" in out, True)
    ck("A14        ...naming the register that suppressed it",
       A14_DECLARED_EXTERNAL in out, True)
    ck("A14        ...and naming the id, so the reader can check the adjudication",
       "external -- D99." in out, True)
    ck("A14        ...and still never moves the exit code (C-53)", code, EXIT_OK)

    # ---- CONTROL 3: a register that declares a DIFFERENT id. This is the arm that carries
    # the four above: every one of them passes just as well against a limb that suppresses on
    # the register's mere PRESENCE, or on everything, and only a register that must NOT match
    # can tell those apart.
    root = mk("a14_declared_other", "The contract D99 states is implemented here.",
              {A14_DECLARED_EXTERNAL: "D98\n"})
    code, out, _err = _st_run(["--root", str(root)])
    ck("A14 CONTROL a register declaring another id suppresses nothing",
       [h[2] for h in _st_content(out, "A14")], ["D99"])
    ck("A14 CONTROL ...and emits no suppression note", "DECLARED EXTERNAL:" in out, False)

    # ---- CONTROL 4: the id present but COMMENTED OUT. A register may carry comment lines,
    # and a reader that did not strip `#` would admit them as identifiers -- matching
    # nothing, while still suppressing correctly on every BARE id line. That partial failure
    # is invisible from THIS arm's output, which is exactly why 4b below exists: this arm
    # cannot fail on it and 4b can.
    root = mk("a14_declared_comment", "The contract D99 states is implemented here.",
              {A14_DECLARED_EXTERNAL: "# D99\n"})
    _code, out, _err = _st_run(["--root", str(root)])
    ck("A14 CONTROL a COMMENTED id declares nothing",
       [h[2] for h in _st_content(out, "A14")], ["D99"])

    # ---- CONTROL 4b: a TRAILING comment on a REAL declaration -- the arm that can actually
    # fail if `#` stops opening a comment. 4a above cannot: an unstripped `# D99` is a garbage
    # token matching no id, which produces the SAME output as stripping the line away.
    # Measured 2026-09-02 by mutating the split out of `recall.load_ignore_file` -- 4a
    # stayed green (R-CLAUDE-49: an arm named after the right case, asserting a property the
    # fault does not disturb). This one reddens, and it is why the pair exists.
    root = mk("a14_declared_trailing", "The contract D99 states is implemented here.",
              {A14_DECLARED_EXTERNAL: "D99  # adjudicated against the durable backup\n"})
    code, out, _err = _st_run(["--root", str(root)])
    ck("A14        a TRAILING comment does not corrupt the id it follows",
       _st_content(out, "A14"), [])
    ck("A14        ...and that suppression is disclosed like any other",
       "DECLARED EXTERNAL: 1 hot-cited dangling id" in out, True)

    # ---- CONTROL 5: NO register at all -- the ordinary case on every scaffolded folder.
    # An absence must be silent: a warning on every clean project in the fleet is noise, and
    # this arm is what stops a future edit turning the absence into one.
    root = mk("a14_declared_absent", "The contract D99 states is implemented here.")
    _code, out, _err = _st_run(["--root", str(root)])
    ck("A14 CONTROL an ABSENT register is silent about itself",
       "DECLARED EXTERNAL" in out or "REGISTER UNREADABLE" in out, False)
    ck("A14 CONTROL ...and the dangling id is still reported",
       [h[2] for h in _st_content(out, "A14")], ["D99"])

    # ---- PRESENT BUT UNREADABLE. Found by cross-family review 2026-09-03 as a must-fix: the
    # old `is_file()` guard reported a DIRECTORY at the register's path as ABSENT, so a
    # register that could not be read went silent and the report looked ordinary. NOT a
    # hypothetical -- a directory sitting where a data file belongs is a shape this corpus has
    # produced before. The fixture builds the real thing rather than injecting an exception, so
    # the arm meets whatever error this platform actually raises.
    root = mk("a14_declared_dir", "The contract D99 states is implemented here.")
    (root / A14_DECLARED_EXTERNAL).mkdir(parents=True, exist_ok=True)
    code, out, _err = _st_run(["--root", str(root)])
    ck("A14        an UNREADABLE register is reported, never read as absent",
       "REGISTER UNREADABLE" in out, True)
    ck("A14        ...and NOTHING is suppressed, so the dangling id still fires",
       [h[2] for h in _st_content(out, "A14")], ["D99"])
    ck("A14        ...and even this never moves the exit code (C-53)", code, EXIT_OK)

    # ---- THE EXCEPTION BOUNDARY. A14 is warning-only, so ANY escape changes the exit code:
    # `main` turns an uncaught exception into exit 7 and a warning-only rule then BLOCKS A SAVE.
    # The arm injects a failure into the scan and asserts the save still completes. Cross-family
    # review found the original narrow catch; this is what holds the repair.
    root = mk("a14_raises", "The contract D99 states is implemented here.")
    saved = citemap.build_map
    try:
        def _boom(*_a, **_k):
            raise RuntimeError("injected -- not in the original except tuple")
        citemap.build_map = _boom
        code, out, _err = _st_run(["--root", str(root)])
    finally:
        citemap.build_map = saved
    ck("A14        an EXCEPTION becomes a warning, never an exit code", code, EXIT_OK)
    ck("A14        ...and says the verdict was not established",
       "REFERENCE INTEGRITY NOT ESTABLISHED" in out, True)
    ck("A14        ...and names the exception type so it can be chased",
       "RuntimeError" in out, True)

    # ---- VOID, not clean. An open fence makes every heading below it invisible, and the
    # truncated scan's own answer to "does anything dangle" is the CLEAN one.
    root = mk("a14_fence", "The contract D99 states is implemented here.\n\n```text\nopen\n")
    code, out, err = _st_run(["--root", str(root)])
    ck("A14        an open fence makes the verdict VOID, never clean",
       "REFERENCE INTEGRITY VOID" in (out + err), True)
    ck("A14        ...and the void verdict still does not abort the save",
       code in (EXIT_OK, EXIT_SCAN_INTEGRITY), True)

    # ---- SCOPE: a narrowed run has not read the definitions, so it may not judge. It SAYS so
    # rather than returning a clean verdict over a corpus it never opened.
    root = mk("a14_scoped", "The contract D99 states is implemented here.")
    _code, out, _err = _st_run(["--root", str(root), "--file", "architecture.md"])
    ck("A14        --file does NOT emit a dangling verdict", _st_content(out, "A14"), [])
    ck("A14        ...and says why, rather than being silently clean",
       "skipped under --file" in out, True)


def _st_arms_invariants(ck, _tmp):
    """Runs LAST. An assertion over every code the whole suite observed."""
    seen = sorted(set(_ST_CODES))
    ck("invariant  no normal invocation returned EXIT_SELFTEST (1)", EXIT_SELFTEST in seen, False)
    ck("invariant  no arm hit EXIT_INTERNAL (7)", EXIT_INTERNAL in seen, False)
    ck("invariant  every observed code is in the published table",
       [c for c in seen if c not in (EXIT_OK, EXIT_USAGE, EXIT_ABORT, EXIT_SCAN_INTEGRITY)], [])
    ck("invariant  all four expected codes were actually exercised", seen,
       [EXIT_OK, EXIT_USAGE, EXIT_ABORT, EXIT_SCAN_INTEGRITY])


# --------------------------------------------------------------------------- selftest driver

# --------------------------------------------------------------------------- arms: regressions
#
# One arm per defect found by adversarial review on 2026-08-03 and fixed. Every one of these was
# a WRONG VERDICT, not a style point: five reported a clean PASS over content a rule in the
# closed set names, four aborted content the spec mandates. An unpinned regression comes back,
# and four of these were previously "documented by measurement" by arms written to CONFIRM the
# behaviour -- a fixture written to confirm a decision cannot also probe it.

def _st_arms_regressions(ck, tmp):
    # ---- R1: the third read path. `resolve_current_save` discarded load_doc's open_line, so a
    # fenced state.md under --file scoping (which drops state.md from the ENTRY scan set) lost
    # exit 6 AND silently deleted A6's renewal ground by making current_save None.
    def _renewal_project(name, state_body):
        root = _st_project(tmp, name)
        _st_write(root, "state.md", state_body)
        _st_write(root, "active.md",
                  "# Active\n"
                  "\n"
                  "## R1 · Broker restarted before each deploy\n"
                  "kind: rules\n"
                  "lifetime: expiring\n"
                  "expires: 3 saves\n"
                  "save: 14\n"
                  "implication: broker restarted before each deploy\n"
                  "quote: \"restart the broker\" (save 9)\n"
                  "\n"
                  "Body.\n")
        _st_write(root, "_archive/active-expired.md",
                  "# Expired\n"
                  "\n"
                  "## RX1 · Broker restarted before each deploy\n"
                  "kind: rules\n"
                  "lifetime: expiring\n"
                  "implication: broker restarted before each deploy\n")
        return root

    root = _renewal_project("x_state_ok", "# State\n\nsave: 14\n")
    code, out, _err = _st_run(["--root", str(root), "--file", "active.md"])
    ck("read-path  control: clean state.md -> the A6 fires under --file", code, EXIT_ABORT)
    ck("read-path  control: the save# came from state.md", "source: state.md" in out, True)

    root = _renewal_project("x_state_fenced", "# State\n\n```text\nsave: 14\n")
    code, out, _err = _st_run(["--root", str(root), "--file", "active.md"])
    ck("read-path  fenced state.md under --file -> exit 6, not 0", code, EXIT_SCAN_INTEGRITY)
    ck("read-path  ...and the truncation is REPORTED", "SCAN INTEGRITY" in out, True)

    code, out, _err = _st_run(["--root", str(root), "--file", "active.md", "--report"])
    ck("read-path  ...6 beats --report on the auxiliary path too", code, EXIT_SCAN_INTEGRITY)

    code, out, _err = _st_run(["--root", str(root), "--file", "active.md", "--save", "14"])
    ck("read-path  --save supplies a number, not evidence the file was read whole",
       code, EXIT_SCAN_INTEGRITY)

    # The sibling auxiliary read, same class, scoped so it is NOT in the entry scan set. This is
    # the arm that was missing entirely: every fence arm wrote into a normally-scanned file, so
    # the suite could not tell "6 fired because the auxiliary guard caught it" from "6 fired
    # because the file was also scanned". Verified by mutation: deleting either guard now fails.
    root = _st_project(tmp, "x_expired_fenced")
    _st_write(root, "CONSTRAINTS.md",
              "# Constraints\n\n## C1 · A rule\nkind: rules\nlifetime: permanent\n")
    _st_write(root, "_archive/active-expired.md", "# Expired\n\n```\n## RX2 · dangling\n")
    code, out, _err = _st_run(["--root", str(root), "--file", "CONSTRAINTS.md"])
    ck("read-path  fenced expired archive under --file -> exit 6",
       code, EXIT_SCAN_INTEGRITY)

    # ---- R2: A6's renewal key contained the entry id, and a renewal is by construction a NEW
    # entry with a NEW id (S6 r3), so ground (c) could never fire in the shape it exists for.
    # Every previous A6 renewal arm wrote an explicit `implication:` on BOTH halves; the default
    # title path was exercised by none of the 251 checks. These omit the field on purpose.
    root = _st_project(tmp, "x_renew_ids")
    _st_write(root, "state.md", "# State\n\nsave: 20\n")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## R9 · Always run the linter before commit\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "save: 20\n"
              "quote: \"always run the linter before commit\" (save 9)\n")
    _st_write(root, "_archive/active-expired.md",
              "# Expired\n"
              "\n"
              "## R4 · Always run the linter before commit\n"
              "kind: rules\n"
              "lifetime: expiring\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("renewal    differing ids, no `implication:` -> A6 still fires", "A6" in _st_rules(out),
       True)
    ck("renewal    ...the message names the expired source", "active-expired.md" in out, True)

    # S6's own heading form, which carries the asserted save# IN the heading -- so the key used
    # to move even when the id did not. state.md says 146, matching the heading's `asserted
    # save#146`, because ground (c) evaluates on the save the entry declares it was asserted in:
    # the spec's entry text is reproduced verbatim and the CLOCK is what moves to meet it.
    root = _st_project(tmp, "x_renew_specform")
    _st_write(root, "state.md", "# State\n\nsave: 146\n")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## A-8 · asserted save#146 · expires save#149 · until the deploy lands\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "\n"
              "\"defer save deep while we're deep in building\"\n")
    _st_write(root, "_archive/active-expired.md",
              "# Expired\n"
              "\n"
              "## A-7 · asserted save#146 · expires save#149 · until the deploy lands\n"
              "kind: rules\n"
              "lifetime: expiring\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("renewal    S6 heading form: metadata segments do not break the key",
       "A6" in _st_rules(out), True)

    # ---- R3: the save# was scraped from the FIRST save-shaped token in the VERBATIM quote, so a
    # fresh quote that merely MENTIONS an older save was reported as stale -- a false statement
    # about the operator's own line. A declared field outranks the scrape; within the quote the
    # LAST token wins, because the provenance marker is appended at the end.
    root = _st_project(tmp, "x_quote_hijack")
    _st_write(root, "state.md", "# State\n\nsave: 14\n")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## R10 · Defer save deep\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "save: 14\n"
              "implication: defer save deep\n"
              "quote: \"we lost 130 ids in save 12's run, so defer save deep\" (said in save 14)\n")
    _st_write(root, "_archive/active-expired.md",
              "# Expired\n"
              "\n"
              "## RX3 · Defer save deep\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "implication: defer save deep\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("renewal    a FRESH quote that mentions an older save is not stale", code, EXIT_OK)
    ck("renewal    ...and no A6 was invented", "A6" in _st_rules(out), False)

    # THIS ARM'S EXPECTATION WAS INVERTED on 2026-08-03, deliberately. It used to assert that a
    # declared `save: 14` rescued a RENEWAL whose quote carries no save# of its own -- which is
    # precisely the self-certification A6 exists to refuse (see `_quote_borne_save`). R3's real
    # content, "a fresh quote that merely MENTIONS an older save is not stale", is carried by
    # `x_quote_hijack` above and is unaffected: there the save# sits in the quote's own trailing
    # marker. Here it sits nowhere but a field the agent wrote, so the renewal is not evidenced.
    root = _st_project(tmp, "x_quote_field_wins")
    _st_write(root, "state.md", "# State\n\nsave: 14\n")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## R11 · Defer save deep\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "save: 14\n"
              "implication: defer save deep\n"
              "quote: \"back in save 12 we lost 130 ids, so defer save deep\"\n")
    _st_write(root, "_archive/active-expired.md",
              "# Expired\n"
              "\n"
              "## RX4 · Defer save deep\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "implication: defer save deep\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("renewal    a declared `save:` does NOT evidence a renewal", code, EXIT_ABORT)
    ck("renewal    ...the rule is A6", _st_rules(out), ["A6"])
    ck("renewal    ...and the message says the quote carries no save# of its own",
       "no save# OF ITS OWN" in out, True)

    # The fix the message prescribes, applied verbatim: mark the QUOTE. Same entry otherwise.
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## R11 · Defer save deep\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "save: 14\n"
              "implication: defer save deep\n"
              "quote: \"back in save 12 we lost 130 ids, so defer save deep\" "
              "(said in save 14)\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("renewal    ...marking the quote itself clears it", code, EXIT_OK)
    ck("renewal    ...with no findings", _st_findings(out), [])

    # ---- R4: spec S6's ONLY worked active.md entry (spec.md:476) aborted A5 + A6. It puts the
    # expiry, the asserted save# and the quote in the HEADING and in a bare quoted line; the
    # field names `expires:` / `quote:` / `save:` appear NOWHERE in spec.md. R-A ruled the field
    # form for kind and lifetime only.
    root = _st_project(tmp, "x_spec_example")
    _st_write(root, "state.md", "# State\n\nsave: 146\n")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## A-7 · asserted save#146 · expires save#149 · until the example-project deploy lands\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "\"defer save deep while we're deep in building\"\n"
              "-> Skip Stage 2 maintenance. The save# counter will fire; that is expected.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("spec form  the spec's own worked active.md entry -> exit 0", code, EXIT_OK)
    ck("spec form  ...no A5 and no A6 were invented", _st_findings(out), [])

    # ---- R5: `expires:` bare-integer semantics were the OPPOSITE of the step-6a drain's
    # (`expires <= current_save`, spec S5.1 row 1). A validator and the drain it validates for
    # cannot read one field two ways.
    for (label, value, want) in (
        ("bare 17 at save 14 is 3 saves out -> clean", "expires: 17", EXIT_OK),
        ("bare 3 at save 14 is already expired, not an abort", "expires: 3", EXIT_OK),
        ("bare 40 at save 14 is 26 saves out -> A5", "expires: 40", EXIT_ABORT),
        ("`in 3 saves` is a bounded relative count", "expires: in 3 saves", EXIT_OK),
        ("`after 3 saves` too", "expires: after 3 saves", EXIT_OK),
        ("`3 saves (save 17)` too", "expires: 3 saves (save 17)", EXIT_OK),
        ("`20 saves` is still over the max", "expires: 20 saves", EXIT_ABORT),
    ):
        root = _st_project(tmp, "x_exp_" + re.sub(r"\W+", "_", value))
        _st_write(root, "active.md", _st_active(**{"expires": value}))
        code, _out, _err = _st_run(["--root", str(root), "--save", "14"])
        ck("expires    " + label, code, want)

    # ---- R6: a qualifier is not a second token. S2.2 REQUIRES a `conditional` to name its
    # event on the line, and that spec-compliant form aborted A3 with a message that ended by
    # telling the author to name the event they had just named.
    for (label, line, want) in (
        ("`conditional -- until channel B opens`", "lifetime: conditional -- until channel B opens",
         EXIT_OK),
        ("`conditional (until the pilot lands)`", "lifetime: conditional (until the pilot lands)",
         EXIT_OK),
        ("`conditional when the drain ships`", "lifetime: conditional when the drain ships",
         EXIT_OK),
        ("`conditional; event = the map rewrite`", "lifetime: conditional; event = the map rewrite",
         EXIT_OK),
        ("`for now` is still not a lifetime", "lifetime: for now", EXIT_ABORT),
    ):
        root = _st_project(tmp, "x_life_" + re.sub(r"\W+", "_", line)[:40])
        _st_write(root, "design.md",
                  "# Design\n\n## D50 · The ranker\nkind: spec\n" + line + "\n\nBody.\n")
        code, _out, _err = _st_run(["--root", str(root)])
        ck("qualifier  " + label, code, want)

    root = _st_project(tmp, "x_kind_qualifier")
    _st_write(root, "design.md",
              "# Design\n\n## D51 · The ranker\nkind: spec (S2.1 question 2)\n"
              "lifetime: conditional\n\nBody.\n")
    code, _out, _err = _st_run(["--root", str(root)])
    ck("qualifier  `kind: spec (S2.1 question 2)` -> exit 0", code, EXIT_OK)

    root = _st_project(tmp, "x_kind_amp")
    _st_write(root, "design.md",
              "# Design\n\n## D52 · The ranker\nkind: spec & rules\nlifetime: conditional\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("qualifier  `spec & rules` is still an unrecognised VALUE", _st_rules(out), ["A2"])
    ck("qualifier  ...and the message names what the author WROTE, not a fragment",
       "'spec & rules'" in out, True)

    # ONE tokeniser: A2 and A4 used to disagree over the same characters.
    root = _st_project(tmp, "x_unres_paren")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "<!-- UNRESOLVED -->\n"
              "\n"
              "## U30 · Delivery ordering\n"
              "kind: unresolved (spec or rules)\n"
              "lifetime: expiring\n"
              "expires: 3 saves\n"
              "\n"
              "<!-- /UNRESOLVED -->\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("tokeniser  `unresolved (spec or rules)` -> exit 0", code, EXIT_OK)
    ck("tokeniser  ...no fragment the author never wrote", "unresolved (spec'" in out, False)

    # ---- R7: A7 and A6 ground (a) are presence checks, so the natural way to write "there is
    # no gate" satisfied them.
    root = _st_project(tmp, "x_gate_none")
    _st_write(root, "_archive/backlog-deferred.md",
              "# Deferred\n\n## P30 · Rebuild the index\nkind: plan\nlifetime: conditional\n"
              "gate: none\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("placeholder `gate: none` names no condition -> A7", _st_rules(out), ["A7"])

    root = _st_project(tmp, "x_gate_real")
    _st_write(root, "_archive/backlog-deferred.md",
              "# Deferred\n\n## P31 · Rebuild the index\nkind: plan\nlifetime: conditional\n"
              "gate: when the broker pilot lands\n")
    code, _out, _err = _st_run(["--root", str(root)])
    ck("placeholder control: a real gate -> exit 0", code, EXIT_OK)

    root = _st_project(tmp, "x_gate_na")
    _st_write(root, "_archive/backlog-deferred.md",
              "# Deferred\n\n## P32 · Rebuild the index\nkind: plan\nlifetime: conditional\n"
              "gate: n/a -- just deferred\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("placeholder `n/a` wearing a qualifier is still a placeholder", _st_rules(out), ["A7"])

    root = _st_project(tmp, "x_exp_hedged")
    _st_write(root, "active.md", _st_active(**{"expires": "expires: 2 saves ish"}))
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("expires    a hedged count is not a bounded save count", _st_rules(out), ["A5"])

    root = _st_project(tmp, "x_quote_tbd")
    _st_write(root, "active.md", _st_active(**{"quote": "quote: TBD (save 14)"}))
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("placeholder `quote: TBD` is not provenance -> A6", _st_rules(out), ["A6"])

    # ---- R8: the file's declared LIFETIME (S3.1) fed only the report's display string. It
    # cannot be an abort (the set is CLOSED), so it is a warning -- and the exit must not move.
    root = _st_project(tmp, "x_life_mismatch")
    _st_write(root, "active.md", _st_active(**{"lifetime": "lifetime: permanent"}))
    code, out, _err = _st_run(["--root", str(root), "--save", "14"])
    ck("lifetime   `permanent` in active.md -> WARNED", "whose declared lifetime is" in out, True)
    ck("lifetime   ...and the exit code does not move", code, EXIT_OK)

    # ---- R8b: A SUPPRESSION AND THE THING IT DEFERS TO MUST HAVE THE SAME REACH, AND THIS
    # ARM EXISTS BECAUSE THEY DID NOT. A1's lifetime half owns `expiring` outside active.md
    # and the warning above yields to it -- but A1 bails on a GENERATED-exempt file, and the
    # first version of that yield did not, so `state.md` and `manifest.md` reported the
    # condition ZERO times at exit 0 where the previous build warned.
    # THE EXTERNAL GATE HAD AN ARM FOR THIS FILE CLASS AND IT STAYED GREEN THROUGHOUT: it
    # asserted only that no ABORT fires and never asked whether the WARNING survived. An arm
    # named after the right case reads exactly like coverage. The question that finds this
    # class is: where do the two predicates DISAGREE -- and test that intersection.
    for _gen, _k in (("state.md", "plan"), ("manifest.md", "rules")):
        root = _st_project(tmp, "x_gen_expiring_" + _gen.split(".")[0])
        _st_write(root, _gen,
                  "# generated\n\n## GEN-1 - a generated entry\n"
                  "kind: %s\nlifetime: expiring\n" % _k)
        code, out, _err = _st_run(["--root", str(root)])
        ck("generated  single `expiring` in %s is still REPORTED" % _gen, "GEN-1" in out, True)
        ck("generated  ...and %s does not abort (it is exempt)" % _gen, code, EXIT_OK)

    # ---- R9: A1's remedy for `record` told the author to move it to a file that does not exist.
    root = _st_project(tmp, "x_record")
    _st_write(root, "design.md",
              "# Design\n\n## D53 · What we tried in save 9\nkind: record\nlifetime: permanent\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("record     A1 fires", "A1" in _st_rules(out), True)
    ck("record     ...and the message does not name a file that does not exist",
       "no hot file to move it to" in out, True)

    # ---- R10: T-2's precondition, as S2.4 writes it: "when kinds AGREE but lifetimes do not".
    root = _st_project(tmp, "x_t2_kinds_disagree")
    _st_write(root, "design.md",
              "# Design\n\n## D54 · The ranker\nkind: spec\nkind: plan\n"
              "lifetime: permanent\nlifetime: expiring\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("T-2        kinds disagree -> no lifetime resolution is reported",
       "LIFETIME RESOLUTION" in out, False)
    ck("T-2        ...A8 is what fires", "A8" in _st_rules(out), True)

    root = _st_project(tmp, "x_t2_kinds_agree")
    _st_write(root, "design.md",
              "# Design\n\n## D55 · The ranker\nkind: spec\n"
              "lifetime: permanent\nlifetime: expiring\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("T-2        kinds agree -> the resolution IS reported", "LIFETIME RESOLUTION" in out, True)
    ck("T-2        ...and it takes the SHORTER lifetime", "-> `expiring`" in out, True)

    # ---- R11: innermost field attachment. One `gate:` under a banner used to satisfy A7 for
    # every deferred entry beneath it.
    root = _st_project(tmp, "x_gate_masked")
    _st_write(root, "_archive/backlog-deferred.md",
              "# Deferred\n"
              "\n"
              "## Deferred to Phase 2\n"
              "\n"
              "### P40 · One\n"
              "kind: plan\n"
              "lifetime: conditional\n"
              "gate: when the drain ships\n"
              "\n"
              "### P41 · Two\n"
              "kind: plan\n"
              "lifetime: conditional\n"
              "\n"
              "### P42 · Three\n"
              "kind: plan\n"
              "lifetime: conditional\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("attach     one `gate:` does not cover its siblings", _st_rules(out), ["A7"])
    ck("attach     ...both ungated entries are named", _st_ids(out), ["P41", "P42"])


# --------------------------------------------------------------------------- arms: A6 renewal
#
# A6 GROUND (c) IS THE #57 REPLAY RULE (S6 r3) and it did not fire on the shape it exists for.
# Two defects, reproduced by the orchestrator on 2026-08-03 and both at exit 0 over a real
# renewal -- an expired entry restated verbatim under a NEW id carrying its ORIGINAL save-1
# quote:
#
#   D1  the match key was built from the heading, and a heading made only of the id and S6's
#       bookkeeping folds to the same degenerate string for every entry -- it matched, but on
#       nothing. The entry's real content, `-> Skip the gate.`, was never read. (`_implication_source`)
#   D2  freshness was read from `asserted:` / `said-in:` / `save:` / the heading's `asserted
#       save#N` -- fields the AGENT writes, in the save being validated. A rule that exists to
#       stop the agent renewing cannot take the agent's word for it. (`_quote_borne_save`)
#
# The arms below pin BOTH DIRECTIONS. The expensive half is the false-positive half: an A6 that
# fires on a legitimate renewal blocks real saves, and a blocked save gets the checker turned off.

# The orchestrator's fixture, byte-for-byte in shape. NO `# Active` title, S6's heading form,
# the implication on its own `->` line, the quote carrying the save it was ACTUALLY said in.
_ST_RENEW_EXPIRED = (
    "## A-1 · asserted save#1 · expires save#4\n"
    "kind: rules\n"
    "lifetime: expiring\n"
    "quote: \"ship the gate\" (save 1)\n"
    "save: 1\n"
    "expires: save 4\n"
    "\n"
    "-> Skip the gate.\n"
)


def _st_arms_renewal(ck, tmp):
    # ---- fixture g: the renewal declares NOTHING about itself. Its only freshness claim is
    # S6's own `asserted save#5` in the heading -- which is the agent's, not the user's.
    root = _st_project(tmp, "renew_g")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 1)\n"
              "expires: save 8\n"
              "\n"
              "-> Skip the gate.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 renew   g: stale quote under a NEW id -> exit 3", code, EXIT_ABORT)
    ck("A6 renew   g: the rule is A6", _st_rules(out), ["A6"])
    ck("A6 renew   g: the renewing entry is named", _st_ids(out), ["A-2"])
    ck("A6 renew   g: the expired source is named", "_archive/active-expired.md:1" in out, True)
    ck("A6 renew   g: `asserted save#5` in the heading does not certify freshness",
       "its quote is from save 1, not the current save 5" in out, True)

    # ---- fixture h: the same renewal with every self-declared field set to the current save.
    # If ANY of these could answer the freshness question, S6 r3 would be self-certifying.
    root = _st_project(tmp, "renew_h")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 1)\n"
              "asserted: save 5\n"
              "said-in: save 5\n"
              "save: 5\n"
              "expires: save 8\n"
              "\n"
              "-> Skip the gate.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 renew   h: `asserted:`+`said-in:`+`save:` all current -> still exit 3",
       code, EXIT_ABORT)
    ck("A6 renew   h: the rule is A6", _st_rules(out), ["A6"])
    ck("A6 renew   h: the entry is named", _st_ids(out), ["A-2"])

    # ---- THE EXPENSIVE ARM. A GENUINE renewal: same implication, and the quote itself carries
    # the CURRENT save#. This must pass. An A6 that fires here blocks real saves.
    root = _st_project(tmp, "renew_genuine")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (said in save 5)\n"
              "expires: save 8\n"
              "\n"
              "-> Skip the gate.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 renew   GENUINE renewal, quote at the CURRENT save -> exit 0", code, EXIT_OK)
    ck("A6 renew   GENUINE renewal -> no findings at all", _st_findings(out), [])

    # The same, in S6's own quote form: a bare quoted body line with the marker after it and no
    # `quote:` field anywhere. The genuine path must not depend on the field form.
    root = _st_project(tmp, "renew_genuine_bare")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "\"ship the gate\" (said in save 5)\n"
              "\n"
              "-> Skip the gate.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 renew   GENUINE renewal in S6's bare-quote form -> exit 0", code, EXIT_OK)
    ck("A6 renew   ...and no finding was invented", _st_findings(out), [])

    # ---- an implication that matches NOTHING expired. Stale quote, new id, same file shape --
    # every ingredient of a renewal EXCEPT the one that defines it.
    root = _st_project(tmp, "renew_nomatch")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 1)\n"
              "expires: save 8\n"
              "\n"
              "-> Run the linter before every commit.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 renew   implication matching nothing expired -> exit 0", code, EXIT_OK)
    ck("A6 renew   ...no A6 invented from a stale quote alone", _st_findings(out), [])

    # ---- FOLDING, BOTH DIRECTIONS. Whitespace, case, emphasis and punctuation must not decide
    # the answer; a different WORD must. A fuzzy matcher here invents aborts, so the near-miss
    # arm is as load-bearing as the match arm.
    root = _st_project(tmp, "renew_fold_match")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 1)\n"
              "expires: save 8\n"
              "\n"
              "->   **Skip   the Gate**!!!\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 renew   a trivially-reformatted restatement still MATCHES", code, EXIT_ABORT)
    ck("A6 renew   ...and it is A6 that fires", _st_rules(out), ["A6"])

    for (name, implication, label) in (
        ("renew_fold_plural", "-> Skip the gates.", "one letter different"),
        ("renew_fold_other", "-> Skip the gate only on release builds.", "a qualifying clause"),
    ):
        root = _st_project(tmp, name)
        _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
        _st_write(root, "active.md",
                  "## A-2 · asserted save#5 · expires save#8\n"
                  "kind: rules\n"
                  "lifetime: expiring\n"
                  "quote: \"ship the gate\" (save 1)\n"
                  "expires: save 8\n"
                  "\n"
                  "%s\n" % implication)
        code, out, _err = _st_run(["--root", str(root), "--save", "5"])
        ck("A6 renew   %s is NOT the same implication -> exit 0" % label, code, EXIT_OK)
        ck("A6 renew   ...%s: nothing invented" % label, _st_findings(out), [])

    # ---- D1's false-POSITIVE half. Two entries in S6's heading form whose headings hold nothing
    # but the id and the bookkeeping used to fold to the same key ("asserted expires") whatever
    # they said, so ground (c) matched on structure rather than content. With no implication text
    # anywhere the key is now None and ground (c) does not fire -- a miss, deliberately, because
    # A6 is an abort and a rule that fires on a guess is worse than one that misses.
    root = _st_project(tmp, "renew_metadata_only")
    _st_write(root, "_archive/active-expired.md",
              "## A-1 · asserted save#1 · expires save#4\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 1)\n")
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"do something entirely different\" (save 5)\n"
              "expires: save 8\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 renew   metadata-only headings do not collide into a renewal", code, EXIT_OK)
    ck("A6 renew   ...no A6 from a degenerate key", _st_findings(out), [])

    # ---- no expired archive at all. The corpus is empty, ground (c) cannot fire, and nothing
    # raises. `_archive/` does not exist either -- not an empty file, no file.
    root = _st_project(tmp, "renew_no_archive")
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 1)\n"
              "expires: save 8\n"
              "\n"
              "-> Skip the gate.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 renew   no `_archive/active-expired.md` at all -> exit 0", code, EXIT_OK)
    ck("A6 renew   ...and no crash (7 is never returned)", code == EXIT_INTERNAL, False)
    ck("A6 renew   ...and no finding", _st_findings(out), [])

    # An expired archive that EXISTS and holds no entry: same verdict, different path through
    # `load_expired_implications`.
    root = _st_project(tmp, "renew_empty_archive")
    _st_write(root, "_archive/active-expired.md", "# Expired\n\nNothing has expired yet.\n")
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 1)\n"
              "expires: save 8\n"
              "\n"
              "-> Skip the gate.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 renew   an EMPTY expired archive -> exit 0", code, EXIT_OK)
    ck("A6 renew   ...still SKIPPED, not entry-validated", "A6's renewal corpus" in out, True)

    # ---- L1 (word numerals) and the vacuous `gate:`, both from the prior round's NOT-FIXED list.
    root = _st_project(tmp, "l1_word_expiry")
    _st_write(root, "state.md", "# State\n\nsave: 14\n")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## T1 · A directive\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "save: 14\n"
              "quote: \"do it\" said in save 14\n"
              "expires: three saves\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("word num   `expires: three saves` is 3 saves, not UNBOUNDED", code, EXIT_OK)
    ck("word num   ...no A5 invented", _st_findings(out), [])

    root = _st_project(tmp, "l1_word_over_max")
    _st_write(root, "state.md", "# State\n\nsave: 14\n")
    _st_write(root, "active.md",
              "# Active\n"
              "\n"
              "## T2 · A directive\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "save: 14\n"
              "quote: \"do it\" (said in save 14)\n"
              "expires: in ten saves\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("word num   `expires: in ten saves` is exactly the max -> exit 0", code, EXIT_OK)

    root = _st_project(tmp, "gate_prose")
    _st_write(root, "_archive/backlog-deferred.md",
              "# Deferred\n"
              "\n"
              "## P1 · x\n"
              "kind: plan\n"
              "lifetime: conditional\n"
              "\n"
              "Body.\n"
              "\n"
              "Gate: we never worked out what would bring this back.\n")
    code, out, _err = _st_run(["--root", str(root)])
    ck("gate prose a `gate:` that disclaims having one -> exit 3", code, EXIT_ABORT)
    ck("gate prose ...the rule is A7", _st_rules(out), ["A7"])
    ck("gate prose ...and the message says it names no condition",
       "names no condition" in out, True)

    # The control the disclaimer detector must not eat: ordinary prose gates that DO name a
    # condition, including a vague one. A vague gate is the DRAIN's call (D81), not an abort here.
    for (name, gate) in (
        ("gate_ok_named", "gate: when the broker pilot lands"),
        ("gate_ok_id", "gate: P3 closes"),
        ("gate_ok_vague", "gate: after we have talked to the team about it"),
        ("gate_ok_explicit_go", "gate: the drain ships AND a separate explicit go"),
    ):
        root = _st_project(tmp, name)
        _st_write(root, "_archive/backlog-deferred.md",
                  "# Deferred\n\n## P1 · x\nkind: plan\nlifetime: conditional\n%s\n" % gate)
        code, out, _err = _st_run(["--root", str(root)])
        ck("gate prose control `%s` -> exit 0" % gate, code, EXIT_OK)


# ------------------------------------------------ arms: A6 ground (c)'s EVALUATION MOMENT

def _st_arms_a6_moment(ck, tmp):
    """The 2026-08-03 ruling: ground (c) fires ONLY on the save an entry is newly asserted in.

    `A >= C` is the GATE (the entry's declared asserted save# has REACHED the current save -- it
    is being asserted now, or claims a save that has not happened yet), `Q != C` is
    the EVIDENCE (the save# the USER'S QUOTE itself carries). See `rule_a6_provenance` for why
    the asymmetry is what keeps this from being self-certification. These arms pin both halves
    and the two skip cases; the STILL-CAUGHT half lives in `_st_arms_renewal` (fixtures g and h,
    `--save 5`), which is where the original critical was pinned and where it stays.
    """
    # ---- THE ACCEPTANCE TEST FOR THE RULING, and the reason it exists. ONE FILE, BYTE-IDENTICAL
    # ACROSS THREE RUNS: a LEGITIMATE renewal made at save 5 -- the user's own quote carries
    # `(said in save 5)` -- expiring at save 8. Measured before the gate, same bytes on disk:
    # `--save 5` exit 0, `--save 6` exit 3, `--save 7` exit 3. The rule re-fired on every
    # subsequent save until the entry expired, and the ONLY way to clear it was to rewrite the
    # user's verbatim quote to a save it was not said in -- the rule that protects verbatim
    # provenance demanding that provenance be falsified. If `--save 6` ever aborts here again,
    # the gate has been removed. This triple is the acceptance test; nothing else replaces it.
    root = _st_project(tmp, "moment_carry")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (said in save 5)\n"
              "expires: save 8\n"
              "\n"
              "-> Skip the gate.\n")
    for save in (5, 6, 7):
        code, out, _err = _st_run(["--root", str(root), "--save", str(save)])
        ck("A6 moment  genuine renewal, UNCHANGED file, --save %d -> exit 0" % save,
           code, EXIT_OK)
        ck("A6 moment  ...--save %d: no finding at all" % save, _st_findings(out), [])
        ck("A6 moment  ...--save %d: A6 did not re-fire" % save, "A6" in _st_rules(out), False)

    # The DELIBERATE cost of having an evaluation moment, pinned so nobody rediscovers it as a
    # bug: fixture g's STALE renewal (quote from save 1, entry declares `asserted save#5`) aborts
    # at save 5 -- and only at save 5. At save 6 the entry no longer claims to be newly asserted,
    # so the gate is shut and ground (c) does not evaluate. That is not a hole in practice: the
    # abort BLOCKS save 5, so an entry in this state cannot reach save 6 through the sanctioned
    # path. A6 grounds (a)/(b) and A5's bounded horizon still apply on every save.
    root = _st_project(tmp, "moment_stale_then_after")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 1)\n"
              "expires: save 8\n"
              "\n"
              "-> Skip the gate.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 moment  stale renewal ON its asserted save -> exit 3", code, EXIT_ABORT)
    ck("A6 moment  ...the rule is A6", _st_rules(out), ["A6"])
    code, out, _err = _st_run(["--root", str(root), "--save", "6"])
    ck("A6 moment  ...and the gate is SHUT one save later", code, EXIT_OK)

    # ---- THE DODGE: understate A to shut the gate. It is not free, and the price is A5.
    # A5's horizon is measured FROM the asserted save# (`_horizon_base` prefers it over
    # everything), so understating A by k shortens the entry's legal hot life by exactly k.
    #
    # (i) The dodge that keeps the horizon a HONEST fresh assertion would have bought. An entry
    # asserted at save 5 may legally run to save 15 (`EXPIRY_MAX_SAVES` = 10). Claim `asserted
    # save#1` to shut the gate and the SAME `expires: save 15` is 14 saves out -- A5 fires. This
    # is the arm the ruling names: the dodge does not come out clean, A5 catches it.
    root = _st_project(tmp, "moment_dodge_full")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#1 · expires save#15\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 1)\n"
              "expires: save 15\n"
              "\n"
              "-> Skip the gate.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 moment  dodge keeping a full horizon -> NOT clean", code, EXIT_ABORT)
    ck("A6 moment  ...and A5 is the rule that caught it, not A6", _st_rules(out), ["A5"])
    ck("A6 moment  ...the message names the understated base it was measured from",
       "asserted save#" in out, True)

    # (ii) The dodge's own EXPIRY WINDOW closes as saves advance, which is the bound that makes
    # the asymmetry hold. `asserted save#1` caps the entry at save 11; at current save 12 there
    # is NO expiry that both keeps it hot and satisfies A5, so the dodge stops being available
    # rather than getting cheaper.
    root = _st_project(tmp, "moment_dodge_window")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#1 · expires save#13\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 1)\n"
              "expires: save 13\n"
              "\n"
              "-> Skip the gate.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "12"])
    ck("A6 moment  dodge at a distance -> the window is shut, A5 fires", code, EXIT_ABORT)
    ck("A6 moment  ...still A5", _st_rules(out), ["A5"])

    # (iii) THE MEASURED RESIDUAL, pinned as fact rather than as approval. A dodge that ALSO
    # accepts the shortened horizon -- `asserted save#1` with `expires: save 8` at save 5 -- is
    # 7 saves out, inside A5's max of 10, and comes out clean. It bought the agent 3 more hot
    # saves where an honest fresh assertion would have bought 10, and it can never buy more than
    # `1 + EXPIRY_MAX_SAVES`. Closing it is NOT available here: the abort set is CLOSED at
    # A1..A8, and a WARNING on every A < C renewal is exactly the per-save nag the ruling above
    # was written to delete. Recorded as a bounded, self-shrinking residual.
    root = _st_project(tmp, "moment_dodge_short")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#1 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 1)\n"
              "expires: save 8\n"
              "\n"
              "-> Skip the gate.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 moment  dodge PAYING the shortened horizon -> clean (measured residual)",
       code, EXIT_OK)

    # ---- A ABSENT: no `asserted:`/`said-in:`/`save:` field and no `asserted save#N` heading.
    # There is no gate to open, so the evaluation moment cannot be located and ground (c) is
    # SKIPPED -- with a NAMED warning, never a silent pass. This is fixture `fp18_head`'s shape:
    # two entries whose headings reduce to the same substantive segment.
    root = _st_project(tmp, "moment_no_asserted")
    _st_write(root, "_archive/active-expired.md",
              "## E-1 · Broker restart · expires save#4\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"restart the broker before each deploy\" (save 1)\n"
              "expires: save 4\n")
    _st_write(root, "active.md",
              "## A-4 · Broker restart · expires save#9\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"restart the broker when qdrant reindexes\" (said in save 3)\n"
              "expires: save 9\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 moment  no asserted save# -> ground (c) SKIPPED, not aborted", code, EXIT_OK)
    ck("A6 moment  ...no A6 invented", _st_findings(out), [])
    ck("A6 moment  ...NOT a silent 0: a warning was emitted", "WARNINGS (" in out, True)
    ck("A6 moment  ...the warning says the ground was not evaluated",
       "NOT EVALUATED" in out, True)
    ck("A6 moment  ...and it says why", "declares no assertion save#" in out, True)
    ck("A6 moment  ...the warning NAMES the entry", "[A-4]" in out, True)

    # The same entry with the assertion save# supplied: the warning goes away and ground (c)
    # evaluates for real. Without this half the arm above cannot tell "warned" from "always
    # warns".
    _st_write(root, "active.md",
              "## A-4 · Broker restart · expires save#9\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "save: 5\n"
              "quote: \"restart the broker when qdrant reindexes\" (said in save 3)\n"
              "expires: save 9\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 moment  supplying the assertion save# opens the gate -> exit 3", code, EXIT_ABORT)
    ck("A6 moment  ...the rule is A6", _st_rules(out), ["A6"])
    ck("A6 moment  ...and the not-evaluated warning is gone", "NOT EVALUATED" in out, False)

    # ---- THE NORMALISER. `_normalise_implication` used to strip `save#N` out of the match key,
    # so `-> Defer the migration until save 4.` and `-> Defer the migration until save 12.` folded
    # onto "defer the migration until" and matched each other (measured 2026-08-03, fixture
    # `fp7_savestrip`, exit 3). Those are two different instructions with two different deadlines.
    # BOTH DIRECTIONS ARE PINNED: dropping the strip is only correct if a genuine restatement
    # still matches.
    root = _st_project(tmp, "moment_norm_differs")
    _st_write(root, "_archive/active-expired.md",
              "## E-1 · asserted save#1 · expires save#4\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"hold the migration\" (save 1)\n"
              "expires: save 4\n"
              "\n"
              "-> Defer the migration until save 4.\n")
    _st_write(root, "active.md",
              "## A-8 · asserted save#5 · expires save#9\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"hold the migration\" (save 1)\n"
              "expires: save 9\n"
              "\n"
              "-> Defer the migration until save 12.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("normaliser `until save 4` vs `until save 12` do NOT match -> exit 0", code, EXIT_OK)
    ck("normaliser ...and no A6 was invented from a stripped deadline",
       _st_findings(out), [])

    # The genuine restatement of the SAME deadline still matches -- same file, same stale save-1
    # quote, same `asserted save#5`, only the deadline made equal. If this stops firing, the
    # normaliser has been broken in the other direction.
    _st_write(root, "active.md",
              "## A-8 · asserted save#5 · expires save#9\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"hold the migration\" (save 1)\n"
              "expires: save 9\n"
              "\n"
              "-> Defer the migration until save 4.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("normaliser a genuine restatement of the SAME deadline still matches", code, EXIT_ABORT)
    ck("normaliser ...and it is A6 that fires", _st_rules(out), ["A6"])


# ------------------------------------------------ arms: the FUTURE-DATED assertion save#

def _st_arms_a6_future(ck, tmp):
    """Round 3 of the same lesson: a self-declared field may not shut A6's gate or stretch A5's.

    THE DEFECT, measured 2026-08-03 and reproduced by the orchestrator before the fix. The gate
    was `A == C`, so an entry declaring an assertion save# that HAS NOT HAPPENED skipped ground
    (c) at every current save below it -- and the same future A was A5's horizon base, so a
    100-save hot life read as a 6-save one:

        _archive/active-expired.md:  ## A-1 · asserted save#1 · expires save#4   "(save 1)"
        active.md:                   ## A-2 · asserted save#99 · expires save#105
                                     quote: "ship the gate" (save 1)   <- STALE, from save 1

        BEFORE:  --save 5 -> 0   --save 20 -> 0   --save 50 -> 0   --save 100 -> 0
        CONTROL, identical but honest (asserted save#5, expires save#8): --save 5 -> 3, A6.

    Two rules fooled by one lie: A6's gate (A(99) != C(5), so ground (c) never ran) and A5's
    horizon (105 - 99 = 6, "compliant", while the real hot life was 100 saves from now). Net
    effect: an instruction outliving the interest that created it, indefinitely, on a quote the
    user gave once at save 1 -- which is the exact failure `active.md` and gate L1 exist to
    prevent.

    NEITHER FIX ADDS AN ABORT. The set stays CLOSED at A1..A8. A future A is not itself a
    finding; it only loses the power to answer a question in the agent's own favour. See the
    PRINCIPLE block in `rule_a6_provenance`.
    """
    def _fut(expires, quote):
        return ("## A-2 · asserted save#99 · expires save#%d\n"
                "kind: rules\n"
                "lifetime: expiring\n"
                "quote: \"ship the gate\" (save %d)\n"
                "save: 99\n"
                "expires: save %d\n"
                "\n"
                "-> Skip the gate.\n" % (expires, quote, expires))

    # ---- FIX 1 + FIX 2 together, on the defect fixture itself. `--save 5` is the reproduction;
    # 20 and 50 are there because the defect's signature was that it did NOT decay with distance.
    root = _st_project(tmp, "future_defect")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md", _fut(105, 1))
    for save in (5, 20, 50):
        code, out, _err = _st_run(["--root", str(root), "--save", str(save)])
        ck("A6 future  future A + stale quote, --save %d -> exit 3" % save, code, EXIT_ABORT)
        ck("A6 future  ...--save %d: A6 is NAMED (the gate was forced open)" % save,
           "A6" in _st_rules(out), True)
        ck("A6 future  ...--save %d: A5 too (the horizon base was clamped)" % save,
           "A5" in _st_rules(out), True)
        ck("A6 future  ...--save %d: the A6 message names the stale quote's save" % save,
           "its quote is from save 1" in out, True)
    # `--save 99`: A has been REACHED. The gate is open on the `==` half now, and A5's horizon
    # (105 - 99 = 6) is genuinely compliant, so A6 fires ALONE. This is the arm that shows the
    # two fixes are independent -- clamping A5 is not what produces the A6.
    code, out, _err = _st_run(["--root", str(root), "--save", "99"])
    ck("A6 future  ...--save 99: A6 alone, A5's horizon is honest there", _st_rules(out), ["A6"])

    # ---- THE BOUNDARY, and it is a MEASURED RESIDUAL, not a pass. At `--save 100` the declared
    # A(99) is finally in the PAST, so the gate shuts by the 2026-08-03 ruling, and `expires
    # save#105` is 6 saves past base 99 -- inside A5's max of 10. The entry comes out clean.
    # THIS IS THE ACCEPTED PAST-DATED RESIDUAL VERBATIM (see `moment_dodge_short`), reached from
    # the other side: the future dodge does not survive, it DEGENERATES into the bounded one once
    # the save counter passes A. Its whole remaining life is C in [100, 105] -- 6 saves, strictly
    # inside `A + EXPIRY_MAX_SAVES`, and it dies with no way to renew (any new assertion has
    # A >= C, which opens the gate). Closing this needs a new abort (the set is CLOSED) or an
    # unmeasured heuristic (C-19 forbids). Pinned as fact so the boundary cannot move unseen.
    code, out, _err = _st_run(["--root", str(root), "--save", "100"])
    ck("A6 future  ...--save 100: degenerates to the ACCEPTED residual (measured, not approved)",
       code, EXIT_OK)
    code, out, _err = _st_run(["--root", str(root), "--save", "106"])
    ck("A6 future  ...--save 106: past its own expiry, the drain's job, not an abort",
       code, EXIT_OK)

    # ---- A FUTURE ASSERTION IS NOT ITSELF AN ABORT. Same future A(99), same expiry, but the
    # quote carries the CURRENT save. The gate opens, Q is tested, Q == C, and it passes. If this
    # ever aborts, FIX 1 has been turned into a ninth abort condition by the back door.
    root = _st_project(tmp, "future_fresh")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md", _fut(8, 5))
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 future  future A + FRESH quote -> exit 0", code, EXIT_OK)
    ck("A6 future  ...no finding of any kind", _st_findings(out), [])

    # ---- FIX 2 IN ISOLATION. Future A(99), FRESH quote (so A6 is satisfied and cannot be what
    # fires), `expires save#105`. Unclamped this read 105 - 99 = 6 and passed. Clamped it reads
    # 105 - 5 = 100 against a maximum of 10. THE RULE SET IS PINNED EXACTLY so this arm can never
    # silently start passing because A6 fired instead.
    root = _st_project(tmp, "future_horizon")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md", _fut(105, 5))
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 future  FIX 2 alone: clamped horizon -> exit 3", code, EXIT_ABORT)
    ck("A6 future  ...and A5 is the ONLY rule that fired", _st_rules(out), ["A5"])
    ck("A6 future  ...measured from the CURRENT save, 100 saves", "is 100 saves past" in out, True)
    ck("A6 future  ...the message says the declared base was refused",
       "a save that HAS NOT HAPPENED" in out, True)

    # ---- THE HONEST CONTROL for the defect fixture: identical shape, truthful A. It aborted
    # before the fix and must abort identically after it. If this ever diverges from the future
    # arm above, the fix has started treating the two declarations differently in the wrong
    # direction.
    root = _st_project(tmp, "future_control_honest")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 1)\n"
              "save: 5\n"
              "expires: save 8\n"
              "\n"
              "-> Skip the gate.\n")
    code, out, _err = _st_run(["--root", str(root), "--save", "5"])
    ck("A6 future  honest control (asserted 5, stale quote) -> exit 3", code, EXIT_ABORT)
    ck("A6 future  ...the rule is A6", _st_rules(out), ["A6"])

    # ---- THE USER'S RULING, over the full life of a genuine renewal. Asserted at save 5, the
    # user's own quote carries `(save 5)`, expires save 8. `moment_carry` pins saves 5-7; this
    # runs 5..12, past the expiry, because FIX 1 widened the gate and the widening must not reach
    # ANY save of an honest entry. If a single one of these aborts, the change is wrong.
    root = _st_project(tmp, "future_genuine_life")
    _st_write(root, "_archive/active-expired.md", _ST_RENEW_EXPIRED)
    _st_write(root, "active.md",
              "## A-2 · asserted save#5 · expires save#8\n"
              "kind: rules\n"
              "lifetime: expiring\n"
              "quote: \"ship the gate\" (save 5)\n"
              "save: 5\n"
              "expires: save 8\n"
              "\n"
              "-> Skip the gate.\n")
    for save in (5, 6, 7, 8, 9, 10, 11, 12):
        code, out, _err = _st_run(["--root", str(root), "--save", str(save)])
        ck("A6 future  genuine renewal, --save %d -> exit 0" % save, code, EXIT_OK)
        ck("A6 future  ...--save %d: no finding" % save, _st_findings(out), [])


# --------------------------------------------------------------------------- selftest driver

_ST_GROUPS = (
    _st_arms_acceptance,
    _st_arms_a1_a4,
    _st_arms_a5_a8,
    _st_arms_fences,
    _st_arms_paths,
    _st_arms_not_aborts,
    _st_arms_region,
    _st_arms_encodings,
    _st_arms_generated,
    _st_arms_report_and_t2,
    _st_arms_split,
    _st_arms_shape,
    _st_arms_scope,
    _st_arms_contract,
    _st_arms_regressions,
    _st_arms_renewal,
    _st_arms_a6_moment,
    _st_arms_a6_future,
    _st_arms_content,
    _st_arms_a13,
    _st_arms_a14,
    _st_arms_invariants,          # LAST: it asserts over every code the others observed.
)


def _selftest():
    """Run every arm. EXIT_SELFTEST on any failure, EXIT_OK otherwise. Writes nothing durable."""
    import shutil
    import tempfile
    checks = []
    del _ST_CODES[:]        # a second call in one process must not assert over the first's codes

    def ck(label, got, want):
        checks.append((label, got, want, got == want))

    tmp = tempfile.mkdtemp(prefix="validate_entries_selftest_")
    try:
        for group in _ST_GROUPS:
            group(ck, pathlib.Path(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [c for c in checks if not c[3]]
    for (label, got, want, ok) in checks:
        if not ok:
            sys.stderr.write("FAIL %s: got %r want %r\n" % (label, got, want))
    sys.stderr.write("%d checks, %d failed\n" % (len(checks), len(bad)))
    return EXIT_SELFTEST if bad else EXIT_OK


# --------------------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(
        prog="validate_entries.py",
        description="save v4 step 7d VALIDATE. Checks that every living-doc entry declares "
                    "a kind and a lifetime the v4 taxonomy allows, in a file whose declared "
                    "kind permits it. READS ONLY -- never writes, never fixes.",
        epilog="exit: 0 valid / warnings only / --report | 2 usage or I/O | 3 abort set "
               "non-empty | 6 scan integrity (OUTRANKS 0 and 3, including in --report mode) | "
               "7 internal error. 1 is --selftest's own failure code and nothing else returns "
               "it.")
    p.add_argument("--root", metavar="DIR",
                   help="REQUIRED. The project folder. The scope is DECLARED, never inferred "
                        "from cwd (D53).")
    p.add_argument("--file", metavar="REL", action="append", default=None,
                   help="Check only this file, relative to --root. Repeatable. Absolute paths "
                        "and paths escaping --root are refused (exit 2).")
    p.add_argument("--save", metavar="N", type=int, default=None,
                   help="The current save number. Used by A6's renewal-freshness check. "
                        "Defaults to state.md's `save:` line; when neither is available that "
                        "one check SKIPS with a warning rather than passing silently.")
    p.add_argument("--report", action="store_true",
                   help="MIGRATION MODE (spec S9 step 6): emit the identical report and exit 0 "
                        "even with a non-empty abort set. Migration REPORTS the type rule and "
                        "publishes; it never fails on it. Exit 6 still wins -- an unread file "
                        "is not a clean report.")
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

    # Before the --root check: the suite builds and declares its own roots, so requiring one
    # here would make the tool untestable without pointing it at a real project.
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

    try:
        only = {"save": args.save, "files": None}
        if args.file:
            wanted = []
            for spec in args.file:
                rel, _rp = recall.resolve_under_root(root, spec, "--file")
                wanted.append(rel)
            only["files"] = wanted
        scan = run_scan(root, DEFAULT_PATTERNS, only)
        render_report(scan, root, args.report, out)
        render_lifetime_resolutions(scan.resolutions, sys.stderr)
        if scan.violations and not args.report:
            sys.stderr.write("\n%d ABORT-set violation%s. See the ABORT SET block on stdout for "
                             "rule, file:line and entry id.\n"
                             % (len(scan.violations),
                                "" if len(scan.violations) == 1 else "s"))
        verdict = EXIT_OK if args.report else (
            EXIT_ABORT if scan.violations else EXIT_OK)
        # Integrity outranks the verdict, and it outranks --report's forced 0 as well. Decided
        # in exactly one place across all three scripts; never hand-rolled here.
        return recall.integrity_code(scan.unreliable, verdict)
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
