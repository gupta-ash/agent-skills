# Generated composite — edit the four source buckets, not this file.
# Order: citation → frontmatter → marker → synthesis.

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

# Wiki-frontmatter-schema review checklist (discriminator v1, contract vault-wiki)

The reviewer has no modes. This frontmatter-schema bucket reaches a
reviewer only when a calling pipeline inlines it, via the generated
composite `wiki-all.md`, into the brief it passes as the optional
`--prompt-file`; a caller that omits it ships no rules at all. The
artifact under review travels separately as `--source`, which is
required alongside `--contract vault-wiki`. That flag is how intent is
declared: an absent class is refused, never defaulted, and the reviewer
does not infer "this is a wiki compilation review" from prompt content.
`--checklist` is retired outright with no v1 equivalent, and
`--contract` is not a rename of it.

Scope: wiki-compilation and wiki-strategy frontmatter blocks. Validates
required-field presence, enum membership, ISO-8601 datestamps, list-of-objects
shape, and integer counts that match body section bullet counts.

## C-WIKI-FM-1 — required-fields-present (HIGH severity)

### Rule

Apply the REQUIRED set for the artifact's actual type and resolved canonical
version. Do not impose compilation fields on source/entity/MOC/reference pages.
Custom vault constraints remain applicable. For compilations the required set is:

`type`, `artifact_role`, `source_ids`, `title`, `concept`, `created`, `updated`,
`domain`, `topic`, `tags`, `sources`, `last_compiled`, `contradictions`,
`open_questions`, `related`, `author`, `human_edited`, `confidence`, `freshness`,
`status`, `compiled_from`, `last_verified`, `pipeline_id`, `vault`, plus exactly
one of `contributing_books` (all-book) or `contributing_sources` (otherwise).
`artifact_role` is `topic_distill|synthesis`, never capture. Strategy-family
artifacts use their common and type-specific canonical fields, not `concept`,
`topic`, `last_compiled` or compilation source-label fields merely by analogy.

### OPTIONAL field set

`supersedes` (populated by the emit step only on a re-compile that supersedes
an existing artifact at the same canonical path).

### Finding shape

- `severity`: `high`
- `category`: `usability` — required-field presence is frontmatter well-formedness
- `title`: `"Required frontmatter field '{field}' missing at {file}"`
- `body`: `"Frontmatter block does not declare REQUIRED field `{field}`."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: frontmatter block range
- `basis`: `mechanical` — a key-presence test against a closed REQUIRED set
- `evidence_quote`: `null` — the violation is a missing key, and no span of the artifact can show a field that is not there
- `recommendation`: `"Add the field to the frontmatter block per the hybrid schema."`

## C-WIKI-FM-2 — type-value (HIGH severity)

### Rule

The `type` field MUST equal the literal string `"compilation"` for a
compilation artifact, OR `"strategy"` / `"inference"` / `"strategy-card"` /
`"plan"` for a strategy artifact (the strategy type-family is set by the
caller flag at dispatch time).

### Finding shape

- `severity`: `high`
- `category`: `usability` — a schema field's value must be in its declared set for the artifact to be routable
- `title`: `"Invalid type value '{actual}' at {file}"`
- `body`: `"Frontmatter declares `type: {actual}`; expected one of {compilation, strategy, inference, strategy-card, plan}."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: `type:` line
- `basis`: `mechanical` — string equality against a closed set
- `evidence_quote`: the `type: {actual}` line
- `recommendation`: `"Correct `type:` to match the artifact's canonical type per the caller-flag selection at dispatch time."`

## C-WIKI-FM-3 — confidence-enum (HIGH severity)

### Rule

The `confidence` field MUST be one of the closed set `{high, medium, low}`.

### Finding shape

- `severity`: `high`
- `category`: `usability` — enum membership of a schema field; whether the declared level is WARRANTED is C-WIKI-SYNTH-4, which is `truthfulness`
- `title`: `"Invalid confidence value '{actual}' at {file}"`
- `body`: `"Frontmatter declares `confidence: {actual}`; expected one of {high, medium, low}."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: `confidence:` line
- `basis`: `mechanical` — string equality against the closed set `{high, medium, low}`
- `evidence_quote`: the `confidence: {actual}` line
- `recommendation`: `"Set `confidence:` to the closest enum match given the source set diversity and inference content (see C-WIKI-SYNTH-4 for the high-confidence rubric)."`

## C-WIKI-FM-4 — status-enum (HIGH severity)

### Rule

The `status` field MUST be one of `{draft, verified, published, superseded}`.

### Finding shape

- `severity`: `high`
- `category`: `usability` — enum membership of a schema field
- `title`: `"Invalid status value '{actual}' at {file}"`
- `body`: "Frontmatter declares `status: {actual}`; expected one of {draft, verified, published, superseded}."
- `file`: compilation/strategy path
- `line_start` / `line_end`: `status:` line
- `basis`: `mechanical` — membership in `{draft, verified, published, superseded}`
- `evidence_quote`: the `status: {actual}` line
- `recommendation`: "Use draft until actual acceptance; verified/published requires the bound acceptance gates, superseded follows replacement."

## C-WIKI-FM-5 — freshness-enum (HIGH severity)

### Rule

The `freshness` field MUST be one of the closed set `{live, weekly, monthly,
quarterly, permanent, all, annual}`.

### Finding shape

- `severity`: `high`
- `category`: `usability` — enum membership of a schema field
- `title`: `"Invalid freshness value '{actual}' at {file}"`
- `body`: `"Frontmatter declares `freshness: {actual}`; expected one of {live, weekly, monthly, quarterly, permanent, all, annual}."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: `freshness:` line
- `basis`: `mechanical` — string equality against the closed freshness set
- `evidence_quote`: the `freshness: {actual}` line
- `recommendation`: `"Set `freshness:` to match the staleness profile of the cited source set."`

