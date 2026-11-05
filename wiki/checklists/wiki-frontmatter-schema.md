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
