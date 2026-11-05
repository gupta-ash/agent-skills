---
name: wiki_verify
description: "Review-template contracts (Templates A-D) for wiki content verification. A contract document cited by the wiki pipelines — not an invocable command."
---

# wiki_verify — Adversarial Content Verification Contracts

**This is a contract reference, not a command.** It is a plain resource inside `wiki/`, not another skill registration. Verification runs only through the supported public fronts and shared runtime; there is no standalone `wiki_verify` command or independent self-healing loop.

**Review background:** the four templates below, their selection guidance and confidence rules are reference material. Current Python workflows and each immutable TaskSpec's explicit duty, selected schema and canonical review contract control execution. These examples do not grant publication, repair attempts, tool access or native admission.

**Current invariant:** native review preserves the original caller's identity and
uses the qualified opposite model family; no single product is always the reviewer.
A tooling failure is no review, never permission to switch to a same-family route.
Worker completion, template guidance and a review receipt alone do not establish
knowledge acceptance. Never reconstruct the removed standalone command surface.

## Which template applies to a page

| Page `type:` | Template | Contract |
|---|---|---|
| `source` | Template A | `source_vs_raw` — page claims against the raw archive |
| `compilation` | Template B | `compilation_vs_sources` — synthesis against cited source notes |
| `entity` | Template C | `entity_consistency` — entity facts against referencing pages |
| `inference` / `strategy` / `strategy-card` / `plan` | Template D | `strategy_vs_evidence` — Tier-C structural integrity, reasoning-chain citation, counter-consideration substance |
| `thesis` | none here | handled by the `wiki_thesis` skill's own discriminator step |
| `moc` | none | navigation only; never cited as evidence |

The caller assembles each template's named inputs and sends the assembled prompt to a cross-family
reviewer. Every template below states what to load immediately before its body.

#### Template A: source_vs_raw

Load the source note content and its corresponding raw archive file (from `raw/` subdirectory, referenced in frontmatter or by convention).

~~~
# Wiki Content Verification — Source Note vs. Raw Archive

You are a fact-checker. Your job is to verify that a wiki source note accurately represents its raw source material. You are the DISCRIMINATOR — you find errors, you do not fix them.

## Source Note (the wiki page to verify)
{source_note_content}

## Raw Archive (the ground truth)
{raw_archive_content}

## Instructions
For EVERY claim in the "Key Concepts" section:
1. Find the corresponding passage in the raw archive.
1.5. Dereference each citation marker on the claim (`^[[[source]]]`, `^[file:line]`, etc.) — verify the cited file DIRECTLY contains the passage. A citation pointing to a file that does NOT contain the passage is REFUTED (even if the claim itself is true and a sibling file contains the passage); flag with RECOMMEND citation-move in ISSUE if the passage is locatable in a sibling raw file.
2. Classify: CONFIRMED, WEAKENED, REFUTED, FABRICATED, or UNVERIFIABLE
3. For [QUOTE] markers: verify verbatim match
4. For [PARAPHRASE]: verify interpretation
5. For [INFERENCE]: verify reasonable basis
6. Pay SPECIAL attention to: numbers/statistics, dates, proper nouns, causal claims, superlatives

## Output Format
For each claim:
CLAIM: [text]
MARKER: [QUOTE|PARAPHRASE|INFERENCE]
VERDICT: [CONFIRMED|WEAKENED|REFUTED|FABRICATED|UNVERIFIABLE]
EVIDENCE: [passage from raw, or "NOT FOUND"]
ISSUE: [if not CONFIRMED, explain]
SEVERITY: [CRITICAL|MAJOR|MINOR]

Then:
SUMMARY: [overall assessment]
CONFIDENCE_ASSESSMENT: [high|medium|low]
CLAIMS_TOTAL: [N]
CLAIMS_CONFIRMED: [N]
CLAIMS_WEAKENED: [N]
CLAIMS_REFUTED: [N]
CLAIMS_FABRICATED: [N]
~~~

#### Template B: compilation_vs_sources

Load the compilation page and all its cited source notes (resolve `^[[[source-slug]]]` references).

~~~
# Wiki Content Verification — Compilation vs. Source Notes

You are a fact-checker verifying that a compilation page faithfully synthesizes its cited sources.

## Compilation Page
{compilation_content}