## C-WIKI-FM-6 — last-verified-iso8601 (MEDIUM severity)

### Rule

For accepted published canonical3.1 artifacts, `last_verified` MUST record the
actual successful verification UTC time (`YYYY-MM-DDTHH:MM:SSZ`, optionally
with one to six fractional-second digits), not emit time. Preserve the actual
persisted observation's precision rather than rounding it to another instant.
Drafts retain null and cannot claim not-yet-earned verification. Pinned3.0
artifacts retain their date/null contract. A date alone is not a3.1 UTC timestamp.

### Finding shape

- `severity`: `medium`
- `category`: `usability` — timestamp format conformance
- `title`: `"Invalid last_verified format '{actual}' at {file}"`
- `body`: "Frontmatter `last_verified: {actual}` violates the resolved canonical-version and lifecycle-specific date/null/UTC contract."
- `file`: compilation/strategy path
- `line_start` / `line_end`: `last_verified:` line
- `basis`: `mechanical` — resolved canonical version and lifecycle-specific date/null/UTC conformance
- `evidence_quote`: the `last_verified: {actual}` line
- `recommendation`: "Keep unverified drafts null; record only the actual accepted verification time in the pinned canonical format."

## C-WIKI-FM-7 — compiled-from-list-of-objects (HIGH severity)

### Rule

The `compiled_from` field MUST be a YAML list of objects. Each object MUST
declare the keys `slug`, `source_class`, and `citation_count`.

A scalar string, a list of scalars, or a list of objects with missing required
keys flags HIGH.

### Finding shape

- `severity`: `high`
- `category`: `usability` — the field's YAML shape, which every downstream reader parses
- `title`: `"compiled_from shape invalid at {file}"`
- `body`: `"`compiled_from` declared as {scalar|list-of-scalars|list-of-objects-with-missing-keys: {missing-keys}}; expected list of objects with keys {slug, source_class, citation_count}."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: `compiled_from:` block range
- `basis`: `mechanical` — a YAML shape and key-presence test
- `evidence_quote`: the `compiled_from:` declaration as written (the scalar, the list of scalars, or the entry missing keys)
- `recommendation`: `"Rewrite `compiled_from:` as a list of objects, one per cited raw source, each with `slug`, `source_class`, `citation_count` keys populated."`

## C-WIKI-FM-8 — citation-count-matches-body (HIGH severity)

### Rule

For each entry in `compiled_from:`, the integer `citation_count` MUST equal
the body count of resolved supporting citations (new form `^[[[<slug>]]]`).

### Single source of truth

This rule duplicates C-WIKI-CITE-3 (citation-discipline checklist). When a
violation matches both rules, emit ONE finding under C-WIKI-CITE-3 (the
citation-discipline bucket is the canonical home; this rule defers).

This frontmatter-scoped rule fires ONLY when `citation_count` is malformed
(non-integer, missing) — disagreement with body count is handled by
C-WIKI-CITE-3.

### Finding shape

- `severity`: `high`
- `category`: `usability` — this rule fires only on a malformed value; the count's AGREEMENT with the body is C-WIKI-CITE-3, which is `truthfulness`
- `title`: `"compiled_from[i].citation_count malformed at {file}"`
- `body`: `"Entry for slug `{slug}` has `citation_count: {actual}`; expected non-negative integer."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: the `compiled_from[i]` entry's line range
- `basis`: `mechanical` — an integer type test
- `evidence_quote`: the entry's `citation_count: {actual}` line; `null` when the key is absent from the entry, since no span can show a missing key
- `recommendation`: "Set citation_count to the count of uniquely resolved supporting body citations (see C-WIKI-CITE-3)."

## C-WIKI-FM-9 — open-questions-count (MEDIUM severity)

### Rule

The integer frontmatter field `open_questions` MUST equal the count of bullets
under the `## Open Questions` section in the body.

### Counting

Count first-level bullets (lines beginning with `- ` after `## Open Questions`
heading, up to the next `##` heading). Nested sub-bullets do not count.

### Finding shape

