---
name: wiki_lint
description: "Health check the wiki — 14 diagnostic checks (structural + semantic + tier-aware)."
---

# wiki_lint — Wiki Health Check

**Reference resource, not a discoverable skill or standalone command.** The immutable TaskSpec and selected schema control which checks apply. For supported maintenance on OMP use `/skill:wiki-reverify` with its documented mode; do not reconstruct an independent lint workflow from this background checklist.

Runs 14 diagnostic checks against the wiki and produces a health report. Helps maintain wiki quality and catch drift. Default content scope is `wiki/` plus `mocs/`; vaults with non-canonical layouts can pass `--content-roots <dir1>,<dir2>,...` to walk other top-level directories.

## Execution

### Step 0 — Resolve Vault

1. Use the actual workflow-selected vault and its immutable resolution context; do not infer a vault from unrelated host instruction files.
2. Read `.wiki-schema.md` from the vault root to load tag ontology and conventions.
3. If `.wiki-schema.md` does not exist, report the missing prerequisite. Explicit control initialization uses `/skill:wiki --init --vault NAME-or-path` on OMP and must preserve existing content.

### Step 1 — Gather All Wiki Pages

1. List all `.md` files under each content root (default `wiki/`; override via `--content-roots`) plus `mocs/` (always loaded separately). Use vault_ops.py or MCP tools; files de-duplicated by resolved path.
2. Read frontmatter from each page (use batch reads for efficiency).
3. Build an in-memory index:
   - Map of `page-name → frontmatter` for every page.
   - Map of `page-name → inbound wikilinks` (scan content for `[[...]]` references).
   - Map of `page-name → outbound wikilinks`.

### Step 2 — Run 14 Checks

#### Check 1: Orphan Notes (0 inbound wikilinks)