## Cited Source Notes
{for each source: ### Source: {slug} followed by content}

## Instructions
For EVERY claim in "Key Findings":
1. Identify cited source note(s) via ^[[[source-slug]]]
1.5. Dereference the cited source note(s) — open each, confirm the cited note DIRECTLY contains a passage that supports the claim. A citation pointing to a source note that does NOT contain the supporting passage is REFUTED; flag with RECOMMEND citation-move in ISSUE if the passage is locatable in another cited or sibling source note. Compilation citations MUST resolve to source notes (`type: source`), never to other compilations or MOCs.
2. Verify cited source contains claimed information
3. For [SYNTHESIS]: verify fair representation
4. Flag claims with NO citation (potential fabrication)
5. Flag claims whose citation doesn't support them

## Output Format
For each claim:
CLAIM: [text]
MARKER: [QUOTE|PARAPHRASE|INFERENCE|SYNTHESIS]
VERDICT: [CONFIRMED|WEAKENED|REFUTED|FABRICATED|UNVERIFIABLE]
EVIDENCE: [passage from cited source, or "NOT FOUND"]
ISSUE: [if not CONFIRMED, explain]
SEVERITY: [CRITICAL|MAJOR|MINOR]

Then:
SUMMARY: [overall assessment]
CONFIDENCE_ASSESSMENT: [high|medium|low]
CLAIMS_TOTAL: [N]
CLAIMS_CONFIRMED: [N]
CLAIMS_WEAKENED: [N]
CLAIMS_REFUTED: [N]
CLAIMS_FABRICATED: [N]
~~~

#### Template C: entity_consistency

Load the entity page and all pages that reference it via wikilinks.

~~~
# Wiki Content Verification — Entity Consistency

You are a fact-checker verifying entity page consistency with source material.

## Entity Page
{entity_content}

## Pages Referencing This Entity
{for each: ### Reference: {slug} with relevant excerpts}

## Instructions
1. For each Key Fact, find supporting evidence in referencing pages
2. Flag facts in NO referencing page (potential fabrication)
3. Flag data inconsistencies (entity says X, source says Y)
4. Check description matches how entity is described in sources

## Output Format
For each fact:
CLAIM: [text]
VERDICT: [CONFIRMED|WEAKENED|REFUTED|FABRICATED|UNVERIFIABLE]
EVIDENCE: [passage from referencing page, or "NOT FOUND"]
ISSUE: [if not CONFIRMED, explain]
SEVERITY: [CRITICAL|MAJOR|MINOR]

Then:
SUMMARY: [overall assessment]
CONFIDENCE_ASSESSMENT: [high|medium|low]
CLAIMS_TOTAL: [N]
CLAIMS_CONFIRMED: [N]
CLAIMS_WEAKENED: [N]
CLAIMS_REFUTED: [N]
CLAIMS_FABRICATED: [N]
~~~

#### Template D: strategy_vs_evidence

Load the Tier-C strategy/inference/strategy-card/plan page and all its cited compilations (resolve `^[[[compilation-slug]]]` references in `## Reasoning Chain` + `## Evidence Base`). Optionally include tier-2 fallback raws if `compiled_from:` frontmatter includes raw slugs directly.

~~~
# Wiki Content Verification — Tier-C Strategy vs. Cited Evidence

You are a fact-checker verifying that a Tier-C strategy/inference/strategy-card/plan is grounded in its cited compilations (and optional tier-2 fallback raws). You also verify the page's structural integrity per the Tier-C contract.

## Tier-C Page
{tier_c_content}

## Cited Compilations (tier-1)
{for each compilation: ### Compilation: {slug} followed by content}

## Cited Raws (tier-2 fallback, if any)
{for each raw: ### Raw: {slug} followed by content}

## Instructions

### Structural integrity (REQUIRED sections)

1. Verify `## Goal` section exists + is verbatim restatement of frontmatter `goal:` field
2. Verify `## Context` section exists + has substantive content (**>100 chars** excluding heading)
3. Verify EXACTLY ONE of `## Recommendation` (for type=strategy/strategy-card/plan) OR `## Conclusion` (for type=inference) exists, matching frontmatter `type:`
4. Verify `## Reasoning Chain` section exists + contains numbered steps (lines matching `^\d+\.`)
5. Verify `## Evidence Base` section exists + bulleted citations resolve to existing compilation OR raw files
6. Verify `## Counter-Considerations` section exists + is substantive (not boilerplate "On the other hand..."; contains specific contrary evidence or scenarios; **>150 chars** excluding heading)
7. Verify `## Review Triggers` section exists + contains concrete observable triggers (each trigger MUST be **falsifiable**: contains a conditional connector ["if" / "when" / "unless"] AND a numeric comparator OR a measurable noun with an explicit threshold value. e.g., "if vacancy rate exceeds 8% in any quarter" PASSES; "if circumstances change" FAILS)

### Per-step citation discipline

For EVERY numbered step in `## Reasoning Chain`:
1. Identify cited source(s) via `^[[[slug]]]` footnote
1.5. Dereference each cited slug — confirm the cited compilation OR raw DIRECTLY contains a passage supporting the reasoning step. A citation pointing to a file that does NOT contain the supporting passage is REFUTED; flag with RECOMMEND citation-move in ISSUE.
2. Verify the cited source supports the reasoning step (NOT just the prescriptive claim that follows — the REASONING the step encodes must trace to cited evidence)
3. For `[STRATEGY]` markers: verify the prescriptive claim is grounded in cited evidence; flag if claim is unfalsifiable or contradicts cited evidence
4. For `[INFERENCE]` markers: verify the reasoning shown inline is grounded in cited evidence
5. **Flag uncited reasoning steps** — Tier-C requires 100% citation in Reasoning Chain

### Provenance discipline

- Tier-1 default (cite compilations) — most claims should cite compilation slugs
- Tier-2 fallback (cite raws directly) — rare; only when (a) verbatim quote needed AND not in any compilation, OR (b) raw not yet compiled
- Flag tier-2 cites WITHOUT one of these justifications as RECOMMEND-COMPILE — the strategy should add a `recommend_compile:` h2 surfacing the uncompiled raw

## Output Format

For each STRUCTURAL FINDING (sections 1-7 above):
SECTION: [Goal|Context|Recommendation|Conclusion|Reasoning Chain|Evidence Base|Counter-Considerations|Review Triggers]
VERDICT: [PASS|FAIL]
ISSUE: [if FAIL, explain what's missing or insufficient]
SEVERITY: [CRITICAL|MAJOR|MINOR]

For each REASONING STEP:
STEP: [number]
CITATION: [^[[[slug]]] or "MISSING"]
VERDICT: [CONFIRMED|WEAKENED|REFUTED|FABRICATED|UNVERIFIABLE|UNCITED]
EVIDENCE: [passage from cited source, or "NOT CITED" or "NOT FOUND"]
ISSUE: [if not CONFIRMED, explain]
SEVERITY: [CRITICAL|MAJOR|MINOR]

For each [STRATEGY] marker:
CLAIM: [text]
GROUNDING: [supported by cited evidence|unfalsifiable|contradicts evidence|grounded in inference chain]
VERDICT: [CONFIRMED|WEAKENED|REFUTED|FABRICATED|UNVERIFIABLE]
ISSUE: [if not CONFIRMED, explain]
SEVERITY: [CRITICAL|MAJOR|MINOR]

Then:
SUMMARY: [overall assessment — focus on structural integrity + reasoning-chain grounding + counter-consideration substance]
CONFIDENCE_ASSESSMENT: [high|medium|low]
STRUCTURAL_CHECKS_TOTAL: 7
STRUCTURAL_CHECKS_PASS: [N]
REASONING_STEPS_TOTAL: [N]
REASONING_STEPS_CONFIRMED: [N]
REASONING_STEPS_UNCITED: [N]
STRATEGY_MARKERS_TOTAL: [N]
STRATEGY_MARKERS_CONFIRMED: [N]
~~~

## Confidence re-assessment

The mechanical provenance and consistency checks are NOT specified here — they are specified in
`wiki_lint.md` (Check 9 provenance coverage, Check 10 cross-note data consistency, Check 11
confidence and verification gaps). Duplicating them here is what let the two copies drift. This
section covers only the judgement applied to their results.

**Tier-A + Tier-B pages:**
- If a page has high `confidence:` but >50% of claims are INFERENCE markers with <70% provenance coverage, recommend downgrading to `confidence: medium`.
- If a page has `confidence: high` but no corresponding raw archive file, recommend downgrade.

**Tier-C pages:**
- Tier-C is exempt from the ">50% INFERENCE = downgrade" rule (Tier-C is intentionally inferential).
- New downgrade triggers for Tier-C:
  - `confidence: high` but Reasoning Chain has any UNCITED step → downgrade to medium
  - `confidence: high` but Counter-Considerations is boilerplate/empty → downgrade to medium
  - `confidence: high` but Review Triggers are vague/unfalsifiable → downgrade to medium
  - `confidence: high` but >30% of citations are tier-2 (raws not compilations) → downgrade to medium (surface `recommend_compile:` for the uncompiled raws)
  - `confidence: high` but any `[STRATEGY]` marker classified UNFALSIFIABLE or CONTRADICTS_EVIDENCE → downgrade to medium

## Severity Classification

| Severity | Criteria | Example |
|---|---|---|
| CRITICAL | Factual error — number, date, name contradicts source | "Rate 5%" when source says "3.9%" |
| MAJOR | Misleading — technically defensible but substantively wrong | Omitting crucial qualifying context |
| MINOR | Style/precision — correct but could be clearer | Wrong evidence marker type |