- `severity`: `medium`
- `category`: `usability` — a frontmatter integer disagreeing with its own body section misroutes every index that reads it. It is also a false number, so the rule touches `truthfulness`; the structural failure is the heavier one because the count describes the page's own body rather than a source
- `title`: `"open_questions count mismatch at {file}"`
- `body`: `"Frontmatter declares `open_questions: {declared}`; body section has {actual} first-level bullets."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: `open_questions:` line
- `basis`: `mechanical` — a bullet count under a named heading against a declared integer
- `evidence_quote`: the `open_questions: {declared}` line
- `recommendation`: `"Update `open_questions:` to match the body section bullet count (currently {actual})."`

## C-WIKI-FM-10 — contradictions-count (MEDIUM severity)

### Rule

The integer frontmatter field `contradictions` MUST equal the count of bullets
under the `## Contradictions` section in the body.

### Counting

Count first-level bullets under the `## Contradictions` heading, up to the
next `##` heading. Nested sub-bullets do not count.

### Finding shape

- `severity`: `medium`
- `category`: `usability` — a frontmatter integer disagreeing with its own body section misroutes every index that reads it. It is also a false number, so the rule touches `truthfulness`; the structural failure is the heavier one because the count describes the page's own body rather than a source
- `title`: `"contradictions count mismatch at {file}"`
- `body`: `"Frontmatter declares `contradictions: {declared}`; body section has {actual} first-level bullets."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: `contradictions:` line
- `basis`: `mechanical` — a bullet count under a named heading against a declared integer
- `evidence_quote`: the `contradictions: {declared}` line
- `recommendation`: `"Update `contradictions:` to match the body section bullet count (currently {actual})."`

## C-WIKI-FM-11 — sources-count-minimum (MEDIUM severity)

### Rule

The integer frontmatter field `sources` MUST be greater than or equal to 1.

`sources` is an aggregate count and is NOT required to equal
`len(compiled_from)` (an aggregator may count source-level groupings differently
from per-raw entries). Do not flag disagreement between `sources` and
`len(compiled_from)`.

### Finding shape

- `severity`: `medium`
- `category`: `usability` — a floor on a schema field's value
- `title`: `"sources count too low at {file}"`
- `body`: `"Frontmatter declares `sources: {actual}`; expected >= 1."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: `sources:` line
- `basis`: `mechanical` — an integer comparison against 1
- `evidence_quote`: the `sources: {actual}` line
- `recommendation`: `"Set `sources:` to the aggregate count of distinct sources informing the artifact (>= 1)."`

## C-WIKI-FM-12 — pipeline-id-regex (HIGH severity)

### Rule

New pipeline artifacts MUST use their actual controller run ID. Existing valid
canonical sentinels `legacy-pre-canonicalization`, `hand-curated` and
`imported-{source-name}` remain supported. Never replace a legitimate sentinel
with a fabricated historical run or enforce a compile-only regex on ingestion.

### Finding shape

- `severity`: `high`
- `category`: `usability` — identifier format conformance, which is how an artifact is traced back to its run
- `title`: `"pipeline_id format invalid at {file}"`
- `body`: "Frontmatter pipeline_id is neither the actual originating run ID nor a supported retained canonical sentinel."
- `file`: compilation/strategy path
- `line_start` / `line_end`: `pipeline_id:` line
- `basis`: `mechanical` — actual run identity or supported canonical sentinel
- `evidence_quote`: the `pipeline_id: {actual}` line
- `recommendation`: "Use the actual originating run ID; preserve legitimate retained sentinels."

## C-WIKI-FM-13 — pipeline-id-not-placeholder (HIGH severity)

### Rule

The `pipeline_id` field MUST NOT contain the literal template-placeholder
string `{{PIPELINE_ID}}`. Placeholder substitution must occur before emit.

### Finding shape

- `severity`: `high`
- `category`: `usability` — an unsubstituted template placeholder leaves the artifact untraceable
- `title`: `"pipeline_id placeholder leaked into emit at {file}"`
- `body`: `"Frontmatter declares `pipeline_id: {{PIPELINE_ID}}`; template-placeholder substitution did not occur before emit."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: `pipeline_id:` line
- `basis`: `mechanical` — a literal substring test for `{{PIPELINE_ID}}`
- `evidence_quote`: the `pipeline_id: {{PIPELINE_ID}}` line
- `recommendation`: `"Apply the dispatcher-supplied pipeline_id substitution to the frontmatter before emit. Re-run the emit step or apply a surgical patch."`

## C-WIKI-FM-14 — tag-ontology-compliance (MEDIUM severity)

### Rule

Every tag in the frontmatter `tags:` list MUST appear in the vault's
`.wiki-schema.md` declared `tag_ontology` OR be alias-resolvable per the
vault's tag-alias mapping.

If a tag is neither in the ontology nor alias-resolvable, flag MEDIUM.

### Finding shape

- `severity`: `medium`
- `category`: `usability` — tags are the vault's navigation surface
- `title`: `"Tag '{tag}' not in vault tag_ontology at {file}"`
- `body`: `"Frontmatter tag `{tag}` is not declared in `{vault}/.wiki-schema.md` `tag_ontology` and is not alias-resolvable."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: `tags:` line
- `basis`: `mechanical` — membership in the vault's declared `tag_ontology`, then a lookup in its declared alias mapping
- `evidence_quote`: the `tags:` line carrying the offending tag
- `recommendation`: `"Replace `{tag}` with an ontology-approved tag OR add `{tag}` to the vault's tag_ontology via a separate vault-meta edit."`

