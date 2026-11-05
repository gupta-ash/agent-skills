# Wiki-citation-discipline review checklist (discriminator v1, contract vault-wiki)

The reviewer has no modes. This citation-discipline bucket reaches a
reviewer only when a calling pipeline inlines it, via the generated
composite `wiki-all.md`, into the brief it passes as the optional
`--prompt-file`; a caller that omits it ships no rules at all. The
artifact under review travels separately as `--source`, which is
required alongside `--contract vault-wiki`. That flag is how intent is
declared: an absent class is refused, never defaulted, and the reviewer
does not infer "this is a wiki compilation review" from prompt content.
`--checklist` is retired outright with no v1 equivalent, and
`--contract` is not a rename of it.

Scope: wiki-compilation and wiki-strategy artifacts. Validates that every claim
resolves to a cited raw, that body-cited slugs match the frontmatter
`compiled_from` set, and that corpus-silence claims cite the full source set.

## C-WIKI-CITE-1 — claim-has-citation (HIGH severity)

### Rule

Every claim sentence or bullet in the body of `## Overview`, `## Key Findings`,
`## Open Questions`, and `## Recommend-Strategize` MUST carry at least one
inline citation of the new form `^[[[raw-slug]]]` resolving actual supporting raw evidence. Retained supported forms remain readable only when uniquely resolving that evidence; navigation links never count.

If a claim has no citation, flag HIGH.

### Excluded from the rule

- Section headings, table-of-contents lines, and structural prose (e.g.
  "The following section enumerates ...") are not claims.
- `## Contradictions` bullets MAY carry the cited slugs at the END of the bullet
  rather than per-clause; one or more citations per bullet still required.

### Finding shape (return ONE finding per uncited claim)

- `severity`: `high`
- `category`: `truthfulness` — the rule tests whether a claim resolves to a cited source at all
- `title`: `"Uncited claim at {file}:{line}"`
- `body`: "Claim '{first-80-chars}...' in section '{##-heading}' has no uniquely resolving supporting citation."
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `mechanical` when no supported citation resolves; `judgment` when citation scope/support is ambiguous
- `evidence_quote`: the uncited claim sentence or bullet as written
- `recommendation`: "Add a direct supporting raw citation `^[[[raw-slug]]]`, or remove/qualify an unsupported claim."

## C-WIKI-CITE-2 — slug-set-equality (HIGH severity)

### Rule

The set of raw slugs cited in the body (new form `^[[[<slug>]]]`) MUST equal the set
of slugs declared in frontmatter `compiled_from:`. Both directions:

- Every body-cited slug MUST appear in `compiled_from:` (no "phantom" raws).
- Every `compiled_from:` slug MUST be cited at least once in the body (no
  "ghost" entries).

If either direction fails, flag HIGH.

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — the rule tests whether the body's cited source set agrees with the set the page declares it compiled from
- `title`: `"compiled_from slug set mismatch at {file}"`
- `body`: `"Body-only slugs: [{list}]. Frontmatter-only slugs: [{list}]."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: `null` (file-scoped finding)
- `basis`: `mechanical` — a set comparison in both directions between body citations and `compiled_from:`
- `evidence_quote`: `null` — the defect is a set difference computed across the frontmatter block and every body citation; no single span carries it
- `recommendation`: `"Add missing slugs to `compiled_from:` OR remove the ghost entries. Re-run `citation_count` recount per C-WIKI-CITE-3 after reconciliation."`

## C-WIKI-CITE-3 — citation-count-mechanical (HIGH severity)

### Rule

For each entry `e` in frontmatter `compiled_from:`, the integer field
`e.citation_count` MUST equal the number of uniquely resolved supporting body
citations to that raw. New citations use `^[[[<e.slug>]]]`; do not count navigation.

If any entry's `citation_count` disagrees with the body count by ±1 or more,
flag HIGH.

### Counting

The authoritative recount is `sum(c.slug == slug for c in extract_citations(document))`,
using `wiki.runtime.citations.extract_citations` on the complete document. This
shares the runtime's masking of frontmatter, comments, code and navigation, and
recognizes canonical `^[[[slug]]]` plus the supported historical `^[[slug]]` form.
Record exact declared and observed counts and actual marker locations; approximate
totals or character offsets mistaken for section locations are not mechanical proof.
Citation counting does not itself establish that a cited raw supports the claim.

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — a declared per-source citation count that disagrees with the body is a false statement about how that source was used (the well-formedness half is C-WIKI-FM-8, which is `usability`)
- `title`: `"citation_count mismatch for slug '{slug}' at {file}"`
- `body`: "Frontmatter declares citation_count={declared}; resolved supporting body citations number {actual}."
- `file`: compilation/strategy path
- `line_start` / `line_end`: the `compiled_from[i]` entry's frontmatter line range
- `basis`: `mechanical` — the declared integer against the exact runtime citation recount
- `evidence_quote`: the entry's `citation_count: {declared}` line, the span a fixer corrects
- `recommendation`: `"Recompute and update citation_count to match the exact runtime body count (currently {actual})."`

## C-WIKI-CITE-4 — no-lump-citation (MEDIUM severity)

### Rule

Each claim sentence MUST carry its own inline citation. Trailing citations at
the END of a paragraph that purport to cover multiple sentences upstream are
REJECTED.

If a paragraph contains two or more claim sentences AND only ONE trailing
citation, flag MEDIUM.

### Approved forms

- One citation per sentence: "Claim A ^[[[slug-1]]]. Claim B ^[[[slug-2]]]."
- Multi-source citation: "Claim A ^[[[slug-1]]] ^[[[slug-2]]]."

### REJECTED forms

- "Claim A. Claim B. ^[[[slug-1]]]" (trailing citation does not unambiguously
  resolve which claim it supports).

### Finding shape

- `severity`: `medium`
- `category`: `truthfulness` — a trailing citation covering several sentences leaves each claim's attribution unresolved
- `title`: `"Lump-citation pattern at {file}:{line}"`
- `body`: `"Paragraph contains {N} claim sentences but only 1 trailing citation; per-sentence resolution ambiguous."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: paragraph range
- `basis`: `judgment` — what counts as a claim sentence, and whether the trailing citation reads as covering the sentences upstream, are readings rather than counts
- `evidence_quote`: the offending paragraph, whose single trailing citation is the pattern
- `recommendation`: `"Split into per-sentence citations OR consolidate the claims into one sentence with multi-source citation."`