- For each page, count inbound `[[wikilinks]]` from other pages.
- A page is an orphan if no other page links to it AND it's not listed in any MOC.
- Exclude `index.md`, `log.md`, `.wiki-schema.md`, and all `type: moc` pages from this check (MOCs are top-level entry points — they're navigational hubs, not orphans).
- **Severity:** Warning (orphans are discoverable only via search, not browsing).

#### Check 2: Stale Compilations (source notes not incorporated)

- For each `type: compilation` page:
  - Read its `sources` list from frontmatter or the Sources section.
  - Search for source notes with the **same `topic:` frontmatter value** (not just same directory) that are NOT listed in the compilation.
  - If any unincorporated source notes with matching topic exist, flag the compilation as stale.
  - **Important:** Match on the `topic:` field specifically (e.g., `tech/ai/memory`), NOT on directory path. A compilation about `tech/ai/memory` should only flag source notes also tagged `tech/ai/memory`, not all 50+ notes in `wiki/tech/ai/`.
- **Severity:** Warning (compilation may be missing recent knowledge).

#### Check 3: MOC Drift (note counts don't match)

- For each `type: moc` page:
  - Count the actual number of notes and compilations in the MOC's domain.
  - Compare against the counts in the MOC's frontmatter (`notes:`, `compilations:`).
  - If they diverge by more than 1, flag as drifted.
- **Severity:** Info (cosmetic but can mislead).

#### Check 4: Tag Violations (unknown/unnormalized tags)

- For each page, check its `tags` array against the `.wiki-schema.md` ontology.
- Flag any tag that:
  - Is not in the allowed topics list.
  - Has an alias that should have been normalized (e.g., `claude` should be `devtools/claude-code`).
  - Is missing entirely (page has no tags).
- **Severity:** Warning (affects search and organization).

#### Check 5: Contradictions (flagged but unresolved)

- Search all pages for `contradictions:` frontmatter field > 0.
- Search content for the word "contradicts" or "contradiction" near `[[wikilinks]]`.
- List all unresolved contradictions with the pages involved.
- **Severity:** Info (requires human judgment to resolve).

#### Check 6: Freshness (past threshold)

- For each page, check `freshness` and `last_verified` frontmatter fields.
- Calculate staleness:
  - `live`: stale after 1 day
  - `weekly`: stale after 7 days
  - `monthly`: stale after 30 days (default)
  - `quarterly`: stale after 90 days
  - `permanent`: never stale
- Flag pages past their freshness threshold.
- **Severity:** Info (content may be outdated).

#### Check 7: Missing Compilations (3+ notes, no compilation)

- Group source notes by domain/topic.
- For each group with 3+ source notes:
  - Check if a compilation page exists for that topic.
  - If not, flag as a missing compilation opportunity.
- **Severity:** Suggestion (knowledge can be compounded).

#### Check 8: Unfetched Links (depth-2 never fetched)

- Search all source notes for `unfetched_links` frontmatter arrays.
- Collect all unfetched URLs across the wiki.
- Deduplicate and group by domain.
- **Severity:** Info (potential knowledge gaps).

#### Check 9: Provenance Coverage (unsourced claims) — tier-aware thresholds

- For each page, classify tier:
  - Tier-A (`type: source`): threshold 50% (raws are factual; lower tolerance because evidence is the source itself).
  - Tier-B (`type: compilation` / `entity`): threshold 70%.
  - Tier-C (`type: inference|strategy|strategy-card|plan`): SKIP this check; use Check 12 instead (Tier-C has a structural check with stricter coverage rules).
- For Tier-A + Tier-B pages:
  - Count lines in Key Concepts / Key Findings sections that contain claims (lines starting with `- **`).
  - Count how many have `^[` provenance markers.
  - Calculate coverage percentage.
  - Flag pages below their tier-specific threshold.
- **Severity:** Warning (claims without provenance may be hallucinated).

#### Check 10: Cross-Note Data Consistency

- Extract all numeric data points with entity context from wiki pages.
  Example: "acceptance rate" near "Stanford" with a percentage → record as data point.
- Group data points by entity + metric.
- If the same entity+metric appears in multiple notes with different values,
  flag as inconsistent (unless one is explicitly marked as updated/corrected).
- **Severity:** Warning (contradictory data undermines trust).

#### Check 11: Confidence & Verification Gaps

- Flag pages missing `confidence:` frontmatter field (new pages should have it).
- Flag pages with `confidence: low` that have no `## Verification Notes` section.
- Flag source notes that have no corresponding raw archive file (raw/{subdir}/ missing).
- **Severity:** Warning (unverified content in decision-critical wiki).

#### Check 12: Tier-C Citation Coverage

- For each `type: inference|strategy|strategy-card|plan` page:
  - Locate the `## Reasoning Chain` section (REQUIRED on Tier-C pages).
  - Parse each numbered step (lines matching `^\d+\.`).
  - For each step, check for `^[[[<slug>]]]` provenance footnote pattern.
  - Calculate coverage: numbered_steps_with_citation / total_numbered_steps.
  - **Flag if coverage < 100%** (Tier-C requires every reasoning step to cite).
  - Verify `## Context` section EXISTS + has substantive content (**>100 chars excluding heading**).
  - Verify `## Counter-Considerations` section EXISTS + has substantive content (**>150 chars excluding heading**).
  - Verify `## Review Triggers` section EXISTS + contains **falsifiable** triggers. Each bullet MUST contain a concrete observable condition — e.g., "if vacancy rate exceeds 8% in any quarter" PASSES; "if circumstances change" FAILS. Heuristic: each trigger bullet must contain (a) a conditional connector ("if" / "when" / "unless") AND (b) at least one numeric comparator (`>`, `<`, `>=`, `<=`, `==`, `exceeds`, `falls below`, `drops`, `reaches`) OR a measurable noun with an explicit threshold value. Bullets matching only (a) without (b) are flagged as unfalsifiable.
- **Severity:** Critical (Tier-C drives decisions; un-cited prescriptive claims undermine the whole architecture).

#### Check 13: Tier Mixing Detection

- For each `type: compilation` or `type: entity` page (Tier-B):
  - Scan body for `[STRATEGY]` markers — Tier-B MUST NOT use the `[STRATEGY]` marker (Tier-C only).
  - Scan body for prescriptive language patterns: imperative-mood sentences ("you should X", "the right approach is Y", "always Z", "never W"), recommendation-shaped paragraphs (e.g., starting with "Recommendation:", "I recommend:").
  - **Flag pages with prescriptive content as candidates for Tier-C extraction.** Each finding includes a suggested extraction target: `wiki/{domain}/strategy/{tentative-goal-slug}.md`.
- For each `type: source, artifact_role: capture` page (Tier-A):
  - Preserve the source author's own analysis, citation syntax and markers as immutable evidence. Source-owned `[INFERENCE]`, `[SYNTHESIS]` or `[STRATEGY]` text is not generated wiki analysis and must not be removed or migrated on that basis.
  - Check source identity, original snapshot/unit membership and fidelity under the selected Source Note contract. Generated interpretation belongs in a separate Tier-B/Tier-C artifact.
- **Severity:** Major (tier mixing weakens evidence-quality contract integrity).

#### Check 14: Raw Provenance Completeness

- For each `type: source` capture, apply the selected canonical version and explicit vault declarations, not a private rollout-date cutoff.
  - Bind `artifact_role: capture`, source identity/IDs, physical `source_class`, `provenance_class`, `raw_modality`, actual acquisition `source_hash`/`source_hash_kind`/`source_hash_algorithm`, normalized-body `content_hash`, ingestion date, source locator and required selected-schema metadata.
  - Physical source class and provenance class are distinct; a class name must not create an undeclared raw modality or read grant. Preserve original source authors separately from the actual generator.
  - Preserve declared manifest-authoritative `source_sha256`/`source_hash_authority` separately from body and transport hashes. Never replace unavailable evidence with an invented digest or date.
  - Captures start draft with null verification; only actual hash-bound acceptance may establish published lifecycle metadata. Schema 3.0 date/null and 3.1 UTC timestamp rules stay distinct.
  - Missing source bytes, incomplete inventory, inaccessible visual evidence or failed fidelity cannot become acceptance through advisory lint or metadata backfill. Legacy diagnostics do not authorize manual rewriting of immutable captures.
- **Acceptance:** the current TaskSpec, selected schema and runtime gates determine blocking findings. This reference checklist grants neither publication nor a separate backfill/repair workflow.

### Step 3 — Produce Health Report

Format the report:

```
=== wiki Health Report ===
Date: YYYY-MM-DD HH:MM
Vault: <vault path>
Total pages: <count> (sources: N, compilations: N, entities: N, queries: N, mocs: N)

Score: X/100

## Check Results

### 1. Orphan Notes — X found
⚠️ WARN
- wiki/tech/ai/2026-04-01-orphan-page.md (0 inbound links)

### 2. Stale Compilations — X found
⚠️ WARN
- wiki/tech/ai/ai-agents-overview.md (2 unincorporated sources)

### 3. MOC Drift — X found
ℹ️ INFO
- mocs/tech.md (claims 5 notes, actual 8)

### 4. Tag Violations — X found
⚠️ WARN
- wiki/tech/ai/2026-04-01-page.md: tag "claude" → should be "devtools/claude-code"

### 5. Contradictions — X unresolved
ℹ️ INFO
- wiki/tech/ai/compilation-a.md ↔ wiki/tech/ai/source-b.md

### 6. Freshness — X stale pages
ℹ️ INFO
- wiki/tech/ai/old-page.md (monthly, last verified 2025-12-01)

### 7. Missing Compilations — X opportunities
💡 SUGGESTION
- tech/ai/agents: 5 sources, no compilation

### 8. Unfetched Links — X URLs
ℹ️ INFO
- 3 URLs from reddit.com
- 2 URLs from arxiv.org

## Recommended Actions
1. Run `wiki <url>` for unfetched links in topic X
2. Re-run wiki compilation for stale compilation Y
3. Fix tags on pages: [list]
```

### Step 4 — Calculate Score

Score out of 100, deducting points:
- -5 per orphan note (max -20)
- -5 per stale compilation (max -20)
- -2 per MOC drift (max -10)
- -3 per tag violation (max -15)
- -2 per unresolved contradiction (max -10)
- -1 per stale page (max -10)
- -3 per missing compilation opportunity (max -15)
- Unfetched links don't deduct (informational only)
- -3 per page below 70% provenance coverage (max -15) [Check 9]
- -3 per inconsistent data point (max -15) [Check 10]
- -2 per missing confidence field (max -10) [Check 11]

### Step 5 — Append to Log

Append a summary to `log.md`:
```
## YYYY-MM-DD HH:MM — wiki_lint
- **Score:** X/100
- **Orphans:** X | **Stale compilations:** X | **MOC drift:** X
- **Tag violations:** X | **Contradictions:** X | **Stale pages:** X
- **Missing compilations:** X | **Unfetched links:** X
```

## Options

| Flag | Effect |
|---|---|
| `--vault <path>` | Override default vault path |
| `--check <n>` | Run only check number N (1-14). Comma-separated for multiple, e.g. `--check 1,9,11,12,13,14` |
| `--fix` | Auto-fix what's possible (normalize tags, update MOC counts) |
| `--verbose` | Show full details for every issue found |
| `--summary` | One-line score output only |
| `--json` | JSON output for automation/cron |
| `--content-roots <dirs>` | Comma-separated content directory names under vault root (default: `wiki`). For vaults with non-canonical layouts. `mocs/` is always loaded separately in addition. Empty/absolute/traversal/outside-vault entries are skipped with a warning |

## Auto-Fix Capabilities (with `--fix`)

When `--fix` is passed, the following issues are automatically corrected:

1. **Tag violations (aliases):** Replace aliased tags with their canonical form per `.wiki-schema.md`.
2. **MOC drift:** Update note/compilation counts in MOC frontmatter.
3. **Orphan notes:** Add orphan pages to the relevant MOC's topic section.

The following are NOT auto-fixed (require human judgment):
- Stale compilations (need content merge)
- Contradictions (need human resolution)
- Missing compilations (need synthesis)
- Unfetched links (need network access)