## Future checklist items

Additional frontmatter-schema review criteria (cross-field consistency checks,
supersedes-chain integrity, deprecated-field detection, etc.) get added as
additional `## C-WIKI-FM-N` sections in this file. Single canonical home; no
need to modify other checklists or the agent def when adding new criteria.

# Wiki-atomic-marker-accuracy review checklist (discriminator v1, contract vault-wiki)

The reviewer has no modes. This atomic-marker-accuracy bucket reaches a
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
carries exactly one atomic evidence marker from the closed vocabulary, that
compound markers are absent, and that marker semantics (verbatim for `[QUOTE]`,
multi-source for `[SYNTHESIS]`, reasoning-shown for `[INFERENCE]`) hold.

The closed marker vocabulary is exactly 5 tokens:
`[QUOTE]`, `[PARAPHRASE]`, `[INFERENCE]`, `[SYNTHESIS]`, `[UNKNOWN]`.

Tokens like `[REFUTED]`, `[FABRICATED]` and `[UNVERIFIABLE]` are verifier
verdict labels (NOT body markers). `[UNVERIFIABLE]` is named explicitly
because `wiki_thesis.md` lists it alongside the five body markers in its
evidence-tag taxonomy, where it is an Anti-Bias Audit CLASSIFICATION rather
than a body marker; `wiki-compile.md` gives the matching instruction for
bodies ("UNVERIFIABLE claims -> remove or downgrade to `[UNKNOWN]`"). The
`[STRATEGY]` token belongs to wiki-strategy artifacts only and is governed by
a separate checklist; do not use in compilation bodies.

## C-WIKI-MARK-1 — exactly-one-marker (HIGH severity)

### Rule

Every claim sentence or bullet in the body of `## Overview` and
`## Key Findings` MUST carry EXACTLY ONE marker from the closed 5-token set:
`[QUOTE]`, `[PARAPHRASE]`, `[INFERENCE]`, `[SYNTHESIS]`, `[UNKNOWN]`.

Zero markers OR two-or-more distinct markers on the same claim flags HIGH.

### Counting

For each claim line, count occurrences of each token from the closed set.
The sum across all 5 tokens MUST equal 1.

### Excluded from the rule

- Section headings, structural prose, table headers, and bullet-list
  introductions are not claims.
