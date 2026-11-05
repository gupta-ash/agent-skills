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