## C-WIKI-CITE-5 — corpus-silence-fully-cited (HIGH severity)

### Rule

Claims asserting that the corpus is SILENT on a topic (trigger phrases:
"silent on", "does not address", "no source", "corpus-silent", "absent from",
"no convergence", "none of the raws", "the corpus does not", "not discussed
in any of the") MUST cite ALL N entries in `compiled_from:` OR point to a
`## Open Questions` bullet that itself cites all N.

If a corpus-silence claim cites a strict subset and lacks an OQ pointer, flag
HIGH.

### Counting

- Let `N = len(frontmatter.compiled_from)`.
- Let `cited_in_claim = len(set(slugs cited inline on the claim line or its OQ pointer))`.
- Rule fires when trigger-phrase present AND `cited_in_claim < N` AND no `## Open Questions` pointer found.

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — asserting the corpus is silent is a claim about the whole source set
- `title`: `"Corpus-silence claim under-cited at {file}:{line}"`
- `body`: `"Claim '{first-80-chars}...' asserts corpus-silence but cites only {cited_in_claim} of {N} `compiled_from` entries with no OQ pointer."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `mechanical` — the trigger-phrase list is closed and the firing test is `cited_in_claim < N` against `len(compiled_from)`
- `evidence_quote`: the claim sentence carrying the trigger phrase
- `recommendation`: `"Cite all {N} `compiled_from` entries on the claim line OR add a `## Open Questions` bullet that cites all {N} and reference it from this claim."`

## C-WIKI-CITE-6 — no-tier-mixing (HIGH severity)

### Rule

Apply the citation target rule for the artifact's resolved tier and schema;
never treat a navigation/MOC link as evidence.

For Tier B a citation must resolve actual raw support, not generated analysis,
MOCs, indexes or bundle navigation. Tier C is synthesis-first with justified raw
fallback, subject to the selected schema; no Tier-C task exists in facts-only
vaults. A reference link by itself is not supporting evidence.

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — the rule tests what a citation actually resolves to
- `title`: `"Tier-mixed citation at {file}:{line}"`
- `body`: "Citation resolves a target that cannot support this artifact's type/tier under the selected schema."
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `mechanical` — resolve the slug, read the target's `type:` field
- `evidence_quote`: the citation-bearing body line
- `recommendation`: "Use actual supporting evidence for the selected tier, preserving direct raw support and required accepted synthesis lineage."

## C-WIKI-CITE-7 — quote-byte-exact (HIGH severity)

### Rule

For every `[QUOTE]`-marked claim, the quoted text span MUST appear byte-exact
verbatim in the cited raw source.

The quoted span is the text between the opening quote glyph and the closing
quote glyph (or the marker delimiters used in the body).

If the quoted text does not match byte-exact in any cited raw, flag HIGH.

### Excluded from the rule

- Ellipses (`…` or `...`) marking elided text are permitted; verify the
  non-elided fragments byte-exact.
- Bracketed editor insertions like `[sic]` or `[name]` are permitted; verify
  the text outside brackets byte-exact.

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — quote byte-exactness against the cited source
- `title`: `"[QUOTE] text not verbatim in cited raw at {file}:{line}"`
- `body`: `"Quoted span '{first-80-chars}...' from claim does not appear byte-exact in cited raw(s) [{slug-list}]. Closest match: '{closest-80-chars}...' (Levenshtein distance {d})."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `mechanical` — a byte-exact string comparison against the resolved raw's contents; the ellipsis and bracketed-insertion exclusions narrow which fragments are compared, they do not make the comparison a reading
- `evidence_quote`: the quoted span from the claim that fails to match
- `recommendation`: `"Either correct the quote to byte-exact match against the raw, OR change the marker to `[PARAPHRASE]` if the span is a paraphrase rather than verbatim quote."`

### Single source of truth

This rule is duplicated for cross-bucket coverage at C-WIKI-MARK-3 (atomic-marker
checklist). When a violation matches both rules, emit ONE finding under
C-WIKI-CITE-7 (this rule is the canonical home; atomic-marker bucket defers).

## Future checklist items

Additional citation-discipline review criteria (raw-existence pre-check,
cross-vault citation resolution, page/section-anchor specificity, etc.) get
added as additional `## C-WIKI-CITE-N` sections in this file. Single canonical
home; no need to modify other checklists or the agent def when adding new
criteria.