- `## Open Questions` and `## Recommend-Strategize` bullets MAY carry zero
  markers (those sections have their own marker conventions).

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — the marker declares a claim's evidence class, so a wrong count leaves that class unstated or self-contradictory
- `title`: `"Claim marker count != 1 at {file}:{line}"`
- `body`: `"Claim '{first-80-chars}...' carries {N} markers from the closed set; expected exactly 1."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `mechanical` — the sum of the closed 5-token set on the claim line must equal 1
- `evidence_quote`: the claim sentence or bullet, whose markers are the count
- `recommendation`: `"Add or remove markers so exactly one of {[QUOTE], [PARAPHRASE], [INFERENCE], [SYNTHESIS], [UNKNOWN]} is present per claim."`

## C-WIKI-MARK-2 — no-compound-markers (HIGH severity)

### Rule

The body MUST NOT contain compound markers. A compound marker is any token
that combines two or more closed-set marker names via a separator character
(`/`, `+`, `,`, `-`, `—`, etc.) inside the bracket.

Examples of REJECTED compound forms - every one carries TWO marker names:
- `[QUOTE/PARAPHRASE]`
- `[QUOTE + PARAPHRASE]`
- `[QUOTE on smash; PARAPHRASE on 20%]`

NOT rejected. ONE marker name plus qualifying text is ATOMIC under the rule
above, whatever separator sits between them:
- `[UNKNOWN — brief framing]` - one marker name plus an explanatory phrase.
  The separator does not add a second evidence class.
- `[INFERENCE — derivation]`
- `[QUOTE-fragment]`

### Counting

A compound marker is TWO OR MORE closed-set marker names inside ONE bracket:
`\[[^\]]*\b(QUOTE|PARAPHRASE|INFERENCE|SYNTHESIS|UNKNOWN)\b[^\]]*\b(QUOTE|PARAPHRASE|INFERENCE|SYNTHESIS|UNKNOWN)\b[^\]]*\]`

Any match in body content flags HIGH (one finding per match).

The previous regex was `\[(QUOTE|...)\s*[/\-+,—]\s*\S+`, which fired on ONE
name followed by any text. That contradicted the Rule section directly above
it, which has always defined a compound as "two or more closed-set marker
names". Where a rule's stated test and its stated definition disagree, the
DEFINITION governs.

### Approved alternative

If a claim genuinely spans TWO evidence classes, split it into two claims,
each carrying its own atomic marker:
- `[PARAPHRASE]` with a separate `[QUOTE]` claim if the bullet has both verbatim
  and paraphrased content.

Qualifying ONE marker with rationale needs no split and never did.
`[INFERENCE] (rationale: ...)` and `[INFERENCE — rationale]` are both atomic
and both PASS this rule. The parenthetical form is preferred for readability;
it is not required here.

### Excluded from the rule

- NOTHING is excluded by section. In particular the `## Open Questions` and
  `## Recommend-Strategize` carve-out that `C-WIKI-MARK-1` grants by name does
  NOT extend to this rule, and that asymmetry is DELIBERATE rather than an
  omission. MARK-1's carve-out exists because those sections may legitimately
  carry ZERO markers; it says nothing about whether a marker written there may
  be malformed. A two-name compound is malformed wherever it appears.
- A section carve-out would hide genuine two-name compounds. A section's
  permission to omit a marker does not permit malformed markers.

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — a compound marker leaves the claim's evidence class ambiguous
- `title`: `"Compound marker at {file}:{line}"`
- `body`: `"Body contains compound marker `{matched-text}`; closed-set markers MUST be atomic."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: line containing the compound marker
- `basis`: `mechanical` — the stated regex either matches or it does not
- `evidence_quote`: the compound marker as matched, in its line
- `recommendation`: `"Split into atomic marker + parenthetical clarifier, OR separate into two claims each with its own atomic marker."`

## C-WIKI-MARK-3 — quote-verbatim (HIGH severity)

### Rule

The quoted text span of every `[QUOTE]`-marked claim MUST appear byte-exact
verbatim in at least one of the claim's cited raw sources.

### Single source of truth

This rule duplicates C-WIKI-CITE-7 (citation-discipline checklist). When a
violation matches both rules, emit ONE finding under C-WIKI-CITE-7 (the
citation-discipline bucket is the canonical home; this rule defers).

This atomic-marker-scoped rule fires ONLY for marker-attribution defects (e.g.
`[QUOTE]` applied to clearly synthesized text where no quote candidate exists)
rather than near-verbatim mismatches.

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — marker attribution against the claim's actual evidence shape
- `title`: `"[QUOTE] marker on non-quotable claim at {file}:{line}"`
- `body`: `"Claim '{first-80-chars}...' carries `[QUOTE]` but contains synthesizer-style aggregation (e.g. 'most sources agree', 'in summary')."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `judgment` — recognizing synthesizer-style aggregation in the claim's prose is a reading, not a count (the byte-exact half is C-WIKI-CITE-7, which is `mechanical`)
- `evidence_quote`: the `[QUOTE]`-marked claim carrying the aggregation language
- `recommendation`: `"Change the marker to `[SYNTHESIS]` or `[PARAPHRASE]` to reflect the claim's actual evidence shape."`

## C-WIKI-MARK-4 — quote-page-section-ref (MEDIUM severity)

### Rule

`[QUOTE]` claims MUST include a page or section reference where the cited raw
provides one. Acceptable forms (anywhere on the same line as the marker):

- `p. 42`, `pp. 42-44`, `page 42`
- `ch. 3`, `chapter 3`
- `§ 4.1`, `sec. 4.1`, `section 4.1`

If the cited raw is page-numbered or section-headed AND the `[QUOTE]` claim
omits the reference, flag MEDIUM.

### Excluded from the rule

- Web sources without stable pagination (e.g. blog posts, FAQ pages) are
  exempt; no page/section reference required.
- Cited raws that lack any structural anchor (continuous prose with no
  headings, no pagination) are exempt.

### Finding shape

- `severity`: `medium`
- `category`: `truthfulness` — a quote a reader cannot locate in the source is not resolvable provenance
- `title`: `"[QUOTE] missing page/section reference at {file}:{line}"`
- `body`: `"Claim '{first-80-chars}...' carries `[QUOTE]` from page-numbered raw `{slug}` but omits a page/section reference."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `judgment` — whether the cited raw is page-numbered or section-headed at all, and whether it falls under the pagination exclusions, is a reading of the source
- `evidence_quote`: the `[QUOTE]` claim line, which carries the marker and no page or section reference
- `recommendation`: `"Append a page reference (e.g. `p. 42`, `ch. 3`, or `§ 4.1`) to the claim line."`

## C-WIKI-MARK-5 — inference-shows-reasoning (MEDIUM severity)

### Rule

`[INFERENCE]` claims MUST show the reasoning inline. Acceptable reasoning
markers (anywhere on the same line as the marker):

- `rationale:`, `because`, `since`, `given that`, `due to`, `inferred from`,
  `derived from`, `reasoning:`, `therefore`, `it follows that`

If an `[INFERENCE]` claim has none of these reasoning markers on the same
line or in a parenthetical, flag MEDIUM.

### Finding shape

- `severity`: `medium`
- `category`: `truthfulness` — an inference whose derivation is not shown cannot be checked against its premises
- `title`: `"[INFERENCE] missing reasoning trace at {file}:{line}"`
- `body`: `"Claim '{first-80-chars}...' carries `[INFERENCE]` but shows no premise-to-conclusion trace inline."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `judgment` — the closed marker list is a proxy; whether the line actually shows a premise-to-conclusion trace, which is what the finding asserts, is a reading
- `evidence_quote`: the `[INFERENCE]` claim line
- `recommendation`: `"Add an inline reasoning trace (e.g. `(rationale: ...)`, `because ...`, `given that ...`)."`

## C-WIKI-MARK-6 — synthesis-multi-source (HIGH severity)

### Rule

`[SYNTHESIS]` claims MUST cite at least 2 DISTINCT raw slugs on the same line.

A `[SYNTHESIS]` claim citing 0 or 1 raws flags HIGH (single-source synthesis
is by definition not synthesis).

### Counting

Let `cited_slugs` be the set of slug values extracted from `^[[slug]]` markers
on the same line as the `[SYNTHESIS]` marker. Rule fires when
`len(cited_slugs) < 2`.

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — a `[SYNTHESIS]` marker on a single-source claim misstates the claim's evidence shape
- `title`: `"[SYNTHESIS] cites < 2 distinct raws at {file}:{line}"`
- `body`: `"Claim '{first-80-chars}...' carries `[SYNTHESIS]` but cites only `{len(cited_slugs)}` distinct raw(s): [{slug-list}]."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `mechanical` — `len(cited_slugs) < 2` over the slugs on the same line
- `evidence_quote`: the `[SYNTHESIS]` claim line with its citations
- `recommendation`: `"Either cite >= 2 distinct raws supporting the synthesis, OR demote the marker to `[PARAPHRASE]` or `[INFERENCE]` depending on the single-source content's evidence shape."`

## C-WIKI-MARK-7 — paraphrase-found-in-spirit (MEDIUM severity)

### Rule

`[PARAPHRASE]` claims MUST be found-in-spirit in at least one cited raw
source. The match is semantic (the cited raw must contain the key noun
phrases or proposition direction of the claim), not byte-exact.

If a `[PARAPHRASE]` claim has no semantic anchor in any cited raw, flag MEDIUM.

### Excluded from the rule

This check is partially heuristic. When the cited raw's prose is dense or the
paraphrase compresses across multiple sentences, allow latitude. Bias is
toward UNDER-flagging; reserve this finding for clear semantic drift.

### Finding shape

- `severity`: `medium`
- `category`: `truthfulness` — whether the paraphrase agrees with what the cited source actually says
- `title`: `"[PARAPHRASE] not anchored in cited raw at {file}:{line}"`
- `body`: `"Claim '{first-80-chars}...' carries `[PARAPHRASE]` but the key noun phrases [{nps}] do not appear in cited raw `{slug}`."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `judgment` — the match is explicitly semantic rather than byte-exact, and the rule states it is partially heuristic
- `evidence_quote`: the `[PARAPHRASE]` claim line
- `recommendation`: `"Either correct the paraphrase to match the source's actual proposition, OR demote to `[INFERENCE]` if the claim is derived rather than paraphrased."`

## C-WIKI-MARK-8 — no-marker-outside-closed-set (HIGH severity)

### Rule

The body MUST NOT contain any all-caps bracketed token of the form `[XYZ]`
that is NOT in the closed 5-token marker set OR in the closed footnote-style
citation form `^[[<slug>]]`.

Examples of REJECTED out-of-vocabulary markers:
- `[REFUTED]`, `[FABRICATED]` (these are verifier verdict labels, not body markers)
- `[STRATEGY]` (belongs only in strategy-type artifacts under a separate marker rule)
- `[NOTE]`, `[TODO]`, `[FIXME]` (dev-comment markers; do not belong in published body)
- `[CLAIM]`, `[FACT]`, `[CONJECTURE]` (not in the closed vocabulary)

### Counting

Detect via regex: `\[[A-Z]+\]` (all-caps inside brackets). Filter to tokens
NOT in `{QUOTE, PARAPHRASE, INFERENCE, SYNTHESIS, UNKNOWN}` and NOT preceded
by `^` (which marks the footnote citation form).

### Excluded from the rule

- The footnote-style citation form `^[[<slug>]]` (square brackets nested inside
  caret-anchored brackets) is not a marker; ignore.
- Inline-code fenced content (between backticks) is excluded.

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — the closed set IS the evidence-class vocabulary, and the rule's leading examples (`[REFUTED]`, `[FABRICATED]`) are verdict labels that misstate a claim's evidence status; dev-comment residue like `[TODO]` is the `usability` half, which is the lighter of the two
- `title`: `"Out-of-vocabulary marker `{matched-token}` at {file}:{line}"`
- `body`: `"Body contains token `{matched-token}` not in the closed marker vocabulary {[QUOTE], [PARAPHRASE], [INFERENCE], [SYNTHESIS], [UNKNOWN]}."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: line containing the out-of-vocab token
- `basis`: `mechanical` — the stated regex, the closed-set filter and the fenced-code exclusion are all decidable
- `evidence_quote`: the out-of-vocabulary token as matched, in its line
- `recommendation`: `"Replace with the closest closed-set marker, OR remove if the token is dev-comment residue (e.g. `[TODO]`)."`

## Future checklist items

Additional atomic-marker review criteria (marker-citation-line-alignment,
synthesis-source-diversity floor, inference-premise-cited check, etc.) get
added as additional `## C-WIKI-MARK-N` sections in this file. Single canonical
home; no need to modify other checklists or the agent def when adding new
criteria.

# Wiki-synthesis-quality review checklist (discriminator v1, contract vault-wiki)

The reviewer has no modes. This synthesis-quality bucket reaches a
reviewer only when a calling pipeline inlines it, via the generated
composite `wiki-all.md`, into the brief it passes as the optional
`--prompt-file`; a caller that omits it ships no rules at all. The
artifact under review travels separately as `--source`, which is
required alongside `--contract vault-wiki`. That flag is how intent is
declared: an absent class is refused, never defaulted, and the reviewer
does not infer "this is a wiki compilation review" from prompt content.
`--checklist` is retired outright with no v1 equivalent, and
`--contract` is not a rename of it.

Scope: synthesis-quality checks that catch over-generalization, source-set
bias, confidence inflation, and scope leakage in wiki-compilation and
wiki-strategy artifacts. Five rules; the historical "recommend-strategize-gap"
sub-type is operationalized as a sub-check of C-WIKI-SYNTH-1 rather than a
separate rule.

## C-WIKI-SYNTH-1 — tier-misclassification (HIGH severity)

### Rule

Every `## Recommend-Strategize` item carrying a tier label (Tier-1, Tier-2,
Tier-3, Tier-4) MUST satisfy:

```
recommend_tier <= len(effective_independent_lineages(item.sources))
```

Where `effective_independent_lineages(sources)` deduplicates the source set by
author or lineage — sources from the same author, the same publisher, or
mutually-citing authors collapse into one lineage.

If an item declares Tier-1 but only 1 or 2 effective independent lineages
support it, flag HIGH. Same for Tier-2 with < 2 lineages, Tier-3 with < 3,
etc.

### Sub-check: recommend-strategize-gap

A Recommend item that names an actionable strategy or decision but cites only
a single source's actionability is a tier-misclassification (overclaims
tier-1 convergence on what is actually a single-source recommendation). Apply
the lineage-count rule above; do not emit a separate finding.

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — a tier label is a claim about how much independent support the recommendation has
- `title`: `"Tier-{N} misclassification at {file}:{line}"`
- `body`: `"Recommend item '{first-80-chars}...' declares Tier-{N} but cites only {M} effective independent lineage(s): [{lineage-summary}]."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: Recommend item's line range
- `basis`: `judgment` — collapsing sources into effective independent lineages by author, publisher or mutual citation is a reading of the source set; the comparison only follows it
- `evidence_quote`: the Recommend item line carrying the tier label
- `recommendation`: `"Demote to Tier-{M} OR add additional independent-lineage citations to support the Tier-{N} claim."`

## C-WIKI-SYNTH-2 — synthesis-bias (HIGH severity)

### Rule

For every multi-source convergent claim, each cited raw MUST support the
claim's direction. Flag HIGH when the cited raw contradicts or only weakly
qualifies the claim's direction.

The check is grep-verify per cited raw: extract the claim's key proposition +
polarity, then assert each cited raw contains corroborating text (not
contradicting text).

### Counting

For each claim of the form "Sources [s1, s2, ..., sN] agree that X":
- Read each cited raw.
- If any cited raw's relevant passage contradicts X or only partially
  supports X (with significant qualifiers the claim drops), flag HIGH.

### Excluded from the rule

When a claim explicitly acknowledges divergence (e.g. "Most sources hold X,
though source Y dissents [^source-y]"), the dissent acknowledgment satisfies
this rule. Do not flag.

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — whether each cited raw actually supports the claim's direction
- `title`: `"Synthesis-bias on claim at {file}:{line}"`
- `body`: `"Claim '{first-80-chars}...' attributes convergence to sources [{slug-list}], but cited raw `{slug}` actually {contradicts|partially-qualifies}: '{relevant-passage}'."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `judgment` — a per-source reading of proposition and polarity, including whether the qualifiers the claim dropped are significant
- `evidence_quote`: the claim asserting convergence, with its citation list
- `recommendation`: `"Either rewrite the claim to acknowledge the divergence, OR drop the contradicting source from the citation set if the claim direction is still supported by the remaining sources."`

