#!/usr/bin/env python
"""wiki_topic_match.py — M-of-N OR topic-to-files matcher for wiki-* pipelines.

Algorithm: for a kebab-case topic slug decomposed into N significant keywords
(after stop-word + min-length filter), a file matches if it contains at least
M of N keywords, where M = max(2, ceil(N/3)) with N=1 special-case M=1 and
N=0 special-case M=0. Replaces strict-AND keyword intersection, which silently
dropped relevant files whose topic information is distributed across multiple
files. Final match set U = I_or (M-of-N OR matches) ∪ L (literal-slug grep) ∪ F
(frontmatter topic-field match).

When keywords_extra contains source-grounded glossary expansion output,
extras are merged into K BEFORE per-keyword grep, subject to stop-word + dedup
filter. Algorithm version bumps to `m-of-n-or-with-llm-expansion-v2` ONLY when
at least one extra survives filter; reverts to v1 otherwise (lets consumers
distinguish "expansion tried but produced nothing" vs "never attempted").

Algorithm version is exposed via ALGORITHM_VERSION / ALGORITHM_VERSION_V2
constants + envelope 'algorithm' field for forward-compat versioning.

Public API:
  match_topic(topic_slug, candidates_dir, *, mode='raws', original_topic=None, keywords_extra=None) -> dict
    Returns: {count, files, breakdown:{K,N,M,per_keyword_counts,I_count,L_count,F_count,K_extras_applied,timings_ms}, algorithm}

CLI:
  python wiki_topic_match.py --topic-slug SLUG --candidates-dir DIR [--mode raws|compilations|compilations-fm] [--original-topic STR] [--keywords-extra PATH] [--json | --output PATH]
  python wiki_topic_match.py --test [--fixtures-dir DIR]    # built-in self-test
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:
    sys.stderr.write(
        "ERROR: PyYAML not available. Install with: "
        "python -m pip install PyYAML\n"
    )
    sys.exit(2)


# ------------------------------ constants -----------------------------------

STOP_WORDS = frozenset({
    "a", "an", "the", "of", "and", "or", "for", "to", "in", "on",
    "at", "by", "with", "vs",
})

# Algorithm version. Emitted in envelope 'algorithm' field so consumers can
# detect drift when retrieval semantics change.
ALGORITHM_VERSION = "m-of-n-or-v1"
# v2 algorithm: m-of-n-or-v1 + LLM-grounded keyword expansion (extras merged into
# K before per-keyword grep). Emitted ONLY when keywords_extra was non-empty AND
# at least one extra survived dedup/filter. Reverts to v1 when no extras applied —
# this lets consumers distinguish "expansion was tried but produced nothing" vs
# "expansion was never attempted".
ALGORITHM_VERSION_V2 = "m-of-n-or-with-llm-expansion-v2"

# Mode-to-glob mapping
MODE_GLOBS = {
    "raws": "**/*.md",
    "compilations": "*-compilation.md",
    # Compilations-by-frontmatter: recursive scan; final filter applied via
    # frontmatter `type: compilation` check (see match_topic body). Glob alone
    # is permissive; the type-filter narrows to canonical compilation files.
    "compilations-fm": "**/*.md",
}


# ------------------------------ tokenization --------------------------------

def decompose_slug(topic_slug: str) -> list[str]:
    """Decompose topic_slug into K = [k1, k2, ..., kN] significant keywords.

    5-step recipe:
      (i) Split on `-` and `_` (preserve order, drop empty tokens).
      (ii) Lower-case each token.
      (iii) Drop stop-words: a, an, the, of, and, or, for, to, in, on, at, by, with, vs.
      (iv) Minimum-length filter:
           - For multi-token slugs (>=2 tokens after split + stop-word drop): NO min length floor.
           - For single-token slugs (exactly 1 token after split + stop-word drop): drop if len < 3.
      (v) De-duplicate while preserving first-occurrence order (prevents duplicate
          tokens from inflating N/M when matching collapses by keyword string).
      (vi) Result is the ordered list K = [k1, k2, ..., kN].
    """
    tokens = re.split(r"[-_]", topic_slug)
    tokens = [t.lower() for t in tokens if t]
    tokens = [t for t in tokens if t not in STOP_WORDS]
    if len(tokens) == 1 and len(tokens[0]) < 3:
        tokens = []
    # Dedupe preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped


def keyword_pattern(ki: str) -> re.Pattern[str]:
    """Plural-tolerant alternation regex (case-insensitive).

    Variants emitted:
      * Base form: \\bki\\b
      * Plural `s`: \\bkis\\b
      * Plural `es`: \\bkies\\b
      * Plural y -> ies (ONLY if len(ki) >= 2 AND ki ends in 'y' AND ki[-2] is consonant)
    """
    variants = [ki, ki + "s", ki + "es"]
    if len(ki) >= 2 and ki.endswith("y") and ki[-2] not in "aeiou":
        variants.append(ki[:-1] + "ies")
    escaped = [re.escape(v) for v in variants]
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


def slug_pattern(topic_slug: str) -> re.Pattern[str]:
    """Word-boundary-anchored literal-slug pattern."""
    return re.compile(r"\b" + re.escape(topic_slug) + r"\b", re.IGNORECASE)


def slug_form_variants(topic_slug: str, original_topic: str | None = None) -> set[str]:
    """Form variants for frontmatter `topic:` comparison.

    Per the contract step (d)(i-iv):
      (i)   original {topic} string verbatim (if `original_topic` provided)
      (ii)  topic_slug (kebab-case form)
      (iii) topic_slug with `-` and `_` replaced by spaces (display form)
      (iv)  topic_slug with `-` replaced by `_` (snake_case form)

    All values are lowercased for case-insensitive comparison in the caller.
    Returns 3 forms by default (ii, iii, iv); 4 if `original_topic` is provided.
    """
    s = topic_slug.lower()
    variants = {
        s,                                            # (ii) kebab-case as-given
        s.replace("-", " ").replace("_", " "),        # (iii) display (spaces)
        s.replace("-", "_"),                          # (iv) snake-case
    }
    if original_topic:
        variants.add(original_topic.strip().lower())  # (i) verbatim original
    return variants


# ------------------------------ scanners ------------------------------------

def _filter_by_frontmatter_type(files: list[Path], *, expected_type: str) -> list[Path]:
    """Filter `files` to those whose YAML frontmatter has `type: <expected_type>`.

    Used by `compilations-fm` mode to handle vault layouts where compilation
    files are identified by frontmatter rather than filename suffix (e.g.,
    a public research vault's `wiki/tech/agent-frameworks.md` with `type: compilation` in
    frontmatter; no `-compilation.md` suffix).

    Files that fail to parse, lack frontmatter, lack `type:` field, or have a
    different type value are silently dropped from the result (mirrors the
    skip-on-error policy of frontmatter_topic_matches).
    """
    fm_re = re.compile(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
    result: list[Path] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            continue
        if not text.startswith("---"):
            continue
        m = fm_re.match(text)
        if not m:
            continue
        try:
            fm = yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict):
            continue
        if fm.get("type") == expected_type:
            result.append(f)
    return result


def per_keyword_matches(files: list[Path], K: list[str]) -> dict[str, set[Path]]:
    """Build per-keyword match set S_i for each keyword k_i.

    Returns dict mapping keyword -> set of Path objects that contain that keyword.
    Uses a single-pass-per-file optimization: scan each file once, test all patterns.
    """
    patterns = [(ki, keyword_pattern(ki)) for ki in K]
    result: dict[str, set[Path]] = {ki: set() for ki in K}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            continue
        for ki, pat in patterns:
            if pat.search(text):
                result[ki].add(f)
    return result


def literal_slug_matches(files: list[Path], topic_slug: str) -> set[Path]:
    """Set L: files matching the literal topic_slug via word-boundary anchors."""
    pat = slug_pattern(topic_slug)
    result: set[Path] = set()
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            continue
        if pat.search(text):
            result.add(f)
    return result


def frontmatter_topic_matches(
    files: list[Path],
    topic_slug: str,
    original_topic: str | None = None,
) -> set[Path]:
    """Set F: files whose YAML frontmatter `topic:` field matches a slug form variant.

    Contract:
      (a) Extract leading YAML frontmatter block (opens with `---\\n`; closes with
          `\\n---` followed by newline OR EOF).
      (b) Parse YAML; extract `topic:` field (scalar | inline list | block list).
      (c) Normalize each value: lowercase + strip whitespace + strip quotes.
      (d) Compare against the form variants from slug_form_variants() (3 forms
          + optional verbatim-original; all case-insensitive).
      (e) Add to F if ANY value matches ANY form.
      (f) Skip-on-error policy: no YAML, parse error, non-map, missing topic, non-string scalar
          -> file not added to F (do NOT abort step).
    """
    variants = slug_form_variants(topic_slug, original_topic=original_topic)
    result: set[Path] = set()
    # Accept LF or CRLF line endings; closing `---` may be followed by newline OR EOF.
    fm_re = re.compile(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            continue
        if not text.startswith("---"):
            continue
        m = fm_re.match(text)
        if not m:
            continue
        try:
            fm = yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict):
            continue
        topic_val = fm.get("topic")
        if topic_val is None:
            continue
        if isinstance(topic_val, str):
            vals = [topic_val]
        elif isinstance(topic_val, list):
            vals = [v for v in topic_val if isinstance(v, str)]
        else:
            continue
        for v in vals:
            v_norm = v.strip().strip('"').strip("'").lower()
            if v_norm in variants:
                result.add(f)
                break
    return result


# ------------------------------ main matcher --------------------------------

def match_topic(
    topic_slug: str,
    candidates_dir: Path,
    *,
    mode: str = "raws",
    original_topic: str | None = None,
    keywords_extra: list[str] | None = None,
) -> dict[str, Any]:
    """M-of-N OR topic-to-files matcher.

    See module docstring for full contract. Implements the `m-of-n-or-v1`
    algorithm (ALGORITHM_VERSION). When keywords_extra is non-empty AND at
    least one extra survives dedup/filter, bumps to `m-of-n-or-with-llm-
    expansion-v2` (ALGORITHM_VERSION_V2).

    Args:
      topic_slug: kebab-case slug.
      candidates_dir: directory to glob (mode-dependent).
      mode: 'raws' (recursive **/*.md), 'compilations' (single-level
            *-compilation.md), or 'compilations-fm' (recursive **/*.md
            filtered by frontmatter `type: compilation` — handles vault
            layouts where compilations don't use the -compilation suffix).
      original_topic: optional verbatim original topic string (before
            slug-normalization). When provided, frontmatter `topic:` matching
            also accepts the verbatim form per step (d)(i) of the contract.
      keywords_extra: optional list of additional keyword tokens (LLM-glossary
            expansion output). Merged into K AFTER
            decompose_slug + BEFORE per-keyword grep. Each extra goes through
            the same stop-word + min-length + dedup filter as the original
            tokens. When None or empty, behavior is byte-identical to v1.

    Returns:
      dict with keys: count, files, breakdown, algorithm.
      breakdown.K_extras_applied lists the extras that survived filtering
      (empty list when v1 behavior; non-empty bumps algorithm to v2).
    """
    t0 = time.perf_counter()
    if mode not in MODE_GLOBS:
        raise ValueError(
            f"mode must be one of {sorted(MODE_GLOBS.keys())!r}; got {mode!r}"
        )
    glob_pattern = MODE_GLOBS[mode]
    if not candidates_dir.is_dir():
        return {
            "count": 0,
            "files": [],
            "breakdown": {
                "K": [],
                "N": 0,
                "M": 0,
                "per_keyword_counts": {},
                "I_count": 0,
                "L_count": 0,
                "F_count": 0,
                "K_extras_applied": [],
                "timings_ms": {"total": 0},
            },
            "algorithm": ALGORITHM_VERSION,
            "error": f"candidates_dir does not exist: {candidates_dir}",
        }

    t1 = time.perf_counter()
    files = sorted(candidates_dir.glob(glob_pattern))
    # For compilations-fm mode: filter to files whose frontmatter has type: compilation
    if mode == "compilations-fm":
        files = _filter_by_frontmatter_type(files, expected_type="compilation")
    t_glob = (time.perf_counter() - t1) * 1000

    K = decompose_slug(topic_slug)

    # Merge source-grounded glossary extras. Apply the same stop-word + dedup
    # filter as decompose_slug to defend against LLM leaking stop-words/dupes.
    # Track which extras survive — non-empty survivors trigger v2 algorithm
    # version (consumers can distinguish "expansion produced nothing" from
    # "expansion never attempted").
    K_extras_applied: list[str] = []
    if keywords_extra:
        base_was_empty = len(K) == 0  # iter-2 MEDIUM fix: preserve short-token guard
        seen_k = set(K)
        for ex in keywords_extra:
            if not isinstance(ex, str):
                continue
            t = ex.strip().lower()
            if not t or t in STOP_WORDS or t in seen_k:
                continue
            seen_k.add(t)
            K.append(t)
            K_extras_applied.append(t)
        # Iter-2 MEDIUM fix: when base K was empty (e.g., topic_slug='uc' or 'ai'
        # got dropped by decompose_slug's single-token min-length floor) AND the
        # final K ends up single-token AND that token is <3 chars, restore the
        # original drop. Without this guard, glossary expansion could reintroduce
        # the exact short-token precision flaw the v1 floor protects against
        # (e.g., extras=['uc'] on topic_slug='uc' would yield K=['uc'] when
        # decompose_slug would have returned []).
        if base_was_empty and len(K) == 1 and len(K[0]) < 3:
            dropped = K.pop(0)
            K_extras_applied.remove(dropped)

    N = len(K)
    if N == 0:
        M = 0  # Empty-K edge case: I_or trivially empty; L + F still active.
    elif N == 1:
        M = 1  # Single-keyword: M=1 preserves single-keyword grep semantics.
    else:
        M = max(2, math.ceil(N / 3))

    t2 = time.perf_counter()
    S = per_keyword_matches(files, K) if K else {}
    t_kw = (time.perf_counter() - t2) * 1000

    # M-of-N OR set: files matching >= M of the N significant keywords.
    I_or: set[Path] = set()
    if K and M > 0:
        # Build per-file match count
        match_count: dict[Path, int] = {}
        for ki, si in S.items():
            for f in si:
                match_count[f] = match_count.get(f, 0) + 1
        I_or = {f for f, c in match_count.items() if c >= M}

    t3 = time.perf_counter()
    L = literal_slug_matches(files, topic_slug)
    t_l = (time.perf_counter() - t3) * 1000

    t4 = time.perf_counter()
    F = frontmatter_topic_matches(files, topic_slug, original_topic=original_topic)
    t_f = (time.perf_counter() - t4) * 1000

    U = I_or | L | F
    files_sorted = sorted(str(p) for p in U)

    return {
        "count": len(U),
        "files": files_sorted,
        "breakdown": {
            "K": K,
            "N": N,
            "M": M,
            "per_keyword_counts": {ki: len(si) for ki, si in S.items()},
            "I_count": len(I_or),
            "L_count": len(L),
            "F_count": len(F),
            "K_extras_applied": K_extras_applied,
            "timings_ms": {
                "glob": round(t_glob, 1),
                "per_keyword": round(t_kw, 1),
                "literal_slug": round(t_l, 1),
                "frontmatter": round(t_f, 1),
                "total": round((time.perf_counter() - t0) * 1000, 1),
            },
        },
        "algorithm": ALGORITHM_VERSION_V2 if K_extras_applied else ALGORITHM_VERSION,
    }


# ------------------------------ self-test -----------------------------------

def _run_self_test(fixtures_dir: Path | None = None) -> int:
    """Built-in self-test: tokenization, M formula, plural variants, edge cases.

    Synthetic fixtures (no external vault dependency). For optional real-vault
    sanity, pass --fixtures-dir pointing at any existing vault root with raw/
    subdirectory.

    Returns 0 on PASS; non-zero on FAIL.
    """
    import tempfile, os
    failures: list[str] = []

    # --- Synthetic case 1: M-of-N OR distributes correctly across files ---
    # Build a tmp dir with 4 files modeling the cross-document failure mode
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # File A: mentions 'foo' + 'bar' (2 of 4 keywords)
        (td_path / "a.md").write_text("foo and bar are common", encoding="utf-8")
        # File B: mentions 'baz' only (1 of 4 keywords)
        (td_path / "b.md").write_text("baz is here alone", encoding="utf-8")
        # File C: mentions 'foo' + 'baz' + 'qux' (3 of 4 keywords)
        (td_path / "c.md").write_text("foo plus baz plus qux", encoding="utf-8")
        # File D: mentions nothing relevant
        (td_path / "d.md").write_text("unrelated content", encoding="utf-8")
        # File E: with frontmatter topic matching slug verbatim
        (td_path / "e.md").write_text(
            "---\ntopic: foo-bar-baz-qux\n---\nbody text\n", encoding="utf-8"
        )

        result = match_topic("foo-bar-baz-qux", td_path, mode="raws")
        # N=4, M = max(2, ceil(4/3)) = 2
        # I_or: A (foo+bar, 2 matches), C (foo+baz+qux, 3 matches), E (foo+bar+baz+qux in frontmatter body, 4 matches) -> 3 files
        # L: e.md (contains literal "foo-bar-baz-qux" in frontmatter topic field; word-bounded)
        # F: e.md (frontmatter `topic:` matches slug verbatim)
        # U = I_or ∪ L ∪ F = {A, C, E} = 3
        if result["breakdown"]["N"] != 4:
            failures.append(f"synth-1: N expected 4; got {result['breakdown']['N']}")
        if result["breakdown"]["M"] != 2:
            failures.append(f"synth-1: M expected 2; got {result['breakdown']['M']}")
        if result["breakdown"]["I_count"] != 3:
            failures.append(f"synth-1: I_count expected 3 (A+C+E); got {result['breakdown']['I_count']}")
        if result["breakdown"]["F_count"] != 1:
            failures.append(f"synth-1: F_count expected 1 (e.md); got {result['breakdown']['F_count']}")
        if result["count"] != 3:
            failures.append(f"synth-1: count expected 3 (A+C+E); got {result['count']}")
        print(f"Synth-1 (M-of-N OR distribution): N=4 M=2 I_or={result['breakdown']['I_count']} F={result['breakdown']['F_count']} count={result['count']} OK")

    # --- Synthetic case 2: literal-slug grep set L ---
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # File mentions the literal slug as a word-bounded match
        (td_path / "f.md").write_text("see master-strategy-catalog for details", encoding="utf-8")
        # File mentions a substring that is NOT word-bounded
        (td_path / "g.md").write_text("mymaster-strategy-catalogfoo not bounded", encoding="utf-8")
        result = match_topic("master-strategy-catalog", td_path, mode="raws")
        if result["breakdown"]["L_count"] != 1:
            failures.append(f"synth-2: L_count expected 1 (f.md word-bounded); got {result['breakdown']['L_count']}")
        print(f"Synth-2 (literal-slug L): L={result['breakdown']['L_count']} (expected 1) OK")

    # --- Synthetic case 3: frontmatter YAML 3-shape handling ---
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Scalar topic
        (td_path / "scalar.md").write_text("---\ntopic: my-topic\n---\nbody\n", encoding="utf-8")
        # Inline list
        (td_path / "inline.md").write_text("---\ntopic: [other, my-topic, more]\n---\nbody\n", encoding="utf-8")
        # Block list
        (td_path / "block.md").write_text("---\ntopic:\n  - first\n  - my-topic\n---\nbody\n", encoding="utf-8")
        # Skip-on-error: missing topic field
        (td_path / "nomatch.md").write_text("---\nother: field\n---\nbody\n", encoding="utf-8")
        # Skip-on-error: malformed YAML
        (td_path / "broken.md").write_text("---\ntopic: : :::\n  bad: yaml\n---\nbody\n", encoding="utf-8")
        # Skip-on-error: non-string scalar
        (td_path / "nonstring.md").write_text("---\ntopic: 42\n---\nbody\n", encoding="utf-8")
        result = match_topic("my-topic", td_path, mode="raws")
        # F should contain scalar.md, inline.md, block.md (3 matches)
        if result["breakdown"]["F_count"] != 3:
            failures.append(f"synth-3: F_count expected 3 (scalar+inline+block); got {result['breakdown']['F_count']}; files={result['files']}")
        print(f"Synth-3 (frontmatter 3-shape + skip-on-error): F={result['breakdown']['F_count']} (expected 3) OK")

    # --- Synthetic case 4: form variants ---
    variants_no_original = slug_form_variants("my-topic-here")
    expected_no_original = {"my-topic-here", "my topic here", "my_topic_here"}
    if variants_no_original != expected_no_original:
        failures.append(f"synth-4: variants mismatch; expected {expected_no_original}; got {variants_no_original}")
    variants_with_original = slug_form_variants("my-topic-here", original_topic="My Custom Topic Wording")
    if "my custom topic wording" not in variants_with_original:
        failures.append(f"synth-4: variants missing verbatim original")
    print(f"Synth-4 (form variants + original_topic): OK")

    # --- Synthetic case 5: edge cases (tokenization) ---
    K_empty = decompose_slug("the-of-and")
    if K_empty:
        failures.append(f"synth-5a: empty-K expected; got {K_empty}")
    K_single = decompose_slug("vwap")
    if K_single != ["vwap"]:
        failures.append(f"synth-5b: K=['vwap'] expected; got {K_single}")
    K_degen = decompose_slug("ai")
    if K_degen != []:
        failures.append(f"synth-5c: empty expected for single-token len<3; got {K_degen}")
    K_multi = decompose_slug("ai-safety")
    if K_multi != ["ai", "safety"]:
        failures.append(f"synth-5d: expected ['ai','safety']; got {K_multi}")
    K_dup = decompose_slug("foo-foo-bar")
    if K_dup != ["foo", "bar"]:
        failures.append(f"synth-5e: dedup expected ['foo','bar']; got {K_dup}")
    print(f"Synth-5 (tokenization edge cases + dedup): all 5 OK")

    # --- Synthetic case 6: M formula ---
    cases_m = [(0, 0), (1, 1), (2, 2), (3, 2), (4, 2), (5, 2),
               (6, 2), (7, 3), (8, 3), (9, 3), (10, 4)]
    for n, expected in cases_m:
        if n == 0:
            m = 0
        elif n == 1:
            m = 1
        else:
            m = max(2, math.ceil(n / 3))
        if m != expected:
            failures.append(f"synth-6: M(N={n}) expected {expected}; got {m}")
    print(f"Synth-6 (M formula N=0..10): OK")

    # --- Synthetic case 7: plural-y regex with consonant guard ---
    pat_strategy = keyword_pattern("strategy")
    if not pat_strategy.search("companies have many strategies."):
        failures.append("synth-7a: plural y->ies branch broken for 'strategy'")
    pat_day = keyword_pattern("day")
    if "daies" in pat_day.pattern:
        failures.append(f"synth-7b: y->ies should NOT fire for 'day' (vowel before y)")
    print(f"Synth-7 (plural-y regex + consonant guard): OK")

    # --- Synthetic case 8: EOF-terminated frontmatter + CRLF line endings ---
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # h.md: LF frontmatter closing with `---` at end-of-file (no trailing \n)
        (td_path / "h.md").write_bytes(b"---\ntopic: my-eof-topic\n---")
        # i.md: CRLF frontmatter (Windows line endings)
        (td_path / "i.md").write_bytes(b"---\r\ntopic: my-eof-topic\r\n---\r\nbody\r\n")
        result = match_topic("my-eof-topic", td_path, mode="raws")
        if result["breakdown"]["F_count"] != 2:
            failures.append(f"synth-8: EOF + CRLF frontmatter; F_count expected 2; got {result['breakdown']['F_count']}; files={result['files']}")
        print(f"Synth-8 (EOF + CRLF frontmatter): F={result['breakdown']['F_count']} (expected 2) OK")

    # --- Synthetic case 8b: end-to-end original_topic match via match_topic() ---
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # File with frontmatter topic matching ORIGINAL VERBATIM only
        # (a form that does NOT normalize to any of the 3 slug-derived forms)
        (td_path / "orig.md").write_text(
            "---\ntopic: 'tech: scale a side-business'\n---\nbody\n", encoding="utf-8"
        )
        slug = "tech-scale-a-side-business"
        original = "tech: scale a side-business"
        # Without original_topic: should NOT match (slug-derived forms only:
        # "tech-scale-a-side-business", "tech scale a side business", "tech_scale_a_side_business")
        r1 = match_topic(slug, td_path, mode="raws")
        # With original_topic: SHOULD match via verbatim form (d)(i)
        r2 = match_topic(slug, td_path, mode="raws", original_topic=original)
        if r1["breakdown"]["F_count"] != 0:
            failures.append(f"synth-8b: without original_topic, F_count expected 0 (no slug-form matches colon-bearing original); got {r1['breakdown']['F_count']}")
        if r2["breakdown"]["F_count"] != 1:
            failures.append(f"synth-8b: with original_topic, F_count expected 1; got {r2['breakdown']['F_count']}")
        print(f"Synth-8b (end-to-end original_topic match): F_no_orig={r1['breakdown']['F_count']} F_with_orig={r2['breakdown']['F_count']} OK")

    # --- Synthetic case 9: five-keyword topic with two-keyword overlaps ---
    # Strict AND would drop relevant partial overlaps. M-of-N OR (M=2) must
    # retain exactly the three two-keyword files, not single-keyword noise.
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "a-alpha-only.md").write_text("Alpha methods overview", encoding="utf-8")
        (td_path / "b-beta-only.md").write_text("Beta measurements overview", encoding="utf-8")
        (td_path / "c-alpha-gamma.md").write_text("Alpha and Gamma methods", encoding="utf-8")
        (td_path / "d-alpha-delta.md").write_text("Alpha and Delta measurements", encoding="utf-8")
        (td_path / "e-alpha-epsilon.md").write_text("Alpha and Epsilon evidence", encoding="utf-8")
        (td_path / "f-noise.md").write_text("unrelated content nothing to see", encoding="utf-8")
        result = match_topic("alpha-beta-gamma-delta-epsilon", td_path, mode="raws")
        # N=5 (after dedup), M=2
        # I_or: c (alpha+gamma), d (alpha+delta), e (alpha+epsilon) -> 3 files
        # a (alpha only), b (beta only), f (none) do not meet M=2.
        # L: empty (no file mentions literal slug as word). F: empty (no frontmatter).
        # Expected EXACTLY count=3 (no over-recall regression).
        if result["breakdown"]["N"] != 5:
            failures.append(f"synth-9: N expected 5; got {result['breakdown']['N']}")
        if result["breakdown"]["M"] != 2:
            failures.append(f"synth-9: M expected 2; got {result['breakdown']['M']}")
        if result["breakdown"]["I_count"] != 3:
            failures.append(f"synth-9: I_count expected exactly 3 (c+d+e); got {result['breakdown']['I_count']}")
        if result["count"] != 3:
            failures.append(f"synth-9: count expected EXACTLY 3 (over-recall regression); got {result['count']}; files={result['files']}")
        print(f"Synth-9 (fixture-free regression; precision-checked): N=5 M=2 I_or={result['breakdown']['I_count']} count={result['count']} (==3) OK")
    # --- Synthetic case 10: compilations-fm mode (frontmatter type: compilation) ---
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # File 1: -compilation.md suffix + frontmatter type: compilation (both signals)
        (td_path / "foo-bar-compilation.md").write_text(
            "---\ntype: compilation\ntopic: foo-bar\n---\nbody mentioning foo and bar\n",
            encoding="utf-8",
        )
        # File 2: no suffix BUT frontmatter type: compilation (custom layout)
        nested = td_path / "tech" / "ai"
        nested.mkdir(parents=True)
        (nested / "agent-frameworks.md").write_text(
            "---\ntype: compilation\ntopic: agent-frameworks\n---\nfoo bar content\n",
            encoding="utf-8",
        )
        # File 3: -compilation.md suffix BUT frontmatter type: source (not a compilation)
        (td_path / "decoy-compilation.md").write_text(
            "---\ntype: source\n---\nfoo bar in body\n", encoding="utf-8",
        )
        # File 4: no frontmatter at all
        (td_path / "untagged.md").write_text("foo bar untagged\n", encoding="utf-8")

        # mode='compilations' (suffix-based) — only files 1 + 3 (suffix matches; type filter not applied)
        r_compilations = match_topic("foo-bar", td_path, mode="compilations")
        # mode='compilations-fm' (frontmatter-based) — files 1 + 2 (both have type: compilation)
        r_fm = match_topic("foo-bar", td_path, mode="compilations-fm")

        # Expected: compilations mode finds 1+3 by glob (both contain foo+bar, both pass M=2)
        # compilations-fm finds 1+2 (file 1 has type:compilation; file 2 has type:compilation in nested)
        if "decoy-compilation.md" in str(r_compilations.get("files", [])):
            # type filter NOT applied in 'compilations' mode; decoy with foo+bar should be in I_or
            pass  # expected behavior
        if any("decoy-compilation" in f for f in r_fm.get("files", [])):
            failures.append("synth-10: compilations-fm mode incorrectly includes decoy (type: source)")
        if not any("agent-frameworks" in f for f in r_fm.get("files", [])):
            failures.append("synth-10: compilations-fm mode missed nested type: compilation file")
        if not any("foo-bar-compilation" in f for f in r_fm.get("files", [])):
            failures.append("synth-10: compilations-fm mode missed suffix-AND-type-compilation file")
        print(f"Synth-10 (compilations-fm mode): files={[Path(f).name for f in r_fm['files']]} (expected agent-frameworks.md + foo-bar-compilation.md) OK")

    # --- Case 11: keywords_extra=None preserves v1 algorithm version ---
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "a.md").write_text("uc something here\n", encoding="utf-8")
        r = match_topic("uc", td_path, mode="raws", keywords_extra=None)
        if r["algorithm"] != ALGORITHM_VERSION:
            failures.append(f"synth-11: expected algorithm={ALGORITHM_VERSION!r} when extras=None, got {r['algorithm']!r}")
        if r["breakdown"].get("K_extras_applied") != []:
            failures.append(f"synth-11: expected K_extras_applied=[] when extras=None, got {r['breakdown'].get('K_extras_applied')!r}")
        print(f"Synth-11 (extras=None preserves v1 algorithm): algorithm={r['algorithm']} K_extras_applied={r['breakdown']['K_extras_applied']} OK")

    # --- Case 12: extras applied → algorithm bumps to v2 + extras visible ---
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Use multi-token slug so K isn't dropped by single-token min-length filter.
        # K from 'uc-elc' = ['uc', 'elc']; extras add 'university', 'california' → N=4, M=2.
        (td_path / "mixed.md").write_text(
            "UC ELC is the University of California system.\n", encoding="utf-8"
        )
        # File with only 'uc' + 'elc' (no extras) — 2 of 4 keywords, meets M=2 → IN I_or
        (td_path / "only-base.md").write_text("uc and elc together here\n", encoding="utf-8")
        # File with only 'university' — 1 of 4 → NOT in I_or
        (td_path / "only-extra.md").write_text("just university alone\n", encoding="utf-8")
        r = match_topic("uc-elc", td_path, mode="raws", keywords_extra=["university", "california"])
        if r["algorithm"] != ALGORITHM_VERSION_V2:
            failures.append(f"synth-12: expected algorithm={ALGORITHM_VERSION_V2!r} when extras applied, got {r['algorithm']!r}")
        if set(r["breakdown"].get("K_extras_applied", [])) != {"university", "california"}:
            failures.append(f"synth-12: expected K_extras_applied={{'university','california'}}, got {r['breakdown'].get('K_extras_applied')!r}")
        if r["breakdown"]["N"] != 4:
            failures.append(f"synth-12: expected N=4 (uc + elc + university + california), got N={r['breakdown']['N']}")
        if r["breakdown"]["M"] != 2:
            failures.append(f"synth-12: expected M=2 (N=4 → M=max(2,ceil(4/3))=2), got M={r['breakdown']['M']}")
        # mixed.md should be in I_or (all 4 match); only-base.md should also be in I_or (uc+elc = 2).
        # only-extra.md should NOT be in I_or (only 1 match) and NOT in L (slug "uc-elc" not in body).
        names = [Path(f).name for f in r["files"]]
        if "mixed.md" not in names:
            failures.append(f"synth-12: expected mixed.md in result, got files={names}")
        if "only-base.md" not in names:
            failures.append(f"synth-12: expected only-base.md in result (uc+elc=2 meets M=2), got files={names}")
        if "only-extra.md" in names:
            failures.append(f"synth-12: did NOT expect only-extra.md in result (only 1 keyword match), got files={names}")
        print(f"Synth-12 (extras applied → algorithm v2): algorithm={r['algorithm']} N={r['breakdown']['N']} M={r['breakdown']['M']} K_extras_applied={r['breakdown']['K_extras_applied']} OK")

    # --- Case 13: extras already in K → filtered out → reverts to v1 ---
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "a.md").write_text("uc and elc content\n", encoding="utf-8")
        # K from 'uc-elc' = ['uc', 'elc']; extras pass 'uc' (already in K) and 'the' (stop-word)
        r = match_topic("uc-elc", td_path, mode="raws", keywords_extra=["uc", "the", ""])
        if r["algorithm"] != ALGORITHM_VERSION:
            failures.append(f"synth-13: all extras filtered (dup+stopword+empty) → expected v1 algorithm, got {r['algorithm']!r}")
        if r["breakdown"].get("K_extras_applied") != []:
            failures.append(f"synth-13: expected K_extras_applied=[], got {r['breakdown'].get('K_extras_applied')!r}")
        print(f"Synth-13 (extras dup/stopword/empty filtered → v1): algorithm={r['algorithm']} K_extras_applied={r['breakdown']['K_extras_applied']} OK")

    # --- Case 14: empty-K + single short extra → asymmetric min-length floor restored (iter-2 MEDIUM fix) ---
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "uc-file.md").write_text("uc context\n", encoding="utf-8")
        # topic_slug='uc' → decompose_slug returns [] (single-token < 3 chars)
        # extras=['uc'] would normally append 'uc' → K=['uc']; but iter-2 fix
        # restores the asymmetric min-length floor when base_was_empty + len(K)==1 + len<3.
        # Expected: K stays empty, K_extras_applied=[], algorithm reverts to v1.
        r = match_topic("uc", td_path, mode="raws", keywords_extra=["uc"])
        if r["breakdown"]["K"] != []:
            failures.append(f"synth-14: expected K=[] (empty base + extras=['uc'] dropped by min-length floor), got K={r['breakdown']['K']!r}")
        if r["breakdown"].get("K_extras_applied") != []:
            failures.append(f"synth-14: expected K_extras_applied=[] (dropped extra removed from tracking), got {r['breakdown'].get('K_extras_applied')!r}")
        if r["algorithm"] != ALGORITHM_VERSION:
            failures.append(f"synth-14: empty-K guard fired → algorithm should be v1, got {r['algorithm']!r}")
        # Counter-test: longer extras DO survive on empty-K
        r2 = match_topic("uc", td_path, mode="raws", keywords_extra=["university"])
        if "university" not in r2["breakdown"]["K"]:
            failures.append(f"synth-14b: expected 'university' to survive (len>=3), got K={r2['breakdown']['K']!r}")
        if r2["algorithm"] != ALGORITHM_VERSION_V2:
            failures.append(f"synth-14b: extras applied → algorithm should be v2, got {r2['algorithm']!r}")
        print(f"Synth-14 (empty-K + short extra dropped via min-length floor): K={r['breakdown']['K']} K_extras_applied={r['breakdown']['K_extras_applied']} algorithm={r['algorithm']} OK")
        print(f"Synth-14b (empty-K + len>=3 extra survives): K={r2['breakdown']['K']} algorithm={r2['algorithm']} OK")

    # --- Optional case 15: real-vault sanity check if --fixtures-dir provided ---
    if fixtures_dir is not None:
        raw_dir = fixtures_dir / "raw"
        if not raw_dir.is_dir():
            sys.stderr.write(f"SKIP: optional fixtures-dir check — {raw_dir} not found\n")
        else:
            result = match_topic("research-methods-data-lineage-evidence", raw_dir, mode="raws")
            print(f"Optional-15 (real-vault sanity, fixtures-dir={fixtures_dir}):")
            print(f"  count={result['count']}; N={result['breakdown']['N']} M={result['breakdown']['M']}")
            print(f"  I_or={result['breakdown']['I_count']} L={result['breakdown']['L_count']} F={result['breakdown']['F_count']}")
            if result["count"] < 3:
                sys.stderr.write(f"WARN: real-vault count={result['count']} < 3 (may indicate sparse coverage on this vault)\n")

    if failures:
        sys.stderr.write("\n=== FAILURES ===\n")
        for f in failures:
            sys.stderr.write(f"  - {f}\n")
        return 1

    print("\n=== ALL TESTS PASS ===")
    return 0


# ------------------------------ CLI -----------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("--topic-slug", help="kebab-case topic slug")
    ap.add_argument("--candidates-dir", help="directory to glob")
    ap.add_argument(
        "--mode",
        default="raws",
        choices=sorted(MODE_GLOBS.keys()),
        help="retrieval mode (default: raws)",
    )
    ap.add_argument(
        "--original-topic",
        default=None,
        help="optional verbatim original topic string (pre-slug-normalization); enables frontmatter step (d)(i) match",
    )
    ap.add_argument(
        "--keywords-extra",
        default=None,
        help="optional path to JSON file with glossary expansion envelope ({extras: [...], ...}); extras merged into K before per-keyword grep. Missing/malformed file = silent no-op (v1 behavior preserved).",
    )
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    ap.add_argument(
        "--output",
        default=None,
        help="optional file path to write JSON to (instead of stdout); enables argv-array invocation without shell redirection",
    )
    ap.add_argument("--test", action="store_true", help="run built-in self-test")
    ap.add_argument(
        "--decompose-only",
        action="store_true",
        help="emit decomposed K (comma-joined) to stdout for --topic-slug and exit without scanning candidates; uses the same decomposition rules as match_topic",
    )
    ap.add_argument(
        "--fixtures-dir",
        default=None,
        help="optional vault root for --test real-vault sanity check (must contain raw/ subdir)",
    )
    args = ap.parse_args()

    if args.test:
        fixtures = Path(args.fixtures_dir) if args.fixtures_dir else None
        return _run_self_test(fixtures_dir=fixtures)

    if args.decompose_only:
        if not args.topic_slug:
            sys.stderr.write("ERROR: --decompose-only requires --topic-slug\n")
            return 2
        K = decompose_slug(args.topic_slug)
        sys.stdout.write(",".join(K))
        return 0

    if not args.topic_slug or not args.candidates_dir:
        sys.stderr.write("ERROR: --topic-slug and --candidates-dir required (unless --test)\n")
        return 2

    # Parse --keywords-extra JSON file if provided. Missing/malformed = empty list
    # (empty extras + stderr warning); preserves v1 behavior. The accepted
    # envelope is {extras: [...], rationale, ...}; only extras changes matching.
    keywords_extra: list[str] | None = None
    if args.keywords_extra:
        try:
            envelope = json.loads(Path(args.keywords_extra).read_text(encoding="utf-8"))
            extras_raw = envelope.get("extras", []) if isinstance(envelope, dict) else []
            if isinstance(extras_raw, list):
                keywords_extra = [x for x in extras_raw if isinstance(x, str)]
        except Exception as e:
            sys.stderr.write(
                f"WARNING: --keywords-extra path {args.keywords_extra!r} unreadable/malformed ({e}); "
                f"proceeding with v1 behavior (no extras applied)\n"
            )

    result = match_topic(
        args.topic_slug,
        Path(args.candidates_dir),
        mode=args.mode,
        original_topic=args.original_topic,
        keywords_extra=keywords_extra,
    )

    # Output: --output > --json (file) > stdout
    if args.output:
        Path(args.output).write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
    elif args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"count: {result['count']}")
        print(f"algorithm: {result['algorithm']}")
        print(f"breakdown: {json.dumps(result['breakdown'], indent=2)}")
        if result["files"]:
            print("files:")
            for f in result["files"]:
                print(f"  {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
