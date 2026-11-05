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