## C-WIKI-SYNTH-3 — counter-considerations-gap (MEDIUM severity)

### Rule

Every Tier-1 claim under `## Recommend-Strategize` MUST carry a
counter-considerations scan flag — either as inline sub-bullets enumerating
counter-considerations OR as a `counter-considerations-scanned: true` flag.

This is a PROCESS check (did the agent SCAN for counter-evidence), not a
CONTENT check (was the scan exhaustive). The rule is partially heuristic.

If a Tier-1 claim has neither inline counter-consideration sub-bullets nor an
explicit scan-flag, flag MEDIUM.

### Approved forms

```
- Tier-1: Do X because A, B, C ^[[slug-1]] ^[[slug-2]] ^[[slug-3]]
  - Counter-consideration: condition Y inverts the recommendation ^[[slug-4]]
  - Counter-consideration: ...
```

OR

```
- Tier-1: Do X (counter-considerations-scanned: true; no significant counters
  found in compiled_from set) ^[[slug-1]] ^[[slug-2]] ^[[slug-3]]
```

### Finding shape

- `severity`: `medium`
- `category`: `truthfulness` — a Tier-1 recommendation issued without a counter-evidence scan overstates the evidence behind it
- `title`: `"Counter-considerations scan missing on Tier-1 claim at {file}:{line}"`
- `body`: `"Tier-1 Recommend item '{first-80-chars}...' has no inline counter-consideration sub-bullets and no `counter-considerations-scanned` flag."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `judgment` — the rule states it is a partially heuristic PROCESS check, and identifying a Tier-1 item and its counter-consideration sub-bullets is a reading
- `evidence_quote`: the Tier-1 Recommend item line
- `recommendation`: `"Either add counter-consideration sub-bullets enumerating scenarios that invert the recommendation, OR add `(counter-considerations-scanned: true; no significant counters found)` after confirming the scan."`

## C-WIKI-SYNTH-4 — inflated-confidence (HIGH severity)

### Rule

If the frontmatter declares `confidence: high`, ALL of the following MUST hold:

1. `sources >= 5` (at least 5 distinct sources informing the artifact)
2. Zero major contradictions in `## Contradictions` (no bullet flagged
   `[major]` or `[high-severity]`)
3. Zero inference-only claims (no claim in the body carries `[INFERENCE]`
   without a corroborating `[QUOTE]` or `[PARAPHRASE]` sibling claim)

If any of the three conditions fails, flag HIGH.

### Counting

- `sources` from frontmatter `sources:` field.
- Major-contradiction count: regex `\[major\]|\[high-severity\]` in the
  `## Contradictions` section.
- Inference-only claim count: count of `[INFERENCE]` claims with no `[QUOTE]`
  or `[PARAPHRASE]` claim in the same body sub-section.

### Finding shape

- `severity`: `high`
- `category`: `truthfulness` — the rubric tests whether the declared confidence level is WARRANTED by the evidence; enum membership of the same field is C-WIKI-FM-3, which is `usability`
- `title`: `"Inflated confidence at {file}"`
- `body`: `"Frontmatter declares `confidence: high` but rubric is not satisfied: sources={sources} (need >= 5), major_contradictions={mc} (need 0), inference_only_claims={ioc} (need 0)."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: `confidence:` line
- `basis`: `mechanical` — a numeric comparison, a regex over `## Contradictions`, and a marker count; the rule already called this a mechanical rubric
- `evidence_quote`: the `confidence: high` line, the declaration a fixer corrects
- `recommendation`: `"Either demote `confidence:` to `medium` or `low` to match the rubric, OR address the failing condition (add sources / resolve contradictions / add corroborating non-inference claims)."`

## C-WIKI-SYNTH-5 — state-vs-universal-claim (MEDIUM severity)

### Rule

When a claim is derived from a state-specific or region-specific raw source,
the claim's wording MUST inherit the geographic scope, OR the state-specific
provenance MUST be flagged inline.

A claim cited from a state-specific source but worded as universal (e.g. "all
colleges accept X", "in general, Y holds") flags MEDIUM.

### Counting

- `is_state_specific(raw)`: the cited raw's frontmatter or first body heading
  mentions a state, country, or other geographic scope marker.
- `claim_wording_is_universal(claim)`: claim contains universal quantifiers
  ("all", "every", "everywhere", "universally", "always", "in general") AND
  lacks geographic-scope qualifiers ("in {state/region}", "for {jurisdiction}
  applicants", "state-specific", "regional").

Rule fires when `is_state_specific(cited_raw)` AND
`claim_wording_is_universal(claim)` AND no inline provenance flag for the
state-specific raw.

### Finding shape

- `severity`: `medium`
- `category`: `truthfulness` — a claim that outruns the geographic scope of its source disagrees with that source
- `title`: `"State-specific claim worded as universal at {file}:{line}"`
- `body`: `"Claim '{first-80-chars}...' cites state-specific raw `{slug}` (scope: `{geographic-scope}`) but uses universal wording. Either scope the claim or flag the provenance."`
- `file`: compilation/strategy path
- `line_start` / `line_end`: claim's line range
- `basis`: `judgment` — `is_state_specific(raw)` requires reading the cited raw for a scope marker, and the word lists only approximate whether the claim's wording inherits that scope
- `evidence_quote`: the claim line using universal wording
- `recommendation`: `"Add a geographic-scope qualifier to the claim (e.g. 'in {state}'), OR flag the state-specific provenance inline (e.g. '(state-specific to {scope})')."`

## Future checklist items

Additional synthesis-quality review criteria (multi-region claim aggregation,
temporal-scope drift, source-class skew detection, etc.) get added as
additional `## C-WIKI-SYNTH-N` sections in this file. Single canonical home;
no need to modify other checklists or the agent def when adding new criteria.
